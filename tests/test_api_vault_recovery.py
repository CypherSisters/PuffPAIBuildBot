"""Tests for the vault's HTTP surface: honest unlock errors and recovery.

The vault itself is tested in ``tests/test_vault_recovery.py``. What is tested
here is the *boundary* - because the failure that actually happened in the field
was not a broken vault, it was an API layer that turned every internal fault
into "I couldn't unlock the vault with that PIN". An owner told that with a
correct PIN concludes they have lost their vault.

So the properties asserted here are:

* **Three outcomes, three answers.** A cooldown, a wrong PIN, and an internal
  fault must be distinguishable to the person on the other end, in chat and over
  HTTP. An internal fault is NEVER reported as a bad PIN.
* **No exception text reaches the owner.** The original bug leaked
  ``type object 'Vault' has no attribute 'authenticate'`` into a user-facing
  string.
* **Recovery is reachable.** ``redeem_recovery_code`` used to be callable only
  from Python, which meant the code an owner was told to write down could not
  actually be used.
* **The submitted code never appears anywhere** - not in a response, not in a
  log record.
* **The recovery cooldown is independent of the PIN cooldown**, so someone
  hammering the front door cannot block the way back in.

Run:
    pytest tests/test_api_vault_recovery.py
"""
from __future__ import annotations

import logging
import os
import tempfile

# Isolate on-disk state BEFORE importing the app (module-level singletons
# resolve their paths at import time). Mirrors tests/test_api.py so this module
# is safe to run first, last, or alone.
os.environ.setdefault(
    "PUFFPAI_DATA_DIR", tempfile.mkdtemp(prefix="puffpai_api_recovery_test_")
)

import pytest  # noqa: E402

from puffpai import api_server  # noqa: E402
from puffpai.security import vault as vault_mod  # noqa: E402
from puffpai.security.vault import (  # noqa: E402
    AuthenticationError,
    Vault,
    VaultThrottled,
)

API_KEY = api_server._API_KEY

# A PIN the vault page may make: /vault/unlock with initialize uses the same
# PIN rules as /setup/vault, which leave spaces out.
GOOD_PIN = "Harbor-7-Tulip-q"
NEW_PIN = "a-different-strong-pin"
WRONG_CODE = "ZZZZ-ZZZZ-ZZZZ-ZZZZ-QQQQ"


def _auth(extra=None):
    headers = {"Authorization": f"Bearer {API_KEY}"}
    if extra:
        headers.update(extra)
    return headers


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch):
    """Run the real code path at a work factor a test suite can afford.

    Every wrap reads these module globals at call time and every unwrap reads the
    iteration count back out of the stored record, so lowering them exercises
    exactly the same logic. tests/test_vault_recovery.py asserts the real values.
    """
    monkeypatch.setattr(vault_mod, "_PBKDF2_ITERATIONS", 1000)
    monkeypatch.setattr(vault_mod, "_RECOVERY_PBKDF2_ITERATIONS", 1000)


@pytest.fixture
def client():
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client()


@pytest.fixture
def vault(monkeypatch, tmp_path):
    """A fresh, isolated vault injected as the API's singleton."""
    v = Vault(vault_dir=str(tmp_path / "vault"))
    monkeypatch.setattr(api_server, "_vault_instance", v)
    api_server._vault_sessions.clear()
    yield v
    api_server._vault_sessions.clear()
    api_server._VAULT_CHAT_STATE.clear()


# ── The chat unlock path: three outcomes, three answers ─────────────────────

def test_chat_unlock_reports_a_wrong_pin_as_a_wrong_pin(vault):
    vault.create(GOOD_PIN)
    vault.lock()

    answer = api_server._try_unlock_and_answer("not-the-pin-at-all", "q", "c1")

    assert "PIN" in answer
    assert "didn't open the vault" in answer
    assert "NOT your PIN" not in answer


