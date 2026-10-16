"""Every request BuildBot makes goes through a listed exit -- or the suite fails.

"What leaves your computer" says it shows every request BuildBot makes. That
is only true while every network connection in ``src/`` is opened by one of
the exits below, each of which writes the outside-traffic log and pins its
connection. Any other module that opens a connection fails this test, unless
the line says it is a home-network (LAN) destination with an inline
``# lan-egress: <why>`` marker -- ``lan_posture`` holds those to the run
setting, and they never reach the internet.

Things BuildBot cannot see are not exits and are named on the page instead:
Ollama's own model downloads, the book reader's reading-helper download, and
installing BuildBot itself.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "puffpai"

#: The listed exits: module -> why it may open connections.
EXITS = {
    "security/egress_doors.py": "every door, live card, card picture, town "
                                "lookup and sample look (_send, pinned, logged)",
    "security/pinned_net.py": "the pinned transport the exits above and below use",
    "source_fetch.py": "books, library searches and lookups (_open_chain, "
                       "pinned, logged per hop)",
    "wizard/netcheck.py": "the internet check (pinned, logged)",
    # two programs BuildBot starts that reach outside, each through
    # its own wall and logged (checked by test_the_new_exits_log_and_wall).
    "box/driver.py": "the locked box: wsl.exe by full path; the box's own "
                     "allowlist proxy, its lines re-checked by ingest_outside",
    "webapps/runner_host.py": "the hidden browser that drives added apps: "
                              "Edge or Chrome by full path behind WALL_FLAGS "
                              "and a refuse-all logging proxy",
    # Inside the locked box only: the
    # box's allowlisting proxy dials only the address it vetted and writes one
    # log line per attempt, which box.driver.ingest_outside re-checks; the
    # box launcher starts that proxy under bubblewrap with the network shared.
    "box/assets/egress_proxy.py": "the locked box's allowlisting proxy (runs only "
                                  "inside the box; dials the address it vetted, one "
                                  "log line per attempt, re-checked by "
                                  "box.driver.ingest_outside)",
    "box/assets/box_run.py": "the locked box's launcher (runs only inside the box): "
                             "starts the allowlisting proxy under bubblewrap; "
                             "opens no connection itself",
    # Tool servers she added. The mcp package opens
    # the connection; this module checks the server's own switch first and
    # writes one log line per use.
    "mcp_client.py": "tool servers she added (the mcp package's sessions, "
                     "each behind its own switch, one log line per use)",
    # An outside project she said yes
    # to runs on this computer as built. Its install steps (npm, pip, docker
    # by full path) run only while the code_download switch is on and write
    # one outside-traffic line per host; its free-port probe binds 127.0.0.1;
    # the brain's calls to the app go to 127.0.0.1 only. What the project
    # itself sends is NOT seen here: the yes card says it runs with her
    # permissions and can reach the internet by itself.
    "integrate/run_here.py": "apps she said yes to that run on this computer: "
                             "install steps behind the code_download switch, "
                             "logged; calls to the app on 127.0.0.1 only",
}

#: What each of the new exits must mention (the log, and its wall). Checked only
#: when the file is in this build; a file without them fails.
_EXIT_MENTIONS = {
    "box/driver.py": ("egress_ledger", "ingest_outside"),
    "webapps/runner_host.py": ("egress_ledger", "WALL_FLAGS"),
    "box/assets/egress_proxy.py": ("--allow", "--log"),
    "box/assets/box_run.py": ("egress_proxy.py", "--allow", "--log"),
    "mcp_client.py": ("egress_ledger", "outside_switches"),
    "integrate/run_here.py": ("egress_ledger", "outside_switches", "host_programs"),
}

#: Anything that opens a network connection, in any of the usual spellings.
_CONNECT_RE = re.compile(
    r"(?:^|[^A-Za-z0-9_.])_?requests\.(?:api\.|sessions\.)?(get|post|put|patch|delete|head|options|request|Session|session)\b"
    r"|\bhttpx\.(get|post|put|patch|delete|head|request|stream|Client|AsyncClient)\b"
    r"|\baiohttp\.(ClientSession|request)\b"
    r"|^\s*(import|from)\s+(httpx|aiohttp|websockets?|urllib3|pycurl)\b"
    r"|\burllib3\.(PoolManager|HTTPConnectionPool|HTTPSConnectionPool|request|connection_from_url)\b"
    r"|urllib\.request\.(urlopen|build_opener|OpenerDirector)"
    r"|\burlopen\("
    r"|http\.client\.HTTPS?Connection"
    r"|\bHTTPS?Connection\("
    r"|socket\.create_connection"
    r"|socket\.socket\("
    r"|\.connect\(\(|\.connect_ex\("
    r"|asyncio\.open_connection"
    r"|\bwebsockets?\.(connect|create_connection)"
    r"|ftplib\.|smtplib\.|telnetlib\.|poplib\.|imaplib\."
    r"|ssl\.(create_connection|wrap_socket)"
)

_LAN_MARKER = "# lan-egress:"


def _offenders() -> list[str]:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel in EXITS or rel.startswith("vendor/"):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0] if _LAN_MARKER not in line else ""
            stripped = code.strip()
            if not stripped or stripped.startswith(("\"", "'", ":", "*", "``")):
                continue
            if _CONNECT_RE.search(code):
                found.append(f"{rel}:{number}: {line.strip()}")
    return found


#: Modules whose calls can open a connection. An alias of one of them
#: (``import requests as r``, ``from requests import get``) is read back to
#: its real name before the spellings above are matched.
_NET_MODULES = ("requests", "httpx", "aiohttp", "urllib3", "websocket",
                "websockets", "pycurl", "socket", "ssl", "http.client",
                "urllib.request", "ftplib", "smtplib", "telnetlib", "poplib",
                "imaplib")

#: Programs that fetch from the internet (or run code that may) when a
#: module starts them. Also the ones BuildBot now starts on purpose:
#: only a listed exit may, and only through ``security.host_programs.path``.
#: taskkill and tasklist reach nothing, but BuildBot starts them, so a planted
#: copy found by name would run with its rights (M11): they count too.
_NET_PROGRAM_NAMES = (
    r"curl|wget|invoke-webrequest|invoke-restmethod|iwr|irm|bitsadmin|certutil"
    r"|ncat|telnet|ftp|sftp|scp|ssh|rsync"
    r"|wsl|git|npm|npx|pip3|pip|node|docker|msedge|chrome|bwrap|taskkill|tasklist|powershell")
_NET_PROGRAMS = re.compile(
    r"(?i)(?:^|[\s/\\\"'])(?:" + _NET_PROGRAM_NAMES + r")(?:\.exe|\.cmd)?(?:$|[\s\"'])")
#: One program name on its own (a path's last part, quotes and ``.exe`` off).
_NET_PROGRAM_NAME = re.compile(
    r"(?i)^(?:" + _NET_PROGRAM_NAMES + r")(?:\.exe|\.cmd)?$")

#: Windows network libraries reached through ctypes.
_NET_DLLS = re.compile(r"(?i)(?:^|[^a-z0-9])(?:ws2_32|wininet|winhttp|wsock32)(?:$|[^a-z0-9])")


def _is_net_module(name: str) -> bool:
    return any(name == m or name.startswith(m + ".") for m in _NET_MODULES)


def _dotted(node: ast.AST) -> str:
    """``a.b.c`` for a call's function; a call in the chain reads ``f()``."""
    parts = []
    while True:
        if isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        elif isinstance(node, ast.Call):
            parts.append("()")
            node = node.func
        elif isinstance(node, ast.Name):
            parts.append(node.id)
            break
        else:
            return ""
    out = ""
    for part in reversed(parts):
        out = (out + "()") if part == "()" else (f"{out}.{part}" if out else part)
    return out


