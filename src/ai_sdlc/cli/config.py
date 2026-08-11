"""Local CLI configuration: workspace path, server host/port, current
workflow/initiator id. Lives in the CLI's own config directory, never in
the target workspace's `.ai-sdlc/` (that directory belongs to Core/Orion's
workflow state, which the CLI never reads or writes directly)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


class CLIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace: str
    host: str = "127.0.0.1"
    port: int = 8000
    initiator_id: str
    current_workflow_id: Optional[str] = None
    #: Which interchangeable AI agent framework this workspace has chosen
    #: to use for the capabilities that are actually built as
    #: swappable-provider integrations (`CodingCapability`,
    #: `RetrievalCapability` -- see
    #: `capabilities/providers/coding_factory.py`/`retrieval_factory.py`).
    #: `None` means "not yet chosen" -- a real, expected state before
    #: `ai-sdlc init` has resolved it for the first time -- never a silent
    #: third default; `handlers.run_init` resolves this to a concrete
    #: value (via `--agent-framework`, a stored prior value, or an
    #: interactive prompt) before saving. Deliberately excludes the plain
    #: single-call "think and answer" reasoning step (PO/Architecture/UX's
    #: `ReasoningCapability`) -- Copilot has no plain-completion API
    #: equivalent, so that stays Claude-only regardless of this setting.
    agent_framework: Optional[Literal["claude", "copilot"]] = None


def config_dir() -> Path:
    override = os.environ.get("AI_SDLC_CLI_CONFIG_DIR")
    if override:
        return Path(override)
    return Path.home() / ".config" / "ai-sdlc-cli"


def config_path() -> Path:
    return config_dir() / "config.json"


def load_config() -> Optional[CLIConfig]:
    path = config_path()
    if not path.exists():
        return None
    return CLIConfig.model_validate_json(path.read_text(encoding="utf-8"))


def save_config(config: CLIConfig) -> None:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    config_path().write_text(config.model_dump_json(indent=2) + "\n", encoding="utf-8")
