"""Command-line tools from npm or PyPI, run in the locked box on files from a
folder the owner gave.

* **Applier** ``cli_tool`` (plan and Add page): installs the exact package
  version the plan showed into box project ``<integration id>`` with no
  install scripts (npm ``--ignore-scripts``, pip wheels only), records the
  lock (:mod:`puffpai.box.locks`), and has Drawbridge check it (checker
  ``cli_tool``) before anything of the package runs.
* **Actions** (source ``cli``): read from the command's OWN ``--help``, run in
  the box with no internet and no folder: subcommands listed under a
  "Commands"/"Subcommands" heading, and an argument named FILE, PATH or INPUT
  becomes a ``file`` argument from the granted folder (extensions from the
  help text, or none). The words an action says are BuildBot's own template,
  never the tool's help text. ``provenance.by = "toolsmith_cli"``.
* **Effects**: ``reads`` with a read-only folder; with BuildBot's write
  folder at least ``changes``, and ``removes`` when the command has an
  ``-o``/``--output`` option (it could overwrite an existing file).
* **Running**: each argument is its own argv item (no shell); a string
  argument starting with ``-`` is refused; the file must be a plain file
  inside the granted folder; stdout is data, capped at ``stdout_max`` and
  neutralised; run-time ``allow_hosts`` is empty.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import secrets
import threading
import time
from typing import Any, Callable, Optional

from puffpai.box import driver, locks
from puffpai.box import wording as W
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

KIND = "cli_tool"
SOURCE_KIND = "cli"
CHECKER_VERSION = "r3.1"
BLOCK_CODES = frozenset({"not_exact", "scripts_allowed", "no_hashes", "not_open",
                         "starjacking", "hosts_at_run", "shell_chars", "lock_changed"})
HOLD_CODES = frozenset({"lock_offsite"})
STDOUT_MAX = 65536
WALL_S = 60
HELP_MAX_BYTES = 65536
MAX_HELP_CALLS = 24
MAX_ACTIONS = 20
MAX_EXTS = 8
INSTALL_WALL_S = 900
DISK_MB = 2048

#: Subcommands that are never actions (servers, shells, self-changing ones).
SKIP_SUBCOMMANDS = frozenset({
    "help", "server", "serve", "daemon", "watch", "completion", "completions", "shell",
    "repl", "console", "init", "install", "uninstall", "update", "upgrade", "self-update",
    "selfupdate", "login", "logout", "auth", "config", "publish", "deploy", "version",
    "lsp"})
#: Licences whose words let everyone use the tool (families, upper-cased).
OPEN_LICENSES = frozenset({
    "MIT", "MIT-0", "APACHE-2.0", "BSD-2-CLAUSE", "BSD-3-CLAUSE", "ISC", "0BSD", "MPL-2.0",
    "GPL-2.0", "GPL-2.0-ONLY", "GPL-2.0-OR-LATER", "GPL-3.0", "GPL-3.0-ONLY",
    "GPL-3.0-OR-LATER", "LGPL-2.1", "LGPL-2.1-ONLY", "LGPL-2.1-OR-LATER", "LGPL-3.0",
    "LGPL-3.0-ONLY", "LGPL-3.0-OR-LATER", "AGPL-3.0", "AGPL-3.0-ONLY", "AGPL-3.0-OR-LATER",
    "UNLICENSE", "ZLIB", "CC0-1.0", "PYTHON-2.0", "PSF-2.0", "ARTISTIC-2.0",
    "BLUEOAK-1.0.0", "EUPL-1.2", "WTFPL", "BSL-1.0"})
_LICENSE_WORDS = {"MIT LICENSE": "MIT", "APACHE SOFTWARE LICENSE": "APACHE-2.0",
                  "APACHE 2.0": "APACHE-2.0", "APACHE LICENSE 2.0": "APACHE-2.0",
                  "BSD LICENSE": "BSD-3-CLAUSE", "BSD": "BSD-3-CLAUSE",
                  "ISC LICENSE (ISCL)": "ISC", "GNU GENERAL PUBLIC LICENSE V3 (GPLV3)": "GPL-3.0",
                  "GNU GENERAL PUBLIC LICENSE V2 (GPLV2)": "GPL-2.0",
                  "MOZILLA PUBLIC LICENSE 2.0 (MPL 2.0)": "MPL-2.0",
                  "THE UNLICENSE (UNLICENSE)": "UNLICENSE", "PUBLIC DOMAIN": "UNLICENSE"}
_SHELL_CHARS = set(";|&$`<>(){}*?!~\n\r\\\"'")
_PLACEHOLDER_RE = re.compile(r"^\{([a-z][a-z0-9_]{0,39})\}\Z")
#: Where an installed tool's program lives in its box project. A spec's
#: ``argv`` names the program (``["cook", "recipe", "read", "{file}"]``, the
#: pinned shape); its ``bin`` is this full path, used at run time instead of a
#: PATH lookup.
_BIN_PATH_RE = re.compile(r"^/work/(node_modules/\.bin|venv/bin)/([A-Za-z0-9][A-Za-z0-9._-]{0,63})\Z")
_WORD_RE = re.compile(r"^[a-z][a-z0-9_-]{0,30}\Z")
_BIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_ACTION_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}\Z")
_FILE_WORDS = ("FILE", "PATH", "INPUT", "INFILE")
_REL_RE = re.compile(r"^[^\x00-\x1f<>:\"|?*\\]{1,240}\Z")

_lock = threading.Lock()


def _optional(modname: str):
    try:
        return importlib.import_module(modname)
    except ModuleNotFoundError as e:
        if e.name is not None and (e.name == modname or modname.startswith(e.name + ".")):
            return None          # not in this build yet
        raise                    # exists but broken: fail loudly


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ═════════════════════════════════════════════════════════════════════════════
# reading --help
# ═════════════════════════════════════════════════════════════════════════════

_HEADING_RE = re.compile(r"^\s*(?:available\s+)?(?:sub)?commands\s*:?\s*\Z", re.IGNORECASE)
_ANY_HEADING_RE = re.compile(r"^\S.*:\s*\Z")
_ITEM_RE = re.compile(r"^\s{1,10}([a-z][a-z0-9_-]{0,30})(?:,\s*[a-z][a-z0-9_-]{0,30})*"
                      r"(?:\s{2,}\S.*)?\s*\Z")
_OUTPUT_RE = re.compile(r"(?:^|[\s,\[])(?:-o|--output|--out|--outfile|--output-file|"
                        r"--output-dir|--outdir)(?=[\s,=\]<]|\Z)", re.M)
_EXT_RE = re.compile(r"(?:(?<=\s)|(?<=\*)|(?<=\()|(?<=\A))\.([a-z0-9]{1,6})(?=[\s),;]|\Z)",
                     re.IGNORECASE)


def _usage_block(text: str) -> str:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^\s*usage\s*:\s*(.*)\Z", line, re.IGNORECASE)
        if not m:
            continue
        out = [m.group(1)]
        for more in lines[i + 1:i + 12]:
            if not more.strip() or not more[:1].isspace():
                break
            out.append(more.strip())
        return " ".join(out)
    return ""


def _strip_groups(usage: str) -> tuple[str, list[str]]:
    """Take option groups out of a usage line; returns (rest, {a,b,c} choices).

    Brackets are read by depth: a top-level ``[...]`` group whose content
    starts with ``-`` is an option group and goes whole, nested parts and
    all (``[--null-value X [X ...]]``); other groups stay (``[FILE]``)."""
    choices: list[str] = []
    for m in re.finditer(r"\{([a-z0-9_,-]{1,300})\}", usage):
        choices += [c for c in m.group(1).split(",") if _WORD_RE.match(c)]
    out: list[str] = []
    i, n = 0, len(usage)
    while i < n:
        ch = usage[i]
        if ch == "[":
            depth, j = 0, i
            while j < n:
                if usage[j] == "[":
                    depth += 1
                elif usage[j] == "]":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            group = usage[i:j + 1]
            if group[1:].lstrip().startswith("-"):
                out.append(" ")
            else:
                out.append(group)
            i = j + 1
            continue
        out.append(ch)
        i += 1
    text = "".join(out)
    text = re.sub(r"(?:^|\s)--?[A-Za-z][\w-]*(?:[ =](?:[A-Z_]{2,}|<[^>]+>))?", " ", text)
    return text, choices


_ARGS_HEADING_RE = re.compile(r"^\s*(?:positional\s+)?arguments\s*:?\s*\Z", re.IGNORECASE)
_ARG_ENTRY_RE = re.compile(r"^(\s{1,10})[\[<]?([A-Za-z][A-Za-z0-9_-]{0,30})[\]>]?(?:\.\.\.)?"
                           r"(?:\s{2,}(\S.*))?\s*\Z")
_FILE_DESC_RE = re.compile(r"\b(?:files?|paths?)\b", re.IGNORECASE)
_DIR_DESC_RE = re.compile(r"\bdirector(?:y|ies)\b|\bfolders?\b", re.IGNORECASE)


def _argument_descriptions(text: str) -> dict:
    """``{NAME: {"first": first description line, "all": whole block}}`` from
    an "Arguments:" section."""
    out: dict = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if not _ARGS_HEADING_RE.match(lines[i]):
            i += 1
            continue
        i += 1
        current = None
        indent = None
        while i < len(lines):
            line = lines[i]
            if line.strip() and not line[:1].isspace():
                break                                   # the next section
            m = _ARG_ENTRY_RE.match(line)
            if m and (indent is None or len(m.group(1)) <= indent):
                indent = len(m.group(1))
                current = m.group(2).upper()
                first = (m.group(3) or "").strip()
                out.setdefault(current, {"first": first, "all": first})
            elif current and line.strip():
                entry = out[current]
                if not entry["first"]:
                    entry["first"] = line.strip()
                entry["all"] = (entry["all"] + " " + line.strip())[:2000]
            i += 1
    return {k: {"first": v["first"][:200], "all": v["all"]} for k, v in out.items()}


#: Lower-case usage words that name the file a command reads, as some tools
#: write them ("markdown file-to-convert [options]", "tool filename").
_LOWER_FILE_PARTS = frozenset({"file", "filename", "filepath", "infile", "inputfile",
                               "input"})


def _names_a_file(tok: str) -> bool:
    """True for a lower-case usage word made of plain parts, one of which
    names a file (``file-to-convert``, ``input_file``, ``filename``)."""
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+){0,4}", tok):
        return False
    return any(part in _LOWER_FILE_PARTS for part in re.split(r"[-_]", tok))


def _positionals(usage: str, descriptions: Optional[dict] = None) -> tuple[list[dict], bool, list[str]]:
    """``([{"name","file","required"}], takes_a_subcommand, {choices})``.

    A positional is a file when its name says so (FILE, PATH, INPUT) or its
    own description in the help's "Arguments" section says it is a file or a
    path (and not a folder)."""
    descriptions = descriptions or {}
    if not usage:
        return [], False, []
    rest, choices = _strip_groups(usage)
    tokens = rest.split()[1:]                # drop the program's own name(s) below
    out: list[dict] = []
    sub = bool(choices)
    for raw in tokens:
        tok = raw.strip(".,")
        required = True
        if tok.startswith("[") and tok.endswith("]"):
            required, tok = False, tok[1:-1]
        elif tok.startswith("["):
            required, tok = False, tok[1:]
        tok = tok.rstrip("]").strip(".")
        if tok.startswith("<") and tok.endswith(">"):
            tok = tok[1:-1]
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,30}", tok):
            continue
        upper = tok.upper()
        if upper in ("OPTIONS", "OPTION", "ARGS", "ARGUMENTS", "FLAGS"):
            continue
        if upper in ("COMMAND", "SUBCOMMAND", "CMD"):
            sub = True
            continue
        if tok != upper and not raw.startswith(("<", "[<")) and not _names_a_file(tok):
            continue                          # a lower-case word: part of the name
        name = re.sub(r"[^a-z0-9]+", "_", tok.lower()).strip("_")[:39] or "arg"
        desc = str((descriptions.get(upper) or {}).get("first") or "")
        is_file = any(w in upper for w in _FILE_WORDS) or (
            bool(_FILE_DESC_RE.search(desc)) and not _DIR_DESC_RE.search(desc))
        if is_file:
            name = "file"
        if any(p["name"] == name for p in out):
            continue
        out.append({"name": name, "file": is_file, "required": required,
                    "about": str((descriptions.get(upper) or {}).get("all") or "")})
    return out, sub, choices


def parse_help(text: str) -> dict:
    """What a command's own ``--help`` says about using it. Plain data.

    ``{"subcommands": [names], "positionals": [{"name","file","required"}],
    "has_output_option": bool, "exts": [".x"], "takes_subcommand": bool}``.
    The text is untrusted: only short, shape-checked names come out of it.
    """
    text = str(text or "")[:HELP_MAX_BYTES]
    subs: list[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if _HEADING_RE.match(lines[i]):
            i += 1
            while i < len(lines):
                line = lines[i]
                if not line.strip():
                    if subs:
                        break
                    i += 1
                    continue
                if _ANY_HEADING_RE.match(line) and not line[:1].isspace():
                    break
                m = _ITEM_RE.match(line)
                if m and m.group(1) not in subs:
                    subs.append(m.group(1))
                i += 1
            continue
        i += 1
    positionals, takes_sub, choices = _positionals(_usage_block(text),
                                                   _argument_descriptions(text))
    for c in choices:
        if c not in subs:
            subs.append(c)
    exts: list[str] = []
    # Extensions from the file argument's own description when it has one,
    # else from the whole help (never output formats of other options).
    about = " ".join(p.get("about") or "" for p in positionals if p["file"])
    for m in _EXT_RE.finditer(" " + about if about.strip() else text):
        ext = "." + m.group(1).lower()
        if ext not in exts and not ext[1:].isdigit():
            exts.append(ext)
        if len(exts) >= MAX_EXTS:
            break
    for p in positionals:
        p.pop("about", None)
    return {"subcommands": subs[:40], "positionals": positionals,
            "has_output_option": bool(_OUTPUT_RE.search(text)), "exts": exts,
            "takes_subcommand": takes_sub or bool(subs)}


#: Headings over a list of subcommands: "Commands:", "Available Commands:",
#: "Common Commands:", "Management Commands:", "All commands:", "Subcommands:".
_LIST_HEADING_RE = re.compile(r"^\s*(?:[A-Za-z]+\s+){0,2}(?:sub)?commands\s*:\s*\Z",
                              re.IGNORECASE)
#: git's own heading: "These are common Git commands used in various situations:"
#: (groups of indented "   clone   Clone a repository ..." lines under plain titles).
_GIT_HEADING_RE = re.compile(r"^these are (?:the )?common \S+ commands\b.*:\s*\Z", re.IGNORECASE)
_LIST_ITEM_RE = re.compile(r"^\s{1,10}([a-z][a-z0-9_-]{0,30})\*?"
                           r"(?:,\s*[a-z][a-z0-9_-]{0,30})*(?:\s{2,}(\S.*))?\s*\Z")
_COMMA_LIST_RE = re.compile(r"^\s{1,10}[a-z][a-z0-9_-]{0,30}(?:,\s*[a-z][a-z0-9_-]{0,30})+,?\s*\Z")
LIST_MAX = 60


def _plain_about(text: str) -> str:
    """A help line's description as short plain text (it is the program's
    own words: shown, never run)."""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(text or ""))
    return " ".join(text.split())[:120]


def list_subcommands(text: str, limit: int = LIST_MAX) -> list[tuple[str, str]]:
    """``[(name, description)]`` of the subcommands a program's own help
    lists: "Commands:" style sections (docker, cobra and click tools), npm's
    comma list under "All commands:", and git's "These are common Git
    commands..." groups. Shape-checked names only; at most *limit*."""
    out: list[tuple[str, str]] = []
    seen: set = set()

    def add(name: str, about: str = "") -> None:
        if name not in seen and _WORD_RE.match(name) and len(out) < limit:
            seen.add(name)
            out.append((name, _plain_about(about)))

    lines = str(text or "")[:HELP_MAX_BYTES].splitlines()
    i = 0
    while i < len(lines):
        git_style = bool(_GIT_HEADING_RE.match(lines[i].strip()))
        if not (git_style or _LIST_HEADING_RE.match(lines[i])):
            i += 1
            continue
        i += 1
        found = False
        while i < len(lines):
            line = lines[i]
            if not line.strip():
                if found and not git_style:
                    break
                i += 1
                continue
            if not line[:1].isspace():
                if git_style and not _LIST_HEADING_RE.match(line):
                    i += 1                       # a group title ("start a working area")
                    continue
                break                            # the next section
            if _COMMA_LIST_RE.match(line):
                for word in line.split(","):
                    add(word.strip())
                found = True
            else:
                m = _LIST_ITEM_RE.match(line)
                if m:
                    add(m.group(1), m.group(2) or "")
                    found = True
            i += 1
    return out


