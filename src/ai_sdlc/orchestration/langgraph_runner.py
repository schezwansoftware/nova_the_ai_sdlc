from __future__ import annotations
from typing import List, Dict, Any, Optional

from ai_sdlc.orchestration.orchestrator import Orchestrator
from ai_sdlc.orchestration.state import WorkflowState, WorkflowStatus


# Default MVP workflow graph: PO -> Architecture -> UX design, executed
# sequentially (see docs/architecture/v1_architecture.md section 5.2's
# WorkflowPhase ordering; LangGraphRunner has no branching support today,
# so Architecture/UX -- both independent consumers of PO's output -- run
# one after another rather than in parallel).
#
# This is the single source of truth for the node list: Orchestrator's
# run_workflow_graph / resume_workflow_after_clarification /
# resume_workflow_after_approval all import this rather than hardcoding
# their own copies, so extending the workflow (e.g. adding a Development
# node next) only requires editing this one list.
#
# Each node's optional "output_key" tells Orchestrator.invoke_agent_for_stage
# where to merge that node's AgentResult.data onto wf.inputs once it
# COMPLETEs, so the next node automatically receives it as part of its
# merged inputs (see Orchestrator.invoke_agent_for_stage). Both the
# Architecture and UX agents read PO's structured output via
# request.inputs["requirements"].
DEFAULT_WORKFLOW_NODES: List[Dict[str, Any]] = [
    {"id": "requirements", "type": "agent", "agent_id": "po", "output_key": "requirements"},
    {"id": "architecture", "type": "agent", "agent_id": "architecture", "output_key": "architecture"},
    {"id": "ux_design", "type": "agent", "agent_id": "ux", "output_key": "ux_design"},
]


class LangGraphRunner:
    """A LangGraph-backed (adapter) runner that executes a simple linear graph

    For V1 the graph is kept simple and linear -- see DEFAULT_WORKFLOW_NODES
    above for the current default sequence.

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
        # nodes is a list of dicts: {"id": "po", "type": "agent", "agent_id": "po", "output_key": "requirements"}
        if nodes is None:
            self.nodes = list(DEFAULT_WORKFLOW_NODES)
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

            if nid == "ux_design" and self._architecture_says_no_ui():
                # Architecture explicitly classified this requirement as
                # having no UI surface (requires_ui: false) -- running the
                # UX Agent anyway would force it to invent screens/flows
                # for a feature that has none (e.g. a headless CLI/backend
                # change). Skip the node entirely rather than invoking it.
                self.wf.stages[nid] = "skipped"
                self.orch.save_workflow(self.wf)
                continue

            if ntype == "agent":
                agent_id = node.get("agent_id")
                node_inputs = self.inputs if i == start_index else None
                res = self.orch.invoke_agent_for_stage(
                    self.wf, agent_id, inputs=node_inputs, output_key=node.get("output_key")
                )

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

    def _architecture_says_no_ui(self) -> bool:
        """True once the Architecture stage has completed and explicitly
        classified this requirement as not needing a UI
        (`architecture.requires_ui is False`). Missing/absent/non-dict
        architecture data means "don't skip" -- the only behavior possible
        before this field existed, and the safe choice if architecture
        hasn't run yet (e.g. this check firing for a node that isn't
        actually `ux_design`, or a graph where nodes were reordered)."""
        architecture = self.wf.inputs.get("architecture")
        if not isinstance(architecture, dict):
            return False
        return architecture.get("requires_ui") is False

    def resume_after_clarification(self, answer: str, question_id: str) -> Dict[str, Any]:
        # Provide the answer as input to the current agent and continue execution.
        current_stage = self.wf.current_stage
        if not current_stage:
            return {"status": "no_active_stage"}
        # Find node for current stage
        node_index = next((i for i, n in enumerate(self.nodes) if n.get("id") == current_stage), None)
        if node_index is None:
            return {"status": "unknown_stage"}
        node = self.nodes[node_index]
        agent_id = node.get("agent_id")
        merged_inputs = self.inputs.copy() if self.inputs else {}
        merged_inputs.update({"clarification_answer": answer, "question_id": question_id})
        res = self.orch.invoke_agent_for_stage(
            self.wf, agent_id, inputs=merged_inputs, output_key=node.get("output_key")
        )
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
