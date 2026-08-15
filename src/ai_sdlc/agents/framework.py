"""Shared specialist-agent framework.

Every real specialist agent (PO, Architecture, and future agents Craft or
another owner adds) shares the same execute -> build prompt -> call
reasoning capability -> validate structured output -> `AgentResult` flow.
`SpecialistAgent` implements that plumbing once so individual agents only
need to supply:

  - ambiguity/clarification detection (`check_needs_clarification`)
  - a prompt (`build_prompt`)
  - the Pydantic schema their structured output must satisfy
    (`output_schema`)

`SpecialistAgent` intentionally does NOT:
  - touch `.ai-sdlc/` or any workspace file (agents are stateless)
  - call another agent
  - manage LangGraph, workflow transitions, retries, or approvals -- those
    remain the Orchestrator's (Orion's) job. A specialist agent only ever
    returns a schema-valid `AgentResult`, or raises
    `AgentExecutionError(retryable=...)`, which the Orchestrator's existing
    retry loop (`invoke_agent_for_stage`) already knows how to handle.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Dict, Optional, Type

from pydantic import BaseModel

from ai_sdlc.agents.base import (
    Agent,
    AgentRequest,
    AgentResult,
    AgentStatus,
)
from ai_sdlc.capabilities.providers.reasoning_factory import (
    get_default_reasoning_provider,
)
from ai_sdlc.capabilities.reasoning import (
    MalformedResponseError,
    ProviderError,
    ReasoningCapability,
)

# Imported from the orchestration layer deliberately: `AgentExecutionError`
# is Orion's existing retry-loop signal (`Orchestrator.invoke_agent_for_stage`
# catches it and drives retries/failure). Reusing it here means Craft never
# reimplements retry semantics; it just raises through the mechanism Orion
# already owns. This import does not create a cycle: orchestrator.py only
# imports from ai_sdlc.agents.base/registry, never from ai_sdlc.agents.framework
# or any concrete agent module (agent implementations are loaded dynamically,
# by dotted path string, via AgentRegistry._load_impl).
from ai_sdlc.orchestration.orchestrator import AgentExecutionError


class ClarificationNeeded(Exception):
    """Raised by a subclass's `check_needs_clarification` hook to signal
    that the agent cannot proceed without a human answer. Carries the
    question text to surface to the initiator."""

    def __init__(self, question: str):
        super().__init__(question)
        self.question = question


class SpecialistAgent(Agent):
    """Common base class for LLM-abstracted specialist agents.

    Subclasses must set `output_schema` and implement `build_prompt()`.
    Subclasses may optionally override `check_needs_clarification()`
    (default: never needs clarification).
    """

    #: Pydantic model the reasoning capability's structured output must
    #: satisfy. Subclasses must set this.
    output_schema: Type[BaseModel]

    def __init__(
        self,
        agent_id: str,
        version: str = "1.0",
        reasoning: Optional[ReasoningCapability] = None,
    ):
        super().__init__(agent_id=agent_id, version=version)
        # Default to whichever ReasoningCapability this workspace/process
        # has configured (see `get_default_reasoning_provider` --
        # `MockReasoningProvider` unless `AI_SDLC_AGENT_FRAMEWORK` (the same
        # single preference CodingCapability/RetrievalCapability read) opts
        # into a real provider), so every concrete agent subclass remains
        # zero-arg constructible (required by AgentRegistry._load_impl,
        # which calls `cls()`), while still allowing a caller/test to
        # inject a specific ReasoningCapability (e.g. one configured with
        # force_error=...) to prove the agent code is provider-independent.
        self.reasoning: ReasoningCapability = reasoning or get_default_reasoning_provider()

    # -- hooks for subclasses --------------------------------------------

    def check_needs_clarification(self, request: AgentRequest) -> Optional[str]:
        """Return a clarification question string if this request cannot
        be processed as-is, else None. Default: never needs clarification."""
        return None

    @abstractmethod
    def build_prompt(self, request: AgentRequest) -> str:
        """Build the plain-text, provider-agnostic prompt for this request."""
        raise NotImplementedError()

    def build_result_extras(self, request: AgentRequest, data: BaseModel) -> Dict[str, Any]:
        """Optional hook for a subclass to attach an ArtifactRef/AgentDecision
        to the successful AgentResult. Default: none."""
        return {}

    # -- shared execute() flow --------------------------------------------

    def execute(self, request: AgentRequest) -> AgentResult:
        try:
            question = self.check_needs_clarification(request)
        except ClarificationNeeded as exc:
            question = exc.question

        if question:
            return AgentResult(
                request_id=request.request_id,
                workflow_id=request.workflow_id,
                agent_id=self.agent_id,
                status=AgentStatus.NEEDS_CLARIFICATION,
                questions=[question],
            )

        prompt = self.build_prompt(request)

        try:
            data = self.reasoning.complete(prompt, output_schema=self.output_schema)
        except (MalformedResponseError, ProviderError) as exc:
            # Controlled agent failure: never a bare/unhandled exception,
            # never a silently-fabricated FAILED result. Reuse Orion's
            # existing retry mechanism.
            raise AgentExecutionError(str(exc), retryable=True) from exc

        # `check_needs_clarification()` above is a cheap pre-LLM gate (empty
        # input, obviously-too-short, etc.) -- it never sees the model's own
        # judgment. `needs_clarification` is the reasoning call itself
        # deciding, after actually reasoning over a well-formed-but-still-
        # ambiguous input, that it cannot proceed without asking. Every
        # concrete `output_schema` declares this field (see
        # `agents/*/schemas.py`); a schema that doesn't would just never
        # trip this (`getattr` default `False`), not error.
        if getattr(data, "needs_clarification", False):
            return AgentResult(
                request_id=request.request_id,
                workflow_id=request.workflow_id,
                agent_id=self.agent_id,
                status=AgentStatus.NEEDS_CLARIFICATION,
                questions=[data.clarification_question],
            )

        extras = self.build_result_extras(request, data)
        return AgentResult(
            request_id=request.request_id,
            workflow_id=request.workflow_id,
            agent_id=self.agent_id,
            status=AgentStatus.COMPLETED,
            data=data.model_dump(),
            **extras,
        )
