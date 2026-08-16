"""Shared Atlassian site config and auth-header construction for Jira and
Confluence -- both connectors' `client.py` build on this instead of each
re-deriving the same three-auth-method logic against the same product
family.

## Deployment types and auth methods (approved design)

Jira and Confluence must both support Atlassian **Cloud** and **Data
Center** (self-hosted). The base URL is always caller-configured
(`AtlassianSiteConfig.base_url`, never hardcoded to `*.atlassian.net` or
validated against that pattern -- a Data Center site's URL looks nothing
like it). Per the approved design, JQL/CQL project/space scoping syntax
is identical across both deployment types, so query-construction logic
doesn't need to fork on it -- only base URL and auth do (Jira's search
*endpoint path* turns out to need a deployment-type fork too, for a
reason unrelated to auth -- see `jira/client.py`'s module docstring for
that one, separately-flagged exception).

Three auth methods, one underlying `Authorization` header mechanic each
(`Basic <base64>` or `Bearer <token>`), so one function
(`build_auth_headers`) covers all three:

  - **`cloud_api_token`** (Cloud): HTTP Basic, `base64("<email>:<api
    token>")`. The token half is an Atlassian API token
    (id.atlassian.com/manage-profile/security/api-tokens), never the
    account's real password.
  - **`data_center_pat`** (Data Center 8.14+ for Jira / 7.9+ for
    Confluence): `Authorization: Bearer <PAT>` -- no separate
    email/username needed, the token itself identifies the account. The
    simplest of the three, deliberately mirroring Cloud's simplicity per
    the approved design.
  - **`data_center_basic`** (older Data Center, below those version
    floors, no PAT support): HTTP Basic, `base64("<username>:<password>")`
    -- the account's real login password. Named distinctly from
    `cloud_api_token` even though the wire mechanics are identical,
    because what's in the credential differs (a real password vs. a
    scoped API token) and a config file should say which one it is
    rather than leave that ambiguous.

## Not independently verified against a live tenant

No Jira/Confluence Cloud or Data Center tenant/credentials were available
to exercise this against. The three auth shapes above and the Data
Center PAT version floors (8.14+/7.9+) come from the approved design
brief, which this module implements as specified rather than re-deriving
-- see `mcp_connectors/__init__.py`'s module docstring for this
package's general "no live credentials" posture. What *is* independently
checked: `httpx` (verified installed, `httpx==0.28.1`) is a real,
importable HTTP client whose `Client(base_url=..., headers=...,
transport=...)` constructor shape this module uses is exercised directly
in this repo's own test suite via `httpx.MockTransport` (no network
call), not merely assumed.
"""
from __future__ import annotations

import base64
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from ai_sdlc.mcp_connectors.common import ConnectorConfigError, CredentialRef

try:
    import httpx as _httpx

    _HTTPX_IMPORT_ERROR: Optional[BaseException] = None
except ImportError as exc:  # pragma: no cover - exercised only without
    # the `jira`/`confluence` extras (both pull in `httpx`) installed.
    # Deferred to `build_http_client` call time, not module import time,
    # mirroring `mcp_connectors/common.py`'s `keyring` import guard.
    _httpx = None
    _HTTPX_IMPORT_ERROR = exc


class AtlassianDeploymentType(str, Enum):
    CLOUD = "cloud"
    DATA_CENTER = "data_center"


class AtlassianAuthMethod(str, Enum):
    CLOUD_API_TOKEN = "cloud_api_token"
    DATA_CENTER_PAT = "data_center_pat"
    DATA_CENTER_BASIC = "data_center_basic"


#: Which `AtlassianAuthMethod`s are valid for which `AtlassianDeploymentType`
#: -- enforced by `AtlassianSiteConfig`'s own validator below, so an
#: invalid pairing (e.g. `cloud_api_token` against `data_center`) is
#: rejected at config-load time, not discovered later as a confusing
#: auth failure against the wrong endpoint shape.
_VALID_AUTH_METHODS_BY_DEPLOYMENT = {
    AtlassianDeploymentType.CLOUD: {AtlassianAuthMethod.CLOUD_API_TOKEN},
    AtlassianDeploymentType.DATA_CENTER: {
        AtlassianAuthMethod.DATA_CENTER_PAT,
        AtlassianAuthMethod.DATA_CENTER_BASIC,
    },
}

#: Auth methods that require `AtlassianSiteConfig.account_identifier`
#: (the email, for Cloud; the username, for older Data Center Basic auth)
#: alongside the keyring-stored secret -- a PAT identifies the account by
#: itself, so `data_center_pat` is deliberately absent from this set.
_AUTH_METHODS_REQUIRING_ACCOUNT_IDENTIFIER = {
    AtlassianAuthMethod.CLOUD_API_TOKEN,
    AtlassianAuthMethod.DATA_CENTER_BASIC,
}