def test_chat_unlock_reports_a_cooldown_as_a_cooldown_not_a_bad_pin(vault, monkeypatch):
    vault.create(GOOD_PIN)
    vault.lock()
    monkeypatch.setattr(
        vault, "unlock",
        lambda pw: (_ for _ in ()).throw(VaultThrottled(42.0)),
    )

    answer = api_server._try_unlock_and_answer(GOOD_PIN, "q", "c1")

    assert "42" in answer
    # The whole point: the owner is told their PIN may well be right.
    assert "may well be the right one" in answer
    assert "didn't open the vault" not in answer


def test_chat_unlock_never_reports_an_internal_fault_as_a_bad_pin(vault, monkeypatch, caplog):
    """The exact field failure. An AttributeError inside the vault used to be
    reported to the owner as a wrong PIN, with the repr appended."""
    vault.create(GOOD_PIN)
    vault.lock()

    def boom(_pw):
        raise AttributeError("type object 'Vault' has no attribute 'authenticate'")

    monkeypatch.setattr(vault, "unlock", boom)

    with caplog.at_level(logging.DEBUG, logger="puffpai"):
        answer = api_server._try_unlock_and_answer(GOOD_PIN, "q", "c1")

    assert "NOT your PIN" in answer
    assert "didn't open the vault" not in answer
    assert "couldn't unlock the vault with that PIN" not in answer
    # The exception text must not be interpolated into the owner-facing string.
    assert "AttributeError" not in answer
    assert "has no attribute" not in answer
    # ...but it must be logged, with a traceback, so it can be diagnosed.
    assert any(r.exc_info for r in caplog.records)


def test_chat_unlock_does_not_leak_the_pin_into_the_answer(vault, monkeypatch):
    vault.create(GOOD_PIN)
    vault.lock()
    monkeypatch.setattr(
        vault, "unlock",
        lambda pw: (_ for _ in ()).throw(RuntimeError("disk on fire")),
    )

    answer = api_server._try_unlock_and_answer("hunter2-hunter2", "q", "c1")

    assert "hunter2" not in answer
    assert "disk on fire" not in answer


def test_chat_unlock_does_not_treat_a_missing_migration_hook_as_an_error(vault, caplog):
    """migrate_plaintext_files is appliance-only. Its absence is the expected
    case on the reference vault and must not log an error."""
    vault.create_with_recovery(GOOD_PIN)
    vault.store("note", "x")
    vault.lock()
    assert not hasattr(vault, "migrate_plaintext_files")

    with caplog.at_level(logging.DEBUG, logger="puffpai"):
        api_server._try_unlock_and_answer(GOOD_PIN, "q", "c-mig")

    # The unlock really did succeed, so the migration point really was reached.
    assert vault.is_unlocked
    assert api_server._VAULT_CHAT_STATE["c-mig"]["vault_session"]
    assert not any("migrat" in r.getMessage().lower() for r in caplog.records)


# ── /vault/unlock: one status code per outcome ──────────────────────────────

def test_unlock_wrong_password_is_401(client, vault):
    vault.create(GOOD_PIN)
    vault.lock()

    resp = client.post("/vault/unlock", json={"password": "wrong-password"},
                       headers=_auth())

    assert resp.status_code == 401
    assert resp.get_json()["success"] is False


def test_unlock_cooldown_is_429_with_retry_after(client, vault, monkeypatch):
    vault.create(GOOD_PIN)
    vault.lock()
    monkeypatch.setattr(
        vault, "unlock",
        lambda pw: (_ for _ in ()).throw(VaultThrottled(30.0)),
    )

    resp = client.post("/vault/unlock", json={"password": GOOD_PIN},
                       headers=_auth())

    assert resp.status_code == 429
    body = resp.get_json()
    assert body["retry_after"] == 30
    assert body["throttled"] is True


