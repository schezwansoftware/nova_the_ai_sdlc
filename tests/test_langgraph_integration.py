from pathlib import Path
import json
from ai_sdlc.orchestration.orchestrator import Orchestrator
from ai_sdlc.orchestration.state import WorkflowState
from tests.conftest import init_git_repo


# Helper to prepare workspace and registry metadata
def prepare_workspace(tmp_path):
    workspace = tmp_path / "repo"
    # The real workflow graph's fourth node (Development) always creates a
    # real isolated git worktree (see ai_sdlc.agents.developer.worktree),
    # regardless of which CodingCapability provider is configured -- so
    # `workspace` itself must be a real git repository for a full run to
    # reach that node successfully, not just a plain directory.
    init_git_repo(workspace)
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

    # The real workflow graph now also runs Architecture and UX after PO
    # (see DEFAULT_WORKFLOW_NODES in orchestration/langgraph_runner.py), so
    # a full run needs both discoverable too, or it will stop at
    # "Agent not found" once it reaches those nodes.
    architecture_metadata = {
        "agent_id": "architecture",
        "version": "1.0",
        "impl": "ai_sdlc.agents.architecture.architecture_agent.ArchitectureAgent",
        "input_schema": "architecture-input-v1",
        "output_schema": "architecture-output-v1",
        "capabilities": ["reasoning"],
        "state_artifact": "architecture.json",
    }
    (agents_dir / "architecture.json").write_text(json.dumps(architecture_metadata), encoding="utf-8")
    ux_metadata = {
        "agent_id": "ux",
        "version": "1.0",
        "impl": "ai_sdlc.agents.ux.ux_agent.UXAgent",
        "input_schema": "ux-input-v1",
        "output_schema": "ux-output-v1",
        "capabilities": ["reasoning", "design"],
        "state_artifact": "ux.json",
    }
    (agents_dir / "ux.json").write_text(json.dumps(ux_metadata), encoding="utf-8")

    developer_metadata = {
        "agent_id": "developer",
        "version": "1.0",
        "impl": "ai_sdlc.agents.developer.developer_agent.DeveloperAgent",
        "input_schema": "developer-input-v1",
        "output_schema": "developer-output-v1",
        "capabilities": ["coding"],
        "state_artifact": "implementation.json",
    }
    (agents_dir / "developer.json").write_text(json.dumps(developer_metadata), encoding="utf-8")
    return workspace


def test_requirement_po_completed(tmp_path):
    workspace = prepare_workspace(tmp_path)
    # run_workflow_graph's own `inputs=` param (used below) is only ever
    # threaded to the *first* node (LangGraphRunner.run: `node_inputs =
    # self.inputs if i == start_index else None`) -- invoke_agent_for_stage's
    # COMPLETED branch persists a node's output_key-keyed result data onto
    # wf.inputs, but not the rest of that call's merged_inputs, so anything
    # not threaded via an output_key (like target_repository) would vanish
    # once "requirements" completes if passed only that way. The real flow
    # (OrchestratorAPI.start_workflow) avoids this by setting inputs
    # directly on the WorkflowState instead -- matched here.
    wf = WorkflowState(
        workflow_id="wf1",
        current_stage="requirements",
        initiator_id="u1",
        inputs={"target_repository": {"workspace_path": str(workspace)}},
    )
    orch = Orchestrator(workspace)
    orch.store.write_workflow(wf)

    # The real POAgent needs concrete, non-ambiguous requirement text to
    # complete (see tests/test_po_agent.py for its ambiguity heuristics).
    res = orch.run_workflow_graph(
        wf.workflow_id,
        inputs={"requirement_text": "Add a CSV export button to the reports page for finance users."},
    )
    # The graph now runs all the way through Architecture/UX/Development --
    # Development always interrupts for human approval once it succeeds
    # (it never auto-completes; see DeveloperAgent's module docstring),
    # so a full run's natural terminus is an approval interrupt on
    # "development", not "completed".
    assert res["status"] == "interrupted"
    assert res["type"] == "approval"


def test_requirement_po_clarify_and_resume(tmp_path):
    workspace = prepare_workspace(tmp_path)
    wf = WorkflowState(workflow_id="wf2", current_stage="requirements", initiator_id="u1")
    orch = Orchestrator(workspace)
    orch.store.write_workflow(wf)

    # Use the PO Agent's documented `force` test hook to deterministically
    # exercise the needs_clarification path via invoke_agent_for_stage.
    wf_loaded = orch.load_workflow()
    res = orch.invoke_agent_for_stage(
        wf_loaded,
        "po",
        inputs={
            "requirement_text": "Add export feature",
            "force": "clarify",
            "target_repository": {"workspace_path": str(workspace)},
        },
    )
    assert res["status"] == "needs_clarification"
    qid = res["question_id"]

    # invoke_agent_for_stage now persists this call's merged inputs onto
    # wf.inputs (so real per-call data, like PO's structured requirements,
    # threads forward to the next graph node) -- as a side effect the
    # test-only `force: "clarify"` hook would otherwise persist too and
    # re-trigger clarification forever on resume. A real clarification
    # round never carries a "force" flag, so clear it before resuming,
    # exactly as a real caller's inputs wouldn't have included it.
    wf_paused = orch.load_workflow(wf_loaded.workflow_id)
    wf_paused.inputs.pop("force", None)
    orch.save_workflow(wf_paused)

    # resume after providing answer. The graph then runs Architecture/UX/
    # Development -- Development always interrupts for approval once it
    # succeeds rather than auto-completing (see DeveloperAgent's module
    # docstring), so that's the real terminus of a full run now.
    resume_res = orch.resume_workflow_after_clarification(wf_loaded.workflow_id, qid, "Only selected fields")
    assert resume_res["status"] == "interrupted"
    assert resume_res["type"] == "approval"


