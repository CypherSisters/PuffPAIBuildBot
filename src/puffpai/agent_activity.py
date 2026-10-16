"""What the agent team really did, for the team page.

``GET /agents/status`` reads the newest line per agent from
``agent-activity.jsonl``. Only ``POST /agents/task`` used to write there, so a
team that had installed things, made pages, refreshed feeds and run routines
still showed every agent as idle. The places that do that work call
:func:`record` under the agent that owns it.

Lines hold a short plain description and a tool name, never tool arguments or
replies: the log is shown on a page and must not repeat what she typed.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Optional

#: The agent id of the chat tool loop, the one she talks to.
CHAT_AGENT = "chat"

_LOCK = threading.Lock()
_path_fn: Optional[Callable[[], str]] = None
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def use_path(fn: Optional[Callable[[], str]]) -> None:
    """The backend says where its activity log is (it reads the same file)."""
    global _path_fn
    _path_fn = fn


def path() -> str:
    if _path_fn is not None:
        try:
            got = _path_fn()
            if got:
                return str(got)
        except Exception:  # noqa: BLE001 - fall back to the usual place
            pass
    root = os.environ.get("PUFFPAI_DATA_DIR") or os.path.join(_REPO_ROOT, "data")
    return os.path.join(root, "agent-activity.jsonl")


def _short(text: Any, limit: int = 160) -> str:
    one = " ".join(str(text or "").split())
    return one if len(one) <= limit else one[: limit - 3].rstrip() + "..."


def record(agent: str, task: str, *, status: str = "completed",
           tool: str = "", event: str = "work", reason: str = "") -> None:
    """Add one line. Never raises: a log that cannot be written never stops
    the work it describes."""
    row = {"agent": str(agent or ""), "event": event, "task": _short(task),
           "status": status, "timestamp": datetime.now(timezone.utc).isoformat()}
    if tool:
        row["tool"] = _short(tool, 80)
    if reason:
        row["reason"] = _short(reason, 200)       # why it did not work, plain words
    try:
        where = path()
        os.makedirs(os.path.dirname(where) or ".", exist_ok=True)
        with _LOCK, open(where, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:  # noqa: BLE001
        pass


def recent(rows: list[dict], agent: str, limit: int = 5) -> list[dict]:
    """The newest *limit* lines of *agent* out of *rows* (oldest first in),
    newest first out, trimmed to what a card shows."""
    out = []
    for row in reversed(rows):
        if row.get("agent") == agent:
            out.append({k: row.get(k) for k in ("task", "status", "timestamp", "tool", "reason")
                        if row.get(k) is not None})
            if len(out) >= limit:
                break
    return out


__all__ = ["CHAT_AGENT", "path", "recent", "record", "use_path"]
