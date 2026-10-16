"""Her own programs: command line tools and desktop apps she adds herself.

The owner asked: "If I have Office on my computer, can I add those? If I have
my own programs, can I connect them?" Yes. One list, ``<state>/programs.json``
(``{"programs": [row ...], "open_yes": [program id ...]}``), one row per
program she added::

    {"id", "name", "kind": "cli" | "desktop", "path", "command", "added_at",
     "help": "<first lines of its own help>", "commands": [specs],
     "always_allow": false}

* **A command line program** (``kind: "cli"``) is added by its full path or
  by a name on ``PATH`` (``ffmpeg``), found once, here, and kept by its full
  path. Its own ``--help`` (then ``-h``, then ``/?``) runs once, 10 seconds at
  most, no shell, no window; the first 200 lines are kept and read with
  :func:`puffpai.box.cli_tools.parse_help` / ``derive_specs``; a program with
  subcommands gets one entry per subcommand (:func:`specs_from_help`). The brain sees
  it in ``installed.listing()`` and in ``run_command``'s description, and
  runs it with ``run_command``: her yes for every command, unless she turned
  on **Always allow** for that program. Then a plain call of THAT program
  (one program, its arguments, no shell features at all) runs as a list of
  arguments by its full path, never through a shell, and every run writes a
  security log line. Anything with shell features still asks.
* **A desktop program** (``kind: "desktop"``): an ``.exe`` or Start Menu
  shortcut on Windows, an ``.app`` on a Mac, a ``.desktop`` file on Linux. It
  is started on purpose with its window showing (``os.startfile``, ``open
  -a``, the ``.desktop`` file's own ``Exec``), optionally with a file. The
  brain opens a file in one with ``open_in_program``, which asks her yes the
  first time per program.
* **Opening a file** (``POST /files/open``, ``open_in_program``) works only
  for files inside her data folder (``My files`` is in it) or a linked
  folder, never the vault, the state folder or her sign-in keys. A program
  file (``.exe``, ``.bat``, ``.js`` ...) is never opened in its default app:
  that would run it.

``security.host_programs.registered`` reads the same file, so a registered
program is always started by the full path she chose.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os
import re
import secrets
import shlex
import struct
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Optional

from puffpai import fsutil, procflags
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

FILE = "programs.json"
KINDS = ("cli", "desktop")
HELP_SECONDS = 10
HELP_LINES = 200
#: A program with subcommands (git, docker, npm): at most this many
#: ``<program> <sub> --help`` reads, this many seconds for all of them, and
#: this many commands kept.
SUB_HELP_MAX = 25
SUB_HELP_BUDGET = 60
COMMANDS_MAX = 60
#: The flag that prints a subcommand's help as text. ``git <sub> --help``
#: opens a web page on Windows (or a pager); ``-h`` prints the usage.
SUB_HELP_FLAG = {"git": "-h"}
#: Subcommands whose help is never run (listed all the same): ones that
#: change or remove things, publish, or start something long-running, in
#: case a program ignores ``--help`` and does the thing.
NO_SUB_HELP = frozenset({
    "help", "server", "serve", "daemon", "watch", "shell", "repl", "console", "start",
    "stop", "restart", "kill", "run", "exec", "up", "down", "rm", "rmi", "remove",
    "delete", "del", "uninstall", "unpublish", "publish", "deploy", "push", "prune",
    "purge", "clean", "reset", "destroy", "drop", "wipe", "format", "update",
    "upgrade", "self-update", "selfupdate", "install", "login", "logout", "auth",
    "edit", "open", "docs", "bugs", "repo", "home", "browse"})
#: Installed desktop programs offered to pick from.
DESKTOP_MAX = 300
#: How long a command of her own program may run.
RUN_SECONDS = 120
MAX_OUTPUT = 4000

_IS_WINDOWS = sys.platform == "win32"
_ID_RE = re.compile(r"^p[0-9a-f]{8}\Z")
_COMMAND_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}\Z")
#: What a plain call never has: anything a shell would read as more than one
#: program and its arguments (chaining, pipes, redirection, variables,
#: command substitution, grouping, a second line).
_SHELL_CHARS = frozenset(";|&$`<>(){}\n\r")
#: More characters cmd.exe reads in a .bat/.cmd file's arguments.
_BATCH_CHARS = frozenset('%^!"')
_CLI_EXTS_WIN = (".exe", ".com", ".bat", ".cmd", ".ps1", ".py", ".sh")
_DESKTOP_EXTS_WIN = (".exe", ".lnk")
#: Files that would RUN, not open, in their default app.
RUNNABLE_EXTS = frozenset({
    ".exe", ".com", ".bat", ".cmd", ".ps1", ".psm1", ".psd1", ".vbs", ".vbe", ".js",
    ".jse", ".wsf", ".wsh", ".hta", ".msi", ".msp", ".mst", ".lnk", ".url", ".scr",
    ".pif", ".cpl", ".jar", ".reg", ".application", ".appref-ms", ".sh", ".command",
    ".app", ".desktop", ".py", ".pyw", ".scf", ".inf", ".msc", ".gadget", ".diagcab",
    ".settingcontent-ms", ".library-ms", ".search-ms", ".xll", ".xbap", ".chm",
    ".appimage", ".run", ".bin", ".pkg", ".dmg", ".deb", ".rpm"})

#: Programs that run any other command: with Always allow on, the bot could
#: do anything on this computer without asking. Said plainly, never refused.
_RUNS_ANYTHING = frozenset({
    "cmd", "powershell", "pwsh", "bash", "sh", "zsh", "fish", "dash", "wsl", "python",
    "python3", "py", "pythonw", "node", "deno", "bun", "ruby", "perl", "php", "lua",
    "wscript", "cscript", "mshta", "rundll32", "regsvr32", "env", "sudo", "osascript",
    "java", "dotnet", "conhost", "start", "xargs", "find", "git"})
#: Shells and interpreters: their "help" is a banner, a prompt or an error
#: (cmd, powershell), and some open a window. It is never run for them; the
#: row says what they are instead.
_SHELLS_NO_HELP = frozenset({
    "cmd", "powershell", "powershell_ise", "pwsh", "bash", "sh", "zsh", "fish", "dash",
    "ksh", "csh", "tcsh", "wsl", "python", "python3", "py", "pythonw", "pyw", "node",
    "deno", "bun", "ruby", "perl", "php", "lua", "wscript", "cscript", "mshta",
    "osascript"})
_PY_VERSION_RE = re.compile(r"^(?:python|pythonw|python3)[0-9.]*\Z", re.IGNORECASE)
SHELL_NOTE = "A command shell: BuildBot passes it one command at a time."


def is_shell(command: str) -> bool:
    """True for a shell or interpreter (its help is never run)."""
    word = str(command or "").lower()
    return word in _SHELLS_NO_HELP or bool(_PY_VERSION_RE.match(word))


ALWAYS_ALLOW_WARNING = ("This program can start any other program, so with Always allow "
                        "on, the bot can do anything on this computer without asking you.")

NOT_FOUND = "There is no program at that place. Check the path and try again."
NOT_ON_PATH = ("I could not find a program called {name} on this computer. Give its "
               "full path instead.")
NOT_A_PROGRAM = "That file is not a program BuildBot can start here."
NO_SUCH = "There is no program by that name in your list."
FILE_OUTSIDE = ("I can only open files in your data folder, My files, or a folder you "
                "linked. That file is somewhere else.")
FILE_MISSING = "There is no file at that place."
FILE_RUNS = ("That file is a program, not a document, so opening it would run it. "
             "I only open documents this way.")

_lock = threading.Lock()


# ── the list ───────────────────────────────────────────────────────────────

def _file() -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), FILE)


def _load() -> dict:
    try:
        import json
        with open(_file(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"programs": [], "open_yes": []}
    if not isinstance(data, dict):
        return {"programs": [], "open_yes": []}
    rows = [r for r in data.get("programs") or [] if isinstance(r, dict)
            and _ID_RE.match(str(r.get("id") or "")) and r.get("kind") in KINDS]
    yes = [str(i) for i in data.get("open_yes") or [] if isinstance(i, str)]
    return {"programs": rows, "open_yes": yes}


def _save(data: dict) -> None:
    from puffpai.config import get_state_dir
    get_state_dir(create=True)
    fsutil.atomic_write_json(_file(), data)


def _public(row: dict) -> dict:
    """The row in the pinned shape (and nothing else)."""
    return {"id": str(row.get("id") or ""), "name": str(row.get("name") or ""),
            "kind": row.get("kind") if row.get("kind") in KINDS else "cli",
            "path": str(row.get("path") or ""), "command": str(row.get("command") or ""),
            "added_at": str(row.get("added_at") or ""), "help": str(row.get("help") or ""),
            "commands": [c for c in row.get("commands") or [] if isinstance(c, dict)],
            "always_allow": row.get("always_allow") is True}


def rows() -> list[dict]:
    """Every program she added, in the pinned shape."""
    return [_public(r) for r in _load()["programs"]]


def get(pid: Any) -> Optional[dict]:
    for r in rows():
        if r["id"] == pid:
            return r
    return None


def registered_paths() -> list[str]:
    """The full path of every program she added that is still there (read by
    ``security.host_programs.registered``)."""
    return [r["path"] for r in rows() if _exists(r["path"])]


def _exists(path: str) -> bool:
    if not isinstance(path, str) or not path or "\0" in path or not os.path.isabs(path):
        return False
    try:
        if sys.platform == "darwin" and path.lower().endswith(".app"):
            return os.path.isdir(path)
        return os.path.isfile(path)
    except (OSError, ValueError):
        return False


# ── finding a program ──────────────────────────────────────────────────────

def _path_dirs() -> list[str]:
    """``PATH`` folders, full paths only (never the current folder, never a
    relative entry: a program planted where BuildBot stands is not found)."""
    out = []
    for d in (os.environ.get("PATH") or "").split(os.pathsep):
        d = d.strip().strip('"')
        if d and os.path.isabs(d) and d not in out:
            out.append(d)
    return out


def find_on_path(name: str) -> Optional[str]:
    """The full path of the program *name* on ``PATH``, or None."""
    if not _COMMAND_RE.match(str(name or "")):
        return None
    exts = [""]
    if _IS_WINDOWS:
        base, ext = os.path.splitext(name)
        exts = [""] if ext.lower() in (".exe", ".com", ".bat", ".cmd") else \
            [".exe", ".com", ".bat", ".cmd"]
    for d in _path_dirs():
        for ext in exts:
            cand = os.path.join(d, name + ext)
            try:
                if os.path.isfile(cand) and (_IS_WINDOWS or os.access(cand, os.X_OK)):
                    return os.path.abspath(cand)
            except (OSError, ValueError):
                continue
    return None


def _ok_kind(path: str, kind: str) -> bool:
    low = path.lower()
    if kind == "desktop":
        if _IS_WINDOWS:
            return low.endswith(_DESKTOP_EXTS_WIN)
        if sys.platform == "darwin":
            return low.endswith(".app") or os.access(path, os.X_OK)
        return low.endswith(".desktop") or os.access(path, os.X_OK)
    if _IS_WINDOWS:
        return low.endswith(_CLI_EXTS_WIN)
    return os.access(path, os.X_OK) or low.endswith((".sh", ".py"))


def argv_prefix(path: str) -> Optional[list[str]]:
    """How a command line program at *path* starts, as a list (no shell):
    the program itself, or the interpreter BuildBot trusts for a script."""
    from puffpai.security import host_programs
    low = path.lower()
    if low.endswith(".py"):
        return [sys.executable, path]
    if low.endswith(".ps1"):
        ps = host_programs.path("powershell") if _IS_WINDOWS else None
        return [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", path] if ps else None
    if low.endswith(".sh"):
        if _IS_WINDOWS:
            from puffpai import brain_commands
            bash = brain_commands.git_bash()
            return [bash, path] if bash else None
        return [path] if os.access(path, os.X_OK) else ["/bin/sh", path]
    return [path]


def _stem(path: str) -> str:
    name = os.path.basename(path.rstrip("\\/"))
    stem = os.path.splitext(name)[0] if not name.lower().endswith(".app") else name[:-4]
    return re.sub(r"[^A-Za-z0-9._+-]+", "-", stem).strip("-.")[:64] or "program"


# ── its own help ───────────────────────────────────────────────────────────

def _help_flags() -> tuple[str, ...]:
    return ("--help", "-h", "/?") if _IS_WINDOWS else ("--help", "-h")


def read_help(prefix: list[str], flags: Optional[tuple[str, ...]] = None) -> str:
    """The program's own help: ``--help``, then ``-h``, then ``/?`` (Windows),
    each at most :data:`HELP_SECONDS`, no shell, no window, no input. The
    first :data:`HELP_LINES` lines of the first one that says anything."""
    from puffpai.security import host_programs
    kw = host_programs.run_kwargs()
    for flag in (flags or _help_flags()):
        try:
            done = subprocess.run(prefix + [flag], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=HELP_SECONDS,
                                  stdin=subprocess.DEVNULL, shell=False, cwd=kw["cwd"],
                                  env=kw["env"], **procflags.hidden())
        except (OSError, subprocess.SubprocessError, ValueError):
            continue
        text = ((done.stdout or "") + ("\n" + done.stderr if done.stderr else "")).strip()
        if text:
            return "\n".join(text.splitlines()[:HELP_LINES])
    return ""


def _sub_help_flag(command: str) -> str:
    return SUB_HELP_FLAG.get(str(command or "").lower(), "--help")


def _command_row(path: str, command: str, sub: str, about: str,
                 info: Optional[dict]) -> dict:
    """One subcommand as a ``commands`` entry: what the brain may type
    (``argv``, the program's name first) and what the page shows (``name``,
    ``what``)."""
    pos = list((info or {}).get("positionals") or [])
    files = [p for p in pos if p.get("file")][:1]
    strings = [p for p in pos if not p.get("file")][:3]
    argv = [command, sub] + ["{" + p["name"] + "}" for p in pos if p in files or p in strings]
    name = re.sub(r"[^a-z0-9]+", "_", f"{command}_{sub}".lower()).strip("_")[:48].rstrip("_")
    row = {"name": name, "command": f"{command} {sub}", "what": about, "argv": argv,
           "bin": path,
           "strings": [{"name": p["name"], "required": bool(p.get("required"))} for p in strings],
           "has_output_option": bool((info or {}).get("has_output_option"))}
    if files:
        row["files"] = {"file": {"from": "folder", "ext": list((info or {}).get("exts") or [])}}
    return row


def specs_from_help(path: str, command: str, text: str,
                    prefix: Optional[list[str]] = None) -> list[dict]:
    """What the program can do, from its own help.

    A program with subcommands (git, docker, npm: "Commands:" sections, npm's
    "All commands:" list, git's "These are common Git commands" groups): one
    entry per subcommand, :data:`COMMANDS_MAX` at most, and for the first
    :data:`SUB_HELP_MAX` of them ``<program> <sub> --help`` is read the same
    way as the program's own help (argv only, hidden, :data:`HELP_SECONDS`
    each, :data:`SUB_HELP_BUDGET` in all) for the words each one takes.
    Subcommands that change things or start something long-running
    (:data:`NO_SUB_HELP`) are listed but their help is never run: a program
    that ignores ``--help`` must not do the thing itself.

    A program without subcommands: ``derive_specs`` over the one help text
    already read (no more runs)."""
    from puffpai.box import cli_tools
    try:
        subs = cli_tools.list_subcommands(text, COMMANDS_MAX)
    except Exception:  # noqa: BLE001 - help we cannot read is no commands
        subs = []
    if subs:
        out: list[dict] = []
        reads, started = 0, time.monotonic()
        flag = _sub_help_flag(command)
        for sub, about in subs:
            info = None
            if (prefix and reads < SUB_HELP_MAX and sub not in NO_SUB_HELP
                    and time.monotonic() - started < SUB_HELP_BUDGET):
                reads += 1
                try:
                    info = cli_tools.parse_help(read_help(prefix + [sub], (flag,)))
                except Exception:  # noqa: BLE001 - one unreadable help is no details
                    info = None
            out.append(_command_row(path, command, sub, about, info))
        return out[:COMMANDS_MAX]
    try:
        return cli_tools.derive_specs(path, command, lambda argv: text if argv == [path]
                                      else "", max_calls=1, host=True)
    except Exception as exc:  # noqa: BLE001 - help we cannot read is no commands
        logger.debug("programs: help not read (%s)", type(exc).__name__)
        return []


# ── adding, changing, removing ─────────────────────────────────────────────

class ProgramError(ValueError):
    """A plain reason for the owner."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def register(kind: str, path: str = "", command: str = "", name: str = "", *,
             read_help_now: bool = True) -> dict:
    """Add a program she chose. Refused only when it is not there."""
    kind = str(kind or "")
    if kind not in KINDS:
        raise ProgramError("Say whether it is a command line program or a desktop program.")
    path = str(path or "").strip().strip('"')
    command = str(command or "").strip()
    if path:
        path = os.path.expanduser(path)
        if not os.path.isabs(path) or not _exists(path):
            raise ProgramError(NOT_FOUND)
        path = os.path.abspath(path)
    elif command:
        found = find_on_path(command)
        if not found:
            raise ProgramError(NOT_ON_PATH.format(name=command[:64]))
        path = found
    else:
        raise ProgramError("Give the program's full path, or its name.")
    if not _ok_kind(path, kind):
        raise ProgramError(NOT_A_PROGRAM)
    word = _stem(path) if kind == "cli" else ""
    label = " ".join(str(name or "").split())[:80] or _stem(path)
    help_text, specs = "", []
    if kind == "cli" and read_help_now and is_shell(word):
        help_text = SHELL_NOTE                  # never run: a banner or a prompt
    elif kind == "cli" and read_help_now:
        prefix = argv_prefix(path)
        if prefix:
            help_text = read_help(prefix)
            specs = specs_from_help(path, word, help_text, prefix)
    row = {"id": "p" + secrets.token_hex(4), "name": label, "kind": kind, "path": path,
           "command": word, "added_at": _now(), "help": help_text, "commands": specs,
           "always_allow": False}
    with _lock:
        data = _load()
        same = [r for r in data["programs"] if os.path.normcase(str(r.get("path"))) ==
                os.path.normcase(path) and r.get("kind") == kind]
        if same:
            # Added again: the same row, its help read again; her choices kept.
            old = same[0]
            row["id"], row["always_allow"] = old["id"], old.get("always_allow") is True
            if not name:
                row["name"] = old.get("name") or label
            data["programs"] = [row if r is old else r for r in data["programs"]]
        else:
            data["programs"].append(row)
        _save(data)
    _audit("program_added", f"{kind}: {label} ({path})")
    return _public(row)


