"""One consent ticket for "turn an outside thing on".

No PIN is needed to add things or to switch an
outside service on from a paired device: being paired is the gate. What the ticket still
guarantees is that **the action that runs is exactly the one the owner was
shown**, on **the device that was shown it**, **once**, **soon**.

The protocol
------------
1. The server builds the sentence (never the browser, never the model) and
   mints a ticket bound to ``(action, target, device_id)``::

       ticket = consent_tickets.mint("outside_on", "weather", sentence,
                                     device_id)

   The caller shows ``sentence`` to the owner and hands ``ticket`` back to
   the page (or keeps it in the terminal process).
2. When the owner says yes, the same device presents the ticket::

       ok, verdict = consent_tickets.redeem(ticket, device_id,
                                            "outside_on", "weather")

   and only on ``ok`` does the caller perform the action.

Rules the redeem enforces
-------------------------
* **Single use.** The record is removed the moment its secret matches --
  before anything else is checked -- so a ticket presented for the wrong
  action, or from the wrong device, is burned, not handed back.
* **Ten minutes.** :data:`TTL_SECONDS`. A ticket is evidence someone saw the
  sentence a moment ago, not a standing permission.
* **Bound to the device.** ``device_id`` is the paired device's identity as the
  backend already knows it (``device:<hmac>`` from the dashboard proxy's
  ``X-Puff-Device-Id``, see ``api_server._get_chat_client_id``), or
  :func:`terminal_device_id` for the terminal. An anonymous or empty identity
  can neither mint nor redeem.
* **Bound to the action.** ``(action, target)`` must match exactly. No
  wildcard, no prefix match.
* **In memory only.** The secret is returned once and only its SHA-256 is
  kept; nothing survives a restart. At most :data:`MAX_LIVE` tickets are live;
  the oldest is evicted rather than a new mint refused.

Using tickets elsewhere
-----------------------
* **add-more** (approving a found live card / book, which switches an outside
  service on): mint with ``action="add_on"``, ``target=<addition id>`` and the
  proposal sentence the server built; redeem in the approve route before
  ``install_draft(allow=True)``. Do not add a second consent mechanism.
  Done for live-card drafts: ``POST /apps/feed-drafts/<id>/confirm`` mints
  ``("add_on", "draft-<id>")`` and ``.../install`` with ``allow: true``
  redeems it (no ticket -> nothing installed). The wizard's own build step
  (``wizard.factory``) still installs with ``allow=True`` under the owner's
  yes to the whole plan; it is in-process, not a route.
* **keys** (turning on a keyed service): mint with ``action="key_on"``,
  ``target=<key row id>``. Storing the key itself still needs the vault open,
  because that WRITES the vault; the ticket is what binds "switch it on".
* Actions this module knows are in :data:`ACTIONS`; add yours there.

Outside apps and code
---------------------
* ``app_host_on`` (an added app may reach one outside website),
  ``box_setup`` (set up the locked box), ``folder_give`` (give an added app a
  folder) and ``action_yes`` (a yes for one app action, for removes) are in
  :data:`OWNER_ONLY`. Their targets are built ONLY by the ``target_*`` helpers
  below, which hash whatever could carry a name, a path or a host, so a
  target is short, fits :data:`_TARGET_RE`, and never shows the owner's
  folder names in a log line.
* **Owner devices.** When a household has profiles, the build sets a
  provider (:func:`set_owner_devices_provider`) that answers the set of the
  owner's device ids. An :data:`OWNER_ONLY` ticket is then minted and
  redeemed only on one of those devices. A provider that answers ``None``
  (the public build today: no household owner) lets any paired device
  through. A provider that fails, or answers anything else, refuses. The
  terminal is not let through by default either: a provider that wants the
  terminal lists :func:`terminal_device_id`.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import threading
import time
from typing import Any, Callable, Iterator, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

#: A ticket lives this long (the owner's brief: 10 minutes).
TTL_SECONDS = 600

#: Simultaneously live tickets. At the cap the OLDEST is evicted: eviction
#: only removes a capability, while refusing to mint would let a flood lock
#: the owner out of switching anything on.
MAX_LIVE = 32

#: Mints per device per minute. A paired device minting in a loop is noise,
#: not consent.
MINTS_PER_MINUTE = 20

#: The actions a ticket may be minted for.
ACTIONS = frozenset({"outside_on", "add_on", "key_on",
                     "app_host_on", "box_setup", "folder_give", "action_yes"})

#: Actions only the owner's own device may mint or redeem, when the build
#: knows who the owner is (see :func:`set_owner_devices_provider`).
OWNER_ONLY = frozenset({"app_host_on", "box_setup", "folder_give", "action_yes"})

_DEVICE_RE = re.compile(r"^(device|terminal):[A-Za-z0-9_\-]{8,80}$")
_TARGET_RE = re.compile(r"^[a-z0-9][a-z0-9_:\-.]{0,95}$")

#: An app or integration id inside a target: the shape records.new_id
#: issues (``^[a-z0-9][a-z0-9-]{0,29}-[a-z2-7]{8}$``) fits; ``:`` and ``.`` do
#: not, so an id can never pretend to be the next field of a target.
_APP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,38}$")
_DISTRO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,63}$")
_HEX_RE = re.compile(r"^[0-9a-f]{16,128}$")

_lock = threading.Lock()
_live: list[dict[str, Any]] = []
_owner_provider: dict[str, Optional[Callable[[], Any]]] = {"fn": None}
_mints: dict[str, list[float]] = {}
_terminal_id: dict[str, str] = {}


class TicketRefused(ValueError):
    """A ticket could not be minted. ``message`` is plain words."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.message = message
        self.code = code


