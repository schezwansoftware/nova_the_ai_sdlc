"""GitHub Copilot SDK-backed `SageCapability` provider.

The Copilot-SDK-driven sibling of `sage_claude.py`: same `SageCapability`
contract (`ask(request) -> SageResponse`, same `ProviderError`/
`MalformedResponseError` failure contract), driving `github/copilot-sdk`
instead of the Claude Agent SDK. `CopilotClient`/`create_session`/
`send_and_wait`, the `on_permission_request`/`on_user_input_request`
callback shapes, `_field()`'s `TypedDict`-vs-attribute-object
normalization, and `_run_async`'s event-loop bridging are copied and
adapted from `coding_copilot.py`/`reasoning_copilot.py`, not imported --
mirroring those modules' own "each capability's provider stays
self-contained" rationale.

## No repository, no working directory

Same as `sage_claude.py`/`reasoning_copilot.py`: Sage has no repository
at all. Verified (via `reasoning_copilot.py`'s own introspection of the
installed SDK) that leaving `working_directory` unset is safe -- the
session-open path resolves `cwd = opts.working_directory or
os.getcwd()` when `None`, with no constructor-time validation requiring
a caller-supplied path to exist.

## MCP server wiring: CONFIRMED via direct SDK introspection, resolving
## the locked design's one previously-flagged unknown

The locked design (`todo.md`) flagged "not yet verified: whether
Copilot's SDK exposes an allowlist step the same way Claude's does" as an
open question. It does -- and it's actually **more granular** than
Claude's flat, session-wide `allowed_tools` list. Directly introspected
against the installed `github-copilot-sdk==1.0.9` package (the same
version `coding_copilot.py`/`reasoning_copilot.py` document verifying
against):

  - `CopilotClient.create_session(...)` genuinely accepts
    `mcp_servers: dict[str, MCPServerConfig] | None`.
  - `MCPServerConfig = MCPStdioServerConfig | MCPHTTPServerConfig`;
    `MCPStdioServerConfig` (a `TypedDict`) has exactly `{tools: list[str],
    type: NotRequired[Literal["local", "stdio"]], timeout:
    NotRequired[int], command: str, args: NotRequired[list[str]], env:
    NotRequired[dict[str, str]], working_directory:
    NotRequired[str]}` -- where `tools` is documented in the SDK's own
    source comment as "List of tools to include. [] means none. '*'
    means all." -- a genuine **per-server** allowlist, unlike Claude's
    one flat `allowed_tools`.
  - Every individual MCP tool call still round-trips through
    `on_permission_request` regardless of the `tools:` allowlist --
    `PermissionRequestMcp` (`copilot.generated.session_events`) is real,
    carrying `kind: ClassVar[str] = "mcp"`, `server_name: str`,
    `tool_name: str`, `read_only: bool`. This directly confirms
    `coding_copilot.py`'s existing `_KIND_TO_TOOL_NAMES["mcp"] =
    ("Mcp",)` mapping (`coding_copilot.py:202`) is accurate, and gives
    this provider exactly the hook it needs.

## Permission handling: approve every `kind=="mcp"` call, reject
## everything else

Sage's sub-session *is* the trust boundary here -- connectors are
read-only by construction (the locked design's edge case #1), so a
uniform "approve any MCP tool call, since the connector itself cannot
mutate anything" policy is the V1 posture. `PermissionRequestMcp` carries
`read_only`, so a future pass *could* gate more precisely (only
auto-approve `read_only=True`) -- noted as a possible future tightening
in `todo.md`'s deferred list, not built here. No non-MCP tool is ever
approved; Sage needs none.

## `available_tools`: deliberately left unset, not `[]`

`reasoning_copilot.py` uses `available_tools=[]` for its own structural
zero-tool guarantee. **Genuinely unresolved, flagged rather than
guessed**: whether that mechanism would *also* suppress
`mcp_servers`-derived tools, or only non-MCP builtin tools, isn't
determinable from the installed package's Python-side type hints alone
(the resolution logic lives server-side in the Copilot CLI binary). The
conservative choice, given real uncertainty: leave `available_tools`/
`excluded_tools` unset (`None`, i.e. no restriction) and rely on the
`on_permission_request` handler above as the *primary* tool-scoping
mechanism instead of layering an unverified allowlist on top of it.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import re
from typing import Any, Dict, Optional

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
    import copilot as _copilot_sdk
    from copilot.generated import rpc as _copilot_rpc

    _COPILOT_SDK_IMPORT_ERROR: Optional[BaseException] = None
except ImportError as exc:  # pragma: no cover - exercised only when the
    # optional `github-copilot-sdk` extra (and its Python 3.11+
    # requirement) isn't installed. Import failure is deferred to
    # provider construction, not module import -- same convention every
    # other Copilot-backed provider in this package follows.
    _copilot_sdk = None
    _copilot_rpc = None
    _COPILOT_SDK_IMPORT_ERROR = exc

PROVIDER_NAME = "github_copilot_sdk_sage"

#: Same translation heuristic `coding_copilot.py`/`reasoning_copilot.py`
#: use to turn a step budget into a `send_and_wait` wall-clock timeout.
_STEP_TO_SECONDS_FACTOR = 20.0
_MIN_SESSION_TIMEOUT_SECONDS = 30.0

_FOUND_RE = re.compile(r"^\s*FOUND:\s*(?P<val>true|false)\s*$", re.IGNORECASE | re.MULTILINE)
_SOURCE_CONNECTOR_RE = re.compile(r"^\s*SOURCE_CONNECTOR:\s*(?P<val>\S+)\s*$", re.IGNORECASE | re.MULTILINE)
_SOURCE_URL_RE = re.compile(r"^\s*SOURCE_URL:\s*(?P<val>\S+)\s*$", re.IGNORECASE | re.MULTILINE)


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """Read `name` off `obj` regardless of whether the SDK represents it
    as a real attribute-bearing object or a `TypedDict` (a plain `dict`
    at runtime) -- copied from `coding_copilot.py`/`reasoning_copilot.py`'s
    helper of the same name/behavior."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _parse_sage_response(text: str) -> Dict[str, Any]:
    """Identical parsing convention to `sage_claude.py`'s
    `_parse_sage_response` -- copied, not imported, per this module's
    self-containment convention."""
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


