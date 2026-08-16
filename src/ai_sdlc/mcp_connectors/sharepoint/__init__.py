"""Standalone SharePoint MCP connector (`ai-sdlc-mcp-sharepoint` console
script).

Unlike Jira/Confluence, this connector has **two disjoint backend
implementations** behind one `search`/`fetch` interface -- SharePoint
Online (Microsoft Graph Search API, Azure AD app auth) and SharePoint
Server on-prem (`_api/search/query` REST, NTLM/Basic auth; genuinely no
Graph involvement at all, Graph is M365-cloud-only) -- selected per
configured site via an explicit `deployment_type: "online" | "server"`
config field, never auto-detected from the URL. See `config.py`'s module
docstring for why auto-detection was explicitly rejected, and
`online_client.py`/`onprem_client.py`/`client.py`'s docstrings for the
rest.

See `mcp_connectors/__init__.py` for this package's overall scope
boundary.
"""
