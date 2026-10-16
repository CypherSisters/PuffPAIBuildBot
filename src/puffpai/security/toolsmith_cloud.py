"""Toolsmith's cloud model: the ONE agent that may think with a cloud model.

The owner's decision, 2026-10-14: "I want Toolsmith to be the ONLY
cloud-dependent agent. Everything else is locally run." Provider: Anthropic.

What this module guarantees
---------------------------
1. **Local by default.** The setting ``toolsmith.model`` lives in
   ``<state>/toolsmith_model.json``. No file, junk, or a cloud choice without
   her recorded yes all read as ``{"where": "local"}``: Toolsmith uses the
   model on this computer exactly as before.
2. **Toolsmith only (the scope guard).** :func:`send` refuses every call that
   does not carry a :class:`Scope`, and a Scope is minted ONLY by
   :func:`toolsmith_scope`, which refuses any caller but
   ``puffpai.build.toolsmith_run`` (its install and make loop) and
   Toolsmith's own finding work in ``puffpai.build.apps`` (``search_words``
   and ``rank`` only, :data:`FIND_CALLERS`; a "find" scope sends no tools and
   asks for a small reply). Chat, voice, interpret, decide, Drawbridge,
   Archivist, routines and every other agent cannot reach this path. A forged Scope (``object.__new__``)
   has no mark and is refused. ``tests/test_toolsmith_cloud.py`` also fails
   if any other module names :func:`toolsmith_scope`.
3. **Her key only in the vault**, through the curated key flow
   (``config/catalog_seeds/keys.json`` row ``anthropic``, vault only, card
   backed: never works while the vault is locked). It is sent only by
   :func:`keys.keyed_post`, bound to api.anthropic.com as a header. Never in
   a log, a state file, an answer or the browser.
4. **Only through the Bridge.** The ``toolsmith_cloud`` switch on "What
   leaves your computer" must be on; every request writes one line to the
   outside-traffic log (``what``: Toolsmith install of an app; bytes only).
5. **What is sent is cleaned first.** Every text a person or a program on
   this computer wrote (the task, tool results) passes the secret detector
   and the PII screen before it leaves, and a health statement about a person
   loses the person and the amounts (:func:`clean`). If the check cannot
   run, nothing is sent. The model's own earlier turns go back exactly as
   they came (the API requires it); they only ever saw cleaned text.
6. **Never silent about money.** Each call's ``usage`` is kept per job with a
   cost estimate (:func:`cost_usd`); Settings shows "Toolsmith used about $X
   so far".

Residual, stated plainly
------------------------
* Once she chooses Anthropic, an app's README, file list, Toolsmith's
  commands and their output go to Anthropic under its terms. The screens
  catch known key shapes and the PII classifier's kinds, not every private
  fact; an app folder holding her own data would be read like any app.
* The scope guard is a code rule inside one Python process: code already
  running in the process could call the private parts. It stops mistakes and
  new callers, not a hostile module.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

SETTING_FILE = "toolsmith_model.json"
USAGE_FILE = "toolsmith_cloud_usage.json"
SCHEMA = "puffpai_toolsmith_model/v1"
USAGE_SCHEMA = "puffpai_toolsmith_usage/v1"
DOOR = "toolsmith_cloud"
#: The one module that may mint a Scope (Toolsmith's install and make loop).
SCOPE_CALLER = "puffpai.build.toolsmith_run"
#: Toolsmith's own finding work (owner, 2026-10-14): the GitHub search phrases
#: and the ranking of what was found. Module -> the ONLY functions that may
#: mint a "find" scope there. Nothing else, ever.
FIND_CALLERS: dict[str, frozenset] = {
    "puffpai.build.apps": frozenset({"search_words", "rank"}),
}
#: A finding ask is small: no tools, a short JSON answer.
FIND_MAX_TOKENS = 2048

ANTHROPIC_VERSION = "2023-06-01"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16000
#: A cloud reply may take a while (thinking); a hung one may not.
CALL_TIMEOUT = 300.0
#: The largest request this sends. A try that grows past this (or past
#: CONTEXT_TOKENS) ends with a summary; earlier turns are never trimmed.
MAX_BODY_BYTES = 3_000_000
CONTEXT_TOKENS = 180_000
RETRIES = 4
MAX_WAIT_S = 60.0

#: Prices in US dollars per million tokens. Cache writes cost 1.25x input.
MODELS: dict[str, dict[str, Any]] = {
    "claude-opus-5-5": {"name": "Opus 5.5", "in": 4.0, "out": 20.0, "cache_read": 0.20},
    "claude-sonnet-5-5": {"name": "Sonnet 5.5", "in": 2.0, "out": 10.0, "cache_read": 0.20},
}
CACHE_WRITE_FACTOR = 1.25
DEFAULT_MODEL = "claude-opus-5-5"

#: Providers. DATA written by a person; only Anthropic is offered today. A new
#: one needs its own door, key row, adapter and prices.
PROVIDERS: dict[str, dict[str, Any]] = {
    "anthropic": {"label": "Anthropic", "host": "api.anthropic.com",
                  "url": "https://api.anthropic.com/v1/messages",
                  "key_id": "anthropic", "models": MODELS, "default": DEFAULT_MODEL},
}

#: The consent sentence, shown in Settings and minted into her ticket. It must
#: stay true of what is sent: the finding asks (build/apps.py search_words and
#: rank), the install and make loop (build/toolsmith_run.py task, download,
#: read_docs, terminal, server_log, read_page), a folder of her own code read
#: like any app, and :func:`clean` before anything leaves. Changing it asks
#: every owner on Anthropic to agree again (read_setting, RECONSENT_LINE).
CONSENT_TEXT = ("When Toolsmith finds and installs apps, it sends Anthropic your goal "
                "and your bot's jobs, the names and descriptions of the apps it found, "
                "each app's README, docs and file list, the start of its setup files, "
                "the commands it runs with their output, and what the app's pages "
                "show. If you point it at a folder of your own, it reads that folder "
                "the same way. Keys and personal details it spots are taken out first. "
                "It never sends your chats or your vault. Everything else in BuildBot "
                "stays on this computer.")

KEY_LINE = "Toolsmith's Anthropic key was not accepted. Check it in Settings."
OFFLINE_LINE = ("Toolsmith could not reach Anthropic, so it uses the model on this "
                "computer for this app.")

_lock = threading.RLock()
_no_fallback: set[str] = set()      # models that answered 400 to "fallbacks"
_clean_cache: dict[str, str] = {}
_classifier_box: dict[str, Any] = {}


class CloudError(RuntimeError):
    """A cloud step that did not happen. ``message`` is plain words for her."""

    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class CloudOffline(CloudError):
    """Anthropic could not be reached: that try uses the local model."""


class ScopeRefused(PermissionError):
    """A caller other than Toolsmith's install loop asked for the cloud model."""


