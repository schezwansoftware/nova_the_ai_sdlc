"""Tests for the SharePoint connector's config models (the discriminated
`online`/`server` union -- see `sharepoint/config.py`'s module docstring
for why `deployment_type` is explicit, never auto-detected) and the
SharePoint **Online** backend (`online_client.py`): Graph Search
`Path:`-scoped query construction, Azure AD client-credentials token
acquisition/caching, and defensive Graph response parsing -- all against
injected `httpx.MockTransport`s, no live tenant.
"""
from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from mcp_connectors.common import ConnectorAPIError, ConnectorAuthError, CredentialRef
from mcp_connectors.sharepoint.config import (
    SharePointConnectorConfig,
    SharePointOnlineSiteConfig,
    SharePointServerSiteConfig,
)
from mcp_connectors.sharepoint.online_client import (
    GRAPH_SEARCH_PATH,
    SharePointOnlineClient,
    _extract_graph_hits,
    build_graph_query_string,
)


def _online_site(monkeypatch, secret="client-secret") -> SharePointOnlineSiteConfig:
    monkeypatch.setattr(CredentialRef, "resolve", lambda self: secret)
    return SharePointOnlineSiteConfig(
        site_url="https://contoso.sharepoint.com/sites/Finance",
        tenant_id="tenant-1",
        client_id="client-1",
        client_credential=CredentialRef(service="svc", username="user"),
    )


def _server_site(monkeypatch, secret="password") -> SharePointServerSiteConfig:
    monkeypatch.setattr(CredentialRef, "resolve", lambda self: secret)
    return SharePointServerSiteConfig(
        site_url="https://sp2019.internal/sites/Legal",
        auth_method="basic",
        username="jdoe",
        credential=CredentialRef(service="svc", username="jdoe"),
    )


# -- config: discriminated union, allowlist-as-sites -----------------------------


def test_deployment_type_discriminates_online_vs_server(monkeypatch):
    online = _online_site(monkeypatch)
    server = _server_site(monkeypatch)
    config = SharePointConnectorConfig(sites=[online, server])
    assert isinstance(config.sites[0], SharePointOnlineSiteConfig)
    assert isinstance(config.sites[1], SharePointServerSiteConfig)


def test_deployment_type_is_required_and_must_be_a_known_value(monkeypatch):
    with pytest.raises(ValidationError):
        SharePointConnectorConfig(
            sites=[
                {
                    "deployment_type": "hybrid-auto-detect",  # not a real value
                    "site_url": "https://contoso.sharepoint.com/sites/X",
                }
            ]
        )


def test_sites_must_be_non_empty():
    with pytest.raises(ValidationError):
        SharePointConnectorConfig(sites=[])


def test_duplicate_site_urls_rejected(monkeypatch):
    online = _online_site(monkeypatch)
    online_dupe = SharePointOnlineSiteConfig(
        site_url=online.site_url,
        tenant_id="tenant-2",
        client_id="client-2",
        client_credential=CredentialRef(service="svc2", username="user2"),
    )
    with pytest.raises(ValidationError):
        SharePointConnectorConfig(sites=[online, online_dupe])


def test_site_url_must_be_absolute_http_url():
    with pytest.raises(ValidationError):
        SharePointOnlineSiteConfig(
            site_url="contoso.sharepoint.com/sites/Finance",
            tenant_id="t",
            client_id="c",
            client_credential=CredentialRef(service="svc", username="user"),
        )


def test_online_site_rejects_unknown_fields(monkeypatch):
    with pytest.raises(ValidationError):
        SharePointOnlineSiteConfig(
            site_url="https://contoso.sharepoint.com/sites/Finance",
            tenant_id="t",
            client_id="c",
            client_credential=CredentialRef(service="svc", username="user"),
            username="oops-not-an-online-field",
        )


# -- build_graph_query_string ----------------------------------------------------


def test_build_graph_query_string_shape():
    qs = build_graph_query_string("report", ["https://contoso.sharepoint.com/sites/Finance"])
    assert qs == '(report) AND (Path:"https://contoso.sharepoint.com/sites/Finance")'


def test_build_graph_query_string_multiple_sites_ored():
    qs = build_graph_query_string("report", ["https://a.example.com/sites/X", "https://a.example.com/sites/Y"])
    assert 'Path:"https://a.example.com/sites/X"' in qs
    assert 'Path:"https://a.example.com/sites/Y"' in qs
    assert " OR " in qs


def test_build_graph_query_string_rejects_empty_query():
    with pytest.raises(ConnectorAPIError):
        build_graph_query_string("", ["https://contoso.sharepoint.com/sites/Finance"])


def test_build_graph_query_string_rejects_no_sites():
    with pytest.raises(ConnectorAPIError):
        build_graph_query_string("report", [])


# -- _extract_graph_hits (defensive parsing) -------------------------------------


