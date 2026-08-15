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
from ai_sdlc.capabilities.reasoning import ReasoningCapability
from ai_sdlc.orchestration.orchestrator import AgentExecutionError


class _StubClarifyingReasoning(ReasoningCapability):
    """Simulates a real model that, after actually reasoning over a
    well-formed-but-ambiguous prompt, decides it needs to ask something --
    the case the pre-LLM `check_needs_clarification()` heuristic gate can
    never see (it only ever looks at the raw input text, before any
    reasoning call happens at all). See `framework.py`'s `needs_clarification`
    handling in `SpecialistAgent.execute()`."""

    def __init__(self, question: str):
        self.question = question

    def complete(self, prompt, *, output_schema):
        return output_schema(
            needs_clarification=True,
            clarification_question=self.question,
            feature_title="",
            summary="",
            functional_requirements=[],
            non_functional_requirements=[],
            out_of_scope=[],
            acceptance_criteria=[],
        )


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


def test_clarification_answer_resolves_ambiguity_even_though_requirement_text_is_still_present():
    """Regression test for the bug documented in todo.md: `wf.inputs` is
    cumulative and Orion never clears `requirement_text` on resume (it's a
    whole-workflow input set once at `start_workflow`, not per-node), so a
    resumed request's `inputs` dict carries *both* the original
    `requirement_text` (still the same ambiguous string) and the new
    `clarification_answer` -- exactly like `LangGraphRunner.
    resume_after_clarification` actually sends it. The agent must use the
    answer, not keep re-deciding based on the stale original text (which
    would ask the identical question forever)."""
    agent = POAgent()
    ambiguous_request = _make_request({"requirement_text": "TBD, not sure yet, figure out later."})
    first = agent.execute(ambiguous_request)
    assert first.status == AgentStatus.NEEDS_CLARIFICATION

    resumed_request = _make_request(
        {
            # Both present simultaneously, as a real resume sends it --
            # requirement_text is never cleared.
            "requirement_text": "TBD, not sure yet, figure out later.",
            "clarification_answer": (
                "Add support for Redis caching to our order service to reduce "
                "DB load under high traffic. The system must respond within "
                "50ms for cached hits."
            ),
        }
    )

    second = agent.execute(resumed_request)

    assert second.status == AgentStatus.COMPLETED
    assert second.data is not None
    validated = POAgentOutputData(**second.data)
    assert validated.feature_title


def test_model_driven_clarification_on_well_formed_but_ambiguous_input():
    """A requirement that clears the pre-LLM gate (long enough, no
    vagueness markers) but is still genuinely ambiguous -- the reasoning
    call itself must be able to raise NEEDS_CLARIFICATION, not just the
    heuristic gate before it."""
    agent = POAgent(reasoning=_StubClarifyingReasoning("Which user roles should be able to trigger this?"))
    request = _make_request(
        {"requirement_text": "Add a permissions system so certain actions require elevated access."}
    )

    result = agent.execute(request)

    assert result.status == AgentStatus.NEEDS_CLARIFICATION
    assert result.questions == ["Which user roles should be able to trigger this?"]
    assert result.data is None


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


def test_po_agent_output_data_allows_empty_fields_when_needs_clarification():
    validated = POAgentOutputData(
        needs_clarification=True,
        clarification_question="Who is the primary user of this feature?",
        feature_title="",
        summary="",
        functional_requirements=[],
        non_functional_requirements=[],
        out_of_scope=[],
        acceptance_criteria=[],
    )
    assert validated.needs_clarification is True
    assert validated.clarification_question == "Who is the primary user of this feature?"


def test_po_agent_output_data_requires_clarification_question_when_needs_clarification():
    payload = {
        "needs_clarification": True,
        "clarification_question": "   ",  # blank -- must be rejected, not silently accepted
        "feature_title": "",
        "summary": "",
        "functional_requirements": [],
        "non_functional_requirements": [],
        "out_of_scope": [],
        "acceptance_criteria": [],
    }
    with pytest.raises(ValidationError):
        POAgentOutputData(**payload)


def test_po_agent_never_raises_unhandled_exception():
    agent = POAgent()
    # Deliberately malformed inputs: no requirement_text key at all.
    request = _make_request({})
    result = agent.execute(request)
    assert result.status == AgentStatus.NEEDS_CLARIFICATION
