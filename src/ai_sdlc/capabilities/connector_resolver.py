"""Framework-agnostic resolver: `.ai-sdlc/connectors.json` -> MCP server
launch specs.

Per the locked Sage Phase 2 design (`todo.md`), tool wiring must be
framework-agnostic: this module turns "this workspace's enabled
connectors" into plain-data launch specs, and each agent-framework
provider (`providers/sage_claude.py`, `providers/sage_copilot.py`, any
future framework) does only a thin last-step translation into its own
SDK's native tool-config mechanism. This module never imports
`claude-agent-sdk`/`github-copilot-sdk` -- it stays importable with zero
optional extras installed, and never re-solves "which connectors, what
config" per framework the way each provider previously would have had to.

## What a caller needs to launch a connector

Every one of the 5 shipped connectors (`packages/mcp-connectors/`) is a
**stdio subprocess** with exactly 2 tools (`search`/`fetch`) -- there is
no HTTP/socket transport (see that package's `INSTALL.md` §3a, a real,
tested example of launching `jira-mcp` via `mcp.StdioServerParameters`).
Launching one needs: a command (the absolute path to that connector's
installed console script -- `jira-mcp`/`confluence-mcp`/`sharepoint-mcp`/
`local-docs-mcp`/`onedrive-mcp`), optional args, and optional env (e.g.
`MCP_CONNECTORS_CONFIG_DIR`, if that connector's config file isn't at the
default `~/.config/mcp-connectors/`).

## `command` is never auto-derived

`packages/mcp-connectors` is a fully independent sibling package (its own
`pyproject.toml`, own venv, zero dependency on Nova) -- Nova has no
reliable way to know at runtime where an operator installed it. A
connector's `command` is therefore always exactly what
`.ai-sdlc/connectors.json` says, hand-filled by the operator (or left
`null`, in which case this resolver treats it as unconfigured and skips
it quietly -- see `resolve()`). This mirrors the locked design's
"declare now, configure later" posture, the same two-step model the
connectors' own credential storage already uses.

## Skip, don't raise

A connector enabled but not properly configured (`command` missing/
blank) is **skipped quietly**, never a hard error -- per the locked
design. This module has zero logging/audit side effects of its own
(callers -- the two Sage providers, or whatever invokes them -- are
responsible for turning `ConnectorResolutionResult.skipped` into audit
events, e.g. `orchestration/orchestrator.py`'s `connector_skipped`
event), keeping this module pure data/one config read, no I/O side
effects beyond that read.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Union

from pydantic import BaseModel, Field

#: `.ai-sdlc/connectors.json`'s current schema version. Bumped if the
#: format ever changes incompatibly; unread today (no migration logic
#: exists yet), but present from the start so a future migration has
#: something to key off of.
SCHEMA_VERSION = "connectors-v1"

#: The 5 connector names this resolver recognizes -- must match
#: `packages/mcp-connectors`' own connector directory names exactly
#: (`jira`, `confluence`, `sharepoint`, `local_docs`, `onedrive`), since
#: these become the `mcp__<name>__<tool>` tool-name prefix on the Claude
#: side (see `providers/sage_claude.py`).
KNOWN_CONNECTOR_NAMES = ("jira", "confluence", "sharepoint", "local_docs", "onedrive")

#: Every connector's fixed tool surface (`packages/mcp-connectors`'
#: `INSTALL.md`: "2 tools" per connector, `search`/`fetch`, for all 5).
DEFAULT_TOOL_NAMES = ["search", "fetch"]

_CONNECTORS_CONFIG_RELATIVE_PATH = Path(".ai-sdlc") / "connectors.json"


class ConnectorLaunchSpec(BaseModel):
    """Plain-data launch spec for one enabled, configured connector."""

    name: str
    command: str
    args: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)
    tool_names: List[str] = Field(default_factory=lambda: list(DEFAULT_TOOL_NAMES))


class ConnectorResolutionResult(BaseModel):
    """Result of resolving `.ai-sdlc/connectors.json` for one workspace."""

    enabled: List[ConnectorLaunchSpec] = Field(default_factory=list)
    #: One entry per connector that was `enabled: true` but could not be
    #: launched (e.g. `{"name": "jira", "reason": "not_configured"}`).
    #: Never raised -- see module docstring's "Skip, don't raise" section.
    skipped: List[Dict[str, str]] = Field(default_factory=list)


def default_connectors_config_path(workspace_path: Union[str, Path]) -> Path:
    return Path(workspace_path) / _CONNECTORS_CONFIG_RELATIVE_PATH


class ConnectorResolver:
    """Reads `<workspace>/.ai-sdlc/connectors.json` and resolves it into
    launch specs. Stateless beyond the workspace path -- safe to
    construct once per `SageCapability` provider and call `resolve()`
    fresh on every `ask()` (connectors.json is hand-editable between
    calls; re-reading it each time means an operator's edit takes effect
    on the very next question, no restart needed)."""

    def __init__(self, workspace_path: Union[str, Path]) -> None:
        self.workspace_path = Path(workspace_path)
        self.config_path = default_connectors_config_path(self.workspace_path)

    def resolve(self) -> ConnectorResolutionResult:
        if not self.config_path.exists():
            return ConnectorResolutionResult()

        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # A malformed/unreadable connectors.json is the same as "no
            # connectors configured" from Sage's perspective -- never a
            # hard error that would fail a worker's whole invocation over
            # a config-file typo. Nothing to report per-connector here
            # since the file itself couldn't be parsed at all.
            return ConnectorResolutionResult()

        entries = raw.get("connectors") if isinstance(raw, dict) else None
        if not isinstance(entries, list):
            return ConnectorResolutionResult()

        enabled: List[ConnectorLaunchSpec] = []
        skipped: List[Dict[str, str]] = []

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            if not name:
                continue
            if not entry.get("enabled"):
                continue

            command = str(entry.get("command") or "").strip()
            if not command:
                skipped.append({"name": name, "reason": "not_configured"})
                continue

            args = entry.get("args")
            env = entry.get("env")
            tool_names = entry.get("tool_names")
            enabled.append(
                ConnectorLaunchSpec(
                    name=name,
                    command=command,
                    args=list(args) if isinstance(args, list) else [],
                    env=dict(env) if isinstance(env, dict) else {},
                    tool_names=list(tool_names) if isinstance(tool_names, list) else list(DEFAULT_TOOL_NAMES),
                )
            )

        return ConnectorResolutionResult(enabled=enabled, skipped=skipped)
