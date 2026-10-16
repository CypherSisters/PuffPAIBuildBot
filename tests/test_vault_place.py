"""Where your vault lives.

A person may already have a vault, or want vault data stored in a separate
location, such as their own server or hard drive.

* **The folder checks** (security/vault_place.py): a whole path, no device
  paths, a bare drive or a network share's top gets a "PuffPAI Vault" folder
  offered, the folder is there and writable, not inside BuildBot's own folder
  or any git folder, not the vault's own folder or inbox. A network path gets
  its one plain line. No real network share is used: network paths are
  recognised by their shape, and the "is it a network drive" call is replaced.
* **A vault she already has** is found by its files and opens with its own
  PIN; another folder's PIN checks share one throttle per folder.
* **A move** proves the copy opens with the PIN before anything is switched,
  keeps the old copy, and keys set to keep working while the vault is locked
  still work in the new folder.
* **A missing vault drive** still refuses a second vault, whatever the face.
* The routes need a paired device; the server lets go of its old vault
  handle; the terminal commands; the wizard's question before the PIN; the
  Use CLI list.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import types

import pytest

from puffpai import api_server, cli, developer_api, outside_api
from puffpai.config import get_config
from puffpai.security import vault as vault_mod
from puffpai.security import vault_place as vp
from puffpai.security.vault import Vault

PIN = "orbit-lantern-77"
OTHER_PIN = "comet-harbor-58"
API_KEY = api_server._API_KEY


@pytest.fixture(autouse=True)
def fast_and_local(monkeypatch, tmp_path):
    """A cheap key derivation. Git folders above the test's own folder (the
    developer's home can be a git folder) need no hiding any more: only
    BuildBot's own git folder is refused."""
    monkeypatch.setattr(vault_mod, "_PBKDF2_ITERATIONS", 1000)
    monkeypatch.setattr(vault_mod, "_RECOVERY_PBKDF2_ITERATIONS", 1000)
    monkeypatch.setattr(vault_mod, "_SCRYPT_N", 2 ** 10)
    monkeypatch.setattr(vp, "_drive_type", lambda root: None)
    vp._CHECKERS.clear()
    yield
    vp._CHECKERS.clear()


def _install_vault(pin=PIN, items=None):
    v = Vault()
    v.create(pin)
    for name, value in (items or {}).items():
        v.store(name, value)
    v.lock()
    return v


def _refused(code, func, *a, **k):
    with pytest.raises(vp.PlaceRefused) as exc:
        func(*a, **k)
    assert exc.value.code == code, exc.value.message
    assert "\u2014" not in exc.value.message
    return exc.value


# ── the folder checks ───────────────────────────────────────────────────────

@pytest.mark.parametrize("typed", ["", "   ", "vault", "relative\\folder",
                                   "\\\\?\\C:\\vault", "\\\\.\\PhysicalDrive0",
                                   "C:\\x\u0007y"])
def test_only_a_whole_ordinary_folder_path_is_taken(typed):
    _refused("not_folder", vp.check_folder, typed, purpose="new", create=False)


@pytest.mark.skipif(os.name != "nt", reason="drive letters")
def test_a_bare_drive_gets_a_puffpai_vault_folder_offered():
    exc = _refused("bare_drive", vp.check_folder, "E:\\", purpose="new",
                   create=False)
    assert exc.extra["suggestion"] == "E:\\PuffPAI Vault"
    assert "whole drive" in exc.message and "E:\\PuffPAI Vault" in exc.message


@pytest.mark.skipif(os.name != "nt", reason="network shares (\\\\server) are Windows paths")
def test_the_top_of_a_network_share_gets_a_folder_offered_too():
    exc = _refused("bare_drive", vp.check_folder, "\\\\mynas\\vault",
                   purpose="new", create=False)
    assert exc.extra["suggestion"].endswith("PuffPAI Vault")
    assert "network share" in exc.message


def test_saying_yes_to_the_folder_on_a_bare_drive_uses_it(monkeypatch, tmp_path):
    drive = tmp_path / "drive"
    drive.mkdir()
    monkeypatch.setattr(vp, "bare_root",
                        lambda f: str(drive) if vp.same_folder(f, str(drive)) else "")
    out = vp.check_folder(str(drive), purpose="new", make_subfolder=True)
    assert out["folder"] == os.path.join(str(drive), vp.SUBFOLDER)
    assert out["made"] and os.path.isdir(out["folder"])


@pytest.mark.skipif(os.name != "nt", reason="network shares (\\\\server) are Windows paths")
def test_network_paths_are_known_by_their_shape_and_get_their_line(monkeypatch, tmp_path):
    assert vp.is_network("\\\\mynas\\vault\\PuffPAI Vault")
    assert vp.is_network("//mynas/vault/PuffPAI Vault")
    assert not vp.is_network(str(tmp_path))
    if os.name == "nt":
        monkeypatch.setattr(vp, "_drive_type", lambda root: 4)   # a mapped drive
        assert vp.is_network("Z:\\Vault")
    # An unreachable server is said plainly, and nothing is made.
    exc = _refused("unreachable", vp.check_folder,
                   "\\\\no-such-nas-puffpai-test\\share\\Vault", purpose="new",
                   create=False)
    assert "server or NAS" in exc.message
    # A reachable network folder (a local folder standing in for one).
    monkeypatch.setattr(vp, "is_network", lambda folder: True)
    out = vp.check_folder(str(tmp_path / "nas"), purpose="new")
    assert out["network"] is True and out["lines"] == [vp.NETWORK_LINE]
    assert vp.NETWORK_LINE == ("It must be reachable whenever BuildBot starts; "
                               "if it is not, BuildBot will say so and will not "
                               "make a second vault.")


