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
- [ ] **Jira/Confluence Enterprise Connectors are still entirely deferred**,
      unaffected by the above — a harnessed coding-agent tool has no native
      Jira/Confluence awareness, so this gap isn't closed by PR #18 at all.
      Still Sage's (or Nexus-MCP-adapter) job, still not started.
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