# ── the scope token ──────────────────────────────────────────────────────────

_MINT = object()


class Scope:
    """Proof that the caller is Toolsmith's install and make loop. Made only
    by :func:`toolsmith_scope`; carries the job it is for."""

    __slots__ = ("_mark", "job_id", "job_name", "kind")

    def __init__(self, mark: object, job_id: str, job_name: str, kind: str = "install"):
        if mark is not _MINT:
            raise ScopeRefused("only Toolsmith's install loop may use the cloud model")
        self._mark = mark
        self.job_id = str(job_id or "")[:80]
        self.job_name = str(job_name or "")[:120]
        self.kind = "find" if kind == "find" else "install"


def toolsmith_scope(job_id: str, job_name: str = "") -> Scope:
    """The scope token, for ``puffpai.build.toolsmith_run`` (install) and the
    functions named in :data:`FIND_CALLERS` (find) ONLY."""
    frame = sys._getframe(1)
    caller = frame.f_globals.get("__name__", "")
    if caller == SCOPE_CALLER:
        return Scope(_MINT, job_id, job_name)
    if frame.f_code.co_name in FIND_CALLERS.get(caller, frozenset()):
        return Scope(_MINT, job_id, job_name, "find")
    logger.warning("toolsmith cloud: scope refused for %s.%s", caller[:80],
                   frame.f_code.co_name[:40])
    raise ScopeRefused("only Toolsmith's install loop may use the cloud model")


def _check_scope(scope: Any) -> Scope:
    if type(scope) is not Scope or getattr(scope, "_mark", None) is not _MINT:
        raise ScopeRefused("only Toolsmith's install loop may use the cloud model")
    return scope


# ── the session key lease ────────────────────────────────────────────────────
#
# The vault locks itself after a few idle minutes; a building session is much
# longer (owner, 2026-10-14: "Vault closed out again and messed up the
# toolsmith"). So Toolsmith holds its Anthropic key for the SESSION: read ONCE
# from the OPEN vault into this process's memory (a bytearray, never a file, a
# log, a state file or an answer), used only through the scope token, only by
# keys.keyed_post, only for api.anthropic.com. It is taken when a cloud run
# starts, when she unlocks the vault while cloud Toolsmith is on, when she
# saves or replaces its key (the vault is open then: the wizard's Toolsmith
# step and Settings), when she approves the wizard's plan or its build starts
# with the vault open, and when she gives a build yes with the vault open
# (owner, 2026-10-15: "a normal user wouldn't know to do this"). The idle lock
# leaves it alone (that is the point). It ends when Toolsmith has done nothing
# for her chosen time (1 hour by default, 4 hours, or "until I lock the
# vault"; a wizard build running, and each wizard step she takes, count as
# work: a wizard left alone does not), after
# LEASE_MAX_S whatever she chose, or at once when she locks the vault by
# hand, turns cloud Toolsmith off, or replaces or removes the key (a new key
# saved with the vault open starts a new lease with it at once). Every other
# vault item keeps the 5-minute idle lock.

#: The hard cap on one lease, whatever she chose.
LEASE_MAX_S = 24 * 60 * 60
KEEP_FILE = "toolsmith_key_ready.json"
KEEP_SCHEMA = "puffpai_toolsmith_key_ready/v1"
#: Her choice -> seconds of no Toolsmith work before the key is let go (None: her lock).
KEEP_CHOICES: dict[str, Optional[int]] = {"1h": 60 * 60, "4h": 4 * 60 * 60, "lock": None}
KEEP_DEFAULT = "1h"
KEEP_QUESTION = "Keep Toolsmith's key ready while I'm building"
KEEP_LABELS = {"1h": "1 hour", "4h": "4 hours", "lock": "until I lock the vault"}
#: How long a run waits in place for her to open the vault, and how often it looks.
WAIT_LIMIT_S = 2 * 60 * 60
WAIT_POLL_S = 15.0
LEASE_ON_LINE = "Toolsmith keeps its Anthropic key ready while you build"
LEASE_CARD_LINE = "Toolsmith's key is ready until {until}. Lock the vault to stop it."
VAULT_WAIT_LINE = "Unlock your vault to let Toolsmith go on."
_LEASE_STARTS = {
    "run": "a build started",
    "find": "Toolsmith looked for apps",
    "unlock": "you unlocked the vault",
    "yes": "you said yes to the build",
    "saved": "you saved its key",
    "plan": "you approved the plan",
}
_LEASE_ENDS = {
    "idle": "it was no longer needed",
    "quiet": "Toolsmith had no work for {keep}",
    "cap": "24 hours passed",
    "lock": "you locked the vault",
    "rekeyed": "the vault was locked",
    "off": "you turned cloud Toolsmith off",
    "key": "its key changed",
}
_LEASE_MINT = object()
_clock: Callable[[], float] = time.monotonic
_wall: Callable[[], float] = time.time


class _Lease:
    """The one key Toolsmith holds for a session. Never printed, never copied out."""

    __slots__ = ("_mark", "_buf", "hosts", "stored_at", "started", "last")

    def __init__(self, mark: object, value: str, hosts: list, stored_at: str):
        if mark is not _LEASE_MINT:
            raise ScopeRefused("only Toolsmith's install loop may hold its key")
        self._mark = mark
        self._buf = bytearray(value.encode("utf-8"))
        self.hosts = [str(h) for h in hosts]
        self.stored_at = str(stored_at or "")
        self.started = _clock()
        self.last = self.started          # the last Toolsmith activity

    def __repr__(self) -> str:
        return "<Toolsmith key lease>"

    __str__ = __repr__

    def zero(self) -> None:
        for i in range(len(self._buf)):
            self._buf[i] = 0
        self._buf = bytearray()


_lease: Optional[_Lease] = None
_timer: Optional[threading.Timer] = None
_active_runs = 0
_queue_busy: Optional[Callable[[], bool]] = None
_wizard_busy: Optional[Callable[[], bool]] = None
_listening: set[int] = set()
_opened = threading.Event()
_waiting = 0
_resumers: list[Callable[[], None]] = []


def set_queue_probe(probe: Optional[Callable[[], bool]]) -> None:
    """The build queue says whether it still has a job running or waiting
    (build/api.py). A busy queue counts as Toolsmith activity."""
    global _queue_busy
    _queue_busy = probe


