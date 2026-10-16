"""The find tools for the chat loop: ``find_things``, ``install_thing``
and ``skill_view``, registered in ``puffpai.brain_tools``.

The tools go into the one tool registry, ``puffpai.brain_tools``
(``register(name, description, json_schema, fn, needs_yes=False)`` and
``available(ctx)``), which the chat's tool loop reads every turn.

``install_thing`` asks her before it runs: its ``needs_yes`` is
:func:`install_question`, which hands the loop the exact question (what it
is, where it comes from, what it runs, the shelf's own check), the way
``brain_tools`` lets a tool word its own yes.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from typing import Any, Callable, Optional


def registry():
    """The tool loop's registry, ``puffpai.brain_tools`` (a seam for tests)."""
    from puffpai import brain_tools
    return brain_tools


def _args(a: tuple, kw: dict) -> dict:
    """The tool's arguments however the loop passes them: ``fn(**args)``,
    ``fn(args)`` or ``fn(args, ctx)``."""
    out: dict[str, Any] = {}
    if a and isinstance(a[0], dict):
        out.update(a[0])
    out.update({k: v for k, v in kw.items() if k != "ctx"})
    return out


FIND_DESCRIPTION = (
    "Search for things other people already built that could help: skills (instructions "
    "for a job) on ClawHub, skills.sh and the Hermes skill list; MCP tool servers on the "
    "MCP Registry; apps and code on GitHub, Codeberg, npm, PyPI and the awesome-selfhosted "
    "list; and free books, guides, Q&A collections and textbooks from the free libraries "
    "(Project Gutenberg, Wikibooks, the Internet Archive, OpenStax, the Kiwix library), "
    "which the bot reads to answer questions. Use it for \"find me books about ...\" too, "
    "with kinds [\"book\"]. Search before saying you can't do something. Use 2 to 4 plain "
    "words about the job, never personal details. Returns a list grouped by type, each with an id in square "
    "brackets, what it is, its numbers, licence, the shelf's safety check and what BuildBot "
    "can do with it. Searching installs nothing.")
FIND_SCHEMA = {
    "type": "object",
    "properties": {
        "words": {"type": "string", "description": "2 to 4 words about the job, e.g. "
                                                   "'wedding guest list'"},
        "kinds": {"type": "array", "items": {"type": "string", "enum": [
            "skill", "MCP tool server", "browser app", "app that needs its own server",
            "command-line tool", "code library", "data/template", "book"]},
                  "description": "Only these types (leave out to search everything)."},
    },
    "required": ["words"],
}
INSTALL_DESCRIPTION = (
    "Install one thing from find_things, by its id. Only when the person asked for that "
    "thing. Before calling, tell them what it is, where it comes from, what it runs and the "
    "shelf's safety check. A skill is ready on the next message; a tool server joins your "
    "tools. Never install anything that can trade money unless they named it.")
INSTALL_SCHEMA = {
    "type": "object",
    "properties": {"id": {"type": "string",
                          "description": "The id shown in square brackets by find_things."}},
    "required": ["id"],
}
VIEW_DESCRIPTION = (
    "Read an installed skill's whole SKILL.md (or one file it names, with path). Use it as "
    "soon as a skill in the skills list fits the request, then follow it.")
VIEW_SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string", "description": "The skill's name."},
                   "path": {"type": "string",
                            "description": "A file inside the skill, e.g. references/api.md"}},
    "required": ["name"],
}


def tool_find_things(*a: Any, **kw: Any) -> str:
    from puffpai.finding import find
    args = _args(a, kw)
    result = find.find_things(args.get("words"), args.get("kinds"), subject=_bot_subject(),
                              allow_trading=_she_asked_trading(_ctx(a, kw)))
    return find.as_text(result)


def _she_asked_trading(ctx: Optional[dict]) -> bool:
    """Did HER message ask for trading? The search words are the model's,
    so they never decide it (her rule: nothing that trades money by default)."""
    from puffpai.finding import wording as W
    return bool(ctx) and bool(W.ASKS_TRADING_RE.search(str(ctx.get("said") or "")))


def _bot_subject() -> str:
    """What this bot is for (her goal at build time), or ""."""
    try:
        from puffpai import offers
        return offers.bot_goal()
    except Exception:  # noqa: BLE001
        return ""


def _ctx(a: tuple, kw: dict) -> Optional[dict]:
    ctx = a[1] if len(a) > 1 else kw.get("ctx")
    return ctx if isinstance(ctx, dict) else None


#: What the model reads with an install that did not happen (Hermes: the
#: tool result is the truth). Its own words go to her as they are.
NOT_INSTALLED_NOTE = ("Nothing was installed. Tell the owner what 'said' says, in its own "
                      "words. Do not offer to install it, do not add safety notes of your "
                      "own, and do not say it was done.")


def _not_installed(said: str) -> dict:
    return {"installed": False, "ok": False, "said": said.strip(), "note": NOT_INSTALLED_NOTE}


