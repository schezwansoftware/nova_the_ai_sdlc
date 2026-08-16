"""Jira search/fetch client: JQL construction with hard project scoping,
defensive issue-JSON parsing into `Document`.

## Search endpoint: forks by deployment type, and *why* -- a real
## deviation from "only base URL/auth fork," found via live docs research

The approved design says Jira/Confluence's JQL/CQL project/space scoping
syntax is identical across Cloud and Data Center, so query-construction
logic doesn't need to fork -- only base URL and auth do. That holds for
the *JQL string itself* (`build_jql` below is one function, no
deployment-type branch in it at all) but turns out **not** to hold for
which HTTP endpoint carries that JQL string, for a reason that has
nothing to do with auth: Atlassian deprecated and fully removed Jira
**Cloud's** classic `GET/POST /rest/api/{2,3}/search` bulk-search
endpoints between May and October 2025, migrating all Cloud JQL search to
a new endpoint, `POST /rest/api/3/search/jql` (paginated via
`nextPageToken`, not the old `startAt`). This migration is Cloud-only --
it's Atlassian's own backend search-infrastructure scaling change, not a
JQL-language change -- so Jira **Data Center** was never affected and
still runs the classic `POST /rest/api/2/search` (`startAt`-paginated).
Verified via live web search on 2026-08-16 (Atlassian Community threads
describing the deprecation timeline and HTTP 410s from the old endpoint;
`confluence.atlassian.com/jirakb/run-jql-search-query-using-jira-cloud-rest-api-1289424308.html`
documents the replacement endpoint), not recalled from training data or
assumed from the approved design brief.

This connector therefore does fork on deployment type for `search()`'s
endpoint/pagination-field choice (`_search_request` below) -- flagged
explicitly here as a considered deviation from the brief's stated
assumption, not a silent one, and not a JQL-construction fork: the
allowlist-scoped JQL string that actually encodes this connector's
precision guarantee is built identically either way (`build_jql`) and
handed to whichever endpoint the deployment type calls for.

`fetch()` (single-issue-by-key lookup) does *not* fork: `GET
/rest/api/2/issue/{key}` is unaffected by the Cloud search migration
(that migration only touched the bulk-search endpoints) and works
identically on both deployment types -- but, per the precision
requirement's "every actual search call... native query language"
wording applying to fetch too, this connector implements `fetch()` as a
single-result JQL search (`issueKey = "..."` ANDed onto the same
allowlist-scoped `project in (...)` clause) rather than a raw GET, so
project-scope enforcement for a by-key lookup goes through the exact
same native-query mechanism as a free-text search, not a separate
"fetch then check" code path. See `fetch()`'s own docstring.

## Description field: plain string (v2) vs. Atlassian Document Format (v3)

Jira Cloud's `/rest/api/3/search/jql` is a `v3` endpoint; `v3` issue
`fields.description` is **Atlassian Document Format (ADF)** -- a nested
JSON node tree, not a plain string -- whereas Data Center's `v2` search
endpoint returns `fields.description` as plain wiki-markup text.
`_extract_description_text` below handles both shapes defensively (a
best-effort ADF text walker, not a full ADF renderer -- good enough for
a search-result snippet, not a promise of perfect fidelity), rather than
assuming one fixed shape and breaking on the other deployment type.

## Not independently verified against a live tenant

No Jira tenant/credentials were available to exercise any of this
end-to-end. JQL/endpoint/pagination shapes above come from live
documentation research (see above); issue-JSON field access throughout
(`_issue_to_document`) is defensive (`dict.get` with fallbacks, never a
bare `issue["fields"]["summary"]`), mirroring
`capabilities/providers/claude_sdk.py`'s documented stance on the same
"verified against docs, not a live session" situation.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from mcp_connectors.atlassian.auth import AtlassianDeploymentType, build_http_client
from mcp_connectors.common import ConnectorAPIError, Document, enforce_allowlist
from mcp_connectors.jira.config import JiraConnectorConfig

#: Cloud's replacement for the removed bulk-search endpoints -- see
#: module docstring. POST body, `nextPageToken`-paginated (this
#: connector never paginates past the first page: `result_limit` is
#: always a small top-N cap, per the approved design's "sensible default
#: result limit, not unbounded").
JIRA_CLOUD_SEARCH_PATH = "/rest/api/3/search/jql"

#: Data Center's unaffected classic search endpoint -- see module
#: docstring. POST body, `startAt`-paginated.
JIRA_DATA_CENTER_SEARCH_PATH = "/rest/api/2/search"

#: Single-issue-by-key GET, identical on both deployment types (used by
#: neither `search()` nor `fetch()` directly -- both now go through
#: `_search_request` per the precision requirement; kept only as a
#: documented "this exists and is unaffected" fact, not dead code
#: elsewhere).
JIRA_ISSUE_GET_PATH_TEMPLATE = "/rest/api/2/issue/{key}"

JIRA_SEARCH_FIELDS = ["summary", "description", "status", "updated", "project", "issuetype"]


def build_jql(query: str, projects: List[str]) -> str:
    """Build the JQL string carrying both the caller's free-text query
    and the allowlist-scoped project restriction, in one clause --
    exactly the approved design's `project in (KEY1, KEY2) AND text ~
    "..."` shape. `projects` must already be allowlist-validated
    (`enforce_allowlist` output) -- this function itself does not
    consult any allowlist; it only ever emits what it's given, so the
    caller enforcing that first is what makes the guarantee real."""
    query = (query or "").strip()
    if not query:
        raise ConnectorAPIError("query text must not be empty")
    if not projects:
        raise ConnectorAPIError("at least one project must be specified")

    escaped_query = query.replace("\\", "\\\\").replace('"', '\\"')
    project_clause = "project in (" + ", ".join(projects) + ")"
    text_clause = f'text ~ "{escaped_query}"'
    return f"{project_clause} AND {text_clause} ORDER BY updated DESC"


def _extract_text_from_adf(node: Any) -> str:
    """Best-effort Atlassian Document Format text walker -- see module
    docstring. Not a full ADF renderer (no formatting/tables/media
    awareness); good enough for a plain-text snippet."""
    if not isinstance(node, dict):
        return ""
    parts = []
    if node.get("type") == "text" and isinstance(node.get("text"), str):
        parts.append(node["text"])
    for child in node.get("content") or []:
        text = _extract_text_from_adf(child)
        if text:
            parts.append(text)
    return " ".join(parts)


def _extract_description_text(description: Any) -> str:
    if isinstance(description, str):
        return description
    if isinstance(description, dict):
        return _extract_text_from_adf(description)
    return ""


def _parse_jira_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        # Jira's timestamps look like "2026-08-10T14:03:00.000+0000" --
        # normalize the trailing "+0000"-style offset (no colon) to the
        # "+00:00" ISO form `datetime.fromisoformat` expects.
        normalized = value
        if len(normalized) >= 5 and normalized[-5] in "+-" and normalized[-3] != ":":
            normalized = normalized[:-2] + ":" + normalized[-2:]
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _extract_issues(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("issues"), list):
        return [issue for issue in payload["issues"] if isinstance(issue, dict)]
    return []


class JiraClient:
    """Search/fetch against one Jira site, scoped to one connector's
    project allowlist. `http_client` is the test seam (an
    `httpx.Client(transport=httpx.MockTransport(...))` in tests -- real
    network client when left `None`)."""

    def __init__(self, config: JiraConnectorConfig, *, http_client: Any = None) -> None:
        self._config = config
        self._client = http_client if http_client is not None else build_http_client(config.site)

    def search(self, query: str, projects: Optional[List[str]] = None) -> List[Document]:
        allowed = enforce_allowlist(projects or [], self._config.allowed_projects, kind="project")
        jql = build_jql(query, allowed)
        issues = self._run_search(jql, limit=self._config.result_limit)
        return [self._issue_to_document(issue) for issue in issues]

    def fetch(self, issue_key: str) -> Document:
        """Fetch one issue by key, scoped through the same allowlisted
        JQL mechanism `search()` uses (`issueKey = "..."` ANDed onto
        `project in (...)`) rather than a raw by-key GET -- see module
        docstring for why. The project half of the key
        (`"PROJ-123"` -> `"PROJ"`) is checked against the allowlist
        before any request is made, since it's derivable from the key's
        own well-known structure with no query required."""
        issue_key = (issue_key or "").strip().upper()
        if not issue_key or "-" not in issue_key:
            raise ConnectorAPIError(f"{issue_key!r} is not a valid Jira issue key (expected e.g. 'PROJ-123')")
        project_key = issue_key.rsplit("-", 1)[0]
        allowed = enforce_allowlist([project_key], self._config.allowed_projects, kind="project")

        escaped_key = issue_key.replace("\\", "\\\\").replace('"', '\\"')
        jql = f'project in ({", ".join(allowed)}) AND issueKey = "{escaped_key}"'
        issues = self._run_search(jql, limit=1)
        if not issues:
            raise ConnectorAPIError(
                f"no Jira issue found for {issue_key!r} within the allowlisted "
                f"project(s) {allowed} -- either it doesn't exist, or it exists "
                "outside this connector's configured scope"
            )
        return self._issue_to_document(issues[0])

    # -- request plumbing ---------------------------------------------------

    def _search_request(self, jql: str, limit: int) -> Tuple[str, Dict[str, Any]]:
        """The one place `search()`/`fetch()` fork by deployment type --
        see module docstring's "Search endpoint" section."""
        if self._config.site.deployment_type == AtlassianDeploymentType.CLOUD:
            return JIRA_CLOUD_SEARCH_PATH, {"jql": jql, "maxResults": limit, "fields": JIRA_SEARCH_FIELDS}
        return (
            JIRA_DATA_CENTER_SEARCH_PATH,
            {"jql": jql, "maxResults": limit, "fields": JIRA_SEARCH_FIELDS, "startAt": 0},
        )

    def _run_search(self, jql: str, limit: int) -> List[Dict[str, Any]]:
        path, body = self._search_request(jql, limit)
        try:
            response = self._client.post(path, json=body)
        except Exception as exc:  # noqa: BLE001 - network/transport failure
            raise ConnectorAPIError(f"Jira search request to {path} failed: {exc}") from exc

        if response.status_code >= 400:
            raise ConnectorAPIError(
                f"Jira search request to {path} failed with HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ConnectorAPIError(f"Jira search response from {path} was not valid JSON: {exc}") from exc

        return _extract_issues(payload)

    def _issue_to_document(self, issue: Dict[str, Any]) -> Document:
        fields = issue.get("fields") or {}
        key = issue.get("key") or ""
        summary = fields.get("summary") or key or "(no summary)"
        snippet = _extract_description_text(fields.get("description"))
        project = fields.get("project") or {}
        project_key = project.get("key") or (key.rsplit("-", 1)[0] if "-" in key else "")
        status = fields.get("status") or {}
        issue_type = fields.get("issuetype") or {}

        base_url = self._config.site.base_url
        url = f"{base_url}/browse/{key}" if key else None

        return Document(
            id=key,
            title=summary,
            snippet=snippet[:2000],
            source="jira",
            url=url,
            last_modified=_parse_jira_datetime(fields.get("updated")),
            container=project_key,
            metadata={
                "status": status.get("name"),
                "issue_type": issue_type.get("name"),
            },
        )
