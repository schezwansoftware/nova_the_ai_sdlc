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
    api.orch.store.write_workflow(wf)
    wf.status = "paused"
    api.orch.save_workflow(wf)

    req = SubmitClarificationRequest(workflow_id=wf.workflow_id, initiator_id="u2", response_text="Yes use CSV")
    resp = api.submit_clarification(req)
    assert resp.success
    assert resp.data.status is not None


def test_clarification_submission_requires_paused_state(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    api = OrchestratorAPI(str(workspace))

    wf = WorkflowState(workflow_id="wf-c2", current_stage="requirements", initiator_id="u2")
    api.orch.store.write_workflow(wf)
    wf.status = "running"
    api.orch.save_workflow(wf)

    req = SubmitClarificationRequest(workflow_id=wf.workflow_id, initiator_id="u2", response_text="Yes use CSV")
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
    api.orch.store.write_workflow(wf)
    wf.status = "waiting_for_approval"
    api.orch.save_workflow(wf)

    req = SubmitApprovalRequest(workflow_id=wf.workflow_id, initiator_id="u3", approved=True)
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

    req = SubmitApprovalRequest(workflow_id=wf.workflow_id, initiator_id="u3", approved=True)
    resp = api.submit_approval(req)
    assert not resp.success
    assert resp.error is not None
    assert resp.error.code == ErrorCode.INVALID_STATE_TRANSITION


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
