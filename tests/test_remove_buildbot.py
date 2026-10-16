"""Remove BuildBot: stops the bot, says what goes, asks once, deletes, and can
never delete more than BuildBot (invariant 15).

Everything runs against a scratch install tree (``PUFFPAI_L7_REMOVE_ROOT``
when set, else pytest's tmp folder) with a fake locked box (the fake wsl.exe
and Lxss registry from test_box_status_states), faked process lists and kills,
and the conftest's stand-in keyring. The PowerShell half (the generated
remove-buildbot.ps1) is run for real, on Windows, against its own scratch tree.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from puffpai import config, remove_cli
from puffpai.box import driver
from puffpai.box import wording as W
from puffpai.security import api_key_store, owner_folders, vault

from test_box_status_states import FakeWsl

#: The WSL box is Windows only: off Windows box.driver answers before it
#: ever reaches WSL (Linux uses bubblewrap), so these have nothing to test.
wsl_box_only = pytest.mark.skipif(os.name != "nt", reason="the WSL box is Windows only")

ROOT = Path(__file__).resolve().parents[1]
needs_windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def _junction(target, link) -> None:
    import _winapi
    _winapi.CreateJunction(str(target), str(link))


def _base(tmp_path, request) -> Path:
    env = os.environ.get("PUFFPAI_L7_REMOVE_ROOT", "").strip()
    if not env:
        return tmp_path
    base = Path(env) / f"{request.node.name[:40]}-{secrets.token_hex(3)}"
    base.mkdir(parents=True)
    return base


def _write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def inst(tmp_path, monkeypatch, request):
    base = _base(tmp_path, request)
    profile = base / "profile"
    folders = {}
    for name in owner_folders.READ_ROOT_NAMES:
        (profile / name).mkdir(parents=True)
        folders[name] = [str(profile / name)]
    _write(profile / "Pictures" / "photo.jpg", "her photo")
    monkeypatch.setattr(owner_folders, "known_folders", lambda: folders)
    monkeypatch.setenv("USERPROFILE", str(profile))

    install = base / "BuildBot"
    _write(install / ".puffpai-install.json", "{}")
    _write(install / "src" / "puffpai" / "__init__.py", "")
    _write(install / "venv" / "Scripts" / "python.exe", "not really python")
    packed = _write(install / ".git" / "objects" / "pack" / "pack-1.pack", "git keeps these read-only")
    os.chmod(packed, stat.S_IREAD)
    _write(install / "vault_data" / "secret.txt", "vault canary")
    state = base / "state"
    _write(state / "config" / "settings.json", "{}")
    _write(state / "logs" / "bot.log", "log")
    _write(state / "buildbot.env", f"PUFFPAI_CHECKOUT={install}\nPUFFPAI_API_KEY=not-a-real-key\n")
    apps = base / "apps"
    _write(apps / "seating" / "index.html", "<html>")
    box = base / "box"
    box.mkdir()

    monkeypatch.setenv("PUFFPAI_CHECKOUT", str(install))
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(state))
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(install))
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", str(apps))
    # This scratch apps folder stands in for the usual one (%LOCALAPPDATA%\PuffPAI\apps).
    monkeypatch.setattr(owner_folders, "default_apps_root", lambda: str(apps))
    monkeypatch.setenv("PUFFPAI_BOX_ROOT", str(box))
    monkeypatch.delenv("PUFFPAI_BOX_DISTRO", raising=False)
    config.get_config(_force_reload=True)

    monkeypatch.setattr(driver, "_platform", lambda: "windows")
    fake = FakeWsl(monkeypatch)
    home = box / "PuffPAI-sandbox"
    _write(home / "ext4.vhdx", "d" * 2048)
    fake.add("PuffPAI-sandbox", str(home))
    driver.add_claim("PuffPAI-sandbox")

    killed: list[int] = []
    monkeypatch.setattr(remove_cli, "list_processes", lambda: [])
    monkeypatch.setattr(remove_cli, "kill", killed.append)
    monkeypatch.setattr(remove_cli, "loaded_files", lambda pid: [])
    monkeypatch.setattr(remove_cli, "STOP_WAIT_SECONDS", 0.0)
    return {"base": base, "profile": profile, "install": install, "state": state,
            "apps": apps, "box": box, "fake": fake, "killed": killed,
            "vault": install / "vault_data"}


def _args(**kw) -> argparse.Namespace:
    ns = argparse.Namespace(yes=False, keep_vault=False, facts=False, only="")
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _answers(monkeypatch, *replies: str) -> None:
    it = iter(replies)
    monkeypatch.setattr("builtins.input", lambda *a: next(it))


def _unregistered(fake) -> list[str]:
    return [c[2] for c in fake.calls if len(c) == 3 and c[1] == "--unregister"]


def _our_keys(state, vault_dir) -> list:
    return [("puffpai-buildbot", vault._keyring_user(str(vault_dir))),  # noqa: SLF001
            ("puffpai", api_key_store.entry_name(str(state)))]


# ── the plan ─────────────────────────────────────────────────────────────────

@pytest.mark.keyring_stub
@wsl_box_only
def test_the_plan_lists_folder_state_apps_box_and_saved_entries(inst, keyring_entries):
    for key in _our_keys(inst["state"], inst["vault"]):
        keyring_entries[key] = "s" * 64
    keyring_entries[("puffpai", "PUFFPAI_API_KEY.someone-else.0123456789ab")] = "o" * 64
    plan = remove_cli.build_plan()
    first, rest = remove_cli.plan_lines(plan)
    size = r"\((?:\d+ MB|\d+\.\d GB|less than 1 MB)\)"
    want = [
        r"Remove BuildBot", r"",
        r"This stops your bot and its web page, then deletes:",
        r"  the BuildBot folder: " + re.escape(str(inst["install"])) + " " + size,
        r"  your bot's settings, books and chats: " + re.escape(str(inst["state"])) + " " + size,
        r"  the apps BuildBot added: " + re.escape(str(inst["apps"])) + " " + size,
        r"  the locked box BuildBot set up: PuffPAI-sandbox " + size,
        r"  2 saved entries BuildBot kept in Windows Credential Manager",
        re.escape(W.REMOVE_VAULT), re.escape(W.REMOVE_VAULT_ASK),
        re.escape(W.REMOVE_OWN_FILES), r"",
        re.escape("Remove all of this? Type Y and press Enter. Anything else keeps it."),
    ]
    got = first + rest
    assert len(got) == len(want), got
    for pattern, line in zip(want, got):
        assert re.fullmatch(pattern, line), (pattern, line)
    assert "\u2014" not in "\n".join(got)


def test_lines_that_do_not_apply_are_left_out(inst, monkeypatch):
    other = inst["base"] / "another-install"
    other.mkdir()
    folder = inst["box"] / "claims" / "PuffPAI-sandbox"
    (folder / ("9" * 16 + ".json")).write_text(json.dumps(
        {"install_root": str(other), "state_dir": "", "created": ""}), encoding="utf-8")
    plan = remove_cli.build_plan()
    text = "\n".join(sum(remove_cli.plan_lines(plan), []))
    assert "the locked box BuildBot set up" not in text      # not the last claim
    assert "saved entr" not in text                           # none present
    assert "the apps BuildBot added:" not in text             # another BuildBot may use them
    assert W.REMOVE_APPS_STAY.format(path=inst["apps"]) in text


# ── asking ───────────────────────────────────────────────────────────────────

@pytest.mark.keyring_stub
def test_n_deletes_nothing(inst, monkeypatch, capsys, keyring_entries):
    for key in _our_keys(inst["state"], inst["vault"]):
        keyring_entries[key] = "s" * 64
    _answers(monkeypatch, "", "n")
    assert remove_cli.cmd_remove(_args()) == 1
    out = capsys.readouterr().out
    assert W.REMOVE_KEPT in out and W.REMOVE_FINISHED not in out
    for key in ("install", "state", "apps"):
        assert inst[key].exists()
    assert (inst["vault"] / "secret.txt").exists()
    assert inst["killed"] == []
    assert _unregistered(inst["fake"]) == []
    assert all(k in keyring_entries for k in _our_keys(inst["state"], inst["vault"]))
    assert driver._has_my_claim("PuffPAI-sandbox")


@pytest.mark.keyring_stub
@wsl_box_only
def test_k_copies_the_vault_first_then_everything_goes(inst, monkeypatch, capsys,
                                                       keyring_entries):
    ours = _our_keys(inst["state"], inst["vault"])
    for key in ours:
        keyring_entries[key] = "s" * 64
    stranger = ("puffpai", "PUFFPAI_API_KEY.someone-else.0123456789ab")
    keyring_entries[stranger] = "o" * 64
    _answers(monkeypatch, "K", "Y")
    assert remove_cli.cmd_remove(_args()) == 0
    out = capsys.readouterr().out
    copies = list((inst["profile"] / "Documents").glob("BuildBot vault copy *"))
    assert len(copies) == 1
    assert (copies[0] / "vault_data" / "secret.txt").read_text(encoding="utf-8") == "vault canary"
    assert f"Kept a copy of the vault in {copies[0]}." in out
    assert "Stopping your bot ... done" in out
    assert "Deleting ... done" in out
    assert out.rstrip().endswith(W.REMOVE_FINISHED)
    for key in ("install", "state", "apps"):
        assert not inst[key].exists(), key
    assert _unregistered(inst["fake"]) == ["PuffPAI-sandbox"]
    assert not any(k in keyring_entries for k in ours)
    assert keyring_entries[stranger] == "o" * 64          # not this install's
    assert (inst["profile"] / "Pictures" / "photo.jpg").exists()


def test_yes_skips_the_questions(inst, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("asked a question"))
    assert remove_cli.cmd_remove(_args(yes=True)) == 0
    assert not inst["install"].exists()
    assert "Remove all of this?" not in capsys.readouterr().out


# ── links, keeps and roots ───────────────────────────────────────────────────

@needs_windows
def test_a_junction_inside_state_to_a_scratch_vault_survives(inst, monkeypatch):
    outside = inst["base"] / "scratch_vault"
    _write(outside / "secret.txt", "must survive")
    _junction(outside, inst["state"] / "shortcut_to_vault")
    _junction(inst["profile"] / "Pictures", inst["install"] / "my_pictures")
    assert remove_cli.cmd_remove(_args(yes=True)) == 0
    assert (outside / "secret.txt").read_text(encoding="utf-8") == "must survive"
    assert (inst["profile"] / "Pictures" / "photo.jpg").read_text(encoding="utf-8") == "her photo"
    assert not inst["state"].exists() and not inst["install"].exists()


def test_the_box_root_inside_state_is_kept(inst, monkeypatch):
    """The default layout: the state folder holds the box root. Deleting the
    state folder must leave the box to the guard."""
    other = inst["base"] / "another-install"
    other.mkdir()
    box = inst["state"] / "box"
    monkeypatch.setenv("PUFFPAI_BOX_ROOT", str(box))
    home = box / "PuffPAI-sandbox"
    _write(home / "ext4.vhdx", "d")
    inst["fake"].registry[0]["base_path"] = str(home)
    driver.add_claim("PuffPAI-sandbox")
    theirs = box / "claims" / "PuffPAI-sandbox" / ("8" * 16 + ".json")
    theirs.write_text(json.dumps({"install_root": str(other)}), encoding="utf-8")
    assert remove_cli.cmd_remove(_args(yes=True)) == 0
    assert (home / "ext4.vhdx").exists()                  # another BuildBot's box
    assert theirs.exists()
    assert sorted(os.listdir(inst["state"])) == ["box"]
    assert _unregistered(inst["fake"]) == []


@needs_windows
def test_a_root_that_is_a_link_is_refused(inst, monkeypatch, capsys):
    real = inst["base"] / "real_state"
    _write(real / "keep.txt")
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(inst["base"] / "linked_state"))
    _junction(real, inst["base"] / "linked_state")
    config.get_config(_force_reload=True)
    assert remove_cli.cmd_remove(_args(yes=True)) == 2
    out = capsys.readouterr().out
    assert W.REMOVE_REFUSED.format(path=str(inst["base"] / "linked_state"),
                                   why=W.REMOVE_WHY_LINK) in out
    assert (real / "keep.txt").exists() and inst["install"].exists()


@pytest.mark.parametrize("which", ["profile", "documents", "profile_parent"])
def test_main_folders_are_refused_as_roots(inst, monkeypatch, capsys, which):
    target = {"profile": inst["profile"], "documents": inst["profile"] / "Documents",
              "profile_parent": inst["profile"].parent}[which]
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(target))
    config.get_config(_force_reload=True)
    assert remove_cli.cmd_remove(_args(yes=True)) == 2
    assert W.REMOVE_WHY_MAIN_FOLDER in capsys.readouterr().out
    assert (inst["profile"] / "Pictures" / "photo.jpg").exists()
    assert inst["install"].exists()


def test_a_folder_that_is_not_buildbot_is_refused(inst, monkeypatch, capsys):
    stranger = inst["base"] / "Some folder"
    _write(stranger / "taxes.xlsx")
    monkeypatch.setenv("PUFFPAI_CHECKOUT", str(stranger))
    assert remove_cli.cmd_remove(_args(yes=True)) == 2
    assert W.REMOVE_WHY_NOT_BUILDBOT in capsys.readouterr().out
    assert (stranger / "taxes.xlsx").exists()


def test_an_apps_setting_pointed_at_her_own_folder_is_left_alone(inst, monkeypatch, capsys):
    """A mistaken apps setting is never taken as permission: nothing in that
    folder goes unless its name is an id this install issued (and
    the rest of the removal does not stop for it)."""
    holiday = inst["profile"] / "Pictures" / "holiday"
    _write(holiday / "beach.jpg", "her photo")
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", str(holiday))
    plan = remove_cli.build_plan()
    assert not plan.apps_delete and not plan.refused
    assert "apps" not in [k for k, _p, _n in plan.roots]
    assert remove_cli.cmd_remove(_args(yes=True)) == 0
    assert (holiday / "beach.jpg").read_text(encoding="utf-8") == "her photo"


def test_an_apps_folder_inside_the_state_folder_is_buildbots(inst, monkeypatch):
    inner = inst["state"] / "apps"
    _write(inner / "seating" / "index.html")
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", str(inner))
    plan = remove_cli.build_plan()
    assert not plan.refused and ("apps", str(inner)) in [(k, p) for k, p, _ in plan.roots]


def test_a_drive_root_is_refused():
    drive = os.path.splitdrive(os.getcwd())[0] + os.sep if os.name == "nt" else "/"
    assert remove_cli.root_problem(drive) in (W.REMOVE_WHY_DRIVE, W.REMOVE_WHY_MAIN_FOLDER)


def test_delete_tree_refuses_outside_its_allowed_roots(tmp_path):
    victim = _write(tmp_path / "a" / "file.txt")
    with pytest.raises(ValueError):
        remove_cli.delete_tree(str(tmp_path / "a"), allowed_roots=[str(tmp_path / "b")])
    assert victim.exists()


# ── a held file ──────────────────────────────────────────────────────────────

@needs_windows
@pytest.mark.keyring_stub
def test_a_held_file_means_nothing_is_deleted(inst, capsys, keyring_entries):
    """Nothing may be deleted while Windows still holds a
    file, or the bot is left without its settings and vault while its web
    page still runs. It is checked (and retried) before anything goes."""
    for key in _our_keys(inst["state"], inst["vault"]):
        keyring_entries[key] = "secret"
    plan = remove_cli.build_plan()
    held = inst["state"] / "logs" / "bot.log"
    now = [0.0]
    sleeps: list[float] = []

    def sleep(s):
        sleeps.append(s)
        now[0] += s

    with open(held, "a", encoding="utf-8"):        # Windows will not delete an open file
        rc = remove_cli.do_remove(plan, keep_vault=False, sleep=sleep, clock=lambda: now[0])
    out = capsys.readouterr().out
    assert rc == 1
    assert sum(1 for s in sleeps if s == 1.0) >= 30
    assert W.REMOVE_IN_USE_WINDOWS.format(path=plan.state_dir) in out
    assert W.REMOVE_FINISHED not in out and W.REMOVE_DELETING not in out
    assert held.exists() and (inst["vault"] / "secret.txt").exists()
    assert (inst["install"] / "src" / "puffpai" / "__init__.py").exists()
    assert _unregistered(inst["fake"]) == []            # the box was not touched
    assert all(k in keyring_entries for k in _our_keys(inst["state"], inst["vault"]))
    # Once the file is let go, the same removal finishes.
    assert remove_cli.do_remove(remove_cli.build_plan(), keep_vault=False,
                                sleep=sleep, clock=lambda: now[0]) == 0
    assert not inst["install"].exists() and not held.exists()


@needs_windows
def test_a_lock_part_way_keeps_the_settings_and_vault_and_says_what_is_left(
        inst, capsys, monkeypatch):
    """If something grabs a file after the check, the program files go first,
    so the settings and the vault are still there, the removal files and
    signs are still there, and the owner is told what is left and how to
    finish."""
    _write(inst["install"] / "Remove BuildBot.cmd", "@echo off")
    held = _write(inst["install"] / "dashboard" / "node_modules" / "swc" / "swc.node", "x")
    plan = remove_cli.build_plan()
    monkeypatch.setattr(remove_cli, "busy_path", lambda plan, **k: "")   # the race
    now = [0.0]

    def sleep(s):
        now[0] += s

    with open(held, "a", encoding="utf-8"):
        rc = remove_cli.do_remove(plan, keep_vault=False, sleep=sleep, clock=lambda: now[0])
    out = capsys.readouterr().out
    assert rc == 1
    # The folder handed to the handle-based delete when the lock stopped it.
    assert W.REMOVE_PART_WAY_WINDOWS.format(path=inst["install"] / "dashboard") in out
    assert W.REMOVE_STILL_HERE in out
    assert W.REMOVE_LEFT_FOLDER.format(path=plan.install_root) in out
    assert W.REMOVE_LEFT_STATE.format(path=plan.state_dir) in out
    assert W.REMOVE_LEFT_VAULT.format(path=plan.vault_dir) in out
    assert W.REMOVE_RUN_AGAIN_WINDOWS.format(path=plan.install_root) in out
    assert W.REMOVE_FINISHED not in out
    # Coherent and re-runnable: data intact, the signs and the removal file kept.
    assert (inst["vault"] / "secret.txt").exists()
    assert (inst["state"] / "config" / "settings.json").exists()
    assert (inst["install"] / "Remove BuildBot.cmd").exists()
    assert remove_cli.root_problem(str(inst["install"]), kind="folder") == ""
    assert remove_cli.root_problem(str(inst["state"]), kind="state") == ""
    assert remove_cli.do_remove(remove_cli.build_plan(), keep_vault=False,
                                sleep=sleep, clock=lambda: now[0]) == 0
    assert not inst["install"].exists() and not inst["state"].exists()


def test_the_delete_order_puts_the_vault_after_the_program_files(inst):
    plan = remove_cli.build_plan()
    phases = remove_cli._phases(plan)
    # The saved entries (the vault's own among them) only after the vault.
    assert [k for k, _p, _s in phases] == ["folder", "apps", "state", "vault", "keyring",
                                           "state", "folder"]
    first_skip = [os.path.normcase(p) for p in phases[0][2]]
    for kept in (inst["vault"], inst["install"] / "Remove BuildBot.cmd",
                 inst["install"] / "venv", inst["install"] / ".puffpai-install.json"):
        assert os.path.normcase(str(kept)) in first_skip
    state_skip = [os.path.normcase(p) for p in phases[2][2]]
    assert os.path.normcase(str(inst["state"] / "buildbot.env")) in state_skip


def test_a_busy_check_that_cannot_put_a_name_back_deletes_nothing(inst, capsys, monkeypatch):
    plan = remove_cli.build_plan()

    def stuck(path, **k):
        raise remove_cli.NameNotBack(path, path + ".puffpai-check-0000")
    monkeypatch.setattr(remove_cli, "_rename_free", stuck)
    monkeypatch.setattr(remove_cli.os, "name", "nt")
    rc = remove_cli.do_remove(plan, keep_vault=False, sleep=lambda s: None)
    out = capsys.readouterr().out
    assert rc == 1 and W.REMOVE_FINISHED not in out
    assert "could not put back the name of" in out
    assert (inst["vault"] / "secret.txt").exists()


@needs_windows
def test_the_busy_check_finds_an_open_file_and_a_program_working_inside(tmp_path):
    root = tmp_path / "r"
    f = _write(root / "a" / "f.txt")
    plan = remove_cli.Plan(install_root=str(root), state_dir="", data_dir="", vault_dir="",
                           apps_root="", apps_delete=False, roots=[("folder", str(root), 1)])
    assert remove_cli.busy_path(plan) == ""
    with open(f, "a", encoding="utf-8"):
        assert remove_cli.busy_path(plan) == str(root)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                             cwd=str(root / "a"), stdin=subprocess.DEVNULL)
    try:
        time.sleep(1.0)
        assert remove_cli.busy_path(plan) == str(root)
    finally:
        child.kill()
        child.wait()
    assert remove_cli.busy_path(plan) == "" and f.exists()


# ── what is stopped ─────────────────────────────────────────────────────────

def test_a_program_running_a_file_from_the_folder_is_stopped(inst):
    """The web page started by run-dashboard.ps1: node.exe lives outside the
    BuildBot folder and nothing wrote its number down, but it runs the page's
    own native part from inside the folder. The operating system's list of
    loaded files finds it; an unrelated program is left alone."""
    install, state = inst["install"], inst["state"]
    swc = str(install / "dashboard" / "node_modules" / "@next" / "swc" / "next-swc.node")
    loaded = {
        300: [r"C:\Program Files\nodejs\node.exe", r"C:\Windows\System32\ntdll.dll", swc],
        301: [r"C:\Program Files\nodejs\node.exe", r"C:\Windows\System32\ntdll.dll"],
        302: [r"C:\Windows\System32\notepad.exe"],
        500: [str(install / "venv" / "Lib" / "x.pyd")],       # this removal itself
    }
    procs = [{"pid": 300, "ppid": 1, "exe": r"C:\Program Files\nodejs\node.exe", "created": 1.0},
             {"pid": 301, "ppid": 1, "exe": r"C:\Program Files\nodejs\node.exe", "created": 1.0},
             {"pid": 302, "ppid": 1, "exe": r"C:\Windows\System32\notepad.exe", "created": 1.0},
             {"pid": 500, "ppid": 1, "exe": r"C:\Python\python.exe", "created": 1.0}]
    got = remove_cli.select_processes(procs, str(install), str(state), me=500,
                                      roots=[str(install), str(state)],
                                      loaded=lambda pid: loaded.get(pid, []))
    assert got == [300]
    # Without the loaded-file list (the old rule) it was missed.
    assert remove_cli.select_processes(procs, str(install), str(state), me=500) == []


def test_the_launchers_own_records_count_and_a_reused_number_does_not(inst):
    state = inst["state"]
    page = state / "running.page.json"
    page.write_text(json.dumps({"pid": 610}), encoding="utf-8")
    bot = state / "running.bot.json"
    bot.write_text(json.dumps({"pid": 620}), encoding="utf-8")
    now = os.path.getmtime(page)
    procs = [{"pid": 610, "ppid": 1, "exe": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
              "created": now - 30},
             {"pid": 620, "ppid": 1, "exe": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
              "created": now + 600}]
    assert remove_cli.select_processes(procs, str(inst["install"]), str(state), me=999) == [610]


@needs_windows
def test_stop_really_stops_a_program_holding_a_library_from_the_folder(tmp_path):
    """Live: a real program outside the folder with a library from inside it
    loaded (as node.exe holds next-swc.node) is found and stopped, and then
    the folder can be deleted."""
    import glob
    install = tmp_path / "BuildBot"
    state = tmp_path / "state"
    state.mkdir()
    lib_dir = install / "dashboard" / "node_modules" / "native"
    lib_dir.mkdir(parents=True)
    src = (glob.glob(os.path.join(sys.base_prefix, "DLLs", "_ctypes_test*.pyd"))
           or glob.glob(os.path.join(sys.base_prefix, "DLLs", "*.pyd")))[0]
    lib = lib_dir / "held.pyd"
    lib.write_bytes(Path(src).read_bytes())
    child = subprocess.Popen(
        [sys.executable, "-c", f"import ctypes, time; ctypes.WinDLL(r'{lib}'); time.sleep(60)"],
        cwd=tempfile.gettempdir(), stdin=subprocess.DEVNULL)
    try:
        deadline = time.time() + 20
        while time.time() < deadline and not any(
                os.path.normcase(f) == os.path.normcase(str(lib))
                for f in remove_cli.loaded_files(child.pid)):
            time.sleep(0.2)
        with pytest.raises(PermissionError):
            os.unlink(lib)                      # Windows holds a loaded library
        stopped = remove_cli.stop_everything(str(install), str(state), [str(install)])
        assert child.pid in stopped
        assert child.wait(timeout=15) is not None
        os.unlink(lib)
        assert remove_cli.still_running(str(install), str(state), [str(install)]) == []
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


# ── deleting never falls back to a walk by path ─────────────────────────────

@needs_windows
def test_a_failed_safe_rmtree_is_not_followed_by_a_path_walk(tmp_path, monkeypatch):
    """Acceptance finding: after safe_rmtree raised (a locked file, say), the
    old code listed the folder by path, so a junction swapped in after the
    link check was walked through. Now a lock is retried whole and reported,
    and nothing is walked by path."""
    from puffpai.security import safe_archive
    root = tmp_path / "root"
    sub = _write(root / "sub" / "a.txt").parent
    victim = _write(tmp_path / "victim" / "keep.txt", "must survive").parent
    calls = []
    real_listdir = os.listdir

    def locked_then_swapped(p, **k):
        calls.append(p)
        if len(calls) == 1:
            # The race: the folder is swapped for a junction to the victim.
            for f in real_listdir(sub):
                os.unlink(sub / f)
            os.rmdir(sub)
            _junction(victim, sub)
        raise PermissionError(13, "in use")
    monkeypatch.setattr(safe_archive, "safe_rmtree", locked_then_swapped)
    listed = []
    monkeypatch.setattr(remove_cli.os, "listdir",
                        lambda p=".": listed.append(str(p)) or real_listdir(p))
    now = [0.0]
    with pytest.raises(remove_cli.StillInUse):
        remove_cli.delete_tree(str(root), allowed_roots=[str(root)],
                               sleep=lambda s: now.__setitem__(0, now[0] + s),
                               clock=lambda: now[0], wait_s=3)
    assert (victim / "keep.txt").read_text(encoding="utf-8") == "must survive"
    assert listed == []
    assert len(calls) >= 3


def test_a_refused_safe_rmtree_stops_the_delete(tmp_path, monkeypatch):
    from puffpai.security import safe_archive
    root = tmp_path / "root"
    _write(root / "sub" / "a.txt")

    def refuse(p, **k):
        raise safe_archive.SafeArchiveError("is_link", "That folder is a link.")
    monkeypatch.setattr(safe_archive, "safe_rmtree", refuse)
    with pytest.raises(remove_cli.Changed):
        remove_cli.delete_tree(str(root), allowed_roots=[str(root)])
    assert (root / "sub" / "a.txt").exists()


@needs_windows
def test_a_kept_holding_folder_swapped_for_a_junction_is_not_walked(tmp_path, monkeypatch):
    """The path walk is left only for folders that hold something kept. One
    that became a junction after the link look stops the delete."""
    import shutil
    root = tmp_path / "root"
    mid = root / "mid"
    keep = mid / "kept"
    _write(keep / "k.txt")
    victim = tmp_path / "victim"
    _write(victim / "kept" / "v.txt", "must survive")
    _write(victim / "other.txt", "must survive too")
    shutil.rmtree(mid)
    _junction(victim, mid)
    real = remove_cli.is_link
    monkeypatch.setattr(remove_cli, "is_link",
                        lambda p: False if os.path.normcase(str(p)) == os.path.normcase(str(mid))
                        else real(p))
    with pytest.raises(remove_cli.Changed):
        remove_cli.delete_tree(str(root), allowed_roots=[str(root)], keeps=[str(keep)])
    assert (victim / "other.txt").read_text(encoding="utf-8") == "must survive too"
    assert (victim / "kept" / "v.txt").exists()


# ── the generated script: answers piped in, and the web page from elsewhere ──

def _real_install(base: Path, *, eat_stdin: bool = False) -> dict:
    """A scratch install whose venv really runs BuildBot's Python (this
    checkout's src), so the script gets its facts and its stop step."""
    repo = base / "BuildBot"
    _write(repo / ".puffpai-install.json", "{}")
    _write(repo / "src" / "puffpai" / "__init__.py", "")
    _write(repo / "Remove BuildBot.cmd", "@echo off")
    _write(repo / "vault_data" / "secret.txt", "vault canary")
    state = base / "state"
    _write(state / "logs" / "bot.log", "log")
    _write(state / "buildbot.env", f"PUFFPAI_CHECKOUT={repo}\n")
    box = base / "box"
    box.mkdir()
    apps = state / "apps"      # inside the state folder: BuildBot's own
    _write(apps / "seating" / "index.html", "<html>")
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", "--system-site-packages",
                    str(repo / "venv")], check=True, capture_output=True, timeout=180)
    shim = base / "shim"
    shim.mkdir()
    if eat_stdin:
        # Stands in for wsl.exe, which reads whatever input it inherits.
        (shim / "sitecustomize.py").write_text(
            "import sys\ntry:\n    sys.stdin.read()\nexcept Exception:\n    pass\n",
            encoding="utf-8")
    env_file = base / "buildbot.env"
    env_file.write_text("\n".join([
        f"PUFFPAI_CHECKOUT={repo}", f"PUFFPAI_STATE_DIR={state}", f"PUFFPAI_DATA_DIR={repo}",
        f"PUFFPAI_APPS_ROOT={apps}", f"PUFFPAI_BOX_ROOT={box}",
        "PUFFPAI_BOX_DISTRO=PuffPAI-sandbox-fxtest00",
        f"PYTHONPATH={shim};{ROOT / 'src'}", "PYTHONDONTWRITEBYTECODE=1",
        "PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring", ""]), encoding="utf-8")
    script = base / "remove-buildbot.ps1"
    script.write_text(_render_remove_script(repo, state, repo, env_file), encoding="ascii")
    return {"repo": repo, "state": state, "box": box, "apps": apps, "script": script}


