"""Integration test proving the requirements -> UX vertical slice works at
the agent level, through the real Orchestrator/AgentRegistry, without
touching Orion's LangGraph graph node lists (which remain Orion's
territory).

Flow proven here:
    User Requirement -> Orchestrator.invoke_agent_for_stage("po") -> PO Agent
        -> structured requirements (AgentResult.data)
        -> UXAgent().execute(...) with those requirements as input
        -> structured UX design

Note: AgentResult.data is intentionally NOT persisted into WorkflowState by
the Orchestrator (that would require editing orchestrator.py, which is out
of scope/off-limits for Craft -- see approved plan boundary #3, restated for
the UX increment). So this test captures the PO agent's AgentResult.data
directly from the call site, then feeds it into a directly-constructed
UXAgent -- this simulates what Orion will wire into the real graph later.
"""
from __future__ import annotations

import json

from ai_sdlc.agents.base import AgentRequest, AgentStatus
from ai_sdlc.agents.po.schemas import POAgentOutputData
from ai_sdlc.agents.ux.schemas import UXOutputData
from ai_sdlc.agents.ux.ux_agent import UXAgent
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


def test_po_through_orchestrator_then_ux_direct_call(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    agents_dir = workspace / ".ai-sdlc" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "po.json").write_text(json.dumps(PO_METADATA), encoding="utf-8")

    wf = WorkflowState(workflow_id="wf-ux-flow-e2e", current_stage="requirements", initiator_id="user-1")
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
        "Add a CSV export button to the reports page so users can download "
        "the current report as a file. The export must complete within 2 "
        "seconds for typical report sizes."
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

    # Feed PO's structured output into a directly-constructed UXAgent.
    # UXAgent is called directly here (agent object called directly is fine
    # per the approved plan) -- this simulates what Orion will wire up later
    # once the UX Agent is added to the graph node list (Orion's territory,
    # not Craft's).
    ux_agent = UXAgent()
    ux_request = AgentRequest(
        request_id="ux-req-1",
        workflow_id=wf.workflow_id,
        agent_id="ux",
        agent_version="1.0",
        action="default",
        inputs={"requirements": po_result.data},
    )
    ux_result = ux_agent.execute(ux_request)

    assert ux_result.status == AgentStatus.COMPLETED
    assert ux_result.data is not None
    validated_ux = UXOutputData(**ux_result.data)

    # Coherence: UX output derives from the PO output, not a fabricated
    # fixture -- assert on structure rather than a specific keyword since
    # this requirement (unlike the Redis example) has no single distinctive
    # technology token, but the flows/screens must be non-trivially derived
    # from the actual PO requirements text fed in.
    assert len(validated_ux.user_flows) > 0
    assert len(validated_ux.screens) > 0
    assert len(validated_ux.accessibility_considerations) > 0
    assert any("export" in flow.lower() for flow in validated_ux.user_flows)
