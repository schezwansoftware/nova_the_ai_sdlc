"""Provider-facing protocol for `CodingCapability`.

Mirrors `providers/base.py`'s `ReasoningProvider` / `providers/design_base
.py`'s `DesignProvider` Protocols exactly. Unlike those two, `Coding
Capability` already ships a real (not deferred/out-of-scope) provider for
V1 -- `providers/claude_sdk.py` -- because harnessing an existing agentic
coding tool is this capability's whole point (section 18 Decision 4), not
a later add-on. This Protocol documents the seam a *second* low-level
vendor client would need to satisfy if it were wired in below
`CodingCapability` directly rather than by subclassing `CodingCapability`
itself the way `MockCodingProvider` and the real Claude Agent SDK provider
both do -- kept for the same structural-documentation reason `base.py`/
`design_base.py` are kept, not because anything in this package
constructs one today.

No vendor SDK is imported anywhere in this file.
"""
from __future__ import annotations

from typing import Any, Dict, Protocol, runtime_checkable


@runtime_checkable
class CodingProvider(Protocol):
    """Low-level provider protocol.

    A real implementation would take a normalized coding-task payload
    (task/context, working-tree path, allowed-tool/command policy) and
    return a raw dict payload (before it gets validated into a
    `CodingResult` by the `CodingCapability` layer). This intentionally
    stays vendor-agnostic: no agentic-coding-SDK-specific session/message
    objects.
    """

    def execute(self, request_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Return a raw (not-yet-validated) structured payload for
        `request_payload` that is expected to conform to `CodingResult`."""
        ...