def set_wizard_probe(probe: Optional[Callable[[], bool]]) -> None:
    """The setup wizard says whether its build is running (wizard/service.py).
    That counts as Toolsmith activity, like a busy build queue. A wizard that
    is only under way counts through her steps (:func:`wizard_touched`)."""
    global _wizard_busy
    _wizard_busy = probe


def wizard_touched() -> None:
    """She took a wizard step (wizard/service.py note_step): Toolsmith
    activity now, so her keep time counts from her LAST step. A wizard left
    alone lets go of the key after her keep time like any other quiet; a
    lease already past its time is not brought back."""
    if _valid_lease() is not None:
        _touch()


def on_resume(callback: Callable[[], None]) -> None:
    """Called (on its own thread) each time the vault opens: the build queue
    starts again any job left waiting for it."""
    if callable(callback) and callback not in _resumers:
        _resumers.append(callback)


def _the_vault(vault: Any = None) -> Any:
    from puffpai.security import keys
    return keys._vault(vault)  # noqa: SLF001 - same package


def vault_open(vault: Any = None) -> bool:
    from puffpai.security import keys
    return keys._is_open(_the_vault(vault))  # noqa: SLF001 - same package


def _listen(vault: Any) -> None:
    """Hear the vault lock: by hand clears the lease, idle does not."""
    if vault is None or id(vault) in _listening or not hasattr(vault, "add_lock_listener"):
        return
    try:
        vault.add_lock_listener(_on_vault_locked)
        _listening.add(id(vault))
    except Exception as exc:  # noqa: BLE001
        logger.debug("toolsmith lease: no lock listener (%s)", type(exc).__name__)


def _on_vault_locked(reason: str) -> None:
    if reason != "idle":
        drop_lease("lock" if reason == "lock" else "rekeyed")


# -- her choice: how long the key stays ready ---------------------------------

def keep_choice() -> str:
    """``1h`` (default), ``4h`` or ``lock``. Anything odd reads as the default."""
    from puffpai import fsutil
    try:
        data = fsutil.read_json(_path(KEEP_FILE), default=None)
    except Exception:  # noqa: BLE001 - unreadable is the default
        return KEEP_DEFAULT
    if not isinstance(data, dict) or data.get("schema") != KEEP_SCHEMA:
        return KEEP_DEFAULT
    keep = data.get("keep")
    return keep if isinstance(keep, str) and keep in KEEP_CHOICES else KEEP_DEFAULT


def keep_seconds() -> Optional[int]:
    return KEEP_CHOICES[keep_choice()]


def set_keep(keep: Any, *, by: str = "owner") -> dict:
    """Her choice on the Toolsmith card. A shorter time applies at once."""
    if not isinstance(keep, str) or keep not in KEEP_CHOICES:
        raise CloudError("keep", "Pick 1 hour, 4 hours or until you lock the vault.", 400)
    from puffpai import fsutil
    with _lock:
        fsutil.atomic_write_json(_path(KEEP_FILE), {"schema": KEEP_SCHEMA, "keep": keep,
                                                    "at": _now_iso()})
    _audit("toolsmith_key_ready_choice", f"{KEEP_QUESTION}: {KEEP_LABELS[keep]}.", by)
    _valid_lease()                 # ends now if it is already past the new time
    _arm()
    return status()


# -- the lease ----------------------------------------------------------------

def _deadline(held: _Lease) -> float:
    """When *held* ends on its own (on the lease clock), if nothing happens."""
    cap = held.started + LEASE_MAX_S
    keep = keep_seconds()
    return cap if keep is None else min(cap, held.last + keep)


def _until_text(held: _Lease) -> str:
    """Her local clock time the lease ends, for the card."""
    at = datetime.fromtimestamp(_wall() + max(0.0, _deadline(held) - _clock()))
    words = at.strftime("%I:%M %p").lstrip("0")
    today = datetime.fromtimestamp(_wall()).date()
    if at.date() != today:
        words += " tomorrow" if (at.date() - today).days == 1 else at.strftime(" on %A")
    return words


def _arm() -> None:
    """One timer at the lease's end, so the key is let go (and the end logged)
    on time even if nobody asks. Re-armed on every activity."""
    global _timer
    with _lock:
        if _timer is not None:
            _timer.cancel()
            _timer = None
        held = _lease
        if held is None:
            return
        wait = min(max(0.0, _deadline(held) - _clock()) + 1.0, float(LEASE_MAX_S))
        t = threading.Timer(wait, _on_timer)
        t.daemon = True
        t.name = "toolsmith-lease-end"
        _timer = t
    t.start()


def _on_timer() -> None:
    try:
        if _valid_lease() is not None:
            _arm()
    except Exception as exc:  # noqa: BLE001 - a timer never raises
        logger.debug("toolsmith lease timer: %s", type(exc).__name__)


def _touch() -> None:
    """Toolsmith did something: the session's quiet time starts again."""
    with _lock:
        held = _lease
        if held is not None:
            held.last = max(held.last, _clock())
    if held is not None:
        _arm()


def _take(vault: Any, why: str) -> bool:
    """Hold the key when cloud Toolsmith is on and the vault is open. No scope:
    holding it hands it to nobody; only :func:`send` (scoped) ever uses it."""
    global _lease
    if not in_use():
        return False
    if _valid_lease() is not None:
        _touch()
        return True
    v = _the_vault(vault)
    from puffpai.security import keys
    if not keys._is_open(v):  # noqa: SLF001
        return False
    got = keys.read_for_lease(PROVIDERS["anthropic"]["key_id"], vault=v)
    if got is None:
        return False
    value, hosts, stored_at = got
    try:
        with _lock:
            if _lease is not None:
                _lease.zero()
            _lease = _Lease(_LEASE_MINT, value, hosts, stored_at)
            held = _lease
    finally:
        value = None
        got = None
    _listen(v)
    _arm()
    line = (f"{LEASE_ON_LINE} ({KEEP_LABELS[keep_choice()]}): "
            f"{_LEASE_STARTS.get(why, why)}. Ready until {_until_text(held)}.")
    _audit("toolsmith_key_lease_on", line, "toolsmith")
    logger.info("%s", line)
    return True


def take_lease(scope: Any, vault: Any = None) -> bool:
    """Hold Toolsmith's Anthropic key for the session (the vault must be open
    now). True when a lease is held after this. Never raises for a closed
    vault or a missing key: the call path then pauses in place."""
    _check_scope(scope)
    return _take(vault, "run")


def take_for_yes(vault: Any = None) -> bool:
    """Her build yes with the vault open (build/yes.py): the key is made ready
    for the session. Never raises."""
    try:
        return _take(vault, "yes")
    except Exception as exc:  # noqa: BLE001 - a run takes it later if it can
        logger.info("toolsmith lease not taken at yes: %s", type(exc).__name__)
        return False


