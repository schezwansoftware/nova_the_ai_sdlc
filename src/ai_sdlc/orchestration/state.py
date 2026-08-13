from __future__ import annotations
import contextlib
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Dict, Optional, Any

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None
from datetime import datetime, timezone
from pydantic import BaseModel, Field

DEFAULT_LOCK_TIMEOUT = 5.0


class WorkflowLockTimeoutError(Exception):
    """Raised when a compound read-modify-write transaction cannot acquire
    the in-process workflow lock within the timeout."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


class WorkflowStatus(str):
    """Centralized internal workflow status vocabulary.

    Values are the authoritative on-disk string representations; do not
    change them without a migration, since they are persisted in
    .ai-sdlc/workflows/{workflow_id}.json.
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

    The fcntl-based `_locked()` lock below only protects each individual file
    operation (a single read or a single write) from corruption. It does NOT
    make a compound "load -> mutate -> save" sequence atomic — two such
    sequences can still interleave and silently lose one side's update. Code
    performing a compound read-modify-write against the workflow must hold
    `transaction()` for the full sequence.
    """

    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace)
        self.state_dir = self.workspace / ".ai-sdlc"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        # Each workflow gets its own file, keyed by workflow_id, so the
        # REST API's per-id addressing (/v1/workflows/{id}) doesn't clobber
        # other in-flight workflows in the same workspace. `current_pointer_path`
        # tracks the most-recently-written workflow_id purely so that legacy
        # no-id callers (read_workflow()/write_workflow() with no id) keep
        # working for the common single-workflow-per-workspace case.
        self.workflows_dir = self.state_dir / "workflows"
        self.workflows_dir.mkdir(parents=True, exist_ok=True)
        self.current_pointer_path = self.state_dir / "current_workflow.json"
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

        # In-process mutex guarding compound read-modify-write sequences.
        # This is scoped to this StateStore instance, which is sufficient
        # because the platform HTTP server (ThreadedHTTPServer) runs a
        # single process with one shared CorePlatform/StateStore instance
        # across all request threads.
        self._mutex = threading.Lock()

    @contextlib.contextmanager
    def transaction(self, timeout: float = DEFAULT_LOCK_TIMEOUT):
        """Serialize a compound read-modify-write sequence against the
        workflow (load_workflow() -> mutate -> save_workflow()) so concurrent
        callers can't interleave and lose each other's updates.

        Raises WorkflowLockTimeoutError if the lock isn't acquired within
        `timeout` seconds, instead of blocking forever.
        """
        acquired = self._mutex.acquire(timeout=timeout)
        if not acquired:
            raise WorkflowLockTimeoutError(f"Could not acquire workflow lock within {timeout}s")
        try:
            yield
        finally:
            self._mutex.release()

    @contextlib.contextmanager
    def _locked(self, exclusive: bool = True):
        # Same self-healing reasoning as `_atomic_write_text` -- if the
        # whole `.ai-sdlc/` tree (not just one subdirectory under it) was
        # deleted out from under this long-lived StateStore, `os.open`
        # with O_CREAT still needs the *parent* directory to exist.
        self.state_dir.mkdir(parents=True, exist_ok=True)
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
        # `__init__` creates `workflows_dir`/`approvals_dir`/etc. once, at
        # StateStore construction time -- but this is a long-lived server
        # process (spawned once by `cli/bootstrap.py:spawn_server` and left
        # running, with no lifecycle tying it to any particular caller), so
        # nothing re-verifies those directories still exist on every write.
        # If the workspace's `.ai-sdlc/` tree is deleted out from under a
        # running server (e.g. a stale, still-running server from an
        # earlier `--start-server` -- `init` only spawns a new one if the
        # configured host:port isn't already reachable, so a leftover
        # process from a previous run keeps serving against a workspace
        # that's since been recreated), `os.replace()` below fails with
        # ENOENT on the *destination* directory, not something recoverable
        # by retrying the write itself. Recreating the parent directory
        # defensively here -- the same `mkdir(parents=True, exist_ok=True)`
        # `__init__` already uses -- makes every write self-healing against
        # this instead of surfacing a confusing raw ENOENT.
        path.parent.mkdir(parents=True, exist_ok=True)
        # `self.state_dir` itself (not just `path.parent`) could be the
        # thing that's gone missing -- e.g. the whole `.ai-sdlc/` tree was
        # removed, not just one subdirectory -- and the temp file below is
        # created directly inside it, before `path.parent` is even
        # touched. Same defensive recreation, same reasoning as above.
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.state_dir, delete=False) as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        os.replace(temp_path, path)

    def _atomic_write_json(self, path: Path, payload: Dict[str, Any]) -> None:
        self._atomic_write_text(path, json.dumps(payload, indent=2))

    def _workflow_path(self, workflow_id: str) -> Path:
        return self.workflows_dir / f"{workflow_id}.json"

    def _read_current_pointer(self) -> Optional[str]:
        if not self.current_pointer_path.exists():
            return None
        try:
            return json.loads(self.current_pointer_path.read_text(encoding="utf-8")).get("workflow_id")
        except (json.JSONDecodeError, OSError):
            return None

    def read_workflow(self, workflow_id: Optional[str] = None) -> Optional[WorkflowState]:
        with self._locked(exclusive=False):
            if workflow_id is None:
                workflow_id = self._read_current_pointer()
                if workflow_id is None:
                    return None
            path = self._workflow_path(workflow_id)
            if not path.exists():
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
        return WorkflowState(**data)

    def write_workflow(self, state: WorkflowState) -> None:
        state.updated_at = utc_now_iso()
        with self._locked(exclusive=True):
            self._atomic_write_text(self._workflow_path(state.workflow_id), state.model_dump_json(indent=2))
            self._atomic_write_text(self.current_pointer_path, json.dumps({"workflow_id": state.workflow_id}))

    def append_audit_event(self, event: Dict) -> None:
        event.setdefault("timestamp", utc_now_iso())
        line = json.dumps(event, ensure_ascii=False)
        with self._locked(exclusive=True):
            # Same self-healing reasoning as `_atomic_write_text` -- this
            # doesn't go through it (append mode, not atomic-replace), but
            # is exposed to the identical "directory deleted out from under
            # a long-lived server" failure mode.
            self.audit_path.mkdir(parents=True, exist_ok=True)
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
