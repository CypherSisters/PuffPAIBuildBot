"""The owner's on/off switches for outside services, kept in the state folder.

One small file, ``<state>/outside_switches.json``::

    {"schema": "puffpai_outside_switches/v1",
     "services": {"weather": {"on": false, "by": "owner", "at": "..."}},
     "reset": {"at": "...", "kept_as": "outside_switches.damaged-....json",
               "seen_at": null}}

``reset`` is only there after a damaged file was found (see below).

Beside it, ``<state>/outside_switches.guard.json`` remembers two things that
must outlive the switch file itself: that switches were ever written here,
and the reset record. And ``<state>/outside_switches.lock`` is the lock file
every process takes before it changes either (it holds nothing).

Who reads it
------------
* ``egress_doors`` for the built-in services (weather, town lookup, ...):
  the environment wins, then THIS file, then the shipped config, then the
  wizard's choice. So a one-tap OFF here beats a yes written anywhere else.
* ``source_fetch`` (``library``), ``catalogs.live`` (``catalog_refresh``),
  ``egress_doors.build_probe_get`` (``sample_look``) and ``wizard.netcheck``
  (``internet_check``). For these a missing entry means "as built" and an
  entry with ``on: false`` refuses every request before a socket opens.
* Live cards keep their own yes/no in ``egress_app_doors.json`` (see
  ``egress_doors.set_app_door_consent``); they are not stored here.

Fail closed, and stay closed
----------------------------
A file that is too big, is not JSON, is not in the expected shape, or holds
even ONE entry that is not a proper switch (``{"on": "false"}``,
``null``, a bad service name) is DAMAGED. It is never overwritten, and a
damaged entry is never quietly dropped to "as built". Instead, in this order:

1. the reset record is written to the guard file FIRST,
2. the damaged file is renamed aside
   (``outside_switches.damaged-<UTC time>.json``, same folder),
3. a fresh file is written holding no switches and the reset record.

If step 1 fails nothing is moved and every service reads as off. If step 3
fails the guard's reset record still stands, so the next read (in this
process or another) finds no switch file, sees the reset, and every service
still reads as off. A switch file that goes MISSING after switches were ever
written here is damage too (the guard remembers the writing), and gets the
same reset. From a reset on, every service without a switch the owner set
AFTER it reads as OFF -- not "as built" -- so the owner's earlier OFF
switches cannot come back on by themselves, and a later write (switching one
service on or off) never re-enables anything else. The owner is
told in plain words on "What leaves your computer"
(:func:`reset_notice`), and switches back on only what they want, one at a
time, each with its own consent ticket. Hiding that note
(:func:`acknowledge_reset`) switches nothing on.

Every change (a switch, the reset, the note being seen) happens while this
process holds ``outside_switches.lock`` (an operating-system lock, so it
works across processes and is let go if a process dies). A second process
therefore cannot write between another's rename and its fresh write.

A file that exists but cannot be READ (permissions, a disk error), or a lock
that stays taken, reads as every service off, and every write is refused
until it can be read: nothing is written over a file we could not look at.

What is NOT covered: deleting the whole state folder (guard included) starts
a new build, whose services are "as built". Putting an OLDER, well-formed
copy of the file back is read as the owner's choice.

Writing ON is the caller's responsibility to have earned: in the running
server only :mod:`puffpai.outside_services` writes ``on: true``, and only after
a consent ticket was redeemed (:mod:`puffpai.security.consent_tickets`).

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

FILE_NAME = "outside_switches.json"
GUARD_NAME = "outside_switches.guard.json"
LOCK_NAME = "outside_switches.lock"
SCHEMA = "puffpai_outside_switches/v1"
GUARD_SCHEMA = "puffpai_outside_switches_guard/v1"
MAX_FILE_BYTES = 64 * 1024
DAMAGED_PREFIX = "outside_switches.damaged-"
#: How long a change waits for another process to finish its own.
LOCK_TIMEOUT = 10.0

_SID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")
_BY = frozenset({"owner", "wizard", "installer", "migration"})
_KEPT_RE = re.compile(r"^outside_switches\.damaged-[0-9TZ]{16,24}(?:-\d{1,3})?\.json$")

_lock = threading.RLock()
_depth = 0  # how deep this process is inside _changing() (guarded by _lock)
_cache: dict[str, Any] = {"path": None, "stamp": None, "services": {},
                          "broken": False, "reset": None}


class SwitchesUnreadable(OSError):
    """The switch file exists but cannot be read, so nothing is written."""

    def __init__(self, message: str = "") -> None:
        super().__init__(message or (
            "The list of outside switches on this computer could not be "
            "read, so nothing was changed."))
        self.message = str(self)


class SwitchesBusy(SwitchesUnreadable):
    """Another program kept the switch list busy for too long."""

    def __init__(self) -> None:
        super().__init__("Another program on this computer was changing the "
                         "list of outside switches, so nothing was changed. "
                         "Try again in a moment.")


def path() -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), FILE_NAME)


def guard_path() -> str:
    return os.path.join(os.path.dirname(path()), GUARD_NAME)


def forget() -> None:
    """Drop the cached copy (tests, and after an outside edit)."""
    with _lock:
        _cache.update({"path": None, "stamp": None, "services": {},
                       "broken": False, "reset": None})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_atomic(target: str, payload: dict) -> None:
    from puffpai import fsutil
    fsutil.atomic_write(target, json.dumps(payload, indent=2, sort_keys=True))


@contextlib.contextmanager
def _changing() -> Iterator[None]:
    """This thread, and this process, are the only ones changing the files.

    Re-entrant within a thread (a read that finds damage inside a write).
    Raises :class:`SwitchesBusy` when another process holds the lock too long.
    """
    global _depth
    from puffpai import fsutil
    with _lock:
        if _depth:
            _depth += 1
            try:
                yield
            finally:
                _depth -= 1
            return
        lock_file = os.path.join(os.path.dirname(path()), LOCK_NAME)
        try:
            held = fsutil.file_lock(lock_file, timeout=LOCK_TIMEOUT)
            held.__enter__()
        except OSError as exc:
            logger.warning("outside switches: could not take the lock (%s)", exc)
            raise SwitchesBusy() from exc
        _depth = 1
        try:
            yield
        finally:
            _depth = 0
            held.__exit__(None, None, None)


def _valid_reset(value: Any) -> Optional[dict]:
    if not isinstance(value, dict) or not isinstance(value.get("at"), str):
        return None
    kept = value.get("kept_as")
    seen = value.get("seen_at")
    return {"at": value["at"][:40],
            "kept_as": kept if isinstance(kept, str) and _KEPT_RE.match(kept) else "",
            "seen_at": seen[:40] if isinstance(seen, str) else None}


def _valid_entry(sid: Any, entry: Any) -> bool:
    """A proper switch: a service name, and ``on`` that is True or False."""
    return (isinstance(sid, str) and bool(_SID_RE.match(sid))
            and isinstance(entry, dict)
            and isinstance(entry.get("on"), bool)
            and isinstance(entry.get("by", ""), str)
            and isinstance(entry.get("at", ""), str))


# ── the guard: "switches were written here", and the reset record ───────────

def _read_guard() -> tuple[bool, Optional[dict], Any]:
    """``(written, reset, stamp)`` from the guard file.

    A guard that cannot be read or is not in shape counts as "written, and
    reset" -- fail closed: every service the owner has not switched since
    reads as off. Its stamp is then ``"damaged"``, so the next change
    rewrites it whole.
    """
    target = guard_path()
    try:
        stat = os.stat(target)
    except FileNotFoundError:
        return False, None, None
    except OSError:
        return True, {"at": _now_iso(), "kept_as": "", "seen_at": None}, "damaged"
    stamp = (stat.st_mtime_ns, stat.st_size)
    try:
        if stat.st_size > MAX_FILE_BYTES:
            raise ValueError("too big")
        with open(target, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not (isinstance(data, dict) and data.get("schema") == GUARD_SCHEMA
                and data.get("written") is True):
            raise ValueError("not in shape")
        reset = None
        if data.get("reset") is not None:
            reset = _valid_reset(data["reset"])
            if reset is None:
                raise ValueError("reset record damaged")
        return True, reset, stamp
    except (OSError, ValueError):
        return True, {"at": _now_iso(), "kept_as": "", "seen_at": None}, "damaged"


def _write_guard(reset: Optional[dict]) -> None:
    """Record "switches were written here" (and *reset*). Hold _changing()."""
    payload: dict[str, Any] = {"schema": GUARD_SCHEMA, "written": True,
                               "reset": reset}
    _write_atomic(guard_path(), payload)


def _ensure_guard(reset: Optional[dict] = None) -> Optional[dict]:
    """Make sure the guard says "written" and holds a reset record if there is
    one (theirs or *reset*). Returns the reset record now in force. Hold
    _changing(). Raises OSError when the guard cannot be written."""
    written, current, stamp = _read_guard()
    wanted = current or reset
    if written and current == wanted and stamp != "damaged":
        return current
    _write_guard(wanted)
    return wanted


# ── reading ─────────────────────────────────────────────────────────────────

def _parse(target: str, stat: os.stat_result) -> tuple[dict[str, dict], Optional[dict]]:
    """``(services, reset)`` of a switch file. ValueError when damaged."""
    if stat.st_size > MAX_FILE_BYTES:
        raise ValueError("switch file is larger than 64 KB")
    with open(target, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not (isinstance(data, dict) and data.get("schema") == SCHEMA
            and isinstance(data.get("services"), dict)):
        raise ValueError("switch file is not in the expected shape")
    services: dict[str, dict] = {}
    for sid, entry in data["services"].items():
        if not _valid_entry(sid, entry):
            # A damaged entry is damage, never "as built".
            raise ValueError(f"switch file has a damaged entry ({str(sid)[:40]!r})")
        services[sid] = dict(entry)
    reset = None
    if "reset" in data:
        reset = _valid_reset(data["reset"])
        if reset is None:
            raise ValueError("switch file's reset record is damaged")
    return services, reset


def _quarantine(target: str, why: str) -> bool:
    """Keep a damaged file aside and start a fresh one in reset mode.

    Order matters (see the module note): the reset record goes to the guard
    FIRST, then the damaged file is renamed, then the fresh file is written.
    Returns True when the reset now stands (even if the fresh write failed:
    the guard carries it), False when the guard could not be written or the
    file could not be moved (the caller reads everything as off).
    """
    with _changing():
        # Another process may have dealt with it while we waited.
        try:
            stat = os.stat(target)
            _parse(target, stat)
            return True
        except FileNotFoundError:
            return True
        except (OSError, ValueError):
            pass
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        folder = os.path.dirname(target)
        kept = f"{DAMAGED_PREFIX}{stamp}.json"
        for n in range(1, 50):
            if not os.path.exists(os.path.join(folder, kept)):
                break
            kept = f"{DAMAGED_PREFIX}{stamp}-{n}.json"
        reset = {"at": _now_iso(), "kept_as": kept, "seen_at": None}
        try:
            _write_guard(reset)
        except OSError as exc:
            logger.warning("outside switch file %s is damaged (%s) and the "
                           "reset could not be recorded (%s); it was left as it "
                           "is and every service stays off", target, why, exc)
            return False
        try:
            os.rename(target, os.path.join(folder, kept))
        except OSError as exc:
            logger.warning("outside switch file %s is damaged (%s) and could not "
                           "be kept aside (%s); every service stays off", target,
                           why, exc)
            return False
        try:
            _write_atomic(target, {"schema": SCHEMA, "services": {},
                                   "reset": reset})
        except OSError as exc:
            logger.warning("outside switch file: the damaged copy was kept as %s "
                           "but a fresh one could not be written (%s); the reset "
                           "is recorded and every service stays off", kept, exc)
            return True
        logger.warning("outside switch file %s was damaged (%s). It was kept as "
                       "%s, and every outside service is off until the owner "
                       "switches it on again", target, why, kept)
        return True


def _missing(target: str) -> tuple[dict[str, dict], bool, Optional[dict]]:
    """No switch file. As built -- unless switches were ever written here."""
    written, reset, _ = _read_guard()
    if not written:
        return {}, False, None
    if reset:
        return {}, False, reset
    # Switches were written here and the file is gone: damage.
    try:
        with _changing():
            if os.path.exists(target):
                return {}, True, None  # it came back meanwhile: read it next time
            reset = _ensure_guard({"at": _now_iso(), "kept_as": "",
                                   "seen_at": None})
    except OSError as exc:
        logger.warning("outside switch file %s is missing and the reset could "
                       "not be recorded (%s); every service stays off", target, exc)
        return {}, True, None
    logger.warning("outside switch file %s is missing although switches were "
                   "set here; every outside service is off until the owner "
                   "switches it on again", target)
    return {}, False, reset


def _load(_again: bool = True) -> tuple[dict[str, dict], bool, Optional[dict]]:
    """``(services, broken, reset)``. Re-read when either file changes.

    ``broken`` means "could not be read or kept aside": every service off, no
    writes. ``reset`` is the record left after a damaged or missing file.
    """
    try:
        target = path()
    except Exception as exc:
        logger.debug("outside switches path unavailable: %s", exc)
        return {}, True, None
    try:
        stat = os.stat(target)
    except FileNotFoundError:
        forget()
        return _missing(target)
    except OSError as exc:
        logger.warning("outside switch file %s cannot be looked at (%s); every "
                       "service it governs reads as switched off", target, exc)
        return {}, True, None
    written, guard_reset, guard_stamp = _read_guard()
    stamp = (stat.st_mtime_ns, stat.st_size, guard_stamp)
    with _lock:
        if _cache["path"] == target and _cache["stamp"] == stamp:
            return (dict(_cache["services"]), bool(_cache["broken"]),
                    dict(_cache["reset"]) if _cache["reset"] else None)
    try:
        services, reset = _parse(target, stat)
    except ValueError as exc:  # json.JSONDecodeError and UnicodeError included
        if _again:
            try:
                if _quarantine(target, str(exc)):
                    return _load(False)
            except SwitchesBusy:
                pass
        return {}, True, None
    except OSError as exc:
        logger.warning("outside switch file %s unreadable (%s); every service "
                       "it governs reads as switched off", target, exc)
        return {}, True, None
    reset = reset or guard_reset
    if not written or guard_stamp == "damaged" or (reset and not guard_reset):
        # An older file, from before the guard: remember that switches were
        # written here (and any reset it holds), so losing the file later is
        # seen as damage. Best effort: reading must not fail over it.
        try:
            with _changing():
                _ensure_guard(reset)
            written, guard_reset, guard_stamp = _read_guard()
            stamp = (stat.st_mtime_ns, stat.st_size, guard_stamp)
        except OSError as exc:
            logger.debug("outside switches guard not written: %s", exc)
    with _lock:
        _cache.update({"path": target, "stamp": stamp, "services": services,
                       "broken": False, "reset": reset})
    return dict(services), False, (dict(reset) if reset else None)


def get(sid: str) -> Optional[bool]:
    """The owner's switch for *sid*: True, False, or None (never set).

    Fails closed: an unreadable file answers False for every service, and
    after a damaged or missing file every service the owner has not switched
    since answers False as well.
    """
    services, broken, reset = _load()
    if broken:
        return False
    entry = services.get(str(sid or "").strip().lower())
    if isinstance(entry, dict):
        return entry["on"]
    return False if reset else None


def entry(sid: str) -> Optional[dict]:
    """The stored record for *sid* (``on``, ``by``, ``at``), or None."""
    services, broken, reset = _load()
    if broken:
        return {"on": False, "by": "", "at": "", "broken": True}
    found = services.get(str(sid or "").strip().lower())
    if isinstance(found, dict):
        return dict(found)
    if reset:
        return {"on": False, "by": "", "at": reset["at"], "reset": True}
    return None


def is_off(sid: str) -> bool:
    """True when *sid* may not be used now. Every "as built" service asks this.

    Off when the owner switched it off (or the file is broken/reset). Also off
    when the Bridge says so (:func:`puffpai.security.bridge_guard.switch_allows`): with
    the Bridge up a service nobody switched on is off rather than "as built",
    and on a build that keeps everything on this computer every service is
    off. Fails closed: if the Bridge cannot be read, the service is off.
    """
    if get(sid) is False:
        return True
    try:
        from puffpai.security import bridge_guard
        return not bridge_guard.switch_allows(sid, get)
    except Exception as exc:  # the Bridge unreadable: off, never on
        logger.warning("outside switches: the Bridge could not be read (%s); "
                       "treating the service as off", type(exc).__name__)
        return True


def reset_notice() -> Optional[dict]:
    """What the owner should be told, or None when there is nothing to say.

    ``{"kind": "reset", "at", "kept_as", "seen"}`` after a damaged or missing
    file, or ``{"kind": "unreadable"}`` while the file cannot be read.
    """
    services, broken, reset = _load()
    del services
    if broken:
        return {"kind": "unreadable", "at": "", "kept_as": "", "seen": False}
    if reset:
        return {"kind": "reset", "at": reset["at"], "kept_as": reset["kept_as"],
                "seen": bool(reset.get("seen_at"))}
    return None


def _write_switches(services: dict, reset: Optional[dict]) -> None:
    """Write the switch file. Hold _changing(). The guard is written FIRST."""
    reset = _ensure_guard(reset)
    payload: dict[str, Any] = {"schema": SCHEMA, "services": services}
    if reset:
        payload["reset"] = reset
    _write_atomic(path(), payload)
    forget()


def acknowledge_reset() -> bool:
    """The owner read the note. Hides it; switches NOTHING on. True if done."""
    with _changing():
        forget()
        services, broken, reset = _load()
        if broken:
            raise SwitchesUnreadable()
        if not reset or reset.get("seen_at"):
            return False
        reset["seen_at"] = _now_iso()
        _write_guard(reset)
        _write_switches(services, reset)
    logger.info("outside switches: the owner saw the reset note")
    return True


def set_switch(sid: str, on: bool, *, by: str = "owner",
               when: Optional[str] = None) -> dict:
    """Record the switch for *sid*. Returns the stored entry.

    Every other entry, and a reset record, is kept as it is: one write changes
    one service. A file that cannot be read is not written over
    (:class:`SwitchesUnreadable`); a damaged or missing one was already dealt
    with by the read (reset recorded first), so this write lands on the
    fresh file and cannot switch any other service back on. The whole
    read-change-write holds the cross-process lock.
    """
    key = str(sid or "").strip().lower()
    if not _SID_RE.match(key):
        raise ValueError("not a service id")
    if not isinstance(on, bool):
        raise ValueError("on must be True or False")
    who = by if by in _BY else "owner"
    stamp = when or _now_iso()
    with _changing():
        forget()
        services, broken, reset = _load()
        if broken:
            logger.warning("outside switch %s not recorded: the switch file "
                           "cannot be read", _shown(key))
            raise SwitchesUnreadable()
        services[key] = {"on": on, "by": who, "at": stamp}
        _write_switches(services, reset)
    logger.info("outside service %s switched %s by %s", _shown(key),
                "on" if on else "off", who)
    return dict(services[key])


def _shown(name: Any) -> str:
    """A service id as a log line may show it: plain and key-safe, else
    "(not shown)" (an id is checked for its shape only). Fail closed."""
    try:
        from puffpai import web_search
        return web_search.log_name(name, placeholder="(not shown)", limit=64)
    except Exception:
        return "(not shown)"


def clear(sid: str) -> None:
    """Remove *sid*'s entry, so it goes back to "as built".

    After a reset, "as built" means off: clearing never switches anything on.
    """
    key = str(sid or "").strip().lower()
    with _changing():
        forget()
        services, broken, reset = _load()
        if broken or key not in services:
            return
        services.pop(key, None)
        _write_switches(services, reset)


# -- switch ids for rows other modules register -------------------------------
#
# A browser app's outside site and a locked-box project each get a row on
# "What leaves your computer" (``outside_services.register_rows``). Their
# switch ids are fixed-shape hashes, so an app id, a host or a project name
# (outside text) never becomes a switch id or a log field as it is. Both fit
# ``_SID_RE`` (28 characters). The outside-traffic log accepts exactly these
# shapes as service ids (``egress_ledger``), although they look random.

def switch_id_app(app_id: str, host: str) -> str:
    """``"app_" + sha256(app_id + "|" + host)[:24]``: one app, one outside site
    (a host or the registrable name a row groups hosts under). Exact, no
    normalising: every caller computes the same id from the same two strings."""
    if not isinstance(app_id, str) or not isinstance(host, str)             or not app_id or not host:
        raise ValueError("an app id and a host are needed")
    digest = hashlib.sha256((app_id + "|" + host).encode("utf-8")).hexdigest()
    return "app_" + digest[:24]


def switch_id_box(project: str) -> str:
    """``"box_" + sha256(project)[:24]``: one project in the locked box."""
    if not isinstance(project, str) or not project:
        raise ValueError("a project name is needed")
    return "box_" + hashlib.sha256(project.encode("utf-8")).hexdigest()[:24]


__all__ = ["FILE_NAME", "GUARD_NAME", "LOCK_NAME", "SCHEMA", "SwitchesBusy",
           "SwitchesUnreadable", "acknowledge_reset", "clear", "entry",
           "forget", "get", "guard_path", "is_off", "path", "reset_notice",
           "set_switch", "switch_id_app", "switch_id_box"]
