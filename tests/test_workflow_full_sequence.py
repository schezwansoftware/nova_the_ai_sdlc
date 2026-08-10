"""End-to-end integration tests driving the full PO -> Architecture ->
UX_design workflow graph through the real public Orchestrator API only
(`OrchestratorAPI` in `orchestration/api.py`): `start_workflow()`,
`get_workflow_status()`, `submit_clarification()`, `submit_approval()`. No
agent is ever called directly and no LangGraph/graph-node internals
(`LangGraphRunner`, `invoke_agent_for_stage`, node dicts) are touched from
the test -- this is exactly the perspective Core/Pixel/CLI have on the
system.

Craft's existing flow tests (`test_po_architecture_flow.py`,
`test_po_ux_flow.py`) call agents directly or drive individual
`invoke_agent_for_stage()` calls; they don't exercise `start_workflow()`'s
real 3-node graph end-to-end, and that's deliberate -- graph wiring is
Orion's territory, not Craft's. This file is that missing coverage:

  1. Happy path: PO -> Architecture -> UX_design all complete, with each
     stage's structured output threaded into the next via
     `wf.inputs[output_key]`.
  2. A clarification interrupt firing on the *second* node (Architecture,
     not just PO) and `submit_clarification()` correctly resuming into the
     *third* node (UX_design) afterward.
  3. An approval interrupt firing on the *third* node (UX_design) and
     `submit_approval()` correctly resuming and completing the workflow.
"""
from __future__ import annotations

from pathlib import Path

from ai_sdlc.agents.architecture.architecture_agent import ArchitectureAgent
from ai_sdlc.agents.base import AgentDecision, AgentResult, AgentStatus, ArtifactRef
from ai_sdlc.agents.po.po_agent import POAgent
from ai_sdlc.agents.ux.ux_agent import UXAgent
from ai_sdlc.orchestration.api import (
    GetWorkflowStatusRequest,
    OrchestratorAPI,
    StartWorkflowRequest,
    SubmitApprovalRequest,
    SubmitClarificationRequest,
    WorkflowPhase,
    WorkflowStatusType,
)

_REQUIREMENT_TEXT = (
    "Add support for Redis caching to our order service to reduce DB load "
    "under high traffic. The system must respond within 50ms for cached hits."
)


def _make_api(tmp_path: Path) -> OrchestratorAPI:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    return OrchestratorAPI(str(workspace))


class _InterruptOnceThenCompleteAgent:
    """Stub specialist agent: NEEDS_CLARIFICATION or NEEDS_APPROVAL on the
    first call, COMPLETED with structured `data` on the second. Used to
    deterministically exercise a mid-sequence HITL interrupt on the
    Architecture/UX nodes without depending on the real (mock) reasoning
    provider's heuristics ever landing on that branch."""

    def __init__(self, agent_id: str, interrupt: str):
        assert interrupt in ("clarification", "approval")
        self.agent_id = agent_id
        self.interrupt = interrupt
        self.calls = 0

    def execute(self, request):
        self.calls += 1
        if self.calls == 1:
            if self.interrupt == "clarification":
                return AgentResult(
                    request_id=request.request_id,
                    workflow_id=request.workflow_id,
                    agent_id=self.agent_id,
                    status=AgentStatus.NEEDS_CLARIFICATION,
                    questions=[f"{self.agent_id}: please clarify before proceeding."],
                )
            return AgentResult(
                request_id=request.request_id,
                workflow_id=request.workflow_id,
                agent_id=self.agent_id,
                status=AgentStatus.NEEDS_APPROVAL,
                artifact=ArtifactRef(type=self.agent_id, path=f".ai-sdlc/{self.agent_id}.json"),
                decision=AgentDecision(status="ready_for_approval", approval_required=True),
            )
        return AgentResult(
            request_id=request.request_id,
            workflow_id=request.workflow_id,
            agent_id=self.agent_id,
            status=AgentStatus.COMPLETED,
            data={"stage": self.agent_id, "resolved": True},
        )


