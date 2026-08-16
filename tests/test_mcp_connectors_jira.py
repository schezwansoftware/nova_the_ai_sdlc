"""Tests for the Jira connector: config validation/round-trip, JQL
construction, ADF/plain-text description parsing, and `JiraClient.search()
`/`fetch()` against an injected `httpx.MockTransport` -- real request
construction (JQL string, allowlist enforcement, Cloud-vs-Data-Center
endpoint selection) exercised end to end, no network call, no live Jira
tenant.
"""
from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from ai_sdlc.mcp_connectors.atlassian.auth import AtlassianSiteConfig
from ai_sdlc.mcp_connectors.common import ConnectorAPIError, ConnectorConfigError, CredentialRef
from ai_sdlc.mcp_connectors.jira.client import (
    JIRA_CLOUD_SEARCH_PATH,
    JIRA_DATA_CENTER_SEARCH_PATH,
    JiraClient,
    _extract_description_text,
    _extract_issues,
    _parse_jira_datetime,
    build_jql,
)
from ai_sdlc.mcp_connectors.jira.config import (
    JiraConnectorConfig,
    config_path,
    load_config,
    save_config,
)


def _cloud_site(monkeypatch, secret="tok") -> AtlassianSiteConfig:
    monkeypatch.setattr(CredentialRef, "resolve", lambda self: secret)
    return AtlassianSiteConfig(
        base_url="https://example.atlassian.net",
        deployment_type="cloud",
        auth_method="cloud_api_token",
        account_identifier="bot@example.com",
        credential=CredentialRef(service="svc", username="user"),
    )


def _dc_site(monkeypatch, secret="pat") -> AtlassianSiteConfig:
    monkeypatch.setattr(CredentialRef, "resolve", lambda self: secret)
    return AtlassianSiteConfig(
        base_url="https://jira.internal.corp",
        deployment_type="data_center",
        auth_method="data_center_pat",
        credential=CredentialRef(service="svc", username="user"),
    )


def _issue(key="ENG-1", summary="Summary", description="Body text", updated="2026-08-10T14:03:00.000+0000"):
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "description": description,
            "status": {"name": "Open"},
            "issuetype": {"name": "Task"},
            "project": {"key": key.rsplit("-", 1)[0]},
            "updated": updated,
        },
    }


# -- JiraConnectorConfig -------------------------------------------------------


def test_allowed_projects_normalized_uppercased_and_deduped(monkeypatch):
    site = _cloud_site(monkeypatch)
    config = JiraConnectorConfig(site=site, allowed_projects=["eng", "ENG", " plat "])
    assert config.allowed_projects == ["ENG", "PLAT"]


def test_allowed_projects_must_be_non_empty(monkeypatch):
    site = _cloud_site(monkeypatch)
    with pytest.raises(ValidationError):
        JiraConnectorConfig(site=site, allowed_projects=[])


def test_result_limit_default_and_bounds(monkeypatch):
    site = _cloud_site(monkeypatch)
    config = JiraConnectorConfig(site=site, allowed_projects=["ENG"])
    assert config.result_limit == 15
    with pytest.raises(ValidationError):
        JiraConnectorConfig(site=site, allowed_projects=["ENG"], result_limit=0)
    with pytest.raises(ValidationError):
        JiraConnectorConfig(site=site, allowed_projects=["ENG"], result_limit=51)


def test_config_round_trips_through_save_and_load(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_SDLC_MCP_CONFIG_DIR", str(tmp_path))
    site = _cloud_site(monkeypatch, secret="super-secret-value-xyz")
    config = JiraConnectorConfig(site=site, allowed_projects=["ENG"], result_limit=20)
    save_config(config)

    loaded = load_config()
    assert loaded.allowed_projects == ["ENG"]
    assert loaded.result_limit == 20
    assert loaded.site.base_url == "https://example.atlassian.net"

    on_disk = json.loads(config_path().read_text(encoding="utf-8"))
    # the credential reference is stored, never a raw secret
    assert on_disk["site"]["credential"] == {"service": "svc", "username": "user"}
    assert "super-secret-value-xyz" not in json.dumps(on_disk)


def test_load_config_raises_clear_error_when_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_SDLC_MCP_CONFIG_DIR", str(tmp_path))
    with pytest.raises(ConnectorConfigError) as excinfo:
        load_config()
    assert "jira.json" in str(excinfo.value) or str(tmp_path) in str(excinfo.value)


# -- build_jql -----------------------------------------------------------------


def test_build_jql_shape_matches_approved_design():
    jql = build_jql("outage", ["ENG", "PLAT"])
    assert jql == 'project in (ENG, PLAT) AND text ~ "outage" ORDER BY updated DESC'


def test_build_jql_escapes_quotes_in_query():
    jql = build_jql('say "hi"', ["ENG"])
    assert '\\"hi\\"' in jql


def test_build_jql_rejects_empty_query():
    with pytest.raises(ConnectorAPIError):
        build_jql("", ["ENG"])


def test_build_jql_rejects_empty_projects():
    with pytest.raises(ConnectorAPIError):
        build_jql("outage", [])


# -- description text extraction (plain string vs ADF) ------------------------


def test_extract_description_text_plain_string():
    assert _extract_description_text("plain text") == "plain text"


def test_extract_description_text_adf_document():
    adf = {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Hello"}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "world"}]},
        ],
    }
    assert _extract_description_text(adf) == "Hello world"


def test_extract_description_text_none_or_unknown_type():
    assert _extract_description_text(None) == ""
    assert _extract_description_text(42) == ""


