"""Which paired devices are real: the dashboard's pairing store, read-only.

The dashboard proxy asserts ``X-Puff-Device-Id`` from a VERIFIED pairing
cookie. The value is not the device id itself but
``HMAC-SHA256(pairing secret, "puff-chat-session:v1:" + device id)[:32]``
(``deviceSessionId`` in ``dashboard/app/_lib/server/pairing.ts``). Until now
the backend only checked the value's SHAPE, so any process on this computer
holding the API key could invent one and pass every "paired device" gate.

This module recomputes the same value for every device in the paired-device
list (``devices.json`` beside ``pairing.json``) and answers whether an
asserted value belongs to one of them. Nothing here writes: the files belong
to the dashboard and to :mod:`puffpai.pairing_code`.

Fail closed: a missing, unreadable or malformed ``pairing.json`` or
``devices.json`` means no device is paired. Both files are re-read when their
stamp changes, so un-pairing a device takes effect on its next request.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
from typing import Any, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

#: ``DEVICE_SESSION_LABEL`` in pairing.ts.
SESSION_LABEL = "puff-chat-session:v1:"
#: ``DEVICE_ID_RE`` in pairing.ts.
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{12}\Z")
#: What the proxy asserts: 32 lowercase hex characters.
SESSION_RE = re.compile(r"^[0-9a-f]{32}\Z")
_SECRET_RE = re.compile(r"^[0-9a-f]{64}\Z")
#: The registry is tiny; a file larger than this is not one.
MAX_FILE_BYTES = 256 * 1024
#: Shown for a device that is not (or no longer) in the list.
UNKNOWN_LABEL = "a device that is not paired"

_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "rows": []}


def _stamp(path: Optional[str]) -> Any:
    if not path:
        return None
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return (path, None)
    except OSError:
        return None
    return (path, st.st_mtime_ns, st.st_size, getattr(st, "st_ino", 0))


def pairing_file() -> Optional[str]:
    """Where this build's ``pairing.json`` is, worked out WITHOUT writing.

    The same order as :func:`puffpai.pairing_code.state_path`, minus its side
    effects (it may record the build id and claim the old shared file):
    ``PUFF_PAIRING_STATE``; this build's own file when it exists; the code
    copy's old shared file only when this build already owns it."""
    override = os.environ.get("PUFF_PAIRING_STATE")
    if override:
        return override
    try:
        from puffpai import pairing_code
        from puffpai.security import build_identity
        own = pairing_code.per_build_path()
        if os.path.isfile(own):
            return own
        legacy = pairing_code.legacy_path()
        if legacy and os.path.isfile(legacy):
            owner_file = os.path.join(os.path.dirname(legacy), pairing_code.OWNER_FILE)
            if pairing_code._read_owner(owner_file) == build_identity.build_id():  # noqa: SLF001
                return legacy
        return own
    except Exception as exc:  # noqa: BLE001 - not knowing where: nobody is paired
        logger.debug("pairing store not found: %s", type(exc).__name__)
        return None


def devices_file(pairing: Optional[str] = None) -> Optional[str]:
    """``devices.json``: ``PUFF_PAIRED_DEVICES``, else beside ``pairing.json``."""
    override = os.environ.get("PUFF_PAIRED_DEVICES")
    if override:
        return override
    pairing = pairing or pairing_file()
    return os.path.join(os.path.dirname(pairing), "devices.json") if pairing else None


def _read_json(path: str) -> Any:
    if os.path.getsize(path) > MAX_FILE_BYTES:
        raise ValueError("too large")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _session_id(secret: str, device_id: str) -> str:
    return hmac.new(bytes.fromhex(secret), (SESSION_LABEL + device_id).encode("utf-8"),
                    hashlib.sha256).hexdigest()[:32]


def _clean_name(value: Any) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(value or ""))
    return " ".join(text.split())[:40]


def _rows() -> list[dict]:
    """``[{"session", "id", "name"}]`` for every paired device. [] on any doubt."""
    pairing = pairing_file()
    devices = devices_file(pairing)
    key = (_stamp(pairing), _stamp(devices))
    cacheable = None not in key
    with _lock:
        if cacheable and _cache["key"] == key:
            return list(_cache["rows"])
    rows: list[dict] = []
    try:
        if not pairing or not devices:
            raise FileNotFoundError("no pairing store")
        state = _read_json(pairing)
        secret = state.get("secret") if isinstance(state, dict) else None
        if not isinstance(secret, str) or not _SECRET_RE.match(secret):
            raise ValueError("pairing secret missing or malformed")
        listed = _read_json(devices)
        found = listed.get("devices") if isinstance(listed, dict) else None
        if not isinstance(found, list):
            raise ValueError("device list malformed")
        for rec in found:
            if not isinstance(rec, dict):
                continue
            did = rec.get("id")
            if not isinstance(did, str) or not DEVICE_ID_RE.match(did) \
                    or not isinstance(rec.get("name"), str) \
                    or not isinstance(rec.get("createdAt"), str):
                continue          # the same per-row check pairing.ts makes
            rows.append({"session": _session_id(secret, did), "id": did,
                         "name": _clean_name(rec.get("name"))})
    except FileNotFoundError:
        rows = []
    except Exception as exc:  # noqa: BLE001 - unreadable: nobody is paired
        logger.warning("paired-device list unreadable (%s); no device counts as "
                       "paired", type(exc).__name__)
        rows = []
    with _lock:
        _cache.update({"key": key if cacheable else None, "rows": rows})
    return list(rows)


def forget() -> None:
    """Drop the cached reading (tests)."""
    with _lock:
        _cache.update({"key": None, "rows": []})


def lookup(session_id: Any) -> Optional[dict]:
    """``{"id", "name", "label"}`` of the paired device whose derived session
    id is *session_id*, or None when no paired device has it."""
    sid = str(session_id or "")
    if not SESSION_RE.match(sid):
        return None
    hit = None
    for row in _rows():
        # Every row is compared, so the time taken says nothing about which.
        if hmac.compare_digest(row["session"], sid) and hit is None:
            hit = row
    if hit is None:
        return None
    return {"id": hit["id"], "name": hit["name"], "label": _label(hit["name"], sid)}


def is_paired(session_id: Any) -> bool:
    return lookup(session_id) is not None


def _label(name: str, session_id: str) -> str:
    tag = hashlib.sha256(("puffpai-device-label:" + session_id).encode("utf-8")).hexdigest()[:6]
    return f"{name} (device {tag})" if name else f"device {tag}"


def label(client_id: Any) -> str:
    """A stable, non-secret label for logs: the device's own name and a short
    tag (``Kitchen tablet (device 3f9a1c)``). The tag is a hash of the session
    id, so it says which device without being one of its secrets. Accepts
    ``device:<id>`` or the bare id. Other callers get a plain word."""
    text = str(client_id or "")
    if text.startswith("terminal:"):
        return "this computer's terminal"
    if text.startswith("device:"):
        text = text[len("device:"):]
    found = lookup(text)
    return found["label"] if found else UNKNOWN_LABEL


__all__ = ["SESSION_LABEL", "UNKNOWN_LABEL", "devices_file", "forget", "is_paired",
           "label", "lookup", "pairing_file"]
