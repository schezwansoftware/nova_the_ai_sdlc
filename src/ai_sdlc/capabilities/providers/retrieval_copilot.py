"""GitHub Copilot SDK-backed, read-only `RetrievalCapability` provider.

The Copilot-SDK-driven sibling of `providers/retrieval_claude.py`: same
`RetrievalCapability` contract (`RetrievalRequest` in, validated
`RetrievalResult` out, same `ProviderError`/`MalformedResponseError`
failure contract), driving `github/copilot-sdk` instead of
`claude-agent-sdk`. Session-lifecycle plumbing (`CopilotClient`/
`create_session`/`send_and_wait`, the `on_permission_request`/
`on_user_input_request` callback shapes, `_field()`'s `TypedDict`-vs-
attribute-object normalization, `_run_async`'s event-loop bridging) is
copied and adapted from `providers/coding_copilot.py`, not imported --
mirroring how `retrieval_claude.py` itself copies rather than imports
from `claude_sdk.py` (see that module's docstring for why: each
capability's provider stays self-contained, matching `capabilities/
__init__.py`'s documented convention). Repurposed throughout for
unattended, read-only exploration instead of code editing: no working
tree to set up, no self-check commands to run, no branch/diff
introspection, no git-push denial (there is nothing to push).

## No worktree isolation

Like `retrieval_claude.py`, this provider points `working_directory`
straight at `request.repository_path` -- the real target repository, not
an isolated worktree. `coding_copilot.py` needs isolation because its
session is granted write/shell permissions; this provider's permission
callback (below) never approves anything that could mutate the
repository, so there is nothing for isolation to protect against. See
`retrieval.py`'s module docstring for the full reasoning; this provider
does not repeat `coding_copilot.py`'s worktree-verification logic at all.

## Read-only enforcement: what's verified vs. assumed here

`coding_copilot.py`'s docstring already documents what was verified by
installing `github-copilot-sdk==1.0.9` into an isolated Python 3.13 venv
and introspecting the installed classes directly, rather than trusting
docs alone. This provider's permission enforcement rests on one
additional fact checked the same way, against that same installed
package:

  - **The complete permission-request `kind` enumeration.** Reading each
    `PermissionRequest*` dataclass's `kind: ClassVar[str]` default off
    `copilot.generated.session_events` gives exactly: `"shell"`,
    `"write"`, `"read"`, `"mcp"`, `"url"`, `"memory"`, `"custom-tool"`,
    `"hook"`, `"extension-management"`, `"extension-permission-access"`,
    `"factory"`. There is **no distinct `"search"`/`"grep"` kind** in
    this SDK version. Whatever read-only file exploration/search
    Copilot's agent performs during a session is therefore gated under
    the single `"read"` kind (a `PermissionRequestRead` carries a `path`,
    consistent with per-file-or-directory read access) -- so approving
    `"read"` and rejecting every other kind, unconditionally, is what
    "read/search operations only, no exceptions" collapses to for this
    SDK version. `_ALLOWED_KINDS` below is intentionally just
    `{"read"}`, not a larger set -- if a future SDK version adds a
    genuinely distinct search-only kind, this set is the one place to
    revisit.
  - **`"shell"` is never approved, not even for read-only-looking
    commands.** `coding_copilot.py`'s own verified-facts section notes
    each shell permission request's `commands` carry a per-command
    `read_only: bool` flag. It is tempting to approve shell requests
    where every command claims `read_only=True` (a natural way to permit
    `grep`/`find`/`cat`-style search) -- deliberately not done here. That
    flag is the *tool's own self-report* about what a command does, not
    something this provider can independently verify before the command
    runs; trusting it would mean trusting the model/tool not to mislabel
    a command, exactly the weaker guarantee section 18 Decision 5 rejects
    ("structurally incapable... rather than merely trusted not to").
    `"shell"` is therefore in the deny set unconditionally, same as every
    other non-`"read"` kind -- consistent with `coding_copilot.py`'s own
    unconditional `git push`/`gh pr create`/`gh pr merge` denial
    regardless of what `allowed_commands` says, just extended to the
    entire kind instead of a substring blocklist.
  - **A stronger, structural alternative exists but is deliberately not
    used.** `CopilotClient.create_session()` accepts `available_tools`/
    `excluded_tools` (`list[str] | ToolSet`, entries like
    `"builtin:bash"`), which -- per `copilot._mode.ToolSet`'s own
    docstring -- removes tool *definitions* from what the agent can even
    attempt, the true Copilot-side equivalent of `retrieval_claude.py`'s
    bare `disallowed_tools` entries. It is not used here because the
    concrete non-isolated builtin tool-name strings (whatever the runtime
    registers for "read a file", "run a shell command", "write a file",
    etc.) are determined by the runtime at registration time, not
    documented or enumerable from the installed package (only
    `_mode.BUILTIN_TOOLS_ISOLATED` -- a different, session-scoped tool
    family unrelated to filesystem access -- is actually enumerated).
    Passing guessed names to `excluded_tools` risks a silent no-op if a
    guess doesn't match the runtime's real registration, which is worse
    than not using it at all: it would look like structural enforcement
    without being any. The `on_permission_request` callback below is the
    verified, always-invoked control point instead (every kind must be
    explicitly approved; there is no bypass), which is why it -- not
    `excluded_tools` -- carries the actual guarantee here. Upgrading to
    `excluded_tools` as defense-in-depth is a reasonable follow-up once a
    live runtime is available to confirm the real builtin tool-name
    strings against.

## Snippet/summary extraction: same simplification as `retrieval_claude.py`

`coding_copilot.py`'s docstring already flags that `SessionEvent`'s own
field shape was not individually verified against a live session (no
authenticated Copilot session was available to exercise
`send_and_wait()` end-to-end). Building snippet extraction from
structured tool-call-result events would mean depending on exactly the
part of the shape that's least verified. Rather than guess at it, this
provider reuses `retrieval_claude.py`'s approach unchanged in spirit:
prompt-instruct the agent to end its final answer with a `SOURCES:`
section and regex-parse that back out of the final event's plain-text
result. The regexes and `_extract_sources_section` logic are copied
(not imported) from `retrieval_claude.py`, per this module's
self-containment convention. This is the same deliberate, lower-risk
simplification `retrieval_claude.py` documents, not a new decision.

## `agent_mode="autopilot"` still applies

`coding_copilot.py` uses `agent_mode="autopilot"` because coding tasks
must run to completion unattended, without a human answering mid-session
prompts. The same reasoning holds for retrieval: a `RetrievalCapability`
provider's `retrieve()` call is a single synchronous call with no human
in the loop, so the session must be able to explore and conclude without
pausing for interactive approval. `agent_mode="autopilot"` is reused
unchanged.

## Step budget: same interpretation as `coding_copilot.py`

Like `coding_copilot.py`, this provider does not re-drive Copilot's
session step-by-step from Nova's side -- autopilot already is Copilot's
own bounded-loop execution. `steps_used` is populated from the session's
own event count (`len(session.get_events())`) as an observability proxy,
and `request.max_steps`/`DEFAULT_MAX_STEPS` (`retrieval.py`'s smaller
default of 20, not `coding.py`'s 40 -- these are two different
capabilities' constants, not shared) is enforced as a wall-clock
`send_and_wait` timeout rather than a literal step ceiling, using the
same `_STEP_TO_SECONDS_FACTOR` translation heuristic `coding_copilot.py`
uses (with a smaller floor, since a read-only grounding query is a
lighter task than a full coding session).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from ai_sdlc.capabilities.retrieval import (
    DEFAULT_MAX_CONTEXT_TOKENS,
    DEFAULT_MAX_STEPS,
    ContextSnippet,
    MalformedResponseError,
    ProviderError,
    RetrievalCapability,
    RetrievalRequest,
    RetrievalResult,
    TerminationReason,
)

try:
    import copilot as _copilot_sdk
    from copilot.generated import rpc as _copilot_rpc

    _COPILOT_SDK_IMPORT_ERROR: Optional[BaseException] = None
except ImportError as exc:  # pragma: no cover - exercised only when the
    # optional `github-copilot-sdk` extra (and its Python 3.11+
    # requirement) isn't installed. Import failure is deferred to
    # provider construction, not module import -- same convention
    # `coding_copilot.py` follows -- so this module always imports
    # cleanly regardless of environment.
    _copilot_sdk = None
    _copilot_rpc = None
    _COPILOT_SDK_IMPORT_ERROR = exc

PROVIDER_NAME = "github_copilot_sdk_retrieval"

#: Same translation heuristic `coding_copilot.py` uses to turn a step
#: budget into a `send_and_wait` wall-clock timeout (see that module's
#: docstring for the caveats -- this is a documented heuristic, not a
#: measured constant). The floor is lower than `coding_copilot.py`'s
#: 120s: a bounded read-only exploration is a lighter task than a full
#: coding session.
_STEP_TO_SECONDS_FACTOR = 20.0
_MIN_SESSION_TIMEOUT_SECONDS = 60.0

#: Permission-request `kind`s this provider ever approves -- see module
#: docstring's "Read-only enforcement" section for exactly what was
#: verified about the SDK's kind enumeration and why `"shell"` is never
#: included even for commands self-reporting `read_only=True`.
_ALLOWED_KINDS = frozenset({"read"})

#: Crude, well-known characters-per-token approximation used only as a
#: safety-net truncation on `context_summary` -- identical to
#: `retrieval_claude.py`'s constant of the same name, not exact token
#: counting.
_APPROX_CHARS_PER_TOKEN = 4

#: Copied unchanged from `retrieval_claude.py` -- see that module's
#: docstring and this module's "Snippet/summary extraction" section for
#: why the same `SOURCES:`-section-parsing approach is reused here.
_SOURCES_HEADING_RE = re.compile(r"^SOURCES:\s*$", re.IGNORECASE | re.MULTILINE)
_SOURCE_LINE_RE = re.compile(
    r"^\s*-?\s*(?P<path>\S+?)(?::(?P<start>\d+)-(?P<end>\d+))?\s*(?:[-—]{1,2}|:)\s*(?P<reason>.+)$"
)


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """Read `name` off `obj` regardless of whether the SDK represents it
    as a real attribute-bearing object or a `TypedDict` (a plain `dict`
    at runtime) -- copied from `coding_copilot.py`'s helper of the same
    name/behavior; see that module's docstring for the verified SDK
    inconsistency this works around."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _extract_sources_section(text: str) -> Tuple[str, List[ContextSnippet]]:
    """Split `text` into (summary_without_sources_section, parsed
    snippets). Copied unchanged from `retrieval_claude.py` -- see that
    module's docstring for the exact `SOURCES:` format the prompt asks
    for and why this is a best-effort enrichment, never a requirement for
    a valid result."""
    match = _SOURCES_HEADING_RE.search(text)
    if not match:
        return text.strip(), []

    summary = text[: match.start()].strip()
    sources_block = text[match.end() :]

    snippets: List[ContextSnippet] = []
    for line in sources_block.splitlines():
        line = line.strip()
        if not line:
            continue
        line_match = _SOURCE_LINE_RE.match(line)
        if not line_match:
            continue
        path = line_match.group("path").strip()
        reason = line_match.group("reason").strip()
        start = line_match.group("start")
        end = line_match.group("end")
        if not path or not reason:
            continue
        snippets.append(
            ContextSnippet(
                source_path=path,
                content=reason,
                line_start=int(start) if start else None,
                line_end=int(end) if end else None,
            )
        )
    return (summary or text.strip()), snippets