def derive_specs(bin_path: str, bin_name: str, help_of: Callable[[list[str]], str], *,
                 max_calls: int = MAX_HELP_CALLS, host: bool = False) -> list[dict]:
    """The CLI actions a command offers, from its own help (depth 2).

    *help_of(argv)* returns the help text of ``argv + ["--help"]`` (run in the
    box). Returns specs ``{"name","command","argv","bin","files","strings",
    "has_output_option","stdout_max","wall_s","allow_hosts"}``: ``argv`` is
    the pinned shape starting with the program's name, ``bin`` its full path
    in the box (see :func:`resolved_argv`). A program outside the tool's own
    folders gives no actions.

    *host*: a program the owner added herself on this computer
    (:mod:`puffpai.programs`): *bin_path* is its full path on this computer
    and *bin_name* its file name without the extension. These specs describe
    the program for the brain only; they are never run by
    :func:`resolved_argv` (which still takes box paths only)."""
    if host:
        if not (isinstance(bin_path, str) and os.path.isabs(bin_path) and _BIN_RE.match(
                bin_name or "") and os.path.splitext(os.path.basename(bin_path))[0].lower()
                == str(bin_name).lower()):
            return []
    else:
        m = _BIN_PATH_RE.match(bin_path or "")
        if not m or m.group(2) != bin_name:
            return []
    calls = {"n": 0}

    def ask(path: list[str]) -> Optional[dict]:
        if calls["n"] >= max_calls:
            return None
        calls["n"] += 1
        try:
            return parse_help(help_of([bin_path] + path))
        except Exception:
            return None

    specs: list[dict] = []

    def visit(path: list[str], info: Optional[dict], depth: int) -> None:
        if info is None or len(specs) >= MAX_ACTIONS:
            return
        subs = [s for s in info["subcommands"] if s not in SKIP_SUBCOMMANDS
                and _WORD_RE.match(s)]
        if subs and depth < 2:
            for sub in subs:
                visit(path + [sub], ask(path + [sub]), depth + 1)
            return
        pos = info["positionals"]
        files = [p for p in pos if p["file"]]
        if not files:
            return                          # an action works on a file from the folder
        strings = [p for p in pos if not p["file"]][:3]
        argv = [bin_name] + path
        for p in pos:
            if p["file"] or p in strings:
                argv.append("{" + p["name"] + "}")
        name = "_".join([bin_name] + path)
        name = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        if not name or not name[0].isalpha():
            name = "run_" + name
        name = name[:48].rstrip("_")
        if any(s["name"] == name for s in specs):
            return
        specs.append({
            "name": name, "command": " ".join([bin_name] + path),
            "argv": argv, "bin": bin_path, "files": {"file": {"from": "folder", "ext": info["exts"]}},
            "strings": [{"name": p["name"], "required": p["required"]} for p in strings],
            "has_output_option": info["has_output_option"],
            "stdout_max": STDOUT_MAX, "wall_s": WALL_S, "allow_hosts": []})

    visit([], ask([]), 0)
    return specs


