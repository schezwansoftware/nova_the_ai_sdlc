"""AI Capability Layer.

Specialist agents (owned by Craft) must never depend directly on a vendor
LLM SDK (OpenAI, Anthropic, Bedrock, Ollama, Copilot, etc) or a vendor
visual-design/multimodal service. Instead, agents depend on abstract
capability interfaces defined in this package -- e.g. `ReasoningCapability`,
`DesignCapability` -- which are backed by a provider implementation
selected/configured elsewhere (out of scope for Craft; see
`providers/base.py` / `providers/design_base.py` for the provider-facing
contracts a future real vendor client would implement).

For V1/MVP, the only implementations available are the fully
deterministic, network-free `MockReasoningProvider` (see
`providers/mock.py`) and `MockDesignProvider` (see
`providers/design_mock.py`), which are sufficient to prove both
abstraction boundaries and to keep the test suite runnable without
external credentials.

Note: `ReasoningCapability` and `DesignCapability` each define their own
`ProviderError`/`MalformedResponseError` classes (not shared) so each
capability's failure contract stays self-contained; callers that need both
(e.g. the UX Agent) import each pair from its own module rather than from
this package's re-exports, to avoid ambiguity between the two.
"""
from __future__ import annotations

from ai_sdlc.capabilities.design import DesignCapability
from ai_sdlc.capabilities.design import MalformedResponseError as DesignMalformedResponseError
from ai_sdlc.capabilities.design import ProviderError as DesignProviderError
from ai_sdlc.capabilities.reasoning import (
    MalformedResponseError,
    ProviderError,
    ReasoningCapability,
)

__all__ = [
    "ReasoningCapability",
    "ProviderError",
    "MalformedResponseError",
    "DesignCapability",
    "DesignProviderError",
    "DesignMalformedResponseError",
]
