"""Run an outside project on this computer, the way its authors built it.

The rule: outside projects run directly on her computer
after one yes, like ``run_command`` does. This module is that path for code
projects (server apps, command line tools, libraries, and web apps that need
a build while the locked box is parked). It copies Hermes and OpenClaw's
simple pattern: read how the project says it runs, show exactly that, and
after one yes run it, as her, in a folder of its own.

What it does:

* :func:`detect` reads (never runs) the project: ``package.json`` scripts and
  ``bin``, ``Procfile``, ``pyproject.toml`` entry points, ``requirements.txt``,
  a few well-known Python app shapes, and ``docker-compose`` files. It answers
  a plan: the exact install steps, the start command, the port, the keys the
  project asks for, and what interface the brain can use (an MCP server, an
  OpenAPI description, command line tools).
* :func:`shown_lines` is what the yes card shows. Every step runs as an
  argument list (never a shell line BuildBot builds), so what is shown is
  what runs. The plan's fingerprint (:func:`plan_sha`) is what the yes covers.
* :func:`set_up` copies the files into ``<state>/host_apps/<id>/app``, runs
  the install steps (``npm ci --ignore-scripts``, a venv and pip), and
  :func:`start` runs it bound to 127.0.0.1 on a free port. A watcher checks
  where every process it started listens: anything that listens beyond
  127.0.0.1 gets all of them stopped at once (fail closed). Start, Stop,
  Restart, status and logs are on the thing page; :func:`autostart_all`
  starts again what she left on.
* Every process it starts stays together. On Windows each start and each
  install step runs in its own Job Object, joined before the program runs
  any code, with no way out (no breakaway) and "end everything when the job
  closes", so a detached or grandchild process is watched and stopped with
  the rest, and nothing outlives this bot. Elsewhere: its own session and
  process group. It counts as running while ANY of its processes lives.
* ``npm run <script>`` steps show, on the yes card, the exact text npm runs
  from package.json (its ``pre`` and ``post`` scripts too); the plan's
  fingerprint covers that text, and a package.json that says something else
  later is not run. Install steps never run a project's install scripts
  (``--ignore-scripts``).
* A Node project's own settings files (``.npmrc``, ``.yarnrc``,
  ``.yarnrc.yml``) are in the fingerprint and checked again before every
  step and start. One that makes npm or yarn load code the card cannot show
  (``node-options``, ``script-shell``, ``yarnPath`` and the like) is refused;
  any other setting is shown on the card. npm always runs with an empty
  ``node-options`` on its own command line, and her own and the computer's
  npm settings files are never read. pip and the venv step run with
  ``python -I`` (a ``pip`` folder in the project is never run as pip).
* Programs start by full path only: node and npm from
  :mod:`puffpai.security.host_programs`, Python from this bot's own Python
  (then the project's own venv), Docker from ``host_programs.path("docker")``.
* Our own calls stay behind the Bridge: an install step that downloads parts
  runs only while the ``code_download`` service is on, and writes one line to
  the outside-traffic log (``egress_ledger``). What the project does once it
  runs is NOT behind the Bridge: it runs on her computer with her
  permissions, and the yes card says so in those words.
* Install, start and stop go to the security log (``AuditLog``). A key it
  asks for (named in its README or its example settings file, and shown on
  the yes card) can be kept in her vault on its page; that key reaches only
  this app's own process when it starts (:func:`app_keys`), never an install
  step, never a Docker project. Nothing else from her vault is given to it.
* Docker first: a project that ships a compose file (or whose
  README documents its image) runs in its own containers; Docker is
  installed and started under the same yes when it is missing
  (:func:`puffpai.deps.install_program`), and its own native way is the
  fallback. Every published port goes to a free place on 127.0.0.1; Stop,
  Restart and its logs are compose's own. Docker's own settings are never
  changed: BuildBot renders the project's
  compose file with ``docker compose config`` (without reading its settings
  files into it), limits every published port to 127.0.0.1, and runs that
  copy only when every part of it is one BuildBot understands
  (:func:`compose_problems`): no host network, memory, programs or users,
  no privileged containers, extra powers or devices, Docker's safety rules
  on, no Docker socket or pipe, and no folder or file from outside the
  project.

Goal-agnostic: nothing here names a project, a topic or a goal.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import struct
import subprocess

from puffpai import procflags
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

from puffpai import fsutil
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

SCHEMA = "host_plan/v1"
ROOT = "host_apps"
#: How long one install or build step may take.
STEP_SECONDS = 900
#: How long a start may take before it listens.
READY_SECONDS = 600
#: How often a running app's listening places are checked again.
WATCH_SECONDS = 5.0
#: A log is cut back to its last part when it grows past this.
LOG_MAX = 2 * 1024 * 1024
LOG_TAIL_LINES = 200
LOOPBACK = "127.0.0.1"
#: The outside service whose switch covers downloads for her picked projects
#: (the download itself uses it, so the parts it needs use it too).
SERVICE = "code_download"
NPM_HOST = "registry.npmjs.org"
PYPI_HOST = "pypi.org"

HOWS = ("node_server", "python_server", "compose", "web_build_static", "node_cli",
        "python_cli", "library", "assembled")
#: What each way of running is, in plain words (the yes card and the page).
HOW_WORDS = {
    "node_server": "its own server, run with Node.js",
    "python_server": "its own server, run with Python",
    "compose": "its own containers, run with Docker",
    "web_build_static": "a web app, built once on this computer, then opened as a page",
    "node_cli": "a command line tool, run with Node.js",
    "python_cli": "a command line tool, run with Python",
    "library": "a code library other programs use",
    "assembled": "an app BuildBot put together from found parts, run by its own small server",
}
#: Plain names of the programs a plan needs.
PROGRAM_WORDS = {"node": "Node.js", "python": "Python", "docker": "Docker"}
#: How each way of running counts in the one build (its classify step): an
#: APP has its own screen (route A), a TOOL hands the brain tools (route B),
#: a library is neither (an ingredient only).
PLAN_CLASS = {"node_server": "APP", "python_server": "APP", "compose": "APP",
              "web_build_static": "APP", "assembled": "APP", "node_cli": "TOOL",
              "python_cli": "TOOL", "library": ""}
#: Ways of running that have a server to start (the rest are set up only).
SERVER_HOWS = frozenset({"node_server", "python_server", "compose", "assembled"})


def plan_class(plan: Any, *, has_ui: Optional[bool] = None) -> str:
    """``"APP"``, ``"TOOL"`` or ``""`` for one :func:`detect` plan.

    A server whose only face is an OpenAPI description (*has_ui* False: no
    page of its own) is a TOOL, its operations being what the brain uses; a
    library that still names an MCP server or command line tools is a TOOL.
    Reads the plan only."""
    if not isinstance(plan, dict):
        return ""
    how = str(plan.get("how") or "")
    cls = PLAN_CLASS.get(how, "")
    faces = plan.get("interfaces") if isinstance(plan.get("interfaces"), dict) else {}
    if cls == "APP" and has_ui is False and how != "web_build_static" \
            and (faces.get("openapi") or faces.get("mcp")):
        return "TOOL"
    if not cls and (faces.get("mcp") or faces.get("cli")):
        return "TOOL"
    return cls

_SCRIPT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,39}\Z")
_BIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_REL_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]{0,199}\Z")
_PYMOD_APP_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,99}:[A-Za-z_][A-Za-z0-9_]{0,59}\Z")
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}\Z")
_SECRETISH = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)")
#: One argument read from a project's Procfile: plain characters only.
_ARG_RE = re.compile(r"^[A-Za-z0-9_./:=@,+%-]{1,200}\Z")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,29}-[a-z2-7]{8}\Z")
_LOCKS = ("npm-shrinkwrap.json", "package-lock.json")
_COMPOSE_FILES = ("compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml")
_ENV_EXAMPLES = (".env.example", ".env.sample", ".env.template", "env.example",
                 ".env.local.example", "example.env")
_OPENAPI_FILES = ("openapi.json", "openapi.yaml", "openapi.yml", "swagger.json",
                  "docs/openapi.json", "docs/openapi.yaml", "api/openapi.json",
                  "api/openapi.yaml")
_PY_APP_FILES = ("app.py", "main.py", "server.py", "streamlit_app.py", "run.py")
#: Programs a Procfile may start, mapped to how BuildBot starts them.
_PROCFILE_PROGRAMS = {"python": "venv_python", "python3": "venv_python",
                      "uvicorn": "venv_bin", "flask": "venv_bin", "streamlit": "venv_bin",
                      "hypercorn": "venv_bin", "waitress-serve": "venv_bin",
                      "node": "node"}
#: Start scripts tried in this order.
_START_SCRIPTS = ("start", "serve", "dev", "preview")
#: pnpm and yarn come from Node's own corepack, kept in the app's own folder
#: (``COREPACK_HOME``, shims in ``{pm_bin}``), never installed for the whole
#: computer. The version is the project's ``packageManager`` field (with its
#: hash, which corepack checks), else one pinned here by its lock file.
_PM_SPEC_RE = re.compile(r"^(pnpm|yarn|npm)@(\d{1,3}\.\d{1,3}\.\d{1,4}(?:-[0-9A-Za-z.]{1,40})?)"
                         r"(\+sha(?:1|224|256|384|512)\.[0-9a-f]{40,128})?\Z")
_PNPM_BY_LOCK = {"5": "pnpm@7.33.7", "6": "pnpm@8.15.9", "7": "pnpm@9.15.9",
                 "9": "pnpm@9.15.9"}
_PNPM_DEFAULT = "pnpm@9.15.9"
_YARN_CLASSIC = "yarn@1.22.22"
_YARN_BERRY = {"4": "yarn@2.4.3", "6": "yarn@3.8.7"}
_YARN_BERRY_DEFAULT = "yarn@4.18.1"
#: Settings in pnpm-workspace.yaml or package.json "pnpm" that make pnpm run
#: or fetch code the yes card cannot show: such a project is not run here.
_PNPM_CODE_KEYS = ("pnpmfile", "globalpnpmfile", "nodeoptions", "node-options",
                   "scriptshell", "script-shell", "shellemulator", "configdependencies",
                   "executionenv", "usenodeversion", "use-node-version", "npmpath", "hooks")
#: Folders of a project that holds its app in parts (a monorepo, or a
#: frontend and backend side by side), most likely to hold the server first.
_PART_DIRS = ("server", "backend", "api", "app", "web", "frontend", "client", "site", "ui")
#: manage.py commands a Django project's own notes may ask for before it
#: starts (run in its own folder, with its own Python); migrate always runs.
_DJANGO_STEPS = ("makemigrations", "migrate", "collectstatic", "compress")
#: Web dev tools that ignore PORT and HOST: the flags that pin them.
_DEV_FLAGS = {
    "vite": ["--host", LOOPBACK, "--port", "{port}", "--strictPort"],
    "next": ["-H", LOOPBACK, "-p", "{port}"],
    "astro": ["--host", LOOPBACK, "--port", "{port}"],
    "webpack": ["--host", LOOPBACK, "--port", "{port}"],
    "webpack-dev-server": ["--host", LOOPBACK, "--port", "{port}"],
    "ng": ["--host", LOOPBACK, "--port", "{port}"],
    "nuxt": ["--host", LOOPBACK, "--port", "{port}"],
    "nuxi": ["--host", LOOPBACK, "--port", "{port}"],
}
_IS_WINDOWS = sys.platform == "win32"
_IS_MAC = sys.platform == "darwin"

_LOCKS_BY_ID: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
#: Live processes started in this run of the bot: id -> Popen.
_PROCS: dict[str, Any] = {}
_WATCHERS: dict[str, threading.Event] = {}

#: Seams for the tests.
_popen = subprocess.Popen
_run = subprocess.run


class HostError(Exception):
    """A plain sentence for the owner and a short code."""

    def __init__(self, line: str, code: str = "failed"):
        super().__init__(line)
        self.line = line
        self.code = code


def _lock_for(iid: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS_BY_ID.setdefault(iid, threading.Lock())


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# reading the project (pure: nothing here runs or imports what it reads)
# ---------------------------------------------------------------------------

def _read_text(path: str, limit: int = 512 * 1024) -> str:
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            return ""
        with open(path, "rb") as fh:
            return fh.read(limit).decode("utf-8", "replace")
    except OSError:
        return ""


def _read_json(path: str) -> dict:
    try:
        data = json.loads(_read_text(path))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _inside_rel(rel: Any) -> str:
    """*rel* as a plain relative path inside the project, or ""."""
    text = str(rel or "").replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    if not _REL_RE.match(text) or ".." in text.split("/") or text.startswith("/"):
        return ""
    return text


#: Where a project's own words are (its README), read for key names and links.
_READMES = ("README.md", "README", "README.txt", "README.rst", "readme.md", "Readme.md")


def _readme(src: str) -> str:
    for name in _READMES:
        text = _read_text(os.path.join(src, name), 256 * 1024)
        if text:
            return text
    return ""


def declared_keys(src: str) -> list[str]:
    """Key names the project asks for: in its example settings file
    (``.env.example`` and the like: names that say key, token, secret or
    password) and in its README (``puffpai.deps.keys_in_text``, the same
    reader as a skill's text). Never a value, never a name that changes how
    programs start (``deps.env_name_ok``)."""
    out: list[str] = []
    for name in _ENV_EXAMPLES:
        for line in _read_text(os.path.join(src, name), 64 * 1024).splitlines():
            m = re.match(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]{1,63})\s*=", line)
            if m and _SECRETISH.search(m.group(1)) and m.group(1) not in out:
                out.append(m.group(1))
    try:
        from puffpai import deps
        for row in deps.keys_in_text(_readme(src)):
            if _ENV_NAME_RE.match(row["name"]) and row["name"] not in out:
                out.append(row["name"])
        out = [n for n in out if deps.env_name_ok(n)]
    except Exception:  # noqa: BLE001 - without the reader: the settings file only
        pass
    return out[:12]


def _node_deps(pkg: dict) -> bool:
    return any(isinstance(pkg.get(k), dict) and pkg.get(k)
               for k in ("dependencies", "devDependencies", "optionalDependencies"))


def package_manager(src: str, pkg: dict) -> dict:
    """``{"name", "spec", "from"}`` for a pnpm or yarn project, ``{}`` for npm.
    Reads (never runs) ``packageManager`` and the lock files."""
    raw = str(pkg.get("packageManager") or "").strip()
    m = _PM_SPEC_RE.match(raw)
    if m and m.group(1) != "npm":
        return {"name": m.group(1), "spec": raw, "from": "packageManager"}
    if raw and not m:
        return {"name": "", "spec": "", "from": "bad",
                "why": "its packageManager field cannot be read"}
    if os.path.isfile(os.path.join(src, "pnpm-lock.yaml")):
        head = _read_text(os.path.join(src, "pnpm-lock.yaml"), 4096)
        v = re.search(r"lockfileVersion:\s*['\"]?(\d+)", head)
        return {"name": "pnpm", "spec": _PNPM_BY_LOCK.get(v.group(1) if v else "",
                                                          _PNPM_DEFAULT), "from": "lock"}
    if os.path.isfile(os.path.join(src, "yarn.lock")) and not any(
            os.path.isfile(os.path.join(src, n)) for n in _LOCKS):
        head = _read_text(os.path.join(src, "yarn.lock"), 4096)
        if "__metadata:" in head:
            v = re.search(r"version:\s*(\d+)", head)
            return {"name": "yarn", "spec": _YARN_BERRY.get(v.group(1) if v else "",
                                                            _YARN_BERRY_DEFAULT),
                    "from": "lock", "berry": True}
        return {"name": "yarn", "spec": _YARN_CLASSIC, "from": "lock"}
    return {}


def _pm_major(pm: dict) -> int:
    m = re.match(r"^[a-z]+@(\d+)", str(pm.get("spec") or ""))
    return int(m.group(1)) if m else 0


def _pm_install(src: str, pm: dict) -> list[dict]:
    """corepack steps: shims in its own folder, the pinned version into its
    own corepack folder, then the install, never running install scripts."""
    name, spec = pm["name"], pm["spec"]
    steps = [{"what": "install", "tool": "corepack",
              "args": ["enable", "--install-directory", "{pm_bin}", name], "hosts": []},
             {"what": "install", "tool": "corepack", "args": ["install", "-g", spec],
              "hosts": [NPM_HOST]}]
    if name == "pnpm":
        args = ["install", "--ignore-scripts", "--ignore-pnpmfile"]
        if os.path.isfile(os.path.join(src, "pnpm-lock.yaml")):
            args.insert(1, "--frozen-lockfile")
    elif pm.get("berry") or _pm_major(pm) >= 2:
        args = ["install", "--mode=skip-build"]
        if os.path.isfile(os.path.join(src, "yarn.lock")):
            args.insert(1, "--immutable")
    else:
        args = ["install", "--ignore-scripts", "--non-interactive"]
        if os.path.isfile(os.path.join(src, "yarn.lock")):
            args.insert(1, "--frozen-lockfile")
    steps.append({"what": "install", "tool": "corepack", "args": [spec, *args],
                  "hosts": [NPM_HOST]})
    return steps


def _node_install(src: str, pkg: dict,
                  pm: Optional[dict] = None) -> tuple[list[dict], list[str]]:
    """(steps, notes) to install a Node project's parts with npm, or with
    pnpm or yarn through corepack (*pm*)."""
    # The parts it downloads never run their own install scripts; the
    # project's OWN install scripts (what its README's "npm install" runs,
    # often making files it needs) run as their own steps, shown on the card.
    own = [_npm_run(pkg, "install", n, pm=pm) for n in OWN_INSTALL_SCRIPTS
           if n in _scripts_of(pkg)]
    if pm and pm.get("name"):
        return _pm_install(src, pm) + own, [
            f"It uses {pm['name']} ({pm['spec'].split('+')[0]}), which Node.js's own "
            f"corepack keeps in this app's folder. Nothing is installed for the whole "
            f"computer."]
    if not _node_deps(pkg):
        return own, []
    flags = ["--ignore-scripts", "--no-audit", "--no-fund"]
    if any(os.path.isfile(os.path.join(src, n)) for n in _LOCKS):
        return [{"what": "install", "tool": "npm", "args": ["ci", *flags],
                 "hosts": [NPM_HOST]}] + own, []
    return ([{"what": "install", "tool": "npm", "args": ["install", *flags],
              "hosts": [NPM_HOST]}] + own,
            ["It has no npm lock file, so npm picks today's versions of its parts."])


#: The project's own install scripts, run (by name, shown) after its parts
#: are in. ``prepare`` is left out: it sets up git hooks for its makers.
OWN_INSTALL_SCRIPTS = ("preinstall", "install", "postinstall")


def _dev_flags(script_text: str, port: int) -> list[str]:
    # Extra words go to the end of the script, so its last command decides.
    last = re.split(r"&&|\|\||;", str(script_text or ""))[-1]
    first = last.strip().split()
    if not first:
        return []
    word = first[0]
    flags = _DEV_FLAGS.get(word)
    if flags is None:
        return []
    return [str(port) if f == "{port}" else f for f in flags]


def _node_bins(pkg: dict) -> dict[str, str]:
    raw = pkg.get("bin")
    name = str(pkg.get("name") or "").rsplit("/", 1)[-1]
    if isinstance(raw, str):
        raw = {name: raw} if _BIN_RE.match(name) else {}
    out: dict[str, str] = {}
    for k, v in (raw or {}).items() if isinstance(raw, dict) else ():
        rel = _inside_rel(v)
        if isinstance(k, str) and _BIN_RE.match(k) and rel:
            out[k] = rel
    return dict(list(out.items())[:8])


def _is_mcp_node(pkg: dict) -> bool:
    deps = {}
    for k in ("dependencies", "peerDependencies"):
        if isinstance(pkg.get(k), dict):
            deps.update(pkg[k])
    return "@modelcontextprotocol/sdk" in deps or "fastmcp" in deps


def _npm_scripts(pkg: dict) -> dict:
    """Every script in *pkg* (unfiltered: npm runs a ``pre``/``post`` script
    whatever its name looks like), as ``{name: text}``."""
    raw = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
    return {str(k): v if isinstance(v, str) else json.dumps(v) for k, v in raw.items()}


def npm_run_scripts(pkg: dict, name: str) -> list[list[str]]:
    """What ``npm run <name>`` runs from *pkg*, in order: ``pre<name>``,
    ``<name>``, ``post<name>`` as ``[[script, exact text], ...]``. npm runs
    the pre and post scripts by itself, so the yes card shows them too and
    the plan's fingerprint covers them."""
    raw = _npm_scripts(pkg)
    return [[n, raw[n]] for n in (f"pre{name}", name, f"post{name}") if n in raw]


#: A project's own settings files for npm (and for yarn, which a script may
#: call). npm reads ``.npmrc`` for every command it runs in the project, so
#: each file's fingerprint is in the plan (:func:`plan_sha`) and is checked
#: again before every step and start (:func:`_check_node_config`).
_NODE_CONFIG_FILES = (".npmrc", ".yarnrc", ".yarnrc.yml")
_NODE_CONFIG_MAX = 64 * 1024
#: npm settings that make npm run code other than the script text the yes
#: card shows (or read settings from somewhere else, or run other folders'
#: scripts). A project whose ``.npmrc`` sets one is not run here.
_NPM_CODE_KEYS = frozenset({
    "node-options", "script-shell", "shell", "node-gyp", "git", "editor", "browser",
    "viewer", "call", "globalconfig", "userconfig", "init-module", "onload-script",
    "workspace", "workspaces", "include-workspace-root", "prefix", "global", "location",
    "cache", "logs-dir", "scripts-prepend-node-path"})
#: Words in a yarn settings file that load code (yarn's own program path,
#: its plugins, or settings files that set NODE_OPTIONS for scripts).
_YARN_CODE_WORDS = ("yarnpath", "yarn-path", "plugins", "injectenvironmentfiles")
#: Setting names whose value is never shown on the card.
_SECRET_SETTING = re.compile(r"(auth|token|password|passwd|secret|cert|key)", re.I)


def _ini_unquote(text: str) -> str:
    """One key or value of an npm settings file, read the way npm's ``ini``
    reader reads it (quotes, ``;``/``#`` comments, ``\\`` escapes)."""
    text = text.strip().strip("﻿").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        if text[0] == "'":
            text = text[1:-1]
        try:
            got = json.loads(text)
            return got if isinstance(got, str) else text
        except ValueError:
            return text
    out, esc = [], False
    for ch in text:
        if esc:
            out.append(ch if ch in "\\;#" else "\\" + ch)
            esc = False
        elif ch in ";#":
            break
        elif ch == "\\":
            esc = True
        else:
            out.append(ch)
    return "".join(out).strip()


def _npmrc_settings(text: str) -> list[tuple[str, str]]:
    """``[(key, value), ...]`` from an ``.npmrc``. Keys are normalized the
    strict way (lower case, ``_`` as ``-``, odd characters dropped), so a
    spelling npm would ignore can only make BuildBot refuse more."""
    rows: list[tuple[str, str]] = []
    for line in re.split(r"[\r\n]+", text):
        line = line.strip().strip("﻿").strip()
        if not line or line[0] in ";#" or (line.startswith("[") and line.endswith("]")):
            continue
        key, _eq, value = line.partition("=")
        raw_key = _ini_unquote(key)
        norm = re.sub(r"\[\]$", "", raw_key.lower().replace("_", "-"))
        if "${" in raw_key:
            norm = "${"        # a key built from the environment: never read as harmless
        else:
            norm = re.sub(r"[^a-z0-9./:@-]", "", norm)
        rows.append((norm, _ini_unquote(value) if _eq else "true"))
    return rows


def _node_config(folder: str, dirs: Any = ()) -> dict:
    """The project's own npm, pnpm and yarn settings in *folder* (and in the
    part folders *dirs* a step runs in):
    ``{"files": {name: sha256}, "shown": [lines], "code": [what loads code]}``."""
    out: dict = {"files": {}, "shown": [], "code": []}
    for rel in [""] + [d for d in dirs or () if d]:
        got = _node_config_one(os.path.join(folder, *rel.split("/")) if rel else folder)
        pre = f"{rel}/" if rel else ""
        out["files"].update({pre + k: v for k, v in got["files"].items()})
        out["shown"] += [pre + x for x in got["shown"]]
        out["code"] += [pre + x for x in got["code"]]
    return out


def _plan_dirs(plan: dict) -> list[str]:
    """The part folders a plan's steps and start run in."""
    steps = list(plan.get("steps") or []) + ([plan["start"]] if plan.get("start") else [])
    return sorted({str(st.get("dir")) for st in steps if st.get("dir")})


def _node_config_one(folder: str) -> dict:
    files: dict[str, str] = {}
    shown: list[str] = []
    code: list[str] = []
    for name in _NODE_CONFIG_FILES:
        path = os.path.join(folder, name)
        if os.path.islink(path) or not os.path.lexists(path):
            continue           # a link is never copied into its folder
        try:
            if not os.path.isfile(path):
                raise OSError
            with open(path, "rb") as fh:
                raw = fh.read(_NODE_CONFIG_MAX + 1)
        except OSError:
            code.append(f"{name} (cannot be read)")
            continue
        files[name] = hashlib.sha256(raw).hexdigest()
        if len(raw) > _NODE_CONFIG_MAX:
            code.append(f"{name} (too large to read)")
            continue
        text = raw.decode("utf-8", "replace")
        if name == ".npmrc":
            parts = []
            for key, value in _npmrc_settings(text):
                if key in _NPM_CODE_KEYS or key == "${":
                    code.append(f"{key} in {name}")
                    continue
                parts.append(f"{key}=" + ("(hidden)" if _SECRET_SETTING.search(key)
                                          else json.dumps(value)[1:-1][:120]))
            if parts:
                shown.append(f"{name}: " + ", ".join(parts[:40])
                             + (" ..." if len(parts) > 40 else ""))
        else:
            low = text.lower()
            code += [f"{word} in {name}" for word in _YARN_CODE_WORDS if word in low]
            shown.append(f"{name} (settings for yarn, if a script uses yarn)")
    ws = os.path.join(folder, "pnpm-workspace.yaml")
    if os.path.islink(ws):
        code.append("pnpm-workspace.yaml (a link)")
    elif os.path.isfile(ws):
        try:
            with open(ws, "rb") as fh:
                raw = fh.read(_NODE_CONFIG_MAX + 1)
        except OSError:
            raw = b""
            code.append("pnpm-workspace.yaml (cannot be read)")
        files["pnpm-workspace.yaml"] = hashlib.sha256(raw).hexdigest()
        keys = {m.group(1).lower() for m in re.finditer(
            r"^\s*['\"]?([A-Za-z][A-Za-z0-9_-]*)['\"]?\s*:", raw.decode("utf-8", "replace"), re.M)}
        code += [f"{k} in pnpm-workspace.yaml" for k in sorted(keys & set(_PNPM_CODE_KEYS))]
    pkg = _read_json(os.path.join(folder, "package.json"))
    pn = pkg.get("pnpm") if isinstance(pkg.get("pnpm"), dict) else {}
    code += [f"pnpm.{k} in package.json" for k in pn if str(k).lower() in _PNPM_CODE_KEYS]
    return {"files": files, "shown": shown, "code": code}


def _npm_run(pkg: dict, what: str, name: str, extra: Optional[list[str]] = None,
             pm: Optional[dict] = None, folder: str = "") -> dict:
    args = ["run", name] + (["--", *extra] if extra else [])
    step = {"what": what, "tool": "npm", "args": args, "hosts": [],
            "scripts": npm_run_scripts(pkg, name)}
    if pm and pm.get("name"):
        if pm["name"] == "pnpm":
            # pnpm passes the words after the script name on as they are.
            args = ["run", name, *(extra or [])]
        step.update(tool="corepack", args=[pm["spec"], *args])
    if folder:
        step["dir"] = folder
    return step


def _run_index(step: dict) -> int:
    """Where "run" is in a script step's arguments (-1 when it is not one)."""
    args = [str(a) for a in step.get("args") or []]
    if step.get("tool") == "npm" and args[:1] == ["run"] and len(args) > 1:
        return 0
    if step.get("tool") == "corepack" and len(args) > 2 and args[1] == "run":
        return 1
    return -1


def _scripts_of(pkg: dict) -> dict:
    scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
    return {k: v for k, v in scripts.items()
            if isinstance(k, str) and _SCRIPT_RE.match(k) and isinstance(v, str)}


def _documented_script(src: str, scripts: dict) -> str:
    """A start-like script the README tells people to run (``npm run dev:all``,
    ``pnpm start:web``, ``yarn serve``), or ""."""
    text = _readme(src)
    for m in re.finditer(r"\b(?:npm run|pnpm(?: run)?|yarn(?: run)?)\s+([A-Za-z0-9:._-]{1,40})",
                         text):
        name = m.group(1)
        if name in scripts and re.search(r"start|serve|dev|preview", name):
            return name
    return ""


def _part_folder(src: str) -> tuple[str, dict]:
    """``(folder, package.json)`` of the part of a project in parts that
    starts (a workspace, or frontend and backend side by side), or ("", {})."""
    found = []
    for name in _PART_DIRS:
        for rel in (name, f"apps/{name}", f"packages/{name}"):
            path = os.path.join(src, *rel.split("/"))
            if os.path.islink(path) or not os.path.isdir(path):
                continue
            pkg = _read_json(os.path.join(path, "package.json"))
            if pkg and any(s in _scripts_of(pkg) for s in _START_SCRIPTS):
                found.append((rel, pkg))
    for rel, pkg in found:
        if "start" in _scripts_of(pkg):
            return rel, pkg
    return found[0] if found else ("", {})


def _parts_plan(src: str, pkg: dict, pm: dict, *, port: int) -> dict:
    """A project in parts: the root installs (workspaces) or the part
    installs in its own folder (side by side); the part starts."""
    folder, sub = _part_folder(src)
    if not folder:
        return {}
    sub_scripts = _scripts_of(sub)
    start_name = next(s for s in _START_SCRIPTS if s in sub_scripts)
    workspace = bool(pkg.get("workspaces")) or os.path.isfile(
        os.path.join(src, "pnpm-workspace.yaml"))
    if workspace and pkg:
        steps, notes = _node_install(src, pkg, pm)
        if not steps:
            steps, notes = _node_install(src, sub, pm)
    else:
        sub_src = os.path.join(src, *folder.split("/"))
        pm = package_manager(sub_src, sub) or pm
        if pm.get("from") == "bad":
            return {"runtime": "node", "how": "", "why_not": pm["why"]}
        steps, notes = _node_install(sub_src, sub, pm)
        for st in steps:
            if not (st.get("tool") == "corepack" and st["args"][:1] in (["enable"],
                                                                         ["install"])):
                st["dir"] = folder
    build = [_npm_run(sub, "build", "build", pm=pm, folder=folder)] \
        if "build" in sub_scripts and start_name in ("start", "preview", "serve") else []
    start = _npm_run(sub, "start", start_name, _dev_flags(sub_scripts[start_name], port),
                     pm=pm, folder=folder)
    start.pop("what", None)
    start.pop("hosts", None)
    return {"runtime": "node", "notes": notes + [f"It starts from its {folder} folder."],
            "how": "node_server", "steps": steps + build, "start": start, "port": port,
            "interfaces": {}, **({"pm": pm} if pm else {})}


def _node_plan(src: str, pkg: dict, *, port: int, box_ready: bool) -> dict:
    scripts = _scripts_of(pkg)
    pm = package_manager(src, pkg) if pkg else {}
    if pm.get("from") == "bad":
        return {"runtime": "node", "how": "", "why_not": pm["why"]}
    bins = _node_bins(pkg)
    start_name = next((s for s in _START_SCRIPTS if s in scripts), "") \
        or _documented_script(src, scripts)
    if not start_name and not bins and not (_is_mcp_node(pkg)) and (
            "build" not in scripts or pkg.get("workspaces")):
        parts = _parts_plan(src, pkg, pm, port=port)
        if parts:
            return parts
    if not pkg:
        return {}
    steps, notes = _node_install(src, pkg, pm)
    plan: dict[str, Any] = {"runtime": "node", "notes": notes}
    if pm:
        plan["pm"] = pm
    if _is_mcp_node(pkg) and bins:
        first = next(iter(bins.values()))
        build = [_npm_run(pkg, "build", "build", pm=pm)] if "build" in scripts else []
        plan.update(how="node_cli", steps=steps + build, start=None, port=None,
                    interfaces={"mcp": {"tool": "node", "args": [first]}, "cli": bins})
        return plan
    if "build" in scripts and not start_name:
        if box_ready:
            return {}
        plan.update(how="web_build_static",
                    steps=steps + [_npm_run(pkg, "build", "build", pm=pm)],
                    start=None, port=None, interfaces={})
        return plan
    if start_name:
        build = [_npm_run(pkg, "build", "build", pm=pm)] \
            if "build" in scripts and start_name in ("start", "preview", "serve") else []
        start = _npm_run(pkg, "start", start_name, _dev_flags(scripts[start_name], port),
                         pm=pm)
        start = {"tool": start["tool"], "args": start["args"], "scripts": start["scripts"]}
        plan.update(how="node_server", steps=steps + build, start=start, port=port,
                    interfaces={"cli": bins} if bins else {})
        return plan
    if bins:
        plan.update(how="node_cli", steps=steps, start=None, port=None,
                    interfaces={"cli": bins})
        return plan
    plan.update(how="library", steps=steps, start=None, port=None, interfaces={})
    return plan


def _pyproject(src: str) -> dict:
    text = _read_text(os.path.join(src, "pyproject.toml"), 256 * 1024)
    if not text:
        return {}
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _py_scripts(pyproject: dict) -> dict[str, str]:
    project = pyproject.get("project") if isinstance(pyproject.get("project"), dict) else {}
    poetry = ((pyproject.get("tool") or {}).get("poetry") or {}) \
        if isinstance(pyproject.get("tool"), dict) else {}
    raw: dict = {}
    for got in (project.get("scripts"), poetry.get("scripts") if isinstance(poetry, dict)
                else None):
        if isinstance(got, dict):
            raw.update(got)
    return {k: str(v) for k, v in raw.items()
            if isinstance(k, str) and _BIN_RE.match(k) and isinstance(v, str)
            and _PYMOD_APP_RE.match(v.strip())}


def _py_install(src: str, pyproject: dict) -> list[dict]:
    pip = ["install", "--no-input", "--disable-pip-version-check"]
    steps: list[dict] = []
    if os.path.isfile(os.path.join(src, "requirements.txt")):
        steps.append({"what": "install", "tool": "pip",
                      "args": [*pip, "-r", "requirements.txt"], "hosts": [PYPI_HOST]})
    if isinstance(pyproject.get("project"), dict) or (
            isinstance(pyproject.get("build-system"), dict)):
        steps.append({"what": "install", "tool": "pip", "args": [*pip, "."],
                      "hosts": [PYPI_HOST]})
    # Its own Python folder; without pip when there is nothing to install.
    venv = ["-m", "venv", "{venv}"] if steps else ["-m", "venv", "--without-pip", "{venv}"]
    return [{"what": "install", "tool": "python", "args": venv, "hosts": []}] + steps


def _procfile_start(src: str, port: int) -> Optional[dict]:
    """The ``web:`` line of a Procfile as a start step, or None when it is not
    one BuildBot can run as plain arguments."""
    for line in _read_text(os.path.join(src, "Procfile"), 16 * 1024).splitlines():
        m = re.match(r"^\s*web\s*:\s*(.+)$", line)
        if not m:
            continue
        words = m.group(1).split()
        if not words or words[0] not in _PROCFILE_PROGRAMS:
            return None
        args = []
        for w in words[1:]:
            w = w.replace("${PORT}", str(port)).replace("$PORT", str(port))
            w = w.replace("0.0.0.0", LOOPBACK)
            if not _ARG_RE.match(w) or ".." in w.split("/"):
                return None
            args.append(w)
        tool = _PROCFILE_PROGRAMS[words[0]]
        if tool == "venv_bin":
            return {"tool": "venv_bin", "bin": words[0], "args": args}
        return {"tool": tool, "args": args}
    return None


def _py_app_start(src: str, port: int) -> Optional[dict]:
    """A well-known Python app shape at the top of the project."""
    if os.path.isfile(os.path.join(src, "manage.py")):
        return {"tool": "venv_python", "args": ["manage.py", "runserver", f"{LOOPBACK}:{port}",
                                                "--noreload"]}
    for name in _PY_APP_FILES:
        text = _read_text(os.path.join(src, name), 256 * 1024)
        if not text:
            continue
        module = name[:-3]
        if re.search(r"^\s*import\s+streamlit|^\s*from\s+streamlit", text, re.M):
            return {"tool": "venv_bin", "bin": "streamlit",
                    "args": ["run", name, "--server.address", LOOPBACK,
                             "--server.port", str(port), "--server.headless", "true"]}
        m = re.search(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*FastAPI\(", text, re.M)
        if m:
            return {"tool": "venv_bin", "bin": "uvicorn",
                    "args": [f"{module}:{m.group(1)}", "--host", LOOPBACK,
                             "--port", str(port)]}
        m = re.search(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*Flask\(", text, re.M)
        if m:
            return {"tool": "venv_bin", "bin": "flask",
                    "args": ["--app", f"{module}:{m.group(1)}", "run", "--host", LOOPBACK,
                             "--port", str(port)]}
        if re.search(r"^\s*import\s+gradio|^\s*from\s+gradio", text, re.M):
            return {"tool": "venv_python", "args": [name]}
        if re.search(r"__name__\s*==\s*['\"]__main__['\"]", text) and re.search(
                r"\b(HTTPServer|serve_forever|app\.run|run_simple)\b", text):
            return {"tool": "venv_python", "args": [name]}
    return None


_FLASK_APP_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,99}(?::[A-Za-z_][A-Za-z0-9_]{0,59})?\Z")
_SKIP_DIRS = frozenset({"node_modules", "tests", "test", "docs", "doc", "examples", "example",
                        "venv", ".venv", "env", "build", "dist", "scripts", "static"})


def _notes_text(src: str) -> str:
    """The project's own setup notes: README, start scripts at the top,
    Procfile, Makefile, Dockerfile (read as data, never run)."""
    parts = [_readme(src)]
    try:
        names = sorted(os.listdir(src))
    except OSError:
        names = []
    for name in names:
        low = name.lower()
        if low.endswith(".sh") or low in ("procfile", "makefile", "dockerfile"):
            parts.append(_read_text(os.path.join(src, name), 64 * 1024))
    return "\n".join(parts)


def _django_dir(src: str) -> Optional[str]:
    """Where manage.py is: "" at the top, a folder one level down, or None."""
    if os.path.isfile(os.path.join(src, "manage.py")):
        return ""
    try:
        names = sorted(os.listdir(src))
    except OSError:
        return None
    for name in names:
        path = os.path.join(src, name)
        if name.lower() in _SKIP_DIRS or name.startswith(".") or os.path.islink(path):
            continue
        if os.path.isfile(os.path.join(path, "manage.py")) and _REL_RE.match(name):
            return name
    return None


def _django_steps(src: str, folder: str) -> list[dict]:
    """The manage.py commands its notes ask for before it starts (from
    :data:`_DJANGO_STEPS`), migrate always; each as plain arguments."""
    text = _notes_text(src)
    asked = set(re.findall(r"manage\.py\s+([a-z_]+)", text)) | {"migrate"}
    extra = {"makemigrations": ["--noinput"], "migrate": ["--noinput"],
             "collectstatic": ["--noinput"], "compress": []}
    steps = []
    for name in _DJANGO_STEPS:
        if name in asked:
            st = {"what": "build", "tool": "venv_python",
                  "args": ["manage.py", name, *extra[name]], "hosts": []}
            if folder:
                st["dir"] = folder
            steps.append(st)
    return steps


def _module_file(src: str, module: str) -> bool:
    rel = module.replace(".", "/")
    return any(os.path.isfile(os.path.join(src, *(rel + tail).split("/")))
               for tail in (".py", "/__init__.py"))


def _documented_py_start(src: str, port: int, deps: str) -> Optional[dict]:
    """A start command the README gives that BuildBot can run as plain
    arguments bound to 127.0.0.1: ``uvicorn mod:app``, ``flask --app X run``
    (or ``FLASK_APP=X flask run``), ``streamlit run X.py``."""
    text = _readme(src)
    m = re.search(r"\buvicorn\s+([A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*)", text)
    if m and _module_file(src, m.group(1).split(":")[0]):
        return {"tool": "venv_bin", "bin": "uvicorn",
                "args": [m.group(1), "--host", LOOPBACK, "--port", str(port)]}
    m = re.search(r"\bflask\s+--app\s+([A-Za-z_][A-Za-z0-9_.:]*)\s+run\b", text) or \
        re.search(r"\bFLASK_APP=([A-Za-z_][A-Za-z0-9_.:]*)\s+flask\s+run\b", text)
    if m and _FLASK_APP_RE.match(m.group(1).removesuffix(".py")) \
            and _module_file(src, m.group(1).removesuffix(".py").split(":")[0]):
        return _flask_start(m.group(1).removesuffix(".py"), port)
    m = re.search(r"\bstreamlit\s+run\s+([A-Za-z0-9_./-]+\.py)\b", text)
    if m and _inside_rel(m.group(1)) and os.path.isfile(os.path.join(src, *m.group(1).split("/"))):
        return {"tool": "venv_bin", "bin": "streamlit",
                "args": ["run", m.group(1), "--server.address", LOOPBACK,
                         "--server.port", str(port), "--server.headless", "true"]}
    return None


def _flask_start(app: str, port: int) -> dict:
    return {"tool": "venv_bin", "bin": "flask",
            "args": ["--app", app, "run", "--host", LOOPBACK, "--port", str(port)]}


def _package_app_start(src: str, port: int, deps: str) -> Optional[dict]:
    """A web app object inside the project's own package: ``X = FastAPI(``
    (run with uvicorn when it lists uvicorn), ``X = Flask(`` or a Flask
    ``create_app`` factory; or ``FLASK_APP`` in its ``.flaskenv``."""
    env = _read_text(os.path.join(src, ".flaskenv"), 4096)
    m = re.search(r"^\s*FLASK_APP\s*=\s*['\"]?([A-Za-z0-9_.:]+?)(?:\.py)?['\"]?\s*$", env, re.M)
    if m and _FLASK_APP_RE.match(m.group(1)) and _module_file(src, m.group(1).split(":")[0]):
        return _flask_start(m.group(1), port)
    try:
        names = sorted(os.listdir(src))
    except OSError:
        return None
    roots = [(n, n) for n in names]
    if os.path.isdir(os.path.join(src, "src")) and not os.path.islink(os.path.join(src, "src")):
        try:
            roots += [(f"src/{n}", n) for n in sorted(os.listdir(os.path.join(src, "src")))]
        except OSError:
            pass
    for rel, pkg in roots:
        path = os.path.join(src, *rel.split("/"))
        if pkg.lower() in _SKIP_DIRS or pkg.startswith(".") or os.path.islink(path) \
                or not re.match(r"^[A-Za-z_][A-Za-z0-9_]{0,59}\Z", pkg) \
                or not os.path.isfile(os.path.join(path, "__init__.py")):
            continue
        for fname in ("__init__.py", "app.py", "main.py", "server.py", "asgi.py", "api.py",
                      "wsgi.py"):
            text = _read_text(os.path.join(path, fname), 256 * 1024)
            if not text:
                continue
            mod = pkg if fname == "__init__.py" else f"{pkg}.{fname[:-3]}"
            got = re.search(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:FastAPI|Starlette)\(", text, re.M)
            if got and "uvicorn" in deps:
                return {"tool": "venv_bin", "bin": "uvicorn",
                        "args": [f"{mod}:{got.group(1)}", "--host", LOOPBACK,
                                 "--port", str(port)],
                        **({"pythonpath_src": True} if rel.startswith("src/") else {})}
            got = re.search(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*Flask\(", text, re.M)
            if got:
                return _flask_start(f"{mod}:{got.group(1)}", port)
            if re.search(r"^def create_app\(", text, re.M) and "flask" in text.lower():
                return _flask_start(mod, port)
    return None


def _python_plan(src: str, *, port: int) -> dict:
    pyproject = _pyproject(src)
    django = _django_dir(src)
    has_py = (pyproject or os.path.isfile(os.path.join(src, "requirements.txt"))
              or os.path.isfile(os.path.join(src, "Procfile")) or django is not None
              or any(os.path.isfile(os.path.join(src, n)) for n in _PY_APP_FILES + ("manage.py",)))
    if not has_py:
        return {}
    steps = _py_install(src, pyproject)
    deps = json.dumps(pyproject.get("project") or {}).lower() + json.dumps(
        ((pyproject.get("tool") or {}).get("poetry") or {}) if isinstance(
            pyproject.get("tool"), dict) else {}).lower() + _read_text(
        os.path.join(src, "requirements.txt"), 64 * 1024).lower()
    start = _procfile_start(src, port) or _documented_py_start(src, port, deps)
    if not start and django is not None:
        # Django: its own notes' manage.py steps (migrate always), then its
        # own small server on 127.0.0.1, in the folder manage.py is in.
        if django and not os.path.isfile(os.path.join(src, "requirements.txt")) and \
                os.path.isfile(os.path.join(src, django, "requirements.txt")):
            steps[1:1] = [{"what": "install", "tool": "pip",
                           "args": ["install", "--no-input", "--disable-pip-version-check",
                                    "-r", f"{django}/requirements.txt"], "hosts": [PYPI_HOST]}]
            if steps[0]["args"][:3] == ["-m", "venv", "--without-pip"]:
                steps[0]["args"] = ["-m", "venv", "{venv}"]
        steps += _django_steps(src, django)
        start = {"tool": "venv_python", "args": ["manage.py", "runserver",
                                                 f"{LOOPBACK}:{port}", "--noreload"]}
        if django:
            start["dir"] = django
    start = start or _py_app_start(src, port) or _package_app_start(src, port, deps)
    if start and start.pop("pythonpath_src", False):
        start["args"] = [*start["args"], "--app-dir", "src"]
    scripts = _py_scripts(pyproject)
    interfaces: dict[str, Any] = {"cli": scripts} if scripts else {}
    if start:
        return {"runtime": "python", "how": "python_server", "steps": steps, "start": start,
                "port": port, "interfaces": interfaces, "notes": []}
    if scripts:
        if re.search(r"(?<![a-z])(mcp|fastmcp)(?![a-z])", deps):
            interfaces["mcp"] = {"tool": "venv_bin", "bin": next(iter(scripts)), "args": []}
        return {"runtime": "python", "how": "python_cli", "steps": steps, "start": None,
                "port": None, "interfaces": interfaces, "notes": []}
    return {"runtime": "python", "how": "library", "steps": steps, "start": None,
            "port": None, "interfaces": {}, "notes": []}


#: Where a project keeps its compose file besides its top folder.
_COMPOSE_DIRS = ("", "docker", "deploy", "deployment", ".docker", "docker-compose")
#: The compose file BuildBot writes from the image a project's README documents.
README_COMPOSE = "buildbot-readme-compose.yml"
#: Images that are only a database or a cache (a compose file of only these
#: is the project's helpers, not the app itself).
_HELPER_IMAGES = re.compile(r"^(?:[a-z0-9.-]+/)?(?:library/)?(?:postgres|postgis/postgis|mysql|"
                            r"mariadb|redis|valkey/valkey|mongo|memcached|rabbitmq|"
                            r"elasticsearch|minio/minio|adminer|mailhog/mailhog|"
                            r"axllent/mailpit)(?::|@|\Z)", re.I)


def _compose_web(text: str) -> bool:
    """Does a compose file run more than a database: a service that is built
    from the project, or an image that is not only a helper, with a port?"""
    if not re.search(r"^\s*(?:ports|expose)\s*:", text, re.M):
        return False
    if re.search(r"^\s*build\s*:", text, re.M):
        return True
    images = re.findall(r"^\s*image\s*:\s*[\"']?([^\s\"'#]+)", text, re.M)
    return any(not _HELPER_IMAGES.match(i) for i in images)


def _compose_file(src: str) -> str:
    """The project's own compose file that runs the app (relative), or ""."""
    for folder in _COMPOSE_DIRS:
        for name in _COMPOSE_FILES:
            rel = f"{folder}/{name}" if folder else name
            path = os.path.join(src, *rel.split("/"))
            if os.path.isfile(path) and not os.path.islink(path) \
                    and _compose_web(_read_text(path)):
                return rel
    return ""


_IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,199}(?::[A-Za-z0-9._-]{1,128})?"
                       r"(?:@sha256:[a-f0-9]{64})?\Z")
#: ``docker run`` options with a value, and the ones without.
_RUN_VALUE_OPTS = frozenset({"-p", "--publish", "-v", "--volume", "-e", "--env", "--name",
                             "--restart", "-w", "--workdir", "--hostname", "-h", "--label",
                             "-l", "--platform", "-u", "--user", "--network", "--net",
                             "--entrypoint", "--env-file", "--cap-add", "--device",
                             "--security-opt", "--shm-size", "--add-host", "--tz",
                             "--memory", "-m", "--cpus", "--log-opt", "--stop-timeout",
                             "--health-cmd", "--pull", "--ulimit", "--gpus", "--mount"})
_RUN_FLAGS = frozenset({"-d", "--detach", "-i", "-t", "-it", "-ti", "-dit", "-itd", "-td",
                        "-dt", "--rm", "--init", "--interactive", "--tty", "--privileged",
                        "-P", "--publish-all", "--read-only"})


def _run_lines(text: str) -> list[str]:
    """Every ``docker run`` command in *text*, its continued lines joined."""
    joined = re.sub(r"[ \t]*(?:\\|`|\^)[ \t]*\r?\n[ \t]*", " ", text)
    out = []
    for line in joined.splitlines():
        at = line.find("docker run ")
        if at >= 0:
            out.append(line[at:].strip().strip("`").strip())
    return out


def _run_to_service(line: str) -> Optional[dict]:
    """One ``docker run`` command as a compose service, or None when it is
    not one BuildBot can read plainly."""
    import shlex
    try:
        words = shlex.split(line, posix=True)
    except ValueError:
        return None
    words = words[2:]                      # "docker run"
    svc: dict = {"restart": "unless-stopped"}
    ports: list = []
    volumes: list = []
    env: dict = {}
    i = 0
    while i < len(words):
        w = words[i]
        if not w.startswith("-"):
            break
        name, _, inline = w.partition("=") if w.startswith("--") else (w, "", "")
        if name in _RUN_FLAGS:
            if name == "--privileged":
                svc["privileged"] = True
            i += 1
            continue
        if name not in _RUN_VALUE_OPTS:
            return None
        if inline:
            value = inline
            i += 1
        else:
            if i + 1 >= len(words):
                return None
            value = words[i + 1]
            i += 2
        if name in ("-p", "--publish"):
            parts = value.split("/")[0].split(":")
            try:
                target = int(parts[-1])
                published = int(parts[-2]) if len(parts) >= 2 and parts[-2] else None
            except ValueError:
                return None
            ports.append({"target": target, **({"published": str(published)}
                                                if published else {})})
        elif name in ("-v", "--volume"):
            source, _, rest = value.partition(":")
            if re.fullmatch(r"[A-Za-z]", source) and rest:     # C:\... on Windows
                source, _, rest = rest.partition(":")
                source = "x" + source
            if not rest or "docker.sock" in value or "docker_engine" in value:
                return None
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", source):
                source = f"data{len(volumes) + 1}"     # a folder of hers: Docker's own
            volumes.append(f"{source}:{rest}")
        elif name in ("-e", "--env"):
            key, eq, val = value.partition("=")
            if eq and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", key):
                env[key] = val
        elif name in ("--network", "--net"):
            if value.lower() not in ("bridge", "default"):
                return None
        elif name == "--cap-add":
            svc.setdefault("cap_add", []).append(value)
        elif name == "--device":
            svc.setdefault("devices", []).append(value)
        elif name in ("-u", "--user"):
            if re.fullmatch(r"[0-9]{1,6}(?::[0-9]{1,6})?", value):
                svc["user"] = value
        elif name == "--entrypoint":
            svc["entrypoint"] = value
        elif name in ("--env-file", "--mount", "--security-opt", "--gpus", "--add-host"):
            return None
    if i >= len(words) or not _IMAGE_RE.match(words[i]) or not ports:
        return None
    svc["image"] = words[i]
    if words[i + 1:]:
        if not all(re.fullmatch(r"[A-Za-z0-9_./:=@,+%-]{1,200}", a) for a in words[i + 1:]):
            return None
        svc["command"] = words[i + 1:]
    svc["ports"] = ports
    if volumes:
        svc["volumes"] = volumes
    if env:
        svc["environment"] = env
    return svc


def readme_compose(src: str) -> str:
    """A compose file for the image the project's README documents (a
    compose example in it, or its ``docker run`` command), as text, or ""."""
    text = _readme(src)
    if "docker" not in text.lower():
        return ""
    for lang, block in re.findall(r"```[ \t]*([A-Za-z-]*)[^\n]*\n(.*?)```", text, re.S):
        if lang.lower() in ("", "yaml", "yml", "docker-compose", "compose") \
                and re.search(r"^services\s*:", block, re.M) \
                and re.search(r"^\s+image\s*:", block, re.M) \
                and not re.search(r"^\s+build\s*:", block, re.M) \
                and _compose_web(block):
            return block.strip() + "\n"
    for line in _run_lines(text):
        svc = _run_to_service(line)
        if svc:
            config: dict = {"services": {"app": svc}}
            named = sorted({v.split(":", 1)[0] for v in svc.get("volumes") or []})
            if named:
                config["volumes"] = {n: {} for n in named}
            return json.dumps(config, indent=1) + "\n"
    return ""


def _compose_plan(src: str) -> dict:
    name = _compose_file(src)
    inline = "" if name else readme_compose(src)
    if not name and not inline:
        return {}
    plan = {"runtime": "docker", "how": "compose", "compose_file": name or README_COMPOSE,
            "steps": [{"what": "install", "tool": "docker",
                       "args": ["compose", "-f", name or README_COMPOSE, "config",
                                "--no-env-resolution", "--format", "json"],
                       "hosts": []}],
            "start": {"tool": "docker",
                      "args": ["compose", "-f", "buildbot-compose.json", "-p", "{project}",
                               "up", "--build", "-d"]},
            "port": None, "interfaces": {},
            "notes": ["BuildBot runs a copy of its compose file with every published "
                      "port limited to 127.0.0.1."]}
    if inline:
        plan["compose_inline"] = inline
        plan["notes"].append("Its README names its Docker image; BuildBot wrote that into "
                             "a compose file and runs it the same way.")
    return plan


def docker_route_ok() -> bool:
    """Docker is on this computer, or BuildBot can install it here."""
    if _host_program("docker"):
        return True
    try:
        from puffpai import deps
        return deps.can_install("docker")
    except Exception:  # noqa: BLE001
        return False


def _openapi(src: str, plan: dict) -> str:
    for rel in _OPENAPI_FILES:
        if os.path.isfile(os.path.join(src, rel)):
            return rel
    start = plan.get("start") or {}
    if start.get("bin") == "uvicorn":
        return "runtime:/openapi.json"
    return ""


def detect(src: str, *, port: int, box_ready: bool = False, docker: bool = True) -> dict:
    """How the project at *src* runs, as a plan (see module doc). A plan with
    ``how == ""`` cannot run here; ``why_not`` says why in plain words.

    *box_ready*: the locked box is there, so a web app with only a build
    script is built there (no host plan). *docker* False: its own way
    without Docker (the fallback when its containers cannot run here)."""
    # The README's Quick Start says how its makers run it (npm, Python or
    # Docker): that way first (the owner's rule: run it the way it's built).
    from puffpai.integrate import readme_steps
    said = readme_steps.read(_readme(src))["runtime"]
    if docker and docker_route_ok() and said in ("docker", ""):
        # Docker first: an app that ships its own containers runs
        # in them, the way its makers run it; native is the fallback.
        compose = _compose_plan(src)
        if compose:
            return finish_plan(src, compose)
    pkg = _read_json(os.path.join(src, "package.json"))
    plan: dict = {}
    if pkg:
        plan = _node_plan(src, pkg, port=port, box_ready=box_ready)
        if not plan and box_ready:
            return {"schema": SCHEMA, "how": "", "why_not": "box"}
        if plan and plan.get("how") == "" and plan.get("why_not"):
            return {"schema": SCHEMA, "how": "", "why_not": plan["why_not"]}
    if not plan or plan.get("how") == "library":
        py = _python_plan(src, port=port)
        if py and (not plan or py.get("how") != "library"):
            plan = py
    if not pkg and (not plan or plan.get("how") in ("library", "python_cli")):
        # No package.json at the top: a Node app in one of its folders
        # (frontend and backend side by side).
        parts = _node_plan(src, {}, port=port, box_ready=box_ready)
        if parts and parts.get("how"):
            plan = parts
    if (not plan or plan.get("how") == "library") and docker:
        compose = _compose_plan(src)
        if compose:
            plan = compose
    if not plan:
        return {"schema": SCHEMA, "how": "", "why_not": UNKNOWN_START}
    return finish_plan(src, plan)


#: Why a project's start is not known.
UNKNOWN_START = "BuildBot could not tell how this project starts"


def finish_plan(src: str, plan: dict) -> dict:
    """The parts every plan gets, whoever worked out its commands: its own
    npm, pnpm and yarn settings (refused when they load code), the keys its
    notes ask for, an OpenAPI description, where its parts come from."""
    plan = dict(plan)
    if plan.get("runtime") == "node":
        config = _node_config(src, _plan_dirs(plan))
        if config["code"]:
            return {"schema": SCHEMA, "how": "",
                    "why_not": "its own npm or yarn settings tell it to run code the yes "
                               "card cannot show (" + "; ".join(config["code"][:5]) + ")"}
        # Always recorded for a Node plan (even empty), so a settings file
        # that appears after the yes is caught too.
        plan["config_files"] = config["files"]
        plan["config_settings"] = config["shown"]
    plan["schema"] = SCHEMA
    plan.setdefault("interfaces", {})
    plan.setdefault("notes", [])
    api = _openapi(src, plan)
    if api:
        plan["interfaces"]["openapi"] = api
    plan["keys"] = declared_keys(src)
    try:
        from puffpai.integrate import apply
        far = apply.lock_download_hosts(src) if plan.get("runtime") == "node" else []
    except Exception:  # noqa: BLE001
        far = []
    plan["outside_parts"] = list(far)[:5]
    return plan


def plan_sha(plan: dict) -> str:
    """The fingerprint of exactly what the yes card shows and what runs."""
    keep = {k: plan.get(k) for k in ("schema", "how", "runtime", "steps", "start", "port",
                                     "compose_file", "keys", "outside_parts")}
    if plan.get("pm"):
        keep["pm"] = plan.get("pm")
    if plan.get("agentic"):
        keep["agentic"] = plan.get("agentic")
    if plan.get("compose_inline"):
        keep["compose_inline"] = plan.get("compose_inline")
    keep["interfaces"] = plan.get("interfaces")
    for k in ("config_files", "config_settings"):
        if k in plan:          # (a plan saved before these existed keeps its fingerprint)
            keep[k] = plan.get(k)
    if plan.get("how") == "assembled":
        # The spec and every glue file it runs (assemble.server.host.make_plan).
        keep["assembled"] = plan.get("assembled")
    return hashlib.sha256(json.dumps(keep, sort_keys=True, default=str).encode()).hexdigest()


# ---------------------------------------------------------------------------
# folders and the saved state
# ---------------------------------------------------------------------------

def _check_id(iid: str) -> str:
    if not isinstance(iid, str) or not _ID_RE.match(iid):
        raise HostError("That app is not known.", "unknown")
    return iid


def root() -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(create=True), ROOT)


def home(iid: str) -> str:
    return os.path.join(root(), _check_id(iid))


def app_dir(iid: str) -> str:
    return os.path.join(home(iid), "app")


def venv_dir(iid: str) -> str:
    return os.path.join(home(iid), "venv")


def _log_path(iid: str, which: str = "run") -> str:
    return os.path.join(home(iid), "logs", f"{which}.log")


def _state_path(iid: str) -> str:
    return os.path.join(home(iid), "run.json")


def read_state(iid: str) -> dict:
    data = fsutil.read_json(_state_path(iid), default={})
    return data if isinstance(data, dict) else {}


def _save_state(iid: str, state: dict) -> None:
    os.makedirs(home(iid), exist_ok=True)
    fsutil.atomic_write_json(_state_path(iid), state)


def all_ids() -> list[str]:
    try:
        names = sorted(os.listdir(root()))
    except OSError:
        return []
    return [n for n in names if _ID_RE.match(n) and os.path.isfile(_state_path(n))]


def project_name(iid: str) -> str:
    """The Docker compose project name for *iid*: lowercase letters, digits,
    dashes (``PUFFPAI_COMPOSE_PREFIX`` names a test run's own prefix)."""
    prefix = os.environ.get("PUFFPAI_COMPOSE_PREFIX") or "bb-"
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,14}", prefix):
        prefix = "bb-"
    return prefix + _check_id(iid)


def _venv_bin(iid: str, name: str) -> str:
    sub = "Scripts" if _IS_WINDOWS else "bin"
    exe = name + (".exe" if _IS_WINDOWS else "")
    return os.path.join(venv_dir(iid), sub, exe)


def _security_log(action: str, iid: str, result: str, details: str = "",
                  extra: Optional[dict] = None, by: str = "owner") -> None:
    try:
        from puffpai.security.audit_log import AuditLog
        AuditLog().log(action=f"host_app_{action}", user=str(by or "owner")[:20],
                       result=result, details=f"{iid} {details}".strip(), extra=extra or {})
    except Exception as exc:  # noqa: BLE001 - the log never stops the work
        logger.warning("host app: security log line not written: %s", type(exc).__name__)


# ---------------------------------------------------------------------------
# programs, by full path only
# ---------------------------------------------------------------------------

def _host_program(name: str) -> Optional[str]:
    from puffpai.security import host_programs
    return host_programs.path(name)


def _npm_cli() -> Optional[str]:
    from puffpai.security import host_programs
    return host_programs.npm_cli()


def corepack_js() -> Optional[str]:
    """corepack's own ``corepack.js`` beside the trusted npm (it ships with
    Node.js), or None. Never a link."""
    cli = _npm_cli()
    if not cli:
        return None
    modules = os.path.dirname(os.path.dirname(os.path.dirname(cli)))
    path = os.path.join(modules, "corepack", "dist", "corepack.js")
    if os.path.islink(path) or not os.path.isfile(path):
        return None
    return path


def pm_bin(iid: str) -> str:
    """The app's own folder for the pnpm or yarn shims corepack writes."""
    return os.path.join(home(iid), "pm_bin")


def missing_programs(plan: dict) -> list[str]:
    """The programs a plan needs that this computer does not have (plain
    names: ``node``, ``docker``). Python is always there: it is this bot's."""
    runtime = str(plan.get("runtime") or "")
    if runtime == "node":
        node = _host_program("node")
        if plan.get("pm") and not corepack_js():
            return ["node"]
        return [] if node and _npm_cli() else ["node"]
    if runtime == "docker":
        return [] if _host_program("docker") else ["docker"]
    return []


def _thing_of(thing: Any) -> str:
    """The thing page id an "Install what's missing" plan belongs to."""
    if isinstance(thing, dict):
        iid = str(thing.get("integration") or "")
        if iid and _ID_RE.match(iid):
            return f"app-{iid}"
    return ""


def install_missing(thing: Any, programs: Optional[list[str]] = None) -> dict:
    """The "Install what's missing" card for the programs a host app needs
    (``node``, ``docker``): the one installer for the whole bot
    (:mod:`puffpai.deps`). Nothing is installed here: this makes the plan
    and the card; her one yes on it (``POST /things/install-missing``) runs
    exactly those steps, each checked by Drawbridge, with no key in the
    installer's environment, one security-log line each.

    Only names from :data:`PROGRAM_WORDS` are passed on, and deps installs
    only programs on BuildBot's own list (``deps.SYSTEM_PROGRAMS``), never a
    name a project wrote. Docker is on that list; where no installer can
    take a program (a Mac, Linux without admin) the card says to install it
    herself. ``{"ok", "line", "programs", "thing", "installer", "card"}``;
    ``ok`` is True when one yes can install all of it."""
    progs = [p for p in (programs or []) if p in PROGRAM_WORDS]
    names = [PROGRAM_WORDS[p] for p in progs]
    line = ("It needs " + " and ".join(names) + ", which is not on this computer yet."
            if names else "Nothing is missing.")
    card: dict = {}
    if progs:
        try:
            from puffpai import deps
            plan = deps.make_plan("", thing=_thing_of(thing),
                                  found={"programs": progs, "modules": [], "node_modules": []},
                                  needs=deps.read(""))
            card = deps.public_plan(plan)
            card["line"] = deps.plan_line(plan)
        except Exception as exc:  # noqa: BLE001 - no card: she installs it herself
            logger.warning("host app: no install card: %s", type(exc).__name__)
            card = {}
    if card.get("plan"):
        line += " \"Install what's missing\" installs it with one yes."
    elif progs:
        line += " Install it yourself, then add it again."
    return {"ok": bool(card.get("plan")) and not card.get("manual"), "line": line,
            "programs": progs, "thing": thing, "installer": "deps" if card else None,
            "card": card}


def _argv(iid: str, step: dict, plan: dict) -> list[str]:
    """The full argument list for one step (programs by full path)."""
    tool = str(step.get("tool") or "")
    args = [str(a).replace("{venv}", venv_dir(iid)).replace("{project}", project_name(iid))
            for a in step.get("args") or []]
    if any("{pm_bin}" in a for a in args):
        args = [a.replace("{pm_bin}", pm_bin(iid)) for a in args]
    if tool == "corepack":
        node, js = _host_program("node"), corepack_js()
        if not node or not js:
            raise HostError("It needs Node.js with its corepack, which is not on this "
                            "computer yet.", "needs")
        return [node, js, *args]
    if tool == "npm":
        node, cli = _host_program("node"), _npm_cli()
        if not node or not cli:
            raise HostError("It needs Node.js, which is not on this computer yet.", "needs")
        # node-options empty on npm's own command line (it wins over every
        # settings file; an empty npm_config_node_options is ignored by npm).
        return [node, cli, "--node-options=", *args]
    if tool == "node":
        node = _host_program("node")
        if not node:
            raise HostError("It needs Node.js, which is not on this computer yet.", "needs")
        return [node, *args]
    if tool == "python":
        return [sys.executable, "-I", *args]
    if tool in ("pip", "venv_python"):
        py = _venv_bin(iid, "python")
        if not os.path.isfile(py):
            raise HostError("Its Python folder is missing. Set it up again.", "failed")
        # pip runs isolated (-I): never a pip/ folder or PYTHON* setting of the
        # project's own folder in place of the real pip.
        return [py, *(["-I", "-m", "pip"] if tool == "pip" else []), *args]
    if tool == "venv_bin":
        name = str(step.get("bin") or "")
        if not _BIN_RE.match(name):
            raise HostError("Its start command cannot be read.", "failed")
        exe = _venv_bin(iid, name)
        if not os.path.isfile(exe):
            raise HostError(f"Its program {name} was not installed. Look at its install log.",
                            "failed")
        return [exe, *args]
    if tool == "assembled_server" and plan.get("how") == "assembled":
        # BuildBot's own app server (assemble/server/app.py), by this bot's Python.
        from puffpai.assemble.server import host as assembled_host
        return [sys.executable, "-I", "-B", assembled_host._SERVER_SCRIPT, *args]  # noqa: SLF001
    if tool == "docker":
        docker = _host_program("docker")
        if not docker:
            raise HostError("It needs Docker, which is not on this computer yet.", "needs")
        return [docker, *args]
    raise HostError("Its start command cannot be read.", "failed")


#: How each tool is shown on the yes card (the program's own name).
_TOOL_SHOWN = {"npm": "npm", "node": "node", "python": "python", "pip": "python -m pip",
               "corepack": "corepack",
               "venv_python": "python", "docker": "docker",
               "assembled_server": "python assembled-server"}


def _shown(step: dict, folder: str, venv: str, project: str) -> str:
    tool = str(step.get("tool") or "")
    word = step.get("bin") if tool == "venv_bin" else _TOOL_SHOWN.get(tool, tool)
    args = [str(a).replace("{venv}", venv).replace("{project}", project)
            .replace("{pm_bin}", os.path.join(os.path.dirname(venv), "pm_bin") if venv
                     else "{pm_bin}") for a in step.get("args") or []]
    line = " ".join([str(word), *args])
    if step.get("dir"):
        line = f"(in its {step['dir']} folder) {line}"
    if _run_index(step) >= 0:
        # npm runs the script's text (and its pre and post scripts) itself:
        # show that text exactly, so what is shown is what runs.
        runs = ", then ".join(f"{json.dumps(n)}: {json.dumps(t)}"
                              for n, t in step.get("scripts") or [])
        line += f" (its package.json runs {runs})" if runs else ""
    return line


def shown_lines(plan: dict, iid: str) -> list[str]:
    """The exact commands, in order, as the yes card shows them."""
    folder, venv, project = app_dir(iid), venv_dir(iid), project_name(iid)
    out = [_shown(s, folder, venv, project) for s in plan.get("steps") or []]
    if plan.get("start"):
        out.append(_shown(plan["start"], folder, venv, project))
    return out


def card_lines(plan: dict, iid: str) -> list[str]:
    """Every line the yes card adds for a host app, in plain words."""
    how = str(plan.get("how") or "")
    lines = [f"How it runs: {HOW_WORDS.get(how, how)}."]
    cmds = shown_lines(plan, iid)
    if cmds:
        lines.append(f"After your yes these commands run, in this order, in its own folder "
                     f"({app_dir(iid)}):")
        lines += [f"  {c}" for c in cmds]
    lines.append("It runs on your computer with your permissions. It can reach the "
                 "internet by itself; the Bridge only covers what BuildBot itself sends.")
    if plan.get("port"):
        lines.append(f"It opens at http://{LOOPBACK}:{plan['port']}/. BuildBot tells it to "
                     f"use only this computer; if it listens on your home network anyway, "
                     f"its page says so.")
    for row in plan.get("config_settings") or []:
        lines.append(f"Its own settings file, which npm uses for every command above: {row}")
    if plan.get("outside_parts"):
        lines.append("Its parts also come from: " + ", ".join(plan["outside_parts"]) + ".")
    if plan.get("keys"):
        if plan.get("how") == "compose":
            lines.append("It asks for these keys: " + ", ".join(plan["keys"]) +
                         ". BuildBot gives a Docker project none of your keys.")
        else:
            lines.append("It asks for these keys: " + ", ".join(plan["keys"]) +
                         ". You can keep them in your vault on its page; only this app "
                         "gets them, when it starts. It gets no other key.")
    lines += [str(n) for n in plan.get("notes") or []]
    env = (plan.get("agentic") or {}).get("env") or {}
    if env:
        lines.append("These settings are set for it: " + ", ".join(
            f"{k}={v}" for k, v in list(env.items())[:12]) + ".")
    if plan.get("asks"):
        lines.append("It also asks for these, which BuildBot does not run by itself (each "
                     "needs your own yes): " + "; ".join(str(a) for a in plan["asks"][:6]) + ".")
    return lines


# ---------------------------------------------------------------------------
# environment
# ---------------------------------------------------------------------------

def _base_env(iid: str, plan: dict) -> dict[str, str]:
    """A small, fixed environment: the system folders, its own folders, the
    port. Nothing of BuildBot's own environment (no keys, no tokens)."""
    from puffpai.security import host_programs
    env = dict(host_programs.run_kwargs()["env"])
    paths = [p for p in env.get("PATH", "").split(os.pathsep) if p]
    node = _host_program("node") if plan.get("runtime") == "node" else None
    if node:
        paths.insert(0, os.path.dirname(node))
    if plan.get("pm"):
        paths.insert(0, pm_bin(iid))
        env["COREPACK_HOME"] = os.path.join(home(iid), "corepack")
        env["COREPACK_ENABLE_DOWNLOAD_PROMPT"] = "0"
        env["COREPACK_ENABLE_AUTO_PIN"] = "0"
        env["COREPACK_NPM_REGISTRY"] = "https://" + NPM_HOST
        env["COREPACK_DEFAULT_TO_LATEST"] = "0"
    if plan.get("runtime") == "docker":
        docker = _host_program("docker")
        if docker:
            # Its own helpers (docker-credential-desktop, compose) sit beside it.
            paths.insert(0, os.path.dirname(docker))
        real_home = os.path.expanduser("~")
        if not _IS_WINDOWS and os.path.isabs(real_home):
            # Her Docker context (Docker Desktop's socket) is in her own folder.
            env["DOCKER_CONFIG"] = os.path.join(real_home, ".docker")
    if plan.get("runtime") == "python":
        paths.insert(0, os.path.dirname(_venv_bin(iid, "python")))
        env["VIRTUAL_ENV"] = venv_dir(iid)
        env["PYTHONNOUSERSITE"] = "1"
        env["PIP_CACHE_DIR"] = os.path.join(home(iid), "cache", "pip")
        # Never her own pip settings (an index address there can hold a password).
        env["PIP_CONFIG_FILE"] = os.devnull
    env["PATH"] = os.pathsep.join(dict.fromkeys(paths))
    if _IS_WINDOWS:
        system32 = host_programs.SYSTEM32
        env["ComSpec"] = os.path.join(system32, "cmd.exe")
        env["PATHEXT"] = ".COM;.EXE;.BAT;.CMD"
    own = os.path.join(home(iid), "home")
    os.makedirs(own, exist_ok=True)
    env["HOME"] = own
    # npm: its own cache and an empty settings file, so her own npm settings
    # (and any sign-in token in them) are never read.
    env["npm_config_cache"] = os.path.join(home(iid), "cache", "npm")
    # The computer's own npm settings (npm's global file) are never read either.
    for var, name in (("npm_config_userconfig", "npmrc"),
                      ("npm_config_globalconfig", "npmrc-global")):
        npmrc = os.path.join(home(iid), name)
        if not os.path.exists(npmrc):
            with open(npmrc, "w", encoding="utf-8") as fh:
                fh.write("")
        env[var] = npmrc
    env["npm_config_update_notifier"] = "false"
    env["npm_config_fund"] = "false"
    env["npm_config_audit"] = "false"
    # The shell npm runs a script with is this computer's own, never one a
    # project's .npmrc names (the environment wins over a project's .npmrc).
    env["npm_config_script_shell"] = env["ComSpec"] if _IS_WINDOWS else "/bin/sh"
    env["npm_config_shell"] = env["npm_config_script_shell"]
    if plan.get("pm") and _IS_WINDOWS:
        # pnpm and yarn treat a named shell as a POSIX one (``-c``), so
        # cmd.exe opened a prompt instead of running the script. They use
        # cmd.exe themselves on Windows; a project .npmrc or
        # pnpm-workspace.yaml that names a shell is refused anyway.
        env.pop("npm_config_script_shell", None)
        env.pop("npm_config_shell", None)
    port = plan.get("port")
    if port:
        env["PORT"] = str(port)
        env["HOST"] = LOOPBACK
        env["HOSTNAME"] = LOOPBACK
        env["BIND"] = LOOPBACK
        for name in ("BIND_ADDRESS", "LISTEN_ADDRESS", "SERVER_HOST", "APP_HOST",
                     "UVICORN_HOST", "STREAMLIT_SERVER_ADDRESS", "NUXT_HOST", "NITRO_HOST"):
            env[name] = LOOPBACK
        env["BROWSER"] = "none"
        env["GRADIO_SERVER_NAME"] = LOOPBACK
        env["GRADIO_SERVER_PORT"] = str(port)
        # Web frameworks that check the address asked for (Django and the
        # like): only this computer's own names.
        env["ALLOWED_HOSTS"] = env["DJANGO_ALLOWED_HOSTS"] = f"{LOOPBACK},localhost"
        env["CSRF_TRUSTED_ORIGINS"] = f"http://{LOOPBACK}:{port},http://localhost:{port}"
    # Settings its own notes name, as Toolsmith's plan (checked again: plain
    # names, never one that looks like a key, never one BuildBot sets).
    for name, value in ((plan.get("agentic") or {}).get("env") or {}).items():
        if _ENV_NAME_RE.match(str(name)) and not _SECRETISH.search(str(name)) \
                and str(name) not in env and str(name) not in _NEVER_ENV \
                and isinstance(value, str) and len(value) <= 200 \
                and not re.search(r"[\n\r\x00]", value):
            env[str(name)] = value
    # No key here: this environment is also every install step's. Keys are
    # added by start() alone (app_keys).
    env.update(_EXTRA_ENV)
    for name in _NEVER_ENV:
        env.pop(name, None)
    return env


#: Never in a project's environment: settings that load code into Node or
#: Python before the shown command runs.
_NEVER_ENV = ("NODE_OPTIONS", "NODE_PATH", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP",
              "PYTHONINSPECT", "PYTHONUSERBASE")


#: Seam for the tests (``npm_config_offline`` and the like).
_EXTRA_ENV: dict[str, str] = {}


def app_keys(iid: str, plan: dict) -> dict[str, str]:
    """Keys from her vault for this app's own process: only names its yes
    card listed (``plan["keys"]``, covered by :func:`plan_sha`), only ones
    she kept for THIS app on its page (``keys.put_app_key``). Never for a
    Docker project (compose writes what it is given into its rendered file).

    Raises ``keys.KeyUnavailable`` when a kept key cannot be read (vault
    locked): the caller does not start it."""
    if plan.get("how") == "compose" or not plan.get("keys"):
        return {}
    from puffpai.security import keys
    return keys.app_keys(_check_id(iid), list(plan.get("keys") or []))


def key_rows(iid: str) -> list[dict]:
    """Its page's keys: ``[{"name", "set", "get_url", "from", "prompt",
    "help"}]`` for each name its yes card listed, with a "Get a key" link
    read from its own README. Names only, never a value."""
    plan = read_state(_check_id(iid)).get("plan") or {}
    names = [str(n) for n in plan.get("keys") or []]
    if not names or plan.get("how") == "compose":
        return []           # a Docker project gets none of her keys
    try:
        from puffpai.security import keys
        kept = set(keys.app_keys_set(iid))
    except Exception:  # noqa: BLE001
        kept = set()
    try:
        from puffpai import deps
        text = _readme(app_dir(iid))
        link = {n: deps.get_url_for(n, text) for n in names}
    except Exception:  # noqa: BLE001
        link = {}
    return [{"name": n, "set": n in kept, "get_url": link.get(n, ""), "from": "text",
             "prompt": "", "help": "Only this app gets it, when it starts."}
            for n in names]


def _popen_kwargs() -> dict:
    if _IS_WINDOWS:
        # Its own group, and never a console window (procflags).
        return procflags.hidden(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200))
    return {"start_new_session": True}


def _open_log(path: str, *, fresh: bool = False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        if fresh or os.path.getsize(path) > LOG_MAX:
            if os.path.exists(path) and not fresh:
                os.replace(path, path + ".1")
            elif fresh and os.path.exists(path):
                os.remove(path)
    except OSError:
        pass
    return open(path, "ab")


# ---------------------------------------------------------------------------
# setting it up
# ---------------------------------------------------------------------------

def _copy_tree(src: str, dest: str) -> None:
    """Regular files only; never a link, never ``.git`` or ``node_modules``."""
    from puffpai.integrate import classify
    if os.path.lexists(dest):
        _rmtree(dest)
    os.makedirs(dest, exist_ok=True)
    for rel, full, _size in classify._walk(src):  # noqa: SLF001 - links never followed
        target = os.path.join(dest, *rel.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(full, target)


def _rmtree(path: str) -> None:
    from puffpai.security import safe_archive
    rm = getattr(safe_archive, "safe_rmtree", None)
    if rm is not None:
        rm(path, allowed_roots=[root()])
    else:  # pragma: no cover - the helper is always there in a real build
        shutil.rmtree(path, ignore_errors=True)


def _bridge_open() -> bool:
    try:
        from puffpai.security import outside_switches
        return not outside_switches.is_off(SERVICE)
    except Exception:  # noqa: BLE001 - unreadable: closed
        return False


def _ledger(host: str, ok: bool) -> None:
    try:
        from puffpai.security import egress_ledger
        egress_ledger.record(SERVICE, host=host, outcome="done" if ok else "failed",
                             method="GET", path="", code="" if ok else "no_answer",
                             what="box_install")
    except Exception:  # noqa: BLE001 - the log never stops the work
        pass


def _check_npm_scripts(iid: str, step: dict) -> None:
    """An ``npm run`` step runs only while its package.json still says
    exactly what the yes card showed (:func:`npm_run_scripts`)."""
    args = [str(a) for a in step.get("args") or []]
    at = _run_index(step)
    if at < 0:
        return
    now = npm_run_scripts(_read_json(os.path.join(_step_dir(iid, step), "package.json")),
                          args[at + 1])
    if now != [list(x) for x in step.get("scripts") or []]:
        raise HostError("Its package.json changed after your yes, so BuildBot did not run "
                        "it. Add it again to see what it runs now.", "changed")


def _check_node_config(iid: str, plan: dict) -> None:
    """A Node project runs only while its own npm and yarn settings files are
    exactly the ones its yes covered (none added, none changed)."""
    if plan.get("runtime") != "node":
        return
    now = _node_config(app_dir(iid), _plan_dirs(plan))
    if now["code"] or now["files"] != (plan.get("config_files") or {}):
        raise HostError("Its npm settings file changed after your yes, so BuildBot did not "
                        "run it. Add it again to see what it runs now.", "changed")


def _check_assembled(iid: str, plan: dict) -> None:
    """An assembled app starts only while its spec and every glue file are
    exactly what was checked (and its gateway and Bridge sites are ready)."""
    from puffpai.assemble.server import host as assembled_host
    try:
        assembled_host.prepare_start(iid, plan)
    except assembled_host.AssembleError as exc:
        raise HostError(exc.line, exc.code) from None


def _step_dir(iid: str, step: dict) -> str:
    """The folder a step runs in: the app's own folder, or one of its part
    folders (checked to stay inside it)."""
    base = app_dir(iid)
    rel = str(step.get("dir") or "")
    if not rel:
        return base
    if not _inside_rel(rel):
        raise HostError("Its folder cannot be read.", "failed")
    path = os.path.join(base, *rel.split("/"))
    if os.path.islink(path) or not os.path.isdir(path):
        raise HostError("Its folder is missing. Set it up again.", "failed")
    return path


def _run_step(iid: str, step: dict, plan: dict, *, by: str) -> None:
    hosts = [str(h) for h in step.get("hosts") or []] + (
        list(plan.get("outside_parts") or []) if step.get("hosts") else [])
    if hosts and not _bridge_open():
        raise HostError("The Bridge is closed for downloads, so its parts could not be "
                        "fetched. Open it for projects you pick, then set it up again.",
                        "bridge")
    argv = _argv(iid, step, plan)
    _check_npm_scripts(iid, step)
    _check_node_config(iid, plan)
    env = _base_env(iid, plan)
    cwd = home(iid) if step.get("tool") == "python" else _step_dir(iid, step)
    _security_log("step", iid, "start", details=str(step.get("what") or ""),
                  extra={"command": _shown(step, app_dir(iid), venv_dir(iid),
                                           project_name(iid))[:300]}, by=by)
    with _open_log(_log_path(iid, "install")) as log:
        log.write(f"\n$ {_shown(step, '', venv_dir(iid), project_name(iid))}\n".encode())
        log.flush()
        try:
            code = _run_contained(argv, cwd=cwd, env=env, stdout=log,
                                  timeout=STEP_SECONDS)
        except HostError as exc:
            log.write(f"Could not start: {exc.code}\n".encode())
            code = -1
        except OSError as exc:
            log.write(f"Could not start: {type(exc).__name__}\n".encode())
            code = -1
    for host in hosts:
        _ledger(host, code == 0)
    if code != 0:
        _security_log("step", iid, "failed", details=str(step.get("what") or ""), by=by)
        what = "build" if step.get("what") == "build" else "install"
        raise HostError(f"Its {what} step did not work"
                        + (" in time" if code is None else "")
                        + ". Its install log on its page says why.", "step")


def set_up(iid: str, src: str, plan: dict, *, by: str = "owner",
           progress: Callable[[str], None] = lambda _l: None, from_step: int = 0) -> dict:
    """Copy the project, run its install steps. ``{"ok", "line"}``; raises
    nothing. Saves the plan so Start, Stop and the bot's start can find it.

    *from_step*: the files are already copied and the steps before it ran
    (Toolsmith's next try after a step did not work). A step that does not
    work is named in ``failed_step`` (its index) and ``failed`` (as shown)."""
    current = {"i": -1}
    with _lock_for(_check_id(iid)):
        try:
            if plan.get("how") not in HOWS:
                raise HostError("BuildBot could not tell how this project starts.", "how")
            missing = missing_programs(plan)
            if missing:
                hook = install_missing({"integration": iid}, missing)
                raise HostError(hook["line"], "needs")
            os.makedirs(home(iid), exist_ok=True)
            if plan.get("pm"):
                os.makedirs(pm_bin(iid), exist_ok=True)     # corepack's shims go here
            if from_step <= 0 or not os.path.isdir(app_dir(iid)):
                _copy_tree(src, app_dir(iid))
                from_step = 0
            if plan.get("how") == "compose":
                _compose_ready(iid, plan, progress)
            _security_log("install", iid, "start", details=str(plan.get("how")), by=by)
            for i, step in enumerate(plan.get("steps") or []):
                if i < from_step:
                    continue
                current["i"] = i
                progress(f"  Running: {_shown(step, '', venv_dir(iid), project_name(iid))}")
                _run_step(iid, step, plan, by=by)
            current["i"] = -1
            if plan.get("how") == "compose":
                _compose_prepare(iid, plan)
            _save_state(iid, {"plan": plan, "plan_sha": plan_sha(plan), "wanted_on": False,
                              "set_up_at": _now(), "state": "stopped"})
            _security_log("install", iid, "done", details=str(plan.get("how")), by=by)
            return {"ok": True, "line": "  Set it up on this computer ... done"}
        except HostError as exc:
            _security_log("install", iid, "failed", details=exc.code, by=by)
            return {"ok": False, "line": exc.line, "code": exc.code, **_failed_at(
                iid, plan, current["i"])}
        except Exception as exc:  # noqa: BLE001 - fail closed, say it plainly
            logger.warning("host app: set up failed: %s", type(exc).__name__)
            _security_log("install", iid, "failed", details=type(exc).__name__, by=by)
            return {"ok": False, "line": "Setting it up did not work.", "code": "failed",
                    **_failed_at(iid, plan, current["i"])}


def _all_output(got: Any) -> str:
    out = b""
    for part in (getattr(got, "stderr", b""), getattr(got, "stdout", b"")):
        if isinstance(part, str):
            part = part.encode("utf-8", "replace")
        out += part or b""
    return out.decode("utf-8", "replace")


def _last_line(got: Any) -> str:
    """The last meaningful line a finished command printed (its real error)."""
    lines = [ln.strip() for ln in _all_output(got).splitlines() if ln.strip()]
    return (lines[-1] if lines else "no output")[:300]


def _failed_at(iid: str, plan: dict, i: int) -> dict:
    steps = plan.get("steps") or []
    if not 0 <= i < len(steps):
        return {}
    return {"failed_step": i,
            "failed": _shown(steps[i], "", venv_dir(iid), project_name(iid))}


def build_output(iid: str) -> str:
    """For ``web_build_static``: the built folder with an ``index.html``, or ""."""
    from puffpai.integrate import apply
    src = app_dir(iid)
    for rel in apply.build_output_candidates(src):
        path = os.path.join(src, *rel.split("/"))
        if os.path.isfile(os.path.join(path, "index.html")) and not os.path.islink(path):
            return path
    return ""


# ---------------------------------------------------------------------------
# docker compose: a rendered copy with every published port on 127.0.0.1
# ---------------------------------------------------------------------------

_DOCKER_SOCK = re.compile(r"docker\.sock|\\\\\.\\pipe\\docker_engine|//\./pipe/docker_engine",
                          re.I)


#: The parts of a rendered compose file BuildBot understands. Anything else
#: is refused (an allow-list: a part BuildBot does not know is not run).
_COMPOSE_TOP = frozenset({"name", "services", "networks", "volumes", "secrets", "configs"})
_SERVICE_KEYS = frozenset({
    "image", "build", "command", "entrypoint", "environment", "env_file", "ports", "expose",
    "volumes", "volumes_from", "depends_on", "healthcheck", "restart", "working_dir", "user",
    "labels", "container_name", "hostname", "domainname", "networks", "network_mode", "links",
    "tty", "stdin_open", "init", "stop_signal", "stop_grace_period", "logging", "deploy",
    "mem_limit", "mem_reservation", "memswap_limit", "cpus", "cpu_shares", "cpu_count",
    "cpu_percent", "cpu_quota", "cpu_period", "cpuset", "shm_size", "ulimits", "pids_limit",
    "read_only", "tmpfs", "dns", "dns_search", "dns_opt", "extra_hosts", "platform",
    "pull_policy", "profiles", "scale", "secrets", "configs", "cap_drop", "security_opt",
    "ipc", "pid", "gpus", "runtime", "privileged", "cap_add", "devices", "userns_mode",
    "attach", "sysctls", "mac_address", "annotations"})
_BUILD_KEYS = frozenset({"context", "dockerfile", "dockerfile_inline", "args", "target",
                         "labels", "cache_from", "shm_size", "extra_hosts", "platforms",
                         "tags", "pull", "no_cache", "network"})
_DEPLOY_KEYS = frozenset({"resources", "restart_policy", "replicas", "labels", "mode",
                          "update_config", "rollback_config", "placement", "endpoint_mode"})
_NETWORK_KEYS = frozenset({"name", "driver", "internal", "attachable", "labels", "ipam",
                           "enable_ipv6", "external", "driver_opts"})
_VOLUME_KEYS = frozenset({"name", "driver", "driver_opts", "labels", "external"})
_FILE_KEYS = frozenset({"name", "file", "environment", "content", "external", "labels"})
#: The only security_opt values allowed: ones that take powers away.
_SAFE_SECURITY_OPTS = frozenset({"no-new-privileges", "no-new-privileges:true",
                                 "no-new-privileges=true"})
#: A GPU for the containers is allowed (AI apps need it); no other device.
_GPU_CAPS = frozenset({"gpu", "compute", "utility", "video", "graphics", "display"})


def _inside_project(path: Any, root_real: str, project_root: str) -> bool:
    text = str(path or "")
    if not text:
        return False
    if not os.path.isabs(text):
        text = os.path.join(project_root, text)
    real = os.path.normcase(os.path.realpath(text))
    return real == root_real or real.startswith(root_real.rstrip("\\/") + os.sep)


def _unknown(keys: Any, known: frozenset) -> list[str]:
    return sorted(str(k) for k in (keys or {}) if str(k) not in known
                  and not str(k).startswith("x-"))


def _shares_only_own(value: Any, services: dict, allowed: tuple[str, ...]) -> bool:
    """A namespace setting (network_mode, ipc, pid) that is private, or
    shared with another service of this same project only."""
    text = str(value or "").strip().lower()
    if text in allowed:
        return True
    return text.startswith("service:") and str(value).strip()[8:] in services


def _service_problems(name: str, svc: dict, services: dict, root_real: str,
                      project_root: str) -> list[str]:
    out: list[str] = []
    inside = lambda p: _inside_project(p, root_real, project_root)  # noqa: E731
    for key in _unknown(svc, _SERVICE_KEYS):
        out.append(f"{name} uses {key}, which BuildBot does not run")
    if not _shares_only_own(svc.get("network_mode"), services,
                            ("", "bridge", "none", "default")):
        out.append(f"{name} wants this computer's own network or another container's")
    if svc.get("privileged"):
        out.append(f"{name} wants full control of this computer")
    if svc.get("cap_add"):
        out.append(f"{name} wants extra powers on this computer")
    if svc.get("devices"):
        out.append(f"{name} wants devices from this computer")
    if not _shares_only_own(svc.get("ipc"), services, ("", "private", "shareable", "none")):
        out.append(f"{name} wants to share memory with this computer or another container")
    if not _shares_only_own(svc.get("pid"), services, ("",)):
        out.append(f"{name} wants to see this computer's programs")
    if str(svc.get("userns_mode") or "").strip():
        out.append(f"{name} wants this computer's own users")
    for opt in svc.get("security_opt") or []:
        if str(opt).strip().lower() not in _SAFE_SECURITY_OPTS:
            out.append(f"{name} wants to switch off Docker's safety rules ({str(opt)[:40]})")
            break
    if str(svc.get("runtime") or "").strip().lower() not in ("", "runc", "nvidia"):
        out.append(f"{name} wants a container runtime BuildBot does not know")
    driver = str((svc.get("logging") or {}).get("driver") or "") \
        if isinstance(svc.get("logging"), dict) else ""
    if driver not in ("", "json-file", "local", "none"):
        out.append(f"{name} sends its logs somewhere BuildBot does not know")
    for ref in svc.get("volumes_from") or []:
        source = str(ref).split(":", 1)
        own = source[0] if source[0] not in ("service", "container") else (
            source[1].split(":", 1)[0] if source[0] == "service" and len(source) > 1 else "")
        if own not in services:
            out.append(f"{name} wants files from a container outside the project")
            break
    for entry in svc.get("env_file") or []:
        path = entry.get("path") if isinstance(entry, dict) else entry
        if not inside(path):
            out.append(f"{name} wants a settings file from outside the project")
            break
    build = svc.get("build")
    if build is not None:
        build = build if isinstance(build, dict) else {"context": build}
        for key in _unknown(build, _BUILD_KEYS):
            out.append(f"{name} builds with {key}, which BuildBot does not run")
        context = build.get("context") or project_root
        if not inside(context):
            out.append(f"{name} builds from a folder outside the project")
        dockerfile = build.get("dockerfile")
        if dockerfile and not inside(dockerfile if os.path.isabs(str(dockerfile))
                                     else os.path.join(str(context), str(dockerfile))):
            out.append(f"{name} builds from a file outside the project")
        if str(build.get("network") or "").strip().lower() not in ("", "default", "bridge",
                                                                   "none"):
            out.append(f"{name} builds on this computer's own network")
    deploy = svc.get("deploy")
    if isinstance(deploy, dict):
        for key in _unknown(deploy, _DEPLOY_KEYS):
            out.append(f"{name} uses deploy {key}, which BuildBot does not run")
        reservations = ((deploy.get("resources") or {}).get("reservations") or {}) \
            if isinstance(deploy.get("resources"), dict) else {}
        for dev in (reservations.get("devices") or []) if isinstance(reservations, dict) else []:
            caps = {str(c).lower() for c in (dev.get("capabilities") or [])} \
                if isinstance(dev, dict) else set()
            if not caps or not caps <= _GPU_CAPS or "gpu" not in caps:
                out.append(f"{name} wants devices from this computer")
                break
    for vol in svc.get("volumes") or []:
        vol = vol if isinstance(vol, dict) else {"type": "bind", "source": str(vol)}
        source = str(vol.get("source") or "")
        kind = str(vol.get("type") or "bind")
        if _DOCKER_SOCK.search(source) or kind == "npipe":
            out.append(f"{name} wants to control Docker itself")
        elif kind == "bind":
            if not inside(source):
                out.append(f"{name} wants a folder from outside the project")
        elif kind not in ("volume", "tmpfs"):
            out.append(f"{name} wants a {kind[:20]} mount, which BuildBot does not run")
    return out


def compose_problems(config: dict, project_root: str) -> list[str]:
    """Why a rendered compose file may not run here (plain words), or [].

    An allow-list: every part of the file must be one BuildBot understands
    (:data:`_COMPOSE_TOP`, :data:`_SERVICE_KEYS` and the rest), and then:
    no host network or another container's, no privileged containers, no
    extra powers, no devices (a GPU is allowed), no host memory, programs or
    users, Docker's safety rules stay on, no Docker socket or pipe, and every
    folder, settings file, secret, config and build folder is inside the
    project. Named volumes and networks are Docker's own (no outside ones,
    no options that bind a folder or a device of this computer)."""
    out: list[str] = []
    root_real = os.path.normcase(os.path.realpath(project_root))
    inside = lambda p: _inside_project(p, root_real, project_root)  # noqa: E731
    for key in _unknown(config, _COMPOSE_TOP):
        out.append(f"its compose file uses {key}, which BuildBot does not run")
    services = config.get("services") if isinstance(config.get("services"), dict) else {}
    for name, svc in services.items():
        if isinstance(svc, dict):
            out += _service_problems(str(name), svc, services, root_real, project_root)
    for name, net in (config.get("networks") or {}).items() \
            if isinstance(config.get("networks"), dict) else ():
        net = net if isinstance(net, dict) else {}
        for key in _unknown(net, _NETWORK_KEYS):
            out.append(f"network {name} uses {key}, which BuildBot does not run")
        if net.get("external"):
            out.append(f"network {name} is from outside the project")
        if str(net.get("driver") or "").lower() not in ("", "bridge", "default"):
            out.append(f"network {name} wants a network BuildBot does not run")
        if net.get("driver_opts"):
            out.append(f"network {name} sets network options BuildBot does not run")
    for name, vol in (config.get("volumes") or {}).items() \
            if isinstance(config.get("volumes"), dict) else ():
        vol = vol if isinstance(vol, dict) else {}
        for key in _unknown(vol, _VOLUME_KEYS):
            out.append(f"volume {name} uses {key}, which BuildBot does not run")
        if vol.get("external"):
            out.append(f"volume {name} is from outside the project")
        if str(vol.get("driver") or "").lower() not in ("", "local"):
            out.append(f"volume {name} wants a storage driver BuildBot does not run")
        opts = vol.get("driver_opts") or {}
        opts = opts if isinstance(opts, dict) else {"device": str(opts)}
        if opts and not (str(opts.get("type") or "") == "tmpfs"
                         and str(opts.get("device") or "tmpfs") == "tmpfs"
                         and "bind" not in str(opts.get("o") or "")):
            out.append(f"volume {name} wants a folder or a device of this computer")
    for top in ("secrets", "configs"):
        for name, item in (config.get(top) or {}).items() \
                if isinstance(config.get(top), dict) else ():
            item = item if isinstance(item, dict) else {}
            for key in _unknown(item, _FILE_KEYS):
                out.append(f"{top[:-1]} {name} uses {key}, which BuildBot does not run")
            if item.get("external"):
                out.append(f"{top[:-1]} {name} is from outside the project")
            if item.get("file") and not inside(item["file"]):
                out.append(f"{top[:-1]} {name} wants a file from outside the project")
    return out[:5]


#: Container ports that are a web page (most likely first), and ones that never are.
_WEB_TARGETS = (80, 8080, 3000, 8000, 5000, 8081, 8888, 8096, 9000, 4000, 5173, 8090, 3001,
                8443, 443)
_NOT_WEB_TARGETS = frozenset({5432, 3306, 6379, 27017, 11211, 5672, 15672, 9200, 9300, 1883,
                              25, 53, 22, 21, 123, 161, 514, 1900, 5353, 67, 68})


def _web_rank(target: Any) -> int:
    try:
        t = int(target)
    except (TypeError, ValueError):
        return 99
    if t in _NOT_WEB_TARGETS:
        return 98
    return _WEB_TARGETS.index(t) if t in _WEB_TARGETS else 50


def limit_ports(config: dict, *, remap: bool = False) -> list[int]:
    """Every published port of *config* bound to 127.0.0.1 (in place). The
    published port numbers, the one most likely its web page first.

    *remap*: a port with no published number, or one another program uses,
    gets a free one (so two apps that both say 8080 both run)."""
    published: list[tuple[int, int]] = []
    taken: set[int] = set()
    services = config.get("services") or {}
    if remap and not any(isinstance(s, dict) and s.get("ports") for s in services.values()):
        # Nothing published: the first port it exposes, on a free place.
        for svc in services.values():
            if isinstance(svc, dict) and svc.get("expose"):
                exposed = sorted(svc["expose"], key=_web_rank)
                try:
                    svc["ports"] = [{"target": int(str(exposed[0]).split("/")[0]),
                                     "protocol": "tcp"}]
                except ValueError:
                    continue
                break
    for svc in services.values():
        if not isinstance(svc, dict):
            continue
        ports = []
        for p in svc.get("ports") or []:
            if not isinstance(p, dict):
                continue
            p = dict(p)
            p["host_ip"] = LOOPBACK
            try:
                number = int(str(p.get("published") or "0").split("-")[0])
            except ValueError:
                number = 0
            if remap and (not number or number in taken or not _free(number)):
                number = free_port()
                p["published"] = str(number)
            if number:
                taken.add(number)
                published.append((_web_rank(p.get("target")), number))
            ports.append(p)
        if "ports" in svc:
            svc["ports"] = ports
    return [n for _rank, n in sorted(published, key=lambda x: x[0])]


def _compose_ready(iid: str, plan: dict, progress: Callable[[str], None]) -> None:
    """Before its compose steps: Docker answers (started if it was not), the
    compose file its README documents is written, and a settings file it
    reads (``.env``) is made from its own example when it has none."""
    try:
        from puffpai import deps
        got = deps.ensure_docker_running(progress)
    except Exception as exc:  # noqa: BLE001
        got = {"ok": False, "line": f"Docker did not answer ({type(exc).__name__})."}
    if not got.get("ok"):
        raise HostError(str(got.get("line") or "Docker is not running."), "needs")
    folder = app_dir(iid)
    if plan.get("compose_inline"):
        with open(os.path.join(folder, README_COMPOSE), "w", encoding="utf-8") as fh:
            fh.write(str(plan["compose_inline"]))
    rel = str(plan.get("compose_file") or "")
    where = os.path.dirname(os.path.join(folder, *rel.split("/"))) if rel else folder
    for base in dict.fromkeys([where, folder]):
        env = os.path.join(base, ".env")
        if os.path.lexists(env):
            continue
        for name in _ENV_EXAMPLES:
            example = os.path.join(base, name)
            if os.path.isfile(example) and not os.path.islink(example):
                shutil.copyfile(example, env)
                break
    # A settings file its compose file names but the project does not ship
    # (made by hand in its own notes): an empty one, inside its folder only.
    step = {"tool": "docker", "args": ["compose", "-f", rel or README_COMPOSE, "config",
                                       "--no-env-resolution", "--format", "json"]}
    root_real = os.path.normcase(os.path.realpath(folder))
    for _ in range(5):
        got = _run(_argv(iid, step, plan), cwd=folder, env=_base_env(iid, plan),
                   stdin=subprocess.DEVNULL, capture_output=True, timeout=120, check=False,
                   **_popen_kwargs())
        text = _all_output(got)
        found = _MISSING_ENV_FILE.search(text) if got.returncode != 0 else None
        if not found:
            return
        path = found.group(1).strip().strip("\"'")
        if not _inside_project(path, root_real, folder) or os.path.lexists(path):
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8"):
            pass


_MISSING_ENV_FILE = re.compile(r"env file (.+?) not found", re.I)


def _compose_prepare(iid: str, plan: dict) -> None:
    """Read the project's compose file as Docker renders it (the install step
    wrote it to the install log; it is read again here), check it, and write
    the copy that runs."""
    step = {"tool": "docker", "args": ["compose", "-f", str(plan.get("compose_file")),
                                       "config", "--no-env-resolution", "--format",
                                       "json"]}
    argv = _argv(iid, step, plan)
    got = _run(argv, cwd=app_dir(iid), env=_base_env(iid, plan), stdin=subprocess.DEVNULL,
               capture_output=True, timeout=120, check=False, **_popen_kwargs())
    if got.returncode != 0:
        raise HostError("Docker could not read its compose file: " + _last_line(got), "step")
    try:
        config = json.loads(got.stdout.decode("utf-8", "replace")
                            if isinstance(got.stdout, bytes) else got.stdout)
    except ValueError:
        raise HostError("Docker could not read its compose file.", "step") from None
    problems = compose_problems(config, app_dir(iid))
    if problems:
        raise HostError("BuildBot does not run it this way: " + "; ".join(problems) + ".",
                        "unsafe")
    ports = limit_ports(config, remap=True)
    config.pop("name", None)
    fsutil.atomic_write_json(os.path.join(app_dir(iid), "buildbot-compose.json"), config)
    plan["compose_ports"] = ports[:8]


# ---------------------------------------------------------------------------
# processes: start, watch, stop
# ---------------------------------------------------------------------------

def _free(port: int) -> bool:
    """Nothing listens on *port* now. A connection that just closed (TIME_WAIT
    on Linux and macOS) is not a program using it: right after Stop, Restart
    must not read "another program is using port"."""
    if not 1024 <= int(port) <= 65535:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if not _IS_WINDOWS:
            # Lets TIME_WAIT go; Linux still refuses while anything listens.
            # Never on Windows, where it would let this bind share a live port.
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((LOOPBACK, int(port)))
        except OSError:
            return False
    if not _IS_WINDOWS:
        # macOS lets that bind share a port a program holds on 0.0.0.0: ask it.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as c:
            c.settimeout(1.0)
            try:
                if c.connect_ex((LOOPBACK, int(port))) == 0:
                    return False
            except OSError:
                pass
    return True


def free_port() -> int:
    """A port nothing listens on now (the system picks it)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((LOOPBACK, 0))
        return int(s.getsockname()[1])


class _FILETIME(ctypes.Structure):
    _fields_ = [("lo", ctypes.c_uint32), ("hi", ctypes.c_uint32)]


def _proc_identity(pid: int) -> Optional[str]:
    """When the process *pid* started, as a string, or None when it is gone."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    if _IS_WINDOWS:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        handle = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            code = ctypes.c_ulong()
            if not k32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)) \
                    or code.value != 259:                # STILL_ACTIVE
                return None
            c, e, kt, ut = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
            if not k32.GetProcessTimes(ctypes.c_void_p(handle), ctypes.byref(c),
                                       ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut)):
                return None
            return str((c.hi << 32) | c.lo)
        finally:
            k32.CloseHandle(ctypes.c_void_p(handle))
    if _IS_MAC:
        info = _mac_info(pid)
        return None if info is None else info["start"]
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            text = fh.read().decode("utf-8", "replace")
        fields = text.rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        return fields[19]
    except (OSError, IndexError):
        return None


# ---------------------------------------------------------------------------
# macOS: the process table, read with libproc (no /proc there)
# ---------------------------------------------------------------------------

_PROC_PIDTBSDINFO = 3
_PROC_PIDVNODEPATHINFO = 9
_SZOMB = 5
_MAXPATHLEN = 1024
_VNODE_INFO_SIZE = 152          # struct vnode_info (vinfo_stat 136 + type, pad, fsid)


class _ProcBsdInfo(ctypes.Structure):
    """struct proc_bsdinfo (sys/proc_info.h)."""
    _fields_ = [("flags", ctypes.c_uint32), ("status", ctypes.c_uint32),
                ("xstatus", ctypes.c_uint32), ("pid", ctypes.c_uint32),
                ("ppid", ctypes.c_uint32), ("uid", ctypes.c_uint32),
                ("gid", ctypes.c_uint32), ("ruid", ctypes.c_uint32),
                ("rgid", ctypes.c_uint32), ("svuid", ctypes.c_uint32),
                ("svgid", ctypes.c_uint32), ("rfu_1", ctypes.c_uint32),
                ("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32),
                ("nfiles", ctypes.c_uint32), ("pgid", ctypes.c_uint32),
                ("pjobc", ctypes.c_uint32), ("e_tdev", ctypes.c_uint32),
                ("e_tpgid", ctypes.c_uint32), ("nice", ctypes.c_int32),
                ("start_tvsec", ctypes.c_uint64), ("start_tvusec", ctypes.c_uint64)]


_LIBPROC: Any = None


def _libproc() -> Any:
    """libproc with its argument types set, or None when it cannot be loaded."""
    global _LIBPROC
    if _LIBPROC is None:
        lib = None
        for name in (None, "/usr/lib/libproc.dylib", "/usr/lib/libSystem.dylib"):
            try:
                cand = ctypes.CDLL(name, use_errno=True)
                cand.proc_pidinfo  # noqa: B018 - present?
                lib = cand
                break
            except (OSError, AttributeError):
                continue
        if lib is None:
            _LIBPROC = False
            return None
        vp = ctypes.c_void_p
        lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, vp,
                                     ctypes.c_int]
        lib.proc_pidinfo.restype = ctypes.c_int
        lib.proc_listallpids.argtypes = [vp, ctypes.c_int]
        lib.proc_listallpids.restype = ctypes.c_int
        lib.proc_pidpath.argtypes = [ctypes.c_int, vp, ctypes.c_uint32]
        lib.proc_pidpath.restype = ctypes.c_int
        _LIBPROC = lib
    return _LIBPROC or None


def _mac_info(pid: int) -> Optional[dict]:
    """``{"ppid", "pgid", "uid", "start"}`` of a live process, or None when it
    is gone (or a zombie, or cannot be read)."""
    lib = _libproc()
    if lib is None or pid <= 0:
        return None
    info = _ProcBsdInfo()
    size = ctypes.sizeof(info)
    if lib.proc_pidinfo(int(pid), _PROC_PIDTBSDINFO, 0, ctypes.byref(info), size) != size:
        return None
    if info.status == _SZOMB:
        return None
    return {"ppid": int(info.ppid), "pgid": int(info.pgid), "uid": int(info.uid),
            "start": f"{info.start_tvsec}.{info.start_tvusec:06d}"}


def _mac_pids() -> Optional[list[int]]:
    """Every process id now, or None when they cannot be listed."""
    lib = _libproc()
    if lib is None:
        return None
    count = lib.proc_listallpids(None, 0)
    if count <= 0:
        return None
    for _ in range(3):
        buf = (ctypes.c_int * (count + 256))()
        got = lib.proc_listallpids(buf, ctypes.sizeof(buf))
        if got <= 0:
            return None
        if got < count + 256:
            return [int(buf[i]) for i in range(got) if buf[i] > 0]
        count = got * 2
    return None


def _mac_table() -> Optional[dict[int, dict]]:
    """``{pid: _mac_info}`` for every live process, or None."""
    pids = _mac_pids()
    if pids is None:
        return None
    out: dict[int, dict] = {}
    for pid in pids:
        info = _mac_info(pid)
        if info is not None:
            out[pid] = info
    return out


def _mac_paths(pid: int) -> tuple[str, str]:
    """``(working folder, program file)`` of *pid*, "" where unknown."""
    lib = _libproc()
    if lib is None:
        return "", ""
    cwd = ""
    size = 2 * (_VNODE_INFO_SIZE + _MAXPATHLEN)          # struct proc_vnodepathinfo
    buf = ctypes.create_string_buffer(size)
    if lib.proc_pidinfo(int(pid), _PROC_PIDVNODEPATHINFO, 0, buf, size) == size:
        cwd = buf.raw[_VNODE_INFO_SIZE:_VNODE_INFO_SIZE + _MAXPATHLEN].split(b"\0", 1)[0] \
            .decode("utf-8", "replace")
    exe = ""
    pbuf = ctypes.create_string_buffer(4096)
    if lib.proc_pidpath(int(pid), pbuf, 4096) > 0:
        exe = pbuf.value.decode("utf-8", "replace")
    return cwd, exe


def _inside(path: str, folder: str) -> bool:
    if not path or not folder:
        return False
    try:
        path, folder = os.path.realpath(path), os.path.realpath(folder)
        return os.path.commonpath([path, folder]) == folder
    except ValueError:
        return False


def _folder_members(folder: str) -> set[int]:
    """macOS: this user's processes working in, or run from, *folder* (an
    app's own folder under BuildBot's apps folder, never anything wider).

    macOS has no child subreaper, so a process that starts a new session and
    whose parent exits is handed to launchd and is no longer under the app.
    It still works in the app's folder (or runs a program from it): that is
    how Stop finds it. Never BuildBot itself or what started BuildBot."""
    if not _IS_MAC or not _inside(folder, root()) \
            or os.path.realpath(folder) == os.path.realpath(root()):
        return set()
    table = _mac_table() or {}
    me, uid = os.getpid(), os.getuid()
    mine = {me}
    p = me
    while p in table and table[p]["ppid"] not in mine and table[p]["ppid"] > 1:
        p = table[p]["ppid"]
        mine.add(p)
    found: set[int] = set()
    for pid, info in table.items():
        if pid in mine or pid <= 1 or info["uid"] != uid:
            continue
        cwd, exe = _mac_paths(pid)
        if _inside(cwd, folder) or _inside(exe, folder):
            found.add(pid)
    return found


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32),
                ("th32ProcessID", ctypes.c_uint32), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", ctypes.c_uint32), ("cntThreads", ctypes.c_uint32),
                ("th32ParentProcessID", ctypes.c_uint32), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", ctypes.c_uint32), ("szExeFile", ctypes.c_wchar * 260)]


