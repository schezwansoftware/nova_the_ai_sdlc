from pathlib import Path
import json
from ai_sdlc.orchestration.orchestrator import Orchestrator
from ai_sdlc.orchestration.state import WorkflowState


# Helper to prepare workspace and registry metadata
def prepare_workspace(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    agents_dir = workspace / ".ai-sdlc" / "agents"
    agents_dir.mkdir(parents=True)
    metadata = {
        "agent_id": "po",
        "version": "1.0",
        "impl": "ai_sdlc.agents.po.po_agent.POAgent",
        "input_schema": "po-input-v1",
        "output_schema": "po-output-v1",
        "capabilities": ["reasoning"],
        "state_artifact": "requirements.json"
    }
    (agents_dir / "po.json").write_text(json.dumps(metadata), encoding="utf-8")
    return workspace


def test_requirement_po_completed(tmp_path):
    workspace = prepare_workspace(tmp_path)
    wf = WorkflowState(workflow_id="wf1", current_stage="requirements", initiator_id="u1")
    orch = Orchestrator(workspace)
    orch.store.write_workflow(wf)

    res = orch.run_workflow_graph(wf.workflow_id)
    assert res["status"] == "completed"


def test_requirement_po_clarify_and_resume(tmp_path):
    workspace = prepare_workspace(tmp_path)
    wf = WorkflowState(workflow_id="wf2", current_stage="requirements", initiator_id="u1")
    orch = Orchestrator(workspace)
    orch.store.write_workflow(wf)

    # invoke with clarify by passing special input (we call run_workflow_graph but po reads inputs from workflow?)
    # For this test, we will directly invoke the PO with clarify via invoke_agent_for_stage
    wf_loaded = orch.load_workflow()
    res = orch.invoke_agent_for_stage(wf_loaded, "po", inputs={"requirement_text": "clarify this"})
    assert res["status"] == "needs_clarification"
    qid = res["question_id"]

    # resume after providing answer
    resume_res = orch.resume_workflow_after_clarification(wf_loaded.workflow_id, qid, "Only selected fields")
    assert resume_res["status"] == "completed"


def test_requirement_po_approval_and_resume(tmp_path):
    workspace = prepare_workspace(tmp_path)
    wf = WorkflowState(workflow_id="wf3", current_stage="requirements", initiator_id="u1")
    orch = Orchestrator(workspace)
    orch.store.write_workflow(wf)

    wf_loaded = orch.load_workflow()
    res = orch.invoke_agent_for_stage(wf_loaded, "po", inputs={"requirement_text": "please approve this"})
    assert res["status"] == "needs_approval"
    aid = res["approval_id"]

    # Approve and resume
    resume_res = orch.resume_workflow_after_approval(wf_loaded.workflow_id, aid, "approved")
    assert resume_res["status"] == "completed"


def test_approval_acceptance_resumes_exactly_once_without_recursion(tmp_path):
    workspace = prepare_workspace(tmp_path)
    orch = Orchestrator(workspace)
    wf = WorkflowState(workflow_id="wf6", current_stage="requirements", initiator_id="u1")
    orch.store.write_workflow(wf)

    class CountingApprovalAgent:
        def __init__(self):
            self.calls = 0

        def execute(self, request):
            self.calls += 1
            from ai_sdlc.agents.base import AgentResult, AgentStatus, ArtifactRef, AgentDecision
            return AgentResult(
                request_id=request.request_id,
                workflow_id=request.workflow_id,
                agent_id="po",
                status=AgentStatus.NEEDS_APPROVAL,
                artifact=ArtifactRef(type="requirements", path=".ai-sdlc/requirements.json"),
                decision=AgentDecision(status="ready_for_approval", approval_required=True),
            )

    agent = CountingApprovalAgent()
    orch.register_agent("po", agent)

    wf_loaded = orch.load_workflow()
    res = orch.invoke_agent_for_stage(wf_loaded, "po")
    assert res["status"] == "needs_approval"
    aid = res["approval_id"]
    assert agent.calls == 1

    # Approving must resume through the normal Runner path exactly once —
    # no RecursionError — and the agent is invoked exactly once more for the
    # resumed attempt (it happens to request approval again here, which is a
    # legitimate terminal outcome for this stub, not a bug).
    resume_res = orch.resume_workflow_after_approval(wf_loaded.workflow_id, aid, "approved")
    assert agent.calls == 2
    assert resume_res["status"] == "interrupted"
    assert resume_res["type"] == "approval"


def test_retryable_failure_then_success(tmp_path):
    # We'll create a temporary agent that raises a retryable error first, then succeeds.
    workspace = prepare_workspace(tmp_path)
    orch = Orchestrator(workspace)

    # create workflow
    wf = WorkflowState(workflow_id="wf4", current_stage="requirements", initiator_id="u1")
    orch.store.write_workflow(wf)

    # register a flaky agent programmatically
    class Flaky:
        def __init__(self):
            self.calls = 0
        def execute(self, request):
            self.calls += 1
            from ai_sdlc.orchestration.orchestrator import AgentExecutionError
            if self.calls < 2:
                raise AgentExecutionError("transient", retryable=True)
            from ai_sdlc.agents.base import AgentResult, AgentStatus
            return AgentResult(request_id=request.request_id, workflow_id=request.workflow_id, agent_id="po", status=AgentStatus.COMPLETED)

    orch.register_agent("po", Flaky())

    # run graph (should attempt, get retry, then succeed)
    res = orch.run_workflow_graph(wf.workflow_id)
    # If first attempt raised a retryable error, runner may return retry; to simulate, call run again until completion
    if res.get("status") == "retry":
        res = orch.run_workflow_graph(wf.workflow_id)

    assert res["status"] == "completed"


def test_retry_exhaustion_leads_to_failure(tmp_path):
    workspace = prepare_workspace(tmp_path)
    orch = Orchestrator(workspace)
    wf = WorkflowState(workflow_id="wf5", current_stage="requirements", initiator_id="u1")
    orch.store.write_workflow(wf)

    class AlwaysFail:
        def execute(self, request):
            from ai_sdlc.orchestration.orchestrator import AgentExecutionError
            raise AgentExecutionError("perm", retryable=True)

    orch.register_agent("po", AlwaysFail())

    # invoke repeatedly until exhaustion
    res = orch.run_workflow_graph(wf.workflow_id)
    # keep invoking until failed state
    attempts = 0
    while res.get("status") != "failed" and attempts < 5:
        res = orch.run_workflow_graph(wf.workflow_id)
        attempts += 1

    assert res.get("status") == "failed"