def remove(pid: str) -> bool:
    with _lock:
        data = _load()
        keep = [r for r in data["programs"] if r.get("id") != pid]
        if len(keep) == len(data["programs"]):
            return False
        data["programs"] = keep
        data["open_yes"] = [i for i in data["open_yes"] if i != pid]
        _save(data)
    _audit("program_removed", pid)
    return True


def set_always_allow(pid: str, on: bool) -> dict:
    """Her choice, from a paired device only (the route checks). Returns
    ``{"program": row, "warning"?: line}``."""
    with _lock:
        data = _load()
        row = next((r for r in data["programs"] if r.get("id") == pid), None)
        if row is None:
            raise ProgramError(NO_SUCH)
        row["always_allow"] = bool(on)
        _save(data)
    _audit("program_always_allow", f"{row.get('name')}: {'on' if on else 'off'}")
    out: dict = {"program": _public(row)}
    if on and str(row.get("command") or "").lower() in _RUNS_ANYTHING:
        out["warning"] = ALWAYS_ALLOW_WARNING
    return out


def _audit(action: str, details: str, extra: Optional[dict] = None) -> None:
    try:
        from puffpai.security.audit_log import AuditLog
        AuditLog().log(action=action, user="owner", result="done", details=details[:400],
                       extra=extra or {})
    except Exception:  # noqa: BLE001 - the log never stops her action
        pass


