"""What the bot can do with the things Toolsmith added, as ability rows.

One row per integration that is ready: ``{"id": "app:<id>", "label": "Use
<name>: <what up to 4 of its actions say>", "status": "available"}``. A row is
``available`` only when at least one of its actions passed its check (else it
says the app opens but the bot cannot drive it). Plus one row per outside
service an app wants, so "what can you do?" never hides a connection.

Words come from the checked actions only (``says``), never from the app.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

from typing import Any

from puffpai.integrate import actions, records, wording


def ability_rows() -> list[dict[str, Any]]:
    """Rows for :func:`puffpai.build_abilities.register_provider`. Never raises."""
    try:
        recs = [r for r in records.all() if r.get("status") == "ready"]
        acts = [a for a in actions.all_actions() if actions.allowed(a)]
    except Exception:  # noqa: BLE001 - the list of abilities never breaks
        return []
    out: list[dict[str, Any]] = []
    for rec in recs:
        ids = {rec["id"], rec.get("app_id") or "", rec.get("list_app_id") or ""} - {""}
        mine = [a for a in acts if a["integration"] in ids]
        says = []
        for a in mine:
            s = str(a["says"]).rstrip(".")
            if s and s.lower() not in (x.lower() for x in says):
                says.append(s[0].lower() + s[1:] if s[:2] != s[:2].upper() else s)
        if says:
            label = wording.ABILITY_USE.format(name=rec["name"], says=", ".join(says[:4]))
            status = "available"
        else:
            label = wording.ABILITY_OPEN.format(name=rec["name"])
            status = "available" if rec.get("mode") in ("static_web", "web_build",
                                                         "picture_set", "folder_palette") \
                else "unavailable"
        out.append({"id": f"app:{rec['id']}", "label": label[:200], "status": status,
                    "how_to_add": None})
        for row in rec.get("outside") or []:
            host = str(row.get("host") or "")
            if not host:
                continue
            on = str(row.get("state") or "") == "on"
            out.append({"id": f"app:{rec['id']}:outside:{host}"[:120],
                        "label": wording.ABILITY_OUTSIDE.format(name=rec["name"], host=host,
                                                                state="on" if on else "off"),
                        "status": "available" if on else "unavailable", "how_to_add": None})
    return out


__all__ = ["ability_rows"]
