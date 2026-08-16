"""Tests for the SharePoint **Server** (on-prem) backend
(`onprem_client.py`): `Path:`-scoped KQL construction against
`_api/search/query`, defensive parsing of the classic nested
`Rows`/`Cells` response shape, and NTLM/Basic session construction --
including the deferred-import guard for `requests_ntlm` (verified
installed in this environment, `requests-ntlm==1.3.0` -- see that
module's docstring -- so the "not installed" path is exercised by
monkeypatching the module's own import-error sentinel, mirroring
`test_mcp_connectors_common.py::
test_keyring_not_installed_raises_clear_connector_auth_error`'s
identical technique for the same kind of optional-dependency guard).

`session` is always an injected fake (never a real `requests.Session`
making a network call) -- either a hand-rolled fake with a `.get(...)`
method (for search/fetch response-shape tests) or the real `requests`
package's `Session`/`HttpNtlmAuth` classes (for auth-construction
tests, to prove the wiring uses the real installed classes' real
constructor shapes, not just a same-named stand-in)."""
from __future__ import annotations

import pytest

from mcp_connectors.common import ConnectorAPIError, ConnectorAuthError, CredentialRef
from mcp_connectors.sharepoint.config import SharePointServerSiteConfig
from mcp_connectors.sharepoint.onprem_client import (
    ONPREM_SEARCH_PATH,
    SharePointServerClient,
    _extract_onprem_rows,
    build_onprem_kql,
)

ONPREM_PAYLOAD = {
    "d": {
        "query": {
            "PrimaryQueryResult": {
                "RelevantResults": {
                    "Table": {
                        "Rows": {
                            "results": [
                                {
                                    "Cells": {
                                        "results": [
                                            {"Key": "Title", "Value": "Legal Memo"},
                                            {"Key": "Path", "Value": "https://sp2019.internal/sites/Legal/memo.docx"},
                                            {"Key": "Author", "Value": "Jane Doe"},
                                            {"Key": "LastModifiedTime", "Value": "2026-07-01T00:00:00Z"},
                                            {"Key": "UniqueId", "Value": "guid-123"},
                                            {"Key": "HitHighlightedSummary", "Value": "the <c0>memo</c0> content"},
                                        ]
                                    }
                                }
                            ]
                        }
                    }
                }
            }
        }
    }
}


def _server_site(monkeypatch, secret="password", auth_method="basic") -> SharePointServerSiteConfig:
    monkeypatch.setattr(CredentialRef, "resolve", lambda self: secret)
    return SharePointServerSiteConfig(
        site_url="https://sp2019.internal/sites/Legal",
        auth_method=auth_method,
        username="jdoe" if auth_method == "basic" else "CONTOSO\\svc-mcp-connectors",
        credential=CredentialRef(service="svc", username="jdoe"),
    )


class _FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or str(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


class _FakeSession:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        return self._response


# -- build_onprem_kql -----------------------------------------------------------


def test_build_onprem_kql_shape():
    kql = build_onprem_kql("memo", ["https://sp2019.internal/sites/Legal"])
    assert kql == '(memo) AND (Path:"https://sp2019.internal/sites/Legal*")'


def test_build_onprem_kql_multiple_sites_ored():
    kql = build_onprem_kql("memo", ["https://a/sites/X", "https://a/sites/Y"])
    assert 'Path:"https://a/sites/X*"' in kql
    assert 'Path:"https://a/sites/Y*"' in kql
    assert " OR " in kql


def test_build_onprem_kql_rejects_empty_query():
    with pytest.raises(ConnectorAPIError):
        build_onprem_kql("", ["https://sp2019.internal/sites/Legal"])


def test_build_onprem_kql_rejects_no_sites():
    with pytest.raises(ConnectorAPIError):
        build_onprem_kql("memo", [])


# -- _extract_onprem_rows (defensive parsing) ------------------------------------


def test_extract_onprem_rows_well_formed():
    rows = _extract_onprem_rows(ONPREM_PAYLOAD)
    assert len(rows) == 1
    assert rows[0]["Title"] == "Legal Memo"
    assert rows[0]["Path"] == "https://sp2019.internal/sites/Legal/memo.docx"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"d": {}},
        {"d": {"query": {}}},
        {"d": {"query": {"PrimaryQueryResult": {"RelevantResults": {"Table": {}}}}}},
        None,
        "not-a-dict",
    ],
)
def test_extract_onprem_rows_degrades_gracefully_on_malformed_payload(payload):
    assert _extract_onprem_rows(payload) == []


def test_extract_onprem_rows_skips_cells_with_no_key():
    payload = {
        "d": {
            "query": {
                "PrimaryQueryResult": {
                    "RelevantResults": {
                        "Table": {"Rows": {"results": [{"Cells": {"results": [{"Value": "no key here"}]}}]}}
                    }
                }
            }
        }
    }
    assert _extract_onprem_rows(payload) == []


# -- SharePointServerClient: auth session construction ---------------------------


