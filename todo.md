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

- [ ] **Phase 2 (Sage-style aggregator + agent-framework wiring)** —
      nothing in this pass touches `RetrievalCapability`, the
      orchestrator, or any specialist agent. A future pass would need to
      decide how (or whether) an agent fans a query out across all three
      connectors, how a workspace configures which connectors are
      active, and whether that goes through the existing
      `AI_SDLC_AGENT_FRAMEWORK` selection or something connector-specific.
      None of those questions were answered or even opened here.
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
      Nexus-MCP-adapter's) job, still not started.
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
