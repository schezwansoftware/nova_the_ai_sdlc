"""Tests for `CopilotRetrievalProvider`'s own logic -- permission-kind
enforcement, the user-input auto-answer fallback, repository-path
verification, prompt assembly, `SOURCES:`-section parsing, termination
mapping, and token-budget truncation.

Mirrors `tests/test_capabilities_coding_copilot.py`'s approach: none of
these tests make a network call, require Copilot credentials, or start a
real Copilot session (`create_session`/`send_and_wait` are never
invoked) -- they exercise the provider's pure-Python plumbing plus,
where the real SDK's request/response classes are needed to prove the
wiring matches their actual shape, the installed `github-copilot-sdk`
package's own classes.

The whole module is skipped if `github-copilot-sdk` (the optional
`copilot` extra) isn't installed -- it requires Python 3.11+, stricter
than this project's own `>=3.10`, so it won't be present in every
environment. This mirrors `test_capabilities_coding_copilot.py`'s own
skip convention.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

copilot = pytest.importorskip("copilot")
from copilot.generated import session_events as se  # noqa: E402
from copilot.generated import rpc  # noqa: E402

from ai_sdlc.capabilities.providers.retrieval_copilot import (  # noqa: E402
    CopilotRetrievalProvider,
    _extract_sources_section,
    _field,
)
from ai_sdlc.capabilities.retrieval import (  # noqa: E402
    ProviderError,
    RetrievalRequest,
    TerminationReason,
)


def _make_request(repository_path, **overrides):
    fields = dict(
        query="How does the order cache get invalidated?",
        repository_path=str(repository_path),
        scope_paths=["src/order_service/cache.py"],
    )
    fields.update(overrides)
    return RetrievalRequest(**fields)


# -- construction / repository-path verification ------------------------------


def test_provider_constructs_when_sdk_is_installed():
    provider = CopilotRetrievalProvider()
    assert provider.model is None


def test_retrieve_rejects_missing_repository_path(tmp_path):
    provider = CopilotRetrievalProvider()
    request = _make_request(tmp_path / "does-not-exist")
    with pytest.raises(ProviderError):
        provider.retrieve(request)


def test_retrieve_accepts_directory_without_git(tmp_path):
    """Unlike CodingCapability's working tree, repository_path doesn't
    need to be a Git repo -- retrieval is generic exploration. This only
    exercises `_verify_repository_path` succeeding (no exception raised
    before the session would be started); it does not start a session."""
    plain_dir = tmp_path / "not-a-repo"
    plain_dir.mkdir()
    provider = CopilotRetrievalProvider()
    provider._verify_repository_path(str(plain_dir))  # must not raise


# -- final-event summary extraction -------------------------------------------
#
# Regression coverage for a real bug found via live testing against the
# installed github-copilot-sdk==1.0.9: `send_and_wait` returns a
# `SessionEvent` whose actual text lives at `.data.content` (an
# `AssistantMessageData`), never at a top-level `.result`/`.summary` --
# see `session.py`'s own docstring example (`match response.data: case
# AssistantMessageData() as data: print(data.content)`), verified live
# against a real authenticated session. `_build_retrieval_result`
# previously read the wrong path and silently fell back to the "No
# context could be derived" summary on every real Copilot call.


def test_build_retrieval_result_extracts_summary_from_event_data_content(tmp_path):
    provider = CopilotRetrievalProvider()
    request = _make_request(tmp_path)
    final_event = {
        "data": {
            "content": (
                "The cache is invalidated on order update.\n\nSOURCES:\n"
                "- src/order_service/cache.py:10-20 — defines the invalidation handler"
            )
        }
    }
    result = provider._build_retrieval_result(
        request, final_event, steps_used=2, max_steps=20, max_context_tokens=4000
    )
    assert "cache is invalidated" in result.context_summary
    assert len(result.snippets) == 1
    assert result.snippets[0].source_path == "src/order_service/cache.py"


def test_build_retrieval_result_falls_back_when_no_content(tmp_path):
    provider = CopilotRetrievalProvider()
    request = _make_request(tmp_path)
    result = provider._build_retrieval_result(
        request, final_event=None, steps_used=1, max_steps=20, max_context_tokens=4000
    )
    assert result.context_summary == f"No context could be derived for: {request.query}"
    assert result.snippets == []


# -- permission handler: only "read" is ever approved --------------------------


def test_permission_handler_approves_read():
    provider = CopilotRetrievalProvider()
    handler = provider._make_permission_handler()
    request = se.PermissionRequestRead(intention="test", path="/tmp/foo.py")
    decision = asyncio.run(handler(request))
    assert isinstance(decision, rpc.PermissionDecisionApproveOnce)


def test_permission_handler_rejects_shell_even_when_all_commands_are_read_only():
    """Verifies the documented "no exceptions" stance: a shell request
    whose commands self-report `read_only=True` (e.g. `grep`/`find`) is
    still rejected -- see module docstring for why that flag is not
    trusted."""
    provider = CopilotRetrievalProvider()
    handler = provider._make_permission_handler()
    request = se.PermissionRequestShell(
        can_offer_session_approval=False,
        commands=[se.PermissionRequestShellCommand(identifier="grep", read_only=True)],
        full_command_text="grep -r foo .",
        has_write_file_redirection=False,
        intention="test",
        possible_paths=[],
        possible_urls=[],
    )
    decision = asyncio.run(handler(request))
    assert isinstance(decision, rpc.PermissionDecisionReject)


def test_permission_handler_rejects_write():
    provider = CopilotRetrievalProvider()
    handler = provider._make_permission_handler()
    request = se.PermissionRequestWrite(
        can_offer_session_approval=False,
        diff="",
        file_name="/tmp/foo.py",
        intention="test",
    )
    decision = asyncio.run(handler(request))
    assert isinstance(decision, rpc.PermissionDecisionReject)


def test_permission_handler_rejects_mcp():
    provider = CopilotRetrievalProvider()
    handler = provider._make_permission_handler()
    request = se.PermissionRequestMcp(
        read_only=True,
        server_name="some-server",
        tool_name="some-tool",
        tool_title="Some Tool",
    )
    decision = asyncio.run(handler(request))
    assert isinstance(decision, rpc.PermissionDecisionReject)


def test_permission_handler_rejects_unknown_kind():
    """A permission-request kind this provider doesn't recognize at all
    must still be rejected, not accidentally approved by a permissive
    default."""
    provider = CopilotRetrievalProvider()
    handler = provider._make_permission_handler()
    decision = asyncio.run(handler({"kind": "some-future-kind-not-yet-seen"}))
    assert isinstance(decision, rpc.PermissionDecisionReject)


# -- user-input auto-answer fallback ------------------------------------------


def test_user_input_handler_answers_with_first_choice_when_offered():
    provider = CopilotRetrievalProvider()
    handler = provider._make_user_input_handler()
    request = copilot.session.UserInputRequest(
        question="Which file matters more?", choices=["A", "B"], allowFreeform=True
    )
    response = asyncio.run(handler(request))
    assert response["answer"] == "A"
    assert response["wasFreeform"] is False


def test_user_input_handler_falls_back_to_freeform_note_when_no_choices():
    provider = CopilotRetrievalProvider()
    handler = provider._make_user_input_handler()
    request = copilot.session.UserInputRequest(
        question="What should I search next?", choices=[], allowFreeform=True
    )
    response = asyncio.run(handler(request))
    assert response["wasFreeform"] is True
    assert "No human reviewer" in response["answer"]


# -- prompt assembly -----------------------------------------------------------


def test_prompt_includes_query_scope_and_repository_path(tmp_path):
    provider = CopilotRetrievalProvider()
    request = _make_request(tmp_path)
    prompt = provider._build_prompt(request)
    assert request.query in prompt
    assert "src/order_service/cache.py" in prompt
    assert str(tmp_path) in prompt
    assert "SOURCES:" in prompt


# -- SOURCES: section parsing ---------------------------------------------------


def test_extract_sources_section_parses_snippets():
    text = (
        "The cache is invalidated on order update.\n\nSOURCES:\n"
        "- src/order_service/cache.py:10-20 — defines the invalidation handler"
    )
    summary, snippets = _extract_sources_section(text)
    assert "cache is invalidated" in summary
    assert "SOURCES:" not in summary
    assert len(snippets) == 1
    assert snippets[0].source_path == "src/order_service/cache.py"
    assert snippets[0].line_start == 10
    assert snippets[0].line_end == 20


def test_extract_sources_section_falls_back_when_absent():
    text = "Just a plain answer."
    summary, snippets = _extract_sources_section(text)
    assert summary == "Just a plain answer."
    assert snippets == []


# -- termination mapping / truncation -------------------------------------------


def test_map_termination_step_budget_exhausted():
    provider = CopilotRetrievalProvider()
    reason = provider._map_termination({"result": "done"}, steps_used=5, max_steps=5)
    assert reason == TerminationReason.STEP_BUDGET_EXHAUSTED


def test_map_termination_provider_reported_failure():
    provider = CopilotRetrievalProvider()
    reason = provider._map_termination({"is_error": True}, steps_used=1, max_steps=20)
    assert reason == TerminationReason.PROVIDER_REPORTED_FAILURE


def test_map_termination_completed():
    provider = CopilotRetrievalProvider()
    reason = provider._map_termination({}, steps_used=1, max_steps=20)
    assert reason == TerminationReason.COMPLETED


def test_truncate_to_budget():
    provider = CopilotRetrievalProvider()
    long_answer = "word " * 5000
    truncated = provider._truncate_to_budget(long_answer, max_context_tokens=10)
    assert len(truncated) <= 10 * 4
    assert truncated.endswith("…")


# -- steps_used estimation -------------------------------------------------------


def test_estimate_steps_used_counts_events():
    provider = CopilotRetrievalProvider()

    class _FakeSession:
        def get_events(self):
            return [object(), object(), object()]

    steps = asyncio.run(provider._estimate_steps_used(_FakeSession()))
    assert steps == 3


def test_estimate_steps_used_defaults_to_zero_on_error():
    provider = CopilotRetrievalProvider()

    class _BrokenSession:
        def get_events(self):
            raise RuntimeError("boom")

    steps = asyncio.run(provider._estimate_steps_used(_BrokenSession()))
    assert steps == 0


# -- _field helper ----------------------------------------------------------------


def test_field_reads_dict_and_attribute_shapes():
    assert _field({"answer": "A"}, "answer") == "A"

    class _Obj:
        answer = "B"

    assert _field(_Obj(), "answer") == "B"
    assert _field({}, "missing", "default") == "default"
