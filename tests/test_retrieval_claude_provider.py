"""Tests for `ClaudeAgentSDKRetrievalProvider`.

None of these tests require the real `claude-agent-sdk` package or a real
`claude` CLI: the fake `query`/`ClaudeAgentOptions`/`ResultMessage`
objects below are injected via the provider's `_query_fn`/`_options_cls`/
`_result_message_cls` test seams (see `providers/retrieval_claude.py`'s
class docstring), mirroring `tests/test_coding_claude_sdk_provider.py`'s
approach exactly.

One test (`test_constructor_raises_when_sdk_unavailable`) intentionally
exercises the *real*, uninjected import path, to assert this
environment's actual state: `claude-agent-sdk` is not installed here, so
the constructor must fail fast rather than fail later inside `retrieve()`.
That test is skipped automatically if the package ever does become
available in whatever environment runs this suite.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, List

import pytest

from ai_sdlc.capabilities.providers import retrieval_claude as retrieval_claude_module
from ai_sdlc.capabilities.providers.retrieval_claude import ClaudeAgentSDKRetrievalProvider
from ai_sdlc.capabilities.retrieval import ProviderError, RetrievalRequest, TerminationReason


# -- fakes standing in for the claude-agent-sdk package -----------------------


class _FakeResultMessage:
    def __init__(
        self,
        *,
        is_error: bool = False,
        num_turns: int | None = None,
        session_id: str = "sess-retrieval-1",
        total_cost_usd: float = 0.01,
        result: str = (
            "The cache is invalidated on order update.\n\nSOURCES:\n"
            "- src/order_service/cache.py:10-20 — defines the invalidation handler"
        ),
    ):
        self.is_error = is_error
        self.num_turns = num_turns
        self.session_id = session_id
        self.total_cost_usd = total_cost_usd
        self.result = result


class _FakeAssistantMessage:
    """Stand-in for a non-terminal SDK message the provider must ignore
    when scanning for the terminal `ResultMessage`."""


class _FakeOptions:
    def __init__(self, **kwargs: Any):
        self.kwargs = kwargs


def _make_fake_query(*, on_call=None, messages: List[Any] | None = None):
    messages = messages if messages is not None else [_FakeResultMessage()]

    async def _fake_query(*, prompt: str, options: _FakeOptions):
        if on_call is not None:
            on_call(prompt, options)
        for message in messages:
            yield message

    return _fake_query


def _provider(**messages_kwargs) -> tuple[ClaudeAgentSDKRetrievalProvider, dict]:
    captured: dict = {}

    def _on_call(prompt: str, options: _FakeOptions) -> None:
        captured["prompt"] = prompt
        captured["options"] = options

    messages = messages_kwargs.pop("messages", None)
    provider = ClaudeAgentSDKRetrievalProvider(
        _query_fn=_make_fake_query(on_call=_on_call, messages=messages),
        _options_cls=_FakeOptions,
        _result_message_cls=_FakeResultMessage,
        **messages_kwargs,
    )
    return provider, captured


def _base_request(repository_path: Path, **overrides: Any) -> RetrievalRequest:
    fields = dict(
        query="How does the order cache get invalidated?",
        repository_path=str(repository_path),
        scope_paths=["src/order_service/cache.py"],
    )
    fields.update(overrides)
    return RetrievalRequest(**fields)


# -- constructor ----------------------------------------------------------------


def test_constructor_raises_when_sdk_unavailable():
    if retrieval_claude_module.SDK_IMPORT_ERROR is None:
        pytest.skip("claude-agent-sdk is installed in this environment; nothing to assert here")
    with pytest.raises(ProviderError):
        ClaudeAgentSDKRetrievalProvider()


def test_constructor_succeeds_with_injected_fakes():
    provider, _ = _provider()
    assert isinstance(provider, ClaudeAgentSDKRetrievalProvider)


# -- happy path -------------------------------------------------------------------


def test_retrieve_returns_valid_result_with_parsed_sources(tmp_path: Path):
    provider, _ = _provider()
    result = provider.retrieve(_base_request(tmp_path))

    assert result.provider_name == "claude_agent_sdk_retrieval"
    assert result.terminated_reason == TerminationReason.COMPLETED
    assert "cache is invalidated" in result.context_summary
    assert "SOURCES:" not in result.context_summary
    assert len(result.snippets) == 1
    assert result.snippets[0].source_path == "src/order_service/cache.py"
    assert result.snippets[0].line_start == 10
    assert result.snippets[0].line_end == 20


def test_retrieve_falls_back_gracefully_when_no_sources_section(tmp_path: Path):
    provider, _ = _provider(messages=[_FakeResultMessage(result="Just a plain answer.")])
    result = provider.retrieve(_base_request(tmp_path))

    assert result.context_summary == "Just a plain answer."
    assert result.snippets == []


def test_prompt_includes_query_and_scope_and_repository_path(tmp_path: Path):
    provider, captured = _provider()
    request = _base_request(tmp_path)

    provider.retrieve(request)

    prompt = captured["prompt"]
    assert request.query in prompt
    assert "src/order_service/cache.py" in prompt
    assert str(tmp_path) in prompt


# -- read-only permission enforcement -----------------------------------------------


def test_allowed_tools_is_read_only_and_fixed(tmp_path: Path):
    provider, captured = _provider()
    provider.retrieve(_base_request(tmp_path))

    allowed = captured["options"].kwargs["allowed_tools"]
    assert set(allowed) == {"Read", "Grep", "Glob"}


def test_write_and_execute_tools_are_hard_disallowed(tmp_path: Path):
    provider, captured = _provider()
    provider.retrieve(_base_request(tmp_path))

    disallowed = captured["options"].kwargs["disallowed_tools"]
    for tool in ("Edit", "Write", "NotebookEdit", "Bash"):
        assert tool in disallowed


def test_request_has_no_allowed_tools_field():
    """RetrievalRequest deliberately has no allowed_tools/allowed_commands
    field -- unlike CodingRequest, retrieval's permission surface is fixed
    by the capability, not caller policy (see retrieval.py's docstring)."""
    assert "allowed_tools" not in RetrievalRequest.model_fields
    assert "allowed_commands" not in RetrievalRequest.model_fields


def test_permission_mode_is_dont_ask(tmp_path: Path):
    provider, captured = _provider()
    provider.retrieve(_base_request(tmp_path))
    assert captured["options"].kwargs["permission_mode"] == "dontAsk"


def test_max_turns_defaults_and_overrides(tmp_path: Path):
    provider, captured = _provider()
    provider.retrieve(_base_request(tmp_path))
    assert captured["options"].kwargs["max_turns"] == 20  # DEFAULT_MAX_STEPS

    provider, captured = _provider()
    provider.retrieve(_base_request(tmp_path, max_steps=3))
    assert captured["options"].kwargs["max_turns"] == 3


# -- termination mapping -----------------------------------------------------------


def test_step_budget_exhausted(tmp_path: Path):
    provider, _ = _provider(messages=[_FakeResultMessage(num_turns=3)])
    result = provider.retrieve(_base_request(tmp_path, max_steps=3))
    assert result.terminated_reason == TerminationReason.STEP_BUDGET_EXHAUSTED


def test_provider_reported_failure(tmp_path: Path):
    provider, _ = _provider(messages=[_FakeResultMessage(is_error=True, num_turns=1)])
    result = provider.retrieve(_base_request(tmp_path, max_steps=20))
    assert result.terminated_reason == TerminationReason.PROVIDER_REPORTED_FAILURE


def test_provider_error_when_no_result_message_ever_arrives(tmp_path: Path):
    provider = ClaudeAgentSDKRetrievalProvider(
        _query_fn=_make_fake_query(messages=[_FakeAssistantMessage()]),
        _options_cls=_FakeOptions,
        _result_message_cls=_FakeResultMessage,
    )
    with pytest.raises(ProviderError):
        provider.retrieve(_base_request(tmp_path))


# -- token budget truncation ---------------------------------------------------------


def test_context_summary_is_truncated_to_budget(tmp_path: Path):
    long_answer = "word " * 5000  # comfortably over any small token budget
    provider, _ = _provider(messages=[_FakeResultMessage(result=long_answer)])
    result = provider.retrieve(_base_request(tmp_path, max_context_tokens=10))

    assert len(result.context_summary) <= 10 * 4
    assert result.context_summary.endswith("…")


# -- repository path validation ---------------------------------------------------


def test_retrieve_rejects_nonexistent_repository_path(tmp_path: Path):
    provider, _ = _provider()
    request = _base_request(tmp_path / "does-not-exist")

    with pytest.raises(ProviderError):
        provider.retrieve(request)


def test_retrieve_accepts_directory_without_git(tmp_path: Path):
    """Unlike CodingCapability's working tree, repository_path doesn't
    need to be a Git repo at all -- retrieval is generic exploration."""
    plain_dir = tmp_path / "not-a-repo"
    plain_dir.mkdir()
    provider, _ = _provider()

    result = provider.retrieve(_base_request(plain_dir))
    assert result.provider_name == "claude_agent_sdk_retrieval"


# -- async safety ---------------------------------------------------------------------


def test_retrieve_is_safe_to_call_from_within_a_running_event_loop(tmp_path: Path):
    import asyncio

    provider, _ = _provider()
    request = _base_request(tmp_path)

    async def _caller():
        return provider.retrieve(request)

    result = asyncio.run(_caller())
    assert result.provider_name == "claude_agent_sdk_retrieval"