# ── the brain runs her command line programs (run_command) ─────────────────

def cli_commands() -> dict[str, str]:
    """``{command: how the brain runs it}`` for ``run_command``'s description."""
    out: dict[str, str] = {}
    for r in rows():
        if r["kind"] != "cli" or not r["command"] or not _exists(r["path"]):
            continue
        shown = [" ".join(a for a in c.get("argv") or [] if isinstance(a, str))
                 for c in r["commands"][:3]]
        line = f"{r['command']} ... (your own program {r['name']}"
        if shown:
            line += "; for example " + ", ".join(s.strip() for s in shown)
        out[r["command"]] = line + ")"
    return out


def match(command: str) -> Optional[tuple[dict, list[str]]]:
    """``(row, arguments)`` when *command* is ONE plain call of a command
    line program she added (by its name or its full path), else None. Plain:
    no shell features at all, so it can run as a list of arguments."""
    text = str(command or "").strip()
    if not text or any(c in _SHELL_CHARS for c in text):
        return None
    try:
        words = shlex.split(text, posix=True)
    except ValueError:
        return None
    if not words:
        return None
    head = words[0]
    for r in rows():
        if r["kind"] != "cli" or not _exists(r["path"]):
            continue
        same = (head.lower() == r["command"].lower() if _IS_WINDOWS else head == r["command"]) \
            or os.path.normcase(head) == os.path.normcase(r["path"])
        if not same:
            continue
        args = words[1:]
        if r["path"].lower().endswith((".bat", ".cmd")) and any(
                c in _BATCH_CHARS for a in args for c in a):
            return None
        return r, args
    return None