def test_unlock_weak_new_password_is_400_not_401(client, vault):
    """Initializing with a password below the vault's floor is a policy refusal,
    not a failed authentication."""
    resp = client.post("/vault/unlock",
                       json={"password": "abc", "initialize": True},
                       headers=_auth())

    assert resp.status_code == 400
    assert resp.get_json()["weak_password"] is True
    assert not vault.is_initialized
    # The vault's policy text is deliberately passed through here - it is what
    # tells the owner how to pick a better password. It must still never carry
    # the submitted secret or a filesystem path.
    blob = resp.get_data(as_text=True)
    assert "abc" not in blob
    assert vault.vault_dir not in blob


def test_unlock_internal_fault_is_500_and_says_it_is_not_the_password(client, vault, monkeypatch, caplog):
    vault.create(GOOD_PIN)
    vault.lock()

    def boom(_pw):
        raise AttributeError("type object 'Vault' has no attribute 'authenticate'")

    monkeypatch.setattr(vault, "unlock", boom)

    with caplog.at_level(logging.DEBUG, logger="puffpai"):
        resp = client.post("/vault/unlock", json={"password": GOOD_PIN},
                           headers=_auth())

    assert resp.status_code == 500
    body = resp.get_json()
    assert body["internal_error"] is True
    assert "not because of the password" in body["message"]
    assert "has no attribute" not in body["message"]
    assert any(r.exc_info for r in caplog.records)


# ── /vault/recover ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/vault/recover", "/vault/recovery-code"])
def test_recovery_endpoints_require_the_api_key(client, vault, path):
    """Gated exactly like every other vault endpoint, via the global hook."""
    resp = client.post(path, json={"recovery_code": WRONG_CODE,
                                   "new_password": NEW_PIN})
    assert resp.status_code == 401
    assert "recover" not in str(api_server._PUBLIC_ENDPOINTS)


def test_recovery_round_trips_end_to_end(client, vault):
    _, code = vault.create_with_recovery(GOOD_PIN)
    vault.store("HA_TOKEN", "eyJ.kept.through.recovery")
    vault.lock()

    resp = client.post("/vault/recover",
                       json={"recovery_code": code, "new_password": NEW_PIN},
                       headers=_auth())

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    # A session token usable against the rest of the vault API.
    contents = client.get(
        "/vault/contents",
        headers=_auth({"X-Vault-Token": body["session_token"]}))
    assert contents.status_code == 200
    assert "HA_TOKEN" in [i["key"] for i in contents.get_json()["items"]]

    # A fresh code, returned once, with an instruction to write it down.
    new_code = body["recovery_code"]
    assert new_code and new_code != code
    assert "write this code down" in body["recovery_code_notice"].lower()

    # The old PIN and the old code are both retired; the new ones both work.
    vault.lock()
    with pytest.raises(AuthenticationError):
        vault.unlock(GOOD_PIN)
    vault.unlock(NEW_PIN)
    vault.lock()
    assert vault.redeem_recovery_code(new_code, "third-strong-pin-here")


def test_recovery_requires_a_new_password_in_the_same_call(client, vault):
    _, code = vault.create_with_recovery(GOOD_PIN)
    vault.lock()

    resp = client.post("/vault/recover", json={"recovery_code": code},
                       headers=_auth())

    assert resp.status_code == 400
    assert "new vault password is required" in resp.get_json()["message"]
    # Nothing was spent: the code still works.
    assert vault.redeem_recovery_code(code, NEW_PIN)


def test_a_weak_new_pin_is_refused_400_and_nothing_is_re_keyed(client, vault):
    _, code = vault.create_with_recovery(GOOD_PIN)
    vault.store("k", "v")
    vault.lock()
    meta_before = open(vault._meta_path, encoding="utf-8").read()

    resp = client.post("/vault/recover",
                       json={"recovery_code": code, "new_password": "abc"},
                       headers=_auth())

    assert resp.status_code == 400
    assert resp.get_json()["weak_password"] is True
    assert vault.vault_dir not in resp.get_data(as_text=True)
    # Refused before any unwrap: the vault is byte-for-byte as it was, the old
    # PIN still works, and the code has not been spent.
    assert open(vault._meta_path, encoding="utf-8").read() == meta_before
    vault.unlock(GOOD_PIN)
    assert vault.retrieve("k") == "v"
    vault.lock()
    assert vault.redeem_recovery_code(code, NEW_PIN)


