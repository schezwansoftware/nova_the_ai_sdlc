"""Regression coverage for a real bug hit while manually testing the
Claude/Copilot agent-framework preference (`AI_SDLC_AGENT_FRAMEWORK`):
when a specialist agent's `impl` is found but fails to *construct* (e.g.
`ArchitectureAgent()` -> ... -> some `..._factory.get_default_..._provider()`
raising `ProviderError` because `AI_SDLC_AGENT_FRAMEWORK=claude` is set but
the real SDK it selects isn't installed), `AgentRegistry` used to swallow
the real exception unconditionally and report a misleading, contextless
"Agent not found: architecture" -- indistinguishable from the metadata
file genuinely not existing at all.

Which capability's factory actually raises first here is
`SpecialistAgent.__init__`'s construction order, not something this test
controls directly: `ArchitectureAgent.__init__` calls `super().__init__()`
(which resolves `reasoning` via `reasoning_factory.
get_default_reasoning_provider()`) before it resolves its own
`retrieval` via `retrieval_factory.get_default_retrieval_provider()` --
see `architecture_agent.py`. Since `reasoning_factory.py` now reads the
same `AI_SDLC_AGENT_FRAMEWORK` variable `retrieval_factory.py` does
(previously it read a separate `AI_SDLC_REASONING_PROVIDER`, left unset
here, so this scenario used to trip on `retrieval_factory`'s
`claude-agent-sdk`-not-installed check instead), `AI_SDLC_AGENT_FRAMEWORK=
claude` now trips `AnthropicReasoningProvider`'s own construction check
first -- exercised below by leaving `ANTHROPIC_API_KEY` unset so it fails
fast on the missing-credential check `reasoning_anthropic.py` documents
(deliberately *not* the SDK-unavailable path, to keep this test
independent of whether the optional `anthropic` package happens to be
installed in whatever environment runs it). The bug this test protects
against is unchanged: a genuine construction failure must still be
recorded, not discarded as "Agent not found."

No network access / external credentials required.
"""
from __future__ import annotations

import json

import pytest

from ai_sdlc.agents.registry import AgentRegistry
from ai_sdlc.orchestration.orchestrator import Orchestrator
from ai_sdlc.orchestration.state import WorkflowState


def _write_architecture_metadata(workspace) -> None:
    agents_dir = workspace / ".ai-sdlc" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "agent_id": "architecture",
        "version": "1.0",
        "impl": "ai_sdlc.agents.architecture.architecture_agent.ArchitectureAgent",
        "input_schema": "architecture-input-v1",
        "output_schema": "architecture-output-v1",
        "capabilities": ["reasoning", "retrieval"],
        "state_artifact": "architecture.json",
    }
    (agents_dir / "architecture.json").write_text(json.dumps(metadata), encoding="utf-8")


def test_construction_failure_is_recorded_not_silently_dropped(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_SDLC_AGENT_FRAMEWORK", "claude")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _write_architecture_metadata(workspace)

    registry = AgentRegistry(workspace)

    # The agent genuinely failed to construct -- `SpecialistAgent.__init__`
    # resolves `reasoning` before `ArchitectureAgent` resolves its own
    # `retrieval`, and `AnthropicReasoningProvider` raises at construction
    # time when `ANTHROPIC_API_KEY` isn't set (see module docstring) -- so
    # `get()` still correctly returns None...
    assert registry.get("architecture") is None
    # ...but unlike before this fix, the *reason* is no longer discarded.
    load_error = registry.get_load_error("architecture")
    assert load_error is not None
    assert "ANTHROPIC_API_KEY" in load_error or "anthropic_reasoning_provider" in load_error


def test_agent_genuinely_absent_has_no_load_error(tmp_path):
    """The other failure mode -- no metadata file at all -- must stay
    distinguishable from a construction failure: `get_load_error` returns
    None, not a fabricated reason."""
    workspace = tmp_path / "repo"
    workspace.mkdir()
    registry = AgentRegistry(workspace)

    assert registry.get("architecture") is None
    assert registry.get_load_error("architecture") is None


def test_orchestrator_surfaces_the_real_reason_instead_of_agent_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_SDLC_AGENT_FRAMEWORK", "claude")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _write_architecture_metadata(workspace)

    wf = WorkflowState(workflow_id="wf-load-error", current_stage="architecture", initiator_id="user-1")
    orch = Orchestrator(workspace)
    orch.store.write_workflow(wf)

    with pytest.raises(RuntimeError) as excinfo:
        orch.invoke_agent_for_stage(wf, "architecture", inputs={"requirements": {"feature_title": "x"}})

    message = str(excinfo.value)
    assert "failed to load" in message
    assert "ANTHROPIC_API_KEY" in message or "anthropic_reasoning_provider" in message
    # The old, contextless message must not be what the caller sees when a
    # real load error is available.
    assert message != "Agent not found: architecture"


def test_orchestrator_still_reports_plain_not_found_when_truly_absent(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    wf = WorkflowState(workflow_id="wf-truly-absent", current_stage="architecture", initiator_id="user-1")
    orch = Orchestrator(workspace)
    orch.store.write_workflow(wf)

    with pytest.raises(RuntimeError, match="^Agent not found: architecture$"):
        orch.invoke_agent_for_stage(wf, "architecture", inputs={})
