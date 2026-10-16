"""At-rest handling for the HTML apps' JSON state (``app_data/*.json``).

One thing lives here and nothing else:

* :func:`write_json_secure` - the ONE way an app document reaches disk. It
  writes to a sibling temp file and ``os.replace``s it into place, so a crash
  or a concurrent reader can never see a half-written document, and it lands
  the file at ``0600`` in a ``0700`` directory.

HomeBot's companion ``sweep_app_data_modes()`` (a boot-time pass that tightens
documents this build never happens to rewrite) is deliberately NOT ported here:
BuildBot has no service-start hook to run it from, and a sweep nothing calls is
the dead code this branch has already been refuted for once.

WHY THIS MODULE EXISTS
----------------------
Before this, ``AppData._save`` was a plain ``open(path, 'w')``. Two consequences
were live on the appliance:

1. **Mode drift.** A file's mode is set at CREATION from the process umask, and
   the backend has been started by several different launchers over the box's
   life. The result was that the two MOST sensitive documents had the LOOSEST
   modes - ``calendar.json`` (medications, bills) at ``0644`` and ``fitfam.json``
   (a named 15-year-old's health history) at ``0664`` - while ``freshtable.json``
   was already ``0600``. ``open(path,'w')`` truncates in place and therefore
   PRESERVES whatever mode the file already had, so the drift was permanent:
   nothing in the write path would ever have corrected it.
2. **Non-atomic replacement.** A truncate-then-write is a window in which the
   document is empty or short. That window is also the reason someone once
   reached for a ``.bak-`` copy as a safety net - which is how a world-readable
   stale copy of the family calendar came to sit on disk for nine days. An
   atomic write removes the motive for the backup, so this module is the fix for
   BOTH findings, not just the mode one.

WHAT THIS IS NOT
----------------
This is permissions and durability, not encryption. A ``0600`` file is protected
from other UNIX accounts on the box and from a static file server; it is NOT
protected from the appliance's own account, from physical media access, or from
a backup that copies it elsewhere. At-rest encryption for ``calendar.json`` and
``fitfam.json`` is a separate, scoped piece of work (the ``_project_aura_doc`` /
``_decrypt_aura_doc`` pattern) and is deliberately not attempted here.

PLATFORM
--------
``os.chmod`` on Windows only toggles the read-only bit, so every mode operation
is best-effort and never fatal - the dev checkout must keep working. The
security property is asserted on POSIX, which is what the appliance is.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import time

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

#: Owner-only. Every app document, without exception - the tier of the document
#: does not change the floor, because the floor is what stops the NEXT app from
#: being the one that leaks.
APP_DATA_FILE_MODE = 0o600

#: Owner-only directory. Removes the "list the household's apps" read that a
#: 0755 directory grants to every account on the box.
APP_DATA_DIR_MODE = 0o700

#: Bits that must NOT be set on an app-data file. Group and other, all classes.
_LOOSE_BITS = (stat.S_IRWXG | stat.S_IRWXO)

#: Only these are swept/tightened. A sweep that chmods everything it finds is a
#: sweep that will one day chmod something that needed to be readable.
_SWEEP_SUFFIXES = (".json",)

#: POSIX permission bits are a POSIX mechanism. On Windows ``os.stat`` reports a
#: synthetic 0666/0777 and ``os.chmod`` only toggles the read-only flag, so a
#: mode check there would report every file as world-readable and every chmod as
#: having failed. Reporting "unsupported platform" is the honest answer;
#: reporting "0600, secured" on a system where that is not what happened would
#: be a status line that lies, which is worse than no status line at all. The
#: appliance is Linux, which is where the guarantee has to hold.
MODES_ENFORCEABLE = (os.name == "posix")


def _chmod_quiet(path: str, mode: int) -> bool:
    """Best-effort chmod. Returns True if the call went through.

    Never raises: a permissions tweak must not be the thing that stops the
    appliance from writing the household's calendar.
    """
    try:
        os.chmod(path, mode)
        return True
    except OSError as exc:
        logger.debug("app-data chmod failed for %s: %s",
                     os.path.basename(path), exc)
        return False


def _is_loose(path: str) -> bool:
    """True if the file grants ANY group/other access.

    Always False where modes are not enforceable (see
    :data:`MODES_ENFORCEABLE`), so the sweep degrades to a no-op on a dev
    checkout instead of logging a permissions error on every start.
    """
    if not MODES_ENFORCEABLE:
        return False
    try:
        return bool(stat.S_IMODE(os.stat(path).st_mode) & _LOOSE_BITS)
    except OSError:
        return False


def ensure_secure_dir(directory: str) -> None:
    """Create ``directory`` if needed and hold it at :data:`APP_DATA_DIR_MODE`.

    Idempotent and cheap enough to call on every write: ``os.makedirs`` with
    ``exist_ok`` is a single stat in the common case, and the chmod only runs
    when the mode is actually loose.
    """
    try:
        os.makedirs(directory, mode=APP_DATA_DIR_MODE, exist_ok=True)
    except OSError as exc:
        logger.debug("app-data dir create failed: %s", exc)
        return
    if not MODES_ENFORCEABLE:
        return
    try:
        if stat.S_IMODE(os.stat(directory).st_mode) & _LOOSE_BITS:
            _chmod_quiet(directory, APP_DATA_DIR_MODE)
    except OSError:
        pass


#: How long to keep retrying the rename on Windows before giving up.
_REPLACE_ATTEMPTS = 12
_REPLACE_DELAY = 0.02


def _replace_with_retry(tmp: str, path: str) -> None:
    """``os.replace``, retried briefly on Windows.

    On POSIX a rename over an open file is unconditional, which is the whole
    reason this module writes-then-renames. Windows is different: the
    destination cannot be replaced while ANY handle to it is open, and a reader
    that has just closed the file can still be holding one for a few
    milliseconds, because the release is not synchronous under some filesystem
    filters (indexers and AV are the usual ones).

    Retrying is not a weaker guarantee. The replace is still atomic when it
    happens, and the previous document is still intact until it does; this only
    declines to fail the write over a millisecond of Windows bookkeeping. The
    appliance is Linux, where the loop runs exactly once.
    """
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if os.name != "nt" or attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_DELAY)


def write_json_secure(path: str, payload, *, indent: int = 2,
                      default=str, ensure_ascii: bool = False) -> None:
    """Serialise ``payload`` to ``path`` atomically, at :data:`APP_DATA_FILE_MODE`.

    Order is load-bearing:

    1. ``mkstemp`` in the SAME directory (a cross-device ``os.replace`` would
       not be atomic) - and ``mkstemp`` already creates at ``0600``, so the
       plaintext never exists at a looser mode even for an instant.
    2. Serialise, ``flush``, ``fsync`` - the bytes are on the platter before
       anything points at them.
    3. ``os.replace`` (via :func:`_replace_with_retry`) - atomic on POSIX and on
       Windows. A reader either sees the whole old document or the whole new
       one, never a truncated file.
    4. ``fsync`` the directory on POSIX so the rename itself survives power loss.

    On any failure the temp file is removed and the ORIGINAL document is left
    exactly as it was. That is the property that makes ad-hoc ``.bak`` copies
    unnecessary - and ad-hoc ``.bak`` copies are how household data ends up
    world-readable and outside every retention rule.

    Raises whatever the serialisation or the filesystem raises: a caller that
    cannot persist must find out, not silently continue with a stale document.
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    ensure_secure_dir(directory)

    base = os.path.basename(path)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix="." + base + ".",
                               suffix=".tmp")
    # What the ``finally`` still has to clean up, tracked apart from the
    # descriptor and path themselves: each is cleared the moment ownership
    # moves on (the descriptor to fdopen, the path to the replace), and the
    # names above stay exactly what mkstemp handed back.
    unclosed_fd: int | None = fd
    unlinked_tmp: str | None = tmp
    try:
        # mkstemp is already 0600; assert it explicitly so the guarantee is
        # visible here rather than inherited from a stdlib detail.
        _chmod_quiet(tmp, APP_DATA_FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            # fdopen owns it now; the finally must not double-close
            unclosed_fd = None
            json.dump(payload, handle, indent=indent, default=default,
                      ensure_ascii=ensure_ascii)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(tmp, path)
        unlinked_tmp = None
    finally:
        if unclosed_fd is not None:
            try:
                os.close(unclosed_fd)
            except OSError:
                pass
        if unlinked_tmp is not None:
            try:
                os.unlink(unlinked_tmp)
            except OSError:
                pass

    # Defensive: the replaced-in file carries the temp file's mode, but a future
    # platform quirk here would be silent, so re-assert rather than assume.
    if _is_loose(path):
        _chmod_quiet(path, APP_DATA_FILE_MODE)

    if hasattr(os, "O_DIRECTORY"):  # POSIX only
        try:
            dir_fd = os.open(directory, os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
