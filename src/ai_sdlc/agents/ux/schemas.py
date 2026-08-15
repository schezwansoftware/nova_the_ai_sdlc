"""Structured output contract for the UX Agent.

`docs/architecture/v1_architecture.md` does not give the UX Agent a full
input/output contract example the way it does for the PO Agent (section 4)
-- only a state-artifact hint: `ux.json  # UX Agent state (wireframes, user
flows)` (line 532, original revision). This schema is therefore Craft's
derived design, following the same field-naming/density convention
established by `POAgentOutputData`/`ArchitectureOutputData` rather than a
literal doc transcription.

`VisualDesignPackage` (added alongside `UXOutputData`, not merged into it)
is Craft's derived structure for the progressive lo-fi/mid-fi/hi-fi visual
design artifacts described in the "Progressive UX Design as a First-Class
Artifact" bullet and the illustrative `visual_designs` shape in section 4
of the same doc (`{"lo_fi": [...], "mid_fi": [...], "hi_fi": [...]}`). It
is kept as a *sibling* schema rather than new fields bolted directly onto
`UXOutputData` because:
  - `UXOutputData` instances are produced by `ReasoningCapability.complete()`,
    whose mock implementation derives every field generically from the
    schema's field *type* (str vs list[str]); a nested object field would
    break that generic derivation.
  - Visual artifacts come from a structurally different capability
    (`DesignCapability`, not `ReasoningCapability`) with its own
    request/response contract and failure modes.
`UXAgent.execute()` (see `ux_agent.py`) validates each independently, then
merges `visual_designs`/`design_package_status` into the final result dict
next to the flattened `UXOutputData` fields, matching the flat top-level
shape shown in the architecture doc's `UXAgentOutput.data` example.
Existing consumers that only construct `UXOutputData(**result.data)`
continue to work unaffected: Pydantic ignores unrecognized extra keys by
default, so the additive keys are purely additive, not breaking.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from ai_sdlc.capabilities.design import DesignArtifact, FidelityLevel


def _require_nonempty_strings(value: List[str], field_name: str, *, skip: bool = False) -> List[str]:
    cleaned = [item.strip() for item in value]
    if skip:
        return cleaned
    if not cleaned:
        raise ValueError(f"{field_name} must contain at least one item")
    if any(not item for item in cleaned):
        raise ValueError(f"{field_name} must not contain empty strings")
    return cleaned


class UXOutputData(BaseModel):
    # Declared first, same reason as POAgentOutputData/ArchitectureOutputData:
    # Pydantic v2 validates fields in declaration order, and the fields
    # below read this one via `ValidationInfo.data` (only already-validated
    # fields appear there).
    needs_clarification: bool = Field(
        default=False,
        description=(
            "True only if the requirements are genuinely too ambiguous to "
            "design a meaningful UX flow without guessing at something "
            "important -- a real blocker, not a minor unstated detail you "
            "could reasonably assume. When true, set `clarification_question` "
            "to one specific, answerable question and the other fields may be "
            "left empty/minimal; they are not used. Default to false and "
            "proceed with a reasonable, explicitly stated assumption instead "
            "of asking -- an agent that interrupts for anything less than a "
            "real blocker is worse than one that makes a sensible assumption "
            "and says so."
        ),
    )
    clarification_question: Optional[str] = Field(
        default=None,
        description="Required, non-empty, when `needs_clarification` is true; otherwise unused.",
    )
    flow_title: str
    summary: str
    user_flows: List[str]
    screens: List[str]
    accessibility_considerations: List[str]

    @staticmethod
    def _skip_nonempty(info: ValidationInfo) -> bool:
        return bool(info.data.get("needs_clarification"))

    @model_validator(mode="after")
    def _clarification_question_required_when_needed(self) -> "UXOutputData":
        if self.needs_clarification and not (self.clarification_question or "").strip():
            raise ValueError("clarification_question must not be empty when needs_clarification is true")
        return self

    @field_validator("flow_title", "summary")
    @classmethod
    def _nonempty_str(cls, value: str, info: ValidationInfo) -> str:
        value = (value or "").strip()
        if not value and not cls._skip_nonempty(info):
            raise ValueError("must not be empty")
        return value

    @field_validator("user_flows")
    @classmethod
    def _user_flows_nonempty(cls, value: List[str], info: ValidationInfo) -> List[str]:
        return _require_nonempty_strings(value, "user_flows", skip=cls._skip_nonempty(info))

    @field_validator("screens")
    @classmethod
    def _screens_nonempty(cls, value: List[str], info: ValidationInfo) -> List[str]:
        return _require_nonempty_strings(value, "screens", skip=cls._skip_nonempty(info))

    @field_validator("accessibility_considerations")
    @classmethod
    def _accessibility_considerations_nonempty(cls, value: List[str], info: ValidationInfo) -> List[str]:
        return _require_nonempty_strings(value, "accessibility_considerations", skip=cls._skip_nonempty(info))


class VisualDesignPackage(BaseModel):
    """Progressive lo-fi/mid-fi/hi-fi visual design artifacts for a UX
    flow, grouped by fidelity to match the illustrative `visual_designs`
    shape in `docs/architecture/v1_architecture.md` section 4 (UX Agent
    Contract). Built by `UXAgent` from a validated `DesignResponse`
    (`capabilities/design.py`) -- each `DesignArtifact` is already
    schema-validated on its own; the `_fidelity_matches_bucket` check
    below additionally guards against a bucketing bug placing an artifact
    under the wrong fidelity key.

    Persistence into `.ai-sdlc/artifacts/ux/`, artifact versioning, and
    approval-status transitions (`DRAFT` -> `APPROVED`/`REJECTED`/...)
    remain Core/Orion's job (see "UX Artifact Persistence Model" in the
    same doc) -- this is only the in-memory structured result the UX
    Agent returns for a single invocation.
    """

    lo_fi: List[DesignArtifact] = []
    mid_fi: List[DesignArtifact] = []
    hi_fi: List[DesignArtifact] = []

    @model_validator(mode="after")
    def _fidelity_matches_bucket(self) -> "VisualDesignPackage":
        expected = {
            "lo_fi": FidelityLevel.LO_FI,
            "mid_fi": FidelityLevel.MID_FI,
            "hi_fi": FidelityLevel.HI_FI,
        }
        for bucket_name, fidelity in expected.items():
            bucket = getattr(self, bucket_name)
            mismatched = [a for a in bucket if a.fidelity != fidelity]
            if mismatched:
                raise ValueError(
                    f"{bucket_name} must only contain artifacts with fidelity={fidelity.value}"
                )
        return self

    @model_validator(mode="after")
    def _at_least_one_artifact(self) -> "VisualDesignPackage":
        if not (self.lo_fi or self.mid_fi or self.hi_fi):
            raise ValueError(
                "visual design package must contain at least one artifact across all fidelities"
            )
        return self
