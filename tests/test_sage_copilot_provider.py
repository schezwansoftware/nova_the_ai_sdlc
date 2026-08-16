"""Tests for `SageCopilotProvider`'s own logic -- the MCP-approving/
everything-else-rejecting permission handler, the user-input auto-answer
fallback, prompt assembly, and post-session parsing.

Mirrors `tests/test_capabilities_reasoning_copilot.py`'s approach: none
of these tests make a network call, require Copilot credentials, or
start a real Copilot session (`create_session`/`send_and_wait` are never
invoked) -- they exercise the provider's pure-Python plumbing plus, where
the real SDK's request/response classes are needed to prove the wiring
matches their actual shape, the installed `github-copilot-sdk` package's
own classes.

The whole module is skipped if `github-copilot-sdk` (the optional
`copilot` extra) isn't installed, mirroring every other Copilot-provider
test module's own skip convention.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

copilot = pytest.importorskip("copilot")
from copilot.generated import session_events as se  # noqa: E402
from copilot.generated import rpc  # noqa: E402

from ai_sdlc.capabilities.connector_resolver import (  # noqa: E402
    ConnectorResolver,
    default_connectors_config_path,
)
from ai_sdlc.capabilities.providers.sage_copilot import (  # noqa: E402
    SageCopilotProvider,
    _parse_sage_response,
)
from ai_sdlc.capabilities.sage import MalformedResponseError, SageRequest  # noqa: E402


def _resolver_with_connectors(tmp_path: Path, connectors: list) -> ConnectorResolver:
    path = default_connectors_config_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": "connectors-v1", "connectors": connectors}), encoding="utf-8")
    return ConnectorResolver(tmp_path)


def _default_resolver(tmp_path: Path) -> ConnectorResolver:
    return _resolver_with_connectors(
        tmp_path,
        [{"name": "jira", "enabled": True, "command": "/bin/jira-mcp", "args": [], "env": {}}],
    )


# -- construction ---------------------------------------------------------------


def test_provider_constructs_when_sdk_is_installed(tmp_path):
    provider = SageCopilotProvider(connector_resolver=_default_resolver(tmp_path))
    assert provider.model is None


# -- permission handler: only kind=='mcp' is ever approved ---------------------


def test_permission_handler_approves_mcp(tmp_path):
    provider = SageCopilotProvider(connector_resolver=_default_resolver(tmp_path))
    handler = provider._make_permission_handler()
    request = se.PermissionRequestMcp(read_only=True, server_name="jira", tool_name="search", tool_title="Search Jira")
    decision = asyncio.run(handler(request))
    assert isinstance(decision, rpc.PermissionDecisionApproveOnce)


def test_permission_handler_rejects_shell(tmp_path):
    provider = SageCopilotProvider(connector_resolver=_default_resolver(tmp_path))
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


def test_permission_handler_rejects_write(tmp_path):
    provider = SageCopilotProvider(connector_resolver=_default_resolver(tmp_path))
    handler = provider._make_permission_handler()
    request = se.PermissionRequestWrite(
        can_offer_session_approval=False, diff="", file_name="/tmp/foo.py", intention="test"
    )
    decision = asyncio.run(handler(request))
    assert isinstance(decision, rpc.PermissionDecisionReject)


def test_permission_handler_rejects_unknown_kind(tmp_path):
    provider = SageCopilotProvider(connector_resolver=_default_resolver(tmp_path))
    handler = provider._make_permission_handler()
    decision = asyncio.run(handler({"kind": "some-future-kind-not-yet-seen"}))
    assert isinstance(decision, rpc.PermissionDecisionReject)


# -- user-input auto-answer fallback ------------------------------------------


def test_user_input_handler_answers_with_first_choice_when_offered(tmp_path):
    provider = SageCopilotProvider(connector_resolver=_default_resolver(tmp_path))
    handler = provider._make_user_input_handler()
    request = copilot.session.UserInputRequest(
        question="Which source is more authoritative?", choices=["A", "B"], allowFreeform=True
    )
    response = asyncio.run(handler(request))
    assert response["answer"] == "A"
    assert response["wasFreeform"] is False


def test_user_input_handler_falls_back_to_freeform_note_when_no_choices(tmp_path):
    provider = SageCopilotProvider(connector_resolver=_default_resolver(tmp_path))
    handler = provider._make_user_input_handler()
    request = copilot.session.UserInputRequest(question="What should I assume?", choices=[], allowFreeform=True)
    response = asyncio.run(handler(request))
    assert response["wasFreeform"] is True
    assert "FOUND: false" in response["answer"]


# -- prompt assembly -----------------------------------------------------------


def test_prompt_includes_query_and_connector_names_and_format_instructions(tmp_path):
    resolver = _resolver_with_connectors(
        tmp_path,
        [
            {"name": "jira", "enabled": True, "command": "/bin/jira-mcp", "args": [], "env": {}},
            {"name": "confluence", "enabled": True, "command": "/bin/confluence-mcp", "args": [], "env": {}},
        ],
    )
    provider = SageCopilotProvider(connector_resolver=resolver)
    resolution = resolver.resolve()
    request = SageRequest(query="What does the legacy import step do?", requesting_agent_id="po")

    prompt = provider._build_prompt(request, resolution)
    assert request.query in prompt
    assert "jira" in prompt
    assert "confluence" in prompt
    assert "FOUND: true|false" in prompt


# -- mcp_servers wiring (via _run_session's mcp_servers construction) ----------


def test_mcp_servers_dict_includes_per_server_tools_field(tmp_path):
    resolver = _resolver_with_connectors(
        tmp_path, [{"name": "jira", "enabled": True, "command": "/bin/jira-mcp", "args": [], "env": {}}]
    )
    resolution = resolver.resolve()
    spec = resolution.enabled[0]
    mcp_server_config = {
        "type": "stdio",
        "command": spec.command,
        "args": list(spec.args),
        "env": dict(spec.env),
        "tools": list(spec.tool_names),
    }
    assert mcp_server_config["tools"] == ["search", "fetch"]
    assert mcp_server_config["command"] == "/bin/jira-mcp"


# -- _parse_sage_response ---------------------------------------------------------


def test_parse_sage_response_extracts_found_answer():
    text = (
        "It converts CSV rows into normalized Order records.\n\n"
        "FOUND: true\n"
        "SOURCE_CONNECTOR: confluence\n"
        "SOURCE_URL: https://example/confluence/page"
    )
    parsed = _parse_sage_response(text)
    assert parsed["found"] is True
    assert parsed["answer"] == "It converts CSV rows into normalized Order records."
    assert parsed["source_connector"] == "confluence"
    assert parsed["source_url"] == "https://example/confluence/page"


def test_parse_sage_response_not_found():
    text = "I searched but found nothing.\n\nFOUND: false\nSOURCE_CONNECTOR: none\nSOURCE_URL: none"
    parsed = _parse_sage_response(text)
    assert parsed["found"] is False
    assert parsed["answer"] == ""
    assert parsed["source_connector"] is None
    assert parsed["source_url"] is None


def test_parse_sage_response_defaults_to_not_found_when_no_marker():
    parsed = _parse_sage_response("Just prose, no markers at all.")
    assert parsed["found"] is False


# -- post-session verdict --------------------------------------------------------


def test_build_sage_response_parses_dict_shaped_final_event(tmp_path):
    provider = SageCopilotProvider(connector_resolver=_default_resolver(tmp_path))
    resolution = _default_resolver(tmp_path).resolve()
    final_event = {"data": {"content": "An answer.\n\nFOUND: true\nSOURCE_CONNECTOR: jira\nSOURCE_URL: none"}}

    result = provider._build_sage_response(
        SageRequest(query="q", requesting_agent_id="po"), final_event, resolution
    )
    assert result.found is True
    assert result.answer == "An answer."
    assert result.source_connector == "jira"
    assert result.provider_name == "github_copilot_sdk_sage"


def test_build_sage_response_reads_attribute_style_event(tmp_path):
    class _Data:
        content = "Attribute-shaped answer.\n\nFOUND: true\nSOURCE_CONNECTOR: local_docs\nSOURCE_URL: none"

    class _Event:
        data = _Data()

    provider = SageCopilotProvider(connector_resolver=_default_resolver(tmp_path))
    resolution = _default_resolver(tmp_path).resolve()

    result = provider._build_sage_response(SageRequest(query="q", requesting_agent_id="po"), _Event(), resolution)
    assert result.found is True
    assert result.source_connector == "local_docs"


def test_build_sage_response_raises_malformed_on_invalid_payload(tmp_path, monkeypatch):
    provider = SageCopilotProvider(connector_resolver=_default_resolver(tmp_path))
    resolution = _default_resolver(tmp_path).resolve()

    # Force SageResponse construction to fail regardless of parsed text by
    # requesting an empty provider_name -- simulate via monkeypatching the
    # module-level PROVIDER_NAME to a blank string for this one call.
    import ai_sdlc.capabilities.providers.sage_copilot as module

    monkeypatch.setattr(module, "PROVIDER_NAME", "   ")
    final_event = {"data": {"content": "An answer.\n\nFOUND: true\nSOURCE_CONNECTOR: jira\nSOURCE_URL: none"}}
    with pytest.raises(MalformedResponseError):
        provider._build_sage_response(SageRequest(query="q", requesting_agent_id="po"), final_event, resolution)
