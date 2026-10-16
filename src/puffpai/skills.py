"""Skills: folders with one ``SKILL.md`` (the agentskills.io format).

A skill lives in ``<state>/skills/<name>/SKILL.md``, with optional
``scripts/``, ``references/``, ``templates/`` and ``assets/`` beside it. The
format is the open one shared by Hermes Agent, OpenClaw, Claude and about 40
other agents, so anything on ClawHub, skills.sh or the Hermes index works here
as it is.

How the brain uses them (copied from Hermes ``agent/prompt_builder.py``,
"available_skills", and OpenClaw ``docs/tools/skills.md``, "Token impact"):

* the system prompt lists every installed skill by name and one-line
  description only (:func:`prompt_block`);
* when a skill fits, the model reads the whole ``SKILL.md`` with the
  ``skill_view`` tool (:func:`view`), and any file it names with
  ``skill_view(name, path)``.

Nothing here runs anything a skill says. Reading a skill is reading a text
file; running a command a skill asks for goes through the chat loop's
``run_command`` tool, which asks first.

Parts of the frontmatter parsing and the prompt wording are adapted from
Hermes Agent (MIT, Copyright (c) 2025 Nous Research; see THIRD_PARTY_NOTICES.md).

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional

#: The agentskills.io limits: a name of at most 64 characters (lowercase
#: letters, digits and hyphens), a description of at most 1024.
NAME_MAX = 64
DESCRIPTION_MAX = 1024
#: What one line of the prompt list may hold (Hermes keeps it short too).
LIST_DESCRIPTION_MAX = 200
#: The most of one file ``skill_view`` hands the model.
VIEW_MAX_CHARS = 60_000
#: The most skills listed in the prompt (a small model is not swamped).
PROMPT_MAX_SKILLS = 80

SKILL_FILE = "SKILL.md"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
#: Folders under the skills folder that are BuildBot's own, never a skill.
_OWN_FOLDERS = frozenset({".staging", ".removed"})


def skills_dir(create: bool = False) -> str:
    """``<state>/skills``."""
    from puffpai.config import get_state_dir
    path = os.path.join(get_state_dir(), "skills")
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def safe_name(value: Any) -> str:
    """A folder name in the agentskills.io shape, from any text ("" if none)."""
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    text = re.sub(r"-{2,}", "-", text)[:NAME_MAX].strip("-")
    return text if _NAME_RE.match(text) else ""


# ── SKILL.md ────────────────────────────────────────────────────────────────

def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _simple_yaml(text: str) -> dict[str, Any]:
    """``key: value`` lines, with ``>``/``|`` blocks and indented lines folded
    into the key above (Hermes's fallback when YAML will not parse)."""
    out: dict[str, Any] = {}
    key = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw[:1] in (" ", "\t") and key is not None:
            more = raw.strip()
            prev = out.get(key)
            if isinstance(prev, str) and not more.startswith("- "):
                out[key] = (prev + " " + more).strip()
            continue
        if ":" not in raw:
            continue
        k, v = raw.split(":", 1)
        key = k.strip()
        v = v.strip()
        out[key] = "" if v in (">", "|", ">-", "|-") else _unquote(v)
    return out


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """``(frontmatter, body)`` of a ``SKILL.md``. No frontmatter gives ``({}, text)``.

    YAML is read with PyYAML's safe loader when it is installed, else line by
    line. A leading byte-order mark is dropped first (Windows editors)."""
    content = str(content or "").removeprefix("﻿")
    if not content.startswith("---"):
        return {}, content
    end = re.search(r"\n---\s*(?:\n|$)", content[3:])
    if not end:
        return {}, content
    head = content[3:end.start() + 3]
    body = content[end.end() + 3:]
    data: dict[str, Any] = {}
    try:
        import yaml  # type: ignore[import-untyped]
        parsed = yaml.safe_load(head)
        if isinstance(parsed, dict):
            data = parsed
    except Exception:  # noqa: BLE001 - malformed YAML falls back to lines
        data = _simple_yaml(head)
    return data, body


def _one_line(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + "..."


def read_skill_file(path: str) -> Optional[dict[str, Any]]:
    """``{"name","description","folder","path","frontmatter"}`` for one
    ``SKILL.md`` file, or None when it cannot be read."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read(512 * 1024)
    except OSError:
        return None
    meta, _body = parse_frontmatter(text)
    folder = os.path.basename(os.path.dirname(path))
    name = str(meta.get("name") or folder).strip() or folder
    description = _one_line(meta.get("description"), DESCRIPTION_MAX)
    return {"name": name, "folder": folder, "description": description,
            "path": path, "frontmatter": meta}


def installed() -> list[dict[str, Any]]:
    """Every skill in the skills folder, by folder name. Never raises."""
    root = skills_dir()
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for folder in names:
        if folder in _OWN_FOLDERS or folder.startswith("."):
            continue
        path = os.path.join(root, folder, SKILL_FILE)
        if not os.path.isfile(path):
            continue
        rec = read_skill_file(path)
        if rec is not None:
            out.append(rec)
    return out


def find(name: Any) -> Optional[dict[str, Any]]:
    """The installed skill called *name* (its folder name or its own name)."""
    want = str(name or "").strip().lower()
    if not want:
        return None
    rows = installed()
    for rec in rows:
        if rec["folder"].lower() == want:
            return rec
    for rec in rows:
        if rec["name"].lower() == want:
            return rec
    return None


# ── what a skill needs: programs and keys ──────────────────────────────────
#
# Copied in shape from the two places skills declare it, so a skill from
# ClawHub or the Hermes index is read as its author wrote it:
#
# * OpenClaw (``docs/tools/skills.md``, "Gating"): ``metadata.openclaw`` holds
#   ``requires.bins`` (each must be on the path), ``requires.anyBins`` (one of
#   them), ``requires.env`` (variables it needs) and ``primaryEnv`` (the one
#   that is its API key). Older skills put the same block under
#   ``metadata.clawdbot`` or ``metadata.clawdis``; those are read too.
# * Hermes Agent (``creating-skills.md``, "Environment Variable
#   Requirements"): ``required_environment_variables``, a list of
#   ``{name, prompt, help, required_for}``, and the legacy
#   ``prerequisites: {env_vars, commands}``.
#
# Reading these runs nothing. The names are checked: a program is a plain
# file name, a key is a plain variable name.

_BIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}\Z")
_ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
_METADATA_KEYS = ("openclaw", "clawdbot", "clawdis")
#: At most this many programs, and this many keys, from one skill.
NEEDS_MAX = 12
#: Programs the bot always has (python and python3 start its own Python).
_ALWAYS_HAS = frozenset({"python", "python3", "puffpai"})


def _as_mapping(value: Any) -> dict:
    """A frontmatter value that should be a mapping: a dict, or the JSON (or
    JSON5 with trailing commas) text of one. Anything else is {}."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip().startswith("{"):
        return {}
    import json
    text = value.strip()
    for attempt in (text, re.sub(r",\s*([}\]])", r"\1", text)):
        try:
            loaded = json.loads(attempt)
        except ValueError:
            continue
        return loaded if isinstance(loaded, dict) else {}
    return {}


def _names(value: Any, pattern: "re.Pattern[str]") -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, dict):
            item = item.get("name")
        if isinstance(item, str) and pattern.match(item.strip()) and item.strip() not in out:
            out.append(item.strip())
    return out[:NEEDS_MAX]


def requirements(meta: Any) -> dict[str, Any]:
    """What a skill's frontmatter says it needs.

    ``{"bins": [...], "any_bins": [...], "env": [...], "primary_env": "",
    "env_help": {name: {"prompt", "help"}}}``. Never raises."""
    meta = meta if isinstance(meta, dict) else {}
    bins: list[str] = []
    any_bins: list[str] = []
    env: list[str] = []
    primary = ""
    help_for: dict[str, dict[str, str]] = {}
    blocks: list[dict] = [_as_mapping(meta.get("requires"))]
    metadata = _as_mapping(meta.get("metadata"))
    for key in _METADATA_KEYS:
        block = _as_mapping(metadata.get(key))
        if block:
            blocks.append(_as_mapping(block.get("requires")))
            got = block.get("primaryEnv")
            if isinstance(got, str) and _ENV_RE.match(got.strip()) and not primary:
                primary = got.strip()
    for block in blocks:
        bins += _names(block.get("bins"), _BIN_RE)
        any_bins += _names(block.get("anyBins") or block.get("any_bins"), _BIN_RE)
        env += _names(block.get("env"), _ENV_RE)
    hermes = meta.get("required_environment_variables")
    env += _names(hermes, _ENV_RE)
    if isinstance(hermes, list):
        for item in hermes:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                help_for[item["name"].strip()] = {
                    "prompt": _one_line(item.get("prompt"), 120),
                    "help": _one_line(item.get("help"), 200)}
    legacy = _as_mapping(meta.get("prerequisites"))
    env += _names(legacy.get("env_vars"), _ENV_RE)
    bins += _names(legacy.get("commands"), _BIN_RE)
    if primary:
        env.insert(0, primary)
    env = list(dict.fromkeys(env))[:NEEDS_MAX]
    return {"bins": list(dict.fromkeys(bins))[:NEEDS_MAX],
            "any_bins": list(dict.fromkeys(any_bins))[:NEEDS_MAX],
            "env": env, "primary_env": primary,
            "env_help": {k: v for k, v in help_for.items() if k in env}}


def requirements_of(name: Any) -> dict[str, Any]:
    """:func:`requirements` of the installed skill *name* (empty if none)."""
    rec = find(name)
    return requirements((rec or {}).get("frontmatter"))


def has_program(name: str) -> bool:
    """Is the program *name* on this computer's path, in the bot's own tools
    folders (``puffpai.deps``: what "Install what's missing" put there), or
    one the bot always has?"""
    if name in _ALWAYS_HAS:
        return True
    try:
        from puffpai import deps
        return deps.has_program(name)
    except Exception:  # noqa: BLE001 - fall back to the path alone
        pass
    import shutil
    try:
        return shutil.which(name) is not None
    except (OSError, ValueError):
        return False


def missing_programs(req: dict[str, Any]) -> list[str]:
    """The programs a skill needs that this computer does not have. For
    ``any_bins`` the whole group is named when none of them is there."""
    missing = [b for b in req.get("bins") or [] if not has_program(b)]
    group = req.get("any_bins") or []
    if group and not any(has_program(b) for b in group):
        missing.append(" or ".join(group))
    return missing


def needs_line(req: dict[str, Any], missing: list[str], keys_set: dict[str, bool]) -> str:
    """One plain line: which programs are missing and which keys it needs."""
    parts: list[str] = []
    if missing:
        parts.append(f"It needs the program{'s' if len(missing) > 1 else ''} "
                     f"{_and_list(missing)}, which this computer does not have yet.")
    wanted = [k for k in req.get("env") or [] if not keys_set.get(k)]
    if wanted:
        parts.append(f"It needs your key{'s' if len(wanted) > 1 else ''} "
                     f"{_and_list(wanted)}.")
    elif req.get("env"):
        parts.append("Its key is in your vault.")
    return " ".join(parts)


def _and_list(items: list[str]) -> str:
    items = [str(i) for i in items]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


# ── how a skill runs: a program here, an outside service, or words ─────────
#
# The one rule: a skill runs as
#
# * ``cli``: a program on this computer. It declares the programs it needs
#   (``requires.bins`` / ``anyBins``), install specs, ``allowed-tools:
#   Bash(x ...)``, it ships its own code (``scripts/``, a package file), or
#   its own shell examples run a program (``curl ...``; lines that install
#   other skills do not count). The brain runs it through ``run_command`` (a
#   yes per command; a key only through the one checked request).
# * ``remote``: an outside service. It declares no local program and names an
#   MCP address, an OpenAPI description, an API host, or says it is a hosted
#   remote tool. It is a TOOL when it has an MCP or OpenAPI address, else a
#   DATA source (the host is the source). It is NEVER a shell command:
#   ``run_command`` refuses it (``brain_commands``), and the skills list tells
#   the brain so.
# * ``guide``: words only. The brain reads it; nothing runs.
#
# Reading runs nothing: the frontmatter, the text and the folder's file names.

_REMOTE_SAYS = re.compile(
    r"(?i)\b(?:remote(?:ly)?[- ]hosted|hosted[- ]remote|remote tool[- ]calls?|"
    r"remote (?:mcp|api) (?:server|tool|service)|no local (?:command )?runtime|"
    r"no local commands?)\b")
_MCP_URL = re.compile(r"https://[A-Za-z0-9.-]+(?::\d{2,5})?(?:/[^\s\"'`)<>\]]*)?/(?:mcp|sse)/?"
                      r"(?=[\s\"'`)<>\]]|$)", re.I)
_OPENAPI_URL = re.compile(r"https://[^\s\"'`)<>\]]+?(?:openapi|swagger)[^\s\"'`)<>\]]*?"
                          r"\.(?:json|ya?ml)\b", re.I)
_HTTPS_URL = re.compile(r"https://([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)(?::\d{2,5})?"
                        r"(/[^\s\"'`)<>\]]*)?")
#: A URL that reads as an API call: a verb before it, an api host or path.
_API_HINT = re.compile(r"(?i)(?:^api[.-]|[.-]api\.)")
_API_PATH = re.compile(r"(?i)/(?:api|v\d+(?:\.\d+)*|graphql|rest|query|search)(?:/|\?|$)")
_VERB_BEFORE = re.compile(r"(?i)\b(?:GET|POST|PUT|PATCH|DELETE)\s*`?$")
_ACTION_JSON = re.compile(r'"action"\s*:\s*"[A-Za-z][A-Za-z0-9_]{2,}"')
_ENDPOINT_KEYS = ("mcp", "mcp_url", "mcpServers", "mcp_servers", "server_url", "openapi",
                  "openapi_url", "api_base", "base_url", "endpoint")
_RUNNABLE_EXT = (".py", ".sh", ".bash", ".js", ".mjs", ".cjs", ".ts", ".ps1", ".rb", ".pl")
_CODE_FILES = ("package.json", "requirements.txt", "pyproject.toml")
#: Hosts never counted as a skill's service (code hosting, docs, registries).
_NOT_SERVICE_HOSTS = re.compile(
    r"(?i)(?:^|\.)(?:github\.com|githubusercontent\.com|gitlab\.com|npmjs\.com|"
    r"pypi\.org|clawhub\.(?:ai|com)|skills\.sh|agentskills\.io|wikipedia\.org|"
    r"example\.(?:com|org|net)|localhost)$")
HOSTS_MAX = 5
_HOW_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _text_of(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(512 * 1024)
    except OSError:
        return ""


def _ships_code(folder: str) -> bool:
    """Does the skill's folder hold code it runs (scripts or a package file)?"""
    if not folder or not os.path.isdir(folder):
        return False
    for name in _CODE_FILES:
        if os.path.isfile(os.path.join(folder, name)):
            return True
    for sub in ("scripts", "bin", ""):
        base = os.path.join(folder, sub) if sub else folder
        try:
            names = os.listdir(base)
        except OSError:
            continue
        if any(n.lower().endswith(_RUNNABLE_EXT) for n in names):
            return True
    return False


def _declared_install(meta: dict) -> bool:
    metadata = _as_mapping(meta.get("metadata"))
    for key in _METADATA_KEYS:
        block = _as_mapping(metadata.get(key))
        if isinstance(block.get("install"), list) and block["install"]:
            return True
    return False


def _allowed_bash(meta: dict) -> list[str]:
    text = meta.get("allowed-tools") or meta.get("allowed_tools") or ""
    if isinstance(text, list):
        text = " ".join(str(t) for t in text)
    return re.findall(r"Bash\(\s*([A-Za-z0-9][A-Za-z0-9._+-]{0,63})", str(text))


_SHELL_FENCE = re.compile(r"^```[ \t]*(?:bash|sh|shell|zsh|console|terminal)[ \t]*$(.*?)^```",
                          re.M | re.S | re.I)
#: First words that are not the program a skill runs (shell words, and the
#: installers of skills themselves: ``npx skills add``, ``clawhub install``).
_NOT_PROGRAM_WORDS = frozenset({"cd", "export", "set", "echo", "source", ".", "#", "mkdir",
                                "cat", "ls", "sudo", "env", "then", "fi", "do", "done"})
_SKILL_INSTALLERS = re.compile(r"^(?:npx\s+(?:-y\s+)?(?:skills|clawhub)(?:@\S+)?\b|"
                               r"(?:clawhub|openclaw|skills)\b)", re.I)


def shell_programs(body: str) -> list[str]:
    """The programs a skill's own shell examples run (```bash`` blocks): the
    first word of each command line, leaving out shell words and the lines
    that install other skills. A skill that shows a command to run is a
    program here (a checked key request runs exactly that way)."""
    out: list[str] = []
    for block in _SHELL_FENCE.findall(body or ""):
        cont = False
        for raw in block.splitlines():
            line = raw.strip()
            was_cont, cont = cont, line.endswith("\\")
            if not line or was_cont or line.startswith(("#", "-", "|", ">")):
                continue
            line = line.removeprefix("$ ").strip()
            if _SKILL_INSTALLERS.match(line):
                continue
            word = line.split(None, 1)[0].strip("\"'")
            word = os.path.basename(word)
            if word in _NOT_PROGRAM_WORDS or "=" in word or not _BIN_RE.match(word):
                continue
            if word not in out:
                out.append(word)
    return out[:NEEDS_MAX]


def _service_host(host: str) -> bool:
    host = host.lower().rstrip(".")
    return "." in host and not _NOT_SERVICE_HOSTS.search(host)


def _endpoint_from_meta(meta: dict) -> str:
    for key in _ENDPOINT_KEYS:
        value = meta.get(key)
        if isinstance(value, dict):
            value = value.get("url") or next(
                (v.get("url") for v in value.values() if isinstance(v, dict)), "")
        if isinstance(value, str) and value.strip().lower().startswith("https://"):
            return value.strip()[:300]
    return ""


def how_it_runs_text(text: str, folder: str = "") -> dict[str, Any]:
    """How the skill whose ``SKILL.md`` is *text* (in *folder*) runs.

    ``{"kind": "cli"|"remote"|"guide", "class": "TOOL"|"DATA"|"GUIDE",
    "bins": [...], "endpoint": url, "endpoint_kind": "mcp"|"openapi"|"",
    "hosts": [...], "why": plain words}``. Never raises."""
    meta, body = parse_frontmatter(text or "")
    meta = meta if isinstance(meta, dict) else {}
    req = requirements(meta)
    bins = list(dict.fromkeys(req["bins"] + req["any_bins"] + _allowed_bash(meta)))
    shown = shell_programs(body)
    if not bins and shown:
        bins = shown
    if bins or _declared_install(meta) or _ships_code(folder):
        why = (f"It runs the program{'s' if len(bins) > 1 else ''} {_and_list(bins)} on this "
               f"computer." if bins else "It runs its own code on this computer.")
        return {"kind": "cli", "class": "TOOL", "bins": bins, "endpoint": "",
                "endpoint_kind": "", "hosts": [], "why": why}
    said = " ".join(str(meta.get(k) or "") for k in ("compatibility", "description"))
    endpoint = _endpoint_from_meta(meta)
    kind = ""
    if endpoint:
        kind = "openapi" if re.search(r"(?i)openapi|swagger", endpoint) else "mcp"
    if not kind:
        m = _MCP_URL.search(body)
        if m:
            endpoint, kind = m.group(0).rstrip("/")[:300], "mcp"
    if not kind:
        m = _OPENAPI_URL.search(body)
        if m:
            endpoint, kind = m.group(0)[:300], "openapi"
    hosts: list[str] = []
    for m in _HTTPS_URL.finditer(body):
        host, path = m.group(1).lower(), m.group(2) or ""
        before = body[max(0, m.start() - 12):m.start()]
        if not _service_host(host):
            continue
        if _API_HINT.search(host) or _API_PATH.search(path) or _VERB_BEFORE.search(before):
            if host not in hosts:
                hosts.append(host)
    if endpoint:
        try:
            from urllib.parse import urlsplit
            h = (urlsplit(endpoint).hostname or "").lower()
        except ValueError:
            h = ""
        if h and h not in hosts:
            hosts.insert(0, h)
    says_remote = bool(_REMOTE_SAYS.search(said) or _REMOTE_SAYS.search(body[:4000]))
    calls_json = bool(_ACTION_JSON.search(body))
    if not (endpoint or hosts or says_remote or calls_json):
        return {"kind": "guide", "class": "GUIDE", "bins": [], "endpoint": "",
                "endpoint_kind": "", "hosts": [], "why": "It is words the bot reads."}
    if not hosts:
        home = str(meta.get("homepage") or "")
        m = _HTTPS_URL.match(home.strip())
        if m and _service_host(m.group(1)):
            hosts.append(m.group(1).lower())
    hosts = hosts[:HOSTS_MAX]
    where = _and_list(hosts) if hosts else "a service on the internet"
    if kind:
        why = (f"It is an outside service at {where} that the bot connects to, "
               f"not a program on this computer.")
        cls = "TOOL"
    else:
        why = (f"It describes an outside service ({where}), not a program on this "
               f"computer, so it is never run as a command.")
        cls = "DATA"
    return {"kind": "remote", "class": cls, "bins": [], "endpoint": endpoint,
            "endpoint_kind": kind, "hosts": hosts, "why": why}


#: Programs that only make one web request (the checked request).
WEB_PROGRAMS = frozenset({"curl", "wget", "http", "https"})
_KEY_VAR = re.compile(r"\$\{?([A-Z][A-Z0-9_]{1,63})\}?|%([A-Z][A-Z0-9_]{1,63})%")


def _shell_commands(body: str) -> list[str]:
    """Each command a skill's own shell examples run (continued lines joined),
    leaving out comments, shell words and lines that install other skills."""
    out: list[str] = []
    for block in _SHELL_FENCE.findall(body or ""):
        cur = ""
        for raw in block.splitlines():
            line = raw.strip()
            if not cur and (not line or line.startswith(("#", "-", "|", ">"))):
                continue
            cur = (cur + " " + line.rstrip("\\").strip()).strip() if cur else \
                line.removeprefix("$ ").rstrip("\\").strip()
            if line.endswith("\\"):
                continue
            first = cur.split(None, 1)[0].strip("\"'") if cur else ""
            if cur and not _SKILL_INSTALLERS.match(cur) \
                    and os.path.basename(first) not in _NOT_PROGRAM_WORDS:
                out.append(cur)
            cur = ""
        if cur:
            out.append(cur)
    return out


def key_request_service(text: str, folder: str = "") -> Optional[dict[str, Any]]:
    """A skill whose ONLY action is one web request (curl, wget or httpie) to
    its own service: ``{"hosts", "keys", "program"}``, else None.

    The build treats such a skill as a DATA source (its hosts are the source),
    fetched through the Bridge with its vault key under the key-use rule
    (the key goes only to the skill's own service), never as a command line
    tool. Own service, as :mod:`puffpai.security.skill_key_use` reads it: the
    homepage or API base its frontmatter declares, or the host of its own
    example when the example names its key. A skill that declares programs,
    install steps or ``Bash(...)``, ships code, or shows more than one command
    is not one of these. Reads only; never raises."""
    try:
        meta, body = parse_frontmatter(text or "")
        meta = meta if isinstance(meta, dict) else {}
        req = requirements(meta)
        if req["bins"] or req["any_bins"] or _allowed_bash(meta) or _declared_install(meta) \
                or _ships_code(folder):
            return None
        commands = _shell_commands(body)
        if len(commands) != 1:
            return None
        command = commands[0]
        program = os.path.basename(command.split(None, 1)[0].strip("\"'")).lower()
        if program not in WEB_PROGRAMS or re.search(r"(?i)\bhttp://", command):
            return None
        hosts = list(dict.fromkeys(m.group(1).lower().rstrip(".")
                                   for m in _HTTPS_URL.finditer(command)))
        if not hosts or len(hosts) > HOSTS_MAX:
            return None
        named = list(dict.fromkeys(a or b for a, b in _KEY_VAR.findall(command)))
        from puffpai.security import skill_key_use
        if not all(skill_key_use._usable_host(h) for h in hosts):  # noqa: SLF001
            return None                     # an address, localhost, a generic domain
        declared = {skill_key_use.registrable(h)
                    for v in skill_key_use._declared_service_values(meta)  # noqa: SLF001
                    for h in skill_key_use._hosts_in(v)}  # noqa: SLF001
        declared.discard("")
        if not named and not all(skill_key_use.registrable(h) in declared for h in hosts):
            return None
        return {"hosts": hosts, "keys": named, "program": program}
    except Exception:  # noqa: BLE001 - unreadable: not one of these
        return None


def how_it_runs(skill: Any) -> dict[str, Any]:
    """:func:`how_it_runs_text` for an installed skill (a name or a record
    from :func:`installed`). An unknown skill is a guide. Never raises."""
    rec = skill if isinstance(skill, dict) and skill.get("path") else find(skill)
    if not rec:
        return how_it_runs_text("")
    path = str(rec["path"])
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        stamp = -1.0
    hit = _HOW_CACHE.get(path)
    if hit and hit[0] == stamp:
        return dict(hit[1])
    got = how_it_runs_text(_text_of(path), os.path.dirname(path))
    if len(_HOW_CACHE) > 400:
        _HOW_CACHE.clear()
    _HOW_CACHE[path] = (stamp, got)
    return dict(got)


def is_remote(skill: Any) -> bool:
    """True when *skill* is an outside service (never a shell command)."""
    return how_it_runs(skill)["kind"] == "remote"


# ── what the brain sees ────────────────────────────────────────────────────

#: The mark on a remote skill's line in the brain's list.
REMOTE_MARK = "(outside service, never a command)"

PROMPT_HEAD = (
    "## Skills\n"
    "Skills are instructions people wrote for a job, installed on this computer. "
    "Before replying, scan the skills below. If a skill matches or is even partly "
    "relevant to the request, load it with skill_view(name) and follow it. "
    "A skill may name other files; read them with skill_view(name, path). "
    "If a skill asks to run a command, use run_command, which asks the person first. "
    f"A skill marked {REMOTE_MARK} describes a service on the internet, not a program "
    "here: never use run_command for it and never send its JSON as a command; use its "
    "connected tool if one is listed, or say it needs to be connected first.\n"
)
PROMPT_TAIL = "Only answer without a skill when none of them fits."
PROMPT_NONE = ("No skills are installed yet. If the person wants something you cannot "
               "do, search with find_things before saying you can't.")


def prompt_block(rows: Optional[list[dict[str, Any]]] = None) -> str:
    """The skills list for the system prompt: names and one-line descriptions.

    Copied from Hermes's ``<available_skills>`` block, shortened for small
    local models."""
    rows = installed() if rows is None else rows
    if not rows:
        return PROMPT_NONE
    lines = []
    for rec in rows[:PROMPT_MAX_SKILLS]:
        desc = _one_line(rec.get("description"), LIST_DESCRIPTION_MAX)
        if rec.get("path") and is_remote(rec):
            desc = f"{REMOTE_MARK} {desc}".strip()
        lines.append(f"  - {rec['folder']}: {desc}" if desc else f"  - {rec['folder']}")
    more = len(rows) - PROMPT_MAX_SKILLS
    if more > 0:
        lines.append(f"  ({more} more: ask skill_view with a name)")
    return (PROMPT_HEAD + "<available_skills>\n" + "\n".join(lines)
            + "\n</available_skills>\n" + PROMPT_TAIL)


def _inside(root: str, candidate: str) -> bool:
    root = os.path.realpath(root)
    candidate = os.path.realpath(candidate)
    return candidate == root or candidate.startswith(root.rstrip(os.sep) + os.sep)


def view(name: Any, path: Any = None) -> dict[str, Any]:
    """The whole ``SKILL.md`` of *name*, or one file inside its folder.

    ``{"ok": True, "name", "path", "text", "files"}`` or ``{"ok": False,
    "message"}``. A path that leaves the skill's folder is refused."""
    rec = find(name)
    if rec is None:
        names = ", ".join(r["folder"] for r in installed()[:30]) or "none"
        return {"ok": False, "message": f"No skill called {str(name or '')[:64]!r} is "
                                        f"installed. Installed skills: {names}."}
    folder = os.path.dirname(rec["path"])
    rel = str(path or "").strip().replace("\\", "/").lstrip("/")
    target = os.path.join(folder, *rel.split("/")) if rel else rec["path"]
    if not _inside(folder, target):
        return {"ok": False, "message": "That file is not inside the skill's folder."}
    if not os.path.isfile(target):
        return {"ok": False, "message": f"The skill {rec['folder']} has no file {rel!r}.",
                "files": files_of(folder)}
    try:
        with open(target, encoding="utf-8", errors="replace") as fh:
            text = fh.read(VIEW_MAX_CHARS + 1)
    except OSError:
        return {"ok": False, "message": "That file could not be read."}
    cut = len(text) > VIEW_MAX_CHARS
    return {"ok": True, "name": rec["folder"], "path": rel or SKILL_FILE,
            "text": text[:VIEW_MAX_CHARS] + ("\n[cut here: the file is longer]" if cut else ""),
            "folder": folder, "files": files_of(folder)}


def files_of(folder: str, limit: int = 60) -> list[str]:
    """The files in a skill's folder (relative, forward slashes)."""
    out: list[str] = []
    for base, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for f in sorted(files):
            rel = os.path.relpath(os.path.join(base, f), folder).replace(os.sep, "/")
            out.append(rel)
            if len(out) >= limit:
                return out
    return out


__all__ = ["DESCRIPTION_MAX", "NAME_MAX", "PROMPT_NONE", "REMOTE_MARK", "SKILL_FILE",
           "files_of", "find", "has_program", "how_it_runs", "how_it_runs_text", "installed",
           "is_remote", "key_request_service", "missing_programs", "needs_line",
           "parse_frontmatter",
           "prompt_block", "read_skill_file", "requirements", "requirements_of", "safe_name",
           "skills_dir", "view"]
