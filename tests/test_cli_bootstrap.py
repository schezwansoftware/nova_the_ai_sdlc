"""Tests for `ai_sdlc.cli.bootstrap.spawn_server`'s `env` threading, and
`write_connectors_config`'s `.ai-sdlc/connectors.json` scaffolding.

`subprocess.Popen` is monkeypatched rather than actually spawning a
process -- these tests only need to prove *what gets passed* to `Popen`,
not that the Core Platform API subprocess actually starts (that end-to-end
behavior is already covered by `test_cli_contract.py`'s
`test_init_start_server_spawns_and_waits_for_reachability`, which spawns a
real subprocess).

No network access / external credentials required.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_sdlc.cli import bootstrap


class _FakePopen:
    def __init__(self, args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.pid = 12345


@pytest.fixture
def fake_popen(monkeypatch: pytest.MonkeyPatch):
    calls = []

    def _factory(args, **kwargs):
        proc = _FakePopen(args, **kwargs)
        calls.append(proc)
        return proc

    monkeypatch.setattr(bootstrap.subprocess, "Popen", _factory)
    return calls


def test_spawn_server_without_env_arg_passes_none_to_popen(fake_popen, tmp_path: Path) -> None:
    """No explicit `env=` given -- `Popen` must receive `env=None`, which
    means "inherit this process's environment unchanged" (Python stdlib
    semantics), identical to `spawn_server`'s behavior before `env`
    existed as a parameter."""
    bootstrap.spawn_server(tmp_path, "127.0.0.1", 8000)

    assert len(fake_popen) == 1
    assert fake_popen[0].kwargs["env"] is None


def test_spawn_server_threads_explicit_env_dict_to_popen(fake_popen, tmp_path: Path) -> None:
    custom_env = {"PATH": "/usr/bin", "AI_SDLC_AGENT_FRAMEWORK": "claude"}

    bootstrap.spawn_server(tmp_path, "127.0.0.1", 8000, env=custom_env)

    assert len(fake_popen) == 1
    assert fake_popen[0].kwargs["env"] == custom_env
    assert fake_popen[0].kwargs["env"]["AI_SDLC_AGENT_FRAMEWORK"] == "claude"


def test_spawn_server_command_line_unaffected_by_env(fake_popen, tmp_path: Path) -> None:
    bootstrap.spawn_server(tmp_path, "127.0.0.1", 8123, env={"AI_SDLC_AGENT_FRAMEWORK": "copilot"})

    args = fake_popen[0].args
    assert "ai_sdlc.platform.server" in args
    assert str(tmp_path) in args
    assert "--port" in args and "8123" in args


# -- write_connectors_config -------------------------------------------------------


def test_write_connectors_config_declares_all_known_connectors(tmp_path: Path) -> None:
    path = bootstrap.write_connectors_config(tmp_path, ["jira", "confluence"])

    assert path == str(tmp_path / ".ai-sdlc" / "connectors.json")
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    assert payload["schema_version"] == "connectors-v1"
    names = {c["name"] for c in payload["connectors"]}
    assert names == set(bootstrap.KNOWN_CONNECTOR_NAMES)


def test_write_connectors_config_marks_only_selected_names_enabled(tmp_path: Path) -> None:
    path = bootstrap.write_connectors_config(tmp_path, ["jira", "local_docs"])
    payload = json.loads(Path(path).read_text(encoding="utf-8"))

    enabled = {c["name"] for c in payload["connectors"] if c["enabled"]}
    disabled = {c["name"] for c in payload["connectors"] if not c["enabled"]}
    assert enabled == {"jira", "local_docs"}
    assert disabled == set(bootstrap.KNOWN_CONNECTOR_NAMES) - enabled


def test_write_connectors_config_never_guesses_a_command(tmp_path: Path) -> None:
    path = bootstrap.write_connectors_config(tmp_path, ["jira"])
    payload = json.loads(Path(path).read_text(encoding="utf-8"))

    for connector in payload["connectors"]:
        assert connector["command"] is None
        assert connector["args"] == []
        assert connector["env"] == {}


def test_write_connectors_config_with_empty_selection_still_declares_all_disabled(tmp_path: Path) -> None:
    path = bootstrap.write_connectors_config(tmp_path, [])
    payload = json.loads(Path(path).read_text(encoding="utf-8"))

    assert all(not c["enabled"] for c in payload["connectors"])


def test_write_connectors_config_is_idempotent_and_non_destructive(tmp_path: Path) -> None:
    first_path = bootstrap.write_connectors_config(tmp_path, ["jira"])
    assert first_path is not None

    # Hand-edit the file, as an operator would after filling in a real
    # command -- re-running must leave it alone.
    config_path = Path(first_path)
    hand_edited = json.loads(config_path.read_text(encoding="utf-8"))
    hand_edited["connectors"][0]["command"] = "/usr/local/bin/jira-mcp"
    config_path.write_text(json.dumps(hand_edited), encoding="utf-8")

    second_call = bootstrap.write_connectors_config(tmp_path, ["confluence"])

    assert second_call is None
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["connectors"][0]["command"] == "/usr/local/bin/jira-mcp"
