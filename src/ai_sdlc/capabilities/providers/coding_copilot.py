"""GitHub Copilot SDK-backed `CodingCapability` provider.

Drives `github/copilot-sdk` (PyPI package `github-copilot-sdk`) rather
than parsing Copilot CLI subprocess output -- per
`docs/architecture/v1_architecture.md` section 20 open question 2's
resolution. `CopilotCodingProvider.execute()` stays synchronous from the
Developer Agent's perspective; internally it runs the SDK's async
session lifecycle to completion via `asyncio.run` and returns one
validated `CodingResult`, mirroring exactly what a Claude Agent SDK
provider has to do for its own async/session-based reality (section 8).

## What was actually verified in this environment vs. assumed from docs

This project has already had to walk back one wrong assumption about this
exact tool (section 20 open question 2's history), so this module is
explicit about which of the following was checked directly against the
installed package, versus taken from documentation/prior research:

**Verified** (by installing `github-copilot-sdk==1.0.9` from PyPI into an
isolated Python 3.13 venv -- required, since it declares Python 3.11+,
while this repo's `pyproject.toml` still targets >=3.10; see that file's
`[project.optional-dependencies]` entry -- and introspecting the actual
installed classes/signatures, not just reading marketing docs):

  - The package installs cleanly and its PyPI "Home-page" is
    `https://github.com/github/copilot-sdk`, confirming it's the SDK the
    architecture doc means, not a different package.
  - `copilot.CopilotClient(working_directory=..., ...)` is real; `start()`
    /`stop()` are async methods.
  - `client.create_session(on_permission_request=..., on_user_input_request=...,
    model=..., working_directory=..., ...)` returns a `CopilotSession` and is
    async.
  - `session.send_and_wait(prompt, *, agent_mode: Literal["interactive",
    "plan", "autopilot", "shell"] | None = None, timeout: float = 60.0) ->
    SessionEvent | None` is real and async -- confirms the brief's claim
    that an "Autopilot" mode exists as a real, typed parameter, not just
    marketing copy.
  - `on_permission_request`'s payload is a discriminated union
    (`PermissionRequestShell | PermissionRequestWrite | PermissionRequestRead
    | PermissionRequestMcp | PermissionRequestUrl | ...`). The shell variant
    -- the one this provider's allow-list enforcement actually needs --
    has `commands: list[{identifier: str, read_only: bool}]` and
    `full_command_text: str`. Decisions returned from the callback include
    `PermissionDecisionApproveOnce(approved_interactively: bool | None)`
    and `PermissionDecisionReject(feedback: str | None)`, among others.
    `PermissionRequestShell`/`PermissionDecisionApproveOnce`/`Reject` are
    real classes with attribute access -- but `copilot.session.
    UserInputRequest`/`UserInputResponse` (below) are `TypedDict`s,
    meaning they're plain `dict`s at runtime, not attribute-bearing
    objects. This inconsistency across the SDK's own generated modules
    was caught by writing a real test against the installed package (a
    `getattr`-only read on a `UserInputRequest` silently returned
    defaults instead of the actual question text) -- see `_field()`
    below, which normalizes both shapes at every callback read site.
  - **Corrects the architecture doc's section 20 open-question-2 note**:
    the doc says prior research "found no confirmed distinct event type"
    for a mid-session clarifying question, "meaning a live mid-session
    clarification-pause likely isn't available." That statement does not
    hold for `github-copilot-sdk` 1.0.9: `create_session` accepts a
    distinct `on_user_input_request` callback, separate from
    `on_permission_request`, carrying
    `UserInputRequest(question: str, choices: list[str], allowFreeform: bool)`
    and expecting `UserInputResponse(answer: str, wasFreeform: bool)` back.
    The SDK *does* have the event type the doc's prior research says
    doesn't exist. See `_make_user_input_handler` below for how this
    provider handles it regardless -- Nova's architecture (section 4: the
    Developer Agent runs this capability unattended; the only
    human-in-the-loop gate is the post-hoc approval on the *finished*
    diff) still has nowhere to route a live question to an actual human
    mid-session, so this callback existing doesn't change the "final
    structured verdict" execution model section 8/20 already establish --
    it just means the auto-response below is answering a *real* callback
    the SDK will actually invoke, not a hypothetical one.

**Not verified** -- flagged, not silently assumed solid: this environment
has no authenticated Copilot CLI session (`use_logged_in_user`/
`github_token`), so no `create_session()` or `send_and_wait()` call in
this module has been exercised against a live Copilot backend end-to-end.
Field names/decision shapes above were confirmed via `typing.get_type_hints`
against the installed package's real classes, which is stronger than
reading docs, but still short of an actual session round-trip. Treat this
module as implemented-against-verified-shapes, not integration-tested.
Non-shell permission-request variants (`Write`/`Read`/`Mcp`/`Url`/...)
were *not* individually field-inspected the way `Shell` was (time-boxed);
they are handled generically below (see `_make_permission_handler`).

## Design choices flagged for reconciliation / open questions

  - **Worktree creation is this provider's job, not the caller's.**
    `docs/architecture/v1_architecture.md` section 4 point 1 lists "An
    isolated working tree -- a disposable Git worktree/branch created off
    the target repository" as something "V1's provider drives that tool
    ... scoped to", and section 7 describes the Developer Agent's git
    operations as happening "via that provider's own tool use". This
    provider therefore treats `CodingRequest.workspace_path` as the
    **target repository root** (matching `DeveloperAgentInput.target_repository
    .workspace_path` in section 4's literal example), and creates its own
    disposable `git worktree` off it before starting a Copilot session --
    it never runs Copilot directly against `workspace_path` itself. If
    Claude Forge's reconciled interface instead expects `workspace_path`
    to already *be* the isolated worktree (caller-created), this
    provider's worktree-creation step would need to move up a layer.
    Flagged explicitly in the final report.
  - **This provider commits locally but never pushes or opens a PR.**
    Section 4 is explicit that nothing is pushed/opened until a human
    approves the packaged diff, and that approval happens *after*
    `CodingCapability` returns (the Developer Agent packages the diff from
    this capability's result and requests approval as a separate step).
    So `execute()` instructs Copilot to make and commit the change inside
    the isolated worktree, but never to push or open a PR -- push/PR-open
    is out of `CodingCapability`'s scope as specified today. What
    triggers the actual push+PR-open after approval (a second call into
    this same capability, a different Forge-owned entrypoint, or Nexus's
    GitHub adapter) is genuinely unspecified in the architecture doc as
    read -- flagged as an open question, not resolved here.
  - **Self-check command semantics**: `CodingRequest.self_check_commands`
    is an unstructured `List[str]` (matching the interface as designed in
    `coding.py`), with no field distinguishing "build" from "test"
    commands the way `DeveloperAgentOutput`'s example
    (`["./gradlew build", "./gradlew test"]`) implies by convention only.
    This provider treats `self_check_commands[0]` as the build step and
    the remainder as test steps, skipping the test steps (reporting
    `tests_passed=None`) if the build step fails. This is a documented
    convention, not something the architecture doc specifies structurally
    -- Claude Forge may have made a different, equally valid choice.
  - **Section 20 open question 8 (step/retry budget)**: this provider
    does not re-implement its own step-by-step loop control on top of
    Copilot's own `agent_mode="autopilot"` run -- autopilot mode is
    already Copilot's own bounded-loop execution, and re-driving it
    step-by-step from Nova's side would fight the SDK rather than use it.
    `CodingRequest.max_steps` is instead enforced as an overall
    **wall-clock session timeout** (see `_STEP_TO_SECONDS_FACTOR` below,
    a deliberately rough, documented heuristic, not a measured constant)
    passed to `send_and_wait`, and `CodingResult.steps_used` is populated
    from the session's own event count as a best-effort proxy for "how
    much work happened", not a verified 1:1 mapping to
    "agentic loop steps" as the architecture doc's phrase might imply.
    This is a real design tradeoff, flagged rather than silently asserted
    as the correct interpretation.
"""
from __future__ import annotations

