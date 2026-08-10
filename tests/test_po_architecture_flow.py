"""Integration test proving the MVP vertical slice works at the agent
level, through the real Orchestrator/AgentRegistry, without touching
Orion's LangGraph graph node lists (which remain Orion's territory).

Flow proven here:
    User Requirement -> Orchestrator.invoke_agent_for_stage("po") -> PO Agent
        -> structured requirements (AgentResult.data)
        -> ArchitectureAgent().execute(...) with those requirements as input
        -> structured architecture

Note: AgentResult.data is intentionally NOT persisted into WorkflowState by
the Orchestrator (that would require editing orchestrator.py, which is out
of scope/off-limits for Craft -- see approved plan boundary #3). So this
test captures the PO agent's AgentResult.data directly from the call site,
then feeds it into a directly-constructed ArchitectureAgent -- this
simulates what Orion will wire into the real two-stage graph later.
"""
from __future__ import annotations

import json

from ai_sdlc.agents.architecture.architecture_agent import ArchitectureAgent
from ai_sdlc.agents.architecture.schemas import ArchitectureOutputData
from ai_sdlc.agents.base import AgentRequest, AgentStatus
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


def test_po_through_orchestrator_then_architecture_direct_call(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    agents_dir = workspace / ".ai-sdlc" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "po.json").write_text(json.dumps(PO_METADATA), encoding="utf-8")

    wf = WorkflowState(workflow_id="wf-flow-e2e", current_stage="requirements", initiator_id="user-1")
    orch = Orchestrator(workspace)
    orch.save_workflow(wf)

    # Registry discovery via the real AgentRegistry (no re-implementation).
    assert orch.registry.get("po") is not None

    wf_loaded = orch.load_workflow()

    # We need the PO agent's AgentResult.data, which invoke_agent_for_stage()
    # does not return (it only returns {"status": ...}). Build the exact same
    # AgentRequest the Orchestrator would build and call the registered PO
    # agent directly for this one call, so we can inspect .data, while still
    # proving discovery + orchestrator wiring above. This does not bypass or
    # reimplement the Orchestrator's retry/clarification/approval handling --
    # it only reads back a result the Orchestrator's own agent produced.
    po_agent = orch.registry.get("po")
    requirement_text = (
        "Add support for Redis caching to our order service to reduce DB "
        "load under high traffic. The system must respond within 50ms for "
        "cached hits."
    )
    request = orch._make_request(wf_loaded.workflow_id, "po", "default", {"requirement_text": requirement_text})
    po_result = po_agent.execute(request)
    assert po_result.status == AgentStatus.COMPLETED
    assert po_result.data is not None
    POAgentOutputData(**po_result.data)  # schema-valid

    # Also drive the same call through the real orchestrator invocation path
    # (retry loop, audit events, workflow status transitions) to prove the
    # PO agent is actually wired up and callable via Orion's contract.
    res = orch.invoke_agent_for_stage(wf_loaded, "po", inputs={"requirement_text": requirement_text})
    assert res["status"] == "completed"
    wf_after = orch.load_workflow()
    assert wf_after.stages.get("requirements") == "completed"

    # Feed PO's structured output into a directly-constructed ArchitectureAgent.
    # ArchitectureAgent is called directly here (agent object called directly
    # is fine per the approved plan) -- this simulates what Orion will wire
    # up later once the Architecture Agent is added to the graph node list
    # (Orion's territory, not Craft's).
    arch_agent = ArchitectureAgent()
    arch_request = AgentRequest(
        request_id="arch-req-1",
        workflow_id=wf.workflow_id,
        agent_id="architecture",
        agent_version="1.0",
        action="default",
        inputs={"requirements": po_result.data},
    )
    arch_result = arch_agent.execute(arch_request)

    assert arch_result.status == AgentStatus.COMPLETED
    assert arch_result.data is not None
    validated_architecture = ArchitectureOutputData(**arch_result.data)

    # Coherence: architecture output derives from the PO output.
    assert "Redis" in validated_architecture.tech_stack
    assert len(validated_architecture.component_changes) > 0
    assert len(validated_architecture.decisions) > 0
