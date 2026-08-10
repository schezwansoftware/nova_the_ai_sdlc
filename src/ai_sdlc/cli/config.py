"""Local CLI configuration: workspace path, server host/port, current
workflow/initiator id. Lives in the CLI's own config directory, never in
the target workspace's `.ai-sdlc/` (that directory belongs to Core/Orion's
workflow state, which the CLI never reads or writes directly)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict


class CLIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace: str
    host: str = "127.0.0.1"
    port: int = 8000
    initiator_id: str
    current_workflow_id: Optional[str] = None


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
