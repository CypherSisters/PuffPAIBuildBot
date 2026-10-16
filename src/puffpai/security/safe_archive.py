"""Unpacking downloaded code, and removing folders, without trusting either.

BuildBot downloads other people's projects (a GitHub or Codeberg archive at a
pinned version). An archive is a list of names and bytes chosen by a stranger,
so every name is checked before a byte lands, and everything lands in ONE
folder that holds nothing else of ours: the apps folder
(:func:`extraction_root`), outside the state folder, the vault, the vault's
inbox and the machine key's folder.

What :func:`extract` refuses, member by member (listed in ``skipped`` with a
plain reason, see :func:`check_member_name`):

* absolute paths, drive letters, ``..``, backslashes, and a colon anywhere
  (alternate data streams like ``x:stream``, drive-relative names like
  ``C:evil``);
* Windows device names in any folder or file name, with or without an
  extension, trailing spaces or dots (``CON``, ``nul``, ``com1.js``,
  ``COM0``, ``CONIN$``...), and a name ending in a dot or a space (Windows
  drops them, so ``a.js.`` would land on ``a.js``);
* control characters and other hidden characters (the right-to-left
  override that can make a name read differently on screen, zero-width
  marks, bytes that were not text), and names Windows cannot hold
  (``<>"|?*``);
* a member path longer than 240 characters;
* a second name that differs from an earlier one only in letter case
  (``A.js`` next to ``a.js``): the first is kept;
* anything that is not a regular file: symlinks, hard links, devices, FIFOs.

And for the whole archive, before anything is written (a zip bomb costs
nothing): more than ``max_total_bytes`` expanded, more than ``max_files``
files, or any zip member that expands more than 100 times its packed size
(for a compressed tar, the whole archive against its file size) refuses the
lot. Writing then counts real bytes again, so a member that lies about its
size is cut off and the archive refused, and every file is created new
(never over an existing one) inside ``dest_dir``.

:func:`safe_rmtree` removes a folder the way the owner would expect "remove
this" to work: it never follows a link out of it. A junction or symlink
inside is removed as a link; what it pointed at is left alone. Below the
folder it was given, it never goes by path: each item is opened through its
parent's open handle, so a folder swapped for a junction mid-way is never
entered.

Nothing here runs, imports or opens a downloaded file as anything but bytes.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import os
import stat
import struct
import sys
import tarfile
import time
import unicodedata
import zipfile
from typing import Any, Callable, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

APPS_ROOT_ENV = "PUFFPAI_APPS_ROOT"

#: Longest member path accepted (characters, as written in the archive).
MAX_MEMBER_PATH = 240

#: A zip member (or a compressed tar as a whole) that expands more than this
#: many times its packed size is a bomb, not a project.
MAX_RATIO = 100

#: How long :func:`safe_rmtree` keeps trying a file another program holds.
LOCK_RETRY_SECONDS = 30.0
LOCK_RETRY_STEP = 1.0

_CHUNK = 64 * 1024

#: The largest tar extension header read (a pax header, a GNU long name or
#: long link). The reader holds each one whole in memory before any other
#: check can run, so a compressed header of zeros could make it hold
#: gigabytes. Real ones are a few hundred bytes (git archive writes 52).
MAX_TAR_HEADER_BYTES = 64 * 1024

_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", "CLOCK$"}
    # COM0 and LPT0 too: current Windows naming rules list them as reserved.
    | {f"COM{i}" for i in range(0, 10)} | {f"LPT{i}" for i in range(0, 10)}
    # Windows treats the superscript digits as device numbers too.
    | {f"COM{d}" for d in "¹²³"}
    | {f"LPT{d}" for d in "¹²³"})
_WINDOWS_BAD = frozenset('<>"|?*')
#: Unicode kinds a name may not hold: control and format characters (the
#: right-to-left override U+202E can make "evil.exe" read as something
#: else on screen; zero-width marks make two names look alike), lone
#: surrogates (bytes that were not text), and line or paragraph separators.
_HIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})

FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_DIRECTORY = 0x10


class SafeArchiveError(ValueError):
    """Refused. ``code`` is one word; ``message`` is plain words."""

    def __init__(self, code: str, message: str, **counts: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.counts = counts


# -- where things land ------------------------------------------------------

def _real(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _inside(root: str, candidate: str) -> bool:
    """True when *candidate* is *root* or below it (both already real)."""
    root = root.rstrip("\\/")
    return candidate == root or candidate.startswith(root + os.sep)


def _default_root() -> str:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        # NOT %LOCALAPPDATA%\PuffPAI\apps: %LOCALAPPDATA%\PuffPAI is the
        # default state folder on Windows (config._user_state_root), and the
        # apps folder must never be inside it. A sibling keeps them apart.
        return os.path.join(base, "PuffPAI-apps")
    xdg = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share")
    return os.path.join(xdg, "puffpai", "apps")


def configured_root() -> str:
    """Where the apps folder is configured to be. No checks, nothing created."""
    override = os.environ.get(APPS_ROOT_ENV, "").strip()
    return os.path.abspath(override) if override else os.path.abspath(_default_root())


def secret_roots() -> list[str]:
    """The folders extracted code must never be in (or contain): the state
    folder plus :func:`vault_and_key_roots`."""
    try:
        from puffpai.config import get_state_dir
        state = get_state_dir()
    except Exception as exc:
        raise SafeArchiveError(
            "no_state", "Could not tell where BuildBot keeps its private "
            "files, so nothing was unpacked.") from exc
    return [state, *vault_and_key_roots()]


def vault_and_key_roots() -> list[str]:
    """The vault folder, its inbox, and the folder of each machine key file."""
    roots: list[str] = []
    try:
        from puffpai.security.vault import _default_vault_dir
        vault_dir = os.path.abspath(_default_vault_dir())
        roots.append(vault_dir)
        roots.append(vault_dir.rstrip("\\/") + ".inbox")
    except Exception as exc:
        raise SafeArchiveError(
            "no_vault", "Could not tell where your vault is, so nothing was "
            "unpacked.") from exc
    try:
        from puffpai.security.vault import service_key_candidates
        for _label, keyfile in service_key_candidates():
            roots.append(os.path.dirname(os.path.abspath(str(keyfile))))
    except Exception:
        pass  # no key files configured: nothing to keep away from
    return [r for r in roots if r]


def extraction_root() -> str:
    """The apps folder: ``$PUFFPAI_APPS_ROOT``, else the per-user default.

    Defaults: Windows ``%LOCALAPPDATA%\\PuffPAI-apps``; elsewhere
    ``$XDG_DATA_HOME/puffpai/apps`` (``~/.local/share/puffpai/apps``).

    WHY NOT THE OBVIOUS DEFAULT: the obvious
    Windows default is ``%LOCALAPPDATA%\\PuffPAI\\apps``. That folder is inside
    ``%LOCALAPPDATA%\\PuffPAI``, the default state folder, which this function
    must refuse, so the default is the sibling ``PuffPAI-apps``. Callers (the
    box driver, the web-app runner) must call this function and never
    hard-code either path.

    Created if missing. Raises :class:`SafeArchiveError` when it is (or is
    inside, or contains) the state folder, the vault, the vault's inbox or
    the machine key's folder, or when it is a drive root.
    """
    root = configured_root()
    real = _real(root)
    if _is_drive_root(real):
        raise SafeArchiveError("bad_root", "The apps folder cannot be a whole "
                               "drive. Nothing was unpacked.")
    for secret in secret_roots():
        s = _real(secret)
        if _inside(s, real) or _inside(real, s):
            raise SafeArchiveError(
                "bad_root", "The apps folder is set to a place that holds "
                "BuildBot's private files. Nothing was unpacked there.")
    os.makedirs(root, exist_ok=True)
    if os.path.islink(root) or _is_reparse(root):
        raise SafeArchiveError("bad_root", "The apps folder is a link to "
                               "somewhere else. Nothing was unpacked.")
    return os.path.realpath(root)


# -- member names ------------------------------------------------------------

def check_member_name(name: str) -> Optional[str]:
    """A plain reason *name* may not be unpacked on this computer, or None.

    *name* is the path as written in the archive, with ``/`` between parts.
    A single trailing ``/`` (a folder entry) is allowed.
    """
    if not isinstance(name, str) or not name:
        return "it has no name"
    if any(ord(ch) < 32 or ord(ch) == 127
           or unicodedata.category(ch) in _HIDDEN_CATEGORIES for ch in name):
        return "its name has hidden characters in it"
    if len(name) > MAX_MEMBER_PATH:
        return f"its name is longer than {MAX_MEMBER_PATH} characters"
    if "\\" in name:
        return "its name has a backslash in it"
    if ":" in name:
        return "its name has a colon in it (a drive or a hidden stream)"
    if name.startswith("/"):
        return "it points at a fixed place on the computer"
    body = name[:-1] if name.endswith("/") else name
    parts = body.split("/")
    for part in parts:
        if part == "":
            return "its name has an empty folder in it"
        if part == "..":
            return "it tries to climb out of its folder"
        if part == ".":
            return "its name has a folder called '.' in it"
        if part[-1] in ". ":
            return "a name in it ends with a dot or a space"
        if any(ch in _WINDOWS_BAD for ch in part):
            return "its name has a character Windows cannot use"
        stem = part.split(".", 1)[0].rstrip(" ").upper()
        if stem in _RESERVED:
            return "it uses a name Windows keeps for devices"
    return None


# -- reading archives ----------------------------------------------------------

class _Member:
    __slots__ = ("name", "kind", "size", "packed", "handle")

    def __init__(self, name: str, kind: str, size: int, packed: int, handle: Any):
        self.name = name
        self.kind = kind      # file | dir | link | hardlink | device | fifo | other
        self.size = size
        self.packed = packed
        self.handle = handle


def _zip_members(zf: zipfile.ZipFile) -> list[_Member]:
    out = []
    for info in zf.infolist():
        # The name exactly as the archive wrote it: zipfile turns "\" into "/"
        # on Windows and cuts a name at a null byte, which would hide both.
        name = getattr(info, "orig_filename", None) or info.filename
        mode = (info.external_attr >> 16) & 0xFFFF
        if name.endswith("/"):
            kind = "dir"
        elif mode and stat.S_ISLNK(mode):
            kind = "link"
        elif mode and (stat.S_ISCHR(mode) or stat.S_ISBLK(mode)):
            kind = "device"
        elif mode and stat.S_ISFIFO(mode):
            kind = "fifo"
        elif mode and stat.S_ISSOCK(mode):
            kind = "other"
        else:
            kind = "file"
        out.append(_Member(name, kind, int(info.file_size),
                           int(info.compress_size), info))
    return out


class _HeaderTooBig(tarfile.TarError):
    """A tar extension header over :data:`MAX_TAR_HEADER_BYTES`, or a sparse
    member (its maps are read whole too, and a project never needs one)."""


class _BoundedTarInfo(tarfile.TarInfo):
    """The standard reader's member, with a size check BEFORE an extension
    header is read, and no sparse members at all."""

    def _proc_member(self, tarfile_):
        if self.type in (tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK,
                         tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE) \
                and self.size > MAX_TAR_HEADER_BYTES:
            raise _HeaderTooBig("extension header too big")
        if self.type == tarfile.GNUTYPE_SPARSE:
            raise _HeaderTooBig("sparse member")
        return super()._proc_member(tarfile_)

    def _proc_gnusparse_00(self, *a, **k):
        raise _HeaderTooBig("sparse member")

    def _proc_gnusparse_01(self, *a, **k):
        raise _HeaderTooBig("sparse member")

    def _proc_gnusparse_10(self, *a, **k):
        raise _HeaderTooBig("sparse member")


_HEADER_REFUSAL = ("A header in the download is far bigger than a project "
                   "needs, so nothing was unpacked.")


def _tar_members(tf: tarfile.TarFile, max_total_bytes: int,
                 max_files: int) -> list[_Member]:
    """The members, read one by one with the limits applied AS they are read:
    a compressed tar with a million tiny entries, or one huge one, is refused
    before it is all decompressed or listed in memory."""
    out = []
    total = 0
    files = 0
    for info in tf:
        if len(out) > max_files * 2 + 1000:
            raise SafeArchiveError("too_many_files", f"The download has more "
                                   f"than {max_files} files, so nothing was "
                                   f"unpacked.")
        if info.isreg():
            files += 1
            total += max(int(info.size), 0)
            if files > max_files:
                raise SafeArchiveError("too_many_files", f"The download has "
                                       f"more than {max_files} files, so "
                                       f"nothing was unpacked.")
            if total > max_total_bytes:
                raise SafeArchiveError("too_big", "The download would take "
                                       "more room unpacked than allowed, so "
                                       "nothing was unpacked.")
        if info.isreg():
            kind = "file"
        elif info.isdir():
            kind = "dir"
        elif info.issym():
            kind = "link"
        elif info.islnk():
            kind = "hardlink"
        elif info.ischr() or info.isblk():
            kind = "device"
        elif info.isfifo():
            kind = "fifo"
        else:
            kind = "other"
        out.append(_Member(info.name, kind, int(info.size), int(info.size), info))
    return out


_KIND_WHY = {
    "link": "it is a link to somewhere else",
    "hardlink": "it is a second name for another file",
    "device": "it is a device, not a file",
    "fifo": "it is a special kind of file, not a normal one",
    "other": "it is not a regular file",
}


def _strip(name: str, top: Optional[str]) -> str:
    while name.startswith("./"):
        name = name[2:]
    if top and (name == top or name.startswith(top + "/")):
        name = name[len(top):].lstrip("/")
    return name


def _single_top(members: list[_Member]) -> Optional[str]:
    """The one folder every file sits in (GitHub and Codeberg archives have
    ``<project>-<version>/``), or None."""
    tops = set()
    for m in members:
        if m.kind not in ("file", "dir"):
            continue
        name = _strip(m.name, None).rstrip("/")
        if not name:
            continue
        head, _, rest = name.partition("/")
        if m.kind == "file" and not rest:
            return None  # a file at the top: there is no single folder
        tops.add(head)
    return next(iter(tops)) if len(tops) == 1 else None


def _is_reparse(path: str) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    attrs = getattr(st, "st_file_attributes", 0) or 0
    return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT) or stat.S_ISLNK(st.st_mode)


def extract(archive_path: str, dest_dir: str, *, max_total_bytes: int,
            max_files: int = 20000, strip_top: bool = True) -> dict:
    """Unpack *archive_path* (zip or tar, optionally gz/bz2/xz) into *dest_dir*.

    Returns ``{"files", "bytes", "skipped": [{"name", "why"}]}``. Raises
    :class:`SafeArchiveError` for the whole archive (bomb, too many files,
    not an archive, a destination outside the apps folder); on a refusal
    after writing began, what was written is removed again.
    """
    if not isinstance(max_total_bytes, int) or max_total_bytes <= 0:
        raise SafeArchiveError("bad_limit", "No size limit was given, so "
                               "nothing was unpacked.")
    if not isinstance(max_files, int) or max_files <= 0:
        raise SafeArchiveError("bad_limit", "No file limit was given, so "
                               "nothing was unpacked.")
    root = _real(extraction_root())
    dest_abs = os.path.abspath(dest_dir)
    dest_real = _real(dest_abs)
    if not _inside(root, dest_real) or dest_real == root.rstrip("\\/"):
        raise SafeArchiveError("outside_root", "That folder is not inside the "
                               "apps folder, so nothing was unpacked there.")
    if os.path.lexists(dest_abs):
        if _is_reparse(dest_abs) or not os.path.isdir(dest_abs):
            raise SafeArchiveError("bad_dest", "That folder is not a plain "
                                   "folder, so nothing was unpacked there.")
        if any(os.scandir(dest_abs)):
            raise SafeArchiveError("bad_dest", "That folder already has "
                                   "things in it, so nothing was unpacked "
                                   "there.")
    try:
        archive_size = os.path.getsize(archive_path)
    except OSError:
        raise SafeArchiveError("no_archive", "The download could not be "
                               "read, so nothing was unpacked.") from None

    with _open_archive(archive_path, max_total_bytes, max_files) as (
            kind, members, reader):
        _check_whole(kind, members, archive_size, max_total_bytes, max_files)
        top = _single_top(members) if strip_top else None
        plan, skipped = _plan(members, top)
        created_dest = not os.path.exists(dest_abs)
        os.makedirs(dest_abs, exist_ok=True)
        # The folder may only now exist: check where it really is again.
        if not _inside(root, _real(dest_abs)):
            raise SafeArchiveError("outside_root", "That folder is not inside "
                                   "the apps folder, so nothing was unpacked "
                                   "there.")
        try:
            files, written = _write_all(plan, reader, dest_abs,
                                        max_total_bytes, skipped)
        except BaseException:
            _undo(dest_abs, created_dest)
            raise
    return {"files": files, "bytes": written, "skipped": skipped}


#: Room allowed per listed zip entry when judging the list's size up front
#: (an ordinary entry is well under 200 bytes).
_ZIP_LIST_BYTES_PER_ENTRY = 1024


def _zip_list_check(path: str, max_files: int) -> None:
    limit = max_files * 2 + 1000
    try:
        with open(path, "rb") as fp:
            end = zipfile._EndRecData(fp)  # noqa: SLF001 - the stdlib's own reader
    except (OSError, ValueError, zipfile.BadZipFile):
        end = None
    if not end:
        raise SafeArchiveError("not_archive", "The download is damaged, so "
                               "nothing was unpacked.")
    entries = int(end[zipfile._ECD_ENTRIES_TOTAL])  # noqa: SLF001
    list_size = int(end[zipfile._ECD_SIZE])  # noqa: SLF001
    if entries > limit or list_size > limit * _ZIP_LIST_BYTES_PER_ENTRY:
        raise SafeArchiveError("too_many_files", f"The download has more than "
                               f"{max_files} files, so nothing was unpacked.")
    # The count in the end record is only a claim: the reader parses every
    # entry the list's bytes hold. So the entries are counted here, header by
    # header, without holding the list, and the read stops past the limit.
    if _zip_list_entries(path, end, limit) > limit:
        raise SafeArchiveError("too_many_files", f"The download has more than "
                               f"{max_files} files, so nothing was unpacked.")


def _zip_list_entries(path: str, end: Any, limit: int) -> int:
    """How many entries the zip's list really holds (at most *limit* + 1)."""
    size_cd = int(end[zipfile._ECD_SIZE])  # noqa: SLF001
    try:
        handle = getattr(zipfile, "_handle_prepended_data", None)
        if handle is not None:
            offset_cd, concat = handle(end)
        else:  # older readers: the same sum, done here
            offset_cd = int(end[zipfile._ECD_OFFSET])  # noqa: SLF001
            concat = int(end[zipfile._ECD_LOCATION]) - size_cd - offset_cd  # noqa: SLF001
            if end[zipfile._ECD_SIGNATURE] == zipfile.stringEndArchive64:  # noqa: SLF001
                concat -= zipfile.sizeEndCentDir64 + zipfile.sizeEndCentDir64Locator
    except (zipfile.BadZipFile, ValueError, TypeError, IndexError):
        raise SafeArchiveError("not_archive", "The download is damaged, so "
                               "nothing was unpacked.") from None
    start = int(offset_cd) + int(concat)
    if start < 0:
        raise SafeArchiveError("not_archive", "The download is damaged, so "
                               "nothing was unpacked.")
    head_size = zipfile.sizeCentralDir
    count = 0
    total = 0
    try:
        with open(path, "rb") as fp:
            fp.seek(start)
            while total < size_cd:
                head = fp.read(head_size)
                if len(head) != head_size:
                    break                       # the reader refuses it too
                fields = struct.unpack(zipfile.structCentralDir, head)
                if fields[0] != zipfile.stringCentralDir:
                    break
                skip = (fields[zipfile._CD_FILENAME_LENGTH]  # noqa: SLF001
                        + fields[zipfile._CD_EXTRA_FIELD_LENGTH]  # noqa: SLF001
                        + fields[zipfile._CD_COMMENT_LENGTH])  # noqa: SLF001
                fp.seek(skip, 1)
                total += head_size + skip
                count += 1
                if count > limit:
                    return count
    except (OSError, struct.error):
        raise SafeArchiveError("not_archive", "The download is damaged, so "
                               "nothing was unpacked.") from None
    return count


