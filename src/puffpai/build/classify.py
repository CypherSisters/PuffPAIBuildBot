"""Classify: every candidate by HOW IT RUNS, never by what it is called.

* a repo with an entry page / web build / its own server and a way to start
  it, or an integration of those modes -> APP
* an MCP tool server (http or stdio) -> TOOL
* a skill with scripts AND declared programs / install specs /
  ``allowed-tools: Bash(x *)`` -> TOOL (a command line for x)
* a skill naming an MCP address or an HTTP OpenAPI endpoint -> TOOL; naming
  another remote API host -> DATA (the host is its source). Never a shell
  command.
* a skill that is text only -> GUIDE
* a command line tool repo, app calls (OpenAPI) without a screen -> TOOL
* a feed, a website row, an API, a stream, a library, a ready-made list, a
  picture set -> DATA
* a book, an offline library (ZIM) -> TEXT

A candidate is a plain dict (see :mod:`puffpai.build.find`): ``ref``,
``name``, ``type``, ``what``; skills may carry ``skill_md`` and ``files``;
integrations ``mode``; tool servers ``mcp``. Goal-agnostic.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os
import re
from typing import Any, Optional

from puffpai.build.schema import APP, DATA, GUIDE, TEXT, TOOL

APP_TYPES = frozenset({"browser_app", "server_app", "app", "web_app"})
APP_MODES = frozenset({"static_web", "web_build", "own_server", "server_app"})
TOOL_TYPES = frozenset({"mcp", "tool_server", "cli", "cli_tool"})
DATA_TYPES = frozenset({"feed", "live", "live_data", "seed", "data", "data_template", "api",
                        "stream", "library", "picture_set", "pictures", "assembled"})
TEXT_TYPES = frozenset({"book", "zim", "textbook"})
DATA_MODES = frozenset({"data_template", "picture_set", "library", "box"})
TOOL_MODES = frozenset({"cli_tool"})

#: Hosts a skill names that are where it came from, not what it reaches.
HOME_HOSTS = frozenset({"github.com", "raw.githubusercontent.com", "clawhub.ai", "skills.sh",
                        "www.npmjs.com", "npmjs.com", "pypi.org", "docs.github.com",
                        "gist.github.com", "example.com", "localhost", "127.0.0.1",
                        "img.shields.io", "opensource.org", "creativecommons.org"})
_URL_RE = re.compile(r"https?://([a-z0-9][a-z0-9.-]*\.[a-z]{2,})(/[^\s)\"'<>`]*)?", re.I)
_MCP_PATH_RE = re.compile(r"/(mcp|sse)(/|$)", re.I)


_API_PATH_RE = re.compile(r"(?i)(/api\b|/v\d+\b|\.json\b|\?|/graphql\b|/rest\b|/feed\b|"
                          r"/rss\b|/search\b|/query\b)")


def api_like(host: str, path: str) -> bool:
    """Does this address look like a service a skill asks for data, rather
    than its homepage or its docs (``https://clawic.com``)?"""
    return host.startswith(("api.", "data.", "feeds.")) or bool(_API_PATH_RE.search(path))


def skill_facts(skill_md: str, files: Any = ()) -> dict:
    """What a skill runs and reaches, read (never run) from its text and files:
    ``{"scripts", "programs", "declared", "keys", "hosts", "mcp_url", "openapi"}``."""
    from puffpai import deps
    from puffpai.finding import install
    md = str(skill_md or "")
    files = [str(f) for f in files or []]
    runs = install.what_it_runs(md, files)
    needs = deps.read(text=md)
    declared_progs = [p["name"] for p in needs["programs"] if p.get("from") == "declared"]
    declared_pkgs = [p["name"] for p in needs["packages"] if p.get("from") == "declared"]
    hosts: list[str] = []
    api_hosts: list[str] = []
    mcp_url = ""
    for m in _URL_RE.finditer(md):
        host = m.group(1).lower().rstrip(".")
        if host in HOME_HOSTS or host.endswith(".github.io"):
            continue
        if host not in hosts:
            hosts.append(host)
        if host not in api_hosts and api_like(host, m.group(2) or ""):
            api_hosts.append(host)
        if not mcp_url and _MCP_PATH_RE.search(m.group(2) or ""):
            mcp_url = m.group(0).rstrip(".,;")
    openapi = bool(re.search(r"openapi\s*[:=]?\s*https?://", md, re.I))
    return {"scripts": runs["scripts"], "programs": [p["name"] for p in needs["programs"]],
            "declared": bool(declared_progs or declared_pkgs),
            "packages": [p["name"] for p in needs["packages"]],
            "keys": [k["name"] for k in needs["keys"]], "hosts": hosts[:12],
            "api_hosts": api_hosts[:12], "mcp_url": mcp_url, "openapi": openapi}


def classify(c: dict) -> str:
    """The class of one candidate (one of APP, TOOL, DATA, TEXT, GUIDE)."""
    kind = str(c.get("type") or c.get("kind") or "").lower()
    mode = str(c.get("mode") or "").lower()
    if c.get("builtin") == "routine":
        return TOOL
    if kind in APP_TYPES or mode in APP_MODES:
        return APP
    if c.get("entry_html") or c.get("server"):
        return APP if c.get("start") else DATA
    if kind in TOOL_TYPES or mode in TOOL_MODES or c.get("mcp"):
        return TOOL
    if kind == "skill":
        f = c.get("facts")
        if not isinstance(f, dict):
            f = skill_facts(c.get("skill_md") or "", c.get("files") or [])
            c["facts"] = f
        if key_request(c):
            # Its only action is one web request to its own service: a source
            # of data (make fetches it through the Bridge), never a command.
            return DATA
        if f["scripts"] and f["declared"]:
            return TOOL
        if f["mcp_url"] or f["openapi"]:
            return TOOL
        if f.get("api_hosts", f["hosts"]):
            # A service it asks for data; a homepage or docs link alone is
            # no data source (a text skill naming its author's site).
            return DATA
        return GUIDE
    if c.get("operations") and not c.get("screen"):
        return TOOL
    if kind in DATA_TYPES or mode in DATA_MODES:
        return DATA
    if kind in TEXT_TYPES:
        return TEXT
    return GUIDE


#: Where code projects and packages are found (``find.from_find`` source).
CODE_SITES = frozenset({"github", "codeberg", "gitlab", "npm", "pypi", "huggingface",
                        "awesome-selfhosted"})
#: Words saying a project runs on a phone or a watch, not on this computer.
_PHONE_RE = re.compile(r"(?i)(?<![a-z])(android|ios|iphone|ipad|harmony ?os|arkts|wear ?os|"
                       r"watch ?os|apple watch|smart ?watch|mobile app|apk|flutter app|"
                       r"react native app|xamarin)(?![a-z])")
#: Words saying it lives inside another company's assistant or chat app.
_PLATFORM_RE = re.compile(r"(?i)(?<![a-z])(google actions|actions on google|google assistant|"
                          r"alexa skill|amazon alexa|telegram bot|discord bot|slack app|"
                          r"chrome extension|firefox add-?on|vs ?code extension|"
                          r"obsidian plugin|nextcloud app|wordpress plugin)(?![a-z])")
#: Words saying it runs only on another desktop system.
_OTHER_OS = {"win32": re.compile(r"(?i)\b(macos only|mac only|linux only|for macos|"
                                 r"for linux|gnome extension|kde plasma)\b"),
             "darwin": re.compile(r"(?i)\b(windows only|linux only|for windows|"
                                  r"for linux)\b"),
             "linux": re.compile(r"(?i)\b(windows only|macos only|mac only|for windows|"
                                 r"for macos)\b")}


#: What the app list says a program runs on, and how BuildBot starts that
#: here (``integrate.run_here``: Node.js and Python directly, Docker compose
#: only when Docker is on this computer).
NATIVE_PLATFORMS = frozenset({"nodejs", "python", "javascript"})
DOCKER_PLATFORMS = frozenset({"docker"})
NATIVE_LANGUAGES = frozenset({"python", "javascript", "typescript", "html", "vue", "svelte"})
NATIVE, DOCKER, NO_START, UNKNOWN = "native", "docker", "none", ""


def run_hint(c: dict) -> str:
    """How a code find would start on this computer, from what its list or
    site says (never downloads): ``native`` (Node.js or Python), ``docker``
    (only with Docker), ``none`` (only on things BuildBot cannot start here)
    or ``""`` (not said: the download decides)."""
    plats = {str(p).lower() for p in c.get("platforms") or []} - {"unknown"}
    if plats:
        if plats & NATIVE_PLATFORMS:
            return NATIVE
        if plats & DOCKER_PLATFORMS:
            return DOCKER
        return NO_START
    if str(c.get("language") or "").lower() in NATIVE_LANGUAGES:
        return NATIVE
    return UNKNOWN


def docker_here() -> bool:
    try:
        from puffpai.security import host_programs
        return bool(host_programs.path("docker"))
    except Exception:  # noqa: BLE001
        return False


def needs_docker(c: dict) -> bool:
    """An app that runs only with Docker, on a computer without it: still an
    option, with a "needs Docker" row on the setup list."""
    return run_hint(c) == DOCKER and not docker_here()


def _mcp_sdk() -> bool:
    try:
        from puffpai import mcp_client
        return bool(mcp_client.sdk_available())
    except Exception:  # noqa: BLE001
        return False


def _os_key() -> str:
    import sys
    return "win32" if sys.platform.startswith("win") else (
        "darwin" if sys.platform == "darwin" else "linux")


def not_usable(c: dict) -> str:
    """Why BuildBot cannot use this candidate on THIS computer ("" when it
    can). Read from what the find says about itself; never downloads.

    * an app for a phone or a watch, or only for another desktop system;
    * a code library or package (not something that runs on its own);
    * a command line project or a data repo from a code site (make has no
      way to connect it);
    * a tool server that only starts as a program with no command given."""
    if c.get("builtin") or str(c.get("source") or "") in ("installed", "live card",
                                                           "assembled"):
        return ""
    kind = str(c.get("type") or c.get("kind") or "").lower()
    said = re.findall(r"[A-Za-z]{3,}", str(c.get("what") or ""))
    if len(said) < 4 and not c.get("skill_md"):
        # Only a name (or an emoji): nothing to tell what it does by.
        return "It says too little about what it does."
    source = str(c.get("source") or "").lower()
    text = f"{c.get('name', '')} {c.get('what', '')} {' '.join(c.get('topics') or [])}"
    if kind in APP_TYPES or kind in ("cli", "cli_tool") or source in CODE_SITES:
        # The app list holds programs people run on their own computer: one
        # that also has a phone app ("web + Android") is still one of those.
        if _PHONE_RE.search(text) and not c.get("listed"):
            return "It runs on a phone or a watch, not on this computer."
        if _PLATFORM_RE.search(text):
            return "It is made for another service's assistant or chat app, not this computer."
        other = _OTHER_OS.get(_os_key())
        if other is not None and other.search(text):
            found = other.search(text).group(0).lower()
            if "linux" in found and _os_key() != "linux":
                return ("It is built for Linux. It needs a Linux computer (on Windows, WSL); "
                        "BuildBot does not convert it.")
            return "It runs on another kind of computer, not this one."
        if run_hint(c) == NO_START and not re.match(
                r"^https://github\.com/[\w.-]+/[\w.-]+/?$", str(c.get("url") or "")):
            # (a GitHub project is still tried: Toolsmith asks for the program
            # it needs on the setup list)
            plats = ", ".join(str(p) for p in (c.get("platforms") or [])[:2])
            return f"It runs on {plats}, which BuildBot cannot start on this computer yet."
    mcp = c.get("mcp") if isinstance(c.get("mcp"), dict) else None
    if kind == "mcp" and not (mcp or {}).get("url") \
            and str((mcp or {}).get("command") or "stdio") == "stdio":
        return "It is a tool server BuildBot cannot start yet (it gives no address)."
    # A tool server whose mcp package is missing here still fits: the setup
    # list offers that package as a row her Yes covers (build.setup, MAKE_SDK).
    if source in CODE_SITES and kind not in APP_TYPES and kind not in ("mcp", "skill") \
            and not c.get("integration_id") and not c.get("prepared"):
        if kind == "library":
            return "It is a code library, not something that runs on its own."
        if kind in ("cli", "cli_tool"):
            return "It is a command line program BuildBot cannot connect yet."
        return "It is a code project BuildBot cannot use as a data source."
    return ""


def hosts_of(c: dict) -> list[str]:
    """The outside hosts one candidate reaches (for setup rows)."""
    out = [str(h).lower() for h in c.get("hosts") or [] if h]
    f = c.get("facts") if isinstance(c.get("facts"), dict) else {}
    for h in f.get("hosts") or []:
        if h not in out:
            out.append(h)
    return out


def _skill_text(c: dict) -> str:
    text = c.get("skill_md") if isinstance(c.get("skill_md"), str) else c.get("text")
    return text if isinstance(text, str) and text.startswith("---") else ""


def key_request(c: dict) -> Optional[dict]:
    """``skills.key_request_service`` for a skill candidate (its SKILL.md
    text, or the installed copy), or None. One rule for classify, setup and
    make, so the build never routes such a skill to a command line."""
    if str(c.get("type") or c.get("kind") or "").lower() != "skill":
        return None
    from puffpai import skills
    text = _skill_text(c)
    folder = ""
    if not text:
        rec = skills.find(c.get("skill") or c.get("folder") or "") if \
            (c.get("skill") or c.get("folder")) else None
        if not rec:
            return None
        folder = os.path.dirname(str(rec["path"]))
        try:
            with open(rec["path"], encoding="utf-8", errors="replace") as fh:
                text = fh.read(512 * 1024)
        except OSError:
            return None
    return skills.key_request_service(text, folder)


def skill_hosts(c: dict) -> list[str]:
    """The hosts a DATA skill is a source from: its own service for a keyed
    request, else the API hosts its text names."""
    kr = key_request(c)
    if kr:
        return list(kr["hosts"])
    text = _skill_text(c)
    if text:
        from puffpai import skills
        how = skills.how_it_runs_text(text, "")
        if how["kind"] == "remote" and how["hosts"]:
            return list(how["hosts"])
    return hosts_of(c)


def skill_keys(c: dict) -> list[str]:
    """The key names a DATA skill needs (kept in the vault, sent only to its
    own service)."""
    kr = key_request(c)
    names = list(kr["keys"]) if kr else []
    f = c.get("facts") if isinstance(c.get("facts"), dict) else {}
    for k in f.get("keys") or []:
        if isinstance(k, str) and k not in names:
            names.append(k)
    return [k for k in names if re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", k)]


__all__ = ["APP_MODES", "APP_TYPES", "CODE_SITES", "DOCKER", "HOME_HOSTS", "NATIVE", "NO_START",
           "classify", "docker_here", "needs_docker", "not_usable", "hosts_of", "key_request",
           "run_hint",
           "skill_facts", "skill_hosts", "skill_keys"]