def test_a_wrong_code_is_401_and_reveals_nothing(client, vault):
    _, code = vault.create_with_recovery(GOOD_PIN)
    vault.lock()

    resp = client.post("/vault/recover",
                       json={"recovery_code": WRONG_CODE, "new_password": NEW_PIN},
                       headers=_auth())

    assert resp.status_code == 401
    body = resp.get_json()
    assert body["success"] is False
    blob = resp.get_data(as_text=True)
    # Nothing about the real code, the real PIN, the vault path, or how close
    # the guess was.
    assert code not in blob
    assert code.replace("-", "") not in blob
    assert GOOD_PIN not in blob
    assert vault.vault_dir not in blob
    assert "recovery_code" not in body
    # And the real code still works afterwards.
    assert vault.redeem_recovery_code(code, NEW_PIN)


def test_a_vault_with_no_recovery_code_says_so_rather_than_wrong_code(client, vault):
    vault.create(GOOD_PIN)      # no recovery code issued
    vault.lock()

    resp = client.post("/vault/recover",
                       json={"recovery_code": WRONG_CODE, "new_password": NEW_PIN},
                       headers=_auth())

    assert resp.status_code == 401
    body = resp.get_json()
    assert body["recovery_available"] is False
    assert "no recovery code" in body["message"]
    assert "does not match" not in body["message"]


def test_a_legacy_vault_is_409_with_its_own_explanation(client, vault, tmp_path):
    """A vault written by the original single-secret format has no second wrap
    to unwrap. That is not a wrong code and must not read as one."""
    from cryptography.fernet import Fernet

    os.makedirs(vault.vault_dir, exist_ok=True)
    salt = os.urandom(16)
    key = vault_mod._derive_key(GOOD_PIN, salt, vault_mod._PBKDF2_ITERATIONS)
    with open(vault._salt_path, "wb") as fh:
        fh.write(salt)
    with open(vault._data_path, "wb") as fh:
        fh.write(Fernet(key).encrypt(b'{"secrets": {}, "documents": {}}'))
    assert vault.is_legacy

    resp = client.post("/vault/recover",
                       json={"recovery_code": WRONG_CODE, "new_password": NEW_PIN},
                       headers=_auth())

    assert resp.status_code == 409
    body = resp.get_json()
    assert body["recovery_available"] is False
    assert body["legacy_vault"] is True
    assert "predates recovery codes" in body["message"]
    # Our own fixed wording, not the vault's - some VaultError messages embed
    # the vault directory, and a filesystem path is not something an HTTP
    # response should carry just to sound specific.
    blob = resp.get_data(as_text=True)
    assert vault.vault_dir not in blob
    assert str(tmp_path) not in blob
    assert "vault_data" not in blob
    assert "\\\\" not in blob


def test_recovery_cooldown_is_429_and_independent_of_the_pin_cooldown(client, vault):
    """Hammering the PIN must never close the owner's way back in."""
    _, code = vault.create_with_recovery(GOOD_PIN)
    vault.lock()

    # Drive the PIN counter into cooldown over HTTP.
    for _ in range(vault_mod._FREE_ATTEMPTS + 1):
        client.post("/vault/unlock", json={"password": "definitely-wrong"},
                    headers=_auth())
    pin_resp = client.post("/vault/unlock", json={"password": GOOD_PIN},
                           headers=_auth())
    assert pin_resp.status_code == 429

    # The recovery path is untouched: the correct code still redeems.
    resp = client.post("/vault/recover",
                       json={"recovery_code": code, "new_password": NEW_PIN},
                       headers=_auth())
    assert resp.status_code == 200


