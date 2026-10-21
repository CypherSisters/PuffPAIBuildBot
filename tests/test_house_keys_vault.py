"""B2-1: the Home Assistant token and the Jellyfin key are kept in the vault.

They used to sit in plain text in home_assistant_config.json and
media_server_config.json. Now the key broker keeps each twice: a working copy
in the install's state folder, sealed with this install's machine key, and a
PIN-locked copy in the vault. Lights and media keep working while the vault is
locked, after it is moved or switched, and while its drive is unplugged,
exactly as before. A plain copy is moved in at the next vault open and deleted
only after both copies read back; until then it keeps working.

Nothing here touches the real Credential Manager: the machine key is a file
(the HBB override path), and tests/conftest.py stubs ``keyring``.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import threading

import pytest

from keys_support import PIN, fast_vault, machine_key_file, reset_keys
from puffpai import cli, config as config_mod, setup_ops
from puffpai.security import keys, lan_posture

TOKEN = "eyJhbGciOiJIUzI1NiJ9.house-token-canary-7f3a9c.signature-part-xyz"
JF_KEY = "jellyfin0canary0key0b6e2d41a9"


@pytest.fixture(autouse=True)
def _clean():
    reset_keys()
    lan_posture.clear_active_posture()
    yield
    reset_keys()
    lan_posture.clear_active_posture()


@pytest.fixture
def install(tmp_path, monkeypatch):
    """A temp install under island (a LAN Home Assistant is allowed)."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "puffpai_config.json").write_text("{}", encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(state))
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    config_mod.get_config(_force_reload=True)
    lan_posture.set_active_posture({"run_egress": "island"})
    yield cfg_dir
    monkeypatch.undo()
    config_mod.get_config(_force_reload=True)


@pytest.fixture
def vault(install, tmp_path, monkeypatch):
    """An open vault the broker reaches, and a machine key for the locked copy."""
    machine_key_file(monkeypatch, tmp_path)
    v = fast_vault(monkeypatch, setup_ops._vault_dir())  # noqa: SLF001
    v.unlock(PIN)
    keys.set_vault_provider(lambda: v)
    return v


def _files_holding(root, secret: str) -> list[str]:
    hits = []
    for here, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(here, name)
            with open(path, "rb") as fh:
                if secret.encode() in fh.read():
                    hits.append(path)
    return hits


def _plain_ha(cfg_dir, token=TOKEN):
    (cfg_dir / "home_assistant_config.json").write_text(json.dumps(
        {"url": "http://192.168.1.50:8123", "token": token, "enabled": True}),
        encoding="utf-8")
    config_mod.get_config(_force_reload=True)


# ── setup: no plain text ──────────────────────────────────────────────────

def test_setup_keeps_the_token_out_of_plain_text(install, vault, tmp_path):
    setup_ops.set_ha_config("http://192.168.1.50:8123", TOKEN)
    saved = json.loads((install / "home_assistant_config.json").read_text())
    assert "token" not in saved and saved["enabled"] is True
    assert _files_holding(tmp_path, TOKEN) == [], "the token is in plain text"
    assert setup_ops.ha_configured() is True
    assert keys.house_key("home_assistant") == TOKEN


def test_setup_with_the_vault_locked_writes_as_before(install, vault):
    """Works over hardening: no new question, Home Assistant works at once."""
    vault.lock()
    setup_ops.set_ha_config("http://192.168.1.50:8123", TOKEN)
    saved = json.loads((install / "home_assistant_config.json").read_text())
    assert saved["token"] == TOKEN
    assert setup_ops.ha_configured() is True


# ── the move ──────────────────────────────────────────────────────────────

def test_opening_the_vault_moves_the_plain_token_then_deletes_it(install, vault,
                                                                 tmp_path):
    vault.lock()
    _plain_ha(install)
    assert setup_ops.move_plain_keys() == {"home_assistant": "locked"}
    assert json.loads((install / "home_assistant_config.json").read_text())[
        "token"] == TOKEN, "a locked vault must leave the plain copy"
    vault.unlock(PIN)               # keys.on_vault_opened runs the move
    saved = json.loads((install / "home_assistant_config.json").read_text())
    assert "token" not in saved
    assert saved["url"] == "http://192.168.1.50:8123" and saved["enabled"]
    assert _files_holding(tmp_path, TOKEN) == []
    assert keys.house_key("home_assistant") == TOKEN
    assert config_mod.get_config().ha.token == ""


