from __future__ import annotations
from typing import List, Dict, Any, Optional

from ai_sdlc.orchestration.orchestrator import Orchestrator
from ai_sdlc.orchestration.state import WorkflowState, WorkflowStatus


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

    def __init__(self, orchestrator: Orchestrator, workflow: WorkflowState, nodes: Optional[List[Dict[str, Any]]] = None, inputs: Optional[Dict[str, Any]] = None):
        self.orch = orchestrator
        self.wf = workflow
        self.inputs = inputs or {}
        # nodes is a list of dicts: {"id": "po", "type": "agent", "agent_id": "po"}
        if nodes is None:
            self.nodes = [{"id": "po", "type": "agent", "agent_id": "po"}]
        else:
            self.nodes = nodes

    def run(self, start_index: Optional[int] = None) -> Dict[str, Any]:
        # Execute nodes sequentially from the current_stage position, unless
        # the caller already knows exactly which index to resume from (used
        # by resume_after_clarification to avoid re-matching current_stage,
        # which still points at the node it just finished).
        if start_index is None:
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
                node_inputs = self.inputs if i == start_index else None
                res = self.orch.invoke_agent_for_stage(self.wf, agent_id, inputs=node_inputs)

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
                    # Orchestrator is authoritative for retry logic. Propagate failure details upstream.
                    return {"status": "failed", "details": res}

                if status == "retry":
                    # orchestrator already incremented retry and saved state; stop to allow external retry or re-run
                    return {"status": "retry", "details": res}

                # fallback
                return {"status": "unknown", "result": res}

        # all nodes completed
        self.wf.current_stage = None
        self.wf.status = WorkflowStatus.COMPLETED
        self.orch.save_workflow(self.wf)
        self.orch._emit({"event": "workflow_completed", "workflow_id": self.wf.workflow_id})
        return {"status": "completed"}

    def resume_after_clarification(self, answer: str, question_id: str) -> Dict[str, Any]:
        # Provide the answer as input to the current agent and continue execution.
        current_stage = self.wf.current_stage
        if not current_stage:
            return {"status": "no_active_stage"}
        # Find node for current stage
        node_index = next((i for i, n in enumerate(self.nodes) if n.get("id") == current_stage), None)
        if node_index is None:
            return {"status": "unknown_stage"}
        agent_id = self.nodes[node_index].get("agent_id")
        merged_inputs = self.inputs.copy() if self.inputs else {}
        merged_inputs.update({"clarification_answer": answer, "question_id": question_id})
        res = self.orch.invoke_agent_for_stage(self.wf, agent_id, inputs=merged_inputs)
        if res.get("status") != "completed":
            return res

        # This node is done. Advance past it before continuing, rather than
        # calling run() with current_stage still pointing at the node we
        # just finished — otherwise run() would re-match this same stage
        # and invoke the agent a second time for one resume.
        next_index = node_index + 1
        if next_index >= len(self.nodes):
            self.wf.current_stage = None
            self.wf.status = WorkflowStatus.COMPLETED
            self.orch.save_workflow(self.wf)
            self.orch._emit({"event": "workflow_completed", "workflow_id": self.wf.workflow_id})
            return {"status": "completed"}

        self.wf.current_stage = self.nodes[next_index].get("id")
        self.orch.save_workflow(self.wf)
        return self.run(start_index=next_index)