import asyncio
import shlex
import subprocess
import tempfile
from typing import Any, Dict, List, Optional

from ai_sdlc.capabilities.coding import (
    CodingCapability,
    CodingRequest,
    CodingResult,
    MalformedResponseError,
    ProviderError,
    ToolPolicy,
)

try:
    import copilot as _copilot_sdk
    from copilot.generated import rpc as _copilot_rpc

    _COPILOT_SDK_IMPORT_ERROR: Optional[BaseException] = None
except ImportError as exc:  # pragma: no cover - exercised only when the
    # optional `github-copilot-sdk` extra (and its Python 3.11+
    # requirement) isn't installed. Import failure is deferred to
    # provider construction, not module import, so this module can still
    # be imported (and, e.g., appear in test collection) without the SDK
    # present -- matching this project's "tests never require real
    # provider credentials" convention, extended here to "not even the
    # real SDK needs to be installed to import this module."
    _copilot_sdk = None
    _copilot_rpc = None
    _COPILOT_SDK_IMPORT_ERROR = exc

# Rough, documented heuristic (not a measured constant -- see module
# docstring's "Section 20 open question 8" note): how many wall-clock
# seconds one `max_steps` unit is worth when translated into
# `send_and_wait`'s `timeout`. Chosen conservatively; revisit once real
# session timing data exists.
_STEP_TO_SECONDS_FACTOR = 20.0
_MIN_SESSION_TIMEOUT_SECONDS = 120.0


