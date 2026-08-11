"""Tests for `AnthropicReasoningProvider`.

None of these tests require the real `anthropic` package or a live API
key: the fake client injected via the provider's `client=...` test seam
(see `providers/reasoning_anthropic.py`'s class docstring) stands in for
`anthropic.Anthropic`, mirroring exactly how
`tests/test_coding_claude_sdk_provider.py` exercises `ClaudeAgentSDKProvider`
without the real `claude-agent-sdk` package. This file only exercises the
provider's own logic -- tool-schema assembly, forced tool_choice, response
parsing, and error mapping -- against a scripted fake `messages.create`.

One test (`test_constructor_raises_when_sdk_unavailable_and_no_client`)
intentionally exercises the real, uninjected import path to assert this
environment's actual state: `anthropic` is not installed here, so the
constructor must fail fast with a clear `ProviderError` when no client is
injected (and a key *is* configured, so the SDK-availability check -- not
the key check -- is what's actually being exercised). That test is skipped
automatically if the package ever does become available in whatever
environment runs this suite.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest
from pydantic import BaseModel

from ai_sdlc.capabilities.providers import reasoning_anthropic as reasoning_anthropic_module
from ai_sdlc.capabilities.providers.reasoning_anthropic import (
    API_KEY_ENV_VAR,
    DEFAULT_MODEL,
    MODEL_ENV_VAR,
    AnthropicReasoningProvider,
)
from ai_sdlc.capabilities.reasoning import MalformedResponseError, ProviderError


class _DummySchema(BaseModel):
    title: str
    items: List[str]


_PROMPT = "Do the thing."


# -- fakes standing in for the anthropic package -----------------------------


class _FakeToolUseBlock:
    def __init__(self, *, name: str, input: Any, type: str = "tool_use"):  # noqa: A002
        self.type = type
        self.name = name
        self.input = input


class _FakeTextBlock:
    """Stand-in for a non-tool-use content block the provider must ignore."""

    def __init__(self, text: str = "some text"):
        self.type = "text"
        self.text = text


class _FakeMessage:
    def __init__(self, content: List[Any]):
        self.content = content


class _FakeMessagesResource:
    def __init__(
        self,
        *,
        response: Optional[Any] = None,
        exception: Optional[BaseException] = None,
        on_call=None,
    ):
        self._response = response
        self._exception = exception
        self._on_call = on_call

    def create(self, **kwargs: Any) -> Any:
        if self._on_call is not None:
            self._on_call(kwargs)
        if self._exception is not None:
            raise self._exception
        return self._response


class _FakeAnthropicClient:
    def __init__(self, messages: _FakeMessagesResource):
        self.messages = messages


def _valid_tool_response(payload: Optional[Dict[str, Any]] = None) -> _FakeMessage:
    payload = payload if payload is not None else {"title": "A title", "items": ["one", "two"]}
    return _FakeMessage(
        content=[
            _FakeTextBlock(),
            _FakeToolUseBlock(name="emit_structured_output", input=payload),
        ]
    )


def _provider_with(
    *, response=None, exception=None, on_call=None
) -> AnthropicReasoningProvider:
    messages = _FakeMessagesResource(response=response, exception=exception, on_call=on_call)
    client = _FakeAnthropicClient(messages)
    return AnthropicReasoningProvider(client=client)


# -- constructor --------------------------------------------------------------


def test_constructor_succeeds_with_injected_client_and_no_api_key(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
    provider = _provider_with(response=_valid_tool_response())
    assert isinstance(provider, AnthropicReasoningProvider)


def test_constructor_raises_provider_error_when_no_key_and_no_client(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
    with pytest.raises(ProviderError):
        AnthropicReasoningProvider()


def test_constructor_raises_when_sdk_unavailable_and_no_client(monkeypatch):
    if reasoning_anthropic_module.SDK_IMPORT_ERROR is None:
        pytest.skip("anthropic is installed in this environment; nothing to assert here")
    monkeypatch.setenv(API_KEY_ENV_VAR, "sk-ant-fake-key-for-test")
    with pytest.raises(ProviderError):
        AnthropicReasoningProvider()


def test_constructor_uses_env_api_key_when_not_passed_explicitly(monkeypatch):
    # Still hits the "SDK not installed" branch in this environment (package
    # absent), but proves the key was read from the env var, not silently
    # required as a constructor kwarg -- the key check passes and the
    # failure that *does* occur is specifically the SDK-availability one.
    if reasoning_anthropic_module.SDK_IMPORT_ERROR is None:
        pytest.skip("anthropic is installed in this environment; nothing to assert here")
    monkeypatch.setenv(API_KEY_ENV_VAR, "sk-ant-fake-key-for-test")
    with pytest.raises(ProviderError, match="anthropic"):
        AnthropicReasoningProvider()


def test_default_model_and_override(monkeypatch):
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
    provider = _provider_with(response=_valid_tool_response())
    assert provider.model == DEFAULT_MODEL

    provider = AnthropicReasoningProvider(
        client=_FakeAnthropicClient(_FakeMessagesResource(response=_valid_tool_response())),
        model="claude-opus-5",
    )
    assert provider.model == "claude-opus-5"


def test_model_env_var_override(monkeypatch):
    monkeypatch.setenv(MODEL_ENV_VAR, "claude-haiku-4-5")
    provider = _provider_with(response=_valid_tool_response())
    assert provider.model == "claude-haiku-4-5"


# -- happy path -----------------------------------------------------------------


def test_complete_returns_valid_structured_output():
    provider = _provider_with(response=_valid_tool_response())
    result = provider.complete(_PROMPT, output_schema=_DummySchema)
    assert isinstance(result, _DummySchema)
    assert result.title == "A title"
    assert result.items == ["one", "two"]


def test_complete_forces_tool_choice_and_sends_schema():
    captured: Dict[str, Any] = {}

    def _on_call(kwargs: Dict[str, Any]) -> None:
        captured.update(kwargs)

    provider = _provider_with(response=_valid_tool_response(), on_call=_on_call)
    provider.complete(_PROMPT, output_schema=_DummySchema)

    assert captured["tool_choice"] == {"type": "tool", "name": "emit_structured_output"}
    assert captured["tools"][0]["name"] == "emit_structured_output"
    assert captured["tools"][0]["input_schema"] == _DummySchema.model_json_schema()
    assert captured["messages"] == [{"role": "user", "content": _PROMPT}]
    assert captured["model"] == provider.model
    assert captured["max_tokens"] == provider.max_tokens


# -- error mapping ----------------------------------------------------------------


def test_complete_raises_provider_error_on_sdk_exception():
    provider = _provider_with(exception=RuntimeError("simulated network failure"))
    with pytest.raises(ProviderError):
        provider.complete(_PROMPT, output_schema=_DummySchema)


def test_complete_raises_malformed_when_no_tool_use_block_present():
    provider = _provider_with(response=_FakeMessage(content=[_FakeTextBlock()]))
    with pytest.raises(MalformedResponseError):
        provider.complete(_PROMPT, output_schema=_DummySchema)


def test_complete_raises_malformed_when_tool_name_does_not_match():
    provider = _provider_with(
        response=_FakeMessage(
            content=[_FakeToolUseBlock(name="some_other_tool", input={"title": "x", "items": []})]
        )
    )
    with pytest.raises(MalformedResponseError):
        provider.complete(_PROMPT, output_schema=_DummySchema)


def test_complete_raises_malformed_when_tool_input_is_not_a_dict():
    provider = _provider_with(
        response=_FakeMessage(
            content=[_FakeToolUseBlock(name="emit_structured_output", input="not-a-dict")]
        )
    )
    with pytest.raises(MalformedResponseError):
        provider.complete(_PROMPT, output_schema=_DummySchema)


def test_complete_raises_malformed_when_tool_input_fails_schema_validation():
    provider = _provider_with(response=_valid_tool_response(payload={"title": "only title"}))
    with pytest.raises(MalformedResponseError):
        provider.complete(_PROMPT, output_schema=_DummySchema)
