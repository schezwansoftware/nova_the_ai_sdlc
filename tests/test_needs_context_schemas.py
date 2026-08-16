"""Tests for the `needs_context`/`context_query` field pair added to
`POAgentOutputData`/`ArchitectureOutputData`/`UXOutputData` (Sage Phase 2
Knowledge Consumption -- see `orchestration/orchestrator.py`'s
NEEDS_CONTEXT handling), and the corresponding `MockReasoningProvider`
test hook that exercises it deterministically.

Mirrors the existing `needs_clarification` test coverage each schema
already has (see `tests/test_po_agent.py`), just for the sibling flag.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_sdlc.agents.architecture.schemas import ArchitectureOutputData
from ai_sdlc.agents.po.schemas import POAgentOutputData
from ai_sdlc.agents.ux.schemas import UXOutputData
from ai_sdlc.capabilities.providers.mock import MockReasoningProvider

_PO_DOMAIN_FIELDS = dict(
    feature_title="Redis caching",
    summary="Add Redis caching to the order service.",
    functional_requirements=["System shall cache orders."],
    non_functional_requirements=["System shall respond within 50ms."],
    out_of_scope=[],
    acceptance_criteria=["Verify that cached reads are fast."],
)

_ARCHITECTURE_DOMAIN_FIELDS = dict(
    tech_stack=["Python", "Redis"],
    component_changes=["Add a caching layer."],
    decisions=["Use Redis for the cache."],
    rationale="Redis is fast and already used elsewhere.",
    risks=[],
)

_UX_DOMAIN_FIELDS = dict(
    flow_title="Cached order lookup",
    summary="A faster order lookup flow.",
    user_flows=["User views an order and sees it load quickly."],
    screens=["Order detail screen."],
    accessibility_considerations=["Ensure keyboard navigation works."],
)


@pytest.mark.parametrize(
    "schema_cls, domain_fields",
    [
        (POAgentOutputData, _PO_DOMAIN_FIELDS),
        (ArchitectureOutputData, _ARCHITECTURE_DOMAIN_FIELDS),
        (UXOutputData, _UX_DOMAIN_FIELDS),
    ],
)
class TestNeedsContextAcrossAllThreeSchemas:
    def test_defaults_to_false_and_none(self, schema_cls, domain_fields):
        instance = schema_cls(**domain_fields)
        assert instance.needs_context is False
        assert instance.context_query is None

    def test_needs_context_true_with_query_is_valid_and_skips_domain_validation(
        self, schema_cls, domain_fields
    ):
        empty_domain = {k: ("" if isinstance(v, str) else []) for k, v in domain_fields.items()}
        instance = schema_cls(
            needs_context=True,
            context_query="What does the existing import step do?",
            **empty_domain,
        )
        assert instance.needs_context is True
        assert instance.context_query == "What does the existing import step do?"

    def test_needs_context_true_without_query_is_rejected(self, schema_cls, domain_fields):
        with pytest.raises(ValidationError):
            schema_cls(needs_context=True, context_query=None, **domain_fields)

    def test_needs_context_true_with_blank_query_is_rejected(self, schema_cls, domain_fields):
        with pytest.raises(ValidationError):
            schema_cls(needs_context=True, context_query="   ", **domain_fields)

    def test_needs_clarification_and_needs_context_both_true_is_rejected(
        self, schema_cls, domain_fields
    ):
        with pytest.raises(ValidationError):
            schema_cls(
                needs_clarification=True,
                clarification_question="A question?",
                needs_context=True,
                context_query="A context question?",
                **domain_fields,
            )

    def test_needs_clarification_alone_is_still_valid(self, schema_cls, domain_fields):
        empty_domain = {k: ("" if isinstance(v, str) else []) for k, v in domain_fields.items()}
        instance = schema_cls(
            needs_clarification=True,
            clarification_question="A real question?",
            **empty_domain,
        )
        assert instance.needs_clarification is True
        assert instance.needs_context is False


# -- MockReasoningProvider's trigger_needs_context test hook --------------------


def test_mock_provider_trigger_needs_context_via_constructor():
    provider = MockReasoningProvider(trigger_needs_context=True)
    result = provider.complete(
        '"""Add Redis caching to the order service."""', output_schema=POAgentOutputData
    )
    assert result.needs_context is True
    assert result.context_query
    assert result.needs_clarification is False


def test_mock_provider_trigger_needs_context_per_call_overrides_constructor():
    provider = MockReasoningProvider()
    result = provider.complete(
        '"""Add Redis caching to the order service."""',
        output_schema=POAgentOutputData,
        trigger_needs_context=True,
    )
    assert result.needs_context is True


def test_mock_provider_default_never_triggers_needs_context():
    provider = MockReasoningProvider()
    result = provider.complete(
        '"""Add Redis caching to the order service."""', output_schema=POAgentOutputData
    )
    assert result.needs_context is False
    assert result.context_query is None


def test_mock_provider_trigger_needs_context_works_for_architecture_and_ux_too():
    provider = MockReasoningProvider(trigger_needs_context=True)

    arch_result = provider.complete(
        '"""tech_stack: Python\n- decisions: use Redis"""', output_schema=ArchitectureOutputData
    )
    assert arch_result.needs_context is True

    ux_result = provider.complete('"""A UX flow."""', output_schema=UXOutputData)
    assert ux_result.needs_context is True
