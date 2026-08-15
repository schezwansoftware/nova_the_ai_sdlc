"""Structured output contract for the Architecture Agent.

Compatible with the `architecture.json` artifact shape described in
docs/architecture/v1_architecture.md section 6 (target stack, component
changes, architectural decisions) plus rationale/risks as required by the
Craft task brief.
"""
from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field, field_validator


def _require_nonempty_strings(value: List[str], field_name: str) -> List[str]:
    if not value:
        raise ValueError(f"{field_name} must contain at least one item")
    cleaned = [item.strip() for item in value]
    if any(not item for item in cleaned):
        raise ValueError(f"{field_name} must not contain empty strings")
    return cleaned


class ArchitectureOutputData(BaseModel):
    tech_stack: List[str]
    component_changes: List[str]
    decisions: List[str]
    rationale: str
    risks: List[str]
    requires_ui: bool = Field(
        default=True,
        description=(
            "Whether this feature needs a user-facing UI/UX design -- a web "
            "page, GUI screen, form, dashboard, or any visual interface an "
            "end user interacts with. False for backend-only, headless, "
            "console/CLI-output-only, script, or library changes that have "
            "no interface for a user to look at or interact with (e.g. a "
            "program that only prints to stdout). Defaults to true "
            "(assume a UX design pass is needed) when it cannot be "
            "determined from the requirements."
        ),
    )

    @field_validator("rationale")
    @classmethod
    def _rationale_nonempty(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("rationale must not be empty")
        return value

    @field_validator("tech_stack")
    @classmethod
    def _tech_stack_nonempty(cls, value: List[str]) -> List[str]:
        return _require_nonempty_strings(value, "tech_stack")

    @field_validator("component_changes")
    @classmethod
    def _component_changes_nonempty(cls, value: List[str]) -> List[str]:
        return _require_nonempty_strings(value, "component_changes")

    @field_validator("decisions")
    @classmethod
    def _decisions_nonempty(cls, value: List[str]) -> List[str]:
        return _require_nonempty_strings(value, "decisions")

    @field_validator("risks")
    @classmethod
    def _risks_strings(cls, value: List[str]) -> List[str]:
        # risks may legitimately be an empty list for a very simple change,
        # but any items present must be non-empty strings.
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("risks must not contain empty strings")
        return cleaned
