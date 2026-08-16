"""Standalone Jira MCP server -- the `jira-mcp` console script's entry
point.

Built on the official `mcp` Python SDK's high-level `FastMCP` server
(verified installed: `mcp==1.29.0`), **not** any in-process/embedded
MCP-server helper tied to a particular AI SDK -- those can't run
standalone as their own process, which is exactly why this design
requires the real, standalone `mcp` SDK instead. `FastMCP.run(transport
="stdio")` (the default transport, confirmed via `inspect.signature`
against the installed package) is what makes `jira-mcp` a real,
independent process talking MCP over stdio to whatever client launches
it -- Claude Desktop/Code, VS Code, IntelliJ, or any other
MCP-compatible client -- with no other orchestration process involved
at all.

Only imports `mcp` and `mcp_connectors.{common,jira}` -- no other
project's code, anywhere -- so this really is standalone, not
standalone "in principle." See `mcp_connectors/__init__.py` for the
full scope boundary.

Exceptions raised inside `search`/`fetch` below (typically
`ConnectorConfigError`/`ConnectorAPIError`/`ConnectorAuthError` from
`common.py`/`jira/client.py`, but this relies on no particular type --
*any* exception works) are intentionally left to propagate out of the
tool functions uncaught: `mcp_connectors/common.py`'s module docstring
documents, against the installed package's own source, that FastMCP
already converts any such exception into a real `isError` MCP tool
response. No bespoke translation layer belongs here.
"""
from __future__ import annotations

import sys
from typing import List, Optional

from mcp.server.fastmcp import FastMCP

from mcp_connectors.common import ConnectorError, Document
from mcp_connectors.jira.client import JiraClient
from mcp_connectors.jira.config import JiraConnectorConfig, load_config

SERVER_NAME = "jira-mcp"


def build_server(config: JiraConnectorConfig, *, client: Optional[JiraClient] = None) -> FastMCP:
    """Construct the `FastMCP` server for a given, already-loaded config.
    Separated from `main()` so tests can build and drive a real server
    (`server.call_tool(...)`) against an injected fake `JiraClient`
    without touching the filesystem or `sys.argv`/stdio at all."""
    jira_client = client if client is not None else JiraClient(config)

    server = FastMCP(
        SERVER_NAME,
        instructions=(
            "Search and fetch Jira issues, hard-scoped to this connector's "
            f"configured project allowlist: {config.allowed_projects}. Every "
            "search is restricted to these projects at the JQL level -- naming "
            "a project outside this list is refused, never silently widened."
        ),
    )

    @server.tool()
    def search(query: str, projects: Optional[List[str]] = None) -> List[Document]:
        """Search Jira issues by free text. `projects` optionally narrows
        the search to a subset of this connector's allowlisted project
        keys (default: every allowlisted project). Naming a project
        outside the connector's configured allowlist is refused."""
        return jira_client.search(query, projects=projects)

    @server.tool()
    def fetch(id: str) -> Document:
        """Fetch a single Jira issue by its key (e.g. 'PROJ-123'). Refuses
        to return an issue outside this connector's configured project
        allowlist."""
        return jira_client.fetch(id)

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
