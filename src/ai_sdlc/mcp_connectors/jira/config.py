"""Jira connector config: which Jira site, which project allowlist, what
result cap. One JSON file, `<connectors_config_dir()>/jira.json` -- see
`mcp_connectors/common.py`'s module docstring for the config-file
convention this follows and why it's a separate file/namespace from
`cli/config.py`'s `CLIConfig`.

## Provisioning a config file

There's no setup wizard in this pass (out of scope -- see `todo.md`).
An operator writes the JSON directly (after storing the real credential
in the OS keyring via `mcp_connectors.common.store_secret` -- never in
this file) and either places it at the default path or points
`AI_SDLC_MCP_CONFIG_DIR` at a directory containing it, e.g.:

```json
{
  "site": {
    "base_url": "https://yourorg.atlassian.net",
    "deployment_type": "cloud",
    "auth_method": "cloud_api_token",
    "account_identifier": "bot@yourorg.com",
    "credential": {"service": "ai-sdlc-mcp-jira", "username": "bot@yourorg.com"}
  },
  "allowed_projects": ["ENG", "PLAT"],
  "result_limit": 15
}
```

with the matching credential stored once via:

```
python -c "from ai_sdlc.mcp_connectors.common import store_secret; \\
  store_secret('ai-sdlc-mcp-jira', 'bot@yourorg.com', '<the real API token>')"
```
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_sdlc.mcp_connectors.atlassian.auth import AtlassianSiteConfig
from ai_sdlc.mcp_connectors.common import (
    DEFAULT_RESULT_LIMIT,
    MAX_RESULT_LIMIT,
    ConnectorConfigError,
    connectors_config_dir,
)

CONFIG_FILE_NAME = "jira.json"


class JiraConnectorConfig(BaseModel):
    """This connector's full config: the Jira site to talk to, the
    project-key allowlist (the config-time half of the precision
    requirement -- see `mcp_connectors/__init__.py`), and the search
    result cap."""

    model_config = ConfigDict(extra="forbid")

    site: AtlassianSiteConfig
    #: Hard allowlist of Jira project keys this connector may ever touch.
    #: Must be non-empty -- a connector with no configured projects has
    #: nothing it's allowed to search, which is a config mistake to
    #: surface at load time, not a valid "search nothing" configuration.
    allowed_projects: List[str] = Field(min_length=1)
    result_limit: int = Field(default=DEFAULT_RESULT_LIMIT, ge=1, le=MAX_RESULT_LIMIT)

    @field_validator("allowed_projects")
    @classmethod
    def _normalize_projects(cls, value: List[str]) -> List[str]:
        normalized = [item.strip().upper() for item in value if item.strip()]
        if not normalized:
            raise ValueError("allowed_projects must contain at least one non-empty project key")
        # De-dupe while preserving first-seen order, rather than silently
        # allowing a config to list the same project twice.
        seen = set()
        deduped = []
        for key in normalized:
            if key not in seen:
                seen.add(key)
                deduped.append(key)
        return deduped


def config_path() -> Path:
    return connectors_config_dir() / CONFIG_FILE_NAME


def load_config() -> JiraConnectorConfig:
    path = config_path()
    if not path.exists():
        raise ConnectorConfigError(
            f"no Jira connector config found at {path}. Create one first -- see "
            "this module's docstring for the expected JSON shape -- or set "
            "AI_SDLC_MCP_CONFIG_DIR to a directory containing jira.json."
        )
    return JiraConnectorConfig.model_validate_json(path.read_text(encoding="utf-8"))


def save_config(config: JiraConnectorConfig) -> None:
    directory = connectors_config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    config_path().write_text(config.model_dump_json(indent=2) + "\n", encoding="utf-8")
