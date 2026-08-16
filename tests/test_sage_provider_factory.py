"""Tests for `get_default_sage_provider` (the `SageCapability`
provider-selection factory), mirroring
`test_reasoning_provider_factory.py`/`test_retrieval_provider_factory.py`'s
style/coverage.

One deliberate deviation from those two factories' test style, matching
the deliberate API deviation itself: `get_default_sage_provider` takes a
real `workspace_path` argument (see `sage_factory.py`'s docstring for
why), so every call site here passes `tmp_path`.

No network access / external credentials required anywhere in this file.
"""
from __future__ import annotations

import importlib

import pytest

from ai_sdlc.capabilities.connector_resolver import ConnectorResolver
from ai_sdlc.capabilities.providers.sage_factory import (
    PROVIDER_ENV_VAR,
    get_default_sage_provider,
)
from ai_sdlc.capabilities.providers.sage_mock import MockSageProvider
from ai_sdlc.capabilities.sage import ProviderError


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(PROVIDER_ENV_VAR, raising=False)


def test_unset_env_var_defaults_to_mock(tmp_path):
    provider = get_default_sage_provider(tmp_path)
    assert isinstance(provider, MockSageProvider)


def test_explicit_mock_returns_mock_provider(monkeypatch, tmp_path):
    monkeypatch.setenv(PROVIDER_ENV_VAR, "mock")
    provider = get_default_sage_provider(tmp_path)
    assert isinstance(provider, MockSageProvider)


def test_value_is_case_insensitive_and_trims_whitespace(monkeypatch, tmp_path):
    monkeypatch.setenv(PROVIDER_ENV_VAR, "  MOCK  ")
    provider = get_default_sage_provider(tmp_path)
    assert isinstance(provider, MockSageProvider)


def test_empty_string_value_defaults_to_mock(monkeypatch, tmp_path):
    monkeypatch.setenv(PROVIDER_ENV_VAR, "")
    provider = get_default_sage_provider(tmp_path)
    assert isinstance(provider, MockSageProvider)


def test_explicit_claude_selects_sage_claude_provider_and_wires_resolver(monkeypatch, tmp_path):
    monkeypatch.setenv(PROVIDER_ENV_VAR, "claude")
    from ai_sdlc.capabilities.providers.sage_claude import SDK_IMPORT_ERROR, SageClaudeProvider

    if SDK_IMPORT_ERROR is None:
        provider = get_default_sage_provider(tmp_path)
        assert isinstance(provider, SageClaudeProvider)
        assert isinstance(provider._connector_resolver, ConnectorResolver)
        assert provider._connector_resolver.workspace_path == tmp_path
    else:
        with pytest.raises(ProviderError):
            get_default_sage_provider(tmp_path)


def test_explicit_copilot_selects_sage_copilot_provider_and_wires_resolver(monkeypatch, tmp_path):
    monkeypatch.setenv(PROVIDER_ENV_VAR, "copilot")
    from ai_sdlc.capabilities.providers.sage_copilot import (
        _COPILOT_SDK_IMPORT_ERROR,
        SageCopilotProvider,
    )

    if _COPILOT_SDK_IMPORT_ERROR is None:
        provider = get_default_sage_provider(tmp_path)
        assert isinstance(provider, SageCopilotProvider)
        assert isinstance(provider._connector_resolver, ConnectorResolver)
    else:
        with pytest.raises(ProviderError):
            get_default_sage_provider(tmp_path)


def test_unsupported_value_raises_value_error(monkeypatch, tmp_path):
    monkeypatch.setenv(PROVIDER_ENV_VAR, "openai")
    with pytest.raises(ValueError):
        get_default_sage_provider(tmp_path)


def test_module_imports_without_requiring_optional_copilot_extra():
    module = importlib.import_module("ai_sdlc.capabilities.providers.sage_factory")
    assert hasattr(module, "get_default_sage_provider")


def test_shares_ai_sdlc_agent_framework_env_var_with_other_capabilities():
    """Per the locked design's edge case #5: Sage inherits the workspace's
    single AI_SDLC_AGENT_FRAMEWORK choice rather than getting its own."""
    from ai_sdlc.capabilities.providers.coding_factory import PROVIDER_ENV_VAR as CODING_VAR
    from ai_sdlc.capabilities.providers.reasoning_factory import PROVIDER_ENV_VAR as REASONING_VAR
    from ai_sdlc.capabilities.providers.retrieval_factory import PROVIDER_ENV_VAR as RETRIEVAL_VAR

    assert PROVIDER_ENV_VAR == CODING_VAR == REASONING_VAR == RETRIEVAL_VAR == "AI_SDLC_AGENT_FRAMEWORK"
