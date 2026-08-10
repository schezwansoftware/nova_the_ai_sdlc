"""Concrete implementations of the `ReasoningCapability` and
`DesignCapability` interfaces.

Only deterministic, network-free mock providers are implemented here for
V1: `mock.py` (`MockReasoningProvider`) and `design_mock.py`
(`MockDesignProvider`). Real vendor providers (OpenAI, Anthropic, Bedrock,
Ollama, Copilot, multimodal/image-generation services, design-tool
adapters such as a future Nexus-owned Figma integration, ...) are
explicitly out of scope for Craft and are deferred to be added later
behind the `ReasoningProvider` (`base.py`) / `DesignProvider`
(`design_base.py`) protocols, without any change required to agent code.
"""
