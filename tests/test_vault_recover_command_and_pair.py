"""The recovery code is usable: from the Vault page's route and from a terminal.

Before this the wizard made the owner write a recovery code down, and the
only thing that accepted it was ``POST /vault/recover``, with no page and no
command in front of it. What is locked down here:

* ``puffpai vault recover``: the code and the new PIN are read without echo,
  the new PIN is held to the setup wizard's rules, the vault is re-keyed, the
  old code and old PIN stop working, a new code is shown once with the
  wizard's ceremony, and the items the bot filed while the vault was locked
  are put into it.
* The route and the command apply the wizard's PIN rules through the vault's
  ``policy`` hook, which runs AFTER the cooldown check and BEFORE any key
  derivation, exactly where the vault's own floor runs.
* A handle that had the vault open when ANOTHER handle re-keyed it (the
  server, while the terminal recovered) locks itself instead of serving the
  old contents under the old key, or writing to a file the vault no longer
  reads.
* ``puffpai pair`` prints this build's code and when it stops, never the
  secret; the pairing command everywhere is that one line.

Run:
    pytest tests/test_vault_recover_command_and_pair.py
"""
from __future__ import annotations

import json
import os
import tempfile

os.environ.setdefault(
    "PUFFPAI_DATA_DIR", tempfile.mkdtemp(prefix="puffpai_w2c_recover_test_")
)

import pytest  # noqa: E402

from puffpai import api_server, cli, setup_ops, start_cli  # noqa: E402
from puffpai.security import vault as vault_mod  # noqa: E402
from puffpai.security import vault_inbox  # noqa: E402
from puffpai.security.vault import (  # noqa: E402
    AuthenticationError,
    Vault,
    VaultLocked,
    VaultThrottled,
    WeakSecretError,
)

API_KEY = api_server._API_KEY
OLD_PIN = "Maple-4-Lantern"
NEW_PIN = "Tulip-9-Harbor"
WRONG_CODE = "ZZZZZ-ZZZZZ-ZZZZZ-ZZZZZ-QQQQQ"


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch):
    """The real code path at a work factor a test can afford (see
    tests/test_api_vault_recovery.py, which asserts the real values)."""
    monkeypatch.setattr(vault_mod, "_PBKDF2_ITERATIONS", 1000)
    monkeypatch.setattr(vault_mod, "_RECOVERY_PBKDF2_ITERATIONS", 1000)


@pytest.fixture
def vdir(tmp_path):
    return str(tmp_path / "vault")


def _made(vdir: str) -> str:
    """A vault with one secret, locked, and its recovery code."""
    v = Vault(vault_dir=vdir)
    _s, code = v.create_with_recovery(OLD_PIN)
    v.store("wifi", "hunter2-house")
    v.lock()
    return code


def _records(v: Vault) -> list:
    return json.loads(v.retrieve(vault_inbox.PII_RECORD_NAME) or "[]")


# ---------------------------------------------------------------------------
# the vault's policy hook
# ---------------------------------------------------------------------------

def test_policy_runs_after_the_cooldown_check(vdir):
    _made(vdir)
    v = Vault(vault_dir=vdir)
    calls = []

    def policy(pin):
        calls.append(1)
        return False, "no"

    v._attempts["recovery"] = (99, 10**12)  # deep in cooldown
    with pytest.raises(VaultThrottled):
        v.redeem_recovery_code(WRONG_CODE, "x", policy=policy)
    assert calls == [], "a caller in cooldown must hear about the cooldown"


def test_policy_refusal_costs_no_key_derivation_and_spends_nothing(vdir, monkeypatch):
    code = _made(vdir)
    v = Vault(vault_dir=vdir)
    derived = []
    real = vault_mod._derive_key
    monkeypatch.setattr(vault_mod, "_derive_key",
                        lambda *a, **k: derived.append(1) or real(*a, **k))
    with pytest.raises(WeakSecretError) as err:
        v.redeem_recovery_code(code, "1234567890",
                               policy=setup_ops.validate_vault_pin)
    assert derived == []
    assert "1234567890" not in str(err.value)
    # Nothing was re-keyed: the old PIN and the same code still work.
    Vault(vault_dir=vdir).unlock(OLD_PIN)
    _s, new_code = Vault(vault_dir=vdir).redeem_recovery_code(code, NEW_PIN)
    assert new_code and new_code != code


# ---------------------------------------------------------------------------
# a handle re-keyed from elsewhere locks itself (fail closed)
# ---------------------------------------------------------------------------

def test_open_handle_locks_itself_when_another_handle_recovers(vdir):
    code = _made(vdir)
    server = Vault(vault_dir=vdir)
    server.unlock(OLD_PIN)
    assert server.is_unlocked

    terminal = Vault(vault_dir=vdir)
    terminal.redeem_recovery_code(code, NEW_PIN)
    terminal.lock()

    assert server.is_unlocked is False
    with pytest.raises(VaultLocked):
        server.store("late", "would have gone to a dead file")
    with pytest.raises(AuthenticationError):
        server.unlock(OLD_PIN)
    server.unlock(NEW_PIN)
    assert server.retrieve("wifi") == "hunter2-house"
    assert server.retrieve("late") is None


