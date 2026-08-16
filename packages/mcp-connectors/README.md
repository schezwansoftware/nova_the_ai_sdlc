# mcp-connectors

Standalone MCP (Model Context Protocol) servers for **Jira**, **Confluence**,
**SharePoint** (Online + on-prem Server), **local directories**, and
**OneDrive** — precise, scoped search and fetch, usable with any
MCP-compatible client (Claude Desktop/Code, VS Code, IntelliJ, or your own
agent framework).

This package lives inside the Nova (`ai-sdlc`) repo at
`packages/mcp-connectors/`, but is a fully independent sibling package —
zero dependency on `ai_sdlc` or any other project, no shared package
namespace, no orchestration engine, nothing beyond this directory's own
`src/mcp_connectors/`. Verified: a fresh venv with only
`pip install -e ".[all]"` run from this directory passes the full test
suite and resolves all five console scripts with no other package
installed.

**Local directories and OneDrive need no credential, no keyring, and make
no network call at all** — OneDrive's desktop client already syncs files to
a local folder on disk, so that connector reads the synced folder directly
rather than calling Microsoft Graph. This was a deliberate design choice to
avoid the Azure AD OAuth complexity SharePoint Online needed — see
`src/mcp_connectors/onedrive/__init__.py`'s module docstring.

## The precision requirement

Every connector enforces scope in two places, always:

1. **Config-time hard allowlist** — the connector's config file declares
   exactly which projects/spaces/sites/directories it may touch. Naming
   anything outside that list is a hard error, never a silent widening.
2. **Query-time scope filter** — for Jira/Confluence/SharePoint, every
   search passes the restriction through the native query language itself
   (JQL `project in (...)`, CQL `space in (...)`, Graph/on-prem search
   `Path:"..."` clauses) — never fetch-broadly-then-filter. Local
   directories and OneDrive have no remote API doing server-side scoping,
   so the equivalent guarantee is enforced here directly: every file's
   *real*, symlink-resolved path (`Path.resolve()`) is verified to actually
   be inside an allowlisted directory before its content is ever read or
   returned — rejecting both symlink escapes and path-traversal-style
   `fetch()` ids, not just a `str.startswith(...)` prefix check. See
   `src/mcp_connectors/local_fs/search.py`'s module docstring for the full
   design and `tests/test_local_fs.py` for the tests that exercise it
   directly (a real symlink pointing outside the allowlist, a real
   path-traversal id).

## Quick start

```bash
cd packages/mcp-connectors   # from the Nova repo root
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[jira,confluence,sharepoint,local-docs,onedrive]"   # or just the one(s) you need
```

Then see `INSTALL.md` for per-connector config, credential storage (OS
keyring, never plaintext — not applicable to `local-docs`/`onedrive`, which
need no credential at all), and IDE (VS Code / IntelliJ) setup.

## V1 file-type scope (local directories / OneDrive only)

Only plain-text formats are read: `.md`, `.markdown`, `.txt`, `.rst`.
PDF/Word/Excel/image files are explicitly out of scope for V1 — not
attempted, not silently ignored; see `todo.md` for this deliberately
deferred gap. OneDrive's Files-On-Demand cloud-only placeholder files
(present in a directory listing but not yet downloaded locally) are
detected on a best-effort basis and skipped/flagged rather than returned as
empty or garbage content — see `src/mcp_connectors/local_fs/search.py`'s
module docstring for exactly what is and isn't detected.

## Status

No live Jira/Confluence/SharePoint tenant has been used to test this code —
built against current API documentation with defensive response parsing.
Treat a live-credentialed verification pass as required before production
use. See each module's own docstring for exactly what was checked against
live docs versus general familiarity. Local directories and OneDrive, by
contrast, were exercised against a real local filesystem and a real MCP
stdio protocol round trip this session (search, fetch, an allowlist
violation, and a path-traversal rejection all observed as real tool
responses) — see `todo.md`'s connectors section for the details.