def terminal_device_id() -> str:
    """This terminal process's device id: random, per process, never stored."""
    with _lock:
        value = _terminal_id.get("id")
        if value is None:
            value = f"terminal:{os.getpid()}-{secrets.token_hex(12)}"
            _terminal_id["id"] = value
        return value


def usable_device(device_id: Any) -> bool:
    """True for a paired device id or this process's terminal id."""
    return isinstance(device_id, str) and bool(_DEVICE_RE.match(device_id))


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


# -- owner devices ------------------------------------------------------------

def set_owner_devices_provider(fn: Optional[Callable[[], Any]]) -> None:
    """How this build learns the owner's device ids (``fn() -> set | None``).

    ``None`` from the provider means "this build has no household owner"
    (the public build today): any paired device may say yes. Passing
    ``fn=None`` removes the provider, which means the same.
    """
    if fn is not None and not callable(fn):
        raise TypeError("the owner devices provider must be callable")
    with _lock:
        _owner_provider["fn"] = fn


def _owner_verdict(action: str, device_id: Any) -> str:
    """'ok' or 'not_owner' for *device_id* on *action*. Fails closed."""
    if action not in OWNER_ONLY:
        return "ok"
    with _lock:
        fn = _owner_provider["fn"]
    if fn is None:
        return "ok"
    try:
        owners = fn()
    except Exception as exc:  # a broken provider refuses, it never allows
        logger.warning("owner devices provider failed (%s); owner-only "
                       "yes refused", type(exc).__name__)
        return "not_owner"
    if owners is None:
        return "ok"
    if not isinstance(owners, (set, frozenset)):
        logger.warning("owner devices provider answered %s, not a set; "
                       "owner-only yes refused", type(owners).__name__)
        return "not_owner"
    if not isinstance(device_id, str) or not device_id:
        return "not_owner"
    found = False
    for owner in owners:  # no early break: work does not depend on a match
        if isinstance(owner, str) and secrets.compare_digest(
                owner.encode("utf-8", "replace"),
                device_id.encode("utf-8", "replace")):
            found = True
    return "ok" if found else "not_owner"


# -- targets ------------------------------------------------------------------

