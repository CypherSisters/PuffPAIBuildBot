"""The fix commands a gap record is allowed to name, and how they are rendered.

**A gap's ``fix`` is never a string.** It carries a registered command
id and typed arguments, and the runner renders the line the owner sees. The
reason is narrow and worth stating: a gap record is the one place where a
package gets to put text in front of an owner that looks like something to
paste into a terminal. If the package supplied the string, the package would be
choosing the command.

So: unknown id is refused, an argument that is not declared is refused, and an
argument value that is not a plain token is refused rather than quoted --
nothing here builds a shell string out of package output, and nothing here runs
anything. The rendered line is text for a human to read and type.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import re
import time
from typing import Any

#: What an argument value may look like. Deliberately narrower than "a safe
#: shell token": source ids, pack ids and persona ids are all words. Anything
#: with a space, a quote, a semicolon or a backslash is refused, not escaped.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")

#: A path argument is the one place a space is plausible. It is still refused if
#: it carries a shell metacharacter, and it is rendered inside the sentence, not
#: appended to a command line.
_PATH_RE = re.compile(r"^[A-Za-z0-9 ._:\\/-]{1,200}$")

#: A knowledge source id: a registry id or a Kiwix book id such as
#: ``kiwix:vikidia_en_all:nopic``. Same characters as a token, longer.
_SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,190}$")

#: An Ollama model name with its tag, optionally under a namespace
#: (``library/llama3.2:3b``). Still no spaces, quotes or shell characters.
_MODEL_RE = re.compile(r"^(?:[A-Za-z0-9][A-Za-z0-9._-]{0,60}/){0,2}"
                       r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}:[A-Za-z0-9._-]{1,40}$")

#: A web address for an Ollama that is not on the default port: scheme, host
#: and port, nothing else.
_URL_RE = re.compile(r"^https?://[A-Za-z0-9._-]{1,120}(?::\d{1,5})?/?$")

_TYPES = {"token": _TOKEN_RE, "path": _PATH_RE, "source_id": _SOURCE_ID_RE,
          "model": _MODEL_RE, "url": _URL_RE}


class FixError(ValueError):
    """A fix this build will not put in front of an owner, and why."""


def _render_fetch_source(args: dict[str, str]) -> str:
    # Shows the size and license and asks for a yes; it does not re-run the
    # whole wizard the way `setup --yes --fetch-source` did.
    return f"puffpai sources add {args['source_id']}"


def _render_knowledge_sync(args: dict[str, str]) -> str:
    return "puffpai knowledge sync"


def _render_persona_rename(args: dict[str, str]) -> str:
    return f"puffpai persona edit {args['persona_id']} --rename-id {args['new_id']}"


def _render_model_pull(args: dict[str, str]) -> str:
    return f"ollama pull {args['model']}"


def _render_model_pull_at(args: dict[str, str]) -> str:
    # Named, because a plain ``ollama pull`` would download into whatever
    # Ollama that command reaches by default -- not the one this build uses.
    return (f"ollama pull {args['model']}    (on the Ollama at "
            f"{args['url']})")


def _render_apps_drafts(args: dict[str, str]) -> str:
    return "puffpai apps drafts"


def _render_setup_rerun(args: dict[str, str]) -> str:
    return "puffpai setup"


def _render_outside_on(args: dict[str, str]) -> str:
    return f"puffpai outside on {args['service']}"


def _render_setup_scaffold(args: dict[str, str]) -> str:
    # --own-place is --scaffold under the name an owner is shown (plain words).
    return "puffpai setup --own-place"


#: id -> (required arg name -> type, renderer, what it does in plain words).
#: Only commands this build actually ships are registered; a closing screen
#: printing commands that do not apply to the owner's install is the failure this registry exists to make unrepeatable.
COMMANDS: dict[str, dict[str, Any]] = {
    "source.fetch": {
        "args": {"source_id": "source_id"},
        "render": _render_fetch_source,
        "what": "Download one book after you see its size and say yes.",
    },
    "knowledge.sync": {
        "args": {},
        "render": _render_knowledge_sync,
        "what": "Index the packs that are on disk but not searchable yet.",
    },
    "persona.rename_id": {
        "args": {"persona_id": "token", "new_id": "token"},
        "render": _render_persona_rename,
        "what": "Give the bot an id that is not already used for something.",
    },
    "model.pull": {
        "args": {"model": "model"},
        "render": _render_model_pull,
        "what": "Download the brain this build is set to use.",
    },
    "model.pull_at": {
        "args": {"model": "model", "url": "url"},
        "render": _render_model_pull_at,
        "what": "Download the brain into the Ollama this build uses.",
    },
    "apps.drafts": {
        "args": {},
        "render": _render_apps_drafts,
        "what": "See the live cards BuildBot made that are waiting for you.",
    },
    "setup.rerun": {
        "args": {},
        "render": _render_setup_rerun,
        "what": "Run the wizard again and answer the step that did not finish.",
    },
    "outside.on": {
        "args": {"service": "token"},
        "render": _render_outside_on,
        "what": "Switch on one outside service after you read what it sends.",
    },
    "setup.scaffold": {
        "args": {},
        "render": _render_setup_scaffold,
        "what": "Run the wizard again and give the bot a place of its own "
                "(a folder, a page and its lists).",
    },
}


def command_ids() -> tuple[str, ...]:
    return tuple(sorted(COMMANDS))


def validate_fix(fix: Any) -> dict[str, Any]:
    """Check a ``fix`` block. Returns it canonical, or raises :class:`FixError`."""
    if not isinstance(fix, dict):
        raise FixError("A fix is an object, never a line of text.")
    kind = fix.get("kind")
    if kind == "none":
        # Said plainly rather than pointing at a command that changes nothing.
        return {"kind": "none"}
    if kind == "card":
        skill_id = fix.get("id")
        if not isinstance(skill_id, str) or not _TOKEN_RE.match(skill_id):
            raise FixError(f"A card fix names a skill id, not {skill_id!r}.")
        return {"kind": "card", "id": skill_id}
    if kind != "command":
        raise FixError(
            f"A fix is kind 'command' or kind 'card'; {kind!r} is neither.")

    command_id = fix.get("id")
    spec = COMMANDS.get(command_id) if isinstance(command_id, str) else None
    if spec is None:
        raise FixError(
            f"Nothing in this build runs {command_id!r}, so it is not shown as "
            f"a fix. A gap may only point at a command this build owns.")

    args = fix.get("args", {})
    if not isinstance(args, dict):
        raise FixError(f"The arguments for {command_id} are not an object.")
    declared = spec["args"]
    unknown = set(args) - set(declared)
    if unknown:
        raise FixError(
            f"{command_id} takes {sorted(declared) or 'no arguments'}; "
            f"{sorted(unknown)} is not among them.")
    clean: dict[str, str] = {}
    for name, kind_name in declared.items():
        value = args.get(name)
        if not isinstance(value, str) or not _TYPES[kind_name].match(value):
            raise FixError(
                f"{command_id} needs {name} as a plain {kind_name}; "
                f"{value!r} is not one.")
        clean[name] = value
    return {"kind": "command", "id": command_id, "args": clean}


def render(fix: Any) -> str:
    """The line the owner reads. Raises :class:`FixError` rather than guessing."""
    clean = validate_fix(fix)
    if clean["kind"] == "none":
        return ""
    if clean["kind"] == "card":
        return f"Open the '{clean['id']}' step again."
    spec = COMMANDS[clean["id"]]
    return spec["render"](clean["args"])


#: Caps on the prose a gap carries. A gap is three sentences for a person, not
#: a place to park a screenful of text.
MAX_GAP_TEXT = 400

_FALLBACK_FIX = {"kind": "command", "id": "setup.rerun", "args": {}}


def _plain(text: Any, limit: int = MAX_GAP_TEXT) -> str:
    """Printable text, control characters removed rather than escaped.

    A gap is printed to a terminal and rendered in a browser. An escape
    sequence in a stored ``what`` could repaint a console line, so the
    characters that could do it are dropped on the way out.
    """
    raw = text if isinstance(text, str) else ""
    clean = "".join(ch for ch in raw if ch == " " or (ch.isprintable() and ch != "\x7f"))
    return " ".join(clean.split())[:limit]


def safe_gap(record: Any, *, drop_invalid: bool = False) -> dict[str, Any] | None:
    """One stored gap, re-rendered from its typed fix. Never trusts the string.

    A gap record can arrive from a file this process wrote minutes ago or from
    a file something else edited in between. There is no way to tell them
    apart, so neither is trusted: ``fix_command`` is thrown away and rebuilt
    from ``fix`` through the registry every time it is read.

    *drop_invalid* is for the persisted path: a stored gap whose fix does not
    validate is discarded outright rather than degraded, because the one thing
    it definitely is not is a gap this build recorded.
    """
    if not isinstance(record, dict):
        return None
    try:
        clean_fix = validate_fix(record.get("fix"))
    except FixError:
        if drop_invalid:
            return None
        clean_fix = dict(_FALLBACK_FIX)
    return {
        "skill": _plain(record.get("skill"), 40),
        "what": _plain(record.get("what")),
        "why": _plain(record.get("why")),
        "impact": _plain(record.get("impact")),
        "fix": clean_fix,
        "fix_command": render(clean_fix),
        "blocking": bool(record.get("blocking")),
        "recorded_at": _plain(record.get("recorded_at"), 32),
    }


def safe_gaps(records: Any, *, drop_invalid: bool = False,
              limit: int = 100) -> list[dict[str, Any]]:
    """Every stored gap, re-rendered. Anything that is not a gap is dropped."""
    if not isinstance(records, list):
        return []
    out: list[dict[str, Any]] = []
    for record in records[:limit]:
        clean = safe_gap(record, drop_invalid=drop_invalid)
        if clean is not None:
            out.append(clean)
    return out


def fix_line(record: Any) -> str:
    """The command to print for a stored gap, rebuilt from its typed fix.

    Every screen that shows a fix goes through here. Nothing prints
    ``gap["fix_command"]`` directly: that string is data from a file, and the
    whole point of the registry is that the build decides what a command line
    says.
    """
    clean = safe_gap(record)
    return clean["fix_command"] if clean else ""


def gap(*, skill: str, what: str, why: str, impact: str, fix: Any,
        blocking: bool = False) -> dict[str, Any]:
    """A gap record in the A.8 shape, with its fix checked before it is stored.

    An unrenderable fix does not silently become a gap without one: it becomes a
    gap whose fix is the honest fallback (run the wizard again), because a gap
    an owner cannot act on is the shape of failure this whole design is against.
    """
    try:
        clean_fix = validate_fix(fix)
    except FixError:
        clean_fix = dict(_FALLBACK_FIX)
    return {
        "skill": _plain(skill, 40),
        "what": _plain(what),
        "why": _plain(why),
        "impact": _plain(impact),
        "fix": clean_fix,
        "fix_command": render(clean_fix),
        "blocking": bool(blocking),
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


__all__ = ["COMMANDS", "MAX_GAP_TEXT", "FixError", "command_ids", "fix_line",
           "gap", "render", "safe_gap", "safe_gaps", "validate_fix"]