def always_allowed(command: str) -> Optional[tuple[dict, list[str]]]:
    """The plain call of a program she set to Always allow, or None."""
    got = match(command)
    return got if got and got[0]["always_allow"] else None


def run_cli(row: dict, args: list[str], *, folder: str, asked: bool,
            ctx: Optional[dict] = None) -> dict:
    """Run her program by its full path with *args*, a list, never a shell.
    One security log line per run (which program, asked or Always allow)."""
    from puffpai import brain_commands
    from puffpai.security import host_programs
    full = host_programs.registered(row["id"])
    prefix = argv_prefix(full) if full else None
    if not prefix:
        raise ProgramError(f"{row['name']} cannot start on this computer.")
    argv = prefix + [str(a) for a in args]
    device = str((ctx or {}).get("device_id") or "")
    _audit("own_program_run",
           brain_commands.hide_secrets(" ".join([row["command"]] + args))[:300],
           {"program": row["name"], "how": "asked" if asked else "always_allow",
            "device": device or "this computer"})
    try:
        done = subprocess.run(argv, cwd=folder, env=brain_commands.clean_env(),
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=RUN_SECONDS,
                              stdin=subprocess.DEVNULL, shell=False, **procflags.hidden())
    except subprocess.TimeoutExpired:
        return {"exit_code": None, "worked": False, "folder": folder, "output": "",
                "note": f"It was stopped after {RUN_SECONDS} seconds, so it did not finish."}
    except OSError as exc:
        return {"exit_code": None, "worked": False, "folder": folder, "output": "",
                "note": f"{row['name']} could not start ({type(exc).__name__})."}
    output = (done.stdout or "") + (("\n" + done.stderr) if done.stderr else "")
    text = brain_commands.hide_secrets(output.strip())
    if len(text) > MAX_OUTPUT:
        text = "...(start cut)\n" + text[-MAX_OUTPUT:]
    out: dict = {"exit_code": done.returncode, "worked": done.returncode == 0,
                 "folder": folder, "output": text}
    if done.returncode != 0:
        out["note"] = brain_commands.FAILED_NOTE.format(code=done.returncode)
    return out


