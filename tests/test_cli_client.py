"""Tests for `PlatformClient`'s request timeout default.

No network access required -- these only check the configured value, not
an actual slow request.
"""
from __future__ import annotations

from ai_sdlc.cli.client import DEFAULT_REQUEST_TIMEOUT_SECONDS, PlatformClient


def test_default_timeout_is_generous_enough_for_a_real_agent_call():
    """The old default (15.0) was tuned for the mock providers and made
    the CLI report "timed out" on a real, successfully-completing
    multi-stage agent run (`reasoning_copilot.py`'s own per-call budget
    alone can reach ~120s) -- see `client.py`'s module-level docstring
    for the full account. Asserting a floor here, not an exact value, so
    this doesn't need touching every time the real providers' own step
    budgets are tuned."""
    client = PlatformClient(host="127.0.0.1", port=8000)
    assert client.timeout == DEFAULT_REQUEST_TIMEOUT_SECONDS
    assert client.timeout >= 300.0


def test_timeout_still_overridable_explicitly():
    client = PlatformClient(host="127.0.0.1", port=8000, timeout=5.0)
    assert client.timeout == 5.0
