"""Tests for the OneDrive connector: config validation/round-trip
(structurally identical to local_docs -- see config.py's docstring for
why) and `OneDriveClient.search()`/`fetch()` against real `tmp_path`
directories standing in for a synced OneDrive folder, including the
Files-On-Demand cloud-only-placeholder handling this connector opts into
that `local_docs` deliberately does not.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from mcp_connectors.common import ConnectorAPIError, ConnectorConfigError
from mcp_connectors.local_fs import search as local_fs_search
from mcp_connectors.onedrive.client import OneDriveClient
from mcp_connectors.onedrive.config import (
    OneDriveConnectorConfig,
    config_path,
    load_config,
    save_config,
)


# -- OneDriveConnectorConfig ------------------------------------------------------


def test_allowed_directories_resolved_and_deduped(tmp_path):
    real_dir = tmp_path / "OneDrive-Contoso"
    real_dir.mkdir()
    symlink_dir = tmp_path / "OneDrive-link"
    symlink_dir.symlink_to(real_dir)

    config = OneDriveConnectorConfig(allowed_directories=[str(real_dir), str(symlink_dir)])
    assert config.allowed_directories == [str(real_dir.resolve())]


def test_allowed_directories_must_be_non_empty():
    with pytest.raises(ValidationError):
        OneDriveConnectorConfig(allowed_directories=[])


def test_allowed_directories_rejects_nonexistent_path(tmp_path):
    missing = tmp_path / "OneDrive-not-synced-yet"
    with pytest.raises(ValidationError):
        OneDriveConnectorConfig(allowed_directories=[str(missing)])


def test_result_limit_default_and_bounds(tmp_path):
    allowed = tmp_path / "OneDrive"
    allowed.mkdir()
    config = OneDriveConnectorConfig(allowed_directories=[str(allowed)])
    assert config.result_limit == 15
    with pytest.raises(ValidationError):
        OneDriveConnectorConfig(allowed_directories=[str(allowed)], result_limit=0)
    with pytest.raises(ValidationError):
        OneDriveConnectorConfig(allowed_directories=[str(allowed)], result_limit=51)


def test_config_round_trips_through_save_and_load(monkeypatch, tmp_path):
    config_dir = tmp_path / "config-home"
    monkeypatch.setenv("MCP_CONNECTORS_CONFIG_DIR", str(config_dir))
    allowed = tmp_path / "OneDrive-Contoso"
    allowed.mkdir()

    config = OneDriveConnectorConfig(allowed_directories=[str(allowed)], result_limit=25)
    save_config(config)

    loaded = load_config()
    assert loaded.allowed_directories == [str(allowed.resolve())]
    assert loaded.result_limit == 25

    on_disk = json.loads(config_path().read_text(encoding="utf-8"))
    assert on_disk["allowed_directories"] == [str(allowed.resolve())]
    assert "credential" not in on_disk


def test_load_config_raises_clear_error_when_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_CONNECTORS_CONFIG_DIR", str(tmp_path))
    with pytest.raises(ConnectorConfigError) as excinfo:
        load_config()
    assert "onedrive.json" in str(excinfo.value) or str(tmp_path) in str(excinfo.value)


# -- file_categories: the config-time "permission" gate --------------------------
# (identical mechanism to local_docs -- see tests/test_local_docs.py for the
# fuller commentary; duplicated here since each connector's config model is
# its own self-contained module, per this package's established convention)


def test_file_categories_defaults_to_text_only(tmp_path):
    allowed = tmp_path / "OneDrive"
    allowed.mkdir()
    config = OneDriveConnectorConfig(allowed_directories=[str(allowed)])
    assert config.file_categories == ["text"]


def test_file_categories_backward_compatible_with_configs_predating_the_field(tmp_path):
    allowed = tmp_path / "OneDrive"
    allowed.mkdir()
    payload = f'{{"allowed_directories": ["{allowed}"]}}'
    config = OneDriveConnectorConfig.model_validate_json(payload)
    assert config.file_categories == ["text"]


def test_file_categories_rejects_unknown_category(tmp_path):
    allowed = tmp_path / "OneDrive"
    allowed.mkdir()
    with pytest.raises(ValidationError):
        OneDriveConnectorConfig(allowed_directories=[str(allowed)], file_categories=["not-a-real-category"])


def test_file_categories_office_without_documents_extra_raises_clear_config_error(tmp_path, monkeypatch):
    monkeypatch.setattr(local_fs_search, "_DOCX_IMPORT_ERROR", ImportError("no docx"))
    monkeypatch.setattr(local_fs_search, "_OPENPYXL_IMPORT_ERROR", ImportError("no openpyxl"))
    monkeypatch.setattr(local_fs_search, "_PPTX_IMPORT_ERROR", ImportError("no pptx"))
    allowed = tmp_path / "OneDrive"
    allowed.mkdir()
    with pytest.raises(ValidationError) as excinfo:
        OneDriveConnectorConfig(allowed_directories=[str(allowed)], file_categories=["office"])
    assert "documents" in str(excinfo.value)


def test_file_categories_pdf_without_documents_extra_raises_clear_config_error(tmp_path, monkeypatch):
    monkeypatch.setattr(local_fs_search, "_PYPDF_IMPORT_ERROR", ImportError("no pypdf"))
    allowed = tmp_path / "OneDrive"
    allowed.mkdir()
    with pytest.raises(ValidationError) as excinfo:
        OneDriveConnectorConfig(allowed_directories=[str(allowed)], file_categories=["pdf"])
    assert "pypdf" in str(excinfo.value)


def test_config_round_trip_persists_file_categories(monkeypatch, tmp_path):
    config_dir = tmp_path / "config-home"
    monkeypatch.setenv("MCP_CONNECTORS_CONFIG_DIR", str(config_dir))
    allowed = tmp_path / "OneDrive-Contoso"
    allowed.mkdir()

    config = OneDriveConnectorConfig(allowed_directories=[str(allowed)], file_categories=["text", "code"])
    save_config(config)

    loaded = load_config()
    assert loaded.file_categories == ["text", "code"]


# -- OneDriveClient -----------------------------------------------------------------


def _config(tmp_path, *dirs, result_limit=10, file_categories=None):
    kwargs = {}
    if file_categories is not None:
        kwargs["file_categories"] = file_categories
    return OneDriveConnectorConfig(allowed_directories=[str(d) for d in dirs], result_limit=result_limit, **kwargs)


def test_search_and_fetch_happy_path(tmp_path):
    allowed = tmp_path / "OneDrive-Contoso"
    allowed.mkdir()
    (allowed / "plan.md").write_text("Quarterly roadmap and milestones", encoding="utf-8")
    (allowed / "other.md").write_text("unrelated content", encoding="utf-8")

    client = OneDriveClient(_config(tmp_path, allowed))
    results = client.search("roadmap")
    assert len(results) == 1
    assert results[0].source == "onedrive"
    assert results[0].title == "plan.md"

    fetched = client.fetch(results[0].id)
    assert "Quarterly roadmap" in fetched.snippet


def test_search_rejects_directory_outside_allowlist(tmp_path):
    allowed = tmp_path / "OneDrive-Contoso"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    client = OneDriveClient(_config(tmp_path, allowed))
    with pytest.raises(ConnectorConfigError):
        client.search("anything", directories=[str(outside)])


def test_fetch_rejects_path_traversal_id(tmp_path):
    allowed = tmp_path / "OneDrive-Contoso"
    allowed.mkdir()

    client = OneDriveClient(_config(tmp_path, allowed))
    with pytest.raises(ConnectorAPIError):
        client.fetch("../../etc/passwd")


def test_fetch_rejects_symlink_escaping_allowlist(tmp_path):
    allowed = tmp_path / "OneDrive-Contoso"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside_file = outside / "secret.md"
    outside_file.parent.mkdir(parents=True)
    outside_file.write_text("classified", encoding="utf-8")

    escape_link = allowed / "escape.md"
    escape_link.symlink_to(outside_file)

    client = OneDriveClient(_config(tmp_path, allowed))
    with pytest.raises(ConnectorAPIError):
        client.fetch(str(escape_link))
    # And the symlink target's content must never surface via search either.
    assert client.search("classified") == []


# -- OneDrive Files-On-Demand cloud-only placeholder handling ---------------------


def test_search_skips_cloud_only_placeholder_files(tmp_path):
    allowed = tmp_path / "OneDrive-Contoso"
    allowed.mkdir()
    (allowed / "real.md").write_text("shared-needle real content", encoding="utf-8")
    (allowed / "placeholder.md").write_bytes(b"")  # zero-byte cloud-only placeholder

    client = OneDriveClient(_config(tmp_path, allowed))
    results = client.search("shared-needle")
    assert [doc.title for doc in results] == ["real.md"]


def test_fetch_cloud_only_placeholder_raises_clear_error_not_empty_content(tmp_path):
    allowed = tmp_path / "OneDrive-Contoso"
    allowed.mkdir()
    placeholder = allowed / "placeholder.md"
    placeholder.write_bytes(b"")

    client = OneDriveClient(_config(tmp_path, allowed))
    with pytest.raises(ConnectorAPIError) as excinfo:
        client.fetch(str(placeholder))
    assert "placeholder" in str(excinfo.value).lower()


# -- file_categories gating, exercised through the real client -------------------


def test_code_file_invisible_to_search_with_default_file_categories(tmp_path):
    allowed = tmp_path / "OneDrive-Contoso"
    allowed.mkdir()
    (allowed / "config.yaml").write_text("unique_onedrive_code_marker: true", encoding="utf-8")

    client = OneDriveClient(_config(tmp_path, allowed))  # default file_categories
    assert client.search("unique_onedrive_code_marker") == []


def test_code_file_found_once_code_category_opted_into_via_config(tmp_path):
    allowed = tmp_path / "OneDrive-Contoso"
    allowed.mkdir()
    (allowed / "config.yaml").write_text("unique_onedrive_code_marker: true", encoding="utf-8")

    client = OneDriveClient(_config(tmp_path, allowed, file_categories=["text", "code"]))
    results = client.search("unique_onedrive_code_marker")
    assert [doc.title for doc in results] == ["config.yaml"]
