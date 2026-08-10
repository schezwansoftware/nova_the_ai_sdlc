"""AI Capability Layer.

Specialist agents (owned by Craft) must never depend directly on a vendor
LLM SDK (OpenAI, Anthropic, Bedrock, Ollama, Copilot, etc). Instead, agents
depend on abstract capability interfaces defined in this package -- e.g.
`ReasoningCapability` -- which are backed by a provider implementation
selected/configured elsewhere (out of scope for Craft; see
`providers/base.py` for the provider-facing contract a future real vendor
client would implement).

For V1/MVP, the only implementation available is the fully deterministic,
network-free `MockReasoningProvider` (see `providers/mock.py`), which is
sufficient to prove the abstraction boundary and to keep the test suite
runnable without external credentials.
"""
from __future__ import annotations

from ai_sdlc.capabilities.reasoning import (
    MalformedResponseError,
    ProviderError,
    ReasoningCapability,
)

__all__ = [
    "ReasoningCapability",
    "ProviderError",
    "MalformedResponseError",
]
