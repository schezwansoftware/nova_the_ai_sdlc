# Pending Work

Tracked from the code review of the Specialist Agent Layer (Craft), branch
`agents/craft-specialist-agent-layer` (commit `ee8f925`), reviewed 2026-08-10.
Update/remove items as they're resolved.

## Craft — real Anthropic ReasoningCapability provider (this pass, branch `agents/craft-reasoning-anthropic-provider`)

- [x] `AnthropicReasoningProvider` (`src/ai_sdlc/capabilities/providers/reasoning_anthropic.py`)
      added: real `ReasoningCapability` provider backed by the Anthropic
      Messages API's forced tool-use structured output. Verified against
      the actually-installed `anthropic` package (0.121.0, in a disposable
      venv) rather than assumed from memory/docs — see that module's own
      docstring for exactly what was introspected (client/`.messages.create`
      signature, tool/tool_choice shape, `ToolUseBlock.input` already being
      a parsed dict, the `APIError`/`AnthropicError` exception hierarchy,
      and the surprising fact that `anthropic.Anthropic()` does **not**
      itself fail fast on a missing API key — this provider enforces that
      itself at `__init__` instead).
- [x] `get_default_reasoning_provider()` (`src/ai_sdlc/capabilities/providers/reasoning_factory.py`)
      added and wired into `SpecialistAgent.__init__` (`agents/framework.py`),
      replacing the hardcoded `MockReasoningProvider()` fallback. Selection
      was originally via a reasoning-specific `AI_SDLC_REASONING_PROVIDER=
      anthropic|mock` (default `mock`) env var, **not**
      `--coding-provider`'s per-workspace-config shape — justified at the
      time by "Copilot has no equivalent single-completion API," so
      reasoning was kept off the shared `AI_SDLC_AGENT_FRAMEWORK` switch
      `coding_factory.py`/`retrieval_factory.py` use.
      **Corrected** (branch `agents/craft-reasoning-copilot-unify`): that
      justification was wrong on both counts. `CopilotReasoningProvider`
      (`src/ai_sdlc/capabilities/providers/reasoning_copilot.py`) now
      exists — it doesn't need a literal single-call completion endpoint,
      it uses the same "bounded agentic session, one task, prompt-instruct
      structured output" technique `retrieval_copilot.py` already
      established, just extracting an arbitrary caller-supplied schema
      out of a fenced JSON block instead of a fixed summary+sources
      shape. And `reasoning_factory.py` now reads `AI_SDLC_AGENT_FRAMEWORK`
      — the exact same variable `coding_factory.py`/`retrieval_factory.py`
      read — instead of its own separate variable, so `ai-sdlc init`'s one
      "which AI agent framework" choice actually governs every AI call the
      platform makes, reasoning included, not just coding/codebase-lookup.
      `AI_SDLC_REASONING_PROVIDER` was removed entirely (no deprecated
      alias kept — nothing deployed depends on it). `"claude"` still means
      `AnthropicReasoningProvider` for reasoning specifically (Anthropic's
      Messages API remains the right real backend for a single-call
      reasoning completion regardless of which framework name selects it);
      `"copilot"` now means `CopilotReasoningProvider`. See
      `reasoning_factory.py`'s module docstring for the full account, and
      `reasoning_copilot.py`'s for what was verified against the same
      installed `github-copilot-sdk==1.0.9` package `coding_copilot.py`/
      `retrieval_copilot.py` already introspect (notably: `available_
      tools=[]` at session-create time is a structural, verified-not-
      guessed "zero tools available" guarantee — stronger than either of
      those two providers' kind-based permission allow/deny approach,
      since there's no concrete builtin tool-name string to mis-guess when
      the allowlist is simply empty). The original per-workspace-config
      reasoning above (why an environment variable at all, not a direct
      `CLIConfig` read) was correct and is preserved, just no longer tied
      to a reasoning-specific variable.
- [x] ~~**Aegis follow-up gap now has a real, not just hypothetical,
      target:**~~ still applies, now against either real provider: none of
      `MockReasoningProvider`'s existing prompt-injection-sanitization gap
      (no sanitization of repository/requirement content before it reaches
      the reasoning capability) is addressed by `AnthropicReasoningProvider`
      or `CopilotReasoningProvider` either — both pass `prompt` straight
      through. Still explicitly out of scope for Craft (Aegis owns
      prompt-injection protection per the architecture doc's ownership
      table), but worth flagging as higher-priority now that opting into
      either real provider is one environment variable away.
- [ ] `anthropic` added as an optional extra (`pyproject.toml`,
      `pip install ai-sdlc[anthropic]`) rather than a base dependency, kept
      consistent with the mock-is-the-hard-default posture. Not yet wired
      into any packaging/release/CI matrix that actually installs extras
      and exercises the real provider end-to-end against a live key — this
      pass's tests all go through the injected-fake-client seam
      (`tests/test_reasoning_anthropic_provider.py`), same posture as
      `ClaudeAgentSDKProvider`'s own tests. A real, credentialed
      integration test (skipped by default, opt-in via env var) would be a
      reasonable follow-up for whoever owns CI/release configuration.
      `reasoning_copilot.py` reuses the existing `copilot` extra (no new
      extra needed — same `github-copilot-sdk` dependency
      `coding_copilot.py`/`retrieval_copilot.py` already require) and is in
      the same boat: `tests/test_capabilities_reasoning_copilot.py` exercises
      real installed SDK classes at the permission/prompt/parsing level, but
      no live, authenticated `create_session()`/`send_and_wait()` round-trip
      has been run against it.

## Craft — UX DesignCapability follow-up (this pass, branch `agents/craft-ux-design-capability`)

- [ ] **Scope note, not a bug:** The UX Agent's `ux_specification` field
      (personas/navigation/components/states/validation) illustrated in
      `docs/architecture/v1_architecture.md` section 4's `UXAgentOutput`
      example was deliberately **not** implemented in this pass — only
      `visual_designs`/`design_package_status` were added. The task scope
      for this pass was specifically "progressive lo-fi/mid-fi/hi-fi
      visual design artifacts alongside the existing structured UX spec"
      (i.e. alongside the already-shipped `UXOutputData` text fields), not
      a second breaking addition. If a future pass wants the richer
      `ux_specification` object too, scope it separately — it has no
      DesignCapability dependency and would go through
      `ReasoningCapability` like the rest of `UXOutputData`.
- [ ] **Fidelity progression is caller-driven, not agent-tracked:** Per
      the architecture doc's "UX Revision & Feedback Loop", a real
      progressive workflow advances through LO_FI -> MID_FI -> HI_FI
      across separate *revision* invocations, driven by `.ai-sdlc/ux.json`
      state (`current_fidelity`) that only Core/Orion own. The UX Agent
      stays stateless, so this pass exposes an optional
      `request.inputs["fidelity_levels"]` override (defaulting to all
      three fidelities every call) rather than implementing progression
      itself. Whoever wires the UX Agent into the real revision loop
      (Orion/Core) needs to set `fidelity_levels` per revision call based
      on `ux.json`'s `current_fidelity` — the agent will not infer this on
      its own.

## Craft — cleanup before merge

