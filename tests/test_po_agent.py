"""Tests for the real (LLM-abstracted) PO Agent.

No network access / external credentials required.
"""
from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from ai_sdlc.agents.base import AgentRequest, AgentStatus
from ai_sdlc.agents.po.po_agent import POAgent
from ai_sdlc.agents.po.schemas import POAgentOutputData
from ai_sdlc.capabilities.providers.mock import MockReasoningProvider
from ai_sdlc.orchestration.orchestrator import AgentExecutionError


def _make_request(inputs, workflow_id="wf-po-test"):
    return AgentRequest(
        request_id=str(uuid.uuid4()),
        workflow_id=workflow_id,
        agent_id="po",
        agent_version="1.0",
        action="default",
        inputs=inputs,
    )


def test_normal_requirement_completes_with_valid_structured_output():
    agent = POAgent()
    request = _make_request(
        {
            "requirement_text": (
                "Add support for Redis caching to our order service to reduce "
                "DB load under high traffic. The system must respond within "
                "50ms for cached hits."
            )
        }
    )

    result = agent.execute(request)

    assert result.status == AgentStatus.COMPLETED
    assert result.data is not None
    # round-trips through the real schema without error
    validated = POAgentOutputData(**result.data)
    assert validated.feature_title
    assert validated.summary
    assert len(validated.functional_requirements) > 0
    assert len(validated.non_functional_requirements) > 0
    assert len(validated.acceptance_criteria) > 0


def test_short_ambiguous_requirement_needs_clarification():
    agent = POAgent()
    request = _make_request({"requirement_text": "TBD"})

    result = agent.execute(request)

    assert result.status == AgentStatus.NEEDS_CLARIFICATION
    assert result.questions
    assert result.questions[0]
    assert result.data is None


def test_vagueness_marker_triggers_clarification_even_if_long_enough():
    agent = POAgent()
    request = _make_request(
        {"requirement_text": "We need something but honestly it's not sure what yet, figure out later."}
    )

    result = agent.execute(request)

    assert result.status == AgentStatus.NEEDS_CLARIFICATION
    assert result.questions


def test_force_clarify_hook():
    agent = POAgent()
    request = _make_request({"requirement_text": "Add export feature", "force": "clarify"})

    result = agent.execute(request)

    assert result.status == AgentStatus.NEEDS_CLARIFICATION
    assert result.questions


def test_force_approval_hook():
    agent = POAgent()
    request = _make_request({"requirement_text": "Add export feature", "force": "approval"})

    result = agent.execute(request)

    assert result.status == AgentStatus.NEEDS_APPROVAL
    assert result.decision is not None
    assert result.decision.approval_required is True


def test_forced_malformed_provider_output_raises_retryable_agent_execution_error():
    agent = POAgent(reasoning=MockReasoningProvider(force_error="malformed"))
    request = _make_request(
        {"requirement_text": "Add a CSV export button to the reports page for finance users."}
    )

    with pytest.raises(AgentExecutionError) as exc_info:
        agent.execute(request)

    assert exc_info.value.retryable is True


def test_forced_provider_failure_raises_retryable_agent_execution_error():
    agent = POAgent(reasoning=MockReasoningProvider(force_error="provider_failure"))
    request = _make_request(
        {"requirement_text": "Add a CSV export button to the reports page for finance users."}
    )

    with pytest.raises(AgentExecutionError) as exc_info:
        agent.execute(request)

    assert exc_info.value.retryable is True


def test_po_agent_output_data_rejects_missing_required_field():
    payload = {
        "feature_title": "Export button",
        "summary": "Add an export button.",
        "functional_requirements": ["Add export button to reports page."],
        "non_functional_requirements": ["Must be fast."],
        "out_of_scope": [],
        # acceptance_criteria intentionally omitted
    }
    with pytest.raises(ValidationError):
        POAgentOutputData(**payload)


def test_po_agent_output_data_rejects_empty_required_list():
    payload = {
        "feature_title": "Export button",
        "summary": "Add an export button.",
        "functional_requirements": [],
        "non_functional_requirements": ["Must be fast."],
        "out_of_scope": [],
        "acceptance_criteria": ["Verify export works."],
    }
    with pytest.raises(ValidationError):
        POAgentOutputData(**payload)


def test_po_agent_never_raises_unhandled_exception():
    agent = POAgent()
    # Deliberately malformed inputs: no requirement_text key at all.
    request = _make_request({})
    result = agent.execute(request)
    assert result.status == AgentStatus.NEEDS_CLARIFICATION
