"""The vault's safety fixes.

* **It locks itself when left alone, on the server.** Wherever it was opened
  (the Vault page, a chat, voice), the vault locks after its idle time
  without depending on any page being open, and the page says how long.
* **The PIN check** refuses 1234567890 and the other runs and wrap-arounds,
  and keeps accepting the PINs it accepted before.
* **A missing vault drive** is said plainly ("Your vault drive is not plugged
  in") and no new vault is ever made in its place.
* **.gitignore** covers the vault inbox folder.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

import pytest

from puffpai import api_server, setup_ops, zone_vault
from puffpai.config import get_config
from puffpai.security import vault as vault_mod
from puffpai.security.vault import Vault, VaultLocked, VaultMissing

API_KEY = api_server._API_KEY
GOOD_PIN = "correct horse battery staple"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch):
    """The real code path at a work factor a test suite can afford."""
    monkeypatch.setattr(vault_mod, "_PBKDF2_ITERATIONS", 1000)
    monkeypatch.setattr(vault_mod, "_RECOVERY_PBKDF2_ITERATIONS", 1000)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _open_vault(tmp_path, seconds=300.0):
    v = Vault(vault_dir=str(tmp_path / "v"), idle_lock_seconds=seconds)
    clock = _Clock()
    v._clock = clock
    v.create(GOOD_PIN)
    return v, clock


# ── it locks itself when left alone ─────────────────────────────────────────

def test_the_default_idle_time_is_five_minutes_in_plain_words():
    assert vault_mod.IDLE_LOCK_SECONDS == 300
    line = vault_mod.idle_lock_line()
    assert line.startswith("Your vault locks itself after 5 minutes without use")
    assert "a chat or voice" in line and "—" not in line
    assert vault_mod.idle_lock_line(60).startswith(
        "Your vault locks itself after 1 minute without use")


def test_an_open_vault_locks_after_its_idle_time(tmp_path):
    v, clock = _open_vault(tmp_path)
    reasons = []
    v.add_lock_listener(reasons.append)
    v.store("a", "1")
    clock.now += 299
    assert v.is_unlocked
    clock.now += 2
    assert not v.is_unlocked
    assert reasons == ["idle"]
    assert v._data_key is None and v._data == {"secrets": {}, "documents": {}}
    with pytest.raises(VaultLocked):
        v.retrieve("a")


def test_using_the_vault_starts_its_idle_time_again(tmp_path):
    v, clock = _open_vault(tmp_path)
    clock.now += 240
    v.store("a", "1")               # the owner used it
    clock.now += 240
    assert v.retrieve("a") == "1"   # 240 s since the last use
    clock.now += 301
    assert not v.is_unlocked


def test_looking_at_whether_it_is_open_is_not_use(tmp_path):
    v, clock = _open_vault(tmp_path)
    for _ in range(10):
        clock.now += 40
        v.is_unlocked               # a page polling the status
        v.idle_seconds_left()
    assert not v.is_unlocked


def test_an_automatic_key_read_does_not_keep_it_open(tmp_path):
    v, clock = _open_vault(tmp_path)
    v._data["secrets"]["__puffpai.inbox"] = "x"
    clock.now += 200
    assert v._reserved_get("__puffpai.inbox") == "x"
    clock.now += 101
    assert not v.is_unlocked


def test_the_watchdog_locks_it_with_nobody_looking(tmp_path):
    v, clock = _open_vault(tmp_path)
    clock.now += 301
    vault_mod.watch_once()
    assert v._data_key is None and v._session is None


def test_the_watchdog_thread_locks_it_on_real_time(tmp_path):
    v = Vault(vault_dir=str(tmp_path / "v"), idle_lock_seconds=0.3)
    v.create(GOOD_PIN)
    deadline = time.monotonic() + 5.0
    while v._data_key is not None and time.monotonic() < deadline:
        time.sleep(0.05)          # nothing touches the vault meanwhile
    assert v._data_key is None and v._session is None


def test_nobody_can_leave_it_open_for_hours(tmp_path):
    v = Vault(vault_dir=str(tmp_path / "v"), idle_lock_seconds=10 ** 9)
    assert v.idle_lock_seconds == 3600
    assert Vault(vault_dir=str(tmp_path / "w"), idle_lock_seconds=0).idle_lock_seconds > 0


# ── wherever it was opened: page, chat, voice ───────────────────────────────

@pytest.fixture
def client():
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client()


@pytest.fixture
def served(monkeypatch, tmp_path):
    v = Vault(vault_dir=str(tmp_path / "vault"))
    clock = _Clock()
    v._clock = clock
    v.add_lock_listener(api_server._on_vault_locked)
    monkeypatch.setattr(api_server, "_vault_instance", v)
    api_server._vault_sessions.clear()
    api_server._VAULT_CHAT_STATE.clear()
    yield v, clock
    api_server._vault_sessions.clear()
    api_server._VAULT_CHAT_STATE.clear()


def _auth(extra=None):
    headers = {"Authorization": f"Bearer {API_KEY}"}
    headers.update(extra or {})
    return headers


def test_opened_on_the_page_it_locks_without_the_page(served, client):
    v, clock = served
    # The page's own create route holds to the PIN rules (no spaces).
    got = client.post("/vault/unlock", json={"password": "Tulip-9-Harbor", "initialize": True},
                      headers=_auth())
    token = got.get_json()["session_token"]
    ok = client.get("/vault/contents", headers=_auth({"X-Vault-Token": token}))
    assert ok.status_code == 200
    clock.now += 301
    vault_mod.watch_once()          # the server side, no request involved
    assert api_server._vault_sessions == {}
    later = client.get("/vault/contents", headers=_auth({"X-Vault-Token": token}))
    assert later.status_code == 403
    # Opening it again does not bring the old page token back.
    client.post("/vault/unlock", json={"password": "Tulip-9-Harbor"}, headers=_auth())
    again = client.get("/vault/contents", headers=_auth({"X-Vault-Token": token}))
    assert again.status_code == 403


def test_opened_in_a_chat_or_by_voice_it_locks_by_itself(served, monkeypatch):
    v, clock = served
    v.create(GOOD_PIN)
    v.lock()
    monkeypatch.setattr(api_server, "_synthesize_from_vault", lambda q, docs: "answer")
    answer = api_server._try_unlock_and_answer(GOOD_PIN, "my passport number", "voice-1")
    assert "answer" in answer and v.is_unlocked
    assert api_server._VAULT_CHAT_STATE["voice-1"].get("vault_session")
    clock.now += 301
    vault_mod.watch_once()
    assert not v.is_unlocked
    assert "vault_session" not in api_server._VAULT_CHAT_STATE["voice-1"]


def test_the_page_is_told_how_long_in_plain_words(served, client):
    got = client.get("/vault/mfa-status", headers=_auth()).get_json()
    assert got["auto_lock_seconds"] == 300 and got["auto_lock_minutes"] == 5
    assert got["auto_lock_line"] == vault_mod.idle_lock_line(300)
    assert got["vault_missing"] is False


def test_the_vault_page_shows_the_servers_words():
    page = open(os.path.join(REPO, "dashboard", "app", "dashboard", "vault",
                             "page.tsx"), encoding="utf-8").read()
    assert "status.auto_lock_seconds" in page and "{autoLockLine}" in page
    assert "15 * 60" not in page


# ── the PIN check ───────────────────────────────────────────────────────────

REFUSED = ["1234567890", "0987654321", "2345678901", "7890123456", "9876543210",
           "1357913579", "9630741852", "qwertyuiop", "poiuytrewq", "asdfghjkl",
           "xyzabcdefg", "abcd1234", "1234554321", "qwerty123", "1qaz2wsx",
           "abcdefgh", "ABCDEFGHIJ", "abcabcabc",
           # three or more runs back to back a run
           # with a character left over, a short block with a part-block
           "123456123456123456", "qwertqwertqwert", "abcdabcdabcd",
           "1234512345123", "9876598765987", "123abc456", "abcdefgh1",
           "abcabcab", "12121212121",
           # fixed-step runs back to back and keypad column walks:
           # evens then odds, odds then evens, both counting down,
           # phone keypad columns in two orders, letters and digits by twos
           "2468013579", "1357924680", "8642097531", "1470258369", "3691470258",
           "2580147369", "7418529630", "acegi13579",
           # shapes drawn on the keypad, the corner key shared by two strokes
           # snakes, spirals, the square
           # around the edge, an X and a diamond, and one with a letter added
           "1478963250", "1236987410", "7896321450", "3698741250", "1452369870", "1593574862", "9632147850", "7412369850",
           "1478963250x", "Q1236987410"]
STILL_FINE = ["583920174x", "5839201746", "Tulip-9-Harbor", "Maple-4-Lantern",
              "a-different-strong-pin", "7bS!k2@Lp", "UPPER-lower-123",
              "only-letters-and-dashes", "Pass1234word", "4815162342"]
# "hunter2hunter2" left this list. A
# PIN typed twice is refused by the PIN rule now (the vault's own floor still
# takes it, see test_the_vault_floor_keeps_passphrases).


@pytest.mark.parametrize("pin", REFUSED)
def test_runs_and_wrap_arounds_are_refused(pin):
    ok, why = zone_vault.validate_pin(pin)
    assert not ok and why and pin not in why
    assert not setup_ops.validate_vault_pin(pin)[0]


@pytest.mark.parametrize("pin", STILL_FINE)
def test_the_good_pins_are_still_fine(pin):
    assert zone_vault.validate_pin(pin) == (True, "")


@pytest.mark.parametrize("secret", ["1234567890", "0987654321", "7890123456",
                                    "qwertyuiop", "xyzabcdefgh", "1357913579",
                                    "123456123456123456", "qwertqwertqwert",
                                    "abcdabcdabcd", "1234512345123",
                                    "9876598765987", "abcd1234efgh",
                                    "2468013579", "1357924680", "8642097531",
                                    "1470258369", "3691470258", "2580147369",
                                    "1478963250", "1236987410", "7896321450", "3698741250", "1452369870", "1593574862", "9632147850", "7412369850"])
def test_the_vault_itself_refuses_a_wrap_around_run(secret, tmp_path):
    assert not vault_mod.validate_vault_secret(secret)[0]
    with pytest.raises(vault_mod.WeakSecretError):
        Vault(vault_dir=str(tmp_path / "v")).create(secret)


@pytest.mark.parametrize("pin", ["2468013579", "1357924680", "8642097531",
                                 "1470258369", "3691470258",
                                 "1478963250", "1236987410", "7896321450", "3698741250", "1452369870", "1593574862", "9632147850", "7412369850"])
def test_the_vault_page_will_not_make_a_vault_with_stepped_runs(served, client, pin):
    """The validator's repro: POST /vault/unlock with initialize made a real
    vault from 2468013579 and friends. Now it is refused and nothing is made."""
    v, _clock = served
    got = client.post("/vault/unlock", json={"password": pin, "initialize": True},
                      headers=_auth())
    assert got.status_code == 400
    assert "session_token" not in (got.get_json() or {})
    assert not v.is_initialized and not v.is_unlocked


def test_most_random_pins_are_still_fine():
    """The stepped-run rule must not lock honest owners out: a PIN picked at
    random is almost never refused by it (about 0.2% of ten-digit PINs)."""
    import random
    rng = random.Random(20261002)
    digits = ["".join(rng.choice("0123456789") for _ in range(10)) for _ in range(3000)]
    mixed = ["".join(rng.choice("abcdefghijklmnopqrstuvwxyz0123456789")
                     for _ in range(10)) for _ in range(3000)]
    assert sum(vault_mod.weak_pattern(p) != "" for p in digits) <= 30
    assert sum(vault_mod.weak_pattern(p) != "" for p in mixed) <= 3


def _owner_refuses(pins):
    return sum(not setup_ops.validate_vault_pin(p)[0] for p in pins)


@pytest.mark.parametrize("length,alphabet,most", [
    # dates (0611199001), phone shapes and a word or a name
    # with a number on the end are refused now, which
    # costs some random PINs: 0.94% of these ten-digit ones, 0.28% of the
    # eight-character ones (was 0.6% and 0.06%).
    # keypad shapes are closed as a class (a
    # path through neighbouring keys, or the X, diamond and corners drawn in
    # any order) and the 555 numbers films use: 1.21% of these ten-digit
    # ones on this seed (0.94% before). Every one added is such a shape or
    # number; the other three rows are unchanged.
    (10, "0123456789", 0.013),  # 0.6% before the first rules, 5.1% after them
    (12, "0123456789", 0.003),   # 0.1% before, 2.18% after
    (14, "0123456789", 0.002),   # 0.03% before, 0.83% after
    (8, "abcdefghijklmnopqrstuvwxyz0123456789", 0.004),
])
def test_the_whole_pin_rule_rarely_refuses_a_random_pin(length, alphabet, most):
    """The 0.2% figure was weak_pattern
    alone. The rule the owner meets (setup_ops.validate_vault_pin: the
    wizard's rule, its dressed-up shapes, and the vault's floor) refused 5% of
    random ten-digit PINs, mostly as "a pattern with a character or two
    added". Measured on the validator's own seed."""
    import random
    rng = random.Random(4242)
    pins = ["".join(rng.choice(alphabet) for _ in range(length))
            for _ in range(10000)]
    assert _owner_refuses(pins) <= most * len(pins)


