"""OneDrive connector config: which absolute local OneDrive-sync-folder
path(s) this connector may read from, what result cap. One JSON file,
`<connectors_config_dir()>/onedrive.json` -- see
`mcp_connectors/common.py`'s module docstring for the config-file
convention.

No credential, no keyring entry, no network config -- this connector
never talks to Microsoft Graph or any other cloud API. It only reads
whatever the OneDrive desktop client has already synced to local disk
(see `mcp_connectors/onedrive/__init__.py`'s module docstring for why).

## The sync folder path is explicit required config -- never auto-detected

Real OneDrive local sync folder locations vary too much across OS,
OneDrive client version, and account type to guess reliably:

  - **macOS**: typically under
    `~/Library/CloudStorage/OneDrive-<AccountName>/` for current OneDrive
    versions, or `~/OneDrive` for older ones.
  - **Windows**: typically `%USERPROFILE%\\OneDrive` for a personal
    account, or `%USERPROFILE%\\OneDrive - <Org Name>` for a work/school
    (business) account.
  - Multiple accounts (personal + one or more organizations) can be
    synced side by side, each with its own folder.

So this connector deliberately requires the operator to supply the real,
already-existing path(s) themselves -- exactly like `local_docs`'s
`allowed_directories` (this field is even the same name, since
structurally it's the same thing: a hard allowlist of local directories
this connector may read from). There is no default-path guess anywhere
in this module.

## Provisioning a config file

```json
{
  "allowed_directories": ["/Users/alice/Library/CloudStorage/OneDrive-Contoso"],
  "result_limit": 15
}
```

Each entry is resolved via `Path.resolve(strict=True)` at config-load
time (this module's `field_validator`, below) -- it must already exist
(i.e. the OneDrive client must have already created/synced that folder)
and be a real directory, or config loading fails immediately with a
clear error.

## `file_categories`: an explicit, opt-in permission gate on file type

Same as `local_docs` -- see that module's docstring for the full
rationale, this is the identical mechanism. Defaults to `["text"]`,
backward compatible with any config predating this field:

```json
{
  "allowed_directories": ["/Users/alice/Library/CloudStorage/OneDrive-Contoso"],
  "file_categories": ["text", "office", "pdf"],
  "result_limit": 15
}
```

`"office"`/`"pdf"` need the `documents` extra
(`pip install mcp-connectors[onedrive,documents]`) -- requesting either
without it fails config loading immediately with a clear error. OCR/
image-based text recognition is explicitly out of scope regardless of
what's opted into here -- see `mcp_connectors/local_fs/search.py`'s
module docstring.

## OneDrive Files-On-Demand: read this before assuming every listed file is real

A file that *appears* in this folder isn't guaranteed to have real local
content -- OneDrive's Files-On-Demand feature can leave a file as a
cloud-only placeholder until it's opened once through the OneDrive
client or Explorer/Finder. This connector's `client.py` opts into
best-effort placeholder detection (`detect_cloud_placeholders=True`) --
see `mcp_connectors/local_fs/search.py`'s module docstring for exactly
what is and is not detected. This is a real, honest gap, not a "fully
solved" claim.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mcp_connectors.common import DEFAULT_RESULT_LIMIT, MAX_RESULT_LIMIT, ConnectorConfigError, connectors_config_dir
from mcp_connectors.local_fs.search import missing_libraries_for_categories

CONFIG_FILE_NAME = "onedrive.json"

FileCategory = Literal["text", "code", "office", "pdf"]


class OneDriveConnectorConfig(BaseModel):
    """This connector's full config: the hard allowlist of local
    OneDrive-sync-folder path(s) it may ever read from (the config-time
    half of the precision requirement -- see module docstring), which
    categories of file it may read (the config-time "permission" gate --
    see module docstring's `file_categories` section), and the search
    result cap. Structurally identical to `LocalDocsConnectorConfig` --
    see this module's docstring for why that's expected, not an
    accident."""

    model_config = ConfigDict(extra="forbid")

    #: Hard allowlist of local OneDrive sync folder(s) this connector may
    #: ever read from. Must be non-empty. Never auto-detected -- see
    #: module docstring.
    allowed_directories: List[str] = Field(min_length=1)
    #: Which categories of file this connector may read -- see module
    #: docstring. Defaults to `["text"]` only, matching this connector's
    #: original V1 behavior exactly (backward compatible, not a breaking
    #: schema change).
    file_categories: List[FileCategory] = Field(default_factory=lambda: ["text"])
    result_limit: int = Field(default=DEFAULT_RESULT_LIMIT, ge=1, le=MAX_RESULT_LIMIT)

    @field_validator("allowed_directories")
    @classmethod
    def _normalize_directories(cls, value: List[str]) -> List[str]:
        """Identical normalization to `LocalDocsConnectorConfig
        ._normalize_directories` -- see that module for the full
        rationale. Duplicated rather than shared as a mixin/base class:
        both are short, and each connector's config model owning its own
        validator keeps every connector genuinely self-contained (no
        connector needs to import another connector's config module),
        matching this package's established "each connector only imports
        `common`/its shared support module, never a sibling connector"
        convention (see `mcp_connectors/__init__.py`)."""
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
                    f"configured OneDrive directory {raw!r} does not exist or is "
                    f"not accessible: {exc}. Make sure the OneDrive desktop client "
                    "has actually synced this folder before configuring it here -- "
                    "this connector never auto-detects or creates it."
                ) from exc
            if not real.is_dir():
                raise ValueError(f"configured OneDrive directory {raw!r} (resolved to {real}) is not a directory")
            key = str(real)
            if key not in seen:
                seen.add(key)
                resolved.append(key)
        if not resolved:
            raise ValueError("allowed_directories must contain at least one non-empty directory path")
        return resolved

    @field_validator("file_categories")
    @classmethod
    def _validate_file_categories(cls, value: List[str]) -> List[str]:
        """Identical to `LocalDocsConnectorConfig._validate_file_categories`
        -- see that module for the full rationale, duplicated here for the
        same "each connector's config model is self-contained" reason
        `_normalize_directories` above already explains."""
        seen = set()
        deduped: List[str] = []
        for item in value:
            if item not in seen:
                seen.add(item)
                deduped.append(item)
        if not deduped:
            raise ValueError("file_categories must contain at least one category (the default is ['text'])")
        missing = missing_libraries_for_categories(deduped)
        if missing:
            raise ValueError(
                f"file_categories {deduped!r} requests a category whose parsing "
                f"library isn't installed in this environment: {missing!r}. Install "
                "the `documents` extra to enable 'office'/'pdf' support: "
                "pip install mcp-connectors[onedrive,documents]"
            )
        return deduped


def config_path() -> Path:
    return connectors_config_dir() / CONFIG_FILE_NAME


def load_config() -> OneDriveConnectorConfig:
    path = config_path()
    if not path.exists():
        raise ConnectorConfigError(
            f"no OneDrive connector config found at {path}. Create one first -- "
            "see this module's docstring for the expected JSON shape and how to "
            "find your real local OneDrive sync folder path -- or set "
            "MCP_CONNECTORS_CONFIG_DIR to a directory containing onedrive.json."
        )
    return OneDriveConnectorConfig.model_validate_json(path.read_text(encoding="utf-8"))


def save_config(config: OneDriveConnectorConfig) -> None:
    directory = connectors_config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    config_path().write_text(config.model_dump_json(indent=2) + "\n", encoding="utf-8")
