"""Integration test proving the MVP vertical slice works at the agent
level, through the real Orchestrator/AgentRegistry, without touching
Orion's LangGraph graph node lists (which remain Orion's territory).

Flow proven here:
    User Requirement -> Orchestrator.invoke_agent_for_stage("po") -> PO Agent
        -> structured requirements (AgentResult.data)
        -> Orchestrator.invoke_agent_for_stage("architecture") -> Architecture Agent
        -> structured architecture

Previously, `Orchestrator.invoke_agent_for_stage` never persisted
`AgentResult.data` into `WorkflowState`, so this test worked around that
gap by manually capturing PO's `AgentResult.data` at the call site and
feeding it into a directly-constructed `ArchitectureAgent`. Orion has since
fixed that (see `Orchestrator.invoke_agent_for_stage`'s `output_key`
parameter -- a COMPLETED node's `AgentResult.data` is now merged onto
`wf.inputs[output_key]`), so this test now calls both stages through
`invoke_agent_for_stage` and relies on the Orchestrator itself to thread
PO's output into Architecture's `inputs["requirements"]`, exactly as the
real graph (`DEFAULT_WORKFLOW_NODES` in `orchestration/langgraph_runner.py`)
does. This test still does not touch the graph node lists directly --
see `tests/test_workflow_full_sequence.py` for that end-to-end coverage
through the public API.
"""
from __future__ import annotations

import json

from ai_sdlc.agents.architecture.schemas import ArchitectureOutputData
from ai_sdlc.agents.base import AgentStatus
from ai_sdlc.agents.po.schemas import POAgentOutputData
from ai_sdlc.orchestration.orchestrator import Orchestrator
from ai_sdlc.orchestration.state import WorkflowState

PO_METADATA = {
    "agent_id": "po",
    "version": "1.0",
    "impl": "ai_sdlc.agents.po.po_agent.POAgent",
    "input_schema": "po-input-v1",
    "output_schema": "po-output-v1",
    "capabilities": ["reasoning"],
    "state_artifact": "requirements.json",
}

ARCHITECTURE_METADATA = {
    "agent_id": "architecture",
    "version": "1.0",
    "impl": "ai_sdlc.agents.architecture.architecture_agent.ArchitectureAgent",
    "input_schema": "architecture-input-v1",
    "output_schema": "architecture-output-v1",
    "capabilities": ["reasoning"],
    "state_artifact": "architecture.json",
}


def test_po_output_threads_into_architecture_via_orchestrator(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    agents_dir = workspace / ".ai-sdlc" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "po.json").write_text(json.dumps(PO_METADATA), encoding="utf-8")
    (agents_dir / "architecture.json").write_text(json.dumps(ARCHITECTURE_METADATA), encoding="utf-8")

    wf = WorkflowState(workflow_id="wf-flow-e2e", current_stage="requirements", initiator_id="user-1")
    orch = Orchestrator(workspace)
    orch.save_workflow(wf)

    # Registry discovery via the real AgentRegistry (no re-implementation).
    assert orch.registry.get("po") is not None
    assert orch.registry.get("architecture") is not None

    wf_loaded = orch.load_workflow()

    requirement_text = (
        "Add support for Redis caching to our order service to reduce DB "
        "load under high traffic. The system must respond within 50ms for "
        "cached hits."
    )

    # Drive PO through the real orchestrator invocation path (retry loop,
    # audit events, workflow status transitions), with output_key set the
    # same way the real graph node does, so PO's structured output gets
    # threaded onto wf.inputs["requirements"].
    res = orch.invoke_agent_for_stage(
        wf_loaded, "po", inputs={"requirement_text": requirement_text}, output_key="requirements"
    )
    assert res["status"] == "completed"
    wf_after_po = orch.load_workflow()
    assert wf_after_po.stages.get("requirements") == "completed"

    # PO's AgentResult.data must now be sitting on wf.inputs, schema-valid.
    requirements_payload = wf_after_po.inputs.get("requirements")
    assert requirements_payload is not None
    POAgentOutputData(**requirements_payload)  # schema-valid

    # Architecture stage receives that as inputs["requirements"] purely via
    # invoke_agent_for_stage's own wf.inputs-merging -- no manual feeding of
    # PO's output into the request, and no direct ArchitectureAgent()
    # construction. This is what proves the output->input threading fix.
    wf_after_po.current_stage = "architecture"
    orch.save_workflow(wf_after_po)
    arch_res = orch.invoke_agent_for_stage(wf_after_po, "architecture", output_key="architecture")
    assert arch_res["status"] == "completed"

    wf_final = orch.load_workflow()
    assert wf_final.stages.get("architecture") == "completed"
    validated_architecture = ArchitectureOutputData(**wf_final.inputs["architecture"])

    # Coherence: architecture output derives from the PO output.
    assert "Redis" in validated_architecture.tech_stack
    assert len(validated_architecture.component_changes) > 0
    assert len(validated_architecture.decisions) > 0
