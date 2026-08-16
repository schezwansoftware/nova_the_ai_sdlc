from __future__ import annotations
from typing import Dict, Any, List, Optional
import time
import uuid
from pydantic import ValidationError

from ai_sdlc.orchestration.state import StateStore, WorkflowState, WorkflowStatus, utc_now_iso
from ai_sdlc.agents.base import AgentRequest, AgentResult, AgentStatus
from ai_sdlc.agents.registry import AgentRegistry
from ai_sdlc.capabilities.providers.sage_factory import get_default_sage_provider
from ai_sdlc.capabilities.sage import (
    MalformedResponseError as SageMalformedResponseError,
    ProviderError as SageProviderError,
    SageRequest,
    normalize_context_query,
)


class AgentExecutionError(Exception):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


#: Marker note appended to a `sage_context` entry when the per-invocation
#: NEEDS_CONTEXT round budget (`Orchestrator.max_context_rounds`) is
#: exhausted -- see `invoke_agent_for_stage`'s NEEDS_CONTEXT branch. Also
#: used to detect a genuine loop bug (the worker asking again even after
#: being told the budget is exhausted).
_CONTEXT_BUDGET_EXHAUSTED_NOTE = "context round budget exhausted; proceed with available information"


class Orchestrator:
    def __init__(self, workspace_path):
        self.store = StateStore(workspace_path)
        # AgentRegistry supports discovery from the workspace
        self.registry = AgentRegistry(workspace_path)
        self.max_attempts = 3
        # Sage Phase 2 knowledge consumption (see capabilities/sage.py's
        # module docstring): the Orchestrator is the only caller of
        # SageCapability -- a specialist agent never touches it directly.
        # `get_default_sage_provider` takes `workspace_path` directly
        # (unlike the zero-arg reasoning/coding/retrieval factories) since
        # the Orchestrator, unlike AgentRegistry._load_impl, already has
        # the real workspace path in hand -- see sage_factory.py's
        # docstring.
        self.sage = get_default_sage_provider(workspace_path)
        # Separate bound from max_attempts -- a needs_context round is not
        # a failure and must never count against the real retry budget.
        # Mirrors max_attempts's existing hardcoded-constant precedent.
        self.max_context_rounds = 3

    def register_agent(self, agent_id: str, agent_obj: Any):
        self.registry.register(agent_id, agent_obj)

    def load_workflow(self, workflow_id: Optional[str] = None) -> Optional[WorkflowState]:
        return self.store.read_workflow(workflow_id)

    def save_workflow(self, wf: WorkflowState) -> None:
        self.store.write_workflow(wf)

    def _emit(self, event: Dict[str, Any]):
        self.store.append_audit_event(event)

    # LangGraph integration helpers
    def run_workflow_graph(self, workflow_id: str, inputs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # Whole load -> mutate -> save sequence (including nested runner
        # execution) is one compound transaction so concurrent requests
        # against the same workflow can't interleave and lose updates.
        with self.store.transaction():
            wf = self.load_workflow(workflow_id)
            if not wf:
                raise RuntimeError("workflow not found")
            from ai_sdlc.orchestration.langgraph_runner import DEFAULT_WORKFLOW_NODES, LangGraphRunner
            nodes = list(DEFAULT_WORKFLOW_NODES)
            runner = LangGraphRunner(self, wf, nodes=nodes, inputs=inputs)
            return runner.run()

    def resume_workflow_after_clarification(self, workflow_id: str, question_id: str, answer: str) -> Dict[str, Any]:
        with self.store.transaction():
            wf = self.load_workflow(workflow_id)
            if not wf:
                raise RuntimeError("workflow not found")
            # Validate pending clarification
            if not wf.pending_clarification or wf.pending_clarification.get("question_id") != question_id:
                raise RuntimeError("invalid_or_stale_question_id")
            if wf.status != WorkflowStatus.WAITING_FOR_CLARIFICATION:
                raise RuntimeError("workflow_not_paused_for_clarification")

            # persist the answer into the clarification record
            clar_record = {
                "question_id": question_id,
                "workflow_id": wf.workflow_id,
                "stage": wf.pending_clarification.get("stage"),
                "question": wf.pending_clarification.get("question"),
                "answer": answer,
                "inputs": wf.pending_clarification.get("inputs", {}),
            }
            self.store.write_clarification(question_id, clar_record)

            # clear pending clarification and set inputs for resume
            wf.pending_clarification = None
            wf.status = WorkflowStatus.RUNNING
            # Persist the answer onto wf.inputs itself (accumulated by
            # question_id), not just a transient local dict, so a later
            # clarification round doesn't silently drop this round's answer
            # — wf.inputs is what invoke_agent_for_stage merges into every
            # subsequent agent call, including future rounds.
            clarification_answers = dict(wf.inputs.get("clarification_answers", {}))
            clarification_answers[question_id] = answer
            wf.inputs = {
                **wf.inputs,
                "clarification_answers": clarification_answers,
                "clarification_answer": answer,
                "question_id": question_id,
            }
            self.save_workflow(wf)

            # resume by invoking runner; invoke_agent_for_stage will merge
            # the now-updated wf.inputs (including clarification_answers)
            # into the agent's request automatically.
            from ai_sdlc.orchestration.langgraph_runner import DEFAULT_WORKFLOW_NODES, LangGraphRunner
            nodes = list(DEFAULT_WORKFLOW_NODES)
            runner = LangGraphRunner(self, wf, nodes=nodes)
            return runner.resume_after_clarification(answer, question_id)

    def resume_workflow_after_approval(self, workflow_id: str, approval_id: str, decision: str, feedback: str | None = None) -> Dict[str, Any]:
        with self.store.transaction():
            wf = self.load_workflow(workflow_id)
            if not wf:
                raise RuntimeError("workflow not found")
            # Validate pending approval
            if not wf.pending_approval or wf.pending_approval.get("approval_id") != approval_id:
                raise RuntimeError("invalid_or_stale_approval_id")
            if wf.status != WorkflowStatus.WAITING_FOR_APPROVAL:
                raise RuntimeError("workflow_not_waiting_for_approval")

            # persist approval decision
            approval_record = {
                "approval_id": approval_id,
                "workflow_id": wf.workflow_id,
                "stage": wf.pending_approval.get("stage"),
                "artifact": wf.pending_approval.get("artifact"),
                "decision": decision,
                "feedback": feedback,
                "approver_id": wf.initiator_id,
                "timestamp": None,
            }
            self.store.write_approval(approval_id, approval_record)

            if decision == "approved":
                # The node's AgentResult.data (persisted onto pending_approval
                # by invoke_agent_for_stage below) travels forward through the
                # resume instead of being regenerated -- see
                # LangGraphRunner.resume_after_approval's docstring for why
                # re-invoking the agent here would be wrong for a Tier 3
                # agent whose approved output came from real, already-
                # completed (and potentially expensive/non-deterministic)
                # work.
                approval_data = wf.pending_approval.get("data")
                wf.pending_approval = None
                wf.status = WorkflowStatus.RUNNING
                self.save_workflow(wf)
                from ai_sdlc.orchestration.langgraph_runner import DEFAULT_WORKFLOW_NODES, LangGraphRunner
                nodes = list(DEFAULT_WORKFLOW_NODES)
                runner = LangGraphRunner(self, wf, nodes=nodes)
                return runner.resume_after_approval(approval_data)
            else:
                # rejected — set explicit revision state and do not continue.
                # Thread the reviewer's feedback onto wf.inputs (matching the
                # "revision_feedback" input-threading pattern documented in
                # docs/architecture/v1_architecture.md section 6's "UX
                # Revision & Feedback Loop", the same accumulated-wf.inputs
                # mechanism invoke_agent_for_stage already uses for
                # clarification answers) so that whenever this stage is next
                # invoked -- via a fresh run_workflow_graph call, since no
                # automatic revision-resume trigger exists yet -- the agent
                # receives it as request.inputs["revision_feedback"].
                wf.pending_approval = {**wf.pending_approval, "decision": "rejected", "feedback": feedback}
                wf.inputs = {**wf.inputs, "revision_feedback": feedback}
                wf.status = WorkflowStatus.REVISION_REQUIRED
                self.save_workflow(wf)
                self._emit({"event": "approval_rejected", "workflow_id": wf.workflow_id, "approval_id": approval_id, "feedback": feedback})
                return {"status": "rejected"}

    def _make_request(self, workflow_id: str, agent_id: str, action: str, inputs: Dict[str, Any]) -> AgentRequest:
        return AgentRequest(
            request_id=str(uuid.uuid4()),
            workflow_id=workflow_id,
            agent_id=agent_id,
            agent_version="1.0",
            action=action,
            inputs=inputs or {},
        )

    def invoke_agent_for_stage(
        self,
        wf: WorkflowState,
        agent_id: str,
        action: str = "default",
        inputs: Dict[str, Any] = None,
        output_key: Optional[str] = None,
    ) -> Dict:
        """Invoke an agent for the current workflow stage.

        Behavior:
        - Create AgentRequest
        - Emit agent_started audit
        - Invoke agent.execute(request)
        - Validate AgentResult
        - Update workflow state according to AgentResult.status and decision
        - Handle needs_clarification and needs_approval by persisting records
        - Retry on AgentExecutionError when retryable and attempts < max_attempts
        - Emit audit events for agent_completed/failed/clarification/approval

        `output_key`, when provided by the calling graph node (see
        `LangGraphRunner.DEFAULT_WORKFLOW_NODES`), is where a COMPLETED
        result's `AgentResult.data` gets merged onto `wf.inputs` so the
        next node in the graph automatically receives this node's
        structured output as part of its own merged inputs. This keeps the
        graph declarative -- new stages can be wired in by adding a node
        dict, without editing this method.
        """
        agent = self.registry.get(agent_id)
        if not agent:
            # Two different failure modes look identical from `.get()`
            # alone: no metadata file for `agent_id` was ever found (a
            # real "not found"), versus metadata was found but the
            # implementation failed to *construct* (e.g. a real
            # ReasoningCapability/RetrievalCapability/CodingCapability
            # provider-selection factory raising ProviderError because a
            # configured provider's SDK isn't installed). Surfacing
            # `AgentRegistry.get_load_error()` when present turns a
            # misleading "Agent not found" into the actual reason, since
            # this message reaches the caller verbatim (`str(exc)` is
            # propagated through APIErrorDetail.message unmodified).
            load_error = self.registry.get_load_error(agent_id)
            if load_error:
                raise RuntimeError(
                    f"Agent '{agent_id}' failed to load: {load_error}"
                )
            raise RuntimeError(f"Agent not found: {agent_id}")

        # Orchestrator owns retry loop deterministically
        attempts = wf.retry_count.get(agent_id, 0)
        # Separate accumulator/counter for NEEDS_CONTEXT rounds -- never
        # touches `attempts`/`wf.retry_count` (context resolution is not a
        # failure, see the NEEDS_CONTEXT branch below).
        context_rounds = 0
        sage_context: List[Dict[str, Any]] = []
        while attempts < self.max_attempts:
            # make request per attempt; merge persisted wf.inputs with provided inputs so agents receive full context
            merged_inputs = {}
            if wf.inputs:
                merged_inputs.update(wf.inputs)
            if inputs:
                merged_inputs.update(inputs)
            if sage_context:
                merged_inputs["sage_context"] = list(sage_context)
            request = self._make_request(wf.workflow_id, agent_id, action, merged_inputs)
            self._emit({
                "event": "agent_started",
                "workflow_id": wf.workflow_id,
                "request_id": request.request_id,
                "agent_id": agent_id,
                "stage": wf.current_stage,
            })

            try:
                raw_result = agent.execute(request)
            except AgentExecutionError as e:
                # record failure and decide retry
                self._emit({
                    "event": "agent_failed",
                    "workflow_id": wf.workflow_id,
                    "request_id": request.request_id,
                    "agent_id": agent_id,
                    "error": str(e),
                    "retryable": e.retryable,
                })
                if e.retryable:
                    attempts += 1
                    wf.retry_count[agent_id] = attempts
                    self.save_workflow(wf)
                    self._emit({"event": "agent_retry", "workflow_id": wf.workflow_id, "agent_id": agent_id, "attempt": attempts})
                    continue
                else:
                    wf.status = WorkflowStatus.FAILED
                    self.save_workflow(wf)
                    self._emit({"event": "workflow_failed", "workflow_id": wf.workflow_id, "reason": "non_retryable_agent_error"})
                    return {"status": "failed", "error": str(e), "retryable": False}
            except Exception as e:
                # unknown error, treat as non-retryable
                self._emit({
                    "event": "agent_failed",
                    "workflow_id": wf.workflow_id,
                    "request_id": request.request_id,
                    "agent_id": agent_id,
                    "error": str(e),
                    "retryable": False,
                })
                wf.status = WorkflowStatus.FAILED
                self.save_workflow(wf)
                self._emit({"event": "workflow_failed", "workflow_id": wf.workflow_id, "reason": "non_retryable_agent_error"})
                return {"status": "failed", "error": str(e), "retryable": False}

            # Validate AgentResult schema
            try:
                if isinstance(raw_result, AgentResult):
                    result = raw_result
                else:
                    result = AgentResult(**(raw_result if isinstance(raw_result, dict) else raw_result.__dict__))
            except ValidationError as e:
                self._emit({
                    "event": "agent_result_invalid",
                    "workflow_id": wf.workflow_id,
                    "agent_id": agent_id,
                    "request_id": request.request_id,
                    "error": str(e),
                })
                # treat as retryable until attempts exhausted
                attempts += 1
                wf.retry_count[agent_id] = attempts
                if attempts >= self.max_attempts:
                    wf.status = WorkflowStatus.FAILED
                    self.save_workflow(wf)
                    self._emit({"event": "workflow_failed", "workflow_id": wf.workflow_id, "reason": "invalid_agent_output"})
                    return {"status": "failed", "error": "invalid_agent_output", "retryable": False}
                else:
                    self.save_workflow(wf)
                    self._emit({"event": "agent_retry", "workflow_id": wf.workflow_id, "agent_id": agent_id, "attempt": attempts})
                    continue

            # Normal result handling
            self._emit({
                "event": "agent_completed",
                "workflow_id": wf.workflow_id,
                "request_id": result.request_id,
                "agent_id": result.agent_id,
                "status": result.status.value if isinstance(result.status, AgentStatus) else str(result.status),
                "stage": wf.current_stage,
            })

            # Handle statuses
            if result.status == AgentStatus.COMPLETED:
                # mark stage complete
                if wf.current_stage:
                    wf.stages[wf.current_stage] = "completed"
                # Thread this node's structured output onto wf.inputs under
                # its declared output_key so the next graph node's
                # merged_inputs (computed at the top of this loop) picks it
                # up automatically -- e.g. PO's output_key="requirements"
                # becomes inputs["requirements"] for Architecture/UX.
                if output_key:
                    wf.inputs[output_key] = result.data
                wf.status = WorkflowStatus.RUNNING
                wf.retry_count.pop(agent_id, None)
                self.save_workflow(wf)
                return {"status": "completed"}

            if result.status == AgentStatus.NEEDS_CONTEXT:
                # Auto-resolved by Sage inline, entirely within this call
                # -- no persisted pending-context record, no
                # human-visible pause (unlike NEEDS_CLARIFICATION/
                # NEEDS_APPROVAL below, which must survive a separate
                # HTTP round-trip). Re-invokes the same agent afterward
                # (via `continue`), matching NEEDS_CLARIFICATION's
                # resume shape, not NEEDS_APPROVAL's -- PO/Architecture/
                # UX are cheap, reasoning-only, and haven't done real
                # expensive work yet.
                query = (result.context_query or "").strip()
                if not query:
                    # Malformed result: NEEDS_CONTEXT without a usable
                    # query indicates a bug in the agent/schema, not a
                    # normal miss -- same retryable-until-exhausted
                    # treatment as the agent_result_invalid branch above.
                    self._emit({
                        "event": "agent_result_invalid",
                        "workflow_id": wf.workflow_id,
                        "agent_id": agent_id,
                        "request_id": request.request_id,
                        "error": "NEEDS_CONTEXT result missing a usable context_query",
                    })
                    attempts += 1
                    wf.retry_count[agent_id] = attempts
                    if attempts >= self.max_attempts:
                        wf.status = WorkflowStatus.FAILED
                        self.save_workflow(wf)
                        self._emit({"event": "workflow_failed", "workflow_id": wf.workflow_id, "reason": "invalid_agent_output"})
                        return {"status": "failed", "error": "invalid_agent_output", "retryable": False}
                    self.save_workflow(wf)
                    self._emit({"event": "agent_retry", "workflow_id": wf.workflow_id, "agent_id": agent_id, "attempt": attempts})
                    continue

                self._emit({
                    "event": "context_requested",
                    "workflow_id": wf.workflow_id,
                    "agent_id": agent_id,
                    "stage": wf.current_stage,
                    "request_id": result.request_id,
                    "context_query": query,
                    "round": context_rounds,
                })

                if context_rounds >= self.max_context_rounds:
                    self._emit({
                        "event": "context_budget_exceeded",
                        "workflow_id": wf.workflow_id,
                        "agent_id": agent_id,
                        "context_query": query,
                        "round": context_rounds,
                    })
                    already_told = any(
                        entry.get("note") == _CONTEXT_BUDGET_EXHAUSTED_NOTE for entry in sage_context
                    )
                    if already_told:
                        # Asked again even after being told the budget is
                        # exhausted -- a genuine bug (an agent ignoring
                        # the caveat and re-asking), not a normal miss.
                        # Fail rather than loop forever.
                        wf.status = WorkflowStatus.FAILED
                        self.save_workflow(wf)
                        self._emit({"event": "workflow_failed", "workflow_id": wf.workflow_id, "reason": "needs_context_loop_exceeded"})
                        return {"status": "failed", "details": {"reason": "needs_context_loop_exceeded"}}
                    sage_context.append({
                        "query": query,
                        "answer": "",
                        "found": False,
                        "note": _CONTEXT_BUDGET_EXHAUSTED_NOTE,
                    })
                    context_rounds += 1
                    continue

                key = normalize_context_query(query)
                memory_entry = self.store.read_sage_memory().get(key)
                self._emit({
                    "event": "context_memory_check",
                    "workflow_id": wf.workflow_id,
                    "agent_id": agent_id,
                    "context_query": query,
                    "memory_key": key,
                    "hit": memory_entry is not None,
                })

                if memory_entry:
                    sage_context.append({
                        "query": query,
                        "answer": memory_entry.get("answer", ""),
                        "found": bool(memory_entry.get("found")),
                        "source_connector": memory_entry.get("source_connector"),
                        "source_url": memory_entry.get("source_url"),
                        "source": "memory",
                        "saved_at": memory_entry.get("saved_at"),
                    })
                    self._emit({
                        "event": "context_resolved",
                        "workflow_id": wf.workflow_id,
                        "agent_id": agent_id,
                        "context_query": query,
                        "found": bool(memory_entry.get("found")),
                        "source": "memory",
                        "round": context_rounds,
                    })
                    context_rounds += 1
                    continue

                self._emit({
                    "event": "sage_invoked",
                    "workflow_id": wf.workflow_id,
                    "agent_id": agent_id,
                    "context_query": query,
                })
                started_at = time.monotonic()
                sage_response = None
                try:
                    sage_response = self.sage.ask(
                        SageRequest(query=query, requesting_agent_id=agent_id)
                    )
                except (SageProviderError, SageMalformedResponseError) as exc:
                    # A Sage failure never fails the workflow -- treated
                    # the same as Sage finding nothing (see the `else`
                    # branch below).
                    self._emit({
                        "event": "sage_failed",
                        "workflow_id": wf.workflow_id,
                        "agent_id": agent_id,
                        "context_query": query,
                        "error": str(exc),
                    })
                duration_ms = int((time.monotonic() - started_at) * 1000)

                if sage_response is not None:
                    for skip in sage_response.metadata.get("skipped") or []:
                        self._emit({
                            "event": "connector_skipped",
                            "workflow_id": wf.workflow_id,
                            "connector_name": skip.get("name"),
                            "reason": skip.get("reason"),
                        })
                    self._emit({
                        "event": "sage_answered",
                        "workflow_id": wf.workflow_id,
                        "agent_id": agent_id,
                        "context_query": query,
                        "found": sage_response.found,
                        "source_connector": sage_response.source_connector,
                        "source_url": sage_response.source_url,
                        "duration_ms": duration_ms,
                        "steps_used": sage_response.steps_used,
                        "terminated_reason": sage_response.terminated_reason.value,
                    })
                    if sage_response.found:
                        # Only found=True answers are ever cached -- a
                        # miss is never cached, since caching "nothing was
                        # found" would prevent a later, differently-
                        # configured connector set from ever being tried
                        # again for the same query (see sage.py's
                        # SageMemoryEntry docstring).
                        self.store.write_sage_memory_entry(key, {
                            "query": query,
                            "answer": sage_response.answer,
                            "found": True,
                            "source_connector": sage_response.source_connector,
                            "source_url": sage_response.source_url,
                            "saved_at": utc_now_iso(),
                        })
                    sage_context.append({
                        "query": query,
                        "answer": sage_response.answer,
                        "found": sage_response.found,
                        "source_connector": sage_response.source_connector,
                        "source_url": sage_response.source_url,
                        "source": "sage",
                    })
                else:
                    sage_context.append({
                        "query": query,
                        "answer": "",
                        "found": False,
                        "note": "Sage was unavailable; proceed with available information",
                    })

                self._emit({
                    "event": "context_resolved",
                    "workflow_id": wf.workflow_id,
                    "agent_id": agent_id,
                    "context_query": query,
                    "found": sage_context[-1]["found"],
                    "source": sage_context[-1].get("source", "none"),
                    "round": context_rounds,
                })
                context_rounds += 1
                continue

            if result.status == AgentStatus.NEEDS_CLARIFICATION or (result.questions and len(result.questions) > 0):
                # persist clarification question(s)
                qid = f"q-{uuid.uuid4().hex[:8]}"
                question = {
                    "question_id": qid,
                    "agent_id": agent_id,
                    "question": result.questions[0] if result.questions else "clarification requested",
                    "reason": "agent_requested_clarification",
                    "required": True,
                }
                self.store.write_clarification(qid, question)
                wf.status = WorkflowStatus.WAITING_FOR_CLARIFICATION
                # Persist the inputs actually supplied to *this* call (not
                # just whatever was in wf.inputs before it), so a resume
                # sees them rather than silently dropping this call's
                # caller-supplied inputs.
                wf.inputs = merged_inputs
                wf.pending_clarification = {
                    "question_id": qid,
                    "stage": wf.current_stage,
                    "question": question.get("question"),
                    "inputs": merged_inputs.copy(),
                }
                self.save_workflow(wf)
                self._emit({"event": "clarification_requested", "workflow_id": wf.workflow_id, "question_id": qid, "agent_id": agent_id})
                return {"status": "needs_clarification", "question_id": qid}

            if result.status == AgentStatus.NEEDS_APPROVAL or (result.decision and result.decision.approval_required):
                aid = f"approval-{uuid.uuid4().hex[:8]}"
                approval = {
                    "approval_id": aid,
                    "workflow_id": wf.workflow_id,
                    "stage": wf.current_stage,
                    "artifact": result.artifact.model_dump() if result.artifact else {},
                    "decision": "pending",
                    "initiator_id": wf.initiator_id,
                    "timestamp": None,
                }
                self.store.write_approval(aid, approval)
                wf.status = WorkflowStatus.WAITING_FOR_APPROVAL
                # Same fix as the clarification branch above: persist this
                # call's actual merged inputs onto wf.inputs, not just what
                # was there before the interrupt.
                wf.inputs = merged_inputs
                wf.pending_approval = {
                    "approval_id": aid,
                    "stage": wf.current_stage,
                    "artifact": approval.get("artifact"),
                    "inputs": merged_inputs.copy(),
                    # Carried across the approval boundary so
                    # resume_workflow_after_approval can hand it to
                    # LangGraphRunner.resume_after_approval instead of
                    # re-invoking this agent to regenerate it (see that
                    # method's docstring).
                    "data": result.data,
                    "output_key": output_key,
                }
                self.save_workflow(wf)
                self._emit({"event": "approval_requested", "workflow_id": wf.workflow_id, "approval_id": aid, "stage": wf.current_stage})
                return {"status": "needs_approval", "approval_id": aid}

            # Unknown or other statuses -> mark blocked/failed accordingly
            if result.status == AgentStatus.FAILED:
                wf.status = WorkflowStatus.FAILED
                self.save_workflow(wf)
                self._emit({"event": "workflow_failed", "workflow_id": wf.workflow_id, "reason": "agent_failed"})
                return {"status": "failed", "attempts": attempts}

            # Default fallback
            wf.status = WorkflowStatus.RUNNING
            self.save_workflow(wf)
            return {"status": "unknown_status", "result_status": str(result.status)}

        # If we exit the retry loop without returning, attempts were exhausted
        wf.status = WorkflowStatus.FAILED
        self.save_workflow(wf)
        self._emit({"event": "workflow_failed", "workflow_id": wf.workflow_id, "reason": "retry_exhausted"})
        return {"status": "failed", "details": {"reason": "retry_exhausted"}}
