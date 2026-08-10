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
        if inputs.get("force") == "approval":
            return AgentResult(
                request_id=request.request_id,
                workflow_id=request.workflow_id,
                agent_id=self.agent_id,
                status=AgentStatus.NEEDS_APPROVAL,
                artifact=ArtifactRef(type="requirements", path=".ai-sdlc/requirements.json"),
                decision=AgentDecision(status="ready_for_approval", approval_required=True),
            )

        return super().execute(request)

    @staticmethod
    def _effective_text(inputs: Dict[str, Any]) -> str:
        """The requirement text this invocation should reason over.

        Normally this is just `inputs["requirement_text"]`. But when Orion
        resumes this agent after a clarification round
        (`resume_workflow_after_clarification` /
        `LangGraphRunner.resume_after_clarification`), the request carries
        `clarification_answer` instead of the original `requirement_text`
        (the Orchestrator does not currently re-inject prior stage inputs on
        resume). Falling back to the clarification answer lets a
        clarification round actually resolve ambiguity rather than looping
        forever on "no requirement text provided" -- this is a PO Agent
        input-handling decision, not a change to Orchestrator behavior.
        """
        text = (inputs.get("requirement_text") or "").strip()
        if text:
            return text
        return (inputs.get("clarification_answer") or "").strip()

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
        context = {k: v for k, v in inputs.items() if k not in ("requirement_text", "force")}
        return build_po_prompt(text, context=context)
