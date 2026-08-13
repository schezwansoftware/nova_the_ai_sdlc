"""Verifies `reasoning_copilot.py` degrades gracefully when the optional
`github-copilot-sdk` extra isn't installed -- mirrors
`test_coding_copilot_import_guard.py`/`test_retrieval_copilot_import_guard.py`
exactly, adapted to `CopilotReasoningProvider`/`ReasoningCapability`'s
failure contract. The module itself must always import cleanly, and only
fail with a clear, actionable `ProviderError` at
`CopilotReasoningProvider()` construction time.

Meaningful in either environment: exercises the real guard path when the
extra is absent, and the module-import-always-succeeds guarantee
(trivially true, but keeps this file from being silently skipped
everywhere) when it's present.
"""
from __future__ import annotations

import importlib

import pytest

from ai_sdlc.capabilities.reasoning import ProviderError


def test_module_imports_regardless_of_sdk_availability():
    module = importlib.import_module("ai_sdlc.capabilities.providers.reasoning_copilot")
    assert hasattr(module, "CopilotReasoningProvider")


def test_constructor_raises_provider_error_when_sdk_unavailable(monkeypatch):
    module = importlib.import_module("ai_sdlc.capabilities.providers.reasoning_copilot")
    monkeypatch.setattr(module, "_COPILOT_SDK_IMPORT_ERROR", ImportError("simulated: not installed"))
    with pytest.raises(ProviderError, match="github-copilot-sdk is not usable"):
        module.CopilotReasoningProvider()