class _open_archive:
    """``with _open_archive(p) as (kind, members, reader)``."""

    def __init__(self, path: str, max_total_bytes: int, max_files: int):
        self.path = path
        self.max_total_bytes = max_total_bytes
        self.max_files = max_files
        self._closer: Optional[Callable[[], None]] = None

    def __enter__(self):
        if zipfile.is_zipfile(self.path):
            # ZipFile() reads the whole list of names into memory at once, so
            # the count and the list's size are checked from the end record
            # first: a million-entry list is refused before it is read.
            _zip_list_check(self.path, self.max_files)
            try:
                zf = zipfile.ZipFile(self.path)
            except (zipfile.BadZipFile, OSError, ValueError):
                raise SafeArchiveError("not_archive", "The download is not a kind of "
                                       "file this can unpack.") from None
            self._closer = zf.close
            return "zip", _zip_members(zf), (lambda m: zf.open(m.handle, "r"))
        try:
            tf = tarfile.open(self.path, mode="r:*", tarinfo=_BoundedTarInfo)
        except _HeaderTooBig:
            raise SafeArchiveError("header_too_big", _HEADER_REFUSAL) from None
        except (tarfile.TarError, OSError, EOFError, ValueError):
            raise SafeArchiveError("not_archive", "The download is not a kind of "
                                   "file this can unpack.") from None
        self._closer = tf.close
        try:
            members = _tar_members(tf, self.max_total_bytes, self.max_files)
        except SafeArchiveError:
            tf.close()
            raise
        except _HeaderTooBig:
            tf.close()
            raise SafeArchiveError("header_too_big", _HEADER_REFUSAL) from None
        except (tarfile.TarError, OSError, EOFError, ValueError):
            tf.close()
            raise SafeArchiveError("not_archive", "The download is damaged, so "
                                   "nothing was unpacked.") from None
        compressed = type(tf.fileobj).__module__ in ("gzip", "bz2", "lzma")
        kind = "tar_compressed" if compressed else "tar"

        def reader(m: _Member):
            got = tf.extractfile(m.handle)
            if got is None:
                raise SafeArchiveError("not_archive", "The download is damaged, "
                                       "so nothing was unpacked.")
            return got
        return kind, members, reader

    def __exit__(self, *exc: Any) -> None:
        if self._closer is not None:
            try:
                self._closer()
            except Exception:
                pass


