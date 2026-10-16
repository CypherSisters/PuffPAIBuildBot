"""The unattended tier: HomeBotBuild's format, restricted by the key policy.

The machine secret comes from a file (the HBB override path) or, BuildBot's
addition, the operating system's password store through ``keyring``. Tests
use an in-memory store; nothing here touches the real Credential Manager.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import types

import pytest

from keys_support import (PIN, canary, fast_vault, install_vendor,
                          machine_key_file, reset_keys, set_posture)
from puffpai.security import egress_doors, keys
from puffpai.security import vault as vault_mod

#: These tests are about keyring behaviour: each gets an in-memory stand-in
#: (tests/conftest.py) and may put its own in place. Never the real store.
pytestmark = pytest.mark.keyring_stub


class MemoryStore:
    """A stand-in for a real OS password store (priority > 0)."""
    priority = 5

    def __init__(self):
        self.items: dict = {}


class FailStore:
    priority = 0


FailStore.__module__ = "keyring.backends.fail"


def _fake_keyring(monkeypatch, backend):
    mod = types.ModuleType("keyring")
    mod.get_keyring = lambda: backend
    store = getattr(backend, "items", {})
    mod.get_password = lambda s, u: store.get((s, u))
    mod.set_password = lambda s, u, v: store.__setitem__((s, u), v)
    mod.delete_password = lambda s, u: store.pop((s, u), None)
    monkeypatch.setitem(sys.modules, "keyring", mod)
    return store


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for d in egress_doors.DOORS:
        monkeypatch.delenv(d.env_var, raising=False)
    monkeypatch.delenv("PUFFPAI_VAULT_SERVICE_KEY_FILE", raising=False)
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    reset_keys()
    set_posture("enumerated")
    yield
    reset_keys()


def test_the_os_password_store_holds_the_machine_key(monkeypatch, tmp_path):
    store = _fake_keyring(monkeypatch, MemoryStore())
    v = fast_vault(monkeypatch, tmp_path / "vault")
    vendor = install_vendor(monkeypatch)
    value = canary()
    vendor.accept("API-Key", value)
    out = keys.add_key("marginalia_search", value, vault=v, background=True)
    assert out["key"]["background"] is True
    assert len(store) == 1
    (service, user), secret = next(iter(store.items()))
    assert service == "puffpai-buildbot" and user.startswith("vault-service-key:")
    assert value not in secret
    v.lock()
    assert keys.keyed_get("key:marginalia_search",
                          url="https://api2.marginalia-search.com/search?query=a",
                          caller="t", vault=v).status == 200
    # Another vault folder gets its own machine key.
    other = fast_vault(monkeypatch, tmp_path / "other")
    assert vault_mod.create_machine_key(other.vault_dir)[0] is True
    assert len(store) == 2


def test_a_store_that_is_not_real_is_refused(monkeypatch, tmp_path):
    _fake_keyring(monkeypatch, FailStore())
    v = fast_vault(monkeypatch, tmp_path / "vault")
    assert keys.background_available(v) is False
    vendor = install_vendor(monkeypatch)
    value = canary()
    vendor.accept("API-Key", value)
    out = keys.add_key("marginalia_search", value, vault=v, background=True)
    assert out["key"]["background"] is False
    assert any("cannot keep keys working" in n for n in out["notes"])
    assert out["key"]["background_possible"] is False


def test_the_data_key_is_never_wrapped_under_the_machine_key(monkeypatch,
                                                             tmp_path):
    machine_key_file(monkeypatch, tmp_path)
    v = fast_vault(monkeypatch, tmp_path / "vault")
    vendor = install_vendor(monkeypatch)
    value = canary()
    vendor.accept("API-Key", value)
    keys.add_key("marginalia_search", value, vault=v, background=True)
    meta = json.loads((tmp_path / "vault" / "vault.meta.json").read_text())
    assert set(meta["wraps"]) <= {"pin", "recovery"}
    assert meta["service"]["kdf"] == "scrypt"
    assert meta["service_by_dek"]["kdf"] == "hkdf-sha256"
    sdk = v._sdk_from_machine_key()  # noqa: SLF001
    assert len(sdk) == 32
    assert sdk != base64.urlsafe_b64decode(v._data_key)  # noqa: SLF001
    # Only the allowed record is in the unattended tier, as ciphertext.
    files = sorted(os.listdir(tmp_path / "vault"))
    assert "key_marginalia_search.json" in files
    raw = (tmp_path / "vault" / "key_marginalia_search.json").read_bytes()
    assert value.encode() not in raw and b"marginalia" not in raw


def test_the_unattended_path_opens_nothing_but_its_records(monkeypatch,
                                                           tmp_path):
    machine_key_file(monkeypatch, tmp_path)
    v = fast_vault(monkeypatch, tmp_path / "vault")
    v.store("BANK", "a secret the machine key must never open")
    vendor = install_vendor(monkeypatch)
    value = canary()
    vendor.accept("API-Key", value)
    keys.add_key("marginalia_search", value, vault=v, background=True)
    v.lock()
    for name in ("vault.meta.json", "BANK", "../vault.meta.json",
                 "key_brave_search.json"):
        assert v.read_service_secret_unattended(name) is None
    with pytest.raises(vault_mod.VaultError):
        v.retrieve("BANK")


def test_removing_the_machine_wrap_is_one_step_and_pauses_the_key(monkeypatch,
                                                                  tmp_path):
    machine_key_file(monkeypatch, tmp_path)
    v = fast_vault(monkeypatch, tmp_path / "vault")
    vendor = install_vendor(monkeypatch)
    value = canary()
    vendor.accept("API-Key", value)
    keys.add_key("marginalia_search", value, vault=v, background=True)
    v.lock()
    assert v.remove_service_wrap() is True
    with pytest.raises(keys.KeyUnavailable) as exc:
        keys.keyed_get("key:marginalia_search",
                       url="https://api2.marginalia-search.com/search?query=a",
                       caller="t", vault=v)
    assert exc.value.reason == "locked"


def test_a_rekey_keeps_the_locked_path_working(monkeypatch, tmp_path):
    machine_key_file(monkeypatch, tmp_path)
    v = fast_vault(monkeypatch, tmp_path / "vault")
    vendor = install_vendor(monkeypatch)
    value = canary()
    vendor.accept("API-Key", value)
    code = v.add_recovery_code()
    keys.add_key("marginalia_search", value, vault=v, background=True)
    v.lock()
    v.redeem_recovery_code(code, "a brand new long passphrase")
    v.lock()
    assert keys.keyed_get("key:marginalia_search",
                          url="https://api2.marginalia-search.com/search?query=a",
                          caller="t", vault=v).status == 200
    v.unlock("a brand new long passphrase")
    assert keys._record_for(v, "marginalia_search")["value"] == value  # noqa: SLF001
    # The open vault still reaches the unattended copy through the new wrap.
    assert v._sdk_from_session(create=False) == v._sdk_from_machine_key()  # noqa: SLF001


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits only")
def test_a_machine_key_file_readable_by_others_is_refused(monkeypatch, tmp_path):
    path = machine_key_file(monkeypatch, tmp_path)
    os.chmod(path, 0o644)
    secret, reason = vault_mod.read_service_key(str(tmp_path / "v"))
    assert secret is None and "readable beyond its owner" in reason


def test_only_the_broker_touches_key_records_and_files(monkeypatch, tmp_path):
    machine_key_file(monkeypatch, tmp_path)
    v = fast_vault(monkeypatch, tmp_path / "vault")
    with pytest.raises(vault_mod.VaultError):
        v._reserved_set("__puffpai.key.marginalia_search", "{}")  # noqa: SLF001
    with pytest.raises(vault_mod.VaultError):
        v.write_service_secret("key_marginalia_search.json", {"value": "x"})
    with pytest.raises(vault_mod.VaultError):
        v.delete_service_secret("key_marginalia_search.json")
    assert PIN  # the fixture vault really is open
    assert v.is_unlocked
