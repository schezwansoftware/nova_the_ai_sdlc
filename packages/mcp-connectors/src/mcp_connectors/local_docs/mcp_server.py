"""Standalone local-directories MCP server -- the `local-docs-mcp`
console script's entry point.

Built on the official `mcp` Python SDK's `FastMCP` server, stdio
transport -- same construction as every other connector in this package
(see `jira/mcp_server.py`'s module docstring for the verified-against-
the-installed-package rationale, which applies identically here).

Only imports `mcp` and `mcp_connectors.{common,local_fs,local_docs}`, plus
-- only if this connector's config opts into the `"office"`/`"pdf"`
`file_categories` -- the `documents` extra's parsing libraries (deferred-
imported by `local_fs/search.py`, not here) -- no HTTP client, no
`keyring`, no other project's code -- this connector needs strictly less
than Jira/Confluence/SharePoint since it has no credential or network
boundary at all.

Exceptions raised inside `search`/`fetch` below (typically
`ConnectorConfigError`/`ConnectorAPIError` from `common.py`/
`local_fs/search.py`, but any exception works) are intentionally left to
propagate out of the tool functions uncaught -- FastMCP already converts
them into a real `isError` MCP tool response; see
`mcp_connectors/common.py`'s module docstring for why no bespoke
translation layer is needed here.
"""
from __future__ import annotations

import sys
from typing import List, Optional

from mcp.server.fastmcp import FastMCP

from mcp_connectors.common import ConnectorError, Document
from mcp_connectors.local_docs.client import LocalDocsClient
from mcp_connectors.local_docs.config import LocalDocsConnectorConfig, load_config

SERVER_NAME = "local-docs-mcp"


def build_server(config: LocalDocsConnectorConfig, *, client: Optional[LocalDocsClient] = None) -> FastMCP:
    """Construct the `FastMCP` server for a given, already-loaded config.
    Separated from `main()` so tests can build and drive a real server
    against an injected fake client without touching the real filesystem
    or `sys.argv`/stdio at all (mirrors `jira/mcp_server.py::build_server`)."""
    local_client = client if client is not None else LocalDocsClient(config)

    server = FastMCP(
        SERVER_NAME,
        instructions=(
            "Search and fetch files from this connector's configured local "
            f"directory allowlist: {config.allowed_directories}. Readable file "
            f"types are gated by this connector's configured file_categories "
            f"({config.file_categories}) -- 'text' (.md/.markdown/.txt/.rst) is the "
            "default; 'code' (source/config files), 'office' (.docx/.xlsx/.pptx), "
            "and 'pdf' are opt-in via config, not auto-enabled. OCR/image-based "
            "text recognition is out of scope regardless of configured categories "
            "-- a deliberate exclusion, not a gap. Every search/fetch resolves the "
            "real, symlink-followed path of each file and verifies it is actually "
            "inside one of the allowlisted directories before returning its "
            "content -- naming a directory outside this list, or a "
            "path-traversal/symlink-escape id, is refused, never silently widened "
            "or resolved. No credential, no network call -- this reads local disk "
            "only."
        ),
    )

    @server.tool()
    def search(query: str, directories: Optional[List[str]] = None) -> List[Document]:
        """Search plain-text files under this connector's allowlisted
        local directories. `directories` optionally narrows the search
        to a subset of this connector's allowlisted directories (default:
        every allowlisted directory). Naming a directory outside the
        connector's configured allowlist is refused."""
        return local_client.search(query, directories=directories)

    @server.tool()
    def fetch(id: str) -> Document:
        """Fetch a single file by id (an absolute path string, normally
        a prior `search` result's id). Refuses to return a file outside
        this connector's configured directory allowlist, including via a
        path-traversal id or a symlink resolving outside the allowlist."""
        return local_client.fetch(id)

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
