"""Shared scaffolding for every connector in this package: the `Document`
result model, this package's own self-contained MCP tool-error contract,
the config-time hard-allowlist enforcement helper (half of the "precision
requirement" -- see `mcp_connectors/__init__.py`), OS-keyring-backed
credential storage, and the connector-config-file directory convention.

## Error contract at the MCP tool-call boundary

Each server needs its own, fully self-contained error contract -- no
dependency on any other project's exception hierarchy, since a
standalone MCP server must not require anything beyond this package to
be importable. `ConnectorError` and its three subclasses below are that
contract.

They don't need any bespoke "convert this into an MCP error" plumbing,
either -- verified directly against the installed `mcp==1.29.0` package's
source (`mcp/server/lowlevel/server.py::Server.call_tool`'s registered
handler, and `mcp/server/fastmcp/tools/base.py::Tool.run`): any exception
raised inside an `@server.tool()`-decorated function is caught by
`Tool.run` and re-raised as `mcp.server.fastmcp.exceptions.ToolError`,
which the low-level server's own `call_tool` handler catches in turn and
converts into a `CallToolResult(isError=True, content=[TextContent(...)])`
carrying the exception's `str()` -- i.e. a real MCP tool-error response
the calling client sees, not a crashed process. So every connector's
`mcp_server.py` just lets `ConnectorError` (or any other exception)
propagate out of its `search`/`fetch` tool functions; no `try`/`except`
translation layer is needed at that boundary. This was checked against
the real installed package, not assumed from the MCP spec docs.

## Credential storage: real OS-native keyring, never plaintext config

`store_secret`/`get_secret`/`delete_secret` wrap the real `keyring`
package (verified installed: `keyring==25.7.0`) -- macOS Keychain /
Windows Credential Locker / Linux Secret Service, selected automatically
by `keyring`'s own backend discovery. A connector's JSON config file
(see `connectors_config_dir` below) never stores a raw secret -- only a
`CredentialRef` (a `(service, username)` pair, keyring's own two-part
lookup key), resolved to the real secret at request time via
`CredentialRef.resolve()`.

`keyring` is imported defensively (deferred-ImportError pattern): this
module always imports cleanly even in an
environment with none of the `jira`/`confluence`/`sharepoint` extras
installed (e.g. a future phase that only wants the `Document` model's
shape) -- only the credential functions themselves raise
`ConnectorAuthError` at call time if `keyring` truly isn't importable.

## Config file convention

Each connector owns exactly one JSON config file under
`connectors_config_dir()` (default `~/.config/mcp-connectors/`, overridable
via `MCP_CONNECTORS_CONFIG_DIR` for tests/CI) -- e.g. `jira.json`,
`confluence.json`, `sharepoint.json`. One JSON file per connector (rather
than one shared multi-connector file) is what makes "run `jira-mcp`
completely standalone" literally true: that server never has to parse or
even know about the other two connectors' config.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

try:
    import keyring as _keyring
    import keyring.errors as _keyring_errors

    _KEYRING_IMPORT_ERROR: Optional[BaseException] = None
except ImportError as exc:  # pragma: no cover - exercised only when the
    # optional `keyring` dependency (pulled in by every one of the
    # `jira`/`confluence`/`sharepoint` extras -- see pyproject.toml) isn't
    # installed. Deferred to call time, not module import time, so
    # `Document`/the exception hierarchy/`enforce_allowlist` stay usable
    # without it -- see module docstring.
    _keyring = None
    _keyring_errors = None
    _KEYRING_IMPORT_ERROR = exc


# -- result capping -----------------------------------------------------------

#: Default `search()` result cap when a connector's config doesn't
#: override it -- "a sensible default result limit (10-20)" per the
#: approved design. Configurable per connector (`result_limit` field on
#: each connector's config model), never unbounded.
DEFAULT_RESULT_LIMIT = 15

#: Hard ceiling on `result_limit`, regardless of what a config file
#: requests -- a config-validation error, not a silent clamp, if
#: exceeded (see each connector's config model's `result_limit` field).
MAX_RESULT_LIMIT = 50


# -- shared result model -------------------------------------------------------


class Document(BaseModel):
    """The one result shape every connector's `search`/`fetch` tools
    return, regardless of backend. Fields per the approved design:
    `id`/`title`/`snippet`/`source`/`url`/`last_modified`/`container`,
    plus a free-form `metadata` bag for whatever else is naturally
    available from a given backend (e.g. Jira issue status/type,
    Confluence content type, SharePoint file size) without forcing every
    connector's client to agree on a fixed extra-fields schema.

    `container` is the connector-specific scope unit a document lives in
    -- the Jira project key, the Confluence space key, or the SharePoint
    site URL -- i.e. exactly the unit each connector's config-time
    allowlist is expressed in terms of (see `enforce_allowlist` below).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    snippet: str = ""
    source: str
    url: Optional[str] = None
    last_modified: Optional[datetime] = None
    container: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


# -- self-contained MCP tool-error contract -------------------------------------


class ConnectorError(Exception):
    """Base class for every error this package's MCP tool-call boundary
    raises -- see module docstring's "Error contract" section for why
    this is fully self-contained, and for why no explicit MCP-error
    translation code is needed anywhere: FastMCP already converts any
    exception raised inside a tool function into a real `isError` MCP
    tool response."""


class ConnectorConfigError(ConnectorError):
    """The connector's config file is missing/invalid, or a query named
    a project/space/site outside this connector's configured allowlist
    -- the config-time half of the precision requirement (see
    `mcp_connectors/__init__.py`)."""


