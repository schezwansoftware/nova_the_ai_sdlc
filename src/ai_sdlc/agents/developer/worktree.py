"""Isolated Git worktree lifecycle for the Developer Agent.

Neither of `CodingCapability`'s real providers create the isolation they
operate inside -- `claude_sdk.py`/`coding_copilot.py` both require an
*already-isolated* `working_tree_path` and only ever verify it, never
create it (see `coding.py`'s module docstring, and `coding_copilot.py`'s
own reconciliation note about an earlier draft getting this backwards).
Left unmanaged, both providers would happily operate directly on whatever
path they're handed -- including the initiator's live checkout, if that's
what a caller passed. This module is what makes "never the initiator's
live checkout" (section 4/7/10 of the architecture doc) actually true: it
is the only thing in this codebase that runs `git worktree add`.

## Placement: a sibling directory, not inside the target repo

Worktrees live at `<workspace>.ai-sdlc-worktrees/<node_id>/<workflow_id>`,
next to the target repository rather than nested inside it (e.g. under
`.ai-sdlc/`). `.ai-sdlc/` is Nova's own control-plane state directory, but
nothing in `ai-sdlc init` adds it to the target repo's `.gitignore` today
-- nesting a live worktree there would mean `git status` in the
initiator's real checkout starts showing an untracked directory full of
what looks like tracked-repo content, which is exactly the kind of
live-checkout noise the isolation requirement exists to avoid. A sibling
directory (mirroring the `<repo>.worktrees/<branch>` convention this very
project's own contributors already use manually for parallel work) keeps
the target repo's own tree untouched and survives independently of
whatever `.gitignore` the target repo happens to have.

## Re-entry: reset, never resume-and-skip

`DeveloperAgent.execute()` can be called again for the same
`(node_id, workflow_id)` pair in exactly two legitimate cases: a retryable
`ProviderError` (Orchestrator's own `max_attempts` loop) or a rejected
approval being retried with revision feedback folded in (Orion sets
`wf.status = REVISION_REQUIRED` and threads `revision_feedback` onto
`wf.inputs`, but nothing about that path re-runs the agent
automatically -- see `orchestrator.py`'s `resume_workflow_after_approval`
rejected branch). Neither case means "the previous attempt's commits are
still good, just report them again" -- a `ProviderError` means the
provider didn't finish, and a rejection means a human explicitly said the
previous diff was wrong. The one case that used to mean "don't redo the
work, just reuse the previous result" -- a granted approval -- no longer
re-invokes this agent at all as of Orion's `resume_after_approval` fix
(`langgraph_runner.py`), so it never reaches this module a second time.
`ensure_clean_worktree` therefore always resets an existing worktree back
to `base_branch` before reuse rather than trying to detect and preserve
prior committed state -- there is no re-entry scenario left where prior
state should survive.

## What happens to the worktree after approval

Per the currently agreed Developer Agent scope ("stop at approved diff" --
push/PR-open is a deferred follow-up pass), an approved worktree is the
*only* copy of the produced change until that follow-up pass exists and
pushes it. `remove_worktree` is therefore never called on the approval
path -- only on rejection-without-retry (cancellation) and by
`sweep_orphaned_worktrees` (a crash-recovery safety net, not wired into
any automatic trigger yet; see that function's own docstring).
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable, List

_BRANCH_PREFIX = "ai-sdlc"


class WorktreeError(Exception):
    """Raised when a git worktree operation fails. Callers (the Developer
    Agent) should generally treat this as retryable -- the same posture
    `ProviderError` already has for a coding-provider failure, since the
    underlying cause (disk space, a stale git lock, a missing base branch)
    is often transient or operator-fixable, not a permanent defect in the
    request itself."""


def _run(args: List[str], *, cwd: str | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorktreeError(f"failed to run {args!r}: {exc}") from exc


def _run_checked(args: List[str], *, cwd: str | None = None) -> subprocess.CompletedProcess:
    completed = _run(args, cwd=cwd)
    if completed.returncode != 0:
        raise WorktreeError(
            f"{' '.join(args)!r} failed (exit {completed.returncode}): {completed.stderr.strip()}"
        )
    return completed


def worktree_root(workspace_path: Path) -> Path:
    """Sibling directory holding every Developer-Agent-managed worktree for
    this target repository, grouped by node id then workflow id. See the
    module docstring's "Placement" section for why this is a sibling of
    `workspace_path` rather than nested inside it."""
    return workspace_path.parent / f"{workspace_path.name}.ai-sdlc-worktrees"


def worktree_path(workspace_path: Path, node_id: str, workflow_id: str) -> Path:
    """Deterministic per-(node, workflow) path -- the same call, or a
    legitimate retry/revision call for the same workflow stage, always
    resolves to the same location. Determinism (rather than a fresh
    tempdir per call) is what lets a resumed call find and reset the same
    worktree instead of leaking a new one every attempt."""
    return worktree_root(workspace_path) / node_id / workflow_id


def branch_name(node_id: str, workflow_id: str) -> str:
    return f"{_BRANCH_PREFIX}/{node_id}/{workflow_id}"


def detect_base_branch(workspace_path: Path) -> str:
    """The target repository's currently checked-out branch, used as the
    default `base_branch` when the caller doesn't specify one. Deliberately
    not hardcoded to "main" (unlike `CodingRequest.base_branch`'s own
    fallback default) -- the target repo is an arbitrary application
    repository whose default branch name Nova has no reason to assume,
    and asking `git` what's actually checked out is a correct answer
    "main" would only be a guess at."""
    completed = _run_checked(["git", "-C", str(workspace_path), "rev-parse", "--abbrev-ref", "HEAD"])
    branch = completed.stdout.strip()
    if not branch or branch == "HEAD":
        raise WorktreeError(
            f"could not determine a base branch for {workspace_path} "
            "(detached HEAD or not a git repository); pass one explicitly"
        )
    return branch


def _is_existing_worktree(path: Path) -> bool:
    # A real git worktree has a `.git` *file* (not directory) pointing back
    # at the main repository's `.git/worktrees/<name>` -- checking for that
    # file specifically (not just directory existence) avoids mistaking an
    # unrelated leftover directory at this deterministic path for a worktree
    # we can safely reset.
    return path.is_dir() and (path / ".git").is_file()


def ensure_clean_worktree(workspace_path: Path, node_id: str, workflow_id: str, base_branch: str) -> Path:
    """Return an isolated worktree for `(node_id, workflow_id)`, freshly
    created off `base_branch` if none exists yet, or hard-reset back to
    `base_branch` if one already does. See the module docstring's
    "Re-entry" section for why reset (never resume-and-skip) is correct
    for every legitimate re-entry case."""
    path = worktree_path(workspace_path, node_id, workflow_id)
    branch = branch_name(node_id, workflow_id)

    if _is_existing_worktree(path):
        _run_checked(["git", "-C", str(path), "reset", "--hard", base_branch])
        _run_checked(["git", "-C", str(path), "clean", "-fd"])
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    branch_exists = _run(["git", "-C", str(workspace_path), "rev-parse", "--verify", "--quiet", branch]).returncode == 0
    if branch_exists:
        # Leftover branch from a worktree that was removed but whose branch
        # wasn't deleted (e.g. an interrupted cleanup) -- attach a fresh
        # worktree to it rather than failing on "branch already exists".
        _run_checked(["git", "-C", str(workspace_path), "worktree", "add", str(path), branch])
        _run_checked(["git", "-C", str(path), "reset", "--hard", base_branch])
        _run_checked(["git", "-C", str(path), "clean", "-fd"])
    else:
        _run_checked(
            ["git", "-C", str(workspace_path), "worktree", "add", "-b", branch, str(path), base_branch]
        )
    return path


def remove_worktree(workspace_path: Path, node_id: str, workflow_id: str) -> None:
    """Delete a worktree and its branch. Best-effort on the branch delete
    (`-D` after the worktree itself is already gone can legitimately fail
    if, say, it was never actually created) -- the worktree removal is the
    part that must succeed or raise."""
    path = worktree_path(workspace_path, node_id, workflow_id)
    if path.exists():
        _run_checked(["git", "-C", str(workspace_path), "worktree", "remove", "--force", str(path)])
    branch = branch_name(node_id, workflow_id)
    _run(["git", "-C", str(workspace_path), "branch", "-D", branch])


def sweep_orphaned_worktrees(workspace_path: Path, node_id: str, keep_workflow_ids: Iterable[str]) -> List[str]:
    """Remove every `node_id` worktree whose workflow id is not in
    `keep_workflow_ids` -- a crash-recovery safety net for worktrees left
    behind by a process that died mid-run before it could clean up after
    itself (e.g. a rejection-without-retry, or a cancelled workflow).

    Not wired into any automatic trigger yet (no periodic sweep, no
    call from CLI startup) -- callers should pass the live set of
    RUNNING/WAITING_FOR_APPROVAL workflow ids for this node (from the
    `StateStore`) and decide when to invoke this themselves. Returns the
    list of workflow ids actually removed, for logging/observability.
    """
    root = worktree_root(workspace_path) / node_id
    if not root.is_dir():
        return []
    keep = set(keep_workflow_ids)
    removed: List[str] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name in keep:
            continue
        remove_worktree(workspace_path, node_id, entry.name)
        removed.append(entry.name)
    return removed
