"""Tests for the Phase-4 security layer: vault, PII router, and consent ledger.

These exercise the public reference implementations end to end:

* the vault round-trips secrets and documents, rejects wrong passwords cleanly,
  refuses access while locked, and salts each vault independently;
* the PII router classifies email/phone/none correctly and routes accordingly;
* the consent ledger defaults to pending, persists across instances, and honours
  human approve/deny decisions.

Run:
    pytest tests/test_vault.py
"""
from __future__ import annotations

import os

import pytest

from puffpai.security.vault import (
    Vault,
    VaultError,
    VaultLocked,
    AuthenticationError,
)
from puffpai.security.data_classifier import DataClassifier, DataZone
from puffpai.security.consent_gate import (
    ConsentLedger,
    PENDING,
    APPROVED,
    DENIED,
)
from puffpai.security.secrets_provider import SecretsProvider, AgentScope


# ── vault ────────────────────────────────────────────────────────────────────

def _new_vault(tmp_path, name="v"):
    return Vault(vault_dir=str(tmp_path / name))


def test_vault_create_and_roundtrip(tmp_path) -> None:
    vault = _new_vault(tmp_path)
    vault.create("correct horse battery staple")
    assert vault.is_initialized
    assert vault.is_unlocked

    vault.store("HA_TOKEN", "eyJhbGciOi.secret.value")
    assert vault.retrieve("HA_TOKEN") == "eyJhbGciOi.secret.value"
    assert vault.list_secrets() == ["HA_TOKEN"]


def test_vault_persists_across_unlock(tmp_path) -> None:
    """A secret survives lock + a fresh Vault handle re-unlocking the same dir."""
    vdir = tmp_path / "persist"
    v1 = Vault(vault_dir=str(vdir))
    v1.create("hunter2hunter2")
    v1.store("N8N_API_KEY", "abc123")
    v1.lock()

    v2 = Vault(vault_dir=str(vdir))
    assert v2.is_initialized
    v2.unlock("hunter2hunter2")
    assert v2.retrieve("N8N_API_KEY") == "abc123"


def test_vault_wrong_password_fails_cleanly(tmp_path) -> None:
    vdir = tmp_path / "wrong"
    v1 = Vault(vault_dir=str(vdir))
    v1.create("the right password")
    v1.store("K", "V")
    v1.lock()

    v2 = Vault(vault_dir=str(vdir))
    with pytest.raises(AuthenticationError):
        v2.unlock("the wrong password")
    # After a failed unlock the vault stays locked — no partial access.
    assert not v2.is_unlocked


def test_locked_vault_refuses_access(tmp_path) -> None:
    vdir = tmp_path / "locked"
    v1 = Vault(vault_dir=str(vdir))
    v1.create("pw pw pw pw")
    v1.store("K", "V")
    v1.lock()

    v2 = Vault(vault_dir=str(vdir))  # initialized but not unlocked
    assert v2.is_initialized and not v2.is_unlocked
    with pytest.raises(VaultLocked):
        v2.retrieve("K")
    with pytest.raises(VaultLocked):
        v2.store("K2", "V2")
    with pytest.raises(VaultLocked):
        v2.list_secrets()
    # get_service_secret never raises; it returns None while locked.
    assert v2.get_service_secret("K") is None


