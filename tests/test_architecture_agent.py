"""Tests for the Architecture Agent.

No network access / external credentials required.
"""
from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from ai_sdlc.agents.architecture.architecture_agent import ArchitectureAgent
from ai_sdlc.agents.architecture.schemas import ArchitectureOutputData
from ai_sdlc.agents.base import AgentRequest, AgentStatus
from ai_sdlc.agents.po.po_agent import POAgent
from ai_sdlc.capabilities.providers.mock import MockReasoningProvider
from ai_sdlc.capabilities.providers.retrieval_mock import MockRetrievalProvider
from ai_sdlc.orchestration.orchestrator import AgentExecutionError


def _make_request(inputs, workflow_id="wf-arch-test"):
    return AgentRequest(
        request_id=str(uuid.uuid4()),
        workflow_id=workflow_id,
        agent_id="architecture",
        agent_version="1.0",
        action="default",
        inputs=inputs,
    )


_SAMPLE_REQUIREMENTS = {
    "feature_title": "Redis Cache Integration for Order Service",
    "summary": "Implement Redis-backed caching for order retrieval endpoints.",
    "functional_requirements": [
        "System shall: Cache GET /orders/{id} responses in Redis.",
    ],
    "non_functional_requirements": [
        "System shall satisfy: Order retrieval response time under 50ms for cached hits.",
    ],
    "out_of_scope": ["Distributed session management."],
    "acceptance_criteria": ["Verify that: cache hit ratio is measurable via metrics endpoint."],
}


def test_valid_requirements_produce_valid_architecture_output():
    agent = ArchitectureAgent()
    request = _make_request({"requirements": _SAMPLE_REQUIREMENTS})

    result = agent.execute(request)

    assert result.status == AgentStatus.COMPLETED
    assert result.data is not None
    validated = ArchitectureOutputData(**result.data)
    assert len(validated.tech_stack) > 0
    assert len(validated.component_changes) > 0
    assert len(validated.decisions) > 0
    assert validated.rationale


def test_missing_requirements_needs_clarification():
    agent = ArchitectureAgent()
    request = _make_request({})

    result = agent.execute(request)

    assert result.status == AgentStatus.NEEDS_CLARIFICATION
    assert result.questions
    assert result.data is None


def test_empty_requirements_dict_needs_clarification():
    agent = ArchitectureAgent()
    request = _make_request({"requirements": {}})

    result = agent.execute(request)

    assert result.status == AgentStatus.NEEDS_CLARIFICATION


def test_forced_malformed_provider_output_raises_retryable_agent_execution_error():
    agent = ArchitectureAgent(reasoning=MockReasoningProvider(force_error="malformed"))
    request = _make_request({"requirements": _SAMPLE_REQUIREMENTS})

    with pytest.raises(AgentExecutionError) as exc_info:
        agent.execute(request)

    assert exc_info.value.retryable is True


def test_forced_provider_failure_raises_retryable_agent_execution_error():
    agent = ArchitectureAgent(reasoning=MockReasoningProvider(force_error="provider_failure"))
    request = _make_request({"requirements": _SAMPLE_REQUIREMENTS})

    with pytest.raises(AgentExecutionError) as exc_info:
        agent.execute(request)

    assert exc_info.value.retryable is True


def test_architecture_output_data_rejects_missing_required_field():
    payload = {
        "tech_stack": ["Redis"],
        "component_changes": ["Add cache layer."],
        "decisions": ["Use Redis for caching."],
        # rationale intentionally omitted
        "risks": [],
    }
    with pytest.raises(ValidationError):
        ArchitectureOutputData(**payload)


def test_architecture_output_data_rejects_empty_required_list():
    payload = {
        "tech_stack": [],
        "component_changes": ["Add cache layer."],
        "decisions": ["Use Redis for caching."],
        "rationale": "Improves latency.",
        "risks": [],
    }
    with pytest.raises(ValidationError):
        ArchitectureOutputData(**payload)


def test_no_workspace_path_means_no_retrieval_call_and_unchanged_prompt():
    """Backward compatibility: every caller/test that predates
    RetrievalCapability wiring never sets `target_repository`, so this
    must produce the exact same behavior as before -- no retrieval call
    at all, not even a failing one."""
    agent = ArchitectureAgent(
        retrieval=MockRetrievalProvider(force_error="provider_failure")
    )
    request = _make_request({"requirements": _SAMPLE_REQUIREMENTS})

    # If retrieval were called, force_error="provider_failure" would raise
    # ProviderError -> AgentExecutionError. Success here proves it wasn't.
    result = agent.execute(request)

    assert result.status == AgentStatus.COMPLETED


def test_workspace_path_present_triggers_retrieval_and_grounds_prompt():
    agent = ArchitectureAgent(retrieval=MockRetrievalProvider())
    request = _make_request(
        {
            "requirements": _SAMPLE_REQUIREMENTS,
            "target_repository": {"workspace_path": "/abs/path/to/order-service"},
        }
    )

    prompt = agent.build_prompt(request)

    assert "/abs/path/to/order-service" in prompt
    assert "Relevant existing codebase context" in prompt
    assert _SAMPLE_REQUIREMENTS["feature_title"] in prompt


def test_retrieval_failure_with_workspace_path_raises_agent_execution_error():
    agent = ArchitectureAgent(
        retrieval=MockRetrievalProvider(force_error="provider_failure")
    )
    request = _make_request(
        {
            "requirements": _SAMPLE_REQUIREMENTS,
            "target_repository": {"workspace_path": "/abs/path/to/order-service"},
        }
    )

    with pytest.raises(AgentExecutionError):
        agent.execute(request)


def test_requirements_to_architecture_flow_is_coherent():
    """PO Agent output data feeds directly into the Architecture Agent,
    with no direct agent-to-agent call -- ArchitectureAgent never imports
    or invokes POAgent; it only reads request.inputs["requirements"]."""
    po_agent = POAgent()
    po_request = _make_request(
        {
            "requirement_text": (
                "Add support for Redis caching to our order service to reduce "
                "DB load under high traffic."
            )
        },
        workflow_id="wf-flow",
    )
    po_result = po_agent.execute(po_request)
    assert po_result.status == AgentStatus.COMPLETED

    arch_agent = ArchitectureAgent()
    arch_request = _make_request({"requirements": po_result.data}, workflow_id="wf-flow")
    arch_result = arch_agent.execute(arch_request)

    assert arch_result.status == AgentStatus.COMPLETED
    validated = ArchitectureOutputData(**arch_result.data)
    # coherence: architecture output should reference/derive from the PO
    # output (e.g. detect "Redis" as a required piece of tech stack, since
    # it was explicitly named in the requirement).
    assert "Redis" in validated.tech_stack