def resolved_argv(spec: dict) -> Optional[list]:
    """The spec's argv template with its first item replaced by the program's
    checked full path in the box, or None when the two do not agree. Never a
    PATH lookup. (Specs saved before ``bin`` existed carry the full path as
    their first item; the same check applies to it.)"""
    argv = spec.get("argv") if isinstance(spec, dict) else None
    if not isinstance(argv, list) or not argv or not isinstance(argv[0], str):
        return None
    head = argv[0]
    full = spec.get("bin")
    if full is None and _BIN_PATH_RE.match(head):
        full, head = head, head.rsplit("/", 1)[-1]
    m = _BIN_PATH_RE.match(full) if isinstance(full, str) else None
    if not m or m.group(2) != head or not _BIN_RE.match(head):
        return None
    return [full] + list(argv[1:])


def argv_problems(argv: list) -> list[str]:
    """Shell characters in an argv template (each argument its own item;
    placeholders only as a whole item)."""
    bad = []
    for arg in argv:
        if not isinstance(arg, str) or not arg:
            bad.append(repr(arg))
            continue
        if _PLACEHOLDER_RE.match(arg):
            continue
        if any(c in _SHELL_CHARS for c in arg) or arg.startswith("-") and arg not in ("--",):
            bad.append(arg)
    return bad


# ═════════════════════════════════════════════════════════════════════════════
# Drawbridge's checklist for a command-line tool
# ═════════════════════════════════════════════════════════════════════════════

def license_family(text: Any) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    up = raw.upper()
    if up in _LICENSE_WORDS:
        return _LICENSE_WORDS[up]
    up = up.replace(" ", "-")
    if up.startswith("LICENSE-::-OSI-APPROVED-::-"):
        return license_family(raw.split("::")[-1])
    return up.strip("()")


def _row(check: str, ok: Optional[bool], why_ok: str, code: str) -> dict:
    result = "pass" if ok else ("unknown" if ok is None else "fail")
    why = why_ok if ok else W.CLI_REASONS.get(code, "it did not pass this check")
    return {"check": check, "result": result, "why": why, "code": code}


def cli_tool_rows(draft: dict) -> list[dict]:
    """The ``cli_tool`` checklist. Reads only the draft."""
    d = draft if isinstance(draft, dict) else {}
    pkg = d.get("package") if isinstance(d.get("package"), dict) else {}
    got = d.get("installed") if isinstance(d.get("installed"), dict) else {}
    rows = []
    exact = bool(pkg.get("name")) and pkg.get("name") == got.get("name") \
        and bool(pkg.get("version")) and pkg.get("version") == got.get("version")
    rows.append(_row("Exactly the name and version the plan showed", exact,
                     "It is exactly the name and version the plan showed.", "not_exact"))
    flags = [str(f) for f in d.get("install_flags") or []]
    safe = ("--ignore-scripts" in flags) if pkg.get("registry") == "npm" \
        else ("--only-binary=:all:" in flags or "--require-hashes" in flags)
    rows.append(_row("Installed without running its own install steps", safe,
                     "Nothing of it ran while it was installed.", "scripts_allowed"))
    lock = d.get("lock") if isinstance(d.get("lock"), dict) else {}
    hashed = bool(re.fullmatch(r"[0-9a-f]{64}", str(lock.get("sha256") or ""))) \
        and int(lock.get("hashes") or 0) > 0
    rows.append(_row("Every part fingerprinted and recorded", hashed,
                     "Every part it uses is fingerprinted and recorded.", "no_hashes"))
    offsite = list(lock.get("offsite") or [])
    rows.append(_row("Every part comes from the package site", not offsite,
                     "Every part comes from the package site.", "lock_offsite"))
    fam = license_family(d.get("license"))
    lic_ok: Optional[bool] = (fam in OPEN_LICENSES) if fam else None
    rows.append(_row("Its licence lets everyone use it", lic_ok,
                     "Its licence lets everyone use it.", "not_open"))
    if pkg.get("registry") == "npm":
        match = d.get("repo_name_matches")
        rows.append(_row("Its code project has the same name", match is not False,
                         "Its code project has the same name.", "starjacking"))
    hosts = d.get("allow_hosts")
    rows.append(_row("Needs no internet while it runs", hosts == [],
                     "It runs with no internet.", "hosts_at_run"))
    templates = d.get("argv_templates")
    shell_ok = isinstance(templates, list) and all(
        isinstance(t, list) and t and not argv_problems(t) for t in templates)
    rows.append(_row("Started safely, one argument at a time", shell_ok,
                     "It is started one argument at a time, with no shell.", "shell_chars"))
    return rows


# ═════════════════════════════════════════════════════════════════════════════
# where things are kept
# ═════════════════════════════════════════════════════════════════════════════

def _records():
    return _optional("puffpai.integrate.records")


