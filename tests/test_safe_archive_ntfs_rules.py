"""safe_archive: unpacking a stranger's archive with Windows (NTFS) rules.

Every hazardous member name is built into a real zip AND a real tar here,
unpacked into a scratch apps folder, and must be skipped with a reason while
the ordinary file next to it lands. Whole-archive refusals (bombs, file
caps), the destination rule and the apps-folder rule are tested too.
"""

from __future__ import annotations

import io
import os
import stat
import tarfile
import zipfile

import pytest

from puffpai.security import safe_archive
from puffpai.security.safe_archive import SafeArchiveError

LONG = "d/" * 119 + "abc"  # 241 characters


@pytest.fixture
def apps(tmp_path, monkeypatch):
    root = tmp_path / "apps"
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", str(root))
    return root


def _zip(path, entries):
    """entries: list of (name, data | None, extra) ; extra = external mode."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data, mode in entries:
            info = zipfile.ZipInfo("placeholder")
            info.filename = name          # exactly as given, backslashes too
            info.orig_filename = name
            info.compress_type = zipfile.ZIP_DEFLATED
            if mode is not None:
                info.external_attr = mode << 16
            zf.writestr(info, data if data is not None else b"")
    return str(path)


def _tar(path, entries, mode="w:gz"):
    """entries: list of (name, data | None, type, linkname)."""
    with tarfile.open(path, mode) as tf:
        for name, data, kind, link in entries:
            info = tarfile.TarInfo(name)
            info.type = kind
            if link:
                info.linkname = link
            if kind == tarfile.REGTYPE:
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
            else:
                tf.addfile(info)
    return str(path)


def _files_under(root):
    out = []
    for base, _dirs, files in os.walk(root):
        for f in files:
            out.append(os.path.relpath(os.path.join(base, f), root).replace("\\", "/"))
    return sorted(out)


BAD_NAMES = [
    "a\\b.js", "x:stream", "C:evil", "CON.txt", "nul", "com1.js", "LPT9",
    "conin$", "sub/AUX.json", "trail.", "trail ", "dir./f.js", "\x07bell",
    LONG, "/etc/passwd", "../evil.txt", "a/../../evil.txt", "a/./b.js",
    "a//b.js", "q?.js", "pipe|.js",
    # beyond the pinned list: COM0/LPT0 are reserved
    # in current Windows naming rules; format characters can disguise a name.
    "COM0.js", "lpt0", "sub/Com0.txt", "evil‮gnp.js", "a​b.js",
    "﻿x.js", "a\u0085b.js", "x y.js", "rtl⁧/f.js",
]


@pytest.mark.parametrize("bad", BAD_NAMES, ids=[repr(n)[:30] for n in BAD_NAMES])
@pytest.mark.parametrize("fmt", ["zip", "tar"])
def test_each_ntfs_rule_skips_the_member(apps, tmp_path, bad, fmt):
    assert safe_archive.check_member_name(bad) is not None
    if fmt == "zip":
        arc = _zip(tmp_path / "a.zip", [("ok.txt", b"fine", None),
                                        (bad, b"bad", None)])
    else:
        arc = _tar(tmp_path / "a.tgz", [("ok.txt", b"fine", tarfile.REGTYPE, None),
                                        (bad, b"bad", tarfile.REGTYPE, None)])
    dest = apps / "one"
    got = safe_archive.extract(arc, str(dest), max_total_bytes=10_000)
    assert got["files"] == 1 and got["bytes"] == 4
    assert _files_under(dest) == ["ok.txt"]
    assert len(got["skipped"]) == 1 and got["skipped"][0]["why"]
    # nothing landed anywhere else under the scratch folder
    assert not (tmp_path / "evil.txt").exists()
    assert not (apps / "evil.txt").exists()


@pytest.mark.parametrize("ok", ["README.md", "src/app.js", "a.b.c/d-e_f.json",
                                "Console.js", "nullish.txt", "com10.js", "lpt.txt",
                                "café.js", "日本/読む.md"])
def test_ordinary_names_pass(ok):
    assert safe_archive.check_member_name(ok) is None


@pytest.mark.parametrize("fmt", ["zip", "tar"])
def test_case_duplicates_keep_the_first(apps, tmp_path, fmt):
    if fmt == "zip":
        arc = _zip(tmp_path / "a.zip", [("A.js", b"first", None),
                                        ("a.js", b"second", None)])
    else:
        arc = _tar(tmp_path / "a.tgz", [("A.js", b"first", tarfile.REGTYPE, None),
                                        ("a.js", b"second", tarfile.REGTYPE, None)])
    got = safe_archive.extract(arc, str(apps / "x"), max_total_bytes=1000)
    assert got["files"] == 1
    assert _files_under(apps / "x") == ["A.js"]
    assert (apps / "x" / "A.js").read_bytes() == b"first"
    assert got["skipped"][0]["name"] == "a.js"


def test_tar_links_devices_and_fifos_are_skipped(apps, tmp_path):
    arc = _tar(tmp_path / "a.tgz", [
        ("ok.txt", b"fine", tarfile.REGTYPE, None),
        ("sym", None, tarfile.SYMTYPE, "../../outside"),
        ("hard", None, tarfile.LNKTYPE, "ok.txt"),
        ("dev", None, tarfile.CHRTYPE, None),
        ("blk", None, tarfile.BLKTYPE, None),
        ("fifo", None, tarfile.FIFOTYPE, None),
    ])
    got = safe_archive.extract(arc, str(apps / "x"), max_total_bytes=1000)
    assert got["files"] == 1 and _files_under(apps / "x") == ["ok.txt"]
    assert sorted(s["name"] for s in got["skipped"]) == [
        "blk", "dev", "fifo", "hard", "sym"]


def test_zip_symlinks_and_devices_are_skipped(apps, tmp_path):
    arc = _zip(tmp_path / "a.zip", [
        ("ok.txt", b"fine", None),
        ("sym", b"../../outside", stat.S_IFLNK | 0o777),
        ("dev", b"", stat.S_IFCHR | 0o600),
        ("fifo", b"", stat.S_IFIFO | 0o600),
    ])
    got = safe_archive.extract(arc, str(apps / "x"), max_total_bytes=1000)
    assert _files_under(apps / "x") == ["ok.txt"]
    assert sorted(s["name"] for s in got["skipped"]) == ["dev", "fifo", "sym"]


def test_github_top_folder_is_stripped(apps, tmp_path):
    arc = _tar(tmp_path / "a.tgz", [
        ("proj-0123abc", None, tarfile.DIRTYPE, None),
        ("proj-0123abc/README.md", b"# hi", tarfile.REGTYPE, None),
        ("proj-0123abc/src/app.js", b"x=1", tarfile.REGTYPE, None),
    ])
    got = safe_archive.extract(arc, str(apps / "x"), max_total_bytes=1000)
    assert got["files"] == 2
    assert _files_under(apps / "x") == ["README.md", "src/app.js"]
    got = safe_archive.extract(arc, str(apps / "y"), max_total_bytes=1000,
                               strip_top=False)
    assert _files_under(apps / "y") == ["proj-0123abc/README.md",
                                        "proj-0123abc/src/app.js"]


def test_a_zip_bomb_is_refused_whole(apps, tmp_path):
    arc = tmp_path / "bomb.zip"
    with zipfile.ZipFile(arc, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("ok.txt", b"fine")
        zf.writestr("zeros.bin", b"\0" * 2_000_000)
    dest = apps / "x"
    with pytest.raises(SafeArchiveError) as exc:
        safe_archive.extract(str(arc), str(dest), max_total_bytes=50_000_000)
    assert exc.value.code == "bomb"
    assert not dest.exists() or _files_under(dest) == []


def test_the_ratio_line_is_100():
    class M:
        def __init__(self, size, packed):
            self.kind, self.size, self.packed, self.name = "file", size, packed, "m"
    safe_archive._check_whole("zip", [M(10_000, 100)], 1, 10**9, 10)   # 100: kept
    with pytest.raises(SafeArchiveError) as exc:
        safe_archive._check_whole("zip", [M(10_100, 100)], 1, 10**9, 10)  # 101
    assert exc.value.code == "bomb"


def test_a_compressed_tar_bomb_is_refused_whole(apps, tmp_path):
    arc = _tar(tmp_path / "bomb.tgz", [("zeros.bin", b"\0" * 3_000_000,
                                        tarfile.REGTYPE, None)])
    with pytest.raises(SafeArchiveError) as exc:
        safe_archive.extract(arc, str(apps / "x"), max_total_bytes=50_000_000)
    assert exc.value.code == "bomb"


@pytest.mark.parametrize("fmt", ["zip", "tar"])
def test_the_file_cap_refuses_whole(apps, tmp_path, fmt):
    names = [f"f{i}.txt" for i in range(5)]
    if fmt == "zip":
        arc = _zip(tmp_path / "a.zip", [(n, b"x", None) for n in names])
    else:
        arc = _tar(tmp_path / "a.tgz", [(n, b"x", tarfile.REGTYPE, None) for n in names])
    with pytest.raises(SafeArchiveError) as exc:
        safe_archive.extract(arc, str(apps / "x"), max_total_bytes=1000, max_files=4)
    assert exc.value.code == "too_many_files"
    assert not (apps / "x").exists()


def test_a_huge_zip_name_list_is_refused_before_it_is_read(apps, tmp_path, monkeypatch):
    arc = tmp_path / "many.zip"
    with zipfile.ZipFile(arc, "w", zipfile.ZIP_STORED) as zf:
        for i in range(1100):
            zf.writestr(f"f{i}", b"")
    opened = []
    real = zipfile.ZipFile

    def spy(*a, **k):
        opened.append(a)
        return real(*a, **k)
    monkeypatch.setattr(safe_archive.zipfile, "ZipFile", spy)
    with pytest.raises(SafeArchiveError) as exc:
        safe_archive.extract(str(arc), str(apps / "x"), max_total_bytes=1000,
                             max_files=1)
    assert exc.value.code == "too_many_files" and opened == []


@pytest.mark.parametrize("fmt", ["zip", "tar"])
def test_the_size_cap_refuses_whole(apps, tmp_path, fmt):
    data = os.urandom(3000)
    if fmt == "zip":
        arc = _zip(tmp_path / "a.zip", [("a.bin", data, None)])
    else:
        arc = _tar(tmp_path / "a.tar", [("a.bin", data, tarfile.REGTYPE, None)], mode="w")
    with pytest.raises(SafeArchiveError) as exc:
        safe_archive.extract(arc, str(apps / "x"), max_total_bytes=2000)
    assert exc.value.code == "too_big"


def test_a_destination_outside_the_apps_folder_is_refused(apps, tmp_path):
    arc = _zip(tmp_path / "a.zip", [("ok.txt", b"fine", None)])
    for dest in (tmp_path / "elsewhere", apps, apps / ".." / "sneaky"):
        with pytest.raises(SafeArchiveError) as exc:
            safe_archive.extract(arc, str(dest), max_total_bytes=1000)
        assert exc.value.code == "outside_root"
    assert not (tmp_path / "elsewhere").exists()
    assert not (tmp_path / "sneaky").exists()


def test_a_destination_with_things_in_it_is_refused(apps, tmp_path):
    arc = _zip(tmp_path / "a.zip", [("ok.txt", b"fine", None)])
    (apps / "x").mkdir(parents=True)
    (apps / "x" / "mine.txt").write_bytes(b"keep")
    with pytest.raises(SafeArchiveError):
        safe_archive.extract(arc, str(apps / "x"), max_total_bytes=1000)
    assert (apps / "x" / "mine.txt").read_bytes() == b"keep"


def test_not_an_archive_is_refused(apps, tmp_path):
    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"this is not an archive at all")
    with pytest.raises(SafeArchiveError) as exc:
        safe_archive.extract(str(junk), str(apps / "x"), max_total_bytes=1000)
    assert exc.value.code == "not_archive"


def test_extraction_root_under_the_state_folder_is_refused(monkeypatch):
    state = os.environ["PUFFPAI_STATE_DIR"]
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", os.path.join(state, "apps"))
    with pytest.raises(SafeArchiveError) as exc:
        safe_archive.extraction_root()
    assert exc.value.code == "bad_root"
    assert not os.path.exists(os.path.join(state, "apps"))


def test_extraction_root_holding_the_state_or_vault_is_refused(monkeypatch):
    from puffpai.security.vault import _default_vault_dir
    state = os.environ["PUFFPAI_STATE_DIR"]
    for root in (os.path.dirname(state), os.path.dirname(_default_vault_dir()),
                 _default_vault_dir(), _default_vault_dir() + ".inbox"):
        monkeypatch.setenv("PUFFPAI_APPS_ROOT", root)
        with pytest.raises(SafeArchiveError):
            safe_archive.extraction_root()


def test_extraction_root_is_created_and_used(apps):
    got = safe_archive.extraction_root()
    assert os.path.isdir(got)
    assert os.path.normcase(got) == os.path.normcase(os.path.realpath(apps))


def test_the_windows_default_is_not_inside_the_default_state(monkeypatch, tmp_path):
    monkeypatch.delenv("PUFFPAI_APPS_ROOT", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(safe_archive.sys, "platform", "win32")
    default_state = os.path.join(str(tmp_path / "local"), "PuffPAI")
    root = safe_archive.configured_root()
    assert not (os.path.normcase(root) + os.sep).startswith(
        os.path.normcase(default_state) + os.sep)


def test_the_default_apps_folder_is_a_sibling_of_the_default_state(monkeypatch, tmp_path):
    """Recorded interface deviation.

    The spec pins the Windows default as %LOCALAPPDATA%/PuffPAI/apps, but
    %LOCALAPPDATA%/PuffPAI is the default state folder (config._user_state_root),
    and the same spec says the apps folder is never inside the state folder.
    The default is therefore the sibling %LOCALAPPDATA%/PuffPAI-apps. Callers
    must ask extraction_root() and never hard-code either path.
    """
    import inspect
    from puffpai import config
    local = str(tmp_path / "local")
    # config._user_state_root on Windows is %LOCALAPPDATA%/PuffPAI (read from
    # its source: the suite may not resolve the real per-user folder).
    assert 'os.path.join(base, "PuffPAI")' in inspect.getsource(config)
    monkeypatch.delenv("PUFFPAI_APPS_ROOT", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", local)
    monkeypatch.setenv("PUFFPAI_STATE_DIR", os.path.join(local, "PuffPAI"))
    monkeypatch.setattr(safe_archive.sys, "platform", "win32")
    assert os.path.normcase(safe_archive.configured_root()) == os.path.normcase(
        os.path.join(local, "PuffPAI-apps"))
    # The spec's literal default, if configured, breaks the spec's own rule.
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", os.path.join(local, "PuffPAI", "apps"))
    with pytest.raises(SafeArchiveError) as exc:
        safe_archive.extraction_root()
    assert exc.value.code == "bad_root"


def test_a_name_holding_bytes_that_were_not_text_is_refused():
    # tarfile keeps undecodable bytes as lone surrogates
    assert safe_archive.check_member_name("x\udc80.js") is not None