def test_a_missing_folder_is_made_only_when_its_parent_is_there(tmp_path):
    out = vp.check_folder(str(tmp_path / "new"), purpose="new", create=False)
    assert out["will_make"] and not os.path.exists(tmp_path / "new")
    out = vp.check_folder(str(tmp_path / "new"), purpose="new")
    assert out["made"] and os.path.isdir(tmp_path / "new")
    exc = _refused("missing", vp.check_folder, str(tmp_path / "gone" / "new"),
                   purpose="new")
    assert "Plug in the drive" in exc.message
    assert not os.path.exists(tmp_path / "gone")


def test_a_folder_bot_cannot_write_in_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(vp, "_writable", lambda folder: False)
    _refused("not_writable", vp.check_folder, str(tmp_path), purpose="new")


def test_a_file_is_not_a_folder(tmp_path):
    (tmp_path / "f").write_text("x")
    _refused("not_folder", vp.check_folder, str(tmp_path / "f"), purpose="new")


def test_never_inside_buildbots_own_folder():
    inside_install = os.path.join(vp.install_folder(), "my vault")
    exc = _refused("in_install", vp.check_folder, inside_install, purpose="new",
                   create=False)
    assert "Git could copy" in exc.message
    assert not os.path.exists(inside_install)


def test_a_git_folder_above_that_is_not_buildbots_is_fine(tmp_path):
    """The owner's whole user folder can be a git folder (this PC's is):
    Documents and Desktop in it must still be usable."""
    home = tmp_path / "home"
    (home / ".git").mkdir(parents=True)
    (home / "Documents").mkdir()
    out = vp.check_folder(str(home / "Documents" / "PuffPAI Vault"),
                          purpose="new", create=False)
    assert out["ok"] and out["will_make"]


def test_never_inside_buildbots_own_git_folder(monkeypatch, tmp_path):
    repo = tmp_path / "buildbot"
    (repo / ".git").mkdir(parents=True)
    (repo / "src" / "puffpai").mkdir(parents=True)
    (repo / "notes").mkdir()
    monkeypatch.setattr(vp, "install_folder", lambda: str(repo / "src"))
    exc = _refused("in_git", vp.check_folder, str(repo / "notes"),
                   purpose="new")
    assert str(repo) in exc.message and "BuildBot's own git folder" in exc.message


def test_never_inside_the_main_checkout_of_a_buildbot_worktree(monkeypatch, tmp_path):
    main = tmp_path / "main"
    (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
    (main / "src" / "puffpai").mkdir(parents=True)
    (main / "notes").mkdir()
    wt = tmp_path / "wt"
    (wt / "src" / "puffpai").mkdir(parents=True)
    (wt / ".git").write_text(
        f"gitdir: {main / '.git' / 'worktrees' / 'wt'}\n", encoding="utf-8")
    monkeypatch.setattr(vp, "install_folder", lambda: str(wt))
    assert vp.same_folder(vp.own_repos()[1], str(main))
    _refused("in_git", vp.check_folder, str(main / "notes"), purpose="new")


def test_the_default_folder_is_always_fine():
    out = vp.check_folder(vp.default_folder(), purpose="new", create=False)
    assert out["default"] is True


def test_never_the_vaults_own_inbox_or_inside_its_folder():
    v = _install_vault()
    _refused("inbox", vp.check_folder, v.vault_dir + ".inbox", purpose="new",
             create=False)
    _refused("inside_vault", vp.check_folder, os.path.join(v.vault_dir, "sub"),
             purpose="new", create=False)
    _refused("same", vp.check_folder, v.vault_dir, purpose="move", create=False)


def test_a_vault_already_there_is_found_by_its_files(tmp_path):
    have = tmp_path / "have"
    Vault(vault_dir=str(have)).create(PIN)
    _refused("has_vault", vp.check_folder, str(have), purpose="new")
    assert vp.check_folder(str(have), purpose="existing")["folder"] == str(have)
    (tmp_path / "plain").mkdir()
    exc = _refused("no_vault", vp.check_folder, str(tmp_path / "plain"),
                   purpose="existing")
    assert "json" not in exc.message
    _refused("missing", vp.check_folder, str(tmp_path / "nope"),
             purpose="existing")


def test_a_move_only_goes_into_an_empty_folder(tmp_path):
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "photo.jpg").write_text("x")
    _refused("not_empty", vp.check_folder, str(tmp_path / "full"), purpose="move")


# ── a new vault somewhere she chooses ───────────────────────────────────────

def test_a_new_vault_is_made_where_she_chose(tmp_path):
    out = vp.set_new_place(str(tmp_path / "ext"))
    assert out["folder"] == str(tmp_path / "ext")
    assert vp.same_folder(vp.configured_folder(), str(tmp_path / "ext"))
    v = Vault()                               # the install's vault, now there
    assert vp.same_folder(v.vault_dir, str(tmp_path / "ext"))
    v.create(PIN)
    assert vault_mod.read_place() == v.vault_dir
    with pytest.raises(vp.PlaceRefused) as exc:
        vp.set_new_place(str(tmp_path / "other"))
    assert exc.value.code == "vault_exists"
    assert "puffpai vault move" in exc.value.message


def test_on_this_computer_puts_the_setting_back_as_it_always_was(tmp_path):
    vp.set_new_place(str(tmp_path / "ext"))
    vp.set_new_place(None)
    assert get_config().paths.vault_data_dir == "vault_data"
    assert vp.same_folder(vp.configured_folder(), vp.default_folder())


