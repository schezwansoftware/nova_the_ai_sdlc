"""Structured output contract for the PO (Product Owner) Agent.

Mirrors the `POAgentOutput.data` shape described in
docs/architecture/v1_architecture.md section 4 (feature_title, summary,
functional_requirements, non_functional_requirements, out_of_scope,
acceptance_criteria).
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


class POAgentOutputData(BaseModel):
    # Declared first so Pydantic v2 validates it before any field below
    # reads it via `ValidationInfo.data` (fields are validated in
    # declaration order; `info.data` only ever contains already-validated
    # fields). See `_skip_nonempty` for why the fields below need it.
    needs_clarification: bool = Field(
        default=False,
        description=(
            "True only if the requirement is genuinely too ambiguous to produce "
            "a meaningful requirements spec without guessing at something "
            "important -- a real blocker, not a minor unstated detail you could "
            "reasonably assume. When true, set `clarification_question` to one "
            "specific, answerable question and the other fields may be left "
            "empty/minimal; they are not used. Default to false and proceed "
            "with a reasonable, explicitly stated assumption instead of asking "
            "-- an agent that interrupts for anything less than a real blocker "
            "is worse than one that makes a sensible assumption and says so."
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
    feature_title: str
    summary: str
    functional_requirements: List[str]
    non_functional_requirements: List[str]
    out_of_scope: List[str]
    acceptance_criteria: List[str]

    @staticmethod
    def _skip_nonempty(info: ValidationInfo) -> bool:
        return bool(info.data.get("needs_clarification") or info.data.get("needs_context"))

    @model_validator(mode="after")
    def _clarification_question_required_when_needed(self) -> "POAgentOutputData":
        if self.needs_clarification and not (self.clarification_question or "").strip():
            raise ValueError("clarification_question must not be empty when needs_clarification is true")
        return self

    @model_validator(mode="after")
    def _context_query_required_when_needed(self) -> "POAgentOutputData":
        if self.needs_context and not (self.context_query or "").strip():
            raise ValueError("context_query must not be empty when needs_context is true")
        return self

    @model_validator(mode="after")
    def _needs_clarification_and_needs_context_mutually_exclusive(self) -> "POAgentOutputData":
        if self.needs_clarification and self.needs_context:
            raise ValueError("at most one of needs_clarification/needs_context may be true")
        return self

    @field_validator("feature_title", "summary")
    @classmethod
    def _nonempty_str(cls, value: str, info: ValidationInfo) -> str:
        value = (value or "").strip()
        if not value and not cls._skip_nonempty(info):
            raise ValueError("must not be empty")
        return value

    @field_validator("functional_requirements")
    @classmethod
    def _functional_requirements_nonempty(cls, value: List[str], info: ValidationInfo) -> List[str]:
        return _require_nonempty_strings(value, "functional_requirements", skip=cls._skip_nonempty(info))

    @field_validator("non_functional_requirements")
    @classmethod
    def _non_functional_requirements_nonempty(cls, value: List[str], info: ValidationInfo) -> List[str]:
        return _require_nonempty_strings(value, "non_functional_requirements", skip=cls._skip_nonempty(info))

    @field_validator("acceptance_criteria")
    @classmethod
    def _acceptance_criteria_nonempty(cls, value: List[str], info: ValidationInfo) -> List[str]:
        return _require_nonempty_strings(value, "acceptance_criteria", skip=cls._skip_nonempty(info))

    @field_validator("out_of_scope")
    @classmethod
    def _out_of_scope_strings(cls, value: List[str], info: ValidationInfo) -> List[str]:
        # out_of_scope legitimately may be an empty list (a feature can have
        # nothing explicitly excluded), but any items present must be
        # non-empty strings -- unless this is a clarification response, in
        # which case the field isn't used at all.
        cleaned = [item.strip() for item in value]
        if not cls._skip_nonempty(info) and any(not item for item in cleaned):
            raise ValueError("out_of_scope must not contain empty strings")
        return cleaned