def _parents() -> dict[int, int]:
    """``{pid: parent pid}`` for every process."""
    out: dict[int, int] = {}
    if _IS_WINDOWS:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        snap = k32.CreateToolhelp32Snapshot(0x2, 0)
        if not snap or snap == ctypes.c_void_p(-1).value:
            return out
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
            ok = k32.Process32FirstW(ctypes.c_void_p(snap), ctypes.byref(entry))
            while ok:
                out[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                ok = k32.Process32NextW(ctypes.c_void_p(snap), ctypes.byref(entry))
        finally:
            k32.CloseHandle(ctypes.c_void_p(snap))
        return out
    if _IS_MAC:
        return {pid: info["ppid"] for pid, info in (_mac_table() or {}).items()}
    try:
        names = os.listdir("/proc")
    except OSError:
        return out
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as fh:
                text = fh.read().decode("utf-8", "replace")
            out[int(name)] = int(text.rsplit(")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            continue
    return out


def tree(pid: int) -> set[int]:
    """*pid* and every process started from it."""
    parents = _parents()
    found = {pid}
    grew = True
    while grew:
        grew = False
        for child, parent in parents.items():
            if parent in found and child not in found and child != parent:
                found.add(child)
                grew = True
    return found


# ---------------------------------------------------------------------------
# keeping every process it starts together
#
# Windows: each start (and each install step) runs in its own Job Object,
# made before the program runs any code (it is created suspended, put in the
# job, then let go). The job kills everything in it when its last handle
# closes (Stop, Restart, or this bot ending in any way) and allows no
# breakaway, so a detached or grandchild process stays in it.
#
# Linux: each start runs under a small keeper process (its own new session)
# that is a "child subreaper" (prctl PR_SET_CHILD_SUBREAPER): a process that
# starts a new session and whose parent exits is handed to the keeper, not to
# init, so it is still found under the keeper and still ended by Stop. The
# keeper ignores SIGTERM and is ended last, after everything under it.
#
# macOS: a new session (its own process group and session). Its members are
# that group and session and everything started from them (read with
# libproc), every member the watcher has seen, and any process of this user
# working in or run from the app's own folder (_folder_members). Stop
# freezes and ends all of them. Known gap: macOS has no child subreaper, so a
# process that starts a new session, leaves the app's folder and whose parent
# exits before the watcher sees it is handed to launchd and can outlive Stop
# (the same class as the Windows WMI-launched process).
# ---------------------------------------------------------------------------

_CREATE_SUSPENDED = 0x00000004
_JOB_KILL_ON_CLOSE = 0x00002000
_JOB_EXTENDED_LIMITS = 9
_JOB_PROCESS_IDS = 3
_ERROR_MORE_DATA = 234

#: Live jobs started in this run of the bot: id -> job handle (Windows).
_JOBS: dict[str, int] = {}
_JOBS_GUARD = threading.Lock()
#: Members seen while it ran (elsewhere): id -> {pid: identity}.
_SEEN: dict[str, dict[int, str]] = {}


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]


class _JOB_BASIC_LIMITS(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32)]


class _JOB_EXTENDED_LIMITS_INFO(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _JOB_BASIC_LIMITS),
                ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


_K32: Any = None


def _k32() -> Any:
    """kernel32 and ntdll with their argument types set (handles stay 64 bit)."""
    global _K32
    if _K32 is None:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        nt = ctypes.WinDLL("ntdll")
        vp, u32 = ctypes.c_void_p, ctypes.c_uint32
        k32.CreateJobObjectW.argtypes, k32.CreateJobObjectW.restype = [vp, ctypes.c_wchar_p], vp
        k32.SetInformationJobObject.argtypes = [vp, ctypes.c_int, vp, u32]
        k32.AssignProcessToJobObject.argtypes = [vp, vp]
        k32.TerminateJobObject.argtypes = [vp, ctypes.c_uint]
        k32.QueryInformationJobObject.argtypes = [vp, ctypes.c_int, vp, u32, vp]
        k32.TerminateProcess.argtypes = [vp, ctypes.c_uint]
        k32.CloseHandle.argtypes = [vp]
        nt.NtResumeProcess.argtypes, nt.NtResumeProcess.restype = [vp], ctypes.c_long
        _K32 = (k32, nt)
    return _K32


def _job_new() -> int:
    """A new job that kills all of its processes when it closes and lets
    none of them leave it."""
    k32, _nt = _k32()
    job = k32.CreateJobObjectW(None, None)
    if not job:
        raise HostError("BuildBot could not keep its processes together, so it did not "
                        "start it.", "contain")
    info = _JOB_EXTENDED_LIMITS_INFO()
    info.BasicLimitInformation.LimitFlags = _JOB_KILL_ON_CLOSE   # no breakaway flag
    if not k32.SetInformationJobObject(job, _JOB_EXTENDED_LIMITS, ctypes.byref(info),
                                       ctypes.sizeof(info)):
        k32.CloseHandle(job)
        raise HostError("BuildBot could not keep its processes together, so it did not "
                        "start it.", "contain")
    return int(job)


def _job_pids(job: int) -> Optional[set[int]]:
    """The processes in *job* now, or None when the job cannot be asked."""
    k32, _nt = _k32()
    n = 256
    while n <= 65536:
        class _List(ctypes.Structure):
            _fields_ = [("assigned", ctypes.c_uint32), ("listed", ctypes.c_uint32),
                        ("ids", ctypes.c_size_t * n)]
        buf = _List()
        if k32.QueryInformationJobObject(job, _JOB_PROCESS_IDS, ctypes.byref(buf),
                                         ctypes.sizeof(buf), None):
            return {int(buf.ids[i]) for i in range(min(buf.listed, n))}
        if ctypes.get_last_error() != _ERROR_MORE_DATA:
            return None
        n *= 4
    return None


_IS_LINUX = sys.platform.startswith("linux")

#: The keeper (Linux). argv: ``<status fd> <program> <args...>``. It becomes
#: a child subreaper (or exits 125 and runs nothing), starts the program with
#: default signal handling, reaps everything handed to it, writes the
#: program's exit code to the status fd, and exits once nothing is left.
_KEEPER = r'''
import os, signal, sys
def report(fd, code):
    if fd >= 0:
        try:
            os.write(fd, b"%d\n" % code)
            os.close(fd)
        except OSError:
            pass
    return -1
def main():
    fd, argv = int(sys.argv[1]), sys.argv[2:]
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:        # PR_SET_CHILD_SUBREAPER
        os.write(2, b"BuildBot could not keep its processes together.\n")
        report(fd, 125)
        return 125
    names = ("SIGTERM", "SIGINT", "SIGHUP", "SIGPIPE", "SIGXFSZ")
    reset = [getattr(signal, n) for n in names if hasattr(signal, n)]
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, signal.SIG_IGN)
    if fd >= 0:
        os.set_inheritable(fd, False)
    try:
        child = os.posix_spawnp(argv[0], argv, dict(os.environ), setsigdef=reset)
    except OSError as exc:
        os.write(2, ("It could not start: %s\n" % (exc.strerror or "error")).encode())
        report(fd, 127)
        return 127
    code = 0
    while True:
        try:
            pid, status = os.waitpid(-1, 0)
        except ChildProcessError:
            return code
        except InterruptedError:
            continue
        if pid == child:
            code = os.waitstatus_to_exitcode(status)
            code = 128 - code if code < 0 else code
            fd = report(fd, code)
sys.exit(main())
'''


def _spawn(argv: list[str], *, cwd: str, env: dict, stdout: Any) -> tuple[Any, int]:
    """Start *argv* contained: ``(Popen, box)``; *box* is the job handle on
    Windows, the keeper's session on Linux, the process group on macOS.
    Raises HostError (nothing runs) when it cannot be contained.

    On Linux the Popen is the keeper; ``proc.status_fd`` reads the
    program's own exit code once it exits (see :func:`_run_contained`)."""
    if _IS_LINUX:
        rfd, wfd = os.pipe()
        try:
            proc = _popen([sys.executable, "-I", "-c", _KEEPER, str(wfd), *argv], cwd=cwd,
                          env=env, stdin=subprocess.DEVNULL, stdout=stdout,
                          stderr=subprocess.STDOUT, start_new_session=True, pass_fds=(wfd,),
                          **procflags.hidden())
        except BaseException:
            os.close(rfd)
            raise
        finally:
            os.close(wfd)
        proc.status_fd = rfd
        return proc, int(proc.pid)
    if not _IS_WINDOWS:
        proc = _popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=stdout,
                      stderr=subprocess.STDOUT, start_new_session=True, **procflags.hidden())
        return proc, int(proc.pid)
    job = _job_new()
    k32, nt = _k32()
    kw = _popen_kwargs()
    kw["creationflags"] |= _CREATE_SUSPENDED
    try:
        proc = _popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=stdout,
                      stderr=subprocess.STDOUT, **kw)
    except BaseException:
        k32.CloseHandle(job)
        raise
    handle = int(proc._handle)  # noqa: SLF001 - the process handle Popen holds
    if not k32.AssignProcessToJobObject(job, handle) or nt.NtResumeProcess(handle) != 0:
        k32.TerminateProcess(handle, 1)
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            pass
        k32.CloseHandle(job)
        raise HostError("BuildBot could not keep its processes together, so it did not "
                        "start it.", "contain")
    return proc, job


