"""`ReasoningCapability` provider-selection factory.

`SpecialistAgent.__init__` (`agents/framework.py`) needs a
zero-argument way to get "whichever `ReasoningCapability` this workspace
has configured, defaulting to the mock" -- this module is that seam.

## Selection mechanism: the same environment variable as
`coding_factory.py`/`retrieval_factory.py`, not a separate one

An earlier version of this module read a `ReasoningCapability`-specific
env var (`AI_SDLC_REASONING_PROVIDER`), justified at the time by "Copilot
has no equivalent single-completion API" -- reasoning was therefore
treated as Claude/Anthropic-only and kept off the shared switch
`coding_factory.py`/`retrieval_factory.py` both use. That justification
was wrong: Copilot's SDK doesn't need a literal single-call completion
endpoint for a Copilot-backed `ReasoningCapability` provider to exist --
`reasoning_copilot.py`'s `CopilotReasoningProvider` uses the exact same
technique `retrieval_copilot.py` already uses for extracting one
structured answer out of a bounded, free-form agentic session, just
applied to an arbitrary caller-supplied schema instead of a fixed
summary+sources shape. See that module's docstring for the full account.

Once a real Copilot reasoning provider exists, keeping reasoning
selection on its own separate env var was itself the remaining bug: the
actual user-facing feature (`ai-sdlc init`'s interactive "which AI agent
framework would you like to use?" prompt, persisted to
`CLIConfig.agent_framework` and threaded into the spawned server
subprocess as `AI_SDLC_AGENT_FRAMEWORK` -- see `cli/handlers.py:run_init`/
`bootstrap.spawn_server`) is meant to be **one** choice governing every
interchangeable-agent-framework capability the platform calls through,
reasoning included -- not a per-capability setting a workspace could set
inconsistently (e.g. `AI_SDLC_AGENT_FRAMEWORK=copilot` for coding/
retrieval while reasoning silently stayed on Anthropic because a second
variable was never set). This module now reads the exact same
`AI_SDLC_AGENT_FRAMEWORK` variable `coding_factory.py`/
`retrieval_factory.py` already read, for the same reason those two
modules read it from each other -- see either module's docstring,
intentionally near-identical to this one. `AI_SDLC_REASONING_PROVIDER` is
removed entirely rather than kept as a deprecated alias: nothing real
depends on it yet (nothing is deployed), so a compatibility shim for it
would only reintroduce the two-switches confusion this change exists to
remove.

Note what *doesn't* change with this rename: `"claude"` still means
"backed by the Anthropic Messages API" (`AnthropicReasoningProvider`,
`reasoning_anthropic.py`) for reasoning specifically, same as it always
has -- `"claude"` is the shared framework-preference name (matching
`coding_factory.py`/`retrieval_factory.py`'s own `PROVIDER_CLAUDE`), not
a claim that reasoning literally calls a `claude-agent-sdk` CLI session
the way `CodingCapability`/`RetrievalCapability`'s `"claude"` providers
do. Anthropic's Messages API remains the right real backend for a
single-call reasoning completion regardless of which agent-framework name
selects it.

## Selection mechanism: why a plain environment variable at all

This part of the original analysis was correct and is preserved
unchanged: agents are constructed by `AgentRegistry._load_impl`
(`agents/registry.py`) via a bare `cls()` call with no workspace/CLI
context threaded in at all -- and by design (`SpecialistAgent`'s own
docstring), agents must stay stateless and importable/constructible
independent of any particular caller. The Core Platform API server
process (`ai_sdlc.platform.server`, spawned by
`cli/bootstrap.py:spawn_server` as a *separate* subprocess) is where
agents actually run, via the Orchestrator -- not inside the CLI process
itself. `CLIConfig` (`cli/config.py`) lives in the CLI process's own
home-directory config and was never reachable from either construction
path without new cross-process plumbing. A plain environment variable is
therefore not a shortcut taken to avoid work -- it's the mechanism that
actually fits how/where agents are constructed today, and (per
`coding_factory.py`'s docstring) is exactly what `cli/handlers.py:
run_init` already threads into the spawned server subprocess's
environment when `--start-server` is used, via `bootstrap.spawn_server`'s
`env=` parameter. This factory doesn't care how the variable got set (by
the CLI, or by an operator's shell directly): it only ever reads
`os.environ`.

## Values

`AI_SDLC_AGENT_FRAMEWORK`:
  - unset, empty, or `"mock"` (default): `MockReasoningProvider()` -- the
    hard default everywhere nothing has been explicitly configured,
    including every test and CI run, exactly matching
    `coding_factory.py`/`retrieval_factory.py`'s own default behavior.
  - `"claude"`: `AnthropicReasoningProvider()` (see
    `reasoning_anthropic.py`), which itself raises `ProviderError` at
    construction time if `ANTHROPIC_API_KEY` isn't also set -- a workspace
    that opts into the real provider without configuring credentials
    fails loudly and immediately, not with a silently-wrong mock result.
  - `"copilot"`: `CopilotReasoningProvider()` (see `reasoning_copilot.py`),
    imported lazily inside `get_default_reasoning_provider()` rather than
    at this module's top level, so importing *this* factory module never
    requires the optional `github-copilot-sdk` package (the `copilot`
    extra) to be installed -- mirroring `coding_factory.py`'s identical
    treatment of `coding_copilot.py`.
  - anything else: `ValueError` -- an explicitly-set-but-unrecognized
    value is a configuration mistake, not a case to silently fall back
    from.
"""
from __future__ import annotations

import os

from ai_sdlc.capabilities.providers.mock import MockReasoningProvider
from ai_sdlc.capabilities.providers.reasoning_anthropic import AnthropicReasoningProvider
from ai_sdlc.capabilities.reasoning import ReasoningCapability

#: Env var a workspace/process sets to opt into a real `ReasoningCapability`
#: provider. See module docstring for why this is the same variable
#: `coding_factory.py`/`retrieval_factory.py` read, and why it's an
#: environment variable at all rather than a direct `CLIConfig` read.
PROVIDER_ENV_VAR = "AI_SDLC_AGENT_FRAMEWORK"

PROVIDER_MOCK = "mock"
PROVIDER_CLAUDE = "claude"
PROVIDER_COPILOT = "copilot"
SUPPORTED_PROVIDERS = (PROVIDER_MOCK, PROVIDER_CLAUDE, PROVIDER_COPILOT)


def get_default_reasoning_provider() -> ReasoningCapability:
    """Return the `ReasoningCapability` this process/workspace is
    configured to use, defaulting to `MockReasoningProvider()` unless
    `AI_SDLC_AGENT_FRAMEWORK` explicitly says otherwise."""
    selected = os.environ.get(PROVIDER_ENV_VAR, PROVIDER_MOCK).strip().lower()
    if not selected:
        selected = PROVIDER_MOCK

    if selected == PROVIDER_MOCK:
        return MockReasoningProvider()
    if selected == PROVIDER_CLAUDE:
        return AnthropicReasoningProvider()
    if selected == PROVIDER_COPILOT:
        from ai_sdlc.capabilities.providers.reasoning_copilot import CopilotReasoningProvider

        return CopilotReasoningProvider()

    raise ValueError(
        f"Unsupported {PROVIDER_ENV_VAR}={selected!r}; expected one of "
        f"{SUPPORTED_PROVIDERS}"
    )
