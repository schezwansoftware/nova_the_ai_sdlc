"""Provider-facing protocol.

This is the seam a *real* vendor client (OpenAI, Anthropic, Bedrock,
Ollama, GitHub Copilot, ...) would implement in a future change. Craft
deliberately does not implement any such client here -- only the contract
it must satisfy, plus the deterministic mock in `mock.py` used for V1 and
for tests.

No vendor SDK is imported anywhere in this package.
"""
from __future__ import annotations

from typing import Any, Dict, Protocol, runtime_checkable


@runtime_checkable
class ReasoningProvider(Protocol):
    """Low-level provider protocol.

    A real implementation would take a plain-text prompt plus a JSON-schema
    description of the desired output and return a raw dict payload (before
    it gets validated into a Pydantic model by the `ReasoningCapability`
    layer). This intentionally stays vendor-agnostic: no message "roles",
    no vendor-specific request/response objects.
    """

    def generate(self, prompt: str, *, json_schema: Dict[str, Any]) -> Dict[str, Any]:
        """Return a raw (not-yet-validated) structured payload for `prompt`
        that is expected to conform to `json_schema`."""
        ...
