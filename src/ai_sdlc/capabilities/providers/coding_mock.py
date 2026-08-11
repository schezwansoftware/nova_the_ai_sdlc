"""Deterministic, offline `CodingCapability` implementation.

`MockCodingProvider` makes zero network calls and never touches the
filesystem or spawns a real agentic-coding-tool subprocess, mirroring
`MockReasoningProvider`/`MockDesignProvider`'s guarantees exactly. It
derives a `CodingResult` purely from the fields of the incoming
`CodingRequest` -- a deterministic branch name and file list drawn from
`request.task_title`/`components_affected`, and a `self_check` outcome
derived from whether `request.build_commands`/`test_commands` were
supplied (see `coding.py`'s `SelfCheckResult.skipped` /
`NO_SELF_CHECK_COMMANDS_REASON` for Forge's documented Open Question 7
answer, which this mock follows exactly so tests exercise the same skip
semantics the real provider uses).

This is the only `CodingCapability` implementation the test suite depends
on. The real Claude Agent SDK provider (`providers/claude_sdk.py`)
requires the `claude-agent-sdk` package and a working `claude` CLI on
`$PATH`; nothing in this package or the test suite needs either to run.

Test hooks (documented, not a hidden hack) -- same convention as
`MockReasoningProvider`/`MockDesignProvider`:
    - `MockCodingProvider(force_error="malformed")` makes every
      `execute()` call deliberately return a payload that fails
      `CodingResult` validation, raising `MalformedResponseError`.
    - `MockCodingProvider(force_error="provider_failure")` makes every
      `execute()` call raise `ProviderError` before generating anything,
      simulating the agentic-coding-tool subprocess failing to start.
    - Either can also be passed per-call via `execute(..., force_error=...)`,
      which takes precedence over the constructor-level setting for that
      one call.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional

from pydantic import ValidationError

from ai_sdlc.capabilities.coding import (
    NO_SELF_CHECK_COMMANDS_REASON,
    CodingCapability,
    CodingRequest,
    CodingResult,
    MalformedResponseError,
    ProviderError,
    SelfCheckResult,
    TerminationReason,
)

_VALID_FORCE_ERRORS = (None, "malformed", "provider_failure")


def _slugify(value: str) -> str:
    slug = "".join(c.lower() if c.isalnum() else "-" for c in value).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "change"


class MockCodingProvider(CodingCapability):
    def __init__(self, force_error: Optional[str] = None):
        if force_error not in _VALID_FORCE_ERRORS:
            raise ValueError(
                f"Unsupported force_error={force_error!r}; expected one of {_VALID_FORCE_ERRORS}"
            )
        self.force_error = force_error

    def execute(
        self,
        request: CodingRequest,
        *,
        force_error: Optional[str] = None,
    ) -> CodingResult:
        effective = force_error if force_error is not None else self.force_error
        if effective not in _VALID_FORCE_ERRORS:
            raise ValueError(
                f"Unsupported force_error={effective!r}; expected one of {_VALID_FORCE_ERRORS}"
            )

        if effective == "provider_failure":
            raise ProviderError(
                "mock_coding_provider: simulated agentic-coding-tool subprocess failure"
            )

        payload = self._derive_payload(request)

        if effective == "malformed":
            payload = self._malform(payload)

        try:
            return CodingResult(**payload)
        except ValidationError as exc:
            raise MalformedResponseError(
                f"mock_coding_provider: generated response failed schema validation: {exc}"
            ) from exc

    # -- payload generation --------------------------------------------

    def _derive_payload(self, request: CodingRequest) -> Dict[str, Any]:
        seed = f"{request.task_title}:{request.working_tree_path}"
        short_hash = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]
        branch_name = f"forge/{_slugify(request.task_title)}-{short_hash}"

        files_changed = [
            f"src/{_slugify(component)}.py" for component in request.components_affected
        ] or [f"src/{_slugify(request.task_title)}.py"]

        self_check = self._derive_self_check(request)

        return {
            "branch_name": branch_name,
            "files_changed": files_changed,
            "self_check": self_check,
            "provider_name": "mock_coding_provider",
            "steps_used": min(len(files_changed) * 2, 40),
            "terminated_reason": TerminationReason.COMPLETED,
            "summary": f"Applied changes for: {request.task_title}.",
            "metadata": {
                "task_title": request.task_title,
                "component_count": len(request.components_affected),
            },
        }

    def _derive_self_check(self, request: CodingRequest) -> SelfCheckResult:
        commands_run = list(request.build_commands) + list(request.test_commands)
        if not commands_run:
            return SelfCheckResult.skipped(NO_SELF_CHECK_COMMANDS_REASON)
        return SelfCheckResult(
            build_passed=bool(request.build_commands) or None,
            tests_passed=bool(request.test_commands) or None,
            commands_run=commands_run,
        )

    def _malform(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Deliberately corrupt a valid payload so schema validation fails.

        Mirrors `MockReasoningProvider._malform`/`MockDesignProvider._malform`:
        drop the required `branch_name` field entirely, rather than hand-
        crafting a schema-specific corruption.
        """
        corrupted = dict(payload)
        corrupted.pop("branch_name", None)
        return corrupted