def _cli_path(iid: str) -> str:
    return os.path.join(_records().folder(iid), "cli.json")


def _folder_path(iid: str) -> str:
    return os.path.join(_records().folder(iid), "folder.json")


def _save_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _load_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def folder_grant(iid: str) -> Optional[dict]:
    """``{"realpath","mode"}`` the owner gave this tool, or None."""
    rec = _records()
    if rec is None or not rec.valid_id(iid):
        return None
    data = _load_json(_folder_path(iid))
    if not data or data.get("mode") not in ("ro", "rw") or not isinstance(data.get("realpath"), str):
        return None
    return {"realpath": data["realpath"], "mode": data["mode"]}


def _effect(spec: dict, grant: Optional[dict]) -> str:
    if not grant or grant["mode"] == "ro":
        return "reads"
    return "removes" if spec.get("has_output_option") else "changes"


# ═════════════════════════════════════════════════════════════════════════════
# the action source "cli"
# ═════════════════════════════════════════════════════════════════════════════

def _action(iid: str, app_name: str, spec: dict, verdict: dict, grant: Optional[dict]) -> dict:
    effect = _effect(spec, grant)
    props: dict = {"file": {"type": "string", "maxLength": 200}}
    required = ["file"]
    for s in spec.get("strings") or []:
        props[s["name"]] = {"type": "string", "maxLength": 200}
        if s.get("required"):
            required.append(s["name"])
    says_tmpl = W.CLI_SAYS_FILE
    return {
        "id": f"{iid}__{spec['name']}", "integration": iid, "app_name": app_name[:80],
        "says": says_tmpl.format(command=spec["command"])[:120], "effect": effect,
        "kind": SOURCE_KIND,
        "args": {"type": "object", "additionalProperties": False, "properties": props,
                 "required": required},
        "undoable": False,
        "provenance": {"by": "toolsmith_cli", "compiler_version": CHECKER_VERSION,
                       "app_sha": str(verdict.get("app_sha") or ""),
                       "recipe_sha": hashlib.sha256(json.dumps(
                           spec, sort_keys=True).encode()).hexdigest()},
        "checked": {"verdict": str(verdict.get("verdict") or ""), "effect_proven": effect,
                    "rows_sha": str(verdict.get("rows_sha") or ""),
                    "at": str(verdict.get("at") or "")},
    }


def list_actions() -> list[dict]:
    """Every action of every ready command-line tool (the source's list_fn)."""
    rec_mod = _records()
    if rec_mod is None:
        return []
    out = []
    for rec in rec_mod.all():
        if rec.get("mode") != KIND or rec.get("status") != "ready":
            continue
        data = _load_json(_cli_path(rec["id"]))
        if not data:
            continue
        grant = folder_grant(rec["id"])
        verdict = data.get("verdict") if isinstance(data.get("verdict"), dict) else {}
        for spec in data.get("specs") or []:
            if isinstance(spec, dict) and _ACTION_NAME_RE.match(str(spec.get("name") or "")):
                out.append(_action(rec["id"], str(rec.get("name") or rec["id"]), spec,
                                   verdict, grant))
    return out


def _spec_for(action: dict) -> tuple[Optional[dict], Optional[dict]]:
    iid = str(action.get("integration") or "")
    rec_mod = _records()
    if rec_mod is None or not rec_mod.valid_id(iid):
        return None, None
    rec = rec_mod.get(iid)
    data = _load_json(_cli_path(iid))
    if not rec or rec.get("status") != "ready" or not data:
        return None, None
    name = str(action.get("id") or "")[len(iid) + 2:]
    for spec in data.get("specs") or []:
        if isinstance(spec, dict) and spec.get("name") == name:
            return rec, spec
    return rec, None


def _file_in(folder_real: str, rel: str, exts: list) -> Optional[str]:
    """The checked path of *rel* inside the granted folder, or None."""
    from puffpai.security import owner_folders
    if not isinstance(rel, str) or not _REL_RE.match(rel) or rel.startswith(("-", "/", ".")):
        return None
    parts = [p for p in rel.replace("\\", "/").split("/")]
    if any(p in ("", ".", "..") or p != p.strip() or p.endswith(".") for p in parts):
        return None
    if exts and not any(parts[-1].lower().endswith(e) for e in exts):
        return None
    path = os.path.join(folder_real, *parts)
    walk = folder_real
    for p in parts:
        walk = os.path.join(walk, p)
        if owner_folders.is_link(walk):
            return None
    try:
        real = os.path.realpath(path, strict=True)
    except (OSError, ValueError):
        return None
    base = os.path.normcase(os.path.realpath(folder_real))
    if not os.path.normcase(real).startswith(base.rstrip("\\/") + os.sep) or not os.path.isfile(real):
        return None
    return "/".join(parts)


def run_action(action: dict, args: dict, ctx: dict) -> dict:
    """The source's run_fn: run one checked action in the box."""
    rec, spec = _spec_for(action)
    name = str(action.get("app_name") or "This tool")
    if rec is None or spec is None:
        return {"ok": False, "line": W.CLI_GONE}
    grant = folder_grant(rec["id"])
    if grant is None:
        return {"ok": False, "line": W.CLI_NO_FOLDER.format(name=name)}
    from puffpai.security import owner_folders
    real, _why = owner_folders.check(grant["realpath"], grant["mode"])
    if not real:
        return {"ok": False, "line": W.CLI_NO_FOLDER.format(name=name)}
    exts = list((spec.get("files") or {}).get("file", {}).get("ext") or [])
    rel = _file_in(real, args.get("file"), exts)
    if rel is None:
        return {"ok": False, "line": W.CLI_BAD_FILE.format(name=name)}
    values = {"file": "/data/files/" + rel}
    for s in spec.get("strings") or []:
        v = args.get(s["name"])
        if v is None:
            if s.get("required"):
                return {"ok": False, "line": W.CLI_FAILED.format(name=name)}
            continue
        if not isinstance(v, str) or not v or v.startswith("-") or len(v) > 200 \
                or any(ord(c) < 32 for c in v):
            return {"ok": False, "line": W.CLI_FAILED.format(name=name)}
        values[s["name"]] = v
    template = resolved_argv(spec)
    if template is None:
        return {"ok": False, "line": W.CLI_FAILED.format(name=name)}
    argv = []
    for item in template:
        m = _PLACEHOLDER_RE.match(item)
        if m:
            if m.group(1) in values:
                argv.append(values[m.group(1)])
            continue
        argv.append(item)
    if argv_problems(spec["argv"]) or spec.get("allow_hosts"):
        return {"ok": False, "line": W.CLI_FAILED.format(name=name)}
    out_path = _tmp_path()
    try:
        res = driver.run(driver.BoxRequest(
            run_id=driver.new_run_id(), project=rec["id"], argv=argv, allow_hosts=[],
            what="box_traffic", folder={"windows_path": real, "mode": grant["mode"],
                                        "name": "files"},
            limits={"wall_s": int(spec.get("wall_s") or WALL_S), "mem_mb": 1024,
                    "cpu_s": int(spec.get("wall_s") or WALL_S), "disk_mb": DISK_MB},
            stdout_mode="raw"), stdout_path=out_path)
        text, cut = _read_capped(out_path, int(spec.get("stdout_max") or STDOUT_MAX))
    finally:
        _unlink(out_path)
    if res.timed_out:
        return {"ok": False, "line": W.CLI_TOO_LONG.format(name=name)}
    if not res.ok:
        return {"ok": False, "line": W.CLI_FAILED.format(name=name)}
    from puffpai import artifact_review
    clean = "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 32)
    cap = int(spec.get("stdout_max") or STDOUT_MAX)
    clean = artifact_review.neutralise_passage(clean, limit=cap)
    if len(clean) > cap:
        # A left-out note can be longer than the line it stands for: cut to
        # the cap again, check the cut text again, then drop whole lines
        # (each one already checked) until it fits. Only a note can still
        # be longer than the cap on its own.
        clean = artifact_review.neutralise_passage(clean[:cap], limit=cap)
        while len(clean) > cap and "\n" in clean:
            clean = clean.rsplit("\n", 1)[0]
        clean = clean[:cap]
        cut = True
    return {"ok": True, "line": W.CLI_DONE.format(name=name),
            "data": {"output": clean, "cut_short": cut},
            "evidence": {"output": clean[:2000], "cut_short": cut}}


