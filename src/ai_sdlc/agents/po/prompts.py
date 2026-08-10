"""Prompt construction for the PO (Product Owner) Agent.

Plain text only -- no provider-specific message roles/formatting. This
module is deliberately separate from `po_agent.py` so the prompt can be
reviewed/edited independently of orchestration/execution logic.
"""
from __future__ import annotations

from typing import Any, Dict

ROLE_AND_RESPONSIBILITY = """\
You are the Product Owner (PO) Agent for an AI-assisted SDLC platform.

Your responsibility is to interpret a raw, informally written product
requirement and turn it into a structured requirements specification that
downstream specialist agents (Architecture, Development, Testing) and a
human reviewer can act on unambiguously.
"""

CONSTRAINTS = """\
Constraints:
- Do not invent requirements that are not implied by the input text.
- Prefer deriving functional requirements from concrete, actionable
  statements in the input.
- Non-functional requirements should cover performance, reliability,
  security, or availability concerns when the input implies them; if none
  are stated, note reasonable defaults appropriate to the described
  feature.
- Explicitly list anything the requirement does not cover as
  out-of-scope, so reviewers know what will NOT be delivered.
- Acceptance criteria must be objectively verifiable.
- If the requirement is too vague or incomplete to produce a meaningful
  structured specification, do not guess -- a clarification question
  should be raised instead (this decision is made before this prompt is
  ever sent; by the time you are reasoning over this prompt, the input has
  already been judged sufficient).
"""

OUTPUT_STRUCTURE = """\
Produce a structured requirements specification with exactly these fields:
- feature_title: a short, human-readable title for the requirement
- summary: 1-2 sentence summary of what is being built and why
- functional_requirements: list of concrete, testable functional requirements
- non_functional_requirements: list of non-functional requirements (performance, security, reliability, etc.)
- out_of_scope: list of things explicitly not covered by this requirement
- acceptance_criteria: list of objectively verifiable acceptance criteria
"""


def build_po_prompt(requirement_text: str, context: Dict[str, Any] | None = None) -> str:
    """Build the plain-text prompt for a single PO Agent invocation.

    `context` may include prior clarification answers or project metadata
    already gathered by Orion (e.g. `project_context`); it is rendered as
    plain text, never as vendor-specific structured messages.
    """
    context = context or {}
    context_lines = "\n".join(f"- {key}: {value}" for key, value in context.items()) or "(none)"

    return (
        f"{ROLE_AND_RESPONSIBILITY}\n"
        f"{CONSTRAINTS}\n"
        f"{OUTPUT_STRUCTURE}\n"
        "Raw product requirement:\n"
        f"\"\"\"\n{requirement_text}\n\"\"\"\n\n"
        "Additional context:\n"
        f"{context_lines}\n"
    )
