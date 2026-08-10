# Pending Work

Tracked from the code review of the Specialist Agent Layer (Craft), branch
`agents/craft-specialist-agent-layer` (commit `ee8f925`), reviewed 2026-08-10.
Update/remove items as they're resolved.

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

- [ ] **`POAgent._effective_text` can never actually resolve a
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

## Sage — follow-up

- [ ] `RetrievalCapability` is entirely deferred — no stub exists yet in
      `src/ai_sdlc/capabilities/`. Needed before any agent can pull
      RAG/knowledge context.

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