def test_parse_jira_datetime_valid_and_invalid():
    parsed = _parse_jira_datetime("2026-08-10T14:03:00.000+0000")
    assert parsed is not None
    assert parsed.year == 2026
    assert _parse_jira_datetime("not-a-date") is None
    assert _parse_jira_datetime(None) is None


# -- JiraClient.search() --------------------------------------------------------


def test_search_scopes_to_allowlist_and_hits_cloud_jql_endpoint(monkeypatch):
    site = _cloud_site(monkeypatch)
    config = JiraConnectorConfig(site=site, allowed_projects=["ENG"], result_limit=10)
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"issues": [_issue()]})

    http_client = httpx.Client(base_url=site.base_url, transport=httpx.MockTransport(handler))
    client = JiraClient(config, http_client=http_client)

    docs = client.search("bug")
    assert captured["url"].endswith(JIRA_CLOUD_SEARCH_PATH)
    assert "project in (ENG)" in captured["body"]["jql"]
    assert captured["body"]["maxResults"] == 10
    assert "startAt" not in captured["body"]
    assert docs[0].id == "ENG-1"
    assert docs[0].container == "ENG"
    assert docs[0].source == "jira"
    assert docs[0].url == "https://example.atlassian.net/browse/ENG-1"


def test_search_hits_data_center_classic_endpoint(monkeypatch):
    site = _dc_site(monkeypatch)
    config = JiraConnectorConfig(site=site, allowed_projects=["ENG"], result_limit=10)
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"issues": []})

    http_client = httpx.Client(base_url=site.base_url, transport=httpx.MockTransport(handler))
    client = JiraClient(config, http_client=http_client)

    client.search("bug")
    assert captured["url"].endswith(JIRA_DATA_CENTER_SEARCH_PATH)
    assert captured["body"]["startAt"] == 0


def test_search_rejects_project_outside_allowlist_without_making_a_request(monkeypatch):
    site = _cloud_site(monkeypatch)
    config = JiraConnectorConfig(site=site, allowed_projects=["ENG"], result_limit=10)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no HTTP request should be made for a disallowed project")

    http_client = httpx.Client(base_url=site.base_url, transport=httpx.MockTransport(handler))
    client = JiraClient(config, http_client=http_client)

    with pytest.raises(ConnectorConfigError):
        client.search("bug", projects=["SECRET"])


def test_search_raises_connector_api_error_on_http_failure(monkeypatch):
    site = _cloud_site(monkeypatch)
    config = JiraConnectorConfig(site=site, allowed_projects=["ENG"], result_limit=10)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    http_client = httpx.Client(base_url=site.base_url, transport=httpx.MockTransport(handler))
    client = JiraClient(config, http_client=http_client)

    with pytest.raises(ConnectorAPIError):
        client.search("bug")


def test_search_raises_on_malformed_json(monkeypatch):
    site = _cloud_site(monkeypatch)
    config = JiraConnectorConfig(site=site, allowed_projects=["ENG"], result_limit=10)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    http_client = httpx.Client(base_url=site.base_url, transport=httpx.MockTransport(handler))
    client = JiraClient(config, http_client=http_client)

    with pytest.raises(ConnectorAPIError):
        client.search("bug")


def test_extract_issues_tolerates_missing_or_malformed_issues_key():
    assert _extract_issues({}) == []
    assert _extract_issues({"issues": "not-a-list"}) == []
    assert _extract_issues(None) == []
    assert _extract_issues({"issues": [_issue(), "not-a-dict"]}) == [_issue()]


# -- JiraClient.fetch() ---------------------------------------------------------


def test_fetch_uses_scoped_jql_search_not_a_raw_get(monkeypatch):
    site = _cloud_site(monkeypatch)
    config = JiraConnectorConfig(site=site, allowed_projects=["ENG"], result_limit=10)
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"issues": [_issue()]})

    http_client = httpx.Client(base_url=site.base_url, transport=httpx.MockTransport(handler))
    client = JiraClient(config, http_client=http_client)

    doc = client.fetch("eng-1")
    assert captured["method"] == "POST"
    assert 'issueKey = "ENG-1"' in captured["body"]["jql"]
    assert "project in (ENG)" in captured["body"]["jql"]
    assert doc.id == "ENG-1"


def test_fetch_rejects_malformed_issue_key_without_making_a_request(monkeypatch):
    site = _cloud_site(monkeypatch)
    config = JiraConnectorConfig(site=site, allowed_projects=["ENG"], result_limit=10)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no HTTP request should be made for a malformed key")

    http_client = httpx.Client(base_url=site.base_url, transport=httpx.MockTransport(handler))
    client = JiraClient(config, http_client=http_client)

    with pytest.raises(ConnectorAPIError):
        client.fetch("NODASHATALL")


def test_fetch_rejects_project_outside_allowlist_without_making_a_request(monkeypatch):
    site = _cloud_site(monkeypatch)
    config = JiraConnectorConfig(site=site, allowed_projects=["ENG"], result_limit=10)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no HTTP request should be made for a disallowed project")

    http_client = httpx.Client(base_url=site.base_url, transport=httpx.MockTransport(handler))
    client = JiraClient(config, http_client=http_client)

    with pytest.raises(ConnectorConfigError):
        client.fetch("SECRET-1")


def test_fetch_raises_when_no_issue_found(monkeypatch):
    site = _cloud_site(monkeypatch)
    config = JiraConnectorConfig(site=site, allowed_projects=["ENG"], result_limit=10)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"issues": []})

    http_client = httpx.Client(base_url=site.base_url, transport=httpx.MockTransport(handler))
    client = JiraClient(config, http_client=http_client)

    with pytest.raises(ConnectorAPIError):
        client.fetch("ENG-999")