def take_for_plan(vault: Any = None) -> bool:
    """She approved the wizard's plan, or its build started, with the vault
    open (wizard/service.py): the key is made ready for the session, as at a
    build yes. Never raises."""
    try:
        return _take(vault, "plan")
    except Exception as exc:  # noqa: BLE001 - a run takes it later if it can
        logger.info("toolsmith lease not taken at the plan: %s", type(exc).__name__)
        return False


def key_saved(vault: Any = None) -> bool:
    """For keys.add_key ONLY, right after a new Anthropic key is locked in the
    open vault: any lease still held is let go (it may hold the old key) and,
    with cloud Toolsmith on, a new one is taken with the new key at once.
    With cloud not on yet, :func:`choose_cloud` takes it once the setting
    says cloud. Never raises for a closed vault or a missing key."""
    caller = sys._getframe(1).f_globals.get("__name__", "")
    if caller != "puffpai.security.keys":
        raise ScopeRefused("only the key broker says a key was saved")
    try:
        drop_lease("key")
        return _take(vault, "saved")
    except Exception as exc:  # noqa: BLE001 - a run or an unlock takes it later
        logger.info("toolsmith lease not taken at key save: %s", type(exc).__name__)
        return False


def drop_lease(reason: str = "idle", *, only: Optional[_Lease] = None) -> bool:
    """Zero and drop the lease (only *only*, when given). True when there was one."""
    global _lease, _timer
    with _lock:
        if _lease is None or (only is not None and _lease is not only):
            return False
        held, _lease = _lease, None
        if _timer is not None:
            _timer.cancel()
            _timer = None
    held.zero()
    why = _LEASE_ENDS.get(reason, "it was no longer needed")
    if "{keep}" in why:
        why = why.format(keep=KEEP_LABELS[keep_choice()])
    line = f"Toolsmith let go of its Anthropic key: {why}."
    _audit("toolsmith_key_lease_off", line, "toolsmith")
    logger.info("%s", line)
    return True


def _valid_lease() -> Optional[_Lease]:
    with _lock:
        held = _lease
        running = _active_runs > 0
    if held is None:
        return None
    now = _clock()
    if now - held.started >= LEASE_MAX_S:
        drop_lease("cap", only=held)
        return None
    if running or _queue_says_busy() or _wizard_says_busy():
        held.last = max(held.last, now)   # a build or the wizard's build going is activity
    keep = keep_seconds()
    if keep is not None and now - held.last >= keep:
        drop_lease("quiet", only=held)
        return None
    return held


def lease_active() -> bool:
    return _valid_lease() is not None and in_use()


def lease_line() -> str:
    """The card's line while the key is ready, else ""."""
    held = _valid_lease()
    if held is None or not in_use():
        return ""
    return LEASE_CARD_LINE.format(until=_until_text(held))


def lease_record(lease: Any, service_id: str, caller: str, stored_at: Any) -> Optional[dict]:
    """For keys._keyed ONLY: the leased key record when *lease* is the live
    lease, for Anthropic, for Toolsmith, for the key still on file."""
    frame_mod = sys._getframe(1).f_globals.get("__name__", "")
    if frame_mod != "puffpai.security.keys":
        raise ScopeRefused("only the key broker may use Toolsmith's lease")
    held = _valid_lease()
    if held is None or lease is not held or type(lease) is not _Lease \
            or getattr(lease, "_mark", None) is not _LEASE_MINT:
        return None
    if service_id != PROVIDERS["anthropic"]["key_id"] or caller != "toolsmith":
        return None
    if str(stored_at or "") != held.stored_at:
        drop_lease("key", only=held)
        return None
    if not in_use():
        drop_lease("off", only=held)
        return None
    _touch()
    return {"value": bytes(held._buf).decode("utf-8"), "hosts": list(held.hosts),  # noqa: SLF001
            "card_required": True}


def run_started(scope: Any) -> None:
    """A Toolsmith run on the cloud model began: take the lease if the vault
    is open now (or keep the one held)."""
    global _active_runs
    _check_scope(scope)
    with _lock:
        _active_runs += 1
    try:
        take_lease(scope)
    except Exception as exc:  # noqa: BLE001 - the call path pauses if it is needed
        logger.info("toolsmith lease not taken: %s", type(exc).__name__)


def run_ended(scope: Any) -> None:
    """A run ended: the session's quiet time starts now. The lease stays."""
    global _active_runs
    _check_scope(scope)
    with _lock:
        _active_runs = max(0, _active_runs - 1)
    _touch()


def _queue_says_busy() -> bool:
    if _queue_busy is None:
        return False
    try:
        return bool(_queue_busy())
    except Exception:  # noqa: BLE001 - unknown is not busy (the quiet time runs)
        return False


def _wizard_says_busy() -> bool:
    if _wizard_busy is None:
        return False
    try:
        return bool(_wizard_busy())
    except Exception:  # noqa: BLE001 - unknown is not busy (the quiet time runs)
        return False


def queue_idle() -> None:
    """The build queue has nothing running or waiting: the quiet time starts.
    The lease is kept for the session (owner, 2026-10-14)."""
    _touch()


def on_vault_opened(vault: Any = None) -> None:
    """The vault opened (security/vault.py ``_after_open``): with cloud
    Toolsmith on, its key is made ready for the session; a run that waits in
    place wakes, and any job left waiting for it starts again. Never raises."""
    try:
        _take(vault, "unlock")
    except Exception as exc:  # noqa: BLE001 - a run takes it later if it can
        logger.info("toolsmith lease not taken at unlock: %s", type(exc).__name__)
    _opened.set()
    for cb in list(_resumers):
        try:
            threading.Thread(target=cb, name="toolsmith-resume", daemon=True).start()
        except Exception as exc:  # noqa: BLE001
            logger.debug("toolsmith resume not started: %s", type(exc).__name__)


def waiting_for_vault() -> bool:
    return _waiting > 0


def vault_wait_line(needed: bool = False) -> str:
    """:data:`VAULT_WAIT_LINE` when a build view should ask for her PIN right
    where she is: a Toolsmith run waits for the vault now, or *needed* (a
    build or job that uses cloud Toolsmith is going or waiting) while its key
    is not ready (no lease, vault locked). Else "". Never raises."""
    try:
        if not in_use():
            return ""
        if waiting_for_vault():
            return VAULT_WAIT_LINE
        if needed and _valid_lease() is None and not vault_open():
            return VAULT_WAIT_LINE
    except Exception:  # noqa: BLE001 - a status line never breaks a view
        return ""
    return ""


