"""Tests for the CodingCapability abstraction and its mock provider.

Mirrors `tests/test_capabilities_reasoning.py`/`tests/test_capabilities_design.py`'s
structure. No network access / external credentials required anywhere in
this file. There is no Developer Agent yet to exercise provider-
independence against (see `coding.py`'s module docstring -- that's
separate, not-yet-assigned Forge-core work), so these tests exercise the
capability/provider contract directly instead.
"""
from __future__ import annotations

import pytest

from ai_sdlc.capabilities.coding import (
    CodingRequest,
    CodingResult,
    MalformedResponseError,
    ProviderError,
    ToolPolicy,
)
from ai_sdlc.capabilities.providers.coding_mock import MockCodingProvider

_REQUEST = CodingRequest(
    task_summary="Add Redis caching to order retrieval",
    task_brief=(
        "Add Redis-backed caching for order retrieval endpoints. "
        "Evict the cache automatically when an order is updated."
    ),
    workspace_path="/tmp/fake-workspace/order-service",
    base_branch="main",
)


def test_mock_provider_returns_valid_structured_result():
    provider = MockCodingProvider()
    result = provider.execute(_REQUEST)

    assert isinstance(result, CodingResult)
    assert result.branch_name.startswith("forge/")
    assert result.provider_name == "mock_coding_provider"
    assert len(result.files_changed) > 0
    assert result.steps_used <= _REQUEST.max_steps


def test_mock_provider_skips_self_check_when_no_commands_configured():
    provider = MockCodingProvider()
    result = provider.execute(_REQUEST)

    assert result.self_check.commands_run == []
    assert result.self_check.build_passed is None
    assert result.self_check.tests_passed is None
    assert result.self_check.skipped_reason is not None


def test_mock_provider_runs_self_check_when_commands_configured():
    request = _REQUEST.model_copy(
        update={"self_check_commands": ["./gradlew build", "./gradlew test"]}
    )
    provider = MockCodingProvider()
    result = provider.execute(request)

    assert result.self_check.commands_run == ["./gradlew build", "./gradlew test"]
    assert result.self_check.build_passed is True
    assert result.self_check.tests_passed is True
    assert result.self_check.skipped_reason is None


def test_mock_provider_force_self_check_failed_reports_failure_without_raising():
    request = _REQUEST.model_copy(update={"self_check_commands": ["pytest"]})
    provider = MockCodingProvider(force_error="self_check_failed")
    result = provider.execute(request)

    assert isinstance(result, CodingResult)
    assert result.self_check.build_passed is False
    assert result.self_check.tests_passed is False


def test_mock_provider_is_deterministic_for_same_request():
    provider = MockCodingProvider()
    first = provider.execute(_REQUEST)
    second = provider.execute(_REQUEST)

    assert first.branch_name == second.branch_name
    assert [f.path for f in first.files_changed] == [f.path for f in second.files_changed]


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


def test_coding_request_rejects_blank_task_summary():
    with pytest.raises(Exception):
        CodingRequest(
            task_summary="   ",
            task_brief="Do something.",
            workspace_path="/tmp/fake-workspace",
        )


def test_coding_request_rejects_zero_max_steps():
    with pytest.raises(Exception):
        CodingRequest(
            task_summary="A",
            task_brief="B",
            workspace_path="/tmp/fake-workspace",
            max_steps=0,
        )


def test_tool_policy_rejects_empty_allowed_commands():
    with pytest.raises(Exception):
        ToolPolicy(allowed_commands=[])


def test_tool_policy_defaults_are_nonempty():
    policy = ToolPolicy()
    assert "git" in policy.allowed_commands
    assert "sudo" in policy.denied_commands
