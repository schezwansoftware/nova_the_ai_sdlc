"""Tests for `CopilotCodingProvider`'s own logic -- worktree creation, git
diff parsing, self-check command execution, permission-decision mapping,
and the user-input auto-answer fallback.

None of these tests make a network call, require Copilot credentials, or
start a real Copilot session -- `create_session`/`send_and_wait` are never
invoked here. They exercise the provider's pure-Python plumbing (worktree
setup against a real *local* throwaway git repo, and the permission/
user-input callback functions in isolation) plus, where the real SDK's
request/response classes are needed to prove the wiring matches their
actual shape, the installed `github-copilot-sdk` package's own classes.

The whole module is skipped if `github-copilot-sdk` (the optional
`copilot` extra) isn't installed -- it requires Python 3.11+, stricter
than this project's own `>=3.10`, so it won't be present in every
environment. This mirrors the project convention that the *default* test
suite never requires anything beyond base dependencies; these tests only
run when the extra is present, and even then touch no network/credentials.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

copilot = pytest.importorskip("copilot")
from copilot.generated import session_events as se  # noqa: E402
from copilot.generated import rpc  # noqa: E402

from ai_sdlc.capabilities.coding import CodingRequest, ToolPolicy  # noqa: E402
from ai_sdlc.capabilities.providers.coding_copilot import (  # noqa: E402
    CopilotCodingProvider,
    _command_basename,
)


def _init_git_repo(path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "a@b.c"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True)
    subprocess.run(["git", "-C", str(path), "branch", "-M", "main"], check=True)


def _shell_permission_request(command_identifiers, full_text=""):
    commands = [se.PermissionRequestShellCommand(identifier=c, read_only=False) for c in command_identifiers]
    return se.PermissionRequestShell(
        can_offer_session_approval=False,
        commands=commands,
        full_command_text=full_text or " ".join(command_identifiers),
        has_write_file_redirection=False,
        intention="test",
        possible_paths=[],
        possible_urls=[],
    )


# -- worktree creation ----------------------------------------------------


def test_create_isolated_worktree_off_target_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    provider = CopilotCodingProvider()
    request = CodingRequest(
        task_summary="Add a health endpoint",
        task_brief="Add /health returning 200.",
        workspace_path=str(repo),
        base_branch="main",
    )
    branch_name = provider._derive_branch_name(request)
    worktree_path = provider._create_isolated_worktree(request, branch_name)

    assert worktree_path != str(repo)
    result = subprocess.run(
        ["git", "-C", worktree_path, "rev-parse", "--abbrev-ref", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == branch_name

    # The target repo itself is untouched (still on main, no new commits).
    target_branch = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert target_branch == "main"


def test_branch_name_is_slugified_and_prefixed():
    provider = CopilotCodingProvider()
    request = CodingRequest(
        task_summary="Add Redis Caching!! For Orders",
        task_brief="Do it.",
        workspace_path="/tmp/whatever",
    )
    branch_name = provider._derive_branch_name(request)
    assert branch_name.startswith("forge/")
    assert " " not in branch_name
    assert "!" not in branch_name


# -- git diff parsing -------------------------------------------------------


def test_collect_files_changed_parses_git_diff_name_status(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    provider = CopilotCodingProvider()
    request = CodingRequest(
        task_summary="task",
        task_brief="brief",
        workspace_path=str(repo),
        base_branch="main",
    )
    branch_name = provider._derive_branch_name(request)
    worktree_path = provider._create_isolated_worktree(request, branch_name)

    Path(worktree_path, "new_file.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", worktree_path, "add", "."], check=True)
    subprocess.run(
        ["git", "-C", worktree_path, "commit", "-q", "-m", "add new_file"], check=True
    )

    changes = provider._collect_files_changed(worktree_path, "main")
    assert {"path": "new_file.py", "change_type": "ADDED"} in changes


# -- self-check execution ----------------------------------------------------


def test_run_self_check_reports_skipped_when_no_commands_configured(tmp_path):
    provider = CopilotCodingProvider()
    result = provider._run_self_check(str(tmp_path), [])
    assert result["skipped_reason"] is not None
    assert result["commands_run"] == []
    assert result["build_passed"] is None


def test_run_self_check_runs_build_then_tests(tmp_path):
    provider = CopilotCodingProvider()
    result = provider._run_self_check(str(tmp_path), ["true", "true"])
    assert result["build_passed"] is True
    assert result["tests_passed"] is True
    assert result["commands_run"] == ["true", "true"]


def test_run_self_check_skips_tests_when_build_fails(tmp_path):
    provider = CopilotCodingProvider()
    result = provider._run_self_check(str(tmp_path), ["false", "true"])
    assert result["build_passed"] is False
    assert result["tests_passed"] is None
    assert result["commands_run"] == ["false"]


# -- permission handler -------------------------------------------------------


def test_permission_handler_approves_allowed_shell_command():
    provider = CopilotCodingProvider()
    policy = ToolPolicy(allowed_commands=["git", "pytest"], denied_commands=["sudo"])
    handler = provider._make_permission_handler(policy)
    decision = asyncio.run(handler(_shell_permission_request(["git"])))
    assert isinstance(decision, rpc.PermissionDecisionApproveOnce)


def test_permission_handler_rejects_command_outside_allow_list():
    provider = CopilotCodingProvider()
    policy = ToolPolicy(allowed_commands=["git"], denied_commands=["sudo"])
    handler = provider._make_permission_handler(policy)
    decision = asyncio.run(handler(_shell_permission_request(["curl"])))
    assert isinstance(decision, rpc.PermissionDecisionReject)


def test_permission_handler_rejects_explicitly_denied_command_even_if_allowed():
    provider = CopilotCodingProvider()
    policy = ToolPolicy(allowed_commands=["git", "sudo"], denied_commands=["sudo"])
    handler = provider._make_permission_handler(policy)
    decision = asyncio.run(handler(_shell_permission_request(["sudo"])))
    assert isinstance(decision, rpc.PermissionDecisionReject)


def test_command_basename_normalizes_paths_and_args():
    assert _command_basename("/usr/bin/git status") == "git"
    assert _command_basename("git") == "git"
    assert _command_basename("") == ""


# -- user-input auto-answer fallback ------------------------------------------


def test_user_input_handler_answers_with_first_choice_when_offered():
    provider = CopilotCodingProvider()
    handler = provider._make_user_input_handler()
    request = copilot.session.UserInputRequest(
        question="Which approach?", choices=["A", "B"], allowFreeform=True
    )
    response = asyncio.run(handler(request))
    assert response["answer"] == "A"
    assert response["wasFreeform"] is False
    assert provider._clarification_log == ["Which approach?"]


def test_user_input_handler_falls_back_to_freeform_note_when_no_choices():
    provider = CopilotCodingProvider()
    handler = provider._make_user_input_handler()
    request = copilot.session.UserInputRequest(
        question="What should the default TTL be?", choices=[], allowFreeform=True
    )
    response = asyncio.run(handler(request))
    assert response["wasFreeform"] is True
    assert "No human reviewer" in response["answer"]


# -- constructor guard -------------------------------------------------------


def test_provider_constructs_when_sdk_is_installed():
    # This test only proves the happy path when the extra IS installed
    # (the whole module is skipped otherwise via importorskip above). The
    # "SDK not installed" guard itself is exercised by
    # `test_coding_copilot_import_guard.py`, run under the *base*
    # environment where the extra is absent.
    provider = CopilotCodingProvider()
    assert provider.model is None