@needs_windows
def test_the_generated_script_reads_piped_answers_itself(tmp_path, request):
    """Without this, piped Enter and Y are swallowed by a program the script
    starts, so a scripted run prints "Nothing was removed". Every program
    the script starts gets empty input."""
    t = _real_install(_base(tmp_path, request), eat_stdin=True)
    # Enter (no K), then Y. Text mode already sends each end of line as CR LF.
    proc = _run_ps(t["script"], "-InstallRoot", str(t["repo"]), stdin="\ny\n",
                   env_extra={"PUFFPAI_BOX_ROOT": str(t["box"])})
    out = proc.stdout
    assert proc.returncode == 0, out + proc.stderr
    assert W.REMOVE_KEPT not in out
    assert W.REMOVE_NOT_CHECKED not in out         # the facts came from BuildBot's Python
    assert out.rstrip().endswith(W.REMOVE_FINISHED)
    assert not t["repo"].exists() and not t["state"].exists()


@needs_windows
def test_the_generated_script_stops_a_web_page_running_from_elsewhere(tmp_path, request):
    """The web page started by run-dashboard.ps1 (node.exe outside the
    BuildBot folder, number never written down) can hold a file; without
    this, the script deletes the settings and the vault and then stops. It
    is found by the files it runs and stopped first, and everything goes."""
    import glob
    t = _real_install(_base(tmp_path, request))
    lib = t["repo"] / "dashboard" / "node_modules" / "@next" / "swc" / "next-swc.pyd"
    lib.parent.mkdir(parents=True)
    src = (glob.glob(os.path.join(sys.base_prefix, "DLLs", "_ctypes_test*.pyd"))
           or glob.glob(os.path.join(sys.base_prefix, "DLLs", "*.pyd")))[0]
    lib.write_bytes(Path(src).read_bytes())
    page = subprocess.Popen(
        [sys.executable, "-c", f"import ctypes, time; ctypes.WinDLL(r'{lib}'); time.sleep(120)"],
        cwd=tempfile.gettempdir(), stdin=subprocess.DEVNULL)
    try:
        deadline = time.time() + 20
        while time.time() < deadline and not any(
                os.path.normcase(f) == os.path.normcase(str(lib))
                for f in remove_cli.loaded_files(page.pid)):
            time.sleep(0.2)
        proc = _run_ps(t["script"], "-InstallRoot", str(t["repo"]), "-Yes",
                       env_extra={"PUFFPAI_BOX_ROOT": str(t["box"])})
        out = proc.stdout
        assert proc.returncode == 0, out + proc.stderr
        assert page.wait(timeout=15) is not None
        assert out.rstrip().endswith(W.REMOVE_FINISHED)
        assert not t["repo"].exists() and not t["state"].exists()
    finally:
        if page.poll() is None:
            page.kill()
            page.wait()