# ── a vault she already has ─────────────────────────────────────────────────

def test_an_existing_vault_is_used_with_its_own_pin(tmp_path):
    have = tmp_path / "have"
    v = Vault(vault_dir=str(have))
    v.create(PIN)
    v.store("passport", "X123")
    v.lock()
    exc = _refused("wrong_pin", vp.use_existing, str(have), "not-the-pin-99")
    assert "Nothing was changed" in exc.message
    assert vp.same_folder(vp.configured_folder(), vp.default_folder())
    out = vp.use_existing(str(have), PIN)
    assert out["changed"] is True
    assert vp.same_folder(vp.configured_folder(), str(have))
    assert not os.path.exists(vp.default_folder())     # no second vault made
    mine = Vault()
    mine.unlock(PIN)
    assert mine.retrieve("passport") == "X123"
    assert vault_mod.read_place() == mine.vault_dir


def test_switching_away_from_a_vault_in_use_needs_its_pin_too(tmp_path):
    current = _install_vault()
    have = tmp_path / "have"
    Vault(vault_dir=str(have)).create(OTHER_PIN)
    _refused("current_pin_needed", vp.use_existing, str(have), OTHER_PIN,
             vault=current)
    _refused("wrong_pin", vp.use_existing, str(have), OTHER_PIN,
             current_pin="wrong-wrong-11", vault=current)
    out = vp.use_existing(str(have), OTHER_PIN, current_pin=PIN, vault=current)
    assert out["old_folder"] == current.vault_dir
    assert os.path.isfile(os.path.join(current.vault_dir, "vault.meta.json"))


def test_guessing_another_folders_pin_hits_the_same_throttle(tmp_path):
    have = tmp_path / "have"
    Vault(vault_dir=str(have)).create(PIN)
    codes = []
    for _ in range(8):
        with pytest.raises(vp.PlaceRefused) as exc:
            vp.use_existing(str(have), "guess-guess-00")
        codes.append(exc.value.code)
    assert codes[:5] == ["wrong_pin"] * 5
    assert "throttled" in codes[5:]
    # Even the right PIN waits out the cooldown: no fresh handle gets around it.
    _refused("throttled", vp.use_existing, str(have), PIN)


def test_check_pin_never_opens_the_vault_and_counts_as_an_unlock_try(tmp_path):
    v = Vault(vault_dir=str(tmp_path / "v"))
    v.create(PIN)
    v.lock()
    assert v.check_pin(PIN) is True and not v.is_unlocked
    for _ in range(6):
        assert v.check_pin("nope-nope-12") is False
    with pytest.raises(vault_mod.VaultThrottled):
        v.unlock(PIN)


# ── a move ──────────────────────────────────────────────────────────────────

def test_a_move_copies_proves_switches_and_keeps_the_old_copy(tmp_path):
    v = _install_vault(items={"a": "1"})
    old = v.vault_dir
    inbox = vp.inbox_of(old)
    os.makedirs(inbox, exist_ok=True)
    with open(os.path.join(inbox, "waiting.sealed"), "wb") as fh:
        fh.write(b"sealed bytes")
    switched = []
    out = vp.move_vault(v, str(tmp_path / "ext"), PIN,
                        on_switched=lambda: switched.append(vp.configured_folder()))
    new = str(tmp_path / "ext")
    assert out["folder"] == new and out["old_folder"] == old
    assert switched and vp.same_folder(switched[0], new)
    assert vp.same_folder(vp.configured_folder(), new)
    assert vault_mod.read_place() == os.path.abspath(new)
    # The old copy is kept, untouched, and the owner is told where it is.
    assert os.path.isfile(os.path.join(old, "vault.meta.json"))
    assert os.path.isfile(os.path.join(inbox, "waiting.sealed"))
    assert any(old in line and "Delete it yourself" in line for line in out["lines"])
    moved = Vault()
    assert vp.same_folder(moved.vault_dir, new)
    moved.unlock(PIN)
    assert moved.retrieve("a") == "1"
    assert all("\u2014" not in line for line in out["lines"])


def test_a_wrong_pin_moves_nothing(tmp_path):
    v = _install_vault()
    _refused("wrong_pin", vp.move_vault, v, str(tmp_path / "ext"), "nope-nope-13")
    assert not os.path.exists(tmp_path / "ext")
    assert vp.same_folder(vp.configured_folder(), vp.default_folder())


def test_a_copy_that_does_not_open_is_never_switched_to(monkeypatch, tmp_path):
    v = _install_vault(items={"a": "1"})
    real_unlock = Vault.unlock

    def copy_will_not_open(self, password):
        if vp.same_folder(self.vault_dir, str(tmp_path / "ext")):
            raise vault_mod.VaultError("the vault contents did not authenticate")
        return real_unlock(self, password)

    monkeypatch.setattr(Vault, "unlock", copy_will_not_open)
    exc = _refused("copy_failed", vp.move_vault, v, str(tmp_path / "ext"), PIN)
    assert f"Your vault is still in {v.vault_dir}" in exc.message
    assert vp.same_folder(vp.configured_folder(), vp.default_folder())
    assert not os.path.exists(tmp_path / "ext")          # the copy was cleared
    assert os.path.isfile(os.path.join(v.vault_dir, "vault.meta.json"))


def test_a_copy_that_does_not_match_is_never_switched_to(monkeypatch, tmp_path):
    v = _install_vault()
    monkeypatch.setattr(vp, "_same_copies", lambda *a: False)
    _refused("copy_failed", vp.move_vault, v, str(tmp_path / "ext"), PIN)
    assert vp.same_folder(vp.configured_folder(), vp.default_folder())


