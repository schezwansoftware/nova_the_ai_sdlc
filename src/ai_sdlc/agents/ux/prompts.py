"""Prompt construction for the UX Agent.

Plain text only -- no provider-specific message roles/formatting. Same
structure as `agents/architecture/prompts.py`: the structured requirements
dict is rendered as `- key: value` lines inside a triple-double-quoted
block (required by `MockReasoningProvider._extract_content()`, which parses
specifically for triple-quoted blocks).
"""
from __future__ import annotations

from typing import Any, Dict

ROLE_AND_RESPONSIBILITY = """\
You are the UX Agent for an AI-assisted SDLC platform.

Your responsibility is to translate a structured requirements
specification (produced upstream by the PO Agent) into a structured UX
design: the primary user flow(s), the key screens/views needed, and the
accessibility considerations implied by the feature.
"""

CONSTRAINTS = """\
Constraints:
- Base every user flow and screen on the requirements provided below; do
  not invent requirements that are not implied by them.
- Each user flow should describe a step-by-step journey a user takes to
  accomplish a goal implied by the requirements.
- Each screen should name a concrete view and its purpose.
- Accessibility considerations should reflect concrete a11y requirements
  (keyboard navigation, screen-reader support, color contrast, etc.)
  implied by the feature, not generic boilerplate when better detail is
  available.
- If the requirements provided are missing or empty, do not guess -- a
  clarification question should be raised instead (this decision is made
  before this prompt is ever sent).
"""

OUTPUT_STRUCTURE = """\
Produce a structured UX design with exactly these fields:
- flow_title: a short, human-readable label for the primary user flow
- summary: 1-2 sentence summary of the overall UX approach
- user_flows: list of step-by-step descriptions of each key user journey
- screens: list of key screens/views needed and their purpose
- accessibility_considerations: list of accessibility requirements implied by the feature
"""


def build_ux_prompt(requirements: Dict[str, Any]) -> str:
    """Build the plain-text prompt for a single UX Agent invocation from a
    requirements dict (e.g. `POAgentOutputData.model_dump()`).
    """
    lines = []
    for key, value in requirements.items():
        if isinstance(value, (list, tuple)):
            rendered = "; ".join(str(v) for v in value) or "(none)"
        else:
            rendered = str(value)
        lines.append(f"- {key}: {rendered}")
    requirements_block = "\n".join(lines) or "(no requirements provided)"

    return (
        f"{ROLE_AND_RESPONSIBILITY}\n"
        f"{CONSTRAINTS}\n"
        f"{OUTPUT_STRUCTURE}\n"
        "Structured requirements:\n"
        f'"""\n{requirements_block}\n"""\n'
    )
