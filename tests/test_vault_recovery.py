"""Tests for the vault's recovery escrow, unlock throttling, and migration.

The vault now wraps a random data key twice - once under the owner's PIN, once
under a one-time recovery code - so a forgotten PIN is survivable. That is a
security mechanism people will bet their private data on, so the properties
tested here are the ones whose failure would be silent and expensive:

* **Either secret opens the vault, and neither is stored.** Nothing on disk can
  be compared against a guess without doing the full key derivation.
* **Redemption is a recovery, not a login.** It re-keys, retires the old PIN and
  the old code, and issues a new one.
* **A failed or refused recovery changes nothing.** A wrong code or a weak new
  PIN must leave the vault exactly as it was.
* **Legacy vaults survive.** A vault written by the original single-secret
  format still opens with its password and is upgraded without data loss.
* **Throttling rejects before the KDF runs**, so it is not itself a CPU lever.

Run:
    pytest tests/test_vault_recovery.py
"""
from __future__ import annotations

import json
import os

import pytest
from cryptography.fernet import Fernet

from puffpai import zone_vault
from puffpai.security import vault as vault_mod
from puffpai.security.vault import (
    AuthenticationError,
    Vault,
    VaultError,
    VaultLocked,
    VaultThrottled,
    WeakSecretError,
)

# Captured before any fixture lowers them, so the real shipped work factor can
# still be asserted even though most tests run with a cheap KDF.
_REAL_PIN_ITERATIONS = vault_mod._PBKDF2_ITERATIONS
_REAL_RECOVERY_ITERATIONS = vault_mod._RECOVERY_PBKDF2_ITERATIONS

GOOD_PIN = "correct horse battery staple"
NEW_PIN = "a-different-strong-pin"


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch):
    """Run the real code path with a cheap work factor.

    Every wrap reads these module globals at call time and every unwrap reads the
    iteration count back out of the stored record, so lowering them exercises
    exactly the same logic at a speed a test suite can afford.
    ``test_shipped_work_factor_meets_owasp`` asserts the real values.
    """
    monkeypatch.setattr(vault_mod, "_PBKDF2_ITERATIONS", 1000)
    monkeypatch.setattr(vault_mod, "_RECOVERY_PBKDF2_ITERATIONS", 1000)


def _vault(tmp_path, name="v"):
    return Vault(vault_dir=str(tmp_path / name))


