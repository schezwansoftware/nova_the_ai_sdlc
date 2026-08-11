"""`RetrievalCapability` provider-selection factory.

`ArchitectureAgent.__init__` (`agents/architecture/architecture_agent.py`)
needs a zero-argument way to get "whichever `RetrievalCapability` this
workspace has configured, defaulting to the mock" -- this module is that
seam, mirroring `reasoning_factory.py`'s
`get_default_reasoning_provider()` (and this package's own
`coding_factory.py`) exactly in shape and intent.

## Selection mechanism: the same environment variable as
`coding_factory.py`, not a second one

`AI_SDLC_AGENT_FRAMEWORK` -- not a `RetrievalCapability`-specific env var
-- because the actual user-facing feature (`ai-sdlc init`'s interactive
"which AI agent framework would you like to use?" prompt, persisted to
`CLIConfig.agent_framework`) is a single choice governing every
interchangeable-agent-framework capability at once, not a per-capability
setting. `coding_factory.py`'s `get_default_coding_provider()` reads this
exact same variable for the exact same reason -- see that module's
docstring, intentionally near-identical to this one. (The plain
single-call `ReasoningCapability` "think and answer" step is explicitly
*not* part of this shared choice -- see `coding_factory.py`'s docstring
for why -- so reasoning provider selection stays on its own separate
`AI_SDLC_REASONING_PROVIDER` env var, untouched by this module.)

`ProviderError`/ `MalformedResponseError` are deliberately **not**
imported from `coding.py`/`coding_factory.py` even though the shape here
is conceptually identical -- this follows `retrieval.py`'s own
self-containment convention (see that module's docstring) of never
coupling `RetrievalCapability`'s stability to `CodingCapability`'s
internals for no real benefit.

## Selection mechanism: why a plain environment variable at all

`reasoning_factory.py`'s module docstring already did the hard analysis
of why an environment variable -- not a direct `CLIConfig` read -- is how
a provider gets selected: agents are constructed by
`AgentRegistry._load_impl` (`agents/registry.py`) via a bare `cls()` call
inside the Core Platform API server process (`ai_sdlc.platform.server`),
a *separate subprocess* from the CLI (spawned by
`cli/bootstrap.py:spawn_server`); `CLIConfig` (`cli/config.py`) lives in
the CLI process's own memory/config directory and was never reachable
from there without new plumbing. That argument is not repeated in full
here -- see `reasoning_factory.py` for it. `coding_factory.py`'s
docstring covers the one thing that *has* changed since that argument was
written (the CLI now actually threads `AI_SDLC_AGENT_FRAMEWORK` into the
spawned subprocess's environment itself, via
`cli/handlers.py:run_init`/`bootstrap.spawn_server`'s `env=` parameter) --
also not repeated here, since both factories read the exact same
variable and are affected identically.

## Values

`AI_SDLC_AGENT_FRAMEWORK`:
  - unset, empty, or `"mock"` (default): `MockRetrievalProvider()` -- the
    hard default everywhere nothing has been explicitly configured,
    including every test and CI run, exactly matching
    `reasoning_factory.py`/`coding_factory.py`'s own default behavior.
  - `"claude"`: `ClaudeAgentSDKRetrievalProvider()` (see
    `providers/retrieval_claude.py`), Nova's confirmed V1/default
    retrieval provider.
  - `"copilot"`: a Copilot-backed `RetrievalCapability` provider, imported
    lazily inside `get_default_retrieval_provider()` (never at this
    module's top level) -- mirroring `coding_factory.py`'s identical
    treatment of `providers/coding_copilot.py`, so importing *this*
    factory module never requires the optional `github-copilot-sdk`
    package to be installed.

    As of this factory's own construction, `providers/retrieval_copilot.py`
    is a **sibling, independently-scoped task** (parallel to this one,
    same "Copilot equivalent of the Claude-backed provider" shape
    `coding_copilot.py` already established for `CodingCapability`) that
    may or may not have landed yet -- this module does not build that
    provider itself. The lazy import below is wrapped so that:
      - if `providers/retrieval_copilot.py` exists and exposes
        `CopilotRetrievalProvider` (the name this factory expects, mirroring
        `coding_copilot.py`'s `CopilotCodingProvider` naming exactly), it is
        used, identically to the `"claude"` branch;
      - if that module doesn't exist yet in this checkout, or doesn't (yet)
        expose that exact class name, `get_default_retrieval_provider()`
        raises `ProviderError` with a clear, actionable message rather than
        letting a bare `ImportError`/`AttributeError` escape -- matching
        every other provider's failure contract (`ProviderError` for "could
        not produce a usable provider at all"). Once that sibling task
        lands with a matching class name, this branch starts working with
        no change needed here; if it lands under a different name, fixing
        this one import line is the only reconciliation required.
  - anything else: `ValueError` -- an explicitly-set-but-unrecognized
    value is a configuration mistake, not a case to silently fall back
    from.
"""
from __future__ import annotations

import os

from ai_sdlc.capabilities.providers.retrieval_mock import MockRetrievalProvider
from ai_sdlc.capabilities.retrieval import ProviderError, RetrievalCapability

#: Env var a workspace/process sets to opt into a real `RetrievalCapability`
#: provider. See module docstring for why this is the same variable
#: `coding_factory.py` reads, and why it's an environment variable at all
#: rather than a direct `CLIConfig` read.
PROVIDER_ENV_VAR = "AI_SDLC_AGENT_FRAMEWORK"

PROVIDER_MOCK = "mock"
PROVIDER_CLAUDE = "claude"
PROVIDER_COPILOT = "copilot"
SUPPORTED_PROVIDERS = (PROVIDER_MOCK, PROVIDER_CLAUDE, PROVIDER_COPILOT)


def get_default_retrieval_provider() -> RetrievalCapability:
    """Return the `RetrievalCapability` this process/workspace is
    configured to use, defaulting to `MockRetrievalProvider()` unless
    `AI_SDLC_AGENT_FRAMEWORK` explicitly says otherwise."""
    selected = os.environ.get(PROVIDER_ENV_VAR, PROVIDER_MOCK).strip().lower()
    if not selected:
        selected = PROVIDER_MOCK

    if selected == PROVIDER_MOCK:
        return MockRetrievalProvider()
    if selected == PROVIDER_CLAUDE:
        from ai_sdlc.capabilities.providers.retrieval_claude import (
            ClaudeAgentSDKRetrievalProvider,
        )

        return ClaudeAgentSDKRetrievalProvider()
    if selected == PROVIDER_COPILOT:
        return _get_copilot_retrieval_provider()

    raise ValueError(
        f"Unsupported {PROVIDER_ENV_VAR}={selected!r}; expected one of {SUPPORTED_PROVIDERS}"
    )


def _get_copilot_retrieval_provider() -> RetrievalCapability:
    """Lazily import and construct the Copilot-backed `RetrievalCapability`
    provider -- see module docstring's `"copilot"` bullet for why this is
    wrapped rather than a direct top-level import."""
    try:
        from ai_sdlc.capabilities.providers.retrieval_copilot import (
            CopilotRetrievalProvider,
        )
    except ImportError as exc:
        raise ProviderError(
            "retrieval_factory: no Copilot-backed RetrievalCapability provider "
            "(ai_sdlc.capabilities.providers.retrieval_copilot.CopilotRetrievalProvider) "
            f"is available in this checkout yet ({exc!r}). See this module's docstring: "
            "that provider is a separate, independently-scoped task."
        ) from exc
    return CopilotRetrievalProvider()
