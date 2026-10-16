"""Looks in the one add-more loop: summary lines and the "yes" that applies one.

:mod:`puffpai.add_more` and :mod:`puffpai.factory_wiring` use these (this
module registers nothing by itself):

* :func:`proposal_items` ``(goal, terms, *, ask=None)`` -- at most ONE line
  for section ``"looks"`` ("How it looks"). A word match in the free look
  lists is switched on by default; a look the brain picked is shown switched
  off; nothing found, no line (evidence or silence).
* :func:`wizard_items` ``(sctx)`` -- the same for the wizard's summary. The
  brain is asked only when the summary says it is already warm, so the
  summary never waits on a model it did not already have running.
* :func:`apply_item` ``(apply)`` -- carries out ``{"kind": "theme", "ref": id}``.
* :func:`ability_rows` -- the row for "what can my bot do / how do I add it".

A look downloads nothing and reaches nothing outside, so a line needs no
separate yes: switching it on is the yes, and undo is one step.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from puffpai.looks import find as F
from puffpai.looks import package as P
from puffpai.looks import store

SECTION = "looks"
APPLY_KIND = "theme"
ITEM_PREFIX = "looks:theme:"


def _item(found: dict) -> dict:
    pkg = found["look"]
    label = f"{pkg['name']}: {P.colours_line(pkg)}"
    detail = found["why"] + " You can change it or go back any time."
    return {
        "id": ITEM_PREFIX + pkg["id"],
        "section": SECTION,
        "label": label[:80],
        "detail": detail,
        "size_bytes": None,
        "outside_host": None,
        "default_on": bool(found.get("default_on")) and found.get("kind") == "list",
        "needs_yes": False,
        "apply": {"kind": APPLY_KIND, "ref": pkg["id"]},
    }


def proposal_items(goal: str, terms: Any = None, *,
                   ask: Optional[Callable[[str], str]] = None) -> list[dict]:
    """At most one "How it looks" line for *goal*, or [] when nothing fits."""
    found = F.word_matches(goal, limit=1)
    if not found and ask is not None:
        found = F.brain_picks(goal, ask, exclude=set(), limit=1)
    if not found:
        return []
    # Both kinds come from the shipped lists, so the yes applies them by id
    # with nothing remembered here: building the summary writes nothing.
    return [_item(found[0])]


def wizard_items(sctx: Any) -> list[dict]:
    """The wizard summary's provider: brain only if it is already warm."""
    shared = getattr(sctx, "shared", None) or {}
    ask = None
    if (shared.get("brain_ready") or {}).get("ready") and shared.get("ollama_url") \
            and shared.get("chosen_model"):
        ask = _ask_for(str(shared["ollama_url"]), str(shared["chosen_model"]))
    items = proposal_items(str(getattr(sctx, "goal", "") or ""),
                           list(getattr(sctx, "terms", None) or []), ask=ask)
    kind = _needs_kind(shared)
    for item in items:
        if item["default_on"] and kind is not None and not _about_the_bot(item, kind):
            # A look matched on a word that is not what the bot is about
            # ("sugar" in "blood sugar" would give a candy look to a health
            # log) is shown, switched off.
            item["default_on"] = False
    return items


def _needs_kind(shared: dict) -> Optional[set[str]]:
    """The words of what the bot is about, from the needs step (None: no needs step)."""
    try:
        from puffpai.catalogs import goal_words
        from puffpai.wizard import proposal
        needs = proposal.needs_of(shared)
    except Exception:  # noqa: BLE001 - no needs step: the word match decides
        return None
    if not isinstance(needs, dict) or not str(needs.get("kind") or "").strip():
        return None
    import re
    return {goal_words.singular(w) for w in re.findall(r"[a-z0-9]+", str(needs["kind"]).lower())}


def _about_the_bot(item: dict, kind: set[str]) -> bool:
    """Does a word the look matched name what the bot is about?"""
    import re
    from puffpai.catalogs import goal_words
    hits = re.findall(r"'([^']+)'", str(item.get("detail") or ""))
    return any(goal_words.singular(h.lower()) in kind for h in hits)


def _ask_for(url: str, model: str) -> Optional[Callable[[str], str]]:
    try:
        from puffpai.inference.ollama_backend import OllamaBackend
        backend = OllamaBackend(url=url, model=model, num_ctx=2048,
                                num_predict=120, timeout=60)
    except Exception:  # noqa: BLE001
        return None

    def ask(prompt: str) -> str:
        reply = backend.chat([{"role": "user", "content": prompt}],
                             temperature=0.0, max_tokens=120)
        text = reply if isinstance(reply, str) else ""
        return "" if text.startswith("[Error]") else text
    return ask


def apply_item(apply: Any) -> dict:
    """``{"ok", "message"}`` for one approved ``{"kind": "theme", "ref"}``."""
    if not isinstance(apply, dict) or apply.get("kind") != APPLY_KIND:
        return {"ok": False, "message": "That is not a look."}
    got = store.use(apply.get("ref"))
    return {"ok": bool(got["ok"]), "changed": bool(got.get("changed")),
            "message": got["message"]}


def ability_rows() -> list[dict]:
    """One row: the dashboard's look can be changed from the owner's words."""
    now = store.current()
    label = "Change how the dashboard looks from your words"
    note = (f"Using the look '{now['name']}'." if now else
            "Using the bot's own look.")
    return [{"id": "looks.change", "label": label, "status": "available",
             "how_to_add": 'puffpai looks find "<your words>"', "note": note}]


__all__ = ["APPLY_KIND", "ITEM_PREFIX", "SECTION", "ability_rows", "apply_item",
           "proposal_items", "wizard_items"]
