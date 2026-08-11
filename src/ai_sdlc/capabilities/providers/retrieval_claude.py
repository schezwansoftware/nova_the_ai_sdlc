"""Claude Agent SDK-backed, read-only `RetrievalCapability` provider.

This is Nova's V1/default `RetrievalCapability` provider for codebase
grounding, per `docs/architecture/v1_architecture.md` section 9's "V1
Provider: Harnessed Read-Only Agent for Codebase Grounding" and section
18 Decision 6: it reuses `providers/claude_sdk.py`'s already-built
agentic-tool-harnessing pattern (same SDK, same `query()`/`dontAsk`/
async-bridge structure) rather than re-solving it, permissioned down so
the underlying tool can explore but structurally cannot write, edit, or
execute anything.

## Relationship to `claude_sdk.py`

This module deliberately does **not** import from `claude_sdk.py`. Each
capability's provider is self-contained (see `retrieval.py`'s module
docstring for why `RetrievalCapability` doesn't import `coding.py`'s
`TerminationReason`/etc either) -- `_run_async`, the SDK-import-guard
pattern, and the defensive `ResultMessage`-field extraction are copied
and adapted here, not shared, mirroring how `coding_copilot.py` adopted
the same patterns by re-implementing them rather than importing across
provider files that belong to different, independently-owned surfaces.

## What is verified vs. documentation-only in this environment

Same starting point as `claude_sdk.py`: `claude-agent-sdk` was not
independently installed/exercised end-to-end here (see that module's
docstring for the full account of the attempted install and why it was
abandoned rather than left blocking). This provider adds one additional
piece of *unverified*, best-effort design on top of what `claude_sdk.py`
already flagged:

  - **How `RetrievalResult.snippets` gets populated.** The most robust
    approach (mirroring `claude_sdk.py`'s "read git, don't trust the
    model's self-report" principle) would be to inspect the SDK's own
    tool-result messages for each `Read`/`Grep` call the agent made
    during its exploration and build snippets directly from those. That
    requires knowing the exact shape of the SDK's intermediate message/
    content-block types (e.g. whatever carries a tool name and a file
    path), which was not independently verified against a live install.
    Rather than guess at that shape, this provider instead asks the
    agent to end its answer with a structured `SOURCES:` section (one
    `path[:start-end] — reason` line per source) and parses that back out
    of `ResultMessage.result` with a regex. This only depends on
    `result` being a plain string, which *is* corroborated by both
    official-docs sources `claude_sdk.py` already cross-checked. It is a
    deliberately lower-risk simplification, not a belief that model
    self-reporting is generally more trustworthy than tool-call ground
    truth -- upgrading to true tool-result-based snippet extraction is a
    reasonable follow-up once a real install is available to verify the
    message shapes against.

## Read-only enforcement

Unlike `claude_sdk.py`, the tool allow-list here is **not** a
`RetrievalRequest` field -- see `retrieval.py`'s module docstring for why
retrieval's permission surface is a structural property of the
capability, not caller policy. It is hardcoded in this module:

  - `allowed_tools=["Read", "Grep", "Glob"]` -- the SDK's built-in
    file-read and search tools, sufficient to explore a codebase without
    ever needing shell access.
  - `disallowed_tools=["Edit", "Write", "NotebookEdit", "Bash", ...]` --
    bare (unscoped) entries, which per the SDK's documented permission
    evaluation order **remove the tool definition from what the agent
    can even see**, not merely deny attempts at runtime. Combined with
    `permission_mode="dontAsk"` (never granting anything outside the
    allow-list), this provider's underlying agent has no code-execution
    or file-mutation primitive available at all, regardless of what any
    prompt-injected content it reads might try to talk it into -- the
    same "structurally incapable, not merely trusted" guarantee section
    18 Decision 5 already establishes for Tier 2 agents generally.
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
    from claude_agent_sdk import ClaudeAgentOptions as _SDKClaudeAgentOptions
    from claude_agent_sdk import ResultMessage as _SDKResultMessage
    from claude_agent_sdk import query as _sdk_query

    SDK_IMPORT_ERROR: Optional[BaseException] = None
except ImportError as exc:  # pragma: no cover - exercised only when the SDK is absent
    _SDKClaudeAgentOptions = None
    _SDKResultMessage = None
    _sdk_query = None
    SDK_IMPORT_ERROR = exc

PROVIDER_NAME = "claude_agent_sdk_retrieval"

#: Read-only exploration tools this provider ever grants. No shell access
#: at all -- see module docstring for why `Bash` is never included, even
#: scoped to read-only-looking commands.
_ALLOWED_TOOLS = ["Read", "Grep", "Glob"]

#: Bare (unscoped) deny entries -- removes these tool definitions from
#: what the agent can even attempt, per the SDK's documented permission
#: evaluation order. Defense-in-depth on top of simply never allow-listing
#: them, mirroring `claude_sdk.py`'s hardcoded `Bash(git push *)` denial.
_HARD_DISALLOWED_TOOLS = ["Edit", "Write", "NotebookEdit", "Bash", "WebFetch", "WebSearch"]

#: Crude, well-known characters-per-token approximation used only as a
#: safety-net truncation on `context_summary` -- not exact token counting
#: (see module docstring).
_APPROX_CHARS_PER_TOKEN = 4

_SOURCES_HEADING_RE = re.compile(r"^SOURCES:\s*$", re.IGNORECASE | re.MULTILINE)
_SOURCE_LINE_RE = re.compile(
    r"^\s*-?\s*(?P<path>\S+?)(?::(?P<start>\d+)-(?P<end>\d+))?\s*(?:[-—]{1,2}|:)\s*(?P<reason>.+)$"
)


def _extract_sources_section(text: str) -> Tuple[str, List[ContextSnippet]]:
    """Split `text` into (summary_without_sources_section, parsed snippets).

    Looks for a trailing `SOURCES:` section (see module docstring for the
    format the prompt asks for) and parses each line under it. Falls back
    to `(text, [])` unchanged if no such section is found or nothing
    parses -- this is a best-effort enrichment, never a requirement for a
    valid result.
    """
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


class ClaudeAgentSDKRetrievalProvider(RetrievalCapability):
    """The real, default V1 `RetrievalCapability` provider.

    Test seams (`_query_fn`/`_options_cls`/`_result_message_cls`) mirror
    `ClaudeAgentSDKProvider`'s exactly, for the same reason: tests can
    exercise this provider's orchestration logic with a fake SDK, without
    requiring `claude-agent-sdk` to be installed or a real `claude` CLI
    to be available.
    """

    def __init__(
        self,
        *,
        cli_path: Optional[str] = None,
        _query_fn: Optional[Any] = None,
        _options_cls: Optional[Any] = None,
        _result_message_cls: Optional[Any] = None,
    ) -> None:
        self._cli_path = cli_path
        self._query_fn = _query_fn if _query_fn is not None else _sdk_query
        self._options_cls = _options_cls if _options_cls is not None else _SDKClaudeAgentOptions
        self._result_message_cls = (
            _result_message_cls if _result_message_cls is not None else _SDKResultMessage
        )

        if self._query_fn is None or self._options_cls is None or self._result_message_cls is None:
            raise ProviderError(
                "claude_agent_sdk_retrieval_provider: the `claude-agent-sdk` package is "
                f"not usable in this environment ({SDK_IMPORT_ERROR!r}); install it with "
                "`pip install claude-agent-sdk` and ensure a `claude` CLI is on PATH (or "
                "pass cli_path=...). See this module's docstring for what was and wasn't "
                "independently verified about this dependency."
            )

    # -- RetrievalCapability -------------------------------------------------

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        self._verify_repository_path(request.repository_path)
        max_turns = request.max_steps or DEFAULT_MAX_STEPS
        max_context_tokens = request.max_context_tokens or DEFAULT_MAX_CONTEXT_TOKENS

        try:
            result_message, steps_used = self._run_async(self._run_session(request, max_turns))
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(
                f"claude_agent_sdk_retrieval_provider: session failed before producing a "
                f"result: {exc}"
            ) from exc

        return self._build_retrieval_result(
            request, result_message, steps_used, max_turns, max_context_tokens
        )

    # -- session orchestration --------------------------------------------

    async def _run_session(self, request: RetrievalRequest, max_turns: int) -> Tuple[Any, int]:
        options = self._options_cls(
            cwd=request.repository_path,
            allowed_tools=list(_ALLOWED_TOOLS),
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
                "claude_agent_sdk_retrieval_provider: session ended without a terminal "
                "ResultMessage"
            )
        return result_message, steps_used

    def _run_async(self, coro: Any) -> Any:
        """Identical bridging strategy to `claude_sdk.py`'s `_run_async` --
        see that module's docstring for why this is necessary (a
        synchronous, single-call `RetrievalCapability.retrieve()` must be
        safe to call from inside an already-running event loop, since
        `BaseAgent.execute()` is itself `async def`)."""
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
                f"{request.repository_path}. You can read and search files, but you have "
                "no ability to edit, write, or run commands -- do not attempt to.",
                "",
                f"Answer the query in at most roughly {request.max_context_tokens or DEFAULT_MAX_CONTEXT_TOKENS} "
                "tokens of prose. Then, on a new line, write exactly `SOURCES:` followed by "
                "one line per file you drew your answer from, in the format "
                "`path/to/file.ext:START-END — one-line reason` (omit `:START-END` if a "
                "specific line range doesn't apply).",
            ]
        )
        return "\n".join(sections)

    # -- post-session verdict -----------------------------------------------

    def _build_retrieval_result(
        self,
        request: RetrievalRequest,
        result_message: Any,
        steps_used: int,
        max_turns: int,
        max_context_tokens: int,
    ) -> RetrievalResult:
        fields = self._extract_result_fields(result_message)
        terminated_reason = self._map_termination(fields, steps_used, max_turns)

        raw_text = fields["result_text"]
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
                "session_id": fields["session_id"],
                "total_cost_usd": fields["total_cost_usd"],
                "sdk_reported_error": fields["is_error"],
            },
        }
        try:
            return RetrievalResult(**payload)
        except ValidationError as exc:
            raise MalformedResponseError(
                f"claude_agent_sdk_retrieval_provider: session outcome failed "
                f"RetrievalResult validation: {exc}"
            ) from exc

    def _extract_result_fields(self, result_message: Any) -> Dict[str, Any]:
        """Defensive `getattr`-based extraction, identical in spirit to
        `claude_sdk.py`'s equivalent -- see that module's docstring for
        why this doesn't assert a fixed `ResultMessage` shape."""
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

    def _truncate_to_budget(self, summary: str, max_context_tokens: int) -> str:
        budget_chars = max_context_tokens * _APPROX_CHARS_PER_TOKEN
        if len(summary) <= budget_chars:
            return summary
        return summary[: max(budget_chars - 1, 0)].rstrip() + "…"

    # -- validation ---------------------------------------------------------

    def _verify_repository_path(self, repository_path: str) -> None:
        path = Path(repository_path)
        if not path.is_dir():
            raise ProviderError(
                f"claude_agent_sdk_retrieval_provider: repository_path "
                f"{repository_path!r} does not exist or is not a directory"
            )
