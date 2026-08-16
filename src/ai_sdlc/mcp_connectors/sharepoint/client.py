"""SharePoint facade client: dispatches `search`/`fetch` to whichever of
the two disjoint backends (`SharePointOnlineClient`,
`SharePointServerClient`) a given configured site declares itself to be
(`SharePointSiteConfig.deployment_type`), and enforces the config-time
site allowlist before either backend is ever touched.

This is the one module in the SharePoint connector that both backends'
callers (the MCP server -- see `mcp_server.py`) actually talk to; neither
`online_client.py` nor `onprem_client.py` needs to know the other
exists.

## Document id convention: `"<site_url>::<backend-specific id>"`

The shared `search(query) -> [Document]` / `fetch(id) -> Document`
interface's `fetch` only takes an opaque `id` string -- no separate
"which site" parameter. That's unambiguous for Jira (`id` already
encodes the project via its own key structure, `"PROJ-123"`) and
Confluence (content ids are unique across the whole site, no
per-space-scoped id space to disambiguate). SharePoint is different: one
connector can have *multiple* configured sites, potentially spanning
both backends, each with their own independent id space (a driveId:
itemId composite for Online, a Path for Server) -- so `fetch(id)` alone
has nowhere to learn which site's backend to even ask.

This facade resolves that by composing `Document.id` as `"<site_url>::
<backend id>"` on the way out of `search()`, and parsing it back apart
on the way into `fetch()`. `"::"` (double colon) is the separator
because `site_url` always contains a single `"://"` of its own (the URL
scheme) but never a literal `"::"` -- so splitting on the first `"::"`
is unambiguous even though `site_url` itself contains colons. Each
backend client's own `Document.id` (what `online_client.py`/
`onprem_client.py` themselves produce) stays in its own native id shape
-- this composition happens only here, at the facade boundary, so
neither backend needs to know about the other's id conventions or this
package's own facade-level scheme.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from ai_sdlc.mcp_connectors.common import ConnectorAPIError, Document, enforce_allowlist
from ai_sdlc.mcp_connectors.sharepoint.config import (
    SharePointConnectorConfig,
    SharePointOnlineSiteConfig,
    SharePointServerSiteConfig,
)
from ai_sdlc.mcp_connectors.sharepoint.online_client import SharePointOnlineClient
from ai_sdlc.mcp_connectors.sharepoint.onprem_client import SharePointServerClient

_ID_SEPARATOR = "::"


class SharePointClient:
    """Search/fetch across every site configured for this connector,
    scoped to the connector's site allowlist (which, for SharePoint, is
    exactly the configured `sites` list itself -- see `config.py`'s
    module docstring). `online_client_factory`/`onprem_client_factory`
    are the test seam: swap in a fake per-backend client constructor
    without needing a real `httpx`/`requests` session underneath either
    one."""

    def __init__(
        self,
        config: SharePointConnectorConfig,
        *,
        online_client_factory: Callable[[SharePointOnlineSiteConfig], Any] = SharePointOnlineClient,
        onprem_client_factory: Callable[[SharePointServerSiteConfig], Any] = SharePointServerClient,
    ) -> None:
        self._config = config
        self._sites_by_url: Dict[str, Any] = {site.site_url: site for site in config.sites}
        self._online_factory = online_client_factory
        self._onprem_factory = onprem_client_factory
        self._backend_cache: Dict[str, Any] = {}

    def search(self, query: str, sites: Optional[List[str]] = None) -> List[Document]:
        allowed = enforce_allowlist(sites or [], list(self._sites_by_url), kind="site")

        results: List[Document] = []
        for site_url in allowed:
            backend = self._backend_for(site_url)
            for doc in backend.search(query, limit=self._config.result_limit):
                results.append(self._prefix_document_id(doc, site_url))
        return results[: self._config.result_limit]

    def fetch(self, document_id: str) -> Document:
        document_id = (document_id or "").strip()
        if _ID_SEPARATOR not in document_id:
            raise ConnectorAPIError(
                f"{document_id!r} is not a valid SharePoint document id (expected "
                f"'<site_url>{_ID_SEPARATOR}<item id>', exactly as returned by this "
                "connector's own search results)"
            )
        site_url, backend_id = document_id.split(_ID_SEPARATOR, 1)
        enforce_allowlist([site_url], list(self._sites_by_url), kind="site")

        backend = self._backend_for(site_url)
        if isinstance(self._sites_by_url[site_url], SharePointOnlineSiteConfig):
            if ":" not in backend_id:
                raise ConnectorAPIError(
                    f"{document_id!r} is not a valid SharePoint Online document id "
                    "(expected the '<driveId>:<itemId>' form this connector's own "
                    "search results return)"
                )
            drive_id, item_id = backend_id.split(":", 1)
            doc = backend.fetch(drive_id, item_id)
        else:
            doc = backend.fetch(backend_id)
        return self._prefix_document_id(doc, site_url)

    # -- backend dispatch ---------------------------------------------------

    def _backend_for(self, site_url: str) -> Any:
        if site_url not in self._backend_cache:
            site = self._sites_by_url[site_url]
            if isinstance(site, SharePointOnlineSiteConfig):
                self._backend_cache[site_url] = self._online_factory(site)
            else:
                self._backend_cache[site_url] = self._onprem_factory(site)
        return self._backend_cache[site_url]

    def _prefix_document_id(self, doc: Document, site_url: str) -> Document:
        return doc.model_copy(update={"id": f"{site_url}{_ID_SEPARATOR}{doc.id}"})
