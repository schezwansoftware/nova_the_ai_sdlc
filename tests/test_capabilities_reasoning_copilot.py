"""Tests for `CopilotReasoningProvider`'s own logic -- the
reject-everything permission handler, the user-input auto-answer
fallback, prompt assembly (schema-in-prompt, JSON-fence instructions),
fenced-JSON-block extraction, and post-session parsing/validation.

Mirrors `tests/test_capabilities_retrieval_copilot.py`/
`tests/test_capabilities_coding_copilot.py`'s approach: none of these
tests make a network call, require Copilot credentials, or start a real
Copilot session (`create_session`/`send_and_wait` are never invoked) --
they exercise the provider's pure-Python plumbing plus, where the real
SDK's request/response classes are needed to prove the wiring matches
their actual shape, the installed `github-copilot-sdk` package's own
classes.

The whole module is skipped if `github-copilot-sdk` (the optional
`copilot` extra) isn't installed -- it requires Python 3.11+, stricter
than this project's own `>=3.10`, so it won't be present in every
environment. This mirrors the other Copilot-provider test modules' own
skip convention.
"""
from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

copilot = pytest.importorskip("copilot")
from copilot.generated import session_events as se  # noqa: E402
from copilot.generated import rpc  # noqa: E402

from ai_sdlc.capabilities.providers.reasoning_copilot import (  # noqa: E402
    CopilotReasoningProvider,
    _extract_json_payload,
    _field,
)
from ai_sdlc.capabilities.reasoning import MalformedResponseError  # noqa: E402


class _DummySchema(BaseModel):
    title: str
    items: list


_PROMPT = "Summarize this into a title and a list of items."


# -- construction ---------------------------------------------------------------


def test_provider_constructs_when_sdk_is_installed():
    provider = CopilotReasoningProvider()
    assert provider.model is None
    assert provider.max_steps == CopilotReasoningProvider().max_steps


def test_max_steps_override():
    provider = CopilotReasoningProvider(max_steps=2)
    assert provider.max_steps == 2


# -- permission handler: everything is rejected, unconditionally --------------


def test_permission_handler_rejects_read():
    provider = CopilotReasoningProvider()
    handler = provider._make_permission_handler()
    request = se.PermissionRequestRead(intention="test", path="/tmp/foo.py")
    decision = asyncio.run(handler(request))
    assert isinstance(decision, rpc.PermissionDecisionReject)


def test_permission_handler_rejects_shell():
    provider = CopilotReasoningProvider()
    handler = provider._make_permission_handler()
    request = se.PermissionRequestShell(
        can_offer_session_approval=False,
        commands=[se.PermissionRequestShellCommand(identifier="echo", read_only=True)],
        full_command_text="echo hi",
        has_write_file_redirection=False,
        intention="test",
        possible_paths=[],
        possible_urls=[],
    )
    decision = asyncio.run(handler(request))
    assert isinstance(decision, rpc.PermissionDecisionReject)


def test_permission_handler_rejects_write():
    provider = CopilotReasoningProvider()
    handler = provider._make_permission_handler()
    request = se.PermissionRequestWrite(
        can_offer_session_approval=False,
        diff="",
        file_name="/tmp/foo.py",
        intention="test",
    )
    decision = asyncio.run(handler(request))
    assert isinstance(decision, rpc.PermissionDecisionReject)


def test_permission_handler_rejects_unknown_kind():
    """A permission-request kind this provider doesn't recognize at all
    must still be rejected -- there is no permissive default, and no kind
    is ever approved (unlike the coding/retrieval providers, which approve
    a narrow allow-list)."""
    provider = CopilotReasoningProvider()
    handler = provider._make_permission_handler()
    decision = asyncio.run(handler({"kind": "some-future-kind-not-yet-seen"}))
    assert isinstance(decision, rpc.PermissionDecisionReject)


# -- user-input auto-answer fallback ------------------------------------------


