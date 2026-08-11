"""AI Capability Layer.

Specialist agents (owned by Craft) must never depend directly on a vendor
LLM SDK (OpenAI, Anthropic, Bedrock, Ollama, Copilot, etc) or a vendor
visual-design/multimodal service. Instead, agents depend on abstract
capability interfaces defined in this package -- e.g. `ReasoningCapability`,
`DesignCapability` -- which are backed by a provider implementation
selected/configured elsewhere (out of scope for Craft; see
`providers/base.py` / `providers/design_base.py` for the provider-facing
contracts a future real vendor client would implement).

For V1/MVP, the fully deterministic, network-free `MockReasoningProvider`
(see `providers/mock.py`) and `MockDesignProvider` (see
`providers/design_mock.py`) remain the hard default everywhere, which
keeps the test suite runnable without external credentials. A real
`ReasoningCapability` provider now also exists --
`providers/reasoning_anthropic.py`'s `AnthropicReasoningProvider`, backed
by the Anthropic Messages API -- selected in favor of the mock only when a
workspace explicitly opts in (`providers/reasoning_factory.py`,
`AI_SDLC_REASONING_PROVIDER=anthropic`); every agent still only ever
depends on the `ReasoningCapability` interface, never this or any other
concrete provider directly.

Note: `ReasoningCapability`, `DesignCapability`, `CodingCapability`, and
`RetrievalCapability` each define their own `ProviderError`/
`MalformedResponseError` classes (not shared) so each capability's
failure contract stays self-contained; callers that need more than one
(e.g. the UX Agent needs Reasoning + Design; the Developer Agent needs
Reasoning + Coding, per section 8's Tier 3; Architecture/Review/
Documentation need Reasoning + Retrieval, per Tier 2) import each pair
from its own module rather than from this package's re-exports, to avoid
ambiguity between them.
"""
from __future__ import annotations

from ai_sdlc.capabilities.coding import CodingCapability
from ai_sdlc.capabilities.coding import MalformedResponseError as CodingMalformedResponseError
from ai_sdlc.capabilities.coding import ProviderError as CodingProviderError
from ai_sdlc.capabilities.design import DesignCapability
from ai_sdlc.capabilities.design import MalformedResponseError as DesignMalformedResponseError
from ai_sdlc.capabilities.design import ProviderError as DesignProviderError
from ai_sdlc.capabilities.reasoning import (
    MalformedResponseError,
    ProviderError,
    ReasoningCapability,
)
from ai_sdlc.capabilities.retrieval import MalformedResponseError as RetrievalMalformedResponseError
from ai_sdlc.capabilities.retrieval import ProviderError as RetrievalProviderError
from ai_sdlc.capabilities.retrieval import RetrievalCapability

__all__ = [
    "ReasoningCapability",
    "ProviderError",
    "MalformedResponseError",
    "DesignCapability",
    "DesignProviderError",
    "DesignMalformedResponseError",
    "CodingCapability",
    "CodingProviderError",
    "CodingMalformedResponseError",
    "RetrievalCapability",
    "RetrievalProviderError",
    "RetrievalMalformedResponseError",
]
