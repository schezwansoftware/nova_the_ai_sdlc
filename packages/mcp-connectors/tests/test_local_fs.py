"""Tests for `mcp_connectors/local_fs/search.py`: the shared
walk/search/path-safety logic both `local_docs` and `onedrive` are built
on. This is where the actual security guarantees live -- see that
module's docstring -- so this file is the load-bearing proof for the
symlink-escape and path-traversal rejection behavior, exercised directly
against real `tmp_path` directories and real symlinks, not mocked.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from mcp_connectors.common import ConnectorAPIError
from mcp_connectors.local_fs import search as local_fs_search
from mcp_connectors.local_fs.search import (
    CODE_EXTENSIONS,
    OFFICE_EXTENSIONS,
    PDF_EXTENSIONS,
    PLAIN_TEXT_EXTENSIONS,
    TEXT_EXTENSIONS,
    extensions_for_categories,
    fetch_local_file,
    iter_candidate_files,
    looks_like_cloud_only_placeholder,
    missing_libraries_for_categories,
    require_within_allowlist,
    search_local_files,
)


def _write(path: Path, text: str = "hello world") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# -- iter_candidate_files / extension scope ------------------------------------


def test_iter_candidate_files_only_yields_plain_text_extensions(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    _write(allowed / "notes.md", "one")
    _write(allowed / "notes.markdown", "two")
    _write(allowed / "notes.txt", "three")
    _write(allowed / "notes.rst", "four")
    _write(allowed / "binary.pdf", "not really a pdf but wrong extension")
    _write(allowed / "sheet.xlsx", "nope")
    _write(allowed / "image.png", "nope")

    found = {real.name for real, _ in iter_candidate_files([allowed])}
    assert found == {"notes.md", "notes.markdown", "notes.txt", "notes.rst"}


def test_iter_candidate_files_skips_missing_directory_gracefully(tmp_path):
    missing = tmp_path / "does-not-exist"
    assert list(iter_candidate_files([missing])) == []


# -- path-safety: symlink escape -------------------------------------------------


def test_symlink_escaping_allowed_directory_is_excluded_from_search(tmp_path):
    outside = tmp_path / "outside"
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    secret = _write(outside / "secret.txt", "top secret content unique-marker-xyz")

    escape_link = allowed / "escape.txt"
    escape_link.symlink_to(secret)

    # The walk itself must not surface the symlink target.
    found = list(iter_candidate_files([allowed]))
    assert found == []

    # And a search for content that only exists via the symlink target
    # must not find it either.
    results = search_local_files("unique-marker-xyz", [allowed], source="test", limit=10)
    assert results == []


def test_symlink_escaping_allowed_directory_is_rejected_on_fetch(tmp_path):
    outside = tmp_path / "outside"
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    secret = _write(outside / "secret.txt", "top secret")

    escape_link = allowed / "escape.txt"
    escape_link.symlink_to(secret)

    with pytest.raises(ConnectorAPIError):
        fetch_local_file(str(escape_link), [allowed], source="test")


def test_symlink_within_allowlist_is_fine(tmp_path):
    """A symlink is only a problem when it resolves *outside* the
    allowlist -- one that stays inside (even via a different allowed
    directory) is legitimate and should work normally. `real.txt` and
    `link.txt` are two distinct directory entries reachable inside the
    allowed directory, so both legitimately appear (this isn't
    deduplicated -- that would require inode-level tracking this module
    doesn't attempt); what matters for the security guarantee is that
    both resolve *inside* the allowlist, not that they're deduplicated."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    real_file = _write(allowed / "real.txt", "genuine content marker-abc")
    link = allowed / "link.txt"
    link.symlink_to(real_file)

    results = search_local_files("marker-abc", [allowed], source="test", limit=10)
    assert len(results) == 2
    assert {doc.id for doc in results} == {str(real_file.resolve())}


# -- path-safety: path-traversal fetch() ids -------------------------------------


def test_fetch_rejects_path_traversal_id_that_resolves_outside_allowlist(tmp_path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    _write(outside / "secret.txt", "should never be readable")

    traversal_id = str(allowed / ".." / "outside" / "secret.txt")
    with pytest.raises(ConnectorAPIError):
        fetch_local_file(traversal_id, [allowed], source="test")


def test_fetch_rejects_traversal_style_id_even_when_target_does_not_exist(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()

    with pytest.raises(ConnectorAPIError):
        fetch_local_file("../../etc/passwd", [allowed], source="test")


def test_fetch_rejects_absolute_path_outside_allowlist(tmp_path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    secret = _write(outside / "secret.txt", "nope")

    with pytest.raises(ConnectorAPIError):
        fetch_local_file(str(secret), [allowed], source="test")


def test_fetch_rejects_empty_id():
    with pytest.raises(ConnectorAPIError):
        fetch_local_file("   ", [Path("/tmp")], source="test")


def test_require_within_allowlist_accepts_a_real_file_inside_allowlist(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    doc = _write(allowed / "doc.txt", "content")

    real, container = require_within_allowlist(doc, [allowed])
    assert real == doc.resolve()
    assert container == allowed.resolve()


# -- search_local_files / fetch_local_file happy paths ---------------------------


def test_search_finds_matching_files_case_insensitively(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    _write(allowed / "a.md", "The Quick Brown Fox")
    _write(allowed / "b.md", "nothing relevant here")

    results = search_local_files("quick brown", [allowed], source="test", limit=10)
    assert len(results) == 1
    assert results[0].title == "a.md"
    assert results[0].source == "test"
    assert results[0].container == str(allowed.resolve())
    assert "Quick Brown Fox" in results[0].snippet


def test_search_respects_result_limit(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    for i in range(5):
        _write(allowed / f"doc{i}.txt", "shared-needle content")

    results = search_local_files("shared-needle", [allowed], source="test", limit=2)
    assert len(results) == 2


def test_search_rejects_empty_query(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    with pytest.raises(ConnectorAPIError):
        search_local_files("   ", [allowed], source="test", limit=10)


def test_fetch_returns_full_content_and_valid_document(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    doc_path = _write(allowed / "doc.md", "full document body text")

    doc = fetch_local_file(str(doc_path), [allowed], source="test")
    assert doc.id == str(doc_path.resolve())
    assert doc.snippet == "full document body text"
    assert doc.container == str(allowed.resolve())


def test_fetch_rejects_non_plain_text_extension(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    pdf_path = _write(allowed / "report.pdf", "%PDF-1.4 fake")

    with pytest.raises(ConnectorAPIError) as excinfo:
        fetch_local_file(str(pdf_path), [allowed], source="test")
    assert "plain-text" in str(excinfo.value) or "extension" in str(excinfo.value)


def test_fetch_rejects_directory_id(tmp_path):
    allowed = tmp_path / "allowed"
    subdir = allowed / "subdir"
    subdir.mkdir(parents=True)

    with pytest.raises(ConnectorAPIError):
        fetch_local_file(str(subdir), [allowed], source="test")


# -- OneDrive Files-On-Demand cloud-only-placeholder detection -------------------


def test_looks_like_cloud_only_placeholder_flags_zero_byte_file(tmp_path):
    placeholder = tmp_path / "placeholder.txt"
    placeholder.write_bytes(b"")
    assert looks_like_cloud_only_placeholder(placeholder) is True


def test_looks_like_cloud_only_placeholder_does_not_flag_real_content(tmp_path):
    real_file = tmp_path / "real.txt"
    real_file.write_text("real content", encoding="utf-8")
    assert looks_like_cloud_only_placeholder(real_file) is False


def test_looks_like_cloud_only_placeholder_false_for_missing_path(tmp_path):
    missing = tmp_path / "missing.txt"
    assert looks_like_cloud_only_placeholder(missing) is False


@pytest.mark.skipif(os.name != "nt", reason="st_file_attributes only exists on Windows")
def test_looks_like_cloud_only_placeholder_checks_windows_attributes(tmp_path, monkeypatch):
    """Only meaningful on Windows -- skipped elsewhere, since
    `st_file_attributes` doesn't exist on POSIX `stat` results at all
    (see local_fs/search.py's module docstring)."""
    flagged = tmp_path / "flagged.txt"
    flagged.write_text("has content but is a cloud placeholder", encoding="utf-8")

    class _FakeStat:
        st_size = 42
        st_file_attributes = 0x00400000  # FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
        st_mtime = 0.0

    monkeypatch.setattr(Path, "stat", lambda self: _FakeStat())
    assert looks_like_cloud_only_placeholder(flagged) is True


def test_search_skips_cloud_only_placeholder_when_detection_enabled(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    _write(allowed / "real.md", "needle content here")
    (allowed / "placeholder.md").write_bytes(b"")  # zero-byte -> looks like placeholder

    results = search_local_files(
        "needle", [allowed], source="onedrive", limit=10, detect_cloud_placeholders=True
    )
    assert [doc.title for doc in results] == ["real.md"]


def test_fetch_raises_clear_error_for_cloud_only_placeholder_when_detection_enabled(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    placeholder = allowed / "placeholder.md"
    placeholder.write_bytes(b"")

    with pytest.raises(ConnectorAPIError) as excinfo:
        fetch_local_file(str(placeholder), [allowed], source="onedrive", detect_cloud_placeholders=True)
    assert "placeholder" in str(excinfo.value).lower()


def test_local_docs_style_zero_byte_file_is_not_flagged_when_detection_disabled(tmp_path):
    """local_docs never opts into placeholder detection -- a genuinely
    empty .txt file there is just an empty file, not a suspected
    cloud-only placeholder (see local_fs/search.py's module docstring on
    why this is opt-in per connector)."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    empty = allowed / "empty.txt"
    empty.write_bytes(b"")

    doc = fetch_local_file(str(empty), [allowed], source="local_docs", detect_cloud_placeholders=False)
    assert doc.snippet == ""


def test_plain_text_extensions_constant_matches_documented_scope():
    assert PLAIN_TEXT_EXTENSIONS == frozenset({".md", ".markdown", ".txt", ".rst"})


# -- file_categories: extension resolution ---------------------------------------


def test_extensions_for_categories_text_only_is_the_default_scope():
    assert extensions_for_categories(["text"]) == TEXT_EXTENSIONS


def test_extensions_for_categories_unions_multiple_categories():
    result = extensions_for_categories(["text", "code"])
    assert result == TEXT_EXTENSIONS | CODE_EXTENSIONS


def test_extensions_for_categories_office_and_pdf():
    assert extensions_for_categories(["office"]) == OFFICE_EXTENSIONS
    assert extensions_for_categories(["pdf"]) == PDF_EXTENSIONS


def test_extensions_for_categories_unknown_category_contributes_nothing():
    assert extensions_for_categories(["not-a-real-category"]) == frozenset()


# -- file_categories: the "permission" gate -- withheld access when not opted in --


def test_search_does_not_find_code_file_when_only_text_category_enabled(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "script.py").write_text("unique-code-marker-1", encoding="utf-8")

    results = search_local_files(
        "unique-code-marker-1", [allowed], source="test", limit=10, file_categories=["text"]
    )
    assert results == []


def test_search_finds_code_file_once_code_category_enabled(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "script.py").write_text("unique-code-marker-2", encoding="utf-8")

    results = search_local_files(
        "unique-code-marker-2", [allowed], source="test", limit=10, file_categories=["text", "code"]
    )
    assert [doc.title for doc in results] == ["script.py"]


def test_fetch_rejects_code_file_when_category_not_enabled(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    script = allowed / "script.py"
    script.write_text("print('hi')", encoding="utf-8")

    with pytest.raises(ConnectorAPIError) as excinfo:
        fetch_local_file(str(script), [allowed], source="test", file_categories=["text"])
    assert "file_categories" in str(excinfo.value)


def test_fetch_succeeds_for_code_file_once_category_enabled(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    script = allowed / "script.py"
    script.write_text("print('hi')", encoding="utf-8")

    doc = fetch_local_file(str(script), [allowed], source="test", file_categories=["text", "code"])
    assert "print" in doc.snippet


def test_search_default_file_categories_is_text_only(tmp_path):
    """`search_local_files`'s own default (no `file_categories` passed
    at all) must match `DEFAULT_FILE_CATEGORIES` / each config model's
    default -- i.e. any caller written before file_categories existed
    keeps its original text-only behavior unchanged."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "script.py").write_text("unique-code-marker-3", encoding="utf-8")
    (allowed / "note.md").write_text("unique-code-marker-3", encoding="utf-8")

    results = search_local_files("unique-code-marker-3", [allowed], source="test", limit=10)
    assert [doc.title for doc in results] == ["note.md"]


# -- missing_libraries_for_categories: the config-validation-time library check --


def test_missing_libraries_for_categories_empty_when_nothing_requires_a_library():
    assert missing_libraries_for_categories(["text", "code"]) == []


def test_missing_libraries_for_categories_reports_all_three_office_libs(monkeypatch):
    monkeypatch.setattr(local_fs_search, "_DOCX_IMPORT_ERROR", ImportError("no docx"))
    monkeypatch.setattr(local_fs_search, "_OPENPYXL_IMPORT_ERROR", ImportError("no openpyxl"))
    monkeypatch.setattr(local_fs_search, "_PPTX_IMPORT_ERROR", ImportError("no pptx"))
    missing = missing_libraries_for_categories(["office"])
    assert set(missing) == {"python-docx", "openpyxl", "python-pptx"}


def test_missing_libraries_for_categories_reports_pypdf_only_for_pdf_category(monkeypatch):
    monkeypatch.setattr(local_fs_search, "_PYPDF_IMPORT_ERROR", ImportError("no pypdf"))
    assert missing_libraries_for_categories(["pdf"]) == ["pypdf"]


def test_missing_libraries_for_categories_partial_office_availability(monkeypatch):
    """Only one of the three office libraries missing still reports just
    that one -- config validation (in local_docs/onedrive's config.py)
    is what turns this into a hard failure for the whole 'office'
    category; this function itself just reports facts."""
    monkeypatch.setattr(local_fs_search, "_DOCX_IMPORT_ERROR", ImportError("no docx"))
    monkeypatch.setattr(local_fs_search, "_OPENPYXL_IMPORT_ERROR", None)
    monkeypatch.setattr(local_fs_search, "_PPTX_IMPORT_ERROR", None)
    assert missing_libraries_for_categories(["office"]) == ["python-docx"]


# -- extraction dispatch when a required library truly isn't importable ----------


def test_extract_text_returns_none_for_pdf_when_pypdf_unavailable(tmp_path, monkeypatch):
    """`_extract_text`'s per-format functions must degrade to `None`
    (skip), never raise, when the corresponding library isn't
    importable -- this is the defensive fallback path that makes
    `search()`/`fetch()` never crash even if a config somehow ended up
    requesting a category whose library went missing after config
    validation already passed (e.g. an environment change between config
    load and a later query)."""
    monkeypatch.setattr(local_fs_search, "_PYPDF_IMPORT_ERROR", ImportError("no pypdf"))
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    fake_pdf = allowed / "doc.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 pretend content")

    assert local_fs_search._extract_text(fake_pdf) is None


# -- symlink escape guard applies identically to office/PDF-extension targets ----


def test_symlink_escape_is_rejected_for_a_pdf_extension_target(tmp_path):
    """The path-safety check runs before any type-specific extraction is
    even attempted (see local_fs/search.py's module docstring) -- proven
    here with a `.pdf`-extension symlink whose *target* is just garbage
    bytes (parseability is irrelevant; the symlink must never even reach
    the extraction step in the first place)."""
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside_pdf = outside / "secret.pdf"
    outside_pdf.parent.mkdir(parents=True)
    outside_pdf.write_bytes(b"not a real pdf, just a marker for the escape test")

    escape_link = allowed / "escape.pdf"
    escape_link.symlink_to(outside_pdf)

    # search: never surfaced, even with "pdf" opted in
    results = search_local_files(
        "marker", [allowed], source="test", limit=10, file_categories=["pdf"]
    )
    assert results == []

    # fetch: refused outright, not silently resolved and (attempted to be) parsed
    with pytest.raises(ConnectorAPIError):
        fetch_local_file(str(escape_link), [allowed], source="test", file_categories=["pdf"])