def canonical_json(obj: Any) -> str:
    """One spelling of *obj*: sorted keys, no spaces, ASCII only."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _checked(target: str) -> str:
    if not _TARGET_RE.match(target):  # pragma: no cover - shapes below fit
        raise ValueError("that target does not fit the ticket shape")
    return target


def _app_id(app_id: Any, what: str = "app id") -> str:
    if not isinstance(app_id, str) or not _APP_ID_RE.match(app_id):
        raise ValueError(f"not a usable {what}")
    return app_id


def target_action_yes(action_id: str, args: dict) -> str:
    """``a:<sha256(action_id + "\\n" + canonical_json(args))[:40]>``."""
    if not isinstance(action_id, str) or not action_id:
        raise ValueError("not a usable action id")
    if not isinstance(args, dict):
        raise ValueError("action arguments must be a JSON object")
    try:
        text = action_id + "\n" + canonical_json(args)
    except (TypeError, ValueError):
        raise ValueError("action arguments must be plain JSON") from None
    return _checked("a:" + _sha(text)[:40])


def target_folder(app_id: str, realpath: str, mode: str) -> str:
    """``f:<app_id>:<sha256(normcase(realpath))[:24]>:<ro|rw>``."""
    app = _app_id(app_id)
    if mode not in ("ro", "rw"):
        raise ValueError("a folder is given read-only (ro) or read-write (rw)")
    if not isinstance(realpath, str) or not realpath or "\0" in realpath \
            or not os.path.isabs(realpath):
        raise ValueError("a folder must be a full path")
    digest = _sha(os.path.normcase(realpath))[:24]
    return _checked(f"f:{app}:{digest}:{mode}")


def _host_key(host: Any) -> str:
    text = host.strip().rstrip(".").lower() if isinstance(host, str) else ""
    if not text or len(text) > 253 or not text.isascii() or any(
            ch in "/\\@:?#[]%" or ord(ch) < 33 or ord(ch) == 127
            for ch in text):
        raise ValueError("not a usable host name")
    return text


def target_app_host(app_id: str, host: str) -> str:
    """``h:<app_id>:<sha256(host)[:24]>`` (host lower-cased, no trailing dot)."""
    app = _app_id(app_id)
    return _checked(f"h:{app}:{_sha(_host_key(host))[:24]}")


def target_box(distro: str) -> str:
    """``box:<distro lower-cased>``."""
    if not isinstance(distro, str) or not _DISTRO_RE.match(distro):
        raise ValueError("not a usable locked box name")
    return _checked("box:" + distro.lower())


def target_held(integration_id: str, rows_sha: str) -> str:
    """``held:<integration_id>:<rows_sha[:16]>``."""
    ident = _app_id(integration_id, "integration id")
    if not isinstance(rows_sha, str) or not _HEX_RE.match(rows_sha.lower()):
        raise ValueError("not a usable check digest")
    return _checked(f"held:{ident}:{rows_sha.lower()[:16]}")


def _digest(secret: str) -> bytes:
    return hashlib.sha256(secret.encode("utf-8", "replace")).digest()


def _audit(event: str, *, ticket_id: str, action: str, target: str,
           result: str) -> None:
    """A line in the process log and the audit trail. Never the secret.

    *action* and *target* are shown only when they are a known action and a
    plain, key-safe target: :func:`redeem` passes them on as the caller sent
    them.
    """
    action = action if action in ACTIONS else "(not shown)"
    if _TARGET_RE.match(target or ""):
        try:
            from puffpai import web_search
            target = web_search.log_name(target, placeholder="(not shown)")
        except Exception:  # never the raw name instead
            target = "(not shown)"
    else:
        target = "(not shown)"
    logger.info("consent ticket %s: %s %s [%s] -> %s", event, action, target,
                ticket_id, result)
    try:
        from puffpai.security.audit_log import AuditLog
        AuditLog().log(action=f"consent_ticket_{event}", user="owner",
                       result=result,
                       details=f"{action} {target} [{ticket_id}]")
    except Exception as exc:  # the process log above already has it
        logger.debug("consent ticket audit line skipped: %s", exc)


#: The most sites one build Yes may name in its one consent record.
BATCH_MAX = 200
_batch_local = threading.local()


def _batch_takes(act: str, tgt: str, device_id: str) -> bool:
    """Called under ``_lock``: True when this mint is one of the exact
    ``(action, target)`` pairs of the batch open in THIS thread for THIS
    device (each pair once). Such a mint does not count again against
    :data:`MINTS_PER_MINUTE`; the batch itself counted once."""
    b = getattr(_batch_local, "current", None)
    if not b or b["device"] != device_id or (act, tgt) not in b["pairs"]:
        return False
    b["pairs"].discard((act, tgt))
    return True


@contextlib.contextmanager
def yes_batch(device_id: str, pairs: Any, *, label: str = "",
              now: Optional[float] = None) -> Iterator[int]:
    """ONE consent record for the sites one build Yes covers.

    The build's Yes names a list of sites; switching each on still mints and
    spends its own single-use ticket (bound to its action, target and this
    device, with its own audit lines), but inside this block the mints for
    exactly *pairs* (``[(action, target), ...]``) on *device_id* in this
    thread count as ONE mint against :data:`MINTS_PER_MINUTE`, so a list of
    more than 19 sites is not refused half way. Any other mint (another
    target, another device, another thread) is throttled as always. Never a
    route: only in-process callers that already hold the recorded Yes open
    one. Yields the number of pairs. Raises :class:`TicketRefused` when the
    device cannot mint or the minute's budget is used up."""
    if not usable_device(device_id):
        raise TicketRefused("This device is not paired with your bot, so it cannot switch "
                            "outside services on.", "not_paired")
    want: set[tuple[str, str]] = set()
    for pair in pairs or ():
        try:
            act, tgt = _norm(pair[0]), _norm(pair[1])
        except (TypeError, IndexError):
            continue
        if act in ACTIONS and _TARGET_RE.match(tgt):
            want.add((act, tgt))
    if len(want) > BATCH_MAX:
        raise TicketRefused("That list is too long to switch on at once.", "too_many")
    clock = time.monotonic() if now is None else now
    with _lock:
        recent = [t for t in _mints.get(device_id, []) if clock - t < 60]
        if len(recent) >= MINTS_PER_MINUTE:
            raise TicketRefused("Too many tries in a minute. Wait a moment and "
                                "try again.", "throttled")
        recent.append(clock)
        _mints[device_id] = recent
    tag = re.sub(r"[^a-z0-9_:\-.]", "", str(label or "").lower())[:60] or "yes"
    _audit("batch", ticket_id="-", action="add_on", target=tag if _TARGET_RE.match(tag)
           else "yes", result=f"{len(want)} sites")
    previous = getattr(_batch_local, "current", None)
    _batch_local.current = {"device": device_id, "pairs": want}
    try:
        yield len(want)
    finally:
        _batch_local.current = previous


