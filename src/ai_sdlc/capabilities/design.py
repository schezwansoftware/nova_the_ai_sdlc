"""Design capability abstraction.

This is the boundary the UX Agent (Craft) calls through instead of
depending on a specific visual-design/multimodal vendor SDK. Concrete
providers implement `DesignCapability.generate()`; the UX Agent only ever
sees this interface. Mirrors `capabilities/reasoning.py`'s
`ReasoningCapability` seam exactly, so both capabilities can be reasoned
about and swapped the same way.

Conceptually:

    UX Agent
        |
        v
    DesignCapability   (this module)
        |
        v
    DesignProvider Protocol (providers/design_base.py)
        |
        v
    Configured provider (providers/design_mock.py for V1; real vendor/
    design-tool providers -- a multimodal LLM, an image-generation
    service, or a future Nexus-owned adapter such as a Figma integration
    -- are deferred/out of scope for this layer; see
    `docs/architecture/v1_architecture.md` section 3, "Design Capability
    Adapter" row)

Request/response shape (Craft's derived design):
`docs/architecture/v1_architecture.md` only gives the conceptual contract
for this capability -- "design request payloads, provider policy, artifact
fidelity needs" in, "structured design artifacts, artifact metadata,
provider response envelopes" out (section 3) -- it does not transcribe a
literal schema the way it does for the PO Agent's input/output contract.
`DesignRequest`/`DesignArtifact`/`DesignResponse` below are therefore
Craft's derived schemas, following the same field-naming/density and
field_validator convention already established by
`POAgentOutputData`/`ArchitectureOutputData`/`UXOutputData`, not a literal
doc transcription. In particular:
  - `DesignRequest.fidelities` carries the caller-supplied "fidelity
    needs" (which of LO_FI/MID_FI/HI_FI to produce this call). The UX
    Agent stays stateless and does not itself decide where a design is in
    its lo-fi -> mid-fi -> hi-fi progression -- that decision (driven by
    `.ai-sdlc/ux.json`'s `current_fidelity` plus the approval/revision
    loop) belongs to Core/Orion, exactly as `revision_feedback` is
    threaded into agent inputs today. Defaults to all three levels when
    the caller does not specify (see `agents/ux/ux_agent.py`).
  - `DesignResponse` is the full "provider response envelope": the
    structured artifacts plus `provider_name`/`metadata`, mirroring how a
    real vendor response would be normalized before validation.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, List

from pydantic import BaseModel, Field, field_validator


class ProviderError(Exception):
    """Raised when the underlying design provider fails to produce a
    response at all (e.g. network failure, rate limit, vendor outage).
    Callers should generally treat this as a retryable condition."""


class MalformedResponseError(Exception):
    """Raised when the underlying design provider *did* respond, but the
    response could not be parsed/validated into a `DesignResponse`."""


class FidelityLevel(str, Enum):
    """Progressive visual-design fidelity levels, matching the
    `ux.json` artifact schema in `docs/architecture/v1_architecture.md`
    section 6."""

    LO_FI = "LO_FI"
    MID_FI = "MID_FI"
    HI_FI = "HI_FI"


class DesignPackageStatus(str, Enum):
    """Artifact/package review status, matching the `ux.json` schema in
    `docs/architecture/v1_architecture.md` section 6. The UX Agent only
    ever produces `DRAFT` output -- transitions to `IN_REVIEW`/`APPROVED`/
    `REJECTED`/`SUPERSEDED` are driven by the human approval loop, which
    is Core/Orion's job, not this capability's or the agent's."""

    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


def _nonempty_str(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise ValueError("must not be empty")
    return value


class DesignRequest(BaseModel):
    """Input payload to `DesignCapability.generate()`: the design request
    payload, provider policy, and fidelity needs described in
    `docs/architecture/v1_architecture.md` section 3."""

    flow_title: str
    summary: str
    screens: List[str]
    user_flows: List[str] = Field(default_factory=list)
    fidelities: List[FidelityLevel] = Field(
        default_factory=lambda: [
            FidelityLevel.LO_FI,
            FidelityLevel.MID_FI,
            FidelityLevel.HI_FI,
        ]
    )
    #: Opaque, provider-specific hints (e.g. max artifacts per fidelity,
    #: style guidance). The capability/provider may interpret these; the
    #: UX Agent never inspects vendor-specific keys itself.
    provider_policy: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("flow_title", "summary")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        return _nonempty_str(value)

    @field_validator("screens")
    @classmethod
    def _screens_nonempty(cls, value: List[str]) -> List[str]:
        if not value:
            raise ValueError("screens must contain at least one item")
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("screens must not contain empty strings")
        return cleaned

    @field_validator("fidelities")
    @classmethod
    def _fidelities_nonempty(cls, value: List[FidelityLevel]) -> List[FidelityLevel]:
        if not value:
            raise ValueError("fidelities must contain at least one item")
        return value


class DesignArtifact(BaseModel):
    """A single generated visual design artifact for one screen at one
    fidelity level.

    Scoped to what a capability response needs to carry. Durable
    persistence into `.ai-sdlc/artifacts/ux/`, version numbers, and
    approval `status` (`DRAFT`/`APPROVED`/...) are added later by
    Core/Orion when the artifact is written to the workflow's artifact
    manifest (see "UX Artifact Persistence Model",
    `docs/architecture/v1_architecture.md` section 6) -- this capability
    only returns the artifact's *content* reference and identity.
    """

    artifact_id: str
    fidelity: FidelityLevel
    screen_ref: str
    mime_type: str
    description: str
    #: Opaque reference to the generated payload. The mock provider uses a
    #: synthetic `mock://` URI since it never touches the filesystem; a
    #: real provider would return a vendor URL, temp file path, or inline
    #: content reference here instead -- the UX Agent treats it as an
    #: opaque string either way.
    payload_ref: str

    @field_validator(
        "artifact_id", "screen_ref", "mime_type", "description", "payload_ref"
    )
    @classmethod
    def _nonempty(cls, value: str) -> str:
        return _nonempty_str(value)


class DesignResponse(BaseModel):
    """Provider response envelope returned by `DesignCapability.generate()`:
    structured design artifacts + artifact metadata + provider response
    envelope, per `docs/architecture/v1_architecture.md` section 3."""

    artifacts: List[DesignArtifact]
    provider_name: str
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("provider_name")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        return _nonempty_str(value)

    @field_validator("artifacts")
    @classmethod
    def _artifacts_nonempty(cls, value: List[DesignArtifact]) -> List[DesignArtifact]:
        if not value:
            raise ValueError("artifacts must contain at least one item")
        return value


class DesignCapability(ABC):
    """Abstract design capability.

    The UX Agent calls `generate(request)` and receives back a validated
    `DesignResponse` containing structured design artifacts (one per
    screen per requested fidelity level) plus provider metadata.
    Implementations are responsible for whatever generation/parsing/
    validation is needed to satisfy `DesignResponse`, and must raise
    `ProviderError` or `MalformedResponseError` (never an arbitrary/
    unrelated exception) on failure -- mirroring `ReasoningCapability`'s
    failure contract exactly, so the UX Agent can handle both capabilities
    uniformly.
    """

    @abstractmethod
    def generate(self, request: DesignRequest) -> DesignResponse:
        """Run a design-generation call and return a validated
        `DesignResponse`.

        Raises:
            ProviderError: the provider could not produce a response.
            MalformedResponseError: the provider responded, but the
                response does not satisfy `DesignResponse`.
        """
        raise NotImplementedError()
