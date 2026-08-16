"""`SageCapability` provider-selection factory.

Follows `reasoning_factory.py`/`coding_factory.py`/`retrieval_factory.py`'s
dispatch pattern (same `AI_SDLC_AGENT_FRAMEWORK` env var, mock/claude/
copilot, lazy Copilot import so importing this module never requires the
optional `github-copilot-sdk` extra) -- per the locked Sage Phase 2
design's edge case #5: "Sage inherits the workspace's single
`AI_SDLC_AGENT_FRAMEWORK` choice rather than getting its own."

## One deliberate divergence: `get_default_sage_provider` takes a real
## argument, not zero-arg

`reasoning_factory.py`'s module docstring explains at length why its
sibling factories must stay zero-arg-constructible: agents are
constructed via `AgentRegistry._load_impl`'s bare `cls()` call, inside
the Core Platform API server process, with no workspace context reachable
at construction time. **Sage is not an `Agent`** -- it is never
registered under `.ai-sdlc/agents/*.json` and never constructed by
`AgentRegistry`. It is a capability the **Orchestrator** calls directly
(see `orchestration/orchestrator.py`'s NEEDS_CONTEXT handling), and
`Orchestrator.__init__(self, workspace_path)` already has the real
workspace path in hand -- so this factory can take it as a natural
argument instead of needing the CLI-to-server environment-variable-
threading gymnastics the other three factories require for exactly the
reason their own docstrings explain.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Union

from ai_sdlc.capabilities.connector_resolver import ConnectorResolver
from ai_sdlc.capabilities.providers.sage_mock import MockSageProvider
from ai_sdlc.capabilities.sage import SageCapability

#: Same env var `reasoning_factory.py`/`coding_factory.py`/
#: `retrieval_factory.py` read -- see module docstring for why Sage
#: shares it rather than getting its own.
PROVIDER_ENV_VAR = "AI_SDLC_AGENT_FRAMEWORK"

PROVIDER_MOCK = "mock"
PROVIDER_CLAUDE = "claude"
PROVIDER_COPILOT = "copilot"
SUPPORTED_PROVIDERS = (PROVIDER_MOCK, PROVIDER_CLAUDE, PROVIDER_COPILOT)


def get_default_sage_provider(workspace_path: Union[str, Path]) -> SageCapability:
    """Return the `SageCapability` this process/workspace is configured
    to use, defaulting to `MockSageProvider()` unless
    `AI_SDLC_AGENT_FRAMEWORK` explicitly says otherwise."""
    selected = os.environ.get(PROVIDER_ENV_VAR, PROVIDER_MOCK).strip().lower()
    if not selected:
        selected = PROVIDER_MOCK

    if selected == PROVIDER_MOCK:
        return MockSageProvider()

    resolver = ConnectorResolver(workspace_path)

    if selected == PROVIDER_CLAUDE:
        from ai_sdlc.capabilities.providers.sage_claude import SageClaudeProvider

        return SageClaudeProvider(connector_resolver=resolver)
    if selected == PROVIDER_COPILOT:
        from ai_sdlc.capabilities.providers.sage_copilot import SageCopilotProvider

        return SageCopilotProvider(connector_resolver=resolver)

    raise ValueError(
        f"Unsupported {PROVIDER_ENV_VAR}={selected!r}; expected one of "
        f"{SUPPORTED_PROVIDERS}"
    )
