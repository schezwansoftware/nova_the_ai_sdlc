"""Standalone OneDrive MCP server -- the `onedrive-mcp` console script's
entry point.

Built on the official `mcp` Python SDK's `FastMCP` server, stdio
transport -- same construction as every other connector in this package
(see `jira/mcp_server.py`'s module docstring). Only imports `mcp` and
`mcp_connectors.{common,local_fs,onedrive}` -- no Microsoft Graph SDK, no
`keyring`, no OAuth library of any kind, since this connector reads the
OneDrive desktop client's already-synced local folder directly rather
than calling any cloud API (see `mcp_connectors/onedrive/__init__.py`'s
module docstring).

Exceptions raised inside `search`/`fetch` below are intentionally left
to propagate out of the tool functions uncaught -- FastMCP already
converts them into a real `isError` MCP tool response; see
`mcp_connectors/common.py`'s module docstring for why no bespoke
translation layer is needed here.
"""
from __future__ import annotations

import sys
from typing import List, Optional

from mcp.server.fastmcp import FastMCP

from mcp_connectors.common import ConnectorError, Document
from mcp_connectors.onedrive.client import OneDriveClient
from mcp_connectors.onedrive.config import OneDriveConnectorConfig, load_config

SERVER_NAME = "onedrive-mcp"


def build_server(config: OneDriveConnectorConfig, *, client: Optional[OneDriveClient] = None) -> FastMCP:
    """Construct the `FastMCP` server for a given, already-loaded config.
    Separated from `main()` so tests can build and drive a real server
    against an injected fake client without touching the real filesystem
    or `sys.argv`/stdio at all (mirrors `jira/mcp_server.py::build_server`)."""
    onedrive_client = client if client is not None else OneDriveClient(config)

    server = FastMCP(
        SERVER_NAME,
        instructions=(
            "Search and fetch plain-text files (.md/.markdown/.txt/.rst only -- "
            "PDF/Word/Excel/image formats are a deliberately deferred V1 gap, "
            "see todo.md) from this connector's configured local OneDrive sync "
            f"folder allowlist: {config.allowed_directories}. Reads the OneDrive "
            "desktop client's already-synced local folder directly -- no "
            "Microsoft Graph API call, no OAuth. Every search/fetch resolves the "
            "real, symlink-followed path of each file and verifies it is actually "
            "inside one of these directories before returning its content -- "
            "naming a directory outside this list, or a path-traversal/"
            "symlink-escape id, is refused. Cloud-only Files-On-Demand "
            "placeholders (not yet downloaded locally) are skipped in search "
            "results and refused on fetch with a clear error, on a best-effort "
            "basis -- see local_fs/search.py's module docstring for exactly what "
            "is and isn't detected."
        ),
    )

    @server.tool()
    def search(query: str, directories: Optional[List[str]] = None) -> List[Document]:
        """Search plain-text files under this connector's allowlisted
        local OneDrive sync folder(s). `directories` optionally narrows
        the search to a subset of this connector's allowlisted
        directories (default: every allowlisted directory). Naming a
        directory outside the connector's configured allowlist is
        refused."""
        return onedrive_client.search(query, directories=directories)

    @server.tool()
    def fetch(id: str) -> Document:
        """Fetch a single file by id (an absolute path string, normally
        a prior `search` result's id). Refuses to return a file outside
        this connector's configured directory allowlist (including via a
        path-traversal id or symlink escape), and refuses a cloud-only
        Files-On-Demand placeholder that hasn't been downloaded locally
        yet."""
        return onedrive_client.fetch(id)

    return server


def main() -> None:
    try:
        config = load_config()
    except ConnectorError as exc:
        print(f"{SERVER_NAME}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    server = build_server(config)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
