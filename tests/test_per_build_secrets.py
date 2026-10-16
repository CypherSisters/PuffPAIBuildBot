"""Every build on one computer has its own API key and its own pairing code.

Six bots built in six state folders printed one pairing
code and one API key, so a device paired to one bot was paired to all of them.
The pairing state now lives in the build's state folder, and the API key's
password-store entry is named after the build.

Migration is part of the contract and is tested here too: an existing
single-build install keeps its paired devices and its key.

Every test uses throwaway folders and a dict-backed stand-in for the password
store. Nothing touches the real password store or this checkout's
dashboard/data folder.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from puffpai import pairing_code, start_cli
from puffpai.security import api_key_store, build_identity, secrets_provider

#: These tests are about keyring behaviour: each gets an in-memory stand-in
#: (tests/conftest.py) and may put its own in place. Never the real store.
pytestmark = pytest.mark.keyring_stub

REPO = Path(__file__).resolve().parents[1]
DASHBOARD = REPO / "dashboard"
PAIRING_TS = DASHBOARD / "app" / "_lib" / "server" / "pairing.ts"
SHOW_CODE_JS = DASHBOARD / "scripts" / "show-pairing-code.js"
NODE = shutil.which("node")

needs_node = pytest.mark.skipif(
    NODE is None or not PAIRING_TS.is_file(),
    reason="needs Node and the dashboard tree for the cross-runtime checks")


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A code copy with a dashboard folder and no pairing file yet.

    Only this fake copy's dashboard folder is ever looked at: the package's
    own dashboard (this checkout's) is hidden from pairing_code.
    """
    root = tmp_path / "copy"
    (root / "dashboard" / "data").mkdir(parents=True)
    monkeypatch.delenv("PUFF_PAIRING_STATE", raising=False)
    monkeypatch.delenv("PUFF_PAIRED_DEVICES", raising=False)
    monkeypatch.setattr(pairing_code, "_dashboard_dirs",
                        lambda start=None: [str(root / "dashboard")])
    return root


@pytest.fixture
def store(monkeypatch):
    """A dict standing in for the OS password store."""
    entries: dict[tuple[str, str], str] = {}
    ring = type("Ring", (), {})()
    ring.get_password = lambda service, name: entries.get((service, name))

    def _set(service, name, value):
        entries[(service, name)] = value
    ring.set_password = _set
    monkeypatch.setattr(secrets_provider, "keyring", ring, raising=False)
    monkeypatch.setattr(secrets_provider, "_HAS_KEYRING", True)
    monkeypatch.setattr(secrets_provider, "_default_provider", None)
    return entries


def _state(tmp_path, name):
    path = tmp_path / name
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _legacy_state(checkout, *, devices=("KeepMe123456",)):
    """The old shared file, as the pre-change dashboard left it."""
    legacy = checkout / "dashboard" / "data" / "pairing.json"
    info = pairing_code.ensure_code(str(legacy))
    assert info is not None
    (legacy.parent / "devices.json").write_text(json.dumps({
        "version": 1,
        "devices": [{"id": d, "name": "old phone", "createdAt": "2026-09-01T00:00:00Z",
                     "lastSeenAt": None} for d in devices]}), encoding="utf-8")
    return legacy, json.loads(legacy.read_text(encoding="utf-8"))


# ── the build's name ────────────────────────────────────────────────────────

def test_a_build_is_named_by_its_state_folder(tmp_path):
    a, b = _state(tmp_path, "botA"), _state(tmp_path, "botB")
    assert build_identity.build_id(a) == build_identity.build_id(a + os.sep)
    assert build_identity.build_id(a) != build_identity.build_id(b)
    assert len(build_identity.build_id(a)) == 16
    if sys.platform == "win32":  # the same folder, spelt differently
        assert build_identity.build_id(a.upper()) == build_identity.build_id(a)


# ── pairing: each build its own ─────────────────────────────────────────────

def test_two_builds_from_one_copy_get_different_pairing_state(checkout, tmp_path):
    a, b = _state(tmp_path, "botA"), _state(tmp_path, "botB")
    pa = pairing_code.state_path(state=a)
    pb = pairing_code.state_path(state=b)
    assert pa == os.path.join(a, "pairing", "pairing.json")
    assert pb == os.path.join(b, "pairing", "pairing.json")
    ia, ib = pairing_code.ensure_code(pa), pairing_code.ensure_code(pb)
    assert ia["code"] != ib["code"]
    sa = json.loads(Path(pa).read_text(encoding="utf-8"))["secret"]
    sb = json.loads(Path(pb).read_text(encoding="utf-8"))["secret"]
    assert sa != sb
    # Nothing was written into the code copy's shared folder.
    assert os.listdir(checkout / "dashboard" / "data") == []


