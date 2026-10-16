"""Messages the bot sends on its own, kept where the owner will see them.

Ported from HomeBotBuild's ``announcer.py`` (the queue half). BuildBot has no
voice satellite to speak through, so that transport is left out: every
announcement is appended to ``announcements.jsonl`` in the state folder, the
dashboard's home card reads it through ``GET /announcements``, and the next
chat shows anything new once, as "While you were away: ...".

Speaking aloud, only when the owner switched it on (``puffpai routines speak
on`` or the card's switch):

* **On this computer's speaker** when a voice engine works here (Piper plus an
  audio output). The record is marked ``spoken: true``.
* **Otherwise the dashboard says it**, through the same speak path the chat
  uses (the backend voice when there is one, else the browser's own voice),
  for any message not already spoken. So a message is said once, not twice.

On a computer with no voice engine (the usual Windows install) the first path
reports why in plain words (:func:`box_voice_status`) and the second does the
speaking. Tests swap the speaker with :func:`set_speaker`.

Never raises: an announcement is a courtesy, and no caller should have to
guard against its failure.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

QUEUE_FILENAME = "announcements.jsonl"
CHAT_CURSOR_FILENAME = "announcements.chat_cursor"

#: An announcement is a few sentences, not a report.
MAX_TEXT_CHARS = 600

#: A rolling window, not an archive.
MAX_QUEUE_ENTRIES = 200

#: How many unseen messages the next chat carries. Older ones stay on the card.
MAX_CHAT_CATCHUP = 3

_LOCK = threading.Lock()


def _state_dir() -> str:
    from puffpai.config import get_state_dir
    return get_state_dir()


def queue_path() -> str:
    return os.path.join(_state_dir(), QUEUE_FILENAME)


def chat_cursor_path() -> str:
    return os.path.join(_state_dir(), CHAT_CURSOR_FILENAME)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_lines() -> list[str]:
    path = queue_path()
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as fh:
        return [line for line in fh.read().splitlines() if line.strip()]


# ── speaking aloud ───────────────────────────────────────────────────────

_speaker = None          # injected speaker: fn(text) -> bool (True = it spoke)
_box_voice = None        # cached (engine, sounddevice) or False when absent
_box_voice_reason = ""


def set_speaker(fn) -> None:
    """Replace the speaker (tests, or a different audio path). None = default."""
    global _speaker
    _speaker = fn


def speak_aloud_on() -> bool:
    """Did the owner switch speaking on? Unreadable means no."""
    try:
        from puffpai import routines
        return bool(routines.settings().get("speak_aloud"))
    except Exception:
        return False


def _load_box_voice():
    global _box_voice, _box_voice_reason
    if _box_voice is not None:
        return _box_voice
    try:
        from puffpai.voice.tts import PiperTTS
        engine = PiperTTS()
        if not engine.is_available():
            raise RuntimeError("no voice engine")
        import sounddevice  # noqa: F401  (optional dependency)
        _box_voice = (engine, sounddevice)
    except Exception as exc:
        logger.info("announcer: no voice on this computer: %s", exc)
        _box_voice = False
        _box_voice_reason = ("This computer has no working voice engine, so the "
                             "dashboard will read updates out loud instead.")
    return _box_voice


def box_voice_status() -> tuple[bool, str]:
    """(this computer can speak by itself, plain reason when it cannot)."""
    return bool(_load_box_voice()), _box_voice_reason


def _box_speak(text: str) -> bool:
    voice = _load_box_voice()
    if not voice:
        return False
    engine, sd = voice
    pcm = engine.speak(text)
    if not pcm:
        return False
    import numpy as np
    sd.play(np.frombuffer(pcm, dtype=np.int16), samplerate=engine.sample_rate,
            blocking=False)
    return True


def _maybe_speak(text: str) -> bool:
    if not speak_aloud_on():
        return False
    try:
        return bool((_speaker or _box_speak)(text))
    except Exception as exc:
        logger.warning("announcer: speaking failed: %s", exc)
        return False


def _private_safe(text: str, source: str) -> str:
    """*text* with any personal detail filed to the vault and a marker left.

    If the scan itself fails the text is replaced, never stored unchecked.
    """
    if not text:
        return text
    try:
        from puffpai.security.chat_privacy import protect_stored_text
        safe, _result = protect_stored_text(text, source=source,
                                            limit=MAX_TEXT_CHARS)
        return safe
    except Exception as exc:
        logger.warning("announcer: could not check text for personal details "
                       "(%s)", type(exc).__name__)
        return "[not saved: it could not be checked for personal details]"


def announce(text: str, *, source: str = "system", title: str = "") -> dict:
    """Record *text* for the owner, and say it aloud when that is switched on.

    Returns ``{"ok", "queued", "id", "text", "spoken"}``.
    """
    clean = " ".join(str(text or "").split())[:MAX_TEXT_CHARS]
    if not clean:
        return {"ok": False, "queued": False, "id": None, "text": "", "spoken": False}
    # The queue is plain text on disk and is read back in chat: a personal
    # detail is filed to the vault and only its plain marker is kept here.
    clean = _private_safe(clean, "announcement")
    spoken = _maybe_speak(clean)
    record = {
        "id": uuid.uuid4().hex[:12],
        "ts": _utcnow(),
        "text": clean,
        "title": _private_safe(" ".join(str(title or "").split())[:80],
                               "announcement_title"),
        "source": str(source)[:60],
        "spoken": spoken,
    }
    try:
        path = queue_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with _LOCK:
            lines = _read_lines()
            lines.append(json.dumps(record, ensure_ascii=False))
            lines = lines[-MAX_QUEUE_ENTRIES:]
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
            os.replace(tmp, path)
    except Exception as exc:
        logger.warning("announcer: could not write the queue: %s", exc)
        return {"ok": False, "queued": False, "id": None, "text": clean,
                "spoken": spoken}
    logger.info("announcer: queued %s from %s", record["id"], record["source"])
    return {"ok": True, "queued": True, "id": record["id"], "text": clean,
            "spoken": spoken}


def recent(limit: int = 20) -> list[dict]:
    """Newest first."""
    limit = max(1, min(int(limit), MAX_QUEUE_ENTRIES))
    try:
        lines = _read_lines()
    except Exception as exc:
        logger.warning("announcer: could not read the queue: %s", exc)
        return []
    out: list[dict] = []
    for line in lines[-limit:]:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("text"):
            out.append(row)
    out.reverse()
    return out


def _read_cursor() -> str:
    try:
        with open(chat_cursor_path(), "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _write_cursor(ts: str) -> None:
    try:
        path = chat_cursor_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(ts)
    except OSError as exc:
        logger.warning("announcer: could not save the chat cursor: %s", exc)


def unseen_in_chat() -> list[dict]:
    """Messages newer than the last one a chat showed, oldest first."""
    cursor = _read_cursor()
    rows = [r for r in reversed(recent(MAX_QUEUE_ENTRIES))
            if str(r.get("ts") or "") > cursor]
    return rows[-MAX_CHAT_CATCHUP:]


def take_chat_catchup() -> Optional[str]:
    """The 'While you were away' text for the next chat, once. None if nothing."""
    with _LOCK:
        rows = unseen_in_chat()
        if not rows:
            return None
        _write_cursor(str(rows[-1].get("ts") or ""))
    if len(rows) == 1:
        return f"While you were away: {rows[0]['text']}"
    body = "\n".join(f"- {r['text']}" for r in rows)
    return f"While you were away:\n{body}"


__all__ = ["announce", "recent", "unseen_in_chat", "take_chat_catchup",
           "queue_path", "set_speaker", "speak_aloud_on", "box_voice_status"]
