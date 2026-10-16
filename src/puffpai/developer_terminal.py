"""The Developer page's command line: a real shell on this computer.

A full command line, no question before each
command. PowerShell on Windows, bash (or sh) elsewhere, one process per
session, so the folder and variables carry over from one command to the
next. It starts in the bot's install folder with this bot's ``puffpai`` first
on the path (the command bin, :func:`puffpai.brain_commands.shim_dir`).

What it is NOT: the chat's ``run_command``. That keeps asking "May I run
this command?" before every command; nothing here changes it, and nothing
here can answer one of its questions.

The gates (no nagging, but fail closed):

* every route needs the bot's own API key (never the voice key) AND a paired
  device; the dashboard reaches them only through its paired proxy, which
  refuses requests another site (or another local port) starts;
* a session belongs to the device that opened it: another device gets
  "no such command line", the same as a wrong id;
* at most :data:`MAX_SESSIONS` at once (:data:`MAX_PER_DEVICE` per device,
  the oldest of that device's is ended for a new one);
* a session with no command typed and nothing printed for
  :data:`IDLE_SECONDS` is ended; ending one kills the shell and everything it
  started; all of them end when the bot stops;
* off while the owner has commands switched off (``PUFFPAI_TOOL_LOOP=off``,
  the chat's own switch);
* one audit line per command typed: the command, with known secrets and
  anything after a secret option or a secret-looking name hidden, checked for
  keys, never what it printed. Text typed while a command runs (an answer to
  its prompt, maybe a password) is logged as "typed", without the text.

Routes (all POST but the first, so no session id is ever in an address)::

    GET  /developer/terminal            the shell's name and the panel's lines
    POST /developer/terminal/open       {} -> {id, cwd, shell}
    POST /developer/terminal/input      {id, line}
    POST /developer/terminal/output     {id, since} -> {text, next, busy, alive, cwd}
    POST /developer/terminal/interrupt  {id}   (Ctrl+C)
    POST /developer/terminal/close      {id}

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import atexit
import codecs
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Optional

from flask import Blueprint, jsonify, request

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

bp = Blueprint("developer_terminal", __name__)

IDLE_SECONDS = 30 * 60
MAX_SESSIONS = 4
MAX_PER_DEVICE = 2
MAX_LINE = 8000
MAX_KEEP = 256_000          # characters of output kept per session
MAX_READ = 64_000           # characters handed back per read
WAIT_SECONDS = 1.5          # how long a read waits for new output
REAP_EVERY = 30
#: Starts the line the shell prints after each command: exit code, folder.
MARK = "\x1ePUFFDONE "
_OFF = ("0", "off", "false", "no")

TILE_LINE = ("A full command line on this computer. What you run here is not "
             "checked by the Bridge.")
NOT_PAIRED = "Pair this device first. The command line only opens on a paired device."
COMMANDS_OFF = ("Running commands is switched off on this bot, the same as in the chat. "
                "Nothing was run.")
TOO_MANY = ("There are already {n} command lines open on this bot. Close one, "
            "or wait until one ends by itself.")
GONE = "That command line has ended. Press Enter to start a new one."
NO_SHELL = "This computer has no shell the command line can start."
STOPPED_RESTARTED = ("Stopped. The command line started again in {cwd}. "
                     "Anything you set before is gone.")
ENDED = "The command line ended."
#: The examples, in this order, when the Use CLI list has them.
EXAMPLE_ORDER = ("where", "vault status", "keys list", "outside list",
                 "agents status", "devices list", "outside log --hours 24")
#: Commands that ask questions as they run, or start or stop the bot itself:
#: never offered as an example (the page has no keyboard for a PIN prompt).
NEEDS_A_WINDOW = frozenset({"setup", "chat", "ui", "start", "stop", "restart"})


# -- audit -----------------------------------------------------------------------

_SECRET_OPTION_RE = re.compile(
    r"(?i)(--(?:vault-pin|vault-password|password|passw\w*|value|ha-token)\b)"
    r"(\s*=\s*|\s+)(\"[^\"]*\"|'[^']*'|\S+)")
_SECRET_NAME_RE = re.compile(
    r"(?i)([\w$:.-]*(?:key|token|secret|password|passwd|pin|credential|auth)\w*)"
    r"(\s*=\s*)(\"[^\"]*\"|'[^']*'|\S+)")
HIDDEN = "[hidden]"


def safe_command(text: str) -> str:
    """*text* as the audit log may keep it: known secrets, the value after a
    secret option (``--vault-pin 1234``) and after a secret-looking name
    (``HA_TOKEN=...``, ``$env:API_KEY = '...'``) hidden, then checked for keys."""
    try:
        from puffpai import brain_commands
        text = brain_commands.hide_secrets(str(text))
    except Exception:  # noqa: BLE001 - the regexes below still run
        text = str(text)
    text = _SECRET_OPTION_RE.sub(lambda m: m.group(1) + m.group(2) + HIDDEN, text)
    text = _SECRET_NAME_RE.sub(lambda m: m.group(1) + m.group(2) + HIDDEN, text)
    try:
        from puffpai.web_search import key_safe_text
        return key_safe_text(text, 300)
    except Exception:  # noqa: BLE001
        return HIDDEN


def device_label(device: str) -> str:
    """Which device, for the audit log: its paired name and a short tag
    (:func:`puffpai.security.paired_devices.label`). The raw id is a 32-hex
    session value, which the log's key check rightly hides, so logging it
    lost the device altogether."""
    try:
        from puffpai.security import paired_devices
        return paired_devices.label(device)
    except Exception:  # noqa: BLE001 - the audit line must never break a call
        return "a device that could not be named"


def _audit(event: str, result: str, device: str, details: str = "") -> None:
    """One line in the audit log: what was typed (made safe), never output."""
    try:
        from puffpai.security.audit_log import AuditLog
        AuditLog().log(action=f"developer_terminal_{event}", user="owner",
                       result=result, details=details,
                       extra={"device": device_label(device)})
    except Exception as exc:  # the audit line must never break a call
        logger.debug("developer terminal audit skipped: %s", type(exc).__name__)


# -- who asks --------------------------------------------------------------------

def commands_off() -> bool:
    """Has the owner switched commands off (the chat's ``PUFFPAI_TOOL_LOOP``)?"""
    return str(os.environ.get("PUFFPAI_TOOL_LOOP", "on")).strip().lower() in _OFF


def _device() -> str:
    """The paired device asking, on a request with the bot's own key (never
    the voice key), or ''."""
    from puffpai import api_server, outside_api
    try:
        if api_server._app_caller_class() != "owner":  # noqa: SLF001
            return ""
    except Exception:  # noqa: BLE001 - not knowing who asks: nothing opens
        return ""
    return outside_api.paired_device_id()


# -- the shell -------------------------------------------------------------------

def shell_argv() -> Optional[list[str]]:
    """How the shell starts, by full path (never looked up on the path)."""
    if os.name == "nt":
        from puffpai.security import host_programs
        ps = host_programs.path("powershell")
        if ps and os.path.isfile(ps):
            return [ps, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "-"]
        return None
    for path in ("/bin/bash", "/usr/bin/bash"):
        if os.path.isfile(path):
            return [path, "--noprofile", "--norc", "-s"]
    return ["/bin/sh", "-s"] if os.path.isfile("/bin/sh") else None


def shell_name() -> str:
    argv = shell_argv() or [""]
    base = os.path.basename(argv[0]).lower()
    return {"powershell.exe": "PowerShell", "bash": "bash", "sh": "sh"}.get(base, "no shell")


def _preamble() -> str:
    if os.name == "nt":
        return ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
                "$OutputEncoding = [Text.Encoding]::UTF8; "
                "$ProgressPreference = 'SilentlyContinue'\n")
    # Ctrl+C stops the command, not the shell: a trap (unlike an ignored
    # signal) is reset in every command the shell starts.
    return "trap ':' INT\n"