def test_a_copy_that_stops_part_way_leaves_nothing_behind(monkeypatch, tmp_path):
    v = _install_vault(items={"a": "1"})
    real_copy = shutil.copy2
    calls = []

    def drive_pulled_out(source, target):
        calls.append(target)
        if len(calls) == 2:
            with open(target, "wb") as fh:      # half a file, then the error
                fh.write(b"half")
            raise OSError("the drive went away")
        return real_copy(source, target)

    monkeypatch.setattr(shutil, "copy2", drive_pulled_out)
    exc = _refused("copy_failed", vp.move_vault, v, str(tmp_path / "ext"), PIN)
    assert "OSError" in exc.message and v.vault_dir in exc.message
    assert not os.path.exists(tmp_path / "ext")
    assert vp.same_folder(vp.configured_folder(), vp.default_folder())
    monkeypatch.setattr(shutil, "copy2", real_copy)
    back = Vault()
    back.unlock(PIN)
    assert back.retrieve("a") == "1"


def test_an_open_vault_is_locked_while_it_moves(tmp_path):
    v = _install_vault()
    v.unlock(PIN)
    out = vp.move_vault(v, str(tmp_path / "ext"), PIN)
    assert not v.is_unlocked
    assert any("locked while it moved" in line for line in out["lines"])


def _fake_keyring(monkeypatch):
    class Store:
        priority = 5

    items: dict = {}
    mod = types.ModuleType("keyring")
    mod.get_keyring = lambda: Store()
    mod.get_password = lambda s, u: items.get((s, u))
    mod.set_password = lambda s, u, v: items.__setitem__((s, u), v)
    mod.delete_password = lambda s, u: items.pop((s, u), None)
    monkeypatch.setitem(sys.modules, "keyring", mod)
    return items


@pytest.mark.keyring_stub
def test_keys_that_keep_working_when_locked_still_work_after_a_move(monkeypatch, tmp_path):
    from keys_support import canary, install_vendor, reset_keys, set_posture
    from puffpai.security import egress_doors, keys
    for d in egress_doors.DOORS:
        monkeypatch.delenv(d.env_var, raising=False)
    monkeypatch.delenv("PUFFPAI_VAULT_SERVICE_KEY_FILE", raising=False)
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    reset_keys()
    set_posture("enumerated")
    store = _fake_keyring(monkeypatch)
    v = Vault()
    v.create_with_recovery(PIN)
    vendor = install_vendor(monkeypatch)
    value = canary()
    vendor.accept("API-Key", value)
    keys.add_key("marginalia_search", value, vault=v, background=True)
    v.lock()
    old_user = vault_mod._keyring_user(v.vault_dir)
    assert ("puffpai-buildbot", old_user) in store

    out = vp.move_vault(v, str(tmp_path / "ext"), PIN)
    assert out["keys_kept"] is True
    assert any("still work" in line for line in out["lines"])
    new_user = vault_mod._keyring_user(str(tmp_path / "ext"))
    assert ("puffpai-buildbot", new_user) in store
    assert ("puffpai-buildbot", old_user) not in store   # the old copy's is gone
    moved = Vault()                                       # locked, in its new place
    got = keys.keyed_get("key:marginalia_search",
                         url="https://api2.marginalia-search.com/search?query=a",
                         caller="t", vault=moved)
    assert got.status == 200
    reset_keys()


# ── a missing vault drive still never gets a second vault ───────────────────

def test_a_missing_vault_drive_refuses_a_new_place_and_a_new_vault(tmp_path):
    v = _install_vault()
    shutil.rmtree(v.vault_dir)                     # the drive is unplugged
    exc = _refused("vault_missing", vp.set_new_place, str(tmp_path / "ext"))
    assert exc.message.startswith("Your vault drive is not plugged in.")
    _refused("vault_missing", vp.set_new_place, None)
    assert not os.path.exists(tmp_path / "ext")


def test_the_drive_back_under_another_letter_is_used_not_remade(tmp_path):
    v = _install_vault(items={"a": "1"})
    shutil.move(v.vault_dir, str(tmp_path / "F_drive_vault"))
    assert Vault().missing_reason()
    vp.use_existing(str(tmp_path / "F_drive_vault"), PIN)
    back = Vault()
    assert back.missing_reason() == ""
    back.unlock(PIN)
    assert back.retrieve("a") == "1"


def test_a_vault_on_a_network_folder_that_is_gone_is_not_remade(monkeypatch, tmp_path):
    monkeypatch.setattr(vp, "is_network", lambda folder: True)
    vp.set_new_place(str(tmp_path / "nas"))
    Vault().create(PIN)
    shutil.rmtree(tmp_path / "nas")               # the NAS is off
    with pytest.raises(vault_mod.VaultMissing):
        Vault().create(PIN)
    assert not os.path.exists(tmp_path / "nas")


# ── the routes ──────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api_server, "_vault_instance", None)
    monkeypatch.setattr(api_server, "_vault_made_for", None)
    api_server.app.config.update(TESTING=True)
    yield api_server.app.test_client()
    api_server._vault_sessions.clear()


def _auth():
    return {"Authorization": f"Bearer {API_KEY}"}


@pytest.fixture
def paired(monkeypatch):
    monkeypatch.setattr(outside_api, "paired_device_id", lambda: "device:test1234")