def _box_members(box: int) -> Optional[set[int]]:
    """Every live process in *box* (see :func:`_spawn`), or None when this
    computer cannot be asked."""
    if _IS_WINDOWS:
        return _job_pids(box)
    if _IS_MAC:
        table = _mac_table()
        if table is None:
            return None
        found = set()
        for pid, info in table.items():
            if info["pgid"] == box or pid == box:
                found.add(pid)
                continue
            try:
                if os.getsid(pid) == box:            # its session, another group
                    found.add(pid)
            except OSError:
                pass
        return _with_children(found, {p: i["ppid"] for p, i in table.items()})
    if not sys.platform.startswith("linux"):
        return None
    found = set()
    parents: dict[int, int] = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as fh:
                fields = fh.read().decode("utf-8", "replace").rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        if fields[0] == "Z":
            continue
        parents[int(name)] = int(fields[1])
        if int(fields[2]) == box or int(fields[3]) == box:     # its group or its session
            found.add(int(name))
    return _with_children(found, parents)


def _with_children(found: set[int], parents: dict[int, int]) -> set[int]:
    """*found* and what they started, even in a new session."""
    found = set(found)
    grew = True
    while grew:
        grew = False
        for child, parent in parents.items():
            if parent in found and child not in found:
                found.add(child)
                grew = True
    return found