def _strings(node: ast.AST) -> list[str]:
    return [n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _ast_offenders_in(rel: str, text: str) -> list[str]:
    """What a spelling scan cannot see: aliases, dynamic imports, programs
    that fetch, ctypes network libraries and a bare ``sock.connect(addr)``."""
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [f"{rel}: could not be read ({exc.msg})"]
    lines = text.splitlines()
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if _is_net_module(a.name):
                    aliases[a.asname or a.name.split(".")[0]] = (
                        a.name if a.asname else a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                full = f"{node.module}.{a.name}"
                if _is_net_module(node.module) or _is_net_module(full):
                    aliases[a.asname or a.name] = full
    found = []
    sockets = _socket_names(tree, aliases)
    launchers = _launcher_aliases(tree)

    def flag(node: ast.AST, why: str) -> None:
        number = getattr(node, "lineno", 0)
        line = lines[number - 1] if 0 < number <= len(lines) else ""
        if _LAN_MARKER in line:
            return
        found.append(f"{rel}:{number}: {why}: {line.strip()}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and _NET_DLLS.search(node.attr):
            flag(node, "a Windows network library")
            continue
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func)
        if not name:
            continue
        root, _, rest = name.partition(".")
        head = root.split("(")[0]
        if head in aliases:
            real = aliases[head] + root[len(head):] + ("." + rest if rest else "")
            if _CONNECT_RE.search(" " + real + "("):
                flag(node, f"a connection ({real})")
                continue
        if name in ("__import__", "importlib.import_module", "import_module"):
            if any(_is_net_module(s) for s in _strings(node)):
                flag(node, "a network module imported by name")
            continue
        if (_real_name(name, launchers).startswith(_LAUNCHERS)
                and any(_NET_PROGRAMS.search(s) for s in _strings(node))):
            flag(node, "a program that fetches from the internet")
            continue
        if any(_NET_DLLS.search(s) for s in _strings(node)) and "ctypes" in name.lower():
            flag(node, "a Windows network library")
            continue
        if (name.endswith((".connect", ".connect_ex"))
                and name.rsplit(".", 1)[0] in sockets):
            flag(node, "a socket connect")
    return found


def _socket_names(tree: ast.AST, aliases: dict[str, str]) -> set[str]:
    """Names (and ``self.x`` attributes) a module binds to a socket."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.withitem)):
            continue
        value = node.context_expr if isinstance(node, ast.withitem) else node.value
        if not isinstance(value, ast.Call):
            continue
        name = _dotted(value.func)
        head = name.split(".")[0].split("(")[0]
        real = (aliases[head] + name[len(head):]) if head in aliases else name
        if not re.search(r"(?:^|\.)(?:socket\.socket|create_connection|wrap_socket|"
                         r"socketpair|fromfd)$", real):
            continue
        targets = ([node.optional_vars] if isinstance(node, ast.withitem)
                   else getattr(node, "targets", None) or [node.target])
        for target in targets:
            if target is not None and _dotted(target):
                out.add(_dotted(target))
    return out


#: Calls that start a program, by their real (alias-free) names.
_LAUNCHERS = ("subprocess.", "os.system", "os.popen", "os.exec", "os.spawn",
              "os.posix_spawn", "os.startfile", "asyncio.create_subprocess")
_LAUNCHER_MODULES = ("subprocess", "os", "asyncio")


def _launcher_aliases(tree: ast.AST) -> dict[str, str]:
    """How this module spells the launchers: ``import subprocess as sp`` ->
    ``{"sp": "subprocess"}``, ``from subprocess import run`` ->
    ``{"run": "subprocess.run"}``."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name in _LAUNCHER_MODULES and a.asname:
                    out[a.asname] = a.name
        elif isinstance(node, ast.ImportFrom) and node.module in _LAUNCHER_MODULES:
            for a in node.names:
                out[a.asname or a.name] = f"{node.module}.{a.name}"
    return out


def _real_name(name: str, aliases: dict[str, str]) -> str:
    """*name* with its first part read back through *aliases*."""
    head = name.split(".", 1)[0].split("(", 1)[0]
    return aliases[head] + name[len(head):] if head in aliases else name


def _program_word(text: str) -> str:
    """The program a command string starts: its first word's last path part."""
    first = text.strip().split()[0] if text.strip() else ""
    first = first.strip("\"'")
    return re.split(r"[\\/]", first)[-1]


def _which_names(tree: ast.AST) -> set[str]:
    """How this module spells ``shutil.which`` (``from shutil import which``)."""
    names = {"shutil.which"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "shutil":
            for a in node.names:
                if a.name == "which":
                    names.add(a.asname or "which")
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "shutil" and a.asname:
                    names.add(f"{a.asname}.which")
    return names


def _is_getattr_which(func: ast.AST, which: set[str]) -> bool:
    """``getattr(shutil, "which")`` (or through an alias of shutil)."""
    modules = {n[:-len(".which")] for n in which if n.endswith(".which")}
    return (isinstance(func, ast.Call) and _dotted(func.func) == "getattr"
            and len(func.args) >= 2 and _dotted(func.args[0]) in modules
            and isinstance(func.args[1], ast.Constant) and func.args[1].value == "which")


def _is_which(node: ast.AST, which: set[str]) -> bool:
    return isinstance(node, ast.Call) and (
        _dotted(node.func) in which or _is_getattr_which(node.func, which))


#: Call names that run what they are given (a wrapper around a launcher):
#: a command STRING handed to one of these counts as a launch. The LAST word
#: of the call's name decides (``run``, ``_spawn``, ``self._launch``,
#: ``check_output``), so a lookup such as ``_system_program("taskkill")`` is
#: not a run.
_RUNS_WORDS = re.compile(r"(?i)(?:^|[._])(?:run|spawn|call|popen|exec|execute|start|"
                         r"launch|system|shell|output)$")


def _argv_program(value: ast.AST, named_argv: dict[str, str],
                  named_lists: dict[str, str], depth: int = 0) -> str:
    """The network program an argv-shaped expression starts with, or ''.

    A list or tuple whose first item is a bare name (a literal, or a name
    bound to one), star-unpacks a list that does, ``list(...)`` / ``tuple(...)``
    of one, ``[...] + rest`` (the left side decides), or a name bound to any
    of these in this module."""
    if depth > 6:
        return ""
    again = lambda v: _argv_program(v, named_argv, named_lists, depth + 1)  # noqa: E731
    if isinstance(value, (ast.List, ast.Tuple)):
        if not value.elts:
            return ""
        first = value.elts[0]
        if isinstance(first, ast.Starred):
            return again(first.value)
        literal = _net_literal(first)
        if literal:
            return literal
        if isinstance(first, (ast.Name, ast.Attribute)):
            return named_lists.get(_dotted(first), "")
        return ""
    if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add):
        return again(value.left)
    if isinstance(value, ast.Call) and _dotted(value.func) in ("list", "tuple") and value.args:
        return again(value.args[0])
    if isinstance(value, (ast.Name, ast.Attribute)):
        return named_argv.get(_dotted(value), "")
    return ""


def _net_literal(node: ast.AST) -> str:
    """The network program a literal (or an f-string's start) names, or ''."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        word = _program_word(node.value)
    elif isinstance(node, ast.JoinedStr) and node.values \
            and isinstance(node.values[0], ast.Constant):
        word = _program_word(str(node.values[0].value))
    else:
        return ""
    return word if _NET_PROGRAM_NAME.match(word) else ""


def _launch_sites_in(rel: str, text: str) -> list[dict[str, object]]:
    """The program rule, in EVERY module (listed exits too): a network
    program is started only by its full path from ``host_programs.path``,
    never by a bare name, never through ``shutil.which`` and never as
    ``python -m <program>``, and ``shutil.which`` is never used to look one
    up. Launchers are read back through their aliases (``import subprocess
    as sp``, ``from subprocess import run``), ``functools.partial`` of a
    launcher is read as the launcher, and ``getattr(shutil, "which")`` as
    ``shutil.which``. A program list that starts with a network program's
    bare name and is handed to ANY other call, in any argument (a wrapper
    such as ``_spawn(["node", ...])``, ``helper(5, [...])``, an injected
    ``run`` parameter, ``program=[...]``, a name holding the program,
    ``[*head, ...]``, ``["node"] + rest``, ``list((...))``) is an offence too:
    the wrapper starts it by that name. A command STRING is an offence when
    handed to a call whose name says it runs things (``run``, ``spawn``,
    ``start``, ``exec`` ...).

    Not covered (a static scan cannot follow them): a program name built at
    run time (joined, read from a file or the environment), a wrapper whose
    name does not say it runs things given a command STRING, and a launcher
    reached through ``getattr`` on a name that is not a plain module. One
    entry per offending call:
    ``{"rel", "line", "why", "first" (its first source line), "call" (the
    whole call's source, spaces collapsed)}``."""
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [{"rel": rel, "line": 0, "why": f"could not be read ({exc.msg})",
                 "first": "", "call": ""}]
    lines = text.splitlines()
    which = _which_names(tree)
    launchers = _launcher_aliases(tree)
    from_which: set[str] = set()
    named_lists: dict[str, str] = {}
    named_argv: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        targets = getattr(node, "targets", None) or [node.target]
        value = node.value
        parts = value.values if isinstance(value, ast.BoolOp) else [value]
        found_which = any(_is_which(v, which) for v in parts)
        first = (value.elts[0] if isinstance(value, (ast.List, ast.Tuple))
                 and value.elts else value)
        literal = _net_literal(first)
        for target in targets:
            name = _dotted(target)
            if not name:
                continue
            if found_which:
                from_which.add(name)
            if literal:
                named_lists[name] = literal
                if isinstance(value, (ast.List, ast.Tuple)):
                    named_argv[name] = literal
    for _round in range(3):          # chains: head = [...]; argv = head + rest
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                continue
            got = _argv_program(node.value, named_argv, named_lists)
            if not got:
                continue
            for target in getattr(node, "targets", None) or [node.target]:
                name = _dotted(target)
                if name:
                    named_argv.setdefault(name, got)
    partials = {"functools.partial"} | {
        a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
        and n.module == "functools" for a in n.names if a.name == "partial"}
    found: list[dict[str, object]] = []

    def flag(node: ast.AST, why: str) -> None:
        number = getattr(node, "lineno", 0)
        line = lines[number - 1] if 0 < number <= len(lines) else ""
        call = " ".join((ast.get_source_segment(text, node) or "").split())
        found.append({"rel": rel, "line": number, "why": why,
                      "first": line.strip(), "call": call})

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func)
        if name in which or _is_getattr_which(node.func, which):
            if any(_NET_PROGRAM_NAME.match(_program_word(s)) for s in _strings(node)):
                flag(node, "a network program looked up through shutil.which")
            continue
        if name in partials and node.args and \
                _real_name(_dotted(node.args[0]), launchers).startswith(_LAUNCHERS):
            # functools.partial(subprocess.run, [...]): the launcher's own
            # arguments follow it.
            node = ast.Call(func=node.args[0], args=node.args[1:], keywords=node.keywords,
                            lineno=node.lineno, col_offset=node.col_offset,
                            end_lineno=getattr(node, "end_lineno", None),
                            end_col_offset=getattr(node, "end_col_offset", None))
            name = _dotted(node.func)
        if not _real_name(name, launchers).startswith(_LAUNCHERS):
            handed = _handed_program(node, named_argv, named_lists)
            if handed:
                flag(node, f"{handed} handed to {name or 'a call'} by a bare name")
            continue
        programs = []
        argv: list[ast.AST] = []
        if node.args:
            first = node.args[0]
            if isinstance(first, (ast.List, ast.Tuple)):
                argv = list(first.elts)
            programs.append(first.elts[0] if isinstance(first, (ast.List, ast.Tuple))
                            and first.elts else first)
        for kw in node.keywords:
            if kw.arg in ("args", "executable") and kw.value is not None:
                v = kw.value
                if kw.arg == "args" and isinstance(v, (ast.List, ast.Tuple)):
                    argv = list(v.elts)
                programs.append(v.elts[0] if isinstance(v, (ast.List, ast.Tuple))
                                and v.elts else v)
        for prog in programs:
            literal = _net_literal(prog)
            dotted = _dotted(prog) if isinstance(prog, (ast.Name, ast.Attribute)) else ""
            if literal:
                flag(node, f"{literal} started by a bare name")
            elif _is_which(prog, which) or (dotted and dotted in from_which):
                flag(node, "a program started through shutil.which")
            elif dotted and dotted in named_lists:
                flag(node, f"{named_lists[dotted]} started by a bare name")
            elif isinstance(prog, ast.Starred):
                got = _argv_program(prog.value, named_argv, named_lists)
                if got:
                    flag(node, f"{got} started by a bare name")
        for value in list(node.args[:1]) + [kw.value for kw in node.keywords
                                            if kw.arg == "args"]:
            if isinstance(value, (ast.BinOp, ast.Call)) or (
                    isinstance(value, (ast.Name, ast.Attribute))
                    and _dotted(value) in named_argv and _dotted(value) not in named_lists):
                got = _argv_program(value, named_argv, named_lists)
                if got:
                    flag(node, f"{got} started by a bare name")
        for flag_arg, module in zip(argv, argv[1:]):
            if (isinstance(flag_arg, ast.Constant) and flag_arg.value == "-m"
                    and _net_literal(module)):
                flag(node, f"{_net_literal(module)} started as python -m")
                break
    return found


def _handed_program(call: ast.Call, named_argv: dict[str, str],
                    named_lists: dict[str, str] | None = None) -> str:
    """The network program a call hands on by a bare name, or ''.

    ANY positional argument or keyword that is argv-shaped (see
    :func:`_argv_program`: a list or tuple starting with a bare name, or with
    a name bound to one, star-unpacked, concatenated, ``list(...)``, or a name
    bound to any of these). And when the call's own name says it runs things
    (:data:`_RUNS_WORDS`: ``run``, ``_spawn``, ``self._launch`` ...), a
    command STRING that starts with one, or a name bound to one."""
    named_lists = named_lists or {}
    values = list(call.args) + [kw.value for kw in call.keywords]
    for value in values:
        got = _argv_program(value, named_argv, named_lists)
        if got:
            return got
    if _RUNS_WORDS.search(_dotted(call.func) or ""):
        for value in values:
            literal = _net_literal(value)
            if literal:
                return literal
            if isinstance(value, (ast.Name, ast.Attribute)) \
                    and _dotted(value) in named_lists:
                return named_lists[_dotted(value)]
    return ""


def _launch_offenders_in(rel: str, text: str) -> list[str]:
    """``rel:line: why: first source line`` for each call that breaks the
    program rule (see :func:`_launch_sites_in`)."""
    return [f"{s['rel']}:{s['line']}: {s['why']}: {s['first']}"
            for s in _launch_sites_in(rel, text)]


def _launch_sites() -> list[dict[str, object]]:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("vendor/"):
            continue
        found.extend(_launch_sites_in(rel, path.read_text(encoding="utf-8")))
    return found


def _launch_offenders() -> list[str]:
    return [f"{s['rel']}:{s['line']}: {s['why']}: {s['first']}"
            for s in _launch_sites()]


def _ast_offenders() -> list[str]:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel in EXITS or rel.startswith("vendor/"):
            continue
        found.extend(_ast_offenders_in(rel, path.read_text(encoding="utf-8")))
    return found


def test_no_module_opens_a_connection_outside_the_listed_exits() -> None:
    offenders = _offenders() + _ast_offenders()
    assert not offenders, (
        "a network connection outside the listed exits -- route it through "
        "egress_doors.egress_get (or source_fetch), or mark a home-network "
        "call '# lan-egress: <why>':\n  " + "\n  ".join(offenders))


def test_network_programs_start_only_by_full_path() -> None:
    """Anywhere in src, listed exits included, a network
    program starts only from ``host_programs.path`` (full path, fixed working
    folder): never a bare name, never ``shutil.which``."""
    offenders = _launch_offenders()
    assert not offenders, (
        "start network programs through security.host_programs.path:\n  "
        + "\n  ".join(offenders))


def test_the_rule_excuses_no_launch_at_all() -> None:
    """The pass-through list for start_cli.py
    and avatar_generator.py is gone, and nothing replaced it. Every launch
    the rule finds fails the suite."""
    names = [n for n in globals() if "KNOWN_LAUNCH" in n.upper()
             or n in ("_excused", "_unexcused", "_known_launch_sites")]
    assert names == [], names


@pytest.mark.parametrize("rel", ["start_cli.py", "avatar_generator.py"])
def test_start_and_the_avatar_start_programs_only_by_full_path(rel) -> None:
    text = (SRC / rel).read_text(encoding="utf-8")
    assert _launch_offenders_in(rel, text) == []
    assert _ast_offenders_in(rel, text) == []
    assert "shutil.which" not in text.replace("never ``shutil.which``", "")
    assert "host_programs.path(" in text


@pytest.mark.parametrize("rel,extra", [
    # the validator's probes, now in files with no excuses at all
    ("start_cli.py", "\ndef _probe():\n"
                     "    subprocess.run(['taskkill', '/IM', 'msedge.exe', '/F'])\n"),
    ("start_cli.py", "\ndef _probe():\n    return shutil.which('git')\n"),
    ("avatar_generator.py", "\ndef _probe(x):\n    subprocess.run(['node', x])\n"),
    ("start_cli.py", '\ndef _probe():\n    return shutil.which("node")\n'),
    # the old spellings, put back
    ("start_cli.py", "\ndef _probe(pid):\n"
                     '    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], '
                     "capture_output=True, check=False)\n"),
    ("start_cli.py", "\ndef _probe(pid):\n"
                     '    subprocess.run(["tasklist", "/FI", f"PID eq {pid}"])\n'),
    # a wrapper or an injected run parameter hides the launcher
    ("start_cli.py", "\ndef _probe(dash, env, log):\n"
                     "    _spawn(['node', 'x.js', 'start'], dash, env, log)\n"),
    ("start_cli.py", "\ndef _probe(run):\n    run(['node', 'x.js'], cwd='.')\n"),
    ("start_cli.py", "\ndef _probe(run):\n    run(['npm.cmd', 'ci'])\n"),
])
def test_a_new_bare_launch_in_start_or_the_avatar_fails(rel, extra) -> None:
    text = (SRC / rel).read_text(encoding="utf-8") + extra
    assert _launch_offenders_in(rel, text), extra


@pytest.mark.parametrize("text", [
    "def f(run):\n    run(['node', 'x.js'])\n",
    "def f(helper):\n    helper(args=['git', 'status'])\n",
    "def f(helper):\n    cmd = ['docker', 'run', 'x']\n    helper(cmd)\n",
    "def f(self):\n    self._launch(('npx', 'x'))\n",
    "def f(pool):\n    pool.submit(command=['curl', 'https://x'])\n",
])
def test_a_program_list_handed_to_a_wrapper_is_caught(text) -> None:
    assert _launch_offenders_in("probe.py", text), text


def test_a_trusted_path_handed_to_a_wrapper_passes() -> None:
    text = ("from puffpai.security import host_programs\n"
            "def f(run, name):\n"
            "    node = host_programs.path('node')\n"
            "    run([node, 'x.js'])\n"
            "    host_programs.path(name)\n"
            "    host_programs.path('node')\n")
    assert _launch_offenders_in("probe.py", text) == []


def test_the_new_exits_log_and_wall() -> None:
    """box/driver.py and webapps/runner_host.py: skipped while absent from
    this build; when present they must name the log and their wall."""
    for rel, words in _EXIT_MENTIONS.items():
        path = SRC / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for word in words:
            assert word in text, f"{rel} is a listed exit but never mentions {word}"


def test_the_scan_is_not_vacuous(tmp_path, monkeypatch) -> None:
    """A planted connection in an unlisted module is caught."""
    planted = SRC / "zz_planted_exit_probe.py"
    planted.write_text("import socket\n"
                       "def f():\n"
                       "    return socket.create_connection(('x.org', 443))\n",
                       encoding="utf-8")
    try:
        assert any("zz_planted_exit_probe.py" in o for o in _offenders())
    finally:
        planted.unlink()


def test_the_listed_exits_write_the_log_and_pin() -> None:
    for rel in ("security/egress_doors.py", "source_fetch.py",
                "wizard/netcheck.py"):
        text = (SRC / rel).read_text(encoding="utf-8")
        assert "egress_ledger" in text, rel
        assert "pinned_net" in text, rel
    doors = (SRC / "security/egress_doors.py").read_text(encoding="utf-8")
    # The one network call in egress_doors is _send's pinned session.
    assert len(re.findall(r"requests\.(request|get|post)\(", doors)) == 0
    assert "session.request(" in doors
    live = (SRC / "catalogs/live.py").read_text(encoding="utf-8")
    assert "egress_ledger" in live and "source_fetch._default_transport" in live


def test_lan_markers_are_only_on_real_home_network_calls() -> None:
    """A marker is a claim; each one names what it talks to."""
    for path in sorted(SRC.rglob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if _LAN_MARKER in line:
                why = line.split(_LAN_MARKER, 1)[1].strip()
                assert len(why) >= 8, f"{path.name}: marker gives no reason"


#: Spellings a line-by-line scan used to miss. Each one
#: must be caught, by the spelling scan or by the syntax scan.
_SNEAKY = [
    "import requests as r\ndef f():\n    return r.get('https://x.org')\n",
    "from requests import get\ndef f():\n    return get('https://x.org')\n",
    "from requests import Session\ndef f():\n    return Session().get('https://x')\n",
    "import urllib.request as ur\ndef f():\n    return ur.urlopen('https://x')\n",
    "import requests\ndef f():\n    return requests.api.request('GET', 'https://x')\n",
    "import requests, json\ndef f():\n    return requests.get('https://x')\n",
    "import subprocess\ndef f():\n    subprocess.run(['curl', 'https://x'])\n",
    "import os\ndef f():\n    os.system('curl https://x')\n",
    "import ctypes\ndef f(s, a, n):\n    ctypes.windll.ws2_32.connect(s, a, n)\n",
    "def f():\n    return getattr(__import__('socket'), 'create_connection')(('x', 443))\n",
    "import socket as so\ndef f(addr):\n    s = so.socket()\n    s.connect(addr)\n",
    "from http import client\ndef f():\n    return client.HTTPSConnection('x')\n",
]


def test_the_scan_sees_aliases_programs_and_dynamic_imports() -> None:
    for text in _SNEAKY:
        spelled = any(_CONNECT_RE.search(line.split("#", 1)[0])
                      for line in text.splitlines())
        parsed = _ast_offenders_in("probe.py", text)
        assert spelled or parsed, f"missed:\n{text}"


def test_a_lan_marker_still_excuses_an_aliased_call() -> None:
    text = ("import requests as r\ndef f(u):\n"
            "    return r.get(u)  # lan-egress: Ollama on the configured LAN url\n")
    assert _ast_offenders_in("probe.py", text) == []


def test_ordinary_calls_are_not_flagged() -> None:
    text = ("import socket\nimport subprocess\ndef f(db, wiring):\n"
            "    db.connect()\n    wiring.connect(x=1)\n"
            "    subprocess.run(['icacls', 'x', '/q'])\n"
            "    return socket.gethostname()\n")
    assert _ast_offenders_in("probe.py", text) == []
    assert _launch_offenders_in("probe.py", text) == []


def test_git_status_is_no_longer_ordinary() -> None:
    """There is no allowance: git reaches outside (fetch, submodules,
    hooks), so even ``git status`` is a network program started by name."""
    text = "import subprocess\ndef f():\n    subprocess.run(['git', 'status'])\n"
    assert _ast_offenders_in("probe.py", text)
    assert _launch_offenders_in("probe.py", text)


@pytest.mark.parametrize("text", [
    "import subprocess\ndef f():\n    subprocess.run(['npm', 'ci'])\n",
    "import subprocess\ndef f():\n    subprocess.run(['npx.cmd', 'x'])\n",
    "import subprocess\ndef f():\n    subprocess.Popen(r'C:\\Windows\\System32\\wsl.exe -l')\n",
    "import os\ndef f():\n    os.system('powershell -c x')\n",
    "import subprocess\ndef f():\n    subprocess.run(args=['node', 'x.js'])\n",
    "import subprocess\ndef f():\n    subprocess.run(f'pip install {x}', shell=True)\n",
    "import subprocess\ndef f():\n    cmd = ['docker', 'run', 'x']\n    subprocess.run(cmd)\n",
    "import shutil, subprocess\ndef f():\n    subprocess.run([shutil.which('wsl'), '-l'])\n",
    "import shutil\ndef f():\n    return shutil.which('wsl')\n",
    "from shutil import which\ndef f():\n    return which('msedge')\n",
    "import shutil, subprocess\ndef f(tool):\n    exe = shutil.which(tool)\n"
    "    subprocess.run([exe, '-v'])\n",
    "import shutil, subprocess\ndef f(tool):\n    exe = shutil.which(tool) or tool\n"
    "    subprocess.Popen([exe])\n",
    # launchers under another name (validator probe p_scan), os.startfile,
    # os.posix_spawn and python -m
    "from subprocess import run\ndef f():\n    run(['npm', 'ci'])\n",
    "from subprocess import Popen as P\ndef f():\n    P(['git', 'clone', 'x'])\n",
    "import subprocess as sp\ndef f():\n    sp.run(['npm', 'ci'])\n",
    "import os\ndef f():\n    os.startfile('msedge.exe')\n",
    "from os import startfile\ndef f():\n    startfile('chrome')\n",
    "import os as o\ndef f():\n    o.system('curl https://x')\n",
    "import os\ndef f(env):\n    os.posix_spawn('node', ['node', 'x.js'], env)\n",
    "from asyncio import create_subprocess_exec as cse\n"
    "async def f():\n    await cse('npx', 'x')\n",
    "import subprocess, sys\ndef f():\n"
    "    subprocess.run([sys.executable, '-m', 'pip', 'install', 'x'])\n",
    "import shutil as sh\ndef f():\n    return sh.which('git')\n",
])
def test_the_program_rule_catches_bare_names_and_which(text) -> None:
    assert _launch_offenders_in("probe.py", text), text


@pytest.mark.parametrize("text", [
    "from subprocess import run\ndef f():\n    run(['npm', 'ci'])\n",
    "import subprocess as sp\ndef f():\n    sp.run(['npm', 'ci'])\n",
    "import os\ndef f():\n    os.startfile('msedge.exe')\n",
])
def test_the_older_program_check_reads_aliases_too(text) -> None:
    assert _ast_offenders_in("probe.py", text), text


def test_ordinary_aliased_launches_are_not_flagged() -> None:
    text = ("from subprocess import run, PIPE\nimport os as o\n"
            "from os import path\ndef f(x):\n"
            "    run(['icacls', x, '/q'], stdout=PIPE)\n"
            "    o.startfile(path.join(x, 'report.txt'))\n"
            "    run([x, '-m', 'json.tool'])\n")
    assert _launch_offenders_in("probe.py", text) == []
    assert _ast_offenders_in("probe.py", text) == []


def test_the_program_rule_holds_inside_a_listed_exit() -> None:
    """A listed exit is not excused: the rule runs on every module."""
    text = "import subprocess\ndef f():\n    subprocess.run(['npm', 'ci'])\n"
    assert "box/driver.py" in EXITS
    assert _launch_offenders_in("box/driver.py", text)


def test_a_launch_through_host_programs_in_a_listed_exit_passes() -> None:
    text = ("import subprocess\nfrom puffpai.security import host_programs\n"
            "def f(distro):\n"
            "    wsl = host_programs.path('wsl')\n"
            "    subprocess.run([wsl, '-d', distro, '--', 'npm', 'ci'],\n"
            "                   **host_programs.run_kwargs())\n")
    assert _launch_offenders_in("box/driver.py", text) == []


def test_a_planted_npm_launch_in_src_is_caught(tmp_path) -> None:
    """End to end on the real tree: a scratch module that runs npm fails."""
    planted = SRC / "zz_planted_launch_probe.py"
    planted.write_text("import subprocess\n"
                       "def f():\n"
                       "    subprocess.run(['npm', 'ci'])\n", encoding="utf-8")
    try:
        found = [o for o in _launch_offenders() if "zz_planted_launch_probe" in o]
        assert found
    finally:
        planted.unlink()


def test_a_planted_which_lookup_in_src_is_caught(tmp_path) -> None:
    planted = SRC / "zz_planted_which_probe.py"
    planted.write_text("import shutil\n"
                       "def f():\n"
                       "    return shutil.which('wsl')\n", encoding="utf-8")
    try:
        assert any("zz_planted_which_probe" in o for o in _launch_offenders())
    finally:
        planted.unlink()


# Shapes that slipped past
# the guard. Each one must be caught, in a
# real module's text, and a trusted path must still pass.
_WIDER_SHAPES = {
    "a name holding the program, first in a wrapper's list":
        "\ndef _p(dash, env, log):\n    prog = 'node'\n    _spawn([prog, 'x.js'], dash, env, log)\n",
    "a list as a later positional argument":
        "\ndef _p(helper):\n    helper(5, ['npm', 'ci'])\n",
    "list concatenation handed on":
        "\ndef _p(run, rest):\n    run(['node'] + rest)\n",
    "a concatenated list bound to a name":
        "\ndef _p(run, rest):\n    argv = ['node'] + rest\n    run(argv)\n",
    "star-unpacking":
        "\ndef _p(run):\n    head = ['node']\n    run([*head, 'x'])\n",
    "any keyword":
        "\ndef _p(run):\n    run(program=['node', 'x'])\n",
    "a command string to a runner":
        "\ndef _p(run):\n    run('node x.js')\n",
    "functools.partial of a launcher":
        "\nimport functools\ndef _p():\n    functools.partial(subprocess.run, ['node', 'x'])()\n",
    "partial imported by name":
        "\nfrom functools import partial\ndef _p():\n    partial(subprocess.run, ['git', 'x'])()\n",
    "getattr(shutil, 'which')":
        "\nimport shutil as _sh\ndef _p():\n    return getattr(_sh, 'which')('node')\n",
    "a launcher given a concatenated list":
        "\ndef _p(rest):\n    subprocess.run(['curl'] + rest)\n",
    "a launcher given a star-unpacked list":
        "\ndef _p():\n    head = ['node']\n    subprocess.run([*head, 'x'])\n",
    "list() of a program tuple":
        "\ndef _p(run):\n    run(list(('node', 'x')))\n",
}


@pytest.mark.parametrize("why", sorted(_WIDER_SHAPES))
def test_the_wider_shapes_are_caught(why) -> None:
    text = (SRC / "start_cli.py").read_text(encoding="utf-8") + _WIDER_SHAPES[why]
    assert _launch_offenders_in("start_cli.py", text), why


@pytest.mark.parametrize("text", [
    "def f(run):\n    run([host_programs.path('node'), 'x'])\n",
    "def f(run):\n    node = host_programs.path('node')\n    run([node, 'x'] + ['y'])\n",
    "def f():\n    return _system_program('taskkill')\n",
    "def f(log):\n    log.info('node x.js failed')\n",
    "def f(rows):\n    rows.append({'name': 'git'})\n",
])
def test_the_wider_shapes_do_not_flag_ordinary_code(text) -> None:
    assert _launch_offenders_in("probe.py", text) == []


def test_the_jail_program_comes_from_host_programs() -> None:
    """agents/toolsmith.py built its jail from a hard-coded "/usr/bin/bwrap",
    hidden from the older scan by a name. On Windows that path is a folder on
    the drive root anyone may create; host_programs answers nothing there."""
    text = (SRC / "agents" / "toolsmith.py").read_text(encoding="utf-8")
    assert _launch_offenders_in("agents/toolsmith.py", text) == []
    assert 'host_programs.path("bwrap")' in text
    import sys
    from puffpai.security import host_programs
    if sys.platform == "win32":
        assert host_programs.path("bwrap") is None