def mint(action: str, target: str, sentence: str, device_id: str, *,
         now: Optional[float] = None) -> str:
    """Mint a single-use ticket for exactly ``(action, target)`` on *device_id*.

    *sentence* is the server's own words for what will happen; it is kept
    with the ticket record in memory (never logged). Returns the secret
    ticket string (shown to the caller once, never stored).
    """
    act = _norm(action)
    tgt = _norm(target)
    if act not in ACTIONS:
        raise TicketRefused("That cannot be switched on this way.", "bad_action")
    if not _TARGET_RE.match(tgt):
        raise TicketRefused("That is not something that can be switched on.",
                            "bad_target")
    if not usable_device(device_id):
        raise TicketRefused(
            "This device is not paired with your bot, so it cannot switch "
            "outside services on.", "not_paired")
    if _owner_verdict(act, device_id) != "ok":
        _audit("refuse", ticket_id="-", action=act, target=tgt,
               result="not_owner")
        raise TicketRefused(_NOT_OWNER_WORDS, "not_owner")
    text = str(sentence or "").strip()
    if not text:
        raise TicketRefused("There is nothing to agree to.", "no_sentence")
    clock = time.monotonic() if now is None else now
    with _lock:
        if not _batch_takes(act, tgt, device_id):
            recent = [t for t in _mints.get(device_id, []) if clock - t < 60]
            if len(recent) >= MINTS_PER_MINUTE:
                raise TicketRefused("Too many tries in a minute. Wait a moment and "
                                    "try again.", "throttled")
            recent.append(clock)
            _mints[device_id] = recent
        if len(_mints) > 256:
            for key in list(_mints)[:64]:
                _mints.pop(key, None)
        secret = secrets.token_urlsafe(32)
        record = {
            "id": secrets.token_hex(4),
            "digest": _digest(secret),
            "action": act, "target": tgt, "device": device_id,
            "sentence": text[:600],
            "expires_at": clock + TTL_SECONDS,
        }
        _live[:] = [t for t in _live if t["expires_at"] > clock]
        while len(_live) >= MAX_LIVE:
            _live.pop(0)
        _live.append(record)
    _audit("mint", ticket_id=record["id"], action=act, target=tgt,
           result="minted")
    return secret