def _tmp_path() -> str:
    from puffpai.config import get_state_dir
    folder = os.path.join(get_state_dir(), "box", "tmp")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"out-{secrets.token_hex(8)}")


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _read_capped(path: str, cap: int) -> tuple[str, bool]:
    try:
        with open(path, "rb") as fh:
            data = fh.read(cap + 1)
    except OSError:
        return "", False
    return data[:cap].decode("utf-8", "replace"), len(data) > cap


# ═════════════════════════════════════════════════════════════════════════════
# giving a tool a folder
# ═════════════════════════════════════════════════════════════════════════════

def grant_folder(integration_id: str, path: str, mode: str, *, ticket: str,
                 device_id: str, bot_name: str = "BuildBot") -> dict:
    """Give a ready tool a folder (the owner's ``folder_give`` yes).

    ``ro``: a folder the rules allow (:func:`owner_folders.check`). ``rw``:
    always BuildBot's own ``Documents\\<Bot> files\\<tool>`` (made here).
    ``{"ok", "line"}``."""
    from puffpai.security import owner_folders
    rec_mod = _records()
    ct = _optional("puffpai.security.consent_tickets")
    if rec_mod is None or ct is None or not callable(getattr(ct, "target_folder", None)):
        return {"ok": False, "line": W.SETUP_NOT_IN_BUILD}
    rec = rec_mod.get(integration_id) if rec_mod.valid_id(integration_id) else None
    if not rec or rec.get("mode") != KIND or rec.get("status") != "ready":
        return {"ok": False, "line": W.FOLDER_NOT_A_TOOL}
    name = str(rec.get("name") or integration_id)
    if mode == "rw":
        try:
            real = owner_folders.write_folder(bot_name, name)
        except PermissionError as exc:
            return {"ok": False, "line": str(exc)}
    elif mode == "ro":
        real, why = owner_folders.check(path, "ro")
        if not real:
            return {"ok": False, "line": why}
    else:
        return {"ok": False, "line": W.FOLDER_BAD_MODE}
    ok, verdict = ct.redeem(ticket, device_id, "folder_give",
                            ct.target_folder(integration_id, real, mode))
    if not ok:
        return {"ok": False, "line": ct.refusal_words(verdict).replace(
            "Nothing was switched on", "No folder was given")}
    _save_json(_folder_path(integration_id), {"realpath": real, "mode": mode,
                                              "granted_at": _now()})
    rec["folder"] = {"granted": True, "mode": mode,
                     "realpath_sha": hashlib.sha256(os.path.normcase(real).encode()).hexdigest()}
    rec_mod.save(rec)
    tmpl = W.FOLDER_GIVEN_RW if mode == "rw" else W.FOLDER_GIVEN_RO
    return {"ok": True, "line": tmpl.format(name=name, folder=real)}


# ═════════════════════════════════════════════════════════════════════════════
# the applier
# ═════════════════════════════════════════════════════════════════════════════

_NPM_NAME_RE = re.compile(r"^(@[a-z0-9-~][a-z0-9-._~]{0,100}/)?[a-z0-9-~][a-z0-9-._~]{0,100}\Z")


def _github_pin(found: dict) -> Optional[dict]:
    """The exact version of a find from a code forge (GitHub, Codeberg): the
    commit the plan pinned, and the archive's sha-256 when one was recorded.
    None when anything about it is not exact (no pin, a host or name that is
    not plain, a recorded fingerprint that is not a sha-256)."""
    fetch = _optional("puffpai.integrate.fetch")
    repo = found.get("repo") if isinstance(found.get("repo"), dict) else None
    if fetch is None or repo is None:
        return None
    try:
        fetch.archive_url(found)                 # host, owner, name and the pin
    except Exception:
        return None
    commit = str(repo.get("pinned_commit") or "").lower()
    sha = str(repo.get("archive_sha256") or "").lower()
    if sha and not re.fullmatch(r"[0-9a-f]{64}", sha):
        return None
    return {"registry": "github", "host": str(repo.get("host") or "").lower(),
            "name": f"{repo.get('owner')}/{repo.get('name')}", "version": commit,
            "commit": commit, "archive_sha256": sha or None}


def _package_of(found: dict) -> Optional[dict]:
    pkg = found.get("package") if isinstance(found.get("package"), dict) else None
    if pkg is None and found.get("source") not in ("npm", "pypi"):
        # A command-line tool found on a code forge: exact means the commit the
        # plan pinned (and the archive fingerprint, when one was recorded).
        return _github_pin(found)
    if not pkg or pkg.get("registry") not in ("npm", "pypi"):
        return None
    name, version = str(pkg.get("name") or ""), str(pkg.get("version") or "")
    try:
        if pkg["registry"] == "npm":
            locks.npm_package_install(name, version)
        else:
            locks.pip_first_install(name, version)
    except locks.LockError:
        return None
    return {"registry": pkg["registry"], "name": name, "version": version,
            "repo_name_matches": pkg.get("repo_name_matches")}


def _box_read(project: str, rel: str, cap: int = 2_000_000) -> Optional[bytes]:
    """Read one file out of a box project (as data)."""
    if not re.fullmatch(r"[A-Za-z0-9._][A-Za-z0-9._/@-]{0,200}", rel) or ".." in rel.split("/"):
        return None
    out = _tmp_path()
    try:
        res = driver.run(driver.BoxRequest(
            run_id=driver.new_run_id(), project=project, argv=["cat", "--", rel],
            what="box_traffic", limits={"wall_s": 60, "mem_mb": 256, "disk_mb": DISK_MB},
            stdout_mode="raw"), stdout_path=out)
        if not res.ok:
            return None
        with open(out, "rb") as fh:
            data = fh.read(cap + 1)
        return data if len(data) <= cap else None
    except OSError:
        return None
    finally:
        _unlink(out)


def _box_step(project: str, argv: list, *, hosts: list, wall: int = INSTALL_WALL_S) -> driver.BoxResult:
    return driver.run(driver.BoxRequest(
        run_id=driver.new_run_id(), project=project, argv=argv, allow_hosts=list(hosts),
        what="box_install", limits={"wall_s": wall, "mem_mb": 2048, "disk_mb": DISK_MB,
                                    "cpu_s": wall}))


def _npm_meta(project: str, name: str) -> dict:
    data = _box_read(project, f"node_modules/{name}/package.json", 1_000_000)
    try:
        meta = json.loads(data.decode("utf-8")) if data else {}
    except (UnicodeDecodeError, ValueError):
        meta = {}
    return meta if isinstance(meta, dict) else {}


def _repo_matches(meta: dict, name: str) -> Optional[bool]:
    repo = meta.get("repository")
    url = repo.get("url") if isinstance(repo, dict) else repo
    if not isinstance(url, str) or not url.strip():
        return None
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    if tail.endswith(".git"):
        tail = tail[:-4]
    short = name.rsplit("/", 1)[-1]
    return tail.lower() == short.lower() or tail.lower() in (f"{short}.js", f"node-{short}",
                                                             f"{short}-cli")


