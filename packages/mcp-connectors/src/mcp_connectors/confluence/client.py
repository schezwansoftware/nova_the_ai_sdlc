"""Confluence search/fetch client: CQL construction with hard space
scoping, defensive `SearchResult`-JSON parsing into `Document`.

## Search endpoint: `/rest/api/search`, appended to a caller-owned `base_url`

Verified via live web search on 2026-08-16 against Atlassian's own
current developer docs (`developer.atlassian.com/cloud/confluence/rest/
api-group-search/`, page timestamp "last updated August 11, 2026" at
fetch time -- about as current as documentation gets) and the Data
Center equivalent (`developer.atlassian.com/server/confluence/rest/
v9211/api-group-search/`): both Cloud and Data Center expose CQL search
at the same relative path, `/rest/api/search?cql=...`. Unlike Jira (see
`jira/client.py`'s module docstring for that connector's endpoint fork),
Confluence's search endpoint itself did **not** need a deployment-type
fork -- no equivalent migration-off-classic-search has happened here.

The one real difference between deployment types is Confluence Cloud's
convention of putting the whole REST API under a `/wiki` context path
(`https://yourorg.atlassian.net/wiki/rest/api/search`), which Data
Center sites typically don't have (or use a site-specific one). Rather
than this client guessing/stripping/inserting `/wiki` based on
`deployment_type` (fragile -- Data Center installs *can* be configured
with their own arbitrary context path), `AtlassianSiteConfig.base_url`
is simply expected to already be "whatever prefix, if any,
`/rest/api/search` should be appended to" for the site in question --
see `config.py`'s docstring for a concrete Cloud example including
`/wiki`. This keeps `_SEARCH_PATH` itself identical across both
deployment types, matching the approved design's "only base URL and
auth fork" framing exactly (contrast with Jira, where the fork turned
out to be real and is flagged as such).

## Fetch-by-id also goes through CQL, not a raw content-by-id GET

Same reasoning as `jira/client.py`'s `fetch()`: a direct `GET
/rest/api/content/{id}` has no space-scope query parameter at all, so
enforcing the allowlist around it would mean "fetch, then check" --
exactly what the precision requirement says to avoid. Confluence's CQL
supports an `id` clause (`id = 12345`), so `fetch()` instead runs `id =
<id> AND space in (<allowlist>)` through the same search codepath
`search()` uses, and treats zero matches as "not found or out of scope"
without distinguishing the two (a client that could tell the difference
would be leaking which content IDs exist outside its own allowlist,
which the precision requirement's spirit argues against).

## Not independently verified against a live tenant

No Confluence tenant/credentials were available to exercise any of this
end-to-end. `SearchResult` JSON field access (`_result_to_document`) is
defensive throughout, mirroring `jira/client.py`'s identical stance.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from mcp_connectors.atlassian.auth import build_http_client
from mcp_connectors.common import ConnectorAPIError, Document, enforce_allowlist
from mcp_connectors.confluence.config import ConfluenceConnectorConfig

#: Relative to `AtlassianSiteConfig.base_url` -- see module docstring for
#: why that's enough, with no deployment-type fork needed here.
CONFLUENCE_SEARCH_PATH = "/rest/api/search"


def build_cql(query: str, spaces: List[str]) -> str:
    """Build the CQL string carrying both the caller's free-text query
    and the allowlist-scoped space restriction -- the approved design's
    `space in ("KEY1","KEY2") AND text ~ "..."` shape exactly. `spaces`
    must already be allowlist-validated (`enforce_allowlist` output)."""
    query = (query or "").strip()
    if not query:
        raise ConnectorAPIError("query text must not be empty")
    if not spaces:
        raise ConnectorAPIError("at least one space must be specified")

    escaped_query = query.replace("\\", "\\\\").replace('"', '\\"')
    quoted_spaces = ", ".join(f'"{space}"' for space in spaces)
    space_clause = f"space in ({quoted_spaces})"
    text_clause = f'text ~ "{escaped_query}"'
    return f"{space_clause} AND {text_clause} order by lastmodified desc"


def build_cql_for_id(content_id: str, spaces: List[str]) -> str:
    """The `fetch()`-specific CQL shape -- see module docstring's
    "Fetch-by-id" section."""
    content_id = (content_id or "").strip()
    if not content_id:
        raise ConnectorAPIError("content id must not be empty")
    if not spaces:
        raise ConnectorAPIError("at least one space must be specified")
    quoted_spaces = ", ".join(f'"{space}"' for space in spaces)
    return f"id = {content_id} AND space in ({quoted_spaces})"


def _parse_confluence_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _extract_results(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        return [item for item in payload["results"] if isinstance(item, dict)]
    return []


class ConfluenceClient:
    """Search/fetch against one Confluence site, scoped to one
    connector's space allowlist. `http_client` is the test seam (mirrors
    `JiraClient`'s identical parameter)."""

    def __init__(self, config: ConfluenceConnectorConfig, *, http_client: Any = None) -> None:
        self._config = config
        self._client = http_client if http_client is not None else build_http_client(config.site)

    def search(self, query: str, spaces: Optional[List[str]] = None) -> List[Document]:
        allowed = enforce_allowlist(spaces or [], self._config.allowed_spaces, kind="space")
        cql = build_cql(query, allowed)
        results = self._run_search(cql, limit=self._config.result_limit)
        return [self._result_to_document(result) for result in results]

    def fetch(self, content_id: str) -> Document:
        """Fetch one piece of content by id, scoped through the same
        allowlisted CQL mechanism `search()` uses -- see module
        docstring's "Fetch-by-id" section for why."""
        content_id = (content_id or "").strip()
        if not content_id:
            raise ConnectorAPIError("content id must not be empty")
        allowed = list(self._config.allowed_spaces)
        cql = build_cql_for_id(content_id, allowed)
        results = self._run_search(cql, limit=1)
        if not results:
            raise ConnectorAPIError(
                f"no Confluence content found for id {content_id!r} within the "
                f"allowlisted space(s) {allowed} -- either it doesn't exist, or it "
                "exists outside this connector's configured scope"
            )
        return self._result_to_document(results[0])

    # -- request plumbing ---------------------------------------------------

    def _run_search(self, cql: str, limit: int) -> List[Dict[str, Any]]:
        try:
            response = self._client.get(CONFLUENCE_SEARCH_PATH, params={"cql": cql, "limit": limit})
        except Exception as exc:  # noqa: BLE001 - network/transport failure
            raise ConnectorAPIError(f"Confluence search request failed: {exc}") from exc

        if response.status_code >= 400:
            raise ConnectorAPIError(
                f"Confluence search request failed with HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ConnectorAPIError(f"Confluence search response was not valid JSON: {exc}") from exc

        return _extract_results(payload)

    def _result_to_document(self, result: Dict[str, Any]) -> Document:
        content = result.get("content") or {}
        content_id = content.get("id") or result.get("id") or ""
        title = content.get("title") or result.get("title") or "(untitled)"
        space = content.get("space") or {}
        space_key = space.get("key") or ""
        excerpt = result.get("excerpt") or ""
        content_type = content.get("type")
        status = content.get("status")

        base_url = self._config.site.base_url
        relative_url = result.get("url") or ""
        url = f"{base_url}{relative_url}" if relative_url else None

        last_modified = _parse_confluence_datetime(
            result.get("lastModified") or result.get("friendlyLastModified")
        )

        return Document(
            id=str(content_id),
            title=title,
            snippet=excerpt[:2000],
            source="confluence",
            url=url,
            last_modified=last_modified,
            container=space_key,
            metadata={"content_type": content_type, "status": status},
        )
