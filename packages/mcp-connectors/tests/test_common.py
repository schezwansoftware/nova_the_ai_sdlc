"""Tests for `mcp_connectors/common.py`'s shared scaffolding: the
`Document` model, the config-time allowlist-enforcement helper (half of
the precision requirement every connector implements -- see
`mcp_connectors/__init__.py`), and OS-keyring-backed credential storage.

Credential tests monkeypatch the module-level `keyring` reference itself
(`common._keyring`) rather than touching a real OS keychain -- this
project's established injected-fake-client convention, applied here to
avoid ever writing to/reading from a real macOS Keychain/Windows
Credential Locker/Linux Secret Service during a test run.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from mcp_connectors.common import (
    ConnectorAuthError,
    ConnectorConfigError,
    CredentialRef,
    Document,
    connectors_config_dir,
    delete_secret,
    enforce_allowlist,
    get_secret,
    store_secret,
)


# -- Document ---------------------------------------------------------------


def test_document_requires_id_title_source_container():
    with pytest.raises(ValidationError):
        Document(title="t", source="jira")  # missing id/container


def test_document_defaults():
    doc = Document(id="1", title="t", source="jira", container="ENG")
    assert doc.snippet == ""
    assert doc.url is None
    assert doc.last_modified is None
    assert doc.metadata == {}


def test_document_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        Document(id="1", title="t", source="jira", container="ENG", made_up_field="x")


# -- enforce_allowlist --------------------------------------------------------


def test_enforce_allowlist_returns_full_allowed_list_when_nothing_requested():
    result = enforce_allowlist([], ["ENG", "PLAT"], kind="project")
    assert result == ["ENG", "PLAT"]


def test_enforce_allowlist_returns_requested_subset_when_all_allowed():
    result = enforce_allowlist(["PLAT"], ["ENG", "PLAT"], kind="project")
    assert result == ["PLAT"]


def test_enforce_allowlist_raises_on_any_disallowed_item():
    with pytest.raises(ConnectorConfigError) as excinfo:
        enforce_allowlist(["ENG", "SECRET"], ["ENG", "PLAT"], kind="project")
    assert "SECRET" in str(excinfo.value)
    assert "project" in str(excinfo.value)


def test_enforce_allowlist_never_widens_scope_even_partially():
    """A request naming one allowed and one disallowed item must be
    refused outright -- not silently narrowed to just the allowed one."""
    with pytest.raises(ConnectorConfigError):
        enforce_allowlist(["ENG", "SECRET"], ["ENG"], kind="project")


def test_enforce_allowlist_strips_whitespace():
    result = enforce_allowlist([" ENG "], ["ENG"], kind="project")
    assert result == ["ENG"]


# -- keyring-backed credential storage ----------------------------------------


class _FakeKeyringBackend:
    """A tiny in-memory stand-in for the real `keyring` module's
    `get_password`/`set_password`/`delete_password` free functions."""

    def __init__(self):
        self._store = {}

    def set_password(self, service, username, password):
        self._store[(service, username)] = password

    def get_password(self, service, username):
        return self._store.get((service, username))

    def delete_password(self, service, username):
        import keyring.errors as errors

        if (service, username) not in self._store:
            raise errors.PasswordDeleteError("not found")
        del self._store[(service, username)]


@pytest.fixture
def fake_keyring(monkeypatch):
    backend = _FakeKeyringBackend()
    import mcp_connectors.common as common_module

    monkeypatch.setattr(common_module, "_keyring", backend)
    return backend


def test_store_and_get_secret_round_trip(fake_keyring):
    store_secret("svc", "user", "s3cr3t")
    assert get_secret("svc", "user") == "s3cr3t"


def test_get_secret_raises_auth_error_when_nothing_stored(fake_keyring):
    with pytest.raises(ConnectorAuthError) as excinfo:
        get_secret("svc", "missing-user")
    assert "svc" in str(excinfo.value)
    assert "missing-user" in str(excinfo.value)
    assert "store_secret" in str(excinfo.value)  # includes a copy-pasteable fix


def test_delete_secret_is_idempotent(fake_keyring):
    store_secret("svc", "user", "s3cr3t")
    delete_secret("svc", "user")
    delete_secret("svc", "user")  # deleting again must not raise
    with pytest.raises(ConnectorAuthError):
        get_secret("svc", "user")


def test_credential_ref_resolve_delegates_to_get_secret(fake_keyring):
    store_secret("svc", "user", "s3cr3t")
    ref = CredentialRef(service="svc", username="user")
    assert ref.resolve() == "s3cr3t"


def test_credential_ref_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        CredentialRef(service="svc", username="user", secret="oops-not-allowed")


def test_keyring_not_installed_raises_clear_connector_auth_error(monkeypatch):
    import mcp_connectors.common as common_module

    monkeypatch.setattr(common_module, "_KEYRING_IMPORT_ERROR", ImportError("no module named keyring"))
    with pytest.raises(ConnectorAuthError) as excinfo:
        store_secret("svc", "user", "secret")
    assert "keyring" in str(excinfo.value)


# -- connectors_config_dir -----------------------------------------------------


def test_connectors_config_dir_default_path(monkeypatch):
    monkeypatch.delenv("MCP_CONNECTORS_CONFIG_DIR", raising=False)
    path = connectors_config_dir()
    assert path.name == "mcp-connectors"


def test_connectors_config_dir_honors_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_CONNECTORS_CONFIG_DIR", str(tmp_path))
    assert connectors_config_dir() == tmp_path
