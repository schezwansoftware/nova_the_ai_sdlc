"""Shared test fixtures/helpers.

`init_git_repo` turns an already-existing directory into a real, minimal
git repository with one commit on its default branch. Needed because the
Developer Agent's worktree lifecycle
(`ai_sdlc.agents.developer.worktree.ensure_clean_worktree`) always runs
real `git worktree` commands regardless of which `CodingCapability`
provider is configured -- isolation is this codebase's own responsibility
(see that module's docstring), not something a mock provider can stand in
for. Any test that drives a workflow through the real "development" node
needs `inputs["target_repository"]["workspace_path"]` to point at an
actual git repository, not just any directory.

In the real flow this is the same directory `.ai-sdlc/` lives in (Nova's
own workspace *is* the target application repository -- see
`OrchestratorAPI.start_workflow`'s `target_repository` population), so
tests git-init that same `workspace` path in place rather than modeling a
separate repository.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def _run(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True, text=True)


def init_git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "-q"], path)
    _run(["git", "config", "user.email", "test@example.com"], path)
    _run(["git", "config", "user.name", "Test"], path)
    (path / "README.md").write_text("test target repository\n", encoding="utf-8")
    _run(["git", "add", "."], path)
    _run(["git", "commit", "-q", "-m", "initial commit"], path)
    return path
