import pytest
from pydantic import ValidationError

from ai_sdlc.agents.po.po_agent import POAgent
from ai_sdlc.orchestration.api import (
    CancelWorkflowRequest,
    ErrorCode,
    GetWorkflowStatusRequest,
    OrchestratorAPI,
    ResumeWorkflowRequest,
    StartWorkflowRequest,
    SubmitApprovalRequest,
    SubmitClarificationRequest,
    WorkflowPhase,
    WorkflowStatusType,
)
from ai_sdlc.orchestration.state import WorkflowState


def test_start_and_get_status(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    api = OrchestratorAPI(str(workspace))
    api.orch.register_agent("po", POAgent())

    req = StartWorkflowRequest(initiator_id="u1", raw_requirement="Add export functionality for customers.", project_context={})
    resp = api.start_workflow(req)
    assert resp.success
    data = resp.data
    assert data.workflow_id

    status_req = GetWorkflowStatusRequest(workflow_id=data.workflow_id)
    status_resp = api.get_workflow_status(status_req)
    assert status_resp.success
    assert status_resp.data.workflow_id == data.workflow_id


def test_start_workflow_reports_missing_agent_error(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    api = OrchestratorAPI(str(workspace))

    req = StartWorkflowRequest(initiator_id="u1", raw_requirement="Add export functionality for customers.", project_context={})
    resp = api.start_workflow(req)
    assert not resp.success
    assert resp.error is not None
    assert resp.error.code == ErrorCode.INTERNAL_ORCHESTRATION_ERROR
    assert resp.error.details is not None
    assert resp.error.details["reason"] == "missing_agent"


def test_clarification_submit_and_resume(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    api = OrchestratorAPI(str(workspace))
    api.orch.register_agent("po", POAgent())

    wf = WorkflowState(workflow_id="wf-c", current_stage="requirements", initiator_id="u2")
    wf.status = "paused"
    wf.pending_clarification = {"question_id": "q-1234", "stage": "requirements", "question": "Please clarify", "inputs": {}}
    api.orch.store.write_workflow(wf)

    req = SubmitClarificationRequest(workflow_id=wf.workflow_id, initiator_id="u2", question_id="q-1234", response_text="Yes use CSV")
    resp = api.submit_clarification(req)
    assert resp.success
    assert resp.data.status is not None


def test_clarification_submission_requires_paused_state(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    api = OrchestratorAPI(str(workspace))

    wf = WorkflowState(workflow_id="wf-c2", current_stage="requirements", initiator_id="u2")
    wf.status = "running"
    api.orch.store.write_workflow(wf)

    req = SubmitClarificationRequest(workflow_id=wf.workflow_id, initiator_id="u2", question_id="q-unknown", response_text="Yes use CSV")
    resp = api.submit_clarification(req)
    assert not resp.success
    assert resp.error is not None
    assert resp.error.code == ErrorCode.INVALID_STATE_TRANSITION


def test_approval_submit_and_resume(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    api = OrchestratorAPI(str(workspace))
    api.orch.register_agent("po", POAgent())

    wf = WorkflowState(workflow_id="wf-a", current_stage="requirements", initiator_id="u3")
    wf.status = "waiting_for_approval"
    wf.pending_approval = {"approval_id": "approval-1234", "stage": "requirements", "artifact": {}, "inputs": {}}
    api.orch.store.write_workflow(wf)

    req = SubmitApprovalRequest(workflow_id=wf.workflow_id, initiator_id="u3", approval_id="approval-1234", approved=True)
    resp = api.submit_approval(req)
    assert resp.success


def test_approval_submission_requires_waiting_state(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    api = OrchestratorAPI(str(workspace))

    wf = WorkflowState(workflow_id="wf-a2", current_stage="requirements", initiator_id="u3")
    api.orch.store.write_workflow(wf)
    wf.status = "running"
    api.orch.save_workflow(wf)

    req = SubmitApprovalRequest(workflow_id=wf.workflow_id, initiator_id="u3", approval_id="approval-unknown", approved=True)
    resp = api.submit_approval(req)
    assert not resp.success
    assert resp.error is not None
    assert resp.error.code == ErrorCode.INVALID_STATE_TRANSITION


def test_clarification_wrong_question_id_rejected(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    api = OrchestratorAPI(str(workspace))
    api.orch.register_agent("po", POAgent())

    wf = WorkflowState(workflow_id="wf-c3", current_stage="requirements", initiator_id="u2")
    wf.status = "paused"
    wf.pending_clarification = {"question_id": "q-real1", "stage": "requirements", "question": "which fields?", "inputs": {}}
    api.orch.store.write_workflow(wf)

    req = SubmitClarificationRequest(workflow_id=wf.workflow_id, initiator_id="u2", question_id="q-wrong", response_text="Yes use CSV")
    resp = api.submit_clarification(req)
    assert not resp.success
    assert resp.error is not None


def test_clarification_missing_question_id_is_validation_failure():
    with pytest.raises(ValidationError):
        SubmitClarificationRequest(workflow_id="wf-x", initiator_id="u2", response_text="Yes use CSV")


def test_approval_wrong_approval_id_rejected(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    api = OrchestratorAPI(str(workspace))

    wf = WorkflowState(workflow_id="wf-a3", current_stage="requirements", initiator_id="u3")
    wf.status = "waiting_for_approval"
    wf.pending_approval = {"approval_id": "approval-real1", "stage": "requirements", "artifact": {}, "inputs": {}}
    api.orch.store.write_workflow(wf)

    req = SubmitApprovalRequest(workflow_id=wf.workflow_id, initiator_id="u3", approval_id="approval-wrong", approved=True)
    resp = api.submit_approval(req)
    assert not resp.success
    assert resp.error is not None


def test_approval_missing_approval_id_is_validation_failure():
    with pytest.raises(ValidationError):
        SubmitApprovalRequest(workflow_id="wf-x", initiator_id="u3", approved=True)


def test_approval_rejection_becomes_revision_required_and_does_not_invoke_runner(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    api = OrchestratorAPI(str(workspace))
    # Deliberately do NOT register any agent: if rejection incorrectly
    # invoked the runner, invoke_agent_for_stage would raise "Agent not
    # found" and this call would come back as a failure instead of
    # REVISION_REQUIRED.
    aid = "approval-rej1"
    wf = WorkflowState(workflow_id="wf-rej", current_stage="requirements", initiator_id="u5")
    wf.status = "waiting_for_approval"
    wf.pending_approval = {"approval_id": aid, "stage": "requirements", "artifact": {}, "inputs": {}}
    api.orch.store.write_workflow(wf)

    req = SubmitApprovalRequest(workflow_id=wf.workflow_id, initiator_id="u5", approval_id=aid, approved=False, feedback="not ready")
    resp = api.submit_approval(req)
    assert resp.success
    assert resp.data.status == WorkflowStatusType.REVISION_REQUIRED

    wf2 = api.orch.load_workflow()
    assert wf2.status == "revision_required"

    record = api.orch.store.read_approval(aid)
    assert record["decision"] == "rejected"
    assert record["feedback"] == "not ready"


def test_workflow_phase_mapping_for_each_stage(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    api = OrchestratorAPI(str(workspace))

    stage_to_phase = {
        "requirements": WorkflowPhase.REQUIREMENTS,
        "ux_design": WorkflowPhase.UX_DESIGN,
        "architecture": WorkflowPhase.ARCHITECTURE,
        "development": WorkflowPhase.DEVELOPMENT,
        "testing": WorkflowPhase.TESTING,
        "security": WorkflowPhase.SECURITY,
        "code_review": WorkflowPhase.CODE_REVIEW,
        "documentation": WorkflowPhase.DOCUMENTATION,
        "pull_request": WorkflowPhase.PULL_REQUEST,
    }
    for stage, phase in stage_to_phase.items():
        wf = WorkflowState(workflow_id=f"wf-{stage}", current_stage=stage, initiator_id="u")
        assert api._workflow_phase(wf) == phase

    completed_wf = WorkflowState(workflow_id="wf-done", current_stage=None, initiator_id="u")
    assert api._workflow_phase(completed_wf) == WorkflowPhase.COMPLETED


def test_workflow_phase_unknown_stage_raises(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    api = OrchestratorAPI(str(workspace))

    wf = WorkflowState(workflow_id="wf-bad", current_stage="not_a_real_stage", initiator_id="u")
    with pytest.raises(RuntimeError):
        api._workflow_phase(wf)


def test_public_status_revision_required_mapping(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    api = OrchestratorAPI(str(workspace))
    assert api._public_status("revision_required") == WorkflowStatusType.REVISION_REQUIRED


def test_get_workflow_status_unknown_internal_status_is_not_silently_running(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    api = OrchestratorAPI(str(workspace))

    wf = WorkflowState(workflow_id="wf-unknown-status", current_stage="requirements", initiator_id="u")
    api.orch.store.write_workflow(wf)
    wf.status = "some_bogus_internal_status"
    api.orch.save_workflow(wf)

    resp = api.get_workflow_status(GetWorkflowStatusRequest(workflow_id=wf.workflow_id))
    assert not resp.success
    assert resp.error.code == ErrorCode.INTERNAL_ORCHESTRATION_ERROR


def test_resume_workflow_blocked_while_waiting_for_clarification(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    api = OrchestratorAPI(str(workspace))
    api.orch.register_agent("po", POAgent())

    qid = "q-pending"
    wf = WorkflowState(
        workflow_id="wf-resume-clar",
        current_stage="requirements",
        initiator_id="u6",
        status="paused",
        pending_clarification={"question_id": qid, "stage": "requirements", "question": "which fields?", "inputs": {}},
    )
    api.orch.store.write_workflow(wf)

    resp = api.resume_workflow(ResumeWorkflowRequest(workflow_id=wf.workflow_id, initiator_id="u6"))
    assert not resp.success
    assert resp.error.code == ErrorCode.INVALID_STATE_TRANSITION

    # resume must not have touched the pending interaction
    untouched = api.orch.load_workflow(wf.workflow_id)
    assert untouched.status == "paused"
    assert untouched.pending_clarification["question_id"] == qid


def test_resume_workflow_blocked_while_waiting_for_approval(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    api = OrchestratorAPI(str(workspace))
    api.orch.register_agent("po", POAgent())

    aid = "approval-pending"
    wf = WorkflowState(
        workflow_id="wf-resume-appr",
        current_stage="requirements",
        initiator_id="u7",
        status="waiting_for_approval",
        pending_approval={"approval_id": aid, "stage": "requirements", "artifact": {}, "inputs": {}},
    )
    api.orch.store.write_workflow(wf)

    resp = api.resume_workflow(ResumeWorkflowRequest(workflow_id=wf.workflow_id, initiator_id="u7"))
    assert not resp.success
    assert resp.error.code == ErrorCode.INVALID_STATE_TRANSITION

    # resume must not have overwritten the pending approval with a new id
    untouched = api.orch.load_workflow(wf.workflow_id)
    assert untouched.status == "waiting_for_approval"
    assert untouched.pending_approval["approval_id"] == aid


def test_get_workflow_status_pending_action_uses_interaction_id_field(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    api = OrchestratorAPI(str(workspace))

    qid = "q-field-check"
    wf = WorkflowState(
        workflow_id="wf-field-check",
        current_stage="requirements",
        initiator_id="u8",
        status="paused",
        pending_clarification={"question_id": qid, "stage": "requirements", "question": "which fields?", "inputs": {}},
    )
    api.orch.store.write_workflow(wf)

    resp = api.get_workflow_status(GetWorkflowStatusRequest(workflow_id=wf.workflow_id))
    assert resp.success
    pending = resp.data.pending_action
    assert pending is not None
    # the interaction id must live in its own named field, not be smuggled
    # into payload_artifact_path (which is for actual artifact paths)
    assert pending.interaction_id == qid
    assert pending.payload_artifact_path is None


def test_resume_and_cancel(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    api = OrchestratorAPI(str(workspace))
    api.orch.register_agent("po", POAgent())

    wf = WorkflowState(workflow_id="wf-r", current_stage="requirements", initiator_id="u4")
    api.orch.store.write_workflow(wf)

    cancel_req = CancelWorkflowRequest(workflow_id=wf.workflow_id, initiator_id="u4", reason="no longer needed")
    cancel_resp = api.cancel_workflow(cancel_req)
    assert cancel_resp.success
    assert cancel_resp.data.status == WorkflowStatusType.CANCELLED

    resume_req = ResumeWorkflowRequest(workflow_id=wf.workflow_id, initiator_id="u4")
    resume_resp = api.resume_workflow(resume_req)
    assert not resume_resp.success
    assert resume_resp.error is not None
    assert resume_resp.error.code == ErrorCode.INVALID_STATE_TRANSITION
