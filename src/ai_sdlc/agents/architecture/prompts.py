"""Prompt construction for the Architecture Agent.

Plain text only -- no provider-specific message roles/formatting.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

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
- When real codebase context is provided below, ground component_changes
  and decisions in what actually exists in the repository rather than
  assuming a greenfield implementation; when no codebase context is
  provided, reason from the requirements alone as before.
"""

OUTPUT_STRUCTURE = """\
Produce a structured architecture with exactly these fields:
- tech_stack: list of target technologies/frameworks/datastores to use
- component_changes: list of component-level changes required (what needs to be created/modified)
- decisions: list of key architectural decisions made
- rationale: a short paragraph explaining the overall reasoning behind the above
- risks: list of architectural risks or constraints to be aware of
- requires_ui: true if this feature needs a user-facing UI/UX design (a web
  page, GUI screen, form, dashboard, or any visual interface a user
  interacts with); false for backend-only, headless, console/CLI-output-only,
  script, or library changes with no interface for a user to look at or
  interact with (e.g. a program that only prints to stdout). Decide this
  carefully from the requirements above -- when true, a UX design stage
  runs next; when false, it is skipped entirely, so do not default to true
  out of caution when the requirements clearly describe a UI-less change.
"""


def build_architecture_prompt(
    requirements: Dict[str, Any], codebase_context: Optional[str] = None
) -> str:
    """Build the plain-text prompt for a single Architecture Agent
    invocation from a requirements dict (e.g. `POAgentOutputData.model_dump()`).

    `codebase_context` is the optional `RetrievalCapability` context
    summary gathered by `ArchitectureAgent._gather_codebase_context()`
    (Tier 2 grounding, `docs/architecture/v1_architecture.md` section 8) --
    `None` when no real repository path was supplied for this invocation,
    which is true for every caller/test that predates this parameter, so
    omitting it produces the exact same prompt as before this existed.
    """
    lines = []
    for key, value in requirements.items():
        if isinstance(value, (list, tuple)):
            rendered = "; ".join(str(v) for v in value) or "(none)"
        else:
            rendered = str(value)
        lines.append(f"- {key}: {rendered}")
    requirements_block = "\n".join(lines) or "(no requirements provided)"

    codebase_section = ""
    if codebase_context:
        codebase_section = (
            "Relevant existing codebase context:\n"
            f'"""\n{codebase_context}\n"""\n'
        )

    return (
        f"{ROLE_AND_RESPONSIBILITY}\n"
        f"{CONSTRAINTS}\n"
        f"{OUTPUT_STRUCTURE}\n"
        "Structured requirements:\n"
        f'"""\n{requirements_block}\n"""\n'
        f"{codebase_section}"
    )