def test_the_random_pin_the_validator_named_is_fine_again():
    assert setup_ops.validate_vault_pin("7396304647") == (True, "")


@pytest.mark.parametrize("pin", ["1478963250", "1236987410", "7896321450", "3698741250", "1452369870", "1593574862", "9632147850", "7412369850"])
def test_a_shape_on_the_keypad_is_named_plainly(pin):
    ok, why = zone_vault.validate_pin(pin)
    assert not ok and "shape on the number pad" in why
    assert vault_mod.weak_pattern(pin) == "keypad"
    assert "\u2014" not in why and "door" not in why.lower()


@pytest.mark.parametrize("secret", ["correct horse battery staple", "hunter2hunter2",
                                    "s3cure-Pass!", "Tr0ub4dor&3"])
def test_the_vault_floor_keeps_passphrases(secret):
    assert vault_mod.validate_vault_secret(secret) == (True, "")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_browser_refuses_them_too():
    ts = os.path.join(REPO, "dashboard", "app", "_lib", "vaultPin.ts")
    url = "file:///" + ts.replace("\\", "/").lstrip("/")
    script = (f"const m = await import({json.dumps(url)});\n"
              "const input = JSON.parse(process.argv[1]);\n"
              "console.log(JSON.stringify(input.map((p) => m.pinRefusal(p))));\n")
    pins = REFUSED + STILL_FINE
    out = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps(pins)],
                         capture_output=True, text=True, encoding="utf-8",
                         timeout=60, check=True).stdout
    browser = json.loads(out.strip().splitlines()[-1])
    for pin, why in zip(pins, browser):
        assert bool(why) == (not setup_ops.validate_vault_pin(pin)[0]), pin


