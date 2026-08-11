"""Anthropic-backed `ReasoningCapability` provider.

This is Nova's real V1 `ReasoningCapability` provider (per
`docs/architecture/v1_architecture.md` section 1's key-decisions list, PR
#17, "V1 Providers: Real Single-Call Reasoning..." -- `ReasoningCapability`'s
V1 provider is a real single request/response call to a hosted LLM
(Anthropic), replacing the deterministic mock for real usage). It mirrors
`providers/mock.py`'s contract exactly (subclasses `ReasoningCapability`,
raises the same `ProviderError`/`MalformedResponseError` pair) with a real
backend instead of rule-based text parsing.

## What is verified vs. assumed in this environment

Unlike `providers/claude_sdk.py` (whose `claude-agent-sdk` dependency could
not be installed in that provider's build environment), the `anthropic`
package (PyPI `anthropic`, latest `0.121.0` as of this writing) *was*
installed into a disposable, isolated venv here and introspected directly
-- not taken from training-data memory or documentation alone, per this
project's validated-facts-only convention. Specifically verified by
importing the installed package and inspecting real classes/signatures:

  - `anthropic.Anthropic(api_key=...)` is the synchronous client; its
    `messages.create(*, model, max_tokens, messages, tools=..., tool_choice=...)`
    method signature was read directly off the installed
    `anthropic.resources.messages.Messages.create`.
  - Forced tool-use structured output is a first-class, documented
    parameter shape: `tools=[{"name": ..., "description": ..., "input_schema":
    <json schema dict>}]` (`anthropic.types.ToolParam`'s real fields --
    `name`, `description`, `input_schema` -- confirm this) plus
    `tool_choice={"type": "tool", "name": <tool name>}`
    (`anthropic.types.ToolChoiceToolParam`'s real fields confirm this
    exact shape, not a guessed one).
  - A response's tool invocation arrives as one of `Message.content`'s
    blocks with `block.type == "tool_use"`, `block.name`, and
    `block.input: Dict[str, object]` (`anthropic.types.ToolUseBlock`'s
    real Pydantic fields -- `id`, `type`, `name`, `input` -- confirm this;
    `block.input` is already a parsed dict, not a JSON string requiring a
    second parse).
  - The SDK's exception hierarchy was introspected directly (`__mro__` on
    the actual installed classes): `anthropic.AnthropicError` is the root
    of everything the SDK raises; `anthropic.APIError` (itself an
    `AnthropicError`) is the base of every *call*-related failure --
    `AuthenticationError`, `RateLimitError`, `APIConnectionError`,
    `APIStatusError` (and its other HTTP-status subclasses) all subclass
    `APIError`. Catching `anthropic.APIError` therefore covers auth/
    network/rate-limit/vendor-outage failures in one place, exactly the
    class of failure `ProviderError` exists for.
  - **Genuinely surprising, verified-not-assumed fact that changed this
    provider's design**: constructing `anthropic.Anthropic()` with no
    `api_key` argument and no `ANTHROPIC_API_KEY` environment variable set
    does **not** raise at construction time in the installed SDK -- it
    succeeds silently and would only fail later, on the first actual API
    call. That is the opposite of "fail fast and loud" for a missing
    credential, so this provider does **not** delegate that check to the
    SDK; it reads/validates the API key itself, explicitly, before ever
    constructing a real client (see `__init__` below) -- directly
    satisfying this task's "raise at construction time" requirement, which
    the SDK's own behavior would not have given us for free.
  - `claude-sonnet-5` is a real, current model literal in the installed
    package's own `anthropic.types.model.Model` type alias (confirmed by
    reading that generated file directly), so it is used as this
    provider's default `model` rather than a guessed/dated snapshot
    string. Overridable per-instance (`model=...`) or per-workspace via
    the `AI_SDLC_ANTHROPIC_MODEL` environment variable, since model names
    change faster than this code does.

What is *not* independently verified here: actual network behavior against
the live API (no live call was made -- no API key was used or is required
anywhere in this build/test process), and the exact wire-level retry/
backoff behavior `max_retries` performs internally. Neither matters to this
provider's own logic, which only needs to know how to call `create()` and
how to interpret its two possible outcomes (an exception, or a `Message`).

## API key handling (see `ReasoningCapability` contract)

Reads `ANTHROPIC_API_KEY` from the environment by default -- the same
variable name the Anthropic SDK itself would otherwise silently look for
via `os.environ` if we didn't pass `api_key` explicitly (see
`anthropic.Anthropic.__init__`'s own docstring in the installed package;
`api_key: str | None = None` maps to that lookup once it *is* enforced) --
so this provider invents no separate credential mechanism, exactly as
required. It differs from the SDK only in *when* a missing key is treated
as fatal: here, at `__init__`, not at the first `complete()` call, because
(as verified above) the SDK itself does not enforce that for us.

## Structured output strategy

One fixed tool (`_TOOL_NAME`) is defined per `complete()` call, with
`input_schema` generated straight from `output_schema.model_json_schema()`
(exactly the JSON Schema Pydantic already produces -- no hand-written
schema translation layer). `tool_choice` forces the model to call it, so a
successful response always carries the structured payload as that tool
call's `input` rather than requiring free-text JSON parsing. `block.input`
is then validated against `output_schema` the same way `providers/mock.py`
validates its own generated payload.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Type

from pydantic import ValidationError

from ai_sdlc.capabilities.reasoning import (
    MalformedResponseError,
    ProviderError,
    ReasoningCapability,
    SchemaT,
)

try:
    import anthropic as _anthropic_sdk

    SDK_IMPORT_ERROR: Optional[BaseException] = None
except ImportError as exc:  # pragma: no cover - exercised only when the SDK is absent
    _anthropic_sdk = None
    SDK_IMPORT_ERROR = exc

PROVIDER_NAME = "anthropic"

#: Env var this provider reads for the API key -- deliberately the exact
#: name the Anthropic SDK itself documents/defaults to (see module
#: docstring); no separate credential mechanism is invented.
API_KEY_ENV_VAR = "ANTHROPIC_API_KEY"

#: Optional per-workspace model override, since model identifiers change
#: independently of this provider's own logic.
MODEL_ENV_VAR = "AI_SDLC_ANTHROPIC_MODEL"

#: Verified-real, current (non-dated-snapshot) model literal -- see module
#: docstring. Used only when neither the constructor `model` argument nor
#: `AI_SDLC_ANTHROPIC_MODEL` is set.
DEFAULT_MODEL = "claude-sonnet-5"

#: Generous enough for the structured, list-of-strings-shaped outputs
#: Craft's agent schemas produce (functional requirements, decisions,
#: risks, ...); callers needing more can override per instance.
DEFAULT_MAX_TOKENS = 4096

#: Fixed tool name every `complete()` call forces the model to invoke --
#: see module docstring's "Structured output strategy".
_TOOL_NAME = "emit_structured_output"
_TOOL_DESCRIPTION = (
    "Return the structured output for this request. You must call this "
    "tool exactly once, with arguments that satisfy the provided schema."
)


class AnthropicReasoningProvider(ReasoningCapability):
    """Real `ReasoningCapability` provider backed by the Anthropic Messages
    API, using a forced tool-use call to obtain schema-validated structured
    output (see module docstring).

    Test seam: pass `client=<fake with a .messages.create(...) method>` to
    exercise this provider's own prompt/tool-schema assembly and response
    parsing without the real `anthropic` package or any network access --
    mirrors `providers/claude_sdk.py`'s `_query_fn`/`_options_cls` seams.
    When `client` is provided, neither the real-SDK-availability check nor
    the API-key-presence check below applies (a fake client needs neither).
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        client: Optional[Any] = None,
    ) -> None:
        self.model = model or os.environ.get(MODEL_ENV_VAR) or DEFAULT_MODEL
        self.max_tokens = max_tokens

        if client is not None:
            self._client = client
            return

        resolved_key = api_key or os.environ.get(API_KEY_ENV_VAR)
        if not resolved_key:
            raise ProviderError(
                "anthropic_reasoning_provider: no API key configured. Set the "
                f"{API_KEY_ENV_VAR} environment variable (or pass api_key=... "
                "explicitly). Raised at construction time, deliberately -- the "
                "underlying anthropic SDK does not itself fail fast on a "
                "missing key (verified: `anthropic.Anthropic()` with no key "
                "constructs successfully and would only fail on the first "
                "real API call), so this provider enforces it here instead."
            )

        if _anthropic_sdk is None:
            raise ProviderError(
                "anthropic_reasoning_provider: the `anthropic` package is not "
                f"installed in this environment ({SDK_IMPORT_ERROR!r}); install "
                "it with `pip install anthropic` (or the `ai-sdlc[anthropic]` "
                "extra)."
            )

        self._client = _anthropic_sdk.Anthropic(api_key=resolved_key)

    # -- ReasoningCapability ------------------------------------------------

    def complete(self, prompt: str, *, output_schema: Type[SchemaT]) -> SchemaT:
        tool = {
            "name": _TOOL_NAME,
            "description": _TOOL_DESCRIPTION,
            "input_schema": output_schema.model_json_schema(),
        }

        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[{"role": "user", "content": prompt}],
                tools=[tool],
                tool_choice={"type": "tool", "name": _TOOL_NAME},
            )
        except Exception as exc:
            # Covers anthropic.APIError and every documented subclass
            # (AuthenticationError, RateLimitError, APIConnectionError,
            # APIStatusError, ...) -- see module docstring's verified
            # exception hierarchy -- plus, via this broad `except Exception`,
            # anything genuinely unexpected. Never let an arbitrary SDK
            # exception escape uncaught (task requirement).
            raise ProviderError(
                f"anthropic_reasoning_provider: request failed: {exc}"
            ) from exc

        payload = self._extract_tool_input(response)
        try:
            return output_schema(**payload)
        except ValidationError as exc:
            raise MalformedResponseError(
                f"anthropic_reasoning_provider: response failed schema validation: {exc}"
            ) from exc

    # -- response parsing -----------------------------------------------------

    def _extract_tool_input(self, response: Any) -> Dict[str, Any]:
        content: List[Any] = list(getattr(response, "content", None) or [])
        for block in content:
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == _TOOL_NAME:
                block_input = getattr(block, "input", None)
                if not isinstance(block_input, dict):
                    raise MalformedResponseError(
                        "anthropic_reasoning_provider: tool_use block's `input` "
                        f"was not a dict (got {type(block_input)!r})"
                    )
                return block_input

        raise MalformedResponseError(
            "anthropic_reasoning_provider: response did not contain the "
            f"expected `{_TOOL_NAME}` tool_use block (forced tool_choice was "
            "not honored, or the model declined to call it)"
        )