def done_line() -> str:
    """What the shell runs after each command: prints MARK, code, folder."""
    if os.name == "nt":
        return ("$__puffOk = $?; $__puffCode = $LASTEXITCODE; "
                "$__puffC = if ($__puffOk) { 0 } elseif ($__puffCode) { $__puffCode } else { 1 }; "
                "[Console]::Out.WriteLine([string][char]30 + 'PUFFDONE ' + $__puffC + ' ' "
                "+ $PWD.ProviderPath); [Console]::Out.Flush()\n")
    return "__puff_s=$?; printf '\\036PUFFDONE %s %s\\n' \"$__puff_s\" \"$PWD\"\n"


def install_folder() -> str:
    """The bot's install folder: the checkout the CLI reports, else the
    folder above the package."""
    try:
        from puffpai.config import get_config
        root = str(get_config().REPO_ROOT or "")
        if root and os.path.isdir(root):
            return os.path.abspath(root)
    except Exception:  # noqa: BLE001
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


def shell_env() -> dict[str, str]:
    """This computer's environment without anything whose name says key,
    token, secret, password or PIN (the bot's own key is never handed to the
    shell), with this bot's ``puffpai`` first on the path and no pagers."""
    from puffpai import brain_commands
    env = brain_commands.clean_env()
    path_key = next((k for k in env if k.upper() == "PATH"), "PATH")
    env[path_key] = os.pathsep.join([brain_commands.shim_dir(), env.get(path_key, "")])
    env.update({"PYTHONIOENCODING": "utf-8", "TERM": "dumb", "NO_COLOR": "1",
                "PAGER": "cat", "GIT_PAGER": "cat"})
    return env


