"""Structured output contract for the UX Agent.

`docs/architecture/v1_architecture.md` does not give the UX Agent a full
input/output contract example the way it does for the PO Agent (section 4)
-- only a state-artifact hint: `ux.json  # UX Agent state (wireframes, user
flows)` (line 532). This schema is therefore Craft's derived design,
following the same field-naming/density convention established by
`POAgentOutputData`/`ArchitectureOutputData` rather than a literal doc
transcription.
"""
from __future__ import annotations

from typing import List

from pydantic import BaseModel, field_validator


def _require_nonempty_strings(value: List[str], field_name: str) -> List[str]:
    if not value:
        raise ValueError(f"{field_name} must contain at least one item")
    cleaned = [item.strip() for item in value]
    if any(not item for item in cleaned):
        raise ValueError(f"{field_name} must not contain empty strings")
    return cleaned


class UXOutputData(BaseModel):
    flow_title: str
    summary: str
    user_flows: List[str]
    screens: List[str]
    accessibility_considerations: List[str]

    @field_validator("flow_title", "summary")
    @classmethod
    def _nonempty_str(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("user_flows")
    @classmethod
    def _user_flows_nonempty(cls, value: List[str]) -> List[str]:
        return _require_nonempty_strings(value, "user_flows")

    @field_validator("screens")
    @classmethod
    def _screens_nonempty(cls, value: List[str]) -> List[str]:
        return _require_nonempty_strings(value, "screens")

    @field_validator("accessibility_considerations")
    @classmethod
    def _accessibility_considerations_nonempty(cls, value: List[str]) -> List[str]:
        return _require_nonempty_strings(value, "accessibility_considerations")