def _box_kill(box: int, extra: Optional[set[int]] = None) -> None:
    """End every process in *box* (and *extra*, members seen before)."""
    if _IS_WINDOWS:
        k32, _nt = _k32()
        k32.TerminateJobObject(box, 1)
        for _ in range(50):
            if not _job_pids(box):
                return
            time.sleep(0.1)
        return
    extra = set(extra or ())

    def _send(pids: set[int], sig: int) -> None:
        for pid in pids:
            try:
                os.kill(pid, sig)
            except (OSError, ProcessLookupError):
                pass

    def _group(sig: int) -> bool:
        """Signal the group; False when no process is left in it."""
        try:
            os.killpg(box, sig)
            return True
        except (OSError, ProcessLookupError):
            return False

    keeper = {box} if _IS_LINUX else set()   # macOS: the leader is the app itself

    def _others() -> Optional[set[int]]:
        """Everything still alive under it except the keeper (Linux)."""
        found = _box_members(box)
        if found is None:
            return None
        return (found | {p for p in extra if _proc_identity(p) is not None}) - keeper

    others = _others()
    if others is None:                     # cannot be asked: the group is all it sees
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if not _group(sig):
                return
            for _ in range(25):
                time.sleep(0.2)
                if not _group(0):
                    return
        return
    # 1. Ask nicely (the keeper ignores this), and wait a little.
    _group(signal.SIGTERM)
    _send(others, signal.SIGTERM)
    for _ in range(25):
        others = _others() or set()
        if not others:
            break
        time.sleep(0.2)
    # 2. Freeze whatever is left (so nothing starts anything new), until no
    #    new process appears, then end all of them.
    for _ in range(10):
        stopped: set[int] = set()
        for _ in range(20):
            others = _others() or set()
            new = others - stopped
            if not new:
                break
            _send(new, signal.SIGSTOP)
            stopped |= new
        if not stopped:
            break
        _send(stopped, signal.SIGKILL)
        time.sleep(0.1)
    # 3. The keeper last, once nothing is left under it to be handed to init.
    _group(signal.SIGKILL)
    for _ in range(25):
        if not (_box_members(box) or set()) - {box} and _proc_identity(box) is None:
            return
        time.sleep(0.2)