# ── a missing vault drive ───────────────────────────────────────────────────

def _default_vault():
    return Vault()   # the install's own vault: the configured folder


def test_the_place_is_remembered_when_the_vault_is_made():
    v = _default_vault()
    v.create(GOOD_PIN)
    assert vault_mod.read_place() == v.vault_dir


def test_a_missing_drive_is_said_plainly_and_no_new_vault_is_made():
    v = _default_vault()
    v.create(GOOD_PIN)
    v.lock()
    shutil.rmtree(v.vault_dir)            # the drive is unplugged
    again = _default_vault()
    why = again.missing_reason()
    assert why.startswith("Your vault drive is not plugged in.")
    assert "—" not in why
    with pytest.raises(VaultMissing) as exc:
        again.create(GOOD_PIN)
    assert str(exc.value) == why
    assert not os.path.exists(again.vault_dir)      # nothing was made
    with pytest.raises(VaultMissing):
        again.unlock(GOOD_PIN)
    with pytest.raises(VaultMissing):
        setup_ops.create_vault(GOOD_PIN)
    assert not os.path.exists(again.vault_dir)


def test_an_empty_folder_where_the_drive_should_be_is_not_a_vault():
    v = _default_vault()
    v.create(GOOD_PIN)
    v.lock()
    shutil.rmtree(v.vault_dir)
    os.makedirs(v.vault_dir)              # an empty mount point
    with pytest.raises(VaultMissing):
        _default_vault().create(GOOD_PIN)


