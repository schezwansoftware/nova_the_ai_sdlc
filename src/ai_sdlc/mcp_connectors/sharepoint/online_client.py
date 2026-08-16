"""SharePoint Online search/fetch client: Microsoft Graph Search API
(`POST /search/query`, `entityTypes: ["driveItem"]`), Azure AD
client-credentials OAuth2 for auth, `Path:"..."` KQL clauses for native
site scoping.

## Auth: Azure AD app registration, client-credentials flow

A standalone MCP server is a headless process with no interactive user
to complete a delegated-permissions sign-in, so this client uses the
OAuth2 **client-credentials** grant against Azure AD's v2 token endpoint
(`POST https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token`,
`grant_type=client_credentials`, `scope=https://graph.microsoft.com/
.default`) -- the standard shape for an unattended app-only Graph
caller, requiring an Azure AD app registration with application (not
delegated) Graph permissions (e.g. `Sites.Read.All`) granted admin
consent. The resulting bearer token is cached in memory
(`_access_token`) and refreshed a little before its documented
`expires_in`, not re-requested on every call.

## Query-time scoping: Graph Search's `Path:` managed property

Verified via live web search on 2026-08-16 against Microsoft Learn's own
Graph Search examples (`learn.microsoft.com/en-us/graph/search-concept-
files`, `.../answers/questions/976219/...`): a driveItem search can be
scoped to one or more specific sites by ANDing/ORing `Path:"<site
url>"` clauses into the KQL `queryString`, e.g. `"sample AND
(Path:https://xyz.sharepoint.com/sites/sitename1 OR
Path:https://xyz.sharepoint.com/sites/sitename2)"`. `build_graph_query_string`
below does exactly this -- the query-time half of the precision
requirement for this backend, matching the approved design's "Graph
Search API query scoped to the specific site/drive resource" wording.

## Fetch-by-id: a direct resource GET, not a search -- the one deliberate exception

`fetch()` resolves a `(drive_id, item_id)` pair via `GET
/drives/{drive_id}/items/{item_id}` -- Graph's normal way to retrieve a
*known* driveItem by its own id -- rather than a KQL search, because
Graph Search's `queryString` has no reliable, documented "match this
exact item id" clause the way Jira's JQL has `issueKey = "..."` or
Confluence's CQL has `id = ...` (see those connectors' `client.py` for
the contrast). This is a genuinely different situation from "fetch
broadly then filter" -- it's "fetch one already-known, specific resource
by its own id" -- but scope still has to be enforced somehow, so this
client verifies the returned item's `webUrl` is prefixed by the expected
site's `site_url` *after* the fetch (`_verify_item_in_scope`), refusing
to return it otherwise. Flagged explicitly as the one place in this
whole package where enforcement is "fetch a specific known resource,
then verify" rather than "scope baked into the query itself" -- and
explained above as a structural property of the Graph API surface, not
a shortcut taken for convenience.

## Not independently verified against a live tenant

No Azure AD app registration or SharePoint Online tenant were available
to exercise this end-to-end. Graph response-JSON field access
(`_hit_to_document`/`_item_to_document`) is defensive throughout,
mirroring every other client in this package.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from ai_sdlc.mcp_connectors.common import ConnectorAPIError, ConnectorAuthError, Document
from ai_sdlc.mcp_connectors.sharepoint.config import SharePointOnlineSiteConfig

try:
    import httpx as _httpx

    _HTTPX_IMPORT_ERROR: Optional[BaseException] = None
except ImportError as exc:  # pragma: no cover - see atlassian/auth.py's
    # identical guard for the `httpx` optional-extra-dependency story.
    _httpx = None
    _HTTPX_IMPORT_ERROR = exc

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
GRAPH_SEARCH_PATH = "/search/query"
GRAPH_TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"

#: How much earlier than the token's own reported `expires_in` this
#: client refreshes it -- a safety margin against clock skew/request
#: latency, not a measured constant.
_TOKEN_REFRESH_MARGIN_SECONDS = 60.0


def build_graph_query_string(query: str, site_urls: List[str]) -> str:
    """Build the KQL `queryString` carrying both the caller's free-text
    query and the allowlist-scoped `Path:` site restriction -- see
    module docstring's "Query-time scoping" section. `site_urls` must
    already be allowlist-validated (`enforce_allowlist` output, applied
    by the facade in `client.py` -- this function itself performs no
    allowlist check, matching `jira.build_jql`'s identical division of
    responsibility)."""
    query = (query or "").strip()
    if not query:
        raise ConnectorAPIError("query text must not be empty")
    if not site_urls:
        raise ConnectorAPIError("at least one site must be specified")

    escaped_query = query.replace('"', '\\"')
    path_clauses = " OR ".join(f'Path:"{url}"' for url in site_urls)
    return f'({escaped_query}) AND ({path_clauses})'


def _parse_graph_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _extract_graph_hits(payload: Any) -> List[Dict[str, Any]]:
    """Defensive walk down Graph Search's documented response shape:
    `value[0].hitsContainers[0].hits[*].resource`. Any missing/malformed
    level degrades to an empty result list rather than raising -- a
    connector returning "no results" for a response shape it didn't
    expect is a safer failure mode than crashing the whole tool call."""
    try:
        value = payload["value"]
        hits_containers = value[0]["hitsContainers"]
        hits = hits_containers[0].get("hits", [])
    except (KeyError, IndexError, TypeError):
        return []

    resources = []
    for hit in hits or []:
        resource = hit.get("resource") if isinstance(hit, dict) else None
        if isinstance(resource, dict):
            # Graph Search's hit-level highlighted snippet lives beside
            # (not inside) `resource` -- stash it on the dict we return
            # so `_hit_to_document` can still find it in one place.
            resource = dict(resource)
            resource["_summary"] = hit.get("summary")
            resources.append(resource)
    return resources


class SharePointOnlineClient:
    """Search/fetch against one SharePoint Online site via Microsoft
    Graph. `http_client`/`token_client` are test seams (mirror every
    other client's `http_client` parameter in this package)."""

    def __init__(
        self,
        site: SharePointOnlineSiteConfig,
        *,
        http_client: Any = None,
        token_client: Any = None,
        timeout: float = 30.0,
    ) -> None:
        if _HTTPX_IMPORT_ERROR is not None:
            raise ConnectorAuthError(
                "the `httpx` package is not usable in this environment "
                f"({_HTTPX_IMPORT_ERROR!r}); install it (it's included in the "
                "`sharepoint` extra -- `pip install ai-sdlc[sharepoint]`)."
            )
        self._site = site
        self._client = http_client if http_client is not None else _httpx.Client(
            base_url=GRAPH_BASE_URL, timeout=timeout
        )
        self._token_client = token_client if token_client is not None else _httpx.Client(timeout=timeout)
        self._cached_token: Optional[str] = None
        self._cached_token_expiry: float = 0.0

    def search(self, query: str, *, limit: int) -> List[Document]:
        query_string = build_graph_query_string(query, [self._site.site_url])
        headers = {"Authorization": f"Bearer {self._access_token()}", "Content-Type": "application/json"}
        body = {
            "requests": [
                {
                    "entityTypes": ["driveItem"],
                    "query": {"queryString": query_string},
                    "from": 0,
                    "size": limit,
                }
            ]
        }
        try:
            response = self._client.post(GRAPH_SEARCH_PATH, headers=headers, json=body)
        except Exception as exc:  # noqa: BLE001 - network/transport failure
            raise ConnectorAPIError(f"Graph Search request failed: {exc}") from exc

        if response.status_code >= 400:
            raise ConnectorAPIError(
                f"Graph Search request failed with HTTP {response.status_code}: {response.text[:500]}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ConnectorAPIError(f"Graph Search response was not valid JSON: {exc}") from exc

        return [self._hit_to_document(hit) for hit in _extract_graph_hits(payload)]

    def fetch(self, drive_id: str, item_id: str) -> Document:
        """See module docstring's "Fetch-by-id" section for why this is
        a direct resource GET, not a search, and why scope is verified
        after the fact rather than baked into a query."""
        headers = {"Authorization": f"Bearer {self._access_token()}"}
        params = {"$select": "id,name,webUrl,lastModifiedDateTime,parentReference,size"}
        try:
            response = self._client.get(f"/drives/{drive_id}/items/{item_id}", headers=headers, params=params)
        except Exception as exc:  # noqa: BLE001
            raise ConnectorAPIError(f"Graph driveItem fetch failed: {exc}") from exc

        if response.status_code >= 400:
            raise ConnectorAPIError(
                f"Graph driveItem fetch failed with HTTP {response.status_code}: {response.text[:500]}"
            )
        try:
            item = response.json()
        except ValueError as exc:
            raise ConnectorAPIError(f"Graph driveItem response was not valid JSON: {exc}") from exc

        self._verify_item_in_scope(item)
        return self._item_to_document(item, drive_id=drive_id)

    # -- auth -----------------------------------------------------------------

    def _access_token(self) -> str:
        now = time.monotonic()
        if self._cached_token and now < self._cached_token_expiry - _TOKEN_REFRESH_MARGIN_SECONDS:
            return self._cached_token

        secret = self._site.client_credential.resolve()
        token_url = GRAPH_TOKEN_URL_TEMPLATE.format(tenant_id=self._site.tenant_id)
        try:
            response = self._token_client.post(
                token_url,
                data={
                    "client_id": self._site.client_id,
                    "client_secret": secret,
                    "scope": GRAPH_SCOPE,
                    "grant_type": "client_credentials",
                },
            )
        except Exception as exc:  # noqa: BLE001
            raise ConnectorAuthError(f"Azure AD token request failed: {exc}") from exc

        if response.status_code >= 400:
            raise ConnectorAuthError(
                f"Azure AD token request failed with HTTP {response.status_code}: {response.text[:500]}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ConnectorAuthError(f"Azure AD token response was not valid JSON: {exc}") from exc

        token = payload.get("access_token")
        if not token:
            raise ConnectorAuthError("Azure AD token response did not contain an access_token")

        self._cached_token = token
        self._cached_token_expiry = now + float(payload.get("expires_in", 3599))
        return token

    # -- response parsing -------------------------------------------------------

    def _verify_item_in_scope(self, item: Dict[str, Any]) -> None:
        web_url = item.get("webUrl") or ""
        if not web_url.startswith(self._site.site_url):
            raise ConnectorAPIError(
                f"fetched driveItem's webUrl ({web_url!r}) is not under this "
                f"connector's configured site ({self._site.site_url!r}) -- refusing "
                "to return content outside the configured scope"
            )

    def _hit_to_document(self, resource: Dict[str, Any]) -> Document:
        item_id = resource.get("id") or ""
        parent_ref = resource.get("parentReference") or {}
        drive_id = parent_ref.get("driveId") or ""
        name = resource.get("name") or "(untitled)"
        summary = resource.get("_summary") or ""

        composite_id = f"{drive_id}:{item_id}" if drive_id and item_id else item_id

        return Document(
            id=composite_id,
            title=name,
            snippet=summary[:2000],
            source="sharepoint",
            url=resource.get("webUrl"),
            last_modified=_parse_graph_datetime(resource.get("lastModifiedDateTime")),
            container=self._site.site_url,
            metadata={"drive_id": drive_id, "size": resource.get("size")},
        )

    def _item_to_document(self, item: Dict[str, Any], *, drive_id: str) -> Document:
        item_id = item.get("id") or ""
        name = item.get("name") or "(untitled)"
        composite_id = f"{drive_id}:{item_id}" if drive_id and item_id else item_id

        return Document(
            id=composite_id,
            title=name,
            snippet="",
            source="sharepoint",
            url=item.get("webUrl"),
            last_modified=_parse_graph_datetime(item.get("lastModifiedDateTime")),
            container=self._site.site_url,
            metadata={"drive_id": drive_id, "size": item.get("size")},
        )