def _box_close(box: int) -> None:
    """Let go of *box* (Windows: closing the job ends anything left in it)."""
    if _IS_WINDOWS:
        k32, _nt = _k32()
        k32.CloseHandle(box)


def _run_contained(argv: list[str], *, cwd: str, env: dict, stdout: Any,
                   timeout: float) -> Optional[int]:
    """Run one install or build step contained, wait for it, then end
    anything it left behind. Its exit code, or None when it ran too long."""
    proc, box = _spawn(argv, cwd=cwd, env=env, stdout=stdout)
    status_fd = getattr(proc, "status_fd", None)
    try:
        if status_fd is None:
            return int(proc.wait(timeout=timeout))
        # Linux: the keeper stays while anything it was handed lives, so
        # the step is done when the program itself exits (the keeper says).
        import select
        ready, _w, _x = select.select([status_fd], [], [], max(0.0, timeout))
        if not ready:
            return None
        text = os.read(status_fd, 64).decode("ascii", "replace").strip()
        return int(text) if text.lstrip("-").isdigit() else 1
    except subprocess.TimeoutExpired:
        return None
    finally:
        # macOS: and anything of this step's still working in its folder.
        _box_kill(box, _folder_members(cwd) if _IS_MAC else None)
        _box_close(box)
        if status_fd is not None:
            os.close(status_fd)
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            pass


