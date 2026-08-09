from __future__ import annotations
import contextlib
import json
import os
import tempfile
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
    """Centralized internal workflow status vocabulary.

    Values are the authoritative on-disk string representations; do not
    change them without a migration, since they are persisted in
    .ai-sdlc/workflow.json.
    """

    RUNNING = "running"
    WAITING_FOR_CLARIFICATION = "paused"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    REVISION_REQUIRED = "revision_required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowState(BaseModel):
    schema_version: str = "workflow-v1"
    workflow_id: str
    status: str = WorkflowStatus.RUNNING
    current_stage: Optional[str] = None
    initiator_id: Optional[str] = None
    repository: Dict[str, str] = Field(default_factory=dict)
    stages: Dict[str, str] = Field(default_factory=dict)
    inputs: Dict[str, Any] = Field(default_factory=dict)
    pending_clarification: Optional[Dict[str, Any]] = None
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
        lock_fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if fcntl is not None:
                flock_flag = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
                fcntl.flock(lock_fd, flock_flag)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)

    def _atomic_write_text(self, path: Path, content: str) -> None:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.state_dir, delete=False) as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        os.replace(temp_path, path)

    def _atomic_write_json(self, path: Path, payload: Dict[str, Any]) -> None:
        self._atomic_write_text(path, json.dumps(payload, indent=2))

    def read_workflow(self) -> Optional[WorkflowState]:
        with self._locked(exclusive=False):
            if not self.workflow_path.exists():
                return None
            data = json.loads(self.workflow_path.read_text(encoding="utf-8"))
        return WorkflowState(**data)

    def write_workflow(self, state: WorkflowState) -> None:
        state.updated_at = utc_now_iso()
        with self._locked(exclusive=True):
            self._atomic_write_text(self.workflow_path, state.model_dump_json(indent=2))

    def append_audit_event(self, event: Dict) -> None:
        event.setdefault("timestamp", utc_now_iso())
        line = json.dumps(event, ensure_ascii=False)
        with self._locked(exclusive=True):
            with self.audit_file.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def write_approval(self, approval_id: str, approval_record: Dict) -> Path:
        path = self.approvals_dir / f"{approval_id}.json"
        with self._locked(exclusive=True):
            self._atomic_write_json(path, approval_record)
        return path

    def read_approval(self, approval_id: str) -> Optional[Dict]:
        path = self.approvals_dir / f"{approval_id}.json"
        with self._locked(exclusive=False):
            if not path.exists():
                return None
            return json.loads(path.read_text(encoding="utf-8"))

    def write_change_request(self, cr_id: str, cr_record: Dict) -> Path:
        path = self.changes_dir / f"{cr_id}.json"
        with self._locked(exclusive=True):
            self._atomic_write_json(path, cr_record)
        return path

    def write_clarification(self, question_id: str, question_record: Dict) -> Path:
        path = self.clarifications_dir / f"{question_id}.json"
        with self._locked(exclusive=True):
            self._atomic_write_json(path, question_record)
        return path

    def read_clarification(self, question_id: str) -> Optional[Dict]:
        path = self.clarifications_dir / f"{question_id}.json"
        with self._locked(exclusive=False):
            if not path.exists():
                return None
            return json.loads(path.read_text(encoding="utf-8"))