def test_user_input_handler_answers_with_first_choice_when_offered():
    provider = CopilotReasoningProvider()
    handler = provider._make_user_input_handler()
    request = copilot.session.UserInputRequest(
        question="Which framing do you prefer?", choices=["A", "B"], allowFreeform=True
    )
    response = asyncio.run(handler(request))
    assert response["answer"] == "A"
    assert response["wasFreeform"] is False


def test_user_input_handler_falls_back_to_freeform_note_when_no_choices():
    provider = CopilotReasoningProvider()
    handler = provider._make_user_input_handler()
    request = copilot.session.UserInputRequest(
        question="What should I assume?", choices=[], allowFreeform=True
    )
    response = asyncio.run(handler(request))
    assert response["wasFreeform"] is True
    assert "No human reviewer" in response["answer"]
    assert "no tools are available" in response["answer"]


# -- prompt assembly -----------------------------------------------------------


def test_prompt_includes_original_prompt_and_json_schema_and_fence_instructions():
    provider = CopilotReasoningProvider()
    prompt = provider._build_prompt(_PROMPT, _DummySchema)
    assert _PROMPT in prompt
    assert "```json" in prompt
    assert '"title"' in prompt  # schema field name present via model_json_schema()
    assert "no tools available" in prompt


# -- fenced JSON block extraction ------------------------------------------------


def test_extract_json_payload_from_fenced_block():
    text = 'Here is my answer.\n```json\n{"title": "A title", "items": ["a", "b"]}\n```\n'
    payload = _extract_json_payload(text)
    assert payload == {"title": "A title", "items": ["a", "b"]}


def test_extract_json_payload_from_bare_json_without_fence():
    text = '{"title": "A title", "items": []}'
    payload = _extract_json_payload(text)
    assert payload == {"title": "A title", "items": []}


def test_extract_json_payload_raises_value_error_on_unparseable_text():
    with pytest.raises(ValueError):
        _extract_json_payload("no json anywhere in here")


def test_extract_json_payload_raises_value_error_when_top_level_is_not_an_object():
    with pytest.raises(ValueError):
        _extract_json_payload("[1, 2, 3]")


# -- post-session parsing/validation --------------------------------------------


def test_parse_result_returns_validated_schema_instance():
    provider = CopilotReasoningProvider()
    final_event = {"result": '```json\n{"title": "A title", "items": ["x"]}\n```'}
    result = provider._parse_result(final_event, _DummySchema)
    assert isinstance(result, _DummySchema)
    assert result.title == "A title"
    assert result.items == ["x"]


def test_parse_result_falls_back_to_summary_field():
    provider = CopilotReasoningProvider()
    final_event = {"summary": '{"title": "From summary", "items": []}'}
    result = provider._parse_result(final_event, _DummySchema)
    assert result.title == "From summary"


def test_parse_result_raises_malformed_when_no_text_result():
    provider = CopilotReasoningProvider()
    with pytest.raises(MalformedResponseError):
        provider._parse_result({}, _DummySchema)


def test_parse_result_raises_malformed_when_text_is_not_parseable_json():
    provider = CopilotReasoningProvider()
    with pytest.raises(MalformedResponseError):
        provider._parse_result({"result": "not json at all"}, _DummySchema)


def test_parse_result_raises_malformed_when_payload_fails_schema_validation():
    provider = CopilotReasoningProvider()
    final_event = {"result": '```json\n{"title": "missing items field"}\n```'}
    with pytest.raises(MalformedResponseError):
        provider._parse_result(final_event, _DummySchema)


# -- _field helper ----------------------------------------------------------------


def test_field_reads_dict_and_attribute_shapes():
    assert _field({"answer": "A"}, "answer") == "A"

    class _Obj:
        answer = "B"

    assert _field(_Obj(), "answer") == "B"
    assert _field({}, "missing", "default") == "default"