def _check_whole(kind: str, members: list[_Member], archive_size: int,
                 max_total_bytes: int, max_files: int) -> None:
    files = [m for m in members if m.kind == "file"]
    if len(files) > max_files:
        raise SafeArchiveError("too_many_files", f"The download has more than "
                               f"{max_files} files, so nothing was unpacked.")
    total = sum(max(m.size, 0) for m in files)
    if total > max_total_bytes:
        raise SafeArchiveError("too_big", "The download would take more room "
                               "unpacked than allowed, so nothing was unpacked.")
    if kind == "zip":
        for m in files:
            if m.size > 0 and m.size > MAX_RATIO * max(m.packed, 1):
                raise SafeArchiveError("bomb", "A file in the download "
                                       "unpacks to far more than its size, "
                                       "so nothing was unpacked.")
    elif kind == "tar_compressed":
        if total > MAX_RATIO * max(archive_size, 1):
            raise SafeArchiveError("bomb", "The download unpacks to far more "
                                   "than its size, so nothing was unpacked.")


def _plan(members: list[_Member], top: Optional[str]
          ) -> tuple[list[tuple[_Member, str]], list[dict]]:
    skipped: list[dict] = []
    plan: list[tuple[_Member, str]] = []
    seen_files: set[str] = set()
    seen_dirs: set[str] = set()
    for m in members:
        why = _KIND_WHY.get(m.kind)
        if why:
            skipped.append({"name": _shown(m.name), "why": why})
            continue
        why = check_member_name(m.name if not m.name.startswith("./")
                                else _strip(m.name, None) or m.name)
        if why:
            skipped.append({"name": _shown(m.name), "why": why})
            continue
        rel = _strip(m.name, top).rstrip("/")
        if not rel:
            continue  # the stripped top folder itself
        if m.kind == "dir":
            continue  # folders are made for the files inside them
        key = rel.casefold()
        parents = []
        parts = key.split("/")
        for i in range(1, len(parts)):
            parents.append("/".join(parts[:i]))
        if key in seen_files or key in seen_dirs:
            skipped.append({"name": _shown(m.name),
                            "why": "another file has the same name "
                                   "(apart from capital letters)"})
            continue
        if any(p in seen_files for p in parents):
            skipped.append({"name": _shown(m.name),
                            "why": "a file already uses one of its folder names"})
            continue
        seen_files.add(key)
        seen_dirs.update(parents)
        plan.append((m, rel))
    return plan, skipped