def wait_for_vault(cancelled: Any = None, *, limit: Optional[float] = None,
                   poll: Optional[float] = None) -> bool:
    """Wait in place until the vault is open (True), or until *limit* passes
    or *cancelled* is set (False). Looks every *poll* seconds and wakes at
    once on the vault's unlock event."""
    global _waiting
    limit = WAIT_LIMIT_S if limit is None else float(limit)
    poll = WAIT_POLL_S if poll is None else float(poll)
    end = _clock() + limit
    with _lock:
        _waiting += 1
    try:
        while True:
            if vault_open():
                return True
            left = end - _clock()
            if left <= 0 or (cancelled is not None and cancelled.is_set()):
                return False
            _opened.wait(max(0.01, min(poll, left)))
            _opened.clear()
    finally:
        with _lock:
            _waiting = max(0, _waiting - 1)


# ── the setting ──────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _path(name: str) -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(create=True), name)


LOCAL = {"where": "local"}


def read_setting() -> dict:
    """``toolsmith.model``. Anything odd, or cloud without her recorded yes
    to the current consent sentence, reads as local."""
    from puffpai import fsutil
    try:
        data = fsutil.read_json(_path(SETTING_FILE), default=None)
    except Exception:  # noqa: BLE001 - unreadable is local
        return dict(LOCAL)
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return dict(LOCAL)
    model = data.get("model")
    if data.get("where") != "cloud":
        return {"where": "local", "last_model": model if model in MODELS else DEFAULT_MODEL}
    provider = data.get("provider")
    consent = data.get("consent")
    row = PROVIDERS.get(provider) if isinstance(provider, str) else None
    if row is None or model not in row["models"] or not isinstance(consent, dict) \
            or consent.get("text") != _sha(CONSENT_TEXT)[:16]:
        return dict(LOCAL)
    return {"where": "cloud", "provider": provider, "model": model,
            "consent": {"at": str(consent.get("at") or "")[:40],
                        "text": consent["text"]}}


def consent_outdated() -> bool:
    """She chose Anthropic and said yes to an EARLIER consent sentence: the
    setting reads as local (:func:`read_setting`) until she reads the new
    sentence and picks Anthropic again. Never migrated for her; this only
    lets the pages say so instead of switching quietly."""
    from puffpai import fsutil
    try:
        data = fsutil.read_json(_path(SETTING_FILE), default=None)
    except Exception:  # noqa: BLE001 - unreadable: nothing to say
        return False
    if not isinstance(data, dict) or data.get("schema") != SCHEMA \
            or data.get("where") != "cloud":
        return False
    consent = data.get("consent")
    return (isinstance(consent, dict) and isinstance(consent.get("text"), str)
            and consent["text"] != _sha(CONSENT_TEXT)[:16])


def _write(setting: dict) -> None:
    from puffpai import fsutil
    fsutil.atomic_write_json(_path(SETTING_FILE), {"schema": SCHEMA, **setting,
                                                   "at": _now_iso()})


def in_use() -> bool:
    """True when the owner chose the cloud model for Toolsmith (her yes kept)."""
    return read_setting()["where"] == "cloud"


def _key_state() -> str:
    try:
        from puffpai.security import keys
        return str(keys.key_status(PROVIDERS["anthropic"]["key_id"]).get("state") or "")
    except Exception:  # noqa: BLE001
        return ""


VAULT_LOCKED_LINE = "Your vault is locked. Type your vault PIN to save the key."
BEFORE_SETUP_LINE = ("Your key can be saved once you approve your plan. Go on with setup; "
                     "BuildBot asks for it before Toolsmith's first install.")
HOLD_LINE = ("You picked Anthropic for Toolsmith, but its key isn't saved yet. "
             "Save it in Settings, or choose This computer.")
#: Said where Toolsmith's model shows while her yes was to an earlier sentence.
RECONSENT_LINE = ("What Toolsmith sends to Anthropic now reads differently. Read it "
                  "and pick Anthropic again to keep using it.")
#: The build holds with this line in that case (no quiet switch to this computer).
RECONSENT_HOLD_LINE = ("What Toolsmith sends to Anthropic now reads differently. Read "
                       "it in Settings and pick Anthropic again to keep using it, or "
                       "choose This computer.")
WISH_FILE = "toolsmith_wish.json"
WISH_SCHEMA = "puffpai_toolsmith_wish/v1"


def _set_up() -> bool:
    """Has the wizard recorded the bot's run setting (so a key's door can open)?"""
    try:
        from puffpai import outside_services
        return bool(outside_services._has_posture_record())  # noqa: SLF001
    except Exception:  # noqa: BLE001 - unknown reads as set up (the plain refusal shows)
        return True


# -- her wish from the wizard: Anthropic picked, key not saved yet -------------

def record_wish(*, by: str = "owner") -> None:
    """The wizard heard "Anthropic" but the key is not saved: the build is
    held before Toolsmith's first run until she saves it or picks this
    computer (:func:`held`). Only the word is kept, never a key."""
    from puffpai import fsutil
    with _lock:
        fsutil.atomic_write_json(_path(WISH_FILE), {"schema": WISH_SCHEMA, "wish": "cloud",
                                                    "at": _now_iso()})
    _audit("toolsmith_cloud_wish", "anthropic picked, key not saved yet", by)


def clear_wish() -> bool:
    """True when there was a wish to clear."""
    with _lock:
        try:
            os.unlink(_path(WISH_FILE))
            return True
        except OSError:
            return False


def wished_cloud() -> bool:
    from puffpai import fsutil
    try:
        data = fsutil.read_json(_path(WISH_FILE), default=None)
    except Exception:  # noqa: BLE001 - unreadable: no wish
        return False
    return (isinstance(data, dict) and data.get("schema") == WISH_SCHEMA
            and data.get("wish") == "cloud")


def held() -> bool:
    """True while she picked Anthropic (in the wizard, or earlier under an
    older consent sentence) but Toolsmith is still on this computer: the
    build does not start Toolsmith (no silent local build)."""
    return (wished_cloud() or consent_outdated()) and not in_use()


def hold_line() -> str:
    """Why the build holds, in her words; "" when it does not."""
    if not held():
        return ""
    return RECONSENT_HOLD_LINE if consent_outdated() else HOLD_LINE


def _wish_met(released: bool = False) -> None:
    """Saved, or she chose this computer: the wish (or the hold for an older
    consent sentence, *released*) is done and held jobs go on."""
    if clear_wish() or released:
        for cb in list(_resumers):
            try:
                threading.Thread(target=cb, name="toolsmith-release", daemon=True).start()
            except Exception as exc:  # noqa: BLE001
                logger.debug("toolsmith release not started: %s", type(exc).__name__)


