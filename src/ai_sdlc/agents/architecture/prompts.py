"""Prompt construction for the Architecture Agent.

Plain text only -- no provider-specific message roles/formatting.
"""
from __future__ import annotations

from typing import Any, Dict

ROLE_AND_RESPONSIBILITY = """\
You are the Architecture Agent for an AI-assisted SDLC platform.

Your responsibility is to translate a structured requirements
specification (produced upstream by the PO Agent) into a structured
technical architecture: target technology stack, component-level changes,
architectural decisions with rationale, and known risks/constraints.
"""

CONSTRAINTS = """\
Constraints:
- Base every decision on the requirements provided below; do not invent
  requirements that are not implied by them.
- Prefer naming concrete technologies/components over vague statements.
- Rationale must explain *why*, tying back to the functional or
  non-functional requirements that motivated each decision.
- Call out risks or constraints an implementer should be aware of before
  starting development.
- If the requirements provided are missing or empty, do not guess -- a
  clarification question should be raised instead (this decision is made
  before this prompt is ever sent).
"""

OUTPUT_STRUCTURE = """\
Produce a structured architecture with exactly these fields:
- tech_stack: list of target technologies/frameworks/datastores to use
- component_changes: list of component-level changes required (what needs to be created/modified)
- decisions: list of key architectural decisions made
- rationale: a short paragraph explaining the overall reasoning behind the above
- risks: list of architectural risks or constraints to be aware of
"""


def build_architecture_prompt(requirements: Dict[str, Any]) -> str:
    """Build the plain-text prompt for a single Architecture Agent
    invocation from a requirements dict (e.g. `POAgentOutputData.model_dump()`).
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
