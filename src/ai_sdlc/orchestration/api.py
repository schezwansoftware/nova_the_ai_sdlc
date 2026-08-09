from __future__ import annotations
from typing import Optional, Dict, Any, Generic, TypeVar
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from uuid import uuid4

from ai_sdlc.orchestration.orchestrator import Orchestrator
from ai_sdlc.orchestration.state import WorkflowLockTimeoutError, WorkflowState, WorkflowStatus, utc_now

T = TypeVar("T")


class StrictModel(BaseModel):
    """Base for all public API request/response models.

    extra="forbid" makes the API boundary strict: a client-supplied field
    that doesn't match the declared schema is rejected with a 400/
    ValidationError instead of being silently dropped (the same failure
    mode that let raw_requirement/project_context go missing before).
    """

    model_config = ConfigDict(extra="forbid")


class WorkflowPhase(str, Enum):
    INIT = "INIT"
    REQUIREMENTS = "REQUIREMENTS"
    UX_DESIGN = "UX_DESIGN"
    ARCHITECTURE = "ARCHITECTURE"
    DEVELOPMENT = "DEVELOPMENT"
    TESTING = "TESTING"
    SECURITY = "SECURITY"
    CODE_REVIEW = "CODE_REVIEW"
    DOCUMENTATION = "DOCUMENTATION"
    PULL_REQUEST = "PULL_REQUEST"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class WorkflowStatusType(str, Enum):
    RUNNING = "RUNNING"
    WAITING_FOR_CLARIFICATION = "WAITING_FOR_CLARIFICATION"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    REVISION_REQUIRED = "REVISION_REQUIRED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ErrorCode(str, Enum):
    WORKFLOW_NOT_FOUND = "WORKFLOW_NOT_FOUND"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNAUTHORIZED_INITIATOR = "UNAUTHORIZED_INITIATOR"
    INTERNAL_ORCHESTRATION_ERROR = "INTERNAL_ORCHESTRATION_ERROR"
    LOCK_ACQUISITION_FAILED = "LOCK_ACQUISITION_FAILED"


class APIErrorDetail(StrictModel):
    code: ErrorCode
    message: str
    details: Optional[Dict[str, Any]] = None
    timestamp: datetime = Field(default_factory=utc_now)


class APIResponse(StrictModel, Generic[T]):
    api_version: str = "v1"
    success: bool
    data: Optional[T] = None
    error: Optional[APIErrorDetail] = None


class StartWorkflowRequest(StrictModel):
    initiator_id: str = Field(...)
    raw_requirement: str = Field(..., min_length=10)
    project_context: Dict[str, Any] = Field(default_factory=dict)


class StartWorkflowData(StrictModel):
    workflow_id: str
    initiator_id: str
    current_phase: WorkflowPhase
    status: WorkflowStatusType
    created_at: datetime


class GetWorkflowStatusRequest(StrictModel):
    workflow_id: str


class PendingAction(StrictModel):
    action_type: str
    prompt_message: str
    target_phase: WorkflowPhase
    payload_artifact_path: Optional[str] = None


class WorkflowStatusData(StrictModel):
    workflow_id: str
    initiator_id: str
    current_phase: WorkflowPhase
    status: WorkflowStatusType
    pending_action: Optional[PendingAction] = None
    updated_at: datetime
    artifacts: Dict[str, str] = Field(default_factory=dict)


class SubmitClarificationRequest(StrictModel):
    workflow_id: str
    initiator_id: str
    question_id: str = Field(..., min_length=1)
    response_text: str = Field(..., min_length=1)


class SubmitClarificationData(StrictModel):
    workflow_id: str
    current_phase: WorkflowPhase
    status: WorkflowStatusType
    message: str = Field(default="Clarification accepted. Workflow resuming.")


class SubmitApprovalRequest(StrictModel):
    workflow_id: str
    initiator_id: str
    approval_id: str = Field(..., min_length=1)
    approved: bool
    feedback: Optional[str] = None


class SubmitApprovalData(StrictModel):
    workflow_id: str
    current_phase: WorkflowPhase
    status: WorkflowStatusType
    message: str


class ResumeWorkflowRequest(StrictModel):
    workflow_id: str
    initiator_id: str


class ResumeWorkflowData(StrictModel):
    workflow_id: str
    current_phase: WorkflowPhase
    status: WorkflowStatusType


class CancelWorkflowRequest(StrictModel):
    workflow_id: str
    initiator_id: str
    reason: str


