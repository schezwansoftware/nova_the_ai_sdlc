"""Tests for the ReasoningCapability abstraction and its mock provider.

No network access / external credentials required anywhere in this file.
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from ai_sdlc.capabilities.reasoning import MalformedResponseError, ProviderError
from ai_sdlc.capabilities.providers.mock import MockReasoningProvider


class _DummySchema(BaseModel):
    title: str
    items: list[str]


_PROMPT = (
    "Some role/instructions boilerplate.\n"
    'Raw content:\n"""\nBuild a widget. It must be fast.\n"""\n'
)


def test_mock_provider_returns_valid_structured_output():
    provider = MockReasoningProvider()
    result = provider.complete(_PROMPT, output_schema=_DummySchema)
    assert isinstance(result, _DummySchema)
    assert result.title
    assert isinstance(result.items, list)
    assert len(result.items) > 0


def test_mock_provider_force_malformed_raises_malformed_response_error():
    provider = MockReasoningProvider(force_error="malformed")
    with pytest.raises(MalformedResponseError):
        provider.complete(_PROMPT, output_schema=_DummySchema)


def test_mock_provider_force_provider_failure_raises_provider_error():
    provider = MockReasoningProvider(force_error="provider_failure")
    with pytest.raises(ProviderError):
        provider.complete(_PROMPT, output_schema=_DummySchema)


def test_mock_provider_per_call_force_error_overrides_constructor():
    # Constructor default is "no error"; a per-call force_error still applies.
    provider = MockReasoningProvider()
    with pytest.raises(ProviderError):
        provider.complete(_PROMPT, output_schema=_DummySchema, force_error="provider_failure")

    # Constructor-level force still applies when no per-call override is given.
    failing_provider = MockReasoningProvider(force_error="provider_failure")
    with pytest.raises(ProviderError):
        failing_provider.complete(_PROMPT, output_schema=_DummySchema)


def test_mock_provider_rejects_unsupported_force_error_value():
    with pytest.raises(ValueError):
        MockReasoningProvider(force_error="not_a_real_mode")


def test_agent_code_is_provider_independent_across_two_mock_configs():
    """The same agent code path works against two differently-configured
    mock provider instances, proving the agent has zero knowledge of which
    provider configuration is in use -- it only knows the
    ReasoningCapability interface."""
    from ai_sdlc.agents.po.po_agent import POAgent
    from ai_sdlc.agents.base import AgentRequest, AgentStatus
    from ai_sdlc.orchestration.orchestrator import AgentExecutionError
    import uuid

    request = AgentRequest(
        request_id=str(uuid.uuid4()),
        workflow_id="wf-provider-independence",
        agent_id="po",
        agent_version="1.0",
        action="default",
        inputs={"requirement_text": "Add a CSV export button to the reports page."},
    )

    healthy_agent = POAgent(reasoning=MockReasoningProvider())
    result = healthy_agent.execute(request)
    assert result.status == AgentStatus.COMPLETED
    assert result.data is not None

    failing_agent = POAgent(reasoning=MockReasoningProvider(force_error="provider_failure"))
    with pytest.raises(AgentExecutionError) as exc_info:
        failing_agent.execute(request)
    assert exc_info.value.retryable is True
