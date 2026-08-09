from __future__ import annotations
from typing import Dict, Any, Optional
import uuid
from pydantic import ValidationError

from ai_sdlc.orchestration.state import StateStore, WorkflowState, WorkflowStatus
from ai_sdlc.agents.base import AgentRequest, AgentResult, AgentStatus
from ai_sdlc.agents.registry import AgentRegistry


class AgentExecutionError(Exception):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class Orchestrator:
    def __init__(self, workspace_path):
        self.store = StateStore(workspace_path)
        # AgentRegistry supports discovery from the workspace
        self.registry = AgentRegistry(workspace_path)
        self.max_attempts = 3

    def register_agent(self, agent_id: str, agent_obj: Any):
        self.registry.register(agent_id, agent_obj)

    def load_workflow(self) -> Optional[WorkflowState]:
        return self.store.read_workflow()

    def save_workflow(self, wf: WorkflowState) -> None:
        self.store.write_workflow(wf)

    def _emit(self, event: Dict[str, Any]):
        self.store.append_audit_event(event)

    # LangGraph integration helpers
    def run_workflow_graph(self, workflow_id: str, inputs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        wf = self.load_workflow()
        if not wf or wf.workflow_id != workflow_id:
            raise RuntimeError("workflow not found")
        from ai_sdlc.orchestration.langgraph_runner import LangGraphRunner
        nodes = [
            {"id": "requirements", "type": "agent", "agent_id": "po"},
        ]
        runner = LangGraphRunner(self, wf, nodes=nodes, inputs=inputs)
        return runner.run()

    def resume_workflow_after_clarification(self, workflow_id: str, question_id: str, answer: str) -> Dict[str, Any]:
        wf = self.load_workflow()
        if not wf or wf.workflow_id != workflow_id:
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
        self.save_workflow(wf)

        # resume by invoking runner with merged inputs
        from ai_sdlc.orchestration.langgraph_runner import LangGraphRunner
        nodes = [{"id": "requirements", "type": "agent", "agent_id": "po"}]
        merged_inputs = wf.inputs.copy() if wf.inputs else {}
        merged_inputs.update({"clarification_answer": answer, "question_id": question_id})
        runner = LangGraphRunner(self, wf, nodes=nodes, inputs=merged_inputs)
        return runner.resume_after_clarification(answer, question_id)

    def resume_workflow_after_approval(self, workflow_id: str, approval_id: str, decision: str, feedback: str | None = None) -> Dict[str, Any]:
        wf = self.load_workflow()
        if not wf or wf.workflow_id != workflow_id:
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
            # clear pending and resume
            wf.pending_approval = None
            wf.status = WorkflowStatus.RUNNING
            self.save_workflow(wf)
            from ai_sdlc.orchestration.langgraph_runner import LangGraphRunner
            nodes = [{"id": "requirements", "type": "agent", "agent_id": "po"}]
            runner = LangGraphRunner(self, wf, nodes=nodes)
            return runner.run()
        else:
            # rejected — set explicit revision state and do not continue
            wf.pending_approval = {**wf.pending_approval, "decision": "rejected", "feedback": feedback}
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

    def invoke_agent_for_stage(self, wf: WorkflowState, agent_id: str, action: str = "default", inputs: Dict[str, Any] = None) -> Dict:
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
        """
        agent = self.registry.get(agent_id)
        if not agent:
            raise RuntimeError(f"Agent not found: {agent_id}")

        # Orchestrator owns retry loop deterministically
        attempts = wf.retry_count.get(agent_id, 0)
        while attempts < self.max_attempts:
            # make request per attempt; merge persisted wf.inputs with provided inputs so agents receive full context
            merged_inputs = {}
            if wf.inputs:
                merged_inputs.update(wf.inputs)
            if inputs:
                merged_inputs.update(inputs)
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
                wf.status = WorkflowStatus.RUNNING
                wf.retry_count.pop(agent_id, None)
                self.save_workflow(wf)
                return {"status": "completed"}

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
                wf.pending_clarification = {
                    "question_id": qid,
                    "stage": wf.current_stage,
                    "question": question.get("question"),
                    "inputs": wf.inputs.copy(),
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
                wf.pending_approval = {
                    "approval_id": aid,
                    "stage": wf.current_stage,
                    "artifact": approval.get("artifact"),
                    "inputs": wf.inputs.copy(),
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