def test_the_recovery_cooldown_is_its_own_counter(client, vault):
    """And the converse: hammering recovery does not lock out the PIN."""
    vault.create_with_recovery(GOOD_PIN)
    vault.lock()

    for _ in range(vault_mod._FREE_ATTEMPTS + 1):
        client.post("/vault/recover",
                    json={"recovery_code": WRONG_CODE, "new_password": NEW_PIN},
                    headers=_auth())
    rec_resp = client.post("/vault/recover",
                           json={"recovery_code": WRONG_CODE,
                                 "new_password": NEW_PIN},
                           headers=_auth())
    assert rec_resp.status_code == 429
    assert rec_resp.get_json()["retry_after"] >= 1

    # The owner can still get in the front door with their PIN.
    assert client.post("/vault/unlock", json={"password": GOOD_PIN},
                       headers=_auth()).status_code == 200


def test_the_recovery_cooldown_rejects_before_the_key_derivation_runs(client, vault, monkeypatch):
    """A throttled request must be cheap for the server: the endpoint must not
    do work ahead of the vault's own pre-KDF throttle check."""
    vault.create_with_recovery(GOOD_PIN)
    vault.lock()
    for _ in range(vault_mod._FREE_ATTEMPTS + 1):
        client.post("/vault/recover",
                    json={"recovery_code": WRONG_CODE, "new_password": NEW_PIN},
                    headers=_auth())

    calls = []
    real_derive = vault_mod._derive_key

    def counting_derive(password, salt, iterations=1000):
        calls.append(iterations)
        return real_derive(password, salt, iterations)

    monkeypatch.setattr(vault_mod, "_derive_key", counting_derive)

    resp = client.post("/vault/recover",
                       json={"recovery_code": WRONG_CODE,
                             "new_password": NEW_PIN},
                       headers=_auth())

    assert resp.status_code == 429
    assert calls == []


def test_a_throttled_request_reports_the_cooldown_not_the_password_policy(client, vault):
    """Ordering: the cooldown is reported ahead of a weak new password, so a
    throttled owner is not told to pick a better password instead."""
    vault.create_with_recovery(GOOD_PIN)
    vault.lock()
    for _ in range(vault_mod._FREE_ATTEMPTS + 1):
        client.post("/vault/recover",
                    json={"recovery_code": WRONG_CODE, "new_password": NEW_PIN},
                    headers=_auth())

    resp = client.post("/vault/recover",
                       json={"recovery_code": WRONG_CODE, "new_password": "abc"},
                       headers=_auth())

    assert resp.status_code == 429


# ── The submitted code is never echoed, never logged ────────────────────────

@pytest.mark.parametrize("outcome", ["wrong", "correct", "weak_pin"])
def test_the_submitted_code_never_appears_in_a_response_or_a_log(client, vault, caplog, outcome):
    _, code = vault.create_with_recovery(GOOD_PIN)
    vault.lock()

    submitted = code if outcome in ("correct", "weak_pin") else WRONG_CODE
    new_pw = "abc" if outcome == "weak_pin" else NEW_PIN

    with caplog.at_level(logging.DEBUG, logger="puffpai"):
        resp = client.post("/vault/recover",
                           json={"recovery_code": submitted,
                                 "new_password": new_pw},
                           headers=_auth())

    body = resp.get_data(as_text=True)
    normalized = submitted.replace("-", "")
    if outcome != "correct":
        assert submitted not in body
        assert normalized not in body
    else:
        # On success the response carries the NEW code only; the spent one and
        # the new password must not be echoed back.
        assert submitted not in body
        assert normalized not in body
        assert NEW_PIN not in body

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert submitted not in logged
    assert normalized not in logged
    assert new_pw not in logged
    assert GOOD_PIN not in logged
    if outcome == "correct":
        assert resp.get_json()["recovery_code"] not in logged


# ── Issuance: the gap that made redemption unreachable ──────────────────────

def _init_via_api(client, password=GOOD_PIN):
    """First-time setup exactly as an API/dashboard client does it."""
    resp = client.post("/vault/unlock",
                       json={"password": password, "initialize": True},
                       headers=_auth())
    assert resp.status_code == 200
    return resp.get_json()


