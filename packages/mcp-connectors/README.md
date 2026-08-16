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
# add ,documents too if you want local-docs/onedrive's "office"/"pdf" file_categories
```

Then see `INSTALL.md` for per-connector config, credential storage (OS
keyring, never plaintext — not applicable to `local-docs`/`onedrive`, which
need no credential at all), and IDE (VS Code / IntelliJ) setup.

## File-type scope (local directories / OneDrive only): opt-in categories

Both connectors gate *what kinds of files* they'll read behind a config
field, `file_categories` — the config-time "permission" for file type,
layered on top of the directory allowlist above. Default: `["text"]`
only (`.md`/`.markdown`/`.txt`/`.rst`) — exactly the original V1 scope, so
any existing config file with no `file_categories` key at all keeps
working unchanged. Opt in explicitly to widen it:

- **`"code"`** — a broad set of common source/config file extensions
  (`.py`, `.js`, `.ts`, `.java`, `.go`, `.yaml`, `.json`, …). No extra
  library needed — read via the same plain-text path as `"text"`.
- **`"office"`** — real embedded text from `.docx`/`.xlsx`/`.pptx`, via
  `python-docx`/`openpyxl`/`python-pptx`.
- **`"pdf"`** — real embedded text from `.pdf`, via `pypdf`.

`"office"`/`"pdf"` need the `documents` extra installed
(`pip install mcp-connectors[local-docs,documents]`, or
`[onedrive,documents]`) — requesting either without it fails config
loading immediately with a clear error, not a silent no-op. **OCR /
image-based text recognition is explicitly, permanently out of scope**
(a direct question to the project owner, answered explicitly: structured
documents with real embedded text only, not image/screenshot
recognition) — no Tesseract or other OCR dependency is used anywhere in
this package. A corrupted, malformed, or password-protected office/PDF
file is skipped gracefully (never crashes a search) — see
`src/mcp_connectors/local_fs/search.py`'s module docstring for exactly
what's caught and why, and for the honest performance note: with no
indexing (still V1's design), an office/PDF file is re-parsed on every
query that walks past it, slower than the plain-text read path.

OneDrive's Files-On-Demand cloud-only placeholder files (present in a
directory listing but not yet downloaded locally) are detected on a
best-effort basis and skipped/flagged rather than returned as empty or
garbage content — see the same module docstring for exactly what is and
isn't detected.

## Status

No live Jira/Confluence/SharePoint tenant has been used to test this code —
built against current API documentation with defensive response parsing.
Treat a live-credentialed verification pass as required before production
use. See each module's own docstring for exactly what was checked against
live docs versus general familiarity. Local directories and OneDrive, by
contrast, were exercised against a real local filesystem and a real MCP
stdio protocol round trip this session (search, fetch, an allowlist
violation, and a path-traversal rejection all observed as real tool
responses, including with `"code"`/`"pdf"` categories opted into a live
config) — see `todo.md`'s connectors section for the details. Real
`.docx`/`.xlsx`/`.pptx`/`.pdf` fixtures (built with the same libraries
that read them) back every office/PDF test — not mocked content.
