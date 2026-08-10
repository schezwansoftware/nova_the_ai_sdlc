from __future__ import annotations
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel


class AgentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    NEEDS_CLARIFICATION = "needs_clarification"
    NEEDS_APPROVAL = "needs_approval"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentRequest(BaseModel):
    request_id: str
    workflow_id: str
    agent_id: str
    agent_version: str
    action: str
    inputs: Dict[str, Any] = {}
    constraints: Dict[str, Any] = {}


class AgentDecision(BaseModel):
    status: str
    approval_required: bool = False


class ArtifactRef(BaseModel):
    type: str
    path: str
    version: Optional[int] = 1


class AgentResult(BaseModel):
    request_id: str
    workflow_id: str
    agent_id: str
    status: AgentStatus
    artifact: Optional[ArtifactRef] = None
    decision: Optional[AgentDecision] = None
    findings: Optional[list] = []
    questions: Optional[list] = []
    warnings: Optional[list] = []
    # Additive field (not present in the original Atlas contract as
    # implemented): structured payload produced by a specialist agent
    # (e.g. a POAgentOutputData/ArchitectureOutputData dump). Agents are
    # stateless and never write to `.ai-sdlc/` themselves, so this is the
    # only place a specialist agent's structured output travels back to
    # Orion. Optional/backward-compatible: existing callers that never set
    # it are unaffected.
    data: Optional[Dict[str, Any]] = None


class Agent(ABC):
    agent_id: str
    version: str

    def __init__(self, agent_id: str, version: str = "1.0"):
        self.agent_id = agent_id
        self.version = version

    @abstractmethod
    def execute(self, request: AgentRequest) -> AgentResult:
        """Execute the agent action and return an AgentResult.

        Implementations must return a schema-valid AgentResult. The Orchestrator
        must validate the returned result before progressing the workflow.
        """
        raise NotImplementedError()
