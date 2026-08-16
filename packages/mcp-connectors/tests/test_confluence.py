"""Tests for the Confluence connector: config validation/round-trip, CQL
construction (including the id-based `fetch()` variant), and
`ConfluenceClient.search()`/`fetch()` against an injected
`httpx.MockTransport`. Mirrors `tests/test_mcp_connectors_jira.py`'s
structure/coverage for the sibling connector.
"""
from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from mcp_connectors.atlassian.auth import AtlassianSiteConfig
from mcp_connectors.common import ConnectorAPIError, ConnectorConfigError, CredentialRef
from mcp_connectors.confluence.client import (
    CONFLUENCE_SEARCH_PATH,
    ConfluenceClient,
    _parse_confluence_datetime,
    build_cql,
    build_cql_for_id,
)
from mcp_connectors.confluence.config import (
    ConfluenceConnectorConfig,
    config_path,
    load_config,
    save_config,
)


def _cloud_site(monkeypatch, secret="tok") -> AtlassianSiteConfig:
    monkeypatch.setattr(CredentialRef, "resolve", lambda self: secret)
    return AtlassianSiteConfig(
        base_url="https://example.atlassian.net/wiki",
        deployment_type="cloud",
        auth_method="cloud_api_token",
        account_identifier="bot@example.com",
        credential=CredentialRef(service="svc", username="user"),
    )


def _result(content_id="999", title="Runbook", space="ENG", excerpt="how to <b>deploy</b>"):
    return {
        "content": {"id": content_id, "type": "page", "status": "current", "title": title, "space": {"key": space}},
        "title": title,
        "excerpt": excerpt,
        "url": f"/spaces/{space}/pages/{content_id}/{title}",
        "lastModified": "2026-07-15T09:00:00.000Z",
    }


# -- ConfluenceConnectorConfig --------------------------------------------------


def test_allowed_spaces_normalized_uppercased_and_deduped(monkeypatch):
    site = _cloud_site(monkeypatch)
    config = ConfluenceConnectorConfig(site=site, allowed_spaces=["eng", "ENG", " plat "])
    assert config.allowed_spaces == ["ENG", "PLAT"]


def test_allowed_spaces_must_be_non_empty(monkeypatch):
    site = _cloud_site(monkeypatch)
    with pytest.raises(ValidationError):
        ConfluenceConnectorConfig(site=site, allowed_spaces=[])


def test_config_round_trips_through_save_and_load(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_CONNECTORS_CONFIG_DIR", str(tmp_path))
    site = _cloud_site(monkeypatch, secret="super-secret-value-xyz")
    config = ConfluenceConnectorConfig(site=site, allowed_spaces=["ENG"], result_limit=20)
    save_config(config)

    loaded = load_config()
    assert loaded.allowed_spaces == ["ENG"]
    assert loaded.result_limit == 20

    on_disk = json.loads(config_path().read_text(encoding="utf-8"))
    assert on_disk["site"]["credential"] == {"service": "svc", "username": "user"}
    assert "super-secret-value-xyz" not in json.dumps(on_disk)


def test_load_config_raises_clear_error_when_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_CONNECTORS_CONFIG_DIR", str(tmp_path))
    with pytest.raises(ConnectorConfigError):
        load_config()


# -- build_cql / build_cql_for_id ------------------------------------------------


def test_build_cql_shape_matches_approved_design():
    cql = build_cql("deploy", ["ENG", "PLAT"])
    assert cql == 'space in ("ENG", "PLAT") AND text ~ "deploy" order by lastmodified desc'


def test_build_cql_escapes_quotes():
    cql = build_cql('say "hi"', ["ENG"])
    assert '\\"hi\\"' in cql


def test_build_cql_rejects_empty_query():
    with pytest.raises(ConnectorAPIError):
        build_cql("", ["ENG"])


def test_build_cql_rejects_empty_spaces():
    with pytest.raises(ConnectorAPIError):
        build_cql("deploy", [])


def test_build_cql_for_id_shape():
    cql = build_cql_for_id("999", ["ENG", "PLAT"])
    assert cql == 'id = 999 AND space in ("ENG", "PLAT")'


def test_build_cql_for_id_rejects_empty_id():
    with pytest.raises(ConnectorAPIError):
        build_cql_for_id("", ["ENG"])


def test_parse_confluence_datetime_valid_and_invalid():
    parsed = _parse_confluence_datetime("2026-07-15T09:00:00.000Z")
    assert parsed is not None
    assert parsed.year == 2026
    assert _parse_confluence_datetime("garbage") is None
    assert _parse_confluence_datetime(None) is None


# -- ConfluenceClient.search() ---------------------------------------------------


def test_search_scopes_to_allowlist_and_appends_wiki_base_url(monkeypatch):
    site = _cloud_site(monkeypatch)
    config = ConfluenceConnectorConfig(site=site, allowed_spaces=["ENG"], result_limit=10)
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"results": [_result()]})

    http_client = httpx.Client(base_url=site.base_url, transport=httpx.MockTransport(handler))
    client = ConfluenceClient(config, http_client=http_client)

    docs = client.search("deploy")
    assert captured["url"].startswith(f"https://example.atlassian.net/wiki{CONFLUENCE_SEARCH_PATH}")
    assert "space+in+%28%22ENG%22%29" in captured["url"] or "space in (\"ENG\")" in captured["url"]
    doc = docs[0]
    assert doc.id == "999"
    assert doc.container == "ENG"
    assert doc.source == "confluence"
    assert doc.url == "https://example.atlassian.net/wiki/spaces/ENG/pages/999/Runbook"