def test_a_failed_read_back_keeps_the_plain_copy(install, vault, monkeypatch):
    _plain_ha(install)
    monkeypatch.setattr(keys, "_unseal", lambda name, secret: None)
    assert setup_ops.move_plain_keys() == {"home_assistant": "read_back"}
    assert json.loads((install / "home_assistant_config.json").read_text())[
        "token"] == TOKEN
    assert not keys.has_house_key("home_assistant")    # nothing half kept
    assert keys._vault_copy(vault, "home_assistant") is None  # noqa: SLF001


def test_no_machine_key_keeps_the_plain_copy(install, tmp_path, monkeypatch):
    """No way to keep it working while locked: it is not moved at all."""
    monkeypatch.delenv("PUFFPAI_VAULT_SERVICE_KEY_FILE", raising=False)
    v = fast_vault(monkeypatch, setup_ops._vault_dir())      # noqa: SLF001
    v.unlock(PIN)
    keys.set_vault_provider(lambda: v)
    _plain_ha(install)
    assert setup_ops.move_plain_keys() == {"home_assistant": "no_machine_key"}
    assert json.loads((install / "home_assistant_config.json").read_text())[
        "token"] == TOKEN


def test_a_vault_in_another_folder_never_takes_the_token(install, tmp_path,
                                                        monkeypatch):
    """The bridges read the install's own vault: moving the token anywhere
    else would lose it for them, so a vault opened elsewhere is left alone."""
    machine_key_file(monkeypatch, tmp_path)
    _plain_ha(install)
    other = fast_vault(monkeypatch, tmp_path / "some_other_vault")
    other.lock()
    other.unlock(PIN)               # keys.on_vault_opened runs for it too
    assert json.loads((install / "home_assistant_config.json").read_text())[
        "token"] == TOKEN
    assert setup_ops.move_plain_keys(other) == {}


def test_no_vault_at_all_keeps_the_plain_copy(install):
    _plain_ha(install)
    assert setup_ops.move_plain_keys() == {"home_assistant": "locked"}
    assert config_mod.get_config().ha.token == TOKEN


def test_the_jellyfin_key_moves_and_other_settings_stay(install, vault, tmp_path):
    vault.lock()
    (install / "media_server_config.json").write_text(json.dumps(
        {"enabled": True, "jellyfin_url": "http://192.168.1.60:8096",
         "jellyfin_api_key": JF_KEY, "timeout": 7}), encoding="utf-8")
    vault.unlock(PIN)
    saved = json.loads((install / "media_server_config.json").read_text())
    assert "jellyfin_api_key" not in saved
    assert saved["jellyfin_url"] == "http://192.168.1.60:8096"
    assert saved["timeout"] == 7 and saved["enabled"] is True
    assert _files_holding(tmp_path, JF_KEY) == []
    assert keys.house_key("jellyfin") == JF_KEY


def test_a_house_record_is_never_swept(install, vault):
    setup_ops.set_ha_config("http://192.168.1.50:8123", TOKEN)
    keys.purge_removed(vault)
    assert keys._vault_copy(vault, "home_assistant") == TOKEN  # noqa: SLF001
    assert keys.house_key("home_assistant") == TOKEN
    assert keys.has_house_key("home_assistant")


def _bridge_token() -> str:
    from puffpai import ha_bridge
    return ha_bridge.HABridge()._token  # noqa: SLF001


def test_a_restart_with_the_vault_locked_still_has_the_token(install, vault):
    """Upgrade, move at an unlock, then a restart: fresh objects, no open
    vault anywhere. The house still answers."""
    vault.lock()
    _plain_ha(install)
    vault.unlock(PIN)
    vault.lock()
    keys.set_vault_provider(None)
    config_mod.get_config(_force_reload=True)
    assert _bridge_token() == TOKEN
    assert setup_ops.ha_configured()


# ── the vault moves, switches or its drive is unplugged ──────────────────