def status() -> dict:
    """What Settings shows. Never a key."""
    st = read_setting()
    usage = read_usage()
    model = st.get("model") or st.get("last_model") or DEFAULT_MODEL
    again = st["where"] != "cloud" and consent_outdated()
    key_state = _key_state()
    line = ("Toolsmith's model: Anthropic " + MODELS[model]["name"]
            if st["where"] == "cloud" else "Toolsmith's model: on this computer")
    if again:
        line += ". " + RECONSENT_LINE
        if key_state not in ("", "missing"):
            line += " Your key is still in your vault."
    return {"where": st["where"], "provider": st.get("provider") or "anthropic",
            "model": model,
            "models": [{"id": mid, "name": row["name"], "in": row["in"],
                        "out": row["out"], "cache_read": row["cache_read"]}
                       for mid, row in MODELS.items()],
            "consent_text": CONSENT_TEXT, "key_state": key_state,
            "consent_again": again,
            "spent_usd": round(float(usage.get("total_usd") or 0.0), 4),
            "spent_line": spent_line(usage),
            "lease_line": lease_line(),
            "keep": keep_choice(), "keep_question": KEEP_QUESTION,
            "hold_line": hold_line(),
            "keep_choices": [{"id": k, "name": KEEP_LABELS[k]} for k in KEEP_CHOICES],
            "line": line}


def spent_line(usage: Optional[dict] = None) -> str:
    total = float((usage or read_usage()).get("total_usd") or 0.0)
    if total <= 0:
        return ""
    return f"Toolsmith used about ${total:.2f} so far."


def choose_cloud(model: Any, device: str, *, key: Any = None, by: str = "owner",
                 vault: Any = None) -> dict:
    """Her tap on "Anthropic" in Settings (from a paired device) with the
    consent text on screen: her yes, a ``key_on`` ticket minted and spent at
    once, the key (if pasted) checked and locked in the vault, the switch on."""
    from puffpai import outside_services
    from puffpai.security import consent_tickets, keys
    mid = model if isinstance(model, str) and model in MODELS else None
    if mid is None:
        raise CloudError("model", "Pick Opus 5.5 or Sonnet 5.5.", 400)
    again = consent_outdated()         # her yes was to an earlier sentence: held jobs wait
    if key not in (None, ""):
        try:
            keys.check_shape("anthropic", key)       # nothing is sent if this fails
        except keys.KeyRefused as exc:
            raise CloudError(exc.code, exc.message, 400) from None
    elif _key_state() in ("", "missing"):
        raise CloudError("no_key", "Paste your key from Anthropic first.", 400)
    target = "toolsmith_cloud"
    try:
        ticket = consent_tickets.mint("key_on", target, CONSENT_TEXT, device)
    except consent_tickets.TicketRefused as exc:
        raise CloudError(exc.code, exc.message, 429 if exc.code == "throttled" else 403) \
            from None
    ok, verdict = consent_tickets.redeem(ticket, device, "key_on", target)
    if not ok:
        raise CloudError("ticket", consent_tickets.refusal_words(verdict), 403)
    pasted = key not in (None, "")
    if pasted:
        try:
            keys.add_key("anthropic", key, vault=vault, by=by)   # switches the door on
        except keys.KeyRefused as exc:
            if exc.code == "locked":
                raise CloudError("vault_locked", VAULT_LOCKED_LINE, 423) from None
            if exc.code == "cannot" and not _set_up():
                raise CloudError("before_setup", BEFORE_SETUP_LINE, 409) from None
            raise CloudError(exc.code, exc.message, 409) from None
        finally:
            key = None
    else:
        try:
            outside_services.switch_on(DOOR, by=by)
        except outside_services.OutsideError as exc:
            if exc.code != "not_set_up":
                raise CloudError(exc.code, exc.message, 409) from None
            outside_services.record_yes(DOOR, by=by)
    with _lock:
        _write({"where": "cloud", "provider": "anthropic", "model": mid,
                "consent": {"at": _now_iso(), "by": by, "text": _sha(CONSENT_TEXT)[:16],
                            "device": _sha(str(device))[:12]}})
    _audit("toolsmith_cloud_on", f"anthropic {mid}", by)
    if pasted:
        # The vault is open (add_key needs it): her new key is made ready for
        # the session now, so a later idle lock in the wizard pauses nothing.
        # add_key already let go of any lease on the old key.
        try:
            _take(vault, "saved")
        except Exception as exc:  # noqa: BLE001 - a run or an unlock takes it later
            logger.info("toolsmith lease not taken at key save: %s", type(exc).__name__)
    _wish_met(again)
    return status()


def set_model(model: Any, *, by: str = "owner") -> dict:
    st = read_setting()
    if st["where"] != "cloud":
        raise CloudError("not_on", "Toolsmith is using the model on this computer.", 409)
    if model not in MODELS:
        raise CloudError("model", "Pick Opus 5.5 or Sonnet 5.5.", 400)
    from puffpai import fsutil
    with _lock:
        raw = fsutil.read_json(_path(SETTING_FILE), default={}) or {}
        raw["model"] = model
        raw.pop("schema", None)
        raw.pop("at", None)
        _write(raw)
    _audit("toolsmith_cloud_model", str(model), by)
    return status()


def use_local(*, by: str = "owner") -> dict:
    """Back to the model on this computer: one click. The switch closes; the
    key stays in the vault until she removes it (Settings, Remove key)."""
    from puffpai import outside_services
    with _lock:
        again = consent_outdated()
        prior = read_setting()
        _write({"where": "local", "model": prior.get("model") or DEFAULT_MODEL})
    drop_lease("off")
    try:
        outside_services.switch_off(DOOR, by=by)
    except Exception as exc:  # noqa: BLE001 - the setting above already says local
        logger.info("toolsmith cloud switch not closed: %s", type(exc).__name__)
    _audit("toolsmith_cloud_off", "local", by)
    _wish_met(again)               # "Use this computer": her choice, the hold ends
    return status()


def forget_key(*, by: str = "owner", vault: Any = None) -> dict:
    from puffpai.security import keys
    use_local(by=by)
    try:
        keys.remove_key("anthropic", vault=vault, by=by)
    except Exception as exc:  # noqa: BLE001
        logger.info("toolsmith cloud key not removed: %s", type(exc).__name__)
    return status()