def test_changes_need_a_paired_device(client, tmp_path):
    for method, path, body in (
            ("post", "/vault/location", {"choice": "folder", "folder": str(tmp_path)}),
            ("post", "/vault/move", {"folder": str(tmp_path / "x"), "pin": PIN}),
            ("post", "/vault/location/check", {"folder": str(tmp_path)}),
            ("get", "/vault/location/browse", None)):
        got = getattr(client, method)(path, json=body, headers=_auth())
        assert got.status_code == 403, path
        assert got.get_json()["code"] == "not_paired"
    seen = client.get("/vault/location", headers=_auth())
    assert seen.status_code == 200
    data = seen.get_json()
    assert data["paired"] is False and data["question"] == vp.QUESTION
    assert [c["label"] for c in data["choices"]] == [
        "On this computer (recommended)", "On a drive or folder I choose",
        "I already have a BuildBot vault"]


def test_a_pin_in_the_address_is_refused(client, paired, tmp_path):
    got = client.post(f"/vault/move?pin={PIN}", json={"folder": str(tmp_path)},
                      headers=_auth())
    assert got.status_code == 400
    assert PIN not in got.get_data(as_text=True)


def test_the_browser_wizard_sets_the_place_then_the_vault_is_made_there(client, paired, tmp_path):
    got = client.post("/vault/location", json={"choice": "folder",
                                               "folder": str(tmp_path / "ext")},
                      headers=_auth())
    assert got.status_code == 200, got.get_json()
    made = client.post("/setup/vault", json={"password": PIN}, headers=_auth())
    assert made.status_code == 200 and made.get_json()["success"] is True
    assert os.path.isfile(tmp_path / "ext" / "vault.meta.json")
    assert not os.path.exists(vp.default_folder())


def test_the_route_offers_the_folder_on_a_bare_drive(client, paired, monkeypatch, tmp_path):
    monkeypatch.setattr(vp, "bare_root",
                        lambda f: str(tmp_path) if vp.same_folder(f, str(tmp_path)) else "")
    got = client.post("/vault/location", json={"choice": "folder",
                                               "folder": str(tmp_path)},
                      headers=_auth())
    assert got.status_code == 409
    assert got.get_json()["suggestion"] == os.path.join(str(tmp_path), vp.SUBFOLDER)
    yes = client.post("/vault/location", json={"choice": "folder",
                                               "folder": str(tmp_path),
                                               "make_subfolder": True},
                      headers=_auth())
    assert yes.status_code == 200
    assert vp.same_folder(vp.configured_folder(),
                          os.path.join(str(tmp_path), vp.SUBFOLDER))


def test_a_move_through_the_route_reloads_the_servers_vault(client, paired, tmp_path):
    _install_vault(items={"a": "1"})
    first = api_server._get_vault()
    wrong = client.post("/vault/move", json={"folder": str(tmp_path / "ext"),
                                             "pin": "wrong-wrong-22"},
                        headers=_auth())
    assert wrong.status_code == 403
    assert "wrong-wrong-22" not in wrong.get_data(as_text=True)
    got = client.post("/vault/move", json={"folder": str(tmp_path / "ext"),
                                           "pin": PIN}, headers=_auth())
    assert got.status_code == 200, got.get_json()
    body = got.get_data(as_text=True)
    assert PIN not in body
    now = api_server._get_vault()
    assert now is not first
    assert vp.same_folder(now.vault_dir, str(tmp_path / "ext"))
    status = client.get("/developer/vault", headers=_auth()).get_json()
    assert vp.same_folder(status["folder"], str(tmp_path / "ext"))


def test_use_an_existing_vault_through_the_route(client, paired, tmp_path):
    have = tmp_path / "have"
    Vault(vault_dir=str(have)).create(OTHER_PIN)
    got = client.post("/vault/location", json={"choice": "existing",
                                               "folder": str(have),
                                               "pin": OTHER_PIN},
                      headers=_auth())
    assert got.status_code == 200, got.get_json()
    assert vp.same_folder(api_server._get_vault().vault_dir, str(have))


def test_the_chooser_lists_folders_never_files(client, paired, tmp_path):
    (tmp_path / "Photos").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "secret.txt").write_text("never listed")
    Vault(vault_dir=str(tmp_path / "Vaulty")).create(PIN)
    got = client.get("/vault/location/browse", query_string={"folder": str(tmp_path)},
                     headers=_auth()).get_json()
    names = [row["name"] for row in got["folders"]]
    assert names == ["Photos", "Vaulty"]
    assert [row["vault"] for row in got["folders"]] == [False, True]
    assert "secret.txt" not in str(got) and "never listed" not in str(got)
    drives = client.get("/vault/location/browse", headers=_auth()).get_json()
    assert drives["folder"] == "" and drives["drives"]


def test_the_server_follows_a_new_folder_saved_by_the_terminal(client, tmp_path):
    first = api_server._get_vault()
    assert vp.same_folder(first.vault_dir, vp.default_folder())
    # Another process (puffpai vault location) writes the setting.
    from puffpai import setup_ops
    setup_ops.write_main_config({"paths": {"vault_data_dir": str(tmp_path / "ext")}})
    path = api_server._vault_made_for[0]
    os.utime(path, ns=(1, 1))                  # a different change time
    now = api_server._get_vault()
    assert now is not first
    assert vp.same_folder(now.vault_dir, str(tmp_path / "ext"))


def test_a_handle_put_in_place_by_hand_is_left_alone(client, monkeypatch, tmp_path):
    mine = Vault(vault_dir=str(tmp_path / "mine"))
    monkeypatch.setattr(api_server, "_vault_instance", mine)
    from puffpai import setup_ops
    setup_ops.write_main_config({"paths": {"vault_data_dir": str(tmp_path / "ext")}})
    assert api_server._get_vault() is mine


# ── the terminal ────────────────────────────────────────────────────────────