def test_switching_to_another_vault_keeps_the_house_working(install, vault,
                                                            tmp_path,
                                                            monkeypatch):
    from puffpai.security import vault_place
    setup_ops.set_ha_config("http://192.168.1.50:8123", TOKEN)
    other = tmp_path / "othervault"
    v2 = fast_vault(monkeypatch, other)
    v2.lock()
    vault_place.use_existing(str(other), PIN, current_pin=PIN, vault=vault)
    keys.set_vault_provider(None)
    config_mod.get_config(_force_reload=True)
    assert os.path.samefile(setup_ops._vault_dir(), other)  # noqa: SLF001
    assert _bridge_token() == TOKEN
    assert setup_ops.ha_configured() and setup_ops.ha_key_problem() == ""
    # The PIN-locked copy went along into the vault in use now.
    v2.unlock(PIN)
    assert keys._vault_copy(v2, "home_assistant") == TOKEN  # noqa: SLF001


def test_moving_the_vault_keeps_the_house_working(install, vault, tmp_path):
    from puffpai.security import vault_place
    setup_ops.set_ha_config("http://192.168.1.50:8123", TOKEN)
    vault_place.move_vault(vault, str(tmp_path / "moved"), PIN)
    keys.set_vault_provider(None)
    config_mod.get_config(_force_reload=True)
    assert _bridge_token() == TOKEN


def test_an_unplugged_vault_drive_keeps_the_house_working(install, tmp_path,
                                                          monkeypatch):
    machine_key_file(monkeypatch, tmp_path)
    usb = tmp_path / "usb" / "vault"
    v = fast_vault(monkeypatch, usb)
    setup_ops.write_main_config({"paths": {"vault_data_dir": str(usb)}})
    config_mod.get_config(_force_reload=True)
    keys.set_vault_provider(lambda: v)
    setup_ops.set_ha_config("http://192.168.1.50:8123", TOKEN)
    assert "token" not in json.loads(
        (install / "home_assistant_config.json").read_text())
    v.lock()
    keys.set_vault_provider(None)
    os.rename(tmp_path / "usb", tmp_path / "usb_unplugged")
    config_mod.get_config(_force_reload=True)
    assert keys.house_key("home_assistant") == TOKEN
    assert _bridge_token() == TOKEN
    assert setup_ops.ha_configured()


# ── one writer at a time ──────────────────────────────────────────────────

T2 = "eyJhbGciOiJIUzI1NiJ9.SECOND-token-canary-91be.signature-part-abc"


def test_a_token_saved_during_a_move_is_not_lost(install, vault, monkeypatch):
    """move_plain_keys reads T1; the owner saves T2 from another request
    thread before the move keeps T1. T2 waits for the move, then wins."""
    _plain_ha(install, TOKEN)
    real_put = keys.put_house_key
    saver: dict = {}

    def put(name, value, **kw):
        if kw.get("by") == "move" and "thread" not in saver:
            saver["thread"] = threading.Thread(
                target=setup_ops.set_ha_config,
                args=("http://192.168.1.51:8123", T2))
            saver["thread"].start()
            saver["thread"].join(0.5)       # it must wait for the move
            saver["waited"] = saver["thread"].is_alive()
        return real_put(name, value, **kw)

    monkeypatch.setattr(keys, "put_house_key", put)
    assert setup_ops.move_plain_keys(vault)["home_assistant"] == "moved"
    saver["thread"].join(30)
    assert saver["waited"] is True
    saved = json.loads((install / "home_assistant_config.json").read_text())
    assert saved["url"] == "http://192.168.1.51:8123", "the new address won"
    assert "token" not in saved
    assert keys.house_key("home_assistant") == T2, "the new token won"


def test_a_token_saved_inside_a_move_is_not_lost(install, vault, monkeypatch):
    """The same race with no second thread (the lock is re-entrant there):
    the move sees the file changed under its lock and keeps nothing."""
    _plain_ha(install, TOKEN)
    real_put = keys.put_house_key
    fired: dict = {}

    def put(name, value, **kw):
        if kw.get("by") == "move" and not fired:
            fired[1] = True
            setup_ops.set_ha_config("http://192.168.1.50:8123", T2)
        return real_put(name, value, **kw)

    monkeypatch.setattr(keys, "put_house_key", put)
    assert setup_ops.move_plain_keys(vault)["home_assistant"] == "changed"
    assert keys.house_key("home_assistant") == T2
    assert "token" not in json.loads(
        (install / "home_assistant_config.json").read_text())


# ── two installs, one vault folder ────────────────────────────────────────

