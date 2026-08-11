"""Concrete implementations of the `ReasoningCapability`, `DesignCapability`,
`CodingCapability`, and `RetrievalCapability` interfaces.

Deterministic, network-free mock providers are implemented here for all
four capabilities: `mock.py` (`MockReasoningProvider`), `design_mock.py`
(`MockDesignProvider`), `coding_mock.py` (`MockCodingProvider`), and
`retrieval_mock.py` (`MockRetrievalProvider`). These remain the hard
default everywhere nothing has been explicitly configured (every test,
CI, and any workspace that hasn't opted in -- see `reasoning_factory.py`).

`reasoning_anthropic.py` (`AnthropicReasoningProvider`) is
`ReasoningCapability`'s real V1 provider, backed by the Anthropic
Messages API's forced tool-use structured output -- selected over the
mock via `reasoning_factory.get_default_reasoning_provider()` when a
workspace sets `AI_SDLC_REASONING_PROVIDER=anthropic`. Real
`DesignCapability` providers (OpenAI/other multimodal/image-generation
services, design-tool adapters such as a future Nexus-owned Figma
integration, ...) remain out of scope for Craft and are deferred to be
added later behind the `DesignProvider` (`design_base.py`) protocol,
without any change required to agent code.

`CodingCapability` and `RetrievalCapability` are different: harnessing a
real agentic coding tool *is* both capabilities' V1 scope (section 18
Decisions 4 and 6), owned by Forge, not deferred:

  - `claude_sdk.py` (`ClaudeAgentSDKProvider`) is `CodingCapability`'s
    real, default V1 provider, backed by the `claude-agent-sdk` package
    and a `claude` CLI subprocess. A second provider targeting
    `github/copilot-sdk` (`coding_copilot.py`) is built separately behind
    the same `CodingCapability` seam.
  - `retrieval_claude.py` (`ClaudeAgentSDKRetrievalProvider`) is
    `RetrievalCapability`'s real, default V1 provider -- the *same*
    harnessing pattern as `claude_sdk.py`, permissioned strictly
    read-only (section 9's "V1 Provider: Harnessed Read-Only Agent for
    Codebase Grounding"). Sage's originally-scoped dual-index design
    remains a documented future/scale-path provider behind this same
    seam (section 9's "Future/Scale Path", section 18 Decision 6), not
    built for V1.

See each provider module's own docstring for exactly what is
verified-against-a-real-install versus implemented-against-documentation-
only in this environment.
"""