class SageCopilotProvider(SageCapability):
    """The Copilot-SDK-backed `SageCapability` provider.

    Unlike `reasoning_factory.py`/`coding_factory.py`/
    `retrieval_factory.py`'s zero-arg-constructible providers, this one
    takes a real `connector_resolver` -- see `sage_factory.py`'s
    docstring for why Sage is not bound by that same zero-arg
    constraint.
    """

    def __init__(
        self,
        *,
        connector_resolver: ConnectorResolver,
        model: Optional[str] = None,
        max_steps: int = DEFAULT_MAX_STEPS,
    ) -> None:
        if _COPILOT_SDK_IMPORT_ERROR is not None:
            raise ProviderError(
                "copilot_sage_provider: github-copilot-sdk is not usable in this "
                f"environment ({_COPILOT_SDK_IMPORT_ERROR!r}); install the optional "
                "extra with `pip install ai-sdlc[copilot]` (requires Python 3.11+). "
                "See this module's and `coding_copilot.py`'s docstrings for what was "
                "and wasn't independently verified about this dependency."
            )
        self._connector_resolver = connector_resolver
        self.model = model
        self.max_steps = max_steps

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

        try:
            final_event = self._run_async(self._run_session(request, resolution))
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - any unexpected SDK failure
            raise ProviderError(
                f"copilot_sage_provider: session failed before producing a result: {exc}"
            ) from exc

        return self._build_sage_response(request, final_event, resolution)

    # -- session orchestration -------------------------------------------

    async def _run_session(self, request: SageRequest, resolution: Any) -> Any:
        max_steps = request.max_steps or self.max_steps
        timeout = max(_MIN_SESSION_TIMEOUT_SECONDS, max_steps * _STEP_TO_SECONDS_FACTOR)

        mcp_servers = {
            spec.name: {
                "type": "stdio",
                "command": spec.command,
                "args": list(spec.args),
                "env": dict(spec.env),
                "tools": list(spec.tool_names),
            }
            for spec in resolution.enabled
        }

        client = _copilot_sdk.CopilotClient(use_logged_in_user=True)
        # `timeout` above only bounds `send_and_wait` below -- `client.start()`/
        # `create_session()` have no timeout of their own in the SDK; see
        # `coding_copilot.py`/`reasoning_copilot.py` for the same verified
        # cold-path timing concern that makes bounding both steps here
        # necessary for the overall budget to mean anything end to end.
        await asyncio.wait_for(client.start(), timeout=timeout)
        try:
            session = await asyncio.wait_for(
                client.create_session(
                    on_permission_request=self._make_permission_handler(),
                    on_user_input_request=self._make_user_input_handler(),
                    model=self.model,
                    mcp_servers=mcp_servers,
                ),
                timeout=timeout,
            )
            try:
                final_event = await session.send_and_wait(
                    self._build_prompt(request, resolution),
                    agent_mode="autopilot",
                    timeout=timeout,
                )
            finally:
                await session.disconnect()
        finally:
            await client.stop()

        return final_event

    def _run_async(self, coro: Any) -> Any:
        """Identical bridging strategy to every other provider in this
        package."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()

    # -- prompt assembly ---------------------------------------------------

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

    # -- permission / user-input callbacks -----------------------------------

    def _make_permission_handler(self):
        """Approve every `kind=='mcp'` call unconditionally (Sage's
        sub-session is the trust boundary; connectors are read-only by
        construction), reject every other kind -- see module docstring's
        "Permission handling" section."""

        async def handler(perm_request: Any, _invocation: Any = None):
            kind = _field(perm_request, "kind")
            if kind == "mcp":
                return _copilot_rpc.PermissionDecisionApproveOnce(approved_interactively=False)
            return _copilot_rpc.PermissionDecisionReject(
                feedback=(
                    f"copilot_sage_provider: permission kind {kind!r} is never granted -- "
                    "this session only ever uses its configured MCP connector tools"
                )
            )

        return handler

    def _make_user_input_handler(self):
        async def handler(request: Any, _metadata: Any = None):
            choices = _field(request, "choices", None) or []
            if choices:
                answer, was_freeform = choices[0], False
            else:
                answer, was_freeform = (
                    "No human reviewer is available mid-session. Answer using only the "
                    "connector search/fetch tools already available; if the answer "
                    "genuinely cannot be found, say so explicitly with FOUND: false "
                    "instead of waiting for clarification.",
                    True,
                )
            return _copilot_sdk.session.UserInputResponse(answer=answer, wasFreeform=was_freeform)

        return handler

    # -- post-session verdict -----------------------------------------------

    def _build_sage_response(self, request: SageRequest, final_event: Any, resolution: Any) -> SageResponse:
        raw_text = _field(_field(final_event, "data"), "content")
        parsed = _parse_sage_response(raw_text if isinstance(raw_text, str) else "")

        payload: Dict[str, Any] = {
            "query": request.query,
            "found": parsed["found"],
            "answer": parsed["answer"],
            "source_connector": parsed["source_connector"],
            "source_url": parsed["source_url"],
            "provider_name": PROVIDER_NAME,
            "steps_used": 1,
            "terminated_reason": TerminationReason.COMPLETED,
            "metadata": {"skipped": resolution.skipped},
        }
        try:
            return SageResponse(**payload)
        except ValidationError as exc:
            raise MalformedResponseError(
                f"copilot_sage_provider: response failed SageResponse validation: {exc}"
            ) from exc