def _meta(vault) -> dict:
    """Read a vault's envelope metadata off disk."""
    import json
    with open(vault._meta_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _contents(vault) -> bytes:
    """Read a vault's encrypted contents off disk."""
    meta = _meta(vault)
    with open(os.path.join(vault.vault_dir, meta["data_file"]), "rb") as f:
        return f.read()


def test_vault_salt_differs_across_vaults(tmp_path) -> None:
    """Two vaults with the same password share nothing on disk."""
    v1 = _new_vault(tmp_path, "a")
    v2 = _new_vault(tmp_path, "b")
    v1.create("same-password-for-both")
    v2.create("same-password-for-both")

    salt1 = _meta(v1)["wraps"]["pin"]["salt"]
    salt2 = _meta(v2)["wraps"]["pin"]["salt"]
    assert salt1 != salt2
    # Same password + different salt => different wrapped key and ciphertext.
    assert _meta(v1)["wraps"]["pin"]["wrapped_key"] != _meta(v2)["wraps"]["pin"]["wrapped_key"]
    assert _contents(v1) != _contents(v2)


def test_vault_refuses_to_overwrite(tmp_path) -> None:
    vault = _new_vault(tmp_path, "once")
    vault.create("first password")
    with pytest.raises(VaultError):
        vault.create("second password")


def test_vault_delete_and_documents(tmp_path) -> None:
    vault = _new_vault(tmp_path, "docs")
    vault.create("doc password here")
    vault.store("temp", "x")
    assert vault.delete("temp") is True
    assert vault.delete("temp") is False
    assert vault.retrieve("temp") is None

    vault.store_document("note.bin", b"\x00\x01binary\xff")
    assert vault.retrieve_document("note.bin") == b"\x00\x01binary\xff"
    assert vault.list_documents() == ["note.bin"]


def test_lock_clears_key(tmp_path) -> None:
    vault = _new_vault(tmp_path, "clear")
    vault.create("lock me out")
    vault.store("S", "secret")
    vault.lock()
    assert not vault.is_unlocked
    assert vault.get_service_secret("S") is None


# ── secrets-provider vault overlay ───────────────────────────────────────────

def test_secrets_provider_resolves_via_vault(tmp_path) -> None:
    """An unlocked vault resolves first in the vault → keyring → env chain.

    Uses a unique, made-up secret name so the result cannot come from a real
    keyring entry or environment variable on the host machine.
    """
    name = "PUFFPAI_UNITTEST_VAULT_ONLY_KEY"
    vault = _new_vault(tmp_path, "chain")
    vault.create("chain password!!")
    vault.store(name, "vault-sourced-token")

    # SYSTEM scope is unrestricted; env fallback off so only vault/keyring apply.
    provider = SecretsProvider(vault=vault, allow_env_fallback=False)
    assert provider.get(name, scope=AgentScope.SYSTEM) == "vault-sourced-token"

    # Locked vault → falls through; the made-up name is in no keyring/env, so None.
    vault.lock()
    assert provider.get(name, scope=AgentScope.SYSTEM) is None


# ── PII router ───────────────────────────────────────────────────────────────

def test_pii_router_detects_email() -> None:
    clf = DataClassifier()
    det = clf.scan_for_pii("reach me at jane.doe@example.com any time")
    assert det.has_pii
    assert "email" in det.pii_types
    zone, _ = clf.classify_content("jane.doe@example.com")
    assert zone is DataZone.VAULT


def test_pii_router_detects_phone() -> None:
    clf = DataClassifier()
    det = clf.scan_for_pii("call the front desk at (415) 555-0132 tomorrow")
    assert det.has_pii
    assert "phone" in det.pii_types
    assert clf.route("415-555-0132") == DataZone.VAULT.value


def test_pii_router_passes_clean_text_to_knowledge() -> None:
    clf = DataClassifier()
    det = clf.scan_for_pii("The mitochondria is the powerhouse of the cell.")
    assert not det.has_pii
    assert det.severity == "none"
    assert det.pii_types == []
    zone, _ = clf.classify_content("The mitochondria is the powerhouse of the cell.")
    assert zone is DataZone.KNOWLEDGE


def test_pii_router_empty_text() -> None:
    clf = DataClassifier()
    det = clf.scan_for_pii("")
    assert not det.has_pii
    assert det.summary() == "no PII detected"


# ── consent ledger ───────────────────────────────────────────────────────────

def _ledger(tmp_path):
    return ConsentLedger(
        state_path=str(tmp_path / "consent_state.json"),
        audit_path=str(tmp_path / "consent_ledger.jsonl"),
    )


def test_consent_defaults_to_pending(tmp_path) -> None:
    led = _ledger(tmp_path)
    assert led.status("light.new_lamp") == PENDING
    led.record("light.new_lamp")
    assert "light.new_lamp" in led.pending()

    verdict = led.gate(["light.new_lamp"])
    assert verdict["action"] == "flag"


def test_consent_approve_deny(tmp_path) -> None:
    led = _ledger(tmp_path)
    led.record("lock.front_door")
    led.approve("lock.front_door")
    assert led.status("lock.front_door") == APPROVED
    assert led.gate(["lock.front_door"])["action"] == "allow"

    led.record("switch.furnace")
    led.deny("switch.furnace")
    assert led.status("switch.furnace") == DENIED
    assert led.gate(["switch.furnace"])["action"] == "deny"


def test_consent_persists_across_instances(tmp_path) -> None:
    led1 = _ledger(tmp_path)
    led1.record("camera.porch")
    led1.approve("camera.porch")

    led2 = _ledger(tmp_path)  # reloads state from disk
    assert led2.status("camera.porch") == APPROVED
    assert led2.gate(["camera.porch"])["action"] == "allow"


def test_consent_mixed_targets_flag_and_deny(tmp_path) -> None:
    led = _ledger(tmp_path)
    led.record("light.a")
    led.approve("light.a")
    led.record("light.b")  # stays pending
    # approved + pending => flag (pending wins over allow, conservative)
    assert led.gate(["light.a", "light.b"])["action"] == "flag"
    # a denial anywhere => deny (deny wins over everything)
    led.deny("light.b")
    assert led.gate(["light.a", "light.b"])["action"] == "deny"
