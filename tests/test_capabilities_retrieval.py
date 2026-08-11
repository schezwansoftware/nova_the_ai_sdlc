"""Tests for the RetrievalCapability abstraction and its mock provider.

Mirrors `tests/test_capabilities_coding.py`'s structure. No network
access / external credentials / real Claude Agent SDK required anywhere
in this file.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_sdlc.capabilities.providers.retrieval_mock import MockRetrievalProvider
from ai_sdlc.capabilities.retrieval import (
    ContextSnippet,
    MalformedResponseError,
    ProviderError,
    RetrievalRequest,
    RetrievalResult,
    TerminationReason,
)

_REQUEST = RetrievalRequest(
    query="How does the order cache get invalidated?",
    repository_path="/tmp/fake-repo",
    scope_paths=["src/order_service/cache.py", "src/order_service/service.py"],
)


def test_mock_provider_returns_valid_structured_result():
    provider = MockRetrievalProvider()
    result = provider.retrieve(_REQUEST)

    assert isinstance(result, RetrievalResult)
    assert result.provider_name
    assert result.query == _REQUEST.query
    assert result.terminated_reason == TerminationReason.COMPLETED


def test_mock_provider_derives_one_snippet_per_scope_path():
    provider = MockRetrievalProvider()
    result = provider.retrieve(_REQUEST)

    assert len(result.snippets) == len(_REQUEST.scope_paths)
    assert {s.source_path for s in result.snippets} == set(_REQUEST.scope_paths)


def test_mock_provider_falls_back_to_default_scope_when_none_given():
    request = _REQUEST.model_copy(update={"scope_paths": []})
    provider = MockRetrievalProvider()
    result = provider.retrieve(request)

    assert len(result.snippets) == 1


def test_mock_provider_is_deterministic_for_same_request():
    provider = MockRetrievalProvider()
    first = provider.retrieve(_REQUEST)
    second = provider.retrieve(_REQUEST)

    assert first.context_summary == second.context_summary
    assert [s.content for s in first.snippets] == [s.content for s in second.snippets]


def test_mock_provider_force_malformed_raises_malformed_response_error():
    provider = MockRetrievalProvider(force_error="malformed")
    with pytest.raises(MalformedResponseError):
        provider.retrieve(_REQUEST)


def test_mock_provider_force_provider_failure_raises_provider_error():
    provider = MockRetrievalProvider(force_error="provider_failure")
    with pytest.raises(ProviderError):
        provider.retrieve(_REQUEST)


def test_mock_provider_per_call_force_error_overrides_constructor():
    provider = MockRetrievalProvider()
    with pytest.raises(ProviderError):
        provider.retrieve(_REQUEST, force_error="provider_failure")

    failing_provider = MockRetrievalProvider(force_error="provider_failure")
    with pytest.raises(ProviderError):
        failing_provider.retrieve(_REQUEST)


def test_mock_provider_rejects_unsupported_force_error_value():
    with pytest.raises(ValueError):
        MockRetrievalProvider(force_error="not_a_real_mode")


def test_retrieval_request_rejects_blank_query():
    with pytest.raises(ValidationError):
        RetrievalRequest(query="   ", repository_path="/tmp/fake-repo")


def test_retrieval_request_rejects_blank_repository_path():
    with pytest.raises(ValidationError):
        RetrievalRequest(query="What does this do?", repository_path="   ")


def test_retrieval_request_rejects_non_positive_max_context_tokens():
    with pytest.raises(ValidationError):
        RetrievalRequest(
            query="q", repository_path="/tmp/fake-repo", max_context_tokens=0
        )


def test_retrieval_request_rejects_non_positive_max_steps():
    with pytest.raises(ValidationError):
        RetrievalRequest(query="q", repository_path="/tmp/fake-repo", max_steps=0)


def test_context_snippet_rejects_blank_content():
    with pytest.raises(ValidationError):
        ContextSnippet(source_path="a.py", content="   ")


def test_retrieval_result_rejects_blank_context_summary():
    with pytest.raises(ValidationError):
        RetrievalResult(
            query="q",
            context_summary="   ",
            provider_name="mock_retrieval_provider",
            steps_used=1,
            terminated_reason=TerminationReason.COMPLETED,
        )


def test_retrieval_result_rejects_negative_steps_used():
    with pytest.raises(ValidationError):
        RetrievalResult(
            query="q",
            context_summary="Some summary.",
            provider_name="mock_retrieval_provider",
            steps_used=-1,
            terminated_reason=TerminationReason.COMPLETED,
        )


def test_retrieval_result_allows_empty_snippets():
    result = RetrievalResult(
        query="q",
        context_summary="Some summary with no verbatim quotes needed.",
        provider_name="mock_retrieval_provider",
        steps_used=0,
        terminated_reason=TerminationReason.COMPLETED,
    )
    assert result.snippets == []
