"""OneDrive search/fetch client: a thin wrapper over
`mcp_connectors.local_fs.search`'s shared walk/search/path-safety logic
-- structurally identical to `local_docs/client.py`, with two
differences: `source="onedrive"`, and `detect_cloud_placeholders=True`
(OneDrive's Files-On-Demand feature can leave cloud-only placeholder
files in the synced folder -- see `local_fs/search.py`'s module
docstring for exactly what detection this opts into and its documented
limits).

This module owns none of the actual search/path-safety logic itself --
see `local_fs/search.py` -- only this connector's own scope and the
query-time allowlist check.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from mcp_connectors.common import Document, enforce_allowlist
from mcp_connectors.local_fs.search import fetch_local_file, search_local_files
from mcp_connectors.onedrive.config import OneDriveConnectorConfig

SOURCE = "onedrive"


class OneDriveClient:
    """Search/fetch against one connector's allowlisted local OneDrive
    sync folder(s). No HTTP client, no auth, no Graph API call anywhere
    in this class -- see `mcp_connectors/onedrive/__init__.py`'s module
    docstring for why. Tests exercise this against real `tmp_path`
    directories standing in for a synced OneDrive folder (see
    `tests/test_onedrive.py`)."""

    def __init__(self, config: OneDriveConnectorConfig) -> None:
        self._config = config

    def search(self, query: str, directories: Optional[List[str]] = None) -> List[Document]:
        """Search allowlisted OneDrive sync folder(s) by free text.
        `directories` optionally narrows the search to a subset of this
        connector's allowlisted directories (default: all of them).
        Naming a directory outside the configured allowlist is refused
        via `enforce_allowlist`. Cloud-only Files-On-Demand placeholders
        encountered during the walk are skipped, not matched/crashed on
        -- see `local_fs/search.py`'s module docstring."""
        allowed = enforce_allowlist(directories or [], self._config.allowed_directories, kind="directory")
        allowed_paths = [Path(item) for item in allowed]
        return search_local_files(
            query,
            allowed_paths,
            source=SOURCE,
            limit=self._config.result_limit,
            detect_cloud_placeholders=True,
        )

    def fetch(self, file_id: str) -> Document:
        """Fetch one file by id (an absolute path string, normally a
        prior `search()` result's `Document.id`). Path-safety is
        enforced by `fetch_local_file` (`local_fs/search.py`); a
        cloud-only placeholder id raises a clear `ConnectorAPIError`
        explaining it hasn't been downloaded locally yet, rather than
        returning empty/garbage content."""
        allowed_paths = [Path(item) for item in self._config.allowed_directories]
        return fetch_local_file(file_id, allowed_paths, source=SOURCE, detect_cloud_placeholders=True)
