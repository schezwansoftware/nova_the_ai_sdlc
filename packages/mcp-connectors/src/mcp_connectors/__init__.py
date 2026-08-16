"""Knowledge Base Tool Connectors -- Phase 1 (Jira, Confluence, SharePoint).

## What this package is

Three standalone **MCP (Model Context Protocol) servers** -- Jira
(`jira/`), Confluence (`confluence/`), SharePoint (`sharepoint/`) -- that
let any MCP-compatible AI client (Claude Desktop/Code, an org's own
agent framework, etc.) pull context from those external systems via two
tools per server: `search(query) -> [Document]` and `fetch(id) -> Document`.

Each connector ships its own console-script entry point
(`jira-mcp`, `confluence-mcp`, `sharepoint-mcp`, see `pyproject.toml`)
and its own optional dependency extra (`mcp-connectors[jira]`,
`mcp-connectors[confluence]`, `mcp-connectors[sharepoint]`). Anyone can
`pip install mcp-connectors[jira]` and run `jira-mcp` entirely on its
own, pointed at any MCP client, with **zero dependency on any other
project or orchestration system** -- see the scope boundary below.

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
and `sharepoint/` each import only `mcp_connectors/common.py` (and, for
Jira/Confluence, `mcp_connectors/atlassian/`) plus their own third-party
SDKs, never each other and never anything outside `mcp_connectors`. This
is what makes "install and run `jira-mcp` on its own" true in practice,
not just in principle.

## The precision requirement (applies identically to all three connectors)

Every connector enforces scope in **two** places, per the approved
design -- see each connector's `config.py`/`client.py` docstring for the
connector-specific mechanics:

  1. **Config-time hard allowlist**: the connector's config file declares
     exactly which projects (Jira) / spaces (Confluence) / sites
     (SharePoint) it may touch. Naming anything outside that list at
     query time is a hard error (`ConnectorConfigError`), never a
     silent scope widening.
  2. **Query-time native scope filter**: every actual search call passes
     the scope restriction as part of the *native query language itself*
     (JQL `project in (...)`, CQL `space in (...)`, Graph Search /
     SharePoint REST search `Path:"..."` clauses) -- never "fetch
     broadly, then filter client-side."

## No live credentials in this environment

None of the three connectors were exercised against a real, credentialed
Jira/Confluence/SharePoint tenant -- there is none available in the
environment this was built in. Every client module says explicitly, in
its own docstring, what was verified against current API documentation
(fetched live, not recalled from training data -- see each module) versus
defensively assumed. Response parsing throughout is defensive
(`dict.get`/`getattr` with fallbacks), mirroring
`capabilities/providers/claude_sdk.py`'s documented stance on the same
"verified against docs/installed SDK, not a live session" situation.
"""