def tool_install_thing(*a: Any, **kw: Any) -> Any:
    """Words when it is installed (or already was); a ``{"ok": False, "said"}``
    result when nothing was installed, so the loop knows it failed."""
    from puffpai.finding import install
    from puffpai.finding import wording as W
    args = _args(a, kw)
    ctx = _ctx(a, kw)
    same = install.already(args.get("id"))
    if same is not None:
        f_name = str(same.get("name") or "It")
        return W.ALREADY.format(name=f_name)
    if ctx is not None and _trades_unasked(args.get("id"), ctx):
        f = _recall(args.get("id"))
        return _not_installed(W.TRADE_NOT_ASKED.format(name=(f or {}).get("name") or "It"))
    if ctx is not None and not ctx.get("said_yes"):
        # No question was asked (the shelf marks it harmful, or BuildBot can
        # only point at it): say why and install nothing. From chat, an
        # install happens only after her yes to the exact preview.
        p = install.preview(args.get("id"))
        if not p.get("ok"):
            return _not_installed(p.get("line") or W.NOT_FOUND)
        if p.get("refuse"):
            return _not_installed(W.REFUSED_MALICIOUS.format(
                name=p["name"], shelf=p.get("from") or "the shelf"))
        return _not_installed("\n".join(list(p.get("lines") or [])
                                        + [p.get("can_words") or ""]))
    out = install.install_thing(args.get("id"), via="chat")
    said = "\n".join(out.get("lines") or [out.get("line") or ""])
    return said if out.get("ok") else _not_installed(said)


def tool_skill_view(*a: Any, **kw: Any) -> str:
    from puffpai import skills
    args = _args(a, kw)
    got = skills.view(args.get("name"), args.get("path"))
    if not got.get("ok"):
        return got.get("message") or "That skill could not be read."
    files = ", ".join(got.get("files") or [])
    return (f"Skill {got['name']}, file {got['path']} (folder: {got['folder']}; "
            f"files: {files}). To run one of its commands, call run_command with "
            f"skill=\"{got['name']}\"; ~ is the bot's work folder.\n\n{got['text']}")


def yes_question(args: Any) -> str:
    """The words to ask her before ``install_thing`` runs."""
    from puffpai.finding import install
    find_id = (args or {}).get("id") if isinstance(args, dict) else args
    p = install.preview(find_id)
    if not p.get("ok"):
        return p.get("line") or ""
    return "\n".join(p["lines"] + [p["question"] or p["can_words"]])


def _recall(find_id: Any) -> Optional[dict]:
    from puffpai.finding import find
    return find.recall(find_id)


def _trades_unasked(find_id: Any, ctx: Any) -> bool:
    """A find that can trade money, in a chat where she did not ask for trading."""
    f = _recall(find_id)
    return bool(f and f.get("can_trade")) and not _she_asked_trading(
        ctx if isinstance(ctx, dict) else None)


def install_question(args: Any, ctx: Any = None) -> Optional[dict]:
    """``brain_tools``'s ``needs_yes`` for ``install_thing``: the exact
    question, or None when there is nothing to ask (an unknown id, or one the
    shelf marks malicious: the call then only says why, and changes nothing)."""
    from puffpai.finding import install
    find_id = (args or {}).get("id") if isinstance(args, dict) else args
    if install.already(find_id) is not None:
        return None          # already there: nothing to ask, nothing is added
    if isinstance(ctx, dict) and _trades_unasked(find_id, ctx):
        return None          # never offered unless she asked for trading
    p = install.preview(find_id)
    if not p.get("ok") or p.get("refuse") or p.get("can") == "look":
        return None
    verb = "Add" if p.get("type") == "book" else "Install"
    return {"title": f"{verb} {p['name']}?", "detail": " ".join(p["lines"]),
            "lines": list(p["lines"])}


def skill_view_description() -> str:
    """``skill_view``'s description with the installed skills in it, so the
    model sees their names and one-line descriptions on every turn (Hermes's
    ``<available_skills>``)."""
    from puffpai import skills
    return VIEW_DESCRIPTION + "\n\n" + skills.prompt_block()


def _skills_provider(reg: Any) -> Callable[[Any], list]:
    def provide(ctx: Any = None) -> list:
        return [reg.make("skill_view", skill_view_description(), VIEW_SCHEMA,
                         tool_skill_view, source="skills")]
    return provide


def register_all() -> None:
    """Register the tools (registering again replaces them, so this is safe
    to call more than once). ``skill_view`` comes from a provider so its list
    of installed skills is read fresh every turn."""
    reg = registry()
    reg.register("find_things", FIND_DESCRIPTION, FIND_SCHEMA, tool_find_things)
    reg.register("install_thing", INSTALL_DESCRIPTION, INSTALL_SCHEMA, tool_install_thing,
                 needs_yes=install_question)
    reg.register_provider("skills", _skills_provider(reg))


__all__ = ["FIND_SCHEMA", "INSTALL_SCHEMA", "VIEW_SCHEMA", "install_question",
           "register_all", "registry", "skill_view_description",
           "tool_find_things", "tool_install_thing", "tool_skill_view", "yes_question"]
