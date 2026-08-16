# Setup guide

Jump to: [Which connector?](#which-connector-do-you-need) ·
[1. VS Code](#1-setup-with-vs-code) ·
[2. IntelliJ](#2-setup-with-intellij-idea) ·
[3. Custom agent code](#3-setup-with-a-custom-agentic-framework-eg-ai-sdlc--nova-itself) ·
[Connector config reference](#connector-config-reference) ·
[file_categories](#file_categories-local-docs--onedrive-only) ·
[What's been verified](#whats-been-verified)

## Which connector do you need?

| Connector | Console command | Credential? | Install extra | Config file |
|---|---|---|---|---|
| Jira | `jira-mcp` | Yes — API token | `jira` | `jira.json` |
| Confluence | `confluence-mcp` | Yes — API token | `confluence` | `confluence.json` |
| SharePoint | `sharepoint-mcp` | Yes — OAuth secret or domain password | `sharepoint` | `sharepoint.json` |
| Local Docs | `local-docs-mcp` | No | `local-docs` | `local_docs.json` |
| OneDrive | `onedrive-mcp` | No | `onedrive` | `onedrive.json` |

Every connector follows the same five-step setup:
**install → (store a credential, if it needs one) → write a config file →
register with your client → verify it's connected.** The three paths below
walk through all five steps for **Jira** (the most complete example, since
it needs a credential); if you're setting up Local Docs or OneDrive
instead, just skip the credential step — everything else is identical with
the connector's own command/config file swapped in from the table above.
Exact config file contents for all five connectors are in the
[Connector config reference](#connector-config-reference).

---

## 1. Setup with VS Code

**Step 1 — Install.**
```bash
git clone <the nova repo>
cd nova/packages/mcp-connectors
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[jira]"   # swap in confluence / sharepoint / local-docs / onedrive as needed
```

**Step 2 — Store your Jira API token in the OS keyring.** Never in a config
file — this writes to macOS Keychain / Windows Credential Locker / Linux
Secret Service.
```bash
python -c "from mcp_connectors.common import store_secret; \
  store_secret('jira-mcp', 'bot@yourorg.com', '<your real Jira API token>')"
```
*(Local Docs and OneDrive: skip this step — no credential needed.)*

**Step 3 — Write the config file** at `~/.config/mcp-connectors/jira.json`:
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
`credential` must match the `service`/`username` from Step 2 exactly.

**Step 4 — Register the server with VS Code.** Create `.vscode/mcp.json` in
your open workspace folder:
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
Only add an `env` block if your config file lives somewhere other than the
default path: `"env": { "MCP_CONNECTORS_CONFIG_DIR": "/your/chosen/path" }`.

**Step 5 — Verify.** Command Palette → `MCP: List Servers` should show
`jira` running with 2 tools (`search`, `fetch`). If it errors, run
`.venv/bin/jira-mcp` directly in a terminal to see the real startup error —
usually a missing credential or a config file in the wrong place.

**Step 6 — Use it.** In Copilot Chat's agent mode, ask something that needs
Jira context — e.g. *"Search Jira project ENG for open bugs about the login
flow."* The model calls `search` itself when it decides it's relevant.

> **Note on confidence**: the `.vscode/mcp.json` mechanism is
> well-established, but this exact file wasn't tested against a live VS
> Code instance. If `MCP: List Servers` doesn't pick it up, check your
> version's current docs — the schema has shifted across VS Code's MCP
> rollout.

---

## 2. Setup with IntelliJ IDEA

**Steps 1–3 are identical to VS Code above** — install, store the
credential, write the config file.

**Step 4 — Register the server with your MCP-capable plugin.** IntelliJ
itself doesn't speak MCP; a plugin does, and the exact menu depends on
which one you have:
- **JetBrains AI Assistant**: `Settings → Tools → AI Assistant → Model
  Context Protocol` → add a server.
- **GitHub Copilot for IntelliJ** (or another MCP plugin): its own,
  separate settings page.

Whichever UI you're in, it needs the same two values:
- **Command**: `/absolute/path/to/nova/packages/mcp-connectors/.venv/bin/jira-mcp`
- **Environment variable** (only if not using the default config path):
  `MCP_CONNECTORS_CONFIG_DIR` = your chosen path

No `env` field in the plugin's UI? Set `MCP_CONNECTORS_CONFIG_DIR` as a
real OS-level environment variable before launching IntelliJ instead.

**Step 5 — Verify and use it** — same as VS Code Steps 5–6: your plugin's
MCP panel should list `jira` with its two tools; ask a Jira-related
question and it calls `search`/`fetch` on its own.

> **Note on confidence**: this is the least-verified path in this guide —
> no specific IntelliJ plugin's MCP settings were clicked through this
> session. The command/env-var values above are correct regardless of
> which plugin you use; only the menu path to enter them differs.

---

## 3. Setup with a custom agentic framework (e.g. ai-sdlc / Nova itself)

**Nova's own agent framework does not support MCP tools today** —
confirmed by reading the code, not assumed. `claude_sdk.py` (Nova's real
Claude provider) builds its session with a hardcoded tool allowlist and
never passes any MCP server config, so even an auto-discovered server's
tools would be silently blocked. There's no config flag that turns this on
in Nova today — it needs real code changes (§3b).

### 3a. Call the server directly from Python (works today, tested)

Any custom agent loop — inside `ai-sdlc` or anywhere else — can use these
servers via the official `mcp` Python SDK's client. This exact script was
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

Observed directly, not assumed:
- `list_tools()` returns real tool definitions with descriptions and JSON
  schemas — feed these into whatever tool-calling format your agent loop
  expects.
- A real upstream failure comes back as `result.isError == True` with a
  readable message — never an exception that crashes your loop.
- An allowlist violation comes back the same way, with the specific
  violation message.

### 3b. Wiring this into Nova's own Claude-backed agents (not built — a real next step)

Two code changes in `claude_sdk.py` would be needed:
1. Pass an `mcp_servers` config into the `ClaudeAgentOptions(...)`
   construction, pointing at the connector(s) you want — same idea as this
   guide's `.vscode/mcp.json`.
2. Add the resulting tool names (`mcp__jira__search`, etc.) to
   `_build_allowed_tools`'s output — otherwise step 1 alone still gets
   silently blocked by the existing allowlist.

This is a real, scoped, currently-unbuilt follow-up — not a hidden feature.

---

## Connector config reference

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
- Self-hosted Data Center: `"deployment_type": "data_center"`,
  `"auth_method"` = `"data_center_pat"` (8.14+) or `"data_center_basic"`
  (older).
- Resolves its credential at server **startup**, not on first search — a
  missing/wrong keyring entry makes the server fail to launch immediately.

### Confluence — `confluence.json`
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
- Same `site` shape and Data Center options as Jira (shared Atlassian auth
  module). Use `allowed_spaces`, not `allowed_projects`.
- Use a distinct keyring `service` (`confluence-mcp`) even against the
  same Atlassian site/token as Jira.

### SharePoint — `sharepoint.json`
Two shapes, chosen explicitly via `deployment_type` — never auto-detected
from the URL (real sync/tenant setups vary too much to guess reliably).

Online:
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
On-prem Server:
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
- Kerberos is **not implemented** for Server (NTLM/Basic only) — a known,
  documented gap.
- Same startup-credential-check behavior as Jira/Confluence.

### Local Docs — `local_docs.json`
```json
{
  "allowed_directories": ["/Users/alice/notes", "/Users/alice/work-docs"],
  "result_limit": 15
}
```
- No `site`/`credential` block — no network call, no credential.
- Every directory is resolved to its real absolute path at config-load
  time; it must already exist or loading fails immediately with a clear
  error.
- By default, reads only `.md`/`.markdown`/`.txt`/`.rst` files (see
  [file_categories](#file_categories-local-docs--onedrive-only) to widen
  this).
- Every file's real, symlink-resolved path is checked against
  `allowed_directories` before it's ever read — a symlink pointing outside
  the list, or a path-traversal `fetch` id (e.g. `"../../etc/passwd"`), is
  refused, never silently followed.
- Fails at startup only if the config file is missing or names a directory
  that doesn't exist — nothing else to check, since there's no credential.

### OneDrive — `onedrive.json`
```json
{
  "allowed_directories": ["/Users/alice/Library/CloudStorage/OneDrive-Contoso"],
  "result_limit": 15
}
```
Structurally identical to Local Docs (same fields, same "no credential"
behavior, same path-safety guarantee, same
[file_categories](#file_categories-local-docs--onedrive-only) mechanism).
Two real differences:

1. **Point it at your already-synced local OneDrive folder** — never
   auto-detected, since real paths vary by OS and account type:
   | OS | Typical path |
   |---|---|
   | macOS | `~/Library/CloudStorage/OneDrive-<AccountName>/` (current) or `~/OneDrive` (older) |
   | Windows | `%USERPROFILE%\OneDrive` (personal) or `%USERPROFILE%\OneDrive - <Org Name>` (work/school) |

   Multiple synced accounts each get their own folder — list as many
   `allowed_directories` entries as you need. Always verify your actual
   path rather than assuming one of these.

2. **Cloud-only placeholder detection.** OneDrive's "Files On-Demand" can
   leave a file visible in a folder listing but not actually downloaded
   yet. This connector detects that on a best-effort basis (a
   Windows-specific attribute check, plus a zero-byte heuristic on any
   platform) and skips such files in search / refuses them in fetch with a
   clear error — rather than returning empty or garbage content. This is
   **not complete**: macOS's real placeholder-status signal isn't reachable
   from Python's stdlib, so a non-zero-byte macOS placeholder won't be
   caught.

## file_categories (Local Docs & OneDrive only)

Both connectors default to `.md`/`.markdown`/`.txt`/`.rst` only. Add
`file_categories` to a config file to opt into more — this is a
config-time **permission gate**, not automatic:

```json
{
  "allowed_directories": ["/Users/alice/notes", "/Users/alice/repo"],
  "file_categories": ["text", "code", "office", "pdf"],
  "result_limit": 15
}
```

| Category | What it reads | Extra dependency needed |
|---|---|---|
| `text` (default) | `.md`, `.markdown`, `.txt`, `.rst` | none |
| `code` | ~40 common source/config extensions (`.py`, `.js`, `.ts`, `.java`, `.go`, `.yaml`, `.json`, `.sql`, …) | none — read as plain text |
| `office` | Real embedded text from `.docx`/`.xlsx`/`.pptx` | `documents` extra |
| `pdf` | Real embedded text from `.pdf`, page by page | `documents` extra |

- Install the `documents` extra to use `office`/`pdf`:
  `pip install -e ".[local-docs,documents]"` (or `[onedrive,documents]`).
  Requesting either category without it installed fails **at config-load
  time** with a clear, actionable error — never a silent no-op later.
- **OCR / image text recognition is explicitly out of scope, permanently**
  — confirmed directly with the project owner. No Tesseract or other OCR
  dependency exists anywhere in this package.
- A corrupted, malformed, or password-protected office/PDF file is skipped
  gracefully — never crashes a search.
- **Performance note**: there's still no indexing, so an office/PDF file is
  re-parsed from scratch on every query that reaches it — noticeably slower
  than plain-text reads. Worth knowing before pointing this at a directory
  with hundreds of large office documents.
- An existing config with no `file_categories` key at all is completely
  unaffected — it defaults to `["text"]`, identical to the original
  behavior before this feature existed.

## What's been verified

**Jira/Confluence/SharePoint**: no real tenant or credentials were
available — built against current API documentation with defensive
response parsing. Treat a live-credentialed pass as required before
production use.

**Local Docs**: fully exercised end-to-end this session — a real MCP
client talking to a real `local-docs-mcp` process against a real directory
on disk. `list_tools()` returned `['search', 'fetch']`; a real search found
real file content; an out-of-allowlist directory came back as
`isError: True`; a path-traversal `fetch` id (`"../../etc/passwd"`) was
correctly refused, not silently resolved.

**OneDrive**: same real end-to-end verification as Local Docs. **Not**
verified: real Files-On-Demand placeholder detection (only tested against
a synthetic zero-byte stand-in — no real OneDrive account was available to
produce an actual placeholder), and the Windows-specific attribute check
(unit-tested via a monkeypatched `stat()` result only — no Windows machine
available).

**`file_categories` / office / PDF support**: re-ran the same real MCP
round trip with `file_categories: ["text", "code", "pdf"]` — a real `.py`
file and a real generated `.pdf` (built with `pypdf` itself) both came back
as genuine search hits with real extracted text. All four formats
(`.docx`/`.xlsx`/`.pptx`/`.pdf`) are tested against real fixtures built
with the same library that reads them, including a corrupted-file case per
format that's confirmed to skip cleanly. **Not** independently verified: a
real password-protected `.docx`/`.xlsx`/`.pptx` (only PDF encryption was
tested), and no large/complex real-world document (multi-hundred-page PDF,
heavily-formulated `.xlsx`) was tested for correctness at scale or the
documented performance cost.
