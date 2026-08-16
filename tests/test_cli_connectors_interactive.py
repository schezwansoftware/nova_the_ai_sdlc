"""Tests for `ai-sdlc init`'s connector-selection checklist
(`_resolve_connectors_interactively`) and its wiring into `run_init`.

Drives the real `ai-sdlc` Typer app exactly like
`test_cli_contract.py`'s agent-framework select-menu tests, using
`prompt_toolkit.input.create_pipe_input()` for the same reason documented
there: `questionary`'s prompts (built on `prompt_toolkit`) can't be driven
through Click/Typer's `CliRunner(..., input=...)` stdin substitution --
they need `prompt_toolkit`'s own input abstraction. Both
`questionary.select` (the agent-framework prompt, asked first) and
`questionary.checkbox` (the connectors prompt, asked second) are patched
to share one pipe, so a single fed key sequence drives both prompts in
one `init` invocation, exactly as a real interactive session would see
them back to back.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pytest
import questionary
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from typer.testing import CliRunner

from ai_sdlc.cli.config import load_config
from ai_sdlc.cli.main import app

runner = CliRunner()


@pytest.fixture
def cli_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config_dir = tmp_path / "cli-config"
    monkeypatch.setenv("AI_SDLC_CLI_CONFIG_DIR", str(config_dir))
    return config_dir


@contextmanager
def _patch_interactive_prompts(monkeypatch: pytest.MonkeyPatch, keys: str):
    """Feeds `keys` through one shared pipe to both `questionary.select`
    (agent framework) and `questionary.checkbox` (connectors) -- see
    module docstring."""
    monkeypatch.setattr("ai_sdlc.cli.handlers._is_interactive_session", lambda: True)
    with create_pipe_input() as pipe_input:
        pipe_input.send_text(keys)
        real_select = questionary.select
        real_checkbox = questionary.checkbox

        def _select_with_pipe(*args, **kwargs):
            kwargs.setdefault("input", pipe_input)
            kwargs.setdefault("output", DummyOutput())
            return real_select(*args, **kwargs)

        def _checkbox_with_pipe(*args, **kwargs):
            kwargs.setdefault("input", pipe_input)
            kwargs.setdefault("output", DummyOutput())
            return real_checkbox(*args, **kwargs)

        monkeypatch.setattr("ai_sdlc.cli.handlers.questionary.select", _select_with_pipe)
        monkeypatch.setattr("ai_sdlc.cli.handlers.questionary.checkbox", _checkbox_with_pipe)
        yield


def _read_connectors(workspace: Path) -> dict:
    return json.loads((workspace / ".ai-sdlc" / "connectors.json").read_text(encoding="utf-8"))


def test_interactive_init_writes_connectors_json_with_selected_names_enabled(
    tmp_path: Path, cli_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "repo"
    # "\n" accepts the default agent-framework choice (claude); " \n"
    # toggles the first connector choice (jira) then confirms.
    with _patch_interactive_prompts(monkeypatch, "\n \n"):
        result = runner.invoke(app, ["init", "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    assert "Scaffolded connectors config" in result.output

    payload = _read_connectors(workspace)
    enabled = {c["name"] for c in payload["connectors"] if c["enabled"]}
    assert enabled == {"jira"}


def test_interactive_init_selecting_none_still_writes_a_valid_config(
    tmp_path: Path, cli_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "repo"
    # "\n" for agent framework, then bare "\n" for the checklist -- confirm
    # with nothing toggled.
    with _patch_interactive_prompts(monkeypatch, "\n\n"):
        result = runner.invoke(app, ["init", "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    payload = _read_connectors(workspace)
    assert all(not c["enabled"] for c in payload["connectors"])


def test_non_interactive_init_writes_empty_connectors_config_without_prompting(
    tmp_path: Path, cli_config_dir: Path
) -> None:
    workspace = tmp_path / "repo"

    result = runner.invoke(
        app, ["init", "--workspace", str(workspace), "--agent-framework", "claude"]
    )

    assert result.exit_code == 0, result.output
    payload = _read_connectors(workspace)
    assert all(not c["enabled"] for c in payload["connectors"])


def test_rerun_leaves_hand_edited_connectors_config_untouched(
    tmp_path: Path, cli_config_dir: Path
) -> None:
    workspace = tmp_path / "repo"
    first = runner.invoke(app, ["init", "--workspace", str(workspace), "--agent-framework", "claude"])
    assert first.exit_code == 0, first.output

    config_path = workspace / ".ai-sdlc" / "connectors.json"
    hand_edited = json.loads(config_path.read_text(encoding="utf-8"))
    hand_edited["connectors"][0]["enabled"] = True
    hand_edited["connectors"][0]["command"] = "/usr/local/bin/jira-mcp"
    config_path.write_text(json.dumps(hand_edited), encoding="utf-8")

    second = runner.invoke(app, ["init", "--workspace", str(workspace)])
    assert second.exit_code == 0, second.output
    assert "Connectors config already present" in second.output

    payload = _read_connectors(workspace)
    assert payload["connectors"][0]["command"] == "/usr/local/bin/jira-mcp"


def test_cancelling_connector_selection_does_not_abort_the_whole_init(
    tmp_path: Path, cli_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ctrl-C during just the connectors checklist is non-fatal -- init
    still completes (config saved, agent metadata written), unlike
    cancelling the agent-framework prompt, which aborts everything."""
    workspace = tmp_path / "repo"
    monkeypatch.setattr("ai_sdlc.cli.handlers._is_interactive_session", lambda: True)

    from ai_sdlc.cli import handlers as handlers_module

    real_agent_framework_prompt = handlers_module._resolve_agent_framework_interactively

    def _fake_agent_framework_prompt(console):
        return "claude"

    def _raise_interrupt(console):
        raise KeyboardInterrupt()

    monkeypatch.setattr(handlers_module, "_resolve_agent_framework_interactively", _fake_agent_framework_prompt)
    monkeypatch.setattr(handlers_module, "_resolve_connectors_interactively", _raise_interrupt)

    result = runner.invoke(app, ["init", "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    assert "Skipped connector selection" in result.output
    assert load_config() is not None
    assert load_config().agent_framework == "claude"

    payload = _read_connectors(workspace)
    assert all(not c["enabled"] for c in payload["connectors"])

    # Not actually used, but keeps the real function referenced so a
    # future refactor that removes it is caught by an import error here
    # rather than this test silently testing nothing.
    assert real_agent_framework_prompt is not None
