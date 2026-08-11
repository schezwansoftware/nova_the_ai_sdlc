"""`ReasoningCapability` provider-selection factory.

`SpecialistAgent.__init__` (`agents/framework.py`) needs a
zero-argument way to get "whichever `ReasoningCapability` this workspace
has configured, defaulting to the mock" -- this module is that seam.

## Selection mechanism: why a plain environment variable, not
`--coding-provider`'s per-workspace-config shape

`docs/architecture/v1_architecture.md` section 20 Open Question 11 (added
alongside the "real V1 reasoning provider" decision, PR #17) explicitly
asks this: reuse `ai-sdlc init`'s existing `--coding-provider`-style
per-workspace setting, or a separate mechanism? This module resolves it in
favor of a separate mechanism, for reasons checked directly against the
current code rather than assumed:

  - `--coding-provider` does not actually exist in this codebase yet --
    `src/ai_sdlc/cli/main.py`'s `init` command has no such option, and
    `CLIConfig` (`cli/config.py`) has no provider field. The architecture
    doc's section 12/1246 mention of it is itself forward-looking, listed
    as a still-open Pixel follow-up. There is no existing mechanism here
    to actually reuse yet.
  - Even once it exists, `CLIConfig` is explicitly documented
    (`cli/config.py`'s own module docstring) as living in the *CLI's* own
    config directory (`~/.config/ai-sdlc-cli/`), never in the target
    workspace's `.ai-sdlc/`, and is read only by the CLI process. Agents
    are constructed by `AgentRegistry._load_impl` (`agents/registry.py`)
    via a bare `cls()` call with no workspace/CLI context threaded in at
    all -- and by design (`SpecialistAgent`'s own docstring): agents must
    stay stateless and importable/constructible independent of any
    particular caller. `ai_sdlc.agents` has no import dependency on
    `ai_sdlc.cli` today, and adding one here to reach `CLIConfig` would
    invert that layering (CLI-owned config reaching into the
    agent/orchestration layer) for a single capability, while every other
    specialist-agent construction path stays workspace-config-free.
  - The Core Platform API server process (`ai_sdlc.platform.server`,
    spawned by `cli/bootstrap.py:spawn_server` as a *separate* subprocess)
    is where agents actually run, via the Orchestrator -- not inside the
    CLI process itself. Even a future on-disk `CLIConfig` extension
    wouldn't be reachable from there without a new cross-process plumbing
    path this task is not scoped to build.

A plain environment variable is therefore not a shortcut taken to avoid
work -- it's the mechanism that actually fits how/where agents are
constructed today. `AGENT_METADATA`'s existing `"capabilities": ["reasoning"]`
declaration (`cli/bootstrap.py`) already establishes the precedent that
*which capabilities* an agent needs is workspace-scaffolded, static
metadata read at discovery time, not something threaded through
`cls()`'s call site -- `AI_SDLC_REASONING_PROVIDER` fits the same "static,
read where the process actually runs" shape, just via the environment
instead of a JSON file, since environment variables are visible to
whichever process (CLI-spawned server or otherwise) actually constructs
the agent, without new plumbing.

If Pixel later builds real per-workspace `--coding-provider`-style config
that a server process can read (e.g. written into `.ai-sdlc/` itself
rather than the CLI's own home-directory config), reconciling this
factory to read from there instead is a natural follow-up -- this module
deliberately keeps the "which provider" decision behind
`get_default_reasoning_provider()` alone, so nothing downstream (agents,
tests) needs to change when that happens.

## Values

`AI_SDLC_REASONING_PROVIDER`:
  - unset, empty, or `"mock"` (default): `MockReasoningProvider()` -- the
    hard default everywhere nothing has been explicitly configured,
    including every test and CI run.
  - `"anthropic"`: `AnthropicReasoningProvider()` (see
    `reasoning_anthropic.py`), which itself raises `ProviderError` at
    construction time if `ANTHROPIC_API_KEY` isn't also set -- a workspace
    that opts into the real provider without configuring credentials
    fails loudly and immediately, not with a silently-wrong mock result.
  - anything else: `ValueError` -- an explicitly-set-but-unrecognized
    value is a configuration mistake, not a case to silently fall back
    from.
"""
from __future__ import annotations

import os

from ai_sdlc.capabilities.providers.mock import MockReasoningProvider
from ai_sdlc.capabilities.providers.reasoning_anthropic import AnthropicReasoningProvider
from ai_sdlc.capabilities.reasoning import ReasoningCapability

#: Env var a workspace sets to opt into a real `ReasoningCapability`
#: provider. See module docstring for why this -- not `--coding-provider`'s
#: shape -- is the selection mechanism.
PROVIDER_ENV_VAR = "AI_SDLC_REASONING_PROVIDER"

PROVIDER_MOCK = "mock"
PROVIDER_ANTHROPIC = "anthropic"
SUPPORTED_PROVIDERS = (PROVIDER_MOCK, PROVIDER_ANTHROPIC)


def get_default_reasoning_provider() -> ReasoningCapability:
    """Return the `ReasoningCapability` this process/workspace is
    configured to use, defaulting to `MockReasoningProvider()` unless
    `AI_SDLC_REASONING_PROVIDER` explicitly says otherwise."""
    selected = os.environ.get(PROVIDER_ENV_VAR, PROVIDER_MOCK).strip().lower()
    if not selected:
        selected = PROVIDER_MOCK

    if selected == PROVIDER_MOCK:
        return MockReasoningProvider()
    if selected == PROVIDER_ANTHROPIC:
        return AnthropicReasoningProvider()

    raise ValueError(
        f"Unsupported {PROVIDER_ENV_VAR}={selected!r}; expected one of "
        f"{SUPPORTED_PROVIDERS}"
    )
