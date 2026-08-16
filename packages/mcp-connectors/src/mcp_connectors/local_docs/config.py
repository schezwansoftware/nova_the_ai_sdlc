"""Local-directories connector config: which absolute local directories
this connector may read from, what result cap. One JSON file,
`<connectors_config_dir()>/local_docs.json` -- see
`mcp_connectors/common.py`'s module docstring for the config-file
convention this follows.

No credential, no keyring entry, no network config of any kind belongs
in this file -- this connector only ever reads local files already on
disk.

## Provisioning a config file

There's no setup wizard in this pass (matching Jira/Confluence/
SharePoint's same "operator writes the JSON by hand" posture -- see
`jira/config.py`). An operator lists the real, absolute directory paths
they want searchable:

```json
{
  "allowed_directories": ["/Users/alice/notes", "/Users/alice/work-docs"],
  "result_limit": 15
}
```

Each entry is resolved via `Path.resolve(strict=True)` at config-*load*
time (this module's own `field_validator`, below) -- it must already
exist and be a real, readable directory, or config loading fails
immediately with a clear error rather than deferring the mistake to the
first search. This is the config-time half of the precision requirement
-- see `mcp_connectors/local_fs/search.py`'s module docstring for the
query-time half (real-path/symlink-escape verification), which is where
the actual security guarantee lives.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mcp_connectors.common import DEFAULT_RESULT_LIMIT, MAX_RESULT_LIMIT, ConnectorConfigError, connectors_config_dir

CONFIG_FILE_NAME = "local_docs.json"


class LocalDocsConnectorConfig(BaseModel):
    """This connector's full config: the hard allowlist of local
    directories it may ever read from (the config-time half of the
    precision requirement -- see module docstring), and the search
    result cap."""

    model_config = ConfigDict(extra="forbid")

    #: Hard allowlist of local directories this connector may ever read
    #: from. Must be non-empty -- a connector with no configured
    #: directories has nothing it's allowed to search, which is a config
    #: mistake to surface at load time (matches `JiraConnectorConfig
    #: .allowed_projects`'s same posture).
    allowed_directories: List[str] = Field(min_length=1)
    result_limit: int = Field(default=DEFAULT_RESULT_LIMIT, ge=1, le=MAX_RESULT_LIMIT)

    @field_validator("allowed_directories")
    @classmethod
    def _normalize_directories(cls, value: List[str]) -> List[str]:
        """Resolves every entry to its canonical, real, existing
        absolute path (`Path.resolve(strict=True)`, following symlinks)
        -- config validation fails loudly on a typo'd or missing
        directory rather than discovering it later as a confusing "found
        nothing" search result. De-dupes by resolved identity while
        preserving first-seen order (two config entries that happen to
        resolve to the same real directory, e.g. via a symlink, collapse
        to one), mirroring `JiraConnectorConfig._normalize_projects`."""
        seen = set()
        resolved: List[str] = []
        for raw in value:
            raw = str(raw).strip()
            if not raw:
                continue
            candidate = Path(raw).expanduser()
            try:
                real = candidate.resolve(strict=True)
            except OSError as exc:
                raise ValueError(
                    f"configured directory {raw!r} does not exist or is not "
                    f"accessible: {exc}"
                ) from exc
            if not real.is_dir():
                raise ValueError(f"configured directory {raw!r} (resolved to {real}) is not a directory")
            key = str(real)
            if key not in seen:
                seen.add(key)
                resolved.append(key)
        if not resolved:
            raise ValueError("allowed_directories must contain at least one non-empty directory path")
        return resolved


def config_path() -> Path:
    return connectors_config_dir() / CONFIG_FILE_NAME


def load_config() -> LocalDocsConnectorConfig:
    path = config_path()
    if not path.exists():
        raise ConnectorConfigError(
            f"no local-docs connector config found at {path}. Create one first -- "
            "see this module's docstring for the expected JSON shape -- or set "
            "MCP_CONNECTORS_CONFIG_DIR to a directory containing local_docs.json."
        )
    return LocalDocsConnectorConfig.model_validate_json(path.read_text(encoding="utf-8"))


def save_config(config: LocalDocsConnectorConfig) -> None:
    directory = connectors_config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    config_path().write_text(config.model_dump_json(indent=2) + "\n", encoding="utf-8")
