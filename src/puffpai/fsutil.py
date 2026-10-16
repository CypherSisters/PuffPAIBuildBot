"""Small file helpers that survive Windows.

``atomic_write`` writes a whole file so a reader sees the old contents or the
new ones, never half. It writes a temporary file beside the target and swaps
it in with ``os.replace``. On Windows that swap fails with PermissionError
while another process has the target open (Python opens files without
FILE_SHARE_DELETE), which a 3-second dashboard poll or a terminal
``status`` does all the time. So the swap is retried a few times (5 x 50 ms
by default) before giving up.

``atomic_append`` adds one line to a log in a single write, with the same
retry, so two writers never interleave inside a line.

``read_text`` reads a file with the same retry, for the reader's side of the
same race.

``file_lock`` holds an exclusive lock across processes, for a
read-change-write that must not interleave with another process's.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from typing import Any, Iterator, Optional, Union

#: How many times a swap, append or read is tried, and the pause between tries.
RETRIES = 5
RETRY_DELAY = 0.05


def _retry(action, retries: int, delay: float):
    last: Optional[BaseException] = None
    for attempt in range(max(1, retries)):
        try:
            return action()
        except PermissionError as exc:  # Windows: someone has it open
            last = exc
            if attempt + 1 < retries:
                time.sleep(delay)
    assert last is not None
    raise last


def atomic_write(path: Union[str, os.PathLike], data: Union[str, bytes], *,
                 encoding: str = "utf-8", retries: int = RETRIES,
                 delay: float = RETRY_DELAY) -> None:
    """Replace *path* with *data* in one step. Raises OSError if it cannot.

    The temporary file is removed on any failure, so a failed write leaves
    the old file exactly as it was and no litter beside it.
    """
    path = os.fspath(path)
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    raw = data.encode(encoding) if isinstance(data, str) else bytes(data)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".part", dir=folder)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        _retry(lambda: os.replace(tmp, path), retries, delay)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: Union[str, os.PathLike], obj: Any, **kw: Any) -> None:
    """:func:`atomic_write` of *obj* as indented UTF-8 JSON."""
    atomic_write(path, json.dumps(obj, indent=2, ensure_ascii=False), **kw)


def atomic_append(path: Union[str, os.PathLike], line: str, *,
                  encoding: str = "utf-8", retries: int = RETRIES,
                  delay: float = RETRY_DELAY) -> None:
    """Append one line (a newline is added if missing) in a single write."""
    path = os.fspath(path)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    raw = (line if line.endswith("\n") else line + "\n").encode(encoding)

    def _do() -> None:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT
                     | getattr(os, "O_BINARY", 0), 0o600)
        try:
            os.write(fd, raw)
        finally:
            os.close(fd)
    _retry(_do, retries, delay)


def read_text(path: Union[str, os.PathLike], *, encoding: str = "utf-8",
              retries: int = RETRIES, delay: float = RETRY_DELAY) -> str:
    """The whole file as text, retried while a writer is swapping it in."""
    def _do() -> str:
        with open(os.fspath(path), "r", encoding=encoding) as fh:
            return fh.read()
    return _retry(_do, retries, delay)


def read_json(path: Union[str, os.PathLike], default: Any = None) -> Any:
    """Parsed JSON from *path*, or *default* when missing or unreadable."""
    try:
        return json.loads(read_text(path))
    except (OSError, ValueError):
        return default


class LockTimeout(TimeoutError):
    """Another process held the lock for longer than the caller would wait."""


@contextlib.contextmanager
def file_lock(path: Union[str, os.PathLike], *, timeout: float = 10.0,
              poll: float = 0.02) -> Iterator[None]:
    """Hold an exclusive lock on *path* across PROCESSES (and threads).

    The lock is the operating system's own byte-range lock on a small lock
    file (``msvcrt.locking`` on Windows, ``fcntl.flock`` elsewhere), so it is
    released when the holder exits or dies: there is never a stale lock to
    clean up. The lock file itself is left in place (it holds nothing).
    Raises :class:`LockTimeout` (an ``OSError``) after *timeout* seconds.

    Not re-entrant: a caller that may nest must count its own depth.
    """
    path = os.fspath(path)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fh = open(path, "a+b")
    deadline = time.monotonic() + max(0.0, timeout)
    try:
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise LockTimeout(f"{path} is locked by another process")
                time.sleep(poll)
        try:
            yield
        finally:
            try:
                if os.name == "nt":
                    import msvcrt
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        fh.close()


def _statx_birth_ns(path: str) -> int:
    """Linux: when *path* was made (statx STATX_BTIME), in nanoseconds, or 0
    when this computer or its file system cannot say."""
    import ctypes
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        statx = libc.statx
    except (OSError, AttributeError):
        return 0
    statx.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint,
                      ctypes.c_void_p]
    statx.restype = ctypes.c_int
    buf = ctypes.create_string_buffer(256)          # struct statx
    at_fdcwd, statx_btime = -100, 0x800
    if statx(at_fdcwd, os.fsencode(path), 0, statx_btime, buf) != 0:
        return 0
    import struct
    mask = struct.unpack_from("<I", buf.raw, 0)[0]
    if not mask & statx_btime:
        return 0
    sec, nsec = struct.unpack_from("<qI", buf.raw, 80)  # stx_btime
    return sec * 1_000_000_000 + nsec


def birth_time(path: Union[str, os.PathLike], st: os.stat_result) -> Any:
    """When the file or folder *path* (``st`` is its stat) was made, as far
    as this computer can say; 0 when it cannot.

    Used, with the file id, to tell one copy of a folder from a new one made
    under the same name. Linux has no ``st_birthtime`` and readily gives a
    deleted folder's inode number to the next one, so there it is read with
    statx; elsewhere it is ``st_birthtime`` (as before)."""
    import sys
    if sys.platform.startswith("linux"):
        try:
            return _statx_birth_ns(os.fspath(path))
        except Exception:  # noqa: BLE001 - unknown is 0, as before
            return 0
    return getattr(st, "st_birthtime_ns", None) or getattr(st, "st_birthtime", None) or 0


__all__ = ["LockTimeout", "RETRIES", "RETRY_DELAY", "atomic_write",
           "atomic_write_json", "atomic_append", "birth_time", "file_lock", "read_text",
           "read_json"]