def test_puffpai_vault_location_shows_where_it_lives(capsys):
    assert cli.main(["vault", "location"]) == 0
    out = capsys.readouterr().out
    assert f"Vault folder : {vp.default_folder()}" in out
    assert "on this computer" in out and "puffpai vault move <folder>" in out


def test_puffpai_vault_location_sets_a_new_place(capsys, tmp_path):
    assert cli.main(["vault", "location", str(tmp_path / "ext")]) == 0
    out = capsys.readouterr().out
    assert "Your vault will be made in" in out and "puffpai setup" in out
    assert vp.same_folder(vp.configured_folder(), str(tmp_path / "ext"))


def test_puffpai_vault_location_uses_an_existing_vault(capsys, monkeypatch, tmp_path):
    current = _install_vault()
    have = tmp_path / "have"
    Vault(vault_dir=str(have)).create(OTHER_PIN)
    asked = []

    def read_pin(prompt):
        asked.append(prompt)
        return OTHER_PIN if len(asked) == 1 else PIN

    monkeypatch.setattr(cli, "_read_pin", read_pin)
    assert cli.main(["vault", "location", str(have)]) == 0
    out = capsys.readouterr().out
    assert "BuildBot now uses the vault in" in out
    assert len(asked) == 2 and current.vault_dir in asked[1]
    assert OTHER_PIN not in out and PIN not in out
    assert vp.same_folder(vp.configured_folder(), str(have))


def test_puffpai_vault_move(capsys, monkeypatch, tmp_path):
    _install_vault(items={"a": "1"})
    monkeypatch.setattr(cli, "_bot_running", lambda: True)
    assert cli.main(["vault", "move", str(tmp_path / "ext")]) == 1
    assert "Stop it first" in capsys.readouterr().out
    assert not os.path.exists(tmp_path / "ext")
    monkeypatch.setattr(cli, "_bot_running", lambda: False)
    monkeypatch.setattr(cli, "_read_pin", lambda prompt: PIN)
    assert cli.main(["vault", "move", str(tmp_path / "ext")]) == 0
    out = capsys.readouterr().out
    assert "Your vault now lives in" in out and "Delete it yourself" in out
    assert PIN not in out
    assert vp.same_folder(vp.configured_folder(), str(tmp_path / "ext"))


def test_the_terminal_location_command_refuses_a_second_vault(capsys, tmp_path):
    v = _install_vault()
    shutil.rmtree(v.vault_dir)
    assert cli.main(["vault", "location", str(tmp_path / "ext")]) == 1
    assert "Your vault drive is not plugged in." in capsys.readouterr().out
    assert not os.path.exists(tmp_path / "ext")


def test_puffpai_where_shows_the_vault_folder(capsys, tmp_path):
    vp.set_new_place(str(tmp_path / "ext"))
    cli.main(["where"])
    out = capsys.readouterr().out
    assert f"vault:       {tmp_path / 'ext'}" in out


def test_the_use_cli_panel_lists_the_new_commands():
    panel = developer_api.cli_page("vault", folder=str(get_config().REPO_ROOT))
    lines = [c["windows"] for c in panel["commands"]]
    assert any(line.endswith("vault location") for line in lines)
    assert any(line.endswith("vault location <folder>") for line in lines)
    assert any(line.endswith("vault move <folder>") for line in lines)
    assert not any("Moving the vault" in line for line in panel["no_command_yet"])
    parser = cli.build_parser()
    for row in developer_api.COMMANDS["vault"]:
        parser.parse_args(row["argv"])


# ── the wizard's question, before the PIN ───────────────────────────────────

def _secure():
    from puffpai.wizard import loader
    packages, _refusals = loader.load_builtin_packages()
    return loader.load_module(packages["secure"]), packages["secure"]


class _Sink:
    def __init__(self):
        self.shown = []

    def show(self, lines):
        self.shown.extend(lines)

    def ask(self, prompt):
        return ""


def _ctx(package, **args):
    from puffpai.wizard import runner as wizard_runner
    base = dict(yes=True, skip_vault=False, vault_password=None,
                vault_folder=None)
    base.update(args)
    return wizard_runner.RunContext(
        args=argparse.Namespace(**base), cfg=get_config(), non_interactive=True,
        shared={}, sections={}, package=package, part="vault",
        show_once=_Sink())


def test_the_terminal_card_asks_where_before_the_pin():
    module, package = _secure()
    ctx = _ctx(package)
    card = module.propose(ctx)["card"]
    ids = [f["id"] for f in card["fields"]]
    assert ids.index("where") < ids.index("folder") < ids.index("pin")
    where = next(f for f in card["fields"] if f["id"] == "where")
    assert [o["label"] for o in where["options"]] == [c[1] for c in vp.CHOICES]
    assert where["current"] == "here"


def test_the_terminal_wizard_makes_the_vault_in_the_chosen_folder(tmp_path):
    module, package = _secure()
    ctx = _ctx(package)
    ctx.proposal = module.propose(ctx)
    out = module.apply(ctx, {"where": "folder", "folder": str(tmp_path / "ext"),
                             "pin": PIN, "pin_again": PIN})
    block = out["data"]["vault"]
    assert block["ready"] is True and block["rejected"] is False
    assert os.path.isfile(tmp_path / "ext" / "vault.meta.json")
    assert not os.path.exists(vp.default_folder())
    assert module._vault_state(ctx) == (str(tmp_path / "ext"), True)