# ── files she may open ─────────────────────────────────────────────────────

def allowed_folders() -> list[str]:
    """Her data folder (``My files`` is inside it) and her linked folders."""
    from puffpai.knowledge import my_data
    out = []
    try:
        out.append(my_data.data_dir())
    except Exception:  # noqa: BLE001
        pass
    try:
        out += [str(r["path"]) for r in my_data.linked_folders()]
    except Exception:  # noqa: BLE001
        pass
    return [p for p in out if p and os.path.isabs(p)]


def file_refusal(path: Any, *, default_app: bool = True) -> str:
    """"" when *path* may be opened, else the plain reason."""
    from puffpai.knowledge import my_data
    text = str(path or "").strip().strip('"')
    if not text or "\0" in text or not os.path.isabs(text):
        return FILE_OUTSIDE
    real = my_data._canon(text)  # noqa: SLF001 - the same reading the watcher uses
    if not any(my_data._inside_or_equal(real, my_data._canon(f))  # noqa: SLF001
               and real != my_data._canon(f) for f in allowed_folders()):  # noqa: SLF001
        return FILE_OUTSIDE
    why = my_data.refusal(text)
    if why:
        return FILE_OUTSIDE
    if not os.path.isfile(real):
        return FILE_MISSING
    if default_app and os.path.splitext(real)[1].lower() in RUNNABLE_EXTS:
        return FILE_RUNS
    return ""


