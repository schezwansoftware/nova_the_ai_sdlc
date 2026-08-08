from __future__ import annotations
from typing import Dict, Any, Optional
import uuid
from pydantic import ValidationError

from ai_sdlc.orchestration.state import StateStore, WorkflowState
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
        from ai_sdlc.orchestration.langgraph_runner import LangGraphRunner
        nodes = [{"id": "requirements", "type": "agent", "agent_id": "po"}]
        runner = LangGraphRunner(self, wf, nodes=nodes)
        return runner.resume_after_clarification(answer, question_id)

    def resume_workflow_after_approval(self, workflow_id: str, approval_id: str, decision: str) -> Dict[str, Any]:
        wf = self.load_workflow()
        if not wf or wf.workflow_id != workflow_id:
            raise RuntimeError("workflow not found")
        from ai_sdlc.orchestration.langgraph_runner import LangGraphRunner
        nodes = [{"id": "requirements", "type": "agent", "agent_id": "po"}]
        runner = LangGraphRunner(self, wf, nodes=nodes)
        return runner.resume_after_approval(approval_id, decision)

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

        request = self._make_request(wf.workflow_id, agent_id, action, inputs or {})
        self._emit({
            "event": "agent_started",
            "workflow_id": wf.workflow_id,
            "request_id": request.request_id,
            "agent_id": agent_id,
            "stage": wf.current_stage,
        })

        # attempt invocation
        try:
            raw_result = agent.execute(request)
        except AgentExecutionError as e:
            # record failure and decide retry
            self._handle_agent_failure(wf, agent_id, request, e)
            return {"status": "failed", "error": str(e), "retryable": e.retryable}
        except Exception as e:
            # unknown error, treat as non-retryable
            self._handle_agent_failure(wf, agent_id, request, AgentExecutionError(str(e), retryable=False))
            return {"status": "failed", "error": str(e), "retryable": False}

        # Validate AgentResult schema
        try:
            if isinstance(raw_result, AgentResult):
                result = raw_result
            else:
                # Try to coerce dict-like into AgentResult
                result = AgentResult(**(raw_result if isinstance(raw_result, dict) else raw_result.__dict__))
        except ValidationError as e:
            # Invalid output from agent
            # Increment retry and decide
            self._emit({
                "event": "agent_result_invalid",
                "workflow_id": wf.workflow_id,
                "agent_id": agent_id,
                "request_id": request.request_id,
                "error": str(e),
            })
            # treat as retryable until attempts exhausted
            self._increment_retry(wf, agent_id)
            attempts = wf.retry_count.get(agent_id, 0)
            if attempts >= self.max_attempts:
                wf.status = WorkflowState.schema().get("status") if False else "failed"
                self.save_workflow(wf)
                self._emit({"event": "workflow_failed", "workflow_id": wf.workflow_id, "reason": "invalid_agent_output"})
                return {"status": "failed", "error": "invalid_agent_output", "retryable": False}
            else:
                self.save_workflow(wf)
                return {"status": "retry", "attempt": attempts, "max_attempts": self.max_attempts}

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
            wf.status = "running"
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
            wf.status = "paused"
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
            wf.status = "waiting_for_approval"
            wf.pending_approval = {"stage": wf.current_stage, "artifact": approval.get("artifact")}
            self.save_workflow(wf)
            self._emit({"event": "approval_requested", "workflow_id": wf.workflow_id, "approval_id": aid, "stage": wf.current_stage})
            return {"status": "needs_approval", "approval_id": aid}

        # Unknown or other statuses -> mark blocked/failed accordingly
        if result.status == AgentStatus.FAILED:
            # Decide retry based on retry_count
            self._increment_retry(wf, agent_id)
            attempts = wf.retry_count.get(agent_id, 0)
            if attempts >= self.max_attempts:
                wf.status = "failed"
                self.save_workflow(wf)
                self._emit({"event": "workflow_failed", "workflow_id": wf.workflow_id, "reason": "agent_failed_exhausted"})
                return {"status": "failed", "attempts": attempts}
            else:
                self.save_workflow(wf)
                return {"status": "retry", "attempts": attempts, "max_attempts": self.max_attempts}

        # Default fallback
        wf.status = "running"
        self.save_workflow(wf)
        return {"status": "unknown_status", "result_status": str(result.status)}

    def _handle_agent_failure(self, wf: WorkflowState, agent_id: str, request: AgentRequest, error: AgentExecutionError):
        # Emit agent_failed and decide retry
        self._emit({
            "event": "agent_failed",
            "workflow_id": wf.workflow_id,
            "request_id": request.request_id,
            "agent_id": agent_id,
            "error": str(error),
            "retryable": error.retryable,
        })
        if error.retryable:
            self._increment_retry(wf, agent_id)
            attempts = wf.retry_count.get(agent_id, 0)
            if attempts >= self.max_attempts:
                wf.status = "failed"
                self.save_workflow(wf)
                self._emit({"event": "workflow_failed", "workflow_id": wf.workflow_id, "reason": "retry_exhausted"})
            else:
                self.save_workflow(wf)
        else:
            wf.status = "failed"
            self.save_workflow(wf)
            self._emit({"event": "workflow_failed", "workflow_id": wf.workflow_id, "reason": "non_retryable_agent_error"})

    def _increment_retry(self, wf: WorkflowState, agent_id: str):
        wf.retry_count[agent_id] = wf.retry_count.get(agent_id, 0) + 1
        self._emit({"event": "agent_retry", "workflow_id": wf.workflow_id, "agent_id": agent_id, "attempt": wf.retry_count[agent_id]})
