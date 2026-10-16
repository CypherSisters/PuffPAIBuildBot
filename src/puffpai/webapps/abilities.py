"""Ability rows for integrated browser apps (``build_abilities.register_provider``).

One row per installed app, id ``app:<id>`` (the same id family as the app
rows, so it replaces a plain one). The label names what the bot can do in it,
from the compiled actions' own words (at most four). ``available`` only while
at least one action has a current Drawbridge ``allow`` for these app files;
otherwise the row says the actions are being checked, or that the bot can only
open the app.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

from typing import Any

from puffpai.webapps import recipes, wording


def rows() -> list[dict[str, Any]]:
    from puffpai.webapps import host
    out: list[dict[str, Any]] = []
    for app_id in host.installed_ids():
        rec = host.read_record(app_id) or {}
        name = str(rec.get("name") or app_id)
        allowed = recipes.load(app_id)
        says = [str(r.get("says") or "").strip() for r in allowed][:4]
        says = [s[:1].lower() + s[1:] for s in says if s]
        if allowed:
            out.append({"id": f"app:{app_id}", "label": f"Use {name}: " + ", ".join(says),
                        "status": "available", "how_to_add": None})
            continue
        held = recipes.status(app_id)["held_back"]
        note = wording.BEING_CHECKED if held else wording.OPENS_INSIDE
        out.append({"id": f"app:{app_id}", "label": f"Use {name}", "status": "not_set_up",
                    "how_to_add": None, "note": note})
    return out


def says_for(app_id: str) -> list[str]:
    """What the bot can do in one app, in its own words ([] when nothing yet)."""
    return [str(r.get("says") or "") for r in recipes.load(app_id) if r.get("says")]


__all__ = ["rows", "says_for"]
