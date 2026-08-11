"""GitHub Copilot SDK-backed `CodingCapability` provider.

Drives `github/copilot-sdk` (PyPI package `github-copilot-sdk`) rather
than parsing Copilot CLI subprocess output -- per
`docs/architecture/v1_architecture.md` section 20 open question 2's
resolution. `CopilotCodingProvider.execute()` stays synchronous from the
Developer Agent's perspective; internally it runs the SDK's async
session lifecycle to completion (see `_run_async`, mirroring
`providers/claude_sdk.py`'s own event-loop-safety pattern) and returns
one validated `CodingResult`.

## Reconciliation note (post-hoc, read this first)

This provider was originally built against a self-authored, provisional
`coding.py` (Claude Forge, the interface's actual owner, hadn't landed
theirs yet). That draft got one structural thing backwards: it had this
provider create its own isolated Git worktree internally from a target-
repo-root `workspace_path`. The canonical `coding.py` (now in place, see
its own module docstring) settles this the other way --
`CodingRequest.working_tree_path` is an **already-isolated** working tree
the caller (the Developer Agent) creates *before* calling this capability,
matching section 3's literal "target working-tree path" as an *input*.
This file was rewritten to match: it no longer creates a worktree at all,
only verifies the one it's given (`_verify_working_tree`, mirroring
`claude_sdk.py`'s own version of the same check almost exactly).

One field this provider's first draft relied on has no equivalent in the
canonical interface: a `revision_feedback` field for re-threading a human
reviewer's rejection into a retry attempt. `CodingRequest` has no such
field -- `coding.py`'s module docstring doesn't mention a revision loop
at the capability level at all. This provider therefore no longer builds
a "previous attempt was rejected, address this feedback" prompt section;
presumably the Developer Agent is expected to fold any rejection feedback
into `task_summary`/`acceptance_criteria` before calling `execute()`
again, the same way the UX Agent's revision loop threads feedback back in
as an additional *input* rather than a side channel. Flagged as a real
gap noticed once the canonical interface was in hand, not resolved here.

## What was actually verified in this environment vs. assumed from docs

This project has already had to walk back one wrong assumption about this
exact tool (section 20 open question 2's history), so this module stays
explicit about what was checked directly against the installed package
versus taken from documentation/prior research.

**Verified** (by installing `github-copilot-sdk==1.0.9` from PyPI into an
isolated Python 3.13 venv -- required, since it declares Python 3.11+,
stricter than this repo's own `pyproject.toml` `>=3.10`; see that file's
`[project.optional-dependencies]` entry -- and introspecting the actual
installed classes/signatures, not just reading docs):

  - The package installs cleanly; its PyPI "Home-page" is
    `https://github.com/github/copilot-sdk`, confirming it's the SDK the
    architecture doc means.
  - `copilot.CopilotClient(working_directory=..., ...)` is real;
    `start()`/`stop()` are async. `client.create_session(on_permission_
    request=..., on_user_input_request=..., model=..., working_directory=
    ..., ...) -> CopilotSession` is real and async.
  - `session.send_and_wait(prompt, *, agent_mode: Literal["interactive",
    "plan", "autopilot", "shell"] | None = None, timeout: float = 60.0) ->
    SessionEvent | None` is real and async -- confirms the brief's claim
    that an "Autopilot" mode exists as a real, typed parameter.
  - `on_permission_request`'s payload is a discriminated union
    (`PermissionRequestShell | PermissionRequestWrite | PermissionRequestRead
    | PermissionRequestMcp | PermissionRequestUrl | ...`). The shell variant
    has `commands: list[{identifier: str, read_only: bool}]` and
    `full_command_text: str`. Decisions returned from the callback include
    `PermissionDecisionApproveOnce(approved_interactively: bool | None)`
    and `PermissionDecisionReject(feedback: str | None)`.
  - `PermissionRequestShell`/`PermissionDecisionApproveOnce`/`Reject` are
    real classes with attribute access -- but `copilot.session.
    UserInputRequest`/`UserInputResponse` are **`TypedDict`s**, plain
    `dict`s at runtime, not attribute-bearing objects. A first draft using
    `getattr()` uniformly silently returned defaults instead of the real
    question text; `_field()` below normalizes both shapes at every
    callback read site. This is a real, verified SDK inconsistency, not a
    hypothetical.
  - **Corrects the architecture doc's section 20 open-question-2 note**:
    the doc says prior research "found no confirmed distinct event type"
    for a mid-session clarifying question. That does not hold for
    `github-copilot-sdk` 1.0.9: `create_session` accepts a distinct
    `on_user_input_request` callback, separate from `on_permission_request`,
    carrying `UserInputRequest(question, choices, allowFreeform)` and
    expecting `UserInputResponse(answer, wasFreeform)` back. The SDK
    *does* have the event type the doc's prior research says doesn't
    exist. This is an implementation detail of *this provider* -- it does
    not change the shared `coding.py` interface, which stays silent on
    mid-session pauses for both providers (Claude's own equivalent
    resolution, `permission_mode="dontAsk"`, similarly never surfaces a
    live prompt). See `_make_user_input_handler` for how this provider
    auto-answers it unattended, same "final structured verdict" model
    section 8/20 already establish.

**Not verified**: no authenticated Copilot CLI session exists in this
environment, so no `create_session()`/`send_and_wait()` call here has
been exercised against a live Copilot backend end-to-end. Field names
were confirmed via `typing.get_type_hints` against the installed
package's real classes -- stronger than reading docs, still short of an
actual session round-trip. `SessionEvent`'s own field shape (used in
`_map_termination` to detect a reported failure) was *not*
individually verified the way permission/user-input types were
(time-boxed) -- `_field()`-based defensive reads there, mirroring
`claude_sdk.py`'s own documented defensive-`getattr` stance on
`ResultMessage`'s field names.

## Design choices specific to this provider (flagged, not silently assumed)

  - **`allowed_tools` -> Copilot permission-kind mapping is this
    provider's own interpretation**, not something `coding.py` specifies
    (it deliberately stays SDK-agnostic on tool-allow-list syntax, the
    same way `claude_sdk.py`'s `Bash(<command> *)` scoped-rule syntax is
    that provider's own interpretation). This provider maps Copilot's
    permission-request `kind` (`shell`/`write`/`read`/`mcp`/`url`/...)
    onto the closest Claude-style tool name(s) in `request.allowed_tools`
    (`Bash`, `Write`/`Edit`, `Read`, ...) -- see `_KIND_TO_TOOL_NAMES`.
    Shell commands additionally require the specific command's basename
    to appear in `request.allowed_commands`, mirroring `claude_sdk.py`'s
    two-axis enforcement (`allowed_tools` gates whether the tool exists at
    all; `allowed_commands` gates which commands within it).
  - **Hardcoded push/PR denial, unconditional**: like `claude_sdk.py`'s
    `_HARD_DISALLOWED_TOOLS`, this provider denies any shell command whose
    text contains a push/PR-open pattern regardless of what
    `allowed_commands` says -- pushing/opening a PR is the Developer
    Agent's job, only after human approval (section 4/6), never this
    capability's.
  - **`steps_used` is a best-effort proxy, not a literal turn count**:
    this provider does not re-drive Copilot's `agent_mode="autopilot"`
    loop step-by-step from Nova's side -- autopilot is already Copilot's
    own bounded-loop execution, and fighting it with an external step
    counter would work against the SDK rather than use it. `steps_used`
    is populated from the session's own event count
    (`len(session.get_events())`) as an observability proxy;
    `request.max_steps`/`DEFAULT_MAX_STEPS` is enforced as an overall
    **wall-clock session timeout** passed to `send_and_wait` instead of a
    literal step ceiling. `TerminationReason.STEP_BUDGET_EXHAUSTED` is
    reported when the event count reaches that budget as a proxy signal.
    This is a real, documented interpretation choice for how this
    provider maps a session-based, autopilot-driven SDK onto a
    step-counted shared contract -- not a verified 1:1 correspondence.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from ai_sdlc.capabilities.coding import (
    DEFAULT_MAX_STEPS,
    NO_SELF_CHECK_COMMANDS_REASON,
    CodingCapability,
    CodingRequest,
    CodingResult,
    MalformedResponseError,
    ProviderError,
    SelfCheckResult,
    TerminationReason,
)

try:
    import copilot as _copilot_sdk
    from copilot.generated import rpc as _copilot_rpc

    _COPILOT_SDK_IMPORT_ERROR: Optional[BaseException] = None
except ImportError as exc:  # pragma: no cover - exercised only when the
    # optional `github-copilot-sdk` extra (and its Python 3.11+
    # requirement) isn't installed. Import failure is deferred to
    # provider construction, not module import, so this module can still
    # be imported (and appear in test collection) without the SDK
    # present, matching this project's "tests never require real
    # provider credentials" convention.
    _copilot_sdk = None
    _copilot_rpc = None
    _COPILOT_SDK_IMPORT_ERROR = exc

PROVIDER_NAME = "github_copilot_sdk"

#: Rough, documented heuristic (not a measured constant -- see module
#: docstring's step-budget note): how many wall-clock seconds one
#: `max_steps` unit is worth when translated into `send_and_wait`'s
#: `timeout`. Chosen conservatively; revisit once real session timing
#: data exists.
_STEP_TO_SECONDS_FACTOR = 20.0
_MIN_SESSION_TIMEOUT_SECONDS = 120.0

#: Shell substrings this provider refuses regardless of the caller's
#: `allowed_commands` -- mirrors `claude_sdk.py`'s unconditional
#: `Bash(git push *)` denial.
_HARD_DISALLOWED_SHELL_SUBSTRINGS = ["git push", "gh pr create", "gh pr merge"]

#: Best-effort mapping from a Copilot permission-request `kind` onto the
#: Claude-style tool name(s) that would need to be present in
#: `request.allowed_tools` for it to be granted. See module docstring.
_KIND_TO_TOOL_NAMES: Dict[str, tuple] = {
    "shell": ("Bash",),
    "write": ("Write", "Edit"),
    "read": ("Read",),
    "mcp": ("Mcp",),
    "url": ("Url", "WebFetch"),
    "memory": ("Memory",),
    "customTool": ("CustomTool",),
    "hook": ("Hook",),
}

_GIT_INTROSPECTION_TIMEOUT_SECONDS = 30
_DEFAULT_SELF_CHECK_TIMEOUT_SECONDS = 600


def _command_basename(identifier: str) -> str:
    token = (identifier or "").strip().split()[0] if identifier.strip() else ""
    return token.rsplit("/", 1)[-1]


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """Read `name` off `obj` regardless of whether the SDK represents it
    as a real attribute-bearing object or a `TypedDict` (a plain `dict` at
    runtime) -- see module docstring's verified-facts section for why this
    is necessary, not defensive-for-its-own-sake."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _run_subprocess(
    args_or_command: Any, *, cwd: str, timeout: int, shell: bool = False
) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        args_or_command,
        cwd=cwd,
        shell=shell,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )


