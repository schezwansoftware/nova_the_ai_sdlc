"""Deterministic, offline `DesignCapability` implementation.

`MockDesignProvider` makes zero network calls and never touches the
filesystem or a real design/image-generation vendor, mirroring
`MockReasoningProvider`'s guarantees exactly. It derives a `DesignResponse`
purely from the fields of the incoming `DesignRequest` -- one
`DesignArtifact` per (fidelity, screen) pair drawn from
`request.fidelities` x `request.screens`, with deterministic
`artifact_id`s/`payload_ref`s that a real provider would instead produce
by generating and storing an actual design file or image. There is no
`.ai-sdlc/artifacts/ux/` filesystem interaction here -- persisting the
referenced payload durably is Core/Orion's job (see "UX Artifact
Persistence Model", `docs/architecture/v1_architecture.md` section 6).

This is the only `DesignCapability` implementation Craft ships for V1.
Real vendor/design-tool providers (a multimodal LLM, an image-generation
service, or e.g. a future Nexus-owned Figma adapter) are explicitly out of
scope here (see `providers/design_base.py`) and can be added later without
any agent code changing, because the UX Agent only ever depends on
`DesignCapability`.

Test hooks (documented, not a hidden hack) -- same convention as
`MockReasoningProvider`:
    - `MockDesignProvider(force_error="malformed")` makes every
      `generate()` call deliberately return a payload that fails
      `DesignResponse` validation, raising `MalformedResponseError`.
    - `MockDesignProvider(force_error="provider_failure")` makes every
      `generate()` call raise `ProviderError` before generating anything,
      simulating a network/vendor outage.
    - Either can also be passed per-call via `generate(..., force_error=...)`,
      which takes precedence over the constructor-level setting for that
      one call.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from ai_sdlc.capabilities.design import (
    DesignCapability,
    DesignRequest,
    DesignResponse,
    FidelityLevel,
    MalformedResponseError,
    ProviderError,
)

_VALID_FORCE_ERRORS = (None, "malformed", "provider_failure")

_MIME_BY_FIDELITY = {
    FidelityLevel.LO_FI: "image/svg+xml",
    FidelityLevel.MID_FI: "image/svg+xml",
    FidelityLevel.HI_FI: "image/png",
}

_DESCRIPTION_BY_FIDELITY = {
    FidelityLevel.LO_FI: "Low-fidelity wireframe sketch of {screen}, outlining layout and primary content areas for: {summary}",
    FidelityLevel.MID_FI: "Mid-fidelity layout of {screen} with primary components and interaction points laid out for: {summary}",
    FidelityLevel.HI_FI: "High-fidelity, developer-handoff-ready mockup of {screen} for: {summary}",
}


def _slugify(value: str) -> str:
    slug = "".join(c.lower() if c.isalnum() else "-" for c in value).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "artifact"


class MockDesignProvider(DesignCapability):
    def __init__(self, force_error: Optional[str] = None):
        if force_error not in _VALID_FORCE_ERRORS:
            raise ValueError(
                f"Unsupported force_error={force_error!r}; expected one of {_VALID_FORCE_ERRORS}"
            )
        self.force_error = force_error

    def generate(
        self,
        request: DesignRequest,
        *,
        force_error: Optional[str] = None,
    ) -> DesignResponse:
        effective = force_error if force_error is not None else self.force_error
        if effective not in _VALID_FORCE_ERRORS:
            raise ValueError(
                f"Unsupported force_error={effective!r}; expected one of {_VALID_FORCE_ERRORS}"
            )

        if effective == "provider_failure":
            raise ProviderError(
                "mock_design_provider: simulated provider/network failure"
            )

        payload = self._derive_payload(request)

        if effective == "malformed":
            payload = self._malform(payload)

        try:
            return DesignResponse(**payload)
        except ValidationError as exc:
            raise MalformedResponseError(
                f"mock_design_provider: generated response failed schema validation: {exc}"
            ) from exc

    # -- payload generation --------------------------------------------

    def _derive_payload(self, request: DesignRequest) -> Dict[str, Any]:
        artifacts: List[Dict[str, Any]] = []
        for fidelity in request.fidelities:
            for screen in request.screens:
                seed = f"{request.flow_title}:{fidelity.value}:{screen}"
                short_hash = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]
                artifact_id = (
                    f"ux-art-{_slugify(fidelity.value)}-{_slugify(screen)}-{short_hash}"
                )
                artifacts.append(
                    {
                        "artifact_id": artifact_id,
                        "fidelity": fidelity,
                        "screen_ref": screen,
                        "mime_type": _MIME_BY_FIDELITY.get(
                            fidelity, "application/octet-stream"
                        ),
                        "description": self._describe(fidelity, screen, request),
                        "payload_ref": f"mock://design-artifact/{artifact_id}",
                    }
                )
        return {
            "artifacts": artifacts,
            "provider_name": "mock_design_provider",
            "metadata": {
                "flow_title": request.flow_title,
                "screen_count": len(request.screens),
                "fidelity_count": len(request.fidelities),
            },
        }

    def _describe(
        self, fidelity: FidelityLevel, screen: str, request: DesignRequest
    ) -> str:
        template = _DESCRIPTION_BY_FIDELITY[fidelity]
        return template.format(screen=screen, summary=request.summary)

    def _malform(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Deliberately corrupt a valid payload so schema validation fails.

        Mirrors `MockReasoningProvider._malform`: drop the `artifacts`
        list entirely so `DesignResponse`'s non-empty-list validator
        rejects it, rather than hand-crafting a schema-specific
        corruption.
        """
        corrupted = dict(payload)
        corrupted["artifacts"] = []
        return corrupted
