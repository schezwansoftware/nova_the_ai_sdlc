"""Concrete implementations of the `ReasoningCapability`, `DesignCapability`,
and `CodingCapability` interfaces.

Deterministic, network-free mock providers are implemented here for all
three capabilities: `mock.py` (`MockReasoningProvider`), `design_mock.py`
(`MockDesignProvider`), and `coding_mock.py` (`MockCodingProvider`). Real
vendor providers for Reasoning/Design (OpenAI, Anthropic, Bedrock, Ollama,
multimodal/image-generation services, design-tool adapters such as a
future Nexus-owned Figma integration, ...) are explicitly out of scope for
Craft and are deferred to be added later behind the `ReasoningProvider`
(`base.py`) / `DesignProvider` (`design_base.py`) protocols, without any
change required to agent code.

`CodingCapability` is different: harnessing a real agentic coding tool
*is* this capability's V1 scope (section 18 Decision 4), owned by Forge,
not deferred. `claude_sdk.py` (`ClaudeAgentSDKProvider`) is the real,
default V1 provider, backed by the `claude-agent-sdk` package and a
`claude` CLI subprocess -- see that module's docstring for exactly what is
verified-against-a-real-install versus implemented-against-documentation-
only in this environment. A second provider targeting `github/copilot-sdk`
is built separately behind the same `CodingCapability` seam.
"""
