"""Local workspace bootstrap for `ai-sdlc init`.

Two independent concerns live here:

1. `write_agent_metadata` -- scaffolds `<workspace>/.ai-sdlc/agents/*.json`
   registry metadata for the shipped specialist agents. `AgentRegistry`
   (`ai_sdlc.agents.registry`) discovers agents *purely* by reading these
   files from the workspace at `Orchestrator.__init__` time -- there is no
   HTTP endpoint for agent registration, and without this scaffolding no
   workflow can ever find an agent to run. This is static bootstrap
   configuration for the target workspace, not workflow *state*; the CLI
   never reads it back, and it doesn't shortcut any orchestration decision
   the way reading `.ai-sdlc/workflow.json` directly would.
2. `spawn_server` -- starts the standalone Core Platform API process via
   `python -m ai_sdlc.platform.server`, i.e. shelling out to the same
   public entrypoint documented in that module's own `__main__` block. The
   CLI process itself never imports `ai_sdlc.platform.server` or anything
   under `ai_sdlc.orchestration`/`ai_sdlc.agents`.
"""
from __future__ import annotations

import getpass
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from ai_sdlc.cli.client import PlatformClient

AGENT_METADATA: List[dict] = [
    {
        "agent_id": "po",
        "version": "1.0",
        "impl": "ai_sdlc.agents.po.po_agent.POAgent",
        "input_schema": "po-input-v1",
        "output_schema": "po-output-v1",
        "capabilities": ["reasoning"],
        "state_artifact": "requirements.json",
    },
    {
        "agent_id": "architecture",
        "version": "1.0",
        "impl": "ai_sdlc.agents.architecture.architecture_agent.ArchitectureAgent",
        "input_schema": "architecture-input-v1",
        "output_schema": "architecture-output-v1",
        "capabilities": ["reasoning"],
        "state_artifact": "architecture.json",
    },
    {
        "agent_id": "ux",
        "version": "1.0",
        "impl": "ai_sdlc.agents.ux.ux_agent.UXAgent",
        "input_schema": "ux-input-v1",
        "output_schema": "ux-output-v1",
        "capabilities": ["reasoning", "design"],
        "state_artifact": "ux.json",
    },
    {
        "agent_id": "developer",
        "version": "1.0",
        "impl": "ai_sdlc.agents.developer.developer_agent.DeveloperAgent",
        "input_schema": "developer-input-v1",
        "output_schema": "developer-output-v1",
        "capabilities": ["coding"],
        "state_artifact": "implementation.json",
    },
]


def default_initiator_id() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    return user or "ai-sdlc-user"


def write_agent_metadata(workspace: Path) -> List[str]:
    """Write any missing `.ai-sdlc/agents/*.json` metadata file for the
    shipped agents. Idempotent and non-destructive: an existing file (hand
    edited, or pointing at a custom agent implementation) is left alone."""
    agents_dir = workspace / ".ai-sdlc" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    written: List[str] = []
    for metadata in AGENT_METADATA:
        path = agents_dir / f"{metadata['agent_id']}.json"
        if path.exists():
            continue
        path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        written.append(str(path))
    return written


def spawn_server(
    workspace: Path, host: str, port: int, env: Optional[Dict[str, str]] = None
) -> subprocess.Popen:
    """Start the Core Platform API subprocess. `env`, when given, replaces
    (not merges with) the child's environment -- callers wanting to add to
    the current process's own environment (e.g. threading
    `AI_SDLC_AGENT_FRAMEWORK` through from the stored `CLIConfig`, see
    `handlers.run_init`) must pass an explicit `{**os.environ, ...}` dict
    themselves. `None` (the default) means "inherit this process's
    environment unchanged", identical to `subprocess.Popen`'s own default
    and to this function's behavior before `env` existed -- so every
    existing caller that doesn't pass it is unaffected."""
    return subprocess.Popen(
        [sys.executable, "-m", "ai_sdlc.platform.server", str(workspace), "--host", host, "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )


def wait_until_reachable(client: PlatformClient, attempts: int = 40, delay: float = 0.25) -> bool:
    for _ in range(attempts):
        if client.is_reachable():
            return True
        time.sleep(delay)
    return False