def test_a_vault_created_through_the_api_can_actually_be_recovered(client, vault):
    """The proof that the gap is closed. Before this, a vault created over HTTP
    got no recovery code, so /vault/recover could only ever answer 'this vault
    has no recovery code' - the mechanism existed and no owner could use it."""
    body = _init_via_api(client)
    code = body["recovery_code"]
    assert body["recovery_issued"] is True

    # Put something in it, then walk away and forget the password.
    client.post("/vault/store",
                json={"key": "k", "value": "v"},
                headers=_auth({"X-Vault-Token": body["session_token"]}))
    client.post("/vault/lock", headers=_auth())

    resp = client.post("/vault/recover",
                       json={"recovery_code": code, "new_password": NEW_PIN},
                       headers=_auth())

    assert resp.status_code == 200
    recovered = resp.get_json()
    contents = client.get(
        "/vault/contents",
        headers=_auth({"X-Vault-Token": recovered["session_token"]}))
    assert contents.status_code == 200
    assert "k" in [i["key"] for i in contents.get_json()["items"]]
    # And the owner is left holding a fresh code, not stranded without one.
    assert recovered["recovery_code"] and recovered["recovery_code"] != code


def test_initialization_never_creates_an_unrecoverable_vault(client, vault):
    body = _init_via_api(client)
    assert body["recovery_code"]
    assert vault.has_recovery_code
    # And the pre-existing fields are untouched.
    assert body["success"] is True
    assert body["message"] == "Vault unlocked"
    assert body["mfa_enabled"] is False
    assert body["session_token"]


def test_a_plain_unlock_reports_recovery_state_without_issuing_one(client, vault):
    vault.create(GOOD_PIN)      # an older vault, no code
    vault.lock()

    body = client.post("/vault/unlock", json={"password": GOOD_PIN},
                       headers=_auth()).get_json()

    assert body["success"] is True
    assert body["recovery_issued"] is False
    assert "recovery_code" not in body      # unlocking never mints one


def test_issuing_a_code_requires_an_unlocked_session(client, vault):
    vault.create(GOOD_PIN)
    vault.lock()

    # No token at all.
    resp = client.post("/vault/recovery-code", json={}, headers=_auth())
    assert resp.status_code == 403
    assert "only moment" in resp.get_json()["message"]

    # A made-up token.
    resp = client.post("/vault/recovery-code", json={},
                       headers=_auth({"X-Vault-Token": "not-a-real-token"}))
    assert resp.status_code == 403
    assert not vault.has_recovery_code


def test_an_older_vault_can_gain_a_code_without_being_rebuilt(client, vault):
    vault.create(GOOD_PIN)
    vault.store("kept", "value")
    vault.lock()
    unlocked = client.post("/vault/unlock", json={"password": GOOD_PIN},
                           headers=_auth()).get_json()
    assert unlocked["recovery_issued"] is False

    resp = client.post(
        "/vault/recovery-code", json={},
        headers=_auth({"X-Vault-Token": unlocked["session_token"]}))

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["replaced"] is False
    assert body["recovery_issued"] is True
    assert "write this code down" in body["recovery_code_notice"].lower()

    # The contents survived, the PIN still works, and the new code redeems.
    vault.lock()
    vault.unlock(GOOD_PIN)
    assert vault.retrieve("kept") == "value"
    vault.lock()
    resp = client.post("/vault/recover",
                       json={"recovery_code": body["recovery_code"],
                             "new_password": NEW_PIN},
                       headers=_auth())
    assert resp.status_code == 200


