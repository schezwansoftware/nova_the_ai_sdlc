# Installing and configuring mcp-connectors

Verified empirically this session: fresh venv, `pip install -e ".[all,dev]"`,
full test suite (139 passed), all three console scripts resolve, all three
servers import with zero `PYTHONPATH` tricks and zero trace of any other
project's package name anywhere in this codebase.

## 1. Install

```bash
git clone <this repo> mcp-connectors
cd mcp-connectors
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[jira,confluence,sharepoint]"   # or a subset, or `.[all]`
```

This gives you three commands on your `PATH` (inside the venv):
`jira-mcp`, `confluence-mcp`, `sharepoint-mcp`.

## 2. Configure each connector — JSON file + OS keyring, not env vars

**The only environment variable this code reads is `MCP_CONNECTORS_CONFIG_DIR`**
(a directory path override — confirmed by grepping the whole package for
every `os.environ`/`getenv` call). Base URL, deployment type, tenant/client
IDs, and allowlists live in a JSON config file. Secrets live in the OS
keyring, referenced (never stored in plaintext) from that JSON file.

| Connector | Default config path |
|---|---|
| Jira | `~/.config/mcp-connectors/jira.json` |
| Confluence | `~/.config/mcp-connectors/confluence.json` |
| SharePoint | `~/.config/mcp-connectors/sharepoint.json` |

Override the directory (not per-field) via `MCP_CONNECTORS_CONFIG_DIR`.

### Jira — `jira.json`

```json
{
  "site": {
    "base_url": "https://yourorg.atlassian.net",
    "deployment_type": "cloud",
    "auth_method": "cloud_api_token",
    "account_identifier": "bot@yourorg.com",
    "credential": {"service": "jira-mcp", "username": "bot@yourorg.com"}
  },
  "allowed_projects": ["ENG", "PLAT"],
  "result_limit": 15
}
```
For self-hosted Data Center: `"deployment_type": "data_center"`,
`"auth_method"` = `"data_center_pat"` (8.14+) or `"data_center_basic"` (older).

Store the matching secret once:
```bash
python -c "from mcp_connectors.common import store_secret; \
  store_secret('jira-mcp', 'bot@yourorg.com', '<the real API token>')"
```

### Confluence — `confluence.json`

Same `site` shape as Jira plus `"allowed_spaces": ["ENG", "PLAT"]` instead of
`allowed_projects`. Use a distinct keyring `service` string
(`confluence-mcp`, not `jira-mcp`) even if it's the same Atlassian site/token.

### SharePoint — `sharepoint.json`

Two shapes, selected explicitly via `deployment_type` (never auto-detected):

```json
{
  "site": {
    "deployment_type": "online",
    "site_url": "https://yourorg.sharepoint.com/sites/finance",
    "tenant_id": "<azure-ad-tenant-guid>",
    "client_id": "<azure-ad-app-client-id>",
    "client_credential": {"service": "sharepoint-mcp", "username": "finance-online-client-secret"}
  },
  "allowed_sites": ["https://yourorg.sharepoint.com/sites/finance"],
  "result_limit": 15
}
```
or, on-prem Server:
```json
{
  "site": {
    "deployment_type": "server",
    "site_url": "https://sharepoint.internal.corp/sites/finance",
    "username": "CONTOSO\\svc-mcp-connectors",
    "credential": {"service": "sharepoint-mcp", "username": "CONTOSO\\svc-mcp-connectors"}
  },
  "allowed_sites": ["https://sharepoint.internal.corp/sites/finance"],
  "result_limit": 15
}
```
Kerberos is **not implemented** (NTLM/Basic only) — a known, documented gap.

## 3. VS Code setup

**Confidence: high on the mechanism, not live-tested against this exact server this session.**

```json
{
  "servers": {
    "jira": {
      "type": "stdio",
      "command": "/absolute/path/to/mcp-connectors/.venv/bin/jira-mcp",
      "env": { "MCP_CONNECTORS_CONFIG_DIR": "/absolute/path/to/mcp-connectors/config" }
    },
    "confluence": {
      "type": "stdio",
      "command": "/absolute/path/to/mcp-connectors/.venv/bin/confluence-mcp",
      "env": { "MCP_CONNECTORS_CONFIG_DIR": "/absolute/path/to/mcp-connectors/config" }
    },
    "sharepoint": {
      "type": "stdio",
      "command": "/absolute/path/to/mcp-connectors/.venv/bin/sharepoint-mcp",
      "env": { "MCP_CONNECTORS_CONFIG_DIR": "/absolute/path/to/mcp-connectors/config" }
    }
  }
}
```
Now that these are real installed console scripts, no `PYTHONPATH`/`-m`
juggling is needed — just point `command` straight at the venv's script.
Reload the window / use the MCP panel to confirm each server starts and
lists its `search`/`fetch` tools. The top-level key name and exact schema
have shifted across VS Code's MCP rollout — verify against your installed
version's `MCP: Add Server` command if this doesn't match.

## 4. IntelliJ IDEA setup

**Confidence: lower — not independently verified, and the surface differs by which AI plugin provides MCP support.**

JetBrains AI Assistant has been adding MCP client support (Settings →
Tools → AI Assistant → Model Context Protocol) with a UI for adding a
server by command + args + env, similar in shape to VS Code's config above.
If you're using a different plugin for MCP, its surface will differ. Either
way, it needs the same two things: a `command` (the venv's `jira-mcp` etc.)
and one `env` entry (`MCP_CONNECTORS_CONFIG_DIR`, if you're not using the
default `~/.config/mcp-connectors/` path). If the plugin's UI has no `env`
block, set it as a real OS/user-level environment variable before launching
the IDE instead.

## 5. What's still unverified

No real Jira/Confluence/SharePoint tenant has exercised any of this code.
No real MCP client (VS Code/IntelliJ/Claude Desktop) has been used to drive
these servers end-to-end — only a low-level protocol round-trip unit test
(`tests/test_servers.py`) proves the `isError` contract, which is not the
same as proving a real client's discovery/launch/tool-call flow works.
Treat both as required verification before trusting this in production, not
as done.