def _meta(vault) -> dict:
    with open(vault._meta_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _on_disk(vault) -> str:
    """Everything this vault has written, as one uppercase string."""
    blob = ""
    for entry in os.listdir(vault.vault_dir):
        with open(os.path.join(vault.vault_dir, entry), "rb") as fh:
            blob += entry + " " + fh.read().decode("latin-1")
    return blob.upper()


# ── the envelope ─────────────────────────────────────────────────────────────

def test_create_with_recovery_wraps_the_key_twice_with_independent_salts(tmp_path):
    vault = _vault(tmp_path)
    _, code = vault.create_with_recovery(GOOD_PIN)

    wraps = _meta(vault)["wraps"]
    assert set(wraps) == {"pin", "recovery"}
    assert wraps["pin"]["salt"] != wraps["recovery"]["salt"]
    assert wraps["pin"]["wrapped_key"] != wraps["recovery"]["wrapped_key"]
    assert code and vault.has_recovery_code


def test_neither_the_pin_nor_the_code_is_written_anywhere(tmp_path):
    vault = _vault(tmp_path)
    _, code = vault.create_with_recovery(GOOD_PIN)
    vault.store("HA_TOKEN", "eyJ.secret.value")
    vault.lock()

    blob = _on_disk(vault)
    assert GOOD_PIN.upper() not in blob
    assert code.upper() not in blob
    assert code.replace("-", "").upper() not in blob
    for group in code.split("-"):
        assert group not in blob
    # And no digest of either that could verify a guess without the full KDF.
    for marker in ("PIN_HASH", "VERIFIER", "PASSWORD", "RECOVERY_CODE"):
        assert marker not in blob


def test_either_secret_opens_the_same_vault(tmp_path):
    vault = _vault(tmp_path)
    _, code = vault.create_with_recovery(GOOD_PIN)
    vault.store("K", "V")
    vault.lock()

    by_pin = _vault(tmp_path)
    by_pin.unlock(GOOD_PIN)
    assert by_pin.retrieve("K") == "V"
    by_pin.lock()

    by_code = _vault(tmp_path)
    by_code.redeem_recovery_code(code, NEW_PIN)
    assert by_code.retrieve("K") == "V"


def test_authenticate_is_an_alias_for_unlock(tmp_path):
    """The name api_server calls. It must exist and behave identically."""
    vault = _vault(tmp_path)
    vault.create(GOOD_PIN)
    vault.store("K", "V")
    vault.lock()

    fresh = _vault(tmp_path)
    session = fresh.authenticate(GOOD_PIN)
    assert session.session_id
    assert fresh.retrieve("K") == "V"


def test_a_plain_create_issues_no_recovery_code(tmp_path):
    vault = _vault(tmp_path)
    vault.create(GOOD_PIN)
    assert not vault.has_recovery_code
    assert "recovery" not in _meta(vault)["wraps"]


# ── redemption is a recovery, not a login ────────────────────────────────────

def test_redemption_retires_the_old_pin_the_old_code_and_the_old_data_file(tmp_path):
    vault = _vault(tmp_path)
    _, code = vault.create_with_recovery(GOOD_PIN)
    vault.store("K", "V")
    vault.lock()

    old_data_file = _meta(vault)["data_file"]

    recovering = _vault(tmp_path)
    _, new_code = recovering.redeem_recovery_code(code, NEW_PIN)
    assert recovering.retrieve("K") == "V"
    recovering.lock()

    # Re-keyed: new contents file, old one gone.
    new_data_file = _meta(recovering)["data_file"]
    assert new_data_file != old_data_file
    assert not os.path.exists(os.path.join(recovering.vault_dir, old_data_file))

    # The old PIN and the old code both open nothing now.
    stale = _vault(tmp_path)
    with pytest.raises(AuthenticationError):
        stale.unlock(GOOD_PIN)
    with pytest.raises(AuthenticationError):
        _vault(tmp_path).redeem_recovery_code(code, "yet-another-pin")

    # The new PIN and the new code both work.
    assert new_code != code
    fresh = _vault(tmp_path)
    fresh.unlock(NEW_PIN)
    assert fresh.retrieve("K") == "V"
    fresh.lock()
    _vault(tmp_path).redeem_recovery_code(new_code, "third-strong-pin")


def test_redemption_accepts_a_hand_written_code(tmp_path):
    vault = _vault(tmp_path)
    _, code = vault.create_with_recovery(GOOD_PIN)
    vault.lock()

    scribbled = f"  {code.lower().replace('-', ' ')}  "
    recovering = _vault(tmp_path)
    recovering.redeem_recovery_code(scribbled, NEW_PIN)
    assert recovering.is_unlocked


def test_a_wrong_code_changes_nothing(tmp_path):
    vault = _vault(tmp_path)
    _, code = vault.create_with_recovery(GOOD_PIN)
    vault.store("K", "V")
    vault.lock()
    before = _meta(vault)

    attacker = _vault(tmp_path)
    with pytest.raises(AuthenticationError):
        attacker.redeem_recovery_code("QQQQQ-QQQQQ-QQQQQ-QQQQQ-QQQQQ", NEW_PIN)
    assert not attacker.is_unlocked
    assert _meta(vault) == before

    owner = _vault(tmp_path)
    owner.unlock(GOOD_PIN)
    assert owner.retrieve("K") == "V"


def test_a_weak_new_pin_is_refused_before_anything_is_re_keyed(tmp_path):
    vault = _vault(tmp_path)
    _, code = vault.create_with_recovery(GOOD_PIN)
    vault.store("K", "V")
    vault.lock()
    before = _meta(vault)

    recovering = _vault(tmp_path)
    with pytest.raises(WeakSecretError):
        recovering.redeem_recovery_code(code, "1234")

    # Nothing moved: same metadata, and both original secrets still work.
    assert _meta(vault) == before
    owner = _vault(tmp_path)
    owner.unlock(GOOD_PIN)
    assert owner.retrieve("K") == "V"
    owner.lock()
    _vault(tmp_path).redeem_recovery_code(code, NEW_PIN)


def test_redeeming_a_vault_that_never_had_a_code_is_refused(tmp_path):
    vault = _vault(tmp_path)
    vault.create(GOOD_PIN)
    vault.lock()
    with pytest.raises(AuthenticationError):
        _vault(tmp_path).redeem_recovery_code("QQQQQ-QQQQQ-QQQQQ-QQQQQ-QQQQQ", NEW_PIN)


# ── adding a code to a vault that already exists ─────────────────────────────

def test_a_recovery_code_can_only_be_added_while_unlocked(tmp_path):
    vault = _vault(tmp_path)
    vault.create(GOOD_PIN)
    vault.lock()

    locked = _vault(tmp_path)
    with pytest.raises(VaultLocked):
        locked.add_recovery_code()

    locked.unlock(GOOD_PIN)
    code = locked.add_recovery_code()
    assert locked.has_recovery_code
    locked.lock()

    recovering = _vault(tmp_path)
    recovering.redeem_recovery_code(code, NEW_PIN)
    assert recovering.is_unlocked


def test_adding_a_code_replaces_the_previous_one(tmp_path):
    vault = _vault(tmp_path)
    _, first = vault.create_with_recovery(GOOD_PIN)
    second = vault.add_recovery_code()
    vault.lock()

    assert second != first
    with pytest.raises(AuthenticationError):
        _vault(tmp_path).redeem_recovery_code(first, NEW_PIN)
    _vault(tmp_path).redeem_recovery_code(second, NEW_PIN)


# ── legacy migration ─────────────────────────────────────────────────────────

def _write_legacy_vault(path: str, password: str, secrets: dict[str, str]) -> None:
    """Write a vault in the original single-secret format."""
    os.makedirs(path, exist_ok=True)
    salt = os.urandom(16)
    key = vault_mod._derive_key(password, salt, vault_mod._PBKDF2_ITERATIONS)
    payload = json.dumps({"secrets": secrets, "documents": {}}).encode("utf-8")
    with open(os.path.join(path, "vault.salt"), "wb") as fh:
        fh.write(salt)
    with open(os.path.join(path, "vault.enc"), "wb") as fh:
        fh.write(Fernet(key).encrypt(payload))


def test_a_legacy_vault_still_opens_and_keeps_its_contents(tmp_path):
    vdir = str(tmp_path / "legacy")
    _write_legacy_vault(vdir, GOOD_PIN, {"HA_TOKEN": "eyJ.legacy.value"})

    vault = Vault(vault_dir=vdir)
    assert vault.is_legacy and vault.is_initialized
    vault.unlock(GOOD_PIN)
    assert vault.retrieve("HA_TOKEN") == "eyJ.legacy.value"


def test_a_legacy_vault_is_upgraded_and_the_old_derivation_path_is_removed(tmp_path):
    vdir = str(tmp_path / "legacy")
    _write_legacy_vault(vdir, GOOD_PIN, {"K": "V"})

    vault = Vault(vault_dir=vdir)
    vault.unlock(GOOD_PIN)

    assert not vault.is_legacy
    assert os.path.isfile(vault._meta_path)
    # The old salt and ciphertext must not survive, or the old password plus the
    # old salt would still reach the contents after a recovery rotates the PIN.
    assert not os.path.exists(os.path.join(vdir, "vault.salt"))
    assert not os.path.exists(os.path.join(vdir, "vault.enc"))

    # Still works, still writable, and now able to carry a recovery code.
    vault.store("K2", "V2")
    code = vault.add_recovery_code()
    vault.lock()

    reopened = Vault(vault_dir=vdir)
    reopened.unlock(GOOD_PIN)
    assert reopened.retrieve("K") == "V" and reopened.retrieve("K2") == "V2"
    reopened.lock()

    recovering = Vault(vault_dir=vdir)
    recovering.redeem_recovery_code(code, NEW_PIN)
    assert recovering.retrieve("K") == "V"


def test_a_legacy_vault_cannot_be_recovered_before_it_is_opened_once(tmp_path):
    """The honest limit: the data key only exists once the owner opens it."""
    vdir = str(tmp_path / "legacy")
    _write_legacy_vault(vdir, GOOD_PIN, {"K": "V"})
    with pytest.raises(VaultError) as exc:
        Vault(vault_dir=vdir).redeem_recovery_code("QQQQQ-QQQQQ", NEW_PIN)
    assert "upgrade" in str(exc.value).lower()


def test_a_wrong_password_on_a_legacy_vault_still_fails_cleanly(tmp_path):
    vdir = str(tmp_path / "legacy")
    _write_legacy_vault(vdir, GOOD_PIN, {"K": "V"})
    vault = Vault(vault_dir=vdir)
    with pytest.raises(AuthenticationError):
        vault.unlock("not the password")
    assert not vault.is_unlocked
    assert vault.is_legacy, "a failed unlock must not upgrade or damage anything"


# ── the strength floor ───────────────────────────────────────────────────────

@pytest.mark.parametrize("weak", ["1234", "1", "aaaaaaaa", "12345678", "password"])
def test_the_vault_refuses_a_weak_secret_no_matter_which_path_creates_it(tmp_path, weak):
    vault = _vault(tmp_path)
    with pytest.raises(WeakSecretError):
        vault.create(weak)
    assert not vault.is_initialized, "a refused secret must not leave files behind"


@pytest.mark.parametrize("good", [
    "correct horse battery staple",   # a passphrase, spaces and all
    "hunter2hunter2",
    "a-strong-passphrase",
])
def test_the_floor_still_allows_real_passphrases(tmp_path, good):
    """Banning spaces would push owners toward shorter, weaker secrets."""
    ok, reason = vault_mod.validate_vault_secret(good)
    assert ok, reason


def test_the_wizard_policy_is_stricter_than_the_vault_floor():
    """Layering: the wizard's PIN rules are tighter than the vault's backstop."""
    # Rejected by the wizard (spaces are a transcription hazard for a typed-back
    # PIN) but accepted by the vault, which must not forbid passphrases.
    assert zone_vault.validate_pin("correct horse battery staple")[0] is False
    assert vault_mod.validate_vault_secret("correct horse battery staple")[0] is True
    # Rejected by both.
    assert zone_vault.validate_pin("1234")[0] is False
    assert vault_mod.validate_vault_secret("1234")[0] is False


def test_shipped_work_factor_meets_owasp():
    """The real constants, not the cheap ones the fixture substitutes."""
    assert _REAL_PIN_ITERATIONS >= 600_000
    assert _REAL_RECOVERY_ITERATIONS >= _REAL_PIN_ITERATIONS, \
        "the recovery wrap must never be the cheaper one to attack"


# ── throttling ───────────────────────────────────────────────────────────────

def test_throttle_rejects_before_the_key_derivation_runs(tmp_path, monkeypatch):
    """The property that stops the throttle being a CPU amplification lever."""
    vault = _vault(tmp_path)
    vault.create(GOOD_PIN)
    vault.lock()

    calls: list[int] = []
    real_derive = vault_mod._derive_key

    def counting_derive(password, salt, iterations=1000):
        calls.append(1)
        return real_derive(password, salt, iterations)

    monkeypatch.setattr(vault_mod, "_derive_key", counting_derive)

    victim = _vault(tmp_path)
    for _ in range(vault_mod._FREE_ATTEMPTS + 1):
        with pytest.raises(AuthenticationError):
            victim.unlock("wrong password entirely")

    derivations_before = len(calls)
    assert derivations_before == vault_mod._FREE_ATTEMPTS + 1

    with pytest.raises(VaultThrottled) as exc:
        victim.unlock("wrong password entirely")
    assert exc.value.retry_after > 0
    assert len(calls) == derivations_before, \
        "a throttled attempt must not reach PBKDF2"


def test_throttle_is_distinguishable_from_a_wrong_password(tmp_path):
    """Reporting a throttle as a bad PIN is how owners think they lost the vault."""
    assert issubclass(VaultThrottled, VaultError)
    assert not issubclass(VaultThrottled, AuthenticationError)


def test_a_correct_password_clears_the_cooldown(tmp_path):
    vault = _vault(tmp_path)
    vault.create(GOOD_PIN)
    vault.lock()

    victim = _vault(tmp_path)
    for _ in range(3):
        with pytest.raises(AuthenticationError):
            victim.unlock("nope nope nope")
    assert victim._attempts["pin"][0] == 3

    victim.unlock(GOOD_PIN)
    assert "pin" not in victim._attempts


def test_hammering_the_pin_does_not_block_the_recovery_path(tmp_path):
    """The owner's way back in stays open while the front door is under attack."""
    vault = _vault(tmp_path)
    _, code = vault.create_with_recovery(GOOD_PIN)
    vault.store("K", "V")
    vault.lock()

    under_attack = _vault(tmp_path)
    for _ in range(vault_mod._FREE_ATTEMPTS + 2):
        try:
            under_attack.unlock("wrong")
        except (AuthenticationError, VaultThrottled):
            pass
    with pytest.raises(VaultThrottled):
        under_attack.unlock(GOOD_PIN)

    # Same handle, same process: recovery is counted separately and still works.
    under_attack.redeem_recovery_code(code, NEW_PIN)
    assert under_attack.retrieve("K") == "V"


def test_the_cooldown_is_capped_so_an_owner_is_never_locked_out_forever(tmp_path):
    vault = _vault(tmp_path)
    vault.create(GOOD_PIN)
    vault.lock()

    victim = _vault(tmp_path)
    for _ in range(40):
        try:
            victim.unlock("wrong")
        except (AuthenticationError, VaultThrottled):
            pass
    try:
        victim.unlock("wrong")
    except VaultThrottled as exc:
        assert exc.retry_after <= vault_mod._BACKOFF_MAX_SECONDS


def test_throttle_state_is_never_written_to_disk(tmp_path):
    """In-memory only, so a remote attacker cannot make a lockout durable."""
    vault = _vault(tmp_path)
    vault.create(GOOD_PIN)
    vault.lock()

    victim = _vault(tmp_path)
    for _ in range(vault_mod._FREE_ATTEMPTS + 2):
        try:
            victim.unlock("wrong")
        except (AuthenticationError, VaultThrottled):
            pass
    with pytest.raises(VaultThrottled):
        victim.unlock(GOOD_PIN)

    blob = _on_disk(victim).lower()
    assert "blocked_until" not in blob and "attempts" not in blob
    # A fresh handle (as after a service restart) is not carrying the cooldown.
    assert _vault(tmp_path).unlock(GOOD_PIN).session_id


# ── the two normalizers must never drift ─────────────────────────────────────

@pytest.mark.parametrize("variant", [
    "ABCDE-FGHJK", "abcde-fghjk", "abcde fghjk", "ABCDEFGHJK",
    "  abcde-fghjk  ", "abcde\nfghjk", "abcde . fghjk", "", "-----",
])
def test_wizard_and_vault_normalize_recovery_codes_identically(variant):
    """A drift between these two would lock owners out of their own vaults."""
    assert (vault_mod.normalize_recovery_code(variant)
            == zone_vault._normalize_recovery_code(variant))
