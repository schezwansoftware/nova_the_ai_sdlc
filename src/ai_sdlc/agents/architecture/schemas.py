"""Structured output contract for the Architecture Agent.

Compatible with the `architecture.json` artifact shape described in
docs/architecture/v1_architecture.md section 6 (target stack, component
changes, architectural decisions) plus rationale/risks as required by the
Craft task brief.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator


def _require_nonempty_strings(value: List[str], field_name: str, *, skip: bool = False) -> List[str]:
    cleaned = [item.strip() for item in value]
    if skip:
        return cleaned
    if not cleaned:
        raise ValueError(f"{field_name} must contain at least one item")
    if any(not item for item in cleaned):
        raise ValueError(f"{field_name} must not contain empty strings")
    return cleaned


class ArchitectureOutputData(BaseModel):
    # Declared first, same reason as POAgentOutputData: Pydantic v2
    # validates fields in declaration order, and the fields below read this
    # one via `ValidationInfo.data` (only already-validated fields appear
    # there).
    needs_clarification: bool = Field(
        default=False,
        description=(
            "True only if the requirements are genuinely too ambiguous to "
            "design a meaningful architecture without guessing at something "
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
    needs_context: bool = Field(
        default=False,
        description=(
            "True only if you are missing specific factual information that "
            "likely already exists in an internal knowledge source (a Jira "
            "ticket, a Confluence page, existing project documentation) -- "
            "not a product/business decision only a human can make (use "
            "`needs_clarification` for that instead). When true, set "
            "`context_query` to one specific, plain-language question "
            "describing exactly what you need to know, and the other fields "
            "may be left empty/minimal; they are not used. At most one of "
            "`needs_clarification`/`needs_context` may be true. Default to "
            "false."
        ),
    )
    context_query: Optional[str] = Field(
        default=None,
        description="Required, non-empty, when `needs_context` is true; otherwise unused.",
    )
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

    @staticmethod
    def _skip_nonempty(info: ValidationInfo) -> bool:
        return bool(info.data.get("needs_clarification") or info.data.get("needs_context"))

    @model_validator(mode="after")
    def _clarification_question_required_when_needed(self) -> "ArchitectureOutputData":
        if self.needs_clarification and not (self.clarification_question or "").strip():
            raise ValueError("clarification_question must not be empty when needs_clarification is true")
        return self

    @model_validator(mode="after")
    def _context_query_required_when_needed(self) -> "ArchitectureOutputData":
        if self.needs_context and not (self.context_query or "").strip():
            raise ValueError("context_query must not be empty when needs_context is true")
        return self

    @model_validator(mode="after")
    def _needs_clarification_and_needs_context_mutually_exclusive(self) -> "ArchitectureOutputData":
        if self.needs_clarification and self.needs_context:
            raise ValueError("at most one of needs_clarification/needs_context may be true")
        return self

    @field_validator("rationale")
    @classmethod
    def _rationale_nonempty(cls, value: str, info: ValidationInfo) -> str:
        value = (value or "").strip()
        if not value and not cls._skip_nonempty(info):
            raise ValueError("rationale must not be empty")
        return value

    @field_validator("tech_stack")
    @classmethod
    def _tech_stack_nonempty(cls, value: List[str], info: ValidationInfo) -> List[str]:
        return _require_nonempty_strings(value, "tech_stack", skip=cls._skip_nonempty(info))

    @field_validator("component_changes")
    @classmethod
    def _component_changes_nonempty(cls, value: List[str], info: ValidationInfo) -> List[str]:
        return _require_nonempty_strings(value, "component_changes", skip=cls._skip_nonempty(info))

    @field_validator("decisions")
    @classmethod
    def _decisions_nonempty(cls, value: List[str], info: ValidationInfo) -> List[str]:
        return _require_nonempty_strings(value, "decisions", skip=cls._skip_nonempty(info))

    @field_validator("risks")
    @classmethod
    def _risks_strings(cls, value: List[str], info: ValidationInfo) -> List[str]:
        # risks may legitimately be an empty list for a very simple change,
        # but any items present must be non-empty strings -- unless this is
        # a clarification response, in which case the field isn't used.
        cleaned = [item.strip() for item in value]
        if not cls._skip_nonempty(info) and any(not item for item in cleaned):
            raise ValueError("risks must not contain empty strings")
        return cleaned