def test_even_a_legacy_vault_can_reach_a_recovery_code_over_http(client, vault):
    """The strongest form of the claim: no vault an owner can still open is
    stuck without a way back in. A legacy single-secret vault upgrades itself on
    unlock, and can then be issued a code without leaving HTTP."""
    from cryptography.fernet import Fernet

    os.makedirs(vault.vault_dir, exist_ok=True)
    salt = os.urandom(16)
    key = vault_mod._derive_key(GOOD_PIN, salt, vault_mod._PBKDF2_ITERATIONS)
    with open(vault._salt_path, "wb") as fh:
        fh.write(salt)
    with open(vault._data_path, "wb") as fh:
        fh.write(Fernet(key).encrypt(
            b'{"secrets": {"old": "kept"}, "documents": {}}'))
    assert vault.is_legacy

    # Step 1: unlock with the password the owner still has. This upgrades it.
    unlocked = client.post("/vault/unlock", json={"password": GOOD_PIN},
                           headers=_auth()).get_json()
    assert unlocked["success"] is True
    assert unlocked["recovery_issued"] is False
    assert not vault.is_legacy

    # Step 2: issue a code.
    issued = client.post(
        "/vault/recovery-code", json={},
        headers=_auth({"X-Vault-Token": unlocked["session_token"]}))
    assert issued.status_code == 200
    code = issued.get_json()["recovery_code"]

    # Step 3: it really redeems, and the original contents came through.
    client.post("/vault/lock", headers=_auth())
    recovered = client.post("/vault/recover",
                            json={"recovery_code": code,
                                  "new_password": NEW_PIN},
                            headers=_auth())
    assert recovered.status_code == 200
    assert vault.retrieve("old") == "kept"


def test_an_existing_code_is_never_silently_replaced(client, vault):
    _, original = vault.create_with_recovery(GOOD_PIN)
    vault.lock()
    unlocked = client.post("/vault/unlock", json={"password": GOOD_PIN},
                           headers=_auth()).get_json()
    headers = _auth({"X-Vault-Token": unlocked["session_token"]})

    resp = client.post("/vault/recovery-code", json={}, headers=headers)

    assert resp.status_code == 409
    body = resp.get_json()
    assert body["replace_required"] is True
    assert body["recovery_issued"] is True
    assert "already wrote down" in body["message"]
    assert "recovery_code" not in body
    # Nothing rotated: the code on paper still works.
    vault.lock()
    assert vault.redeem_recovery_code(original, NEW_PIN)


def test_replacing_a_code_is_possible_but_only_when_asked_for(client, vault):
    _, original = vault.create_with_recovery(GOOD_PIN)
    vault.lock()
    unlocked = client.post("/vault/unlock", json={"password": GOOD_PIN},
                           headers=_auth()).get_json()
    headers = _auth({"X-Vault-Token": unlocked["session_token"]})

    resp = client.post("/vault/recovery-code", json={"replace": True},
                       headers=headers)

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["replaced"] is True
    replacement = body["recovery_code"]
    assert replacement != original

    # The old code is retired the instant the new one is issued - which is
    # exactly why the endpoint refuses to do this without being asked.
    vault.lock()
    with pytest.raises(AuthenticationError):
        vault.redeem_recovery_code(original, NEW_PIN)
    assert vault.redeem_recovery_code(replacement, NEW_PIN)


def test_an_issued_code_is_never_logged(client, vault, caplog):
    vault.create(GOOD_PIN)
    vault.lock()
    unlocked = client.post("/vault/unlock", json={"password": GOOD_PIN},
                           headers=_auth()).get_json()

    with caplog.at_level(logging.DEBUG, logger="puffpai"):
        resp = client.post(
            "/vault/recovery-code", json={},
            headers=_auth({"X-Vault-Token": unlocked["session_token"]}))

    code = resp.get_json()["recovery_code"]
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert code not in logged
    assert code.replace("-", "") not in logged


def test_the_code_minted_at_initialization_is_never_logged(client, vault, caplog):
    with caplog.at_level(logging.DEBUG, logger="puffpai"):
        body = _init_via_api(client)

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert body["recovery_code"] not in logged
    assert body["recovery_code"].replace("-", "") not in logged
    assert GOOD_PIN not in logged


# ── No vault endpoint leaks exception text on a 500 ─────────────────────────

_MARKER = "SECRET-INTERNAL-DETAIL-C:/some/path/vault_data"


def _raiser(*_a, **_k):
    raise RuntimeError(_MARKER)


