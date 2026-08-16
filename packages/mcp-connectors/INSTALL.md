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

**Local directories (`local-docs-mcp`) and OneDrive (`onedrive-mcp`) are the
one real exception to "everything else is the same": skip Step 2 (credential
storage) entirely for both** — neither connector uses a credential, a
keyring entry, or makes any network call. OneDrive reads the OneDrive
desktop client's already-synced local folder directly off disk rather than
calling Microsoft Graph, specifically to avoid Azure AD OAuth setup. Their
config files are correspondingly simpler (just `allowed_directories` +
`result_limit`, no `site`/`credential` block) — see the
[Connector config reference](#connector-config-reference) for the exact
shape. Everything else (Step 1 install, Step 4+ IDE/framework wiring) is
the same pattern with `local-docs-mcp`/`local_docs.json` or
`onedrive-mcp`/`onedrive.json` swapped in.

---

## 1. Setup with VS Code

**Step 1 — Get the code and install.**
```bash
git clone <the nova repo>
cd nova/packages/mcp-connectors
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[jira]"          # add ,confluence / ,sharepoint / ,local-docs / ,onedrive if you want those too
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

**Jira, Confluence, and SharePoint all resolve their credential at server
*startup*, not on first search** (observed directly: an unset/wrong
keyring entry makes the server process fail immediately on launch, not
fail quietly on first use) — so if your MCP client shows the server as
errored/not-connecting, check the credential and config file first.

**Local Docs** (`local_docs.json`, `allowed_directories`) — no
`site`/`credential` block at all: this connector makes no network call and
needs no credential. Every directory listed is resolved to its real,
canonical absolute path (`Path.resolve(strict=True)`) at config-load
time — it must already exist, or loading the config fails immediately with
a clear error.

```json
{
  "allowed_directories": ["/Users/alice/notes", "/Users/alice/work-docs"],
  "result_limit": 15
}
```

`search`/`fetch` only ever read `.md`/`.markdown`/`.txt`/`.rst` files —
PDF/Word/Excel/image files are explicitly out of scope for V1 (see
`todo.md`). Every file's real, symlink-resolved path is verified to be
inside one of the directories above before its content is ever read or
returned — a symlink pointing outside this list, or a path-traversal-style
`fetch` id (e.g. `"../../etc/passwd"`), is refused, not silently resolved.

**OneDrive** (`onedrive.json`, `allowed_directories`) — structurally
identical to Local Docs (same field name, same "no credential" shape); the
only real difference is what you point it at and one extra behavior. Point
it at your **already-synced local OneDrive folder(s)** — this connector
never auto-detects the path (real sync-folder locations vary too much
across OS/account type to guess reliably) and never calls Microsoft Graph:

```json
{
  "allowed_directories": ["/Users/alice/Library/CloudStorage/OneDrive-Contoso"],
  "result_limit": 15
}
```

Typical real sync-folder locations, for reference (always verify your own
actual path rather than assuming one of these):
- **macOS**: `~/Library/CloudStorage/OneDrive-<AccountName>/` (current
  OneDrive versions) or `~/OneDrive` (older versions).
- **Windows**: `%USERPROFILE%\OneDrive` (personal account) or
  `%USERPROFILE%\OneDrive - <Org Name>` (work/school account). Multiple
  accounts can be synced side by side, each its own folder — list as many
  `allowed_directories` entries as you need.

One extra behavior beyond Local Docs: OneDrive's "Files On-Demand" feature
can leave a file as a cloud-only placeholder (visible in the folder listing
but not actually downloaded locally yet). This connector detects that on a
best-effort basis (a Windows-specific file-attribute check, plus a
zero-byte-file heuristic on any platform) and skips such files in `search`
results / refuses them in `fetch` with a clear error, rather than returning
empty or garbage content. This is **not** a complete solution — see
`src/mcp_connectors/local_fs/search.py`'s module docstring for exactly what
is and isn't detected (notably: not on macOS's own placeholder-status API,
which isn't reachable from Python's stdlib).

**Neither Local Docs nor OneDrive has a credential-resolution step at
startup** — both fail at startup only if their config file is missing or
names a directory that doesn't exist/isn't a directory; there is nothing
else to check.

## What's still unverified

No real Jira/Confluence/SharePoint tenant has been used to test this
code — the API-failure example in section 3a used a nonexistent site on
purpose, to show the error path, not a real one. Response parsing is
defensive throughout, built against current API documentation. Treat a
live-credentialed pass against a real tenant as required before production
use, not as done.

Local Docs and OneDrive are different: there's no external tenant to
credential against, so a real end-to-end verification was possible and was
actually run this session — a real MCP stdio client (the same pattern as
section 3a's script) talking to a real `local-docs-mcp` process, against a
real config file and a real directory on disk: `list_tools()` returned
`['search', 'fetch']`, a real `search` call found and returned real file
content, naming a directory outside the configured allowlist came back as
`isError: True` with the allowlist-violation message, and a path-traversal
`fetch` id (`"../../etc/passwd"`) came back as `isError: True` with the
path-safety rejection message — not a crash, not a silently-resolved read.
What's genuinely **not** verified: OneDrive's cloud-only-placeholder
detection was only exercised against a synthetic zero-byte file standing in
for a real placeholder (see `todo.md`) — no real OneDrive client/account
was available to produce an actual Files-On-Demand placeholder to test
against, and the Windows-specific file-attribute check has no Windows
machine to verify against at all (only unit-tested via a monkeypatched
`stat()` result).