def test_search_rejects_space_outside_allowlist_without_making_a_request(monkeypatch):
    site = _cloud_site(monkeypatch)
    config = ConfluenceConnectorConfig(site=site, allowed_spaces=["ENG"], result_limit=10)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no HTTP request should be made for a disallowed space")

    http_client = httpx.Client(base_url=site.base_url, transport=httpx.MockTransport(handler))
    client = ConfluenceClient(config, http_client=http_client)

    with pytest.raises(ConnectorConfigError):
        client.search("deploy", spaces=["SECRET"])


def test_search_raises_connector_api_error_on_http_failure(monkeypatch):
    site = _cloud_site(monkeypatch)
    config = ConfluenceConnectorConfig(site=site, allowed_spaces=["ENG"], result_limit=10)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    http_client = httpx.Client(base_url=site.base_url, transport=httpx.MockTransport(handler))
    client = ConfluenceClient(config, http_client=http_client)

    with pytest.raises(ConnectorAPIError):
        client.search("deploy")


def test_search_raises_on_malformed_json(monkeypatch):
    site = _cloud_site(monkeypatch)
    config = ConfluenceConnectorConfig(site=site, allowed_spaces=["ENG"], result_limit=10)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    http_client = httpx.Client(base_url=site.base_url, transport=httpx.MockTransport(handler))
    client = ConfluenceClient(config, http_client=http_client)

    with pytest.raises(ConnectorAPIError):
        client.search("deploy")


# -- ConfluenceClient.fetch() ----------------------------------------------------


def test_fetch_uses_scoped_cql_search(monkeypatch):
    site = _cloud_site(monkeypatch)
    config = ConfluenceConnectorConfig(site=site, allowed_spaces=["ENG"], result_limit=10)
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"results": [_result()]})

    http_client = httpx.Client(base_url=site.base_url, transport=httpx.MockTransport(handler))
    client = ConfluenceClient(config, http_client=http_client)

    doc = client.fetch("999")
    assert "id" in captured["url"]
    assert doc.id == "999"


def test_fetch_raises_when_nothing_found_within_allowlisted_spaces(monkeypatch):
    site = _cloud_site(monkeypatch)
    config = ConfluenceConnectorConfig(site=site, allowed_spaces=["ENG"], result_limit=10)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    http_client = httpx.Client(base_url=site.base_url, transport=httpx.MockTransport(handler))
    client = ConfluenceClient(config, http_client=http_client)

    with pytest.raises(ConnectorAPIError):
        client.fetch("12345")


def test_fetch_rejects_empty_id(monkeypatch):
    site = _cloud_site(monkeypatch)
    config = ConfluenceConnectorConfig(site=site, allowed_spaces=["ENG"], result_limit=10)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no HTTP request should be made for an empty id")

    http_client = httpx.Client(base_url=site.base_url, transport=httpx.MockTransport(handler))
    client = ConfluenceClient(config, http_client=http_client)

    with pytest.raises(ConnectorAPIError):
        client.fetch("   ")