class CancelWorkflowData(StrictModel):
    workflow_id: str
    status: WorkflowStatusType = WorkflowStatusType.CANCELLED
    cancelled_at: datetime


class OrchestratorAPI:
    """Public Orchestrator API Facade (v1)

    This facade encapsulates LangGraph internals and exposes stable, versioned
    Pydantic request/response models. It delegates execution to the internal
    Orchestrator and LangGraph runner implementations while ensuring public
    contracts are honored.
    """

    # Single canonical internal-stage -> public-phase mapping. Do not
    # duplicate this table elsewhere.
    _STAGE_TO_PHASE = {
        "requirements": WorkflowPhase.REQUIREMENTS,
        "ux_design": WorkflowPhase.UX_DESIGN,
        "architecture": WorkflowPhase.ARCHITECTURE,
        "development": WorkflowPhase.DEVELOPMENT,
        "testing": WorkflowPhase.TESTING,
        "security": WorkflowPhase.SECURITY,
        "code_review": WorkflowPhase.CODE_REVIEW,
        "documentation": WorkflowPhase.DOCUMENTATION,
        "pull_request": WorkflowPhase.PULL_REQUEST,
    }

    # Single canonical internal-status -> public-status mapping. Do not
    # duplicate this table elsewhere.
    _STATUS_TO_TYPE = {
        WorkflowStatus.RUNNING: WorkflowStatusType.RUNNING,
        WorkflowStatus.WAITING_FOR_CLARIFICATION: WorkflowStatusType.WAITING_FOR_CLARIFICATION,
        WorkflowStatus.WAITING_FOR_APPROVAL: WorkflowStatusType.WAITING_FOR_APPROVAL,
        WorkflowStatus.REVISION_REQUIRED: WorkflowStatusType.REVISION_REQUIRED,
        WorkflowStatus.FAILED: WorkflowStatusType.FAILED,
        WorkflowStatus.COMPLETED: WorkflowStatusType.COMPLETED,
        WorkflowStatus.CANCELLED: WorkflowStatusType.CANCELLED,
    }

    def __init__(self, workspace: str):
        self.workspace = workspace
        self.orch = Orchestrator(workspace)

    @staticmethod
    def _is_missing_agent_error(error: Exception) -> bool:
        return "agent not found" in str(error).lower()

    @staticmethod
    def _orchestration_error(message: str, details: Optional[Dict[str, Any]] = None) -> APIResponse:
        return APIResponse(
            success=False,
            error=APIErrorDetail(
                code=ErrorCode.INTERNAL_ORCHESTRATION_ERROR,
                message=message,
                details=details,
            ),
        )

    @staticmethod
    def _workflow_phase(wf: WorkflowState) -> WorkflowPhase:
        if not wf.current_stage:
            return WorkflowPhase.COMPLETED
        if wf.current_stage not in OrchestratorAPI._STAGE_TO_PHASE:
            raise RuntimeError(f"Unknown workflow stage: {wf.current_stage}")
        return OrchestratorAPI._STAGE_TO_PHASE[wf.current_stage]

    @staticmethod
    def _public_status(internal_status: str) -> WorkflowStatusType:
        if internal_status not in OrchestratorAPI._STATUS_TO_TYPE:
            raise RuntimeError(f"Unknown internal workflow status: {internal_status}")
        return OrchestratorAPI._STATUS_TO_TYPE[internal_status]

    def start_workflow(self, req: StartWorkflowRequest) -> APIResponse[StartWorkflowData]:
        # Validate request via pydantic already done by caller
        try:
            # create workflow state using collision-resistant id
            wf_id = f"wf-{uuid4().hex}"
            wf = WorkflowState(
                workflow_id=wf_id,
                current_stage="requirements",
                initiator_id=req.initiator_id,
                inputs={"requirement_text": req.raw_requirement, "project_context": req.project_context},
            )
            self.orch.store.write_workflow(wf)

            # execute initial node via internal graph runner
            try:
                self.orch.run_workflow_graph(wf.workflow_id)
            except WorkflowLockTimeoutError as e:
                return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.LOCK_ACQUISITION_FAILED, message=str(e)))
            except Exception as e:
                if self._is_missing_agent_error(e):
                    return self._orchestration_error(
                        str(e),
                        details={"reason": "missing_agent", "workflow_id": wf.workflow_id},
                    )
                raise

            wf = self.orch.load_workflow()
            data = StartWorkflowData(
                workflow_id=wf.workflow_id,
                initiator_id=wf.initiator_id,
                current_phase=self._workflow_phase(wf),
                status=self._public_status(wf.status),
                created_at=datetime.fromisoformat(wf.created_at.replace("Z", "+00:00")),
            )
            return APIResponse(success=True, data=data)
        except ValidationError as e:
            return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.VALIDATION_ERROR, message=str(e)))
        except Exception as e:
            return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.INTERNAL_ORCHESTRATION_ERROR, message=str(e)))

    def get_workflow_status(self, req: GetWorkflowStatusRequest) -> APIResponse[WorkflowStatusData]:
        try:
            wf = self.orch.load_workflow()
            if not wf or wf.workflow_id != req.workflow_id:
                return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.WORKFLOW_NOT_FOUND, message="Workflow not found"))

            # map internal state to public enums; unknown internal state is a
            # server-side bug, not something to silently paper over as RUNNING.
            phase = self._workflow_phase(wf)
            status = self._public_status(wf.status)

            pending = None
            if wf.status == WorkflowStatus.WAITING_FOR_CLARIFICATION and wf.pending_clarification:
                pending = PendingAction(action_type="CLARIFICATION", prompt_message=wf.pending_clarification.get("question", "clarification requested"), target_phase=phase, payload_artifact_path=wf.pending_clarification.get("question_id"))
            if wf.status == WorkflowStatus.WAITING_FOR_APPROVAL and wf.pending_approval:
                pending = PendingAction(action_type="APPROVAL", prompt_message="approval requested", target_phase=phase, payload_artifact_path=wf.pending_approval.get("approval_id"))

            artifacts_map = wf.stages.copy() if wf.stages else {}

            data = WorkflowStatusData(
                workflow_id=wf.workflow_id,
                initiator_id=wf.initiator_id or "",
                current_phase=phase,
                status=status,
                pending_action=pending,
                updated_at=datetime.fromisoformat(wf.updated_at.replace("Z", "+00:00")),
                artifacts=artifacts_map,
            )
            return APIResponse(success=True, data=data)
        except Exception as e:
            return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.INTERNAL_ORCHESTRATION_ERROR, message=str(e)))

    def submit_clarification(self, req: SubmitClarificationRequest) -> APIResponse[SubmitClarificationData]:
        try:
            wf = self.orch.load_workflow()
            if not wf or wf.workflow_id != req.workflow_id:
                return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.WORKFLOW_NOT_FOUND, message="Workflow not found"))
            if wf.initiator_id != req.initiator_id:
                return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.UNAUTHORIZED_INITIATOR, message="Initiator mismatch"))
            if wf.status != WorkflowStatus.WAITING_FOR_CLARIFICATION:
                return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.INVALID_STATE_TRANSITION, message="No active clarification"))

            # validate provided question id matches pending clarification
            if not wf.pending_clarification or wf.pending_clarification.get("question_id") != req.question_id:
                return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.INVALID_STATE_TRANSITION, message="Invalid or stale question_id"))

            # resume via orchestrator
            try:
                self.orch.resume_workflow_after_clarification(wf.workflow_id, question_id=req.question_id, answer=req.response_text)
            except WorkflowLockTimeoutError as e:
                return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.LOCK_ACQUISITION_FAILED, message=str(e)))
            except Exception as e:
                if self._is_missing_agent_error(e):
                    return self._orchestration_error(str(e), details={"reason": "missing_agent", "workflow_id": wf.workflow_id})
                return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.INTERNAL_ORCHESTRATION_ERROR, message=str(e)))

            # workflow state is authoritative — reload rather than trust the
            # runner's ad-hoc result dict.
            wf = self.orch.load_workflow()
            data = SubmitClarificationData(
                workflow_id=wf.workflow_id,
                current_phase=self._workflow_phase(wf),
                status=self._public_status(wf.status),
            )
            return APIResponse(success=True, data=data)
        except Exception as e:
            return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.INTERNAL_ORCHESTRATION_ERROR, message=str(e)))

    def submit_approval(self, req: SubmitApprovalRequest) -> APIResponse[SubmitApprovalData]:
        try:
            wf = self.orch.load_workflow()
            if not wf or wf.workflow_id != req.workflow_id:
                return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.WORKFLOW_NOT_FOUND, message="Workflow not found"))
            if wf.initiator_id != req.initiator_id:
                return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.UNAUTHORIZED_INITIATOR, message="Initiator mismatch"))
            if wf.status != WorkflowStatus.WAITING_FOR_APPROVAL:
                return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.INVALID_STATE_TRANSITION, message="Not waiting for approval"))
            if not req.approved and (not req.feedback or req.feedback.strip() == ""):
                return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.VALIDATION_ERROR, message="Rejection requires feedback"))

            if not wf.pending_approval or wf.pending_approval.get("approval_id") != req.approval_id:
                return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.INVALID_STATE_TRANSITION, message="Invalid or stale approval_id"))

            decision = "approved" if req.approved else "rejected"
            try:
                self.orch.resume_workflow_after_approval(wf.workflow_id, approval_id=req.approval_id, decision=decision, feedback=req.feedback)
            except WorkflowLockTimeoutError as e:
                return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.LOCK_ACQUISITION_FAILED, message=str(e)))
            except Exception as e:
                if self._is_missing_agent_error(e):
                    return self._orchestration_error(str(e), details={"reason": "missing_agent", "workflow_id": wf.workflow_id})
                return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.INTERNAL_ORCHESTRATION_ERROR, message=str(e)))

            # workflow state is authoritative — reload rather than trust the
            # runner's ad-hoc result dict. This is what correctly surfaces
            # REVISION_REQUIRED on rejection instead of RUNNING.
            wf = self.orch.load_workflow()
            msg = "Approved and resumed" if req.approved else "Rejected: workflow requires revision"
            data = SubmitApprovalData(
                workflow_id=wf.workflow_id,
                current_phase=self._workflow_phase(wf),
                status=self._public_status(wf.status),
                message=msg,
            )
            return APIResponse(success=True, data=data)
        except Exception as e:
            return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.INTERNAL_ORCHESTRATION_ERROR, message=str(e)))

    def resume_workflow(self, req: ResumeWorkflowRequest) -> APIResponse[ResumeWorkflowData]:
        try:
            wf = self.orch.load_workflow()
            if not wf or wf.workflow_id != req.workflow_id:
                return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.WORKFLOW_NOT_FOUND, message="Workflow not found"))
            if wf.initiator_id != req.initiator_id:
                return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.UNAUTHORIZED_INITIATOR, message="Initiator mismatch"))
            if wf.status in (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED):
                return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.INVALID_STATE_TRANSITION, message="Workflow is already terminal"))

            try:
                self.orch.run_workflow_graph(wf.workflow_id)
            except WorkflowLockTimeoutError as e:
                return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.LOCK_ACQUISITION_FAILED, message=str(e)))
            except Exception as e:
                if self._is_missing_agent_error(e):
                    return self._orchestration_error(str(e), details={"reason": "missing_agent", "workflow_id": wf.workflow_id})
                return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.INTERNAL_ORCHESTRATION_ERROR, message=str(e)))

            wf = self.orch.load_workflow()
            data = ResumeWorkflowData(
                workflow_id=wf.workflow_id,
                current_phase=self._workflow_phase(wf),
                status=self._public_status(wf.status),
            )
            return APIResponse(success=True, data=data)
        except Exception as e:
            return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.INTERNAL_ORCHESTRATION_ERROR, message=str(e)))

    def cancel_workflow(self, req: CancelWorkflowRequest) -> APIResponse[CancelWorkflowData]:
        try:
            # The full check-then-mutate sequence must hold the lock: cancel
            # racing submit_clarification/submit_approval/resume_workflow on
            # the same workflow_id must not interleave and lose an update.
            try:
                with self.orch.store.transaction():
                    wf = self.orch.load_workflow()
                    if not wf or wf.workflow_id != req.workflow_id:
                        return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.WORKFLOW_NOT_FOUND, message="Workflow not found"))
                    if wf.initiator_id != req.initiator_id:
                        return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.UNAUTHORIZED_INITIATOR, message="Initiator mismatch"))
                    if wf.status in (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED):
                        return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.INVALID_STATE_TRANSITION, message="Workflow is already terminal"))

                    wf.status = WorkflowStatus.CANCELLED
                    self.orch.save_workflow(wf)
                    data = CancelWorkflowData(workflow_id=wf.workflow_id, cancelled_at=utc_now())
                    return APIResponse(success=True, data=data)
            except WorkflowLockTimeoutError as e:
                return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.LOCK_ACQUISITION_FAILED, message=str(e)))
        except Exception as e:
            return APIResponse(success=False, error=APIErrorDetail(code=ErrorCode.INTERNAL_ORCHESTRATION_ERROR, message=str(e)))
