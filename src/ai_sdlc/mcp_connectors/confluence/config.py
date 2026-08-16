"""Confluence connector config: which Confluence site, which space
allowlist, what result cap. One JSON file,
`<connectors_config_dir()>/confluence.json` -- see
`mcp_connectors/common.py`'s module docstring for the config-file
convention this follows, and `jira/config.py`'s module docstring for the
sibling connector's identical shape/rationale (this file mirrors it
field-for-field, swapping "project" for "space").

## Provisioning a config file

```json
{
  "site": {
    "base_url": "https://yourorg.atlassian.net/wiki",
    "deployment_type": "cloud",
    "auth_method": "cloud_api_token",
    "account_identifier": "bot@yourorg.com",
    "credential": {"service": "ai-sdlc-mcp-confluence", "username": "bot@yourorg.com"}
  },
  "allowed_spaces": ["ENG", "PLAT"],
  "result_limit": 15
}
```

Note `base_url` includes the `/wiki` context path for Cloud (Confluence
Cloud's REST API lives under `<site>/wiki/rest/api/...`) -- Data Center
sites typically don't have that segment (or use a different one,
admin-configured). This connector deliberately never hardcodes or
strips a `/wiki` segment itself (see `client.py`'s module docstring) --
`base_url` is exactly what gets `/rest/api/search` appended to it, so
get the context path right here.

with the matching credential stored once via:

```
python -c "from ai_sdlc.mcp_connectors.common import store_secret; \\
  store_secret('ai-sdlc-mcp-confluence', 'bot@yourorg.com', '<the real API token>')"
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

CONFIG_FILE_NAME = "confluence.json"


class ConfluenceConnectorConfig(BaseModel):
    """This connector's full config: the Confluence site to talk to, the
    space-key allowlist (the config-time half of the precision
    requirement -- see `mcp_connectors/__init__.py`), and the search
    result cap."""

    model_config = ConfigDict(extra="forbid")

    site: AtlassianSiteConfig
    #: Hard allowlist of Confluence space keys this connector may ever
    #: touch. Must be non-empty -- see `jira/config.py`'s identical
    #: reasoning for `allowed_projects`.
    allowed_spaces: List[str] = Field(min_length=1)
    result_limit: int = Field(default=DEFAULT_RESULT_LIMIT, ge=1, le=MAX_RESULT_LIMIT)

    @field_validator("allowed_spaces")
    @classmethod
    def _normalize_spaces(cls, value: List[str]) -> List[str]:
        normalized = [item.strip().upper() for item in value if item.strip()]
        if not normalized:
            raise ValueError("allowed_spaces must contain at least one non-empty space key")
        seen = set()
        deduped = []
        for key in normalized:
            if key not in seen:
                seen.add(key)
                deduped.append(key)
        return deduped


def config_path() -> Path:
    return connectors_config_dir() / CONFIG_FILE_NAME


def load_config() -> ConfluenceConnectorConfig:
    path = config_path()
    if not path.exists():
        raise ConnectorConfigError(
            f"no Confluence connector config found at {path}. Create one first -- "
            "see this module's docstring for the expected JSON shape -- or set "
            "AI_SDLC_MCP_CONFIG_DIR to a directory containing confluence.json."
        )
    return ConfluenceConnectorConfig.model_validate_json(path.read_text(encoding="utf-8"))


def save_config(config: ConfluenceConnectorConfig) -> None:
    directory = connectors_config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    config_path().write_text(config.model_dump_json(indent=2) + "\n", encoding="utf-8")