def _shown(name: Any) -> str:
    """A member name as a list may show it: printable, at most 120 chars."""
    text = "".join(ch if 32 <= ord(ch) != 127 else "?" for ch in str(name))
    return text[:120]


def _write_all(plan: list[tuple[_Member, str]], reader, dest_abs: str,
               max_total_bytes: int, skipped: list[dict]) -> tuple[int, int]:
    dest_real = _real(dest_abs)
    written = 0
    files = 0
    for member, rel in plan:
        target = os.path.join(dest_abs, *rel.split("/"))
        parent = os.path.dirname(target)
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError:
            skipped.append({"name": _shown(member.name),
                            "why": "its folder could not be made here"})
            continue
        if not _inside(dest_real, _real(parent)):
            raise SafeArchiveError("escaped", "A file in the download tried to "
                                   "land outside its folder, so nothing was "
                                   "unpacked.")
        try:
            out = open(target, "xb")
        except FileExistsError:
            skipped.append({"name": _shown(member.name),
                            "why": "another file has the same name "
                                   "(apart from capital letters)"})
            continue
        except OSError:
            skipped.append({"name": _shown(member.name),
                            "why": "it could not be saved on this computer"})
            continue
        with out:
            src = reader(member)
            try:
                own = 0
                while True:
                    chunk = src.read(_CHUNK)
                    if not chunk:
                        break
                    own += len(chunk)
                    written += len(chunk)
                    if own > member.size or written > max_total_bytes:
                        raise SafeArchiveError(
                            "too_big", "A file in the download was bigger than "
                            "it said, so nothing was unpacked.")
                    out.write(chunk)
            except (zipfile.BadZipFile, tarfile.TarError, EOFError,
                    OSError, ValueError, RuntimeError) as exc:
                if isinstance(exc, SafeArchiveError):
                    raise
                raise SafeArchiveError("damaged", "The download is damaged, so "
                                       "nothing was unpacked.") from None
            finally:
                try:
                    src.close()
                except Exception:
                    pass
        files += 1
    return files, written


