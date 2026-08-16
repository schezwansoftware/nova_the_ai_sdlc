"""Tests for `mcp_connectors/atlassian/auth.py`: the site-config model
shared by Jira/Confluence (deployment-type/auth-method cross-validation)
and the three-auth-method header construction (`build_auth_headers`).

No network calls -- `build_http_client`'s tests use `httpx.MockTransport`
(a fake transport, not a fake client class), the same "exercise the real
`httpx.Client` construction/request logic against an injected fake
transport" pattern used throughout this package's tests.
"""
from __future__ import annotations

import base64

import httpx
import pytest
from pydantic import ValidationError

from mcp_connectors.atlassian.auth import (
    AtlassianAuthMethod,
    AtlassianDeploymentType,
    AtlassianSiteConfig,
    build_auth_headers,
    build_http_client,
)
from mcp_connectors.common import ConnectorConfigError, CredentialRef


def _credential(monkeypatch, secret: str = "the-secret") -> CredentialRef:
    ref = CredentialRef(service="svc", username="user")
    monkeypatch.setattr(CredentialRef, "resolve", lambda self: secret)
    return ref


# -- AtlassianSiteConfig validation -------------------------------------------


def test_cloud_api_token_is_valid_for_cloud_deployment():
    site = AtlassianSiteConfig(
        base_url="https://example.atlassian.net",
        deployment_type="cloud",
        auth_method="cloud_api_token",
        account_identifier="bot@example.com",
        credential=CredentialRef(service="svc", username="user"),
    )
    assert site.deployment_type == AtlassianDeploymentType.CLOUD


@pytest.mark.parametrize("auth_method", ["data_center_pat", "data_center_basic"])
def test_data_center_methods_are_valid_for_data_center_deployment(auth_method):
    kwargs = dict(
        base_url="https://jira.internal.corp",
        deployment_type="data_center",
        auth_method=auth_method,
        credential=CredentialRef(service="svc", username="user"),
    )
    if auth_method == "data_center_basic":
        kwargs["account_identifier"] = "jdoe"
    site = AtlassianSiteConfig(**kwargs)
    assert site.deployment_type == AtlassianDeploymentType.DATA_CENTER


def test_cloud_api_token_rejected_for_data_center_deployment():
    with pytest.raises(ValidationError):
        AtlassianSiteConfig(
            base_url="https://jira.internal.corp",
            deployment_type="data_center",
            auth_method="cloud_api_token",
            account_identifier="bot@example.com",
            credential=CredentialRef(service="svc", username="user"),
        )


def test_data_center_pat_rejected_for_cloud_deployment():
    with pytest.raises(ValidationError):
        AtlassianSiteConfig(
            base_url="https://example.atlassian.net",
            deployment_type="cloud",
            auth_method="data_center_pat",
            credential=CredentialRef(service="svc", username="user"),
        )


def test_cloud_api_token_requires_account_identifier():
    with pytest.raises(ValidationError):
        AtlassianSiteConfig(
            base_url="https://example.atlassian.net",
            deployment_type="cloud",
            auth_method="cloud_api_token",
            credential=CredentialRef(service="svc", username="user"),
        )


def test_data_center_pat_rejects_account_identifier():
    """A PAT identifies the account by itself -- supplying
    account_identifier alongside it is a config mistake to catch, not a
    harmless extra field."""
    with pytest.raises(ValidationError):
        AtlassianSiteConfig(
            base_url="https://jira.internal.corp",
            deployment_type="data_center",
            auth_method="data_center_pat",
            account_identifier="jdoe",
            credential=CredentialRef(service="svc", username="user"),
        )


def test_base_url_must_be_absolute_http_url():
    with pytest.raises(ValidationError):
        AtlassianSiteConfig(
            base_url="example.atlassian.net",
            deployment_type="cloud",
            auth_method="cloud_api_token",
            account_identifier="bot@example.com",
            credential=CredentialRef(service="svc", username="user"),
        )


def test_base_url_trailing_slash_is_stripped():
    site = AtlassianSiteConfig(
        base_url="https://example.atlassian.net/",
        deployment_type="cloud",
        auth_method="cloud_api_token",
        account_identifier="bot@example.com",
        credential=CredentialRef(service="svc", username="user"),
    )
    assert site.base_url == "https://example.atlassian.net"


# -- build_auth_headers ----------------------------------------------------------


def test_cloud_api_token_produces_basic_auth_of_email_and_token(monkeypatch):
    site = AtlassianSiteConfig(
        base_url="https://example.atlassian.net",
        deployment_type="cloud",
        auth_method="cloud_api_token",
        account_identifier="bot@example.com",
        credential=_credential(monkeypatch, "my-api-token"),
    )
    headers = build_auth_headers(site)
    expected = base64.b64encode(b"bot@example.com:my-api-token").decode("ascii")
    assert headers == {"Authorization": f"Basic {expected}"}


def test_data_center_pat_produces_bearer_auth(monkeypatch):
    site = AtlassianSiteConfig(
        base_url="https://jira.internal.corp",
        deployment_type="data_center",
        auth_method="data_center_pat",
        credential=_credential(monkeypatch, "my-pat"),
    )
    headers = build_auth_headers(site)
    assert headers == {"Authorization": "Bearer my-pat"}


def test_data_center_basic_produces_basic_auth_of_username_and_password(monkeypatch):
    site = AtlassianSiteConfig(
        base_url="https://jira.internal.corp",
        deployment_type="data_center",
        auth_method="data_center_basic",
        account_identifier="jdoe",
        credential=_credential(monkeypatch, "hunter2"),
    )
    headers = build_auth_headers(site)
    expected = base64.b64encode(b"jdoe:hunter2").decode("ascii")
    assert headers == {"Authorization": f"Basic {expected}"}


# -- build_http_client --------------------------------------------------------


def test_build_http_client_applies_auth_and_accept_headers(monkeypatch):
    site = AtlassianSiteConfig(
        base_url="https://example.atlassian.net",
        deployment_type="cloud",
        auth_method="cloud_api_token",
        account_identifier="bot@example.com",
        credential=_credential(monkeypatch, "tok"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    client = build_http_client(site, transport=httpx.MockTransport(handler))
    assert client.headers["accept"] == "application/json"
    assert "authorization" in client.headers
    assert str(client.base_url).rstrip("/") == site.base_url


def test_build_http_client_requests_go_to_configured_base_url(monkeypatch):
    site = AtlassianSiteConfig(
        base_url="https://example.atlassian.net",
        deployment_type="cloud",
        auth_method="cloud_api_token",
        account_identifier="bot@example.com",
        credential=_credential(monkeypatch, "tok"),
    )
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True})

    client = build_http_client(site, transport=httpx.MockTransport(handler))
    client.get("/rest/api/2/myself")
    assert seen["url"] == "https://example.atlassian.net/rest/api/2/myself"