def redeem(ticket: Any, device_id: Any, action: str, target: str, *,
           now: Optional[float] = None) -> tuple[bool, str]:
    """Spend *ticket* if it was minted for this exact action on this device.

    Returns ``(ok, verdict)``; verdict is one of ``ok``, ``absent`` (no ticket
    given), ``unknown`` (forged, garbage or already spent), ``expired``,
    ``wrong_device``, ``mismatch`` (a real ticket for another action),
    ``not_owner`` (an owner-only action from a device that is not the
    owner's, see :func:`set_owner_devices_provider`).
    """
    if not isinstance(ticket, str) or not ticket:
        return False, "absent"
    offered = _digest(ticket)
    clock = time.monotonic() if now is None else now
    act, tgt = _norm(action), _norm(target)
    with _lock:
        matched = None
        for record in _live:  # no early break: work does not depend on a match
            if secrets.compare_digest(record["digest"], offered):
                matched = record
        if matched is not None:
            _live.remove(matched)  # single use, before anything else
        _live[:] = [t for t in _live if t["expires_at"] > clock]
    if matched is None:
        _audit("refuse", ticket_id="-", action=act, target=tgt,
               result="unknown")
        return False, "unknown"
    verdict = "ok"
    if matched["expires_at"] <= clock:
        verdict = "expired"
    elif not (isinstance(device_id, str)
              and secrets.compare_digest(matched["device"], device_id)):
        verdict = "wrong_device"
    elif matched["action"] != act or matched["target"] != tgt:
        verdict = "mismatch"
    elif _owner_verdict(act, device_id) != "ok":
        # Checked again at redeem: the owner list may have changed since mint.
        verdict = "not_owner"
    _audit("redeem" if verdict == "ok" else "refuse", ticket_id=matched["id"],
           action=act, target=tgt, result=verdict)
    return verdict == "ok", verdict


_NOT_OWNER_WORDS = ("Only the owner of this bot can say yes to that, on "
                    "one of their own devices.")


def refusal_words(verdict: str) -> str:
    """Plain words for a redeem that did not go through."""
    return {
        "absent": "Nothing was switched on: the yes did not come with this "
                  "request. Start again.",
        "unknown": "Nothing was switched on: that yes was already used or "
                   "is not one this bot gave out. Start again.",
        "expired": "Nothing was switched on: that yes is more than 10 minutes "
                   "old. Start again.",
        "wrong_device": "Nothing was switched on: that yes was given on a "
                        "different device. Start again on this one.",
        "mismatch": "Nothing was switched on: that yes was for something "
                    "else. Start again.",
        "not_owner": _NOT_OWNER_WORDS,
    }.get(verdict, "Nothing was switched on. Start again.")


def reset() -> None:
    """Forget every ticket (tests)."""
    with _lock:
        _live.clear()
        _mints.clear()


__all__ = ["ACTIONS", "BATCH_MAX", "MAX_LIVE", "OWNER_ONLY", "TTL_SECONDS", "TicketRefused",
           "canonical_json", "mint", "redeem", "refusal_words", "reset",
           "set_owner_devices_provider", "target_action_yes", "target_app_host",
           "target_box", "target_folder", "target_held", "terminal_device_id",
           "usable_device", "yes_batch"]