def _undo(dest_abs: str, created: bool) -> None:
    """Remove what a refused extract wrote. Never follows a link."""
    try:
        parent = os.path.dirname(os.path.abspath(dest_abs))
        if created:
            safe_rmtree(dest_abs, allowed_roots=[parent], _retry_seconds=2.0)
        else:
            for entry in list(os.scandir(dest_abs)):
                safe_rmtree(entry.path, allowed_roots=[dest_abs],
                            _retry_seconds=2.0)
    except Exception as exc:  # the refusal is the news; this is a log line
        logger.warning("could not clean up a refused unpack: %s",
                       type(exc).__name__)


# -- removing folders ----------------------------------------------------------

def _known_folders() -> list[str]:
    """The profile folder and its known folders (as Windows reports them)."""
    home = os.path.expanduser("~")
    out = [home]
    names = ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos")
    out.extend(os.path.join(home, n) for n in names)
    for var in ("USERPROFILE", "HOME", "OneDrive"):
        value = os.environ.get(var)
        if value and os.path.isabs(value):
            out.append(value)
    if sys.platform == "win32":
        out.extend(_windows_known_folders())
    return out


def _windows_known_folders() -> list[str]:
    """Where Windows really keeps Desktop, Documents... (OneDrive may move them)."""
    try:
        import ctypes
        import uuid
        from ctypes import wintypes
    except Exception:  # pragma: no cover
        return []
    ids = {
        "Desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
        "Documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
        "Downloads": "374DE290-123F-4565-9164-39C4925E467B",
        "Pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
        "Music": "4BD8D571-6D19-48D3-BE97-422220080E43",
        "Videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
        "Profile": "5E6C858F-0E22-4760-9AFE-EA3317B67173",
    }

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", wintypes.BYTE * 8)]

    out = []
    try:
        shell32 = ctypes.windll.shell32
        ole32 = ctypes.windll.ole32
    except Exception:  # pragma: no cover
        return []
    for text in ids.values():
        u = uuid.UUID(text)
        g = GUID(u.fields[0], u.fields[1], u.fields[2],
                 (wintypes.BYTE * 8).from_buffer_copy(u.bytes[8:]))
        ptr = ctypes.c_wchar_p()
        try:
            hr = shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None,
                                              ctypes.byref(ptr))
            if hr == 0 and ptr.value:
                out.append(ptr.value)
        except Exception:
            pass
        finally:
            if ptr:
                try:
                    ole32.CoTaskMemFree(ptr)
                except Exception:
                    pass
    return out


def _is_drive_root(real: str) -> bool:
    trimmed = real.rstrip("\\/")
    return (not trimmed or os.path.dirname(real) == real
            or (len(trimmed) == 2 and trimmed[1] == ":")
            or os.path.splitdrive(real)[1] in ("", "\\", "/"))


def _retrying(fn: Callable[[], None], path: str, budget: float,
              sleeper: Callable[[float], None], *, chmod: bool = True) -> None:
    deadline = time.monotonic() + max(0.0, budget)
    while True:
        try:
            fn()
            return
        except FileNotFoundError:
            return
        except PermissionError:
            # A read-only file (git objects are) cannot be removed on Windows
            # until it is writable again; a file another program holds is
            # waited for.
            if chmod:
                try:  # never through a link: that would change its target
                    os.chmod(path, stat.S_IWRITE | stat.S_IREAD,
                             follow_symlinks=False)
                except (OSError, NotImplementedError):
                    pass
            if time.monotonic() >= deadline:
                raise
            sleeper(LOCK_RETRY_STEP)
        except OSError:
            if time.monotonic() >= deadline:
                raise
            sleeper(LOCK_RETRY_STEP)


