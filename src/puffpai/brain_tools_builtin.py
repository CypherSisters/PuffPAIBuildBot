"""The tools every BuildBot chat starts with.

* the actions of the apps she added (:mod:`puffpai.integrate.actions`),
  one tool per action, for her paired device or the terminal. Reads and
  changes run at once (changes keep their undo); a remove asks her first.
* ``undo_last_change``: put back the last change an app action made.
* ``search_web``: the web search she switched on (Wikipedia, or Brave or
  Marginalia with her own key), through its switch on "What leaves your
  computer".
* ``get_weather``: the weather service, through its switch.
* ``run_command`` (:mod:`puffpai.brain_commands`): asks before each command.
* ``make_document`` / ``edit_document`` (:mod:`puffpai.office_docs`): Word,
  Excel and PowerPoint files in her own files, on this computer only.

These used to be shortcut answers in front of the model (word matching);
now the model picks them. The shortcut code is parked, not deleted.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import threading
from typing import Any

from puffpai import brain_tools
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

#: At most this many app actions are offered in one turn.
MAX_APP_TOOLS = 40

_lock = threading.Lock()
_done = {"registered": False}


def _owner(ctx: dict) -> bool:
    return ctx.get("caller") in ("owner", "terminal")


# -- app actions --------------------------------------------------------------

def _values(args: dict) -> str:
    parts = [f"{k.replace('_', ' ')}: {v}" for k, v in (args or {}).items()
             if isinstance(v, (str, int, float, bool)) and str(v).strip()]
    return ", ".join(parts[:6])


def _action_tool(action: dict) -> brain_tools.Tool:
    from puffpai.integrate import actions
    app = str(action.get("app_name") or "the app")
    says = str(action.get("says") or "").rstrip(".")
    effect = action.get("effect")

    def _run(args: dict, ctx: dict) -> dict:
        try:
            clean = actions.check_args(action, args)
        except actions.ActionError as exc:
            raise ValueError(str(exc)) from None
        run_ctx = {"device_id": ctx.get("device_id"), "surface": ctx.get("surface") or "chat"}
        if effect == "removes":
            from puffpai.security import consent_tickets
            target = consent_tickets.target_action_yes(action["id"], clean)
            try:
                run_ctx["ticket"] = consent_tickets.mint(
                    "action_yes", target, f"Remove from {app}: {says}.",
                    str(ctx.get("device_id") or ""))
            except consent_tickets.TicketRefused as exc:
                raise ValueError(exc.message) from None
        got = actions.run(action["id"], clean, run_ctx)
        out: dict[str, Any] = {"ok": bool(got.get("ok")), "app": app,
                               "said": str(got.get("line") or "")}
        if effect == "reads" and got.get("ok"):
            out["data"] = got.get("data")
            if got.get("examples"):
                out["example_rows_that_came_with_the_app"] = got["examples"]
        if got.get("undo_token") is not None:
            out["can_undo"] = True
        return out

    def _question(args: dict, ctx: dict) -> dict:
        shown = _values(args)
        return {"tool": action["id"], "title": f"Remove this from {app}?",
                "detail": f"{says}" + (f" ({shown})" if shown else "")}

    kind = {"reads": "Reads", "changes": "Changes", "removes": "Removes"}.get(str(effect), "")
    return brain_tools.make(
        action["id"], f"{app}: {says}." + (f" ({kind} her data in {app}.)" if kind else ""),
        action["args"], _run,
        needs_yes=_question if effect == "removes" else False,
        progress=f"Using {app} ...", source="app_action")


def app_action_tools(ctx: dict) -> list[brain_tools.Tool]:
    """The checked actions of the apps she added, for a caller who may use them."""
    from puffpai.integrate import actions
    device = ctx.get("device_id")
    surface = str(ctx.get("surface") or "chat")
    if not device or not actions.caller_ok(device, surface):
        return []
    out: list[brain_tools.Tool] = []
    for action in actions.all_actions():
        if not actions.allowed(action) or not actions.runnable(action):
            continue
        try:
            out.append(_action_tool(action))
        except ValueError as exc:
            logger.info("brain tools: app action left out: %s", exc)
            continue
        if len(out) >= MAX_APP_TOOLS:
            break
    return out


def _undo(args: dict, ctx: dict) -> dict:
    from puffpai.integrate import actions
    got = actions.undo_last(str(ctx.get("device_id") or ""))
    return {"ok": bool(got.get("ok")), "said": str(got.get("line") or "")}


def _has_undo(ctx: dict) -> bool:
    from puffpai.integrate import actions
    device = ctx.get("device_id")
    return bool(device) and actions.has_undo(str(device))


# -- web search and weather -----------------------------------------------------

def _search_line(args: dict) -> str:
    """The step line, only when a search can really go out (none when web
    search is off: a step that does not happen is not shown)."""
    from puffpai import web_search
    return f"Searching the web for \"{args.get('query', '')}\" ..." if web_search.ready() else ""


def _search_web(args: dict, ctx: dict) -> dict:
    from puffpai import web_search
    vault = ctx.get("vault")
    if callable(vault):
        vault = vault()
    got = web_search.search(str(args.get("query") or ""), vault=vault)
    out: dict[str, Any] = {"ok": bool(got.get("ok")), "searched_with": got.get("label") or ""}
    out["found"] = [{"title": r.get("title"), "site": r.get("site"), "link": r.get("url"),
                     "summary": r.get("snippet")} for r in (got.get("results") or [])][:8]
    if got.get("notes"):
        out["notes"] = list(got["notes"])
    if got.get("left_out"):
        out["words_left_out"] = got["left_out"]
    return out


def _weather(args: dict, ctx: dict) -> str:
    from puffpai import weather_service
    when = " ".join(str(args.get("when") or "today").split())[:40] or "today"
    got = weather_service.chat_answer(f"what is the forecast {when}", surface="chat")
    return got or "The weather service did not have an answer for that day."


def register_all() -> None:
    """Register the built-in tools once per process."""
    with _lock:
        if _done["registered"]:
            return
        _done["registered"] = True
    brain_tools.register_provider("app_actions", app_action_tools)
    brain_tools.register(
        "undo_last_change",
        "Put back the last change you made in one of the owner's apps (within 10 minutes).",
        {"type": "object", "properties": {}, "additionalProperties": False},
        _undo, progress="Putting it back ...", when=_has_undo)
    brain_tools.register(
        "search_web",
        "Search the web for facts or news. Uses the search the owner switched on; "
        "the search words leave this computer, nothing else does.",
        {"type": "object", "properties": {
            "query": {"type": "string", "description": "A few plain search words."}},
         "required": ["query"], "additionalProperties": False},
        _search_web, progress=_search_line,
        when=_owner)
    brain_tools.register(
        "get_weather",
        "The weather forecast at the owner's home.",
        {"type": "object", "properties": {
            "when": {"type": "string",
                     "description": "today, tonight, tomorrow, this weekend, or a weekday"}},
         "additionalProperties": False},
        _weather, progress="Checking the weather ...", when=_owner)
    from puffpai import brain_commands, office_docs, programs, routines
    office_docs.register_tools(when=_owner)
    brain_commands.register()
    routines.register_tool()
    programs.register_tool()


def reset_for_tests() -> None:
    with _lock:
        _done["registered"] = False


__all__ = ["MAX_APP_TOOLS", "app_action_tools", "register_all", "reset_for_tests"]
