"""Tests for each connector's `mcp_server.py`: tool registration
(`search`/`fetch`), the happy path through `FastMCP.call_tool`, and --
the one test that matters most for the "self-contained MCP tool-error
contract" claim in `mcp_connectors/common.py`'s module docstring -- a
real low-level MCP protocol round-trip proving an exception raised
inside a tool function comes back as a proper `CallToolResult(isError=
True, ...)`, not a crashed process or an unhandled exception. That
proof is run once, against the real installed `mcp==1.29.0` low-level
server (`test_allowlist_violation_becomes_a_real_mcp_tool_error`) --
the mechanism is identical across all three connectors (none of them
add their own error-translation code; they all rely on the same
FastMCP behavior), so it isn't re-proven three times, just exercised
once at the protocol level and then via the higher-level `call_tool`
API for each connector's own `search`/`fetch` wiring.

Every test here injects a fake connector client (`FakeJiraClient`, etc.)
-- these tests are about the MCP wiring (`build_server`), not about
request-construction logic (covered by each connector's own
`test_mcp_connectors_<name>.py`).
"""
from __future__ import annotations

import asyncio

import pytest

from mcp_connectors.common import ConnectorConfigError, Document

pytest.importorskip("mcp")


# -- Jira -----------------------------------------------------------------------


class _FakeJiraClient:
    def __init__(self):
        self.search_calls = []
        self.fetch_calls = []

    def search(self, query, projects=None):
        self.search_calls.append((query, projects))
        if projects and "SECRET" in projects:
            raise ConnectorConfigError("project(s) ['SECRET'] are not in this connector's configured allowlist")
        return [Document(id="ENG-1", title="Fix the bug", source="jira", container="ENG")]

    def fetch(self, id):
        self.fetch_calls.append(id)
        return Document(id=id, title="Fetched issue", source="jira", container="ENG")


def _jira_config(monkeypatch):
    from mcp_connectors.atlassian.auth import AtlassianSiteConfig
    from mcp_connectors.common import CredentialRef
    from mcp_connectors.jira.config import JiraConnectorConfig

    monkeypatch.setattr(CredentialRef, "resolve", lambda self: "tok")
    site = AtlassianSiteConfig(
        base_url="https://example.atlassian.net",
        deployment_type="cloud",
        auth_method="cloud_api_token",
        account_identifier="bot@example.com",
        credential=CredentialRef(service="svc", username="user"),
    )
    return JiraConnectorConfig(site=site, allowed_projects=["ENG"], result_limit=10)


def test_jira_server_registers_search_and_fetch_tools(monkeypatch):
    from mcp_connectors.jira.mcp_server import build_server

    server = build_server(_jira_config(monkeypatch), client=_FakeJiraClient())
    tools = asyncio.run(server.list_tools())
    assert {t.name for t in tools} == {"search", "fetch"}


def test_jira_server_search_happy_path(monkeypatch):
    from mcp_connectors.jira.mcp_server import build_server

    fake = _FakeJiraClient()
    server = build_server(_jira_config(monkeypatch), client=fake)
    content, structured = asyncio.run(server.call_tool("search", {"query": "bug"}))
    assert structured["result"][0]["id"] == "ENG-1"
    assert fake.search_calls == [("bug", None)]


def test_jira_server_fetch_happy_path(monkeypatch):
    from mcp_connectors.jira.mcp_server import build_server

    fake = _FakeJiraClient()
    server = build_server(_jira_config(monkeypatch), client=fake)
    content, structured = asyncio.run(server.call_tool("fetch", {"id": "ENG-1"}))
    assert structured["id"] == "ENG-1"
    assert fake.fetch_calls == ["ENG-1"]


def test_jira_server_search_allowlist_violation_raises_tool_error(monkeypatch):
    from mcp.server.fastmcp.exceptions import ToolError

    from mcp_connectors.jira.mcp_server import build_server

    server = build_server(_jira_config(monkeypatch), client=_FakeJiraClient())
    with pytest.raises(ToolError) as excinfo:
        asyncio.run(server.call_tool("search", {"query": "bug", "projects": ["SECRET"]}))
    assert "SECRET" in str(excinfo.value)


def test_allowlist_violation_becomes_a_real_mcp_tool_error(monkeypatch):
    """The load-bearing proof: a real low-level MCP protocol round-trip
    (the same code path an actual MCP client talking stdio to
    `jira-mcp` would hit) converts the raised exception into
    `CallToolResult(isError=True, ...)`, carrying the exception's
    message -- never a crash, never an unhandled exception escaping to
    the transport layer. See this module's docstring for why this is
    checked once, not once per connector."""
    from mcp.types import CallToolRequest, CallToolRequestParams

    from mcp_connectors.jira.mcp_server import build_server

    server = build_server(_jira_config(monkeypatch), client=_FakeJiraClient())
    handler = server._mcp_server.request_handlers[CallToolRequest]
    request = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(name="search", arguments={"query": "bug", "projects": ["SECRET"]}),
    )

    server_result = asyncio.run(handler(request))
    result = server_result.root

    assert result.isError is True
    assert "SECRET" in result.content[0].text