class CopilotCodingProvider(CodingCapability):
    """Real `CodingCapability` implementation backed by
    `github/copilot-sdk`. Requires the optional `copilot` extra and an
    authenticated Copilot CLI session (`use_logged_in_user=True` by
    default, matching section 10's "Local CLI binds to developer's
    GitHub/SSO session token" -- this provider does not manage
    credentials itself)."""

    def __init__(
        self,
        *,
        model: Optional[str] = None,
        self_check_timeout_seconds: int = _DEFAULT_SELF_CHECK_TIMEOUT_SECONDS,
    ):
        if _COPILOT_SDK_IMPORT_ERROR is not None:
            raise ProviderError(
                "copilot_provider: github-copilot-sdk is not usable in this "
                f"environment ({_COPILOT_SDK_IMPORT_ERROR!r}); install the optional "
                "extra with `pip install ai-sdlc[copilot]` (requires Python 3.11+). "
                "See this module's docstring for what was and wasn't independently "
                "verified about this dependency."
            )
        self.model = model
        self._self_check_timeout_seconds = self_check_timeout_seconds
        self._clarification_log: List[str] = []

    # -- CodingCapability ---------------------------------------------------

    def execute(self, request: CodingRequest) -> CodingResult:
        self._verify_working_tree(request.working_tree_path)
        max_steps = request.max_steps or DEFAULT_MAX_STEPS
        self._clarification_log = []

        try:
            final_event, steps_used = self._run_async(self._run_session(request, max_steps))
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - any unexpected SDK/subprocess
            # failure must still surface as ProviderError per this
            # capability's failure contract, never an arbitrary exception.
            raise ProviderError(
                f"copilot_provider: session failed before producing a result: {exc}"
            ) from exc

        return self._build_coding_result(request, final_event, steps_used, max_steps)

    # -- session orchestration -----------------------------------------------

    async def _run_session(self, request: CodingRequest, max_steps: int) -> Any:
        timeout = max(_MIN_SESSION_TIMEOUT_SECONDS, max_steps * _STEP_TO_SECONDS_FACTOR)

        client = _copilot_sdk.CopilotClient(
            working_directory=request.working_tree_path,
            use_logged_in_user=True,
        )
        await client.start()
        try:
            session = await client.create_session(
                on_permission_request=self._make_permission_handler(request),
                on_user_input_request=self._make_user_input_handler(),
                model=self.model,
                working_directory=request.working_tree_path,
            )
            try:
                final_event = await session.send_and_wait(
                    self._build_prompt(request), agent_mode="autopilot", timeout=timeout
                )
                steps_used = await self._estimate_steps_used(session)
            finally:
                await session.disconnect()
        finally:
            await client.stop()

        return final_event, steps_used

    def _run_async(self, coro: Any) -> Any:
        """Run `coro` to completion, safe whether or not the caller
        already has an event loop running -- mirrors `claude_sdk.py`'s
        identical `_run_async` pattern, since `CodingCapability.execute()`
        is synchronous by contract (section 8) but may be called from
        inside an async caller's coroutine (Base Agent's own `execute()`
        is `async def`, section 4)."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()

    # -- prompt assembly ------------------------------------------------------

    def _build_prompt(self, request: CodingRequest) -> str:
        sections: List[str] = [f"Task: {request.task_title}", "", request.task_summary]

        def _bulleted(heading: str, items: List[str]) -> None:
            if items:
                sections.extend(["", f"{heading}:"] + [f"- {item}" for item in items])

        _bulleted("Functional requirements", request.functional_requirements)
        _bulleted("Non-functional requirements", request.non_functional_requirements)
        _bulleted("Acceptance criteria", request.acceptance_criteria)
        _bulleted("Technology stack", request.tech_stack)
        _bulleted("Components affected", request.components_affected)
        _bulleted("UX notes", request.ux_notes)

        if request.standards_instructions:
            sections.extend(
                ["", "Project standards and conventions (must follow):", request.standards_instructions]
            )
        if request.standards_skills:
            sections.extend(
                ["", "Relevant standards references: " + ", ".join(request.standards_skills)]
            )

        sections.extend(
            [
                "",
                "Constraints:",
                f"- You are working inside an isolated Git working tree at "
                f"{request.working_tree_path}; never read or write anything outside it.",
                "- Commit your changes to git yourself as you go, on the current branch. "
                "Do not push to any remote and do not open a pull request -- that only "
                "happens later, after a human approves this change.",
                "- Only use the tools and commands you have explicitly been granted.",
            ]
        )
        return "\n".join(sections)

    # -- permission / user-input callbacks -------------------------------------

    def _make_permission_handler(self, request: CodingRequest):
        allowed_tools = request.allowed_tools
        allowed_commands = request.allowed_commands

        async def handler(perm_request: Any):
            kind = _field(perm_request, "kind")

            if kind == "shell":
                full_text = _field(perm_request, "full_command_text", "") or ""
                if any(bad in full_text for bad in _HARD_DISALLOWED_SHELL_SUBSTRINGS):
                    return _copilot_rpc.PermissionDecisionReject(
                        feedback="push/PR-open commands are never permitted from this capability"
                    )
                if "Bash" not in allowed_tools:
                    return _copilot_rpc.PermissionDecisionReject(
                        feedback="'Bash' is not in CodingRequest.allowed_tools"
                    )
                identifiers = [
                    _command_basename(_field(cmd, "identifier", ""))
                    for cmd in _field(perm_request, "commands", [])
                ]
                if identifiers and all(ident in allowed_commands for ident in identifiers):
                    return _copilot_rpc.PermissionDecisionApproveOnce(approved_interactively=False)
                return _copilot_rpc.PermissionDecisionReject(
                    feedback=f"not in CodingRequest.allowed_commands: {identifiers}"
                )

            tool_names = _KIND_TO_TOOL_NAMES.get(kind, ())
            if any(name in allowed_tools for name in tool_names):
                return _copilot_rpc.PermissionDecisionApproveOnce(approved_interactively=False)
            return _copilot_rpc.PermissionDecisionReject(
                feedback=(
                    f"permission kind {kind!r} requires one of {tool_names} in "
                    "CodingRequest.allowed_tools"
                )
            )

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
            return _copilot_sdk.session.UserInputResponse(answer=answer, wasFreeform=was_freeform)

        return handler

    async def _estimate_steps_used(self, session: Any) -> int:
        try:
            events = session.get_events()
            if asyncio.iscoroutine(events):
                events = await events
            return max(0, len(events))
        except Exception:  # noqa: BLE001 - best-effort proxy only, see
            # module docstring's step-budget note; never let this fail
            # the call.
            return 0

    # -- post-session verdict ---------------------------------------------------

    def _build_coding_result(
        self, request: CodingRequest, final_event: Any, steps_used: int, max_steps: int
    ) -> CodingResult:
        terminated_reason = self._map_termination(final_event, steps_used, max_steps)
        branch_name = self._current_branch(request.working_tree_path)
        files_changed = self._files_changed(request.working_tree_path, request.base_branch)
        self_check = self._run_self_check(request)

        summary_text = _field(final_event, "result", None) or _field(final_event, "summary", None)
        summary = summary_text if isinstance(summary_text, str) and summary_text.strip() else (
            f"Applied changes for: {request.task_title}."
        )

        payload: Dict[str, Any] = {
            "branch_name": branch_name,
            "files_changed": files_changed,
            "self_check": self_check,
            "provider_name": PROVIDER_NAME,
            "steps_used": steps_used,
            "terminated_reason": terminated_reason,
            "summary": summary,
            "metadata": {
                "clarification_questions_auto_answered": list(self._clarification_log),
                "model": self.model,
            },
        }
        try:
            return CodingResult(**payload)
        except ValidationError as exc:
            raise MalformedResponseError(
                f"copilot_provider: session outcome failed CodingResult validation: {exc}"
            ) from exc

    def _map_termination(self, final_event: Any, steps_used: int, max_steps: int) -> TerminationReason:
        if steps_used >= max_steps:
            return TerminationReason.STEP_BUDGET_EXHAUSTED
        is_error = bool(_field(final_event, "is_error", False) or _field(final_event, "error", False))
        if is_error:
            return TerminationReason.PROVIDER_REPORTED_FAILURE
        return TerminationReason.COMPLETED

    # -- git introspection (ground truth, not model self-report) ----------------

    def _verify_working_tree(self, working_tree_path: str) -> None:
        path = Path(working_tree_path)
        if not path.is_dir():
            raise ProviderError(
                f"copilot_provider: working_tree_path {working_tree_path!r} does not "
                "exist or is not a directory"
            )
        if not (path / ".git").exists():
            raise ProviderError(
                f"copilot_provider: working_tree_path {working_tree_path!r} is not a "
                "Git working tree (no .git found). This provider cannot itself confirm "
                "the path is a *disposable, isolated* worktree rather than a live "
                "checkout -- that guarantee is the caller's responsibility (see "
                "coding.py's module docstring) -- but it refuses to proceed against a "
                "path that isn't a Git working tree at all."
            )

    def _current_branch(self, working_tree_path: str) -> str:
        try:
            completed = _run_subprocess(
                ["git", "-C", working_tree_path, "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=working_tree_path,
                timeout=_GIT_INTROSPECTION_TIMEOUT_SECONDS,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise ProviderError(
                f"copilot_provider: could not determine current branch in "
                f"{working_tree_path!r}: {exc}"
            ) from exc
        if completed.returncode != 0:
            raise ProviderError(
                f"copilot_provider: `git rev-parse --abbrev-ref HEAD` failed in "
                f"{working_tree_path!r}: {completed.stdout.strip()}"
            )
        return completed.stdout.strip()

    def _files_changed(self, working_tree_path: str, base_branch: str) -> List[str]:
        try:
            completed = _run_subprocess(
                ["git", "-C", working_tree_path, "diff", "--name-only", f"{base_branch}...HEAD"],
                cwd=working_tree_path,
                timeout=_GIT_INTROSPECTION_TIMEOUT_SECONDS,
            )
        except (subprocess.TimeoutExpired, OSError):
            return []
        if completed.returncode != 0:
            return []
        return [line for line in completed.stdout.splitlines() if line.strip()]

    # -- self-check (section 4; Open Question 7 answered in coding.py) -----------

    def _run_self_check(self, request: CodingRequest) -> SelfCheckResult:
        if not request.build_commands and not request.test_commands:
            return SelfCheckResult.skipped(NO_SELF_CHECK_COMMANDS_REASON)

        build_passed = (
            self._run_commands(request.build_commands, request.working_tree_path)
            if request.build_commands
            else None
        )
        tests_passed = (
            self._run_commands(request.test_commands, request.working_tree_path)
            if request.test_commands
            else None
        )
        return SelfCheckResult(
            build_passed=build_passed,
            tests_passed=tests_passed,
            commands_run=list(request.build_commands) + list(request.test_commands),
        )

    def _run_commands(self, commands: List[str], working_tree_path: str) -> bool:
        for command in commands:
            try:
                completed = _run_subprocess(
                    command,
                    cwd=working_tree_path,
                    timeout=self._self_check_timeout_seconds,
                    shell=True,
                )
            except (subprocess.TimeoutExpired, OSError):
                return False
            if completed.returncode != 0:
                return False
        return True
