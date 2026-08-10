"""Tests for the DesignCapability abstraction and its mock provider.

Mirrors `tests/test_capabilities_reasoning.py`'s structure exactly. No
network access / external credentials required anywhere in this file.
"""
from __future__ import annotations

import pytest

from ai_sdlc.capabilities.design import (
    DesignRequest,
    DesignResponse,
    FidelityLevel,
    MalformedResponseError,
    ProviderError,
)
from ai_sdlc.capabilities.providers.design_mock import MockDesignProvider

_REQUEST = DesignRequest(
    flow_title="Export orders to CSV",
    summary="Users can export their current order list to CSV from the Orders page.",
    screens=["OrdersPage", "ExportDialog"],
    user_flows=["User opens the Orders page and clicks Export."],
)


def test_mock_provider_returns_valid_structured_response():
    provider = MockDesignProvider()
    result = provider.generate(_REQUEST)

    assert isinstance(result, DesignResponse)
    assert result.provider_name
    assert len(result.artifacts) == len(_REQUEST.screens) * len(_REQUEST.fidelities)


def test_mock_provider_generates_one_artifact_per_screen_per_fidelity():
    provider = MockDesignProvider()
    result = provider.generate(_REQUEST)

    by_fidelity = {level: [] for level in FidelityLevel}
    for artifact in result.artifacts:
        by_fidelity[artifact.fidelity].append(artifact)

    for level in _REQUEST.fidelities:
        screens_at_level = {a.screen_ref for a in by_fidelity[level]}
        assert screens_at_level == set(_REQUEST.screens)


def test_mock_provider_respects_requested_fidelity_subset():
    request = _REQUEST.model_copy(update={"fidelities": [FidelityLevel.LO_FI]})
    provider = MockDesignProvider()
    result = provider.generate(request)

    assert len(result.artifacts) == len(request.screens)
    assert all(a.fidelity == FidelityLevel.LO_FI for a in result.artifacts)


def test_mock_provider_is_deterministic_for_same_request():
    provider = MockDesignProvider()
    first = provider.generate(_REQUEST)
    second = provider.generate(_REQUEST)

    first_ids = sorted(a.artifact_id for a in first.artifacts)
    second_ids = sorted(a.artifact_id for a in second.artifacts)
    assert first_ids == second_ids


def test_mock_provider_force_malformed_raises_malformed_response_error():
    provider = MockDesignProvider(force_error="malformed")
    with pytest.raises(MalformedResponseError):
        provider.generate(_REQUEST)


def test_mock_provider_force_provider_failure_raises_provider_error():
    provider = MockDesignProvider(force_error="provider_failure")
    with pytest.raises(ProviderError):
        provider.generate(_REQUEST)


def test_mock_provider_per_call_force_error_overrides_constructor():
    provider = MockDesignProvider()
    with pytest.raises(ProviderError):
        provider.generate(_REQUEST, force_error="provider_failure")

    failing_provider = MockDesignProvider(force_error="provider_failure")
    with pytest.raises(ProviderError):
        failing_provider.generate(_REQUEST)


def test_mock_provider_rejects_unsupported_force_error_value():
    with pytest.raises(ValueError):
        MockDesignProvider(force_error="not_a_real_mode")


def test_design_request_rejects_empty_screens():
    with pytest.raises(Exception):
        DesignRequest(flow_title="Flow", summary="Summary", screens=[])


def test_design_request_rejects_blank_flow_title():
    with pytest.raises(Exception):
        DesignRequest(flow_title="   ", summary="Summary", screens=["Screen"])
