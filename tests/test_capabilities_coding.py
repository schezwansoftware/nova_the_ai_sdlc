"""Tests for the CodingCapability abstraction and its mock provider.

Mirrors `tests/test_capabilities_design.py`'s structure exactly. No
network access / external credentials / real Claude Agent SDK required
anywhere in this file.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_sdlc.capabilities.coding import (
    NO_SELF_CHECK_COMMANDS_REASON,
    CodingRequest,
    CodingResult,
    MalformedResponseError,
    ProviderError,
    SelfCheckResult,
    TerminationReason,
)
from ai_sdlc.capabilities.providers.coding_mock import MockCodingProvider

_REQUEST = CodingRequest(
    task_title="Redis Cache Integration for Order Service",
    task_summary="Add a Redis-backed cache in front of the order lookup path.",
    functional_requirements=["Cache order lookups by order id"],
    components_affected=["OrderService", "OrderCacheConfig"],
    working_tree_path="/tmp/fake-isolated-worktree",
    allowed_tools=["Read", "Write", "Edit", "Bash"],
    allowed_commands=["git", "pytest"],
)


def test_mock_provider_returns_valid_structured_result():
    provider = MockCodingProvider()
    result = provider.execute(_REQUEST)

    assert isinstance(result, CodingResult)
    assert result.provider_name
    assert result.branch_name.startswith("forge/")
    assert result.terminated_reason == TerminationReason.COMPLETED


def test_mock_provider_derives_one_file_per_affected_component():
    provider = MockCodingProvider()
    result = provider.execute(_REQUEST)

    assert len(result.files_changed) == len(_REQUEST.components_affected)


def test_mock_provider_is_deterministic_for_same_request():
    provider = MockCodingProvider()
    first = provider.execute(_REQUEST)
    second = provider.execute(_REQUEST)

    assert first.branch_name == second.branch_name
    assert first.files_changed == second.files_changed


def test_mock_provider_skips_self_check_when_no_commands_configured():
    provider = MockCodingProvider()
    result = provider.execute(_REQUEST)

    assert result.self_check.build_passed is None
    assert result.self_check.tests_passed is None
    assert result.self_check.commands_run == []
    assert result.self_check.skipped_reason == NO_SELF_CHECK_COMMANDS_REASON


def test_mock_provider_runs_self_check_when_commands_configured():
    request = _REQUEST.model_copy(
        update={"build_commands": ["make build"], "test_commands": ["pytest"]}
    )
    provider = MockCodingProvider()
    result = provider.execute(request)

    assert result.self_check.build_passed is True
    assert result.self_check.tests_passed is True
    assert result.self_check.commands_run == ["make build", "pytest"]
    assert result.self_check.skipped_reason is None


def test_mock_provider_force_malformed_raises_malformed_response_error():
    provider = MockCodingProvider(force_error="malformed")
    with pytest.raises(MalformedResponseError):
        provider.execute(_REQUEST)


def test_mock_provider_force_provider_failure_raises_provider_error():
    provider = MockCodingProvider(force_error="provider_failure")
    with pytest.raises(ProviderError):
        provider.execute(_REQUEST)


def test_mock_provider_per_call_force_error_overrides_constructor():
    provider = MockCodingProvider()
    with pytest.raises(ProviderError):
        provider.execute(_REQUEST, force_error="provider_failure")

    failing_provider = MockCodingProvider(force_error="provider_failure")
    with pytest.raises(ProviderError):
        failing_provider.execute(_REQUEST)


def test_mock_provider_rejects_unsupported_force_error_value():
    with pytest.raises(ValueError):
        MockCodingProvider(force_error="not_a_real_mode")


def test_coding_request_rejects_empty_allowed_tools():
    with pytest.raises(ValidationError):
        CodingRequest(
            task_title="Title",
            task_summary="Summary",
            working_tree_path="/tmp/fake",
            allowed_tools=[],
        )


def test_coding_request_rejects_blank_task_title():
    with pytest.raises(ValidationError):
        CodingRequest(
            task_title="   ",
            task_summary="Summary",
            working_tree_path="/tmp/fake",
            allowed_tools=["Read"],
        )


def test_coding_request_rejects_non_positive_max_steps():
    with pytest.raises(ValidationError):
        CodingRequest(
            task_title="Title",
            task_summary="Summary",
            working_tree_path="/tmp/fake",
            allowed_tools=["Read"],
            max_steps=0,
        )


def test_self_check_skipped_factory_sets_none_not_false():
    result = SelfCheckResult.skipped("no commands configured")
    assert result.build_passed is None
    assert result.tests_passed is None
    assert result.skipped_reason == "no commands configured"


def test_coding_result_rejects_blank_branch_name():
    with pytest.raises(ValidationError):
        CodingResult(
            branch_name="   ",
            self_check=SelfCheckResult.skipped(NO_SELF_CHECK_COMMANDS_REASON),
            provider_name="mock_coding_provider",
            steps_used=1,
            terminated_reason=TerminationReason.COMPLETED,
            summary="Did something.",
        )


def test_coding_result_rejects_negative_steps_used():
    with pytest.raises(ValidationError):
        CodingResult(
            branch_name="forge/example",
            self_check=SelfCheckResult.skipped(NO_SELF_CHECK_COMMANDS_REASON),
            provider_name="mock_coding_provider",
            steps_used=-1,
            terminated_reason=TerminationReason.COMPLETED,
            summary="Did something.",
        )