def test_extract_graph_hits_well_formed():
    payload = {
        "value": [
            {
                "hitsContainers": [
                    {"hits": [{"resource": {"id": "1", "name": "a.docx"}, "summary": "hi"}]}
                ]
            }
        ]
    }
    hits = _extract_graph_hits(payload)
    assert len(hits) == 1
    assert hits[0]["id"] == "1"
    assert hits[0]["_summary"] == "hi"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"value": []},
        {"value": [{"hitsContainers": []}]},
        {"value": [{"hitsContainers": [{"hits": []}]}]},
        None,
        "not-a-dict",
    ],
)
def test_extract_graph_hits_degrades_gracefully_on_malformed_payload(payload):
    assert _extract_graph_hits(payload) == []


# -- SharePointOnlineClient: token acquisition/caching ---------------------------


def test_search_acquires_and_caches_token_across_calls(monkeypatch):
    site = _online_site(monkeypatch)
    token_calls = {"count": 0}

    def token_handler(request: httpx.Request) -> httpx.Response:
        token_calls["count"] += 1
        return httpx.Response(200, json={"access_token": "jwt-1", "expires_in": 3599})

    def graph_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer jwt-1"
        return httpx.Response(200, json={"value": [{"hitsContainers": [{"hits": []}]}]})

    client = SharePointOnlineClient(
        site,
        http_client=httpx.Client(base_url="https://graph.microsoft.com/v1.0", transport=httpx.MockTransport(graph_handler)),
        token_client=httpx.Client(transport=httpx.MockTransport(token_handler)),
    )
    client.search("report", limit=10)
    client.search("report", limit=10)
    assert token_calls["count"] == 1  # cached, not re-requested


def test_token_request_failure_raises_connector_auth_error(monkeypatch):
    site = _online_site(monkeypatch)

    def token_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid client secret")

    client = SharePointOnlineClient(
        site,
        http_client=httpx.Client(base_url="https://graph.microsoft.com/v1.0"),
        token_client=httpx.Client(transport=httpx.MockTransport(token_handler)),
    )
    with pytest.raises(ConnectorAuthError):
        client.search("report", limit=10)


def test_token_response_missing_access_token_raises(monkeypatch):
    site = _online_site(monkeypatch)

    def token_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"token_type": "Bearer"})  # no access_token

    client = SharePointOnlineClient(
        site,
        http_client=httpx.Client(base_url="https://graph.microsoft.com/v1.0"),
        token_client=httpx.Client(transport=httpx.MockTransport(token_handler)),
    )
    with pytest.raises(ConnectorAuthError):
        client.search("report", limit=10)


# -- SharePointOnlineClient.search()/fetch() end to end --------------------------


def _client_with_fakes(site, graph_handler):
    def token_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "jwt", "expires_in": 3599})

    return SharePointOnlineClient(
        site,
        http_client=httpx.Client(base_url="https://graph.microsoft.com/v1.0", transport=httpx.MockTransport(graph_handler)),
        token_client=httpx.Client(transport=httpx.MockTransport(token_handler)),
    )


def test_search_scopes_query_with_path_clause_and_parses_hits(monkeypatch):
    site = _online_site(monkeypatch)
    captured = {}

    def graph_handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "hitsContainers": [
                            {
                                "hits": [
                                    {
                                        "resource": {
                                            "id": "item1",
                                            "name": "Q3.docx",
                                            "webUrl": f"{site.site_url}/Q3.docx",
                                            "lastModifiedDateTime": "2026-08-01T10:00:00Z",
                                            "parentReference": {"driveId": "drive1"},
                                        },
                                        "summary": "quarterly report",
                                    }
                                ]
                            }
                        ]
                    }
                ]
            },
        )

    client = _client_with_fakes(site, graph_handler)
    docs = client.search("report", limit=10)

    request_body = captured["body"]["requests"][0]
    assert request_body["entityTypes"] == ["driveItem"]
    assert f'Path:"{site.site_url}"' in request_body["query"]["queryString"]
    assert request_body["size"] == 10

    assert docs[0].id == "drive1:item1"
    assert docs[0].container == site.site_url
    assert docs[0].source == "sharepoint"


def test_fetch_verifies_item_is_within_configured_site(monkeypatch):
    site = _online_site(monkeypatch)

    def graph_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "item1",
                "name": "Q3.docx",
                "webUrl": f"{site.site_url}/Q3.docx",
                "lastModifiedDateTime": "2026-08-01T10:00:00Z",
            },
        )

    client = _client_with_fakes(site, graph_handler)
    doc = client.fetch("drive1", "item1")
    assert doc.id == "drive1:item1"


def test_fetch_rejects_item_outside_configured_site(monkeypatch):
    site = _online_site(monkeypatch)

    def graph_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "item1",
                "name": "Leaked.docx",
                "webUrl": "https://contoso.sharepoint.com/sites/HR/Leaked.docx",  # different site
                "lastModifiedDateTime": "2026-08-01T10:00:00Z",
            },
        )

    client = _client_with_fakes(site, graph_handler)
    with pytest.raises(ConnectorAPIError):
        client.fetch("drive1", "item1")


def test_search_raises_connector_api_error_on_http_failure(monkeypatch):
    site = _online_site(monkeypatch)

    def graph_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server error")

    client = _client_with_fakes(site, graph_handler)
    with pytest.raises(ConnectorAPIError):
        client.search("report", limit=10)