def test_unchanged_vault_stays_open(vdir):
    _made(vdir)
    v = Vault(vault_dir=vdir)
    v.unlock(OLD_PIN)
    v.store("more", "x")
    assert v.is_unlocked and v.retrieve("more") == "x"


# ---------------------------------------------------------------------------
# the route: the wizard's PIN rules, and the inbox
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client()


@pytest.fixture
def served(monkeypatch, vdir):
    code = _made(vdir)
    v = Vault(vault_dir=vdir)
    monkeypatch.setattr(api_server, "_vault_instance", v)
    api_server._vault_sessions.clear()
    yield v, code
    api_server._vault_sessions.clear()


def _auth():
    return {"Authorization": f"Bearer {API_KEY}"}


@pytest.mark.parametrize("pin", ["correct horse battery staple", "1234567890",
                                 "abcabcabc", "Password1"])
def test_route_holds_the_new_pin_to_the_wizard_rules(client, served, pin):
    v, code = served
    r = client.post("/vault/recover", headers=_auth(),
                    json={"recovery_code": code, "new_pin": pin})
    body = r.get_json()
    assert r.status_code == 400 and body["weak_password"] is True
    assert body["code_still_valid"] is True
    assert pin not in json.dumps(body) and code not in json.dumps(body)
    ok = client.post("/vault/recover", headers=_auth(),
                     json={"recovery_code": code, "new_pin": NEW_PIN})
    assert ok.status_code == 200


def test_route_needs_the_api_key(client, served):
    _v, code = served
    r = client.post("/vault/recover",
                    json={"recovery_code": code, "new_pin": NEW_PIN})
    assert r.status_code == 401
    Vault(vault_dir=_v.vault_dir).unlock(OLD_PIN)  # nothing changed


def test_route_recovery_files_the_locked_inbox(client, served):
    v, code = served
    assert vault_inbox.deposit(v.vault_dir, {"source": "test",
                                             "items": {"phone": ["555-0101"]}})
    assert vault_inbox.pending_count(v.vault_dir) == 1
    r = client.post("/vault/recover", headers=_auth(),
                    json={"recovery_code": code, "new_pin": NEW_PIN})
    assert r.status_code == 200
    assert vault_inbox.pending_count(v.vault_dir) == 0
    assert _records(v)[-1]["items"]["phone"] == ["555-0101"]
    token = r.get_json()["session_token"]
    listing = client.get("/vault/contents",
                         headers={**_auth(), "X-Vault-Token": token})
    assert listing.status_code == 200


def test_old_route_token_dies_when_the_terminal_recovers(client, served):
    v, code = served
    r = client.post("/vault/unlock", headers=_auth(), json={"pin": OLD_PIN})
    token = r.get_json()["session_token"]
    assert client.get("/vault/contents",
                      headers={**_auth(), "X-Vault-Token": token}).status_code == 200
    Vault(vault_dir=v.vault_dir).redeem_recovery_code(code, NEW_PIN)
    assert client.get("/vault/contents",
                      headers={**_auth(), "X-Vault-Token": token}).status_code == 403


# ---------------------------------------------------------------------------
# puffpai vault recover
# ---------------------------------------------------------------------------

class _Keys:
    """Stands in for the keyboard: answers hidden prompts in order and
    records which prompts were hidden."""

    def __init__(self, hidden, typed_back=("",)):
        self.hidden = list(hidden)
        self.typed_back = list(typed_back)
        self.hidden_prompts = []

    def read_pin(self, prompt):
        self.hidden_prompts.append(prompt)
        return self.hidden.pop(0) if self.hidden else ""

    def read_line(self, prompt):
        return self.typed_back.pop(0) if self.typed_back else ""


def _run_recover(monkeypatch, capsys, vdir, keys, *, tty=True):
    monkeypatch.setattr(cli, "_vault_dir", lambda cfg: vdir)
    monkeypatch.setattr(cli, "_read_pin", keys.read_pin)
    monkeypatch.setattr(cli, "_read_line", keys.read_line)
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: tty)
    args = cli.build_parser().parse_args(["vault", "recover"])
    rc = args.func(args)
    return rc, capsys.readouterr().out


def _new_code_in(out: str, old: str) -> str:
    for line in out.splitlines():
        text = line.strip()
        if len(text) == len(old) and text.count("-") == old.count("-") and text != old:
            return text
    raise AssertionError("no new recovery code on the screen")


