"""Deterministic, offline `SageCapability` implementation.

`MockSageProvider` makes zero network calls and never launches a real MCP
connector subprocess. There are no real connectors to search against, so
the honest default is `found=False` for every question -- mirroring
`MockReasoningProvider`'s "deterministic wiring oracle, not a stand-in
for a real model's judgment" stance (see `providers/mock.py`'s
docstring). This remains the hard default `SageCapability` implementation
-- every test, CI run, and any workspace that hasn't explicitly
configured a real provider gets this, never a live agentic session (see
`providers/sage_factory.py`).

Test hook (documented, not a hidden hack):
    - `MockSageProvider(force_found={"answer": "...", "source_connector":
      "jira", "source_url": "..."})` makes every `ask()` call
      deterministically return `found=True` with those fields, so the
      full Orchestrator -> memory-write -> resume loop
      (`orchestration/orchestrator.py`) can be exercised without a real
      agentic session. Also available per-call via
      `ask(..., force_found=...)`, which takes precedence over the
      constructor-level setting for that one call.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ai_sdlc.capabilities.sage import SageCapability, SageRequest, SageResponse, TerminationReason

PROVIDER_NAME = "mock_sage"


class MockSageProvider(SageCapability):
    def __init__(self, force_found: Optional[Dict[str, Any]] = None) -> None:
        self.force_found = force_found

    def ask(self, request: SageRequest, *, force_found: Optional[Dict[str, Any]] = None) -> SageResponse:
        effective = force_found if force_found is not None else self.force_found

        if effective:
            return SageResponse(
                query=request.query,
                found=True,
                answer=str(effective.get("answer", "")),
                source_connector=effective.get("source_connector"),
                source_url=effective.get("source_url"),
                provider_name=PROVIDER_NAME,
                steps_used=1,
                terminated_reason=TerminationReason.COMPLETED,
            )

        return SageResponse(
            query=request.query,
            found=False,
            provider_name=PROVIDER_NAME,
            steps_used=0,
            terminated_reason=TerminationReason.COMPLETED,
            metadata={"reason": "mock_provider_never_finds_anything"},
        )
