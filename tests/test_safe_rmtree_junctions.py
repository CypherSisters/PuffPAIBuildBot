"""safe_archive.safe_rmtree: removing a folder never follows a link out of it.

Windows only (junctions are the hazard: ``shutil.rmtree`` in older Pythons,
and many hand-written removers, walk into a junction and delete what it
points at). The tests that name a drive root or the profile folder replace
every deleting call first, so even a broken check could not delete anything.
"""

from __future__ import annotations

import os
import stat
import sys

import pytest

from puffpai.security import safe_archive
from puffpai.security.safe_archive import SafeArchiveError

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="junctions are a Windows feature")


def _junction(link, target):
    import _winapi
    _winapi.CreateJunction(str(target), str(link))


@pytest.fixture
def tree(tmp_path):
    allowed = tmp_path / "allowed"
    app = allowed / "app"
    (app / "sub").mkdir(parents=True)
    (app / "f1.txt").write_text("one")
    (app / "sub" / "f2.txt").write_text("two")
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "secret.txt").write_text("do not touch")
    (vault / "deeper").mkdir()
    (vault / "deeper" / "key.bin").write_bytes(b"\x01\x02")
    _junction(app / "link", vault)
    return allowed, app, vault


@pytest.fixture
def no_deleting(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("a deleting call ran")
    monkeypatch.setattr(safe_archive, "_remove_dir", boom)
    monkeypatch.setattr(safe_archive.os, "unlink", boom)
    monkeypatch.setattr(safe_archive.os, "rmdir", boom)
    return boom


def test_a_junction_inside_is_unlinked_and_its_target_left_alone(tree):
    allowed, app, vault = tree
    assert os.path.isfile(app / "link" / "secret.txt")  # the junction works
    got = safe_archive.safe_rmtree(str(app), allowed_roots=[str(allowed)])
    assert got["links_unlinked"] == 1
    assert got["removed"] == 4  # f1.txt, sub/f2.txt, sub, app
    assert not app.exists()
    assert (vault / "secret.txt").read_text() == "do not touch"
    assert (vault / "deeper" / "key.bin").read_bytes() == b"\x01\x02"


def test_a_root_that_is_a_junction_is_refused(tree):
    allowed, _app, vault = tree
    jroot = allowed / "jroot"
    _junction(jroot, vault)
    with pytest.raises(SafeArchiveError) as exc:
        safe_archive.safe_rmtree(str(jroot), allowed_roots=[str(allowed)])
    assert exc.value.code == "is_link"
    assert (vault / "secret.txt").exists() and jroot.exists()


def test_a_path_outside_the_allowed_roots_is_refused(tree):
    allowed, _app, vault = tree
    with pytest.raises(SafeArchiveError) as exc:
        safe_archive.safe_rmtree(str(vault), allowed_roots=[str(allowed)])
    assert exc.value.code == "outside_roots"
    assert (vault / "secret.txt").exists()


def test_a_path_that_only_looks_inside_is_refused(tree, tmp_path):
    allowed, _app, _vault = tree
    twin = tmp_path / "allowed-twin"
    twin.mkdir()
    (twin / "x.txt").write_text("x")
    with pytest.raises(SafeArchiveError):
        safe_archive.safe_rmtree(str(twin), allowed_roots=[str(allowed)])
    assert (twin / "x.txt").exists()


def test_a_drive_root_is_refused(no_deleting):
    drive = os.path.splitdrive(os.path.abspath(os.sep))[0] + "\\"
    with pytest.raises(SafeArchiveError):
        safe_archive.safe_rmtree(drive, allowed_roots=[drive])


def test_the_profile_folder_is_refused(no_deleting):
    home = os.path.expanduser("~")
    with pytest.raises(SafeArchiveError) as exc:
        safe_archive.safe_rmtree(home, allowed_roots=[os.path.dirname(home)])
    assert exc.value.code == "protected"


@pytest.mark.parametrize("name", ["Desktop", "Documents", "Downloads",
                                  "Pictures", "Music", "Videos"])
def test_the_profile_known_folders_are_refused(no_deleting, name):
    target = os.path.join(os.path.expanduser("~"), name)
    if not os.path.isdir(target):
        pytest.skip(f"{name} is not on this computer")
    with pytest.raises(SafeArchiveError) as exc:
        safe_archive.safe_rmtree(target, allowed_roots=[os.path.expanduser("~")])
    assert exc.value.code in ("protected", "is_link")


def test_known_folders_where_windows_really_keeps_them_are_refused(no_deleting):
    """OneDrive moves Desktop, Documents and Pictures; Windows says where."""
    found = safe_archive._windows_known_folders()  # noqa: SLF001
    assert found, "Windows reported no known folders"
    for target in found:
        with pytest.raises(SafeArchiveError):
            safe_archive.safe_rmtree(target, allowed_roots=[os.path.dirname(target)])


def test_a_folder_holding_a_known_folder_is_refused(no_deleting):
    home = os.path.expanduser("~")
    parent = os.path.dirname(home)
    with pytest.raises(SafeArchiveError) as exc:
        safe_archive.safe_rmtree(parent, allowed_roots=[parent])
    assert exc.value.code == "protected"


def test_read_only_files_are_removed(tmp_path):
    allowed = tmp_path / "allowed"
    obj = allowed / "proj" / ".git" / "objects"
    obj.mkdir(parents=True)
    f = obj / "ab12"
    f.write_bytes(b"pack")
    os.chmod(f, stat.S_IREAD)
    got = safe_archive.safe_rmtree(str(allowed / "proj"), allowed_roots=[str(allowed)])
    assert not (allowed / "proj").exists() and got["links_unlinked"] == 0


def test_a_missing_path_is_nothing_to_do(tmp_path):
    got = safe_archive.safe_rmtree(str(tmp_path / "gone"), allowed_roots=[str(tmp_path)])
    assert got == {"removed": 0, "links_unlinked": 0}


def test_a_held_file_is_retried_then_reported(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    (allowed / "app").mkdir(parents=True)
    held = allowed / "app" / "held.node"
    held.write_bytes(b"x")
    sleeps = []
    with open(held, "rb"):  # Windows refuses to delete an open file
        with pytest.raises(OSError):
            safe_archive.safe_rmtree(str(allowed / "app"), allowed_roots=[str(allowed)],
                                     _retry_seconds=2.0, _sleeper=sleeps.append)
    assert sleeps and all(s == safe_archive.LOCK_RETRY_STEP for s in sleeps)
    got = safe_archive.safe_rmtree(str(allowed / "app"), allowed_roots=[str(allowed)])
    assert got["removed"] == 2 and not (allowed / "app").exists()


# -- a folder swapped for a junction mid-walk (the CVE-2022-21658 race) ---------
#
# Each test does the swap at the exact moment a path-based walk would be
# fooled: after the walk has looked at a folder and before it lists or deletes
# through it. The walk opens every child relative to its parent's open handle,
# so the junction put in its place is never entered.

def _vault_intact(vault):
    return ((vault / "secret.txt").read_text() == "do not touch"
            and (vault / "deeper" / "key.bin").read_bytes() == b"\x01\x02")


def test_a_folder_swapped_for_a_junction_before_it_is_listed(tree, monkeypatch):
    allowed, app, vault = tree
    real_list = safe_archive._win_list  # noqa: SLF001
    swapped = []

    def list_then_swap(h):
        if not swapped and safe_archive._win_final_path(h).endswith("\\sub"):  # noqa: SLF001
            # The walk holds a handle on app\sub and has checked it is a plain
            # folder. Now move it away and put a junction to the vault there.
            os.rename(app / "sub", app / "sub-moved")
            _junction(app / "sub", vault)
            swapped.append(True)
        return real_list(h)

    monkeypatch.setattr(safe_archive, "_win_list", list_then_swap)
    got = safe_archive.safe_rmtree(str(app), allowed_roots=[str(allowed)])
    assert swapped == [True]
    assert _vault_intact(vault)
    assert not app.exists()
    assert got["links_unlinked"] >= 2  # the planted "link" and the swapped-in "sub"


def test_a_file_swapped_for_a_junction_before_it_is_opened(tree, monkeypatch):
    allowed, app, vault = tree
    real_open = safe_archive._win_open_child  # noqa: SLF001
    swapped = []

    def swap_then_open(parent, name):
        if name == "f1.txt" and not swapped:
            os.unlink(app / "f1.txt")
            _junction(app / "f1.txt", vault)
            swapped.append(True)
        return real_open(parent, name)

    monkeypatch.setattr(safe_archive, "_win_open_child", swap_then_open)
    got = safe_archive.safe_rmtree(str(app), allowed_roots=[str(allowed)])
    assert swapped == [True]
    assert _vault_intact(vault)
    assert not app.exists()
    assert got["links_unlinked"] == 2


def test_the_root_swapped_for_a_junction_after_the_checks_is_refused(tree, monkeypatch):
    allowed, app, vault = tree
    real_open = safe_archive._win_open_root  # noqa: SLF001

    def swap_then_open(path):
        os.rename(app, allowed / "app-moved")
        _junction(app, vault)
        return real_open(path)

    monkeypatch.setattr(safe_archive, "_win_open_root", swap_then_open)
    with pytest.raises(SafeArchiveError) as exc:
        safe_archive.safe_rmtree(str(app), allowed_roots=[str(allowed)])
    assert exc.value.code == "is_link"
    assert _vault_intact(vault)


def test_the_root_moved_outside_the_allowed_roots_after_the_checks_is_refused(
        tree, tmp_path, monkeypatch):
    allowed, app, vault = tree
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_text("keep")
    real_open = safe_archive._win_open_root  # noqa: SLF001
    moved = []

    def swap_then_open(path):
        # Stand-in for any trick that makes the open land somewhere other
        # than the path that was checked: the handle's own location is
        # checked again, and a folder outside the allowed roots is refused.
        moved.append(path)
        return real_open(str(elsewhere))

    monkeypatch.setattr(safe_archive, "_win_open_root", swap_then_open)
    with pytest.raises(SafeArchiveError) as exc:
        safe_archive.safe_rmtree(str(app), allowed_roots=[str(allowed)])
    assert exc.value.code == "outside_roots"
    assert (elsewhere / "keep.txt").read_text() == "keep"
    assert _vault_intact(vault)
