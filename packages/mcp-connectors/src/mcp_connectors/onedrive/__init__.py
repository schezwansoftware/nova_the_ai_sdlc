"""Standalone OneDrive MCP connector (`onedrive-mcp` console script).

**Reads the OneDrive desktop client's already-synced local folder
directly off disk -- this is a deliberate design choice, not a
shortcut.** It does *not* call Microsoft Graph or any other cloud API,
and needs no Azure AD app registration/OAuth client of any kind,
specifically to avoid the Azure AD OAuth complexity the existing
`sharepoint` connector's Online backend required (see
`sharepoint/online_client.py`). Structurally this connector is nearly
identical to `local_docs` -- both are thin wrappers over
`mcp_connectors.local_fs.search`'s shared logic, differing mainly in
naming/documentation/defaults-hinting and in opting into OneDrive
Files-On-Demand cloud-placeholder detection. See
`config.py`/`client.py`/`mcp_server.py`'s own docstrings for specifics.
"""