@needs_windows
def test_the_generated_script_deletes_nothing_while_a_file_is_held(tmp_path, request):
    """Something it may not stop (here a program working inside the folder,
    started by nobody BuildBot knows) holds the folder: nothing is deleted,
    the owner is told why, and a second run after it closes finishes."""
    t = _real_install(_base(tmp_path, request))
    inside = t["repo"] / "dashboard"
    inside.mkdir()
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                              cwd=str(inside), stdin=subprocess.DEVNULL)
    try:
        time.sleep(1.0)
        proc = _run_ps(t["script"], "-InstallRoot", str(t["repo"]), "-Yes",
                       env_extra={"PUFFPAI_BOX_ROOT": str(t["box"])})
        out = proc.stdout
        assert proc.returncode == 1, out + proc.stderr
        assert W.REMOVE_IN_USE_WINDOWS.format(path=t["repo"]) in out
        assert (t["repo"] / "vault_data" / "secret.txt").exists()
        assert (t["state"] / "logs" / "bot.log").exists()
        assert (t["repo"] / "Remove BuildBot.cmd").exists()
    finally:
        holder.kill()
        holder.wait()
    proc = _run_ps(t["script"], "-InstallRoot", str(t["repo"]), "-Yes",
                   env_extra={"PUFFPAI_BOX_ROOT": str(t["box"])})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not t["repo"].exists() and not t["state"].exists()


