"""Regression coverage for a real bug hit while manually testing: a stale
`ai-sdlc init --start-server` process from an earlier run was still alive
and reachable, so a later `init --start-server` against a *recreated*
workspace directory (`rm -rf`'d and re-`mkdir`'d between test attempts)
never spawned a fresh process -- it just reused the old, still-running
one, whose `StateStore` had only created `.ai-sdlc/workflows/` etc. once,
at its own long-past startup. The next write crashed with a raw
`FileNotFoundError` ("No such file or directory") on the atomic
temp-file-rename, since the destination directory no longer existed.

No network access / external credentials required.
"""
from __future__ import annotations

import shutil

from ai_sdlc.orchestration.state import StateStore, WorkflowState


def test_write_workflow_recreates_a_deleted_workflows_directory(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    store = StateStore(workspace)
    assert store.workflows_dir.exists()

    # Simulate the real-world scenario: something deleted the whole
    # `.ai-sdlc/` tree (a workspace `rm -rf` + recreate cycle) while this
    # StateStore instance -- representing a long-lived server process --
    # is still alive and holds no further reference to "does this
    # directory still exist."
    shutil.rmtree(store.state_dir)
    assert not store.workflows_dir.exists()

    state = WorkflowState(workflow_id="wf-selfheal", initiator_id="user-1")
    store.write_workflow(state)  # must not raise FileNotFoundError

    assert store.workflows_dir.exists()
    reloaded = store.read_workflow("wf-selfheal")
    assert reloaded is not None
    assert reloaded.workflow_id == "wf-selfheal"


def test_append_audit_event_recreates_a_deleted_audit_directory(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    store = StateStore(workspace)
    shutil.rmtree(store.state_dir)
    assert not store.audit_path.exists()

    store.append_audit_event({"event": "test_event"})  # must not raise

    assert store.audit_file.exists()
    assert "test_event" in store.audit_file.read_text(encoding="utf-8")


def test_write_approval_recreates_a_deleted_approvals_directory(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    store = StateStore(workspace)
    shutil.rmtree(store.state_dir)
    assert not store.approvals_dir.exists()

    path = store.write_approval("appr-1", {"decision": "approved"})  # must not raise

    assert path.exists()
    assert store.read_approval("appr-1") == {"decision": "approved"}
