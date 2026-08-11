"""Deterministic, offline `CodingCapability` implementation.

`MockCodingProvider` makes zero network calls, never touches the real
filesystem, and never spawns a subprocess -- mirroring
`MockReasoningProvider`/`MockDesignProvider`'s guarantees exactly. It
derives a `CodingResult` purely from the fields of the incoming
`CodingRequest`: a deterministic branch name from `task_summary`, a
deterministic (small, synthetic) set of changed files derived from
`task_brief`'s content, and a self-check outcome derived from
`self_check_commands` alone -- it never actually runs
`self_check_commands` against `workspace_path`, since doing so would
require the isolated worktree the real provider is responsible for
creating (section 4/10 of the architecture doc), which is out of scope
for a capability-contract mock.

This is the test-suite-safe `CodingCapability` implementation Copilot
Forge ships. The real GitHub Copilot SDK-backed provider
(`coding_copilot.py`) is explicitly not exercised by the default test
suite, matching this project's existing convention that tests never
require real provider credentials.

Section 20 open question 7 (self-check scope when no build/test commands
exist): this mock's answer -- and the documented default this provider
family assumes until the Developer Agent decides otherwise -- is
**skip self-checking entirely** when `self_check_commands` is empty,
recording why in `SelfCheckResult.skipped_reason` rather than reporting a
false `build_passed=False`/`tests_passed=False` (which would misrepresent
"nothing to check" as "checked and failed"). Blocking/requesting
clarification or falling back to a Standards-Layer-declared per-tech-stack
default are both reasonable alternatives the doc leaves open; this
provider does not implement either, since deciding *which* commands to
fall back to is arguably the Developer Agent's/Standards Resolver's job
upstream of this capability, not this capability's own. See
`coding_copilot.py` for the same documented choice on the real provider.

Test hooks (documented, not a hidden hack) -- same convention as
`MockReasoningProvider`/`MockDesignProvider`:
    - `MockCodingProvider(force_error="malformed")` makes every
      `execute()` call deliberately return a payload that fails
      `CodingResult` validation, raising `MalformedResponseError`.
    - `MockCodingProvider(force_error="provider_failure")` makes every
      `execute()` call raise `ProviderError` before doing anything,
      simulating the underlying agentic tool crashing or failing to
      authenticate.
    - `MockCodingProvider(force_error="self_check_failed")` makes every
      `execute()` call return a result whose self-check reports a build
      or test failure -- distinct from `malformed`/`provider_failure`
      because a failed self-check is a *valid*, schema-conforming
      `CodingResult` (the loop finished, self-checking just didn't pass),
      not a provider failure.
    - Any of the above can also be passed per-call via
      `execute(..., force_error=...)`, which takes precedence over the
      constructor-level setting for that one call.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from ai_sdlc.capabilities.coding import (
    CodingCapability,
    CodingRequest,
    CodingResult,
    MalformedResponseError,
    ProviderError,
)

_VALID_FORCE_ERRORS = (None, "malformed", "provider_failure", "self_check_failed")

_ADD_KEYWORDS = ("add", "create", "introduce", "new ", "implement")


def _slugify(value: str) -> str:
    slug = "".join(c.lower() if c.isalnum() else "-" for c in value).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "change"


def _sentences(text: str) -> List[str]:
    raw = re.split(r"[\n\r.;]+", text or "")
    return [s.strip() for s in raw if s.strip()]


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
                "mock_coding_provider: simulated provider/network failure"
            )

        payload = self._derive_payload(request, self_check_failed=effective == "self_check_failed")

        if effective == "malformed":
            payload = self._malform(payload)

        try:
            return CodingResult(**payload)
        except ValidationError as exc:
            raise MalformedResponseError(
                f"mock_coding_provider: generated response failed schema validation: {exc}"
            ) from exc

    # -- payload generation --------------------------------------------

    def _derive_payload(
        self, request: CodingRequest, *, self_check_failed: bool
    ) -> Dict[str, Any]:
        slug = _slugify(request.task_summary)
        seed = f"{request.task_summary}:{request.workspace_path}:{request.base_branch}"
        short_hash = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]
        branch_name = f"forge/{slug}-{short_hash}"

        sentences = _sentences(request.task_brief)
        files_changed = self._derive_files_changed(slug, sentences)
        self_check = self._derive_self_check(request, self_check_failed=self_check_failed)

        return {
            "branch_name": branch_name,
            "files_changed": files_changed,
            "self_check": self_check,
            "summary": self._derive_summary(request, sentences),
            "steps_used": min(request.max_steps, len(files_changed) + 1),
            "provider_name": "mock_coding_provider",
            "metadata": {
                "task_summary": request.task_summary,
                "revision": request.revision_feedback is not None,
            },
        }

    def _derive_files_changed(
        self, slug: str, sentences: List[str]
    ) -> List[Dict[str, str]]:
        basis = sentences[:3] or [slug]
        changes: List[Dict[str, str]] = []
        for i, sentence in enumerate(basis):
            lowered = sentence.lower()
            change_type = "ADDED" if any(k in lowered for k in _ADD_KEYWORDS) else "MODIFIED"
            changes.append(
                {
                    "path": f"src/{slug}/change_{i:02d}.py",
                    "change_type": change_type,
                }
            )
        return changes

    def _derive_self_check(
        self, request: CodingRequest, *, self_check_failed: bool
    ) -> Dict[str, Any]:
        if not request.self_check_commands:
            return {
                "build_passed": None,
                "tests_passed": None,
                "commands_run": [],
                "skipped_reason": "no self_check_commands configured for this workspace",
            }
        return {
            "build_passed": not self_check_failed,
            "tests_passed": not self_check_failed,
            "commands_run": list(request.self_check_commands),
            "skipped_reason": None,
        }

    def _derive_summary(self, request: CodingRequest, sentences: List[str]) -> str:
        basis = ". ".join(sentences[:2]).strip()
        if not basis:
            basis = request.task_summary
        prefix = "Revised change" if request.revision_feedback else "Change"
        return f"{prefix} addressing: {basis}."

    def _malform(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Deliberately corrupt a valid payload so schema validation fails.

        Mirrors `MockReasoningProvider._malform`/`MockDesignProvider._malform`:
        drop `files_changed` entirely so `CodingResult`'s required-list
        typing still parses structurally but a downstream caller relying
        on "at least the file it asked about" logic would notice --
        chosen over dropping a scalar field so this stays meaningful
        regardless of which fields Claude Forge's reconciled schema keeps.
        """
        corrupted = dict(payload)
        corrupted.pop("files_changed", None)
        return corrupted