def test_jira_server_main_exits_cleanly_when_config_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MCP_CONNECTORS_CONFIG_DIR", str(tmp_path))
    from mcp_connectors.jira.mcp_server import main

    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "jira-mcp" in captured.err


# -- Confluence -------------------------------------------------------------------


class _FakeConfluenceClient:
    def search(self, query, spaces=None):
        return [Document(id="999", title="Runbook", source="confluence", container="ENG")]

    def fetch(self, id):
        return Document(id=id, title="Runbook", source="confluence", container="ENG")


def _confluence_config(monkeypatch):
    from mcp_connectors.atlassian.auth import AtlassianSiteConfig
    from mcp_connectors.common import CredentialRef
    from mcp_connectors.confluence.config import ConfluenceConnectorConfig

    monkeypatch.setattr(CredentialRef, "resolve", lambda self: "tok")
    site = AtlassianSiteConfig(
        base_url="https://example.atlassian.net/wiki",
        deployment_type="cloud",
        auth_method="cloud_api_token",
        account_identifier="bot@example.com",
        credential=CredentialRef(service="svc", username="user"),
    )
    return ConfluenceConnectorConfig(site=site, allowed_spaces=["ENG"], result_limit=10)


def test_confluence_server_registers_search_and_fetch_tools(monkeypatch):
    from mcp_connectors.confluence.mcp_server import build_server

    server = build_server(_confluence_config(monkeypatch), client=_FakeConfluenceClient())
    tools = asyncio.run(server.list_tools())
    assert {t.name for t in tools} == {"search", "fetch"}


def test_confluence_server_search_happy_path(monkeypatch):
    from mcp_connectors.confluence.mcp_server import build_server

    server = build_server(_confluence_config(monkeypatch), client=_FakeConfluenceClient())
    content, structured = asyncio.run(server.call_tool("search", {"query": "deploy"}))
    assert structured["result"][0]["id"] == "999"


def test_confluence_server_main_exits_cleanly_when_config_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MCP_CONNECTORS_CONFIG_DIR", str(tmp_path))
    from mcp_connectors.confluence.mcp_server import main

    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "confluence-mcp" in captured.err


# -- SharePoint ----------------------------------------------------------------


class _FakeSharePointClient:
    def search(self, query, sites=None):
        return [Document(id="https://contoso.sharepoint.com/sites/A::drive1:item1", title="Q3.docx", source="sharepoint", container="https://contoso.sharepoint.com/sites/A")]

    def fetch(self, id):
        return Document(id=id, title="Q3.docx", source="sharepoint", container="https://contoso.sharepoint.com/sites/A")


def _sharepoint_config(monkeypatch):
    from mcp_connectors.common import CredentialRef
    from mcp_connectors.sharepoint.config import SharePointConnectorConfig, SharePointOnlineSiteConfig

    monkeypatch.setattr(CredentialRef, "resolve", lambda self: "secret")
    site = SharePointOnlineSiteConfig(
        site_url="https://contoso.sharepoint.com/sites/A",
        tenant_id="t",
        client_id="c",
        client_credential=CredentialRef(service="svc", username="user"),
    )
    return SharePointConnectorConfig(sites=[site], result_limit=10)


def test_sharepoint_server_registers_search_and_fetch_tools(monkeypatch):
    from mcp_connectors.sharepoint.mcp_server import build_server

    server = build_server(_sharepoint_config(monkeypatch), client=_FakeSharePointClient())
    tools = asyncio.run(server.list_tools())
    assert {t.name for t in tools} == {"search", "fetch"}


def test_sharepoint_server_search_happy_path(monkeypatch):
    from mcp_connectors.sharepoint.mcp_server import build_server

    server = build_server(_sharepoint_config(monkeypatch), client=_FakeSharePointClient())
    content, structured = asyncio.run(server.call_tool("search", {"query": "report"}))
    assert structured["result"][0]["title"] == "Q3.docx"


def test_sharepoint_server_main_exits_cleanly_when_config_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MCP_CONNECTORS_CONFIG_DIR", str(tmp_path))
    from mcp_connectors.sharepoint.mcp_server import main

    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "sharepoint-mcp" in captured.err


