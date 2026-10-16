"""May the bot go online at all? Its own go-online setting as a setup row.

Every outside call goes through the Bridge (``egress_doors``), and the Bridge
first asks the bot's run posture (the build record's ``posture.run_egress``):
only ``enumerated`` lets any switch open; ``island`` (the default) refuses
every call with ``bot_offline``, whatever the switches say. A build whose jobs
read outside data, on a bot set to stay off the internet, gets ONE row on its
setup list, "Let <Bot> go online", covered by her one Yes like the rest. It
widens nothing by itself: the bot then reaches only the sites that have their
own switch on (each still listed, each with its own ticket). A bot set to keep
everything on this computer (``sealed``) is never offered the row.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

from typing import Any

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

ROW_ID = "setting-go-online"
ROW_KEY = "online"


def run_egress() -> str:
    try:
        from puffpai.security import egress_doors
        return str(egress_doors.run_posture().get("run_egress") or "")
    except Exception:  # noqa: BLE001 - unknown is offline
        return ""


def is_online() -> bool:
    from puffpai.security import egress_doors
    return run_egress() == egress_doors.OPEN_POSTURE


def can_go_online() -> bool:
    """False for a bot set to keep everything on this computer, and for a
    bot with no build record at all (no wizard has run: nothing to change)."""
    try:
        from puffpai import build_manifest
        if build_manifest.read_manifest() is None:
            return False
    except Exception:  # noqa: BLE001
        return False
    return run_egress() not in ("sealed",)


def _bot() -> str:
    try:
        from puffpai import bot_identity
        return str(bot_identity.bot_name("") or "").strip()[:40] or "your bot"
    except Exception:  # noqa: BLE001
        return "your bot"


def row_for(job_ids: list[str]) -> dict:
    name = _bot()
    return {"id": ROW_ID, "kind": "setting", "key": ROW_KEY,
            "label": f"Let {name} go online",
            "detail": (f"{name[:1].upper() + name[1:]} is set to stay off the internet. "
                       "Your Yes lets it go online, and it reaches only the sites on this "
                       "list, each with its own switch."),
            "job_ids": list(job_ids), "done": False, "by_yes": True}


def go_online(by: str = "owner") -> bool:
    """Set the bot's run posture to ``enumerated`` (her Yes on a list that
    showed :func:`row_for`). False when it may not (sealed) or the record
    could not be written. One audit line."""
    from puffpai import build_manifest
    from puffpai.security import egress_doors
    if is_online():
        return True
    if not can_go_online():
        return False
    try:
        data: Any = build_manifest.read_manifest() or {}
        posture = dict(data.get("posture") or {}) if isinstance(data, dict) else {}
        posture["run_egress"] = egress_doors.OPEN_POSTURE
        data = dict(data) if isinstance(data, dict) else {}
        data["posture"] = posture
        build_manifest.write_manifest(data)
        egress_doors.reset_cache()
    except Exception as exc:  # noqa: BLE001 - it stays offline, the row stays open
        logger.warning("build: could not let the bot go online (%s)", type(exc).__name__)
        return False
    try:
        from puffpai.security.audit_log import AuditLog
        AuditLog().log(action="build_go_online", user=str(by or "owner")[:40], result="on",
                       details="run_egress=enumerated (the build's one Yes)")
    except Exception:  # noqa: BLE001
        pass
    return is_online()


__all__ = ["ROW_ID", "ROW_KEY", "can_go_online", "go_online", "is_online", "row_for",
           "run_egress"]