def safe_rmtree(path: str, *, allowed_roots: list[str],
                _retry_seconds: Optional[float] = None,
                _sleeper: Callable[[float], None] = time.sleep) -> dict:
    """Remove *path* and everything under it, never following a link.

    Refuses (:class:`SafeArchiveError`) when *path* is itself a link or a
    junction, is not inside one of *allowed_roots*, or is a drive root, the
    profile folder, one of its known folders (Desktop, Documents, Downloads,
    Pictures, Music, Videos), or a folder that holds one of them. Returns
    ``{"removed": <files and folders removed>, "links_unlinked": <links>}``.
    """
    budget = LOCK_RETRY_SECONDS if _retry_seconds is None else float(_retry_seconds)
    if not isinstance(path, str) or not path or "\0" in path:
        raise SafeArchiveError("bad_path", "No folder was given to remove.")
    if not isinstance(allowed_roots, (list, tuple)) or not allowed_roots:
        raise SafeArchiveError("bad_path", "No folder is allowed to be removed.")
    target = os.path.abspath(path)
    if not os.path.lexists(target):
        return {"removed": 0, "links_unlinked": 0}
    if _is_reparse(target):
        raise SafeArchiveError("is_link", "That folder is a link to somewhere "
                               "else, so it was not removed.")
    real = _real(target)
    roots = []
    for r in allowed_roots:
        if isinstance(r, str) and r and os.path.isabs(r):
            rr = _real(r)
            if not _is_drive_root(rr):
                roots.append(rr)
    _check_removable(real, roots)
    counts = {"removed": 0, "links_unlinked": 0}
    if not os.path.isdir(target):
        _retrying(lambda: os.unlink(target), target, budget, _sleeper)
        counts["removed"] += 1
        return counts
    _remove_dir(target, counts, budget, _sleeper, roots)
    return counts


def _check_removable(real: str, roots: list[str]) -> None:
    """Refuse *real* (already a real, case-folded path) unless it is inside
    one of *roots* and is not a drive root, the profile or one of its known
    folders, or a folder holding one of them."""
    if not any(_inside(r, real) for r in roots):
        raise SafeArchiveError("outside_roots", "That folder is not one "
                               "BuildBot made, so it was not removed.")
    if _is_drive_root(real):
        raise SafeArchiveError("protected", "A whole drive is never removed.")
    for keep in _known_folders():
        k = _real(keep)
        if _inside(real, k):
            raise SafeArchiveError("protected", "That is one of your own "
                                   "folders, so it was not removed.")


# -- removing folders: the walk --------------------------------------------------
#
# The walk never goes through a path below the folder it was asked to remove.
# A path can change between a check and a use: a folder checked as a plain
# folder can be swapped for a junction before the walk looks inside it (the
# race behind CVE-2022-21658). So on Windows every child is opened RELATIVE TO
# ITS PARENT'S OPEN HANDLE (NtCreateFile with a root directory) with
# FILE_OPEN_REPARSE_POINT, what it is comes from that handle, and it is
# listed and deleted through that same handle. A junction swapped in at any
# moment is opened as the junction itself and deleted as a link; the walk
# never lists or deletes anything through it. On other systems the same is
# done with directory file descriptors (``dir_fd`` and ``O_NOFOLLOW``).

_DELETE = 0x00010000
_SYNCHRONIZE = 0x00100000
_FILE_LIST_DIRECTORY = 0x0001
_FILE_READ_ATTRIBUTES = 0x0080
_FILE_WRITE_ATTRIBUTES = 0x0100
_SHARE_ALL = 0x1 | 0x2 | 0x4
_OPEN_EXISTING = 3
_FLAG_BACKUP_SEMANTICS = 0x02000000
_FLAG_OPEN_REPARSE_POINT = 0x00200000
_NT_FILE_OPEN = 1
_NT_OPEN_REPARSE_POINT = 0x00200000
_NT_SYNCHRONOUS_IO_NONALERT = 0x00000020
_FILE_ATTRIBUTE_READONLY = 0x1
_FILE_ATTRIBUTE_NORMAL = 0x80
_CLASS_BASIC = 0
_CLASS_DISPOSITION = 4
_CLASS_ATTRIBUTE_TAG = 9
_CLASS_FULL_DIR = 14
_CLASS_FULL_DIR_RESTART = 15
_CLASS_DISPOSITION_EX = 21
_DISPOSITION_DELETE = 0x1
_DISPOSITION_POSIX = 0x2
_DISPOSITION_IGNORE_READONLY = 0x10
_ERROR_NO_MORE_FILES = 18
_ERROR_INVALID_PARAMETER = 87
_ERROR_NOT_SUPPORTED = 50
_ERROR_INVALID_FUNCTION = 1
_ERROR_CANNOT_DELETE = 5  # ACCESS_DENIED is what a read-only file gives
_LIST_BUFFER = 64 * 1024

_WIN: dict[str, Any] = {}


def _win() -> dict:
    """ctypes bindings, built once, only on Windows."""
    if _WIN:
        return _WIN
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    nt = ctypes.WinDLL("ntdll")

    class UNICODE_STRING(ctypes.Structure):
        _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT),
                    ("Buffer", ctypes.c_void_p)]

    class OBJECT_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Length", wintypes.ULONG), ("RootDirectory", wintypes.HANDLE),
                    ("ObjectName", ctypes.POINTER(UNICODE_STRING)),
                    ("Attributes", wintypes.ULONG),
                    ("SecurityDescriptor", ctypes.c_void_p),
                    ("SecurityQualityOfService", ctypes.c_void_p)]

    class IO_STATUS_BLOCK(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_void_p)]

    class FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
        _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]

    class FILE_BASIC_INFO(ctypes.Structure):
        _fields_ = [("CreationTime", ctypes.c_longlong),
                    ("LastAccessTime", ctypes.c_longlong),
                    ("LastWriteTime", ctypes.c_longlong),
                    ("ChangeTime", ctypes.c_longlong),
                    ("FileAttributes", wintypes.DWORD)]

    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                wintypes.HANDLE]
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                 ctypes.c_void_p, wintypes.DWORD]
    k32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    k32.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                               ctypes.c_void_p, wintypes.DWORD]
    k32.SetFileInformationByHandle.restype = wintypes.BOOL
    k32.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR,
                                              wintypes.DWORD, wintypes.DWORD]
    k32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    nt.NtCreateFile.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG,
                                ctypes.POINTER(OBJECT_ATTRIBUTES),
                                ctypes.POINTER(IO_STATUS_BLOCK), ctypes.c_void_p,
                                wintypes.ULONG, wintypes.ULONG, wintypes.ULONG,
                                wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG]
    nt.NtCreateFile.restype = ctypes.c_long
    nt.RtlNtStatusToDosError.argtypes = [ctypes.c_long]
    nt.RtlNtStatusToDosError.restype = wintypes.ULONG
    _WIN.update(ctypes=ctypes, wintypes=wintypes, k32=k32, nt=nt,
                UNICODE_STRING=UNICODE_STRING, OBJECT_ATTRIBUTES=OBJECT_ATTRIBUTES,
                IO_STATUS_BLOCK=IO_STATUS_BLOCK,
                FILE_ATTRIBUTE_TAG_INFO=FILE_ATTRIBUTE_TAG_INFO,
                FILE_BASIC_INFO=FILE_BASIC_INFO,
                INVALID=ctypes.c_void_p(-1).value)
    return _WIN


