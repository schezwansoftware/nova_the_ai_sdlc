from __future__ import annotations
import contextlib
import json
from pathlib import Path
from typing import Dict, Optional, Any

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None
from datetime import datetime, timezone
from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


class WorkflowStatus(str):
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkflowState(BaseModel):
    schema_version: str = "workflow-v1"
    workflow_id: str
    status: str = WorkflowStatus.RUNNING
    current_stage: Optional[str] = None
    initiator_id: Optional[str] = None
    repository: Dict[str, str] = Field(default_factory=dict)
    stages: Dict[str, str] = Field(default_factory=dict)
    pending_approval: Optional[Dict[str, Any]] = None
    retry_count: Dict[str, int] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class StateStore:
    """Simple file-backed state engine for .ai-sdlc workflow persistence.

    This class validates JSON state using the WorkflowState model and
    protects workflow persistence with file locks plus atomic temporary writes.
    """

    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace)
        self.state_dir = self.workspace / ".ai-sdlc"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.workflow_path = self.state_dir / "workflow.json"
        self.lock_path = self.state_dir / "workflow.lock"

        self.audit_path = self.state_dir / "audit"
        self.audit_path.mkdir(parents=True, exist_ok=True)
        self.audit_file = self.audit_path / "events.jsonl"

        self.approvals_dir = self.state_dir / "approvals"
        self.approvals_dir.mkdir(parents=True, exist_ok=True)
        self.changes_dir = self.state_dir / "changes"
        self.changes_dir.mkdir(parents=True, exist_ok=True)
        self.clarifications_dir = self.state_dir / "clarifications"
        self.clarifications_dir.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def _locked(self, exclusive: bool = True):
        self.lock_path.touch(exist_ok=True)
        with open(self.lock_path, "r+", encoding="utf-8") as lock_file:
            if fcntl is not None:
                flock_flag = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
                fcntl.flock(lock_file.fileno(), flock_flag)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def read_workflow(self) -> Optional[WorkflowState]:
        if not self.workflow_path.exists():
            return None
        with self._locked(exclusive=False):
            data = json.loads(self.workflow_path.read_text(encoding="utf-8"))
        return WorkflowState(**data)

    def write_workflow(self, state: WorkflowState) -> None:
        state.updated_at = utc_now_iso()
        tmp = self.state_dir / "workflow.json.tmp"
        with self._locked(exclusive=True):
            tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
            tmp.rename(self.workflow_path)

    def append_audit_event(self, event: Dict) -> None:
        event.setdefault("timestamp", utc_now_iso())
        line = json.dumps(event, ensure_ascii=False)
        with self._locked(exclusive=True):
            with self.audit_file.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def write_approval(self, approval_id: str, approval_record: Dict) -> Path:
        path = self.approvals_dir / f"{approval_id}.json"
        with self._locked(exclusive=True):
            path.write_text(json.dumps(approval_record, indent=2), encoding="utf-8")
        return path

    def write_change_request(self, cr_id: str, cr_record: Dict) -> Path:
        path = self.changes_dir / f"{cr_id}.json"
        with self._locked(exclusive=True):
            path.write_text(json.dumps(cr_record, indent=2), encoding="utf-8")
        return path

    def write_clarification(self, question_id: str, question_record: Dict) -> Path:
        path = self.clarifications_dir / f"{question_id}.json"
        with self._locked(exclusive=True):
            path.write_text(json.dumps(question_record, indent=2), encoding="utf-8")
        return path