- [ ] **Dead code:** `ClarificationNeeded` exception in
      `src/ai_sdlc/agents/framework.py` is defined and caught in
      `SpecialistAgent.execute()`, but never raised anywhere — neither
      `POAgent` nor `ArchitectureAgent` uses it; both signal ambiguity by
      returning a string from `check_needs_clarification()` instead. No
      test exercises it either. Remove the exception class and the
      `try/except` around `check_needs_clarification()` in `framework.py`,
      keep the plain string-return contract. (Speculative dual signaling
      path with no current or planned use — not worth keeping "just in
      case.")

## Craft (PO Agent) — clarification-resume bug found by Pixel's CLI integration tests (2026-08-10)

- [x] **`POAgent._effective_text` can never actually resolve a
      clarification requested on the workflow's first node when driven
      through the real public API.** `_effective_text` prefers
      `inputs["requirement_text"]` over `inputs["clarification_answer"]`
      whenever `requirement_text` is non-empty
      (`src/ai_sdlc/agents/po/po_agent.py:74`). Its docstring assumes
      `requirement_text` is *absent* on a clarification resume ("the
      Orchestrator does not currently re-inject prior stage inputs on
      resume") — true for later nodes, but false for PO itself:
      `OrchestratorAPI.start_workflow` sets
      `wf.inputs["requirement_text"]` once and nothing ever clears it, so
      it is still present (and still ambiguous) on every resume. Verified
      end-to-end: `ai-sdlc start --prompt "TBD, not sure yet, figure out
      later."` → `ai-sdlc answer "<any non-ambiguous answer>"` gets a
      `200`/`success: true` response from `submit_clarification`, but the
      workflow comes right back with a **new** `question_id` and the
      *exact same* clarification question — an unresolvable loop for any
      initiator whose very first requirement trips PO's ambiguity
      heuristic. (`test_workflow_full_sequence.py`'s clarification
      coverage doesn't catch this because it interrupts the *second* node
      — Architecture — via a stub agent that never depends on
      `requirement_text`/`clarification_answer` precedence, and
      `test_platform_api_server.py`'s clarification tests construct
      `WorkflowState` directly with no `requirement_text` in `inputs` at
      all, so the fallback branch is what they exercise.) Fix belongs to
      whoever owns `_effective_text`'s precedence (Craft) and/or the
      resume input-merge semantics it assumes (Orion) — not the CLI, which
      only calls the public API and cannot special-case this. Pixel's own
      CLI clarification-flow test therefore uses a stub agent on the
      Architecture node (mirroring `test_workflow_full_sequence.py`)
      instead of the real PO ambiguity path — see
      `tests/test_cli_contract.py::clarification_stub_server`.

  **Resolved** (branch `agents/craft-po-clarification-fix`,
  `src/ai_sdlc/agents/po/po_agent.py::_effective_text`): flipped the
  precedence to prefer `clarification_answer` over `requirement_text`
  whenever the answer is present, instead of the reverse. Safe because PO
  is only ever invoked once-then-resumed in this graph (it never runs
  again once the workflow advances past `requirements`), so a present
  `clarification_answer` on a PO request unambiguously means "this is my
  own resume." Fixed at the Craft layer only — Orion's `wf.inputs`
  accumulation behavior (never clearing `requirement_text` on resume) was
  left as-is; the precedence flip was the smaller, correct fix without
  also needing to change Orion's cumulative-inputs design. Verified two
  ways: a unit test resuming `POAgent.execute()` with both
  `requirement_text` (still the original ambiguous string) and
  `clarification_answer` present simultaneously (`test_po_agent.py::
  test_clarification_answer_resolves_ambiguity_even_though_requirement_text_is_still_present`),
  and an end-to-end test through the real public API with no stub agents
  (`test_workflow_full_sequence.py::
  test_clarification_on_first_node_resolves_instead_of_looping_forever`),
  plus a manual re-run of the exact repro above (now completes instead of
  re-asking). Pixel's CLI still uses a stub agent for its own
  clarification-flow test (`clarification_stub_server` above) rather than
  the real PO path — that's an intentional, still-fine CLI test-isolation
  choice (proving the CLI's `answer` mechanics work against any
  well-behaved node), not a sign this fix is incomplete.

## Orion / Core — UX_DESIGN wiring follow-up (this pass)

- [ ] **Persistence not built:** No `.ai-sdlc/ux.json` artifact manifest,
      no `.ai-sdlc/artifacts/ux/` payload storage, and no approval/revision
      endpoint wiring exist yet (explicitly out of scope for Craft per the
      "UX Artifact Persistence Model" and "UX Revision & Feedback Loop"
      sections of the architecture doc). The UX Agent's `AgentResult.data`
      (including the new `visual_designs`/`design_package_status` keys)
      is available to be persisted, but nothing currently writes it to
      `.ai-sdlc/`.

## Nexus — follow-up

- [ ] **Real design provider not built:** `MockDesignProvider`
      (`src/ai_sdlc/capabilities/providers/design_mock.py`) is the only
      `DesignCapability` implementation. A real vendor/multimodal/
      image-generation provider, and any future Figma-native adapter, is
      Nexus's `integrations/design_provider.py` — not started, not
      Craft's job per the architecture doc's Design Capability Adapter row
      (`docs/architecture/v1_architecture.md` section 3).

## Aegis — follow-up (design capability)

- [ ] None of `MockDesignProvider`'s generated content (artifact
      descriptions, synthetic `payload_ref`s) is sanitized against prompt
      injection from repository content, matching the existing
      `MockReasoningProvider` gap below. Also unaddressed: the
      "Design Artifact Validation" and "Prompt & Input Sanitization"
      requirements in `docs/architecture/v1_architecture.md` section 10
      (validating generated artifacts for type/size/embedded-secret safety
      before persistence) have no implementation yet — only Pydantic shape
      validation (`DesignResponse`/`DesignArtifact`) exists today. Fine
      while only the deterministic mock provider exists; must be addressed
      before any real vendor provider is wired in behind `DesignCapability`.

## Pixel — CLI (this pass, branch `agents/pixel-cli`)

The `ai-sdlc` CLI now exists under `src/ai_sdlc/cli/` (`main.py`,
`handlers.py`, `formatters.py`, `client.py`, `schemas.py`, `config.py`,
`bootstrap.py`), built on `typer` + `rich`, talking to Core's HTTP API
exclusively (`ai_sdlc.platform.server`) — no orchestration/agents imports
anywhere in the package except `bootstrap.spawn_server`, which shells out
to `python -m ai_sdlc.platform.server` as a subprocess rather than
importing it. All seven documented commands (`init`, `start`, `status`,
`answer`, `approve`, `reject`, `cancel`) are implemented and covered by
`tests/test_cli_contract.py`, which drives them through a real
`run_platform_server` instance.

- [x] `.ai-sdlc/agents/*.json` scaffolding is now written by `ai-sdlc
      init` (`src/ai_sdlc/cli/bootstrap.py::write_agent_metadata`),
      resolving the item this section used to track. It writes exactly the
      po/architecture metadata blocks previously listed here, plus a third
      block for the now-real `ux` agent:

  ```json
  {
    "agent_id": "ux",
    "version": "1.0",
    "impl": "ai_sdlc.agents.ux.ux_agent.UXAgent",
    "input_schema": "ux-input-v1",
    "output_schema": "ux-output-v1",
    "capabilities": ["reasoning", "design"],
    "state_artifact": "ux.json"
  }
  ```

  Note `agent_id` is `"ux"`, not `"ux_design"` — matches
  `DEFAULT_WORKFLOW_NODES`' `agent_id` key in
  `orchestration/langgraph_runner.py`, not the public `WorkflowPhase`
  name. Scaffolding is idempotent (an existing file is never overwritten,
  so a hand-edited or custom agent metadata file survives re-running
  `init`).

- [x] ~~No packaging/console-script entry point.~~ **Resolved** (commit
      `d55002a`, see the project-level item below): `ai-sdlc <command>` now
      works directly after `pip install -e .`.
- [ ] **`resume_workflow()` has no CLI command.** Not in the architecture
      doc's §12 command list, so out of scope for this pass, but the
      public API supports it (e.g. resuming a `FAILED`-adjacent or
      manually-paused workflow with no pending HITL interaction). Worth a
      command if a real use case shows up. Note this is unrelated to
      `start`'s interactive loop below — that loop never needed
      `resume_workflow()`, since `submit_clarification`/`submit_approval`
      already auto-advance through every already-completed stage
      server-side (see `LangGraphRunner.run()`).
- [ ] **`ai-sdlc init`'s `.ai-sdlc/` scaffolding is agent-registry
      metadata only, not full workflow-state initialization.** The
      architecture doc's §12 wording ("Initializes `.ai-sdlc/` state
      folder") could be read as more than this; in practice
      `StateStore.__init__` already creates the rest of `.ai-sdlc/`'s
      structure (workflows/, audit/, approvals/, etc.) lazily on first
      write, so there's nothing else for `init` to pre-create.
- [ ] **`--start-server` spawns a background process the CLI doesn't
      track for later shutdown.** No `ai-sdlc stop`/pid-file bookkeeping
      exists (not in the required command list); a user who used
      `--start-server` has to kill the process themselves. Worth a
      pid-file if this becomes a real workflow.

## Pixel — CLI interactive loop (this pass, branch `agents/pixel-cli-interactive-loop`)

Implements `docs/architecture/v1_architecture.md` §12.1: `ai-sdlc start`
now drives a workflow to completion in one continuous session instead of
returning after the first stage. `handlers.run_start` hands off to a new
`_drive_workflow_interactively` loop that repeatedly calls `get_status`
and, for each `WAITING_FOR_CLARIFICATION`/`WAITING_FOR_APPROVAL` it sees,
prompts inline (`console.input`) and submits the answer/decision via the
existing `submit_clarification`/`submit_approval` client calls, then loops
again. No new server endpoint or client method was needed — each of those
calls already runs `LangGraphRunner` forward through every already-completed
stage on its own, stopping only at the next interrupt or a terminal state;
the CLI loop just has to react to wherever the server stops it.

The loop halts on `COMPLETED`/`FAILED`/`CANCELLED` (truly terminal) or
`REVISION_REQUIRED` (rejection halts automatic progress by design, per
§12.1 step 4 — `formatters.py` now also renders an explanatory panel for
this status, previously unhandled). `status`/`answer`/`approve`/`reject`
are unchanged and still work as one-shot escape hatches mid-loop.

- [x] **§20.6 open question (Ctrl-C / non-interactive behavior) resolved
      for this pass, pending revisit if it needs to be a real decision
      later:** non-interactive sessions (`sys.stdin.isatty()` false, e.g.
      CI) never block on input — the loop stops at the first pending
      action and prints the escape-hatch commands instead of prompting.
      Ctrl-C (or Ctrl-D/EOF) mid-prompt leaves the workflow exactly where
      the server already has it (paused on its pending clarification/
      approval) and exits 0 with a "resume with `answer`/`approve`/
      `reject`" hint, rather than attempting a cancel — cancelling on an
      unconfirmed interrupt felt more surprising than just leaving state
      as-is. No `--no-wait` flag was added; not clearly needed yet since
      the non-TTY path already covers the CI case without one.
- [ ] The interactive prompts (`_prompt_and_submit_clarification`,
      `_prompt_and_submit_approval` in `handlers.py`) are plain
      `console.input()` loops with minimal validation (non-empty answer,
      y/n for approval). No multi-line answer support, no readline history
      beyond whatever the terminal already provides. Fine for V1; revisit
      if clarification answers need to be long-form.

**Follow-up in this same pass:** `start` now also asks for the requirement
itself interactively when `--prompt` is omitted, rather than requiring the
flag up front — step zero of the same loop, not a separate feature.
`_resolve_requirement_interactively` (`handlers.py`) prints a startup
banner (`formatters.render_banner`: name, version, description, command
list — pulled from a new `ai_sdlc.cli.version.CLI_VERSION`, sourced via
`importlib.metadata` so it can't drift from `pyproject.toml`) then prompts
`Define your requirement, or paste a path to a requirements.txt file:`.
The input is treated as a file path if it resolves to an existing file
(read and stripped), otherwise as literal requirement text; either way it's
re-prompted (client-side, no server round-trip) if the result is under the
same 10-character minimum `StartWorkflowRequest` enforces. The CLI also
gained a top-level `--version` flag and a fuller `--help` description.
Same non-interactive guard as the rest of the loop: with no TTY and no
`--prompt`, `start` fails fast with the `--prompt "<requirement>"` hint
instead of blocking on input that will never arrive.

The banner itself was redone once already: the first version was a plain
`rich.Panel` of text (name/version/description in a box), which the user
found "poorly designed." Replaced with a "NOVA" wordmark in a top-to-bottom
cyan-to-blue gradient (`formatters._BANNER_ART`, figlet "doom" font,
generated via `pyfiglet.figlet_format("NOVA", font="doom")` then hardcoded
as a tuple of literal strings -- **do not hand-edit these lines**,
regenerate from pyfiglet if the wordmark ever needs to change, since the
glyph shapes depend on exact space/backslash alignment per line) followed
by the version/tagline/description/commands underneath, unboxed. `pyfiglet`
was used only to generate the string at dev time -- it is **not** a new
runtime dependency; `pyproject.toml` is unchanged.

- [ ] Only plain text / file-path requirement input is supported — no
      editor handoff (`$EDITOR`) for composing a long requirement inline,
      no stdin-pipe mode (e.g. `cat req.txt | ai-sdlc start`) distinct from
      the file-path convenience. Worth adding if requirements commonly
      don't fit on one line comfortably typed at a `>` prompt.

## Forge — CodingCapability follow-up (lower priority, PRs #14/#15/#16 merged 2026-08-11)

Non-blocking loose ends from the Claude Forge / Copilot Forge `CodingCapability`
work. Worth picking up once the Developer Agent itself gets scoped, not before.

- [x] ~~**No `revision_feedback`-equivalent field on `CodingRequest`.**~~
      **Resolved** (branch `agents/forge-developer-agent`): confirmed the
      likely answer above was right — `coding.py`'s canonical interface
      stays as-is (no new field), and `DeveloperAgent._build_coding_request`
      (`src/ai_sdlc/agents/developer/developer_agent.py`) folds
      `wf.inputs["revision_feedback"]` into `task_summary` before calling
      `execute()`. That input key itself needed a small Orion-side fix
      first — see "Forge — Developer Agent" below.
- [ ] **What triggers push + PR-open after human approval is unresolved.**
      Both providers stop at "committed locally, not pushed" by design (§4
      gates this on approval) — but nothing in §3/§4 says what the actual
      trigger mechanism is: a second `CodingCapability` call, a separate
      capability method, or something Nexus-owned. Flagged explicitly by
      Copilot Forge rather than guessed. **Still open** even now that the
      Developer Agent exists (branch `agents/forge-developer-agent`,
      deliberately scoped to "stop at approved diff" — see that section
      below): the approved worktree/branch is left on disk specifically for
      whoever picks this up next.
- [ ] **Neither real provider has been verified against a live, authenticated
      session** — both are implementation-against-real-installed-types (or,
      for Claude, docs-only) with no working credentials available in the
      environment they were built in. `claude_sdk.py`'s `ResultMessage`
      field access is defensive (`getattr` with fallbacks) pending a real
      install; `coding_copilot.py`'s wiring tests exercise real SDK classes
      but never a real `create_session()`/`send_and_wait()` round-trip. Worth
      a follow-up pass with real credentials before either provider is
      trusted in anger, not before.
- [ ] **`github-copilot-sdk` requires Python 3.11+**, stricter than this
      repo's `pyproject.toml` floor (`>=3.10`) and stricter than the ambient
      `python3` in the environment this was built in (3.9.6). Added as an
      optional `[project.optional-dependencies].copilot` extra rather than
      bumping the base floor, so it's not urgent, but the base-floor/CI story
      needs a real look before the Copilot provider is anything more than
      optional.

## Forge — Developer Agent (this pass, branch `agents/forge-developer-agent`)

The Developer Agent itself, previously the acknowledged frontier ("the
next real gap is the Developer Agent" — see prior sessions' notes),
now exists: `src/ai_sdlc/agents/developer/developer_agent.py`, wired as
the graph's fourth node (`development`, after `ux_design`) in
`DEFAULT_WORKFLOW_NODES`, registered in both
`AGENT_METADATA`/`write_agent_metadata` (`cli/bootstrap.py`, so real
`ai-sdlc init` actually scaffolds it, not just tests) and every test
fixture that drives a full workflow. Deliberately scoped to **"stop at
approved diff"**: it creates an isolated git worktree, calls
`CodingCapability`, and requests human approval for the diff through the
existing generic approval gate — it does not push the branch or open a
PR (see the still-open item above).

- [x] **Isolated worktree lifecycle**
      (`src/ai_sdlc/agents/developer/worktree.py`, new module) — nothing
      previously created the isolation `CodingCapability`'s real providers
      assume already exists; both `claude_sdk.py`/`coding_copilot.py` only
      ever *verify* `working_tree_path`, never create it. Worktrees live at
      `<workspace>.ai-sdlc-worktrees/<node_id>/<workflow_id>`, a sibling of
      the target repo rather than nested inside it (nothing adds
      `.ai-sdlc/` to the target repo's own `.gitignore` yet, so nesting
      there would make the live checkout's `git status` noisy). Re-entry
      always resets to `base_branch` rather than trying to detect and
      preserve prior state — see that module's docstring for why every
      remaining re-entry case (a retryable provider failure, or a rejected
      approval retried with feedback) means "redo," never "reuse."
      `sweep_orphaned_worktrees` exists as a crash-recovery safety net but
      is **not wired into any automatic trigger yet** (no periodic sweep,
      no CLI-startup call) — a real follow-up, not done here.
- [x] **A real, previously-undiscovered Orion bug, found and fixed before
      building on top of it**: approval-resume used to *re-invoke* the
      requesting agent from scratch (`LangGraphRunner.run()` re-matching
      `wf.current_stage`), correct for cheap reasoning-only agents but
      unsafe for a Tier 3 agent — re-running `CodingCapability.execute()`
      after approval could silently produce a *different* diff than the
      one a human actually approved. Fixed in `orchestration/orchestrator.py`
      / `orchestration/langgraph_runner.py`
      (`LangGraphRunner.resume_after_approval`, new): the approved
      `AgentResult.data` is now persisted onto `wf.pending_approval` and
      merged onto `wf.inputs` on approval, advancing past the node instead
      of re-invoking it — mirroring the clarification-resume path's
      existing (correct) pattern. Required updating one existing test's
      asserted behavior (`test_langgraph_integration.py::
      test_approval_acceptance_resumes_exactly_once_without_recursion`,
      which previously asserted re-invocation as correct) and fixing a
      second, independent stub-generator helper in `test_cli_contract.py`
      (`_write_interrupt_once_stub_agent`) that had the same missing-`data`
      gap and only surfaced once a real multi-approval CLI flow existed to
      exercise it.
- [x] **`wf.inputs["target_repository"]["workspace_path"]` now actually
      gets populated** (`OrchestratorAPI.start_workflow`,
      `orchestration/api.py`) — previously dead-but-harmless input key that
      only `ArchitectureAgent._gather_codebase_context()` read (see Sage's
      follow-up entry below); nothing ever set it. Closes that gap for both
      call sites at once, not just the Developer Agent's.
- [ ] **UX handoff gating is deliberately incomplete.** §6's rule ("the
      Developer Agent stage cannot begin unless `design_package_status ==
      APPROVED`") isn't enforced — only "`ux_design` is present" is
      checked. Enforcing the stricter rule today would make the Developer
      Agent permanently unreachable, since the UX artifact
      persistence/approval-gating this depends on (directly above, "Orion /
      Core — UX_DESIGN wiring follow-up") still doesn't exist. Tighten once
      that lands.
- [ ] **Standards Context Layer (§9.1) still has zero implementation
      anywhere in this codebase.** `DeveloperAgent` passes
      `standards_instructions=""`/`standards_skills=[]` unconditionally, and
      the V1 allow-list/self-check defaults it falls back to
      (`git`/`mvn`/`gradle`/`npm`/`pytest`; skip self-check when no
      build/test commands are given) are hardcoded module constants, not
      read from any per-workspace/per-tech-stack config. Both are meant to
      be superseded by a real Standards Layer once one exists, per §9.1 and
      Open Question 7's own documented answer in `coding.py`.
- [ ] **Push/PR-open follow-up pass** (see the still-open item above) needs
      to also delete the approved worktree once it successfully pushes —
      that responsibility was deliberately left to it rather than built
      speculatively here.
- [ ] **MEDIUM PRIORITY — the human approving a Development change cannot
      actually see the diff.** Found via direct user review of this pass,
      not caught before merge. `DeveloperAgent`/`CodingResult` compute real
      data (`files_changed`, `branch_name`, `summary`,
      self-check pass/fail) and it does reach `wf.inputs["development"]`
      server-side, but nothing carries it past that point:
        - `PendingAction` (`orchestration/api.py`) only has a generic
          `prompt_message` ("approval requested") and
          `payload_artifact_path` — a hardcoded string like
          `.ai-sdlc/implementation.json` that **no code ever writes**, since
          artifact persistence was never built (same underlying gap as "Orion
          / Core — UX_DESIGN wiring follow-up" above, just for Development's
          own artifact instead of UX's).
        - The CLI's approval panel (`cli/formatters.py::
          pending_action_renderable`) only ever renders `prompt_message` +
          `payload_artifact_path` — so a developer sees "Approval requested"
          and a path to a file that doesn't exist, with zero visibility into
          which files changed or what the change actually does, and is asked
          to blindly `ai-sdlc approve`/`reject`.
        - The isolated worktree's real path also isn't surfaced anywhere
          (not in `CodingResult`, not in any API response), so a developer
          who wanted to manually `cd` in and run `git diff` themselves has no
          documented way to find it either.
      `coding.py`'s own module docstring already says `CodingResult.summary`
      is meant to be "surfaced to the human at the approval gate ... alongside
      the diff" — this is a wiring gap against that stated intent, not a new
      requirement. Fix needs: thread the real diff data through
      `PendingAction`/`WorkflowStatusData` (or a new field), render it in the
      CLI's approval panel instead of the fake artifact path, and decide
      whether to show a real `git diff` / worktree path too. Explicitly
      deferred — user flagged this as important but not urgent, medium
      priority, do not start without being asked.

## Nexus — Knowledge Base Tool Connectors, Phase 1 (this pass, branch `agents/nexus-knowledge-base-connectors`)

**Correction, found via direct user review after the first version of this
pass (which nested the package at `src/ai_sdlc/mcp_connectors/`) was
already open as a PR: that layout wasn't actually independent of Nova at
the packaging level, only at the runtime-call level.** Every internal
import was absolute and rooted at `ai_sdlc.mcp_connectors.*`, so a bare
copy of just the connectors folder failed with `ModuleNotFoundError: No
module named 'ai_sdlc'` outside that exact package structure — confirmed
by actually testing the bare-copy scenario in a venv that never had
`ai-sdlc` installed, not assumed. Restructured to live at
**`packages/mcp-connectors/`** instead — a fully separate, sibling
top-level package with its own `pyproject.toml`/`README.md`/`INSTALL.md`,
its own Python package name (`mcp_connectors`, no `ai_sdlc` prefix
anywhere), and its own console-script names (`jira-mcp`/`confluence-mcp`/
`sharepoint-mcp`, renamed from `ai-sdlc-mcp-*`). Still lives in this same
repo (a monorepo-with-independent-sub-packages layout, not a separate git
repo — that was tried first and reverted as unnecessary overhead once the
real requirement turned out to be import/packaging independence, not
repository independence) but Nova's own `pyproject.toml` has zero
reference to it — no shared dependency, no shared package namespace.
Re-verified from a completely fresh venv after the move: `pip install -e
"packages/mcp-connectors[all,dev]"` alone (nothing else pre-installed)
passes all 139 tests and resolves all three console scripts.

Three **standalone MCP (Model Context Protocol) servers** exist under
`packages/mcp-connectors/src/mcp_connectors/` — Jira, Confluence,
SharePoint — each independently installable/runnable with **zero
dependency on Nova at all**, not just on its orchestration machinery.
This closes the "Jira/Confluence Enterprise Connectors are still entirely
deferred" item under Sage's follow-up below — **partially**, see that
item's own updated note for exactly how far, since the connectors
existing and being wired into Nova's own agent framework are two
different things and only the former happened this pass.

Built on the official standalone `mcp` Python SDK
(`mcp.server.fastmcp.FastMCP`, stdio transport), deliberately **not**
`claude_agent_sdk`'s in-process `create_sdk_mcp_server` helper — that one
can't run as its own standalone process, which is exactly why the
approved design rejected it. Each connector ships its own console-script
entry point (`jira-mcp`, `confluence-mcp`, `sharepoint-mcp`) and its own
optional `pyproject.toml` extra (`jira`, `confluence`, `sharepoint`),
mirroring the existing `copilot`/`anthropic` extras' "real integration is
opt-in, mock/nothing is the hard default" convention — now inside
`packages/mcp-connectors/pyproject.toml`, its own independent dependency
surface, not Nova's root `pyproject.toml`.

- [x] **Shared scaffolding** (`mcp_connectors/common.py`): one `Document`
      result model (`id`/`title`/`snippet`/`source`/`url`/
      `last_modified`/`container`/`metadata`) every connector's
      `search`/`fetch` tools return; a self-contained `ConnectorError`
      hierarchy (`ConnectorConfigError`/`ConnectorAuthError`/
      `ConnectorAPIError`) that deliberately does **not** reuse
      `ai_sdlc.capabilities`' `ProviderError`/`MalformedResponseError`,
      per the approved design's "must work with zero dependency on the
      rest of this codebase" requirement; `enforce_allowlist`, the
      config-time half of the precision requirement, shared by all
      three; and real `keyring`-backed credential storage
      (`store_secret`/`get_secret`/`delete_secret`/`CredentialRef`) — a
      connector's JSON config file only ever holds a `(service,
      username)` reference, never a raw secret.
- [x] **Self-contained MCP tool-error contract, verified against the
      installed package, not assumed**: read `mcp==1.29.0`'s own source
      (`mcp/server/lowlevel/server.py::Server.call_tool`,
      `mcp/server/fastmcp/tools/base.py::Tool.run`) to confirm any
      exception raised inside a `@server.tool()` function is already
      converted into a real `CallToolResult(isError=True, ...)` MCP
      response — no bespoke translation layer needed at that boundary.
      Proven end to end, not just read about:
      `packages/mcp-connectors/tests/test_servers.py::
      test_allowlist_violation_becomes_a_real_mcp_tool_error` drives the
      actual low-level protocol handler and asserts `isError is True`
      with the raised exception's message in the response content.
- [x] **The precision requirement, enforced in two places for all three
      connectors, per the approved design**:
      1. Config-time hard allowlist (`allowed_projects`/`allowed_spaces`/
         `sites`) — naming anything outside it raises
         `ConnectorConfigError` before any request is built, verified by
         tests that assert the injected fake HTTP client is never even
         called for a disallowed container.
      2. Query-time native scope filter — Jira JQL `project in (KEY1,
         KEY2) AND text ~ "..."`, Confluence CQL `space in
         ("KEY1","KEY2") AND text ~ "..."`, SharePoint Graph Search
         `Path:"<site url>"` clauses (Online) / `_api/search/query`
         `Path:"<site url>*"` clauses (Server on-prem) — never "fetch
         broadly, then filter client-side." Even each connector's
         `fetch(id)` goes through this same scoped-query mechanism
         rather than a raw unscoped by-id GET, **with one flagged
         exception**: SharePoint Online's `fetch()` calls Graph's direct
         `GET /drives/{id}/items/{id}` (Graph Search's `queryString` has
         no reliable "match this exact item id" clause the way JQL/CQL
         do), so scope is verified *after* that fetch instead
         (`_verify_item_in_scope`) — documented explicitly in
         `online_client.py`'s docstring as the one deliberate departure
         from "always query-scoped," with the structural reason why, not
         silently glossed over.
- [x] **Jira & Confluence share an Atlassian HTTP client module**
      (`mcp_connectors/atlassian/auth.py`) even though they ship as
      separate MCP server processes — one `AtlassianSiteConfig` model,
      one `build_auth_headers` covering all three approved auth methods
      (`cloud_api_token` — Cloud, Basic email+token;
      `data_center_pat` — Data Center 8.14+/7.9+, Bearer;
      `data_center_basic` — older Data Center, Basic username+password),
      cross-validated at config-load time (e.g. `cloud_api_token` against
      `deployment_type: data_center` is rejected immediately, not
      discovered later as a confusing auth failure).
- [x] **A real, live-docs-verified deviation from the approved design,
      flagged rather than silently absorbed**: the brief states Jira/
      Confluence's project/space scoping syntax is identical across
      Cloud and Data Center so query-construction logic doesn't need to
      fork, only base URL/auth do. True for Confluence. **Not fully true
      for Jira**: live web research (2026-08-16) found Atlassian fully
      removed Jira Cloud's classic `GET/POST /rest/api/{2,3}/search`
      bulk-search endpoints between May–October 2025, migrating Cloud to
      a new endpoint, `POST /rest/api/3/search/jql`
      (`nextPageToken`-paginated). This is a Cloud-only backend-scaling
      migration — Data Center is unaffected and still runs classic
      `POST /rest/api/2/search` (`startAt`-paginated). So Jira's
      `search()`/`fetch()` *do* fork on deployment type for which
      endpoint carries the request (`jira/client.py::_search_request`)
      — the allowlist-scoped JQL string itself is still built by one
      shared, unforked function (`build_jql`). Documented in detail in
      `jira/client.py`'s module docstring, including the sources
      checked. A second, smaller consequence of the same Cloud
      migration: `/rest/api/3/search/jql` is a `v3` endpoint, and Jira
      `v3` issue `fields.description` is Atlassian Document Format (a
      nested JSON node tree), not the plain string `v2`/Data Center
      returns — `jira/client.py::_extract_description_text` walks both
      shapes defensively (best-effort ADF text extraction, not a full
      renderer).
- [x] **SharePoint: both backends built, per the explicit instruction
      not to defer either.**
      - **Online** (`sharepoint/online_client.py`): Microsoft Graph
        Search API (`POST /search/query`, `entityTypes: ["driveItem"]`),
        Azure AD app-registration client-credentials OAuth2 (headless,
        no interactive user — this is a server process), in-memory
        token caching with an expiry margin. `Path:"..."` KQL clauses
        for site scoping, verified via live web search against
        Microsoft Learn's own Graph Search examples.
      - **Server on-prem** (`sharepoint/onprem_client.py`): classic
        `_api/search/query` REST — genuinely no Graph involvement at
        all, confirmed structurally (Graph has no on-prem auth
        equivalent). NTLM auth via `requests_ntlm.HttpNtlmAuth`
        (installed and directly inspected in this environment:
        `requests-ntlm==1.3.0`, a real working dependency, added as
        `pyproject.toml`'s new `sharepoint` extra's `requests`/
        `requests-ntlm` entries — `requests`, not `httpx`, since NTLM
        support integrates with `requests` far more commonly in the
        Python ecosystem) or Basic auth (ADFS-fronted/basic-auth-enabled
        deployments). **Kerberos explicitly not implemented** — needs
        system Kerberos ticket infrastructure and native-extension
        packages (`requests-kerberos`/`pykerberos`/`gssapi`) that
        commonly fail to build without system headers present, and there
        was no domain-joined environment to verify it against even if
        built. Flagged as a real, scoped-out gap, not guessed at.
      - Selected per configured site via an explicit, required
        `deployment_type: "online" | "server"` field (a Pydantic
        discriminated union, `SharePointOnlineSiteConfig` |
        `SharePointServerSiteConfig`) — **never auto-detected from the
        URL**, exactly as specified (vanity Online domains and hybrid
        ADFS-joined Server deployments make that unreliable). Built and
        tested Online first, then Server, per the specified sequencing.
      - `sharepoint/client.py`'s `SharePointClient` facade dispatches to
        whichever backend a site declares and enforces the site
        allowlist (SharePoint's allowlist unit is the configured site
        itself — see that module's docstring for why that differs from
        Jira/Confluence's "one site, many allowlisted projects/spaces"
        shape). Document ids are composed as `"<site_url>::<backend
        id>"` at the facade boundary (never inside either backend) since
        `fetch(id)` alone has no other way to know which of potentially
        several configured sites/backends an opaque id belongs to.
- [x] **A real bug found and fixed during development, not just in
      review**: `onprem_client.py`'s `fetch()` originally tried to reuse
      `build_onprem_kql`'s site-scoping clause via string-slicing
      (`build_onprem_kql("*", [site_url])[1:]`) spliced onto an exact-path
      clause — produced a KQL string with mismatched parentheses
      (`'Path:"..." AND *) AND (Path:"...")'`). Caught by a smoke test
      run against the real client logic before any pytest test was even
      written, not by a human reviewer; fixed by composing the two
      clauses directly instead of splicing strings.
- [x] **A second real bug, a genuine breaking upstream dependency change,
      found only by actually installing the package into a disposable
      venv** (this codebase's established "verify against real installs"
      practice, not just reading docs): the `mcp` PyPI package shipped
      a breaking `2.0.0` release, current as of this writing, that
      removes/relocates the high-level `FastMCP` server class every
      connector's `mcp_server.py` imports
      (`mcp.server.fastmcp.FastMCP` doesn't exist under that path in
      2.0.0 — renamed/restructured to `mcp.server.mcpserver.MCPServer`,
      an unrelated API this codebase has not been ported to). The
      original unconstrained `mcp>=1.2.0` floor in `pyproject.toml`
      would have silently resolved to 2.0.0 on a fresh install and
      failed to import. Fixed with an explicit `<2.0.0` upper bound on
      all three extras, documented in `pyproject.toml`'s own comment.
      Every connector here was built and tested against the installed
      `mcp==1.29.0` specifically; porting to `mcp>=2.0.0` is a real,
      scoped-out follow-up.
- [x] **Result capping**: `DEFAULT_RESULT_LIMIT = 15`,
      `MAX_RESULT_LIMIT = 50` (`common.py`) — every connector's config
      has a `result_limit` field (`Field(ge=1, le=MAX_RESULT_LIMIT)`,
      config-validation error if exceeded, never a silent clamp).
- [x] **Tests**: 139 new tests across eight files
      (`tests/test_mcp_connectors_{common,atlassian_auth,jira,
      confluence,sharepoint_online,sharepoint_onprem,sharepoint_client,
      servers}.py`) — full suite went from 397 passed/3 skipped to 536
      passed/3 skipped, zero regressions. Every HTTP-touching test uses
      an injected fake (`httpx.MockTransport` for Jira/Confluence/
      SharePoint Online, a hand-rolled fake `requests.Session`-shaped
      object for SharePoint Server) exercising real request-construction
      logic (real JQL/CQL/KQL/Graph-query strings, real header
      construction) — never a hand-wave over "and then it calls the
      API." Credential tests monkeypatch the module-level `keyring`
      reference rather than touching a real OS keychain during test runs.

**No live credentials anywhere in this environment** — none of the three
connectors were exercised against a real, credentialed Jira/Confluence/
SharePoint tenant. Every client module's docstring says explicitly what
was verified against current, live-fetched API documentation (Jira/
Confluence/Graph Search endpoints — see the deviation notes above) versus
what's carried over from general documentation familiarity without a
fresh live-docs check this session (flagged honestly:
`onprem_client.py`'s classic Search REST JSON response shape, the one
place in this pass that wasn't independently re-verified against live
docs — treat it as needing the most scrutiny in a live-verification
follow-up). Response parsing is defensive throughout (`dict.get`/
`getattr` with fallbacks), mirroring `capabilities/providers/
claude_sdk.py`'s established stance for the same situation.

**Explicitly deferred, not started, matching the approved design's scope
boundary**:

- [x] **Phase 2 (Sage-style aggregator + agent-framework wiring)** —
      nothing in this pass touches `RetrievalCapability`, the
      orchestrator, or any specialist agent.
      **Design locked 2026-08-16, implemented 2026-08-17** — see
      "Sage — Phase 2 Knowledge Consumption" below for the complete,
      now-built design (superseded the "aggregator" framing entirely: Sage
      runs an isolated sub-session per question instead of pre-fetching/
      merging across connectors). All of the open questions this bullet
      originally raised (how an agent reaches connectors, how a workspace
      configures which are active, whether it shares
      `AI_SDLC_AGENT_FRAMEWORK`) are answered and implemented there.
- [ ] Kerberos auth for SharePoint Server (see above).
- [ ] Porting to `mcp>=2.0.0` (see above).
- [ ] No credential-provisioning CLI/wizard — an operator runs
      `store_secret(...)` by hand (documented in each connector's
      `config.py` docstring) or calls `keyring.set_password` directly.
      Reasonable for this pass's scope; a real onboarding flow would
      probably want one.
- [ ] No pagination beyond the first page for any connector — every
      `search()` call is a single top-N request (`result_limit`), by
      design (a "sensible default result limit, not unbounded" per the
      approved design), but there's no way to page further into a larger
      result set from the MCP tool interface itself if a caller wanted
      to.

## Sage — Phase 2 Knowledge Consumption (this pass, branch `agents/sage-phase2-context-wiring`) — IMPLEMENTED 2026-08-17

The design locked below (2026-08-16) is now built, end to end, exactly as
designed — no deviation from the shape, only two upgrades to what was
originally flagged as unverified (see "Verified, not guessed" at the end of
this section). New: `capabilities/sage.py` (`SageRequest`/`SageResponse`/
`SageCapability`/`SageMemoryEntry`/`normalize_context_query`),
`capabilities/connector_resolver.py` (`ConnectorResolver`, framework-agnostic,
zero SDK imports), `capabilities/providers/{sage_mock,sage_claude,sage_copilot,
sage_factory}.py`. Modified: `agents/base.py` (`AgentStatus.NEEDS_CONTEXT`,
`AgentResult.context_query`), `agents/{po,architecture,ux}/schemas.py`
(`needs_context`/`context_query` fields + mutual-exclusivity validator against
`needs_clarification`), `agents/{po,architecture,ux}/prompts.py` (`sage_context`
rendering + `OUTPUT_STRUCTURE` teaching the model when to use which flag),
`agents/framework.py`/`agents/ux/ux_agent.py` (detection), `capabilities/
providers/mock.py` (`MockReasoningProvider(trigger_needs_context=True)` test
hook), `orchestration/state.py` (`sage_memory.json` read/write),
`orchestration/orchestrator.py` (the actual NEEDS_CONTEXT handling loop —
by far the largest single change, ~230 lines in `invoke_agent_for_stage`),
`cli/bootstrap.py`/`cli/handlers.py` (`connectors.json` scaffolding + the
`ai-sdlc init` checklist prompt). 103 new tests across 8 new + 2 extended
test files, all passing (full suite: 500 passed / 4 skipped, zero
regressions against the pre-existing 397). Verified against the actually-
installed `claude-agent-sdk==0.2.139`/`github-copilot-sdk==1.0.9` in this
environment (`/opt/anaconda3/bin/python3`), not docs-only assumption — see
"Verified, not guessed" below.

**Two scope decisions made explicitly by the user before this pass started**
(the design below left both open): (1) both Claude and Copilot get a real
`SageCapability` provider in this pass, not Claude-only/mock-only; (2) PO,
Architecture, and UX get `needs_context` — Developer Agent does not (its
output comes from `CodingCapability`, not a `ReasoningCapability`-validated
schema with a `needs_clarification`-shaped field to mirror; wiring it in
would need a separate mechanism, left as a flagged follow-up below).

Full original design brainstorm for how Nova's own specialist agents actually
consume the 5 MCP connectors shipped above — preserved below with each bullet
marked `[x]` and annotated with what actually shipped, since the design
survived implementation unchanged in every load-bearing respect:

- [x] **Sage runs each question in its own isolated sub-session — not a
      pre-fetch aggregator, and not shared tools on the calling agent.**
      Rejected two other shapes first: (1) a Sage aggregator that fans a
      query out to every connector, merges/dedups/ranks before the agent
      even starts thinking — too much bespoke code duplicating what an
      agentic tool-use loop already does, and wasteful (fetches from every
      source regardless of relevance); (2) giving the calling agent's own
      reasoning session direct MCP tool access — checked against the real
      code (`capabilities/reasoning.py` + `reasoning_anthropic.py` +
      `reasoning_copilot.py`) and confirmed `ReasoningCapability.complete()`
      is **structurally zero-tool by deliberate design** (Copilot provider
      passes `available_tools=[]`, Anthropic provider forces a single
      `tool_choice`) — an explicit anti-prompt-injection guarantee that
      giving PO/Architecture/UX/Developer live tool access would break.
      **What's locked instead**: the calling agent never touches
      connectors directly. It asks Sage a plain-language question; Sage
      spins up its own separate, isolated, bounded agentic session (same
      "bounded session, one task, structured output" shape
      `reasoning_copilot.py` already established) with MCP tools attached
      *only inside that sub-session*. Only the final distilled, cited
      answer crosses back — never the raw tool-call transcript — keeping
      the calling agent's own context window untouched by search noise.
      **Implemented as designed**: `SageCapability.ask()`
      (`capabilities/sage.py`) is the only entry point; `SageClaudeProvider`/
      `SageCopilotProvider` each drive their own bounded session (mirroring
      `retrieval_claude.py`/`reasoning_copilot.py`'s existing patterns).
      `Orchestrator` (`orchestration/orchestrator.py`) is the *only* caller —
      PO/Architecture/UX never import anything Sage-related; they only ever
      set `needs_context`/`context_query` on their own structured output.
- [x] **Tool wiring must be framework-agnostic, not Claude-only.** One
      shared, provider-agnostic resolver (new small module, plain data, no
      SDK imports) turns "this workspace's enabled connectors" into MCP
      server specs + resulting tool names; each framework's provider
      (`claude_sdk.py` today, `coding_copilot.py`/`reasoning_copilot.py`
      today, any future framework) does only a thin last-step translation
      into its own SDK's native tool-config mechanism. Closes the gap
      `INSTALL.md` §3b already flagged (`claude_sdk.py` never passes
      `mcp_servers`), generalized so a future framework only needs the thin
      adapter, never re-solving "which connectors, what config."
      **Not yet verified**: whether Copilot's SDK exposes an allowlist step
      the same way Claude's does — check the installed SDK before assuming
      symmetry. **Now verified** — see "Verified, not guessed" below;
      Copilot's mechanism turned out *more* granular than Claude's, not less.
      **Implemented as designed**: `ConnectorResolver.resolve()`
      (`capabilities/connector_resolver.py`) reads `connectors.json` and
      returns plain-data `ConnectorLaunchSpec`s with zero SDK imports;
      `SageClaudeProvider` maps a spec to `McpStdioServerConfig` +
      `mcp__<name>__<tool>` allowed-tools strings, `SageCopilotProvider` maps
      the same spec to `MCPStdioServerConfig` with its own per-server
      `tools:` field — each provider's mapping is the only framework-specific
      code; `connectors.json`'s shape and parsing exist exactly once.
- [x] **New per-project connector-enablement config**, e.g.
      `.ai-sdlc/connectors.json` — deliberately separate from `CLIConfig`
      and from `AI_SDLC_AGENT_FRAMEWORK` (different questions: which LLM
      runs Nova's own agents vs. which knowledge sources Sage can reach).
      Declared at `ai-sdlc init` via a multi-select checklist (mirrors the
      existing agent-framework arrow-key menu). Per-project, not global.
      Stores only which connector IDs are on + how to launch each MCP
      server — does **not** duplicate each connector's own fine-grained
      scoping (allowed projects/spaces), which stays in that connector's
      own existing config file untouched. "Declare now, configure later" is
      fine (matches the already-shipped two-step auth model). No
      enable/disable CLI command for V1 — hand-edit the file or re-run
      init. A connector enabled but never properly configured is **skipped
      quietly** when Sage tries to use it (not a hard error), with the skip
      itself noted in the visible log below.
      **Implemented as designed**: `.ai-sdlc/connectors.json`
      (`schema_version: "connectors-v1"`, one entry per known connector name
      with `enabled`/`command`/`args`/`env`), scaffolded by
      `cli/bootstrap.py::write_connectors_config` (idempotent/
      non-destructive, same convention as `write_agent_metadata`) and
      declared via a new `questionary.checkbox` multi-select prompt
      (`cli/handlers.py::_resolve_connectors_interactively` — the first
      multi-select interaction in this CLI, every prior prompt was
      single-select) wired into `run_init` right after agent-metadata
      scaffolding. `command` is never auto-derived (Nova can't know where an
      operator installed the independent `packages/mcp-connectors`); an
      enabled-but-unconfigured connector is skipped quietly by
      `ConnectorResolver`, never a hard error, exactly as specified.
      Cancelling just the connectors checklist (Ctrl-C) is deliberately
      **non-fatal** to the rest of `init` — a judgment call beyond what the
      design doc specified, justified by "declare now, configure later"
      already treating zero connectors as a normal, expected state.
- [x] **Orion mediates via the already-shipped `needs_clarification`
      pattern, generalized — not a live tool inside reasoning.** Reuses the
      exact schema mechanism from PR #31 (`needs_clarification`/
      `clarification_question`), generalized to a second flag (e.g.
      `needs_context`/`context_query`) a worker's own structured output can
      set when it judges it's genuinely missing something. Preserves the
      zero-tool guarantee above (it's a schema field, not a live tool call)
      while still making "ask for help" driven by the worker's own
      per-task judgment rather than a blanket rule applied to every
      invocation — this is what delivers "only when genuinely necessary"
      instead of pulling unnecessary context on every run. Orion (which
      already owns pause/resume for clarification/approval interrupts) sees
      the flag, checks local memory (see below), asks Sage only if memory
      came up empty, then resumes the worker with the answer merged into
      its inputs — same resume shape as clarification, except this
      resolves **automatically**, no human involved, since Sage is
      answering, not a person. **Orion stays a pure, dumb messenger** — it
      never needs to know connectors exist or which one to check; all
      routing knowledge lives inside Sage alone, reaffirmed explicitly
      after considering (and rejecting) having the caller hint at which
      connector is relevant.
      **Implemented as designed, with one real simplification found during
      implementation**: `Orchestrator.invoke_agent_for_stage`
      (`orchestration/orchestrator.py`) checks `StateStore.read_sage_memory()`
      first, calls `self.sage.ask(...)` only on a miss, then re-invokes the
      same agent with the answer merged into `inputs["sage_context"]` —
      structurally the *clarification*-resume shape (re-invoke), not the
      *approval*-resume shape (reuse stored data), since PO/Architecture/UX
      haven't done real expensive work yet when they ask. **The
      simplification**: because this resolves entirely synchronously within
      one `invoke_agent_for_stage` call, it needs **no persisted
      `pending_context` record at all** — unlike `pending_clarification`/
      `pending_approval`, which exist specifically because those flows must
      survive a separate HTTP round-trip. `wf.status` never leaves `RUNNING`.
      A separate `context_rounds` counter/`max_context_rounds = 3` budget
      (never touching `attempts`/`wf.retry_count`, since a context round is
      not a failure) bounds a worker that keeps asking; exceeding it once
      lets the worker proceed with a caveat, exceeding it *again* fails the
      workflow (`needs_context_loop_exceeded`) as a genuine bug signal.
- [x] **Full, structured, visible logging — for humans now and Sentinel
      later.** Explicit user requirement: every state change must be
      visible, not silent, even though this flow never blocks waiting for a
      human. Every step (worker's request → memory check result → Sage
      invocation → Sage's answer + source + duration → worker resuming)
      gets written as a **structured** record (not prose) into the
      existing `.ai-sdlc/audit/` mechanism (confirmed to already exist via
      `orchestration/state.py`) — full detail, not a summary, since a
      summary would be useless for Sentinel's later analysis ("is local
      memory saving time," "which connector is dead weight," "does asking
      Sage correlate with better outcomes"). The same live CLI status
      display already used for "Thinking... Ns..." during real LLM calls
      should narrate this flow in real time too (e.g. "Asking Sage about
      X... 12s... Sage found an answer (source: Confluence)").
      **Implemented, with the CLI-narration half explicitly deferred**:
      every one of the 5 named steps (`context_requested`,
      `context_memory_check`, `sage_invoked`/`connector_skipped`,
      `sage_answered`/`sage_failed`, `context_resolved`) is written via
      `StateStore.append_audit_event` into the existing `.ai-sdlc/audit/
      events.jsonl`, following that file's established ad-hoc-dict
      convention (no new typed schema). **Live CLI narration was not
      built**: `cli/handlers.py::_call_with_thinking` only animates one
      blocking HTTP call from the CLI process itself — there is no
      streaming/SSE/polling primitive between the CLI and the Core Platform
      API server today for it to narrate what's happening *inside* a
      server-side `invoke_agent_for_stage` call. The audit log is the
      source of truth for after-the-fact visibility instead; real-time
      narration is a real, scoped-out follow-up (would need new
      streaming infrastructure, not a small addition).
- [x] **Local memory, owned by Sage — no new agent.** The "keep a running
      memory of what's been learned" job belongs to Sage (already owns
      "Knowledge/RAG... context engineering" per the ownership table), not
      a new team member. Written immediately the moment Sage successfully
      answers something — no periodic consolidation/cleanup process for V1,
      matching this project's consistent "no indexing/sync infra until
      proven necessary" philosophy already used for `RetrievalCapability`
      and all 5 connectors. Checking memory stays a **cheap, plain lookup**
      — explicitly not a search index/vector store/another agentic
      session, since the whole point is the common case has to be
      near-free. Each saved entry keeps its **source and save-date**
      alongside the answer (a fact without provenance can't later be
      judged trustworthy); Sage is selective about what's worth saving, not
      everything it ever returns; older entries are treated as *possibly
      stale*, not permanent truth, since a cached Jira/Confluence answer
      can go stale after the source changes.
      **Implemented as designed**: `StateStore.read_sage_memory`/
      `write_sage_memory_entry` (`orchestration/state.py`) — one JSON
      object at `.ai-sdlc/sage_memory.json` (not one-file-per-entry the way
      clarifications/approvals are, since a single small file *is* the
      "cheap plain lookup," and read-modify-write happens under one
      `_locked(exclusive=True)` block). Keyed by `normalize_context_query()`
      (`capabilities/sage.py`) — whitespace-collapsed, lowercased,
      exact-match only, deliberately not fuzzy/semantic. Only `found=True`
      answers are ever written; a miss is never cached (caching "nothing
      found" would block a later, differently-configured connector set from
      ever being retried for the same query). No TTL/staleness enforcement
      — `saved_at` is exposed as data for the worker's own prompt to weigh,
      not an enforced expiry.
- [x] **Five follow-up decisions, all approved:**
      1. **Safety of retrieved content** — relies on connectors already
         being **read-only by construction** (none can write/delete) as the
         primary, already-true mitigation. The deeper "sanitize retrieved
         text against hidden injected instructions" gap is explicitly
         **not** solved here — it's the same pre-existing gap already
         flagged against `MockReasoningProvider` elsewhere in this file
         (Aegis's eventual job), just newly relevant to Sage too. Not made
         worse, not fixed.
      2. **Sage finds nothing anywhere** (memory miss + every relevant
         connector empty) — reported back as a normal, valid "nothing
         found" result, not an error; the worker's own next reasoning pass
         decides whether to proceed with a caveat or escalate to a real
         human clarifying question via the existing mechanism. Deliberately
         not hardcoded as a fixed rule in Orion.
      3. **A connector is configured correctly but fails live** (timeout,
         expired credential, outage) — already covered by the existing,
         verified MCP error contract (a real exception already comes back
         as a clean `isError: true` tool result, not a crash, confirmed
         against installed `mcp` package behavior in Phase 1). Sage's own
         task instructions just need to say "don't treat one failed source
         as fatal, try what's left."
      4. **Connector routing** — stays entirely Sage's own judgment call
         every time, never hinted at by the caller or known by Orion.
      5. **Which AI framework Sage itself runs on** — inherits the same
         single `AI_SDLC_AGENT_FRAMEWORK` setting as the rest of the
         platform; no separate Sage-specific choice unless real evidence
         later shows one framework is meaningfully better at tool-use/
         search tasks specifically.

**Verified, not guessed** — both `claude-agent-sdk==0.2.139` and
`github-copilot-sdk==1.0.9` are actually installed in this environment
(`/opt/anaconda3/bin/python3`, confirmed via `pip show`), letting every MCP
wiring claim below be checked against real installed types rather than docs:

- `ClaudeAgentOptions.mcp_servers: dict[str, McpStdioServerConfig | ...] |
  str | Path` and `ClaudeAgentOptions.cwd: str | Path | None = None` (both
  confirmed via `inspect.signature`) — resolves `packages/mcp-connectors/
  INSTALL.md` §3b from "a real, scoped, currently-unbuilt follow-up" to
  verified-buildable; `McpStdioServerConfig`'s `TypedDict` shape
  (`type`/`command`/`args`/`env`) matches `ConnectorLaunchSpec` field-for-
  field.
- `CopilotClient.create_session(...)` genuinely accepts `mcp_servers:
  dict[str, MCPServerConfig] | None`; `MCPStdioServerConfig` has a
  **per-server** `tools: list[str]` field ("`[]` means none, `'*'` means
  all", per the SDK's own source comment) — **more granular than Claude's
  one flat session-wide `allowed_tools`**, resolving this section's one
  explicitly-flagged unknown ("not yet verified: whether Copilot's SDK
  exposes an allowlist step the same way Claude's does") in Sage's favor,
  not against it. `PermissionRequestMcp` (`copilot.generated.session_events`)
  is real, carrying `kind="mcp"`/`server_name`/`tool_name`/`read_only`,
  confirming `coding_copilot.py`'s pre-existing `_KIND_TO_TOOL_NAMES["mcp"]
  = ("Mcp",)` mapping was accurate.
- **One genuinely new, still-unresolved unknown** (not present in the
  original design): whether Copilot's `available_tools=[]` (the mechanism
  `reasoning_copilot.py` uses for its own structural zero-tool guarantee)
  would *also* suppress `mcp_servers`-derived tools, or only non-MCP
  builtin tools — not determinable from the installed package's Python-side
  types alone (the resolution logic is server-side in the Copilot CLI
  binary). `SageCopilotProvider` takes the conservative path: leaves
  `available_tools`/`excluded_tools` unset and relies on the
  `on_permission_request` handler (approve every `kind=="mcp"` call, reject
  everything else) as the primary scoping mechanism instead of layering an
  unverified allowlist on top.

**Explicitly deferred / out of scope this pass** (flagged, not attempted):

- [ ] **Developer Agent's `needs_context`** — per the user's own scope
      decision for this pass. `DeveloperAgent` subclasses `Agent` directly,
      not `SpecialistAgent`, and its output comes from `CodingCapability`,
      not a `ReasoningCapability`-validated schema — there's no
      `needs_clarification`-shaped field to attach a sibling flag to
      without a larger redesign.
- [ ] **Live CLI real-time narration** of the Sage sub-flow ("Asking Sage
      about X... found via Confluence") — see the logging bullet above for
      why (no streaming/SSE/polling primitive between the CLI and the Core
      Platform API server exists today).
- [ ] **Memory staleness/TTL enforcement** — entries carry `saved_at` but
      nothing expires them automatically.
- [ ] **Fuzzy/semantic memory-key matching**, **a `connectors.json`
      enable/disable CLI command**, **duplicating a connector's own
      fine-grained scoping into `connectors.json`** — all explicitly ruled
      out by the locked design for V1, still true.
- [ ] **Sanitizing retrieved connector content against embedded
      prompt-injection payloads** — pre-existing Aegis gap (see follow-up
      decision #1 above), not solved here, not made worse.
- [ ] **Tightening Copilot's MCP permission handler beyond "approve every
      `kind=='mcp'` call"** — `PermissionRequestMcp` carries `read_only`,
      so a future pass could gate more precisely (e.g. only auto-approve
      read-only tool calls); V1 trusts the connectors' read-only-by-
      construction guarantee uniformly.
- [ ] **A real, pre-existing, unrelated bug found and flagged while
      touching `build_prompt` for `sage_context` threading, not fixed
      here**: `ArchitectureAgent.build_prompt()`/`UXAgent.build_prompt()`
      never surface `inputs["clarification_answer"]` on a
      clarification-resume today — only `POAgent._effective_text` does
      this correctly (see that fix, above, in the Craft PO Agent section).
      A real Architecture/UX clarification round-trip currently discards
      the user's answer and re-reasons over an unchanged prompt. Worth its
      own follow-up ticket.
- [ ] **No live, credentialed end-to-end verification** — no live Jira/
      Confluence/SharePoint/Local Docs/OneDrive credentials, and no
      authenticated Claude/Copilot session, exist in this environment.
      Every new provider is verified against injected fakes (mirroring
      every other provider in this codebase's existing test convention)
      plus, for the SDK wiring itself, direct introspection of the real
      installed packages (see "Verified, not guessed" above) — never a
      real `mcp_servers`-driven tool call end to end. A follow-up pass with
      real credentials and a real workspace's `.ai-sdlc/connectors.json`
      pointed at an actually-installed `packages/mcp-connectors` venv would
      be the natural next verification step.

**No longer open**: what Sentinel (the eval/QA agent, still fully unbuilt)
actually *does* with these logs once they exist — that remains unaddressed,
but the logs themselves now exist and are real, structured data (see the
audit-logging bullet above), not a hypothetical for Sentinel's eventual
consumption to be designed around later.

## Nexus — Local Directories & OneDrive Connectors, Phase 1 (cont'd) (this pass, branch `agents/nexus-local-onedrive-connectors`)

Two more standalone MCP servers added to `packages/mcp-connectors/` --
**`local-docs-mcp`** (arbitrary local directories) and **`onedrive-mcp`**
(a OneDrive desktop client's already-synced local folder) -- alongside the
existing Jira/Confluence/SharePoint three. Built directly inside the
existing `packages/mcp-connectors/` package from the start (not nested
under `src/ai_sdlc/` and then moved, learning from that earlier
Jira/Confluence/SharePoint pass's own correction -- see the "Nexus —
Knowledge Base Tool Connectors, Phase 1" section above); every new import
is `from mcp_connectors.X import Y`, zero `ai_sdlc` references, matching
the existing three connectors exactly.

- [x] **Both connectors are pure local-filesystem access -- no cloud API,
      no OAuth client, no credential, no keyring, no network call of any
      kind.** OneDrive reads the OneDrive desktop client's already-synced
      local folder directly off disk rather than calling Microsoft Graph
      -- an explicit, deliberate design choice by the project owner
      specifically to avoid the Azure AD OAuth complexity the existing
      SharePoint Online connector needed (`sharepoint/online_client.py`'s
      client-credentials OAuth2 flow). Not built here, on purpose, not a
      shortcut taken under time pressure.
- [x] **One shared local-filesystem module, two thin connectors on top**
      -- mirrors how Jira/Confluence already share `atlassian/auth.py`.
      `mcp_connectors/local_fs/search.py` holds the actual
      directory-walk/text-search/path-safety logic; `local_docs/` and
      `onedrive/` are each the usual `config.py`/`client.py`/
      `mcp_server.py` shape on top of it, with their own console-script
      entry points (`local-docs-mcp`, `onedrive-mcp`) and their own
      `pyproject.toml` extras (`local-docs`, `onedrive` -- each just
      `mcp>=1.2.0,<2.0.0`, no `keyring`/`httpx`/`requests` at all, since
      neither needs an HTTP client or auth library). Both extras added to
      `all` too.
- [x] **The precision requirement, adapted for local files -- this was the
      most important part, and the part with real security teeth**:
      1. **Config-time hard allowlist of directories**
         (`allowed_directories` on each connector's config model) --
         every configured directory is resolved via `Path.resolve(
         strict=True)` in a `pydantic` `field_validator` at config-load
         time; a typo'd or not-yet-existing directory fails config
         loading immediately with a clear error, never discovered later
         as a confusing "found nothing" search result. Reuses
         `common.py`'s existing `enforce_allowlist` at query time for the
         allowlist-subset check (`search(query, directories=[...])`),
         exactly like Jira/Confluence use it for project/space keys --
         no reinvention there.
      2. **Query-time path-safety enforcement -- genuinely new logic,
         written and tested here for the first time in this package**
         (`local_fs/search.py::_real_path_within_allowlist`/
         `require_within_allowlist`): before any file's content is read
         or returned, its *real*, symlink-followed path
         (`Path.resolve()`) is checked to actually be `relative_to` one
         of the allowlisted (already-resolved) directories -- never a
         bare prefix-string check, which a symlink or a `..` sequence
         can defeat. A path resolving outside every allowed directory is
         never read: `search()`'s directory walk drops it silently (a
         bad symlink somewhere in a big tree shouldn't abort an
         otherwise-good search), `fetch()` raises `ConnectorAPIError`
         instead (a caller-supplied id deserves a loud, explicit
         refusal).
      3. **Actually tested against a real symlink and a real
         path-traversal id, not just asserted in prose** --
         `tests/test_local_fs.py::test_symlink_escaping_allowed_directory_
         is_excluded_from_search` and `..._is_rejected_on_fetch` construct
         a real symlink inside an allowed `tmp_path` directory pointing at
         a real file outside it, and confirm neither `search()` nor
         `fetch()` ever surfaces the target's content.
         `test_fetch_rejects_path_traversal_id_that_resolves_outside_
         allowlist` and `test_fetch_rejects_traversal_style_id_even_when_
         target_does_not_exist` do the same for a `"../../etc/passwd"`-style
         `fetch()` id (both against a synthetic tmp-path traversal target
         and literally against `"../../etc/passwd"` itself, which is
         rejected whether or not that path happens to exist/be readable on
         the machine running the tests). `tests/test_onedrive.py::
         test_fetch_rejects_symlink_escaping_allowlist` re-proves the same
         guarantee through the connector's own client, not just the shared
         module directly.
- [x] **No indexing/sync infrastructure** -- live search at query time
      only: walk the allowlisted directories, read matching files, plain
      case-insensitive substring match over content. No persistent search
      index, matching this package's established "no indexing pipeline in
      V1" philosophy.
- [x] **V1 file-type scope: plain text only, and this is a documented gap,
      not a silent one** -- `.md`, `.markdown`, `.txt`, `.rst`
      (`local_fs/search.py::PLAIN_TEXT_EXTENSIONS`). PDF/Word (`.docx`)/
      Excel (`.xlsx`)/image formats are **explicitly out of scope for
      V1** -- not attempted, not silently ignored: documented at length
      in `local_fs/search.py`'s module docstring, in both connectors'
      `mcp_server.py` tool instructions text (so an MCP client sees it
      too, not just source readers), in `README.md`/`INSTALL.md`, and
      here. A `.pdf`/`.docx` file sitting in an allowlisted directory
      simply never reaches the read/search path (extension filter in
      `iter_candidate_files`) and is tested explicitly (`tests/
      test_local_docs.py::test_pdf_files_are_never_returned_by_search`).
- [x] **OneDrive's local sync folder path is explicit required config,
      never auto-detected** -- real sync-folder locations vary too much
      across OS/OneDrive-client-version/account-type to guess reliably
      (macOS: typically `~/Library/CloudStorage/OneDrive-<AccountName>/`
      for current versions, `~/OneDrive` for older ones; Windows:
      `%USERPROFILE%\OneDrive` or `%USERPROFILE%\OneDrive - <Org Name>`
      for business accounts; multiple accounts can exist side by side).
      `onedrive/config.py` documents all of this and requires the
      operator to supply the real, already-synced path(s) themselves --
      same `allowed_directories` field name as `local_docs`, deliberately
      not differentiated, since structurally the two connectors really
      are the same thing pointed at different folders. Both connectors'
      `config.py`/`client.py`/`mcp_server.py` are near-identical for
      exactly this reason -- the one genuine structural difference is
      `detect_cloud_placeholders=True` (OneDrive) vs. `False`
      (`local_docs`), see next item.
- [x] **OneDrive Files-On-Demand cloud-only placeholders -- detected on a
      real, explicitly partial, honestly-documented best-effort basis, not
      claimed as fully solved**
      (`local_fs/search.py::looks_like_cloud_only_placeholder`):
        - **What's checked**: on Windows, `os.stat().st_file_attributes`
          (a real attribute that only exists on Windows `stat` results)
          for `FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS` (`0x00400000`) or
          `FILE_ATTRIBUTE_OFFLINE` (`0x00001000`) -- real, documented
          Win32 bits OneDrive sets on an un-hydrated cloud-only
          placeholder. On any platform, a zero-byte file with a
          plain-text extension is treated as a suspected placeholder (a
          genuinely empty synced document is possible but unusual, so
          this errs toward flagging rather than silently returning empty
          content as if it were confirmed-real).
        - **What's explicitly *not* checked, flagged rather than glossed
          over**: macOS implements Files-On-Demand via Apple's File
          Provider extension, and real download status lives behind
          `NSMetadataItemUbiquitousItemDownloadingStatusKey` -- reachable
          from Cocoa/Foundation, not from Python's stdlib `os`/`pathlib`
          at all. Properly detecting it would need a native bridge (e.g.
          `pyobjc`), deliberately not taken on as a dependency for a
          stdlib-only V1. A non-zero-byte macOS cloud-only placeholder
          (uncommon but possible) will not be caught. No first-party
          Linux OneDrive client exists, so the framing barely applies
          there.
        - **Where it fires**: `search()` skips a detected placeholder
          silently (it has no real searchable text anyway); `fetch()`
          raises a clear `ConnectorAPIError` naming the file and
          explaining it looks undownloaded, instead of returning empty or
          partial content pretending to be real. `local_docs` never opts
          into this check at all (`detect_cloud_placeholders=False`) --
          a genuinely empty `.txt`/`.md` file in a plain local directory
          is normal and shouldn't be treated as suspicious; only
          `onedrive`'s client passes `True`.
        - **Verification honesty**: unit tests exercise this against a
          synthetic zero-byte file standing in for a real placeholder
          (no real OneDrive account/Files-On-Demand session was available
          to produce an actual one) and, for the Windows attribute check,
          against a monkeypatched `stat()` result
          (`tests/test_local_fs.py::
          test_looks_like_cloud_only_placeholder_checks_windows_attributes`,
          skipped on non-Windows since the attribute doesn't exist there
          at all) -- no Windows machine was available to verify the real
          attribute bits against a real OneDrive-managed file. Both gaps
          stated plainly, not implied to be covered.
- [x] **Real, live end-to-end verification, not just unit tests** -- a
      real MCP stdio client (the same `mcp` SDK pattern documented in
      `INSTALL.md` section 3a) was actually run against a real
      `local-docs-mcp` subprocess, a real config file, and a real
      directory/file on disk this session: `list_tools()` returned
      `['search', 'fetch']`; a real `search` call found and returned real
      file content; naming a directory outside the configured allowlist
      came back as `isError: True` with the allowlist-violation message;
      a `fetch` call with id `"../../etc/passwd"` came back as
      `isError: True` with the path-safety rejection message -- proving
      both halves of the precision requirement hold at the real MCP
      protocol boundary, not merely in a unit test calling Python
      functions directly. See `INSTALL.md`'s "What's still unverified"
      section for the full transcript-level account.
- [x] **Tests**: 57 new tests (`tests/test_local_fs.py`,
      `tests/test_local_docs.py`, `tests/test_onedrive.py`, plus Local
      Docs/OneDrive sections added to the existing `tests/test_servers.py`)
      -- package's own suite went from 139 passed to **195 passed, 1
      skipped** (the 1 skip is the Windows-only file-attribute test,
      skipped on this non-Windows machine by design, not a failure),
      zero regressions. Covers: config validation/round-trip (directory
      resolution, dedupe-by-real-path, non-empty allowlist, result-limit
      bounds), search/fetch happy paths against real `tmp_path`
      directories (no injected fake transport needed -- neither connector
      makes a network call to fake), the symlink-escape and
      path-traversal rejection tests described above, PDF-extension
      exclusion, and the cloud-placeholder skip/flag behavior including
      the `local_docs` vs. `onedrive` differentiation.
- [x] **Nova's own top-level test suite reconfirmed unaffected, not just
      assumed** -- ran from a completely separate venv
      (`pip install -e ".[anthropic,copilot]"` + `pytest` from the repo
      root): **397 passed, 3 skipped**, identical to the baseline already
      on record after the earlier Jira/Confluence/SharePoint pass's
      restructuring to `packages/mcp-connectors/` as a fully independent
      sibling package (root `pyproject.toml`'s own `testpaths = ["tests"]`
      already excludes `packages/mcp-connectors/` from Nova's own pytest
      discovery, by design -- see that file's comment).

### Follow-up in the same branch/pass: configurable `file_categories` -- code, office documents, and PDF, gated by explicit opt-in

The "PDF/`.docx`/`.xlsx`/image file support" gap below was **partially,
deliberately resolved** in a later commit on this same branch, after a
direct scope decision from the project owner. Kept as one amended section
rather than a duplicate, since it's the same two connectors gaining a
capability, not new connectors.

**Confirmed scope, decided by the project owner via a direct question**:
code/config files (plain text -- just widens the extension allowlist, no
new dependency) and structured documents with real embedded text --
**PDF, `.docx`, `.xlsx`, `.pptx`**. **OCR/images explicitly excluded**:
asked directly "does this include images via OCR?", answered "structured
documents only" -- real embedded text extraction, not image/screenshot
recognition. No Tesseract or any OCR dependency anywhere in this package.

- [x] **`file_categories`: a config field, not an unconditional widening**
      -- both `LocalDocsConnectorConfig` and `OneDriveConnectorConfig`
      gained `file_categories: List[Literal["text", "code", "office",
      "pdf"]]`, defaulting to `["text"]` only. This is the actual
      "permission" gate the owner asked for: a user must explicitly list
      `"code"`/`"office"`/`"pdf"` in their JSON config to grant that
      access. **Backward compatible, not a breaking schema change** --
      an existing config file with no `file_categories` key at all
      behaves exactly as before (proven directly, not just by the field
      default: `tests/test_local_docs.py::
      test_file_categories_backward_compatible_with_configs_predating_the_field`
      hand-writes a JSON payload with no `file_categories` key and
      confirms it loads with the original text-only scope).
- [x] **`"code"` category**: ~40 common source/config extensions
      (`.py`/`.js`/`.ts`/`.java`/`.go`/`.rs`/`.rb`/`.php`/`.swift`/
      `.kt`/`.scala`/`.sh`/`.sql`/`.yaml`/`.json`/`.toml`/`.xml`/`.html`/
      `.css`/and more -- `local_fs/search.py::CODE_EXTENSIONS`,
      deliberately non-exhaustive). Reads via the exact same plain-text
      path as `"text"` -- no new library, no new risk surface.
- [x] **`"office"`/`"pdf"` categories: real embedded-text extraction**
      via the standard, well-maintained library for each --
      `python-docx` (`.docx`: paragraphs + table cells), `openpyxl`
      (`.xlsx`: every sheet's cell values, `read_only`/`data_only` mode),
      `python-pptx` (`.pptx`: every slide shape's text frame text),
      `pypdf` (`.pdf`: per-page text, joined; `reader.is_encrypted` skips
      password-protected files rather than guessing). New `documents`
      `pyproject.toml` extra (`python-docx>=1.0`, `openpyxl>=3.1`,
      `python-pptx>=0.6`, `pypdf>=4.0` -- floors verified by actually
      installing all four together into this project's own disposable
      venv alongside every other extra: resolved to python-docx 1.2.0,
      openpyxl 3.1.5, python-pptx 1.0.2, pypdf 6.16.1, no conflicts).
      **Deliberately kept separate from the `all` extra** -- unlike the
      other extras (each "one more MCP server's core dependency"), this
      one adds four third-party parsing libraries most `local_docs`/
      `onedrive` installs won't need at all (default `file_categories`
      needs none of them); a user opts in with e.g.
      `mcp-connectors[local-docs,documents]`, mirroring the same
      explicit-opt-in posture `file_categories` has at the config layer.
      All four libraries are deferred-imported (mirrors `common.py`'s
      `keyring` pattern) so the package still imports cleanly without
      `documents` installed -- only a config that actually requests
      `"office"`/`"pdf"` hits a real, clear error
      (`missing_libraries_for_categories`, called from each connector's
      `file_categories` field validator), and only at config-*validation*
      time, not a silent per-file no-op discovered later.
- [x] **Defensive per-format extraction, verified against real
      exceptions, not assumed** -- each `_extract_*_text` function
      catches broadly (`except Exception`, documented as deliberate,
      since each library raises a *different* exception type for
      corrupted/malformed input) and returns `None` (skip this one file)
      rather than raising or crashing the walk. Confirmed directly by
      actually feeding each library garbage bytes with the right
      extension before writing any handling code: `pypdf` raised
      `pypdf.errors.PdfStreamError`, `python-docx` raised
      `docx.opc.exceptions.PackageNotFoundError`, `openpyxl` raised
      `zipfile.BadZipFile`, `python-pptx` raised
      `pptx.exc.PackageNotFoundError` -- four different exception types
      across four libraries, confirming a broad catch (not one specific
      class) is the pragmatic, correct choice here. A dedicated test per
      format proves a corrupted file is skipped, not crashed
      (`tests/test_local_fs_documents.py::test_corrupted_{pdf,docx,xlsx,
      pptx}_*`), plus one proving a corrupted file among several good
      ones doesn't abort the rest of the walk
      (`test_corrupted_office_file_does_not_abort_a_multi_file_walk`).
      Encryption is also covered for PDF specifically (`pypdf` alone can
      both write and read an encrypted PDF, so a real encrypted fixture
      was buildable without adding a dependency): a password-protected
      `.pdf` is confirmed to never surface its content via `search()` and
      to raise a clear `ConnectorAPIError` on `fetch()`, not empty/
      garbage content. **Not independently verified**: a real
      password-protected `.docx`/`.xlsx`/`.pptx` (only PDF encryption was
      practically buildable without a new dependency; the other three
      formats' encrypted-file handling only goes through the general
      corrupted-file catch path, not a dedicated encrypted-file test).
- [x] **The path-safety guarantee needed no changes, and was re-proven
      against the new code paths, not just assumed to still hold** --
      `_real_path_within_allowlist`/`require_within_allowlist` run
      before any type-specific extraction and have no awareness of file
      type at all, so office/PDF files inherit the exact same symlink-
      escape and path-traversal rejection as `.txt` files always had.
      Proven directly, not just by inspection:
      `tests/test_local_fs.py::test_symlink_escape_is_rejected_for_a_pdf_extension_target`
      constructs a real symlink with a `.pdf` extension pointing outside
      an allowed directory and confirms both `search()` (with `"pdf"`
      opted in) and `fetch()` reject it -- the *entire* existing
      symlink-escape/path-traversal test suite from the first pass was
      also re-run unchanged and still passes.
- [x] **Real generated fixtures, not mocked content** -- every
      `.docx`/`.xlsx`/`.pptx`/`.pdf` happy-path test in
      `tests/test_local_fs_documents.py` builds a real file with the
      *same* library that reads it (including the `.pdf` fixture, built
      with raw `pypdf.PdfWriter`/content-stream construction since
      `pypdf` itself has no high-level text-drawing API -- prototyped and
      confirmed working via a real `PdfWriter` -> `PdfReader` round trip
      before writing any test) and asserts the real extracted text comes
      back through `search_local_files`/`fetch_local_file` -- e.g. a real
      `.pptx` with a slide titled `"unique-pptx-slide-marker"` is found
      by searching for that exact string. Skipped as a whole module (not
      failed) if the `documents` extra isn't installed
      (`pytest.importorskip` per library at module top) -- confirmed
      directly: a full suite run in a venv *without* `documents`
      correctly skips this one module (reported as a single skip,
      1 module) plus one `documents`-gated test in `test_local_docs.py`,
      while every other test (including all `file_categories` config/
      gating logic) still passes normally.
- [x] **The `file_categories` gate actually withholds access, proven
      end to end through the real client, not just the shared module** --
      `tests/test_local_docs.py::
      test_code_file_invisible_to_search_with_default_file_categories`
      is the literal scenario used to validate this: a real `.py` file
      sitting in an allowed directory is invisible to `LocalDocsClient
      .search()` when `file_categories` is left at its default
      `["text"]`, and becomes findable once `"code"` is added to the
      config -- and the reverse for `fetch()` (refused with a clear
      `ConnectorAPIError` naming the missing category, then succeeds once
      granted). Mirrored for OneDrive.
- [x] **Real, live end-to-end re-verification with the new categories,
      not just unit tests** -- re-ran the same real MCP stdio client
      pattern from the first pass, this time against a `local-docs-mcp`
      config with `file_categories: ["text", "code", "pdf"]`: a real
      `.py` file and a real generated `.pdf` (again, built with `pypdf`
      itself) both came back as genuine `search` hits with real
      extracted text and `isError: False` -- not a mocked or assumed
      result.
- [x] **Tests**: 50 new tests across `tests/test_local_fs.py` (extension/
      category resolution, `missing_libraries_for_categories`, the
      pdf-extension symlink-escape re-proof), the new
      `tests/test_local_fs_documents.py` (16 tests: happy path +
      corrupted-file cases for all four formats, plus encryption and
      multi-file-walk-survival), and `tests/test_local_docs.py`/
      `tests/test_onedrive.py` (config validation for `file_categories`,
      including the "library not installed" config-load-time error, and
      client-level category-gating) -- package's own suite went from
      **195 passed, 1 skipped to 245 passed, 1 skipped**, zero
      regressions (confirmed via a fresh venv,
      `pip install -e ".[all,dev,documents]"`). Also confirmed the suite
      still passes cleanly *without* `documents` installed (228 passed,
      3 skipped -- the extra 2 skips are the whole document-fixtures
      module plus one library-availability-guarded test, exactly the
      intended graceful degradation, not a failure).
- [x] **Nova's own top-level suite re-confirmed unaffected again**:
      **397 passed, 3 skipped**, unchanged from every prior check.

**Explicitly deferred, not started, matching this pass's scope boundary**:

- [ ] **No Sage-style aggregator or `RetrievalCapability`/agent-framework
      wiring for these two connectors either** -- exactly the same
      unstarted Phase 2 gap the original three connectors have (see the
      updated Sage follow-up bullet below). Nothing in this pass touches
      `RetrievalCapability`, the orchestrator, or any specialist agent.
- [ ] ~~PDF/`.docx`/`.xlsx`/image file support~~ **Partially resolved**
      (see the `file_categories` follow-up above): PDF/`.docx`/`.xlsx`/
      `.pptx` are now supported, opt-in via config. **Image formats
      remain out of scope, but now as a confirmed deliberate exclusion
      (OCR explicitly rejected by the project owner), not an open gap
      awaiting a decision.**
- [ ] Full OneDrive Files-On-Demand detection on macOS (needs a native
      `pyobjc`-style bridge to Apple's File Provider API -- see above) and
      real-Windows/real-OneDrive-account verification of the attribute
      check and the placeholder detection generally (both currently only
      unit-tested against synthetic stand-ins).
- [ ] No indexing/caching for office/PDF parsing -- an office/PDF file is
      re-parsed from scratch on every single query that walks past it (no
      cache, matching V1's "no indexing pipeline" scope), markedly slower
      than the plain-text read path. Untested at real scale (no
      multi-hundred-page PDF or large/complex `.xlsx` was used in
      testing) -- a real cost flagged honestly in
      `local_fs/search.py`'s module docstring and `INSTALL.md`, not
      hidden, and a real candidate for an indexing/caching pass if this
      connector is ever pointed at a directory with many large office
      documents.
- [ ] Real password-protected `.docx`/`.xlsx`/`.pptx` files, not just
      `.pdf`, were not independently tested (see above) -- covered only
      by the general corrupted-file catch path, not a dedicated
      encrypted-file assertion.
- [ ] No pagination beyond the first page of results for either
      connector, same "single top-N `result_limit` request" design as the
      other three connectors.
- [ ] No de-duplication of a symlink and its real target both appearing
      as separate search results when both happen to be reachable inside
      an allowlisted directory (this is expected, documented behavior --
      see `tests/test_local_fs.py::test_symlink_within_allowlist_is_fine`
      -- not a bug, but worth noting as a minor "could be nicer" item if
      it ever becomes an actual annoyance in practice).

## Sage — follow-up (RetrievalCapability's *codebase-grounding* portion now built differently — see below)

- [x] ~~`RetrievalCapability` is entirely deferred — no stub exists yet in
      `src/ai_sdlc/capabilities/`.~~ **Superseded, not simply resolved:**
      `RetrievalCapability` now exists (PR #18), but its V1 codebase-grounding
      provider is a read-only harnessed agentic tool (reusing `CodingCapability`'s
      pattern), not Sage's originally-scoped Tree-Sitter/embedding/hybrid-search
      index (`docs/architecture/v1_architecture.md` §9/§18 Decision 6, PR #17).
      That custom-index design remains the documented future/scale upgrade path,
      not abandoned — pick it up if the harnessed-agent approach proves too slow
      or expensive at real repo scale (no concrete trigger threshold defined yet,
      §20 open question 10).
- [ ] ~~**Jira/Confluence Enterprise Connectors are still entirely
      deferred**~~ **Partially resolved** (branch
      `agents/nexus-knowledge-base-connectors`, see the new "Nexus —
      Knowledge Base Tool Connectors, Phase 1" section above): Jira and
      Confluence (plus SharePoint, not originally listed here but part
      of the same approved initiative) now exist as real, standalone MCP
      servers with hard scope enforcement and real JQL/CQL/Graph-query
      construction. **Still true, and still exactly this bullet's
      original point**: none of this is reachable through
      `RetrievalCapability`, the orchestrator, or any specialist agent —
      a harnessed coding-agent tool still has no native Jira/Confluence/
      SharePoint awareness, on purpose (explicitly out of scope for
      Phase 1). That wiring is Phase 2, still Sage's (or a
      Nexus-MCP-adapter's) job, still not started. **Two more Phase 1
      connectors landed in a later pass** (branch
      `agents/nexus-local-onedrive-connectors`, see "Nexus — Local
      Directories & OneDrive Connectors, Phase 1 (cont'd)" below): Local
      Docs and OneDrive, both pure local-filesystem, no credential. Same
      "still not reachable through `RetrievalCapability`/the
      orchestrator/any specialist agent" caveat applies to these two as
      well — that wiring is still the same unstarted Phase 2.
- [x] ~~**Architecture Agent's retrieval call is wired but not reachable in a
      live workflow yet**~~ **Resolved** (branch `agents/forge-developer-agent`,
      found as a side effect of wiring the Developer Agent, which needed the
      same input key): `OrchestratorAPI.start_workflow` (`orchestration/api.py`)
      now populates `inputs["target_repository"]["workspace_path"]` from
      `self.orch.store.workspace` for every workflow — `ai-sdlc init` runs
      inside the target application repository, so the workspace
      `StateStore` is already rooted at *is* that repository. This dead
      code is live now; not independently re-verified beyond the existing
      test suite passing.

## Aegis — follow-up

- [ ] None of the mock-provider-generated content in
      `src/ai_sdlc/capabilities/providers/mock.py` is sanitized against
      prompt injection from repository content (README, source comments,
      etc.). This is fine while only the deterministic mock provider
      exists, but must be addressed before any real vendor provider is
      wired in behind `ReasoningCapability`.

## Sentinel — follow-up

- [ ] No agent-quality evaluation harness exists beyond the Pydantic
      schema validation `SpecialistAgent`/`POAgentOutputData`/
      `ArchitectureOutputData` already enforce. Sentinel owns building
      real structural/quality evaluation on top of that.

## Project-level

- [x] ~~No `pyproject.toml`/packaging config exists at the repo root.~~
      **Resolved** (this branch, commit `d55002a`): added `pyproject.toml`
      with `setuptools` src-layout discovery, a real `ai-sdlc` console-script
      entry point (`ai_sdlc.cli.main:app`), and `[tool.pytest.ini_options]
      pythonpath = ["src"]` so `PYTHONPATH=src` is no longer required by
      hand. Verified via `pip install -e .` + `ai-sdlc init/start/status`
      end-to-end and a full `pytest` run (145/145, no `PYTHONPATH` set).
