# mcp-connectors

Standalone MCP (Model Context Protocol) servers for pulling context into any
AI agent — five connectors, each usable with any MCP-compatible client
(Claude Desktop/Code, VS Code, IntelliJ, or your own agent framework).

| Connector | Source | Credential needed? |
|---|---|---|
| Jira | Cloud or self-hosted Data Center | Yes |
| Confluence | Cloud or self-hosted Data Center | Yes |
| SharePoint | Online or on-prem Server | Yes |
| Local Docs | Any local directory | No |
| OneDrive | Your already-synced local OneDrive folder | No |

Full setup instructions: **[INSTALL.md](INSTALL.md)**.

## What makes this different

- **Independent of Nova.** This package lives inside the Nova (`ai-sdlc`)
  repo at `packages/mcp-connectors/`, but has zero dependency on `ai_sdlc`
  or any other project — no shared package namespace, no orchestration
  engine involved. `pip install -e ".[all]"` from this directory alone
  passes the full test suite with nothing else installed.
- **Scoped, not database-wide.** Every connector enforces what it's allowed
  to touch in two places: a config-time allowlist (specific projects,
  spaces, sites, or directories — never "search everything"), and a
  query-time check that actually enforces it (a native query restriction
  for Jira/Confluence/SharePoint, a real resolved-path check for Local
  Docs/OneDrive that also blocks symlink escapes and path traversal).
- **No cloud API for OneDrive.** It reads the OneDrive desktop client's
  already-synced local folder directly off disk, so it needs no Azure AD
  app registration and no credential at all — a deliberate simplification
  vs. SharePoint Online.
- **Live query, no index.** Every search happens at query time; nothing is
  pre-indexed or cached.

## Quick start

```bash
cd packages/mcp-connectors   # from the Nova repo root
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[jira,confluence,sharepoint,local-docs,onedrive]"   # or just what you need
```

Then follow **[INSTALL.md](INSTALL.md)** for credentials, config files, and
connecting to VS Code, IntelliJ, or your own agent code.

## Status

Jira/Confluence/SharePoint have not been tested against a real tenant — no
credentials were available to do so. Local Docs and OneDrive *have* been
exercised end-to-end against a real filesystem and a real MCP client this
session. Full detail in INSTALL.md's "What's been verified" section.