@needs_windows
def test_the_generated_script_part_way_keeps_the_vault_and_says_what_is_left(tmp_path, request):
    """A file grabbed after the check (a read-only folder entry that the
    deleting cannot pass stands in): the program files go first, so the vault
    and the settings are still there, and the lines say what is left."""
    t = _ps_tree(_base(tmp_path, request))
    held = _write(t["repo"] / "dashboard" / "late.bin", "x")
    text = t["script"].read_text(encoding="ascii")
    # Grab the file right after the check, as a racing program would.
    hook = ('$script:Late = [System.IO.File]::Open(\'' + str(held).replace("'", "''")
            + '\', \'Open\', \'Read\', \'None\')\r\n')
    marker = "Write-Host -NoNewline \"Deleting ...\""
    assert text.count(marker) == 1
    t["script"].write_text(text.replace(marker, hook + marker), encoding="ascii")
    proc = _run_ps(t["script"], "-InstallRoot", str(t["repo"]), "-Yes",
                   env_extra={"PUFFPAI_BOX_ROOT": str(t["box"])})
    out = proc.stdout
    assert proc.returncode == 1, out + proc.stderr
    assert W.REMOVE_PART_WAY_WINDOWS.format(path=held.parent) in out
    assert W.REMOVE_STILL_HERE in out
    assert W.REMOVE_LEFT_STATE.format(path=t["state"]) in out
    assert W.REMOVE_LEFT_VAULT.format(path=t["repo"] / "vault_data") in out
    assert W.REMOVE_RUN_AGAIN_WINDOWS.format(path=t["repo"]) in out
    assert (t["repo"] / "vault_data" / "secret.txt").exists()
    assert (t["state"] / "logs" / "bot.log").exists()
    assert (t["repo"] / ".puffpai-install.json").exists()