def _members(iid: str, st: dict, *, deep: bool = False) -> Optional[set[int]]:
    """Every live process of a started app, or None when it cannot be asked.

    macOS: with *deep* (the watcher), or when nothing else is found, also
    what works in or runs from the app's own folder (:func:`_folder_members`),
    so a process handed to launchd is still watched, and an app is never
    "stopped by itself" while such a process lives."""
    with _JOBS_GUARD:
        job = _JOBS.get(iid)
        if job is not None:
            return _box_members(job)
    group = st.get("group")
    if not _IS_WINDOWS and isinstance(group, int) and group > 0:
        found = _box_members(group)
        if found is None:
            return None
        seen = _SEEN.get(iid) or {}
        found |= {p for p, ident in seen.items() if _proc_identity(p) == ident}
        if _IS_MAC and (deep or not found):
            found |= _folder_members(home(iid))
        return found
    # Not contained by this run of the bot (started before it): its tree.
    pid = st.get("pid")
    if isinstance(pid, int) and _proc_identity(pid) is not None \
            and _proc_identity(pid) == st.get("identity"):
        return tree(pid)
    return set()


def _end_pids(pids: set[int]) -> None:
    """End these processes (POSIX): ask, wait a little, then freeze and kill."""
    live = {p for p in pids if _proc_identity(p) is not None}
    for sig in (signal.SIGTERM, signal.SIGSTOP, signal.SIGKILL):
        for pid in live:
            try:
                os.kill(pid, sig)
            except (OSError, ProcessLookupError):
                pass
        for _ in range(25 if sig == signal.SIGTERM else 1):
            live = {p for p in live if _proc_identity(p) is not None}
            if not live:
                return
            time.sleep(0.2)


