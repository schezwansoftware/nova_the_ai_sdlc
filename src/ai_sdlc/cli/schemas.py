"""Wire-format DTOs for the CLI's HTTP client.

These are deliberately independent, hand-written copies of the shapes
documented in `ai_sdlc.orchestration.api` (the authoritative source of
truth -- see that module for the real Pydantic definitions), not imports of
that module. Importing `ai_sdlc.orchestration.api` would pull in
`ai_sdlc.orchestration.orchestrator` and the rest of the internal
orchestration import chain, which the CLI is not allowed to touch even
indirectly. A thin HTTP client defining its own request/response DTOs is
normal (same as any client for a third-party REST API) and is not the kind
of orchestration-logic duplication the CLI must avoid (retry loops,
state-transition validation, HITL semantics stay server-side).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---- requests the CLI sends -------------------------------------------


class StartWorkflowRequest(_StrictModel):
    initiator_id: str
    raw_requirement: str = Field(..., min_length=10)
    project_context: Dict[str, Any] = Field(default_factory=dict)


class SubmitClarificationRequest(_StrictModel):
    initiator_id: str
    question_id: str
    response_text: str


class SubmitApprovalRequest(_StrictModel):
    initiator_id: str
    approval_id: str
    approved: bool
    feedback: Optional[str] = None


class CancelWorkflowRequest(_StrictModel):
    initiator_id: str
    reason: str


# ---- response data the CLI parses (server is the source of truth; these
# only declare the fields the CLI actually renders) ----------------------


class PendingAction(BaseModel):
    action_type: str
    prompt_message: str
    target_phase: str
    interaction_id: Optional[str] = None
    payload_artifact_path: Optional[str] = None


class WorkflowStatusData(BaseModel):
    workflow_id: str
    initiator_id: str
    current_phase: str
    status: str
    pending_action: Optional[PendingAction] = None
    updated_at: str
    artifacts: Dict[str, str] = Field(default_factory=dict)


class StartWorkflowData(BaseModel):
    workflow_id: str
    initiator_id: str
    current_phase: str
    status: str
    created_at: str


class SubmitClarificationData(BaseModel):
    workflow_id: str
    current_phase: str
    status: str
    message: str


class SubmitApprovalData(BaseModel):
    workflow_id: str
    current_phase: str
    status: str
    message: str


class CancelWorkflowData(BaseModel):
    workflow_id: str
    status: str
    cancelled_at: str
