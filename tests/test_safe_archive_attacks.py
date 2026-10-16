"""Hostile archives, end to end.

The acceptance pass read safe_archive but never attacked it (its attack
script was stopped before it ran). Every archive here is built in the test
the way an attacker would build it (raw headers where the standard writers
refuse), unpacked into a scratch apps folder, and must land nothing outside
its folder, overwrite nothing, and keep memory small:

* zip-slip in zip and tar: ``..``, absolute, drive and UNC names, backslashes;
* NTFS: device names, alternate data streams, trailing dots and spaces, and a
  short-name alias that must not overwrite its long twin;
* links: tar symlinks and hard links (then a file written "through" them),
  zip symlinks, and a junction planted in the middle of an unpack;
* bombs: a deflate bomb, a gzip tar bomb, a file bigger than its header says;
* header size: a tar pax header, a GNU long name and a zip entry list that
  would each make the reader hold hundreds of megabytes before any check.

get_archive: a body that never ends and a body larger than it says are cut at
the byte cap while memory stays small, and nothing is left behind.

No network: every download is a fake transport.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import os
import struct
import sys
import tarfile
import tracemalloc
import zipfile
import zlib

import pytest

from puffpai import source_fetch
from puffpai.security import outside_switches, safe_archive
from puffpai.security.safe_archive import SafeArchiveError

needs_windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
MB = 1024 * 1024


@pytest.fixture
def apps(tmp_path, monkeypatch):
    root = tmp_path / "apps"
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", str(root))
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("must survive", encoding="utf-8")
    return {"root": root, "victim": victim, "tmp": tmp_path}


def _dest(apps, name="out"):
    return str(apps["root"] / name)


def _all_files(root) -> list:
    out = []
    for base, _dirs, files in os.walk(root):
        out += [os.path.join(base, f) for f in files]
    return out


def _victim_ok(apps):
    v = apps["victim"]
    assert sorted(os.listdir(v)) == ["keep.txt"]
    assert (v / "keep.txt").read_text(encoding="utf-8") == "must survive"
    # Nothing landed beside the apps folder either.
    assert {p.name for p in apps["tmp"].iterdir()} <= {
        "apps", "victim", "a.zip", "a.tar", "a.tgz", "a.bin", "dl"}


# ── raw writers (the standard ones refuse the worst names) ──────────────────

def _raw_zip(entries, *, method=zipfile.ZIP_STORED) -> bytes:
    """entries: (name, data, unix_mode or None). Names written byte for byte."""
    out = io.BytesIO()
    central = []
    for name, data, mode in entries:
        raw = name.encode("utf-8")
        body = zlib.compress(data)[2:-4] if method == zipfile.ZIP_DEFLATED else data
        crc = zlib.crc32(data) & 0xFFFFFFFF
        offset = out.tell()
        out.write(struct.pack("<4s5H3L2H", b"PK\x03\x04", 20, 0x800, method, 0, 0,
                              crc, len(body), len(data), len(raw), 0))
        out.write(raw)
        out.write(body)
        attr = ((mode or 0) << 16) & 0xFFFFFFFF
        central.append(struct.pack("<4s6H3L5H2L", b"PK\x01\x02", 0x031E, 20, 0x800,
                                   method, 0, 0, crc, len(body), len(data), len(raw),
                                   0, 0, 0, 0, attr, offset) + raw)
    cd_start = out.tell()
    for c in central:
        out.write(c)
    cd_size = out.tell() - cd_start
    out.write(struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, len(central), len(central),
                          cd_size, cd_start, 0))
    return out.getvalue()


def _tar_member(name, data=b"", kind=tarfile.REGTYPE, link="") -> bytes:
    info = tarfile.TarInfo("x")
    info.name = name
    info.type = kind
    info.linkname = link
    info.size = len(data) if kind == tarfile.REGTYPE else 0
    head = info.tobuf(format=tarfile.GNU_FORMAT, encoding="utf-8", errors="strict")
    pad = (-len(data)) % 512 if kind == tarfile.REGTYPE else 0
    return head + (data + b"\0" * pad if kind == tarfile.REGTYPE else b"")


def _tar_bytes(members, *, gz=True) -> bytes:
    raw = b"".join(members) + b"\0" * 1024
    return gzip.compress(raw, 6) if gz else raw


def _write(apps, name, data) -> str:
    path = apps["tmp"] / name
    path.write_bytes(data)
    return str(path)


def _unpack(apps, path, **kw):
    kw.setdefault("max_total_bytes", 50 * MB)
    kw.setdefault("max_files", 20000)
    kw.setdefault("strip_top", False)
    return safe_archive.extract(path, _dest(apps), **kw)


# ── zip-slip ─────────────────────────────────────────────────────────────────

SLIP = ["../escape.txt", "a/../../escape.txt", "..\\escape.txt", "/escape.txt",
        "C:/escape.txt", "C:escape.txt", "\\\\server\\share\\escape.txt",
        "a/..\\..\\escape.txt", "./../escape.txt", "a/b/../../../escape.txt"]


@pytest.mark.parametrize("fmt", ["zip", "tar"])
def test_zip_slip_names_never_land_outside(apps, fmt):
    entries = [(n, b"pwned", None) for n in SLIP] + [("ok.txt", b"fine", None)]
    if fmt == "zip":
        path = _write(apps, "a.zip", _raw_zip(entries))
    else:
        path = _write(apps, "a.tgz", _tar_bytes([_tar_member(n, d) for n, d, _m in entries]))
    got = _unpack(apps, path)
    assert got["files"] == 1
    assert [os.path.basename(p) for p in _all_files(apps["root"])] == ["ok.txt"]
    assert len(got["skipped"]) == len(SLIP)
    assert not (apps["tmp"] / "escape.txt").exists()
    _victim_ok(apps)


# ── NTFS names ───────────────────────────────────────────────────────────────

NTFS = ["CON", "con.txt", "NUL.json", "aux", "PRN.md", "COM1", "com9.js", "LPT1",
        "lpt9.txt", "COM0", "CONIN$", "CONOUT$", "file.txt:hidden", "file.txt::$DATA",
        "dir:stream/x.txt", "trailing.", "trailing ", "d./x.txt", "d /x.txt",
        "a<b.txt", "a>b.txt", 'a"b.txt', "a|b.txt", "a?b.txt", "a*b.txt"]


@pytest.mark.parametrize("fmt", ["zip", "tar"])
def test_ntfs_device_names_and_streams_are_skipped(apps, fmt):
    entries = [(n, b"x", None) for n in NTFS] + [("file.txt", b"real", None)]
    if fmt == "zip":
        path = _write(apps, "a.zip", _raw_zip(entries))
    else:
        path = _write(apps, "a.tgz", _tar_bytes([_tar_member(n, d) for n, d, _m in entries]))
    got = _unpack(apps, path)
    files = _all_files(apps["root"])
    assert [os.path.basename(p) for p in files] == ["file.txt"]
    with open(files[0], "rb") as fh:
        assert fh.read() == b"real"                     # no stream written into it
    assert len(got["skipped"]) == len(NTFS)
    _victim_ok(apps)


@needs_windows
def test_a_short_name_alias_never_overwrites_its_long_twin(apps):
    entries = [("longfilename.txt", b"first", None), ("LONGFI~1.TXT", b"second", None)]
    path = _write(apps, "a.zip", _raw_zip(entries))
    _unpack(apps, path)
    with open(os.path.join(_dest(apps), "longfilename.txt"), "rb") as fh:
        assert fh.read() == b"first"


# ── links ────────────────────────────────────────────────────────────────────

def test_a_tar_link_then_a_file_through_it_lands_inside(apps):
    victim = str(apps["victim"])
    members = [_tar_member("lnk", kind=tarfile.SYMTYPE, link=victim),
               _tar_member("lnk/keep.txt", b"overwritten"),
               _tar_member("hard", kind=tarfile.LNKTYPE, link=os.path.join(victim, "keep.txt")),
               _tar_member("hard", b"through the hard link"),
               _tar_member("up", kind=tarfile.SYMTYPE, link="../../victim"),
               _tar_member("up/new.txt", b"x")]
    path = _write(apps, "a.tgz", _tar_bytes(members))
    got = _unpack(apps, path)
    whys = {s["name"]: s["why"] for s in got["skipped"]}
    assert whys["lnk"] == "it is a link to somewhere else"
    assert whys["hard"] == "it is a second name for another file"
    for p in _all_files(apps["root"]):
        assert not os.path.islink(p)
        assert os.path.realpath(p).startswith(os.path.realpath(_dest(apps)))
    _victim_ok(apps)


def test_a_zip_symlink_is_never_made(apps):
    entries = [("lnk", str(apps["victim"]).encode(), 0o120777),
               ("lnk/keep.txt", b"overwritten", None)]
    path = _write(apps, "a.zip", _raw_zip(entries))
    got = _unpack(apps, path)
    assert any(s["name"] == "lnk" for s in got["skipped"])
    assert not os.path.islink(os.path.join(_dest(apps), "lnk"))
    _victim_ok(apps)


@needs_windows
def test_a_junction_planted_during_the_unpack_is_caught(apps, monkeypatch):
    """A local program swaps a folder being filled for a junction to another
    place: the real-path check after the folder is made refuses the whole
    unpack, removes what was written, and the other place is untouched."""
    import _winapi
    victim = str(apps["victim"])
    real_makedirs = os.makedirs
    planted = []

    def racing_makedirs(path, *a, **k):
        real_makedirs(path, *a, **k)
        if os.path.basename(path) == "sub" and not planted:
            os.rmdir(path)
            _winapi.CreateJunction(victim, path)
            planted.append(path)
    monkeypatch.setattr(safe_archive.os, "makedirs", racing_makedirs)
    path = _write(apps, "a.zip", _raw_zip([("sub/keep.txt", b"overwritten", None)]))
    with pytest.raises(SafeArchiveError) as caught:
        _unpack(apps, path)
    assert caught.value.code == "escaped" and planted
    _victim_ok(apps)


# ── bombs ────────────────────────────────────────────────────────────────────

def test_a_deflate_bomb_is_refused_whole_with_little_memory(apps):
    big = b"\0" * (40 * MB)
    body = zlib.compress(big, 9)[2:-4]
    crc = zlib.crc32(big) & 0xFFFFFFFF
    raw = io.BytesIO()
    raw.write(struct.pack("<4s5H3L2H", b"PK\x03\x04", 20, 0, 8, 0, 0, crc, len(body),
                          len(big), 5, 0) + b"b.bin" + body)
    cd = struct.pack("<4s6H3L5H2L", b"PK\x01\x02", 0x031E, 20, 0, 8, 0, 0, crc,
                     len(body), len(big), 5, 0, 0, 0, 0, 0, 0) + b"b.bin"
    start = raw.tell()
    raw.write(cd)
    raw.write(struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 1, 1, len(cd), start, 0))
    path = _write(apps, "a.zip", raw.getvalue())
    del big
    tracemalloc.start()
    try:
        with pytest.raises(SafeArchiveError) as caught:
            _unpack(apps, path, max_total_bytes=100 * MB)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert caught.value.code == "bomb"
    assert peak < 8 * MB, peak
    assert not os.path.exists(_dest(apps)) or not _all_files(apps["root"])


def test_a_gzip_tar_bomb_is_refused_whole(apps):
    data = b"\0" * (30 * MB)
    path = _write(apps, "a.tgz", _tar_bytes([_tar_member("z.bin", data)]))
    with pytest.raises(SafeArchiveError) as caught:
        _unpack(apps, path, max_total_bytes=100 * MB)
    assert caught.value.code == "bomb"
    assert not _all_files(apps["root"])


def test_a_zip_file_bigger_than_its_header_says_is_refused(apps):
    real = b"A" * 200_000
    body = zlib.compress(real)[2:-4]
    crc = zlib.crc32(real) & 0xFFFFFFFF
    raw = io.BytesIO()
    # The headers claim 10 bytes; the stream holds 200 000.
    raw.write(struct.pack("<4s5H3L2H", b"PK\x03\x04", 20, 0, 8, 0, 0, crc, len(body),
                          10, 5, 0) + b"l.bin" + body)
    cd = struct.pack("<4s6H3L5H2L", b"PK\x01\x02", 0x031E, 20, 0, 8, 0, 0, crc,
                     len(body), 10, 5, 0, 0, 0, 0, 0, 0) + b"l.bin"
    start = raw.tell()
    raw.write(cd)
    raw.write(struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 1, 1, len(cd), start, 0))
    path = _write(apps, "a.zip", raw.getvalue())
    with pytest.raises(SafeArchiveError):
        _unpack(apps, path)
    assert not _all_files(apps["root"])


# ── header size: memory before any check ────────────────────────────────────

def _huge_header_tar(kind: bytes, size: int) -> bytes:
    """A gzip tar whose first header is a *kind* extension of *size* bytes
    (zeros compress to almost nothing), then one ordinary file."""
    info = tarfile.TarInfo("x")
    info.name = "././@LongLink" if kind in (b"L", b"K") else "pax"
    info.type = kind
    info.size = size
    head = info.tobuf(format=tarfile.USTAR_FORMAT)
    comp = zlib.compressobj(6, zlib.DEFLATED, 31)
    out = [comp.compress(head)]
    zeros = b"\0" * MB
    left = size + ((-size) % 512)
    while left > 0:
        n = min(left, MB)
        out.append(comp.compress(zeros[:n]))
        left -= n
    out.append(comp.compress(_tar_member("ok.txt", b"fine") + b"\0" * 1024))
    out.append(comp.flush())
    return b"".join(out)


@pytest.mark.parametrize("kind", [tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME,
                                  tarfile.GNUTYPE_LONGLINK])
def test_a_huge_tar_extension_header_is_refused_with_little_memory(apps, kind):
    path = _write(apps, "a.tgz", _huge_header_tar(kind, 64 * MB))
    tracemalloc.start()
    try:
        with pytest.raises(SafeArchiveError):
            _unpack(apps, path, max_total_bytes=500 * MB)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 8 * MB, f"{peak / MB:.0f} MB held for one header"
    assert not _all_files(apps["root"])


def test_a_sparse_tar_member_is_refused(apps):
    info = tarfile.TarInfo("x")
    info.name = "sparse.bin"
    info.type = tarfile.GNUTYPE_SPARSE
    info.size = 0
    head = bytearray(info.tobuf(format=tarfile.GNU_FORMAT))
    path = _write(apps, "a.tgz", gzip.compress(bytes(head) + b"\0" * 1024))
    with pytest.raises(SafeArchiveError):
        _unpack(apps, path)


def _lying_zip(real_entries: int, claimed: int) -> bytes:
    """A zip whose end record claims *claimed* entries while its list holds
    *real_entries* minimal ones (no file data is needed to read the list)."""
    cd = io.BytesIO()
    for i in range(real_entries):
        name = b"%x" % i
        cd.write(struct.pack("<4s6H3L5H2L", b"PK\x01\x02", 0x031E, 20, 0, 0, 0, 0, 0,
                             0, 0, len(name), 0, 0, 0, 0, 0, 0) + name)
    body = cd.getvalue()
    return body + struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, claimed & 0xFFFF,
                              claimed & 0xFFFF, len(body), 0, 0)


def test_a_zip_list_that_lies_about_its_count_is_refused_before_it_is_read(apps):
    path = _write(apps, "a.zip", _lying_zip(300_000, 1))
    tracemalloc.start()
    try:
        with pytest.raises(SafeArchiveError) as caught:
            _unpack(apps, path, max_files=20000)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert caught.value.code == "too_many_files"
    assert peak < 16 * MB, f"{peak / MB:.0f} MB held for the list"


def test_an_honest_zip_list_still_opens(apps):
    entries = [(f"f{i}.txt", b"x", None) for i in range(300)]
    path = _write(apps, "a.zip", _raw_zip(entries))
    assert _unpack(apps, path)["files"] == 300


def test_a_zip_with_bytes_before_it_still_opens(apps):
    """Self-extracting style: a stub before the zip (the entry list's offsets
    are relative to the zip, not the file)."""
    data = b"MZ stub " * 100 + _raw_zip([("a.txt", b"x", None)])
    path = _write(apps, "a.zip", data)
    assert _unpack(apps, path)["files"] == 1


# ── get_archive: the download itself ────────────────────────────────────────

class _Endless:
    """A body that never ends (or lies about its length)."""

    def __init__(self, headers=None):
        self.status = 200
        self.headers = dict(headers or {})
        self.sent = 0

    def read(self, n=-1):
        n = 65536 if n is None or n < 0 else n
        self.sent += n
        return b"\0" * n

    def close(self):
        pass


@pytest.fixture
def net(monkeypatch):
    table: dict = {}
    monkeypatch.setattr(source_fetch, "_default_transport",
                        lambda url, headers, timeout: table[url]())
    monkeypatch.setattr(source_fetch, "_default_resolver", lambda h: ["140.82.112.4"])
    monkeypatch.setattr(source_fetch, "HOST_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(source_fetch, "HOST_MIN_INTERVAL_BY_HOST", {})
    source_fetch.clear_caches()
    outside_switches.forget()
    return table


@pytest.mark.parametrize("headers", [{}, {"Content-Length": "100"}])
def test_a_body_that_never_ends_is_cut_at_the_cap_with_little_memory(net, apps, headers):
    url = "https://codeload.github.com/o/r/tar.gz/" + "a" * 40
    body = _Endless(headers)
    net[url] = lambda: body
    dest = str(apps["tmp"] / "dl" / "x.tar.gz")
    tracemalloc.start()
    try:
        with pytest.raises(source_fetch.FetchError) as caught:
            source_fetch.get_archive(url, allowed_hosts={"codeload.github.com"},
                                     max_bytes=20 * MB, dest_path=dest,
                                     service="code_download")
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert caught.value.code == "too_big"
    assert body.sent <= 20 * MB + 2 * source_fetch.CHUNK_BYTES
    assert peak < 4 * MB, peak
    assert not os.path.exists(dest) and not os.path.exists(dest + ".part")


def test_a_pinned_download_that_differs_leaves_nothing(net, apps):
    url = "https://codeload.github.com/o/r/tar.gz/" + "b" * 40
    good = gzip.compress(b"archive")

    class R:
        status, headers = 200, {}

        def __init__(self):
            self.left = good + b"extra"

        def read(self, n=-1):
            out, self.left = self.left[:n], self.left[n:]
            return out

        def close(self):
            pass
    net[url] = R
    dest = str(apps["tmp"] / "dl" / "y.tar.gz")
    with pytest.raises(source_fetch.FetchError) as caught:
        source_fetch.get_archive(url, allowed_hosts={"codeload.github.com"},
                                 max_bytes=MB, dest_path=dest, service="code_download",
                                 expect_sha256=hashlib.sha256(good).hexdigest())
    assert caught.value.code == "sha_mismatch"
    assert not os.path.exists(dest) and not os.path.exists(dest + ".part")


# ── every archive download is pinned (acceptance finding 3) ────────────────

def test_every_get_archive_call_names_its_fingerprint_argument():
    """get_archive accepts expect_sha256=None. Every caller must pass the
    argument on purpose: the box's own download passes a constant, the
    reading helper passes a constant, and integrate.fetch passes the recorded
    fingerprint when there is one (the first download of a project has only
    the commit the plan pinned, which fetch then finds inside the archive)."""
    import ast
    src = os.path.join(os.path.dirname(__file__), os.pardir, "src", "puffpai")
    calls = []
    for base, _dirs, files in os.walk(src):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(base, name)
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    fn = node.func
                    called = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                    if called == "get_archive":
                        calls.append((os.path.relpath(path, src),
                                      {k.arg for k in node.keywords}))
    assert calls, "no get_archive call found"
    for where, kws in calls:
        assert "expect_sha256" in kws, where
    assert {w.replace(os.sep, "/") for w, _k in calls} == {
        "box/driver.py", "finding/app_catalog.py", "integrate/fetch.py", "wizard/needs.py"}