# ── processes ────────────────────────────────────────────────────────────────

def test_processes_are_chosen_by_program_file_or_recorded_number_only(inst):
    install, state = inst["install"], inst["state"]
    run = state / "running.json"
    run.write_text(json.dumps({"backend_pid": 400, "dashboard_pid": 200}), encoding="utf-8")
    now = os.path.getmtime(run)
    procs = [
        {"pid": 100, "ppid": 1, "exe": str(install / "venv" / "Scripts" / "python.exe"),
         "created": now - 100},
        {"pid": 200, "ppid": 1, "exe": r"C:\Program Files\nodejs\node.exe", "created": now - 50},
        # An editor with a file from the BuildBot folder open: its command line
        # names the folder, its program file does not. Never stopped.
        {"pid": 300, "ppid": 1, "exe": r"C:\Windows\System32\notepad.exe", "created": now - 5,
         "cmdline": f'notepad.exe "{install}\\README.md"'},
        # The number Start recorded, now reused by a newer process.
        {"pid": 400, "ppid": 1, "exe": r"C:\Python\python.exe", "created": now + 600},
        # This removal and the launcher that started it, both inside the folder.
        {"pid": 550, "ppid": 1, "exe": str(install / "venv" / "Scripts" / "puffpai.exe"),
         "created": now},
        {"pid": 500, "ppid": 550, "exe": str(install / "venv" / "Scripts" / "python.exe"),
         "created": now},
        {"pid": 4, "ppid": 0, "exe": "", "created": None},
    ]
    assert remove_cli.select_processes(procs, str(install), str(state), me=500) == [200, 100]


