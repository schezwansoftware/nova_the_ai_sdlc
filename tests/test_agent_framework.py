"""Tests for the shared specialist-agent framework: registry discovery,
zero-arg instantiation, and the SpecialistAgent.execute() contract.

No network access / external credentials required.
"""
from __future__ import annotations

import json
import uuid

from ai_sdlc.agents.architecture.architecture_agent import ArchitectureAgent
from ai_sdlc.agents.base import AgentRequest, AgentResult, AgentStatus
from ai_sdlc.agents.po.po_agent import POAgent
from ai_sdlc.agents.registry import AgentRegistry
from ai_sdlc.agents.ux.ux_agent import UXAgent
from ai_sdlc.capabilities.providers.mock import MockReasoningProvider
from ai_sdlc.orchestration.orchestrator import AgentExecutionError


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

UX_METADATA = {
    "agent_id": "ux",
    "version": "1.0",
    "impl": "ai_sdlc.agents.ux.ux_agent.UXAgent",
    "input_schema": "ux-input-v1",
    "output_schema": "ux-output-v1",
    "capabilities": ["reasoning"],
    "state_artifact": "ux.json",
}


def _write_metadata(workspace, metadata):
    agents_dir = workspace / ".ai-sdlc" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{metadata['agent_id']}.json").write_text(json.dumps(metadata), encoding="utf-8")


def test_registry_discovers_and_zero_arg_instantiates_po_agent(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _write_metadata(workspace, PO_METADATA)

    registry = AgentRegistry(workspace)

    agent = registry.get("po")
    assert agent is not None
    assert isinstance(agent, POAgent)
    assert registry.get_metadata("po") == PO_METADATA


def test_registry_discovers_and_zero_arg_instantiates_architecture_agent(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _write_metadata(workspace, ARCHITECTURE_METADATA)

    registry = AgentRegistry(workspace)

    agent = registry.get("architecture")
    assert agent is not None
    assert isinstance(agent, ArchitectureAgent)
    assert registry.get_metadata("architecture") == ARCHITECTURE_METADATA


def test_registry_discovers_and_zero_arg_instantiates_ux_agent(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _write_metadata(workspace, UX_METADATA)

    registry = AgentRegistry(workspace)

    agent = registry.get("ux")
    assert agent is not None
    assert isinstance(agent, UXAgent)
    assert registry.get_metadata("ux") == UX_METADATA


def test_registry_discovers_both_agents_simultaneously(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _write_metadata(workspace, PO_METADATA)
    _write_metadata(workspace, ARCHITECTURE_METADATA)

    registry = AgentRegistry(workspace)

    assert isinstance(registry.get("po"), POAgent)
    assert isinstance(registry.get("architecture"), ArchitectureAgent)


def test_registry_discovers_all_three_agents_simultaneously(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _write_metadata(workspace, PO_METADATA)
    _write_metadata(workspace, ARCHITECTURE_METADATA)
    _write_metadata(workspace, UX_METADATA)

    registry = AgentRegistry(workspace)

    assert isinstance(registry.get("po"), POAgent)
    assert isinstance(registry.get("architecture"), ArchitectureAgent)
    assert isinstance(registry.get("ux"), UXAgent)


def test_specialist_agent_execute_never_raises_unhandled_exception_on_success():
    agent = POAgent()
    request = AgentRequest(
        request_id=str(uuid.uuid4()),
        workflow_id="wf-framework-test",
        agent_id="po",
        agent_version="1.0",
        action="default",
        inputs={"requirement_text": "Add a CSV export button to the reports page."},
    )
    result = agent.execute(request)
    assert isinstance(result, AgentResult)
    assert result.status == AgentStatus.COMPLETED


def test_specialist_agent_execute_converts_provider_failure_to_agent_execution_error():
    agent = POAgent(reasoning=MockReasoningProvider(force_error="provider_failure"))
    request = AgentRequest(
        request_id=str(uuid.uuid4()),
        workflow_id="wf-framework-test",
        agent_id="po",
        agent_version="1.0",
        action="default",
        inputs={"requirement_text": "Add a CSV export button to the reports page."},
    )
    try:
        agent.execute(request)
        assert False, "expected AgentExecutionError"
    except AgentExecutionError as exc:
        assert exc.retryable is True
    except Exception as exc:  # pragma: no cover - explicit failure path
        assert False, f"unexpected unhandled exception type: {type(exc)}: {exc}"


def test_agents_remain_zero_arg_constructible_directly():
    # AgentRegistry._load_impl instantiates with cls(); confirm this works
    # without going through the registry too.
    po = POAgent()
    arch = ArchitectureAgent()
    ux = UXAgent()
    assert po.agent_id == "po"
    assert arch.agent_id == "architecture"
    assert ux.agent_id == "ux"
