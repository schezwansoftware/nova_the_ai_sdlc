"""Tests for `get_default_retrieval_provider` (the `RetrievalCapability`
provider-selection factory), mirroring
`test_reasoning_provider_factory.py`/`test_coding_provider_factory.py`'s
style/coverage.

No network access / external credentials required anywhere in this file.
"""
from __future__ import annotations

import importlib

import pytest

from ai_sdlc.capabilities.providers.retrieval_factory import (
    PROVIDER_ENV_VAR,
    get_default_retrieval_provider,
)
from ai_sdlc.capabilities.providers.retrieval_mock import MockRetrievalProvider
from ai_sdlc.capabilities.retrieval import ProviderError


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(PROVIDER_ENV_VAR, raising=False)


def test_unset_env_var_defaults_to_mock():
    provider = get_default_retrieval_provider()
    assert isinstance(provider, MockRetrievalProvider)


def test_explicit_mock_returns_mock_provider(monkeypatch):
    monkeypatch.setenv(PROVIDER_ENV_VAR, "mock")
    provider = get_default_retrieval_provider()
    assert isinstance(provider, MockRetrievalProvider)


def test_value_is_case_insensitive_and_trims_whitespace(monkeypatch):
    monkeypatch.setenv(PROVIDER_ENV_VAR, "  MOCK  ")
    provider = get_default_retrieval_provider()
    assert isinstance(provider, MockRetrievalProvider)


def test_empty_string_value_defaults_to_mock(monkeypatch):
    monkeypatch.setenv(PROVIDER_ENV_VAR, "")
    provider = get_default_retrieval_provider()
    assert isinstance(provider, MockRetrievalProvider)


def test_explicit_claude_selects_claude_agent_sdk_retrieval_provider(monkeypatch):
    monkeypatch.setenv(PROVIDER_ENV_VAR, "claude")
    from ai_sdlc.capabilities.providers.retrieval_claude import (
        SDK_IMPORT_ERROR,
        ClaudeAgentSDKRetrievalProvider,
    )

    # `claude-agent-sdk` is not installed in this test environment -- this
    # only needs to prove the factory *routes* to the right class, exactly
    # like the coding factory's equivalent test.
    if SDK_IMPORT_ERROR is None:
        provider = get_default_retrieval_provider()
        assert isinstance(provider, ClaudeAgentSDKRetrievalProvider)
    else:
        with pytest.raises(ProviderError):
            get_default_retrieval_provider()


def test_explicit_copilot_without_sibling_provider_raises_clear_provider_error(monkeypatch):
    """As of this factory's own construction,
    `providers/retrieval_copilot.py` is a separate, independently-scoped
    task that may not exist in every checkout yet (see module docstring).
    This must never surface as a bare ImportError/AttributeError -- always
    a documented `ProviderError`, and it must never silently fall back to
    the mock."""
    monkeypatch.setenv(PROVIDER_ENV_VAR, "copilot")
    try:
        importlib.import_module("ai_sdlc.capabilities.providers.retrieval_copilot")
    except ImportError:
        with pytest.raises(ProviderError, match="retrieval_copilot"):
            get_default_retrieval_provider()
    else:
        pytest.skip(
            "providers/retrieval_copilot.py exists in this checkout -- covered by "
            "test_explicit_copilot_uses_sibling_provider_when_present instead."
        )


def test_explicit_copilot_uses_sibling_provider_when_present(monkeypatch):
    """If/once `providers/retrieval_copilot.py` lands with a
    `CopilotRetrievalProvider` class, the factory must route to it exactly
    like every other explicit selection -- proven here via a fake module
    injected into `sys.modules` so this test is meaningful regardless of
    whether that sibling task has actually landed in this checkout yet."""
    import sys
    import types

    class _FakeCopilotRetrievalProvider:
        pass

    fake_module = types.ModuleType("ai_sdlc.capabilities.providers.retrieval_copilot")
    fake_module.CopilotRetrievalProvider = _FakeCopilotRetrievalProvider
    monkeypatch.setitem(sys.modules, "ai_sdlc.capabilities.providers.retrieval_copilot", fake_module)
    monkeypatch.setenv(PROVIDER_ENV_VAR, "copilot")

    provider = get_default_retrieval_provider()
    assert isinstance(provider, _FakeCopilotRetrievalProvider)


def test_unsupported_value_raises_value_error(monkeypatch):
    monkeypatch.setenv(PROVIDER_ENV_VAR, "openai")
    with pytest.raises(ValueError):
        get_default_retrieval_provider()


def test_module_imports_without_requiring_optional_copilot_extra():
    module = importlib.import_module("ai_sdlc.capabilities.providers.retrieval_factory")
    assert hasattr(module, "get_default_retrieval_provider")