def install(found: dict, iid: str, *, say: Callable[[str], None]) -> dict:
    """Install the exact package into box project *iid*, record its lock and
    read its commands. Returns the draft for Drawbridge plus ``specs``, or
    ``{"error": code}``. Nothing of the package runs until the draft passes
    its first check; ``--help`` runs only after that, with no internet."""
    pkg = _package_of(found)
    if pkg is None:
        return {"error": "not_exact"}
    if pkg["registry"] == "github":
        return _install_from_forge(found, iid, pkg)
    name = pkg["name"]
    draft: dict = {"kind": KIND, "id": iid, "name": str(found.get("friendly") or name)[:80],
                   "package": pkg, "allow_hosts": []}
    if pkg["registry"] == "npm":
        init = _box_step(iid, ["sh", "-c", "mkdir -p .home && echo '{\"private\":true}' > package.json"],
                         hosts=[], wall=60)
        if not init.ok:
            return {"error": "install"}
        argv = locks.npm_package_install(name, pkg["version"])
        res = _box_step(iid, argv, hosts=locks.NPM_HOSTS)
        if not res.ok:
            return {"error": "install"}
        lock_bytes = _box_read(iid, locks.NPM_LOCK, locks.MAX_LOCK_BYTES)
        if lock_bytes is None:
            return {"error": "install"}
        problems = locks.scan_npm_lock(lock_bytes)
        meta = _npm_meta(iid, name)
        installed = {"name": str(meta.get("name") or ""), "version": str(meta.get("version") or "")}
        lock_facts = {"kind": "npm", "data": lock_bytes, "offsite": problems,
                      "hashes": sum(1 for _ in _count_npm(lock_bytes)),
                      "package_json": _box_read(iid, "package.json", 100_000) or b""}
        bins = meta.get("bin")
        if isinstance(bins, str):
            bins = {name.rsplit("/", 1)[-1]: bins}
        bin_names = [b for b in (bins or {}) if isinstance(b, str) and _BIN_RE.match(b)]
        bin_paths = {b: f"/work/node_modules/.bin/{b}" for b in bin_names}
        draft.update({"install_flags": argv, "license": meta.get("license"),
                      "repo_name_matches": pkg.get("repo_name_matches") is not False
                      and _repo_matches(meta, name) is not False, "installed": installed})
    else:
        venv = _box_step(iid, locks.VENV, hosts=[], wall=300)
        if not venv.ok:
            return {"error": "install"}
        argv = locks.pip_first_install(name, pkg["version"])
        res = _box_step(iid, argv, hosts=locks.PIP_HOSTS)
        if not res.ok:
            return {"error": "install"}
        report = _box_read(iid, ".pip-report.json", 5_000_000)
        try:
            text, offsite = locks.pip_lock_parts(report or b"")
        except locks.LockError:
            return {"error": "no_hashes"}
        lock_bytes = text.encode("utf-8")
        lock_facts = {"kind": "pip", "data": lock_bytes, "offsite": offsite,
                      "hashes": locks.pip_lock_count(text)}
        meta = _pip_meta(report or b"", name)
        installed = {"name": meta.get("name", ""), "version": meta.get("version", "")}
        bins_res = _box_step(iid, ["sh", "-c", "ls -1 /work/venv/bin > /work/.pip-bins"],
                             hosts=[], wall=60)
        listing = _box_read(iid, ".pip-bins", 100_000) if bins_res.ok else None
        skip = re.compile(r"^(activate.*|pip[0-9.]*|python[0-9.]*|Activate\.ps1|__pycache__)\Z")
        bin_names = [b for b in (listing or b"").decode("utf-8", "replace").split()
                     if _BIN_RE.match(b) and not skip.match(b)]
        bin_paths = {b: f"/work/venv/bin/{b}" for b in bin_names}
        draft.update({"install_flags": argv, "license": meta.get("license"),
                      "installed": installed})
    draft["lock"] = {"sha256": locks.sha256(lock_facts["data"]) if lock_facts["data"] else "",
                     "hashes": lock_facts["hashes"], "offsite": lock_facts["offsite"]}
    draft["_lock_data"] = lock_facts
    draft["bins"] = bin_paths
    return draft


#: The pinned archive's name inside the box project, and in the lock.
FORGE_ARCHIVE = "src.tgz"
_PYTHON_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg")


def _archive_commit(path: str) -> str:
    """The commit a forge archive says it holds (see integrate.fetch)."""
    fetch = _optional("puffpai.integrate.fetch")
    return fetch.archive_commit(path) if fetch is not None else ""


def _file_hashes(path: str) -> tuple[str, str]:
    """(sha-256 hex, npm integrity "sha512-<base64>") of the file at *path*."""
    import base64
    h256, h512 = hashlib.sha256(), hashlib.sha512()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h256.update(chunk)
            h512.update(chunk)
    return h256.hexdigest(), "sha512-" + base64.b64encode(h512.digest()).decode("ascii")


