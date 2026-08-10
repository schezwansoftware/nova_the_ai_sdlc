"""Structured output contract for the PO (Product Owner) Agent.

Mirrors the `POAgentOutput.data` shape described in
docs/architecture/v1_architecture.md section 4 (feature_title, summary,
functional_requirements, non_functional_requirements, out_of_scope,
acceptance_criteria).
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


class POAgentOutputData(BaseModel):
    feature_title: str
    summary: str
    functional_requirements: List[str]
    non_functional_requirements: List[str]
    out_of_scope: List[str]
    acceptance_criteria: List[str]

    @field_validator("feature_title", "summary")
    @classmethod
    def _nonempty_str(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("functional_requirements")
    @classmethod
    def _functional_requirements_nonempty(cls, value: List[str]) -> List[str]:
        return _require_nonempty_strings(value, "functional_requirements")

    @field_validator("non_functional_requirements")
    @classmethod
    def _non_functional_requirements_nonempty(cls, value: List[str]) -> List[str]:
        return _require_nonempty_strings(value, "non_functional_requirements")

    @field_validator("acceptance_criteria")
    @classmethod
    def _acceptance_criteria_nonempty(cls, value: List[str]) -> List[str]:
        return _require_nonempty_strings(value, "acceptance_criteria")

    @field_validator("out_of_scope")
    @classmethod
    def _out_of_scope_strings(cls, value: List[str]) -> List[str]:
        # out_of_scope legitimately may be an empty list (a feature can have
        # nothing explicitly excluded), but any items present must be
        # non-empty strings.
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("out_of_scope must not contain empty strings")
        return cleaned