def _spawn(cwd: str, env: Optional[dict] = None) -> subprocess.Popen:
    argv = shell_argv()
    if not argv:
        raise RuntimeError(NO_SHELL)
    kw: dict[str, Any] = {}
    if os.name == "nt":
        kw["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP
                               | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        kw["start_new_session"] = True
    proc = subprocess.Popen(argv, cwd=cwd, env=env if env is not None else shell_env(),
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            bufsize=0, **kw)
    assert proc.stdin is not None
    proc.stdin.write(_preamble().encode("utf-8"))
    proc.stdin.flush()
    return proc


def kill_tree(proc: Optional[subprocess.Popen]) -> None:
    """End the shell and everything it started."""
    if proc is None or proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            from puffpai.security import host_programs
            tk = host_programs.path("taskkill")
            if tk:
                subprocess.run([tk, "/T", "/F", "/PID", str(proc.pid)],
                               capture_output=True, timeout=15, check=False)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception as exc:  # noqa: BLE001 - the backstop below still runs
        logger.debug("developer terminal tree kill: %s", type(exc).__name__)
    try:
        proc.kill()
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        pass
    # Closed off this thread: on Windows closing a pipe another thread reads
    # waits until every process holding its other end is gone (a dev server
    # the tree kill could not find), which would hold the caller forever.
    threading.Thread(target=_close_pipes, args=(proc,), daemon=True,
                     name="devterm-close").start()


def _close_pipes(proc: subprocess.Popen) -> None:
    for pipe in (proc.stdin, proc.stdout):
        try:
            if pipe:
                pipe.close()
        except Exception:  # noqa: BLE001
            pass


# -- sessions --------------------------------------------------------------------

class Session:
    def __init__(self, device: str, cwd: str) -> None:
        self.id = secrets.token_urlsafe(18)
        self.device = device
        self.cwd = cwd
        self.text = ""
        self.base = 0                       # characters dropped from the front
        self.busy = False
        self.ended = ""                     # why it ended, once it has
        self.last = time.monotonic()
        self.cond = threading.Condition()
        self.proc: Optional[subprocess.Popen] = None
        self._start()

    # output ---------------------------------------------------------------------
    def _add(self, text: str) -> None:
        with self.cond:
            self.text += text
            if len(self.text) > MAX_KEEP:
                cut = len(self.text) - MAX_KEEP
                self.text = self.text[cut:]
                self.base += cut
            self.last = time.monotonic()
            self.cond.notify_all()

    def _line(self, line: str) -> None:
        if MARK in line:
            before, after = line.split(MARK, 1)
            if before:
                self._add(before + "\n")
            code, _, cwd = after.strip().partition(" ")
            with self.cond:
                self.busy = False
                if cwd:
                    self.cwd = cwd
                self.cond.notify_all()
            if code not in ("", "0", "True"):
                self._add(f"(exit code {code})\n")
            return
        self._add(line + "\n")

    def _read(self, proc: subprocess.Popen) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        pending = ""
        out = proc.stdout
        while True:
            try:
                chunk = out.read(4096) if out else b""
            except Exception:  # noqa: BLE001 - a closed pipe ends the session
                chunk = b""
            if not chunk:
                break
            pending += decoder.decode(chunk).replace("\r\n", "\n")
            while "\n" in pending:
                line, pending = pending.split("\n", 1)
                self._line(line.rstrip("\r"))
            if pending and "\x1e" not in pending:
                self._add(pending)          # a prompt with no newline yet
                pending = ""
        if pending:
            self._line(pending)
        if proc is self.proc:               # not one replaced by a restart
            with self.cond:
                self.busy = False
                if not self.ended:
                    self.ended = ENDED
                self.cond.notify_all()

    def _start(self) -> None:
        self.proc = _spawn(self.cwd)
        threading.Thread(target=self._read, args=(self.proc,), daemon=True,
                         name=f"devterm-{self.id[:6]}").start()

    # input ----------------------------------------------------------------------
    def send(self, line: str) -> bool:
        """Write *line*. A new command when the shell is waiting (then the
        done line follows); text for the running command otherwise. Returns
        True when it was a new command."""
        with self.cond:
            new = not self.busy
            if new:
                self.busy = True
            self.last = time.monotonic()
        data = line + "\n" + (done_line() if new else "")
        assert self.proc is not None and self.proc.stdin is not None
        self.proc.stdin.write(data.encode("utf-8"))
        self.proc.stdin.flush()
        return new

    def interrupt(self) -> None:
        """Ctrl+C: stop the running command."""
        self.last = time.monotonic()
        if os.name != "nt":
            try:
                os.killpg(self.proc.pid, signal.SIGINT)  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                pass
            return
        # Windows has no Ctrl+C for a shell on pipes: end the tree, start
        # again in the same folder, and say so plainly.
        old = self.proc
        with self.cond:
            self.busy = False
        self._start()                       # the new shell first, so the old
        kill_tree(old)                      # one's end never ends the session
        self._add(STOPPED_RESTARTED.format(cwd=self.cwd) + "\n")

    def read(self, since: int, wait: float) -> dict:
        with self.cond:
            if since >= self.base + len(self.text) and not self.ended and wait > 0:
                self.cond.wait(timeout=wait)
            start = max(0, int(since) - self.base)
            cut = int(since) < self.base
            text = self.text[start:start + MAX_READ]
            nxt = self.base + start + len(text)
            return {"text": ("(earlier output was cut)\n" if cut and since > 0 else "") + text,
                    "next": nxt, "busy": self.busy, "alive": not self.ended,
                    "cwd": self.cwd, "ended": self.ended}

    def end(self, why: str = ENDED) -> None:
        with self.cond:
            if not self.ended:
                self.ended = why
            self.busy = False
            self.cond.notify_all()
        kill_tree(self.proc)

    def idle_for(self) -> float:
        return time.monotonic() - self.last


_lock = threading.Lock()
_sessions: dict[str, Session] = {}
_reaper: Optional[threading.Thread] = None


def reap() -> int:
    """End sessions idle too long, and forget ended ones. Returns how many
    were ended for being idle."""
    with _lock:
        rows = list(_sessions.items())
    n = 0
    for sid, s in rows:
        if s.ended:
            if s.idle_for() > 60:
                with _lock:
                    _sessions.pop(sid, None)
            continue
        if s.idle_for() > IDLE_SECONDS:
            s.end("The command line ended after being left alone for "
                  f"{IDLE_SECONDS // 60} minutes.")
            _audit("ended", "idle", s.device)
            with _lock:
                _sessions.pop(sid, None)
            n += 1
    return n


def _reap_loop() -> None:
    while True:
        time.sleep(REAP_EVERY)
        try:
            reap()
        except Exception as exc:  # noqa: BLE001
            logger.debug("developer terminal reap: %s", type(exc).__name__)


def close_all() -> None:
    """Every shell ends (the bot is stopping)."""
    with _lock:
        rows = list(_sessions.values())
        _sessions.clear()
    for s in rows:
        s.end()


atexit.register(close_all)


def _ensure_reaper() -> None:
    global _reaper
    with _lock:
        if _reaper is None or not _reaper.is_alive():
            _reaper = threading.Thread(target=_reap_loop, daemon=True,
                                       name="devterm-reaper")
            _reaper.start()


_open_lock = threading.Lock()


def open_session(device: str) -> Session:
    """A new shell for *device*. ValueError(TOO_MANY) when full."""
    with _open_lock:                        # one at a time, so the cap holds
        return _open_session(device)


def _open_session(device: str) -> Session:
    reap()
    with _lock:
        live = [s for s in _sessions.values() if not s.ended]
        mine = sorted((s for s in live if s.device == device), key=lambda s: s.last)
        drop = mine[:max(0, len(mine) - MAX_PER_DEVICE + 1)]
        if len(live) - len(drop) >= MAX_SESSIONS:
            raise ValueError(TOO_MANY.format(n=MAX_SESSIONS))
        for s in drop:
            _sessions.pop(s.id, None)
    for s in drop:
        s.end()
        _audit("closed", "replaced", device)
    s = Session(device, install_folder())
    with _lock:
        _sessions[s.id] = s
    _ensure_reaper()
    return s


def find(sid: Any, device: str) -> Optional[Session]:
    """The session *sid* when *device* opened it, else None (another
    device's session looks exactly like one that does not exist)."""
    with _lock:
        s = _sessions.get(str(sid or ""))
    if s is None or not device or s.device != device:
        return None
    return s


def examples() -> list[dict[str, str]]:
    """Commands from the Use CLI list that run here as they are."""
    from puffpai import developer_api
    seen: dict[str, str] = {}
    for rows in developer_api.COMMANDS.values():
        for row in rows:
            words = " ".join(row["argv"])
            if row.get("asks") or "<" in words or words in seen \
                    or row["argv"][0] in NEEDS_A_WINDOW:
                continue
            seen[words] = row["what"]
    order = [w for w in EXAMPLE_ORDER if w in seen] + \
        [w for w in seen if w not in EXAMPLE_ORDER]
    return [{"command": f"puffpai {w}", "what": seen[w]} for w in order]


# -- routes ----------------------------------------------------------------------

def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _no_device():
    return jsonify({"ok": False, "code": "not_paired", "message": NOT_PAIRED}), 403


def _gone():
    return jsonify({"ok": False, "code": "gone", "message": GONE}), 404


@bp.route("/developer/terminal", methods=["GET"])
def terminal_page():
    if not _device():
        return _no_device()
    return jsonify({"ok": True, "shell": shell_name(), "line": TILE_LINE,
                    "off": commands_off(), "off_line": COMMANDS_OFF if commands_off() else "",
                    "examples": examples(),
                    "window_line": "Commands that ask for a PIN or a password need a "
                                   "terminal window of their own. Use CLI on the Vault, "
                                   "Brain or Bridge page shows how."})


@bp.route("/developer/terminal/open", methods=["POST"])
def terminal_open():
    device = _device()
    if not device:
        return _no_device()
    if commands_off():
        return jsonify({"ok": False, "code": "commands_off", "message": COMMANDS_OFF}), 403
    try:
        s = open_session(device)
    except ValueError as exc:
        return jsonify({"ok": False, "code": "too_many", "message": str(exc)}), 429
    except (OSError, RuntimeError) as exc:
        logger.info("developer terminal could not start: %s", type(exc).__name__)
        return jsonify({"ok": False, "message": NO_SHELL}), 503
    _audit("opened", "opened", device, shell_name())
    return jsonify({"ok": True, "id": s.id, "cwd": s.cwd, "shell": shell_name()})


@bp.route("/developer/terminal/input", methods=["POST"])
def terminal_input():
    device = _device()
    if not device:
        return _no_device()
    body = _body()
    s = find(body.get("id"), device)
    if s is None or s.ended:
        return _gone()
    if commands_off():
        return jsonify({"ok": False, "code": "commands_off", "message": COMMANDS_OFF}), 403
    line = body.get("line")
    if not isinstance(line, str) or len(line) > MAX_LINE or "\x00" in line:
        return jsonify({"ok": False, "message": "That line is too long. Nothing was sent."}), 400
    line = line.replace("\r\n", "\n").replace("\r", "\n")
    try:
        new = s.send(line)
    except (OSError, ValueError):
        s.end()
        return _gone()
    if new:
        _audit("command", "sent", device, safe_command(line))
    else:
        _audit("typed", "sent", device)     # never the text: it may be a password
    return jsonify({"ok": True})


@bp.route("/developer/terminal/output", methods=["POST"])
def terminal_output():
    device = _device()
    if not device:
        return _no_device()
    body = _body()
    s = find(body.get("id"), device)
    if s is None:
        return _gone()
    try:
        since = max(0, int(body.get("since") or 0))
    except (TypeError, ValueError):
        since = 0
    wait = WAIT_SECONDS if body.get("wait") is not False else 0.0
    return jsonify({"ok": True, **s.read(since, wait)})


@bp.route("/developer/terminal/interrupt", methods=["POST"])
def terminal_interrupt():
    device = _device()
    if not device:
        return _no_device()
    s = find(_body().get("id"), device)
    if s is None or s.ended:
        return _gone()
    try:
        s.interrupt()
    except (OSError, RuntimeError):
        s.end()
        return _gone()
    _audit("interrupt", "sent", device)
    return jsonify({"ok": True})


@bp.route("/developer/terminal/close", methods=["POST"])
def terminal_close():
    device = _device()
    if not device:
        return _no_device()
    s = find(_body().get("id"), device)
    if s is None:
        return _gone()
    with _lock:
        _sessions.pop(s.id, None)
    s.end()
    _audit("closed", "closed", device)
    return jsonify({"ok": True})


__all__ = ["COMMANDS_OFF", "IDLE_SECONDS", "MAX_PER_DEVICE", "MAX_SESSIONS", "Session",
           "TILE_LINE", "bp", "close_all", "commands_off", "done_line", "examples", "find",
           "install_folder", "kill_tree", "open_session", "reap", "safe_command",
           "shell_argv", "shell_env", "shell_name"]
