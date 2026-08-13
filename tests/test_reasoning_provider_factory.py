"""Tests for `get_default_reasoning_provider` (the `ReasoningCapability`
provider-selection factory `SpecialistAgent.__init__` uses), mirroring
`test_coding_provider_factory.py`/`test_retrieval_provider_factory.py`'s
style/coverage exactly -- `reasoning_factory.py` now reads the same
`AI_SDLC_AGENT_FRAMEWORK` variable those two factories read (an earlier
version of this file tested a reasoning-specific `AI_SDLC_REASONING_
PROVIDER` variable; see `reasoning_factory.py`'s module docstring for why
that was corrected).

No network access / external credentials required anywhere in this file.
"""
from __future__ import annotations

import importlib

import pytest

from ai_sdlc.capabilities.providers.mock import MockReasoningProvider
from ai_sdlc.capabilities.providers.reasoning_anthropic import (
    API_KEY_ENV_VAR,
    AnthropicReasoningProvider,
)
from ai_sdlc.capabilities.providers.reasoning_factory import (
    PROVIDER_ENV_VAR,
    get_default_reasoning_provider,
)
from ai_sdlc.capabilities.reasoning import ProviderError


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(PROVIDER_ENV_VAR, raising=False)
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)


def test_unset_env_var_defaults_to_mock():
    provider = get_default_reasoning_provider()
    assert isinstance(provider, MockReasoningProvider)


def test_explicit_mock_returns_mock_provider(monkeypatch):
    monkeypatch.setenv(PROVIDER_ENV_VAR, "mock")
    provider = get_default_reasoning_provider()
    assert isinstance(provider, MockReasoningProvider)


def test_value_is_case_insensitive_and_trims_whitespace(monkeypatch):
    monkeypatch.setenv(PROVIDER_ENV_VAR, "  MOCK  ")
    provider = get_default_reasoning_provider()
    assert isinstance(provider, MockReasoningProvider)


def test_empty_string_value_defaults_to_mock(monkeypatch):
    monkeypatch.setenv(PROVIDER_ENV_VAR, "")
    provider = get_default_reasoning_provider()
    assert isinstance(provider, MockReasoningProvider)


def test_explicit_claude_with_key_returns_anthropic_provider(monkeypatch):
    monkeypatch.setenv(PROVIDER_ENV_VAR, "claude")
    monkeypatch.setenv(API_KEY_ENV_VAR, "sk-ant-fake-key-for-test")
    # Real `anthropic` package is not installed in this test environment,
    # so constructing the real client itself would fail -- but that's a
    # *different*, already-covered failure mode
    # (test_reasoning_anthropic_provider.py's SDK-unavailable tests). This
    # test only needs to prove the factory routes to the right class when
    # a key *is* present; it accepts either a successful construction (if
    # the extra happens to be installed) or the documented SDK-unavailable
    # ProviderError, but never silently falls back to the mock.
    import ai_sdlc.capabilities.providers.reasoning_anthropic as reasoning_anthropic_module

    if reasoning_anthropic_module.SDK_IMPORT_ERROR is None:
        provider = get_default_reasoning_provider()
        assert isinstance(provider, AnthropicReasoningProvider)
    else:
        with pytest.raises(ProviderError):
            get_default_reasoning_provider()


def test_explicit_claude_without_key_raises_provider_error(monkeypatch):
    monkeypatch.setenv(PROVIDER_ENV_VAR, "claude")
    # API_KEY_ENV_VAR deliberately left unset by the _clean_env fixture.
    with pytest.raises(ProviderError, match=API_KEY_ENV_VAR):
        get_default_reasoning_provider()


def test_explicit_copilot_selects_copilot_provider_without_requiring_the_sdk(monkeypatch):
    """`get_default_reasoning_provider()` must route to
    `CopilotReasoningProvider` even when `github-copilot-sdk` isn't
    installed -- that provider itself raises `ProviderError` from
    `__init__` in that case (see `test_reasoning_copilot_import_guard.py`),
    not this factory raising something else / falling back silently."""
    monkeypatch.setenv(PROVIDER_ENV_VAR, "copilot")
    from ai_sdlc.capabilities.providers.reasoning_copilot import (
        _COPILOT_SDK_IMPORT_ERROR,
        CopilotReasoningProvider,
    )

    if _COPILOT_SDK_IMPORT_ERROR is None:
        provider = get_default_reasoning_provider()
        assert isinstance(provider, CopilotReasoningProvider)
    else:
        with pytest.raises(ProviderError, match="github-copilot-sdk"):
            get_default_reasoning_provider()


def test_unsupported_value_raises_value_error(monkeypatch):
    monkeypatch.setenv(PROVIDER_ENV_VAR, "openai")
    with pytest.raises(ValueError):
        get_default_reasoning_provider()


def test_module_imports_without_requiring_optional_copilot_extra():
    """Importing this factory module must never itself require
    `github-copilot-sdk` -- the "copilot" branch's import is deferred
    inside the function body, not hoisted to module scope."""
    module = importlib.import_module("ai_sdlc.capabilities.providers.reasoning_factory")
    assert hasattr(module, "get_default_reasoning_provider")