def test_ntlm_auth_uses_httpntlmauth_with_username_and_secret(monkeypatch):
    from requests_ntlm import HttpNtlmAuth

    site = _server_site(monkeypatch, secret="hunter2", auth_method="ntlm")
    fake_response = _FakeResponse(200, ONPREM_PAYLOAD)
    # Construct with no injected session -- exercises the real
    # `_build_session` codepath, including the real `requests.Session`
    # and real `HttpNtlmAuth` classes.
    client = SharePointServerClient(site)
    assert isinstance(client._session.auth, HttpNtlmAuth)
    assert client._session.auth.username == "CONTOSO\\svc-mcp-connectors"
    assert client._session.auth.password == "hunter2"


def test_basic_auth_uses_plain_username_password_tuple(monkeypatch):
    site = _server_site(monkeypatch, secret="hunter2", auth_method="basic")
    client = SharePointServerClient(site)
    assert client._session.auth == ("jdoe", "hunter2")


def test_ntlm_import_guard_raises_clear_connector_auth_error(monkeypatch):
    import mcp_connectors.sharepoint.onprem_client as onprem_module

    monkeypatch.setattr(onprem_module, "_NTLM_IMPORT_ERROR", ImportError("no module named requests_ntlm"))
    site = _server_site(monkeypatch, auth_method="ntlm")
    with pytest.raises(ConnectorAuthError) as excinfo:
        SharePointServerClient(site)
    assert "requests_ntlm" in str(excinfo.value)
    assert "sharepoint" in str(excinfo.value)


def test_requests_import_guard_raises_clear_connector_auth_error(monkeypatch):
    import mcp_connectors.sharepoint.onprem_client as onprem_module

    monkeypatch.setattr(onprem_module, "_REQUESTS_IMPORT_ERROR", ImportError("no module named requests"))
    site = _server_site(monkeypatch, auth_method="basic")
    with pytest.raises(ConnectorAuthError):
        SharePointServerClient(site)


# -- SharePointServerClient.search()/fetch() end to end --------------------------


def test_search_hits_onprem_search_endpoint_with_scoped_querytext(monkeypatch):
    site = _server_site(monkeypatch)
    fake_response = _FakeResponse(200, ONPREM_PAYLOAD)
    session = _FakeSession(fake_response)
    client = SharePointServerClient(site, session=session)

    docs = client.search("memo", limit=10)

    call = session.calls[0]
    assert call["url"] == f"{site.site_url}{ONPREM_SEARCH_PATH}"
    assert "(memo)" in call["params"]["querytext"]
    assert f'Path:"{site.site_url}*"' in call["params"]["querytext"]
    assert call["params"]["rowlimit"] == 10

    doc = docs[0]
    assert doc.title == "Legal Memo"
    assert doc.id == "https://sp2019.internal/sites/Legal/memo.docx"
    assert doc.container == site.site_url
    assert doc.metadata["unique_id"] == "guid-123"


def test_fetch_scopes_by_exact_path_and_site(monkeypatch):
    site = _server_site(monkeypatch)
    session = _FakeSession(_FakeResponse(200, ONPREM_PAYLOAD))
    client = SharePointServerClient(site, session=session)

    doc = client.fetch("https://sp2019.internal/sites/Legal/memo.docx")

    call = session.calls[0]
    assert 'Path:"https://sp2019.internal/sites/Legal/memo.docx"' in call["params"]["querytext"]
    assert f'Path:"{site.site_url}*"' in call["params"]["querytext"]
    assert doc.title == "Legal Memo"


def test_fetch_raises_when_nothing_found(monkeypatch):
    site = _server_site(monkeypatch)
    empty_payload = {
        "d": {"query": {"PrimaryQueryResult": {"RelevantResults": {"Table": {"Rows": {"results": []}}}}}}
    }
    session = _FakeSession(_FakeResponse(200, empty_payload))
    client = SharePointServerClient(site, session=session)

    with pytest.raises(ConnectorAPIError):
        client.fetch("https://sp2019.internal/sites/Legal/missing.docx")


def test_fetch_rejects_empty_path(monkeypatch):
    site = _server_site(monkeypatch)
    session = _FakeSession(_FakeResponse(200, ONPREM_PAYLOAD))
    client = SharePointServerClient(site, session=session)

    with pytest.raises(ConnectorAPIError):
        client.fetch("   ")


def test_search_raises_connector_api_error_on_http_failure(monkeypatch):
    site = _server_site(monkeypatch)
    session = _FakeSession(_FakeResponse(500, text="internal server error"))
    client = SharePointServerClient(site, session=session)

    with pytest.raises(ConnectorAPIError):
        client.search("memo", limit=10)


def test_search_raises_on_malformed_json(monkeypatch):
    site = _server_site(monkeypatch)
    session = _FakeSession(_FakeResponse(200, payload=None, text="not json"))
    client = SharePointServerClient(site, session=session)

    with pytest.raises(ConnectorAPIError):
        client.search("memo", limit=10)
