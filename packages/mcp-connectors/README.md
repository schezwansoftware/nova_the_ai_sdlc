# mcp-connectors

Standalone MCP (Model Context Protocol) servers for **Jira**, **Confluence**,
and **SharePoint** (Online + on-prem Server) — precise, project/space/site-
scoped search and fetch, usable with any MCP-compatible client (Claude
Desktop/Code, VS Code, IntelliJ, or your own agent framework).

Zero dependency on any other project — no shared package namespace, no
orchestration engine, nothing beyond this repo's own `src/mcp_connectors/`.
Verified: a fresh venv with only `pip install -e ".[all]"` run against this
repo passes the full test suite and resolves all three console scripts with
no other package installed.

## The precision requirement

Every connector enforces scope in two places, always:

1. **Config-time hard allowlist** — the connector's config file declares
   exactly which projects/spaces/sites it may touch. Naming anything outside
   that list is a hard error, never a silent widening.
2. **Query-time native scope filter** — every search passes the restriction
   through the native query language itself (JQL `project in (...)`, CQL
   `space in (...)`, Graph/on-prem search `Path:"..."` clauses) — never
   fetch-broadly-then-filter.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[jira,confluence,sharepoint]"   # or just the one(s) you need
```

Then see `INSTALL.md` for per-connector config, credential storage (OS
keyring, never plaintext), and IDE (VS Code / IntelliJ) setup.

## Status

No live Jira/Confluence/SharePoint tenant has been used to test this code —
built against current API documentation with defensive response parsing.
Treat a live-credentialed verification pass as required before production
use. See each module's own docstring for exactly what was checked against
live docs versus general familiarity.
