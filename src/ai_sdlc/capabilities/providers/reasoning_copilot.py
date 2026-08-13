"""GitHub Copilot SDK-backed `ReasoningCapability` provider.

The Copilot-SDK-driven sibling of `providers/reasoning_anthropic.py`: same
`ReasoningCapability` contract (`complete(prompt, *, output_schema) ->
SchemaT`, same `ProviderError`/`MalformedResponseError` failure contract),
driving `github/copilot-sdk` instead of the Anthropic Messages API.

## Why this provider exists at all

This module corrects a wrong assumption an earlier pass made: that
`ReasoningCapability` had to stay Claude/Anthropic-only because "Copilot
has no equivalent single-completion API." That reasoning doesn't hold --
Copilot's SDK doesn't need a literal single-call completion endpoint for
this to work. The same technique `providers/retrieval_copilot.py` (and,
before it, `providers/retrieval_claude.py`) already uses for extracting a
single structured answer out of a free-form agentic session -- drive a
bounded session with one task, prompt-instruct it to emit exactly the
answer shape needed, parse that back out of the final response -- works
identically here: the "one task" is just "answer this reasoning prompt"
instead of "explore this codebase," and the "structured answer" is an
arbitrary caller-supplied Pydantic schema instead of a fixed
summary-plus-sources shape. See `reasoning_factory.py`'s module docstring
for the second half of this correction: reasoning provider selection now
shares `AI_SDLC_AGENT_FRAMEWORK` with `CodingCapability`/
`RetrievalCapability` instead of a separate env var, since one workspace
preference is meant to govern every AI call the platform makes.

## Session-lifecycle plumbing: copied, not imported

`CopilotClient`/`create_session`/`send_and_wait`, the
`on_permission_request`/`on_user_input_request` callback shapes,
`_field()`'s `TypedDict`-vs-attribute-object normalization, and
`_run_async`'s event-loop bridging are copied and adapted from
`providers/coding_copilot.py` (by way of `providers/retrieval_copilot.py`,
which already did this once), not imported -- mirroring both of those
modules' own "each capability's provider stays self-contained" rationale
(see `capabilities/__init__.py`'s documented convention, and
`retrieval_claude.py`'s docstring for the fuller version of this
argument). This module was built against, and verified against, the exact
same installed `github-copilot-sdk==1.0.9` package `coding_copilot.py`
and `retrieval_copilot.py` document introspecting -- no new SDK-version
drift to account for.

## No repository, no worktree, no working directory at all

Unlike `coding_copilot.py` (`working_tree_path`, an isolated worktree the
caller creates) and `retrieval_copilot.py` (`repository_path`, the real
target repository), `ReasoningCapability.complete()`'s signature --
`complete(self, prompt: str, *, output_schema: Type[SchemaT]) ->
SchemaT` -- carries no filesystem path at all. Reasoning never touches a
repository (see `reasoning.py`'s own docstring: it is the "think and
answer" step, not an exploration or coding step), so this provider has no
`working_directory` to pass to either `CopilotClient(...)` or
`create_session(...)`. Verified directly against the installed SDK that
this is safe, not merely convenient: `copilot.client`'s session-open path
resolves `cwd = opts.working_directory or os.getcwd()` when
`working_directory` is `None` -- there is no constructor-time validation
that requires a caller-supplied path to exist. Leaving it unset is
therefore the honest representation of "this task has no repository
context," not a corner cut.

## Structural "no tools at all" guarantee: `available_tools=[]`

`coding_copilot.py`/`retrieval_copilot.py` each grant a real (if narrow)
tool surface -- read/write/shell for coding, read-only for retrieval --
enforced by an `on_permission_request` callback that approves some kinds
and rejects others. Reasoning needs neither: this is "just get an answer
back," with no read/write/execute primitive required at all. Rather than
reuse that same kind-based allow/deny pattern with an empty allow-set
(defense against a permission request that should structurally never
happen), this provider uses a stronger mechanism verified directly
against `create_session`'s own parameter docs in the installed package:
`available_tools: list[str] | ToolSet | None` -- "Allowlist of tools to
enable. When specified, only these tools are enabled." Passing
`available_tools=[]` (an empty list, deliberately distinct from `None`,
which would mean "no restriction") is therefore not a guessed tool-name
denylist the way `retrieval_copilot.py`'s docstring explicitly declined
to build (guessing concrete builtin tool-name strings risks a silent
no-op if a guess doesn't match the runtime's real registration) -- an
*empty* allowlist carries no such risk, since there is nothing to
mis-name. The underlying agent structurally has zero tools available for
the whole session, regardless of what any prompt-injected content it
might somehow encounter tries to talk it into -- the same "structurally
incapable, not merely trusted" guarantee `retrieval_claude.py`/
`retrieval_copilot.py` already establish for their narrower read-only
surfaces, taken to its logical conclusion for a capability that needs no
tools whatsoever. `on_permission_request` is still wired below,
unconditionally rejecting every kind, as belt-and-suspenders in case a
future SDK version ever surfaces a permission request despite an empty
allowlist -- consistent with every other provider in this package never
leaving a callback unset "because it shouldn't be needed."

## Structured output extraction: a fenced JSON block, not a fixed section

`retrieval_claude.py`/`retrieval_copilot.py`'s `SOURCES:`-section
convention works because `RetrievalResult` has one fixed shape
(summary + sources) every provider call produces. `ReasoningCapability.
complete()` takes an arbitrary caller-supplied `output_schema`, so no
fixed section heading can work here. Instead, the prompt (see
`_build_prompt`) includes `output_schema.model_json_schema()` verbatim
and instructs the session to answer with *only* a fenced ```json code
block containing an object satisfying that schema, nothing else.
`_extract_json_payload` parses that block back out with a regex (falling
back to attempting `json.loads` on the whole trimmed response, in case
the model omits the fence but still emits bare JSON) and hands the
resulting dict to `output_schema(**payload)`, exactly like
`reasoning_anthropic.py` does with its forced-tool-use `block.input`.
Unlike the Anthropic provider, there is no SDK-level forced-structured-
output primitive to lean on here (Copilot's session is a free-form
chat/agent loop, not a single tool-call round trip) -- this is a
best-effort prompting strategy, not a structural guarantee the way the
Anthropic provider's `tool_choice` is. A response that doesn't parse or
doesn't validate raises `MalformedResponseError`, same failure contract
either way.

## Step budget: bounded, sized smaller than retrieval's

Like `coding_copilot.py`/`retrieval_copilot.py`, this provider does not
re-drive the session step-by-step from Nova's side; `agent_mode=
"autopilot"` is Copilot's own bounded-loop execution, unattended (no
human in the loop to answer a mid-session prompt, matching every other
provider in this package). `DEFAULT_MAX_STEPS` below is translated into a
`send_and_wait` wall-clock timeout via the same heuristic
`retrieval_copilot.py` documents (a documented approximation, not a
measured constant), with both the step count and the floor timeout
smaller than `retrieval.py`'s `DEFAULT_MAX_STEPS = 20`: answering a
single reasoning prompt with zero tools available converges faster than
even a bounded read-only exploration, which itself converges faster than
a full coding session. Unlike the coding/retrieval providers, this
provider has no `steps_used`/`TerminationReason` to report --
`ReasoningCapability.complete()`'s return type is the bare validated
`SchemaT`, with no envelope for that observability metadata -- so no
`_estimate_steps_used`/`_map_termination` equivalent exists here; the
step budget only ever surfaces as the `send_and_wait` timeout, and a
session that fails to converge within it raises `ProviderError` (via the
SDK's own documented `TimeoutError`, caught by the same broad
`except Exception` every other provider in this package uses to satisfy
its "never let an arbitrary exception escape" contract).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import re
from typing import Any, Dict, Optional, Type

from pydantic import ValidationError

from ai_sdlc.capabilities.reasoning import (
    MalformedResponseError,
    ProviderError,
    ReasoningCapability,
    SchemaT,
)

try:
    import copilot as _copilot_sdk
    from copilot.generated import rpc as _copilot_rpc

    _COPILOT_SDK_IMPORT_ERROR: Optional[BaseException] = None
except ImportError as exc:  # pragma: no cover - exercised only when the
    # optional `github-copilot-sdk` extra (and its Python 3.11+
    # requirement) isn't installed. Import failure is deferred to
    # provider construction, not module import -- same convention
    # `coding_copilot.py`/`retrieval_copilot.py` follow -- so this module
    # always imports cleanly regardless of environment.
    _copilot_sdk = None
    _copilot_rpc = None
    _COPILOT_SDK_IMPORT_ERROR = exc

PROVIDER_NAME = "github_copilot_sdk_reasoning"

#: Bounded-agentic-loop step budget for a single reasoning completion --
#: smaller than `retrieval.py`'s `DEFAULT_MAX_STEPS = 20` (itself smaller
#: than `coding.py`'s 40): answering one prompt with zero tools available
#: converges faster than even a bounded read-only exploration. See module
#: docstring's "Step budget" section for how this translates into a
#: session timeout, not a literal step ceiling.
DEFAULT_MAX_STEPS = 6

#: Same translation heuristic `retrieval_copilot.py`/`coding_copilot.py`
#: use to turn a step budget into a `send_and_wait` wall-clock timeout
#: (documented approximation, not a measured constant). Lower floor than
#: retrieval's 60s: a zero-tool, single-answer task is lighter still.
_STEP_TO_SECONDS_FACTOR = 20.0
_MIN_SESSION_TIMEOUT_SECONDS = 30.0

#: Fenced code block the prompt instructs the session to answer with --
#: see module docstring's "Structured output extraction" section. Accepts
#: an optional `json` language tag; matching is deliberately permissive
#: about surrounding whitespace/prose since this is a best-effort
#: extraction, not a structural guarantee.
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(?P<json>\{.*\})\s*```", re.DOTALL)


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """Read `name` off `obj` regardless of whether the SDK represents it
    as a real attribute-bearing object or a `TypedDict` (a plain `dict` at
    runtime) -- copied from `coding_copilot.py`/`retrieval_copilot.py`'s
    helper of the same name/behavior; see those modules' docstrings for
    the verified SDK inconsistency this works around."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _extract_json_payload(text: str) -> Dict[str, Any]:
    """Parse the structured-output JSON object out of a session's final
    response text -- see module docstring's "Structured output
    extraction" section. Prefers a fenced ```json code block; falls back
    to treating the whole trimmed response as JSON (in case the model
    answers with bare JSON and no fence). Raises `ValueError` (wrapped
    into `MalformedResponseError` by the caller) if neither yields a
    JSON object."""
    match = _JSON_BLOCK_RE.search(text or "")
    candidate = match.group("json") if match else (text or "").strip()

    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(f"response did not contain a parseable JSON object: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"parsed JSON was not an object (got {type(payload).__name__})")

    return payload


class CopilotReasoningProvider(ReasoningCapability):
    """The Copilot-SDK-backed `ReasoningCapability` provider -- the
    Copilot-side sibling of `AnthropicReasoningProvider`
    (`reasoning_anthropic.py`) and the reasoning-side sibling of
    `CopilotCodingProvider`/`CopilotRetrievalProvider`.

    Requires the optional `copilot` extra (the same `github-copilot-sdk`
    dependency `coding_copilot.py`/`retrieval_copilot.py` already require
    -- see `pyproject.toml`) and an authenticated Copilot CLI session
    (`use_logged_in_user=True` by default, matching those providers' own
    credential handling: this provider does not manage credentials
    itself).
    """

    def __init__(self, *, model: Optional[str] = None, max_steps: int = DEFAULT_MAX_STEPS) -> None:
        if _COPILOT_SDK_IMPORT_ERROR is not None:
            raise ProviderError(
                "copilot_reasoning_provider: github-copilot-sdk is not usable in this "
                f"environment ({_COPILOT_SDK_IMPORT_ERROR!r}); install the optional "
                "extra with `pip install ai-sdlc[copilot]` (requires Python 3.11+). "
                "See this module's and `coding_copilot.py`'s docstrings for what was "
                "and wasn't independently verified about this dependency."
            )
        self.model = model
        self.max_steps = max_steps

    # -- ReasoningCapability --------------------------------------------

    def complete(self, prompt: str, *, output_schema: Type[SchemaT]) -> SchemaT:
        try:
            final_event = self._run_async(self._run_session(prompt, output_schema))
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - any unexpected SDK failure
            # must still surface as ProviderError per this capability's
            # failure contract, never an arbitrary exception.
            raise ProviderError(
                f"copilot_reasoning_provider: session failed before producing a result: {exc}"
            ) from exc

        return self._parse_result(final_event, output_schema)

    # -- session orchestration -------------------------------------------

    async def _run_session(self, prompt: str, output_schema: Type[SchemaT]) -> Any:
        timeout = max(_MIN_SESSION_TIMEOUT_SECONDS, self.max_steps * _STEP_TO_SECONDS_FACTOR)

        client = _copilot_sdk.CopilotClient(use_logged_in_user=True)
        await client.start()
        try:
            session = await client.create_session(
                on_permission_request=self._make_permission_handler(),
                on_user_input_request=self._make_user_input_handler(),
                model=self.model,
                available_tools=[],
            )
            try:
                final_event = await session.send_and_wait(
                    self._build_prompt(prompt, output_schema),
                    agent_mode="autopilot",
                    timeout=timeout,
                )
            finally:
                await session.disconnect()
        finally:
            await client.stop()

        return final_event

    def _run_async(self, coro: Any) -> Any:
        """Identical bridging strategy to `coding_copilot.py`/
        `retrieval_copilot.py`'s `_run_async` -- see those modules'
        docstrings for why this is necessary (a synchronous, single-call
        `ReasoningCapability.complete()` must be safe to call from inside
        an already-running event loop, since `SpecialistAgent.execute()`
        is called synchronously but agents themselves may run inside an
        async caller)."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()

    # -- prompt assembly ---------------------------------------------------

    def _build_prompt(self, prompt: str, output_schema: Type[SchemaT]) -> str:
        schema_json = json.dumps(output_schema.model_json_schema(), indent=2)
        return "\n".join(
            [
                prompt,
                "",
                "Respond with *only* a single fenced code block, formatted exactly as",
                "```json",
                "{...}",
                "```",
                "containing one JSON object that satisfies this JSON Schema:",
                schema_json,
                "",
                "Do not include any prose, explanation, or additional text outside that "
                "one fenced code block. You have no tools available for this task -- "
                "answer directly from the prompt above.",
            ]
        )

    # -- permission / user-input callbacks -----------------------------------

    def _make_permission_handler(self):
        """Unconditionally rejects every permission-request kind -- see
        module docstring's "Structural 'no tools at all' guarantee"
        section for why `available_tools=[]` is the primary guarantee and
        this callback is belt-and-suspenders, not the load-bearing
        mechanism."""

        async def handler(perm_request: Any):
            kind = _field(perm_request, "kind")
            return _copilot_rpc.PermissionDecisionReject(
                feedback=(
                    f"copilot_reasoning_provider: permission kind {kind!r} is never "
                    "granted -- this session has no tools available at all"
                )
            )

        return handler

    def _make_user_input_handler(self):
        """Auto-answers mid-session clarifying questions unattended, same
        "final structured verdict, no human in the loop" model every other
        provider in this package establishes."""

        async def handler(request: Any):
            choices = _field(request, "choices", None) or []
            if choices:
                answer, was_freeform = choices[0], False
            else:
                answer, was_freeform = (
                    "No human reviewer is available mid-session, and no tools are "
                    "available for this task. Answer using only the prompt already "
                    "provided; if it genuinely cannot be resolved that way, say so "
                    "explicitly inside the required JSON response instead of waiting "
                    "for clarification.",
                    True,
                )
            return _copilot_sdk.session.UserInputResponse(answer=answer, wasFreeform=was_freeform)

        return handler

    # -- post-session verdict -----------------------------------------------

    def _parse_result(self, final_event: Any, output_schema: Type[SchemaT]) -> SchemaT:
        raw_text = _field(final_event, "result", None) or _field(final_event, "summary", None)
        if not isinstance(raw_text, str) or not raw_text.strip():
            raise MalformedResponseError(
                "copilot_reasoning_provider: session ended without a usable text result"
            )

        try:
            payload = _extract_json_payload(raw_text)
        except ValueError as exc:
            raise MalformedResponseError(
                f"copilot_reasoning_provider: {exc}"
            ) from exc

        try:
            return output_schema(**payload)
        except ValidationError as exc:
            raise MalformedResponseError(
                f"copilot_reasoning_provider: response failed schema validation: {exc}"
            ) from exc