def test_running_env_from_start_bot_sh_counts_too(inst):
    run = inst["state"] / "running.env"
    run.write_text("backend_pid=321\ndashboard_pid=\n", encoding="utf-8")
    now = os.path.getmtime(run)
    procs = [{"pid": 321, "ppid": 1, "exe": "/usr/bin/python3.12", "created": now - 1}]
    assert remove_cli.select_processes(procs, str(inst["install"]), str(inst["state"]),
                                       me=999) == [321]


def test_the_process_list_reads_program_files_never_command_lines():
    src = Path(remove_cli.__file__).read_text(encoding="utf-8")
    assert "cmdline" not in src and "CommandLine" not in src
    me = [p for p in remove_cli.list_processes() if p["pid"] == os.getpid()]
    if sys.platform == "win32":
        assert me and os.path.normcase(os.path.realpath(me[0]["exe"])) == \
            os.path.normcase(os.path.realpath(sys.executable))
        assert me[0]["created"] and me[0]["created"] <= time.time()


def test_stop_uses_the_selection(inst, monkeypatch):
    monkeypatch.setattr(remove_cli, "list_processes", lambda: [
        {"pid": 77, "ppid": 1, "exe": str(inst["install"] / "venv" / "Scripts" / "python.exe"),
         "created": 1.0},
        {"pid": 78, "ppid": 1, "exe": r"C:\Windows\explorer.exe", "created": 1.0}])
    assert remove_cli.cmd_remove(_args(yes=True)) == 0
    assert inst["killed"] == [77]


# ── the box ──────────────────────────────────────────────────────────────────

@wsl_box_only
def test_the_box_goes_only_through_the_guard_and_the_claim_count(inst, capsys):
    other = inst["base"] / "another-install"
    other.mkdir()
    theirs = inst["box"] / "claims" / "PuffPAI-sandbox" / ("7" * 16 + ".json")
    theirs.write_text(json.dumps({"install_root": str(other)}), encoding="utf-8")
    assert remove_cli.cmd_remove(_args(yes=True)) == 0
    out = capsys.readouterr().out
    assert W.SHARED_STAYS in out
    assert _unregistered(inst["fake"]) == []
    assert not driver._has_my_claim("PuffPAI-sandbox")      # ours dropped
    assert theirs.exists()
    for call in inst["fake"].calls:
        assert "--shutdown" not in call


def test_a_box_outside_the_root_is_never_in_the_plan_or_removed(inst, capsys):
    inst["fake"].registry[0]["base_path"] = str(inst["base"] / "elsewhere" / "PuffPAI-sandbox")
    plan = remove_cli.build_plan()
    assert plan.box is None
    assert remove_cli.cmd_remove(_args(yes=True)) == 0
    assert _unregistered(inst["fake"]) == []


# ── Windows: hand over when this Python runs from inside the folder ─────────