def resolve_file(file: Any) -> str:
    """A full path for *file*: as given when full, else inside My files, else
    inside the data folder."""
    text = str(file or "").strip().strip('"')
    if not text or os.path.isabs(text):
        return text
    from puffpai.knowledge import my_data
    for base in (my_data.my_files_dir(), my_data.data_dir()):
        cand = os.path.join(base, text)
        if os.path.isfile(cand):
            return os.path.abspath(cand)
    return os.path.join(my_data.my_files_dir(), text)


# ── starting a desktop program ─────────────────────────────────────────────

_FIELD_CODES = re.compile(r"%[fFuUdDnNickvm%]")


def desktop_exec(desktop_file: str) -> Optional[list[str]]:
    """The ``Exec`` of a Linux ``.desktop`` file as a list, its program by full
    path (found on ``PATH`` once), the file codes (``%f`` ...) left in."""
    entry = _read_desktop(desktop_file)
    if not entry or not entry.get("exec"):
        return None
    try:
        words = shlex.split(entry["exec"], posix=True)
    except ValueError:
        return None
    if not words:
        return None
    head = words[0]
    full = head if os.path.isabs(head) else find_on_path(head)
    if not full or not os.path.isfile(full):
        return None
    return [full] + words[1:]


def _fill(words: list[str], file: str) -> list[str]:
    out, placed = [], False
    for w in words:
        if w in ("%f", "%F", "%u", "%U"):
            if file and not placed:
                out.append(file)
                placed = True
            continue
        w = _FIELD_CODES.sub(lambda m: "%" if m.group(0) == "%%" else "", w)
        if w:
            out.append(w)
    if file and not placed:
        out.append(file)
    return out


def _start_window(target: str, file: str = "") -> None:
    """Start *target* (a program, shortcut, app or ``.desktop`` file; empty:
    the file's default app) with its window SHOWING: that window is the
    point. Never a shell."""
    if _IS_WINDOWS:
        if target:
            if file:
                os.startfile(target, "open", subprocess.list2cmdline([file]))  # type: ignore[attr-defined]  # noqa: S606
            else:
                os.startfile(target)  # type: ignore[attr-defined]  # noqa: S606
        else:
            os.startfile(file)  # type: ignore[attr-defined]  # noqa: S606
        return
    if sys.platform == "darwin":
        if target and target.lower().endswith(".app"):
            argv = ["/usr/bin/open", "-a", target] + ([file] if file else [])
        elif target:
            argv = [target] + ([file] if file else [])
        else:
            argv = ["/usr/bin/open", file]
    elif target and target.lower().endswith(".desktop"):
        words = desktop_exec(target)
        if not words:
            raise ProgramError("That program's .desktop file does not say how to start it.")
        argv = _fill(words, file)
    elif target:
        argv = [target] + ([file] if file else [])
    else:
        argv = ["/usr/bin/xdg-open", file]
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
                     cwd=os.path.expanduser("~"), **procflags.hidden())


def launch(pid: str, file: str = "") -> dict:
    """Start her desktop program *pid*, optionally with *file*."""
    row = get(pid)
    if not row or row["kind"] != "desktop":
        raise ProgramError(NO_SUCH)
    from puffpai.security import host_programs
    full = host_programs.registered(row["id"])
    if not full:
        raise ProgramError(f"{row['name']} is not at {row['path']} any more.")
    if file:
        file = resolve_file(file)
        why = file_refusal(file, default_app=False)
        if why:
            raise ProgramError(why)
    _start_window(full, file)
    _audit("own_program_opened", f"{row['name']}" + (f": {os.path.basename(file)}"
                                                      if file else ""))
    return {"ok": True}


def open_file(path: str) -> dict:
    """Open *path* in its default app (Word for a .docx)."""
    path = resolve_file(path)
    why = file_refusal(path, default_app=True)
    if why:
        raise ProgramError(why)
    _start_window("", path)
    _audit("file_opened", os.path.basename(path))
    return {"ok": True}


# ── the brain's open_in_program ────────────────────────────────────────────

OPEN_SCHEMA = {"type": "object", "properties": {
    "program": {"type": "string", "description": "The name of one of the owner's desktop "
                "programs (from the installed list), or empty for the file's own app."},
    "file": {"type": "string", "description": "The file: a full path, or a name inside "
             "My files."}},
    "required": ["file"], "additionalProperties": False}