def _end_all(iid: str, st: dict) -> None:
    """End every process of a started app and let go of its job."""
    with _JOBS_GUARD:
        job = _JOBS.pop(iid, None)
    # Only members still the same process (a pid may since belong to another).
    seen = {p for p, ident in (_SEEN.pop(iid, None) or {}).items()
            if _proc_identity(p) == ident}
    if job is not None:
        _box_kill(job)
        _box_close(job)
        return
    if _IS_MAC:
        seen |= _folder_members(home(iid))
    group = st.get("group")
    if not _IS_WINDOWS and isinstance(group, int) and group > 0:
        _box_kill(group, seen)
        return
    if seen:
        _end_pids(seen)
    pid = st.get("pid")
    if isinstance(pid, int) and _proc_identity(pid) is not None \
            and _proc_identity(pid) == st.get("identity"):
        _kill_tree(pid)


def _end_all_at_exit() -> None:
    """The bot is ending: end every app it started (she left them on, so
    they start again with the bot)."""
    for iid in list(_JOBS) + [i for i in list(_PROCS) if i not in _JOBS]:
        try:
            _end_all(iid, read_state(iid))
            _write_running(iid, state="stopped", pid=None, identity=None, group=None,
                           why="")
        except Exception:  # noqa: BLE001 - the bot is ending anyway
            pass


_EXIT_HOOKED = False


def _hook_exit() -> None:
    global _EXIT_HOOKED
    if not _EXIT_HOOKED:
        import atexit
        atexit.register(_end_all_at_exit)
        _EXIT_HOOKED = True


def _listeners_windows() -> list[tuple[int, str, int]]:
    """``(pid, address, port)`` for every listening TCP socket."""
    out: list[tuple[int, str, int]] = []
    iphlp = ctypes.WinDLL("iphlpapi")
    for family, row_size in ((2, 24), (23, 56)):
        size = ctypes.c_ulong(0)
        iphlp.GetExtendedTcpTable(None, ctypes.byref(size), False, family, 3, 0)
        if not size.value:
            continue
        buf = ctypes.create_string_buffer(size.value + 1024)
        size = ctypes.c_ulong(len(buf))
        if iphlp.GetExtendedTcpTable(buf, ctypes.byref(size), False, family, 3, 0) != 0:
            continue
        raw = buf.raw
        count = struct.unpack_from("<I", raw, 0)[0]
        for i in range(count):
            off = 4 + i * row_size
            if family == 2:
                _state, addr, port, _ra, _rp, pid = struct.unpack_from("<IIIIII", raw, off)
                address = socket.inet_ntoa(struct.pack("<I", addr))
            else:
                address = socket.inet_ntop(socket.AF_INET6, raw[off:off + 16])
                port = struct.unpack_from("<I", raw, off + 20)[0]
                pid = struct.unpack_from("<I", raw, off + 52)[0]
            out.append((int(pid), address, socket.ntohs(port & 0xFFFF)))
    return out


def _listeners_linux(pids: set[int]) -> list[tuple[int, str, int]]:
    inodes: dict[str, int] = {}
    for pid in pids:
        try:
            for fd in os.listdir(f"/proc/{pid}/fd"):
                try:
                    link = os.readlink(f"/proc/{pid}/fd/{fd}")
                except OSError:
                    continue
                if link.startswith("socket:["):
                    inodes[link[8:-1]] = pid
        except OSError:
            continue
    out: list[tuple[int, str, int]] = []
    for name, v6 in (("tcp", False), ("tcp6", True)):
        for line in _read_text(f"/proc/net/{name}", 4 * 1024 * 1024).splitlines()[1:]:
            parts = line.split()
            if len(parts) < 10 or parts[3] != "0A" or parts[9] not in inodes:
                continue
            hexaddr, hexport = parts[1].split(":")
            raw = bytes.fromhex(hexaddr)
            if v6:
                raw = b"".join(raw[i:i + 4][::-1] for i in range(0, 16, 4))
                address = socket.inet_ntop(socket.AF_INET6, raw)
            else:
                address = socket.inet_ntoa(raw[::-1])
            out.append((inodes[parts[9]], address, int(hexport, 16)))
    return out


def listeners(pids: set[int]) -> Optional[list[tuple[str, int]]]:
    """``[(address, port)]`` the processes *pids* listen on, or None when this
    computer cannot be asked (then a server is not run: fail closed)."""
    if _IS_WINDOWS:
        rows = _listeners_windows()
        return [(a, p) for pid, a, p in rows if pid in pids]
    if sys.platform.startswith("linux"):
        return [(a, p) for _pid, a, p in _listeners_linux(pids)]
    if _IS_MAC:
        return _listeners_mac(pids)
    return None


_LSOF = "/usr/sbin/lsof"


def _listeners_mac(pids: set[int]) -> Optional[list[tuple[str, int]]]:
    """macOS: where *pids* listen, from the system's own lsof (by full path),
    or None when it cannot be asked (then a server is not run)."""
    want = sorted(p for p in pids if isinstance(p, int) and p > 0)
    if not want:
        return []
    if not os.path.isfile(_LSOF):
        return None
    try:
        res = _run([_LSOF, "-nP", "-w", "-iTCP", "-sTCP:LISTEN", "-a",
                    "-p", ",".join(str(p) for p in want), "-Fn"],
                   stdin=subprocess.DEVNULL, capture_output=True, timeout=20, check=False,
                   **procflags.hidden())
    except (OSError, subprocess.SubprocessError):
        return None
    text = (res.stdout or b"").decode("utf-8", "replace")
    if res.returncode not in (0, 1):
        return None
    # 1 also means "one of those processes was not found" (it just ended),
    # with the others' lines still printed: they are read as they are.
    out: list[tuple[str, int]] = []
    for line in text.splitlines():
        if not line.startswith("n") or ":" not in line:
            continue
        address, _, port = line[1:].rpartition(":")
        if not port.isdigit():
            continue
        address = address.strip("[]")
        if address in ("*", ""):
            address = "0.0.0.0"         # every address: beyond this computer
        out.append((address, int(port)))
    return out


#: Shown on its page when an app listens on every address, not only 127.0.0.1.
NETWORK_NOTE = ("It also listens on your home network, so other devices at home can "
                "open it too.")
_ALL_ADDRESSES = frozenset({"0.0.0.0", "::", "[::]", "*"})


def _loopback(address: str) -> bool:
    return address.startswith("127.") or address in ("::1", "::ffff:127.0.0.1")


def _url(port: Any) -> str:
    return f"http://{LOOPBACK}:{int(port)}/" if port else ""


def status(iid: str) -> dict:
    """``{"state", "url", "port", "wanted_on", "how", "why", "pid"?}``.

    state: ``stopped``, ``starting``, ``running``, ``set_up`` (a tool or a
    library: nothing to run), ``failed``."""
    st = read_state(_check_id(iid))
    plan = st.get("plan") or {}
    how = str(plan.get("how") or "")
    out = {"state": str(st.get("state") or "stopped"), "url": "", "port": st.get("port"),
           "wanted_on": bool(st.get("wanted_on")), "how": how,
           "how_words": HOW_WORDS.get(how, ""), "why": str(st.get("why") or ""),
           "can_run": how in ("node_server", "python_server", "compose", "assembled"),
           "note": str(st.get("network_note") or "")}
    if not out["can_run"]:
        out["state"] = "set_up"
        return out
    if out["state"] in ("running", "starting") and how != "compose":
        # Running while ANY process it started lives (not only the first).
        if _members(iid, st) == set():
            out["state"] = "stopped"
            out["why"] = out["why"] or "It stopped by itself. Its log says why."
    if out["state"] == "running":
        out["url"] = _url(st.get("port"))
    return out


def _write_running(iid: str, **changes: Any) -> dict:
    st = read_state(iid)
    st.update(changes)
    _save_state(iid, st)
    return st


def start(iid: str, *, by: str = "owner", wait: float = 0.0) -> dict:
    """Start it (a server only), bound to 127.0.0.1. ``{"ok", "line", "status"}``."""
    iid = _check_id(iid)
    with _lock_for(iid):
        st = read_state(iid)
        plan = st.get("plan") or {}
        if not st or plan_sha(plan) != st.get("plan_sha"):
            return {"ok": False, "line": "It is not set up on this computer.",
                    "code": "unknown"}
        if not plan.get("start"):
            return {"ok": False, "line": "It has nothing to start.", "code": "failed"}
        now = status(iid)
        if now["state"] in ("running", "starting"):
            return {"ok": True, "line": "It is already running.", "status": now}
        if listeners({os.getpid()}) is None:
            return {"ok": False, "line": "BuildBot cannot check where programs listen on "
                                         "this computer, so it does not run servers here.",
                    "code": "failed"}
        port = plan.get("port")
        if port and not _free(int(port)):
            return {"ok": False, "line": f"Another program is using port {port}, so it "
                                         f"cannot start. Stop that program first.",
                    "code": "failed"}
        try:
            argv = _argv(iid, plan["start"], plan)
            env = _base_env(iid, plan)
            if plan.get("how") == "compose":
                return _compose_start(iid, plan, argv, env, by=by)
            _check_npm_scripts(iid, plan["start"])
            _check_node_config(iid, plan)
            if plan.get("how") == "assembled":
                _check_assembled(iid, plan)
            env.update(_given_keys(iid, plan, by=by))
            if plan.get("pm"):
                # The pinned pnpm or yarn is already in its folder: nothing
                # more is fetched by corepack once it runs.
                env["COREPACK_ENABLE_NETWORK"] = "0"
            for name in _NEVER_ENV:
                env.pop(name, None)
            with _open_log(_log_path(iid)) as log:
                log.write(f"\n--- started {_now()} ---\n".encode())
                log.flush()
                proc, box = _spawn(argv, cwd=_step_dir(iid, plan["start"]), env=env,
                                   stdout=log)
            if getattr(proc, "status_fd", None) is not None:
                os.close(proc.status_fd)      # a server: the watcher follows it
                proc.status_fd = None
        except HostError as exc:
            return {"ok": False, "line": exc.line, "code": exc.code}
        except OSError as exc:
            logger.warning("host app: start failed: %s", type(exc).__name__)
            return {"ok": False, "line": "It could not start.", "code": "failed"}
        _PROCS[iid] = proc
        if _IS_WINDOWS:
            with _JOBS_GUARD:
                _JOBS[iid] = box
        _SEEN.pop(iid, None)
        _hook_exit()
        _write_running(iid, state="starting", pid=proc.pid,
                       identity=_proc_identity(proc.pid), port=port, wanted_on=True,
                       group=None if _IS_WINDOWS else box, why="", started_at=_now())
        _security_log("start", iid, "start", extra={"port": port}, by=by)
        _watch(iid)
    if wait:
        wait_ready(iid, wait)
    return {"ok": True, "line": "It is starting.", "status": status(iid)}


