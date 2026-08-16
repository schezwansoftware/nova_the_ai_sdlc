"""Tests for the SageCapability abstraction, its mock provider, and the
local-memory helper functions.

Mirrors `tests/test_capabilities_retrieval.py`'s structure. No network
access / external credentials / real Claude/Copilot SDK required anywhere
in this file.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_sdlc.capabilities.providers.sage_mock import MockSageProvider
from ai_sdlc.capabilities.sage import (
    SageRequest,
    SageResponse,
    TerminationReason,
    normalize_context_query,
)

_REQUEST = SageRequest(
    query="What does the legacy import step do?",
    requesting_agent_id="po",
)


def test_mock_provider_defaults_to_not_found():
    provider = MockSageProvider()
    result = provider.ask(_REQUEST)

    assert isinstance(result, SageResponse)
    assert result.found is False
    assert result.answer == ""
    assert result.source_connector is None
    assert result.terminated_reason == TerminationReason.COMPLETED


def test_mock_provider_force_found_via_constructor():
    provider = MockSageProvider(
        force_found={
            "answer": "It converts CSV rows into normalized Order records.",
            "source_connector": "confluence",
            "source_url": "https://example/confluence/page",
        }
    )
    result = provider.ask(_REQUEST)

    assert result.found is True
    assert result.answer == "It converts CSV rows into normalized Order records."
    assert result.source_connector == "confluence"
    assert result.source_url == "https://example/confluence/page"


def test_mock_provider_per_call_force_found_overrides_constructor():
    provider = MockSageProvider()
    result = provider.ask(_REQUEST, force_found={"answer": "found via override", "source_connector": "jira"})

    assert result.found is True
    assert result.answer == "found via override"
    assert result.source_connector == "jira"


def test_sage_request_rejects_blank_query():
    with pytest.raises(ValidationError):
        SageRequest(query="   ", requesting_agent_id="po")


def test_sage_request_rejects_blank_requesting_agent_id():
    with pytest.raises(ValidationError):
        SageRequest(query="q", requesting_agent_id="   ")


def test_sage_request_rejects_non_positive_max_steps():
    with pytest.raises(ValidationError):
        SageRequest(query="q", requesting_agent_id="po", max_steps=0)


def test_sage_response_rejects_blank_provider_name():
    with pytest.raises(ValidationError):
        SageResponse(
            query="q",
            found=False,
            provider_name="   ",
            steps_used=0,
            terminated_reason=TerminationReason.COMPLETED,
        )


def test_sage_response_rejects_negative_steps_used():
    with pytest.raises(ValidationError):
        SageResponse(
            query="q",
            found=True,
            answer="a",
            provider_name="mock_sage",
            steps_used=-1,
            terminated_reason=TerminationReason.COMPLETED,
        )


def test_sage_response_allows_found_false_with_empty_answer():
    result = SageResponse(
        query="q",
        found=False,
        provider_name="mock_sage",
        steps_used=0,
        terminated_reason=TerminationReason.COMPLETED,
    )
    assert result.answer == ""
    assert result.source_connector is None


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("What does the legacy import step do?", "what does the legacy import step do?"),
        ("  What   does the   legacy import step do?  ", "what does the legacy import step do?"),
        ("WHAT DOES THE LEGACY IMPORT STEP DO?", "what does the legacy import step do?"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_context_query(raw, expected):
    assert normalize_context_query(raw) == expected


def test_normalize_context_query_does_not_treat_differently_phrased_equivalents_as_equal():
    """Documented, accepted limitation -- exact-match only, per the locked
    design's "cheap plain lookup, not a search index" requirement."""
    assert normalize_context_query("What does the import step do?") != normalize_context_query(
        "What is the purpose of the import step?"
    )
