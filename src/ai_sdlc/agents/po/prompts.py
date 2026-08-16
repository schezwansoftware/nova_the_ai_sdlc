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
- The most extreme cases (no input text at all, or a handful of words with
  no real content) are already filtered before this prompt is ever sent --
  by the time you are reasoning over this prompt, the input has cleared
  that bar. That does not mean it is unambiguous: if it is still missing
  something you cannot reasonably assume (e.g. it names a feature but
  gives no way to tell who it's for or what "done" means), do not guess --
  use `needs_clarification` below instead. Reserve this for a real
  blocker, not a minor unstated detail; prefer stating a reasonable
  assumption and proceeding whenever you can.
"""

OUTPUT_STRUCTURE = """\
Produce a structured requirements specification with exactly these fields:
- needs_clarification: true only if the requirement is genuinely too
  ambiguous to produce a meaningful structured specification without
  guessing at something important -- a product/business decision only a
  human can make. When true, set clarification_question to one specific,
  answerable question and leave the fields below empty/minimal -- they are
  not used. Default to false.
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
- feature_title: a short, human-readable title for the requirement
- summary: 1-2 sentence summary of what is being built and why
- functional_requirements: list of concrete, testable functional requirements
- non_functional_requirements: list of non-functional requirements (performance, security, reliability, etc.)
- out_of_scope: list of things explicitly not covered by this requirement
- acceptance_criteria: list of objectively verifiable acceptance criteria
"""


def _render_sage_context(sage_context: list | None) -> str:
    """Render Sage-gathered context entries as their own labeled block,
    kept separate from the generic `context` dict below so it never gets
    flattened into an unreadable `str(list-of-dicts)` line. See
    `orchestration/orchestrator.py`'s NEEDS_CONTEXT handling for how these
    entries get produced."""
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


def build_po_prompt(
    requirement_text: str,
    context: Dict[str, Any] | None = None,
    sage_context: list | None = None,
) -> str:
    """Build the plain-text prompt for a single PO Agent invocation.

    `context` may include prior clarification answers or project metadata
    already gathered by Orion (e.g. `project_context`); it is rendered as
    plain text, never as vendor-specific structured messages.

    `sage_context` is the optional list of Sage-answered/memory-hit context
    entries gathered by the Orchestrator's NEEDS_CONTEXT handling for this
    invocation (see `orchestration/orchestrator.py`); `None`/empty for
    every caller/test that predates it, which produces the exact same
    prompt as before this existed.
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
        f"{_render_sage_context(sage_context)}"
    )
