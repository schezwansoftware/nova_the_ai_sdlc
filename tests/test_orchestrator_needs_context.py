"""Tests for `Orchestrator.invoke_agent_for_stage`'s NEEDS_CONTEXT
handling -- the core of Sage Phase 2 Knowledge Consumption wiring.

Mirrors `tests/test_orchestrator_core.py`'s structure/conventions
exactly: stub agents registered directly via `orch.register_agent`, a
fake `SageCapability` swapped onto `orch.sage` after construction (same
"assign the plain attribute directly" pattern those tests already use for
`orch.store`), and assertions against both the returned dict and the
on-disk audit log / workflow state.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ai_sdlc.agents.base import AgentResult, AgentStatus
from ai_sdlc.capabilities.sage import (
    ProviderError as SageProviderError,
    SageResponse,
    TerminationReason,
    normalize_context_query,
)
from ai_sdlc.orchestration.orchestrator import Orchestrator
from ai_sdlc.orchestration.state import StateStore, WorkflowState

_QUERY = "What does the legacy import step do?"


class FakeSageProvider:
    def __init__(self, response: SageResponse | None = None, raise_exc: Exception | None = None):
        self.response = response
        self.raise_exc = raise_exc
        self.calls = 0

    def ask(self, request):
        self.calls += 1
        if self.raise_exc:
            raise self.raise_exc
        return self.response


class ContextThenCompleteAgent:
    """Asks for context `contexts_needed` times (always the same query),
    then completes. Records every call's inputs so tests can assert on
    what `sage_context` looked like by the time the agent finally
    completes."""

    def __init__(self, contexts_needed: int = 1, query: str = _QUERY):
        self.contexts_needed = contexts_needed
        self.query = query
        self.calls = 0
        self.received_inputs = []

    def execute(self, request):
        self.calls += 1
        self.received_inputs.append(dict(request.inputs))
        if self.calls <= self.contexts_needed:
            return AgentResult(
                request_id=request.request_id,
                workflow_id=request.workflow_id,
                agent_id=request.agent_id,
                status=AgentStatus.NEEDS_CONTEXT,
                context_query=self.query,
            )
        return AgentResult(
            request_id=request.request_id,
            workflow_id=request.workflow_id,
            agent_id=request.agent_id,
            status=AgentStatus.COMPLETED,
        )


def make_workflow(tmp_path: Path, current_stage="requirements"):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    store = StateStore(workspace)
    wf = WorkflowState(workflow_id="wf-001", current_stage=current_stage, initiator_id="user-123")
    store.write_workflow(wf)
    return workspace, wf


def _found_response(**overrides) -> SageResponse:
    fields = dict(
        query=_QUERY,
        found=True,
        answer="It converts CSV rows into normalized Order records.",
        source_connector="confluence",
        source_url="https://example/confluence/page",
        provider_name="fake_sage",
        steps_used=2,
        terminated_reason=TerminationReason.COMPLETED,
    )
    fields.update(overrides)
    return SageResponse(**fields)


def _not_found_response(**overrides) -> SageResponse:
    fields = dict(
        query=_QUERY,
        found=False,
        provider_name="fake_sage",
        steps_used=1,
        terminated_reason=TerminationReason.COMPLETED,
    )
    fields.update(overrides)
    return SageResponse(**fields)


# -- memory miss -> Sage hit ------------------------------------------------------


def test_memory_miss_then_sage_hit_resolves_completes_and_caches(tmp_path):
    workspace, wf = make_workflow(tmp_path)
    orch = Orchestrator(workspace)
    agent = ContextThenCompleteAgent()
    orch.register_agent("po", agent)
    fake_sage = FakeSageProvider(response=_found_response())
    orch.sage = fake_sage

    res = orch.invoke_agent_for_stage(wf, "po")

    assert res["status"] == "completed"
    assert agent.calls == 2  # NEEDS_CONTEXT, then resumed COMPLETED
    assert fake_sage.calls == 1

    resumed_inputs = agent.received_inputs[-1]
    sage_ctx = resumed_inputs.get("sage_context")
    assert sage_ctx and len(sage_ctx) == 1
    assert sage_ctx[0]["found"] is True
    assert sage_ctx[0]["answer"] == "It converts CSV rows into normalized Order records."
    assert sage_ctx[0]["source_connector"] == "confluence"
    assert sage_ctx[0]["source"] == "sage"

    memory = orch.store.read_sage_memory()
    key = normalize_context_query(_QUERY)
    assert key in memory
    assert memory[key]["found"] is True
    assert memory[key]["source_connector"] == "confluence"
    assert memory[key]["answer"] == "It converts CSV rows into normalized Order records."

    audit = (workspace / ".ai-sdlc" / "audit" / "events.jsonl").read_text()
    for event in (
        "context_requested",
        "context_memory_check",
        "sage_invoked",
        "sage_answered",
        "context_resolved",
    ):
        assert event in audit


def test_workflow_never_pauses_for_needs_context(tmp_path):
    """Unlike NEEDS_CLARIFICATION/NEEDS_APPROVAL, resolving NEEDS_CONTEXT
    must never leave the workflow paused -- it's entirely synchronous
    within one invoke_agent_for_stage call."""
    workspace, wf = make_workflow(tmp_path)
    orch = Orchestrator(workspace)
    orch.register_agent("po", ContextThenCompleteAgent())
    orch.sage = FakeSageProvider(response=_found_response())

    orch.invoke_agent_for_stage(wf, "po")

    wf2 = orch.load_workflow()
    assert wf2.status == "running"
    assert wf2.pending_clarification is None
    assert wf2.pending_approval is None


# -- memory hit short-circuits Sage ------------------------------------------------


def test_memory_hit_short_circuits_sage(tmp_path):
    workspace, wf = make_workflow(tmp_path)
    orch = Orchestrator(workspace)
    agent = ContextThenCompleteAgent()
    orch.register_agent("po", agent)

    key = normalize_context_query(_QUERY)
    orch.store.write_sage_memory_entry(
        key,
        {
            "query": _QUERY,
            "answer": "Cached answer from a prior question.",
            "found": True,
            "source_connector": "jira",
            "source_url": None,
            "saved_at": "2026-08-16T00:00:00Z",
        },
    )
    fake_sage = FakeSageProvider(response=_found_response())
    orch.sage = fake_sage

    res = orch.invoke_agent_for_stage(wf, "po")

    assert res["status"] == "completed"
    assert fake_sage.calls == 0  # never invoked -- memory hit

    resumed_inputs = agent.received_inputs[-1]
    sage_ctx = resumed_inputs["sage_context"]
    assert sage_ctx[0]["answer"] == "Cached answer from a prior question."
    assert sage_ctx[0]["source"] == "memory"

    audit = (workspace / ".ai-sdlc" / "audit" / "events.jsonl").read_text()
    assert '"hit": true' in audit
    assert "sage_invoked" not in audit


# -- Sage miss is a normal result, never cached ------------------------------------


def test_sage_miss_is_not_cached_and_workflow_still_completes(tmp_path):
    workspace, wf = make_workflow(tmp_path)
    orch = Orchestrator(workspace)
    agent = ContextThenCompleteAgent()
    orch.register_agent("po", agent)
    orch.sage = FakeSageProvider(response=_not_found_response())

    res = orch.invoke_agent_for_stage(wf, "po")

    assert res["status"] == "completed"
    assert orch.store.read_sage_memory() == {}

    resumed_inputs = agent.received_inputs[-1]
    assert resumed_inputs["sage_context"][0]["found"] is False


# -- Sage failure never fails the workflow -----------------------------------------


def test_sage_failure_is_treated_like_a_miss_not_a_workflow_failure(tmp_path):
    workspace, wf = make_workflow(tmp_path)
    orch = Orchestrator(workspace)
    orch.register_agent("po", ContextThenCompleteAgent())
    orch.sage = FakeSageProvider(raise_exc=SageProviderError("simulated outage"))

    res = orch.invoke_agent_for_stage(wf, "po")

    assert res["status"] == "completed"
    audit = (workspace / ".ai-sdlc" / "audit" / "events.jsonl").read_text()
    assert "sage_failed" in audit
    assert "simulated outage" in audit


# -- malformed NEEDS_CONTEXT result (no usable query) ------------------------------


class EmptyContextQueryAgent:
    def execute(self, request):
        return AgentResult(
            request_id=request.request_id,
            workflow_id=request.workflow_id,
            agent_id=request.agent_id,
            status=AgentStatus.NEEDS_CONTEXT,
            context_query="   ",
        )


def test_needs_context_without_usable_query_is_retried_then_fails(tmp_path):
    workspace, wf = make_workflow(tmp_path)
    orch = Orchestrator(workspace)
    orch.register_agent("po", EmptyContextQueryAgent())

    res = orch.invoke_agent_for_stage(wf, "po")

    assert res["status"] == "failed"
    assert res["error"] == "invalid_agent_output"

    audit = (workspace / ".ai-sdlc" / "audit" / "events.jsonl").read_text()
    assert "agent_result_invalid" in audit


# -- context round budget bounds the loop -------------------------------------------


def test_context_round_budget_prevents_an_infinite_loop(tmp_path):
    workspace, wf = make_workflow(tmp_path)
    orch = Orchestrator(workspace)
    orch.max_context_rounds = 2
    agent = ContextThenCompleteAgent(contexts_needed=1000)  # always asks for context
    orch.register_agent("po", agent)
    orch.sage = FakeSageProvider(response=_not_found_response())

    res = orch.invoke_agent_for_stage(wf, "po")

    assert res["status"] == "failed"
    assert res["details"]["reason"] == "needs_context_loop_exceeded"

    wf2 = orch.load_workflow()
    assert wf2.status == "failed"

    audit = (workspace / ".ai-sdlc" / "audit" / "events.jsonl").read_text()
    assert "context_budget_exceeded" in audit
    assert "needs_context_loop_exceeded" in audit


def test_context_rounds_never_consume_the_retry_budget(tmp_path):
    """A needs_context round is not a failure -- must never count against
    max_attempts/wf.retry_count."""
    workspace, wf = make_workflow(tmp_path)
    orch = Orchestrator(workspace)
    agent = ContextThenCompleteAgent(contexts_needed=2)
    orch.register_agent("po", agent)
    orch.sage = FakeSageProvider(response=_found_response())

    res = orch.invoke_agent_for_stage(wf, "po")

    assert res["status"] == "completed"
    wf2 = orch.load_workflow()
    assert wf2.retry_count.get("po", 0) == 0


# -- connector-skip visibility -------------------------------------------------------


def test_skipped_connectors_are_audited(tmp_path):
    workspace, wf = make_workflow(tmp_path)
    orch = Orchestrator(workspace)
    orch.register_agent("po", ContextThenCompleteAgent())
    response = _found_response(
        metadata={"skipped": [{"name": "onedrive", "reason": "not_configured"}]}
    )
    orch.sage = FakeSageProvider(response=response)

    orch.invoke_agent_for_stage(wf, "po")

    audit = (workspace / ".ai-sdlc" / "audit" / "events.jsonl").read_text()
    assert "connector_skipped" in audit
    assert "onedrive" in audit
