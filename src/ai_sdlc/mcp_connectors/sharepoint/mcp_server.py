"""Standalone SharePoint MCP server -- the `ai-sdlc-mcp-sharepoint`
console script's entry point. Same `FastMCP`/stdio-transport rationale
and "let exceptions propagate, FastMCP converts them to real MCP tool
errors" posture as `jira/mcp_server.py`/`confluence/mcp_server.py` (see
that module's docstring for the fuller account, not repeated here); the
only real difference is that the tool functions here call through
`SharePointClient` (`client.py`), which itself dispatches per-site to
whichever of the two disjoint backends (`online_client.py`/
`onprem_client.py`) that site is configured as.
"""
from __future__ import annotations

import sys
from typing import List, Optional

from mcp.server.fastmcp import FastMCP

from ai_sdlc.mcp_connectors.common import ConnectorError, Document
from ai_sdlc.mcp_connectors.sharepoint.client import SharePointClient
from ai_sdlc.mcp_connectors.sharepoint.config import SharePointConnectorConfig, load_config

SERVER_NAME = "ai-sdlc-mcp-sharepoint"


def build_server(
    config: SharePointConnectorConfig, *, client: Optional[SharePointClient] = None
) -> FastMCP:
    sharepoint_client = client if client is not None else SharePointClient(config)
    site_urls = [site.site_url for site in config.sites]

    server = FastMCP(
        SERVER_NAME,
        instructions=(
            "Search and fetch SharePoint content, hard-scoped to this connector's "
            f"configured site allowlist: {site_urls}. Every search is restricted "
            "to these sites at the native query level (Graph Search 'Path:' "
            "clauses for SharePoint Online sites, '_api/search/query' 'Path:' "
            "clauses for SharePoint Server sites) -- naming a site outside this "
            "list is refused, never silently widened."
        ),
    )

    @server.tool()
    def search(query: str, sites: Optional[List[str]] = None) -> List[Document]:
        """Search SharePoint content by free text. `sites` optionally
        narrows the search to a subset of this connector's allowlisted
        site URLs (default: every allowlisted site). Naming a site
        outside the connector's configured allowlist is refused. Each
        configured site may independently be SharePoint Online or
        SharePoint Server (on-prem) -- this tool doesn't distinguish;
        both are searched the same way from the caller's perspective."""
        return sharepoint_client.search(query, sites=sites)

    @server.tool()
    def fetch(id: str) -> Document:
        """Fetch a single SharePoint item by the id this connector's own
        search results returned for it (SharePoint item ids are not
        independently guessable/constructible -- always pass through an
        id exactly as `search()` returned it). Refuses to return content
        outside this connector's configured site allowlist."""
        return sharepoint_client.fetch(id)

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