def _require_sdk() -> None:
    if _COPILOT_SDK_IMPORT_ERROR is not None:
        raise ImportError(
            "github-copilot-sdk is not installed (or requires Python 3.11+, "
            "which this environment may not have). Install the optional "
            "'copilot' extra: `pip install ai-sdlc[copilot]`. "
            f"Original import error: {_COPILOT_SDK_IMPORT_ERROR}"
        )


def _command_basename(identifier: str) -> str:
    token = (identifier or "").strip().split()[0] if identifier.strip() else ""
    return token.rsplit("/", 1)[-1]


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """Read `name` off `obj` regardless of whether the SDK represents it
    as a real attribute-bearing object or a `TypedDict` (a plain `dict` at
    runtime). Verified necessary: `copilot.generated.session_events.
    PermissionRequestShell` and `copilot.generated.rpc.
    PermissionDecisionApproveOnce`/`Reject` are real classes (attribute
    access), but `copilot.session.UserInputRequest`/`UserInputResponse`
    are `TypedDict`s -- calling them returns a plain `dict`, not an object
    with `.question`/`.answer` attributes. Mixing `getattr` and dict
    access across these was an actual bug caught by
    `tests/test_capabilities_coding_copilot.py` against the real
    installed package, not a hypothetical -- kept as a single helper here
    so it isn't re-discovered per call site.
    """
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


