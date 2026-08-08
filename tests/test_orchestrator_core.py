from pathlib import Path
import pytest
from ai_sdlc.orchestration.state import StateStore, WorkflowState
from ai_sdlc.orchestration.orchestrator import Orchestrator, AgentExecutionError
from ai_sdlc.agents.base import AgentResult, AgentStatus, ArtifactRef, AgentDecision


class StubAgent:
    def __init__(self, result: AgentResult = None, raise_exc: Exception = None):
        self.result = result
        self.raise_exc = raise_exc
        self.calls = 0

    def execute(self, request):
        self.calls += 1
        if self.raise_exc:
            # allow raise_exc to be callable to vary behavior per call
            if callable(self.raise_exc):
                exc = self.raise_exc(self.calls)
                if exc:
                    raise exc
            else:
                raise self.raise_exc
        return self.result


def make_workflow(tmp_path, current_stage="requirements"):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    store = StateStore(workspace)
    wf = WorkflowState(workflow_id="wf-001", current_stage=current_stage, initiator_id="user-123")
    store.write_workflow(wf)
    return workspace, wf


def test_invoke_agent_success(tmp_path):
    workspace, wf = make_workflow(tmp_path)
    orch = Orchestrator(workspace)

    # agent returns completed
    result = AgentResult(request_id="r1", workflow_id=wf.workflow_id, agent_id="po", status=AgentStatus.COMPLETED)
    agent = StubAgent(result=result)
    orch.register_agent("po", agent)

    res = orch.invoke_agent_for_stage(wf, "po")
    assert res["status"] == "completed"

    # reload workflow and assert stage marked completed
    wf2 = orch.load_workflow()
    assert wf2.stages.get("requirements") == "completed"

    # audit file should contain agent_started and agent_completed lines
    audit_file = workspace / ".ai-sdlc" / "audit" / "events.jsonl"
    content = audit_file.read_text()
    assert "agent_started" in content
    assert "agent_completed" in content


def test_invoke_agent_needs_clarification(tmp_path):
    workspace, wf = make_workflow(tmp_path)
    orch = Orchestrator(workspace)

    result = AgentResult(
        request_id="r2",
        workflow_id=wf.workflow_id,
        agent_id="po",
        status=AgentStatus.NEEDS_CLARIFICATION,
        questions=["Which fields should be included?"],
    )
    agent = StubAgent(result=result)
    orch.register_agent("po", agent)

    res = orch.invoke_agent_for_stage(wf, "po")
    assert res["status"] == "needs_clarification"
    qid = res["question_id"]

    # clarification file exists
    clar_path = workspace / ".ai-sdlc" / "clarifications" / f"{qid}.json"
    assert clar_path.exists()

    wf2 = orch.load_workflow()
    assert wf2.status == "paused"

    audit = (workspace / ".ai-sdlc" / "audit" / "events.jsonl").read_text()
    assert "clarification_requested" in audit


def test_invoke_agent_needs_approval(tmp_path):
    workspace, wf = make_workflow(tmp_path)
    orch = Orchestrator(workspace)

    artifact = ArtifactRef(type="architecture", path=".ai-sdlc/architecture.json")
    decision = AgentDecision(status="ready_for_approval", approval_required=True)
    result = AgentResult(
        request_id="r3",
        workflow_id=wf.workflow_id,
        agent_id="architecture",
        status=AgentStatus.NEEDS_APPROVAL,
        artifact=artifact,
        decision=decision,
    )
    agent = StubAgent(result=result)
    orch.register_agent("architecture", agent)

    res = orch.invoke_agent_for_stage(wf, "architecture")
    assert res["status"] == "needs_approval"
    aid = res["approval_id"]

    # approval file exists
    apr_path = workspace / ".ai-sdlc" / "approvals" / f"{aid}.json"
    assert apr_path.exists()

    wf2 = orch.load_workflow()
    assert wf2.status == "waiting_for_approval"
    assert wf2.pending_approval is not None

    audit = (workspace / ".ai-sdlc" / "audit" / "events.jsonl").read_text()
    assert "approval_requested" in audit


def test_agent_retry_and_exhaustion(tmp_path):
    workspace, wf = make_workflow(tmp_path)
    orch = Orchestrator(workspace)

    # agent raises retryable error twice then non-retryable
    def raise_fn(call_count):
        if call_count <= 2:
            return AgentExecutionError("transient", retryable=True)
        return AgentExecutionError("perm", retryable=False)

    agent = StubAgent(raise_exc=raise_fn)
    orch.register_agent("dev", agent)

    # First invocation -> retry
    res1 = orch.invoke_agent_for_stage(wf, "dev")
    assert res1["status"] == "failed" or res1.get("status") in ("failed",)

    # After two transient errors, retry_count should be incremented
    wf2 = orch.load_workflow()
    assert wf2.retry_count.get("dev", 0) >= 1

    # Simulate attempts until exhaustion
    # invoke until workflow fails
    for _ in range(5):
        wf_latest = orch.load_workflow()
        if wf_latest.status == "failed":
            break
        orch.invoke_agent_for_stage(wf_latest, "dev")

    wf_final = orch.load_workflow()
    assert wf_final.status == "failed"

    audit = (workspace / ".ai-sdlc" / "audit" / "events.jsonl").read_text()
    assert "agent_failed" in audit
    assert "workflow_failed" in audit