def test_the_terminal_wizard_uses_a_vault_she_already_has(tmp_path):
    have = tmp_path / "have"
    Vault(vault_dir=str(have)).create("an-older-pin-1")   # made by older rules
    module, package = _secure()
    ctx = _ctx(package)
    ctx.proposal = module.propose(ctx)
    out = module.apply(ctx, {"where": "existing", "folder": str(have),
                             "pin": "an-older-pin-1", "pin_again": "an-older-pin-1"})
    assert out["data"]["vault"]["configured"] is True
    assert vp.same_folder(vp.configured_folder(), str(have))
    assert not os.path.exists(vp.default_folder())
    wrong = module.apply(_ctx(package, vault_folder=str(tmp_path / "x")),
                         {"where": "existing", "folder": str(have), "pin": "nope-nope-14"})
    assert wrong["data"]["vault"]["ready"] is True    # it is still the vault in use


def test_the_terminal_wizard_with_a_folder_flag_finds_the_vault_there(tmp_path):
    have = tmp_path / "have"
    Vault(vault_dir=str(have)).create(PIN)
    module, package = _secure()
    ctx = _ctx(package, vault_folder=str(have), vault_password=PIN)
    ctx.proposal = module.propose(ctx)
    out = module.apply(ctx, {})
    assert out["data"]["vault"]["configured"] is True
    assert vp.same_folder(vp.configured_folder(), str(have))


def test_the_terminal_wizard_says_why_a_folder_will_not_do(tmp_path):
    module, package = _secure()
    ctx = _ctx(package)
    ctx.proposal = module.propose(ctx)
    inside_install = os.path.join(vp.install_folder(), "vault-here")
    out = module.apply(ctx, {"where": "folder", "folder": inside_install,
                             "pin": PIN, "pin_again": PIN})
    assert out["data"]["vault"]["rejected"] is True
    assert "Git could copy" in out["gap"]["why"]
    assert not os.path.exists(inside_install)
    assert not os.path.exists(vp.default_folder())


def test_the_browser_wizard_flow_carries_the_question():
    from puffpai.wizard import service
    words = service._vault_where_words()
    assert words["question"] == "Where should your vault live?"
    assert words["encrypted_drive_line"] == ("Unlock that drive yourself first, "
                                             "then BuildBot keeps its vault "
                                             "inside it.")
    assert [c["value"] for c in words["choices"]] == ["here", "folder", "existing"]


# ── vault drive missing: only the SAME vault from the browser ───────────────

def _unplug(v):
    """The remembered vault's drive is gone; its files are kept aside."""
    aside = os.path.join(os.path.dirname(v.vault_dir) + "_aside", "vault")
    shutil.move(v.vault_dir, aside)
    assert Vault().missing_reason().startswith(vault_mod.VAULT_DRIVE_MISSING)
    return aside


def _other_vault(tmp_path, name="other", pin=OTHER_PIN):
    folder = tmp_path / name
    Vault(vault_dir=str(folder)).create(pin)
    return str(folder)


def test_a_missing_drive_never_lets_the_browser_switch_to_another_vault(tmp_path):
    v = _install_vault()
    _unplug(v)
    other = _other_vault(tmp_path)
    before = vp.configured_folder()
    exc = _refused("vault_missing", vp.use_existing, other, OTHER_PIN)
    assert exc.message == vp.MISSING_SWITCH_LINE
    assert "puffpai vault location <folder>" in exc.message
    assert vp.same_folder(vp.configured_folder(), before)
    assert vault_mod.read_place() == v.vault_dir         # still waits for hers
    exc = _refused("vault_missing", vp.set_new_place, str(tmp_path / "new"))
    assert exc.message == vp.MISSING_SWITCH_LINE
    assert not os.path.exists(tmp_path / "new")


def test_the_same_vault_under_another_letter_is_allowed_with_its_pin(tmp_path):
    v = _install_vault(items={"a": "1"})
    aside = _unplug(v)
    _refused("wrong_pin", vp.use_existing, aside, "not-the-pin-99")
    out = vp.use_existing(aside, PIN)
    assert out["changed"] is True
    assert any("same vault BuildBot used before" in line for line in out["lines"])
    back = Vault()
    assert back.missing_reason() == ""
    back.unlock(PIN)
    assert back.retrieve("a") == "1"


def test_the_fingerprint_is_tied_to_the_key_not_the_meta_file(tmp_path):
    v = _install_vault()
    fp = vault_mod.remembered_fingerprint()
    assert len(fp) == 64
    with open(os.path.join(v.vault_dir, "vault.meta.json"), encoding="utf-8") as fh:
        assert fp not in fh.read()
    # A vault made with another PIN never gives her fingerprint, and a locked
    # vault gives none at all.
    other = Vault(vault_dir=_other_vault(tmp_path))
    other.unlock(OTHER_PIN)
    assert other.place_fingerprint() and other.place_fingerprint() != fp
    other.lock()
    assert other.place_fingerprint() == ""


def test_with_no_fingerprint_remembered_the_browser_fails_closed(tmp_path):
    import json
    v = _install_vault()
    path = vault_mod.place_record_path()
    with open(path, encoding="utf-8") as fh:
        record = json.load(fh)
    record.pop("fingerprint")                    # a record from before today
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh)
    aside = _unplug(v)
    _refused("vault_missing", vp.use_existing, aside, PIN)


