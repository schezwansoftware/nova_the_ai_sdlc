"""SharePoint connector config: the list of configured sites (each one
independently either `"online"` or `"server"`), and the result cap. One
JSON file, `<connectors_config_dir()>/sharepoint.json` -- see
`mcp_connectors/common.py`'s module docstring for the config-file
convention.

## `deployment_type` is explicit per site, never auto-detected

The approved design is explicit about this and it's worth repeating here
because it's easy to get tempted otherwise: SharePoint Online and Server
are **two disjoint client implementations** (Graph Search API + Azure AD
vs. `_api/search/query` REST + NTLM/Kerberos/ADFS -- see
`online_client.py`/`onprem_client.py`), and there is no reliable way to
tell them apart from the URL alone. Vanity/custom domains exist on
Online (a tenant can front `*.sharepoint.com` with its own domain);
hybrid, ADFS-joined Server deployments can look superficially
cloud-adjacent too. Guessing wrong here wouldn't just fail loudly -- an
NTLM-shaped credential sent to Graph, or an Azure AD token sent to an
on-prem farm, fails in confusing ways far from the actual mistake. So
every `SharePointSiteConfig` entry says which backend it is,
un-guessably, via a required `deployment_type` field -- modeled as a
Pydantic discriminated union (`SharePointOnlineSiteConfig` |
`SharePointServerSiteConfig`) so an invalid/missing `deployment_type`
fails config validation immediately, not partway through a request.

## Each configured site *is* the allowlist unit

Unlike Jira/Confluence (one site, many allowlisted projects/spaces
within it), a SharePoint connector's allowlist unit is the site itself
-- `SharePointConnectorConfig.sites` *is* the allowlist (see
`client.py`'s facade, which calls `enforce_allowlist` against
`[s.site_url for s in sites]`). Each site also carries its own
independent auth (a different Online tenant, or an entirely different
on-prem farm, may be configured side by side in one connector), so
"configured" and "allowed" are the same list here by construction --
there's no separate broader "site the credentials could reach" the
allowlist narrows down from, the way a Jira PAT might technically reach
projects outside `allowed_projects`.

## Provisioning a config file

```json
{
  "sites": [
    {
      "deployment_type": "online",
      "site_url": "https://contoso.sharepoint.com/sites/Finance",
      "tenant_id": "11111111-2222-3333-4444-555555555555",
      "client_id": "66666666-7777-8888-9999-aaaaaaaaaaaa",
      "client_credential": {"service": "ai-sdlc-mcp-sharepoint", "username": "finance-online-client-secret"}
    },
    {
      "deployment_type": "server",
      "site_url": "https://sp2019.internal.contoso.com/sites/Legal",
      "auth_method": "ntlm",
      "username": "CONTOSO\\\\svc-ai-sdlc",
      "credential": {"service": "ai-sdlc-mcp-sharepoint", "username": "CONTOSO\\\\svc-ai-sdlc"}
    }
  ],
  "result_limit": 15
}
```

with the matching credentials stored once via
`mcp_connectors.common.store_secret` (see `jira/config.py`'s docstring
for the exact one-line invocation pattern) -- the Azure AD app's client
secret for the Online site, the domain account's password for the
on-prem NTLM site.
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, List, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_sdlc.mcp_connectors.common import (
    DEFAULT_RESULT_LIMIT,
    MAX_RESULT_LIMIT,
    ConnectorConfigError,
    CredentialRef,
    connectors_config_dir,
)

CONFIG_FILE_NAME = "sharepoint.json"


class SharePointOnlineSiteConfig(BaseModel):
    """A SharePoint **Online** site -- Microsoft Graph Search API,
    Azure AD app registration (client-credentials OAuth2 flow, no
    interactive user -- this is a headless server). See
    `online_client.py`'s module docstring for the full auth/query
    account."""

    model_config = ConfigDict(extra="forbid")

    deployment_type: Literal["online"] = "online"
    site_url: str
    tenant_id: str
    client_id: str
    #: The Azure AD app registration's client secret.
    client_credential: CredentialRef

    @field_validator("site_url")
    @classmethod
    def _validate_site_url(cls, value: str) -> str:
        value = (value or "").strip()
        if not value.startswith(("http://", "https://")):
            raise ValueError(f"site_url {value!r} must be an absolute http(s) URL")
        return value.rstrip("/")


class SharePointServerSiteConfig(BaseModel):
    """A SharePoint **Server** (on-prem) site -- classic `_api/search/
    query` REST, NTLM or Basic auth (Kerberos/ADFS are real Server
    on-prem auth models too, but not implemented in this pass -- see
    `onprem_client.py`'s module docstring for the honest account of
    what is and isn't built here)."""

    model_config = ConfigDict(extra="forbid")

    deployment_type: Literal["server"] = "server"
    site_url: str
    auth_method: Literal["ntlm", "basic"] = "ntlm"
    #: `"DOMAIN\\username"` for NTLM (or a plain username, if the
    #: on-prem farm's NTLM provider resolves it against a default
    #: domain); a plain username for `"basic"` (e.g. an ADFS-fronted or
    #: basic-auth-enabled on-prem deployment).
    username: str
    credential: CredentialRef

    @field_validator("site_url")
    @classmethod
    def _validate_site_url(cls, value: str) -> str:
        value = (value or "").strip()
        if not value.startswith(("http://", "https://")):
            raise ValueError(f"site_url {value!r} must be an absolute http(s) URL")
        return value.rstrip("/")


SharePointSiteConfig = Annotated[
    Union[SharePointOnlineSiteConfig, SharePointServerSiteConfig],
    Field(discriminator="deployment_type"),
]


class SharePointConnectorConfig(BaseModel):
    """This connector's full config: the list of configured sites (each
    independently Online or Server -- see module docstring), and the
    search result cap."""

    model_config = ConfigDict(extra="forbid")

    sites: List[SharePointSiteConfig] = Field(min_length=1)
    result_limit: int = Field(default=DEFAULT_RESULT_LIMIT, ge=1, le=MAX_RESULT_LIMIT)

    @field_validator("sites")
    @classmethod
    def _validate_unique_site_urls(cls, value: List) -> List:
        seen = set()
        for site in value:
            if site.site_url in seen:
                raise ValueError(f"duplicate site_url in config: {site.site_url!r}")
            seen.add(site.site_url)
        return value


def config_path() -> Path:
    return connectors_config_dir() / CONFIG_FILE_NAME


def load_config() -> SharePointConnectorConfig:
    path = config_path()
    if not path.exists():
        raise ConnectorConfigError(
            f"no SharePoint connector config found at {path}. Create one first -- "
            "see this module's docstring for the expected JSON shape -- or set "
            "AI_SDLC_MCP_CONFIG_DIR to a directory containing sharepoint.json."
        )
    return SharePointConnectorConfig.model_validate_json(path.read_text(encoding="utf-8"))


def save_config(config: SharePointConnectorConfig) -> None:
    directory = connectors_config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    config_path().write_text(config.model_dump_json(indent=2) + "\n", encoding="utf-8")