def _win_error(code: int) -> OSError:
    w = _win()
    return w["ctypes"].WinError(code)


def _win_open_root(path: str) -> int:
    """A handle on *path* itself (a junction is opened as the junction)."""
    w = _win()
    access = (_DELETE | _SYNCHRONIZE | _FILE_LIST_DIRECTORY | _FILE_READ_ATTRIBUTES
              | _FILE_WRITE_ATTRIBUTES)
    h = w["k32"].CreateFileW(path, access, _SHARE_ALL, None, _OPEN_EXISTING,
                             _FLAG_BACKUP_SEMANTICS | _FLAG_OPEN_REPARSE_POINT, None)
    if h is None or h == w["INVALID"]:
        raise _win_error(w["ctypes"].get_last_error())
    return h


def _win_open_child(parent: int, name: str) -> int:
    """A handle on *name* inside the folder *parent* is a handle on. Never a
    path lookup from the top; a link is opened as the link."""
    w = _win()
    ctypes = w["ctypes"]
    buf = ctypes.create_unicode_buffer(name)
    size = len(name) * 2
    us = w["UNICODE_STRING"](size, size, ctypes.cast(buf, ctypes.c_void_p))
    oa = w["OBJECT_ATTRIBUTES"]()
    oa.Length = ctypes.sizeof(oa)
    oa.RootDirectory = parent
    oa.ObjectName = ctypes.pointer(us)
    oa.Attributes = 0  # exact name, as the listing gave it
    iosb = w["IO_STATUS_BLOCK"]()
    h = w["wintypes"].HANDLE()
    access = (_DELETE | _SYNCHRONIZE | _FILE_LIST_DIRECTORY | _FILE_READ_ATTRIBUTES
              | _FILE_WRITE_ATTRIBUTES)
    status = w["nt"].NtCreateFile(ctypes.byref(h), access, ctypes.byref(oa),
                                  ctypes.byref(iosb), None, 0, _SHARE_ALL,
                                  _NT_FILE_OPEN,
                                  _NT_OPEN_REPARSE_POINT | _NT_SYNCHRONOUS_IO_NONALERT,
                                  None, 0)
    if status < 0:
        raise _win_error(w["nt"].RtlNtStatusToDosError(status))
    return h.value


def _win_close(h: int) -> None:
    try:
        _win()["k32"].CloseHandle(h)
    except Exception:  # pragma: no cover - defensive
        pass


def _win_attrs(h: int) -> int:
    w = _win()
    info = w["FILE_ATTRIBUTE_TAG_INFO"]()
    if not w["k32"].GetFileInformationByHandleEx(
            h, _CLASS_ATTRIBUTE_TAG, w["ctypes"].byref(info),
            w["ctypes"].sizeof(info)):
        raise _win_error(w["ctypes"].get_last_error())
    return int(info.FileAttributes)


def _win_final_path(h: int) -> str:
    w = _win()
    ctypes = w["ctypes"]
    size = 1024
    while True:
        buf = ctypes.create_unicode_buffer(size)
        n = w["k32"].GetFinalPathNameByHandleW(h, buf, size, 0)
        if n == 0:
            raise _win_error(ctypes.get_last_error())
        if n < size:
            text = buf.value
            break
        size = n + 1
    if text.startswith("\\\\?\\UNC\\"):
        text = "\\\\" + text[8:]
    elif text.startswith("\\\\?\\"):
        text = text[4:]
    return os.path.normcase(text)


def _win_list(h: int) -> list[str]:
    """Every name in the folder *h* is a handle on (not '.' or '..')."""
    w = _win()
    ctypes = w["ctypes"]
    buf = ctypes.create_string_buffer(_LIST_BUFFER)
    names: list[str] = []
    cls = _CLASS_FULL_DIR_RESTART
    while True:
        if not w["k32"].GetFileInformationByHandleEx(h, cls, buf, _LIST_BUFFER):
            err = ctypes.get_last_error()
            if err == _ERROR_NO_MORE_FILES:
                return names
            raise _win_error(err)
        cls = _CLASS_FULL_DIR
        raw = buf.raw
        offset = 0
        while True:
            nxt = int.from_bytes(raw[offset:offset + 4], "little")
            length = int.from_bytes(raw[offset + 60:offset + 64], "little")
            name = raw[offset + 68:offset + 68 + length].decode("utf-16-le",
                                                                "surrogatepass")
            if name not in (".", ".."):
                names.append(name)
            if not nxt:
                break
            offset += nxt


def _win_delete(h: int, attrs: int) -> None:
    """Mark what *h* is a handle on for deletion (a link: the link itself)."""
    w = _win()
    ctypes = w["ctypes"]
    flags = ctypes.c_ulong(_DISPOSITION_DELETE | _DISPOSITION_POSIX
                           | _DISPOSITION_IGNORE_READONLY)
    if w["k32"].SetFileInformationByHandle(h, _CLASS_DISPOSITION_EX,
                                           ctypes.byref(flags), 4):
        return
    err = ctypes.get_last_error()
    if err not in (_ERROR_INVALID_PARAMETER, _ERROR_NOT_SUPPORTED,
                   _ERROR_INVALID_FUNCTION):
        raise _win_error(err)
    # Older Windows or a file system without the newer call: clear read-only
    # through the handle, then the classic delete-on-close.
    if attrs & _FILE_ATTRIBUTE_READONLY:
        basic = w["FILE_BASIC_INFO"]()
        basic.FileAttributes = (attrs & ~_FILE_ATTRIBUTE_READONLY) or _FILE_ATTRIBUTE_NORMAL
        w["k32"].SetFileInformationByHandle(h, _CLASS_BASIC, ctypes.byref(basic),
                                            ctypes.sizeof(basic))
    yes = ctypes.c_ubyte(1)
    if not w["k32"].SetFileInformationByHandle(h, _CLASS_DISPOSITION,
                                               ctypes.byref(yes), 1):
        raise _win_error(ctypes.get_last_error())


