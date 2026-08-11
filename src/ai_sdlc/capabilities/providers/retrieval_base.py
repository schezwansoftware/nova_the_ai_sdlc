"""Provider-facing protocol for `RetrievalCapability`.

Mirrors `providers/base.py`/`providers/design_base.py`/`providers/
coding_base.py`'s low-level Protocols exactly -- kept for the same
structural-documentation reason those are kept (the seam a low-level
vendor client would need to satisfy if wired in below `RetrievalCapability`
directly, rather than by subclassing `RetrievalCapability` itself the way
`MockRetrievalProvider` and the real Claude Agent SDK provider both do),
not because anything in this package constructs one today. Sage's future
dual-index provider (section 9's "Future/Scale Path", section 18
Decision 6) is the most likely eventual implementer of this shape.

No vendor SDK is imported anywhere in this file.
"""
from __future__ import annotations

from typing import Any, Dict, Protocol, runtime_checkable


@runtime_checkable
class RetrievalProvider(Protocol):
    """Low-level provider protocol.

    A real implementation would take a normalized retrieval-query payload
    (query, repository/scope context) and return a raw dict payload
    (before it gets validated into a `RetrievalResult` by the
    `RetrievalCapability` layer). This intentionally stays vendor-
    agnostic: no agentic-tool-SDK-specific session/message objects, no
    vector-store-specific query objects.
    """

    def retrieve(self, request_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Return a raw (not-yet-validated) structured payload for
        `request_payload` that is expected to conform to `RetrievalResult`."""
        ...