class AtlassianSiteConfig(BaseModel):
    """The Jira/Confluence site this connector talks to: where it is
    (`base_url`, `deployment_type`) and how to authenticate to it
    (`auth_method`, `account_identifier`, `credential`). One instance of
    this per connector config (a connector talks to exactly one Jira or
    Confluence site, scoped further by that connector's own
    project/space allowlist -- see `jira/config.py`/`confluence/
    config.py`)."""

    model_config = ConfigDict(extra="forbid")

    base_url: str
    deployment_type: AtlassianDeploymentType
    auth_method: AtlassianAuthMethod
    #: Email (Cloud) or username (older Data Center Basic auth).
    #: `None` for `data_center_pat`, where the token alone identifies
    #: the account -- see `_AUTH_METHODS_REQUIRING_ACCOUNT_IDENTIFIER`.
    account_identifier: Optional[str] = None
    #: Where the API token / PAT / password lives in the OS keyring --
    #: never the secret itself (see `common.CredentialRef`).
    credential: CredentialRef

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        value = (value or "").strip()
        if not value.startswith(("http://", "https://")):
            raise ValueError(
                f"base_url {value!r} must be an absolute http(s) URL (e.g. "
                "'https://yourorg.atlassian.net' for Cloud, or "
                "'https://jira.yourcompany.internal' for Data Center)"
            )
        return value.rstrip("/")

    @model_validator(mode="after")
    def _validate_deployment_auth_pairing(self) -> "AtlassianSiteConfig":
        valid_methods = _VALID_AUTH_METHODS_BY_DEPLOYMENT[self.deployment_type]
        if self.auth_method not in valid_methods:
            raise ValueError(
                f"auth_method {self.auth_method.value!r} is not valid for "
                f"deployment_type {self.deployment_type.value!r}; expected one "
                f"of {sorted(m.value for m in valid_methods)!r}"
            )
        needs_identifier = self.auth_method in _AUTH_METHODS_REQUIRING_ACCOUNT_IDENTIFIER
        if needs_identifier and not self.account_identifier:
            raise ValueError(
                f"auth_method {self.auth_method.value!r} requires "
                "account_identifier to be set (the email for cloud_api_token, "
                "the username for data_center_basic)"
            )
        if not needs_identifier and self.account_identifier:
            raise ValueError(
                f"auth_method {self.auth_method.value!r} identifies the account "
                "via its token alone -- account_identifier must be left unset "
                "to avoid implying it's used"
            )
        return self


def build_auth_headers(site: AtlassianSiteConfig) -> dict:
    """Resolve `site.credential` from the OS keyring and build the
    `Authorization` header for `site.auth_method`. Never caches the
    resolved secret beyond the returned header dict -- callers construct
    a fresh `httpx.Client` (see `build_http_client`) per client instance,
    not per request, so this only runs once per connector-process
    lifetime in practice."""
    secret = site.credential.resolve()

    if site.auth_method == AtlassianAuthMethod.DATA_CENTER_PAT:
        return {"Authorization": f"Bearer {secret}"}

    # Both remaining methods (cloud_api_token, data_center_basic) are
    # HTTP Basic with an account_identifier -- guaranteed non-None here
    # by AtlassianSiteConfig's own validator.
    if site.auth_method in (AtlassianAuthMethod.CLOUD_API_TOKEN, AtlassianAuthMethod.DATA_CENTER_BASIC):
        token = base64.b64encode(f"{site.account_identifier}:{secret}".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {token}"}

    raise ConnectorConfigError(f"unknown Atlassian auth_method {site.auth_method!r}")  # pragma: no cover


def build_http_client(site: AtlassianSiteConfig, *, transport: Any = None, timeout: float = 30.0) -> Any:
    """Construct the `httpx.Client` a Jira/Confluence client talks
    through: `base_url` pinned to the site, auth header pre-applied.
    `transport` is the test seam (an `httpx.MockTransport` in tests --
    see `tests/test_mcp_connectors_atlassian_auth.py` -- real network
    transport when left `None`), mirroring this codebase's established
    injected-fake-client convention (`_query_fn`/`_options_cls` in
    `capabilities/providers/claude_sdk.py`, etc.)."""
    if _HTTPX_IMPORT_ERROR is not None:
        raise ConnectorConfigError(
            "the `httpx` package is not usable in this environment "
            f"({_HTTPX_IMPORT_ERROR!r}); install it (it's included in the "
            "`jira`/`confluence` extras -- `pip install ai-sdlc[jira]` etc.)."
        )
    headers = build_auth_headers(site)
    headers["Accept"] = "application/json"
    return _httpx.Client(base_url=site.base_url, headers=headers, timeout=timeout, transport=transport)
