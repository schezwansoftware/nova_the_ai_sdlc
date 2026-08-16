"""Standalone local-directories MCP connector (`local-docs-mcp` console
script).

Pure local-filesystem access -- no credential, no keyring, no network
call of any kind. See `mcp_connectors/local_fs/search.py`'s module
docstring for the shared walk/search/path-safety logic this connector is
built on, and `config.py`/`client.py`/`mcp_server.py`'s own docstrings
for this connector's specifics.
"""
