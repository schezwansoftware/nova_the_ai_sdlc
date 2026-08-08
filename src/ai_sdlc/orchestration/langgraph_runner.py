from __future__ import annotations
from typing import List, Dict, Any, Optional

from ai_sdlc.orchestration.orchestrator import Orchestrator
from ai_sdlc.orchestration.state import WorkflowState


class LangGraphRunner:
    """A LangGraph-backed (adapter) runner that executes a simple linear graph

    For V1 the graph is kept simple and linear:
      requirement_intake -> po -> end

    The runner delegates agent execution to the Orchestrator.invoke_agent_for_stage
    and interprets the structured results to perform interrupts (clarification,
    approval), retries and failures.

    This module is designed so it can later be replaced by a real LangGraph
    runtime adapter without changing Orchestrator or agent contracts.
    """

    def __init__(self, orchestrator: Orchestrator, workflow: WorkflowState, nodes: Optional[List[Dict[str, Any]]] = None):
        self.orch = orchestrator
        self.wf = workflow
        # nodes is a list of dicts: {"id": "po", "type": "agent", "agent_id": "po"}
        if nodes is None:
            self.nodes = [{"id": "po", "type": "agent", "agent_id": "po"}]
        else:
            self.nodes = nodes

    def run(self) -> Dict[str, Any]:
        # Execute nodes sequentially from the current_stage position
        start_index = 0
        if self.wf.current_stage:
            # find node index matching current stage
            for i, n in enumerate(self.nodes):
                if n.get("id") == self.wf.current_stage:
                    start_index = i
                    break

        for i in range(start_index, len(self.nodes)):
            node = self.nodes[i]
            nid = node.get("id")
            ntype = node.get("type")
            self.wf.current_stage = nid
            # persist current stage
            self.orch.save_workflow(self.wf)

            if ntype == "agent":
                agent_id = node.get("agent_id")
                res = self.orch.invoke_agent_for_stage(self.wf, agent_id)

                # handle responses from orchestrator.invoke_agent_for_stage
                status = res.get("status")
                if status == "completed":
                    # continue to next node
                    continue

                if status == "needs_clarification":
                    # pause execution and return interrupt info
                    return {"status": "interrupted", "type": "clarification", "question_id": res.get("question_id")} 

                if status == "needs_approval":
                    return {"status": "interrupted", "type": "approval", "approval_id": res.get("approval_id")}

                # handle failures that may be retryable
                if status == "failed":
                    retryable = res.get("retryable", False)
                    # if retryable and attempts remain, try again up to orchestrator.max_attempts
                    attempts = self.wf.retry_count.get(agent_id, 0)
                    max_attempts = self.orch.max_attempts
                    if retryable and attempts < max_attempts:
                        # attempt a retry loop until success or exhaustion
                        while attempts < max_attempts:
                            attempts += 1
                            # invoke again
                            res2 = self.orch.invoke_agent_for_stage(self.wf, agent_id)
                            if res2.get("status") == "completed":
                                # succeeded — continue to next node
                                break
                            if res2.get("status") == "failed":
                                if not res2.get("retryable", False):
                                    return {"status": "failed", "details": res2}
                                # else continue loop; attempts will have been incremented inside orchestrator
                                attempts = self.wf.retry_count.get(agent_id, attempts)
                                continue
                            # other statuses (clarification/approval) — return upstream
                            if res2.get("status") == "needs_clarification":
                                return {"status": "interrupted", "type": "clarification", "question_id": res2.get("question_id")}
                            if res2.get("status") == "needs_approval":
                                return {"status": "interrupted", "type": "approval", "approval_id": res2.get("approval_id")}
                        else:
                            # exhausted
                            return {"status": "failed", "details": {"reason": "retry_exhausted"}}
                        # if we broke out because succeeded, continue to next node
                        continue
                    else:
                        return {"status": "failed", "details": res}

                if status == "retry":
                    # orchestrator already incremented retry and saved state; stop to allow external retry or re-run
                    return {"status": "retry", "details": res}

                # fallback
                return {"status": "unknown", "result": res}

        # all nodes completed
        self.wf.current_stage = None
        self.wf.status = "completed"
        self.orch.save_workflow(self.wf)
        self.orch._emit({"event": "workflow_completed", "workflow_id": self.wf.workflow_id})
        return {"status": "completed"}

    def resume_after_clarification(self, answer: str, question_id: str) -> Dict[str, Any]:
        # Provide the answer as input to the current agent and continue execution
        # Find which agent requested clarification by reading the clarification file (not strictly required here)
        # For simplicity, pass the answer as input to invoke_agent_for_stage
        # Call invoke_agent_for_stage with inputs including clarification_answer
        current_stage = self.wf.current_stage
        if not current_stage:
            return {"status": "no_active_stage"}
        # Find node for current stage
        node = next((n for n in self.nodes if n.get("id") == current_stage), None)
        if not node:
            return {"status": "unknown_stage"}
        agent_id = node.get("agent_id")
        res = self.orch.invoke_agent_for_stage(self.wf, agent_id, inputs={"clarification_answer": answer, "question_id": question_id})
        if res.get("status") == "completed":
            # continue running remaining nodes
            return self.run()
        return res

    def resume_after_approval(self, approval_id: str, decision: str) -> Dict[str, Any]:
        # For V1, approval decisions are applied at workflow level.
        # Apply approval decision by updating workflow state (clear pending_approval) and continue.
        # In a real system, approval records would be validated and the approver's identity checked.
        self.orch.store.write_approval(approval_id, {"approval_id": approval_id, "decision": decision})
        # clear pending approval and resume
        self.wf.pending_approval = None
        self.wf.status = "running"
        self.orch.save_workflow(self.wf)
        return self.run()
