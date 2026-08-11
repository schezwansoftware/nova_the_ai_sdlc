"""Architecture Agent.

Consumes a structured requirements dict (e.g. what
`POAgentOutputData.model_dump()` produces) via `request.inputs["requirements"]`
and produces a structured architecture: target technology stack,
component-level changes, architectural decisions with rationale, and
risks/constraints.

This agent never imports or calls `POAgent` (or any other agent) directly
-- it only reads from `request.inputs`, exactly as Orion will provide it
once wired into the workflow graph. It is stateless: it never touches
`.ai-sdlc/`, never manages workflow transitions or approvals.

Tier 2 codebase grounding (`docs/architecture/v1_architecture.md` section
8's Agent Capability Tiers): this agent optionally calls
`RetrievalCapability` before reasoning, when `request.inputs` supplies a
real repository path -- see `_gather_codebase_context()`. This is
additive and backward-compatible: no path in `inputs` means no retrieval
call, identical behavior to before this existed. Threading a real path
through from a live workflow is Orion's job (populating
`inputs["target_repository"]["workspace_path"]` when invoking this
stage), not yet wired -- see `todo.md`.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ai_sdlc.agents.architecture.prompts import build_architecture_prompt
from ai_sdlc.agents.architecture.schemas import ArchitectureOutputData
from ai_sdlc.agents.base import AgentRequest
from ai_sdlc.agents.framework import SpecialistAgent
from ai_sdlc.capabilities.providers.retrieval_mock import MockRetrievalProvider
from ai_sdlc.capabilities.reasoning import ReasoningCapability
from ai_sdlc.capabilities.retrieval import (
    MalformedResponseError as RetrievalMalformedResponseError,
    ProviderError as RetrievalProviderError,
    RetrievalCapability,
    RetrievalRequest,
)
from ai_sdlc.orchestration.orchestrator import AgentExecutionError


class ArchitectureAgent(SpecialistAgent):
    output_schema = ArchitectureOutputData

    def __init__(
        self,
        reasoning: Optional[ReasoningCapability] = None,
        retrieval: Optional[RetrievalCapability] = None,
    ):
        super().__init__(agent_id="architecture", version="1.0", reasoning=reasoning)
        # Same zero-arg-constructible discipline `SpecialistAgent` already
        # applies to `reasoning` (AgentRegistry._load_impl calls `cls()`),
        # extended to the new capability: default to the deterministic
        # mock so this agent never requires real credentials/a real repo
        # to instantiate, while still letting a caller/test inject a real
        # or force_error-configured RetrievalCapability.
        self.retrieval: RetrievalCapability = retrieval or MockRetrievalProvider()

    def check_needs_clarification(self, request: AgentRequest) -> Optional[str]:
        inputs: Dict[str, Any] = request.inputs or {}
        requirements = inputs.get("requirements")
        if not requirements or not isinstance(requirements, dict):
            return (
                "No structured requirements were provided. Please run/complete the "
                "PO Agent stage first, or supply a requirements object to design against."
            )
        return None

    def build_prompt(self, request: AgentRequest) -> str:
        inputs: Dict[str, Any] = request.inputs or {}
        requirements: Dict[str, Any] = inputs.get("requirements") or {}
        codebase_context = self._gather_codebase_context(inputs, requirements)
        return build_architecture_prompt(requirements, codebase_context=codebase_context)

    def _gather_codebase_context(
        self, inputs: Dict[str, Any], requirements: Dict[str, Any]
    ) -> Optional[str]:
        """Best-effort Tier 2 codebase grounding via `RetrievalCapability`.

        Only runs when the caller supplies a real repository path at
        `inputs["target_repository"]["workspace_path"]` (matching section
        4's `DeveloperAgentInput` key shape, so a future Orion change that
        populates it needs no new convention). Returns `None` (no
        retrieval call at all) when absent -- true for every existing
        caller/test today, so this method is fully backward-compatible.

        When a path *is* supplied, the caller has explicitly asked for
        grounding, so a retrieval failure is treated the same way
        `SpecialistAgent.execute()` already treats a reasoning-capability
        failure: a controlled `AgentExecutionError(retryable=True)`,
        reusing Orion's existing retry loop, rather than silently
        proceeding ungrounded (which would hide a real provider problem)
        or letting an unrelated exception escape unhandled.
        """
        target_repository = inputs.get("target_repository") or {}
        workspace_path = target_repository.get("workspace_path")
        if not workspace_path:
            return None

        feature_title = requirements.get("feature_title") or "the requested change"
        query = f"Find code relevant to implementing: {feature_title}"

        try:
            result = self.retrieval.retrieve(
                RetrievalRequest(query=query, repository_path=workspace_path)
            )
        except (RetrievalProviderError, RetrievalMalformedResponseError) as exc:
            raise AgentExecutionError(str(exc), retryable=True) from exc

        return result.context_summary
