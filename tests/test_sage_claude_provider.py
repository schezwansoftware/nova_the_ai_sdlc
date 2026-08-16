"""Tests for `SageClaudeProvider`.

None of these tests require the real `claude-agent-sdk` package or a real
`claude` CLI: the fake `query`/`ClaudeAgentOptions`/`ResultMessage`
objects below are injected via the provider's `_query_fn`/`_options_cls`/
`_result_message_cls` test seams, mirroring
`tests/test_retrieval_claude_provider.py`'s approach exactly.

One test (`test_constructor_raises_when_sdk_unavailable`) intentionally
exercises the *real*, uninjected import path to assert this environment's
actual state, skipped automatically if the package ever does become
available in whatever environment runs this suite.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List

import pytest

from ai_sdlc.capabilities.connector_resolver import ConnectorResolver, default_connectors_config_path
from ai_sdlc.capabilities.providers import sage_claude as sage_claude_module
from ai_sdlc.capabilities.providers.sage_claude import SageClaudeProvider
from ai_sdlc.capabilities.sage import MalformedResponseError, ProviderError, SageRequest, TerminationReason


# -- fakes standing in for the claude-agent-sdk package -----------------------


class _FakeResultMessage:
    def __init__(
        self,
        *,
        is_error: bool = False,
        num_turns: int | None = None,
        session_id: str = "sess-sage-1",
        total_cost_usd: float = 0.01,
        result: str = (
            "It converts CSV rows into normalized Order records.\n\n"
            "FOUND: true\n"
            "SOURCE_CONNECTOR: confluence\n"
            "SOURCE_URL: https://example/confluence/page"
        ),
    ):
        self.is_error = is_error
        self.num_turns = num_turns
        self.session_id = session_id
        self.total_cost_usd = total_cost_usd
        self.result = result


class _FakeAssistantMessage:
    """Stand-in for a non-terminal SDK message the provider must ignore."""


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


def _resolver_with_connectors(tmp_path: Path, connectors: list) -> ConnectorResolver:
    path = default_connectors_config_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": "connectors-v1", "connectors": connectors}), encoding="utf-8")
    return ConnectorResolver(tmp_path)


def _default_resolver(tmp_path: Path) -> ConnectorResolver:
    return _resolver_with_connectors(
        tmp_path,
        [{"name": "confluence", "enabled": True, "command": "/bin/confluence-mcp", "args": [], "env": {}}],
    )


def _provider(tmp_path: Path, **messages_kwargs) -> tuple[SageClaudeProvider, dict]:
    captured: dict = {}

    def _on_call(prompt: str, options: _FakeOptions) -> None:
        captured["prompt"] = prompt
        captured["options"] = options

    messages = messages_kwargs.pop("messages", None)
    resolver = messages_kwargs.pop("connector_resolver", None) or _default_resolver(tmp_path)
    provider = SageClaudeProvider(
        connector_resolver=resolver,
        _query_fn=_make_fake_query(on_call=_on_call, messages=messages),
        _options_cls=_FakeOptions,
        _result_message_cls=_FakeResultMessage,
        **messages_kwargs,
    )
    return provider, captured


# -- constructor ----------------------------------------------------------------


def test_constructor_raises_when_sdk_unavailable(tmp_path):
    if sage_claude_module.SDK_IMPORT_ERROR is None:
        pytest.skip("claude-agent-sdk is installed in this environment; nothing to assert here")
    with pytest.raises(ProviderError):
        SageClaudeProvider(connector_resolver=_default_resolver(tmp_path))


def test_constructor_succeeds_with_injected_fakes(tmp_path):
    provider, _ = _provider(tmp_path)
    assert isinstance(provider, SageClaudeProvider)


# -- happy path -------------------------------------------------------------------


def test_ask_returns_valid_found_result(tmp_path):
    provider, _ = _provider(tmp_path)
    result = provider.ask(SageRequest(query="What does the legacy import step do?", requesting_agent_id="po"))

    assert result.provider_name == "claude_agent_sdk_sage"
    assert result.found is True
    assert result.answer == "It converts CSV rows into normalized Order records."
    assert result.source_connector == "confluence"
    assert result.source_url == "https://example/confluence/page"
    assert result.terminated_reason == TerminationReason.COMPLETED


def test_ask_returns_not_found_when_session_says_so(tmp_path):
    provider, _ = _provider(
        tmp_path,
        messages=[_FakeResultMessage(result="I searched but found nothing.\n\nFOUND: false\nSOURCE_CONNECTOR: none\nSOURCE_URL: none")],
    )
    result = provider.ask(SageRequest(query="q", requesting_agent_id="po"))

    assert result.found is False
    assert result.answer == ""
    assert result.source_connector is None
    assert result.source_url is None


def test_ask_defaults_to_not_found_when_no_found_marker_present(tmp_path):
    """No FOUND: line at all -- no signal to trust, conservative default."""
    provider, _ = _provider(tmp_path, messages=[_FakeResultMessage(result="Just a plain answer with no markers.")])
    result = provider.ask(SageRequest(query="q", requesting_agent_id="po"))

    assert result.found is False


def test_no_connectors_enabled_short_circuits_without_a_session(tmp_path):
    resolver = ConnectorResolver(tmp_path)  # no connectors.json at all
    provider, captured = _provider(tmp_path, connector_resolver=resolver)

    result = provider.ask(SageRequest(query="q", requesting_agent_id="po"))

    assert result.found is False
    assert result.metadata["reason"] == "no_connectors_enabled"
    assert "prompt" not in captured  # session never even started


# -- MCP server / tool wiring ---------------------------------------------------


def test_mcp_servers_built_from_resolved_connector_specs(tmp_path):
    resolver = _resolver_with_connectors(
        tmp_path,
        [
            {"name": "jira", "enabled": True, "command": "/bin/jira-mcp", "args": ["--x"], "env": {"K": "V"}},
            {"name": "confluence", "enabled": True, "command": "/bin/confluence-mcp", "args": [], "env": {}},
        ],
    )
    provider, captured = _provider(tmp_path, connector_resolver=resolver)
    provider.ask(SageRequest(query="q", requesting_agent_id="po"))

    mcp_servers = captured["options"].kwargs["mcp_servers"]
    assert set(mcp_servers.keys()) == {"jira", "confluence"}
    assert mcp_servers["jira"] == {
        "type": "stdio",
        "command": "/bin/jira-mcp",
        "args": ["--x"],
        "env": {"K": "V"},
    }


def test_allowed_tools_use_mcp_double_underscore_naming_convention(tmp_path):
    resolver = _resolver_with_connectors(
        tmp_path, [{"name": "jira", "enabled": True, "command": "/bin/jira-mcp", "args": [], "env": {}}]
    )
    provider, captured = _provider(tmp_path, connector_resolver=resolver)
    provider.ask(SageRequest(query="q", requesting_agent_id="po"))

    allowed = captured["options"].kwargs["allowed_tools"]
    assert set(allowed) == {"mcp__jira__search", "mcp__jira__fetch"}


def test_no_cwd_passed_since_sage_has_no_repository(tmp_path):
    provider, captured = _provider(tmp_path)
    provider.ask(SageRequest(query="q", requesting_agent_id="po"))

    assert "cwd" not in captured["options"].kwargs


def test_permission_mode_is_dont_ask(tmp_path):
    provider, captured = _provider(tmp_path)
    provider.ask(SageRequest(query="q", requesting_agent_id="po"))

    assert captured["options"].kwargs["permission_mode"] == "dontAsk"


def test_max_turns_defaults_and_overrides(tmp_path):
    provider, captured = _provider(tmp_path)
    provider.ask(SageRequest(query="q", requesting_agent_id="po"))
    assert captured["options"].kwargs["max_turns"] == 12  # DEFAULT_MAX_STEPS

    provider, captured = _provider(tmp_path)
    provider.ask(SageRequest(query="q", requesting_agent_id="po", max_steps=3))
    assert captured["options"].kwargs["max_turns"] == 3


def test_skipped_connectors_surface_in_metadata(tmp_path):
    resolver = _resolver_with_connectors(
        tmp_path,
        [
            {"name": "jira", "enabled": True, "command": "/bin/jira-mcp", "args": [], "env": {}},
            {"name": "onedrive", "enabled": True, "command": None, "args": [], "env": {}},
        ],
    )
    provider, _ = _provider(tmp_path, connector_resolver=resolver)
    result = provider.ask(SageRequest(query="q", requesting_agent_id="po"))

    assert result.metadata["skipped"] == [{"name": "onedrive", "reason": "not_configured"}]


# -- termination mapping -----------------------------------------------------------


def test_step_budget_exhausted(tmp_path):
    provider, _ = _provider(tmp_path, messages=[_FakeResultMessage(num_turns=12)])
    result = provider.ask(SageRequest(query="q", requesting_agent_id="po", max_steps=12))
    assert result.terminated_reason == TerminationReason.STEP_BUDGET_EXHAUSTED


def test_provider_reported_failure(tmp_path):
    provider, _ = _provider(tmp_path, messages=[_FakeResultMessage(is_error=True, num_turns=1)])
    result = provider.ask(SageRequest(query="q", requesting_agent_id="po"))
    assert result.terminated_reason == TerminationReason.PROVIDER_REPORTED_FAILURE


def test_provider_error_when_no_result_message_ever_arrives(tmp_path):
    provider = SageClaudeProvider(
        connector_resolver=_default_resolver(tmp_path),
        _query_fn=_make_fake_query(messages=[_FakeAssistantMessage()]),
        _options_cls=_FakeOptions,
        _result_message_cls=_FakeResultMessage,
    )
    with pytest.raises(ProviderError):
        provider.ask(SageRequest(query="q", requesting_agent_id="po"))


# -- async safety ---------------------------------------------------------------------


def test_ask_is_safe_to_call_from_within_a_running_event_loop(tmp_path):
    import asyncio

    provider, _ = _provider(tmp_path)

    async def _caller():
        return provider.ask(SageRequest(query="q", requesting_agent_id="po"))

    result = asyncio.run(_caller())
    assert result.provider_name == "claude_agent_sdk_sage"