def test_two_installs_sharing_a_vault_keep_their_own_tokens(install, vault,
                                                            tmp_path,
                                                            monkeypatch):
    setup_ops.set_ha_config("http://192.168.1.50:8123", TOKEN)
    first_state = os.environ["PUFFPAI_STATE_DIR"]
    other_state = tmp_path / "state_two"
    other_state.mkdir()
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(other_state))
    setup_ops.set_ha_config("http://192.168.1.50:8123", T2)
    assert keys.house_key("home_assistant") == T2
    assert keys._vault_copy(vault, "home_assistant") == T2  # noqa: SLF001
    monkeypatch.setenv("PUFFPAI_STATE_DIR", first_state)
    assert keys.house_key("home_assistant") == TOKEN
    assert keys._vault_copy(vault, "home_assistant") == TOKEN  # noqa: SLF001


# ── the machine key is lost: said plainly, sealed again at the next open ──

def test_a_lost_machine_key_is_said_plainly_and_sealed_again(install, vault,
                                                             tmp_path,
                                                             monkeypatch):
    setup_ops.set_ha_config("http://192.168.1.50:8123", TOKEN)
    vault.lock()
    monkeypatch.delenv("PUFFPAI_VAULT_SERVICE_KEY_FILE")    # the key is gone
    assert keys.house_key("home_assistant") is None
    assert not keys.has_house_key("home_assistant")
    assert not setup_ops.ha_configured()
    problem = setup_ops.ha_key_problem()
    assert "cannot open it" in problem and "Open your vault" in problem
    assert setup_ops.setup_status(probe_ollama=False)["ha"]["problem"] == problem
    from puffpai import developer_api
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(developer_api.bp)
    with app.test_request_context():
        body = developer_api.developer_brain().get_json()
    assert body["home_assistant"]["set_up"] is False
    assert body["home_assistant"]["problem"] == problem
    # A new machine key, and the owner opens the vault: put back.
    new_key = tmp_path / "new_machine.key"
    new_key.write_text(os.urandom(32).hex(), encoding="utf-8")
    monkeypatch.setenv("PUFFPAI_VAULT_SERVICE_KEY_FILE", str(new_key))
    assert keys.house_key("home_assistant") is None      # still locked
    vault.unlock(PIN)
    vault.lock()
    assert keys.house_key("home_assistant") == TOKEN
    assert setup_ops.ha_configured() and setup_ops.ha_key_problem() == ""


def test_switching_ha_off_is_not_set_up(install, vault):
    setup_ops.set_ha_config("http://192.168.1.50:8123", TOKEN)
    (install / "home_assistant_config.json").write_text(json.dumps(
        {"url": "http://192.168.1.50:8123", "enabled": False}))
    assert not setup_ops.ha_configured()


# ── the vault locked: lights and media still work ─────────────────────────

class _Resp:
    status_code = 200
    ok = True

    def raise_for_status(self):
        pass

    def json(self):
        return []


def test_ha_control_works_with_the_vault_locked(install, vault, monkeypatch):
    from puffpai import ha_bridge
    from puffpai.agents.envelope import Envelope
    from puffpai.agents.steward import StewardAgent

    setup_ops.set_ha_config("http://192.168.1.50:8123", TOKEN)
    vault.lock()
    assert not vault.is_unlocked
    bridge = ha_bridge.HABridge()
    sent = []

    def post(url, json=None, **kw):
        sent.append((url, dict(bridge._session.headers)))  # noqa: SLF001
        return _Resp()

    monkeypatch.setattr(bridge._session, "post", post)  # noqa: SLF001
    steward = StewardAgent(ha_bridge=bridge)
    reply = steward.handle(Envelope(
        intent="control_device", origin_agent="test",
        payload={"entity_id": "light.kitchen", "action": "turn_on"}))
    assert reply["ok"] is True, reply
    assert sent and sent[0][1]["Authorization"] == f"Bearer {TOKEN}"


def test_media_works_with_the_vault_locked(install, vault):
    from puffpai import media_bridge
    (install / "media_server_config.json").write_text(json.dumps(
        {"enabled": True, "jellyfin_api_key": JF_KEY}), encoding="utf-8")
    vault.lock()
    vault.unlock(PIN)
    vault.lock()
    config_mod.get_config(_force_reload=True)
    bridge = media_bridge.MediaBridge()
    assert f'Token="{JF_KEY}"' in bridge._session.headers["X-Emby-Authorization"]  # noqa: SLF001