def _given_keys(iid: str, plan: dict, *, by: str) -> dict[str, str]:
    """The keys this start gives (names in the security log, never values).
    A kept key that cannot be read stops the start (fail closed)."""
    from puffpai.security import keys
    try:
        given = app_keys(iid, plan)
    except keys.KeyUnavailable:
        _security_log("keys", iid, "locked", by=by)
        raise HostError(keys.locked_app_sentence(keys.app_keys_set(iid)), "locked") from None
    if given:
        _security_log("keys", iid, "given", details=" ".join(sorted(given)), by=by)
    return given


def wait_ready(iid: str, seconds: float) -> dict:
    """Wait up to *seconds* for it to listen (or stop). Its status."""
    end = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < end:
        now = status(iid)
        if now["state"] != "starting":
            return now
        time.sleep(0.2)
    return status(iid)


def _watch(iid: str) -> None:
    old = _WATCHERS.pop(iid, None)
    if old is not None:
        old.set()
    stop_event = threading.Event()
    _WATCHERS[iid] = stop_event
    threading.Thread(target=_watch_loop, args=(iid, stop_event), daemon=True,
                     name=f"host-app-{iid}").start()


def _watch_loop(iid: str, stop_event: threading.Event) -> None:
    """Find where every process it started listens; stop all of them the
    moment any one listens beyond 127.0.0.1 (fail closed), and keep checking
    while ANY of them lives. "Stopped by itself" only when none is left."""
    began = time.monotonic()
    while not stop_event.is_set():
        try:
            st = read_state(iid)
            members = _members(iid, st, deep=True)
            if members is None:
                # The reason is written first, so nobody reads "stopped by
                # itself" while BuildBot is ending it.
                if not stop_event.is_set():
                    _write_running(iid, state="stopped", wanted_on=False,
                                   why="BuildBot could not check where it listens, so "
                                       "BuildBot stopped it.")
                    _security_log("stop", iid, "cannot_check", by="buildbot")
                _end_all(iid, st)
                return
            if not _IS_WINDOWS:
                seen_ids = _SEEN.setdefault(iid, {})
                for member in members:
                    ident = _proc_identity(member)
                    if ident:
                        seen_ids[member] = ident
            if not members:
                _end_all(iid, st)          # lets go of its (now empty) job
                if st.get("state") in ("starting", "running") and not stop_event.is_set():
                    _write_running(iid, state="stopped",
                                   why="It stopped by itself. Its log says why.")
                    _security_log("stop", iid, "exited", by="app")
                return
            seen = listeners(members) or []
            wide = sorted({f"{a}:{p}" for a, p in seen if not _loopback(a)})
            if wide and not st.get("network_note"):
                # It was told to use 127.0.0.1 and listens on every address
                # anyway: it keeps running (owner, 2026-10-12: it must work;
                # her home network is trusted), and its page says so plainly.
                st = _write_running(iid, network_note=NETWORK_NOTE)
                _security_log("note", iid, "open_to_network", extra={"where": wide[:4]},
                              by="buildbot")
            # Every-address listening includes this computer's own address.
            local = sorted({p for a, p in seen if _loopback(a) or a in _ALL_ADDRESSES})
            if local and st.get("state") == "starting":
                port = st.get("port") if st.get("port") in local else local[0]
                _write_running(iid, state="running", port=port)
            elif not local and st.get("state") == "starting" \
                    and time.monotonic() - began > READY_SECONDS:
                _write_running(iid, why="It has not opened a page yet. Its log may say why.")
        except Exception as exc:  # noqa: BLE001 - a watcher never takes the bot down
            logger.debug("host app watcher: %s", type(exc).__name__)
        stop_event.wait(0.5 if time.monotonic() - began < READY_SECONDS else WATCH_SECONDS)


def _kill_tree(pid: int) -> None:
    if _IS_WINDOWS:
        taskkill = _host_program("taskkill")
        if taskkill:
            from puffpai.security import host_programs
            _run([taskkill, "/PID", str(int(pid)), "/T", "/F"], stdin=subprocess.DEVNULL,
                 capture_output=True, timeout=30, check=False,
                 **host_programs.run_kwargs(), **_popen_kwargs())
        return
    # Its group only when it leads one that is not ours (procflags.own_group);
    # otherwise the process alone, never this process's own group.
    def _signal(sig: int) -> bool:
        group = procflags.own_group(pid)
        try:
            if group is not None:
                os.killpg(group, sig)
            else:
                os.kill(pid, sig)
        except (OSError, ProcessLookupError):
            return False
        return True

    if not _signal(signal.SIGTERM):
        return
    for _ in range(25):
        if _proc_identity(pid) is None:
            return
        time.sleep(0.2)
    _signal(signal.SIGKILL)


def stop(iid: str, *, by: str = "owner", keep_wanted: bool = False) -> dict:
    """Stop it and every process it started. ``{"ok", "line", "status"}``."""
    iid = _check_id(iid)
    with _lock_for(iid):
        st = read_state(iid)
        if not st:
            return {"ok": False, "line": "It is not set up on this computer.", "code": "unknown"}
        watcher = _WATCHERS.pop(iid, None)
        if watcher is not None:
            watcher.set()
        plan = st.get("plan") or {}
        if plan.get("how") == "compose" and st.get("state") in ("running", "starting"):
            _compose_down(iid, plan)
        _end_all(iid, st)           # every process it started, not only the first
        proc = _PROCS.pop(iid, None)
        if proc is not None:
            try:
                proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                pass
        _write_running(iid, state="stopped", pid=None, identity=None, group=None, why="",
                       wanted_on=bool(st.get("wanted_on")) if keep_wanted else False)
        _security_log("stop", iid, "done", by=by)
    return {"ok": True, "line": "It is stopped.", "status": status(iid)}


def restart(iid: str, *, by: str = "owner", wait: float = 0.0) -> dict:
    plan = read_state(_check_id(iid)).get("plan") or {}
    if plan.get("how") == "compose" and status(iid)["state"] == "running":
        with _lock_for(iid):
            ok = _compose_cmd(iid, plan, ["restart"], timeout=300)
        _security_log("restart", iid, "done" if ok else "failed", by=by)
        if ok:
            return {"ok": True, "line": "It is restarting.", "status": status(iid)}
    stop(iid, by=by)
    return start(iid, by=by, wait=wait)


def _compose_start(iid: str, plan: dict, argv: list[str], env: dict, *, by: str) -> dict:
    with _open_log(_log_path(iid)) as log:
        got = _run(argv, cwd=app_dir(iid), env=env, stdin=subprocess.DEVNULL, stdout=log,
                   stderr=subprocess.STDOUT, timeout=STEP_SECONDS, check=False,
                   **_popen_kwargs())
    if got.returncode != 0:
        why = "Docker could not start it: " + _log_error(_log_path(iid))
        _write_running(iid, state="stopped", why=why)
        return {"ok": False, "line": why, "code": "failed"}
    ports = list(plan.get("compose_ports") or [])
    _write_running(iid, state="running", pid=None, identity=None,
                   port=ports[0] if ports else None, wanted_on=True, why="",
                   started_at=_now())
    _security_log("start", iid, "start", extra={"ports": ports[:4]}, by=by)
    return {"ok": True, "line": "It is running.", "status": status(iid)}


def _log_error(path: str) -> str:
    """The last line of a log that says what went wrong (or its last line)."""
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, os.path.getsize(path) - 16 * 1024))
            lines = [ln.strip() for ln in fh.read().decode("utf-8", "replace").splitlines()
                     if ln.strip()]
    except OSError:
        lines = []
    hits = [ln for ln in lines if re.search(r"(?i)error|denied|not found|failed|no such", ln)]
    return ((hits or lines or ["its log on its page says why"])[-1])[:240]


def _compose_cmd(iid: str, plan: dict, args: list[str], *, timeout: int = 300) -> bool:
    """``docker compose -f buildbot-compose.json -p <project> <args>``; True when it worked."""
    try:
        argv = _argv(iid, {"tool": "docker", "args": [
            "compose", "-f", "buildbot-compose.json", "-p", "{project}", *args]}, plan)
        got = _run(argv, cwd=app_dir(iid), env=_base_env(iid, plan), stdin=subprocess.DEVNULL,
                   capture_output=True, timeout=timeout, check=False, **_popen_kwargs())
        return got.returncode == 0
    except (HostError, OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("host app: compose %s failed: %s", args[:1], type(exc).__name__)
        return False


def _compose_down(iid: str, plan: dict) -> None:
    """Stop: its containers stop and are kept (Start brings them back)."""
    _compose_cmd(iid, plan, ["stop"])


def compose_logs(iid: str, plan: dict, lines: int) -> str:
    """Its containers' own output (``docker compose logs``), or ""."""
    try:
        argv = _argv(iid, {"tool": "docker", "args": [
            "compose", "-f", "buildbot-compose.json", "-p", "{project}", "logs", "--no-color",
            "--tail", str(max(1, min(int(lines), 1000)))]}, plan)
        got = _run(argv, cwd=app_dir(iid), env=_base_env(iid, plan), stdin=subprocess.DEVNULL,
                   capture_output=True, timeout=60, check=False, **_popen_kwargs())
    except (HostError, OSError, subprocess.TimeoutExpired):
        return ""
    out = got.stdout if isinstance(got.stdout, bytes) else str(got.stdout or "").encode()
    return out.decode("utf-8", "replace")


def logs(iid: str, which: str = "run", lines: int = LOG_TAIL_LINES) -> str:
    """The last lines of its run (or install) log, with secrets hidden."""
    if which not in ("run", "install"):
        which = "run"
    path = _log_path(_check_id(iid), which)
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - 256 * 1024))
            text = fh.read().decode("utf-8", "replace")
    except OSError:
        text = ""
    plan = read_state(iid).get("plan") or {}
    if which == "run" and plan.get("how") == "compose" \
            and os.path.isfile(os.path.join(app_dir(iid), "buildbot-compose.json")):
        text = (text + "\n" + compose_logs(iid, plan, lines)).strip("\n")
    if not text:
        return ""
    tail = "\n".join(text.splitlines()[-max(1, min(int(lines), 1000)):])
    try:
        from puffpai import brain_commands
        try:
            # Its own keys, if it printed one (only while the vault is open).
            own = list(app_keys(iid, read_state(iid).get("plan") or {}).values())
        except Exception:  # noqa: BLE001 - locked: the patterns still hide keys
            own = []
        return brain_commands.hide_secrets(tail, own)
    except Exception:  # noqa: BLE001
        return tail


def remove(iid: str, *, by: str = "owner") -> None:
    """Stop it and delete its folder (and its tool server, if it had one)."""
    iid = _check_id(iid)
    if not os.path.isdir(home(iid)):
        return
    try:
        stop(iid, by=by)
    except Exception as exc:  # noqa: BLE001
        logger.warning("host app: stop before removal failed: %s", type(exc).__name__)
    st = read_state(iid)
    plan = st.get("plan") or {}
    if plan.get("how") == "compose" \
            and os.path.isfile(os.path.join(app_dir(iid), "buildbot-compose.json")):
        _compose_cmd(iid, plan, ["down", "-v", "--remove-orphans"])     # its own volumes go too
    server = str(st.get("mcp_server") or "")
    if server:
        try:
            from puffpai import mcp_client
            mcp_client.remove_server(server)
        except Exception:  # noqa: BLE001
            pass
    with _lock_for(iid):
        _rmtree(home(iid))
    try:
        from puffpai.security import keys
        keys.remove_app_key(iid, by=by)       # its keys go with it
    except Exception as exc:  # noqa: BLE001
        logger.warning("host app: its keys were not cleared: %s", type(exc).__name__)
    _security_log("remove", iid, "done", by=by)


def autostart_all() -> list[str]:
    """At the bot's start: start again every server she left on. The ids started."""
    started = []
    for iid in all_ids():
        st = read_state(iid)
        if not st.get("wanted_on"):
            continue
        try:
            now = status(iid)
            if now["state"] in ("running", "starting") and st.get("pid"):
                if iid in _JOBS or (not _IS_WINDOWS and isinstance(st.get("group"), int)):
                    _watch(iid)   # still contained and running: watch it again
                    continue
                # Still running from before, outside any job this bot holds:
                # end it and start it again, contained.
                stop(iid, by="autostart", keep_wanted=True)
            if start(iid, by="autostart").get("ok"):
                started.append(iid)
        except Exception as exc:  # noqa: BLE001 - one app never stops the others
            logger.warning("host app: autostart failed: %s", type(exc).__name__)
    return started


# ---------------------------------------------------------------------------
# the brain's way in: the app's own interface
# ---------------------------------------------------------------------------

def register_mcp(iid: str, name: str, *, by: str = "owner") -> dict:
    """An app that is an MCP tool server: added to the tool server list as
    its own installed command (by full path), so the brain gets its tools."""
    st = read_state(_check_id(iid))
    plan = st.get("plan") or {}
    spec = (plan.get("interfaces") or {}).get("mcp")
    if not isinstance(spec, dict):
        return {"ok": False, "line": ""}
    try:
        if spec.get("tool") == "node":
            rel = _inside_rel((spec.get("args") or [""])[0])
            if not rel:
                return {"ok": False, "line": ""}
            argv = _argv(iid, {"tool": "node", "args": [os.path.join(app_dir(iid), rel)]}, plan)
        else:
            argv = _argv(iid, {"tool": "venv_bin", "bin": spec.get("bin"), "args": []}, plan)
    except HostError as exc:
        return {"ok": False, "line": exc.line}
    from puffpai import mcp_client
    got = mcp_client.add_server(name, argv, label=name, by=by, source=f"integration:{iid}")
    if got.get("ok"):
        _write_running(iid, mcp_server=str((got.get("server") or {}).get("name") or name))
    return {"ok": bool(got.get("ok")), "line": str(got.get("message") or "")}


def cli_commands(iid: str) -> dict[str, str]:
    """Its command line tools: ``{name: full command}`` for ``run_command``."""
    st = read_state(_check_id(iid))
    plan = st.get("plan") or {}
    cli = (plan.get("interfaces") or {}).get("cli") or {}
    out: dict[str, str] = {}
    for name, ref in cli.items() if isinstance(cli, dict) else ():
        if not _BIN_RE.match(str(name)):
            continue
        if plan.get("runtime") == "node":
            rel = _inside_rel(ref)
            node = _host_program("node")
            if rel and node:
                out[name] = f'"{node}" "{os.path.join(app_dir(iid), rel)}"'
        else:
            exe = _venv_bin(iid, str(name))
            if os.path.isfile(exe):
                out[name] = f'"{exe}"'
    return out


def cli_dirs() -> list[str]:
    """Folders holding host apps' command line tools. ``deps.command_paths``
    hands them to ``brain_commands.command_env`` (last on a command's path)
    and to ``deps.has_program``."""
    out = []
    for iid in all_ids():
        plan = read_state(iid).get("plan") or {}
        if plan.get("how") == "python_cli":
            out.append(os.path.dirname(_venv_bin(iid, "python")))
    return out


_HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        raise urllib.error.HTTPError(req.full_url, code, "redirect not followed", headers, fp)


def _local_request(port: int, method: str, path: str, body: Any = None,
                   timeout: float = 30.0) -> tuple[int, bytes]:
    """One request to the app on 127.0.0.1 (never anywhere else, never through
    a proxy). ``(status, body)``."""
    if method not in _HTTP_METHODS or not path.startswith("/") or "\\" in path \
            or "@" in path.split("?", 1)[0]:
        raise HostError("That request cannot be sent.", "bad")
    url = f"http://{LOOPBACK}:{int(port)}{path}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    # No proxy, and never a redirect: a redirect could send BuildBot's own
    # request off this computer, past the Bridge.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())  # lan-egress: 127.0.0.1 only, no proxy, no redirects
    try:
        with opener.open(req, timeout=timeout) as resp:
            return int(resp.status), resp.read(512 * 1024)
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read(64 * 1024) if exc.fp else b""


def operations(iid: str) -> list[dict]:
    """The app's own API operations from its OpenAPI description:
    ``[{"method", "path", "summary"}]`` (at most 40)."""
    st = read_state(_check_id(iid))
    plan = st.get("plan") or {}
    ref = str((plan.get("interfaces") or {}).get("openapi") or "")
    doc: Any = None
    if ref.startswith("runtime:"):
        now = status(iid)
        if now["state"] != "running":
            return []
        try:
            code, raw = _local_request(int(now["port"]), "GET", ref.split(":", 1)[1],
                                       timeout=5)
            doc = json.loads(raw.decode("utf-8", "replace")) if code == 200 else None
        except (HostError, OSError, ValueError):
            doc = None
    elif ref.endswith(".json"):
        doc = _read_json(os.path.join(app_dir(iid), *ref.split("/")))
    return ops_from_doc(doc)


def ops_from_doc(doc: Any) -> list[dict]:
    """``[{"method", "path", "summary"}]`` (at most 40) of an OpenAPI or
    Swagger description, [] for anything else."""
    if not isinstance(doc, dict) or not isinstance(doc.get("paths"), dict):
        return []
    out = []
    for path, ops in doc["paths"].items():
        if not isinstance(path, str) or not path.startswith("/") or not isinstance(ops, dict):
            continue
        for method, op in ops.items():
            if str(method).upper() in _HTTP_METHODS:
                summary = str((op or {}).get("summary") or (op or {}).get("operationId") or "") \
                    if isinstance(op, dict) else ""
                out.append({"method": str(method).upper(), "path": path[:200],
                            "summary": " ".join(summary.split())[:120]})
    return out[:40]


def _path_matches(template: str, path: str) -> bool:
    pattern = "^" + re.sub(r"\\\{[^/]+?\\\}", "[^/?#]+", re.escape(template)) + "$"
    return bool(re.match(pattern, path.split("?", 1)[0]))


def call(iid: str, method: str, path: str, body: Any = None) -> dict:
    """Call one of the app's own API operations on 127.0.0.1."""
    method = str(method or "GET").upper()
    path = str(path or "")
    now = status(_check_id(iid))
    if now["state"] != "running":
        return {"ok": False, "line": "It is not running. Start it on its page first."}
    if not any(op["method"] == method and _path_matches(op["path"], path)
               for op in operations(iid)):
        return {"ok": False, "line": "That is not one of the calls it offers."}
    try:
        code, raw = _local_request(int(now["port"]), method, path, body)
    except (HostError, OSError) as exc:
        return {"ok": False, "line": f"It did not answer ({type(exc).__name__})."}
    text = raw.decode("utf-8", "replace")
    try:
        from puffpai import brain_commands
        text = brain_commands.hide_secrets(text)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": 200 <= code < 300, "status": code, "answer": text[:8000]}


__all__ = ["HOWS", "HOW_WORDS", "HostError", "PLAN_CLASS", "SCHEMA", "SERVER_HOWS", "all_ids", "app_dir", "autostart_all",
           "app_keys", "build_output", "call", "card_lines", "cli_commands", "cli_dirs",
           "compose_problems", "declared_keys", "detect", "free_port", "home",
           "install_missing", "key_rows", "limit_ports", "listeners", "logs", "missing_programs",
           "operations", "ops_from_doc", "plan_class", "plan_sha", "read_state", "register_mcp", "remove", "restart",
           "set_up", "shown_lines", "start", "status", "stop", "tree", "wait_ready"]
