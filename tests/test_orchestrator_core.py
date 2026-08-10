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


def test_state_store_supports_multiple_concurrent_workflows(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    store = StateStore(workspace)

    wf_a = WorkflowState(workflow_id="wf-A", current_stage="requirements", initiator_id="alice")
    wf_b = WorkflowState(workflow_id="wf-B", current_stage="requirements", initiator_id="bob")
    store.write_workflow(wf_a)
    store.write_workflow(wf_b)

    # Writing a second workflow must not clobber the first — each remains
    # independently readable by its own workflow_id.
    loaded_a = store.read_workflow("wf-A")
    loaded_b = store.read_workflow("wf-B")
    assert loaded_a is not None and loaded_a.workflow_id == "wf-A" and loaded_a.initiator_id == "alice"
    assert loaded_b is not None and loaded_b.workflow_id == "wf-B" and loaded_b.initiator_id == "bob"


class AutoCompleteAgent:
    """Minimal stub agent that always completes immediately, with no
    structured data. Used to stand in for the Architecture/UX stages in
    tests that are only exercising PO-stage clarification/retry mechanics
    (the real graph now runs Architecture/UX after PO -- see
    DEFAULT_WORKFLOW_NODES in orchestration/langgraph_runner.py -- so
    those stages need *some* registered agent for a resume-to-completion
    call to actually reach COMPLETED)."""

    def execute(self, request):
        return AgentResult(
            request_id=request.request_id,
            workflow_id=request.workflow_id,
            agent_id=request.agent_id,
            status=AgentStatus.COMPLETED,
        )


class ClarifyThenCompleteAgent:
    def __init__(self):
        self.calls = 0

    def execute(self, request):
        self.calls += 1
        if self.calls == 1:
            return AgentResult(
                request_id=request.request_id,
                workflow_id=request.workflow_id,
                agent_id="po",
                status=AgentStatus.NEEDS_CLARIFICATION,
                questions=["Which fields?"],
            )
        return AgentResult(request_id=request.request_id, workflow_id=request.workflow_id, agent_id="po", status=AgentStatus.COMPLETED)


def test_clarification_resume_to_completion_invokes_agent_exactly_once(tmp_path):
    workspace, wf = make_workflow(tmp_path)
    orch = Orchestrator(workspace)
    agent = ClarifyThenCompleteAgent()
    orch.register_agent("po", agent)
    orch.register_agent("architecture", AutoCompleteAgent())
    orch.register_agent("ux", AutoCompleteAgent())

    res = orch.invoke_agent_for_stage(wf, "po")
    assert res["status"] == "needs_clarification"
    qid = res["question_id"]
    assert agent.calls == 1

    resume_res = orch.resume_workflow_after_clarification(wf.workflow_id, qid, "Only selected fields")
    assert resume_res["status"] == "completed"
    # exactly one more invocation for the resumed attempt — not a duplicate
    assert agent.calls == 2


class RecordingClarifyTwiceAgent:
    def __init__(self):
        self.calls = 0
        self.received_inputs = []

    def execute(self, request):
        self.calls += 1
        self.received_inputs.append(dict(request.inputs))
        if self.calls <= 2:
            return AgentResult(
                request_id=request.request_id,
                workflow_id=request.workflow_id,
                agent_id="po",
                status=AgentStatus.NEEDS_CLARIFICATION,
                questions=[f"Question {self.calls}?"],
            )
        return AgentResult(request_id=request.request_id, workflow_id=request.workflow_id, agent_id="po", status=AgentStatus.COMPLETED)


def test_clarification_answers_accumulate_across_rounds(tmp_path):
    workspace, wf = make_workflow(tmp_path)
    orch = Orchestrator(workspace)
    agent = RecordingClarifyTwiceAgent()
    orch.register_agent("po", agent)
    orch.register_agent("architecture", AutoCompleteAgent())
    orch.register_agent("ux", AutoCompleteAgent())

    res1 = orch.invoke_agent_for_stage(wf, "po")
    assert res1["status"] == "needs_clarification"
    qid1 = res1["question_id"]

    resume1 = orch.resume_workflow_after_clarification(wf.workflow_id, qid1, "Answer one")
    assert resume1["status"] == "needs_clarification"
    qid2 = resume1["question_id"]

    resume2 = orch.resume_workflow_after_clarification(wf.workflow_id, qid2, "Answer two")
    assert resume2["status"] == "completed"

    # No duplicate invocations across the two rounds either.
    assert agent.calls == 3

    # The final call's inputs must carry BOTH prior answers, not just the
    # latest one.
    final_inputs = agent.received_inputs[-1]
    answers = final_inputs.get("clarification_answers", {})
    assert answers.get(qid1) == "Answer one"
    assert answers.get(qid2) == "Answer two"


def test_agent_failure_then_retry_then_success(tmp_path):
    workspace, wf = make_workflow(tmp_path)
    orch = Orchestrator(workspace)

    result = AgentResult(request_id="r", workflow_id=wf.workflow_id, agent_id="dev", status=AgentStatus.COMPLETED)

    def raise_fn(call_count):
        if call_count == 1:
            return AgentExecutionError("transient", retryable=True)
        return None

    agent = StubAgent(result=result, raise_exc=raise_fn)
    orch.register_agent("dev", agent)

    res = orch.invoke_agent_for_stage(wf, "dev")
    assert res["status"] == "completed"
    assert agent.calls == 2

    wf2 = orch.load_workflow()
    assert wf2.status == "running"
    assert wf2.retry_count.get("dev", 0) == 0


def test_non_retryable_failure_stops_after_one_attempt(tmp_path):
    workspace, wf = make_workflow(tmp_path)
    orch = Orchestrator(workspace)

    agent = StubAgent(raise_exc=AgentExecutionError("permanent", retryable=False))
    orch.register_agent("dev", agent)

    res = orch.invoke_agent_for_stage(wf, "dev")
    assert res["status"] == "failed"
    assert agent.calls == 1

    wf2 = orch.load_workflow()
    assert wf2.status == "failed"


def test_retry_never_exceeds_max_attempts(tmp_path):
    workspace, wf = make_workflow(tmp_path)
    orch = Orchestrator(workspace)

    agent = StubAgent(raise_exc=lambda call_count: AgentExecutionError("always transient", retryable=True))
    orch.register_agent("dev", agent)

    res = orch.invoke_agent_for_stage(wf, "dev")
    assert res["status"] == "failed"
    # agent invoked at most max_attempts times, never more
    assert agent.calls == orch.max_attempts

    wf2 = orch.load_workflow()
    assert wf2.status == "failed"


def test_pending_clarification_and_question_id_survive_persistence(tmp_path):
    workspace, wf = make_workflow(tmp_path)
    orch = Orchestrator(workspace)

    result = AgentResult(
        request_id="r",
        workflow_id=wf.workflow_id,
        agent_id="po",
        status=AgentStatus.NEEDS_CLARIFICATION,
        questions=["Which fields?"],
    )
    orch.register_agent("po", StubAgent(result=result))

    res = orch.invoke_agent_for_stage(wf, "po")
    qid = res["question_id"]

    # A brand new Orchestrator/StateStore instance must see the same state —
    # proves it round-trips through disk, not just in-memory objects.
    orch2 = Orchestrator(workspace)
    wf2 = orch2.load_workflow()
    assert wf2.pending_clarification is not None
    assert wf2.pending_clarification["question_id"] == qid

    record = orch2.store.read_clarification(qid)
    assert record is not None
    assert record["question_id"] == qid


def test_clarification_answer_persists_on_disk(tmp_path):
    workspace, wf = make_workflow(tmp_path)
    orch = Orchestrator(workspace)

    result = AgentResult(
        request_id="r",
        workflow_id=wf.workflow_id,
        agent_id="po",
        status=AgentStatus.NEEDS_CLARIFICATION,
        questions=["Which fields?"],
    )
    orch.register_agent("po", StubAgent(result=result))

    res = orch.invoke_agent_for_stage(wf, "po")
    qid = res["question_id"]

    orch.resume_workflow_after_clarification(wf.workflow_id, qid, "Only the selected fields")

    orch2 = Orchestrator(workspace)
    record = orch2.store.read_clarification(qid)
    assert record["answer"] == "Only the selected fields"


def test_approval_decision_and_feedback_persist_on_rejection(tmp_path):
    workspace, wf = make_workflow(tmp_path)
    orch = Orchestrator(workspace)

    artifact = ArtifactRef(type="requirements", path=".ai-sdlc/requirements.json")
    decision = AgentDecision(status="ready_for_approval", approval_required=True)
    result = AgentResult(
        request_id="r",
        workflow_id=wf.workflow_id,
        agent_id="po",
        status=AgentStatus.NEEDS_APPROVAL,
        artifact=artifact,
        decision=decision,
    )
    orch.register_agent("po", StubAgent(result=result))

    res = orch.invoke_agent_for_stage(wf, "po")
    aid = res["approval_id"]

    resume_res = orch.resume_workflow_after_approval(wf.workflow_id, aid, "rejected", feedback="needs more detail")
    assert resume_res == {"status": "rejected"}

    orch2 = Orchestrator(workspace)
    record = orch2.store.read_approval(aid)
    assert record["decision"] == "rejected"
    assert record["feedback"] == "needs more detail"

    wf2 = orch2.load_workflow()
    assert wf2.status == "revision_required"


def test_approval_invalid_id_is_rejected(tmp_path):
    workspace, wf = make_workflow(tmp_path)
    orch = Orchestrator(workspace)

    artifact = ArtifactRef(type="requirements", path=".ai-sdlc/requirements.json")
    decision = AgentDecision(status="ready_for_approval", approval_required=True)
    result = AgentResult(
        request_id="r",
        workflow_id=wf.workflow_id,
        agent_id="po",
        status=AgentStatus.NEEDS_APPROVAL,
        artifact=artifact,
        decision=decision,
    )
    orch.register_agent("po", StubAgent(result=result))
    orch.invoke_agent_for_stage(wf, "po")

    with pytest.raises(RuntimeError):
        orch.resume_workflow_after_approval(wf.workflow_id, "not-the-real-id", "approved")


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
