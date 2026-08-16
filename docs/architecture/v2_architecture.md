# AI SDLC Platform — V2 Architecture (Draft / Brainstorm)

**Document Owner:** Atlas (AI Architect Agent)

**Status:** DRAFT — brainstorm in progress. Nothing in this document is approved or scheduled for implementation. It exists to capture and organize an active brainstorm so it isn't lost, not to commit to a build order.

**Relationship to V1:** Additive only. Everything in [`v1_architecture.md`](./v1_architecture.md) remains true unless explicitly superseded here. V1's 4-node pipeline (`requirements → architecture → ux_design → development`), its 10-agent ownership model, its capability tiers, and its Public API contract are the foundation V2 extends — not a replacement.

**Target Audience:** Same as V1, plus whoever picks up any of the new agents below once one is actually scoped for a build.

---

## 1. Why V2

V1 shipped a fixed pipeline that answers one question well: *how does Nova turn a requirement into an approved code change?* Real usage plus a survey of comparable tooling ([github/awesome-copilot](https://github.com/github/awesome-copilot)) surfaced two structural gaps that sit outside that question:

1. **The pipeline has no concept of "this request doesn't need that step."** [`DEFAULT_WORKFLOW_NODES`](../../src/ai_sdlc/orchestration/langgraph_runner.py) is a flat, static list; every workflow walks every node in the same order. The one exception — skipping `ux_design` when Architecture says `requires_ui: false` — is a single hardcoded `if` in the runner, not a general mechanism (see §3).
2. **The 10-agent roster covers building a feature. It doesn't cover shipping it, keeping it healthy afterward, coordinating many features at once, or auditing Nova's own agents.** Everything below the fold in V1 stops at "human approves a diff."

V2 is the working answer to "what else belongs here, and how would it actually plug in."

---

## 2. New Agent Categories

Not every new agent idea fits the same shape as PO/Architecture/UX/Development. Four distinct shapes emerged during brainstorming:

| Category | Runs when | Fits inside `DEFAULT_WORKFLOW_NODES`? | Example |
|---|---|---|---|
| **A — Always-on pipeline node** | Every workflow, every time | Yes, as a new fixed node | ADR Agent |
| **B — Conditional pipeline node** | Only when a per-request condition is true | Yes, but needs the routing mechanism in §3 (doesn't exist yet) | GDPR/Compliance, Cost-Optimization |
| **C — Standing agent** | On its own schedule or on-demand, against the whole repo — not tied to any one feature request | No — needs a trigger outside the feature pipeline entirely | Janitor, Dependency/Supply-Chain |
| **D — Fleet-level coordinator** | Continuously, across *all* active workflows at once | No — operates above a single `WorkflowState`, needs new Public API surface (§5) | Scrum Master |

Category A is a small extension of what already exists. Category B requires a real design change to Orion (§3). Category C requires a new invocation path that bypasses the pipeline entirely. Category D requires a new capability at the Core/Orion layer that doesn't exist today, confirmed by reading the actual code — see §5.

---

## 3. Blast-Radius Routing (prerequisite for Category B)

Today's mechanism, verified against [`langgraph_runner.py:87-95`](../../src/ai_sdlc/orchestration/langgraph_runner.py):

```python
if nid == "ux_design" and self._architecture_says_no_ui():
    self.wf.stages[nid] = "skipped"
    continue
```

One node, one hardcoded field (`architecture.requires_ui`), checked by name. Notably, a more general design already exists in the codebase and was never adopted: [`orchestration/graph.py`](../../src/ai_sdlc/orchestration/graph.py)'s unused `GraphRunner` class has real `Transition(from_node, to_node, condition)` edges. `LangGraphRunner` took the simpler static-list approach instead when it was built.

**Proposed direction (not yet decided — see Open Questions):**

1. **A triage step**, likely folded into or run immediately after `requirements`, classifies the request against whatever fields Category-B nodes need to check (`touches_cloud_infra`, `handles_pii`, `change_size`, ...) and writes them into `wf.inputs`, the same way `architecture.requires_ui` already does today.
2. **Generalize the one hardcoded skip** in `LangGraphRunner.run()` into a per-node `applicable(wf.inputs) -> bool` check, evaluated for every node, not just `ux_design`.

This is the same "blast-radius routing" pattern already noted (and parked, brainstorm-only) against the external BMAD-METHOD framework review — this is the first place it would actually get built for Nova, not a new idea introduced by this document.

Nothing here is implemented. This section exists so Category B rows in §4 have something concrete to point at.

---

## 4. Expanded Agent Roster

| # | Agent | Category | Position in / relative to pipeline | Trigger | Proposed owner | Resolves |
|---|---|---|---|---|---|---|
| 11 | **ADR Agent** | A — always-on | New node, right after `architecture` | Always, once Architecture produces a real decision | Craft (specialist) | Matches [[bmad-method-reference]]'s ADR pattern, surfaced independently again via awesome-copilot's `adr-generator` |
| 12 | **Epic Breakdown Agent** | B — conditional | New node **between** `requirements` and `architecture` | Only for multi-part/large requests; skip for small self-contained ones | Craft (specialist, PO-adjacent) | Real gap: nothing today chunks a spec into right-sized units before Architecture/Forge touch it |
| 13 | **DevOps/Release Agent** | A — always-on | New node **after** `development` | Always, once a diff is approved | **Forge** (extends Coding ownership through to shipped, not a new owner) | Resolves ranked-todo item #2 ("push/PR-open trigger... unresolved which layer owns it") — proposed answer: Forge, via a `DeploymentCapability` seam mirroring `CodingCapability` |
| 14 | **Cost-Optimization Agent** | B — conditional | New node after `architecture`, Tier 2 (read-only) | Only if architecture output touches cloud infra | Craft (specialist, Architecture-adjacent) | New — cloud-cost review of proposed designs, not LLM-spend metering |
| 15 | **GDPR/Compliance Agent** | B — conditional | New node after `requirements`/`architecture` | Only if the feature touches personal data | *Open — Craft specialist vs. Aegis expansion, see §6* | Cleanest example of "most requests skip this entirely" |
| 16 | **Cloud-Architecture specialization** | Not a new agent | N/A — a persona/mode of the existing Architecture specialist | Selected when infra decisions are in scope | Craft (extends existing Architecture agent) | Avoids a redundant near-duplicate agent |
| 17 | **Janitor (Tech-Debt/Refactor)** | C — standing | Outside the pipeline | Periodic/on-demand sweep of the whole repo | Craft builds it; *trigger owner still open, see §6* | New — proactive debt-hunting, distinct from Forge (feature work) and Review (reactive PR review) |
| 18 | **Dependency/Supply-Chain Agent** | C — standing | Outside the pipeline | Periodic/on-demand | Aegis (security-adjacent) | New — dependency vuln/upgrade PRs, SBOM |
| 19 | **Agent Governance/Safety** | Cross-cutting, not a pipeline node | Audits Nova's *own* agents/skills/RAG ingestion | Continuous/periodic | Aegis (proposed expansion of its existing "AI security" bullet, not a new top-level owner) | Deepens Aegis's charter with concrete scope: prompt-injection defense on Sage's ingested content, tool-permission scope-creep auditing |
| 20 | **Project Onboarding Agent** | One-time bootstrap, its own shape (not A-D) | Runs once, before the pipeline ever starts for a project | Once per project, at `ai-sdlc init` time | *Open — see §6* | Same gap as V1's already-documented-but-unbuilt §9.1 Standards & Convention Context Layer |
| 21 | **Scrum Master Agent** | D — fleet coordinator | Above all workflows, not inside any one | Continuous | *Open — needs new Public API surface first, see §5* | New — nothing today has visibility across multiple concurrent workflows |

Rows 16 and 19 are deliberately *not* new top-level owners — they're proposed as extensions of existing ones (Architecture, Aegis), flagged as recommendations, not decisions.

---

## 5. Scrum Master Agent (Category D — the structurally new one)

This is the most architecturally different idea in this document, so it gets its own section.

**What it would do, end-to-end:**
- Aggregate status across every active workflow (not one) — what's in progress, what's blocked, what's done
- Detect impediments automatically — a workflow sitting in `WAITING_FOR_APPROVAL` or `WAITING_FOR_CLARIFICATION` far longer than typical, or one that's repeatedly hitting `retry`
- Generate standup-style status summaries
- Work with Epic Breakdown's output (row 12) and PO's output across *many* features to maintain a backlog view
- Potentially group work into sprint-like batches and track completion against them

**Why it doesn't fit the existing pipeline shape:** every other agent — including every Category A/B/C idea above — operates on one `WorkflowState` at a time, reachable through today's Public API (`start_workflow`, `get_workflow_status`, `submit_clarification`, `submit_approval`, `resume_workflow`, `cancel_workflow`, per V1 §5.2). I checked the actual implementation, not just the doc: [`src/ai_sdlc/orchestration/api.py`](../../src/ai_sdlc/orchestration/api.py) has no `list_workflows()` or any aggregate/multi-workflow endpoint today. A Scrum Master agent **cannot be built against the current API surface at all** — it needs a new read capability at the Core/Orion layer first (something like `list_workflows(filter?) -> WorkflowStatusData[]`), which is a real, scoped, `v1`-compatible (additive) API extension, not just a new agent file.

This is the one idea in this document with a concrete, verified blocker rather than just an open design question.

---

## 6. Open Questions

Mirrors V1 §20's format — these are genuinely undecided, not implied defaults:

1. **GDPR/Compliance ownership** — new Craft specialist (parallel to Testing/Security/Review/Documentation) or an expansion of Aegis's existing security charter? Aegis already owns "policy enforcement"; GDPR is policy, but it's product/legal policy, not infrastructure security.
2. **Janitor/Dependency-agent trigger ownership** — these need a scheduler or on-demand entry point outside `DEFAULT_WORKFLOW_NODES` entirely. Does that live in Orion (already owns orchestration broadly) or is it a new, small "Standing Agent Runner" that's simpler than the full graph runner?
3. **Project Onboarding: new persona or a new entry point into existing owners?** It could be a single new agent, or just a `ai-sdlc init`-time script that populates Sage's knowledge base and Craft's Standards Layer (§9.1) without being its own persona at all.
4. **Scrum Master's data boundary** — does it get read-only access to *all* workflows unconditionally, or does an initiator's workflow stay private unless they opt into fleet-level visibility? Not addressed yet.
5. **Blast-radius triage: separate node or folded into `requirements`?** §3 proposes a triage step but doesn't resolve where it lives — its own node (clean separation, one more hop) or additional output fields on the PO Agent (fewer moving parts, couples PO to knowing about every downstream optional agent).
6. **Which Category-B agent ships first, if any** — none of rows 12/14/15 are prioritized against each other or against V1's still-unstarted ranked items (diff-visibility fix, push/PR trigger — though row 13 proposes an answer to that one, eval harness, knowledge-base Phase 2).

---

## 7. Explicitly Not In Scope For This Draft

- Full Pydantic input/output contracts for any row in §4 (V1 §4's level of detail is intentionally not replicated here yet — premature for ideas still being brainstormed)
- Any code changes. Nothing in this document has been built.
- The GTM/product-marketing cluster surfaced during the awesome-copilot scan (positioning, investor comms, etc.) — out of scope for an SDLC platform, noted once and dropped.

---

*This document grows as the brainstorm continues. Add rows to §4, questions to §6, or new categories to §2 rather than starting a v3 — bump to a new version only once something here is actually approved for implementation, matching V1's own versioning rule (§5.3).*
