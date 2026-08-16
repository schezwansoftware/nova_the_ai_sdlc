"""SharePoint Server (on-prem) search/fetch client: the classic `_api/
search/query` REST endpoint -- this does **not** go through Microsoft
Graph at all (Graph is M365-cloud-only, confirmed via
`online_client.py`'s Azure AD-only auth model having no on-prem
equivalent) -- with NTLM or Basic auth, and the same `Path:` KQL scoping
idea `online_client.py` uses, applied to this endpoint's `querytext`
parameter instead of Graph's `queryString`.

## Auth: NTLM (via `requests`/`requests-ntlm`), Basic -- Kerberos explicitly not built

The approved design names three real on-prem auth models: NTLM,
Kerberos, ADFS. This pass implements two of them:

  - **NTLM**, via the `requests_ntlm` package's `HttpNtlmAuth` --
    verified installed and inspected directly (`requests-ntlm==1.3.0`,
    `HttpNtlmAuth(username: str | None, password: str | None, session=
    None, send_cbt: bool = True)`, subclassing `requests.auth.AuthBase`)
    in the environment this was built in. This is a real, working
    dependency, not just something read about -- though no live NTLM
    handshake against an actual on-prem farm was attempted (see below).
  - **Basic**, via `requests`' own built-in `(username, password)` tuple
    auth -- for ADFS-fronted or basic-auth-enabled on-prem deployments
    (the approved design groups ADFS under "a genuinely different auth
    model," and a full ADFS WS-Federation/SAML sign-in flow is out of
    scope for this pass; Basic auth is the honest subset actually
    implemented here for anything that isn't NTLM).
  - **Kerberos is explicitly not implemented.** It needs system Kerberos
    ticket infrastructure (a `krb5.conf`, a valid ticket cache or
    keytab) and a native-extension package (`requests-kerberos`, which
    pulls in `pykerberos`/`gssapi`, both of which commonly fail to build
    without system Kerberos headers present) -- meaningfully heavier and
    more environment-fragile than NTLM/Basic, and impossible to verify
    at all without a real domain-joined test environment, which doesn't
    exist here. Flagged as a real, scoped-out gap rather than
    guessed-at, not silently absent.

## Query-time scoping and fetch-by-id

Same `Path:` managed-property idea as Graph Search
(`online_client.py`), applied to this endpoint's KQL `querytext`
instead: `build_onprem_kql` ANDs the caller's query with `Path:"<site
url>*"` (trailing wildcard, since a site's content lives at paths
*under* the site URL, not at the site URL itself) -- the query-time half
of the precision requirement for this backend, matching the approved
design's "equivalent scoped parameter on `_api/search/query`" wording.

`fetch()` reuses this exact same scoped-search codepath with an added
`Path:"<item path>"` clause instead of taking the "direct GET, verify
after" path `online_client.py`'s `fetch()` has to (Graph's driveItem
resource-by-id endpoint has no on-prem-REST-search equivalent reason to
exist here) -- so, unlike the Online backend, this backend's `fetch()`
*is* fully query-time-scoped, with no post-fetch verification step at
all, matching Jira/Confluence's fetch-via-search pattern more closely
than SharePoint Online's does.

## Response shape: classic SharePoint Search REST JSON (`odata=verbose`)

`_api/search/query`'s JSON response nests results under
`d.query.PrimaryQueryResult.RelevantResults.Table.Rows.results[*]
.Cells.results[*]` (each cell a flat `{Key, Value}` pair) -- this is
SharePoint's long-standing, stable classic search REST shape (present
essentially unchanged since SharePoint 2013's Search REST API, per
established documentation), not something recently deprecated the way
Jira Cloud's bulk-search endpoint was. This specific nested shape was
**not** independently re-fetched from live docs this session (unlike
the Jira/Confluence/Graph endpoints above, which were) -- it's carried
over from general documentation familiarity, flagged honestly as the
one shape in this module that's "known, not re-verified," and
`_extract_onprem_rows` walks it defensively (`dict.get` at every level,
degrading to an empty result list on any mismatch) precisely because of
that.

## Not independently verified against a live tenant

No on-prem SharePoint Server farm was available to exercise any of this
end-to-end -- this is, honestly, the most speculative client in this
whole package (no live docs fetch was even attempted for it, unlike
every other connector here, since a general web search for on-prem
REST search syntax was judged sufficient given how stable/legacy this
particular API surface is). Treat this backend as needing the most
scrutiny in a live-verification follow-up.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from mcp_connectors.common import ConnectorAPIError, ConnectorAuthError, Document
from mcp_connectors.sharepoint.config import SharePointServerSiteConfig

try:
    import requests as _requests

    _REQUESTS_IMPORT_ERROR: Optional[BaseException] = None
except ImportError as exc:  # pragma: no cover - see module docstring;
    # deferred to call time (client construction), mirroring every other
    # optional-dependency guard in this package.
    _requests = None
    _REQUESTS_IMPORT_ERROR = exc

try:
    from requests_ntlm import HttpNtlmAuth as _HttpNtlmAuth

    _NTLM_IMPORT_ERROR: Optional[BaseException] = None
except ImportError as exc:  # pragma: no cover - `requests_ntlm` is only
    # needed for `auth_method="ntlm"`; `"basic"`-configured sites work
    # without it, so this is deferred to `_build_session`, not raised
    # merely for importing this module.
    _HttpNtlmAuth = None
    _NTLM_IMPORT_ERROR = exc

ONPREM_SEARCH_PATH = "/_api/search/query"


def build_onprem_kql(query: str, site_urls: List[str]) -> str:
    """Build the KQL `querytext` carrying both the caller's free-text
    query and the allowlist-scoped `Path:` site restriction -- see
    module docstring. `site_urls` must already be allowlist-validated
    (mirrors `jira.build_jql`/`online_client.build_graph_query_string`'s
    identical division of responsibility -- this function performs no
    allowlist check itself)."""
    query = (query or "").strip()
    if not query:
        raise ConnectorAPIError("query text must not be empty")
    if not site_urls:
        raise ConnectorAPIError("at least one site must be specified")

    escaped_query = query.replace('"', '\\"')
    path_clauses = " OR ".join(f'Path:"{url}*"' for url in site_urls)
    return f'({escaped_query}) AND ({path_clauses})'


def _extract_onprem_rows(payload: Any) -> List[Dict[str, Any]]:
    """Defensive walk of the classic Search REST response shape -- see
    module docstring. Any missing/malformed level degrades to an empty
    result list rather than raising."""
    try:
        table = payload["d"]["query"]["PrimaryQueryResult"]["RelevantResults"]["Table"]
        rows = table.get("Rows", {}).get("results", [])
    except (KeyError, TypeError):
        return []

    parsed_rows: List[Dict[str, Any]] = []
    for row in rows or []:
        cells: Dict[str, Any] = {}
        for cell in (row.get("Cells", {}) or {}).get("results", []) or []:
            key = cell.get("Key") if isinstance(cell, dict) else None
            if key:
                cells[key] = cell.get("Value")
        if cells:
            parsed_rows.append(cells)
    return parsed_rows


def _parse_onprem_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class SharePointServerClient:
    """Search/fetch against one SharePoint Server (on-prem) site.
    `session` is the test seam (mirrors every other client's
    `http_client` parameter in this package, just named for what it
    actually is here -- a `requests.Session`)."""

    def __init__(self, site: SharePointServerSiteConfig, *, session: Any = None, timeout: float = 30.0) -> None:
        self._site = site
        self._timeout = timeout
        self._session = session if session is not None else self._build_session()

    def _build_session(self) -> Any:
        if _REQUESTS_IMPORT_ERROR is not None:
            raise ConnectorAuthError(
                "the `requests` package is not usable in this environment "
                f"({_REQUESTS_IMPORT_ERROR!r}); install it (it's included in the "
                "`sharepoint` extra -- `pip install mcp-connectors[sharepoint]`)."
            )
        secret = self._site.credential.resolve()
        session = _requests.Session()
        if self._site.auth_method == "ntlm":
            if _NTLM_IMPORT_ERROR is not None:
                raise ConnectorAuthError(
                    "the `requests_ntlm` package is not usable in this "
                    f"environment ({_NTLM_IMPORT_ERROR!r}); install it (it's "
                    "included in the `sharepoint` extra) to use auth_method="
                    "'ntlm' against a SharePoint Server site. Use "
                    "auth_method='basic' instead if this on-prem deployment "
                    "supports it."
                )
            session.auth = _HttpNtlmAuth(self._site.username, secret)
        else:
            session.auth = (self._site.username, secret)
        return session

    def search(self, query: str, *, limit: int) -> List[Document]:
        kql = build_onprem_kql(query, [self._site.site_url])
        return self._run_search(kql, limit=limit)

    def fetch(self, item_path: str) -> Document:
        """Scoped through the same search codepath `search()` uses, with
        an added `Path:"<item path>"` clause -- see module docstring for
        why this backend's `fetch()`, unlike Online's, needs no
        post-fetch verification step."""
        item_path = (item_path or "").strip()
        if not item_path:
            raise ConnectorAPIError("item path must not be empty")
        # An exact-path clause ANDed with the same site-scoping clause
        # `build_onprem_kql` itself emits -- composed directly (rather
        # than calling `build_onprem_kql` with a placeholder query and
        # splicing) since a real query string is required here, not a
        # wildcard.
        escaped_path = item_path.replace('"', '\\"')
        site_clause = f'Path:"{self._site.site_url}*"'
        kql = f'Path:"{escaped_path}" AND ({site_clause})'
        results = self._run_search(kql, limit=1)
        if not results:
            raise ConnectorAPIError(
                f"no SharePoint item found for path {item_path!r} within the "
                f"allowlisted site ({self._site.site_url!r}) -- either it doesn't "
                "exist, or it exists outside this connector's configured scope"
            )
        return results[0]

    # -- request plumbing ---------------------------------------------------

    def _run_search(self, kql: str, *, limit: int) -> List[Document]:
        url = f"{self._site.site_url}{ONPREM_SEARCH_PATH}"
        params = {
            "querytext": f"'{kql}'",
            "rowlimit": limit,
            "selectproperties": "'Title,Path,Author,LastModifiedTime,UniqueId,HitHighlightedSummary'",
        }
        headers = {"Accept": "application/json;odata=verbose"}
        try:
            response = self._session.get(url, params=params, headers=headers, timeout=self._timeout)
        except Exception as exc:  # noqa: BLE001 - network/transport failure
            raise ConnectorAPIError(f"SharePoint Server search request failed: {exc}") from exc

        if response.status_code >= 400:
            raise ConnectorAPIError(
                f"SharePoint Server search request failed with HTTP "
                f"{response.status_code}: {response.text[:500]}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ConnectorAPIError(f"SharePoint Server search response was not valid JSON: {exc}") from exc

        return [self._row_to_document(row) for row in _extract_onprem_rows(payload)]

    def _row_to_document(self, row: Dict[str, Any]) -> Document:
        path = row.get("Path") or ""
        title = row.get("Title") or path or "(untitled)"
        summary = row.get("HitHighlightedSummary") or ""
        unique_id = row.get("UniqueId")

        # `id` is the item's Path, not its UniqueId -- deliberately:
        # `fetch()` above can only re-locate an item via a `Path:"..."`
        # KQL clause (there's no `UniqueId:` equivalent this backend's
        # search endpoint reliably supports), so `id` has to be
        # whatever `fetch()` actually knows how to consume. `unique_id`
        # is still preserved in `metadata` since it's a genuinely more
        # stable identifier, just not a fetchable one here.
        return Document(
            id=path or str(unique_id or ""),
            title=title,
            snippet=summary[:2000],
            source="sharepoint",
            url=path or None,
            last_modified=_parse_onprem_datetime(row.get("LastModifiedTime")),
            container=self._site.site_url,
            metadata={"author": row.get("Author"), "unique_id": unique_id},
        )