def test_requirement_po_approval_and_resume(tmp_path):
    workspace = prepare_workspace(tmp_path)
    wf = WorkflowState(workflow_id="wf3", current_stage="requirements", initiator_id="u1")
    orch = Orchestrator(workspace)
    orch.store.write_workflow(wf)

    # Use the PO Agent's documented `force` test hook to deterministically
    # exercise the needs_approval path via invoke_agent_for_stage. The real
    # POAgent has no approval-gating logic of its own; Orion owns approval
    # workflow progression. The hook now runs POAgent's real flow first and
    # only overrides a COMPLETED result's status (see po_agent.py), since
    # approval-resume no longer re-invokes the requesting agent (see below)
    # -- so the real requirements data produced here is what must carry
    # forward to Architecture/UX after approval, not anything regenerated
    # by a second PO call.
    wf_loaded = orch.load_workflow()
    wf_loaded.inputs = {
        "requirement_text": "Add export feature for finance users.",
        "target_repository": {"workspace_path": str(workspace)},
    }
    orch.save_workflow(wf_loaded)
    res = orch.invoke_agent_for_stage(wf_loaded, "po", inputs={"force": "approval"})
    assert res["status"] == "needs_approval"
    aid = res["approval_id"]

    # Approve and resume. LangGraphRunner.resume_after_approval merges the
    # approved PO result's data onto wf.inputs["requirements"] and advances
    # straight to "architecture" -- it does not re-invoke POAgent, so no
    # `force` hook cleanup is needed here (contrast with the clarification
    # test above, which does still need it).
    resume_res = orch.resume_workflow_after_approval(wf_loaded.workflow_id, aid, "approved")
    assert resume_res["status"] == "interrupted"
    assert resume_res["type"] == "approval"


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
                # Real, final data attached to the approval request itself
                # -- the requesting agent is never re-invoked to produce
                # this "later" (see LangGraphRunner.resume_after_approval),
                # so it must be complete before approval is even requested.
                data={"feature_name": "test feature", "user_stories": []},
                artifact=ArtifactRef(type="requirements", path=".ai-sdlc/requirements.json"),
                decision=AgentDecision(status="ready_for_approval", approval_required=True),
            )

    agent = CountingApprovalAgent()
    orch.register_agent("po", agent)

    wf_loaded = orch.load_workflow()
    res = orch.invoke_agent_for_stage(
        wf_loaded, "po", inputs={"target_repository": {"workspace_path": str(workspace)}}
    )
    assert res["status"] == "needs_approval"
    aid = res["approval_id"]
    assert agent.calls == 1

    # Approving must resume through the normal Runner path exactly once —
    # no RecursionError — and must NOT re-invoke the agent that already
    # produced the approved result: its call count stays at 1. The approved
    # data merges onto wf.inputs["requirements"] and the workflow advances
    # through the real Architecture/UX/Development stages (mock reasoning/
    # design/coding providers satisfy them deterministically), ending in a
    # second, distinct approval interrupt on "development" -- its own
    # NEEDS_APPROVAL, not a recursion of the first.
    resume_res = orch.resume_workflow_after_approval(wf_loaded.workflow_id, aid, "approved")
    assert agent.calls == 1
    assert resume_res["status"] == "interrupted"
    assert resume_res["type"] == "approval"


def test_retryable_failure_then_success(tmp_path):
    # We'll create a temporary agent that raises a retryable error first, then succeeds.
    workspace = prepare_workspace(tmp_path)
    orch = Orchestrator(workspace)

    # create workflow
    wf = WorkflowState(
        workflow_id="wf4",
        current_stage="requirements",
        initiator_id="u1",
        inputs={"target_repository": {"workspace_path": str(workspace)}},
    )
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
            # Return schema-valid structured data (not just a bare
            # COMPLETED status): the real graph now runs Architecture/UX
            # after this node, both of which require
            # inputs["requirements"] (threaded forward from this node's
            # AgentResult.data via output_key) to be a populated dict, or
            # they'd request clarification instead of completing.
            return AgentResult(
                request_id=request.request_id,
                workflow_id=request.workflow_id,
                agent_id="po",
                status=AgentStatus.COMPLETED,
                data={
                    "feature_title": "Export Feature",
                    "summary": "Add an export capability for customers.",
                    "functional_requirements": ["System shall allow customers to export their data."],
                    "non_functional_requirements": ["System shall respond within acceptable latency."],
                    "out_of_scope": [],
                    "acceptance_criteria": ["User can trigger and download an export."],
                },
            )

    orch.register_agent("po", Flaky())

    # run graph (should attempt, get retry, then succeed)
    res = orch.run_workflow_graph(wf.workflow_id)
    # If first attempt raised a retryable error, runner may return retry; to simulate, call run again until completion
    if res.get("status") == "retry":
        res = orch.run_workflow_graph(wf.workflow_id)

    # Development always interrupts for approval once it succeeds rather
    # than auto-completing (see DeveloperAgent's module docstring) -- that
    # interrupt, reached only after Flaky's retry succeeded and Architecture/
    # UX/Development all ran, is what proves the retry-then-success path
    # still drives the whole graph forward correctly.
    assert res["status"] == "interrupted"
    assert res["type"] == "approval"


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
