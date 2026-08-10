"""Concrete implementations of the `ReasoningCapability` interface.

Only a deterministic, network-free mock provider is implemented here for
V1 (see `mock.py`). Real vendor providers (OpenAI, Anthropic, Bedrock,
Ollama, Copilot, ...) are explicitly out of scope for Craft and are
deferred to be added later behind the `ReasoningProvider` protocol defined
in `base.py`, without any change required to agent code.
"""