class ConnectorAuthError(ConnectorError):
    """A credential could not be resolved from the OS keyring (missing,
    or the keyring backend itself is unavailable/locked), or the
    upstream API rejected the credential that was resolved."""


class ConnectorAPIError(ConnectorError):
    """The underlying Jira/Confluence/SharePoint HTTP request failed
    (network error, non-2xx status) or returned a response this
    connector's defensive parsing could not make sense of."""


# -- config-time hard allowlist enforcement ------------------------------------


def enforce_allowlist(requested: Sequence[str], allowed: Sequence[str], *, kind: str) -> List[str]:
    """The config-time half of the precision requirement, shared by all
    three connectors (see `mcp_connectors/__init__.py`). `allowed` is the
    connector's configured allowlist (project keys / space keys / site
    URLs); `requested` is whatever the caller named at query time (may be
    empty, meaning "use the full allowlist").

    Returns the normalized, validated container list to actually query
    against. Raises `ConnectorConfigError` -- never silently drops or
    widens -- the instant anything in `requested` isn't in `allowed`.
    `kind` is a human word ("project"/"space"/"site") used only to make
    the error message legible; it has no effect on the check itself.
    """
    allowed_list = [str(item).strip() for item in allowed if str(item).strip()]
    if not requested:
        return list(allowed_list)

    allowed_set = set(allowed_list)
    normalized = [str(item).strip() for item in requested if str(item).strip()]
    disallowed = [item for item in normalized if item not in allowed_set]
    if disallowed:
        raise ConnectorConfigError(
            f"{kind}(s) {disallowed!r} are not in this connector's configured "
            f"allowlist ({sorted(allowed_set)!r}). The config-time allowlist is a "
            "hard boundary -- add them to the connector's config file to grant "
            "access; a query can never widen scope beyond it."
        )
    return normalized


# -- OS-keyring-backed credential storage ---------------------------------------


def _require_keyring() -> Any:
    if _KEYRING_IMPORT_ERROR is not None:
        raise ConnectorAuthError(
            "the `keyring` package is not usable in this environment "
            f"({_KEYRING_IMPORT_ERROR!r}); install it (it's included in every one "
            "of this project's `jira`/`confluence`/`sharepoint` extras -- "
            "`pip install mcp-connectors[jira]` etc.) before storing or resolving "
            "connector credentials."
        )
    return _keyring


def store_secret(service: str, username: str, secret: str) -> None:
    """Store `secret` in the OS-native keyring under `(service, username)`
    -- the pair a `CredentialRef` later resolves. This is the only
    supported way to get a real credential into a connector's reach; a
    connector's JSON config file only ever holds the `(service,
    username)` reference, never the secret itself."""
    keyring = _require_keyring()
    try:
        keyring.set_password(service, username, secret)
    except _keyring_errors.KeyringError as exc:
        raise ConnectorAuthError(
            f"failed to store a credential in the OS keyring "
            f"(service={service!r}, username={username!r}): {exc}"
        ) from exc


def get_secret(service: str, username: str) -> str:
    """Resolve a previously-stored secret. Raises `ConnectorAuthError`
    (with a copy-pasteable fix) if nothing is stored under this
    `(service, username)` pair, or if the keyring backend itself
    couldn't be reached -- either way, this is never silently treated as
    an empty-string credential."""
    keyring = _require_keyring()
    try:
        secret = keyring.get_password(service, username)
    except _keyring_errors.KeyringError as exc:
        raise ConnectorAuthError(
            f"could not reach the OS keyring to resolve a credential "
            f"(service={service!r}, username={username!r}): {exc}"
        ) from exc

    if not secret:
        raise ConnectorAuthError(
            f"no credential is stored in the OS keyring for service={service!r}, "
            f"username={username!r}. Store one first, e.g.:\n"
            "  python -c \"from mcp_connectors.common import store_secret; "
            f"store_secret({service!r}, {username!r}, '<the real secret>')\"\n"
            "Never place the raw secret directly in the connector's JSON config "
            "file -- only this (service, username) reference belongs there."
        )
    return secret


def delete_secret(service: str, username: str) -> None:
    """Remove a stored credential. Deleting one that was never stored is
    not an error (idempotent, matching `save_config`'s own "no surprise
    failures on an already-in-the-desired-state call" posture elsewhere
    in this codebase)."""
    keyring = _require_keyring()
    try:
        keyring.delete_password(service, username)
    except _keyring_errors.PasswordDeleteError:
        return
    except _keyring_errors.KeyringError as exc:
        raise ConnectorAuthError(
            f"failed to delete a credential from the OS keyring "
            f"(service={service!r}, username={username!r}): {exc}"
        ) from exc


class CredentialRef(BaseModel):
    """A reference to a secret stored in the OS keyring -- never the
    secret itself. `service`/`username` together form the OS keyring's
    own two-part lookup key (e.g. macOS Keychain's "service name"/
    "account name"). This is the only credential-shaped thing any
    connector config model ever stores on disk."""

    model_config = ConfigDict(extra="forbid")

    service: str
    username: str

    def resolve(self) -> str:
        return get_secret(self.service, self.username)


# -- connector config file directory convention ----------------------------------


def connectors_config_dir() -> Path:
    """Directory holding every connector's own JSON config file (one file
    per connector -- see module docstring's "Config file convention")."""
    override = os.environ.get("MCP_CONNECTORS_CONFIG_DIR")
    if override:
        return Path(override)
    return Path.home() / ".config" / "mcp-connectors"
