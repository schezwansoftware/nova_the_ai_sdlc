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
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ai_sdlc.agents.architecture.prompts import build_architecture_prompt
from ai_sdlc.agents.architecture.schemas import ArchitectureOutputData
from ai_sdlc.agents.base import AgentRequest
from ai_sdlc.agents.framework import SpecialistAgent
from ai_sdlc.capabilities.reasoning import ReasoningCapability


class ArchitectureAgent(SpecialistAgent):
    output_schema = ArchitectureOutputData

    def __init__(self, reasoning: Optional[ReasoningCapability] = None):
        super().__init__(agent_id="architecture", version="1.0", reasoning=reasoning)

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
        return build_architecture_prompt(requirements)