def test_the_routes_refuse_another_vault_while_the_drive_is_missing(client, paired, tmp_path):
    v = _install_vault()
    aside = _unplug(v)
    other = _other_vault(tmp_path)
    for body in ({"choice": "existing", "folder": other, "pin": OTHER_PIN},
                 {"choice": "existing", "folder": other, "pin": OTHER_PIN,
                  "confirmed_at_terminal": True},
                 {"choice": "folder", "folder": str(tmp_path / "new")},
                 {"choice": "here"}):
        got = client.post("/vault/location", json=body, headers=_auth())
        assert got.status_code == 409, body
        assert got.get_json()["message"] == vp.MISSING_SWITCH_LINE
        assert OTHER_PIN not in got.get_data(as_text=True)
    assert vault_mod.read_place() == v.vault_dir
    seen = client.get("/vault/location", headers=_auth()).get_data(as_text=True)
    assert vault_mod.remembered_fingerprint() not in seen
    same = client.post("/vault/location", headers=_auth(),
                       json={"choice": "existing", "folder": aside, "pin": PIN})
    assert same.status_code == 200, same.get_json()
    assert vp.same_folder(vp.configured_folder(), aside)


def test_the_terminal_changes_vaults_only_with_someone_at_the_computer(capsys, monkeypatch, tmp_path):
    v = _install_vault()
    _unplug(v)
    other = _other_vault(tmp_path)
    monkeypatch.setattr(cli, "_read_pin", lambda prompt: OTHER_PIN)
    # No terminal (a script or a pipe): refused, nothing changed.
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: False)
    assert cli.main(["vault", "location", other]) == 1
    out = capsys.readouterr().out
    assert "Your vault drive is not plugged in." in out
    assert "needs someone at this computer" in out
    assert not vp.same_folder(vp.configured_folder(), other)
    # At a terminal, "no": nothing changed.
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(cli, "_ask_yes_no", lambda q, default=False, **k: False)
    assert cli.main(["vault", "location", other]) == 1
    assert "Nothing was changed." in capsys.readouterr().out
    assert not vp.same_folder(vp.configured_folder(), other)
    # At a terminal, "yes": the other vault, with its PIN.
    asked = []
    monkeypatch.setattr(cli, "_ask_yes_no",
                        lambda q, default=False, **k: asked.append(q) or True)
    assert cli.main(["vault", "location", other]) == 0
    out = capsys.readouterr().out
    assert asked and other in asked[0] and "not deleted" in asked[0]
    assert "BuildBot now uses the vault in" in out
    assert "no longer waits for the vault in" in out and OTHER_PIN not in out
    assert vp.same_folder(vp.configured_folder(), other)
    assert vault_mod.read_place() == os.path.abspath(other)


def test_the_terminal_can_start_a_new_vault_after_a_yes(capsys, monkeypatch, tmp_path):
    v = _install_vault()
    aside = _unplug(v)
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(cli, "_ask_yes_no", lambda q, default=False, **k: True)
    new = str(tmp_path / "fresh")
    assert cli.main(["vault", "location", new]) == 0
    out = capsys.readouterr().out
    assert "Your vault will be made in" in out and "Nothing in it was deleted" in out
    assert vault_mod.read_place() is None
    assert os.path.isfile(os.path.join(aside, "vault.meta.json"))   # untouched
    fresh = Vault()
    assert vp.same_folder(fresh.vault_dir, new) and fresh.missing_reason() == ""
    fresh.create(OTHER_PIN)


# ── the request log never keeps a PIN from the address ─────────────────────

def _werkzeug_record(target, code="400"):
    import logging
    return logging.LogRecord(
        "werkzeug", logging.INFO, __file__, 1,
        '127.0.0.1 - - [04/Oct/2026 10:00:00] "%s" %s %s',
        (f"POST {target} HTTP/1.1", code, "-"), None)


@pytest.mark.parametrize("target", [
    f"/vault/move?pin={PIN}",
    f"/vault/location?current_pin={PIN}",
    f"/vault/location/browse?folder=C%3A%5CUsers&pin={PIN}",
    f"/api/backend/vault/unlock?x={PIN}",
    f"/pairing/confirm?pairing_code={PIN}",
    f"/spotify/callback?code={PIN}&state=abc",
    f"/anything?access_token={PIN}",
])
def test_the_request_log_never_writes_a_secret_query(target):
    import logging
    from puffpai.security import pasted_key_guard as guard
    assert any(isinstance(f, guard.KeySafeRequestLog)
               for f in logging.getLogger("werkzeug").filters)
    record = _werkzeug_record(target)
    assert guard.KeySafeRequestLog().filter(record) is True
    line = record.getMessage()
    assert PIN not in line and "pin=" not in line
    assert guard.QUERY_NOT_SHOWN in line or line == guard.HIDDEN_REQUEST_LINE


def test_an_ordinary_query_is_still_logged():
    from puffpai.security import pasted_key_guard as guard
    record = _werkzeug_record("/knowledge/search?q=cats", "200")
    guard.KeySafeRequestLog().filter(record)
    assert "/knowledge/search?q=cats" in record.getMessage()


@pytest.mark.parametrize("name", ["pin", "current_pin", "recovery_code",
                                  "token", "Vault_PIN"])
def test_the_routes_refuse_a_secret_in_the_address(client, paired, name):
    got = client.post(f"/vault/location?{name}={PIN}",
                      json={"choice": "here"}, headers=_auth())
    assert got.status_code == 400
    assert PIN not in got.get_data(as_text=True)


def test_a_strong_pin_on_the_vault_place_routes_is_not_taken_for_a_pasted_key():
    """A vault PIN is a secret on purpose: the key-paste guard must not refuse it."""
    from puffpai.security import pasted_key_guard as g
    for endpoint, field in (
        ("vault_place.vault_location_set", "pin"),
        ("vault_place.vault_location_set", "current_pin"),
        ("vault_place.vault_move", "pin"),
    ):
        assert (endpoint, field) in g.SECRET_FIELDS
