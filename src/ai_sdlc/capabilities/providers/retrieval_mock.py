"""Deterministic, offline `RetrievalCapability` implementation.

`MockRetrievalProvider` makes zero network calls and never touches the
filesystem or spawns a real agentic-tool subprocess, mirroring
`MockCodingProvider`/`MockDesignProvider`'s guarantees exactly. It derives
a `RetrievalResult` purely from the fields of the incoming
`RetrievalRequest` -- a deterministic context summary and one synthetic
`ContextSnippet` per `request.scope_paths` entry (or a single default
snippet when no scope was hinted), rather than actually reading anything
from `request.repository_path`.

This is the only `RetrievalCapability` implementation the test suite
depends on. The real Claude Agent SDK provider
(`providers/retrieval_claude.py`) requires the `claude-agent-sdk` package
and a working `claude` CLI on `$PATH`; nothing in this package or the
test suite needs either to run.

Test hooks (documented, not a hidden hack) -- same convention as
`MockCodingProvider`/`MockDesignProvider`/`MockReasoningProvider`:
    - `MockRetrievalProvider(force_error="malformed")` makes every
      `retrieve()` call deliberately return a payload that fails
      `RetrievalResult` validation, raising `MalformedResponseError`.
    - `MockRetrievalProvider(force_error="provider_failure")` makes every
      `retrieve()` call raise `ProviderError` before generating anything,
      simulating the harnessed agentic-tool subprocess failing to start.
    - Either can also be passed per-call via `retrieve(..., force_error=...)`,
      which takes precedence over the constructor-level setting for that
      one call.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional

from pydantic import ValidationError

from ai_sdlc.capabilities.retrieval import (
    MalformedResponseError,
    ProviderError,
    RetrievalCapability,
    RetrievalRequest,
    RetrievalResult,
    TerminationReason,
)

_VALID_FORCE_ERRORS = (None, "malformed", "provider_failure")

_DEFAULT_SCOPE = ["README.md"]


class MockRetrievalProvider(RetrievalCapability):
    def __init__(self, force_error: Optional[str] = None):
        if force_error not in _VALID_FORCE_ERRORS:
            raise ValueError(
                f"Unsupported force_error={force_error!r}; expected one of {_VALID_FORCE_ERRORS}"
            )
        self.force_error = force_error

    def retrieve(
        self,
        request: RetrievalRequest,
        *,
        force_error: Optional[str] = None,
    ) -> RetrievalResult:
        effective = force_error if force_error is not None else self.force_error
        if effective not in _VALID_FORCE_ERRORS:
            raise ValueError(
                f"Unsupported force_error={effective!r}; expected one of {_VALID_FORCE_ERRORS}"
            )

        if effective == "provider_failure":
            raise ProviderError(
                "mock_retrieval_provider: simulated agentic-coding-tool subprocess failure"
            )

        payload = self._derive_payload(request)

        if effective == "malformed":
            payload = self._malform(payload)

        try:
            return RetrievalResult(**payload)
        except ValidationError as exc:
            raise MalformedResponseError(
                f"mock_retrieval_provider: generated response failed schema validation: {exc}"
            ) from exc

    # -- payload generation --------------------------------------------

    def _derive_payload(self, request: RetrievalRequest) -> Dict[str, Any]:
        scope = list(request.scope_paths) or list(_DEFAULT_SCOPE)

        snippets = []
        for source_path in scope:
            seed = f"{request.query}:{source_path}"
            short_hash = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]
            snippets.append(
                {
                    "source_path": source_path,
                    "content": f"Mock excerpt ({short_hash}) relevant to: {request.query}",
                    "line_start": 1,
                    "line_end": 1,
                }
            )

        summary = (
            f"Mock context for query {request.query!r} against "
            f"{request.repository_path}, drawn from {len(snippets)} location(s): "
            + ", ".join(scope)
            + "."
        )

        return {
            "query": request.query,
            "context_summary": summary,
            "snippets": snippets,
            "provider_name": "mock_retrieval_provider",
            "steps_used": len(snippets),
            "terminated_reason": TerminationReason.COMPLETED,
            "metadata": {
                "repository_path": request.repository_path,
                "scope_count": len(scope),
            },
        }

    def _malform(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Deliberately corrupt a valid payload so schema validation fails.

        Mirrors `MockCodingProvider._malform`/`MockDesignProvider._malform`:
        drop the required `context_summary` field entirely, rather than
        hand-crafting a schema-specific corruption.
        """
        corrupted = dict(payload)
        corrupted.pop("context_summary", None)
        return corrupted