def test_no_vault_endpoint_returns_exception_text_on_an_internal_fault(
        client, vault, monkeypatch, caplog):
    """Same class of leak as the original bug, on every remaining vault route.
    A valid session token is needed to reach most of these, so this is defence
    in depth rather than the front line - but the front line already failed
    once."""
    _, code = vault.create_with_recovery(GOOD_PIN)
    unlocked = client.post("/vault/unlock", json={"password": GOOD_PIN},
                           headers=_auth()).get_json()
    vheaders = _auth({"X-Vault-Token": unlocked["session_token"]})

    cases = [
        ("list_secrets", lambda: client.get("/vault/contents", headers=vheaders)),
        ("store", lambda: client.post("/vault/store",
                                      json={"key": "k", "value": "v"},
                                      headers=vheaders)),
        ("retrieve", lambda: client.get("/vault/item/k", headers=vheaders)),
        ("delete", lambda: client.delete("/vault/item/k", headers=vheaders)),
        ("lock", lambda: client.post("/vault/lock", headers=_auth())),
        ("add_recovery_code", lambda: client.post(
            "/vault/recovery-code", json={"replace": True}, headers=vheaders)),
        ("redeem_recovery_code", lambda: client.post(
            "/vault/recover",
            json={"recovery_code": code, "new_password": NEW_PIN},
            headers=_auth())),
        ("unlock", lambda: client.post("/vault/unlock",
                                       json={"password": GOOD_PIN},
                                       headers=_auth())),
    ]

    for attr, call in cases:
        with monkeypatch.context() as mp:
            mp.setattr(vault, attr, _raiser)
            with caplog.at_level(logging.DEBUG, logger="puffpai"):
                caplog.clear()
                resp = call()
            blob = resp.get_data(as_text=True)
            assert resp.status_code == 500, f"{attr} -> {resp.status_code}"
            assert _MARKER not in blob, f"{attr} leaked the exception text"
            assert "RuntimeError" not in blob, f"{attr} leaked the type"
            assert resp.get_json()["internal_error"] is True
            # It has to be in the log, or the fault is undiagnosable.
            assert any(r.exc_info for r in caplog.records), f"{attr} not logged"


def test_the_status_probe_does_not_leak_exception_text(client, vault, monkeypatch):
    vault.create(GOOD_PIN)
    monkeypatch.setattr(api_server, "_vault_recovery_issued", _raiser)

    resp = client.get("/vault/mfa-status", headers=_auth())

    blob = resp.get_data(as_text=True)
    assert _MARKER not in blob
    assert resp.get_json()["recovery_issued"] is False


# ── Status honesty ──────────────────────────────────────────────────────────

def test_mfa_status_reports_whether_a_recovery_code_was_actually_issued(client, vault):
    resp = client.get("/vault/mfa-status", headers=_auth())
    assert resp.get_json()["recovery_issued"] is False

    vault.create(GOOD_PIN)      # capable of recovery, but none issued
    resp = client.get("/vault/mfa-status", headers=_auth())
    body = resp.get_json()
    assert body["vault_initialized"] is True
    assert body["recovery_issued"] is False

    vault.add_recovery_code()
    resp = client.get("/vault/mfa-status", headers=_auth())
    assert resp.get_json()["recovery_issued"] is True


def test_system_status_reports_recovery_issued(client, vault):
    vault.create_with_recovery(GOOD_PIN)
    vault.lock()

    resp = client.get("/system/status", headers=_auth())
    assert resp.status_code == 200
    info = resp.get_json()["vault"]
    assert info["status"] == "locked"
    assert info["recovery_issued"] is True
    # A locked vault's item names are inside the ciphertext; the API says so
    # rather than counting files on disk and calling that an item count.
    assert info["item_count_known"] is False


def test_system_status_does_not_claim_recovery_for_a_vault_without_one(client, vault):
    vault.create(GOOD_PIN)
    vault.lock()

    info = client.get("/system/status", headers=_auth()).get_json()["vault"]
    assert info["recovery_issued"] is False
