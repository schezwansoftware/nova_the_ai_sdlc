"""Tests for `CLIConfig`, in particular the `agent_framework` field
(the persisted "which AI agent framework" choice `ai-sdlc init` resolves
and `handlers.run_init` threads into the spawned server subprocess's
environment).

No network access / external credentials required.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from ai_sdlc.cli.config import CLIConfig, config_path, load_config, save_config


def _base_kwargs(**overrides):
    kwargs = dict(workspace="/tmp/some-workspace", initiator_id="u1")
    kwargs.update(overrides)
    return kwargs


def test_agent_framework_defaults_to_none():
    config = CLIConfig(**_base_kwargs())
    assert config.agent_framework is None


@pytest.mark.parametrize("value", ["claude", "copilot"])
def test_agent_framework_accepts_valid_values(value):
    config = CLIConfig(**_base_kwargs(agent_framework=value))
    assert config.agent_framework == value


def test_agent_framework_rejects_unrecognized_value():
    with pytest.raises(ValidationError):
        CLIConfig(**_base_kwargs(agent_framework="openai"))


def test_agent_framework_none_round_trips_through_save_and_load(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_SDLC_CLI_CONFIG_DIR", str(tmp_path))
    config = CLIConfig(**_base_kwargs())
    save_config(config)

    loaded = load_config()
    assert loaded is not None
    assert loaded.agent_framework is None


@pytest.mark.parametrize("value", ["claude", "copilot"])
def test_agent_framework_value_round_trips_through_save_and_load(tmp_path, monkeypatch, value):
    monkeypatch.setenv("AI_SDLC_CLI_CONFIG_DIR", str(tmp_path))
    config = CLIConfig(**_base_kwargs(agent_framework=value))
    save_config(config)

    loaded = load_config()
    assert loaded is not None
    assert loaded.agent_framework == value

    on_disk = json.loads(config_path().read_text(encoding="utf-8"))
    assert on_disk["agent_framework"] == value