def forge_lock_problems(lock_bytes: bytes, name: str, version: str,
                        integrity: str) -> Optional[list[str]]:
    """The lock problems of a tool installed from its pinned archive.

    Exactly one entry may come from outside the npm registry: the tool itself,
    ``node_modules/<name>`` at *version*, resolved from ``file:src.tgz`` with
    *integrity* (the sha512 of the archive BuildBot put there). Every other
    entry is judged as always (:func:`locks.scan_npm_lock`). None when that
    entry is missing or differs: then it is not the exact version."""
    try:
        lock = json.loads(lock_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    packages = lock.get("packages") if isinstance(lock, dict) else None
    if not isinstance(packages, dict):
        return None
    entry = packages.get(f"node_modules/{name}")
    if not isinstance(entry, dict) or entry.get("resolved") != f"file:{FORGE_ARCHIVE}" \
            or entry.get("integrity") != integrity or entry.get("version") != version \
            or entry.get("link"):
        return None
    rest = dict(lock)
    rest["packages"] = {k: v for k, v in packages.items() if k != f"node_modules/{name}"}
    return locks.scan_npm_lock(json.dumps(rest).encode("utf-8"))


def _project_tar(tar_path: str, archive_path: str) -> None:
    """The first files of a forge tool's box project: an empty private
    package, a home folder for npm, and the pinned archive."""
    import io
    import tarfile
    with tarfile.open(tar_path, "w", format=tarfile.USTAR_FORMAT) as tf:
        home = tarfile.TarInfo(".home")
        home.type, home.mode = tarfile.DIRTYPE, 0o755
        tf.addfile(home)
        body = b'{"private":true}\n'
        info = tarfile.TarInfo("package.json")
        info.size, info.mode = len(body), 0o644
        tf.addfile(info, io.BytesIO(body))
        info = tarfile.TarInfo(FORGE_ARCHIVE)
        info.size, info.mode = os.path.getsize(archive_path), 0o644
        with open(archive_path, "rb") as fh:
            tf.addfile(info, fh)


def _install_from_forge(found: dict, iid: str, pkg: dict) -> dict:
    """Install a command-line tool from its code forge, at the pinned commit.

    The archive comes through the one download exit for code (host, pin and
    size checked there; the recorded fingerprint too, when there is one), and
    must say it holds exactly the pinned commit. Only an npm project is
    installed: from that archive, in the box, with ``--ignore-scripts``, and
    its lock must name that archive by its own fingerprint. A Python project
    would have to run its own build code to install, which BuildBot does not
    do. Any mismatch fails closed (``not_exact``)."""
    import tempfile
    fetch = _optional("puffpai.integrate.fetch")
    if fetch is None:
        return {"error": "install"}
    try:
        got = fetch.fetch(iid, found, expect_sha256=pkg.get("archive_sha256"))
    except fetch.FetchRefused as exc:
        if exc.code in ("not_pinned", "pin_changed", "wrong_host"):
            return {"error": "not_exact"}
        # Nothing reached the box: say why the download was not used (a
        # download that unpacks to over 100 times its size is never opened).
        detail = str(getattr(exc, "detail", "") or "")
        reason = detail if detail in W.CLI_DOWNLOAD_REASONS else exc.code
        if reason not in W.CLI_DOWNLOAD_REASONS:
            reason = "download_failed"
        return {"error": "download", "reason": reason}
    try:
        archive = str(got.get("path") or "")
        sha, integrity = _file_hashes(archive)
        if str(got.get("commit") or "").lower() != pkg["commit"] \
                or _archive_commit(archive) != pkg["commit"] \
                or sha != str(got.get("sha256") or "") \
                or (pkg.get("archive_sha256") and sha != pkg["archive_sha256"]):
            return {"error": "not_exact"}
        src = str(got.get("src_dir") or "")
        pj_path = os.path.join(src, "package.json")
        if not os.path.isfile(pj_path) or os.path.islink(pj_path):
            if any(os.path.isfile(os.path.join(src, m)) for m in _PYTHON_MARKERS):
                return {"error": "scripts_allowed"}
            return {"error": "install"}
        with open(pj_path, "rb") as fh:
            raw = fh.read(1_000_001)
        try:
            pj = json.loads(raw.decode("utf-8")) if len(raw) <= 1_000_000 else None
        except (UnicodeDecodeError, ValueError):
            pj = None
        if not isinstance(pj, dict) or not pj.get("bin"):
            return {"error": "install"}
        name, version = str(pj.get("name") or ""), str(pj.get("version") or "")
        try:
            locks.npm_package_install(name, version)       # a plain name and version
        except locks.LockError:
            return {"error": "not_exact"}
    finally:
        _drop_unpacked(iid)
    with tempfile.TemporaryDirectory() as tmp:
        tar_path = os.path.join(tmp, "project.tar")
        _project_tar(tar_path, archive)
        put = driver.put_project(iid, tar_path, disk_mb=DISK_MB)
    if not put.ok:
        return {"error": "install"}
    argv = locks.NPM_INSTALL + ["--save-exact", f"./{FORGE_ARCHIVE}"]
    res = _box_step(iid, argv, hosts=locks.NPM_HOSTS)
    if not res.ok:
        return {"error": "install"}
    lock_bytes = _box_read(iid, locks.NPM_LOCK, locks.MAX_LOCK_BYTES)
    if lock_bytes is None:
        return {"error": "install"}
    problems = forge_lock_problems(lock_bytes, name, version, integrity)
    if problems is None:
        return {"error": "not_exact"}
    meta = _npm_meta(iid, name)
    installed = {"name": str(meta.get("name") or ""), "version": str(meta.get("version") or "")}
    bins = meta.get("bin")
    if isinstance(bins, str):
        bins = {name.rsplit("/", 1)[-1]: bins}
    bin_names = [b for b in (bins or {}) if isinstance(b, str) and _BIN_RE.match(b)]
    source = {"host": pkg["host"], "repo": pkg["name"], "commit": pkg["commit"],
              "archive_sha256": sha}
    # The code is the project the plan showed, by its commit: the name check
    # against a registry page (starjacking) does not apply.
    exact = {"registry": "npm", "name": name, "version": version,
             "repo_name_matches": True, "source": source}
    lock_facts = {"kind": "npm", "data": lock_bytes, "offsite": problems,
                  "hashes": sum(1 for _ in _count_npm(lock_bytes)),
                  "package_json": _box_read(iid, "package.json", 100_000) or b""}
    draft: dict = {"kind": KIND, "id": iid, "name": str(found.get("friendly") or name)[:80],
                   "package": exact, "allow_hosts": [], "install_flags": argv,
                   "license": meta.get("license"), "repo_name_matches": True,
                   "installed": installed}
    draft["lock"] = {"sha256": locks.sha256(lock_bytes), "hashes": lock_facts["hashes"],
                     "offsite": problems}
    draft["_lock_data"] = lock_facts
    draft["bins"] = {b: f"/work/node_modules/.bin/{b}" for b in bin_names}
    return draft


def _drop_unpacked(iid: str) -> None:
    """The unpacked copy is only read for its package facts: it goes."""
    sa = _optional("puffpai.security.safe_archive")
    if sa is None:
        return
    try:
        root = sa.extraction_root()
        where = os.path.join(root, iid)
        if os.path.lexists(where):
            sa.safe_rmtree(where, allowed_roots=[root])
    except Exception as exc:  # noqa: BLE001 - tidying only
        logger.info("cli tools: unpacked copy left (%s)", type(exc).__name__)


def _count_npm(lock_bytes: bytes):
    try:
        lock = json.loads(lock_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return
    for _name, entry in locks._npm_entries(lock):  # noqa: SLF001 - same package
        if isinstance(entry.get("integrity"), str) and entry["integrity"]:
            yield 1


def _pip_meta(report: bytes, name: str) -> dict:
    try:
        data = json.loads(report.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {}
    want = re.sub(r"[-_.]+", "-", name).lower()
    for item in (data.get("install") if isinstance(data, dict) else None) or []:
        meta = item.get("metadata") if isinstance(item, dict) else None
        if not isinstance(meta, dict):
            continue
        if re.sub(r"[-_.]+", "-", str(meta.get("name") or "")).lower() == want:
            lic = meta.get("license_expression") or meta.get("license") or ""
            if not lic or len(str(lic)) > 60:
                for c in meta.get("classifier") or []:
                    if isinstance(c, str) and c.startswith("License :: OSI Approved ::"):
                        lic = c.split("::")[-1].strip()
                        break
            return {"name": name if re.sub(r"[-_.]+", "-", name).lower() == want else "",
                    "version": str(meta.get("version") or ""), "license": str(lic or "")}
    return {}


def _help_runner(iid: str) -> Callable[[list[str]], str]:
    def help_of(argv: list[str]) -> str:
        out = _tmp_path()
        try:
            res = driver.run(driver.BoxRequest(
                run_id=driver.new_run_id(), project=iid, argv=list(argv) + ["--help"],
                what="box_traffic", limits={"wall_s": 30, "mem_mb": 512, "disk_mb": DISK_MB},
                stdout_mode="raw"), stdout_path=out)
            text, _cut = _read_capped(out, HELP_MAX_BYTES)
            if not text.strip():
                text = res.stderr_tail
            return text
        finally:
            _unlink(out)
    return help_of


def _progress_of(ctx: Any) -> Callable[[str], None]:
    fn = getattr(ctx, "progress", None)
    return fn if callable(fn) else (lambda line: None)


def _found_for(item: dict, ctx: Any) -> Optional[dict]:
    cand = getattr(ctx, "candidate", None)
    if isinstance(cand, dict) and cand.get("package"):
        return cand
    fp = _optional("puffpai.wizard.find_parts")
    fn = getattr(fp, "found_for", None) if fp is not None else None
    if not callable(fn):
        return None
    got = fn(str(item.get("id") or ""))
    return got if isinstance(got, dict) else None


def _result(status: str, line: str, lines: list, **extra: Any) -> dict:
    out = {"status": status, "line": line, "lines": list(lines)}
    out.update(extra)
    return out


def apply_cli_tool(item: dict, ctx: Any) -> dict:
    """Plan / Add page applier for ``cli_tool`` (factory I-6, add_more)."""
    found = _found_for(item, ctx)
    label = str(item.get("label") or item.get("id") or "This tool")[:120]
    if not isinstance(found, dict):
        return _result("failed", f"{label}: {W.CLI_GONE}", [])
    if getattr(ctx, "online", True) is False:
        return _result("failed", f"{label}: {W.SETUP_OFFLINE}", [])
    from puffpai.box import plan
    st = plan.ensure_box_for(item, ctx)
    name = str(found.get("friendly") or (found.get("package") or {}).get("name") or label)[:80]
    if st.state in ("no_wsl", "wsl_off"):
        driver.add_waiting({"item": dict(item), "found": found})
        return _result("waiting", W.CLI_WAITING.format(name=name), [])
    if not st.ready:
        return _result("failed", W.CLI_NEEDS_BOX.format(name=name), [])
    return add_found(found, item=item, say=_progress_of(ctx), by=_by(ctx))


def _by(ctx: Any) -> str:
    via = str(getattr(ctx, "via", "") or "")
    return "add_page" if via else "wizard"


def add_found(found: dict, *, item: dict, say: Callable[[str], None], by: str = "wizard") -> dict:
    """Install, check and wire one command-line tool (the box is ready)."""
    lines: list[str] = []

    def tell(line: str) -> None:
        lines.append(line)
        try:
            say(line)
        except Exception:
            pass

    from puffpai import drawbridge
    rec_mod = _records()
    pkg = _package_of(found)
    name = str(found.get("friendly") or (pkg or {}).get("name") or "This tool")[:80]
    if rec_mod is None:
        return _result("failed", f"{name}: {W.SETUP_NOT_IN_BUILD}", lines)
    if pkg is None:
        return _result("refused", W.CLI_STOPPED.format(reason=W.CLI_REASONS["not_exact"]), lines)
    iid = rec_mod.new_id(name)
    rec = rec_mod.blank(iid, name=name, purpose="Command-line tool", by=by,
                        need=str((found.get("meaning") or {}).get("job") or "") or None)
    repo = found.get("repo") if isinstance(found.get("repo"), dict) else {}
    rec["from"].update({"source": str(found.get("source") or "")[:20],
                        "url": str(found.get("url") or "")[:300],
                        "license_reported": str(repo.get("license_reported") or "")[:60]})
    rec["mode"] = KIND
    rec["status"] = "building"
    rec["box"] = {"project": iid, "image_mb": DISK_MB}
    rec["plan_facts"].update({"mode": KIND, "box": True})
    rec_mod.save(rec)
    tell(W.CLI_ADDING.format(name=name))
    tell(W.CLI_INSTALLING.format(name=name))
    draft = install(found, iid, say=tell)
    if draft.get("error"):
        code = draft["error"]
        stopped = code in ("not_exact", "scripts_allowed")
        why = W.CLI_DOWNLOAD_REASONS.get(str(draft.get("reason") or "")) \
            if code == "download" else None
        status = "refused" if stopped or draft.get("reason") == "bomb" else "failed"
        rec["status"] = status
        rec_mod.save(rec)
        driver.remove_project(iid)
        line = (W.CLI_STOPPED.format(reason=W.CLI_REASONS[code]) if stopped
                else W.CLI_LEFT_OUT.format(name=name, reason=why) if why
                else W.CLI_INSTALL_FAILED.format(name=name))
        tell(line)
        return _result(status, line, lines, integration_id=iid)
    lock_facts = draft.pop("_lock_data")
    bins = draft.pop("bins")
    draft["argv_templates"] = []
    first = drawbridge.check_addition(dict(draft), by="toolsmith")
    if first.get("verdict") == "block":
        return _stop(rec, rec_mod, first, lines, tell, iid)
    if first.get("verdict") != "allow":
        return _left_out(rec, rec_mod, first, lines, tell, iid, name)
    try:
        lock_rec = locks.record(iid, lock_facts["kind"], lock_facts["data"])
    except (OSError, locks.LockError):
        return _stop(rec, rec_mod, {"reasons": ["no_hashes"]}, lines, tell, iid)
    rec["lock"] = {"kind": lock_rec["kind"], "path": lock_rec["path"], "sha256": lock_rec["sha256"]}
    tell(W.CLI_LEARNING.format(name=name))
    help_of = _help_runner(iid)
    specs: list = []
    for bin_name, bin_path in list(bins.items())[:12]:
        specs += derive_specs(bin_path, bin_name, help_of)
        if len(specs) >= MAX_ACTIONS:
            break
    specs = specs[:MAX_ACTIONS]
    draft["argv_templates"] = [s["argv"] for s in specs]
    final = drawbridge.check_addition(dict(draft), by="toolsmith")
    if final.get("verdict") == "block":
        return _stop(rec, rec_mod, final, lines, tell, iid)
    if final.get("verdict") != "allow":
        return _left_out(rec, rec_mod, final, lines, tell, iid, name)
    verdict = {"verdict": "allow", "rows_sha": str(final.get("rows_sha") or ""),
               "at": str(final.get("checked_at") or _now()),
               "app_sha": lock_rec["sha256"]}
    _save_json(_cli_path(iid), {"package": draft["package"], "installed": draft.get("installed"),
                                "specs": specs, "verdict": verdict,
                                "package_json": (lock_facts.get("package_json") or b"").decode(
                                    "utf-8", "replace")[:100_000]})
    rec["drawbridge"] = {"verdict": "allow", "rows_sha": verdict["rows_sha"],
                         "checker_version": CHECKER_VERSION, "checked_at": verdict["at"],
                         "line": str(final.get("line") or "")[:300]}
    rec["actions"] = [f"{iid}__{s['name']}" for s in specs][:60]
    rec["status"] = "ready"
    rec["added_at"] = _now()
    rec_mod.save(rec)
    tell(W.CLI_READY.format(name=name))
    if specs:
        tell(W.CLI_ABILITIES.format(abilities=", ".join(
            s["command"] for s in specs[:8]) + (" and more" if len(specs) > 8 else "")))
    else:
        tell(W.CLI_NO_ACTIONS)
    return _result("added", W.CLI_READY.format(name=name), lines, integration_id=iid,
                   undo={"kind": "integration", "ref": iid})


def _reason_words(verdict: dict) -> str:
    first = next(iter(verdict.get("reasons") or []), "")
    return W.CLI_REASONS.get(first, "it did not pass Drawbridge's checks")


def _stop(rec, rec_mod, verdict, lines, tell, iid) -> dict:
    rec["status"] = "refused"
    rec_mod.save(rec)
    driver.remove_project(iid)
    line = W.CLI_STOPPED.format(reason=_reason_words(verdict))
    tell(line)
    return _result("refused", line, lines, integration_id=iid)


def _left_out(rec, rec_mod, verdict, lines, tell, iid, name) -> dict:
    """A hold. There is no second yes for a command-line tool, so it is
    left out, honestly, with the reason (and nothing of it kept)."""
    rec["status"] = "held"
    rec["held"] = {"reasons": [str(r) for r in verdict.get("reasons") or []][:6],
                   "ticket_target": ""}
    rec_mod.save(rec)
    driver.remove_project(iid)
    line = W.CLI_LEFT_OUT.format(name=name, reason=_reason_words(verdict))
    tell(line)
    return _result("held", line, lines, integration_id=iid)


def _forge_archive_again(iid: str, source: Any) -> Optional[bytes]:
    """The kept pinned archive of a forge tool, only while it is byte for byte
    the one first installed (its sha-256 and its commit)."""
    fetch = _optional("puffpai.integrate.fetch")
    if fetch is None or not isinstance(source, dict):
        return None
    want = str(source.get("archive_sha256") or "")
    commit = str(source.get("commit") or "")
    path = fetch.download_path(iid)
    try:
        if os.path.islink(path) or os.path.getsize(path) > fetch.MAX_ARCHIVE_BYTES:
            return None
        with open(path, "rb") as fh:
            blob = fh.read(fetch.MAX_ARCHIVE_BYTES + 1)
    except OSError:
        return None
    if not re.fullmatch(r"[0-9a-f]{64}", want) or hashlib.sha256(blob).hexdigest() != want \
            or _archive_commit(path) != commit:
        return None
    return blob


def rebuild(iid: str) -> driver.BoxResult:
    """Reinstall a tool from its RECORDED lock only (never an update)."""
    data = _load_json(_cli_path(iid))
    rec_lock = locks.recorded(iid)                      # raises lock_changed if edited
    if not data or rec_lock is None:
        return driver.BoxResult(ok=False, refused="lock_changed")
    driver.remove_project(iid)
    import tempfile
    import tarfile
    import io
    with tempfile.TemporaryDirectory() as tmp:
        tar_path = os.path.join(tmp, "lock.tar")
        with tarfile.open(tar_path, "w", format=tarfile.USTAR_FORMAT) as tf:
            files = {locks.NPM_LOCK if rec_lock["kind"] == "npm" else locks.PIP_LOCK: rec_lock["data"]}
            if rec_lock["kind"] == "npm":
                pj = str(data.get("package_json") or "")
                if not pj:
                    return driver.BoxResult(ok=False, refused="lock_changed")
                files["package.json"] = pj.encode("utf-8")
                source = (data.get("package") or {}).get("source")
                if source:
                    blob = _forge_archive_again(iid, source)
                    if blob is None:
                        return driver.BoxResult(ok=False, refused="lock_changed")
                    files[FORGE_ARCHIVE] = blob
            for fname, blob in files.items():
                info = tarfile.TarInfo(fname)
                info.size = len(blob)
                tf.addfile(info, io.BytesIO(blob))
        put = driver.put_project(iid, tar_path, disk_mb=DISK_MB)
        if not put.ok:
            return put
    if rec_lock["kind"] == "npm":
        return _box_step(iid, locks.NPM_CI, hosts=locks.NPM_HOSTS)
    venv = _box_step(iid, locks.VENV, hosts=[], wall=300)
    if not venv.ok:
        return venv
    return _box_step(iid, locks.pip_rebuild(), hosts=locks.PIP_HOSTS)


def apply_waiting(entry: dict, progress: Callable[[str], None]) -> dict:
    """finish_waiting's applier: the box is ready now."""
    found = entry.get("found") if isinstance(entry.get("found"), dict) else None
    item = entry.get("item") if isinstance(entry.get("item"), dict) else {}
    if found is None:
        return {"status": "failed", "line": W.CLI_GONE}
    return add_found(found, item=item, say=progress, by="wizard")


__all__ = ["BLOCK_CODES", "CHECKER_VERSION", "HOLD_CODES", "KIND", "SOURCE_KIND",
           "add_found", "apply_cli_tool", "apply_waiting", "argv_problems", "cli_tool_rows",
           "derive_specs", "folder_grant", "grant_folder", "install", "license_family",
           "list_actions", "list_subcommands", "parse_help", "rebuild", "run_action"]
