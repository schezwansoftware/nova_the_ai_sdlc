"""Tests for `sharepoint/client.py`'s `SharePointClient` facade: site
allowlist enforcement (SharePoint's allowlist unit is the configured
site itself -- see `config.py`'s module docstring), dispatch to whichever
backend a site's `deployment_type` declares, the `"<site_url>::<backend
id>"` document-id composition/parsing scheme, and per-site backend
instance caching.

Backends are injected fakes throughout (`online_client_factory`/
`onprem_client_factory`) -- this module tests the facade's own dispatch/
allowlist/id-composition logic in isolation from either real backend's
HTTP behavior (that's `test_mcp_connectors_sharepoint_online.py`/
`test_mcp_connectors_sharepoint_onprem.py`'s job).
"""
from __future__ import annotations

import pytest

from ai_sdlc.mcp_connectors.common import ConnectorAPIError, ConnectorConfigError, CredentialRef, Document
from ai_sdlc.mcp_connectors.sharepoint.client import SharePointClient
from ai_sdlc.mcp_connectors.sharepoint.config import (
    SharePointConnectorConfig,
    SharePointOnlineSiteConfig,
    SharePointServerSiteConfig,
)


def _online_site(monkeypatch, url="https://contoso.sharepoint.com/sites/Finance") -> SharePointOnlineSiteConfig:
    monkeypatch.setattr(CredentialRef, "resolve", lambda self: "secret")
    return SharePointOnlineSiteConfig(
        site_url=url, tenant_id="t", client_id="c", client_credential=CredentialRef(service="svc", username="user")
    )


def _server_site(monkeypatch, url="https://sp2019.internal/sites/Legal") -> SharePointServerSiteConfig:
    monkeypatch.setattr(CredentialRef, "resolve", lambda self: "secret")
    return SharePointServerSiteConfig(
        site_url=url, auth_method="basic", username="jdoe", credential=CredentialRef(service="svc", username="jdoe")
    )


class _FakeBackend:
    def __init__(self, docs=None):
        self._docs = docs or []
        self.search_calls = []
        self.fetch_calls = []

    def search(self, query, *, limit):
        self.search_calls.append((query, limit))
        return list(self._docs)[:limit]

    def fetch(self, *args):
        self.fetch_calls.append(args)
        if not self._docs:
            raise ConnectorAPIError("not found")
        return self._docs[0]


def _doc(doc_id, site_url):
    return Document(id=doc_id, title="t", source="sharepoint", container=site_url)


# -- allowlist / dispatch --------------------------------------------------------


def test_search_dispatches_to_online_backend_for_online_site(monkeypatch):
    online = _online_site(monkeypatch)
    fake = _FakeBackend(docs=[_doc("item1", online.site_url)])
    config = SharePointConnectorConfig(sites=[online], result_limit=10)
    client = SharePointClient(config, online_client_factory=lambda site: fake)

    docs = client.search("report")
    assert fake.search_calls == [("report", 10)]
    assert docs[0].id == f"{online.site_url}::item1"


def test_search_dispatches_to_onprem_backend_for_server_site(monkeypatch):
    server = _server_site(monkeypatch)
    fake = _FakeBackend(docs=[_doc("path/to/doc", server.site_url)])
    config = SharePointConnectorConfig(sites=[server], result_limit=10)
    client = SharePointClient(config, onprem_client_factory=lambda site: fake)

    docs = client.search("memo")
    assert fake.search_calls == [("memo", 10)]
    assert docs[0].id == f"{server.site_url}::path/to/doc"