def _audit(action: str, details: str, by: str) -> None:
    try:
        from puffpai.security.audit_log import AuditLog
        AuditLog(log_file=_path(os.path.join("security_logs", "toolsmith_cloud_audit.jsonl"))
                 ).log(action=action, user=str(by)[:40], result="done",
                       details=str(details)[:200])
    except Exception as exc:  # noqa: BLE001 - the record never breaks a call
        logger.debug("toolsmith cloud audit skipped: %s", type(exc).__name__)


# ── what may leave: cleaned text ─────────────────────────────────────────────

def clean(text: Any) -> str:
    """*text* with key shapes and private details taken out. Deterministic and
    idempotent, so a turn cleaned once is the same bytes on every later call.
    Raises :class:`CloudError` when the check cannot run (nothing is sent)."""
    text = "" if text is None else str(text)
    if not text:
        return text
    digest = _sha(text)
    hit = _clean_cache.get(digest)
    if hit is not None:
        return hit
    try:
        from puffpai.security import secret_detector
        out = secret_detector.redact_only(text, anywhere=True)
        clf = _classifier_box.get("c")
        if clf is None:
            from puffpai.security.data_classifier import DataClassifier
            clf = _classifier_box["c"] = DataClassifier()
        out, _found = clf.redact_pii(out)
        # A health statement about a person loses the person and the amounts
        # ("I have diabetes, my dose is 10 units"); the health word, every
        # other sentence, URL, path and command stay, so "cd insulin-tracker"
        # and the search subject survive. Not redact_pii's health mode: that
        # replaces every copy of the word everywhere.
        from puffpai.security.data_classifier import scrub_health_statements
        out = scrub_health_statements(out)
    except Exception:  # noqa: BLE001 - fail closed
        raise CloudError("privacy_check", "The privacy check could not run, so "
                                          "nothing was sent to Anthropic.", 500) from None
    if len(_clean_cache) > 4000:
        _clean_cache.clear()
    _clean_cache[digest] = out
    return out


def _clean_turn(message: dict) -> dict:
    """A user turn with every text and tool result cleaned. Assistant turns
    are never touched (they go back exactly as Anthropic sent them)."""
    if message.get("role") != "user":
        return message
    content = message.get("content")
    if isinstance(content, str):
        return {"role": "user", "content": clean(content)}
    blocks = []
    for b in content or []:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            blocks.append({**b, "text": clean(b.get("text")) or "(empty)"})
        elif b.get("type") == "tool_result":
            inner = b.get("content")
            if isinstance(inner, str):
                inner = clean(inner) or "(empty)"
            elif isinstance(inner, list):
                inner = [{**p, "text": clean(p.get("text"))} if isinstance(p, dict)
                         and p.get("type") == "text" else p for p in inner]
            blocks.append({**b, "content": inner})
        else:
            blocks.append(b)
    return {"role": "user", "content": blocks}


# ── the request ──────────────────────────────────────────────────────────────

def body_for(model: str, system: str, tools: list, messages: list, *,
             fallback: bool = True, find: bool = False) -> dict:
    """The Messages API body. *tools* sorted by name (a stable list keeps the
    prompt cache); no ``thinking`` (adaptive is the default), no
    ``tool_choice`` (auto). *find*: a small finding ask, no tools, a short
    reply (:data:`FIND_MAX_TOKENS`), low effort."""
    body: dict[str, Any] = {
        "model": model, "max_tokens": FIND_MAX_TOKENS if find else MAX_TOKENS,
        "system": system,
        "messages": [_clean_turn(m) for m in messages],
        "cache_control": {"type": "ephemeral"},
        "output_config": {"effort": "low" if find else "high"},
    }
    if not find:
        body["tools"] = sorted(tools, key=lambda t: str(t.get("name")))
    if fallback:
        body["fallbacks"] = "default"
    return body


def _retry_wait(after: str, attempt: int) -> float:
    try:
        wait = float(after)
    except (TypeError, ValueError):
        wait = min(MAX_WAIT_S, 2.0 * (2 ** attempt)) + random.uniform(0, 1)
    return max(0.5, min(MAX_WAIT_S, wait))


_sleep: Callable[[float], None] = time.sleep


def send(scope: Any, *, system: str, tools: list, messages: list,
         app_name: str = "", purpose: str = "") -> dict:
    """One Messages call for Toolsmith. Returns ``{"content" (the raw block
    list, unchanged), "stop_reason", "stop_details", "usage", "cost_usd"}``.

    Order is the security property: the scope token, her recorded yes, the
    cleaning, and only then the key and the request (keys.keyed_post: the
    Bridge switch, the vault, the host binding, the outside-traffic line)."""
    scope = _check_scope(scope)
    finding = scope.kind == "find"
    if finding and tools:
        raise ScopeRefused("a finding ask runs no tools")
    st = read_setting()
    if st["where"] != "cloud":
        raise CloudError("not_on", "Toolsmith is set to use the model on this computer.")
    from puffpai.security import keys
    row = PROVIDERS[st["provider"]]
    model = st["model"]
    fallback = model not in _no_fallback
    for attempt in range(RETRIES + 1):
        body = body_for(model, clean(system), tools, messages, fallback=fallback,
                        find=finding)
        data = json.dumps(body, ensure_ascii=False, sort_keys=False).encode("utf-8")
        if len(data) > MAX_BODY_BYTES:
            raise CloudError("too_big", "This app's notes grew too long for one try.", 413)
        headers = {"content-type": "application/json",
                   "anthropic-version": ANTHROPIC_VERSION}
        if fallback:
            headers["anthropic-beta"] = FALLBACK_BETA
        if (_active_runs > 0 or finding) and _valid_lease() is None and vault_open():
            _take(None, "find" if finding else "run")   # open vault: the session key
        try:
            reply = keys.keyed_post(keys.handle(row["key_id"]), url=row["url"],
                                    caller="toolsmith", data=data, headers=headers,
                                    timeout=CALL_TIMEOUT, lease=_valid_lease())
        except keys.KeyUnavailable as exc:
            if exc.reason == "stopped_working":
                raise CloudError("key", KEY_LINE, 401) from None
            if exc.reason == "unreachable":
                raise CloudOffline("offline", OFFLINE_LINE, 502) from None
            if exc.reason == "busy" and attempt < RETRIES:
                _sleep(_retry_wait("", attempt))
                continue
            raise CloudError(exc.reason, exc.message) from None
        status = reply.status
        if status == 400 and fallback and _mentions_fallback(reply.body):
            _no_fallback.add(model)        # this model takes no fallbacks: drop it there
            fallback = False
            continue
        if status == 429 or status >= 500:
            if attempt < RETRIES:
                _sleep(_retry_wait(reply.retry_after, attempt))
                continue
            raise CloudError("busy", "Anthropic is busy right now. Toolsmith stopped "
                                     "this try.", 429 if status == 429 else 502)
        if status in (401, 403):
            raise CloudError("key", KEY_LINE, 401)
        if status != 200:
            raise CloudError("refused", f"Anthropic could not take that request "
                                        f"(it answered {status}).", 502)
        try:
            parsed = reply.json()
        except ValueError:
            parsed = None
        if not isinstance(parsed, dict) or not isinstance(parsed.get("content"), list):
            raise CloudError("odd_reply", "Anthropic gave a reply Toolsmith could not "
                                          "read.", 502)
        usage = parsed.get("usage") if isinstance(parsed.get("usage"), dict) else {}
        cost = record_usage(scope, model, usage, app_name=app_name, purpose=purpose,
                            bytes_out=len(data), bytes_in=len(reply.body))
        return {"content": parsed["content"], "stop_reason": parsed.get("stop_reason"),
                "stop_details": parsed.get("stop_details"), "usage": usage,
                "cost_usd": cost}
    raise CloudError("busy", "Anthropic is busy right now. Toolsmith stopped this try.", 429)


