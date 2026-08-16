"""Tests for `ConnectorResolver` -- `.ai-sdlc/connectors.json` -> MCP
server launch specs.

No network access / real MCP connector subprocess required anywhere in
this file.
"""
from __future__ import annotations

import json
from pathlib import Path

from ai_sdlc.capabilities.connector_resolver import (
    ConnectorResolver,
    default_connectors_config_path,
)


def _write_connectors_json(workspace: Path, payload: dict) -> None:
    path = default_connectors_config_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_missing_config_file_resolves_to_nothing_enabled(tmp_path: Path):
    resolver = ConnectorResolver(tmp_path)
    result = resolver.resolve()

    assert result.enabled == []
    assert result.skipped == []


def test_malformed_json_resolves_to_nothing_enabled_not_an_error(tmp_path: Path):
    path = default_connectors_config_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")

    resolver = ConnectorResolver(tmp_path)
    result = resolver.resolve()

    assert result.enabled == []
    assert result.skipped == []


def test_disabled_connector_is_silently_omitted_not_skipped(tmp_path: Path):
    _write_connectors_json(
        tmp_path,
        {
            "schema_version": "connectors-v1",
            "connectors": [
                {"name": "jira", "enabled": False, "command": None, "args": [], "env": {}}
            ],
        },
    )

    result = ConnectorResolver(tmp_path).resolve()

    assert result.enabled == []
    assert result.skipped == []  # not even a skip entry -- disabled is normal, not a gap


def test_enabled_but_unconfigured_connector_is_skipped_not_raised(tmp_path: Path):
    _write_connectors_json(
        tmp_path,
        {
            "schema_version": "connectors-v1",
            "connectors": [
                {"name": "jira", "enabled": True, "command": None, "args": [], "env": {}}
            ],
        },
    )

    result = ConnectorResolver(tmp_path).resolve()

    assert result.enabled == []
    assert result.skipped == [{"name": "jira", "reason": "not_configured"}]


def test_enabled_and_configured_connector_produces_launch_spec(tmp_path: Path):
    _write_connectors_json(
        tmp_path,
        {
            "schema_version": "connectors-v1",
            "connectors": [
                {
                    "name": "jira",
                    "enabled": True,
                    "command": "/usr/local/bin/jira-mcp",
                    "args": [],
                    "env": {"MCP_CONNECTORS_CONFIG_DIR": "/custom/config"},
                }
            ],
        },
    )

    result = ConnectorResolver(tmp_path).resolve()

    assert len(result.enabled) == 1
    spec = result.enabled[0]
    assert spec.name == "jira"
    assert spec.command == "/usr/local/bin/jira-mcp"
    assert spec.env == {"MCP_CONNECTORS_CONFIG_DIR": "/custom/config"}
    assert spec.tool_names == ["search", "fetch"]
    assert result.skipped == []


def test_multiple_connectors_mixed_enabled_disabled_and_unconfigured(tmp_path: Path):
    _write_connectors_json(
        tmp_path,
        {
            "schema_version": "connectors-v1",
            "connectors": [
                {"name": "jira", "enabled": True, "command": "/bin/jira-mcp", "args": [], "env": {}},
                {"name": "confluence", "enabled": False, "command": None, "args": [], "env": {}},
                {"name": "sharepoint", "enabled": True, "command": None, "args": [], "env": {}},
            ],
        },
    )

    result = ConnectorResolver(tmp_path).resolve()

    assert [spec.name for spec in result.enabled] == ["jira"]
    assert result.skipped == [{"name": "sharepoint", "reason": "not_configured"}]


def test_command_with_only_whitespace_is_treated_as_unconfigured(tmp_path: Path):
    _write_connectors_json(
        tmp_path,
        {
            "schema_version": "connectors-v1",
            "connectors": [
                {"name": "jira", "enabled": True, "command": "   ", "args": [], "env": {}}
            ],
        },
    )

    result = ConnectorResolver(tmp_path).resolve()

    assert result.enabled == []
    assert result.skipped == [{"name": "jira", "reason": "not_configured"}]


def test_resolve_rereads_file_on_every_call(tmp_path: Path):
    """Connectors.json is hand-editable between calls -- an operator's
    edit must take effect on the very next question, no restart needed."""
    _write_connectors_json(
        tmp_path,
        {"schema_version": "connectors-v1", "connectors": [
            {"name": "jira", "enabled": True, "command": None, "args": [], "env": {}}
        ]},
    )
    resolver = ConnectorResolver(tmp_path)
    assert resolver.resolve().enabled == []

    _write_connectors_json(
        tmp_path,
        {"schema_version": "connectors-v1", "connectors": [
            {"name": "jira", "enabled": True, "command": "/bin/jira-mcp", "args": [], "env": {}}
        ]},
    )
    result = resolver.resolve()
    assert len(result.enabled) == 1
    assert result.enabled[0].command == "/bin/jira-mcp"