class CopilotRetrievalProvider(RetrievalCapability):
    """The Copilot-SDK-backed `RetrievalCapability` provider -- the
    Copilot-side sibling of `ClaudeAgentSDKRetrievalProvider`
    (`retrieval_claude.py`) and the retrieval-side sibling of
    `CopilotCodingProvider` (`coding_copilot.py`).

    Requires the optional `copilot` extra (the same `github-copilot-sdk`
    dependency `coding_copilot.py` already requires -- see
    `pyproject.toml`) and an authenticated Copilot CLI session
    (`use_logged_in_user=True` by default, matching
    `CopilotCodingProvider`'s own credential handling: this provider does
    not manage credentials itself).
    """

    def __init__(self, *, model: Optional[str] = None) -> None:
        if _COPILOT_SDK_IMPORT_ERROR is not None:
            raise ProviderError(
                "copilot_retrieval_provider: github-copilot-sdk is not usable in this "
                f"environment ({_COPILOT_SDK_IMPORT_ERROR!r}); install the optional "
                "extra with `pip install ai-sdlc[copilot]` (requires Python 3.11+). "
                "See this module's and `coding_copilot.py`'s docstrings for what was "
                "and wasn't independently verified about this dependency."
            )
        self.model = model

    # -- RetrievalCapability ------------------------------------------------

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        self._verify_repository_path(request.repository_path)
        max_steps = request.max_steps or DEFAULT_MAX_STEPS
        max_context_tokens = request.max_context_tokens or DEFAULT_MAX_CONTEXT_TOKENS

        try:
            final_event, steps_used = self._run_async(self._run_session(request, max_steps))
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - any unexpected SDK failure
            # must still surface as ProviderError per this capability's
            # failure contract, never an arbitrary exception.
            raise ProviderError(
                f"copilot_retrieval_provider: session failed before producing a result: {exc}"
            ) from exc

        return self._build_retrieval_result(request, final_event, steps_used, max_steps, max_context_tokens)

    # -- session orchestration -----------------------------------------------

    async def _run_session(self, request: RetrievalRequest, max_steps: int) -> Tuple[Any, int]:
        timeout = max(_MIN_SESSION_TIMEOUT_SECONDS, max_steps * _STEP_TO_SECONDS_FACTOR)

        client = _copilot_sdk.CopilotClient(
            working_directory=request.repository_path,
            use_logged_in_user=True,
        )
        # `timeout` above only bounds `send_and_wait` below -- `client.start()`/
        # `create_session()` have no timeout of their own in the SDK, and were
        # verified live (via `coding_copilot.py`'s equivalent session setup)
        # to take several minutes on a cold working-directory path, far past
        # this provider's own configured budget with no error signal.
        # Bounding them here with the same overall budget is what makes that
        # budget actually mean something end to end, not just for the final
        # message-wait step.
        await asyncio.wait_for(client.start(), timeout=timeout)
        try:
            session = await asyncio.wait_for(
                client.create_session(
                    on_permission_request=self._make_permission_handler(),
                    on_user_input_request=self._make_user_input_handler(),
                    model=self.model,
                    working_directory=request.repository_path,
                ),
                timeout=timeout,
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
        """Identical bridging strategy to `coding_copilot.py`'s
        `_run_async` -- see that module's docstring for why this is
        necessary (a synchronous, single-call `RetrievalCapability.
        retrieve()` must be safe to call from inside an already-running
        event loop)."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()

    # -- prompt assembly -------------------------------------------------

    def _build_prompt(self, request: RetrievalRequest) -> str:
        sections: List[str] = [f"Query: {request.query}"]

        if request.scope_paths:
            sections.extend(
                ["", "Start by looking in these locations (but follow references "
                 "elsewhere if the answer actually lives there):"]
                + [f"- {path}" for path in request.scope_paths]
            )

        sections.extend(
            [
                "",
                "You are working read-only against a codebase at "
                f"{request.repository_path}. Only read/search permission requests will "
                "ever be approved for this session -- do not attempt to edit, write, or "
                "run shell commands; any such request will be rejected.",
                "",
                f"Answer the query in at most roughly {request.max_context_tokens or DEFAULT_MAX_CONTEXT_TOKENS} "
                "tokens of prose. Then, on a new line, write exactly `SOURCES:` followed by "
                "one line per file you drew your answer from, in the format "
                "`path/to/file.ext:START-END — one-line reason` (omit `:START-END` if a "
                "specific line range doesn't apply).",
            ]
        )
        return "\n".join(sections)

    # -- permission / user-input callbacks -----------------------------------

    def _make_permission_handler(self):
        """Kind-based allow/deny, same shape as `coding_copilot.py`'s
        `_make_permission_handler` -- but with a fixed, hardcoded
        decision (see module docstring's "Read-only enforcement" section)
        instead of one derived from a caller-supplied allow-list:
        `RetrievalRequest` has no `allowed_tools`/`allowed_commands`
        field, matching `retrieval_claude.py`'s own "permission surface
        is fixed by the capability, not caller policy" stance."""

        async def handler(perm_request: Any, _invocation: Any = None):
            kind = _field(perm_request, "kind")
            if kind in _ALLOWED_KINDS:
                return _copilot_rpc.PermissionDecisionApproveOnce(approved_interactively=False)
            return _copilot_rpc.PermissionDecisionReject(
                feedback=(
                    f"copilot_retrieval_provider: permission kind {kind!r} is never granted -- "
                    "this session is read-only and only approves 'read'"
                )
            )

        return handler

    def _make_user_input_handler(self):
        """Auto-answers mid-session clarifying questions unattended, same
        "final structured verdict, no human in the loop" model
        `coding_copilot.py`'s equivalent handler establishes -- adapted
        for a read-only grounding query instead of a coding task."""

        async def handler(request: Any, _metadata: Any = None):
            question = _field(request, "question", "") or ""
            choices = _field(request, "choices", None) or []
            if choices:
                answer, was_freeform = choices[0], False
            else:
                answer, was_freeform = (
                    "No human reviewer is available mid-session. Answer using only what "
                    "you can read/search in the repository already provided; if the query "
                    "genuinely cannot be resolved that way, say so explicitly in your final "
                    "answer instead of waiting for clarification.",
                    True,
                )
            _ = question  # not otherwise used; kept for parity/debuggability with coding_copilot.py
            return _copilot_sdk.session.UserInputResponse(answer=answer, wasFreeform=was_freeform)

        return handler

    async def _estimate_steps_used(self, session: Any) -> int:
        """Best-effort event-count proxy, identical to `coding_copilot.
        py`'s `_estimate_steps_used` -- see module docstring's step-budget
        section."""
        try:
            events = session.get_events()
            if asyncio.iscoroutine(events):
                events = await events
            return max(0, len(events))
        except Exception:  # noqa: BLE001 - best-effort proxy only; never
            # let this fail the call.
            return 0

    # -- post-session verdict -----------------------------------------------

    def _build_retrieval_result(
        self,
        request: RetrievalRequest,
        final_event: Any,
        steps_used: int,
        max_steps: int,
        max_context_tokens: int,
    ) -> RetrievalResult:
        terminated_reason = self._map_termination(final_event, steps_used, max_steps)

        raw_text = _field(_field(final_event, "data"), "content")
        if isinstance(raw_text, str) and raw_text.strip():
            summary, snippets = _extract_sources_section(raw_text)
        else:
            summary, snippets = f"No context could be derived for: {request.query}", []

        summary = self._truncate_to_budget(summary, max_context_tokens)

        payload: Dict[str, Any] = {
            "query": request.query,
            "context_summary": summary,
            "snippets": snippets,
            "provider_name": PROVIDER_NAME,
            "steps_used": steps_used,
            "terminated_reason": terminated_reason,
            "metadata": {
                "model": self.model,
            },
        }
        try:
            return RetrievalResult(**payload)
        except ValidationError as exc:
            raise MalformedResponseError(
                f"copilot_retrieval_provider: session outcome failed RetrievalResult "
                f"validation: {exc}"
            ) from exc

    def _map_termination(self, final_event: Any, steps_used: int, max_steps: int) -> TerminationReason:
        if steps_used >= max_steps:
            return TerminationReason.STEP_BUDGET_EXHAUSTED
        is_error = bool(_field(final_event, "is_error", False) or _field(final_event, "error", False))
        if is_error:
            return TerminationReason.PROVIDER_REPORTED_FAILURE
        return TerminationReason.COMPLETED

    def _truncate_to_budget(self, summary: str, max_context_tokens: int) -> str:
        budget_chars = max_context_tokens * _APPROX_CHARS_PER_TOKEN
        if len(summary) <= budget_chars:
            return summary
        return summary[: max(budget_chars - 1, 0)].rstrip() + "…"

    # -- validation -----------------------------------------------------------

    def _verify_repository_path(self, repository_path: str) -> None:
        path = Path(repository_path)
        if not path.is_dir():
            raise ProviderError(
                f"copilot_retrieval_provider: repository_path {repository_path!r} does "
                "not exist or is not a directory"
            )