def test_an_existing_install_keeps_its_pairing(checkout, tmp_path):
    """The first build to ask owns the old file and keeps using it in place."""
    legacy, before = _legacy_state(checkout)
    a = _state(tmp_path, "the-install")
    assert pairing_code.state_path(state=a) == str(legacy)
    info = pairing_code.ensure_code(pairing_code.state_path(state=a))
    after = json.loads(legacy.read_text(encoding="utf-8"))
    assert after["secret"] == before["secret"]          # devices stay paired
    assert info["code"] == pairing_code.format_code(before["code"])
    owner = json.loads((legacy.parent / "pairing-owner.json").read_text("utf-8"))
    assert owner["build"] == build_identity.build_id(a)
    assert "secret" not in owner and "code" not in owner
    # Asked again (every start): still the old file, nothing copied.
    assert pairing_code.state_path(state=a) == str(legacy)
    assert not os.path.exists(os.path.join(a, "pairing"))


def test_a_second_build_does_not_get_the_old_shared_pairing(checkout, tmp_path):
    legacy, before = _legacy_state(checkout)
    a, b = _state(tmp_path, "first"), _state(tmp_path, "second")
    assert pairing_code.state_path(state=a) == str(legacy)
    own_b = pairing_code.state_path(state=b)
    assert own_b == os.path.join(b, "pairing", "pairing.json")
    pairing_code.ensure_code(own_b)
    assert json.loads(Path(own_b).read_text("utf-8"))["secret"] != before["secret"]


def test_a_build_with_its_own_state_never_goes_back_to_the_old_file(checkout, tmp_path):
    legacy, _ = _legacy_state(checkout)
    a = _state(tmp_path, "rekeyed")
    own = os.path.join(a, "pairing", "pairing.json")
    pairing_code.ensure_code(own)          # the build was given its own state
    assert pairing_code.state_path(state=a) == own
    assert not (legacy.parent / "pairing-owner.json").exists()


def test_a_damaged_owner_record_gives_everybody_their_own(checkout, tmp_path):
    legacy, _ = _legacy_state(checkout)
    (legacy.parent / "pairing-owner.json").write_text("{not json", "utf-8")
    a = _state(tmp_path, "botA")
    assert pairing_code.state_path(state=a) == os.path.join(a, "pairing", "pairing.json")


def test_the_explicit_override_still_wins(checkout, tmp_path, monkeypatch):
    monkeypatch.setenv("PUFF_PAIRING_STATE", str(tmp_path / "x.json"))
    assert pairing_code.state_path(state=_state(tmp_path, "a")) == str(tmp_path / "x.json")


def test_start_tells_the_web_page_which_state_folder_is_this_builds(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "relative-ok"))
    env = start_cli.child_env(8264, 3264, "k" * 43)
    assert os.path.isabs(env["PUFFPAI_STATE_DIR"])
    assert os.path.normcase(env["PUFFPAI_STATE_DIR"]) == os.path.normcase(
        str((tmp_path / "relative-ok").resolve()))


def test_installer_start_scripts_no_longer_name_the_shared_file():
    for name in ("install.ps1", "install.sh"):
        text = (REPO / name).read_text(encoding="utf-8")
        assert "ensure_code(sys.argv[1])" not in text, name
        assert "data\\pairing.json" not in text and "data/pairing.json" not in text, name
        assert "p.ensure_code()" in text, name


# ── pairing: the web page agrees, and refuses the other build's device ──────