def test_search_across_multiple_sites_aggregates_and_caps_at_result_limit(monkeypatch):
    online = _online_site(monkeypatch, url="https://contoso.sharepoint.com/sites/A")
    server = _server_site(monkeypatch, url="https://sp2019.internal/sites/B")
    online_fake = _FakeBackend(docs=[_doc("i1", online.site_url), _doc("i2", online.site_url)])
    server_fake = _FakeBackend(docs=[_doc("p1", server.site_url), _doc("p2", server.site_url)])
    config = SharePointConnectorConfig(sites=[online, server], result_limit=3)
    client = SharePointClient(
        config,
        online_client_factory=lambda site: online_fake,
        onprem_client_factory=lambda site: server_fake,
    )

    docs = client.search("x")
    assert len(docs) == 3  # capped at result_limit even though 4 raw hits exist


def test_search_rejects_site_outside_allowlist_without_touching_any_backend(monkeypatch):
    online = _online_site(monkeypatch)
    config = SharePointConnectorConfig(sites=[online], result_limit=10)

    def factory(site):  # pragma: no cover
        raise AssertionError("no backend should be constructed for a disallowed site")

    client = SharePointClient(config, online_client_factory=factory)
    with pytest.raises(ConnectorConfigError):
        client.search("report", sites=["https://not-configured.example.com"])


def test_backend_instances_are_cached_across_calls(monkeypatch):
    online = _online_site(monkeypatch)
    fake = _FakeBackend(docs=[_doc("item1", online.site_url)])
    construction_count = {"n": 0}

    def factory(site):
        construction_count["n"] += 1
        return fake

    config = SharePointConnectorConfig(sites=[online], result_limit=10)
    client = SharePointClient(config, online_client_factory=factory)
    client.search("a")
    client.search("b")
    assert construction_count["n"] == 1


# -- document id composition / parsing --------------------------------------------


def test_fetch_parses_site_prefix_and_dispatches_to_correct_backend(monkeypatch):
    online = _online_site(monkeypatch)
    fake = _FakeBackend(docs=[_doc("item1", online.site_url)])
    config = SharePointConnectorConfig(sites=[online], result_limit=10)
    client = SharePointClient(config, online_client_factory=lambda site: fake)

    doc = client.fetch(f"{online.site_url}::drive1:item1")
    assert fake.fetch_calls == [("drive1", "item1")]
    assert doc.id == f"{online.site_url}::item1"


def test_fetch_onprem_backend_id_passed_through_whole_after_site_prefix(monkeypatch):
    server = _server_site(monkeypatch)
    fake = _FakeBackend(docs=[_doc("path/to/doc.docx", server.site_url)])
    config = SharePointConnectorConfig(sites=[server], result_limit=10)
    client = SharePointClient(config, onprem_client_factory=lambda site: fake)

    doc = client.fetch(f"{server.site_url}::path/to/doc.docx")
    assert fake.fetch_calls == [("path/to/doc.docx",)]
    assert doc.id == f"{server.site_url}::path/to/doc.docx"


def test_fetch_rejects_id_without_separator():
    online_site_not_needed = None
    config = SharePointConnectorConfig(
        sites=[
            SharePointServerSiteConfig(
                site_url="https://sp2019.internal/sites/Legal",
                auth_method="basic",
                username="jdoe",
                credential=CredentialRef(service="svc", username="jdoe"),
            )
        ]
    )
    client = SharePointClient(config)
    with pytest.raises(ConnectorAPIError):
        client.fetch("no-separator-here")


def test_fetch_rejects_site_outside_allowlist(monkeypatch):
    online = _online_site(monkeypatch)
    config = SharePointConnectorConfig(sites=[online], result_limit=10)
    client = SharePointClient(config, online_client_factory=lambda site: _FakeBackend())

    with pytest.raises(ConnectorConfigError):
        client.fetch("https://not-configured.example.com::item1")


def test_fetch_online_id_without_backend_colon_raises(monkeypatch):
    online = _online_site(monkeypatch)
    config = SharePointConnectorConfig(sites=[online], result_limit=10)
    client = SharePointClient(config, online_client_factory=lambda site: _FakeBackend())

    with pytest.raises(ConnectorAPIError):
        client.fetch(f"{online.site_url}::just-an-item-id-no-drive")
