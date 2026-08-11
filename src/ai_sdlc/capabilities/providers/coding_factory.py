"""`CodingCapability` provider-selection factory.

The (not-yet-built) Developer Agent will need a zero-argument way to get
"whichever `CodingCapability` this workspace has configured, defaulting
to the mock" -- this module is that seam, mirroring
`reasoning_factory.py`'s `get_default_reasoning_provider()` exactly in
shape and intent. It is deliberately usable (and tested) before that
agent exists, the same "built and ready, dormant until a later piece
wires it up" pattern this repository already used for
`RetrievalCapability` before `ArchitectureAgent` called it.

## Selection mechanism: the same environment variable as
`retrieval_factory.py`, not a second one

`AI_SDLC_AGENT_FRAMEWORK` -- not a `CodingCapability`-specific env var --
because the actual user-facing feature (`ai-sdlc init`'s interactive
"which AI agent framework would you like to use?" prompt, persisted to
`CLIConfig.agent_framework`) is a single choice governing every
interchangeable-agent-framework capability at once, not a per-capability
setting. `retrieval_factory.py`'s `get_default_retrieval_provider()`
reads this exact same variable for the exact same reason -- see that
module's docstring, intentionally near-identical to this one. (The plain
single-call `ReasoningCapability` "think and answer" step is explicitly
*not* part of this shared choice: Copilot has no plain-completion API
equivalent to pick between, only an agentic/session-based one like
Claude's, so reasoning provider selection stays on its own separate
`AI_SDLC_REASONING_PROVIDER` env var, untouched by this module.)

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
here -- see `reasoning_factory.py` for it.

One thing has changed since that argument was written, though:
`CLIConfig` now actually has an `agent_framework` field, and
`cli/handlers.py:run_init` (when `--start-server` is used) now threads it
into the spawned server subprocess's environment as
`AI_SDLC_AGENT_FRAMEWORK` explicitly, via `bootstrap.spawn_server`'s
`env=` parameter -- rather than relying on an operator to `export
AI_SDLC_REASONING_PROVIDER=...` by hand the way the reasoning provider
still does. This factory doesn't care how the variable got set (by the
CLI, or by an operator's shell directly -- identical either way): it only
ever reads `os.environ`, so that CLI-side improvement is transparent to
it and required no change here beyond there being a CLI layer worth
reading from in the first place. If `agent_framework` was never
configured for a workspace, the CLI deliberately does not set this
variable at all (see `run_init`'s own comment) -- this factory's "unset
means mock" default below is what makes that safe.

## Values

`AI_SDLC_AGENT_FRAMEWORK`:
  - unset, empty, or `"mock"` (default): `MockCodingProvider()` -- the
    hard default everywhere nothing has been explicitly configured,
    including every test and CI run, exactly matching
    `reasoning_factory.py`'s own default behavior.
  - `"claude"`: `ClaudeAgentSDKProvider()` (see `providers/claude_sdk.py`),
    Nova's confirmed V1/default coding provider.
  - `"copilot"`: `CopilotCodingProvider()` (see
    `providers/coding_copilot.py`), imported lazily inside
    `get_default_coding_provider()` rather than at this module's top
    level, so importing *this* factory module never requires the
    optional `github-copilot-sdk` package (the `copilot` extra) to be
    installed. This is belt-and-suspenders on top of
    `coding_copilot.py`'s own already-guarded top-level `import copilot`
    (which never raises even when the package is absent -- it defers
    failure to `CopilotCodingProvider.__init__`, see that module's
    docstring and `test_coding_copilot_import_guard.py`) -- deferring the
    import here as well means a hard `ImportError` at Python-syntax/
    module-load time in some *other*, stricter environment is never this
    factory's problem to inherit, even indirectly.
  - anything else: `ValueError` -- an explicitly-set-but-unrecognized
    value is a configuration mistake, not a case to silently fall back
    from.
"""
from __future__ import annotations

import os

from ai_sdlc.capabilities.coding import CodingCapability
from ai_sdlc.capabilities.providers.coding_mock import MockCodingProvider

#: Env var a workspace/process sets to opt into a real `CodingCapability`
#: provider. See module docstring for why this is the same variable
#: `retrieval_factory.py` reads, and why it's an environment variable at
#: all rather than a direct `CLIConfig` read.
PROVIDER_ENV_VAR = "AI_SDLC_AGENT_FRAMEWORK"

PROVIDER_MOCK = "mock"
PROVIDER_CLAUDE = "claude"
PROVIDER_COPILOT = "copilot"
SUPPORTED_PROVIDERS = (PROVIDER_MOCK, PROVIDER_CLAUDE, PROVIDER_COPILOT)


def get_default_coding_provider() -> CodingCapability:
    """Return the `CodingCapability` this process/workspace is configured
    to use, defaulting to `MockCodingProvider()` unless
    `AI_SDLC_AGENT_FRAMEWORK` explicitly says otherwise."""
    selected = os.environ.get(PROVIDER_ENV_VAR, PROVIDER_MOCK).strip().lower()
    if not selected:
        selected = PROVIDER_MOCK

    if selected == PROVIDER_MOCK:
        return MockCodingProvider()
    if selected == PROVIDER_CLAUDE:
        from ai_sdlc.capabilities.providers.claude_sdk import ClaudeAgentSDKProvider

        return ClaudeAgentSDKProvider()
    if selected == PROVIDER_COPILOT:
        from ai_sdlc.capabilities.providers.coding_copilot import CopilotCodingProvider

        return CopilotCodingProvider()

    raise ValueError(
        f"Unsupported {PROVIDER_ENV_VAR}={selected!r}; expected one of {SUPPORTED_PROVIDERS}"
    )