def test_start_workflow_runs_po_architecture_ux_to_completion(tmp_path):
    api = _make_api(tmp_path)
    api.orch.register_agent("po", POAgent())
    api.orch.register_agent("architecture", ArchitectureAgent())
    api.orch.register_agent("ux", UXAgent())

    resp = api.start_workflow(
        StartWorkflowRequest(initiator_id="u1", raw_requirement=_REQUIREMENT_TEXT, project_context={})
    )
    assert resp.success, resp.error
    workflow_id = resp.data.workflow_id
    assert resp.data.status == WorkflowStatusType.COMPLETED
    assert resp.data.current_phase == WorkflowPhase.COMPLETED

    status_resp = api.get_workflow_status(GetWorkflowStatusRequest(workflow_id=workflow_id))
    assert status_resp.success
    assert status_resp.data.status == WorkflowStatusType.COMPLETED
    assert status_resp.data.artifacts.get("requirements") == "completed"
    assert status_resp.data.artifacts.get("architecture") == "completed"
    assert status_resp.data.artifacts.get("ux_design") == "completed"

    # Structured output actually threaded through each stage's inputs, not
    # just status flags -- this is the output->input threading fix.
    wf = api.orch.load_workflow(workflow_id)
    assert isinstance(wf.inputs.get("requirements"), dict)
    assert isinstance(wf.inputs.get("architecture"), dict)
    assert isinstance(wf.inputs.get("ux_design"), dict)
    assert "Redis" in wf.inputs["architecture"]["tech_stack"]


def test_clarification_mid_sequence_resumes_into_next_node(tmp_path):
    api = _make_api(tmp_path)
    api.orch.register_agent("po", POAgent())
    arch_stub = _InterruptOnceThenCompleteAgent("architecture", "clarification")
    api.orch.register_agent("architecture", arch_stub)
    api.orch.register_agent("ux", UXAgent())

    resp = api.start_workflow(
        StartWorkflowRequest(initiator_id="u2", raw_requirement=_REQUIREMENT_TEXT, project_context={})
    )
    assert resp.success, resp.error
    workflow_id = resp.data.workflow_id

    # PO already completed; the interrupt fires on the *second* node
    # (Architecture), proving mid-sequence interrupts work, not just on
    # the first node.
    assert resp.data.status == WorkflowStatusType.WAITING_FOR_CLARIFICATION
    assert resp.data.current_phase == WorkflowPhase.ARCHITECTURE
    assert arch_stub.calls == 1

    wf_paused = api.orch.load_workflow(workflow_id)
    assert wf_paused.stages.get("requirements") == "completed"
    assert isinstance(wf_paused.inputs.get("requirements"), dict)

    status_resp = api.get_workflow_status(GetWorkflowStatusRequest(workflow_id=workflow_id))
    assert status_resp.success
    pending = status_resp.data.pending_action
    assert pending is not None
    assert pending.action_type == "CLARIFICATION"
    question_id = pending.interaction_id

    clar_resp = api.submit_clarification(
        SubmitClarificationRequest(
            workflow_id=workflow_id,
            initiator_id="u2",
            question_id=question_id,
            response_text="Use a modular monolith with a dedicated cache layer.",
        )
    )
    assert clar_resp.success, clar_resp.error

    # Resuming re-invokes Architecture (now COMPLETED) and correctly
    # advances into UX_design next, which also completes -- proving
    # resume_workflow_after_clarification resumes into the *next* node,
    # not just re-completing the interrupted one.
    assert clar_resp.data.status == WorkflowStatusType.COMPLETED
    assert clar_resp.data.current_phase == WorkflowPhase.COMPLETED
    assert arch_stub.calls == 2

    wf_final = api.orch.load_workflow(workflow_id)
    assert wf_final.stages.get("architecture") == "completed"
    assert wf_final.stages.get("ux_design") == "completed"
    assert wf_final.inputs.get("architecture") == {"stage": "architecture", "resolved": True}


def test_approval_mid_sequence_resumes_and_completes_workflow(tmp_path):
    api = _make_api(tmp_path)
    api.orch.register_agent("po", POAgent())
    api.orch.register_agent("architecture", ArchitectureAgent())
    ux_stub = _InterruptOnceThenCompleteAgent("ux", "approval")
    api.orch.register_agent("ux", ux_stub)

    resp = api.start_workflow(
        StartWorkflowRequest(initiator_id="u3", raw_requirement=_REQUIREMENT_TEXT, project_context={})
    )
    assert resp.success, resp.error
    workflow_id = resp.data.workflow_id

    # PO and Architecture already completed; the interrupt fires on the
    # *third* node (UX_design).
    assert resp.data.status == WorkflowStatusType.WAITING_FOR_APPROVAL
    assert resp.data.current_phase == WorkflowPhase.UX_DESIGN
    assert ux_stub.calls == 1

    wf_paused = api.orch.load_workflow(workflow_id)
    assert wf_paused.stages.get("requirements") == "completed"
    assert wf_paused.stages.get("architecture") == "completed"
    assert isinstance(wf_paused.inputs.get("architecture"), dict)

    status_resp = api.get_workflow_status(GetWorkflowStatusRequest(workflow_id=workflow_id))
    assert status_resp.success
    pending = status_resp.data.pending_action
    assert pending is not None
    assert pending.action_type == "APPROVAL"
    approval_id = pending.interaction_id

    approve_resp = api.submit_approval(
        SubmitApprovalRequest(workflow_id=workflow_id, initiator_id="u3", approval_id=approval_id, approved=True)
    )
    assert approve_resp.success, approve_resp.error
    assert approve_resp.data.status == WorkflowStatusType.COMPLETED
    assert approve_resp.data.current_phase == WorkflowPhase.COMPLETED
    assert ux_stub.calls == 2

    wf_final = api.orch.load_workflow(workflow_id)
    assert wf_final.stages.get("ux_design") == "completed"
    assert wf_final.inputs.get("ux_design") == {"stage": "ux", "resolved": True}


