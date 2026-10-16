"""Owner folders: one set of rules for the locked box and the browser apps

Every folder is scratch: a fake profile with its own Pictures, Documents and
friends, the conftest's pinned state and data folders, and scratch apps and
box roots. Junctions are real NTFS junctions on Windows.
"""

from __future__ import annotations

import os
import sys

import pytest

from puffpai.box import wording as W
from puffpai.config import get_config, get_state_dir
from puffpai.security import owner_folders as of

needs_windows = pytest.mark.skipif(sys.platform != "win32", reason="NTFS junctions")


def _junction(target, link) -> None:
    import _winapi
    _winapi.CreateJunction(str(target), str(link))


@pytest.fixture
def home(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    folders = {}
    for name in of.READ_ROOT_NAMES:
        (profile / name).mkdir(parents=True)
        folders[name] = [str(profile / name)]
    monkeypatch.setattr(of, "known_folders", lambda: folders)
    apps = tmp_path / "apps"
    box = tmp_path / "box"
    install = tmp_path / "install"
    for d in (apps, box, install):
        d.mkdir()
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", str(apps))
    monkeypatch.setenv("PUFFPAI_BOX_ROOT", str(box))
    monkeypatch.setenv("PUFFPAI_CHECKOUT", str(install))
    vault = of.vault_dir()
    os.makedirs(vault, exist_ok=True)
    with open(os.path.join(vault, "secret.txt"), "w", encoding="utf-8") as fh:
        fh.write("canary")
    return {"profile": profile, "apps": apps, "box": box, "install": install,
            "vault": vault, "tmp": tmp_path}


def test_a_normal_folder_is_given_read_only(home):
    wedding = home["profile"] / "Pictures" / "Wedding ideas"
    wedding.mkdir()
    real, why = of.check(str(wedding), "ro")
    assert why == ""
    assert os.path.normcase(real) == os.path.normcase(os.path.realpath(wedding))


def test_a_whole_read_root_may_be_given(home):
    real, why = of.check(str(home["profile"] / "Music"), "ro")
    assert real and why == ""


@needs_windows
def test_a_junction_in_pictures_to_the_vault_is_refused(home):
    link = home["profile"] / "Pictures" / "vault_junction"
    _junction(home["vault"], link)
    assert (link / "secret.txt").read_text(encoding="utf-8") == "canary"
    assert of.check(str(link), "ro") == (None, W.FOLDER_PRIVATE)
    # A folder that HOLDS a junction to the vault is refused too: a consumer
    # that followed links inside the grant would reach the vault.
    assert of.check(str(link / ".."), "ro") == (None, W.FOLDER_PRIVATE)
    other = home["profile"] / "Music"
    assert of.check(str(other), "ro")[1] == ""               # a clean root is fine


@needs_windows
def test_a_folder_holding_a_deep_junction_to_the_vault_is_refused(home):
    album = home["profile"] / "Pictures" / "album" / "2026" / "june"
    album.mkdir(parents=True)
    _junction(home["vault"], album / "inner")
    top = home["profile"] / "Pictures" / "album"
    assert of.check(str(top), "ro") == (None, W.FOLDER_PRIVATE)
    assert of.links_to_private(str(top)) is True


@needs_windows
def test_a_junction_to_somewhere_ordinary_does_not_block_the_folder(home):
    elsewhere = home["profile"] / "Music" / "mixes"
    elsewhere.mkdir()
    pics = home["profile"] / "Pictures" / "trip"
    pics.mkdir()
    _junction(elsewhere, pics / "mixes")
    assert of.check(str(pics), "ro")[1] == ""


@needs_windows
def test_the_link_scan_stops_at_its_limit(home):
    pics = home["profile"] / "Pictures" / "many"
    pics.mkdir()
    for i in range(30):
        (pics / f"f{i:02d}.txt").write_text("x", encoding="utf-8")
    _junction(home["vault"], pics / "zz_vault")
    # Past the limit the folder is refused (fail closed), never waved through.
    assert of.links_to_private(str(pics), limit=5) is True
    assert of.links_to_private(str(pics)) is True


@needs_windows
def test_a_folder_too_big_to_check_is_refused_even_with_no_link(home, monkeypatch):
    """Validator item: past LINK_SCAN_MAX check() said yes, so a junction to the
    vault after entry 20,000 was handed out. Now the folder is refused."""
    monkeypatch.setattr(of, "LINK_SCAN_MAX", 10)
    pics = home["profile"] / "Pictures" / "big"
    pics.mkdir()
    for i in range(11):
        (pics / f"f{i:02d}.txt").write_text("x", encoding="utf-8")
    assert of.check(str(pics), "ro") == (None, W.FOLDER_TOO_BIG)
    _junction(home["vault"], pics / "zzz")
    assert of.check(str(pics), "ro")[0] is None
    # A small folder next to it is still fine.
    small = home["profile"] / "Pictures" / "small"
    small.mkdir()
    (small / "a.txt").write_text("x", encoding="utf-8")
    assert of.check(str(small), "ro")[1] == ""


def test_a_subfolder_the_scan_cannot_open_is_refused(home, monkeypatch):
    pics = home["profile"] / "Pictures" / "locked"
    (pics / "inner").mkdir(parents=True)
    real_scandir = os.scandir

    def scandir(path):
        if os.path.basename(str(path)) == "inner":
            raise PermissionError(13, "denied")
        return real_scandir(path)
    monkeypatch.setattr(of.os, "scandir", scandir)
    assert of.links_to_private(str(pics), []) is True


@needs_windows
def test_a_junction_in_pictures_to_a_scratch_vault_outside_every_root_is_refused(home):
    """Even a vault the config does not know about is refused, because the
    junction leads outside the read roots."""
    other = home["tmp"] / "scratch_vault"
    other.mkdir()
    link = home["profile"] / "Pictures" / "holiday"
    _junction(other, link)
    assert of.check(str(link), "ro") == (None, W.FOLDER_OUTSIDE)


@pytest.mark.parametrize("which", ["vault", "inbox", "state", "data", "apps", "box", "install"])
def test_private_roots_are_refused(home, which):
    paths = {"vault": home["vault"], "inbox": home["vault"] + ".inbox",
             "state": get_state_dir(), "data": get_config().DATA_DIR,
             "apps": str(home["apps"]), "box": str(home["box"]),
             "install": str(home["install"])}
    os.makedirs(paths[which], exist_ok=True)
    assert of.check(paths[which], "ro") == (None, W.FOLDER_PRIVATE)
    assert of.check(paths[which], "rw") == (None, W.FOLDER_PRIVATE)


def test_deny_roots_name_every_private_place(home):
    denied = of.deny_roots()
    for p in (home["vault"], home["vault"] + ".inbox", get_state_dir(),
              get_config().DATA_DIR, str(home["apps"]), str(home["box"]),
              str(home["install"])):
        assert os.path.normcase(os.path.realpath(p)) in denied, p


def test_a_private_root_inside_a_read_root_makes_its_parent_refused(home, monkeypatch):
    """The apps root placed inside Documents: Documents holds it, so Documents
    is refused; a sibling folder is still fine."""
    inside = home["profile"] / "Documents" / "PuffPAI apps"
    inside.mkdir()
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", str(inside))
    assert of.check(str(home["profile"] / "Documents"), "ro") == (None, W.FOLDER_PRIVATE)
    assert of.check(str(inside), "ro") == (None, W.FOLDER_PRIVATE)
    sibling = home["profile"] / "Documents" / "Recipes"
    sibling.mkdir()
    assert of.check(str(sibling), "ro")[1] == ""


def test_outside_the_read_roots_is_refused(home):
    elsewhere = home["tmp"] / "elsewhere"
    elsewhere.mkdir()
    assert of.check(str(elsewhere), "ro") == (None, W.FOLDER_OUTSIDE)
    assert of.check(str(home["profile"]), "ro") == (None, W.FOLDER_OUTSIDE)


@pytest.mark.parametrize("path,why", [
    ("\\\\server\\share\\photos", W.FOLDER_NETWORK),
    ("//server/share", W.FOLDER_NETWORK),
    ("\\\\?\\C:\\Users", W.FOLDER_NETWORK),
    ("\\\\wsl.localhost\\Ubuntu\\home", W.FOLDER_NETWORK),
    ("", W.FOLDER_NOT_THERE),
    ("bad\x00name", W.FOLDER_NOT_THERE),
])
def test_odd_paths_are_refused(home, path, why):
    assert of.check(path, "ro") == (None, why)


def test_missing_and_file_and_mode(home):
    assert of.check(str(home["profile"] / "Pictures" / "nope"), "ro") == (None, W.FOLDER_NOT_THERE)
    f = home["profile"] / "Pictures" / "a.jpg"
    f.write_bytes(b"x")
    assert of.check(str(f), "ro") == (None, W.FOLDER_NOT_A_FOLDER)
    assert of.check(str(home["profile"] / "Pictures"), "write") == (None, W.FOLDER_BAD_MODE)


def test_write_outside_write_folder_is_refused(home):
    pics = home["profile"] / "Pictures" / "Wedding ideas"
    pics.mkdir()
    assert of.check(str(pics), "rw") == (None, W.FOLDER_WRITE_ONLY_OWN)
    made = of.write_folder("Wedding Planner", "Mood board")
    assert os.path.normcase(made) == os.path.normcase(os.path.realpath(
        home["profile"] / "Documents" / "Wedding Planner files" / "Mood board"))
    real, why = of.check(made, "rw")
    assert why == "" and real
    # The parent (every app's folder) is not a write grant, nor is a sibling.
    parent = os.path.dirname(made)
    assert of.check(parent, "rw") == (None, W.FOLDER_WRITE_ONLY_OWN)
    sib = os.path.join(parent, "Other app")
    os.makedirs(sib)
    assert of.check(sib, "rw") == (None, W.FOLDER_WRITE_ONLY_OWN)


@pytest.mark.parametrize("bot,app,want_bot,want_app", [
    ("../../Windows", "..", "Windows", "app"),
    ("CON", "nul.txt", "BuildBot", "app"),
    ("  My  Bot. ", "Mood:board?", "My Bot", "Mood board"),
    ("", "", "BuildBot", "app"),
])
def test_write_folder_names_are_made_safe(home, bot, app, want_bot, want_app):
    made = of.write_folder(bot, app)
    docs = os.path.realpath(home["profile"] / "Documents")
    assert os.path.normcase(os.path.dirname(os.path.dirname(made))) == os.path.normcase(docs)
    assert os.path.basename(os.path.dirname(made)) == f"{want_bot} files"
    assert os.path.basename(made) == want_app


@needs_windows
def test_write_folder_refuses_a_junction_planted_as_the_bot_folder(home):
    planted = home["profile"] / "Documents" / "Evil files"
    _junction(home["vault"], planted)
    with pytest.raises(PermissionError) as info:
        of.write_folder("Evil", "app")
    assert str(info.value) == W.FOLDER_PRIVATE
    assert os.listdir(home["vault"]) == ["secret.txt"]


def test_the_sweep_renames_active_files(home):
    made = of.write_folder("Planner", "Budget")
    names = ["evil.lnk", "run.ps1", "setup.EXE", "macro.docm", "sneaky.js. ",
             "budget.csv", "photo.jpg"]
    for n in names:
        try:
            with open(os.path.join(made, n), "wb") as fh:
                fh.write(b"x")
        except OSError:
            pass            # Windows strips a trailing ". " itself
    sub = os.path.join(made, "deeper")
    os.makedirs(sub)
    with open(os.path.join(sub, "tool.bat"), "wb") as fh:
        fh.write(b"x")
    renamed = of.after_write_sweep(made)
    got = sorted(os.path.basename(p) for p in renamed)
    assert "evil.lnk.blocked" in got and "run.ps1.blocked" in got
    assert "setup.EXE.blocked" in got and "macro.docm.blocked" in got
    assert "tool.bat.blocked" in got
    left = set(os.listdir(made))
    assert {"budget.csv", "photo.jpg"} <= left
    assert not {"evil.lnk", "run.ps1", "setup.EXE"} & left


def test_the_sweep_never_overwrites_an_earlier_blocked_file(home):
    made = of.write_folder("Planner", "Budget")
    for _ in range(2):
        with open(os.path.join(made, "evil.lnk"), "wb") as fh:
            fh.write(b"x")
        of.after_write_sweep(made)
    assert {"evil.lnk.blocked", "evil.lnk.2.blocked"} <= set(os.listdir(made))


@needs_windows
def test_the_sweep_marks_new_files_as_from_the_internet(home):
    made = of.write_folder("Planner", "Budget")
    for n in ("budget.csv", "run.ps1"):
        with open(os.path.join(made, n), "wb") as fh:
            fh.write(b"x")
    of.after_write_sweep(made)
    for n in ("budget.csv", "run.ps1.blocked"):
        with open(os.path.join(made, n) + ":Zone.Identifier", encoding="ascii") as fh:
            text = fh.read()
        assert "[ZoneTransfer]" in text and "ZoneId=3" in text


@needs_windows
def test_the_sweep_does_not_follow_links(home):
    made = of.write_folder("Planner", "Budget")
    _junction(home["vault"], os.path.join(made, "into_vault"))
    with open(os.path.join(home["vault"], "evil.lnk"), "wb") as fh:
        fh.write(b"x")
    of.after_write_sweep(made)
    assert os.path.exists(os.path.join(home["vault"], "evil.lnk"))
    assert not os.path.exists(os.path.join(home["vault"], "secret.txt:Zone.Identifier"))


def test_the_sweep_only_runs_in_a_write_folder(home):
    pics = home["profile"] / "Pictures"
    with pytest.raises(PermissionError):
        of.after_write_sweep(str(pics))


def test_every_active_ending_is_on_the_list():
    want = (".exe .dll .scr .com .bat .cmd .ps1 .vbs .js .jse .wsf .hta .msi .lnk "
            ".url .scf .library-ms .docm .xlsm .pptm").split()
    # The pinned list, plus endings found missing in review (all stricter).
    more = (".vbe .pif .cpl .reg .msc .settingcontent-ms .appref-ms .jar .chm "
            ".iso .img .vhd .vhdx").split()
    assert set(want) <= set(of.ACTIVE_EXTS)
    assert set(more) <= set(of.ACTIVE_EXTS)
    for ext in want + more:
        assert of.is_active_name("x" + ext.upper())
    assert not of.is_active_name("notes.txt")
    assert not of.is_active_name("photo.jpg")


def test_microsofts_high_risk_endings_are_on_the_list():
    """Review found scriptlets, browser apps, shell scraps and web archives
    left active (d.sct, e.xbap, f.msh, g.vb, h.shs, i.mht)."""
    risky = (".sct .xbap .msh .vb .shs .mht .mhtml .msh1 .msh2 .mshxml .ade .adp "
             ".bas .crt .cer .der .hpj .ins .isp .jnlp .ksh .mda .mdb .mde .msu "
             ".ops .pcd .plg .prf .prg .pst .shb .tmp .vsmacros .vsw .wsb .xnk "
             ".py .pyw .pyz .theme .themepack .deskthemepack").split()
    missing = [e for e in risky if e not in of.ACTIVE_EXTS]
    assert missing == []
    for ext in risky:
        assert of.is_active_name("x" + ext.upper() + ".")
    for plain in ("guest-list.csv", "menu.pdf", "site.html", "plan.json",
                  "party.ics", "notes.md", "photo.png", "song.mp3"):
        assert not of.is_active_name(plain)


@needs_windows
def test_the_sweep_blocks_the_high_risk_endings(home):
    made = of.write_folder("Party Bot", "Scraps")
    names = ("d.sct", "e.xbap", "f.msh", "g.vb", "h.shs", "i.mht", "keep.csv")
    for name in names:
        with open(os.path.join(made, name), "w", encoding="ascii") as fh:
            fh.write("x")
    of.after_write_sweep(made)
    left = sorted(os.listdir(made))
    assert left == sorted([n + ".blocked" for n in names[:-1]] + ["keep.csv"])


@needs_windows
def test_the_sweep_catches_names_ending_in_a_dot_or_space(home):
    """Windows trims a trailing dot or space in ordinary calls, so these names
    could not be renamed and stayed active (review finding)."""
    made = of.write_folder("Party Bot", "Cards")
    ext = of._long(made)
    for name in ("b.exe.", "t.exe ", "ok.txt"):
        with open(os.path.join(ext, name), "w", encoding="ascii") as fh:
            fh.write("x")
    renamed = of.after_write_sweep(made)
    left = sorted(os.listdir(ext))
    assert "b.exe." not in left and "t.exe " not in left
    assert len(renamed) == 2 and all(r.endswith(".blocked") for r in renamed)
    for r in renamed:
        assert os.path.exists(of._long(r) + of.ZONE_STREAM)
    assert of._long(made).startswith("\\\\?\\")


@needs_windows
def test_the_sweep_marks_read_only_files_and_keeps_them_read_only(home):
    import stat as _stat
    made = of.write_folder("Party Bot", "Lists")
    path = os.path.join(made, "ro.txt")
    with open(path, "w", encoding="ascii") as fh:
        fh.write("x")
    os.chmod(path, _stat.S_IREAD)
    try:
        of.after_write_sweep(made)
        assert os.path.exists(path + of.ZONE_STREAM)
        assert not (os.stat(path).st_mode & _stat.S_IWRITE)
    finally:
        os.chmod(path, _stat.S_IWRITE | _stat.S_IREAD)


def test_the_sweep_deletes_an_active_file_it_cannot_rename(home, monkeypatch):
    made = of.write_folder("Party Bot", "Stuck")
    path = os.path.join(made, "run.bat")
    with open(path, "w", encoding="ascii") as fh:
        fh.write("x")

    def no_rename(a, b):
        raise OSError("held")
    monkeypatch.setattr(of.os, "rename", no_rename)
    assert of.after_write_sweep(made) == []
    assert not os.path.exists(path)


def test_the_sweep_refuses_to_hand_back_an_active_file_it_cannot_touch(home, monkeypatch):
    made = of.write_folder("Party Bot", "Held")
    with open(os.path.join(made, "run.bat"), "w", encoding="ascii") as fh:
        fh.write("x")

    def nope(*a, **k):
        raise OSError("held")
    monkeypatch.setattr(of.os, "rename", nope)
    monkeypatch.setattr(of.os, "unlink", nope)
    with pytest.raises(PermissionError):
        of.after_write_sweep(made)


@needs_windows
def test_reading_known_folders_does_not_change_the_shared_windows_function():
    """Regression: owner_folders once set argtypes on the process-wide
    ctypes.windll.shell32.SHGetKnownFolderPath, and safe_archive's own
    known-folder check (a different GUID type) then silently found nothing."""
    import ctypes
    from puffpai.security import safe_archive
    before = ctypes.windll.shell32.SHGetKnownFolderPath.argtypes
    assert of._known_folder_windows(of._KNOWN_FOLDER_IDS["Documents"])
    assert ctypes.windll.shell32.SHGetKnownFolderPath.argtypes == before
    assert safe_archive._windows_known_folders()  # noqa: SLF001
