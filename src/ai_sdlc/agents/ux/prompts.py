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
- Missing/empty requirements are already filtered before this prompt is
  ever sent. That does not mean the requirements are unambiguous for UX
  purposes: if they leave a real UX decision genuinely open (e.g. no hint
  at who the user is or what they're trying to accomplish), do not guess
  -- use `needs_clarification` below instead. Reserve this for a real
  blocker, not a minor unstated detail; prefer stating a reasonable
  assumption and proceeding whenever you can.
"""

OUTPUT_STRUCTURE = """\
Produce a structured UX design with exactly these fields:
- needs_clarification: true only if the requirements are genuinely too
  ambiguous to design a meaningful UX flow without guessing at something
  important -- a decision only a human can make. When true, set
  clarification_question to one specific, answerable question and leave
  the fields below empty/minimal -- they are not used. Default to false.
- clarification_question: required, non-empty, when needs_clarification is
  true; otherwise unused.
- needs_context: true only if you are missing specific factual information
  that likely already exists in an internal knowledge source (a Jira
  ticket, a Confluence page, existing project documentation) -- not a
  decision only a human can make (use needs_clarification for that
  instead). When true, set context_query to one specific, plain-language
  question describing exactly what you need to know, and leave the fields
  below empty/minimal -- they are not used. At most one of
  needs_clarification/needs_context may be true. Default to false.
- context_query: required, non-empty, when needs_context is true;
  otherwise unused.
- flow_title: a short, human-readable label for the primary user flow
- summary: 1-2 sentence summary of the overall UX approach
- user_flows: list of step-by-step descriptions of each key user journey
- screens: list of key screens/views needed and their purpose
- accessibility_considerations: list of accessibility requirements implied by the feature
"""


def _render_sage_context(sage_context: list | None) -> str:
    """Render Sage-gathered context entries as their own labeled block.
    See `orchestration/orchestrator.py`'s NEEDS_CONTEXT handling for how
    these entries get produced."""
    if not sage_context:
        return ""
    blocks = []
    for entry in sage_context:
        found = entry.get("found")
        source = entry.get("source_connector") or "not found"
        url = entry.get("source_url") or "no link"
        blocks.append(
            f"Q: {entry.get('query', '')}\n"
            f"A: {entry.get('answer', '') if found else '(nothing found)'}\n"
            f"Source: {source} ({url})"
        )
    joined = "\n\n".join(blocks)
    return (
        "Additional context gathered by Sage (may be incomplete or stale):\n"
        f'"""\n{joined}\n"""\n'
    )


def build_ux_prompt(requirements: Dict[str, Any], sage_context: list | None = None) -> str:
    """Build the plain-text prompt for a single UX Agent invocation from a
    requirements dict (e.g. `POAgentOutputData.model_dump()`).

    `sage_context` is the optional list of Sage-answered/memory-hit context
    entries gathered by the Orchestrator's NEEDS_CONTEXT handling for this
    invocation; `None`/empty for every caller/test that predates it.
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
        f"{_render_sage_context(sage_context)}"
    )