def ask_json(scope: Any, *, system: str, user: str, purpose: str) -> Optional[Any]:
    """One small finding ask (search phrases, ranking): no tools, JSON asked
    for in the text and read here. None when the reply holds no JSON (the
    caller then uses this computer's model). Raises :class:`CloudError` and
    :class:`ScopeRefused` like :func:`send`."""
    scope = _check_scope(scope)
    if scope.kind != "find":
        raise ScopeRefused("ask_json is for Toolsmith's finding work only")
    got = send(scope, system=system, tools=[], purpose=purpose,
               messages=[{"role": "user", "content": str(user)}])
    text = "".join(str(b.get("text") or "") for b in got.get("content") or []
                   if isinstance(b, dict) and b.get("type") == "text").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


def _mentions_fallback(body: bytes) -> bool:
    return b"fallback" in (body or b"")[:4000].lower()


# ── usage and cost ───────────────────────────────────────────────────────────

def _n(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def cost_usd(model: str, usage: dict) -> float:
    """Estimated dollars for one call's ``usage``."""
    price = MODELS.get(model)
    if price is None or not isinstance(usage, dict):
        return 0.0
    tokens_in = _n(usage.get("input_tokens"))
    written = _n(usage.get("cache_creation_input_tokens"))
    read = _n(usage.get("cache_read_input_tokens"))
    out = _n(usage.get("output_tokens"))
    return (tokens_in * price["in"] + written * price["in"] * CACHE_WRITE_FACTOR
            + read * price["cache_read"] + out * price["out"]) / 1_000_000


def read_usage() -> dict:
    from puffpai import fsutil
    try:
        data = fsutil.read_json(_path(USAGE_FILE), default=None)
    except Exception:  # noqa: BLE001
        data = None
    if not isinstance(data, dict) or data.get("schema") != USAGE_SCHEMA:
        return {"schema": USAGE_SCHEMA, "total_usd": 0.0, "calls": 0, "jobs": {}}
    return data


def record_usage(scope: Scope, model: str, usage: dict, *, app_name: str = "",
                 purpose: str = "", bytes_out: int = 0, bytes_in: int = 0) -> float:
    """Keep one call's tokens and cost (per job and in total). No content."""
    from puffpai import fsutil
    cost = cost_usd(model, usage)
    with _lock:
        data = read_usage()
        jobs = data.setdefault("jobs", {})
        job = jobs.setdefault(scope.job_id or "-", {"name": scope.job_name, "usd": 0.0,
                                                    "calls": 0, "tokens": {}})
        job["usd"] = round(float(job.get("usd") or 0.0) + cost, 6)
        job["calls"] = int(job.get("calls") or 0) + 1
        tok = job.setdefault("tokens", {})
        for k in ("input_tokens", "cache_creation_input_tokens",
                  "cache_read_input_tokens", "output_tokens"):
            tok[k] = int(tok.get(k) or 0) + _n(usage.get(k))
        job["last"] = {"provider": "anthropic", "model": model, "at": _now_iso(),
                       "purpose": (str(purpose)[:120] if purpose else
                                   f"Toolsmith install of {str(app_name or 'an app')[:80]}"),
                       "bytes_out": int(bytes_out), "bytes_in": int(bytes_in)}
        data["total_usd"] = round(float(data.get("total_usd") or 0.0) + cost, 6)
        data["calls"] = int(data.get("calls") or 0) + 1
        fsutil.atomic_write_json(_path(USAGE_FILE), data)
    return cost


def job_cost(job_id: str) -> float:
    return float(((read_usage().get("jobs") or {}).get(job_id) or {}).get("usd") or 0.0)


def context_tokens(usage: Any) -> int:
    """How much of the context the last call used."""
    if not isinstance(usage, dict):
        return 0
    return (_n(usage.get("input_tokens")) + _n(usage.get("cache_creation_input_tokens"))
            + _n(usage.get("cache_read_input_tokens")) + _n(usage.get("output_tokens")))


__all__ = ["CONSENT_TEXT", "CloudError", "CloudOffline", "DEFAULT_MODEL", "KEY_LINE",
           "MODELS", "OFFLINE_LINE", "PROVIDERS", "Scope", "ScopeRefused", "body_for",
           "choose_cloud", "clean", "context_tokens", "cost_usd", "forget_key", "in_use",
           "job_cost", "read_setting", "read_usage", "record_usage", "send", "set_model",
           "spent_line", "status", "toolsmith_scope", "use_local",
           "LEASE_CARD_LINE", "LEASE_ON_LINE", "VAULT_WAIT_LINE", "drop_lease",
           "lease_active", "on_resume", "on_vault_opened", "queue_idle", "run_ended",
           "run_started", "set_queue_probe", "take_lease", "vault_open", "wait_for_vault",
           "waiting_for_vault", "KEEP_CHOICES", "KEEP_DEFAULT", "KEEP_LABELS",
           "KEEP_QUESTION", "keep_choice", "keep_seconds", "lease_line", "set_keep",
           "take_for_yes", "ask_json", "FIND_CALLERS", "HOLD_LINE", "VAULT_LOCKED_LINE",
           "BEFORE_SETUP_LINE", "clear_wish", "held", "record_wish", "wished_cloud",
           "key_saved", "set_wizard_probe", "take_for_plan", "vault_wait_line",
           "wizard_touched", "RECONSENT_LINE", "RECONSENT_HOLD_LINE", "consent_outdated",
           "hold_line"]
