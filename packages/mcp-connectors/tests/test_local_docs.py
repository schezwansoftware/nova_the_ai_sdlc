"""Tests for the local-directories connector: config validation/
round-trip (directory resolution/dedupe, non-empty allowlist, result
limit bounds) and `LocalDocsClient.search()`/`fetch()` against real
`tmp_path` directories -- no injected fake transport needed here (unlike
Jira/Confluence/SharePoint), since this connector never makes a network
call at all.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from mcp_connectors.common import ConnectorAPIError, ConnectorConfigError
from mcp_connectors.local_docs.client import LocalDocsClient
from mcp_connectors.local_docs.config import (
    LocalDocsConnectorConfig,
    config_path,
    load_config,
    save_config,
)
from mcp_connectors.local_fs import search as local_fs_search


# -- LocalDocsConnectorConfig ---------------------------------------------------


def test_allowed_directories_resolved_and_deduped(tmp_path):
    real_dir = tmp_path / "notes"
    real_dir.mkdir()
    symlink_dir = tmp_path / "notes-link"
    symlink_dir.symlink_to(real_dir)

    config = LocalDocsConnectorConfig(allowed_directories=[str(real_dir), str(symlink_dir)])
    # both entries resolve to the same real directory -> deduped to one
    assert config.allowed_directories == [str(real_dir.resolve())]


def test_allowed_directories_must_be_non_empty():
    with pytest.raises(ValidationError):
        LocalDocsConnectorConfig(allowed_directories=[])


def test_allowed_directories_rejects_nonexistent_path(tmp_path):
    missing = tmp_path / "does-not-exist"
    with pytest.raises(ValidationError):
        LocalDocsConnectorConfig(allowed_directories=[str(missing)])


def test_allowed_directories_rejects_a_file_not_a_directory(tmp_path):
    a_file = tmp_path / "file.txt"
    a_file.write_text("x", encoding="utf-8")
    with pytest.raises(ValidationError):
        LocalDocsConnectorConfig(allowed_directories=[str(a_file)])


def test_result_limit_default_and_bounds(tmp_path):
    allowed = tmp_path / "dir"
    allowed.mkdir()
    config = LocalDocsConnectorConfig(allowed_directories=[str(allowed)])
    assert config.result_limit == 15
    with pytest.raises(ValidationError):
        LocalDocsConnectorConfig(allowed_directories=[str(allowed)], result_limit=0)
    with pytest.raises(ValidationError):
        LocalDocsConnectorConfig(allowed_directories=[str(allowed)], result_limit=51)


def test_config_round_trips_through_save_and_load(monkeypatch, tmp_path):
    config_dir = tmp_path / "config-home"
    monkeypatch.setenv("MCP_CONNECTORS_CONFIG_DIR", str(config_dir))
    allowed = tmp_path / "docs"
    allowed.mkdir()

    config = LocalDocsConnectorConfig(allowed_directories=[str(allowed)], result_limit=20)
    save_config(config)

    loaded = load_config()
    assert loaded.allowed_directories == [str(allowed.resolve())]
    assert loaded.result_limit == 20

    on_disk = json.loads(config_path().read_text(encoding="utf-8"))
    assert on_disk["allowed_directories"] == [str(allowed.resolve())]
    assert "credential" not in on_disk


def test_load_config_raises_clear_error_when_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_CONNECTORS_CONFIG_DIR", str(tmp_path))
    with pytest.raises(ConnectorConfigError) as excinfo:
        load_config()
    assert "local_docs.json" in str(excinfo.value) or str(tmp_path) in str(excinfo.value)


# -- file_categories: the config-time "permission" gate --------------------------


def test_file_categories_defaults_to_text_only(tmp_path):
    allowed = tmp_path / "dir"
    allowed.mkdir()
    config = LocalDocsConnectorConfig(allowed_directories=[str(allowed)])
    assert config.file_categories == ["text"]


def test_file_categories_backward_compatible_with_configs_predating_the_field(tmp_path):
    """A config JSON payload with no `file_categories` key at all (as
    every config written before this field existed would be) must load
    exactly as before -- this is the literal backward-compatibility
    requirement, exercised via `model_validate_json` on a hand-written
    payload, not just the Python-level default."""
    allowed = tmp_path / "dir"
    allowed.mkdir()
    payload = f'{{"allowed_directories": ["{allowed}"]}}'
    config = LocalDocsConnectorConfig.model_validate_json(payload)
    assert config.file_categories == ["text"]


def test_file_categories_accepts_code_and_deduplicates(tmp_path):
    allowed = tmp_path / "dir"
    allowed.mkdir()
    config = LocalDocsConnectorConfig(allowed_directories=[str(allowed)], file_categories=["text", "code", "text"])
    assert config.file_categories == ["text", "code"]


def test_file_categories_rejects_unknown_category(tmp_path):
    allowed = tmp_path / "dir"
    allowed.mkdir()
    with pytest.raises(ValidationError):
        LocalDocsConnectorConfig(allowed_directories=[str(allowed)], file_categories=["not-a-real-category"])


def test_file_categories_office_without_documents_extra_raises_clear_config_error(tmp_path, monkeypatch):
    monkeypatch.setattr(local_fs_search, "_DOCX_IMPORT_ERROR", ImportError("no docx"))
    monkeypatch.setattr(local_fs_search, "_OPENPYXL_IMPORT_ERROR", ImportError("no openpyxl"))
    monkeypatch.setattr(local_fs_search, "_PPTX_IMPORT_ERROR", ImportError("no pptx"))
    allowed = tmp_path / "dir"
    allowed.mkdir()
    with pytest.raises(ValidationError) as excinfo:
        LocalDocsConnectorConfig(allowed_directories=[str(allowed)], file_categories=["office"])
    assert "documents" in str(excinfo.value)


def test_file_categories_pdf_without_documents_extra_raises_clear_config_error(tmp_path, monkeypatch):
    monkeypatch.setattr(local_fs_search, "_PYPDF_IMPORT_ERROR", ImportError("no pypdf"))
    allowed = tmp_path / "dir"
    allowed.mkdir()
    with pytest.raises(ValidationError) as excinfo:
        LocalDocsConnectorConfig(allowed_directories=[str(allowed)], file_categories=["pdf"])
    assert "pypdf" in str(excinfo.value)


def test_file_categories_office_and_pdf_succeed_when_libraries_available(tmp_path):
    """The inverse of the two tests above -- in this project's own venv
    (with the `documents` extra actually installed), requesting
    'office'/'pdf' must succeed cleanly, not just fail loudly when
    unavailable."""
    pytest.importorskip("docx")
    pytest.importorskip("openpyxl")
    pytest.importorskip("pptx")
    pytest.importorskip("pypdf")
    allowed = tmp_path / "dir"
    allowed.mkdir()
    config = LocalDocsConnectorConfig(allowed_directories=[str(allowed)], file_categories=["office", "pdf"])
    assert config.file_categories == ["office", "pdf"]


def test_config_round_trip_persists_file_categories(monkeypatch, tmp_path):
    config_dir = tmp_path / "config-home"
    monkeypatch.setenv("MCP_CONNECTORS_CONFIG_DIR", str(config_dir))
    allowed = tmp_path / "docs"
    allowed.mkdir()

    config = LocalDocsConnectorConfig(allowed_directories=[str(allowed)], file_categories=["text", "code"])
    save_config(config)

    loaded = load_config()
    assert loaded.file_categories == ["text", "code"]


# -- LocalDocsClient --------------------------------------------------------------


def _config(tmp_path, *dirs, result_limit=10, file_categories=None):
    kwargs = {}
    if file_categories is not None:
        kwargs["file_categories"] = file_categories
    return LocalDocsConnectorConfig(allowed_directories=[str(d) for d in dirs], result_limit=result_limit, **kwargs)


def test_search_and_fetch_happy_path(tmp_path):
    allowed = tmp_path / "docs"
    allowed.mkdir()
    (allowed / "readme.md").write_text("Project overview and setup instructions", encoding="utf-8")
    (allowed / "other.md").write_text("unrelated content", encoding="utf-8")

    client = LocalDocsClient(_config(tmp_path, allowed))
    results = client.search("setup instructions")
    assert len(results) == 1
    assert results[0].source == "local_docs"
    assert results[0].title == "readme.md"

    fetched = client.fetch(results[0].id)
    assert "Project overview" in fetched.snippet


def test_search_rejects_directory_outside_allowlist_without_touching_disk(tmp_path):
    allowed = tmp_path / "docs"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("classified", encoding="utf-8")

    client = LocalDocsClient(_config(tmp_path, allowed))
    with pytest.raises(ConnectorConfigError):
        client.search("classified", directories=[str(outside)])


def test_fetch_rejects_file_outside_allowlist(tmp_path):
    allowed = tmp_path / "docs"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside_file = outside / "secret.md"
    outside_file.parent.mkdir(parents=True)
    outside_file.write_text("classified", encoding="utf-8")

    client = LocalDocsClient(_config(tmp_path, allowed))
    with pytest.raises(ConnectorAPIError):
        client.fetch(str(outside_file))


def test_fetch_rejects_path_traversal_id(tmp_path):
    allowed = tmp_path / "docs"
    allowed.mkdir()

    client = LocalDocsClient(_config(tmp_path, allowed))
    with pytest.raises(ConnectorAPIError):
        client.fetch("../../etc/passwd")


def test_search_does_not_flag_empty_file_as_cloud_placeholder(tmp_path):
    """local_docs is not OneDrive -- a genuinely empty file just fetches
    as empty content, not a rejected "cloud-only placeholder" (see
    local_fs/search.py's module docstring)."""
    allowed = tmp_path / "docs"
    allowed.mkdir()
    empty = allowed / "empty.md"
    empty.write_bytes(b"")

    client = LocalDocsClient(_config(tmp_path, allowed))
    fetched = client.fetch(str(empty))
    assert fetched.snippet == ""


def test_pdf_files_are_never_returned_by_search(tmp_path):
    allowed = tmp_path / "docs"
    allowed.mkdir()
    (allowed / "manual.pdf").write_bytes(b"%PDF-1.4 unique-search-term")

    client = LocalDocsClient(_config(tmp_path, allowed))
    assert client.search("unique-search-term") == []


# -- file_categories gating, exercised through the real client, not just local_fs --


def test_code_file_invisible_to_search_with_default_file_categories(tmp_path):
    """The exact scenario the project owner used as the litmus test for
    this feature: a `.py` file present in an allowed directory is
    invisible to `search()` when `file_categories` is left at the
    default `["text"]`."""
    allowed = tmp_path / "docs"
    allowed.mkdir()
    (allowed / "script.py").write_text("def unique_code_marker(): pass", encoding="utf-8")

    client = LocalDocsClient(_config(tmp_path, allowed))  # default file_categories
    assert client.search("unique_code_marker") == []


def test_code_file_found_once_code_category_opted_into_via_config(tmp_path):
    allowed = tmp_path / "docs"
    allowed.mkdir()
    (allowed / "script.py").write_text("def unique_code_marker(): pass", encoding="utf-8")

    client = LocalDocsClient(_config(tmp_path, allowed, file_categories=["text", "code"]))
    results = client.search("unique_code_marker")
    assert [doc.title for doc in results] == ["script.py"]

    fetched = client.fetch(results[0].id)
    assert "unique_code_marker" in fetched.snippet


def test_fetch_code_file_refused_with_default_file_categories(tmp_path):
    allowed = tmp_path / "docs"
    allowed.mkdir()
    script = allowed / "script.py"
    script.write_text("print('hi')", encoding="utf-8")

    client = LocalDocsClient(_config(tmp_path, allowed))
    with pytest.raises(ConnectorAPIError):
        client.fetch(str(script))