# -- Local Docs -------------------------------------------------------------------


class _FakeLocalDocsClient:
    def __init__(self):
        self.search_calls = []
        self.fetch_calls = []

    def search(self, query, directories=None):
        self.search_calls.append((query, directories))
        if directories and "/etc" in directories:
            raise ConnectorConfigError("directory(s) ['/etc'] are not in this connector's configured allowlist")
        return [Document(id="/allowed/readme.md", title="readme.md", source="local_docs", container="/allowed")]

    def fetch(self, id):
        self.fetch_calls.append(id)
        return Document(id=id, title="readme.md", source="local_docs", container="/allowed")


def _local_docs_config(tmp_path):
    from mcp_connectors.local_docs.config import LocalDocsConnectorConfig

    allowed = tmp_path / "docs"
    allowed.mkdir()
    return LocalDocsConnectorConfig(allowed_directories=[str(allowed)], result_limit=10)


def test_local_docs_server_registers_search_and_fetch_tools(tmp_path):
    from mcp_connectors.local_docs.mcp_server import build_server

    server = build_server(_local_docs_config(tmp_path), client=_FakeLocalDocsClient())
    tools = asyncio.run(server.list_tools())
    assert {t.name for t in tools} == {"search", "fetch"}


def test_local_docs_server_search_happy_path(tmp_path):
    from mcp_connectors.local_docs.mcp_server import build_server

    fake = _FakeLocalDocsClient()
    server = build_server(_local_docs_config(tmp_path), client=fake)
    content, structured = asyncio.run(server.call_tool("search", {"query": "hello"}))
    assert structured["result"][0]["title"] == "readme.md"
    assert fake.search_calls == [("hello", None)]


def test_local_docs_server_fetch_happy_path(tmp_path):
    from mcp_connectors.local_docs.mcp_server import build_server

    fake = _FakeLocalDocsClient()
    server = build_server(_local_docs_config(tmp_path), client=fake)
    content, structured = asyncio.run(server.call_tool("fetch", {"id": "/allowed/readme.md"}))
    assert structured["id"] == "/allowed/readme.md"
    assert fake.fetch_calls == ["/allowed/readme.md"]


def test_local_docs_server_search_allowlist_violation_raises_tool_error(tmp_path):
    from mcp.server.fastmcp.exceptions import ToolError

    from mcp_connectors.local_docs.mcp_server import build_server

    server = build_server(_local_docs_config(tmp_path), client=_FakeLocalDocsClient())
    with pytest.raises(ToolError) as excinfo:
        asyncio.run(server.call_tool("search", {"query": "x", "directories": ["/etc"]}))
    assert "/etc" in str(excinfo.value)


def test_local_docs_server_main_exits_cleanly_when_config_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MCP_CONNECTORS_CONFIG_DIR", str(tmp_path))
    from mcp_connectors.local_docs.mcp_server import main

    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "local-docs-mcp" in captured.err


# -- OneDrive -----------------------------------------------------------------------


class _FakeOneDriveClient:
    def __init__(self):
        self.search_calls = []
        self.fetch_calls = []

    def search(self, query, directories=None):
        self.search_calls.append((query, directories))
        return [Document(id="/onedrive/plan.md", title="plan.md", source="onedrive", container="/onedrive")]

    def fetch(self, id):
        self.fetch_calls.append(id)
        return Document(id=id, title="plan.md", source="onedrive", container="/onedrive")


def _onedrive_config(tmp_path):
    from mcp_connectors.onedrive.config import OneDriveConnectorConfig

    allowed = tmp_path / "OneDrive-Contoso"
    allowed.mkdir()
    return OneDriveConnectorConfig(allowed_directories=[str(allowed)], result_limit=10)


def test_onedrive_server_registers_search_and_fetch_tools(tmp_path):
    from mcp_connectors.onedrive.mcp_server import build_server

    server = build_server(_onedrive_config(tmp_path), client=_FakeOneDriveClient())
    tools = asyncio.run(server.list_tools())
    assert {t.name for t in tools} == {"search", "fetch"}


def test_onedrive_server_search_happy_path(tmp_path):
    from mcp_connectors.onedrive.mcp_server import build_server

    fake = _FakeOneDriveClient()
    server = build_server(_onedrive_config(tmp_path), client=fake)
    content, structured = asyncio.run(server.call_tool("search", {"query": "roadmap"}))
    assert structured["result"][0]["title"] == "plan.md"


def test_onedrive_server_main_exits_cleanly_when_config_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MCP_CONNECTORS_CONFIG_DIR", str(tmp_path))
    from mcp_connectors.onedrive.mcp_server import main

    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "onedrive-mcp" in captured.err
