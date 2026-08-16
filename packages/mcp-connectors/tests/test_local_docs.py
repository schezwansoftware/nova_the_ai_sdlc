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


# -- LocalDocsClient --------------------------------------------------------------


def _config(tmp_path, *dirs, result_limit=10):
    return LocalDocsConnectorConfig(allowed_directories=[str(d) for d in dirs], result_limit=result_limit)


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