def test_the_vault_comes_back_when_the_drive_does(tmp_path):
    v = _default_vault()
    v.create(GOOD_PIN)
    v.store("a", "1")
    v.lock()
    kept = str(tmp_path / "kept")
    shutil.move(v.vault_dir, kept)
    assert _default_vault().missing_reason()
    shutil.move(kept, v.vault_dir)        # plugged back in
    back = _default_vault()
    assert back.missing_reason() == ""
    back.unlock(GOOD_PIN)
    assert back.retrieve("a") == "1"


def test_a_setting_pointing_elsewhere_never_makes_a_second_vault(monkeypatch, tmp_path):
    v = _default_vault()
    v.create(GOOD_PIN)
    v.lock()
    elsewhere = str(tmp_path / "elsewhere")
    monkeypatch.setattr(vault_mod, "_default_vault_dir", lambda: elsewhere)
    other = Vault()
    why = other.missing_reason()
    assert why.startswith(f"Your vault is in {v.vault_dir}")
    with pytest.raises(VaultMissing):
        other.create(GOOD_PIN)
    assert not os.path.exists(elsewhere)


def test_a_fresh_install_makes_its_vault_as_before():
    assert vault_mod.read_place() is None
    v = _default_vault()
    assert v.missing_reason() == ""
    v.create(GOOD_PIN)
    assert v.is_initialized