@needs_windows
def test_windows_hands_over_to_the_script_when_running_from_inside(inst, monkeypatch, capsys):
    script = _write(inst["install"] / "remove-buildbot.ps1", "# the generated script\n")
    started = []
    monkeypatch.setattr(sys, "executable", str(inst["install"] / "venv" / "Scripts" / "python.exe"))
    monkeypatch.setattr(remove_cli.subprocess, "Popen",
                        lambda argv, **kw: started.append((argv, kw)))
    assert remove_cli.cmd_remove(_args(yes=True)) == 0
    assert W.REMOVE_NEW_WINDOW in capsys.readouterr().out
    assert inst["install"].exists() and inst["state"].exists()
    (argv, kw), = started
    assert os.path.basename(argv[0]).lower() == "powershell.exe" and os.path.isabs(argv[0])
    copy = argv[argv.index("-File") + 1]
    assert os.path.dirname(copy) == tempfile.gettempdir()
    assert os.path.basename(copy).startswith("puffpai-remove-")
    assert Path(copy).read_text(encoding="utf-8") == script.read_text(encoding="utf-8")
    assert argv[argv.index("-InstallRoot") + 1] == str(inst["install"])
    assert "-Yes" in argv and kw["cwd"] == tempfile.gettempdir()
    os.unlink(copy)


@needs_windows
def test_without_the_script_it_points_at_remove_buildbot_cmd(inst, monkeypatch, capsys):
    monkeypatch.setattr(sys, "executable", str(inst["install"] / "venv" / "Scripts" / "python.exe"))
    assert remove_cli.cmd_remove(_args(yes=True)) == 1
    assert W.REMOVE_USE_CMD.format(path=inst["install"]) in capsys.readouterr().out


# ── facts for the script, and the box-and-keys step it asks for ─────────────

@pytest.mark.keyring_stub
@wsl_box_only
def test_facts_hold_no_secret_and_only_step_needs_yes(inst, capsys, keyring_entries):
    for key in _our_keys(inst["state"], inst["vault"]):
        keyring_entries[key] = "SECRET-VALUE-" + "s" * 40
    assert remove_cli.cmd_remove(_args(facts=True)) == 0
    raw = capsys.readouterr().out
    assert "SECRET-VALUE" not in raw
    facts = json.loads(raw.strip().splitlines()[-1])
    assert facts["keyring"] == 2 and facts["box"]["distro"] == "PuffPAI-sandbox"
    assert facts["apps_delete"] is True
    assert remove_cli.cmd_remove(_args(only="box,keyring")) == 2      # no --yes
    assert remove_cli.cmd_remove(_args(only="everything", yes=True)) == 2
    assert _unregistered(inst["fake"]) == []
    assert remove_cli.cmd_remove(_args(only="box,keyring", yes=True)) == 0
    assert _unregistered(inst["fake"]) == ["PuffPAI-sandbox"]
    assert not keyring_entries
    assert inst["install"].exists()                                   # files are the script's


# ── the PowerShell script install.ps1 writes, run for real ──────────────────

def _render_remove_script(repo, state, data, env_file) -> str:
    text = (ROOT / "install.ps1").read_text(encoding="utf-8")
    body = re.search(r"\$removeTemplate = @'\r?\n(.*?)\r?\n'@", text, re.S).group(1)

    def lit(v):
        return str(v).replace("'", "''")

    return (body.replace("__RM_REPO__", lit(repo)).replace("__RM_STATE__", lit(state))
                .replace("__RM_DATA__", lit(data)).replace("__RM_ENVFILE__", lit(env_file)))


def _ps_tree(base: Path) -> dict:
    # A folder name with a quote and a $( ) in it: it must stay a name.
    repo = base / "Owner's $(whoami) BuildBot"
    _write(repo / ".puffpai-install.json", "{}")
    _write(repo / "install.ps1", "# not the real one")
    ro = _write(repo / ".git" / "objects" / "pack-1.pack", "read-only")
    os.chmod(ro, stat.S_IREAD)
    _write(repo / "vault_data" / "secret.txt", "vault canary")
    state = base / "state"
    _write(state / "logs" / "bot.log", "log")
    _write(state / "buildbot.env", f"PUFFPAI_CHECKOUT={repo}\n")
    outside = base / "outside_vault"
    _write(outside / "secret.txt", "must survive")
    _junction(outside, repo / "link_to_outside")
    _junction(outside, state / "another_link")
    box = state / "box"
    _write(box / "claims" / "PuffPAI-sandbox" / "someone.json", "{}")
    script = base / "remove-buildbot.ps1"
    script.write_text(_render_remove_script(repo, state, repo, base / "no.env"),
                      encoding="ascii")
    return {"repo": repo, "state": state, "outside": outside, "box": box, "script": script}


def _run_ps(script: Path, *args: str, stdin: str = "", env_extra: dict | None = None):
    ps = os.path.join(os.environ["SystemRoot"], "System32", "WindowsPowerShell", "v1.0",
                      "powershell.exe")
    env = dict(os.environ)
    env["PUFFPAI_NO_PAUSE"] = "1"
    env.update(env_extra or {})
    return subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(script), *args], input=stdin, capture_output=True,
                          text=True, env=env, timeout=240, cwd=tempfile.gettempdir())


@needs_windows
def test_the_generated_script_removes_only_buildbot(tmp_path, request):
    t = _ps_tree(_base(tmp_path, request))
    apps = t["state"] / "apps"
    _write(apps / "seating" / "index.html", "<html>")
    proc = _run_ps(t["script"], "-InstallRoot", str(t["repo"]), "-Yes",
                   env_extra={"PUFFPAI_BOX_ROOT": str(t["box"]),
                              "PUFFPAI_APPS_ROOT": str(apps)})
    out = proc.stdout
    assert proc.returncode == 0, out + proc.stderr
    assert "Remove BuildBot" in out
    assert f"  the BuildBot folder: {t['repo']} (" in out
    assert f"  your bot's settings, books and chats: {t['state']} (" in out
    assert W.REMOVE_VAULT in out
    assert W.REMOVE_NOT_CHECKED in out            # no BuildBot Python in this scratch tree
    assert "Stopping your bot ... done" in out and "Deleting ... done" in out
    assert out.rstrip().endswith(W.REMOVE_FINISHED)
    assert not t["repo"].exists()
    assert (t["outside"] / "secret.txt").read_text(encoding="utf-8") == "must survive"
    assert (t["box"] / "claims" / "PuffPAI-sandbox" / "someone.json").exists()
    # Without BuildBot's own Python nobody can say who else uses the apps
    # folder, so it stays even though it sits in the state folder.
    assert (apps / "seating" / "index.html").exists()
    assert sorted(os.listdir(t["state"])) == ["apps", "box"]


@needs_windows
def test_the_generated_script_works_for_a_folder_name_with_accents(tmp_path, request):
    """Review finding: the script was written as ASCII, so "Cafe" with an
    e-acute became "Caf?" and the remover crashed before doing anything."""
    base = _base(tmp_path, request)
    repo = base / "Caf\u00e9 BuildBot"
    _write(repo / ".puffpai-install.json", "{}")
    state = base / "st\u00e4te"
    _write(state / "buildbot.env", f"PUFFPAI_CHECKOUT={repo}\n")
    script = base / "remove-buildbot.ps1"
    script.write_text(_render_remove_script(repo, state, repo, base / "no.env"),
                      encoding="utf-8-sig")
    proc = _run_ps(script, "-InstallRoot", str(repo), stdin="n\n",
                   env_extra={"PUFFPAI_BOX_ROOT": str(base / "box")})
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert W.REMOVE_KEPT in proc.stdout and "Exception" not in proc.stdout + proc.stderr
    assert repo.exists()
    proc = _run_ps(script, "-InstallRoot", str(repo), "-Yes",
                   env_extra={"PUFFPAI_BOX_ROOT": str(base / "box")})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not repo.exists() and not state.exists()


