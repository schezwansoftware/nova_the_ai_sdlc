"""Tests for `ai_sdlc.cli.bootstrap.spawn_server`'s `env` threading.

`subprocess.Popen` is monkeypatched rather than actually spawning a
process -- these tests only need to prove *what gets passed* to `Popen`,
not that the Core Platform API subprocess actually starts (that end-to-end
behavior is already covered by `test_cli_contract.py`'s
`test_init_start_server_spawns_and_waits_for_reachability`, which spawns a
real subprocess).

No network access / external credentials required.
"""
from __future__ import annotations

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
