"""Chat privacy: one place that keeps personal details out of saved chats.

Every chat path (HTTP chat, the dashboard's save call, streaming, the
OpenAI-compatible route, voice, and ``puffpai chat``) goes through
:func:`protect_text` for what a person typed, and every conversation that is
written to disk goes through :func:`scrub_conversation` first, which also
catches assistant replies that repeat a detail back.

What happens to a detected detail
---------------------------------
It is never kept in plain text and never silently dropped:

* vault open      -> filed in the vault now (``kept = "vault"``);
* vault locked    -> sealed into the write-only inbox, filed at next unlock
                     (``kept = "inbox"``, see :mod:`vault_inbox`);
* vault locked, and its inbox lock cannot be confirmed (it may have been
  swapped) -> held in this process's memory, filed at the next unlock here
                     (``kept = "held"``); lost if the computer restarts first,
                     and the person is told so;
* no vault yet, or a vault that has not been opened since the inbox existed
                  -> not kept anywhere, and the person is told so in plain
                     words (``kept = "not_kept"``).

In every case the saved chat holds a plain marker such as
``[phone number kept private]`` instead of the value.

Repeats with no naming word
---------------------------
The detector finds a name only after a naming word ("my name is Casey
Verifier"). A reply that says "Thanks, Casey Verifier!" has no such word, so
the detector alone would miss it. Every detail a person gives is therefore
remembered for ``_ECHO_SECONDS`` as a keyed hash (never the value itself,
never on disk), and any later copy of it -- in a reply or in a later message --
is replaced with the same marker before saving. A full name also covers each
part of it ("Casey", "Verifier"). This errs toward hiding: a part of a name
that is also an everyday word ("Will") is hidden too, in the saved copy only.

What this does not do: the local model still reads the original words for the
turn in which they were typed (it runs on this computer; nothing is sent out
by this module). Lists and notes the person asks the bot to write are stored
as asked.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

from puffpai.security.data_classifier import (
    PLAIN_WORDS, REDACTION_MARKERS, DataClassifier,
)
from puffpai.security import vault_inbox

logger = logging.getLogger(__name__)

_classifier = DataClassifier()

KEPT_VAULT = "vault"
KEPT_INBOX = "inbox"
KEPT_HELD = "held"
NOT_KEPT = "not_kept"

# The same detail seen twice in a short time is one detail, not two. The
# dashboard's stream scans a message and its save call may carry it again; the
# second sighting reuses the first outcome instead of filing a copy. Only a
# hash of the values is kept, in memory, for _RECENT_SECONDS.
_RECENT_SECONDS = 300.0
_recent: dict[str, tuple[float, str, str]] = {}
_recent_lock = threading.Lock()


def _fingerprint(items: dict[str, list[str]]) -> str:
    blob = json.dumps(items, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _recently_filed(key: str) -> Optional[tuple[str, str]]:
    now = time.monotonic()
    with _recent_lock:
        for k in [k for k, (t, _, _) in _recent.items()
                  if now - t > _RECENT_SECONDS]:
            del _recent[k]
        hit = _recent.get(key)
    return (hit[1], hit[2]) if hit else None


def _remember(key: str, kept: str, vault_state: str) -> None:
    # A HELD outcome is not reused: the item may be filed at an unlock in
    # between, and "holding it in memory" would then be untrue. A repeat is
    # held again (a duplicate record is the cost, never a lost one).
    if kept in (KEPT_VAULT, KEPT_INBOX):
        with _recent_lock:
            _recent[key] = (time.monotonic(), kept, vault_state)


# Details a person gave, remembered so a later copy with no naming word is
# still hidden. Only keyed hashes are held (key made fresh per process, never
# stored), in memory, for _ECHO_SECONDS, at most _ECHO_MAX entries.
_ECHO_SECONDS = 12 * 3600.0
_ECHO_MAX = 2000
_ECHO_MAX_WORDS = 8
_ECHO_KEY = os.urandom(32)
# digest -> (seen_at, kind, word count)
_echo: dict[str, tuple[float, str, int]] = {}
_echo_lock = threading.Lock()
# Kinds matched without regard to case ("Asthma" / "asthma").
_FOLDED_KINDS = frozenset({"health.condition", "email"})
_LEAD = "\"'([{<\u201c\u2018"
_TRAIL = ".,;:!?\"')]}>\u201d\u2019"


def _echo_digest(value: str, folded: bool) -> str:
    tag = "f:" if folded else "x:"
    data = (tag + (value.casefold() if folded else value)).encode("utf-8")
    return hmac.new(_ECHO_KEY, data, hashlib.sha256).hexdigest()


def _echo_forms(kind: str, value: str) -> list[str]:
    value = " ".join(str(value or "").split())
    # "Sunflower!22," was taken whole; its copy may come without the comma.
    trimmed = value.lstrip(_LEAD).rstrip(_TRAIL)
    forms = [f for f in dict.fromkeys((value, trimmed)) if len(f) >= 3]
    if kind == "name":
        # "Casey Verifier" also covers "Casey" and "Verifier" on their own.
        forms += [w for w in trimmed.split()
                  if len(w) >= 3 and w[:1].isupper()]
    return forms


def remember_details(items: dict[str, list[str]]) -> None:
    """Remember detected values (as keyed hashes) so repeats are hidden too."""
    now = time.monotonic()
    with _echo_lock:
        for kind, values in (items or {}).items():
            folded = kind in _FOLDED_KINDS
            for value in values or []:
                for form in _echo_forms(kind, value):
                    words = len(form.split())
                    if words > _ECHO_MAX_WORDS:
                        continue
                    _echo[_echo_digest(form, folded)] = (now, kind, words)
        for k in [k for k, (t, _, _) in _echo.items()
                  if now - t > _ECHO_SECONDS]:
            del _echo[k]
        if len(_echo) > _ECHO_MAX:
            oldest = sorted(_echo.items(), key=lambda kv: kv[1][0])
            for k, _ in oldest[:len(_echo) - _ECHO_MAX]:
                del _echo[k]


def forget_details() -> None:
    """Drop every remembered detail (tests, and a future "forget" button)."""
    with _echo_lock:
        _echo.clear()


def _echo_lookup(candidate: str, now: float) -> Optional[str]:
    if not candidate:
        return None
    for folded in (False, True):
        hit = _echo.get(_echo_digest(candidate, folded))
        if hit is None or now - hit[0] > _ECHO_SECONDS:
            continue
        if not folded or hit[1] in _FOLDED_KINDS:
            return hit[1]
    return None


def hide_repeats(text: str) -> tuple[str, list[str]]:
    """Replace any remembered detail in ``text`` with its plain marker.

    Returns ``(text, kinds_found)``. Words are compared whole, with leading
    and trailing punctuation (and a trailing "'s") set aside, so "Casey's"
    and "Verifier!" are caught while "Caseyville" is not.
    """
    if not isinstance(text, str) or not text:
        return text, []
    now = time.monotonic()
    spans: list[tuple[int, int, str]] = []
    with _echo_lock:
        max_words = max((v[2] for v in _echo.values()
                         if now - v[0] <= _ECHO_SECONDS), default=0)
        if not max_words:
            return text, []
        words = [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]
        i = 0
        while i < len(words):
            found = None
            for n in range(min(max_words, len(words) - i), 0, -1):
                start, end = words[i][0], words[i + n - 1][1]
                s, e = start, end
                while s < e and text[s] in _LEAD:
                    s += 1
                while e > s and text[e - 1] in _TRAIL:
                    e -= 1
                tries = [(start, end), (s, e)]
                if text[s:e].endswith(("'s", "\u2019s")):
                    tries.append((s, e - 2))
                for a, b in tries:
                    kind = _echo_lookup(text[a:b], now)
                    if kind is not None:
                        found = (a, b, kind, n)
                        break
                if found:
                    break
            if found:
                spans.append(found[:3])
                i += found[3]
            else:
                i += 1
    if not spans:
        return text, []
    out, last, kinds = [], 0, []
    for a, b, kind in spans:
        out.append(text[last:a])
        out.append(REDACTION_MARKERS.get(kind, "[personal detail kept private]"))
        last = b
        if kind not in kinds:
            kinds.append(kind)
    out.append(text[last:])
    return "".join(out), kinds


def redact(text: str) -> tuple[str, Any]:
    """Redact only (no vaulting). Returns ``(redacted_text, detection)``."""
    if not isinstance(text, str) or not text:
        return text, _classifier.scan_for_pii("")
    return _classifier.redact_pii(text, include_health=True)


def safe_for_log(text: str, limit: int = 80) -> str:
    """A log-safe preview of ``text``: redacted first, then cut short."""
    redacted, _ = redact(str(text or ""))
    redacted, _ = hide_repeats(redacted)
    return redacted[:limit]


def _plain_list(types: list[str]) -> str:
    words = []
    for t in types:
        w = PLAIN_WORDS.get(t, "personal detail")
        if w not in words:
            words.append(w)
    if len(words) == 1:
        return words[0]
    return ", ".join(words[:-1]) + " and " + words[-1]


def plain_notice(types: list[str], kept: Optional[str],
                 vault_state: str = "") -> str:
    """One plain sentence telling the person what happened.

    ``vault_state`` is ``"missing"`` (no vault set up) or ``"never_opened"``
    (a vault that has not been opened since it could take things while locked);
    only used when ``kept`` is :data:`NOT_KEPT`.
    """
    if not types:
        return ""
    what = _plain_list(types)
    plural = len(types) > 1 or PLAIN_WORDS.get(types[0], "").endswith(
        ("details", "digits of a card or account"))
    it = "them" if plural else "it"
    head = f"I kept the {what} out of my saved chats"
    if kept == KEPT_VAULT:
        return f"{head} and put {it} in your vault."
    if kept == KEPT_INBOX:
        return (f"{head} and put {it} in your locked vault. You will see "
                f"{it} there next time you open the vault with your PIN.")
    if kept == KEPT_HELD:
        return (f"{head}. I could not confirm the lock on your vault, so I am "
                f"holding {it} in memory until you open the vault with your "
                f"PIN. If this computer restarts first, {it} will be gone.")
    if vault_state == "full":
        return (f"{head}. Too many details are already waiting for your "
                f"vault, so I did not keep a copy this time.")
    if vault_state == "missing":
        return (f"{head}. You have not set up a vault yet, so I did not "
                f"keep a copy anywhere.")
    if vault_state == "not_plugged_in":
        return (f"{head}. Your vault drive is not plugged in, so I did not "
                f"keep a copy anywhere.")
    if vault_state == "never_opened":
        return (f"{head}. Your vault has to be opened once with your PIN "
                f"before it can take things while it is locked, so I did not "
                f"keep a copy this time.")
    return f"{head}. I could not put {it} in your vault, so I did not keep a copy."


def _file(vault, record: dict[str, Any]) -> tuple[str, str]:
    """File ``record``; returns ``(kept, vault_state)``. Never raises."""
    if vault is None or not getattr(vault, "is_initialized", False):
        from puffpai.security.vault import missing_words
        if vault is not None and missing_words(vault):
            return NOT_KEPT, "not_plugged_in"   # never "no vault yet"
        return NOT_KEPT, "missing"
    try:
        if vault.is_unlocked:
            vault_inbox.append_pii_record(vault, record)
            return KEPT_VAULT, "open"
        status = vault_inbox.deposit_status(vault.vault_dir, record)
        if status == vault_inbox.SEALED:
            return KEPT_INBOX, "locked"
        if status == vault_inbox.HELD:
            return KEPT_HELD, "held"
        if status == vault_inbox.FULL:
            return NOT_KEPT, "full"
        if status == vault_inbox.TOO_BIG:
            return NOT_KEPT, "error"
        return NOT_KEPT, "never_opened"
    except Exception:
        # The value is not logged; only that filing failed.
        logger.exception("Chat privacy: could not file a detected detail")
        return NOT_KEPT, "error"


def protect_text(text: str, *, source: str, vault=None,
                 role: str = "user") -> dict[str, Any]:
    """Scan, redact and file one message. Returns the chat-route result dict.

    Keys (the shape the chat routes already used, plus two):
    ``redacted_text``, ``original_text``, ``pii_detected``, ``pii_summary``,
    ``pii_types``, ``severity``, ``vaulted_items``, ``kept`` and ``notice``.

    ``role="assistant"`` redacts only: a reply that repeats a detail is either
    echoing what the person said (already filed) or reading from the vault.
    Either way, a detail the person gave earlier is hidden even when the copy
    has no naming word (see :func:`hide_repeats`).
    """
    redacted, detection = redact(text)
    if detection.has_pii and role == "user":
        remember_details(detection.items())
    redacted, _repeats = hide_repeats(redacted)
    result: dict[str, Any] = {
        "redacted_text": redacted,
        "original_text": text,
        "pii_detected": bool(detection.has_pii),
        "pii_summary": detection.summary() if detection.has_pii else None,
        "pii_types": detection.pii_types if detection.has_pii else [],
        "severity": detection.severity,
        "vaulted_items": [],
        "kept": None,
        "notice": "",
    }
    if not detection.has_pii or role != "user":
        return result
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "severity": detection.severity,
        "items": detection.items(),
    }
    try:
        state = ("open" if vault is not None and vault.is_unlocked
                 else "locked" if vault is not None and vault.is_initialized
                 else "missing")
    except Exception:
        state = "unknown"
    key = state + ":" + _fingerprint(record["items"])
    earlier = _recently_filed(key)
    if earlier is not None:
        kept, vault_state = earlier
    else:
        kept, vault_state = _file(vault, record)
        _remember(key, kept, vault_state)
    result["kept"] = kept
    if kept in (KEPT_VAULT, KEPT_INBOX, KEPT_HELD):
        result["vaulted_items"] = list(record["items"].keys())
    result["notice"] = plain_notice(result["pii_types"], kept, vault_state)
    logger.info("Chat privacy: %s (%s) -> %s", detection.summary(), source, kept)
    return result


#: What a saved message says when it could not be checked. The words the
#: person typed are not kept: keeping them unchecked would break the promise
#: that saved chats never hold a personal detail in plain text.
UNCHECKED_PLACEHOLDER = ("[This message could not be checked for personal "
                         "details, so it was not saved]")


def scrub_conversation(conv: dict, *, vault=None,
                       source: str = "chat_save") -> int:
    """Redact every message of ``conv`` in place before it is written to disk.

    User messages that still hold a detail (a path that skipped the scan) are
    filed as well; assistant messages are redacted only. Returns how many
    messages changed. Never raises.

    Each message is scrubbed on its own. If scrubbing one fails, THAT message
    is replaced by :data:`UNCHECKED_PLACEHOLDER` (never kept in plain text) and
    the rest keep being scrubbed; the chat itself is not lost. A message whose
    content is not text is replaced the same way.
    """
    changed = 0
    try:
        raw = conv.get("messages") if isinstance(conv, dict) else None
        msgs = [m for m in (raw if isinstance(raw, list) else [])
                if isinstance(m, dict) and m.get("content")]
    except Exception:  # pragma: no cover - a conversation we cannot even read
        logger.error("Chat privacy: could not read a conversation to scrub it")
        return 0
    # The person's messages first, so a detail they gave (on a path that
    # forgot to scan) is remembered before any reply is checked.
    order = ([m for m in msgs if m.get("role") == "user"]
             + [m for m in msgs if m.get("role") != "user"])
    for msg in order:
        try:
            content = msg["content"]
            if not isinstance(content, str):
                raise TypeError("message content is not text")
            role = "user" if msg.get("role") == "user" else "assistant"
            res = protect_text(content, source=source, vault=vault, role=role)
            if res["redacted_text"] != content:
                msg["content"] = res["redacted_text"]
                changed += 1
        except Exception as exc:
            # Only the kind of failure is logged, never the message.
            logger.error("Chat privacy: one message could not be checked "
                         "(%s); it is saved as a placeholder",
                         type(exc).__name__)
            try:
                msg["content"] = UNCHECKED_PLACEHOLDER
                changed += 1
            except Exception:  # pragma: no cover - an unwritable message
                pass
    return changed


def _locked_vault_handle():
    """A handle on the configured vault that is never opened here.

    Only its folder and whether it exists are read, so filing through it
    either seals into the inbox or holds; it never unlocks, never drains and
    never writes the vault itself (a second open handle could overwrite the
    live one's contents).
    """
    try:
        from puffpai.security.vault import Vault
        return Vault()
    except Exception:
        return None


def protect_stored_text(text: Any, *, source: str, limit: int = 600
                        ) -> tuple[str, dict[str, Any]]:
    """Scan text an owner or the bot is about to STORE (not a chat message).

    For routine labels and messages and announcements: a detected detail is
    filed the same way chat files it (locked vault inbox, or held), and the
    stored text keeps only the plain marker. Returns ``(safe_text, result)``.
    Never raises; if scanning fails, the text is replaced by a placeholder
    rather than stored unchecked.
    """
    raw = str(text or "")[:limit]
    if not raw.strip():
        return raw, {"pii_detected": False, "kept": None, "notice": ""}
    try:
        res = protect_text(raw, source=source, vault=_locked_vault_handle())
    except Exception as exc:
        logger.error("Chat privacy: could not check text before storing it "
                     "(%s)", type(exc).__name__)
        return STORED_PLACEHOLDER, {"pii_detected": False, "kept": None,
                                    "notice": "", "unchecked": True}
    return res["redacted_text"], res


#: Stored in place of text that could not be checked before storing.
STORED_PLACEHOLDER = "[not saved: it could not be checked for personal details]"


__all__ = [
    "KEPT_VAULT", "KEPT_INBOX", "KEPT_HELD", "NOT_KEPT", "STORED_PLACEHOLDER",
    "UNCHECKED_PLACEHOLDER", "redact", "safe_for_log", "plain_notice",
    "protect_text", "protect_stored_text", "scrub_conversation",
    "remember_details", "forget_details", "hide_repeats",
]