@needs_windows
def test_the_generated_script_keeps_a_state_folder_of_another_install(tmp_path, request):
    t = _ps_tree(_base(tmp_path, request))
    other = t["state"].parent / "Other BuildBot"
    other.mkdir()
    (t["state"] / "buildbot.env").write_text(f"PUFFPAI_CHECKOUT={other}\n", encoding="utf-8")
    proc = _run_ps(t["script"], "-InstallRoot", str(t["repo"]), "-Yes",
                   env_extra={"PUFFPAI_BOX_ROOT": str(t["box"])})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert W.REMOVE_STATE_STAYS.format(path=t["state"]) in proc.stdout
    assert "your bot's settings, books and chats" not in proc.stdout
    assert (t["state"] / "logs" / "bot.log").exists()
    assert not t["repo"].exists()


@needs_windows
def test_the_generated_script_refuses_a_state_folder_that_is_not_buildbots(tmp_path, request):
    t = _ps_tree(_base(tmp_path, request))
    ssh = t["state"].parent / "dot-ssh"
    _write(ssh / "id_ed25519", "private key canary")
    bad = t["state"].parent / "bad-state.ps1"
    bad.write_text(_render_remove_script(t["repo"], ssh, t["repo"], t["state"].parent / "no.env"),
                   encoding="utf-8-sig")
    proc = _run_ps(bad, "-InstallRoot", str(t["repo"]), "-Yes",
                   env_extra={"PUFFPAI_BOX_ROOT": str(t["box"])})
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert W.REMOVE_REFUSED.format(path=ssh, why=W.REMOVE_WHY_NOT_BUILDBOT) in proc.stdout
    assert (ssh / "id_ed25519").exists() and t["repo"].exists()


@needs_windows
def test_the_generated_script_asks_and_n_keeps_everything(tmp_path, request):
    t = _ps_tree(_base(tmp_path, request))
    proc = _run_ps(t["script"], "-InstallRoot", str(t["repo"]), stdin="\nn\n",
                   env_extra={"PUFFPAI_BOX_ROOT": str(t["box"])})
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "Remove all of this? Type Y and press Enter. Anything else keeps it." in proc.stdout
    assert W.REMOVE_KEPT in proc.stdout
    assert (t["repo"] / "vault_data" / "secret.txt").exists()
    assert (t["state"] / "logs" / "bot.log").exists()


@needs_windows
def test_the_generated_script_refuses_another_folder_and_main_folders(tmp_path, request):
    base = _base(tmp_path, request)
    t = _ps_tree(base)
    proc = _run_ps(t["script"], "-InstallRoot", str(base / "some other folder"), "-Yes")
    assert proc.returncode == 2
    assert W.REMOVE_WRONG_FOLDER in proc.stdout
    assert t["repo"].exists()
    home = base / "home"
    home.mkdir()
    bad = base / "bad-state.ps1"
    bad.write_text(_render_remove_script(t["repo"], home, t["repo"], base / "no.env"),
                   encoding="ascii")
    proc = _run_ps(bad, "-InstallRoot", str(t["repo"]), "-Yes",
                   env_extra={"USERPROFILE": str(home)})
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert (f"BuildBot will not delete {home}, because it is one of your own main "
            f"folders, or holds one. Nothing was deleted.") in proc.stdout
    assert t["repo"].exists() and (t["repo"] / "vault_data" / "secret.txt").exists()



# ── the state folder must be BuildBot's, and this install's ─────────────────

def test_a_state_setting_pointed_at_a_private_folder_is_refused(inst, monkeypatch, capsys):
    """Review finding: root_problem accepted any state folder, so a mistaken
    PUFFPAI_STATE_DIR (say the owner's .ssh) would be deleted after Y."""
    ssh = inst["base"] / ".ssh"
    _write(ssh / "id_ed25519", "private key canary")
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(ssh))
    assert remove_cli.root_problem(str(ssh), kind="state") == W.REMOVE_WHY_NOT_BUILDBOT
    plan = remove_cli.build_plan()
    assert [(os.path.normcase(os.path.normpath(p)), w) for p, w in plan.refused] == [
        (os.path.normcase(str(ssh)), W.REMOVE_WHY_NOT_BUILDBOT)]
    assert remove_cli.cmd_remove(_args(yes=True)) == 2
    assert (ssh / "id_ed25519").exists() and inst["install"].exists()


@pytest.mark.parametrize("sign", remove_cli.STATE_SIGNS)
def test_any_state_sign_makes_a_state_folder_buildbots(tmp_path, sign):
    target = tmp_path / "st" / sign
    if sign == "pairing":
        target.mkdir(parents=True)
    else:
        _write(target, "x")
    assert remove_cli.root_problem(str(tmp_path / "st"), kind="state") == ""


@pytest.mark.keyring_stub
def test_a_state_folder_of_another_install_stays(inst, monkeypatch, capsys, keyring_entries):
    for key in _our_keys(inst["state"], inst["vault"]):
        keyring_entries[key] = "secret"
    other = inst["base"] / "Other BuildBot"
    other.mkdir()
    (inst["state"] / "buildbot.env").write_text(f"PUFFPAI_CHECKOUT={other}\n", encoding="utf-8")
    plan = remove_cli.build_plan()
    assert plan.state_stays
    assert "state" not in [k for k, _p, _n in plan.roots]
    assert plan.keyring and all(service != remove_cli.KEYRING_SERVICE_KEYS
                                for service, _n in plan.keyring)
    _first, rest = remove_cli.plan_lines(plan)
    assert W.REMOVE_STATE_STAYS.format(path=plan.state_dir) in rest
    assert remove_cli.facts(plan)["state_stays"] is True
    assert remove_cli.cmd_remove(_args(yes=True)) == 0
    assert (inst["state"] / "logs" / "bot.log").exists()
    assert not inst["install"].exists()


def test_env_file_value_reads_only_the_named_key(tmp_path):
    env = _write(tmp_path / "buildbot.env",
                 "\ufeff# PUFFPAI_CHECKOUT=C:\\commented\nPUFFPAI_CHECKOUT = C:\\x y\nOTHER=1\n")
    assert remove_cli.env_file_value(str(env), "PUFFPAI_CHECKOUT") == "C:\\x y"
    assert remove_cli.env_file_value(str(env), "MISSING") == ""
    assert remove_cli.env_file_value(str(tmp_path / "none.env"), "X") == ""