def test_the_jellyfin_key_is_not_on_the_import_command_line(install, vault,
                                                            monkeypatch):
    from puffpai import media_bridge
    (install / "media_server_config.json").write_text(json.dumps(
        {"enabled": True, "jellyfin_api_key": JF_KEY}), encoding="utf-8")
    config_mod.get_config(_force_reload=True)
    bridge = media_bridge.MediaBridge()
    seen = {}

    def run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"imported": 3}), "")

    scans = []
    monkeypatch.setattr(media_bridge.os.path, "isfile", lambda p: True)
    monkeypatch.setattr(media_bridge.subprocess, "run", run)
    monkeypatch.setattr(bridge, "trigger_scan", lambda library="": scans.append(1))
    result = bridge.import_ipod()
    assert result.tracks_imported == 3
    assert not any(JF_KEY in str(part) for part in seen["cmd"])
    assert "--jellyfin-key" not in seen["cmd"]
    assert JF_KEY not in json.dumps(seen["kw"].get("env") or {})
    assert scans == [1], "the bridge asks Jellyfin to rescan itself"


# ── --ha-token ────────────────────────────────────────────────────────────

def test_a_token_on_the_command_line_still_works_and_warns(capsys):
    args = cli.build_parser().parse_args(["setup", "--ha-token", TOKEN])
    assert cli._resolve_ha_token(args.ha_token) == TOKEN    # noqa: SLF001
    err = capsys.readouterr().err
    assert "shell history" in err and TOKEN not in err


@pytest.mark.parametrize("given", [["--ha-token", "-"], ["--ha-token", ""],
                                   ["--ha-token"]])
def test_the_hidden_way_reads_piped_input(given, monkeypatch, capsys):
    cli._reset_stdin_state()                                 # noqa: SLF001
    args = cli.build_parser().parse_args(["setup", *given])
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(TOKEN + "\n"))
    assert cli._resolve_ha_token(args.ha_token) == TOKEN    # noqa: SLF001
    out = capsys.readouterr()
    assert TOKEN not in out.out and TOKEN not in out.err
    cli._reset_stdin_state()                                 # noqa: SLF001


def test_the_hidden_way_asks_without_echo_at_a_terminal(monkeypatch, capsys):
    import getpass
    asked = []
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(getpass, "getpass",
                        lambda prompt="": asked.append(prompt) or TOKEN)
    assert cli._resolve_ha_token("-") == TOKEN               # noqa: SLF001
    assert asked and "hidden" in asked[0]
    assert TOKEN not in capsys.readouterr().out


def test_main_resolves_the_token_before_any_step(monkeypatch):
    seen = {}
    parser = cli.build_parser()
    real = parser.parse_args

    def parse(argv=None):
        ns = real(argv)
        ns.func = lambda a: seen.setdefault("token", a.ha_token) and 0
        return ns

    monkeypatch.setattr(parser, "parse_args", parse)
    monkeypatch.setattr(cli, "build_parser", lambda: parser)
    monkeypatch.setattr(cli, "_read_pin", lambda prompt: TOKEN)
    cli.main(["setup", "--ha-token", "-"])
    assert seen["token"] == TOKEN


# ── second verification: the data folder moves, the machine key changes ──

def _install_at(root, monkeypatch):
    """An install whose state folder lives inside its data folder (no
    PUFFPAI_STATE_DIR), so moving the data folder moves everything."""
    cfg = root / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "puffpai_config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(root))
    monkeypatch.delenv("PUFFPAI_STATE_DIR", raising=False)
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    config_mod.get_config(_force_reload=True)
    lan_posture.set_active_posture({"run_egress": "island"})
    return cfg


