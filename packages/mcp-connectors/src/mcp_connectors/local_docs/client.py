"""Local-directories search/fetch client: a thin wrapper over
`mcp_connectors.local_fs.search`'s shared walk/search/path-safety logic
-- this module owns none of that logic itself (see that module's
docstring), only this connector's own scope (`source="local_docs"`,
`config.allowed_directories`, `config.file_categories`) and the
query-time allowlist check.

`detect_cloud_placeholders=False` throughout: this connector reads plain
local directories, not a OneDrive sync folder, so a zero-byte `.txt`/
`.md` file is just a normal empty file, not a suspected cloud-only
placeholder -- see `local_fs/search.py`'s module docstring for why that
detection is opt-in per connector rather than always-on. Compare
`onedrive/client.py`, which is otherwise structurally identical but
passes `detect_cloud_placeholders=True`.

`file_categories` is passed straight through from config on every call
-- this client does no category logic of its own (extension resolution,
the office/pdf library-availability check) beyond what
`local_fs/search.py` and `LocalDocsConnectorConfig`'s own field
validator already do.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from mcp_connectors.common import Document, enforce_allowlist
from mcp_connectors.local_docs.config import LocalDocsConnectorConfig
from mcp_connectors.local_fs.search import fetch_local_file, search_local_files

SOURCE = "local_docs"


class LocalDocsClient:
    """Search/fetch against one connector's allowlisted local
    directories. No HTTP client, no auth, no test seam needed for either
    -- unlike Jira/Confluence/SharePoint, there's no network boundary to
    inject a fake transport for; tests exercise this against real
    `tmp_path` directories instead (see `tests/test_local_docs.py`)."""

    def __init__(self, config: LocalDocsConnectorConfig) -> None:
        self._config = config

    def search(self, query: str, directories: Optional[List[str]] = None) -> List[Document]:
        """Search allowlisted directories by free text. `directories`
        optionally narrows the search to a subset of this connector's
        allowlisted directories (default: every allowlisted directory,
        exactly like `JiraClient.search()`'s `projects` param). Naming a
        directory outside the connector's configured allowlist is
        refused via `enforce_allowlist` -- never silently widened."""
        allowed = enforce_allowlist(directories or [], self._config.allowed_directories, kind="directory")
        allowed_paths = [Path(item) for item in allowed]
        return search_local_files(
            query,
            allowed_paths,
            source=SOURCE,
            limit=self._config.result_limit,
            detect_cloud_placeholders=False,
            file_categories=self._config.file_categories,
        )

    def fetch(self, file_id: str) -> Document:
        """Fetch one file by id (an absolute path string, normally a
        prior `search()` result's `Document.id`). Path-safety (real-path
        resolution, symlink-escape and path-traversal rejection) is
        enforced by `fetch_local_file` -- see `local_fs/search.py`."""
        allowed_paths = [Path(item) for item in self._config.allowed_directories]
        return fetch_local_file(
            file_id,
            allowed_paths,
            source=SOURCE,
            detect_cloud_placeholders=False,
            file_categories=self._config.file_categories,
        )
