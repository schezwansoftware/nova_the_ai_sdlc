"""Knowledge Base Tool Connectors -- Phase 1 (Jira, Confluence,
SharePoint, Local Directories, OneDrive).

## What this package is

Five standalone **MCP (Model Context Protocol) servers** -- Jira
(`jira/`), Confluence (`confluence/`), SharePoint (`sharepoint/`), Local
Directories (`local_docs/`), OneDrive (`onedrive/`) -- that let any
MCP-compatible AI client (Claude Desktop/Code, an org's own agent
framework, etc.) pull context from those systems via two tools per
server: `search(query) -> [Document]` and `fetch(id) -> Document`.

The first three talk to a remote API (Jira/Confluence's REST APIs,
SharePoint's Graph/on-prem REST search). The last two -- added in a
later pass -- are pure local-filesystem access: `local_docs` searches
arbitrary allowlisted local directories, and `onedrive` reads a OneDrive
desktop client's already-synced local folder directly off disk rather
than calling Microsoft Graph (a deliberate choice to avoid the Azure AD
OAuth complexity `sharepoint`'s Online backend needed). Neither needs a
credential, keyring entry, or network call of any kind -- see
`local_fs/search.py`'s module docstring for the shared logic both are
built on.

Each connector ships its own console-script entry point
(`jira-mcp`, `confluence-mcp`, `sharepoint-mcp`, `local-docs-mcp`,
`onedrive-mcp`, see `pyproject.toml`) and its own optional dependency
extra (`mcp-connectors[jira]`, `[confluence]`, `[sharepoint]`,
`[local-docs]`, `[onedrive]`). Anyone can `pip install
mcp-connectors[jira]` and run `jira-mcp` entirely on its own, pointed at
any MCP client, with **zero dependency on any other project or
orchestration system** -- see the scope boundary below.

This package originated as a component of a larger AI-SDLC platform
project (internal name "Nova"/`ai_sdlc`), but was deliberately split out
into its own standalone repository specifically so it carries no import,
packaging, or naming dependency on that project -- not just a
"no runtime calls into it" guarantee, but no shared package namespace
either. Nothing in this codebase imports, mentions as a dependency, or
assumes the presence of that project.

## Explicit scope boundary (read before extending this package)

This phase builds *only* the three standalone MCP servers below it.
It deliberately does **not** touch, wire into, or depend on any
orchestration engine, agent framework, or capability-provider
abstraction from any other project. That kind of aggregation/wiring
work -- e.g. a service that fans a single query out across all three
connectors at once -- is out of scope for this package entirely; if
built, it belongs in the *consuming* system, not here.

Every module in this package is self-contained: `jira/`, `confluence/`,
`sharepoint/`, `local_docs/`, and `onedrive/` each import only
`mcp_connectors/common.py` (and, for Jira/Confluence,
`mcp_connectors/atlassian/`; for Local Docs/OneDrive,
`mcp_connectors/local_fs/`) plus their own third-party SDKs (`local_docs`
and `onedrive` need only `mcp` itself -- no third-party SDK at all),
never each other and never anything outside `mcp_connectors`. This is
what makes "install and run `jira-mcp` on its own" true in practice, not
just in principle.

## The precision requirement (applies identically to all five connectors)

Every connector enforces scope in **two** places, per the approved
design -- see each connector's `config.py`/`client.py` docstring for the
connector-specific mechanics:

  1. **Config-time hard allowlist**: the connector's config file declares
     exactly which projects (Jira) / spaces (Confluence) / sites
     (SharePoint) / directories (Local Docs, OneDrive) it may touch.
     Naming anything outside that list at query time is a hard error
     (`ConnectorConfigError`), never a silent scope widening.
  2. **Query-time scope filter**: for Jira/Confluence/SharePoint, every
     actual search call passes the scope restriction as part of the
     *native query language itself* (JQL `project in (...)`, CQL
     `space in (...)`, Graph Search / SharePoint REST search `Path:"..."`
     clauses) -- never "fetch broadly, then filter client-side." Local
     Docs/OneDrive have no remote API to delegate this to, so the
     equivalent guarantee is enforced directly: every file's *real*,
     symlink-resolved path is verified to be inside an allowlisted
     directory before its content is ever read or returned -- see
     `local_fs/search.py`'s module docstring for the full design.

## No live credentials/tenant for Jira/Confluence/SharePoint; nothing to credential for Local Docs/OneDrive at all

None of Jira/Confluence/SharePoint were exercised against a real,
credentialed tenant -- there is none available in the environment this
was built in. Every one of those three client modules says explicitly,
in its own docstring, what was verified against current API
documentation (fetched live, not recalled from training data -- see each
module) versus defensively assumed. Response parsing throughout is
defensive (`dict.get`/`getattr` with fallbacks), mirroring
`capabilities/providers/claude_sdk.py`'s documented stance on the same
"verified against docs/installed SDK, not a live session" situation.

Local Docs and OneDrive are different: there's no credential or tenant
to have missing in the first place, so a real local filesystem and a
real MCP stdio protocol round trip *were* exercised end to end this
session -- see `local_docs/`'s and `onedrive/`'s own module docstrings
and `todo.md`'s "Nexus — Local Directories & OneDrive Connectors" section
for exactly what was verified and what's still a documented gap (V1's
plain-text-only file scope, and OneDrive's Files-On-Demand
cloud-placeholder detection not covering macOS).
"""