def _move_data_folder(tmp_path, monkeypatch):
    import shutil
    a = tmp_path / "a"
    _install_at(a, monkeypatch)
    assert config_mod.get_state_dir().startswith(str(a))
    v = fast_vault(monkeypatch, setup_ops._vault_dir())  # noqa: SLF001
    keys.set_vault_provider(lambda: v)
    setup_ops.set_ha_config("http://192.168.1.50:8123", TOKEN)
    assert "token" not in json.loads(
        (a / "config" / "home_assistant_config.json").read_text())
    v.lock()
    keys.set_vault_provider(None)
    b = tmp_path / "b"
    shutil.copytree(a, b)
    shutil.rmtree(a)
    _install_at(b, monkeypatch)
    keys._house_cache.clear()  # noqa: SLF001 - a new process remembers nothing
    assert keys.house_key("home_assistant") == TOKEN
    assert _bridge_token() == TOKEN
    assert setup_ops.ha_configured() and setup_ops.ha_key_problem() == ""


def test_moving_the_data_folder_keeps_the_house_working(tmp_path, monkeypatch):
    machine_key_file(monkeypatch, tmp_path)
    _move_data_folder(tmp_path, monkeypatch)


@pytest.mark.keyring_stub
def test_moving_the_data_folder_with_the_password_store(tmp_path, monkeypatch):
    """Windows: the machine key is in the password store, under a name that
    moves with the folder, so the moved folder still opens it."""
    import types
    from puffpai.security import vault as vault_mod
    store: dict = {}
    ring = types.SimpleNamespace(
        get_password=lambda s, n: store.get((s, n)),
        set_password=lambda s, n, val: store.__setitem__((s, n), val),
        delete_password=lambda s, n: store.pop((s, n), None))
    monkeypatch.setattr(vault_mod, "_usable_keyring", lambda: ring)
    monkeypatch.delenv("PUFFPAI_VAULT_SERVICE_KEY_FILE", raising=False)
    _move_data_folder(tmp_path, monkeypatch)
    assert sum(1 for (_s, n) in store if n.startswith("house-keys:")) == 1
    from puffpai import remove_cli
    listed = remove_cli.keyring_names(config_mod.get_state_dir(),
                                      setup_ops._vault_dir())  # noqa: SLF001
    assert ("puffpai-buildbot", keys.house_keyring_user()) in listed


def test_a_different_machine_key_is_not_set_up(install, vault, tmp_path,
                                               monkeypatch):
    setup_ops.set_ha_config("http://192.168.1.50:8123", TOKEN)
    vault.lock()
    keys.set_vault_provider(None)
    other = tmp_path / "other.key"
    other.write_text(os.urandom(32).hex(), encoding="utf-8")
    monkeypatch.setenv("PUFFPAI_VAULT_SERVICE_KEY_FILE", str(other))
    from puffpai.security import vault as vault_mod
    runs = []
    real = vault_mod._scrypt_kek  # noqa: SLF001
    monkeypatch.setattr(vault_mod, "_scrypt_kek",
                        lambda *a, **k: runs.append(1) or real(*a, **k))
    assert keys.has_house_key("home_assistant") is False
    assert setup_ops.ha_configured() is False
    assert runs == [], "has_house_key compares a fingerprint, no scrypt"
    problem = setup_ops.ha_key_problem()
    assert problem == keys.HOUSE_KEY_UNREADABLE["home_assistant"]
    assert setup_ops.setup_status(probe_ollama=False)["ha"]["problem"] == problem
    assert keys.house_key("home_assistant") is None
    # Her advice works: opening the vault seals it again with this key.
    keys.set_vault_provider(lambda: vault)
    vault.unlock(PIN)
    vault.lock()
    assert keys.house_key("home_assistant") == TOKEN
    assert setup_ops.ha_configured() and setup_ops.ha_key_problem() == ""


def test_a_box_that_ran_the_first_version_keeps_its_token(install, vault,
                                                          monkeypatch):
    """4e0bbd71 wrote __puffpai.key.svc.<name> (no install id) and a
    key_svc_<name>.json file in the vault folder; the next open moves it."""
    with monkeypatch.context() as m:
        m.setattr(keys, "_house_record_name",
                  lambda name: keys._RESERVED + keys._SVC + name)  # noqa: SLF001
        keys._write_vault_copy(vault, "home_assistant", TOKEN)  # noqa: SLF001
    old_file = os.path.join(vault.vault_dir, "key_svc_home_assistant.json")
    with open(old_file, "wb") as fh:
        fh.write(os.urandom(64))
    (install / "home_assistant_config.json").write_text(json.dumps(
        {"url": "http://192.168.1.50:8123", "enabled": True}))
    config_mod.get_config(_force_reload=True)
    vault.lock()
    assert not setup_ops.ha_configured()
    vault.unlock(PIN)
    vault.lock()
    assert keys.house_key("home_assistant") == TOKEN
    assert setup_ops.ha_configured()
    assert not os.path.exists(old_file)
    vault.unlock(PIN)
    assert keys._legacy_copy(vault, "home_assistant") is None  # noqa: SLF001