def _node(script: str, *, cwd: Path, state: str, extra_env=None, args=()):
    env = {k: v for k, v in os.environ.items()
           if k not in ("PUFF_PAIRING_STATE", "PUFF_PAIRED_DEVICES",
                        "PUFFPAI_DATA_DIR")}
    env["PUFFPAI_STATE_DIR"] = state
    env.update(extra_env or {})
    runner = cwd.parent / f"runner_{abs(hash(script)) % 10**8}.mjs"
    runner.write_text(script, encoding="utf-8")
    out = subprocess.run([NODE, str(runner), *args], cwd=str(cwd), env=env,
                         capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        pytest.fail(f"node failed: {out.stderr[-600:]}")
    return out.stdout.strip()


_TS_RUN = (
    "import { pathToFileURL } from 'node:url';\n"
    "const P = await import(pathToFileURL(process.argv[2]).href);\n"
    "const [act, arg] = [process.argv[3], process.argv[4]];\n"
    "if (act === 'path') process.stdout.write(P.pairingStatePath());\n"
    "if (act === 'id') process.stdout.write(String(P.recordedBuildId(P.buildStateDir())));\n"
    "if (act === 'code') process.stdout.write(P.getPairingCodeInfo().code);\n"
    "if (act === 'pair') { const d = P.addPairedDevice('phone');"
    " process.stdout.write(P.issueDeviceCookieValueV2(d.id)); }\n"
    "if (act === 'verify') process.stdout.write(P.verifyDeviceCookieValue(arg) ? 'YES' : 'NO');\n"
)


def _ts(act, *, cwd, state, arg=""):
    return _node(_TS_RUN, cwd=cwd, state=state,
                 args=(str(PAIRING_TS), act, arg))


@needs_node
def test_python_node_and_the_code_script_agree_on_every_path(checkout, tmp_path):
    dash = checkout / "dashboard"
    legacy, _ = _legacy_state(checkout)
    a, b = _state(tmp_path, "one"), _state(tmp_path, "two")
    # The web page never works the id out: it reads what Python recorded.
    assert _ts("id", cwd=dash, state=a) == "null"
    for state in (a, b):
        build_identity.record(state)
    assert _ts("id", cwd=dash, state=a) == build_identity.build_id(a)
    # The web page for build A claims the old file first ...
    assert Path(_ts("path", cwd=dash, state=a)) == legacy
    # ... and Python and the code script then agree about both builds.
    assert pairing_code.state_path(state=a) == str(legacy)
    assert Path(_ts("path", cwd=dash, state=b)) == Path(b, "pairing", "pairing.json")
    assert pairing_code.state_path(state=b) == os.path.join(b, "pairing", "pairing.json")
    env ={k: v for k, v in os.environ.items()
           if k not in ("PUFF_PAIRING_STATE", "PUFF_PAIRED_DEVICES", "PUFFPAI_DATA_DIR")}
    for state, want in ((a, legacy), (b, Path(b, "pairing", "pairing.json"))):
        env["PUFFPAI_STATE_DIR"] = state
        out = subprocess.run([NODE, str(SHOW_CODE_JS), "--path"], cwd=str(dash),
                             env=env, capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr
        assert Path(out.stdout.strip()) == want


@needs_node
def test_a_device_paired_to_one_build_is_refused_by_the_other(checkout, tmp_path):
    dash = checkout / "dashboard"
    a, b = _state(tmp_path, "botA"), _state(tmp_path, "botB")
    cookie = _ts("pair", cwd=dash, state=a)
    assert cookie.startswith("v2.")
    assert _ts("verify", cwd=dash, state=a, arg=cookie) == "YES"
    assert _ts("verify", cwd=dash, state=b, arg=cookie) == "NO"
    # And the codes the two web pages show are different.
    assert _ts("code", cwd=dash, state=a) != _ts("code", cwd=dash, state=b)
    # What the wizard prints is what that build's web page accepts.
    info = pairing_code.ensure_code(pairing_code.state_path(state=a))
    assert pairing_code.format_code(_ts("code", cwd=dash, state=a)) == info["code"]


@needs_node
def test_an_existing_installs_paired_device_still_works(checkout, tmp_path):
    """Old layout: a device paired through the shared file, before the change."""
    dash = checkout / "dashboard"
    legacy = dash / "data" / "pairing.json"
    old = _node(_TS_RUN, cwd=dash, state=_state(tmp_path, "unused"),
                extra_env={"PUFF_PAIRING_STATE": str(legacy)},
                args=(str(PAIRING_TS), "pair", ""))
    install = _state(tmp_path, "install-state")
    build_identity.record(install)          # the bot has run once
    assert _ts("verify", cwd=dash, state=install, arg=old) == "YES"
    assert pairing_code.state_path(state=install) == str(legacy)
    # A bot built later from the same copy does not accept that device.
    later = _state(tmp_path, "new-bot")
    build_identity.record(later)
    assert _ts("verify", cwd=dash, state=later, arg=old) == "NO"


# ── the API key: each build its own ─────────────────────────────────────────

def _key(monkeypatch, state):
    from puffpai import api_server
    monkeypatch.setenv("PUFFPAI_STATE_DIR", state)
    monkeypatch.delenv("PUFFPAI_API_KEY", raising=False)
    monkeypatch.setattr(api_server, "_LEGACY_API_KEY_PATH",
                        os.path.join(state, "absent", "api_key.txt"))
    return api_server._load_or_create_api_key()


def test_two_builds_mint_two_keys_under_their_own_names(store, tmp_path, monkeypatch):
    a, b = _state(tmp_path, "botA"), _state(tmp_path, "botB")
    ka, kb = _key(monkeypatch, a), _key(monkeypatch, b)
    assert ka != kb
    assert _key(monkeypatch, a) == ka and _key(monkeypatch, b) == kb  # stable
    names = {name for (_svc, name) in store}
    assert names == {api_key_store.entry_name(a), api_key_store.entry_name(b)}
    assert api_key_store.LEGACY_ENTRY not in names   # never written again
    assert build_identity.build_id(a) in api_key_store.entry_name(a)


def test_a_build_never_reads_the_old_shared_entry(store, tmp_path, monkeypatch):
    store[("puffpai", "PUFFPAI_API_KEY")] = "ab" * 32
    key = _key(monkeypatch, _state(tmp_path, "some-bot"))
    assert key != "ab" * 32


def test_the_default_build_keeps_the_key_it_was_using(store, tmp_path, monkeypatch):
    """The single-build layout: no state folder named, the old shared entry."""
    from puffpai import config
    root = tmp_path / "LocalAppData" / "PuffPAI"
    root.mkdir(parents=True)
    monkeypatch.setattr(config, "_user_state_root", lambda: str(root))
    store[("puffpai", "PUFFPAI_API_KEY")] = "cd" * 32
    assert _key(monkeypatch, str(root)) == "cd" * 32
    # Nothing new was written: it keeps using what the dashboard already has.
    assert list(store) == [("puffpai", "PUFFPAI_API_KEY")]


def test_apikey_new_gives_the_default_build_its_own_key(store, tmp_path, monkeypatch, capsys):
    from puffpai import cli, config
    root = tmp_path / "LocalAppData" / "PuffPAI"
    root.mkdir(parents=True)
    monkeypatch.setattr(config, "_user_state_root", lambda: str(root))
    store[("puffpai", "PUFFPAI_API_KEY")] = "cd" * 32
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(root))
    monkeypatch.delenv("PUFFPAI_API_KEY", raising=False)
    assert cli._cmd_apikey(argparse.Namespace(new=True)) == 0
    out = capsys.readouterr()
    new = out.out.strip()
    assert len(new) == 64 and new != "cd" * 32
    assert "Other bots on this computer are not changed" in out.err
    assert new not in out.err
    assert _key(monkeypatch, str(root)) == new
    # The shared entry is left for whatever else still uses it.
    assert store[("puffpai", "PUFFPAI_API_KEY")] == "cd" * 32


def test_apikey_new_refuses_when_the_settings_file_sets_the_key(monkeypatch, capsys):
    from puffpai import cli
    monkeypatch.setenv("PUFFPAI_API_KEY", "ef" * 32)
    assert cli._cmd_apikey(argparse.Namespace(new=True)) == 1
    assert "settings file" in capsys.readouterr().err


def test_where_reports_the_builds_own_store_without_the_key(store, tmp_path, monkeypatch):
    from puffpai import cli
    a = _state(tmp_path, "botA")
    key = _key(monkeypatch, a)
    where = cli._api_key_source()
    assert "own entry" in where and key not in where
    assert cli._api_key_value() == key


def test_without_a_password_store_the_key_file_is_per_build(tmp_path, monkeypatch):
    monkeypatch.setattr(secrets_provider, "_HAS_KEYRING", False)
    monkeypatch.setattr(secrets_provider, "_default_provider", None)
    a, b = _state(tmp_path, "botA"), _state(tmp_path, "botB")
    ka, kb = _key(monkeypatch, a), _key(monkeypatch, b)
    assert ka != kb
    assert Path(a, "api_key.txt").read_text("utf-8").strip() == ka
    assert Path(b, "api_key.txt").read_text("utf-8").strip() == kb
