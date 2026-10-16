"""What was proven: per capability, working or not yet, in plain words.

``<state>/assemble/report.json``::

    {"schema": "assemble_report/v1", "made_at": ..., "capabilities": [
      {"cap", "name", "decision", "app_id", "state", "line", "reason",
       "fix"?, "checks": [{"ask", "action", "ok", "rows", "tool_ran", "reason"}]}]}

``state`` is one of:

* ``working`` -- proven: the action answered with real rows, and the ask went
  through the brain to the right tool, which answered too;
* ``not_yet`` -- not proven, with the plain reason (a switch that is off, a
  part that did not answer, still being built...). ``fix`` says what would
  fix it when that is known (``{"kind": "switch_on", "door": ...}``), for the
  chat's fix card;
* ``uses`` -- a whole thing already does it (a skill, a tool server, an app);
  Assemble did not put anything together for it.

The coverage list (:func:`puffpai.finding.capabilities.coverage`) reads this:
a capability is called working only when this report says ``working``.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from puffpai import fsutil
from puffpai.assemble import REPORT_SCHEMA, state_dir
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

WORKING = "working"
NOT_YET = "not_yet"
USES = "uses"
STATES = (WORKING, NOT_YET, USES)

STILL_BUILDING = "still being built"


def report_path() -> str:
    return state_dir("report.json")


def line_for(row: dict) -> str:
    """The one line a person reads for a capability."""
    state = row.get("state")
    if state == WORKING:
        return "Working: proven with a real question."
    if state == USES:
        return str(row.get("reason") or "Uses a whole thing that does it.")
    return f"Not yet: {row.get('reason') or STILL_BUILDING}."


def entry(cap: dict, state: str, reason: str = "", *, app_id: str = "",
          decision: str = "", checks: Optional[list] = None,
          fix: Optional[dict] = None) -> dict:
    row = {"cap": str(cap.get("cap") or cap.get("id") or ""), "name": str(cap.get("name") or ""),
           "decision": decision or str(cap.get("decision") or ""), "app_id": app_id,
           "state": state if state in STATES else NOT_YET,
           "reason": " ".join(str(reason or "").split()).rstrip(".")[:200],
           "checks": list(checks or []), "checked_at": int(time.time())}
    if fix:
        row["fix"] = fix
    row["line"] = line_for(row)
    return row


def write(rows: list[dict]) -> dict:
    data = {"schema": REPORT_SCHEMA, "made_at": int(time.time()), "capabilities": rows}
    try:
        fsutil.atomic_write_json(report_path(), data)
    except OSError as exc:
        logger.warning("assemble report: not kept (%s)", type(exc).__name__)
    return data


def update(row: dict) -> dict:
    """Replace one capability's row (a re-run of one), keeping the rest."""
    data = load()
    rows = [r for r in data["capabilities"] if r.get("cap") != row.get("cap")]
    rows.append(row)
    return write(rows)


def load() -> dict:
    got = fsutil.read_json(report_path(), default=None)
    if isinstance(got, dict) and got.get("schema") == REPORT_SCHEMA \
            and isinstance(got.get("capabilities"), list):
        got["capabilities"] = [r for r in got["capabilities"] if isinstance(r, dict)]
        return got
    return {"schema": REPORT_SCHEMA, "made_at": 0, "capabilities": []}


def by_cap() -> dict[str, dict]:
    return {str(r.get("cap")): r for r in load()["capabilities"] if r.get("cap")}


def is_working(cap_id: Any) -> bool:
    return (by_cap().get(str(cap_id or "")) or {}).get("state") == WORKING


__all__ = ["NOT_YET", "STATES", "STILL_BUILDING", "USES", "WORKING", "by_cap", "entry",
           "is_working", "line_for", "load", "report_path", "update", "write"]
