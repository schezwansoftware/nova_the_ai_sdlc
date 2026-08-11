"""Verifies `coding_copilot.py` degrades gracefully when the optional
`github-copilot-sdk` extra isn't installed -- the module itself must
still import cleanly (so it can appear in test collection / be imported
by anything that enumerates available providers), and only fail with a
clear, actionable error at `CopilotCodingProvider()` construction time.

This test is meaningful in *either* environment:
  - Extra absent: exercises the real guard path.
  - Extra present: exercises the module-import-always-succeeds guarantee
    (trivially true here, but keeps this file from being silently skipped
    everywhere and never actually asserting anything in CI).
"""
from __future__ import annotations

import importlib

import pytest


def test_module_imports_regardless_of_sdk_availability():
    module = importlib.import_module("ai_sdlc.capabilities.providers.coding_copilot")
    assert hasattr(module, "CopilotCodingProvider")


def test_constructor_raises_clear_error_when_sdk_unavailable(monkeypatch):
    module = importlib.import_module("ai_sdlc.capabilities.providers.coding_copilot")
    monkeypatch.setattr(module, "_COPILOT_SDK_IMPORT_ERROR", ImportError("simulated: not installed"))
    with pytest.raises(ImportError, match="github-copilot-sdk is not installed"):
        module.CopilotCodingProvider()