def test_plain_keys_are_hidden_from_command_output(install):
    from puffpai import brain_commands
    _plain_ha(install)
    (install / "media_server_config.json").write_text(json.dumps(
        {"enabled": True, "jellyfin_api_key": JF_KEY}), encoding="utf-8")
    config_mod.get_config(_force_reload=True)
    out = brain_commands.hide_secrets(f"a {TOKEN} b {JF_KEY} c")
    assert TOKEN not in out and JF_KEY not in out


def test_an_opened_key_is_not_opened_again(install, vault, monkeypatch):
    setup_ops.set_ha_config("http://192.168.1.50:8123", TOKEN)
    keys._house_cache.clear()  # noqa: SLF001
    from puffpai.security import vault as vault_mod
    runs = []
    real = vault_mod._scrypt_kek  # noqa: SLF001
    monkeypatch.setattr(vault_mod, "_scrypt_kek",
                        lambda *a, **k: runs.append(1) or real(*a, **k))
    assert keys.house_key("home_assistant") == TOKEN
    assert keys.house_key("home_assistant") == TOKEN
    assert len(runs) == 1


def test_media_status_says_when_the_jellyfin_key_cannot_be_opened(
        install, vault, tmp_path, monkeypatch):
    from puffpai import api_server
    (install / "media_server_config.json").write_text(json.dumps(
        {"enabled": True, "jellyfin_api_key": JF_KEY}), encoding="utf-8")
    vault.lock()
    vault.unlock(PIN)                   # moves the key in
    vault.lock()
    assert setup_ops.media_key_problem() == ""
    other = tmp_path / "other.key"
    other.write_text(os.urandom(32).hex(), encoding="utf-8")
    monkeypatch.setenv("PUFFPAI_VAULT_SERVICE_KEY_FILE", str(other))

    class _Bridge:
        def get_status(self):
            return {"enabled": True, "connected": False}

    monkeypatch.setattr(api_server, "_get_media_bridge", lambda: _Bridge())
    with api_server.app.test_request_context():
        body = api_server.media_status().get_json()
    assert body["problem"] == keys.HOUSE_KEY_UNREADABLE["jellyfin"]


def test_no_machine_key_at_setup_keeps_the_token_working(install, tmp_path,
                                                         monkeypatch):
    """No machine key file and no usable password store (the conftest stub
    refuses writes): setup writes the token as before, nothing is half kept,
    and the house answers."""
    monkeypatch.delenv("PUFFPAI_VAULT_SERVICE_KEY_FILE", raising=False)
    v = fast_vault(monkeypatch, setup_ops._vault_dir())      # noqa: SLF001
    keys.set_vault_provider(lambda: v)
    setup_ops.set_ha_config("http://192.168.1.50:8123", TOKEN)
    assert json.loads((install / "home_assistant_config.json").read_text())[
        "token"] == TOKEN
    assert setup_ops.move_plain_keys(v) == {"home_assistant": "no_machine_key"}
    assert _bridge_token() == TOKEN and setup_ops.ha_configured()
    assert not os.path.exists(keys.house_key_file("home_assistant"))


# ── third verification: the live bridge heals; a 9783a49c box gets its id ──

def _fake_ring(monkeypatch) -> dict:
    """An in-memory password store (never the real one)."""
    import types
    from puffpai.security import vault as vault_mod
    store: dict = {}
    ring = types.SimpleNamespace(
        get_password=lambda s, n: store.get((s, n)),
        set_password=lambda s, n, val: store.__setitem__((s, n), val),
        delete_password=lambda s, n: store.pop((s, n), None))
    monkeypatch.setattr(vault_mod, "_usable_keyring", lambda: ring)
    monkeypatch.delenv("PUFFPAI_VAULT_SERVICE_KEY_FILE", raising=False)
    return store