def _desktop_named(name: Any) -> Optional[dict]:
    want = " ".join(str(name or "").split()).lower()
    if not want:
        return None
    for r in rows():
        if r["kind"] == "desktop" and want in (r["name"].lower(), r["id"],
                                               _stem(r["path"]).lower()):
            return r
    return None


def _yes_key(args: dict) -> str:
    row = _desktop_named(args.get("program"))
    return row["id"] if row else "default"


def _open_question(args: dict, ctx: dict) -> Optional[dict]:
    if args.get("program") and not _desktop_named(args.get("program")):
        raise ProgramError(NO_SUCH)
    file = resolve_file(args.get("file"))
    row = _desktop_named(args.get("program"))
    why = file_refusal(file, default_app=row is None)
    if why:
        raise ProgramError(why)
    if _yes_key(args) in _load()["open_yes"]:
        return None
    where = row["name"] if row else "its own app"
    return {"tool": "open_in_program", "title": f"May I open files in {where}?",
            "detail": f"Open {os.path.basename(file)} in {where}. After this yes I open "
                      f"files from your folders in {where} without asking again."}


def _open_tool(args: dict, ctx: dict) -> dict:
    key = _yes_key(args)
    with _lock:
        data = _load()
        if key not in data["open_yes"]:
            if not ctx.get("said_yes"):
                raise ProgramError("this was not approved")
            data["open_yes"].append(key)
            _save(data)
    row = _desktop_named(args.get("program"))
    if row:
        launch(row["id"], str(args.get("file") or ""))
        return {"opened": True, "in": row["name"]}
    open_file(str(args.get("file") or ""))
    return {"opened": True, "in": "its own app"}


def register_tool() -> None:
    """``open_in_program``: open a file from her folders in one of her
    desktop programs or in its own app. Asks her yes the first time per
    program."""
    from puffpai import brain_tools
    brain_tools.register(
        "open_in_program", "Open a file from the owner's folders (My files, her data folder, "
        "her linked folders) on this computer's screen, in one of her desktop programs "
        "(like Word or Excel) or in the file's own app. The owner says yes the first time "
        "per program.", OPEN_SCHEMA, _open_tool, needs_yes=_open_question,
        progress=lambda a: f"Opening {os.path.basename(str(a.get('file') or ''))[:60]} ...",
        when=lambda ctx: ctx.get("caller") in ("owner", "terminal"))


# ── installed desktop programs on this computer ────────────────────────────

def _lnk_target(path: str) -> str:
    """The target of a Windows shortcut (.lnk, MS-SHLLINK): its LinkInfo's
    local path, or "" (an advertised shortcut, a folder, a damaged file)."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(65536)
    except OSError:
        return ""
    try:
        if len(data) < 0x4C or struct.unpack_from("<I", data, 0)[0] != 0x4C:
            return ""
        flags = struct.unpack_from("<I", data, 0x14)[0]
        at = 0x4C
        if flags & 0x1:                                  # HasLinkTargetIDList
            at += 2 + struct.unpack_from("<H", data, at)[0]
        if not flags & 0x2:                              # no LinkInfo
            return ""
        size, head = struct.unpack_from("<II", data, at)
        info = data[at:at + size]
        info_flags = struct.unpack_from("<I", info, 8)[0]
        if not info_flags & 0x1:                         # VolumeIDAndLocalBasePath
            return ""
        base_off = struct.unpack_from("<I", info, 16)[0]
        suffix_off = struct.unpack_from("<I", info, 24)[0]
        if head >= 0x24:
            ubase = struct.unpack_from("<I", info, 28)[0]
            usuf = struct.unpack_from("<I", info, 32)[0]
            base = _utf16z(info, ubase)
            suffix = _utf16z(info, usuf) if usuf else ""
        else:
            base = _ansiz(info, base_off)
            suffix = _ansiz(info, suffix_off) if suffix_off else ""
        return (base + suffix).strip()
    except (struct.error, IndexError, UnicodeDecodeError):
        return ""


def _ansiz(buf: bytes, off: int) -> str:
    end = buf.index(b"\0", off)
    return buf[off:end].decode("mbcs" if _IS_WINDOWS else "latin-1", "replace")


def _utf16z(buf: bytes, off: int) -> str:
    end = off
    while end + 1 < len(buf) and buf[end:end + 2] != b"\0\0":
        end += 2
    return buf[off:end].decode("utf-16-le", "replace")


def _start_menu_dirs() -> list[str]:
    out = []
    for var in ("ProgramData", "APPDATA"):
        base = os.environ.get(var)
        if base and os.path.isabs(base):
            out.append(os.path.join(base, "Microsoft", "Windows", "Start Menu", "Programs"))
    return out


_SKIP_NAME = re.compile(r"(?i)\b(uninstall|uninst|remove|readme|help|license|website|"
                        r"release notes|documentation)\b")


def _start_menu() -> list[dict]:
    out = []
    for root in _start_menu_dirs():
        for folder, _dirs, files in os.walk(root):
            for f in files:
                if not f.lower().endswith(".lnk"):
                    continue
                name = f[:-4]
                if _SKIP_NAME.search(name):
                    continue
                target = _lnk_target(os.path.join(folder, f))
                if target and not target.lower().endswith(".exe"):
                    continue            # a document, a web page, a folder
                path = target if target and os.path.isfile(target) else \
                    os.path.join(folder, f)
                out.append({"name": name, "path": path})
    return out


def _app_paths(reg: Any = None) -> list[dict]:
    """Programs in ``App Paths`` (HKLM then HKCU), where installers such as
    Office (WINWORD.EXE, EXCEL.EXE) register themselves."""
    if reg is None:
        try:
            import winreg as reg  # type: ignore[no-redef]
        except ImportError:
            return []
    sub = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
    out = []
    for hive in (reg.HKEY_LOCAL_MACHINE, reg.HKEY_CURRENT_USER):
        try:
            key = reg.OpenKey(hive, sub)
        except OSError:
            continue
        i = 0
        while i < 4000:
            try:
                exe = reg.EnumKey(key, i)
            except OSError:
                break
            i += 1
            if not exe.lower().endswith(".exe"):
                continue
            try:
                with reg.OpenKey(key, exe) as k:
                    value, kind = reg.QueryValueEx(k, "")
            except OSError:
                continue
            if not isinstance(value, str):
                continue
            if kind == getattr(reg, "REG_EXPAND_SZ", -1):
                value = reg.ExpandEnvironmentStrings(value)
            value = value.strip().strip('"').strip()
            if os.path.isabs(value) and value.lower().endswith(".exe") and os.path.isfile(value):
                out.append({"name": exe[:-4], "path": value})
        try:
            reg.CloseKey(key)
        except Exception:  # noqa: BLE001
            pass
    return out


def _read_desktop(path: str) -> dict:
    """``{"name", "exec", "hidden"}`` from a Linux ``.desktop`` file."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read(65536)
    except OSError:
        return {}
    out: dict = {}
    section = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            section = line
            continue
        if section != "[Desktop Entry]" or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key == "Name":
            out["name"] = value.strip()
        elif key == "Exec":
            out["exec"] = value.strip()
        elif key == "Type":
            out["type"] = value.strip()
        elif key in ("NoDisplay", "Hidden") and value.strip().lower() == "true":
            out["hidden"] = True
    return out


