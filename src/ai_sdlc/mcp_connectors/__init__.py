"""Knowledge Base Tool Connectors -- Phase 1 (Jira, Confluence, SharePoint).

## What this package is

Three standalone **MCP (Model Context Protocol) servers** -- Jira
(`jira/`), Confluence (`confluence/`), SharePoint (`sharepoint/`) -- that
let any MCP-compatible AI client (Claude Desktop/Code, an org's own
agent framework, etc.) pull context from those external systems via two
tools per server: `search(query) -> [Document]` and `fetch(id) -> Document`.

Each connector ships its own console-script entry point
(`ai-sdlc-mcp-jira`, `ai-sdlc-mcp-confluence`, `ai-sdlc-mcp-sharepoint`,
see `pyproject.toml`) and its own optional dependency extra
(`ai-sdlc[jira]`, `ai-sdlc[confluence]`, `ai-sdlc[sharepoint]`). An org
can `pip install ai-sdlc[jira]` and run `ai-sdlc-mcp-jira` entirely on
its own, pointed at any MCP client, with **zero dependency on the rest
of this codebase's orchestration machinery** -- see the scope boundary
below.

## Explicit scope boundary (read before extending this package)

This phase builds *only* the three standalone MCP servers below it.
It deliberately does **not** touch, wire into, or depend on:

  - `ai_sdlc.capabilities` (in particular `RetrievalCapability` and its
    providers) -- these connectors are not `RetrievalCapability`
    providers and are not registered behind that interface.
  - `ai_sdlc.orchestration` / `ai_sdlc.agents` -- no workflow node, no
    specialist agent, calls into this package.
  - `ai_sdlc.cli` -- `cli/config.py`'s `CLIConfig` is explicitly scoped
    to CLI/workspace settings only (see its own docstring); connector
    config intentionally does not live there (see
    `mcp_connectors/common.py`'s `connectors_config_dir`).
  - Any "Sage-style" aggregator that would fan a single query out across
    all three connectors at once.

That aggregation/wiring work is a distinct, later phase (tracked in
`todo.md` under this pass's section) -- **deferred, not started, and not
implied by anything in this package**. Every module in this package is
therefore self-contained: `jira/`, `confluence/`, and `sharepoint/` each
import only `mcp_connectors/common.py` (and, for Jira/Confluence,
`mcp_connectors/atlassian/`) plus their own third-party SDKs, never each
other and never anything outside `ai_sdlc.mcp_connectors`. This is what
makes "install and run `ai-sdlc-mcp-jira` on its own" true in practice,
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
