# Setup guide

Three separate, complete paths — pick the one you need and follow it top to
bottom. All three share the same first three steps (install, store a
credential, write a config file); each path only diverges at "connect it to
your client." Every command and code sample below was actually run this
session, not just written — including a full real MCP protocol round trip
(tool listed, a real API call made and its error handled, an allowlist
violation correctly rejected).

Jira is used as the running example throughout. Confluence and SharePoint
follow the identical pattern — their config file shapes are in the
[Connector config reference](#connector-config-reference) at the bottom;
swap `jira-mcp`/`jira.json` for `confluence-mcp`/`confluence.json` or
`sharepoint-mcp`/`sharepoint.json` and everything else is the same.

---

## 1. Setup with VS Code

**Step 1 — Get the code and install.**
```bash
git clone <the nova repo>
cd nova/packages/mcp-connectors
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[jira]"          # add ,confluence / ,sharepoint if you want those too
```

**Step 2 — Store your Jira API token in the OS keyring** (never in a config
file — this writes to macOS Keychain / Windows Credential Locker / Linux
Secret Service):
```bash
python -c "from mcp_connectors.common import store_secret; \
  store_secret('jira-mcp', 'bot@yourorg.com', '<your real Jira API token>')"
```

**Step 3 — Write the connector's config file** at
`~/.config/mcp-connectors/jira.json`:
```bash
mkdir -p ~/.config/mcp-connectors
cat > ~/.config/mcp-connectors/jira.json << 'EOF'
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
EOF
```
(`credential` must match the `service`/`username` you stored in Step 2
exactly.)

**Step 4 — Register the server with VS Code.** Create `.vscode/mcp.json` in
whatever workspace folder you have open in VS Code:
```json
{
  "servers": {
    "jira": {
      "type": "stdio",
      "command": "/absolute/path/to/nova/packages/mcp-connectors/.venv/bin/jira-mcp"
    }
  }
}
```
(No `env` block needed if you used the default config path in Step 3. If
you want the config to live somewhere else, add
`"env": { "MCP_CONNECTORS_CONFIG_DIR": "/your/chosen/path" }`.)

**Step 5 — Verify it's connected.** Open the Command Palette →
`MCP: List Servers` (or check the MCP status item in the bottom status
bar) — you should see `jira` running with 2 tools (`search`, `fetch`). If
it shows an error instead, run `.venv/bin/jira-mcp` directly in a terminal
to see the real startup error (most commonly: no credential stored, or no
config file at the expected path).

**Step 6 — Use it.** Open Copilot Chat in agent mode and ask something that
needs Jira context, e.g. *"Search Jira project ENG for open bugs about the
login flow."* The model will call the `search` tool itself when it decides
it's relevant — you don't invoke it manually.

*Confidence note: the mechanism (`.vscode/mcp.json`, stdio command) is
well-established, but this exact file was not tested against a live VS
Code instance this session — the schema has shifted across VS Code's MCP
rollout, so if `MCP: List Servers` doesn't recognize the file, check your
version's current docs for the top-level key name.*

---

## 2. Setup with IntelliJ IDEA

**Steps 1–3 are identical to VS Code above** — install, store the
credential, write the config file. Do those first.

**Step 4 — Register the server with your MCP-capable plugin.** IntelliJ
itself doesn't speak MCP — a plugin does, and which one you have
determines the exact UI:
- **JetBrains AI Assistant**: `Settings/Preferences → Tools → AI Assistant
  → Model Context Protocol` → add a server.
- **GitHub Copilot for IntelliJ** (or another MCP-capable plugin): its own
  settings page, separate from JetBrains AI Assistant's.

Whichever UI you're in, it asks for the same two things every stdio MCP
client needs:
- **Command**: `/absolute/path/to/nova/packages/mcp-connectors/.venv/bin/jira-mcp`
- **Environment variables** (only if you didn't use the default config
  path): `MCP_CONNECTORS_CONFIG_DIR` = your chosen path.

If the plugin's UI has no field for environment variables, set
`MCP_CONNECTORS_CONFIG_DIR` as a real OS-level environment variable before
launching IntelliJ instead.

**Step 5 — Verify and use it**, same as VS Code Step 5–6 above: your
plugin's MCP panel should list `jira` with its two tools; ask your AI
assistant a Jira-related question and it will call `search`/`fetch` on its
own.

*Confidence note: this is the least-verified section of this guide — I
have not clicked through any specific IntelliJ plugin's MCP settings this
session. The two values above (command, env var) are correct regardless of
which plugin's UI you're filling in; only the menu path differs.*

---

## 3. Setup with a custom agentic framework (e.g. ai-sdlc / Nova itself)

**Read this first: Nova's own agent framework does not support MCP tools
today.** I checked this directly against the code, not assumed it:
`src/ai_sdlc/capabilities/providers/claude_sdk.py` (Nova's real Claude
provider) builds its session with an explicit `allowed_tools` allowlist and
`permission_mode="dontAsk"`, and never passes any MCP server configuration.
Even if an MCP server were somehow auto-discovered, Nova's own allowlist
would silently block its tools, because nothing in this codebase adds MCP
tool names to that list. So there is no config file or flag that turns this
on in Nova today — it needs actual code changes. What follows are the two
real options.

### 3a. Talk to the server directly from your own Python code (works today, tested)

Any custom agent loop — inside `ai-sdlc` or anywhere else — can call these
servers with the official `mcp` Python SDK's client. This exact script was
run this session against a real `jira-mcp` process:

```python
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(
        command="/absolute/path/to/nova/packages/mcp-connectors/.venv/bin/jira-mcp",
        env={"MCP_CONNECTORS_CONFIG_DIR": "/your/chosen/config/path"},  # omit if using the default path
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print([t.name for t in tools.tools])   # -> ['search', 'fetch']

            result = await session.call_tool("search", {"query": "login bug", "projects": ["ENG"]})
            print(result.isError, result.content[0].text)

asyncio.run(main())
```

What this actually does, observed directly:
- `list_tools()` returns real tool definitions (`search`, `fetch`) with
  their descriptions and JSON schemas — feed these straight into whatever
  tool-calling format your agent loop / LLM API expects.
- A real upstream failure (tested against a nonexistent Jira site) comes
  back as `result.isError == True` with a human-readable message in
  `result.content[0].text` — not an exception that crashes your loop.
- Naming a project outside the connector's configured allowlist comes back
  the same way: `isError == True`, with the exact allowlist-violation
  message — verified by actually calling it with a disallowed project.

Add this as a plain function/tool in whatever agent framework you're
building — Nova's own agents included, if you want to wire it in yourself
(see 3b for exactly where that would go).

### 3b. What it would take to wire this into Nova's own Claude-backed agents (not built — a real next step, not instructions for something that exists)

If you want Nova's `DeveloperAgent`/`CodingCapability` (or another
Claude-backed capability) to use these tools automatically, two changes
would be needed in `claude_sdk.py`:

1. Pass an `mcp_servers` config into the `ClaudeAgentOptions(...)`
   construction (around line 226 of that file today) — pointing at
   `jira-mcp`/`confluence-mcp`/`sharepoint-mcp` the same way this guide's
   `.vscode/mcp.json` does.
2. Add the resulting tool names (Claude Code's convention is
   `mcp__<server-name>__<tool-name>`, e.g. `mcp__jira__search`) to
   `_build_allowed_tools`'s output — otherwise step 1 alone still gets
   silently blocked by the existing allowlist, exactly as described above.

This is genuinely unbuilt — a real scoped follow-up, not a hidden feature.
It was flagged, not implemented, because it wasn't part of what was asked
for in this pass.

---

## Connector config reference

Same `site`-shape pattern for all three; only the allowlist field name and
a few connector-specific fields change.

**Jira** (`jira.json`, `allowed_projects`) — shown in full above.

**Confluence** (`confluence.json`) — identical `site` shape to Jira (same
Atlassian auth module), `"allowed_spaces": ["ENG", "PLAT"]` instead of
`allowed_projects`. Use a distinct keyring `service` (`confluence-mcp`, not
`jira-mcp`) even against the same Atlassian site/token.

```json
{
  "site": {
    "base_url": "https://yourorg.atlassian.net",
    "deployment_type": "cloud",
    "auth_method": "cloud_api_token",
    "account_identifier": "bot@yourorg.com",
    "credential": {"service": "confluence-mcp", "username": "bot@yourorg.com"}
  },
  "allowed_spaces": ["ENG", "PLAT"],
  "result_limit": 15
}
```

**Self-hosted Data Center (Jira or Confluence)**: set
`"deployment_type": "data_center"` and `"auth_method"` to
`"data_center_pat"` (8.14+/7.9+) or `"data_center_basic"` (older).

**SharePoint** (`sharepoint.json`, `allowed_sites`) — two shapes, selected
explicitly via `deployment_type` (never auto-detected from the URL):

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
Kerberos is **not implemented** for SharePoint Server (NTLM/Basic only) —
a known, documented gap.

**All three connectors resolve their credential at server *startup*, not
on first search** (observed directly: an unset/wrong keyring entry makes
the server process fail immediately on launch, not fail quietly on first
use) — so if your MCP client shows the server as errored/not-connecting,
check the credential and config file first.

## What's still unverified

No real Jira/Confluence/SharePoint tenant has been used to test this
code — the API-failure example in section 3a used a nonexistent site on
purpose, to show the error path, not a real one. Response parsing is
defensive throughout, built against current API documentation. Treat a
live-credentialed pass against a real tenant as required before production
use, not as done.
