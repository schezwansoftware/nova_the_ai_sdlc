"""PO (Product Owner) Agent.

Interprets a raw product requirement and turns it into a structured
requirements specification (functional requirements, non-functional
requirements, acceptance criteria, out-of-scope items), or asks a
clarification question when the input is too ambiguous to act on.

This agent is stateless: it never touches `.ai-sdlc/`, never manages
workflow transitions or approvals, and never invokes another agent. It
only returns an `AgentResult`; the Orchestrator (Orion) decides what
happens next.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ai_sdlc.agents.base import (
    AgentDecision,
    AgentRequest,
    AgentResult,
    AgentStatus,
    ArtifactRef,
)
from ai_sdlc.agents.framework import SpecialistAgent
from ai_sdlc.agents.po.prompts import build_po_prompt
from ai_sdlc.agents.po.schemas import POAgentOutputData
from ai_sdlc.capabilities.reasoning import ReasoningCapability

_VAGUENESS_MARKERS = ("tbd", "not sure", "unclear", "figure out later", "figure it out later")
_MIN_REQUIREMENT_LENGTH = 12


class POAgent(SpecialistAgent):
    output_schema = POAgentOutputData

    def __init__(self, reasoning: Optional[ReasoningCapability] = None):
        super().__init__(agent_id="po", version="1.0", reasoning=reasoning)

    def execute(self, request: AgentRequest) -> AgentResult:
        inputs: Dict[str, Any] = request.inputs or {}

        # Documented test-only hook (inputs["force"] == "approval"): the PO
        # Agent has no real approval-gating logic of its own -- Orion owns
        # approval workflow progression -- but returning NEEDS_APPROVAL here
        # deterministically exercises the Orchestrator's existing
        # needs_approval handling path without requiring a real LLM.
        #
        # Runs the real flow first and only overrides a COMPLETED result's
        # status, rather than short-circuiting before any real work happens:
        # Orion's approval-resume no longer re-invokes the requesting agent
        # once approved (see Orchestrator.resume_workflow_after_approval /
        # LangGraphRunner.resume_after_approval), so an agent that requests
        # approval must produce its real, final `data` *before* asking, not
        # promise to produce it "later" -- there is no later call. This hook
        # models that correctly rather than a since-removed pattern.
        if inputs.get("force") == "approval":
            result = super().execute(request)
            if result.status != AgentStatus.COMPLETED:
                return result
            return AgentResult(
                request_id=result.request_id,
                workflow_id=result.workflow_id,
                agent_id=result.agent_id,
                status=AgentStatus.NEEDS_APPROVAL,
                data=result.data,
                artifact=ArtifactRef(type="requirements", path=".ai-sdlc/requirements.json"),
                decision=AgentDecision(status="ready_for_approval", approval_required=True),
            )

        return super().execute(request)

    @staticmethod
    def _effective_text(inputs: Dict[str, Any]) -> str:
        """The requirement text this invocation should reason over.

        On a fresh call this is just `inputs["requirement_text"]`. On a
        resume after a clarification round, `inputs["clarification_answer"]`
        holds the user's latest answer and must take precedence over it --
        `wf.inputs` is cumulative and never clears `requirement_text` on
        resume (it's set once at `start_workflow` for the whole workflow,
        not per-node), so it is *still present, unchanged* on the resumed
        call. Preferring it unconditionally, as this used to, meant
        `check_needs_clarification` kept re-evaluating the same original
        (still-ambiguous) text every round regardless of what the user
        answered -- an unresolvable loop for any requirement PO's own
        ambiguity heuristic flags on the very first call. Preferring
        `clarification_answer` whenever present fixes that: PO is only ever
        invoked once-then-resumed in this graph (it never runs again after
        the workflow advances past `requirements`), so a present
        `clarification_answer` unambiguously means "this call is my own
        resume," never a later node's leftover state.
        """
        clarification_answer = (inputs.get("clarification_answer") or "").strip()
        if clarification_answer:
            return clarification_answer
        return (inputs.get("requirement_text") or "").strip()

    def check_needs_clarification(self, request: AgentRequest) -> Optional[str]:
        inputs: Dict[str, Any] = request.inputs or {}

        # Documented test-only hook, same spirit as the previous stub, now
        # layered on top of real ambiguity detection below rather than
        # being the only logic.
        if inputs.get("force") == "clarify":
            return "Please clarify which fields/behavior this requirement needs."

        text = self._effective_text(inputs)

        if not text:
            return "No requirement text was provided. Please describe the feature you want built."

        if len(text) < _MIN_REQUIREMENT_LENGTH:
            return (
                f'The requirement "{text}" is too short to act on. '
                "Please provide more detail: what should be built, for whom, and why?"
            )

        lowered = text.lower()
        if any(marker in lowered for marker in _VAGUENESS_MARKERS):
            return (
                f'The requirement contains unresolved ambiguity ("{text}"). '
                "Please clarify the specific behavior expected instead of leaving it open-ended."
            )

        return None

    def build_prompt(self, request: AgentRequest) -> str:
        inputs: Dict[str, Any] = request.inputs or {}
        text = self._effective_text(inputs)
        context = {
            k: v
            for k, v in inputs.items()
            if k not in ("requirement_text", "force", "sage_context")
        }
        return build_po_prompt(text, context=context, sage_context=inputs.get("sage_context"))