def test_terminal_recover_happy_path(monkeypatch, capsys, vdir):
    code = _made(vdir)
    assert vault_inbox.deposit(vdir, {"source": "test",
                                      "items": {"phone": ["555-0199"]}})
    keys = _Keys([code, NEW_PIN, NEW_PIN])
    rc, out = _run_recover(monkeypatch, capsys, vdir, keys)
    assert rc == 0
    # Everything secret was asked for without echo.
    assert keys.hidden_prompts == ["Recovery code: ", "New PIN: ",
                                   "Type the new PIN once more: "]
    assert code not in out and NEW_PIN not in out and OLD_PIN not in out
    assert "no longer work" in out
    assert "1 personal detail(s)" in out
    assert "build record" not in out  # this command keeps none
    new_code = _new_code_in(out, code)

    # Data survived; the new PIN opens it; the inbox item is in it.
    v = Vault(vault_dir=vdir)
    v.unlock(NEW_PIN)
    assert v.retrieve("wifi") == "hunter2-house"
    assert _records(v)[-1]["items"]["phone"] == ["555-0199"]
    v.lock()
    # The old PIN and the old code do not work; the new code does.
    with pytest.raises(AuthenticationError):
        Vault(vault_dir=vdir).unlock(OLD_PIN)
    with pytest.raises(AuthenticationError):
        Vault(vault_dir=vdir).redeem_recovery_code(code, "Another-7-Pin")
    Vault(vault_dir=vdir).redeem_recovery_code(new_code, "Another-7-Pin")


def test_terminal_recover_typed_back_code_is_checked(monkeypatch, capsys, vdir):
    code = _made(vdir)
    seen = {}

    class _Echo(_Keys):
        def read_line(self, prompt):
            return seen.get("code", "")

    keys = _Echo([code, NEW_PIN, NEW_PIN])
    from puffpai.wizard import secure_vault
    real = secure_vault.show_and_confirm

    def spy(new_code, pin, vault, **kw):
        seen["code"] = new_code
        return real(new_code, pin, vault, **kw)

    monkeypatch.setattr(secure_vault, "show_and_confirm", spy)
    rc, out = _run_recover(monkeypatch, capsys, vdir, keys)
    assert rc == 0
    assert "That matches." in out


def test_terminal_recover_wrong_code_changes_nothing(monkeypatch, capsys, vdir):
    _made(vdir)
    rc, out = _run_recover(monkeypatch, capsys, vdir,
                           _Keys([WRONG_CODE, NEW_PIN, NEW_PIN]))
    assert rc == 1 and "does not open this vault" in out
    assert WRONG_CODE not in out
    Vault(vault_dir=vdir).unlock(OLD_PIN)


def test_terminal_recover_refuses_a_weak_pin_before_trying(monkeypatch, capsys, vdir):
    code = _made(vdir)
    called = []
    monkeypatch.setattr(Vault, "redeem_recovery_code",
                        lambda *a, **k: called.append(1))
    rc, out = _run_recover(monkeypatch, capsys, vdir,
                           _Keys([code, "1234567890", "1234567890"]))
    assert rc == 1 and called == []
    assert "still works" in out


def test_terminal_recover_mismatched_pins(monkeypatch, capsys, vdir):
    code = _made(vdir)
    rc, out = _run_recover(monkeypatch, capsys, vdir,
                           _Keys([code, NEW_PIN, "Tulip-9-Harbour"]))
    assert rc == 1 and "did not match" in out
    Vault(vault_dir=vdir).unlock(OLD_PIN)


def test_terminal_recover_without_a_terminal_prints_the_code_once(monkeypatch, capsys, vdir):
    code = _made(vdir)
    rc, out = _run_recover(monkeypatch, capsys, vdir,
                           _Keys([code, NEW_PIN, NEW_PIN]), tty=False)
    assert rc == 0
    assert "Nobody typed this recovery code back." in out
    new_code = _new_code_in(out, code)
    assert out.count(new_code) == 1


def test_terminal_recover_no_vault(monkeypatch, capsys, tmp_path):
    rc, out = _run_recover(monkeypatch, capsys, str(tmp_path / "none"), _Keys([]))
    assert rc == 1 and "nothing to" in out


def test_terminal_recover_is_a_listed_action():
    args = cli.build_parser().parse_args(["vault", "recover"])
    assert args.vault_action == "recover"
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["vault", "recover", "--recovery-code", "x"])


# ---------------------------------------------------------------------------
# puffpai pair
# ---------------------------------------------------------------------------

def test_puffpai_pair_prints_this_builds_code_and_never_the_secret(tmp_path, capsys, monkeypatch):
    target = tmp_path / "pairing" / "pairing.json"
    monkeypatch.setenv("PUFF_PAIRING_STATE", str(target))
    args = cli.build_parser().parse_args(["pair"])
    assert args.func is start_cli.cmd_pair
    assert args.func(args) == 0
    out = capsys.readouterr().out
    state = json.loads(target.read_text(encoding="utf-8"))
    from puffpai import pairing_code
    assert pairing_code.format_code(state["code"]) in out
    assert state["secret"] not in out
    assert "It works until" in out and ("am," in out or "pm," in out)


def test_puffpai_pair_with_a_broken_file_says_so(tmp_path, capsys, monkeypatch):
    target = tmp_path / "pairing.json"
    target.write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv("PUFF_PAIRING_STATE", str(target))
    args = cli.build_parser().parse_args(["pair"])
    assert args.func(args) == 1
    assert "could not read" in capsys.readouterr().out
    assert target.read_text(encoding="utf-8") == "{ not json"  # left alone


def test_the_pairing_words_are_plain():
    from puffpai import plain_words
    lines = cli.pairing_block_lines()
    assert plain_words.check("\n".join(lines)) == []
    assert cli.pair_code_command() == "puffpai pair"
