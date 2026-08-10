from pathlib import Path
import json
from ai_sdlc.orchestration.orchestrator import Orchestrator
from ai_sdlc.orchestration.state import WorkflowState


def test_po_agent_discovery_and_invoke(tmp_path):
    # Prepare workspace and registry metadata
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

    # Create initial workflow
    wf = WorkflowState(workflow_id="wf-e2e", current_stage="requirements", initiator_id="user-1")
    store = Orchestrator(workspace).store
    store.write_workflow(wf)

    orch = Orchestrator(workspace)

    # Ensure registry discovered the PO agent
    agent = orch.registry.get("po")
    assert agent is not None

    # Invoke PO agent with a requirement that should complete
    wf_loaded = orch.load_workflow()
    res = orch.invoke_agent_for_stage(wf_loaded, "po", inputs={"requirement_text": "Add export feature"})
    assert res["status"] == "completed"

    wf_after = orch.load_workflow()
    assert wf_after.stages.get("requirements") == "completed"

    # Invoke PO agent with text that triggers clarification. The real
    # POAgent's ambiguity heuristics (short text / vagueness markers) are
    # exercised directly in tests/test_po_agent.py; here we use the
    # documented `force` test hook to deterministically exercise the
    # Orchestrator's needs_clarification handling path regardless of the
    # real agent's heuristics.
    wf_after.current_stage = "requirements"
    store.write_workflow(wf_after)
    res2 = orch.invoke_agent_for_stage(
        wf_after, "po", inputs={"requirement_text": "Add export feature", "force": "clarify"}
    )
    assert res2["status"] == "needs_clarification"

    # Invoke PO agent with the documented `force` hook that triggers
    # approval. The real POAgent has no approval-gating logic of its own
    # (Orion owns approval workflow progression); this hook exists purely
    # to exercise the Orchestrator's existing needs_approval handling path.
    wf_latest = orch.load_workflow()
    wf_latest.current_stage = "requirements"
    store.write_workflow(wf_latest)
    res3 = orch.invoke_agent_for_stage(
        wf_latest, "po", inputs={"requirement_text": "Add export feature", "force": "approval"}
    )
    assert res3["status"] == "needs_approval"