def test_a_vault_opened_somewhere_else_on_purpose_is_left_alone(tmp_path):
    _default_vault().create(GOOD_PIN)
    other = Vault(vault_dir=str(tmp_path / "scratch"))
    assert other.missing_reason() == ""
    other.create(GOOD_PIN)


def test_an_older_install_is_remembered_on_its_first_unlock():
    v = _default_vault()
    v.create(GOOD_PIN)
    v.lock()
    os.remove(vault_mod.place_record_path())
    assert vault_mod.read_place() is None
    _default_vault().unlock(GOOD_PIN)
    assert vault_mod.read_place() == v.vault_dir


def test_every_route_refuses_to_offer_a_new_vault(client, monkeypatch):
    v = _default_vault()
    v.create(GOOD_PIN)
    v.lock()
    shutil.rmtree(v.vault_dir)
    fresh = _default_vault()
    monkeypatch.setattr(api_server, "_vault_instance", fresh)
    status = client.get("/vault/mfa-status", headers=_auth()).get_json()
    assert status["vault_missing"] is True
    assert status["vault_missing_message"].startswith("Your vault drive is not plugged in.")
    for path, body in (("/vault/unlock", {"password": GOOD_PIN, "initialize": True}),
                       ("/setup/vault", {"password": "Tulip-9-Harbor"}),
                       ("/vault/recover", {"recovery_code": "X", "new_password": "Tulip-9-Harbor"})):
        got = client.post(path, json=body, headers=_auth())
        assert got.status_code == 409, path
        data = got.get_json()
        assert data["vault_missing"] is True
        assert data["message"].startswith("Your vault drive is not plugged in.")
    assert not os.path.exists(fresh.vault_dir)
    assert "Your vault drive is not plugged in." in api_server._try_unlock_and_answer(
        GOOD_PIN, "q", "chat-1")


def _unplugged():
    """Make the install's vault, then take its drive away."""
    v = _default_vault()
    v.create(GOOD_PIN)
    v.lock()
    shutil.rmtree(v.vault_dir)
    return v.vault_dir


