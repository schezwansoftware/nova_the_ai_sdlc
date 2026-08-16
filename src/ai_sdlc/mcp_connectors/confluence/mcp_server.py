"""Standalone Confluence MCP server -- the `ai-sdlc-mcp-confluence`
console script's entry point. Structurally identical to
`jira/mcp_server.py` (same `FastMCP`/stdio-transport rationale, same
"let exceptions propagate, FastMCP converts them to real MCP tool
errors" posture -- see that module's docstring for the fuller account
of both, not repeated here) with Confluence's own client/config wired in
and `projects` renamed to `spaces` to match this connector's own
vocabulary.
"""
from __future__ import annotations

import sys
from typing import List, Optional

from mcp.server.fastmcp import FastMCP

from ai_sdlc.mcp_connectors.common import ConnectorError, Document
from ai_sdlc.mcp_connectors.confluence.client import ConfluenceClient
from ai_sdlc.mcp_connectors.confluence.config import ConfluenceConnectorConfig, load_config

SERVER_NAME = "ai-sdlc-mcp-confluence"


def build_server(
    config: ConfluenceConnectorConfig, *, client: Optional[ConfluenceClient] = None
) -> FastMCP:
    confluence_client = client if client is not None else ConfluenceClient(config)

    server = FastMCP(
        SERVER_NAME,
        instructions=(
            "Search and fetch Confluence content, hard-scoped to this connector's "
            f"configured space allowlist: {config.allowed_spaces}. Every search is "
            "restricted to these spaces at the CQL level -- naming a space outside "
            "this list is refused, never silently widened."
        ),
    )

    @server.tool()
    def search(query: str, spaces: Optional[List[str]] = None) -> List[Document]:
        """Search Confluence content by free text. `spaces` optionally
        narrows the search to a subset of this connector's allowlisted
        space keys (default: every allowlisted space). Naming a space
        outside the connector's configured allowlist is refused."""
        return confluence_client.search(query, spaces=spaces)

    @server.tool()
    def fetch(id: str) -> Document:
        """Fetch a single piece of Confluence content by its content id.
        Refuses to return content outside this connector's configured
        space allowlist."""
        return confluence_client.fetch(id)

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
