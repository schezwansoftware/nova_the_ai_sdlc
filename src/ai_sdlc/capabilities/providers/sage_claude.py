"""Claude Agent SDK-backed `SageCapability` provider.

The third sibling in the "Claude Agent SDK bounded-session" family
alongside `claude_sdk.py` (`ClaudeAgentSDKProvider`, `CodingCapability`)
and `retrieval_claude.py` (`ClaudeAgentSDKRetrievalProvider`,
`RetrievalCapability`) -- same `_run_async` event-loop bridging, same
`_query_fn`/`_options_cls`/`_result_message_cls` test seams, same
defensive `ResultMessage`-field extraction. This module deliberately does
**not** import from either sibling -- each capability's provider stays
self-contained (see `retrieval_claude.py`'s docstring for the fuller
version of this argument).

## No repository, no working directory

Unlike `claude_sdk.py`/`retrieval_claude.py` (both operate against a real
repository path), Sage has no repository at all -- same reasoning
`reasoning_copilot.py` documents for skipping `working_directory`: this
is a "search external knowledge sources and answer" task, not an
exploration of any codebase. `cwd` is left unset.

## MCP server wiring: verified against the installed `claude-agent-sdk`,
## not assumed

`packages/mcp-connectors/INSTALL.md` §3b already documented this exact
gap and fix as a real, scoped, previously-unbuilt follow-up:
`claude_sdk.py` never passes `mcp_servers` into `ClaudeAgentOptions`. This
provider is the first to actually build it, and does so against
directly-introspected types in the installed `claude-agent-sdk==0.2.139`
package (not docs-only assumption): `ClaudeAgentOptions.mcp_servers:
dict[str, McpStdioServerConfig | ...] | str | Path`, and
`McpStdioServerConfig` is a `TypedDict` with exactly `{type:
NotRequired[Literal["stdio"]], command: str, args: NotRequired[list[str]],
env: NotRequired[dict[str, str]]}` -- matching
`connector_resolver.py::ConnectorLaunchSpec`'s shape field-for-field.
`ClaudeAgentOptions.cwd` was also confirmed genuinely optional
(`str | Path | None = None`).

**Not independently re-verifiable from the SDK's Python-side types
alone** (this is a runtime/CLI-side convention, not something the type
hints encode): whether an enabled MCP server's tools are actually named
`mcp__<server_name>__<tool_name>` in `allowed_tools`, as
`INSTALL.md` §3b proposes and this provider assumes below. Flagged
honestly rather than silently assumed correct -- if a live Claude CLI
session ever shows a different naming convention, this is the first
place to check.

## Structured output: a fixed trailing section, not a fenced JSON block

`SageResponse` has one fixed shape (unlike `ReasoningCapability`'s
caller-supplied schema, which is why `reasoning_copilot.py` needs a
fenced-JSON convention instead) -- the better structural fit here is
`retrieval_claude.py`'s `SOURCES:`-trailing-section convention, adapted:
the prompt (`_build_prompt`) asks the session to end its answer with
`FOUND:`/`SOURCE_CONNECTOR:`/`SOURCE_URL:` lines, parsed back out by a
dedicated `_parse_sage_response` regex parser -- copied and adapted, not
imported, per this module's self-containment convention.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import re
from typing import Any, Dict, Optional, Tuple

from pydantic import ValidationError

from ai_sdlc.capabilities.connector_resolver import ConnectorResolver
from ai_sdlc.capabilities.sage import (
    DEFAULT_MAX_STEPS,
    MalformedResponseError,
    ProviderError,
    SageCapability,
    SageRequest,
    SageResponse,
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

PROVIDER_NAME = "claude_agent_sdk_sage"

_FOUND_RE = re.compile(r"^\s*FOUND:\s*(?P<val>true|false)\s*$", re.IGNORECASE | re.MULTILINE)
_SOURCE_CONNECTOR_RE = re.compile(r"^\s*SOURCE_CONNECTOR:\s*(?P<val>\S+)\s*$", re.IGNORECASE | re.MULTILINE)
_SOURCE_URL_RE = re.compile(r"^\s*SOURCE_URL:\s*(?P<val>\S+)\s*$", re.IGNORECASE | re.MULTILINE)


def _parse_sage_response(text: str) -> Dict[str, Any]:
    """Parse the trailing `FOUND:`/`SOURCE_CONNECTOR:`/`SOURCE_URL:`
    section out of a session's final answer text -- see module docstring.
    Falls back to `found=False` if no `FOUND:` line is present at all
    (no signal to trust otherwise -- the same conservative default
    `MockSageProvider` uses), never raises: a Sage answer that doesn't
    follow the requested format is a normal (if unhelpful) "not found"
    result, not a `MalformedResponseError` -- unlike `SageResponse`
    validation itself, which *can* still fail (e.g. an empty
    `provider_name`) and does raise."""
    text = text or ""
    found_match = _FOUND_RE.search(text)
    connector_match = _SOURCE_CONNECTOR_RE.search(text)
    url_match = _SOURCE_URL_RE.search(text)

    found = bool(found_match and found_match.group("val").strip().lower() == "true")

    source_connector = None
    if connector_match:
        val = connector_match.group("val").strip().lower()
        if val and val != "none":
            source_connector = val

    source_url = None
    if url_match:
        val = url_match.group("val").strip()
        if val and val.lower() != "none":
            source_url = val

    marker_starts = [m.start() for m in (found_match, connector_match, url_match) if m]
    answer = text[: min(marker_starts)].strip() if marker_starts else text.strip()

    return {
        "answer": answer if found else "",
        "found": found,
        "source_connector": source_connector if found else None,
        "source_url": source_url if found else None,
    }


class SageClaudeProvider(SageCapability):
    """The Claude-SDK-backed `SageCapability` provider.

    Unlike `reasoning_factory.py`/`coding_factory.py`/
    `retrieval_factory.py`'s zero-arg-constructible providers, this one
    takes a real `connector_resolver` -- see `sage_factory.py`'s
    docstring for why Sage is not bound by the same zero-arg constraint
    (it's never an `Agent`/never constructed via `AgentRegistry`).

    Test seams (`_query_fn`/`_options_cls`/`_result_message_cls`) mirror
    `ClaudeAgentSDKProvider`/`ClaudeAgentSDKRetrievalProvider` exactly.
    """

    def __init__(
        self,
        *,
        connector_resolver: ConnectorResolver,
        cli_path: Optional[str] = None,
        _query_fn: Optional[Any] = None,
        _options_cls: Optional[Any] = None,
        _result_message_cls: Optional[Any] = None,
    ) -> None:
        self._connector_resolver = connector_resolver
        self._cli_path = cli_path
        self._query_fn = _query_fn if _query_fn is not None else _sdk_query
        self._options_cls = _options_cls if _options_cls is not None else _SDKClaudeAgentOptions
        self._result_message_cls = (
            _result_message_cls if _result_message_cls is not None else _SDKResultMessage
        )

        if self._query_fn is None or self._options_cls is None or self._result_message_cls is None:
            raise ProviderError(
                "claude_agent_sdk_sage_provider: the `claude-agent-sdk` package is not "
                f"usable in this environment ({SDK_IMPORT_ERROR!r}); install it with "
                "`pip install claude-agent-sdk` and ensure a `claude` CLI is on PATH (or "
                "pass cli_path=...). See this module's docstring for what was and wasn't "
                "independently verified about this dependency."
            )

    # -- SageCapability ---------------------------------------------------

    def ask(self, request: SageRequest) -> SageResponse:
        resolution = self._connector_resolver.resolve()

        if not resolution.enabled:
            return SageResponse(
                query=request.query,
                found=False,
                provider_name=PROVIDER_NAME,
                steps_used=0,
                terminated_reason=TerminationReason.COMPLETED,
                metadata={"reason": "no_connectors_enabled", "skipped": resolution.skipped},
            )

        max_turns = request.max_steps or DEFAULT_MAX_STEPS

        try:
            result_message, steps_used = self._run_async(
                self._run_session(request, resolution, max_turns)
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(
                f"claude_agent_sdk_sage_provider: session failed before producing a "
                f"result: {exc}"
            ) from exc

        return self._build_sage_response(request, result_message, steps_used, max_turns, resolution)

    # -- session orchestration --------------------------------------------

    async def _run_session(
        self, request: SageRequest, resolution: Any, max_turns: int
    ) -> Tuple[Any, int]:
        mcp_servers = {
            spec.name: {
                "type": "stdio",
                "command": spec.command,
                "args": list(spec.args),
                "env": dict(spec.env),
            }
            for spec in resolution.enabled
        }
        allowed_tools = [
            f"mcp__{spec.name}__{tool}" for spec in resolution.enabled for tool in spec.tool_names
        ]

        options = self._options_cls(
            mcp_servers=mcp_servers,
            allowed_tools=allowed_tools,
            disallowed_tools=[],
            permission_mode="dontAsk",
            max_turns=max_turns,
            cli_path=self._cli_path,
        )
        prompt = self._build_prompt(request, resolution)

        result_message = None
        steps_used = 0
        async for message in self._query_fn(prompt=prompt, options=options):
            steps_used += 1
            if isinstance(message, self._result_message_cls):
                result_message = message

        if result_message is None:
            raise ProviderError(
                "claude_agent_sdk_sage_provider: session ended without a terminal "
                "ResultMessage"
            )
        return result_message, steps_used

    def _run_async(self, coro: Any) -> Any:
        """Identical bridging strategy to `claude_sdk.py`/
        `retrieval_claude.py`'s `_run_async` -- see those modules'
        docstrings for why this is necessary."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()

    # -- prompt assembly -------------------------------------------------

    def _build_prompt(self, request: SageRequest, resolution: Any) -> str:
        connector_names = ", ".join(spec.name for spec in resolution.enabled)
        return "\n".join(
            [
                f"Question: {request.query}",
                "",
                f"You have search/fetch tools available for these knowledge sources: "
                f"{connector_names}. Use them to find a specific, well-sourced answer to "
                "the question above. Do not guess or fabricate an answer -- only report "
                "something you actually found via a tool call.",
                "",
                "End your response with exactly these three lines (in this order, on "
                "their own lines, nothing after them):",
                "FOUND: true|false",
                "SOURCE_CONNECTOR: <the connector name you found the answer in, or none>",
                "SOURCE_URL: <a URL for the source if one was returned, or none>",
                "",
                "Set FOUND: false (and leave the other two lines as none) if you could not "
                "find a specific answer after searching -- that is a normal, useful result, "
                "not a failure.",
            ]
        )

    # -- post-session verdict -----------------------------------------------

    def _build_sage_response(
        self,
        request: SageRequest,
        result_message: Any,
        steps_used: int,
        max_turns: int,
        resolution: Any,
    ) -> SageResponse:
        fields = self._extract_result_fields(result_message)
        terminated_reason = self._map_termination(fields, steps_used, max_turns)

        raw_text = fields["result_text"]
        parsed = _parse_sage_response(raw_text if isinstance(raw_text, str) else "")

        payload: Dict[str, Any] = {
            "query": request.query,
            "found": parsed["found"],
            "answer": parsed["answer"],
            "source_connector": parsed["source_connector"],
            "source_url": parsed["source_url"],
            "provider_name": PROVIDER_NAME,
            "steps_used": steps_used,
            "terminated_reason": terminated_reason,
            "metadata": {
                "session_id": fields["session_id"],
                "total_cost_usd": fields["total_cost_usd"],
                "sdk_reported_error": fields["is_error"],
                "skipped": resolution.skipped,
            },
        }
        try:
            return SageResponse(**payload)
        except ValidationError as exc:
            raise MalformedResponseError(
                f"claude_agent_sdk_sage_provider: session outcome failed SageResponse "
                f"validation: {exc}"
            ) from exc

    def _extract_result_fields(self, result_message: Any) -> Dict[str, Any]:
        """Defensive `getattr`-based extraction, identical in spirit to
        `claude_sdk.py`/`retrieval_claude.py`'s equivalents."""
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
