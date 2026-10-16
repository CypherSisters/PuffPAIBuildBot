"""The Home Assistant voice key: how voice may use the apps, and only a bit.

Voice through Home Assistant may READ app
data and make changes that can be UNDONE; removing always waits for the
dashboard. Anything on the home network can reach a LAN-bound backend, so a
caller with no key must still never run an app action. Home Assistant is told
apart by its own **voice key**: a per-build secret the owner pastes into Home
Assistant's OpenAI-compatible conversation setup as its key, which Home
Assistant then sends as ``Authorization: Bearer <voice key>`` on
``/v1/chat/completions``.

Caller classes (every chat rule reads one of these):

* voice key  -> identity ``voice:<fingerprint>``: reads and undoable changes,
  never removes, never an owner-only extra;
* paired device or terminal -> ``device:...`` / ``terminal:...``: everything,
  removes after a yes;
* no key -> no identity: ordinary chat only, no app actions.

Storage: like the build's API key, in this build's own password-store entry
(``puffpai`` / ``PUFFPAI_VOICE_KEY.<label>.<build id>``), else a key file in
the build's own state folder readable by this user only. Nothing here prints
or logs a key. There is no voice key until the owner asks for one (fail
closed), a new one replaces the old one at once, and "off" deletes it.

What this protects against: a device on the home network that can reach the
backend (no key) driving or reading the owner's apps through the keyless voice
routes; a voice caller removing anything; the voice key opening any other
route (it is not the API key: every other route still answers 401).
What it does not: anyone who reads the key out of Home Assistant's settings
can read the apps and make undoable changes, until the owner makes a new key.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import sys
import threading
from typing import Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

ENTRY_PREFIX = "PUFFPAI_VOICE_KEY"
KEY_FILE = "voice_key.txt"
PREFIX = "voice:"
#: Only this route accepts the voice key (Home Assistant's OpenAI-compatible
#: conversation setup). ``/api/chat`` (the Ollama shape) sends no key at all.
VOICE_PATHS = ("/v1/chat/completions",)
MIN_LEN = 40

_lock = threading.Lock()
_cache: dict[str, Optional[str]] = {}


def _entry(state: Optional[str] = None) -> str:
    from puffpai.security import build_identity as bi
    return f"{ENTRY_PREFIX}.{bi.build_label(state)}.{bi.build_id(state)}"


def _file(state: Optional[str] = None) -> str:
    from puffpai.security import build_identity as bi
    return os.path.join(bi.state_dir(state), KEY_FILE)


def _ring():
    from puffpai.security import api_key_store
    return api_key_store._keyring()  # noqa: SLF001 - the same store the API key uses


def _state_key() -> str:
    from puffpai.config import get_state_dir
    return os.path.realpath(get_state_dir())


def _read() -> str:
    ring = _ring()
    if ring is not None:
        try:
            from puffpai.security import api_key_store
            value = (ring.get_password(api_key_store.SERVICE, _entry()) or "").strip()
        except Exception:  # a locked or missing store is "nothing stored"
            value = ""
        if value:
            return value
    try:
        with open(_file(), "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def current() -> str:
    """This build's voice key, or "" when there is none. Never logged."""
    key = _state_key()
    with _lock:
        if key in _cache:
            return _cache[key] or ""
    value = _read()
    if value and len(value) < MIN_LEN:
        value = ""                       # not one this module made: no key
    with _lock:
        _cache[key] = value or None
    return value


def is_on() -> bool:
    return bool(current())


def _write_file(path: str, value: str) -> None:
    """The key file, readable by this user only (best effort on Windows)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(value)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    if sys.platform == "win32":
        user = os.environ.get("USERNAME")
        icacls = ""
        try:
            from puffpai.security import host_programs
            icacls = os.path.join(host_programs._system32(), "icacls.exe")  # noqa: SLF001
        except Exception:  # noqa: BLE001
            icacls = ""
        if user and icacls and os.path.isfile(icacls):
            import subprocess

            from puffpai import procflags
            try:
                subprocess.run([icacls, path, "/inheritance:r", "/grant:r", f"{user}:(R,W)"],
                               check=False, capture_output=True, timeout=10,
                               cwd=os.path.dirname(icacls), **procflags.hidden())
            except Exception:  # noqa: BLE001 - the profile's own ACL remains
                pass


def _remove_all_copies() -> None:
    ring = _ring()
    if ring is not None:
        try:
            from puffpai.security import api_key_store
            ring.delete_password(api_key_store.SERVICE, _entry())
        except Exception:  # noqa: BLE001 - nothing stored there
            pass
    try:
        os.remove(_file())
    except OSError:
        pass


def new() -> str:
    """Make a new voice key (the old one stops working now). Returns it once."""
    from puffpai.security import api_key_store
    value = secrets.token_urlsafe(36)
    # Never the API key (compared only when the backend is loaded; never logged).
    main = str(getattr(sys.modules.get("puffpai.api_server"), "_API_KEY", "") or "")
    while main and secrets.compare_digest(value, main):  # pragma: no cover - 2**288
        value = secrets.token_urlsafe(36)
    _remove_all_copies()
    ring = _ring()
    stored = False
    if ring is not None:
        try:
            ring.set_password(api_key_store.SERVICE, _entry(), value)
            stored = True
        except Exception:  # noqa: BLE001 - no working store: the file
            stored = False
    if not stored:
        _write_file(_file(), value)
    with _lock:
        _cache[_state_key()] = value
    logger.info("voice key: a new one was made")
    return value


def off() -> None:
    """No voice key any more: Home Assistant voice is ordinary chat again."""
    _remove_all_copies()
    with _lock:
        _cache[_state_key()] = None
    logger.info("voice key: switched off")


def forget_cache() -> None:
    with _lock:
        _cache.clear()


def matches(token: object) -> bool:
    """True when *token* is this build's voice key (constant time)."""
    if not isinstance(token, str) or len(token) < MIN_LEN:
        return False
    key = current()
    return bool(key) and secrets.compare_digest(token.encode("utf-8"), key.encode("utf-8"))


def identity() -> Optional[str]:
    """The voice caller's identity for the key that is on now, or None."""
    key = current()
    if not key:
        return None
    return PREFIX + "ha-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def is_identity(value: object) -> bool:
    """True for the identity of the voice key that is on NOW (a made-new key
    makes every older voice identity worthless at once)."""
    ident = identity()
    return isinstance(value, str) and ident is not None and secrets.compare_digest(value, ident)


def bearer(auth_header: object) -> str:
    text = str(auth_header or "")
    return text[7:].strip() if text.startswith("Bearer ") else ""


__all__ = ["PREFIX", "VOICE_PATHS", "bearer", "current", "forget_cache", "identity",
           "is_identity", "is_on", "matches", "new", "off"]
