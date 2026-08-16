"""Shared local-filesystem directory-walk / text-search / path-safety
logic for the `local_docs` and `onedrive` connectors. Both are pure
local-filesystem access -- no HTTP client, no OAuth, no credential of any
kind (`onedrive` reads the OneDrive desktop client's already-synced local
folder directly, rather than calling Microsoft Graph -- see
`mcp_connectors/onedrive/client.py`'s module docstring for why that was a
deliberate choice, not a shortcut). This module holds the logic genuinely
shared between the two; each connector's own `client.py` is a thin
wrapper that supplies its allowlisted directories, its `source` label,
and whether OneDrive-specific cloud-placeholder detection applies.

## No indexing/sync infrastructure

Live search at query time only: walk the allowlisted directories, read
matching files, do a plain case-insensitive substring match over
content. No persistent search index is built or maintained, matching
this whole package's established "no indexing pipeline in V1"
philosophy (see `mcp_connectors/__init__.py`).

## V1 file-type scope: plain text formats only -- a real, deliberate gap

Only `.md`, `.markdown`, `.txt`, `.rst` files are ever read or returned.
PDF, Word (`.docx`), Excel (`.xlsx`), and image formats are explicitly
**out of scope for V1** -- not attempted, not silently ignored: this is
a real, deliberately deferred gap, tracked in `todo.md`, not an
oversight. A binary/rich-document file sitting in an allowlisted
directory is simply never matched by `iter_candidate_files` below (its
extension isn't in `PLAIN_TEXT_EXTENSIONS`), so it never reaches the
read/search path at all.

## The precision requirement, adapted for local files

Every other connector in this package (Jira/Confluence/SharePoint)
enforces scope in two places: a config-time hard allowlist, and a
query-time restriction expressed in the backend's own native query
language (JQL/CQL/Graph Search `Path:` clauses) -- see
`mcp_connectors/__init__.py`. There is no remote API doing server-side
scoping for local files, so this module is fully responsible for the
equivalent guarantee, in two analogous places:

  1. **Config-time hard allowlist of directories** -- each connector's
     config model (`local_docs/config.py::LocalDocsConnectorConfig`,
     `onedrive/config.py::OneDriveConnectorConfig`) resolves every
     configured directory via `Path.resolve(strict=True)` at
     config-*validation* time (a `pydantic` `field_validator`, not
     something this module does itself) -- so `config.allowed_directories`
     already holds canonical, existing, real absolute paths by the time
     any client/search code sees them. `enforce_allowlist` (from
     `mcp_connectors/common.py`, reused as-is -- not reimplemented) is
     then used at query time by each connector's `client.py` exactly the
     way Jira/Confluence use it for project/space keys, to validate any
     caller-supplied `directories` subset against that config-time list.

  2. **Query-time path-safety enforcement -- new logic, specific to this
     connector family, written and tested here for the first time.**
     A directory-prefix string check alone is not a real security
     boundary: a symlink *inside* an allowed directory can point
     *outside* it, and a `fetch()` id is caller-supplied, opaque input
     that could contain a path-traversal sequence (e.g. `"../../etc/
     passwd"`). So before any file's content is read or returned in a
     result, **`resolve_within_allowlist`/`require_within_allowlist`
     below always resolve the file's *real* path** (`Path.resolve()`,
     which follows symlinks) and verify that resolved path is actually
     `relative_to` one of the allowlisted (already-resolved) directories
     -- never a bare `str.startswith(...)` prefix check, which a symlink
     or a `..`-laden path can defeat. A path that resolves outside every
     allowed directory -- whether via a traversal sequence in a `fetch()`
     id, or via a symlink encountered mid-walk during `search()` -- is
     never read and never appears in a result. `search()`'s walk drops
     such entries silently (a bad symlink somewhere in a big directory
     tree shouldn't abort an otherwise-valid search); `fetch()`'s
     `require_within_allowlist` raises `ConnectorAPIError` instead,
     because a `fetch()` id is a specific, caller-supplied request that
     deserves a loud, explicit refusal rather than a silent empty result.
     See `tests/test_local_fs.py` for the symlink-escape and
     path-traversal tests this guarantee is checked against directly --
     not just asserted in prose here.

## OneDrive Files-On-Demand: a real, honest gap, not a false "fully handled"

OneDrive's "Files On-Demand" feature can leave a file as a cloud-only
placeholder -- present in a directory listing, but with no actual local
content until it's opened/downloaded through the OneDrive client or
Explorer/Finder UI. Returning such a file's "content" naively would
either read back an empty string (silently wrong -- looks like a real
empty document) or, in principle, whatever partial/placeholder bytes the
OS happens to expose. `looks_like_cloud_only_placeholder` below is a
**best-effort, explicitly partial** detector, applied only when a
connector opts in (`detect_cloud_placeholders=True` -- `onedrive`'s
client passes this; `local_docs`'s does not, since a genuinely empty
`.txt`/`.md` file in a plain local directory is a normal thing to have
and shouldn't be treated as suspicious):

  - **Windows**: `os.stat()` exposes `st_file_attributes` on Windows
    only. This checks it for `FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS`
    (`0x00400000`) or `FILE_ATTRIBUTE_OFFLINE` (`0x00001000`) -- real,
    documented Win32 file-attribute bits OneDrive sets on a cloud-only
    placeholder that hasn't been hydrated to local disk. This is the one
    signal here with real platform-attribute backing, not just a
    heuristic.
  - **All platforms, as a second-line heuristic**: a zero-byte file is
    treated as a suspected placeholder. A genuinely empty file is
    possible but unusual for a synced document, so this errs toward
    flagging it explicitly rather than silently returning empty content
    as if it were a confirmed-real empty document.

  **What this does *not* attempt** (a real gap, documented here rather
  than glossed over): on **macOS**, OneDrive's Files-On-Demand is
  implemented via Apple's File Provider extension, and a file's real
  download status lives behind
  `NSMetadataItemUbiquitousItemDownloadingStatusKey` / the File Provider
  APIs -- reachable from Cocoa/Foundation, **not** from anything in
  Python's stdlib `os`/`pathlib`. Detecting it properly would need a
  native bridge (e.g. `pyobjc`), which this connector deliberately does
  not take on as a dependency for a stdlib-only V1. A macOS cloud-only
  placeholder that happens to be non-zero-byte (uncommon, but not
  impossible) will **not** be caught by this module and could still be
  read as if it were real content. On Linux, OneDrive has no first-party
  client, so this gap doesn't really apply there, but the zero-byte
  heuristic still runs for whatever's on disk.

  Where this check fires: `search_local_files` silently skips a detected
  placeholder (it contributes no real searchable text anyway); `fetch()`
  raises a clear `ConnectorAPIError` naming the file and explaining it
  looks like an undownloaded placeholder, rather than returning empty or
  partial content as if it were the real thing.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator, List, Optional, Sequence, Tuple

from mcp_connectors.common import ConnectorAPIError, Document

#: V1 file-type scope -- see module docstring. Matched case-insensitively
#: against a candidate file's suffix.
PLAIN_TEXT_EXTENSIONS = frozenset({".md", ".markdown", ".txt", ".rst"})

#: Win32 `FILE_ATTRIBUTE_*` bits, only ever present on `os.stat_result
#: .st_file_attributes` on Windows -- see module docstring's OneDrive
#: section. Named here as plain module-level ints (not imported from
#: anywhere platform-specific) so this module still imports cleanly on
#: macOS/Linux; the attribute simply won't be present on those
#: platforms' `stat` results, handled via `getattr(..., None)` below.
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000
_FILE_ATTRIBUTE_OFFLINE = 0x00001000


def _real_path_within_allowlist(path: Path, allowed_dirs: Sequence[Path]) -> Optional[Tuple[Path, Path]]:
    """The core path-safety check -- see module docstring's "precision
    requirement" section. Resolves `path`'s real, symlink-followed
    location and checks it's actually inside one of `allowed_dirs`
    (themselves assumed already-resolved, real paths -- see each
    connector's config model). Returns `(real_path, containing_allowed_dir)`
    if safe, `None` if `path` doesn't resolve to anything inside any
    allowed directory (including: doesn't exist, isn't accessible, or
    resolves -- directly or via a symlink -- outside every allowed dir).
    Never raises; callers decide whether a `None` result means "skip
    silently" (`search`'s directory walk) or "refuse loudly"
    (`require_within_allowlist`, used by `fetch()`)."""
    try:
        real = path.resolve(strict=True)
    except OSError:
        return None
    for allowed in allowed_dirs:
        try:
            real.relative_to(allowed)
        except ValueError:
            continue
        return real, allowed
    return None


def require_within_allowlist(path: Path, allowed_dirs: Sequence[Path]) -> Tuple[Path, Path]:
    """The loud-refusal counterpart to `_real_path_within_allowlist`,
    used by `fetch_local_file` for caller-supplied ids. Raises
    `ConnectorAPIError` -- never silently resolves and reads -- if `path`
    doesn't resolve to a real location inside one of `allowed_dirs`. This
    is what makes a path-traversal id (e.g. `"../../etc/passwd"`) or a
    symlink pointing outside the allowlist a hard, explicit failure
    rather than a quietly-narrowed no-op."""
    result = _real_path_within_allowlist(path, allowed_dirs)
    if result is None:
        raise ConnectorAPIError(
            f"{str(path)!r} does not resolve to a real, existing path inside any "
            "of this connector's allowlisted directories -- refused. This "
            "connector rejects path-traversal-style ids and symlinks that "
            "resolve outside the configured allowlist rather than silently "
            "following them."
        )
    return result


def looks_like_cloud_only_placeholder(real_path: Path) -> bool:
    """Best-effort OneDrive Files-On-Demand cloud-only-placeholder
    detector -- see module docstring's dedicated section for exactly
    what this does and does not catch. `real_path` must already be a
    resolved, verified-safe path (i.e. the output of
    `_real_path_within_allowlist`/`require_within_allowlist`), not raw
    caller input."""
    try:
        st = real_path.stat()
    except OSError:
        return False

    file_attributes = getattr(st, "st_file_attributes", None)
    if file_attributes is not None and file_attributes & (
        _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS | _FILE_ATTRIBUTE_OFFLINE
    ):
        return True

    return st.st_size == 0


def iter_candidate_files(allowed_dirs: Sequence[Path]) -> Iterator[Tuple[Path, Path]]:
    """Walk every allowlisted directory, yielding `(real_path,
    containing_allowed_dir)` for every plain-text-extension regular file
    found -- symlink-escape-guarded (see module docstring): a symlink
    resolving outside every allowed directory is silently dropped, not
    yielded. Directories that vanish or become unreadable between
    config-load time and a query (e.g. an unmounted removable drive) are
    skipped, not a hard failure of the whole search -- a `search()` call
    intentionally degrades gracefully to "found nothing there" rather
    than aborting the entire multi-directory search over one bad entry.
    Sorted for deterministic ordering (tests, and stable "first N
    results" behavior under `result_limit`)."""
    for directory in allowed_dirs:
        try:
            if not directory.is_dir():
                continue
            candidates = sorted(directory.rglob("*"))
        except OSError:
            continue
        for candidate in candidates:
            try:
                if not candidate.is_file():
                    continue
            except OSError:
                continue
            if candidate.suffix.lower() not in PLAIN_TEXT_EXTENSIONS:
                continue
            resolved = _real_path_within_allowlist(candidate, allowed_dirs)
            if resolved is None:
                continue
            yield resolved


def _read_text(real_path: Path) -> Optional[str]:
    try:
        return real_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _to_document(*, real_path: Path, container: Path, text: str, source: str, query: Optional[str]) -> Document:
    last_modified = None
    try:
        last_modified = _mtime_to_datetime(real_path.stat().st_mtime)
    except OSError:
        pass

    snippet = _build_snippet(text, query)
    return Document(
        id=str(real_path),
        title=real_path.name,
        snippet=snippet,
        source=source,
        url=real_path.as_uri(),
        last_modified=last_modified,
        container=str(container),
        metadata={"extension": real_path.suffix.lower(), "size_bytes": len(text.encode("utf-8"))},
    )


def _mtime_to_datetime(mtime: float):
    from datetime import datetime, timezone

    return datetime.fromtimestamp(mtime, tz=timezone.utc)


def _build_snippet(text: str, query: Optional[str], *, context_chars: int = 200, max_len: int = 2000) -> str:
    """A short, human-useful snippet: text around the first match of
    `query` if given (case-insensitive), else just the start of the
    file. Always capped at `max_len` -- mirrors the other connectors'
    "cap snippet length" convention (e.g. Jira's `snippet[:2000]`)."""
    if query:
        lower_text = text.lower()
        idx = lower_text.find(query.lower())
        if idx != -1:
            start = max(0, idx - context_chars)
            end = min(len(text), idx + len(query) + context_chars)
            prefix = "..." if start > 0 else ""
            suffix = "..." if end < len(text) else ""
            return (prefix + text[start:end] + suffix)[:max_len]
    return text[:max_len]


def search_local_files(
    query: str,
    allowed_dirs: Sequence[Path],
    *,
    source: str,
    limit: int,
    detect_cloud_placeholders: bool = False,
) -> List[Document]:
    """Live, no-index text search over `allowed_dirs` (already
    config-time-resolved and, if this is a narrowed request, already
    `enforce_allowlist`-validated by the caller -- this function itself
    trusts `allowed_dirs` as the scope to search, exactly like
    `JiraClient.search()` trusts the JQL it's handed). Case-insensitive
    plain substring match over each candidate file's content. Capped at
    `limit` results, stopping the walk early once reached (never an
    unbounded scan-everything-then-cap)."""
    query_norm = (query or "").strip()
    if not query_norm:
        raise ConnectorAPIError("query text must not be empty")

    needle = query_norm.lower()
    results: List[Document] = []
    for real_path, container in iter_candidate_files(allowed_dirs):
        if detect_cloud_placeholders and looks_like_cloud_only_placeholder(real_path):
            # A cloud-only placeholder has no real local content to
            # search -- skip it gracefully rather than matching on
            # nothing or raising mid-walk. See module docstring.
            continue
        text = _read_text(real_path)
        if text is None:
            continue
        if needle not in text.lower():
            continue
        results.append(_to_document(real_path=real_path, container=container, text=text, source=source, query=query_norm))
        if len(results) >= limit:
            break
    return results


def fetch_local_file(
    file_id: str,
    allowed_dirs: Sequence[Path],
    *,
    source: str,
    detect_cloud_placeholders: bool = False,
) -> Document:
    """Fetch one file by id (an absolute path string, normally a
    previous `search_local_files` result's `Document.id`). Path-safety
    is enforced via `require_within_allowlist` -- see module docstring --
    which is what makes a path-traversal or symlink-escape id a hard,
    explicit `ConnectorAPIError` rather than a silently-resolved read."""
    raw = (file_id or "").strip()
    if not raw:
        raise ConnectorAPIError("file id must not be empty")

    real_path, container = require_within_allowlist(Path(raw).expanduser(), allowed_dirs)

    if not real_path.is_file():
        raise ConnectorAPIError(f"{file_id!r} does not resolve to a regular file")
    if real_path.suffix.lower() not in PLAIN_TEXT_EXTENSIONS:
        raise ConnectorAPIError(
            f"{file_id!r} has extension {real_path.suffix!r}, which is outside this "
            f"connector's V1 plain-text scope ({sorted(PLAIN_TEXT_EXTENSIONS)!r}). "
            "PDF/Word/Excel/image formats are a real, deliberately deferred gap -- "
            "see todo.md -- not attempted here."
        )
    if detect_cloud_placeholders and looks_like_cloud_only_placeholder(real_path):
        raise ConnectorAPIError(
            f"{file_id!r} looks like a OneDrive Files-On-Demand cloud-only "
            "placeholder with no local content yet (zero-byte, or flagged "
            "offline/recall-on-access on Windows) -- open it via the OneDrive "
            "client, Explorer, or Finder first to download it, then fetch again. "
            "This detection is best-effort -- see mcp_connectors/local_fs/search.py's "
            "module docstring for exactly what is and isn't checked."
        )

    text = _read_text(real_path)
    if text is None:
        raise ConnectorAPIError(f"failed to read {file_id!r} ({real_path})")
    return _to_document(real_path=real_path, container=container, text=text, source=source, query=None)
