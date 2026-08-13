"""Tests for `CopilotCodingProvider`'s own logic -- working-tree
verification, git diff/branch introspection, self-check command
execution, permission-decision mapping, and the user-input auto-answer
fallback.

None of these tests make a network call, require Copilot credentials, or
start a real Copilot session -- `create_session`/`send_and_wait` are never
invoked here. They exercise the provider's pure-Python plumbing (git
introspection against a real *local* throwaway git repo, and the
permission/user-input callback functions in isolation) plus, where the
real SDK's request/response classes are needed to prove the wiring
matches their actual shape, the installed `github-copilot-sdk` package's
own classes.

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

from ai_sdlc.capabilities.coding import CodingRequest, ProviderError  # noqa: E402
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


def _make_request(working_tree_path, **overrides):
    fields = dict(
        task_title="Add a health endpoint",
        task_summary="Add /health returning 200.",
        working_tree_path=str(working_tree_path),
        base_branch="main",
        allowed_tools=["Bash"],
        allowed_commands=["git", "pytest"],
    )
    fields.update(overrides)
    return CodingRequest(**fields)


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


# -- construction / working-tree verification --------------------------------


def test_provider_constructs_when_sdk_is_installed():
    provider = CopilotCodingProvider()
    assert provider.model is None


def test_execute_rejects_missing_working_tree(tmp_path):
    provider = CopilotCodingProvider()
    request = _make_request(tmp_path / "does-not-exist")
    with pytest.raises(ProviderError):
        provider.execute(request)


def test_execute_rejects_non_git_directory(tmp_path):
    (tmp_path / "not_a_repo").mkdir()
    provider = CopilotCodingProvider()
    request = _make_request(tmp_path / "not_a_repo")
    with pytest.raises(ProviderError):
        provider.execute(request)


# -- git introspection --------------------------------------------------------


def test_current_branch_reads_real_git_state(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    provider = CopilotCodingProvider()
    assert provider._current_branch(str(repo)) == "main"


def test_files_changed_parses_git_diff_name_only(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    Path(repo, "new_file.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "add new_file"], check=True)

    provider = CopilotCodingProvider()
    changed = provider._files_changed(str(repo), "main")
    # base_branch == HEAD's own branch here, so the diff against itself is
    # empty; verify against a throwaway divergent branch instead.
    assert changed == []

    subprocess.run(["git", "-C", str(repo), "branch", "base-point", "HEAD~1"], check=True)
    changed = provider._files_changed(str(repo), "base-point")
    assert changed == ["new_file.py"]


# -- self-check execution ----------------------------------------------------


def test_run_self_check_reports_skipped_when_no_commands_configured(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    provider = CopilotCodingProvider()
    request = _make_request(repo)
    result = provider._run_self_check(request)
    assert result.skipped_reason is not None
    assert result.commands_run == []
    assert result.build_passed is None
    assert result.tests_passed is None


def test_run_self_check_runs_build_then_tests(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    provider = CopilotCodingProvider()
    request = _make_request(repo, build_commands=["true"], test_commands=["true"])
    result = provider._run_self_check(request)
    assert result.build_passed is True
    assert result.tests_passed is True
    assert result.commands_run == ["true", "true"]


def test_run_self_check_reports_build_failure(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    provider = CopilotCodingProvider()
    request = _make_request(repo, build_commands=["false"], test_commands=["true"])
    result = provider._run_self_check(request)
    assert result.build_passed is False
    assert result.tests_passed is True  # test_commands still run independently


# -- permission handler -------------------------------------------------------


def test_permission_handler_approves_allowed_shell_command(tmp_path):
    provider = CopilotCodingProvider()
    request = _make_request(tmp_path, allowed_tools=["Bash"], allowed_commands=["git"])
    handler = provider._make_permission_handler(request)
    decision = asyncio.run(handler(_shell_permission_request(["git"])))
    assert isinstance(decision, rpc.PermissionDecisionApproveOnce)


def test_permission_handler_rejects_command_outside_allow_list(tmp_path):
    provider = CopilotCodingProvider()
    request = _make_request(tmp_path, allowed_tools=["Bash"], allowed_commands=["git"])
    handler = provider._make_permission_handler(request)
    decision = asyncio.run(handler(_shell_permission_request(["curl"])))
    assert isinstance(decision, rpc.PermissionDecisionReject)


def test_permission_handler_rejects_shell_when_bash_not_in_allowed_tools(tmp_path):
    provider = CopilotCodingProvider()
    request = _make_request(tmp_path, allowed_tools=["Read"], allowed_commands=["git"])
    handler = provider._make_permission_handler(request)
    decision = asyncio.run(handler(_shell_permission_request(["git"])))
    assert isinstance(decision, rpc.PermissionDecisionReject)


def test_permission_handler_rejects_git_push_even_if_allowed(tmp_path):
    provider = CopilotCodingProvider()
    request = _make_request(tmp_path, allowed_tools=["Bash"], allowed_commands=["git"])
    handler = provider._make_permission_handler(request)
    decision = asyncio.run(
        handler(_shell_permission_request(["git"], full_text="git push origin forge/x"))
    )
    assert isinstance(decision, rpc.PermissionDecisionReject)


def test_command_basename_normalizes_paths_and_args():
    assert _command_basename("/usr/bin/git status") == "git"
    assert _command_basename("git") == "git"
    assert _command_basename("") == ""


# -- final-event summary extraction -------------------------------------------
#
# Regression coverage for a real bug found via live testing against the
# installed github-copilot-sdk==1.0.9: `send_and_wait` returns a
# `SessionEvent` whose actual text lives at `.data.content` (an
# `AssistantMessageData`), never at a top-level `.result`/`.summary` --
# see `session.py`'s own docstring example (`match response.data: case
# AssistantMessageData() as data: print(data.content)`), verified live
# against a real authenticated session. `_build_coding_result` previously
# read the wrong path and silently fell back to the generic
# "Applied changes for: ..." summary on every real Copilot call.


def test_build_coding_result_extracts_summary_from_event_data_content(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    provider = CopilotCodingProvider()
    request = _make_request(repo)
    final_event = {"data": {"content": "Implemented the health endpoint and added a test."}}
    result = provider._build_coding_result(request, final_event, steps_used=3, max_steps=40)
    assert result.summary == "Implemented the health endpoint and added a test."


def test_build_coding_result_falls_back_to_generic_summary_when_no_content(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    provider = CopilotCodingProvider()
    request = _make_request(repo)
    result = provider._build_coding_result(request, final_event=None, steps_used=1, max_steps=40)
    assert result.summary == f"Applied changes for: {request.task_title}."


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