def _linux_dirs() -> list[str]:
    home = os.path.expanduser("~")
    return ["/usr/share/applications", "/usr/local/share/applications",
            os.path.join(home, ".local", "share", "applications"),
            "/var/lib/flatpak/exports/share/applications",
            "/var/lib/snapd/desktop/applications"]


def _linux() -> list[dict]:
    out = []
    for d in _linux_dirs():
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for f in names:
            if not f.endswith(".desktop"):
                continue
            p = os.path.join(d, f)
            e = _read_desktop(p)
            if e.get("name") and e.get("exec") and not e.get("hidden") \
                    and e.get("type", "Application") == "Application":
                out.append({"name": e["name"], "path": p})
    return out


def _mac_dirs() -> list[str]:
    return ["/Applications", os.path.join(os.path.expanduser("~"), "Applications")]


def _mac() -> list[dict]:
    out = []
    for d in _mac_dirs():
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        out += [{"name": n[:-4], "path": os.path.join(d, n)} for n in names
                if n.endswith(".app")]
    return out


def installed_desktop(*, finders: Optional[list[Callable[[], list[dict]]]] = None) -> list[dict]:
    """Desktop programs installed on this computer, to pick from: ``[{"name",
    "path"}]``, one per program, sorted by name, at most :data:`DESKTOP_MAX`.
    Read with her own rights only (no administrator)."""
    if finders is None:
        if _IS_WINDOWS:
            finders = [_start_menu, _app_paths]
        elif sys.platform == "darwin":
            finders = [_mac]
        else:
            finders = [_linux]
    got: list[dict] = []
    for find in finders:
        try:
            got += list(find() or [])
        except Exception as exc:  # noqa: BLE001 - one place never hides the others
            logger.debug("programs: %s (%s)", getattr(find, "__name__", "?"),
                         type(exc).__name__)
    out, seen = [], set()
    for g in got:
        key = os.path.normcase(str(g.get("path") or ""))
        if not key or key in seen or not g.get("name"):
            continue
        seen.add(key)
        out.append({"name": str(g["name"])[:120], "path": str(g["path"])})
    out.sort(key=lambda r: r["name"].lower())
    return out[:DESKTOP_MAX]


__all__ = ["ALWAYS_ALLOW_WARNING", "KINDS", "ProgramError", "always_allowed", "cli_commands",
           "file_refusal", "find_on_path", "get", "installed_desktop", "launch", "match",
           "open_file", "read_help", "register", "register_tool", "registered_paths",
           "remove", "rows", "run_cli", "set_always_allow"]
