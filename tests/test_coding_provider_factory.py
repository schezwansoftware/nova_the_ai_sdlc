"""Tests for `get_default_coding_provider` (the `CodingCapability`
provider-selection factory), mirroring
`test_reasoning_provider_factory.py`'s style/coverage.

No network access / external credentials required anywhere in this file.
"""
from __future__ import annotations

import importlib

import pytest

from ai_sdlc.capabilities.providers.coding_factory import (
    PROVIDER_ENV_VAR,
    get_default_coding_provider,
)
from ai_sdlc.capabilities.providers.coding_mock import MockCodingProvider


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(PROVIDER_ENV_VAR, raising=False)


def test_unset_env_var_defaults_to_mock():
    provider = get_default_coding_provider()
    assert isinstance(provider, MockCodingProvider)


def test_explicit_mock_returns_mock_provider(monkeypatch):
    monkeypatch.setenv(PROVIDER_ENV_VAR, "mock")
    provider = get_default_coding_provider()
    assert isinstance(provider, MockCodingProvider)


def test_value_is_case_insensitive_and_trims_whitespace(monkeypatch):
    monkeypatch.setenv(PROVIDER_ENV_VAR, "  MOCK  ")
    provider = get_default_coding_provider()
    assert isinstance(provider, MockCodingProvider)


def test_empty_string_value_defaults_to_mock(monkeypatch):
    monkeypatch.setenv(PROVIDER_ENV_VAR, "")
    provider = get_default_coding_provider()
    assert isinstance(provider, MockCodingProvider)


def test_explicit_claude_selects_claude_agent_sdk_provider(monkeypatch):
    monkeypatch.setenv(PROVIDER_ENV_VAR, "claude")
    from ai_sdlc.capabilities.coding import ProviderError
    from ai_sdlc.capabilities.providers.claude_sdk import (
        SDK_IMPORT_ERROR,
        ClaudeAgentSDKProvider,
    )

    # `claude-agent-sdk` is not installed in this test environment (see
    # test_coding_claude_sdk_provider.py's own SDK-unavailable coverage),
    # so this only needs to prove the factory *routes* to the right class
    # -- it accepts either a successful construction (if the package
    # happens to be installed) or the provider's own documented
    # SDK-unavailable ProviderError, but never a silent mock fallback.
    if SDK_IMPORT_ERROR is None:
        provider = get_default_coding_provider()
        assert isinstance(provider, ClaudeAgentSDKProvider)
    else:
        with pytest.raises(ProviderError):
            get_default_coding_provider()


def test_explicit_copilot_selects_copilot_provider_without_requiring_the_sdk(monkeypatch):
    """`get_default_coding_provider()` must route to `CopilotCodingProvider`
    even when `github-copilot-sdk` isn't installed -- that provider itself
    raises `ProviderError` from `__init__` in that case (see
    `test_coding_copilot_import_guard.py`), not this factory raising
    something else / falling back silently."""
    monkeypatch.setenv(PROVIDER_ENV_VAR, "copilot")
    from ai_sdlc.capabilities.coding import ProviderError
    from ai_sdlc.capabilities.providers.coding_copilot import (
        _COPILOT_SDK_IMPORT_ERROR,
        CopilotCodingProvider,
    )

    if _COPILOT_SDK_IMPORT_ERROR is None:
        provider = get_default_coding_provider()
        assert isinstance(provider, CopilotCodingProvider)
    else:
        with pytest.raises(ProviderError, match="github-copilot-sdk"):
            get_default_coding_provider()


def test_unsupported_value_raises_value_error(monkeypatch):
    monkeypatch.setenv(PROVIDER_ENV_VAR, "openai")
    with pytest.raises(ValueError):
        get_default_coding_provider()


def test_module_imports_without_requiring_optional_copilot_extra():
    """Importing this factory module must never itself require
    `github-copilot-sdk` -- the "copilot" branch's import is deferred
    inside the function body, not hoisted to module scope."""
    module = importlib.import_module("ai_sdlc.capabilities.providers.coding_factory")
    assert hasattr(module, "get_default_coding_provider")
