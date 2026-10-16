"""The one list of tools the bot's brain may call in chat.

Every tool the chat model can use is registered here, whoever makes it:
the built-in ones (:mod:`puffpai.brain_tools_builtin`), the actions of added
apps (:mod:`puffpai.integrate.actions`, as a provider), and later
skills and MCP servers. The tool loop (:mod:`puffpai.brain_loop`) asks
:func:`available` for the tools of one turn and runs each call through
:func:`call`.

The shape is copied from Hermes Agent's ``tools/registry.py`` (MIT, see
THIRD_PARTY_NOTICES.md): a name, a plain description, a JSON schema for the
arguments and one function. Two things are ours:

* ``needs_yes``: the tool asks the owner before it runs. ``True`` asks
  "May I use <tool>?" with the arguments shown; a function
  ``needs_yes(args, ctx) -> dict | None`` returns the exact question (for
  example the exact command ``run_command`` will run), or None when this call
  needs no yes. The question goes to ``ctx["ask"]`` (the dashboard chat or the
  terminal). With no ``ask`` in the context the tool does not run.
* providers: a function ``provider(ctx) -> list[Tool]`` for tools that come
  and go (the apps she added, an MCP server's tools).

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Union

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

#: Function names of letters, digits, _, . and - (app action ids are
#: ``<integration id>__<name>`` and can be long).
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
#: What one tool result may hand back to the model, in characters.
MAX_RESULT_CHARS = 6000

#: Plain lines the owner sees when a tool is not run.
NO_ASK_LINE = ("This needs your yes first, and I can only ask for it in the "
               "dashboard chat or the terminal. Nothing was run.")
SAID_NO_LINE = "You said no, so I did not run it."
NO_ANSWER_LINE = "No answer came, so I did not run it."
UNKNOWN_TOOL_LINE = "There is no tool called {name} right now."
FAILED_LINE = "The tool {name} did not work: {why}"

NeedsYes = Union[bool, Callable[[dict, dict], Optional[dict]]]


@dataclass(frozen=True)
class Tool:
    """One tool the model may call."""
    name: str
    description: str
    schema: dict
    fn: Callable[[dict, dict], Any]
    needs_yes: NeedsYes = False
    #: The plain line shown in chat while it runs ("Looking on GitHub ...").
    #: A string, or a function of the arguments; a function that gives ""
    #: shows no line (a step that will not happen, like a search that is off).
    progress: Union[str, Callable[[dict], str]] = ""
    #: ``when(ctx) -> bool``: is this tool offered for this turn?
    when: Optional[Callable[[dict], bool]] = None
    source: str = "builtin"
    extra: dict = field(default_factory=dict)

    def spec(self) -> dict:
        """The tool as Ollama (and OpenAI) take it."""
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": self.schema}}

    def progress_line(self, args: dict) -> str:
        fallback = f"Using {self.name.replace('_', ' ')} ..."
        if not callable(self.progress):
            return str(self.progress or fallback)
        try:
            return str(self.progress(args) or "")
        except Exception:  # noqa: BLE001 - a progress line never stops a tool
            return fallback


_lock = threading.Lock()
_TOOLS: dict[str, Tool] = {}
_PROVIDERS: dict[str, Callable[[dict], list]] = {}


def _check_schema(schema: Any) -> dict:
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ValueError("a tool's schema is a JSON object schema")
    props = schema.get("properties", {})
    if not isinstance(props, dict):
        raise ValueError("a tool's schema properties are a mapping")
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(r in props for r in required):
        raise ValueError("a tool's required arguments must be among its properties")
    return schema


def make(name: str, description: str, json_schema: dict,
         fn: Callable[[dict, dict], Any], needs_yes: NeedsYes = False, *,
         progress: Union[str, Callable[[dict], str]] = "",
         when: Optional[Callable[[dict], bool]] = None,
         source: str = "builtin") -> Tool:
    """A checked :class:`Tool` (not registered). Providers build theirs here."""
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ValueError(f"{name!r} is not a usable tool name")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("a tool needs a plain description")
    if not callable(fn):
        raise ValueError("a tool needs a function")
    return Tool(name=name, description=description.strip(),
                schema=_check_schema(json_schema), fn=fn, needs_yes=needs_yes,
                progress=progress, when=when, source=source)


def register(name: str, description: str, json_schema: dict,
             fn: Callable[[dict, dict], Any], needs_yes: NeedsYes = False, *,
             progress: Union[str, Callable[[dict], str]] = "",
             when: Optional[Callable[[dict], bool]] = None,
             source: str = "builtin") -> Tool:
    """Add a tool. ``fn(args, ctx)`` returns anything JSON can carry.

    Registering a name again replaces the old tool (a reload, a skill that
    was updated)."""
    tool = make(name, description, json_schema, fn, needs_yes, progress=progress,
                when=when, source=source)
    with _lock:
        _TOOLS[name] = tool
    return tool


def unregister(name: str) -> None:
    with _lock:
        _TOOLS.pop(name, None)


def register_provider(key: str, provider: Callable[[dict], list]) -> None:
    """Add a source of tools that come and go: ``provider(ctx) -> [Tool]``."""
    if not callable(provider):
        raise ValueError("a tool provider is a function")
    with _lock:
        _PROVIDERS[str(key)] = provider


def unregister_provider(key: str) -> None:
    with _lock:
        _PROVIDERS.pop(str(key), None)


def clear() -> None:
    """Forget every tool and provider (tests)."""
    with _lock:
        _TOOLS.clear()
        _PROVIDERS.clear()


def available(ctx: Optional[dict] = None) -> list[Tool]:
    """The tools offered for one turn, registered ones first, by name."""
    ctx = ctx if isinstance(ctx, dict) else {}
    with _lock:
        fixed = list(_TOOLS.values())
        providers = list(_PROVIDERS.items())
    out: list[Tool] = []
    seen: set[str] = set()

    def _offer(tool: Any) -> None:
        if not isinstance(tool, Tool) or tool.name in seen:
            return
        try:
            if tool.when is not None and not tool.when(ctx):
                return
        except Exception:  # noqa: BLE001 - a broken check offers nothing
            return
        seen.add(tool.name)
        out.append(tool)

    for tool in sorted(fixed, key=lambda t: t.name):
        _offer(tool)
    for key, provider in providers:
        try:
            listed = provider(ctx) or []
        except Exception as exc:  # noqa: BLE001 - one source never sinks the list
            logger.warning("brain tools: provider %s failed: %s", key, type(exc).__name__)
            continue
        for tool in listed:
            _offer(tool)
    return out


def specs(tools: list[Tool]) -> list[dict]:
    return [t.spec() for t in tools]


def find(name: str, ctx: Optional[dict] = None) -> Optional[Tool]:
    return next((t for t in available(ctx) if t.name == name), None)


def _type_ok(value: Any, kind: Any) -> bool:
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "array":
        return isinstance(value, list)
    if kind == "object":
        return isinstance(value, dict)
    return True


def check_args(tool: Tool, args: Any) -> dict:
    """The arguments, checked against the tool's schema just enough for a
    model to correct itself. Raises ValueError with a plain reason."""
    if args is None:
        args = {}
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except ValueError:
            raise ValueError("the arguments were not JSON") from None
    if not isinstance(args, dict):
        raise ValueError("the arguments must be an object")
    props = tool.schema.get("properties") or {}
    if tool.schema.get("additionalProperties") is False:
        unknown = [k for k in args if k not in props]
        if unknown:
            raise ValueError(f"unknown argument {unknown[0]!r}")
    missing = [r for r in tool.schema.get("required") or [] if r not in args]
    if missing:
        raise ValueError(f"missing argument {missing[0]!r}")
    for key, value in args.items():
        spec = props.get(key)
        if isinstance(spec, dict) and not _type_ok(value, spec.get("type")):
            raise ValueError(f"{key!r} should be a {spec.get('type')}")
    return args


def question_for(tool: Tool, args: dict, ctx: dict) -> Optional[dict]:
    """The yes question for this call, or None when it needs none."""
    if not tool.needs_yes:
        return None
    if callable(tool.needs_yes):
        got = tool.needs_yes(args, ctx)
        if not got:
            return None
        q = dict(got)
    else:
        shown = json.dumps(args, ensure_ascii=False) if args else ""
        q = {"title": f"May I use {tool.name.replace('_', ' ')}?", "detail": shown}
    q.setdefault("tool", tool.name)
    q.setdefault("title", f"May I use {tool.name.replace('_', ' ')}?")
    return q


def result_text(result: Any) -> str:
    """A tool result as the text the model reads, cut to a size it can hold."""
    if isinstance(result, str):
        text = result
    else:
        try:
            text = json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(result)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + " ...(cut short)"
    return text


def call(name: str, args: Any, ctx: Optional[dict] = None) -> dict:
    """Run one tool call. Returns ``{"ok", "result" | "error", "asked"}``.

    Never raises: whatever goes wrong becomes a plain error the model reads
    and can act on (try other words, ask the owner, give up)."""
    ctx = ctx if isinstance(ctx, dict) else {}
    tool = find(str(name or ""), ctx)
    if tool is None:
        return {"ok": False, "error": UNKNOWN_TOOL_LINE.format(name=name), "asked": False}
    try:
        clean = check_args(tool, args)
    except ValueError as exc:
        # One plain line: what was wrong and what it takes (the model fixes it).
        props = list(((tool.schema or {}).get("properties") or {}).keys())
        takes = f" It takes: {', '.join(props)}." if props else " It takes no arguments."
        return {"ok": False, "error": f"Wrong arguments for {tool.name}: {exc}.{takes}",
                "asked": False}
    asked = False
    try:
        question = question_for(tool, clean, ctx)
    except Exception as exc:  # noqa: BLE001 - a call we cannot describe does not run
        return {"ok": False, "error": FAILED_LINE.format(name=tool.name, why=str(exc)),
                "asked": False}
    if question is not None:
        ask = ctx.get("ask")
        if not callable(ask):
            return {"ok": False, "error": NO_ASK_LINE, "asked": False}
        asked = True
        try:
            answer = ask(question)
        except Exception:  # noqa: BLE001 - a broken ask runs nothing
            answer = None
        if answer is None:
            # No answer in time (or the page went away): nothing runs, and the
            # reply must not say she said no.
            return {"ok": False, "error": NO_ANSWER_LINE, "asked": True}
        if not answer:
            return {"ok": False, "error": SAID_NO_LINE, "asked": True}
        ctx = dict(ctx, said_yes=True)
    try:
        result = tool.fn(clean, ctx)
    except Exception as exc:  # noqa: BLE001 - the model hears it, chat goes on
        logger.info("brain tools: %s failed: %s", tool.name, type(exc).__name__)
        return {"ok": False, "error": FAILED_LINE.format(name=tool.name, why=str(exc) or
                                                         type(exc).__name__),
                "asked": asked}
    return {"ok": True, "result": result, "asked": asked}


__all__ = ["Tool", "available", "call", "check_args", "clear", "find", "make",
           "question_for", "register", "register_provider", "result_text", "specs",
           "unregister", "unregister_provider"]