@pytest.mark.keyring_stub
def test_opening_the_vault_heals_the_live_bridge(tmp_path, monkeypatch):
    """The bridges are built once per process. When the machine key was lost
    and an open vault seals the token again, the SAME bridge (Steward holds a
    reference to it) uses the token at once: no restart."""
    from puffpai import ha_bridge
    from puffpai.agents.steward import StewardAgent
    ring = _fake_ring(monkeypatch)
    _install_at(tmp_path / "a", monkeypatch)
    v = fast_vault(monkeypatch, setup_ops._vault_dir())      # noqa: SLF001
    keys.set_vault_provider(lambda: v)
    setup_ops.set_ha_config("http://192.168.1.50:8123", TOKEN)
    v.lock()
    keys.set_vault_provider(None)
    for entry in [k for k in ring if k[1].startswith("house-keys:")]:
        del ring[entry]                                  # the key is gone
    keys._house_cache.clear()  # noqa: SLF001
    monkeypatch.setattr(ha_bridge, "_instance", None)
    bridge = ha_bridge.get_bridge()
    steward = StewardAgent(ha_bridge=bridge)
    assert bridge._token == "" and not setup_ops.ha_configured()  # noqa: SLF001
    keys.set_vault_provider(lambda: v)
    v.unlock(PIN)                       # keys.on_vault_opened seals it again
    assert setup_ops.ha_configured() and setup_ops.ha_key_problem() == ""
    assert ha_bridge.get_bridge() is bridge
    assert bridge._token == TOKEN  # noqa: SLF001
    assert bridge._session.headers["Authorization"] == f"Bearer {TOKEN}"  # noqa: SLF001
    assert steward._ha_bridge() is bridge  # noqa: SLF001


def test_a_live_media_bridge_reads_a_new_key(install, vault, monkeypatch):
    from puffpai import media_bridge
    (install / "media_server_config.json").write_text(json.dumps(
        {"enabled": True}), encoding="utf-8")
    config_mod.get_config(_force_reload=True)
    monkeypatch.setattr(media_bridge, "_instance", None)
    bridge = media_bridge.get_media_bridge()
    assert 'Token=""' in bridge._session.headers["X-Emby-Authorization"]  # noqa: SLF001
    assert keys.put_house_key("jellyfin", JF_KEY, vault=vault)["ok"]
    assert media_bridge.get_media_bridge() is bridge
    assert f'Token="{JF_KEY}"' in bridge._session.headers["X-Emby-Authorization"]  # noqa: SLF001


@pytest.mark.keyring_stub
@pytest.mark.parametrize("machine_key", ["password_store", "file"])
def test_a_9783a49c_box_gets_its_id_then_moves(machine_key, tmp_path,
                                               monkeypatch):
    """9783a49c sealed with no id file. The first vault open (or a status
    check) writes it, so a later move of the data folder still opens both
    copies."""
    import shutil
    if machine_key == "file":
        machine_key_file(monkeypatch, tmp_path)
    else:
        _fake_ring(monkeypatch)
    a = tmp_path / "a"
    _install_at(a, monkeypatch)
    v = fast_vault(monkeypatch, setup_ops._vault_dir())      # noqa: SLF001
    keys.set_vault_provider(lambda: v)
    setup_ops.set_ha_config("http://192.168.1.50:8123", TOKEN)
    v.lock()
    id_file = a / "state" / "house_keys" / "id"
    os.unlink(id_file)                  # as a 9783a49c box left it
    keys._house_cache.clear()  # noqa: SLF001
    assert keys.house_key("home_assistant") == TOKEN
    v.unlock(PIN)
    assert id_file.exists(), "the first vault open writes the id file"
    v.lock()
    keys.set_vault_provider(None)
    b = tmp_path / "b"
    shutil.copytree(a, b)
    shutil.rmtree(a)
    _install_at(b, monkeypatch)
    keys._house_cache.clear()  # noqa: SLF001
    assert keys.house_key("home_assistant") == TOKEN
    assert setup_ops.ha_configured() and setup_ops.ha_key_problem() == ""


def test_a_status_check_also_writes_a_missing_id(install, vault):
    setup_ops.set_ha_config("http://192.168.1.50:8123", TOKEN)
    id_file = os.path.join(os.path.dirname(keys.house_key_file("home_assistant")), "id")
    os.unlink(id_file)
    assert keys.has_house_key("home_assistant")
    assert os.path.isfile(id_file)
