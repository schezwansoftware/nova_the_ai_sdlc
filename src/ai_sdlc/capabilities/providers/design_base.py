"""Provider-facing protocol for `DesignCapability`.

Mirrors `providers/base.py`'s `ReasoningProvider` Protocol exactly. This is
the seam a *real* visual-design provider (a multimodal LLM, an
image-generation service, or a future design-tool adapter -- e.g. a
Nexus-owned Figma integration, per
`docs/architecture/v1_architecture.md` section 3/7) would implement in a
future change. Craft deliberately does not implement any such client here
-- only the contract it must satisfy, plus the deterministic mock in
`design_mock.py` used for V1 and for tests.

No vendor SDK is imported anywhere in this package.
"""
from __future__ import annotations

from typing import Any, Dict, Protocol, runtime_checkable


@runtime_checkable
class DesignProvider(Protocol):
    """Low-level provider protocol.

    A real implementation would take a normalized design-request payload
    (screens, user flows, fidelity needs, provider policy) and return a
    raw dict payload (before it gets validated into a `DesignResponse` by
    the `DesignCapability` layer). This intentionally stays
    vendor-agnostic: no image-generation SDK objects, no design-tool-
    specific request/response shapes.
    """

    def generate(self, request_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Return a raw (not-yet-validated) structured payload for
        `request_payload` that is expected to conform to `DesignResponse`."""
        ...
