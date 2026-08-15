"""Unit tests for `ai_sdlc.agents.developer.worktree`.

These exercise real `git worktree` commands against a real repository
(via `tests.conftest.init_git_repo`) -- this module's whole job is to be
the thing that actually creates the isolation `CodingCapability` providers
assume already exists (see its module docstring), so a test that never
touches real git would prove nothing about it.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ai_sdlc.agents.developer.worktree import (
    WorktreeError,
    branch_name,
    detect_base_branch,
    ensure_clean_worktree,
    remove_worktree,
    sweep_orphaned_worktrees,
    worktree_path,
    worktree_root,
)
from tests.conftest import init_git_repo


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_worktree_path_and_branch_name_are_deterministic_and_namespaced(tmp_path):
    workspace = tmp_path / "repo"
    p1 = worktree_path(workspace, "developer", "wf-1")
    p2 = worktree_path(workspace, "developer", "wf-1")
    assert p1 == p2
    assert p1 != worktree_path(workspace, "developer", "wf-2")
    assert p1 != worktree_path(workspace, "other-agent", "wf-1")
    # Sibling of the workspace, never nested inside it.
    assert worktree_root(workspace).parent == workspace.parent
    assert workspace not in p1.parents

    assert branch_name("developer", "wf-1") == branch_name("developer", "wf-1")
    assert branch_name("developer", "wf-1") != branch_name("developer", "wf-2")


def test_detect_base_branch_returns_checked_out_branch(tmp_path):
    workspace = init_git_repo(tmp_path / "repo")
    branch = detect_base_branch(workspace)
    assert branch == _git(["rev-parse", "--abbrev-ref", "HEAD"], workspace)


def test_detect_base_branch_raises_worktree_error_for_non_repo(tmp_path):
    not_a_repo = tmp_path / "plain-dir"
    not_a_repo.mkdir()
    with pytest.raises(WorktreeError):
        detect_base_branch(not_a_repo)


def test_ensure_clean_worktree_creates_a_real_isolated_worktree(tmp_path):
    workspace = init_git_repo(tmp_path / "repo")
    base_branch = detect_base_branch(workspace)

    path = ensure_clean_worktree(workspace, "developer", "wf-1", base_branch)

    assert path == worktree_path(workspace, "developer", "wf-1")
    assert path.is_dir()
    assert (path / ".git").is_file()  # linked worktree, not a second real repo
    assert (path / "README.md").is_file()  # base_branch content is present
    assert _git(["rev-parse", "--abbrev-ref", "HEAD"], path) == branch_name("developer", "wf-1")
    # The target repo's own live checkout is completely untouched.
    assert _git(["status", "--porcelain"], workspace) == ""


def test_ensure_clean_worktree_is_idempotent_and_returns_the_same_path(tmp_path):
    workspace = init_git_repo(tmp_path / "repo")
    base_branch = detect_base_branch(workspace)

    first = ensure_clean_worktree(workspace, "developer", "wf-1", base_branch)
    second = ensure_clean_worktree(workspace, "developer", "wf-1", base_branch)

    assert first == second
    assert _git(["worktree", "list"], workspace).count(str(first)) == 1


def test_ensure_clean_worktree_resets_prior_committed_and_uncommitted_state(tmp_path):
    """Every legitimate re-entry (a retryable provider failure, or a
    rejected approval retried with revision feedback) means the previous
    attempt's state should NOT survive -- see the module's "Re-entry"
    docstring section. A granted approval never reaches this function
    again at all (LangGraphRunner.resume_after_approval doesn't
    re-invoke the agent), so there is no case where prior state should be
    preserved."""
    workspace = init_git_repo(tmp_path / "repo")
    base_branch = detect_base_branch(workspace)

    path = ensure_clean_worktree(workspace, "developer", "wf-1", base_branch)
    (path / "new_file.py").write_text("changed = True\n", encoding="utf-8")
    _git(["add", "."], path)
    _git(["commit", "-m", "a previous, now-superseded attempt"], path)
    (path / "untracked.txt").write_text("stray\n", encoding="utf-8")
    assert (path / "new_file.py").exists()
    assert (path / "untracked.txt").exists()

    reset_path = ensure_clean_worktree(workspace, "developer", "wf-1", base_branch)

    assert reset_path == path
    assert not (path / "new_file.py").exists()
    assert not (path / "untracked.txt").exists()
    assert _git(["rev-parse", "HEAD"], path) == _git(["rev-parse", base_branch], workspace)


def test_ensure_clean_worktree_reattaches_to_leftover_branch(tmp_path):
    """If a worktree directory was removed but its branch wasn't deleted
    (e.g. an interrupted cleanup), creating a fresh worktree must reuse
    the existing branch rather than failing with "branch already
    exists"."""
    workspace = init_git_repo(tmp_path / "repo")
    base_branch = detect_base_branch(workspace)

    path = ensure_clean_worktree(workspace, "developer", "wf-1", base_branch)
    _git(["worktree", "remove", "--force", str(path)], workspace)
    assert branch_name("developer", "wf-1") in _git(["branch", "--list"], workspace)
    assert not path.exists()

    recreated = ensure_clean_worktree(workspace, "developer", "wf-1", base_branch)
    assert recreated == path
    assert path.is_dir()


def test_remove_worktree_deletes_directory_and_branch(tmp_path):
    workspace = init_git_repo(tmp_path / "repo")
    base_branch = detect_base_branch(workspace)
    path = ensure_clean_worktree(workspace, "developer", "wf-1", base_branch)

    remove_worktree(workspace, "developer", "wf-1")

    assert not path.exists()
    assert branch_name("developer", "wf-1") not in _git(["branch", "--list"], workspace)


def test_remove_worktree_is_safe_when_nothing_was_ever_created(tmp_path):
    workspace = init_git_repo(tmp_path / "repo")
    remove_worktree(workspace, "developer", "wf-never-ran")  # must not raise


def test_sweep_orphaned_worktrees_removes_unlisted_and_keeps_listed(tmp_path):
    workspace = init_git_repo(tmp_path / "repo")
    base_branch = detect_base_branch(workspace)
    live_path = ensure_clean_worktree(workspace, "developer", "wf-live", base_branch)
    orphan_path = ensure_clean_worktree(workspace, "developer", "wf-orphan", base_branch)

    removed = sweep_orphaned_worktrees(workspace, "developer", keep_workflow_ids={"wf-live"})

    assert removed == ["wf-orphan"]
    assert live_path.exists()
    assert not orphan_path.exists()


def test_sweep_orphaned_worktrees_on_empty_root_returns_empty_list(tmp_path):
    workspace = init_git_repo(tmp_path / "repo")
    assert sweep_orphaned_worktrees(workspace, "developer", keep_workflow_ids=set()) == []