def _win_clear(h: int, counts: dict, budget: float,
               sleeper: Callable[[float], None]) -> None:
    """Remove everything inside the folder *h* is a handle on."""
    for name in _win_list(h):
        box: list = []

        def _open(n=name) -> None:
            box.append(_win_open_child(h, n))
        _retrying(_open, name, budget, sleeper, chmod=False)
        if not box:
            continue  # gone already
        child = box[0]
        try:
            attrs = _win_attrs(child)
            if attrs & FILE_ATTRIBUTE_REPARSE_POINT:
                _retrying(lambda c=child, a=attrs: _win_delete(c, a), name, budget,
                          sleeper, chmod=False)
                counts["links_unlinked"] += 1
            elif attrs & FILE_ATTRIBUTE_DIRECTORY:
                _win_remove_folder(child, attrs, counts, budget, sleeper)
            else:
                _retrying(lambda c=child, a=attrs: _win_delete(c, a), name, budget,
                          sleeper, chmod=False)
                counts["removed"] += 1
        finally:
            _win_close(child)


def _win_remove_folder(h: int, attrs: int, counts: dict, budget: float,
                       sleeper: Callable[[float], None]) -> None:
    """Empty the folder *h* is a handle on, then delete it. Something added
    while it was being emptied is emptied too, within the retry budget."""
    deadline = time.monotonic() + max(0.0, budget)
    while True:
        _win_clear(h, counts, budget, sleeper)
        try:
            _win_delete(h, attrs)
            counts["removed"] += 1
            return
        except FileNotFoundError:
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise
            sleeper(LOCK_RETRY_STEP)


def _remove_dir_windows(folder: str, roots: list[str], counts: dict,
                        budget: float, sleeper: Callable[[float], None]) -> None:
    box: list = []
    _retrying(lambda: box.append(_win_open_root(folder)), folder, budget, sleeper,
              chmod=False)
    if not box:
        return
    h = box[0]
    try:
        attrs = _win_attrs(h)
        # The checks in safe_rmtree looked at a path; this looks at the thing
        # that is now open, so a swap in between is caught.
        if attrs & FILE_ATTRIBUTE_REPARSE_POINT:
            raise SafeArchiveError("is_link", "That folder is a link to somewhere "
                                   "else, so it was not removed.")
        real = _win_final_path(h)
        _check_removable(real, roots)
        if not attrs & FILE_ATTRIBUTE_DIRECTORY:
            _retrying(lambda: _win_delete(h, attrs), folder, budget, sleeper,
                      chmod=False)
            counts["removed"] += 1
            return
        _win_remove_folder(h, attrs, counts, budget, sleeper)
    finally:
        _win_close(h)


_POSIX_DIR_FLAGS = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))


def _posix_supported() -> bool:
    return (os.open in os.supports_dir_fd and os.unlink in os.supports_dir_fd
            and os.rmdir in os.supports_dir_fd and os.stat in os.supports_dir_fd
            and os.scandir in os.supports_fd and hasattr(os, "O_NOFOLLOW")
            and hasattr(os, "O_DIRECTORY"))


def _posix_clear(dfd: int, counts: dict, budget: float,
                 sleeper: Callable[[float], None]) -> None:
    with os.scandir(dfd) as it:
        names = [e.name for e in it]
    for name in names:
        try:
            st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(st.st_mode):
            _retrying(lambda n=name: os.unlink(n, dir_fd=dfd), name, budget,
                      sleeper, chmod=False)
            counts["links_unlinked"] += 1
            continue
        if stat.S_ISDIR(st.st_mode):
            try:
                cfd = os.open(name, _POSIX_DIR_FLAGS, dir_fd=dfd)
            except FileNotFoundError:
                continue
            except OSError:
                # O_NOFOLLOW: it became a link after the look. Remove the link.
                _retrying(lambda n=name: os.unlink(n, dir_fd=dfd), name, budget,
                          sleeper, chmod=False)
                counts["links_unlinked"] += 1
                continue
            try:
                now = os.fstat(cfd)
                if (now.st_dev, now.st_ino) != (st.st_dev, st.st_ino):
                    raise SafeArchiveError("changed", "A folder changed while it "
                                           "was being removed, so it was left.")
                _posix_clear(cfd, counts, budget, sleeper)
            finally:
                os.close(cfd)
            _retrying(lambda n=name: os.rmdir(n, dir_fd=dfd), name, budget,
                      sleeper, chmod=False)
            counts["removed"] += 1
            continue
        _retrying(lambda n=name: os.unlink(n, dir_fd=dfd), name, budget, sleeper,
                  chmod=False)
        counts["removed"] += 1


def _remove_dir_posix(folder: str, counts: dict, budget: float,
                      sleeper: Callable[[float], None]) -> None:
    if not _posix_supported():
        raise SafeArchiveError("unsupported", "This computer cannot remove a "
                               "folder safely, so it was not removed.")
    st = os.lstat(folder)
    fd = os.open(folder, _POSIX_DIR_FLAGS)
    try:
        now = os.fstat(fd)
        if (now.st_dev, now.st_ino) != (st.st_dev, st.st_ino):
            raise SafeArchiveError("changed", "The folder changed while it was "
                                   "being removed, so it was left.")
        _posix_clear(fd, counts, budget, sleeper)
    finally:
        os.close(fd)
    _retrying(lambda: os.rmdir(folder), folder, budget, sleeper, chmod=False)
    counts["removed"] += 1


def _remove_dir(folder: str, counts: dict, budget: float,
                sleeper: Callable[[float], None],
                roots: Optional[list[str]] = None) -> None:
    """Remove *folder* and everything in it without following a link."""
    if sys.platform == "win32":
        _remove_dir_windows(folder, list(roots or []), counts, budget, sleeper)
    else:
        _remove_dir_posix(folder, counts, budget, sleeper)


__all__ = ["APPS_ROOT_ENV", "LOCK_RETRY_SECONDS", "MAX_MEMBER_PATH", "MAX_RATIO",
           "SafeArchiveError", "check_member_name", "configured_root", "extract",
           "extraction_root", "safe_rmtree", "secret_roots",
           "vault_and_key_roots"]
