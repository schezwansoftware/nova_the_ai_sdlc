from __future__ import annotations
from typing import Any, Dict, Optional
from ai_sdlc.agents.base import Agent, AgentRequest, AgentResult, AgentStatus, ArtifactRef, AgentDecision


class POAgent(Agent):
    def __init__(self):
        super().__init__(agent_id="po", version="1.0")

    def execute(self, request: AgentRequest) -> AgentResult:
        # Minimal deterministic behavior for testing/runtime validation.
        # Inputs expected: {"requirement_text": str, "force": "completed"|"clarify"|"approval"}
        inputs: Dict[str, Any] = request.inputs or {}
        text = inputs.get("requirement_text", "")
        forced = inputs.get("force")

        if forced == "clarify" or "clarify" in text.lower():
            return AgentResult(
                request_id=request.request_id,
                workflow_id=request.workflow_id,
                agent_id=self.agent_id,
                status=AgentStatus.NEEDS_CLARIFICATION,
                questions=["Please clarify which fields are required."],
            )

        if forced == "approval" or "approve" in text.lower():
            artifact = ArtifactRef(type="requirements", path=".ai-sdlc/requirements.json")
            decision = AgentDecision(status="ready_for_approval", approval_required=True)
            return AgentResult(
                request_id=request.request_id,
                workflow_id=request.workflow_id,
                agent_id=self.agent_id,
                status=AgentStatus.NEEDS_APPROVAL,
                artifact=artifact,
                decision=decision,
            )

        # default: completed
        return AgentResult(
            request_id=request.request_id,
            workflow_id=request.workflow_id,
            agent_id=self.agent_id,
            status=AgentStatus.COMPLETED,
        )