@pytest.mark.parametrize("argv", [["vault", "status"], ["vault", "store", "testname"],
                                  ["vault", "get", "testname"], ["vault", "unlock"],
                                  ["vault", "recover"]])
def test_the_terminal_says_the_drive_is_not_plugged_in(argv, capsys, monkeypatch):
    """`vault status` said "not created yet" and
    `vault store` said to run setup; neither said where the vault went."""
    from puffpai import cli
    folder = _unplugged()
    monkeypatch.setattr(cli, "_vault_password",
                        lambda *a, **k: pytest.fail("asked for a PIN"), raising=False)
    code = cli.main(argv)
    out = capsys.readouterr().out
    assert code == 1
    assert "Your vault drive is not plugged in." in out
    assert "not created yet" not in out and "No vault exists yet" not in out
    assert "puffpai setup` to" not in out and "Create one" not in out
    assert not os.path.exists(folder)                 # nothing was made


def test_setup_says_so_before_asking_for_a_pin(capsys, monkeypatch):
    from puffpai import cli
    folder = _unplugged()
    monkeypatch.setattr(cli, "_prompt_for_pin",
                        lambda *a, **k: pytest.fail("asked for a PIN"))
    import argparse
    args = argparse.Namespace(skip_vault=False, vault_password=None)
    block = cli._setup_vault_step(args, get_config(), non_interactive=False)
    out = capsys.readouterr().out
    assert "Your vault drive is not plugged in." in out
    assert block["rejected"] is True
    assert not os.path.exists(folder)


def test_a_key_is_not_offered_a_new_vault_when_the_drive_is_out(capsys):
    from keys_support import canary
    from puffpai.security import keys
    _unplugged()
    with pytest.raises(keys.KeyRefused) as exc:
        keys.add_key("marginalia_search", canary(), vault=_default_vault())
    assert exc.value.message.startswith("Your vault drive is not plugged in.")
    assert exc.value.code == "no_vault"


def test_an_older_vault_is_remembered_the_first_time_it_is_seen():
    """A vault made before its place was remembered
    had no record until its next unlock; a drive missing in that window let a
    new, empty vault be made. It is now remembered as soon as it is seen."""
    v = _default_vault()
    v.create(GOOD_PIN)
    v.lock()
    os.remove(vault_mod.place_record_path())
    assert vault_mod.read_place() is None
    _default_vault()                                  # a status read, a start-up
    assert vault_mod.read_place() == v.vault_dir
    shutil.rmtree(v.vault_dir)
    with pytest.raises(VaultMissing):
        _default_vault().create(GOOD_PIN)
    assert not os.path.exists(v.vault_dir)


def test_seeing_a_vault_never_moves_a_remembered_place(monkeypatch, tmp_path):
    v = _default_vault()
    v.create(GOOD_PIN)
    v.lock()
    elsewhere = str(tmp_path / "elsewhere")
    Vault(vault_dir=elsewhere).create(GOOD_PIN)       # another vault, made on purpose
    monkeypatch.setattr(vault_mod, "_default_vault_dir", lambda: elsewhere)
    seen = Vault()                                    # now the configured one
    assert seen.is_initialized and seen.missing_reason() == ""
    assert vault_mod.read_place() == v.vault_dir      # the record is not moved


# ── .gitignore ───────────────────────────────────────────────────────────────

def test_git_ignores_the_vault_inbox_folder():
    from puffpai.security import vault_inbox
    lines = open(os.path.join(REPO, ".gitignore"), encoding="utf-8").read().splitlines()
    assert "vault_data/" in lines and "vault_data.inbox/" in lines
    assert os.path.basename(vault_inbox.inbox_dir("vault_data")) == "vault_data.inbox"
    if shutil.which("git"):
        got = subprocess.run(["git", "check-ignore", "vault_data.inbox/a.box",
                              "vault_data/vault.meta.json"], cwd=REPO,
                             capture_output=True, text=True, timeout=30)
        assert got.stdout.split() == ["vault_data.inbox/a.box",
                                      "vault_data/vault.meta.json"]
