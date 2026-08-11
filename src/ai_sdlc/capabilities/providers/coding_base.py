"""Provider-facing protocol for `CodingCapability`.

Mirrors `providers/base.py`'s `ReasoningProvider` and
`providers/design_base.py`'s `DesignProvider` Protocols exactly. This is
the seam a *real* agentic-coding-tool client (GitHub Copilot via
`github/copilot-sdk`, Claude Agent SDK, or a future provider) implements.
Neither concrete provider in this module subclasses this Protocol
directly -- like `MockReasoningProvider`/`MockDesignProvider`, they
subclass `CodingCapability` itself and implement `execute()` at that
level, since (per `coding.py`'s docstring) there is no lower-level "raw
dict payload" seam that makes sense for an agentic loop the way there is
for a single completion/generation call. This Protocol is kept for
interface-shape documentation and `isinstance()`/`runtime_checkable`
structural typing, consistent with the existing two capabilities' seam
pattern, even though it isn't inherited from in this package today.

No vendor SDK is imported anywhere in this package.
"""
from __future__ import annotations

from typing import Any, Dict, Protocol, runtime_checkable


@runtime_checkable
class CodingProvider(Protocol):
    """Low-level provider protocol.

    A real implementation would take a normalized task/context payload
    (task brief, standards context, workspace path, tool policy, self-
    check commands) and return a raw dict payload (before it gets
    validated into a `CodingResult` by the `CodingCapability` layer).
    This intentionally stays vendor-agnostic: no agentic-coding-SDK
    session objects, no vendor-specific event/message shapes.
    """

    def execute(self, request_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Run the provider's agentic coding loop for `request_payload`
        and return a raw (not-yet-validated) structured payload that is
        expected to conform to `CodingResult`."""
        ...
