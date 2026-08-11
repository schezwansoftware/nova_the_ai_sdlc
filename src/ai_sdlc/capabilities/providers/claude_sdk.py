"""Claude Agent SDK-backed `CodingCapability` provider.

This is Nova's confirmed V1/default coding provider ("claude.ai is correct
choice for first", decided 2026-08-10). It drives the `claude-agent-sdk`
Python package -- itself a thin wrapper around a `claude` CLI subprocess
-- in a fully unattended mode, scoped to an already-isolated Git working
tree (`CodingRequest.working_tree_path`, created by the caller -- see
`coding.py`'s module docstring), with the caller's own allow-list enforced
by denying anything outside it outright rather than prompting.

## What is verified vs. documentation-only in this environment

`claude-agent-sdk` was **not** independently verified end-to-end
(installed, imported, and exercised against a real `claude` CLI session)
in the sandbox this provider was built in. A background `pip install
claude-agent-sdk` was attempted but did not complete within a bounded
time budget, and there is no `claude` CLI on `$PATH` here either. The
package (`pip index versions claude-agent-sdk` succeeded, latest
`0.2.135`) and a `claude` CLI are both real, published artifacts, so
installability itself is not in question -- what's unverified is this
provider's exact runtime behavior against them.

Everything below is instead built against the **current official docs**,
fetched directly rather than relied on from training-data memory (per
this project's validated-facts-only convention, since SDKs move fast):
`https://code.claude.com/docs/en/agent-sdk/python` (API shape) and
`https://code.claude.com/docs/en/agent-sdk/permissions` (permission modes
and allow/deny rule evaluation order) -- both fetched 2026-08-11. Two
specific things follow directly from that second page and are worth
calling out because they overturned this provider's first-draft design:

  - `permission_mode="dontAsk"` is the mode the docs *themselves*
    recommend for "a fixed, explicit tool surface for a headless agent"
    that should hard-deny anything outside an allow-list rather than
    prompt -- confirming architecture doc section 4/10's requirement
    exactly, and resolving section 20 Open Question 2's "final structured
    verdict, no live pause" framing (there is no confirmed mid-session
    clarification feature; `dontAsk` guarantees no prompt is ever raised
    for this provider to get stuck waiting on).
  - Critically, **`dontAsk` mode never calls `can_use_tool`** ("this step
    is skipped and the tool is denied"). A `canUseTool` callback is
    therefore useless for enforcing `CodingRequest.allowed_commands` under
    this mode -- the enforcement has to happen through `allowed_tools`
    itself, using scoped `Bash(<command> *)` allow-rule entries (the docs'
    own `Bash(ls *)`/`Bash(rm *)` scoped-rule examples confirm this syntax
    is supported), never a bare `"Bash"` entry (which would auto-approve
    *every* Bash call regardless of `allowed_commands`). See
    `_build_allowed_tools` below.

What is **not** independently verified and is instead a best-effort,
defensive reading of secondary/summarized sources: the exact field names
on the SDK's terminal `ResultMessage` (e.g. whether cost is
`total_cost_usd` vs. nested under a `Cost` object, exact `is_error`/
`num_turns` semantics). `_extract_result_fields` below uses `getattr(...,
default)` throughout rather than asserting a fixed dataclass shape, so a
field-name mismatch degrades to a documented default instead of raising --
this is the one area a follow-up pass with a real, working install should
tighten.

## Design decisions this provider makes (flag for reconciliation)

  - Uses the stateless `query()` function, not `ClaudeSDKClient` -- this
    provider needs exactly one bounded task per `execute()` call with no
    mid-session permission-mode changes or interrupts, which is what
    `query()` is documented for.
  - Never passes a bare `"Bash"` allow-tools entry (see above); `Bash` is
    only ever granted via scoped `Bash(<command> *)` entries derived from
    `CodingRequest.allowed_commands`.
  - Hardcodes `disallowed_tools=["Bash(git push *)"]` unconditionally,
    regardless of what the caller's `allowed_commands` says. Pushing a
    branch is explicitly the Developer Agent's job, and only *after*
    human approval (section 4/6) -- this capability must never do it,
    full stop, so this is not caller-configurable the way the rest of the
    allow-list is.
  - Computes `files_changed`/`branch_name` from `git` itself after the
    session ends (`git diff --name-only <base_branch>...HEAD`, `git
    rev-parse --abbrev-ref HEAD`), not from asking the model to self-
    report a JSON change summary. Ground truth from the actual working
    tree is more robust than trusting the model's own account of what it
    did, and this is exactly the kind of provider-internal detail the
    shared `coding.py` interface is deliberately silent on.
  - Runs `CodingRequest.build_commands`/`test_commands` itself, as plain
    subprocesses against `working_tree_path`, independent of the SDK
    session's own tool use -- see `coding.py`'s module docstring for why
    self-check lives inside the capability rather than deferred to the
    not-yet-built Developer Agent.
  - `CodingCapability.execute()` is a synchronous, single-call interface
    by design (section 8), but the SDK is async and Base Agent's own
    `execute()` (section 4) is itself `async def`. Calling `asyncio.run()`
    unconditionally would break when a caller invokes this from within its
    own running event loop ("asyncio.run() cannot be called from a running
    event loop"). `_run_async` below detects that case and bridges it via
    a dedicated thread instead, so this provider is safe to call
    synchronously from *either* a sync caller or from inside an async
    caller's coroutine -- this is a real, load-bearing judgment call, not
    a stylistic one, and is worth double-checking against however the
    eventual Developer Agent actually calls `CodingCapability`.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
    from claude_agent_sdk import ClaudeAgentOptions as _SDKClaudeAgentOptions
    from claude_agent_sdk import ResultMessage as _SDKResultMessage
    from claude_agent_sdk import query as _sdk_query

    SDK_IMPORT_ERROR: Optional[BaseException] = None
except ImportError as exc:  # pragma: no cover - exercised only when the SDK is absent
    _SDKClaudeAgentOptions = None
    _SDKResultMessage = None
    _sdk_query = None
    SDK_IMPORT_ERROR = exc

PROVIDER_NAME = "claude_agent_sdk"

#: Commands this provider refuses to run regardless of the caller's
#: `allowed_commands` -- see module docstring.
_HARD_DISALLOWED_TOOLS = ["Bash(git push *)"]

#: Bound on each individual self-check command, so a hung build/test
#: process can't block `execute()` forever.
_DEFAULT_SELF_CHECK_TIMEOUT_SECONDS = 600

#: Bound on `git`/introspection subprocess calls this provider makes
#: itself (as opposed to commands the agent session runs) -- these should
#: always be near-instant; a generous bound just guards against a wedged
#: filesystem/process rather than expecting to ever be hit.
_GIT_INTROSPECTION_TIMEOUT_SECONDS = 30


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


class ClaudeAgentSDKProvider(CodingCapability):
    """The real, default V1 `CodingCapability` provider.

    Test seams (`_query_fn`/`_options_cls`/`_result_message_cls`) let
    tests exercise this provider's orchestration logic -- prompt
    assembly, allow-list composition, git introspection, self-check
    execution, termination mapping -- with a fake SDK, without requiring
    `claude-agent-sdk` to be installed or a real `claude` CLI to be
    available. In production, leaving them unset means the real package
    is required; if it isn't importable, the constructor raises
    `ProviderError` immediately rather than failing later inside
    `execute()`.
    """

    def __init__(
        self,
        *,
        cli_path: Optional[str] = None,
        self_check_timeout_seconds: int = _DEFAULT_SELF_CHECK_TIMEOUT_SECONDS,
        _query_fn: Optional[Any] = None,
        _options_cls: Optional[Any] = None,
        _result_message_cls: Optional[Any] = None,
    ) -> None:
        self._cli_path = cli_path
        self._self_check_timeout_seconds = self_check_timeout_seconds
        self._query_fn = _query_fn if _query_fn is not None else _sdk_query
        self._options_cls = _options_cls if _options_cls is not None else _SDKClaudeAgentOptions
        self._result_message_cls = (
            _result_message_cls if _result_message_cls is not None else _SDKResultMessage
        )

        if self._query_fn is None or self._options_cls is None or self._result_message_cls is None:
            raise ProviderError(
                "claude_agent_sdk_provider: the `claude-agent-sdk` package is not "
                f"usable in this environment ({SDK_IMPORT_ERROR!r}); install it with "
                "`pip install claude-agent-sdk` and ensure a `claude` CLI is on PATH "
                "(or pass cli_path=...). See this module's docstring for what was and "
                "wasn't independently verified about this dependency."
            )

    # -- CodingCapability -------------------------------------------------

    def execute(self, request: CodingRequest) -> CodingResult:
        self._verify_working_tree(request.working_tree_path)
        max_turns = request.max_steps or DEFAULT_MAX_STEPS

        try:
            result_message, steps_used = self._run_async(self._run_session(request, max_turns))
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(
                f"claude_agent_sdk_provider: session failed before producing a result: {exc}"
            ) from exc

        return self._build_coding_result(request, result_message, steps_used, max_turns)

    # -- session orchestration --------------------------------------------

    async def _run_session(self, request: CodingRequest, max_turns: int) -> Tuple[Any, int]:
        options = self._options_cls(
            cwd=request.working_tree_path,
            allowed_tools=self._build_allowed_tools(request),
            disallowed_tools=list(_HARD_DISALLOWED_TOOLS),
            permission_mode="dontAsk",
            max_turns=max_turns,
            cli_path=self._cli_path,
        )
        prompt = self._build_prompt(request)

        result_message = None
        steps_used = 0
        async for message in self._query_fn(prompt=prompt, options=options):
            steps_used += 1
            if isinstance(message, self._result_message_cls):
                result_message = message

        if result_message is None:
            raise ProviderError(
                "claude_agent_sdk_provider: session ended without a terminal ResultMessage"
            )
        return result_message, steps_used

    def _run_async(self, coro: Any) -> Any:
        """Run `coro` to completion, safe to call whether or not the
        caller already has an event loop running (see module docstring).
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()

    # -- allow-list / prompt assembly -------------------------------------

    def _build_allowed_tools(self, request: CodingRequest) -> List[str]:
        """Compose the SDK's `allowed_tools` list from the request's two
        allow-list axes. Never emits a bare `"Bash"` entry -- see module
        docstring for why that would silently defeat `allowed_commands`.
        """
        entries: List[str] = [tool for tool in request.allowed_tools if tool != "Bash"]
        if "Bash" in request.allowed_tools:
            entries.extend(f"Bash({command} *)" for command in request.allowed_commands)
        return entries

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

    # -- post-session verdict -----------------------------------------------

    def _build_coding_result(
        self,
        request: CodingRequest,
        result_message: Any,
        steps_used: int,
        max_turns: int,
    ) -> CodingResult:
        fields = self._extract_result_fields(result_message)
        terminated_reason = self._map_termination(fields, steps_used, max_turns)

        branch_name = self._current_branch(request.working_tree_path)
        files_changed = self._files_changed(request.working_tree_path, request.base_branch)
        self_check = self._run_self_check(request)

        summary = fields["result_text"]
        if not isinstance(summary, str) or not summary.strip():
            summary = f"Applied changes for: {request.task_title}."

        payload: Dict[str, Any] = {
            "branch_name": branch_name,
            "files_changed": files_changed,
            "self_check": self_check,
            "provider_name": PROVIDER_NAME,
            "steps_used": steps_used,
            "terminated_reason": terminated_reason,
            "summary": summary,
            "metadata": {
                "session_id": fields["session_id"],
                "total_cost_usd": fields["total_cost_usd"],
                "sdk_reported_error": fields["is_error"],
            },
        }
        try:
            return CodingResult(**payload)
        except ValidationError as exc:
            raise MalformedResponseError(
                f"claude_agent_sdk_provider: session outcome failed CodingResult validation: {exc}"
            ) from exc

    def _extract_result_fields(self, result_message: Any) -> Dict[str, Any]:
        """Defensive `getattr`-based extraction -- see module docstring's
        "not independently verified" section for why this doesn't assert
        a fixed `ResultMessage` shape."""
        return {
            "is_error": bool(getattr(result_message, "is_error", False)),
            "num_turns": getattr(result_message, "num_turns", None),
            "session_id": getattr(result_message, "session_id", None),
            "total_cost_usd": getattr(result_message, "total_cost_usd", None),
            "result_text": getattr(result_message, "result", None),
        }

    def _map_termination(
        self, fields: Dict[str, Any], steps_used: int, max_turns: int
    ) -> TerminationReason:
        num_turns = fields["num_turns"] if fields["num_turns"] is not None else steps_used
        if num_turns >= max_turns:
            return TerminationReason.STEP_BUDGET_EXHAUSTED
        if fields["is_error"]:
            return TerminationReason.PROVIDER_REPORTED_FAILURE
        return TerminationReason.COMPLETED

    # -- git introspection (ground truth, not model self-report) -----------

    def _verify_working_tree(self, working_tree_path: str) -> None:
        path = Path(working_tree_path)
        if not path.is_dir():
            raise ProviderError(
                f"claude_agent_sdk_provider: working_tree_path {working_tree_path!r} "
                "does not exist or is not a directory"
            )
        if not (path / ".git").exists():
            raise ProviderError(
                f"claude_agent_sdk_provider: working_tree_path {working_tree_path!r} is "
                "not a Git working tree (no .git found). This provider cannot itself "
                "confirm the path is a *disposable, isolated* worktree rather than a "
                "live checkout -- that guarantee is the caller's responsibility (see "
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
                f"claude_agent_sdk_provider: could not determine current branch in "
                f"{working_tree_path!r}: {exc}"
            ) from exc
        if completed.returncode != 0:
            raise ProviderError(
                f"claude_agent_sdk_provider: `git rev-parse --abbrev-ref HEAD` failed in "
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

    # -- self-check (section 4; Open Question 7 answered in coding.py) -----

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
        """Run each command as its own subprocess against the working
        tree, independent of the agent session's own tool use. Commands
        come from caller-supplied workspace/Standards config, never from
        anything the coding agent itself produced, so `shell=True` here
        doesn't introduce a new injection surface beyond what the caller
        already controls by supplying `build_commands`/`test_commands`.
        """
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