def test_clarification_on_first_node_resolves_instead_of_looping_forever(tmp_path):
    """Regression test for the bug documented in `todo.md`: a workflow's
    *first* node (PO) requesting its own clarification used to be
    unresolvable through the real public API -- `submit_clarification()`
    would return `success: true` but the workflow came right back with a
    new `question_id` and the exact same question, forever, because PO's
    `_effective_text` preferred `requirement_text` (never cleared by Orion
    on resume) over the user's `clarification_answer`. Drives this through
    `start_workflow`/`submit_clarification` only -- no stub agent -- to
    prove the real PO agent's ambiguity heuristic and Orion's resume
    semantics now cooperate correctly."""
    api = _make_api(tmp_path)
    api.orch.register_agent("po", POAgent())
    api.orch.register_agent("architecture", ArchitectureAgent())
    api.orch.register_agent("ux", UXAgent())

    resp = api.start_workflow(
        StartWorkflowRequest(
            initiator_id="u5", raw_requirement="TBD, not sure yet, figure out later.", project_context={}
        )
    )
    assert resp.success, resp.error
    workflow_id = resp.data.workflow_id
    assert resp.data.status == WorkflowStatusType.WAITING_FOR_CLARIFICATION
    assert resp.data.current_phase == WorkflowPhase.REQUIREMENTS

    status_resp = api.get_workflow_status(GetWorkflowStatusRequest(workflow_id=workflow_id))
    first_question_id = status_resp.data.pending_action.interaction_id

    clar_resp = api.submit_clarification(
        SubmitClarificationRequest(
            workflow_id=workflow_id,
            initiator_id="u5",
            question_id=first_question_id,
            response_text=(
                "Add support for Redis caching to our order service to reduce "
                "DB load under high traffic. The system must respond within "
                "50ms for cached hits."
            ),
        )
    )
    assert clar_resp.success, clar_resp.error

    # The bug: this would come back WAITING_FOR_CLARIFICATION again, on the
    # same "requirements" phase, with a brand-new question_id but the
    # identical question -- an infinite loop. Fixed: PO accepts the answer
    # and the graph advances all the way through Architecture/UX.
    assert clar_resp.data.status == WorkflowStatusType.COMPLETED
    assert clar_resp.data.current_phase == WorkflowPhase.COMPLETED

    wf_final = api.orch.load_workflow(workflow_id)
    assert wf_final.stages.get("requirements") == "completed"
    assert wf_final.stages.get("architecture") == "completed"
    assert wf_final.stages.get("ux_design") == "completed"
    assert wf_final.pending_clarification is None


def test_approval_rejection_mid_sequence_does_not_advance_past_interrupted_node(tmp_path):
    api = _make_api(tmp_path)
    api.orch.register_agent("po", POAgent())
    api.orch.register_agent("architecture", ArchitectureAgent())
    ux_stub = _InterruptOnceThenCompleteAgent("ux", "approval")
    api.orch.register_agent("ux", ux_stub)

    resp = api.start_workflow(
        StartWorkflowRequest(initiator_id="u4", raw_requirement=_REQUIREMENT_TEXT, project_context={})
    )
    assert resp.success, resp.error
    workflow_id = resp.data.workflow_id
    assert resp.data.status == WorkflowStatusType.WAITING_FOR_APPROVAL

    status_resp = api.get_workflow_status(GetWorkflowStatusRequest(workflow_id=workflow_id))
    approval_id = status_resp.data.pending_action.interaction_id

    reject_resp = api.submit_approval(
        SubmitApprovalRequest(
            workflow_id=workflow_id,
            initiator_id="u4",
            approval_id=approval_id,
            approved=False,
            feedback="Needs another pass on accessibility.",
        )
    )
    assert reject_resp.success, reject_resp.error
    assert reject_resp.data.status == WorkflowStatusType.REVISION_REQUIRED
    # Rejection must not have re-invoked the agent or advanced the graph.
    assert ux_stub.calls == 1

    wf = api.orch.load_workflow(workflow_id)
    assert wf.status == "revision_required"
    assert wf.stages.get("ux_design") is None