class CopilotCodingProvider(CodingCapability):
    """Real `CodingCapability` implementation backed by
    `github/copilot-sdk`. Requires the optional `copilot` extra and an
    authenticated Copilot CLI session (`use_logged_in_user=True` by
    default, matching section 10's "Local CLI binds to developer's
    GitHub/SSO session token" -- this provider does not manage
    credentials itself, it relies on the same logged-in session the
    Copilot CLI itself would use).
    """

    def __init__(self, *, model: Optional[str] = None):
        _require_sdk()
        self.model = model
        self._clarification_log: List[str] = []

    def execute(self, request: CodingRequest) -> CodingResult:
        try:
            payload = asyncio.run(self._execute_async(request))
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - deliberately broad: any
            # unexpected SDK/subprocess failure must still surface as
            # ProviderError per this capability's failure contract, never
            # an arbitrary exception type.
            raise ProviderError(
                f"copilot_provider: session failed to produce a result: {exc}"
            ) from exc

        try:
            return CodingResult(**payload)
        except Exception as exc:  # noqa: BLE001 - Pydantic ValidationError
            # specifically, but caught broadly to guarantee the documented
            # failure contract regardless of what raises.
            raise MalformedResponseError(
                f"copilot_provider: session result failed schema validation: {exc}"
            ) from exc

    # -- orchestration ----------------------------------------------------

    async def _execute_async(self, request: CodingRequest) -> Dict[str, Any]:
        _require_sdk()
        self._clarification_log = []

        branch_name = self._derive_branch_name(request)
        worktree_path = self._create_isolated_worktree(request, branch_name)

        client = _copilot_sdk.CopilotClient(
            working_directory=worktree_path,
            use_logged_in_user=True,
        )
        await client.start()
        try:
            session = await client.create_session(
                on_permission_request=self._make_permission_handler(request.tool_policy),
                on_user_input_request=self._make_user_input_handler(),
                model=self.model,
                working_directory=worktree_path,
            )
            try:
                timeout = max(
                    _MIN_SESSION_TIMEOUT_SECONDS,
                    request.max_steps * _STEP_TO_SECONDS_FACTOR,
                )
                await session.send_and_wait(
                    self._build_prompt(request),
                    agent_mode="autopilot",
                    timeout=timeout,
                )
                steps_used = await self._estimate_steps_used(session, request.max_steps)
            finally:
                await session.disconnect()
        finally:
            await client.stop()

        files_changed = self._collect_files_changed(worktree_path, request.base_branch)
        self_check = self._run_self_check(worktree_path, request.self_check_commands)

        return {
            "branch_name": branch_name,
            "files_changed": files_changed,
            "self_check": self_check,
            "summary": self._build_summary(request, files_changed),
            "steps_used": steps_used,
            "provider_name": "github_copilot_sdk",
            "metadata": {
                "worktree_path": worktree_path,
                "model": self.model,
                "clarification_questions_auto_answered": list(self._clarification_log),
            },
        }

    # -- worktree / prompt --------------------------------------------------

    def _derive_branch_name(self, request: CodingRequest) -> str:
        slug = "".join(
            c.lower() if c.isalnum() else "-" for c in request.task_summary
        ).strip("-")
        while "--" in slug:
            slug = slug.replace("--", "-")
        return f"forge/{slug or 'change'}"

    def _create_isolated_worktree(self, request: CodingRequest, branch_name: str) -> str:
        worktree_path = tempfile.mkdtemp(prefix="nova-copilot-worktree-")
        try:
            subprocess.run(
                [
                    "git",
                    "-C",
                    request.workspace_path,
                    "worktree",
                    "add",
                    "-b",
                    branch_name,
                    worktree_path,
                    request.base_branch,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise ProviderError(
                "copilot_provider: failed to create isolated git worktree "
                f"off {request.workspace_path}@{request.base_branch}: {exc.stderr}"
            ) from exc
        return worktree_path

    def _build_prompt(self, request: CodingRequest) -> str:
        parts = [request.task_brief]
        if request.standards_context:
            parts.append(f"Project standards and conventions:\n{request.standards_context}")
        if request.revision_feedback:
            parts.append(
                "A human reviewer rejected a previous attempt at this task with "
                f"this feedback -- address it directly:\n{request.revision_feedback}"
            )
        parts.append(
            "Make the necessary code changes and commit them locally with a clear "
            "commit message. Do not push the branch and do not open a pull request "
            "-- that happens later, after a separate human approval step."
        )
        return "\n\n".join(parts)

    # -- permission / user-input callbacks ---------------------------------

    def _make_permission_handler(self, tool_policy: ToolPolicy):
        async def handler(request: Any):
            kind = _field(request, "kind")
            if kind == "shell":
                identifiers = [
                    _command_basename(_field(cmd, "identifier", ""))
                    for cmd in _field(request, "commands", [])
                ]
                if any(ident in tool_policy.denied_commands for ident in identifiers):
                    return _copilot_rpc.PermissionDecisionReject(
                        feedback="denied by CodingRequest.tool_policy.denied_commands"
                    )
                if identifiers and all(
                    ident in tool_policy.allowed_commands for ident in identifiers
                ):
                    return _copilot_rpc.PermissionDecisionApproveOnce(
                        approved_interactively=False
                    )
                return _copilot_rpc.PermissionDecisionReject(
                    feedback=(
                        "not in CodingRequest.tool_policy.allowed_commands: "
                        f"{identifiers}"
                    )
                )
            # Non-shell permission kinds (write/read/mcp/url/memory/...):
            # not individually field-verified against this SDK version
            # (see module docstring). Approved by default -- the physical
            # worktree isolation (section 10) is this provider's primary
            # containment boundary for file writes, and MCP/URL access is
            # not part of this capability's scope (Nova routes external
            # tool access through Nexus, section 7), so a real deployment
            # should pass `mcp_servers=None`/no network tools into
            # `create_session` rather than rely on this callback alone to
            # block them. Flagged as a judgment call, not a verified-safe
            # default.
            return _copilot_rpc.PermissionDecisionApproveOnce(approved_interactively=False)

        return handler

    def _make_user_input_handler(self):
        async def handler(request: Any):
            question = _field(request, "question", "") or ""
            choices = _field(request, "choices", None) or []
            self._clarification_log.append(question)
            if choices:
                answer, was_freeform = choices[0], False
            else:
                answer, was_freeform = (
                    "No human reviewer is available mid-session. Proceed using "
                    "only the task brief and standards context already "
                    "provided; make the most reasonable assumption and state "
                    "it explicitly in the final commit message/summary so a "
                    "human can review it during approval.",
                    True,
                )
            return _copilot_sdk.session.UserInputResponse(
                answer=answer, wasFreeform=was_freeform
            )

        return handler

    # -- result assembly ----------------------------------------------------

    async def _estimate_steps_used(self, session: Any, max_steps: int) -> int:
        try:
            events = session.get_events()
            if asyncio.iscoroutine(events):
                events = await events
            return max(0, min(max_steps, len(events)))
        except Exception:  # noqa: BLE001 - best-effort proxy only, see
            # module docstring's Q8 note; never let this fail the call.
            return 0

    def _collect_files_changed(
        self, worktree_path: str, base_branch: str
    ) -> List[Dict[str, str]]:
        try:
            result = subprocess.run(
                ["git", "-C", worktree_path, "diff", "--name-status", f"{base_branch}...HEAD"],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise ProviderError(
                f"copilot_provider: failed to read git diff after session: {exc.stderr}"
            ) from exc

        status_map = {"A": "ADDED", "M": "MODIFIED", "D": "DELETED"}
        changes: List[Dict[str, str]] = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            status, _, path = line.partition("\t")
            change_type = status_map.get(status[:1], "MODIFIED")
            changes.append({"path": path, "change_type": change_type})
        return changes

    def _run_self_check(
        self, worktree_path: str, self_check_commands: List[str]
    ) -> Dict[str, Any]:
        if not self_check_commands:
            return {
                "build_passed": None,
                "tests_passed": None,
                "commands_run": [],
                "skipped_reason": "no self_check_commands configured for this workspace",
            }

        build_cmd, *test_cmds = self_check_commands
        commands_run = [build_cmd]
        build_passed = self._run_command(build_cmd, worktree_path)

        tests_passed: Optional[bool] = None
        if build_passed and test_cmds:
            tests_passed = True
            for cmd in test_cmds:
                commands_run.append(cmd)
                if not self._run_command(cmd, worktree_path):
                    tests_passed = False
                    break
        elif build_passed and not test_cmds:
            tests_passed = True

        return {
            "build_passed": build_passed,
            "tests_passed": tests_passed,
            "commands_run": commands_run,
            "skipped_reason": None,
        }

    def _run_command(self, command: str, cwd: str) -> bool:
        try:
            result = subprocess.run(
                shlex.split(command), cwd=cwd, capture_output=True, text=True
            )
            return result.returncode == 0
        except (OSError, ValueError):
            return False

    def _build_summary(
        self, request: CodingRequest, files_changed: List[Dict[str, str]]
    ) -> str:
        prefix = "Revised change" if request.revision_feedback else "Change"
        if not files_changed:
            return f"{prefix} for: {request.task_summary}. No files were modified."
        paths = ", ".join(f["path"] for f in files_changed[:5])
        more = f" (+{len(files_changed) - 5} more)" if len(files_changed) > 5 else ""
        return f"{prefix} for: {request.task_summary}. Modified: {paths}{more}."
