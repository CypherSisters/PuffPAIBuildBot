"""What outside code needs, and "Install what's missing" after one yes.

Copied in shape from OpenClaw (``metadata.openclaw.requires`` and its
``install: [{kind: node|uv|brew|go|download}]`` specs, ``docs/tools/skills.md``)
and Hermes Agent (``required_environment_variables``): a skill says what it
needs, the owner is shown it in plain words, and one yes installs it.

Reading (:func:`read`) runs nothing. It reads:

* what the skill DECLARES: :func:`puffpai.skills.requirements` (programs and
  keys), the OpenClaw ``install`` specs, ``allowed-tools: Bash(<program> *)``,
  and ``package.json`` / ``requirements.txt`` / ``pyproject.toml`` in its
  folder (top and ``scripts/``);
* what is written only in its TEXT (marked ``"from": "text"``, a suggestion):
  ``npm i -g X``, ``npx X`` (needs node), ``pip install X``, ``uv tool
  install X``, ``pipx install X``, key names like ``$RUNWARE_API_KEY`` or
  ``export X_TOKEN=``, a few well-known programs by name, and sibling skills
  it names ("the `runware-run` skill", a "Related skills" list).

Every name is checked against a strict pattern before it is kept; nothing
read here is ever put into a shell string. Installs run as an argument list.

Installing (:func:`run_plan`), only after her one yes on a card that lists
each step exactly:

* a Python package: the bot's own Python, ``pip install --target
  <state>/tools/py --only-binary=:all:`` (a ready-built copy only, so no setup
  code runs), from pypi.org, one pinned version;
* an npm package: the trusted node and npm (``security.host_programs``),
  ``npm install --prefix <state>/tools/node --ignore-scripts``, from
  registry.npmjs.org, one pinned version;
* a system program (ffmpeg, node, git, jq, gh): only from BuildBot's own
  list (:data:`SYSTEM_PROGRAMS`), with winget on Windows, or apt-get on
  Linux when BuildBot runs as root; both found by full path
  (``security.host_programs``). Never a name read from a skill;
* a sibling skill: the normal skill install from the same publisher
  (:func:`puffpai.finding.install.install_companion`), with the same checks.

Before each package, Drawbridge checks it (kind ``software_package``):
a plain name, on the official registry, one exact version, a ready-built
copy (Python) or a download from the registry itself (npm). An installer
gets a small fixed environment (``host_programs.run_kwargs``): never the
vault's keys, never the bot's API key. Each install is one line in the
security log.

Commands then find what was installed: :func:`command_paths` puts the tools
folders first on a ``run_command``'s path (``brain_commands.command_env``).

At run time, :func:`missing_from_output` reads "command not found", "is not
recognized", ``ModuleNotFoundError`` and "Cannot find module" from a failed
command, so chat can show the same card instead of a dead end.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess

from puffpai import procflags
import sys
import threading
import time
import urllib.parse
from typing import Any, Callable, Optional

from puffpai import skills as skills_mod
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

# ── names ─────────────────────────────────────────────────────────────────────

NPM_RE = re.compile(r"^(?:@[a-z0-9][a-z0-9._-]{0,62}/)?[a-z0-9][a-z0-9._-]{0,99}\Z")
PIP_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?\Z")
VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z.+_-]{0,39}\Z")
BIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}\Z")
ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
SKILL_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}\Z")
#: A key name read from a skill's text must say it is one.
_KEY_WORD = re.compile(r"(KEY|TOKEN|SECRET)")
#: Names a skill may never have a value kept under: they change how programs
#: start, or they are BuildBot's own.
FORBIDDEN_ENV = frozenset({
    "PATH", "HOME", "USERPROFILE", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP",
    "NODE_OPTIONS", "NODE_PATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "BASH_ENV", "ENV",
    "PROMPT_COMMAND", "COMSPEC", "PATHEXT", "SYSTEMROOT", "SHELL", "IFS",
    "NPM_CONFIG_REGISTRY", "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "HTTP_PROXY",
    "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"})
_FORBIDDEN_PREFIX = ("PUFFPAI_", "DYLD_", "LD_", "GIT_", "NPM_CONFIG_", "PIP_")
#: Packages a skill text names that are installers of skills, not tools.
_NOT_TOOLS = frozenset({"skills", "clawhub", "openclaw"})
#: The most of each kind of need read from one skill.
NEEDS_MAX = 12
SIBLINGS_MAX = 5


def env_name_ok(name: Any) -> bool:
    """May a skill's key be kept and handed over under *name*?"""
    if not isinstance(name, str) or not ENV_RE.match(name):
        return False
    up = name.upper()
    return up not in FORBIDDEN_ENV and not up.startswith(_FORBIDDEN_PREFIX)


def _split_npm(spec: str) -> tuple[str, str]:
    """``("@scope/name", "1.2.3")`` from ``@scope/name@1.2.3`` (version "" if none)."""
    spec = spec.strip().strip("\"'`")
    at = spec.rfind("@")
    if at > 0:
        name, version = spec[:at], spec[at + 1:]
    else:
        name, version = spec, ""
    name = name.lower()
    if not NPM_RE.match(name):
        return "", ""
    version = version.lstrip("^~=v")
    return name, version if VERSION_RE.match(version) else ""


def _split_pip(spec: str) -> tuple[str, str]:
    """``("librosa", "0.11.0")`` from ``librosa==0.11.0`` (version "" unless ``==``)."""
    spec = spec.strip().strip("\"'`").split(";", 1)[0].strip()
    m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[A-Za-z0-9,._-]*\])?\s*(.*)$", spec)
    if not m or not PIP_RE.match(m.group(1)):
        return "", ""
    rest = m.group(3).strip()
    version = ""
    if rest.startswith("==") and "," not in rest:
        version = rest[2:].strip()
        if not VERSION_RE.match(version) or "*" in version:
            version = ""
    elif rest and not re.match(r"^[<>=!~]", rest):
        return "", ""
    return m.group(1), version


# ── the programs BuildBot knows how to install itself ─────────────────────────

#: Never read from a skill: winget ids and apt packages come only from here.
#: ``via`` is the Python package that holds the program when there is one.
SYSTEM_PROGRAMS: dict[str, dict[str, str]] = {
    "ffmpeg": {"winget": "Gyan.FFmpeg", "apt": "ffmpeg", "words": "ffmpeg (sound and video)"},
    "ffprobe": {"winget": "Gyan.FFmpeg", "apt": "ffmpeg", "words": "ffmpeg (sound and video)"},
    "node": {"winget": "OpenJS.NodeJS.LTS", "apt": "nodejs", "words": "Node.js"},
    "npm": {"winget": "OpenJS.NodeJS.LTS", "apt": "npm", "words": "Node.js"},
    "npx": {"winget": "OpenJS.NodeJS.LTS", "apt": "npm", "words": "Node.js"},
    "git": {"winget": "Git.Git", "apt": "git", "words": "Git"},
    "jq": {"winget": "jqlang.jq", "apt": "jq", "words": "jq"},
    "gh": {"winget": "GitHub.cli", "apt": "gh", "words": "the GitHub command line"},
    "docker": {"winget": "Docker.DockerDesktop", "words": "Docker Desktop"},
    "python": {"winget": "Python.Python.3.12", "apt": "python3", "words": "Python"},
    "php": {"winget": "PHP.PHP.8.3", "apt": "php-cli", "words": "PHP"},
    "uv": {"pip": "uv", "words": "uv"},
    "yt-dlp": {"pip": "yt-dlp", "words": "yt-dlp"},
}

#: Where a person gets each program herself when BuildBot cannot install it
#: on this computer (a Mac; Linux without root). Fixed here, never from a
#: skill: the card shows the link, nothing is fetched.
SYSTEM_PROGRAM_LINKS: dict[str, str] = {
    "ffmpeg": "https://ffmpeg.org/download.html",
    "ffprobe": "https://ffmpeg.org/download.html",
    "node": "https://nodejs.org/en/download",
    "npm": "https://nodejs.org/en/download",
    "npx": "https://nodejs.org/en/download",
    "git": "https://git-scm.com/downloads",
    "jq": "https://jqlang.org/download/",
    "gh": "https://cli.github.com/",
    "docker": "https://docs.docker.com/get-started/get-docker/",
}
#: Programs named in plain text that count as a need (well known, few false hits).
_TEXT_PROGRAMS = ("ffmpeg", "ffprobe", "jq", "yt-dlp")

#: A Python module that is not its package's name (only these are guessed).
MODULE_PACKAGES = {
    "cv2": "opencv-python", "PIL": "Pillow", "yaml": "PyYAML", "sklearn": "scikit-learn",
    "bs4": "beautifulsoup4", "dateutil": "python-dateutil", "dotenv": "python-dotenv",
    "docx": "python-docx", "Crypto": "pycryptodome", "magic": "python-magic",
    "skimage": "scikit-image", "fitz": "PyMuPDF", "serial": "pyserial",
}


# ── reading what a skill needs ────────────────────────────────────────────────

def _add(rows: list[dict], row: dict, key: str = "name") -> None:
    if len(rows) < NEEDS_MAX and not any(r[key] == row[key] and r.get("kind") == row.get("kind")
                                         for r in rows):
        rows.append(row)


def _body_of(text: str) -> tuple[dict, str]:
    try:
        return skills_mod.parse_frontmatter(text or "")
    except Exception:  # noqa: BLE001
        return {}, text or ""


_CMD_END = re.compile(r"\s(?:&&|\|\||[|;#>])|`")


_SHELL_CHARS = re.compile(r"[;|&$()<>`*?{}\\]")


def _command_tail(line: str, start: int) -> list[str]:
    """The words of one shell command from *start* to its end. A word with
    shell characters in it is never a package name: it is dropped."""
    rest = line[start:]
    m = _CMD_END.search(rest)
    if m:
        rest = rest[:m.start()]
    return [w for w in rest.split() if not _SHELL_CHARS.search(w)]


_NPM_GLOBAL = re.compile(r"\bnpm\s+(?:i|install|add)\b")
_NPX = re.compile(r"\bnpx\s+")
_PIP = re.compile(r"\b(?:python3?\s+-m\s+)?pip3?\s+install\b")
_UV_PIPX = re.compile(r"\b(?:uv\s+tool\s+install|uv\s+pip\s+install|pipx\s+install)\b")
_ENV_REF = re.compile(r"(?:\$\{?|\bexport\s+|\bset\s+)([A-Z][A-Z0-9_]{2,63})\b|"
                      r"`([A-Z][A-Z0-9_]{2,63})`")
_URL = re.compile(r"https://[A-Za-z0-9.-]+(?:/[^\s)>\]\"'`]*)?")
_ALLOWED_BASH = re.compile(r"Bash\(\s*([A-Za-z0-9][A-Za-z0-9._+-]{0,63})(?:[\s:][^)]*)?\)")
_BACKTICK_NAME = re.compile(r"`([a-z0-9][a-z0-9-]{1,63})`")
_NAMED_SKILL = re.compile(r"\b(?:the\s+)?([a-z0-9]+(?:-[a-z0-9]+)+)\s+skills?\b")


def _text_packages(body: str, out: dict[str, list]) -> None:
    for line in body.splitlines():
        if _NPM_GLOBAL.search(line):
            m = _NPM_GLOBAL.search(line)
            words = _command_tail(line, m.end())
            if any(w in ("-g", "--global") for w in words):
                for w in words:
                    if w.startswith("-"):
                        continue
                    name, version = _split_npm(w)
                    if name and name not in _NOT_TOOLS:
                        _add(out["packages"], {"kind": "npm", "name": name, "version": version,
                                               "from": "text"})
        for m in _NPX.finditer(line):
            words = [w for w in _command_tail(line, m.end()) if not w.startswith("-")]
            if words:
                name, _v = _split_npm(words[0])
                if name and name not in _NOT_TOOLS:
                    _add(out["programs"], {"name": "npx", "from": "text"})
        for rx in (_PIP, _UV_PIPX):
            for m in rx.finditer(line):
                words = _command_tail(line, m.end())
                skip = False
                for w in words:
                    if skip:
                        skip = False
                        continue
                    if w in ("-r", "--requirement", "-c", "--constraint", "-t", "--target",
                             "-i", "--index-url", "--extra-index-url", "-e", "--editable"):
                        skip = True
                        continue
                    if w.startswith("-") or "/" in w or "\\" in w or "<" in w:
                        continue
                    name, version = _split_pip(w)
                    if name:
                        _add(out["packages"], {"kind": "pip", "name": name, "version": version,
                                               "from": "text"})


def _text_keys(body: str, out: dict[str, list]) -> None:
    for m in _ENV_REF.finditer(body):
        name = m.group(1) or m.group(2)
        if not name or not _KEY_WORD.search(name) or not env_name_ok(name):
            continue
        if name.startswith(("YOUR_", "MY_")):
            continue
        _add(out["keys"], {"name": name, "from": "text"})


def _text_programs(body: str, out: dict[str, list]) -> None:
    low = body.lower()
    for prog in _TEXT_PROGRAMS:
        if re.search(r"(?<![a-z0-9_.-])" + re.escape(prog) + r"(?![a-z0-9_-])", low):
            _add(out["programs"], {"name": prog, "from": "text"})


#: One backticked name, maybe a markdown link: "`x`" or "[`x`](https://...)".
_TICKED = r"\[?`([a-z0-9][a-z0-9-]{1,63})`\]?(?:\([^)\s]{0,300}\))?"
#: Names right before the word skill: "the `a` + `b` skills", "[`a`](...) skill".
_BEFORE_SKILL = re.compile(r"((?:" + _TICKED + r"\s*(?:,|\+|/|&|and|or)?\s*)+)skills?\b")
#: The names a list item starts with: "- `a`, `b`; `c` (does x)".
_LEADING = re.compile(r"^(?:[-*]|\d+[.)])?\s*((?:" + _TICKED + r"\s*(?:[,;+/]|and|or)?\s*)+)")


def siblings_in(text: str, own: str = "") -> list[str]:
    """Other skills a skill's text names: names right before the word skill
    ("the `runware-run` skill", "via the `a` + `b` skills"), the names a line
    starts with under a heading about skills ("## Related skills"), and
    "x-y skill" when that name is also written as a name (backticked)."""
    _meta, body = _body_of(text)
    ticked = set(_BACKTICK_NAME.findall(body))
    found: list[str] = []
    under_skills = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            under_skills = bool(re.search(r"(?i)\bskills?\b", stripped))
            continue
        names: list[str] = []
        for m in _BEFORE_SKILL.finditer(line):
            names += re.findall(_TICKED, m.group(1))
        if under_skills:
            m = _LEADING.match(stripped)
            if m:
                names += re.findall(_TICKED, m.group(1))
        names += [n for n in _NAMED_SKILL.findall(line) if n in ticked]
        for name in names:
            if name in found or name == own or not SKILL_RE.match(name):
                continue
            if name in ("skill", "skills") or name in _NOT_TOOLS:
                continue
            found.append(name)
    return found[:SIBLINGS_MAX]


# ── the one publisher rule ──────────────────────────────────────────────────
# One rule for "the same publisher", used by the plan (:func:`read`,
# :func:`make_plan`) and by the installer
# (:func:`puffpai.finding.install.install_companion`), so a page never offers
# a sibling skill the installer then refuses ("One yes installs: summarizer,
# scrape, reading" next to "not from the same publisher").

_OWNER_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_REPO_RE = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")


def publisher(origin: Any) -> Optional[dict]:
    """Where an installed skill came from, as a publisher its siblings can come
    from too, or None. *origin* is the skill's lock entry. A ClawHub skill
    needs its owner; a GitHub skill needs its project."""
    o = origin if isinstance(origin, dict) else {}
    if o.get("source") == "clawhub":
        owner = str(o.get("owner") or "")
        return {"source": "clawhub", "owner": owner} if _OWNER_RE.fullmatch(owner) else None
    if o.get("source") == "github":
        repo = str(o.get("repo") or "")
        if _REPO_RE.fullmatch(repo):
            return {"source": "github", "repo": repo, "folder": str(o.get("folder") or "")}
    return None


def sibling_find(origin: Any, name: str) -> Optional[dict]:
    """A find for the sibling skill *name* from the same publisher as the skill
    whose lock entry is *origin*, or None (then it is neither offered nor
    installed)."""
    if not isinstance(name, str) or not SKILL_RE.match(name) or skills_mod.safe_name(name) != name:
        return None
    pub = publisher(origin)
    if pub is None:
        return None
    if pub["source"] == "clawhub":
        from puffpai.finding import sites
        owner = pub["owner"]
        return {"id": f"clawhub:{owner}/{name}", "name": name, "type": "skill",
                "can": "install", "site_name": "ClawHub",
                "url": f"https://{sites.CLAWHUB}/{owner}/skills/{name}", "raw": {}}
    base = pub["folder"].rsplit("/", 1)
    hint = (base[0] + "/" + name) if len(base) == 2 else ""
    return {"id": f"skills.sh:{pub['repo']}/{name}", "name": name, "type": "skill",
            "can": "install", "site_name": "GitHub",
            "url": f"https://github.com/{pub['repo']}", "raw": {"hint": hint}}


def siblings_offered(text: str, own: str, origin: Any) -> list[str]:
    """The sibling skills a skill's text names that its publisher can give:
    what a plan may offer and exactly what the installer accepts."""
    if publisher(origin) is None:
        return []
    return [n for n in siblings_in(text, own) if sibling_find(origin, n) is not None]


def lock_entry(folder_name: str) -> dict:
    """An installed skill's lock entry (``<skills>/.lock.json``), or {}."""
    try:
        with open(os.path.join(skills_mod.skills_dir(), ".lock.json"), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    got = data.get(str(folder_name or "")) if isinstance(data, dict) else None
    return got if isinstance(got, dict) else {}


def _install_specs(meta: dict, out: dict[str, list]) -> None:
    metadata = skills_mod._as_mapping(meta.get("metadata"))  # noqa: SLF001
    for key in ("openclaw", "clawdbot", "clawdis"):
        block = skills_mod._as_mapping(metadata.get(key))  # noqa: SLF001
        specs = block.get("install") if isinstance(block.get("install"), list) else []
        for spec in specs[:NEEDS_MAX]:
            if not isinstance(spec, dict):
                continue
            kind = str(spec.get("kind") or "").lower()
            bins = [b for b in (spec.get("bins") or []) if isinstance(b, str) and BIN_RE.match(b)]
            label = skills_mod._one_line(spec.get("label"), 120)  # noqa: SLF001
            if kind == "node":
                name, version = _split_npm(str(spec.get("package") or ""))
                if name:
                    _add(out["packages"], {"kind": "npm", "name": name, "version": version,
                                           "from": "declared", "bins": bins})
                    continue
            elif kind == "uv":
                name, version = _split_pip(str(spec.get("package") or ""))
                if name:
                    _add(out["packages"], {"kind": "pip", "name": name, "version": version,
                                           "from": "declared", "bins": bins})
                    continue
            # brew, go, download and anything else: she installs it herself,
            # unless BuildBot's own list knows the program.
            for b in bins:
                if b in SYSTEM_PROGRAMS:
                    continue
                url = str(spec.get("url") or "")
                _add(out["manual"], {"name": b, "how": kind or "other",
                                     "label": label, "url": url if url.startswith("https://") else ""})


def _folder_files(folder: Optional[str], out: dict[str, list]) -> None:
    if not folder or not os.path.isdir(folder):
        return
    for sub in ("", "scripts"):
        base = os.path.join(folder, sub) if sub else folder
        req = os.path.join(base, "requirements.txt")
        if os.path.isfile(req):
            try:
                with open(req, encoding="utf-8", errors="replace") as fh:
                    for raw in fh.read(64 * 1024).splitlines():
                        line = raw.split("#", 1)[0].strip()
                        if not line or line.startswith("-"):
                            continue
                        name, version = _split_pip(line)
                        if name:
                            _add(out["packages"], {"kind": "pip", "name": name,
                                                   "version": version, "from": "declared"})
            except OSError:
                pass
        pkg = os.path.join(base, "package.json")
        if os.path.isfile(pkg):
            try:
                with open(pkg, encoding="utf-8") as fh:
                    data = json.loads(fh.read(256 * 1024))
            except (OSError, ValueError):
                data = None
            deps = data.get("dependencies") if isinstance(data, dict) else None
            for name, spec in (deps or {}).items() if isinstance(deps, dict) else ():
                if not isinstance(spec, str):
                    continue
                n, v = _split_npm(f"{name}@{spec}")
                if n:
                    _add(out["packages"], {"kind": "npm", "name": n, "version": v,
                                           "from": "declared"})
            if deps:
                _add(out["programs"], {"name": "node", "from": "declared"})
        pyproject = os.path.join(base, "pyproject.toml")
        if os.path.isfile(pyproject):
            try:
                import tomllib
                with open(pyproject, "rb") as fh:
                    data = tomllib.loads(fh.read(256 * 1024).decode("utf-8", "replace"))
            except Exception:  # noqa: BLE001 - a broken file declares nothing
                data = {}
            project = data.get("project") if isinstance(data, dict) else None
            for spec in (project or {}).get("dependencies") or [] if isinstance(project, dict) else []:
                name, version = _split_pip(str(spec))
                if name:
                    _add(out["packages"], {"kind": "pip", "name": name, "version": version,
                                           "from": "declared"})


def _get_url(name: str, meta: dict, body: str, help_text: str = "") -> str:
    """Where to get key *name*: a link on its own service's site in the
    skill's text, else the skill's homepage."""
    token = name.split("_", 1)[0].lower()
    for text in (help_text, body):
        for url in _URL.findall(text or ""):
            host = urllib.parse.urlsplit(url).hostname or ""
            if len(token) >= 4 and token in host.lower().replace("-", ""):
                return url[:300]
    home = str(meta.get("homepage") or "")
    if home.startswith("https://") and len(home) <= 300:
        return home
    return ""


def read(text: Optional[str] = None, folder: Optional[str] = None,
         origin: Optional[dict] = None) -> dict[str, Any]:
    """Everything a skill needs, from its ``SKILL.md`` *text* (or the one in
    *folder*) and the package files in its folder.

    Sibling skills are listed only when they pass the one publisher rule
    (:func:`siblings_offered`) for *origin*, the skill's lock entry (read
    from the lock for an installed *folder* when not given). With no known
    publisher none is listed, because none would be installed.

    ``{"programs": [{name, from}], "any_programs": [...], "packages": [{kind,
    name, version, from, bins}], "keys": [{name, from, prompt, help,
    get_url}], "skills": [{name, from}], "manual": [{name, how, label,
    url}]}``. ``from`` is ``"declared"`` or ``"text"``. Never raises."""
    if text is None and folder:
        try:
            with open(os.path.join(folder, skills_mod.SKILL_FILE), encoding="utf-8",
                      errors="replace") as fh:
                text = fh.read(512 * 1024)
        except OSError:
            text = ""
    meta, body = _body_of(text or "")
    meta = meta if isinstance(meta, dict) else {}
    out: dict[str, list] = {"programs": [], "any_programs": [], "packages": [], "keys": [],
                            "skills": [], "manual": []}
    try:
        req = skills_mod.requirements(meta)
        for b in req["bins"]:
            _add(out["programs"], {"name": b, "from": "declared"})
        out["any_programs"] = list(req["any_bins"])
        for k in req["env"]:
            if env_name_ok(k):
                h = req["env_help"].get(k) or {}
                _add(out["keys"], {"name": k, "from": "declared", "prompt": h.get("prompt", ""),
                                   "help": h.get("help", "")})
        for m in _ALLOWED_BASH.finditer(str(meta.get("allowed-tools") or "")):
            _add(out["programs"], {"name": m.group(1), "from": "declared"})
        _install_specs(meta, out)
        _folder_files(folder, out)
        _text_packages(body, out)
        _text_keys(body, out)
        _text_programs(body, out)
        own = str(meta.get("name") or (os.path.basename(folder) if folder else ""))
        if origin is None and folder:
            origin = lock_entry(os.path.basename(os.path.normpath(folder)))
        for name in siblings_offered(text or "", own, origin):
            _add(out["skills"], {"name": name, "from": "text"})
    except Exception as exc:  # noqa: BLE001 - a skill we cannot read needs nothing we know
        logger.info("deps: a skill could not be read (%s)", type(exc).__name__)
    out["programs"] = [p for p in out["programs"] if p["name"] not in skills_mod._ALWAYS_HAS]  # noqa: SLF001
    for k in out["keys"]:
        k.setdefault("prompt", "")
        k.setdefault("help", "")
        k["get_url"] = _get_url(k["name"], meta, body, k.get("help", ""))
    for p in out["packages"]:
        p.setdefault("bins", [])
    return out


def read_skill(folder_name: str) -> dict[str, Any]:
    """:func:`read` for the installed skill *folder_name* ({} parts if none)."""
    rec = skills_mod.find(folder_name)
    if rec is None:
        return read("")
    return read(folder=os.path.dirname(rec["path"]))


def keys_in_text(text: str) -> list[dict[str, str]]:
    """Key names a plain text asks for (an outside project's README), each
    with a "Get a key" link from the same text: ``[{name, from, get_url}]``.
    The same rules as a skill's text: names that say key, token or secret,
    never one that changes how programs start (:func:`env_name_ok`)."""
    meta, body = _body_of(str(text or "")[:512 * 1024])
    meta = meta if isinstance(meta, dict) else {}
    out: dict[str, list] = {"keys": []}
    try:
        _text_keys(body, out)
    except Exception:  # noqa: BLE001 - unreadable text names no key
        return []
    return [{"name": k["name"], "from": "text", "get_url": _get_url(k["name"], meta, body)}
            for k in out["keys"]]


def get_url_for(name: str, text: str) -> str:
    """Where to get key *name*, from a link in *text* (or ""): a link on its
    own service's site (as for a skill), else the one link that sits on the
    key's line (or the line after it) and says key (``.../apikey``)."""
    meta, body = _body_of(str(text or "")[:512 * 1024])
    found = _get_url(name, meta if isinstance(meta, dict) else {}, body)
    if found:
        return found
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if name not in line:
            continue
        near = line + " " + (lines[i + 1] if i + 1 < len(lines) else "")
        urls = [u for u in _URL.findall(near) if "key" in u.lower()]
        if len(urls) == 1 and len(urls[0]) <= 300:
            return urls[0]
    return ""


def key_names(folder_name: str) -> list[str]:
    """The keys the installed skill may have kept for it: declared ones and
    ones its text names (``$X_API_KEY``)."""
    return [k["name"] for k in read_skill(folder_name)["keys"]]


# ── what is here already ──────────────────────────────────────────────────────

def tools_dir(*parts: str) -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), "tools", *parts)


def py_target() -> str:
    return tools_dir("py")


def node_prefix() -> str:
    return tools_dir("node")


def command_paths() -> dict[str, list[str]]:
    """Folders a command needs to find what was installed here:
    ``{"path": [...], "pythonpath": [...], "node_path": [...], "host_path":
    [...]}`` (existing only). ``host_path`` holds the command line tools of
    apps set up on this computer (``integrate.run_here.cli_dirs``); it goes
    LAST on a command's path, so an app's own Python never stands in for
    the system's programs."""
    out: dict[str, list[str]] = {"path": [], "pythonpath": [], "node_path": [],
                                 "host_path": []}
    try:
        from puffpai.integrate import run_here
        out["host_path"] = [d for d in run_here.cli_dirs() if os.path.isdir(d)]
    except Exception:  # noqa: BLE001 - no host apps, no extra folders
        out["host_path"] = []
    try:
        node_bin = os.path.join(node_prefix(), "node_modules", ".bin")
        py_bin = os.path.join(py_target(), "bin")
    except Exception:  # noqa: BLE001
        return out
    for d in (node_bin, py_bin):
        if os.path.isdir(d):
            out["path"].append(d)
    if os.path.isdir(py_target()):
        out["pythonpath"].append(py_target())
    if os.path.isdir(os.path.join(node_prefix(), "node_modules")):
        out["node_path"].append(os.path.join(node_prefix(), "node_modules"))
    return out


def has_program(name: str) -> bool:
    """Is *name* on this computer's path or in the bot's own tools folders?"""
    import shutil
    found = command_paths()
    dirs = found["path"] + found["host_path"]
    if dirs:
        try:
            if shutil.which(name, path=os.pathsep.join(dirs)):
                return True
        except (OSError, ValueError):
            pass
    try:
        return shutil.which(name) is not None
    except (OSError, ValueError):
        return False


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def has_package(kind: str, name: str) -> bool:
    if kind == "npm":
        return os.path.isfile(os.path.join(node_prefix(), "node_modules", *name.split("/"),
                                           "package.json"))
    want = _norm(name)
    try:
        from importlib import metadata
        for dist in metadata.distributions(path=[py_target()] + list(sys.path)):
            got = dist.metadata["Name"] if dist.metadata else ""
            if got and _norm(str(got)) == want:
                return True
    except Exception:  # noqa: BLE001 - unreadable means not there
        return False
    return False


def _skill_installed(name: str) -> bool:
    return skills_mod.find(name) is not None


def missing(needs: dict[str, Any], kept_keys: Any = ()) -> dict[str, Any]:
    """The part of *needs* this computer does not have yet."""
    kept = set(kept_keys or ())
    progs = [p for p in needs["programs"] if not has_program(p["name"])]
    group = needs.get("any_programs") or []
    if group and not any(has_program(b) for b in group):
        progs.append({"name": group[0], "from": "declared", "any_of": list(group)})
    return {"programs": progs,
            "packages": [p for p in needs["packages"] if not has_package(p["kind"], p["name"])],
            "keys": [k for k in needs["keys"] if k["name"] not in kept],
            "skills": [s for s in needs["skills"] if not _skill_installed(s["name"])],
            "manual": [m for m in needs["manual"] if not has_program(m["name"])]}


# ── at run time: what a failed command says is missing ───────────────────────

_NOT_FOUND = (
    re.compile(r"(?m)(?:^|:\s)([A-Za-z0-9][A-Za-z0-9._+-]{0,63}): (?:command )?not found\s*$"),
    re.compile(r"'([A-Za-z0-9][A-Za-z0-9._+-]{0,63})' is not recognized as an internal or "
               r"external command"),
)
_NO_MODULE = re.compile(r"ModuleNotFoundError: No module named '([A-Za-z0-9_.]{1,100})'")
_NO_NODE_MODULE = re.compile(r"Cannot find (?:module|package) '([^'./][^']{0,120})'")


def missing_from_output(output: str, exit_code: Any = None,
                        command: str = "") -> dict[str, list[str]]:
    """``{"programs": [...], "modules": [...], "node_modules": [...]}`` a
    failed command's output says are missing (names checked)."""
    text = str(output or "")[-20000:]
    progs: list[str] = []
    for rx in _NOT_FOUND:
        for name in rx.findall(text):
            if BIN_RE.match(name) and name not in progs and name not in ("bash", "sh", "line"):
                progs.append(name)
    if exit_code == 127 and not progs and command:
        first = command.strip().split(None, 1)[0].strip("\"'") if command.strip() else ""
        first = os.path.basename(first)
        if BIN_RE.match(first):
            progs.append(first)
    mods = []
    for name in _NO_MODULE.findall(text):
        top = name.split(".", 1)[0]
        if top not in mods:
            mods.append(top)
    node_mods = []
    for spec in _NO_NODE_MODULE.findall(text):
        parts = spec.split("/")
        name = "/".join(parts[:2]) if spec.startswith("@") else parts[0]
        if NPM_RE.match(name) and name not in node_mods:
            node_mods.append(name)
    return {"programs": progs[:NEEDS_MAX], "modules": mods[:NEEDS_MAX],
            "node_modules": node_mods[:NEEDS_MAX]}


# ── the plan she says yes to ─────────────────────────────────────────────────

PLAN_TTL_S = 30 * 60
_PLANS: dict[str, dict] = {}
_PLANS_LOCK = threading.Lock()
_RUN_LOCK = threading.Lock()
PLANS_MAX = 50

#: Which outside switch the package registries go under (the finding one).
REGISTRY_SERVICE = "code_search"
PYPI = "pypi.org"
NPM = "registry.npmjs.org"


def _is_windows() -> bool:
    return sys.platform == "win32"


def _is_root() -> bool:
    try:
        return hasattr(os, "geteuid") and os.geteuid() == 0
    except Exception:  # noqa: BLE001
        return False


def _system_step(prog: str) -> Optional[dict]:
    row = SYSTEM_PROGRAMS.get(prog)
    if not row:
        return None
    if row.get("pip"):
        return {"kind": "pip", "name": row["pip"], "version": "", "from": "list",
                "for": prog}
    if _is_windows() and row.get("winget"):
        return {"kind": "winget", "name": row["winget"], "version": "", "from": "list",
                "for": prog, "label": row["words"]}
    if sys.platform.startswith("linux") and row.get("apt") and _is_root():
        return {"kind": "apt", "name": row["apt"], "version": "", "from": "list",
                "for": prog, "label": row["words"]}
    return None


def _step_words(step: dict) -> str:
    text_note = " (read from its text)" if step.get("from") == "text" else ""
    kind = step["kind"]
    if kind == "pip":
        v = f" {step['version']}" if step.get("version") else " (newest version)"
        return f"The Python package {step['name']}{v}, into the bot's own tools folder{text_note}."
    if kind == "npm":
        v = f" {step['version']}" if step.get("version") else " (newest version)"
        return f"The npm package {step['name']}{v}, into the bot's own tools folder{text_note}."
    if kind == "winget":
        return f"{step.get('label') or step['name']}, with Windows' own installer (winget)."
    if kind == "apt":
        return f"{step.get('label') or step['name']}, with this computer's apt-get."
    if kind == "skill":
        return (f"The skill {step['name']} from the same publisher, checked like any "
                f"skill{text_note}.")
    return step["name"]


def _manual_line(name: str, how: str = "", url: str = "") -> str:
    url = url or SYSTEM_PROGRAM_LINKS.get(name, "")
    where = f" See {url}" if url else ""
    if how == "brew":
        return f"{name}: BuildBot cannot install it here (it comes with Homebrew). Install it yourself.{where}"
    if name in ("node", "npm", "npx") and sys.platform != "win32":
        # BuildBot's own setup gets a checked Node.js into its tools folder
        # (scripts/install_tools.sh): the same way in, after the same one yes.
        return ("Node.js: BuildBot's setup can get it for you. Run its setup again "
                f"(bash install.sh) and type y when it offers.{where}")
    return f"{name}: BuildBot cannot install it for you. Install it yourself.{where}"


def make_plan(skill: str = "", *, thing: str = "", found: Optional[dict] = None,
              needs: Optional[dict] = None) -> dict[str, Any]:
    """The card's plan: what to install, in order, and what she has to do
    herself. *found* is :func:`missing_from_output` from a failed command.
    Kept for :data:`PLAN_TTL_S`; her yes installs exactly these steps."""
    if needs is None:
        needs = read_skill(skill) if skill else read("")
    kept: list[str] = []
    if skill:
        try:
            from puffpai.security import keys
            kept = keys.skill_keys_set(skill)
        except Exception:  # noqa: BLE001
            kept = []
    gone = missing(needs, kept)
    steps: list[dict] = []
    manual: list[str] = []

    def add(step: dict) -> None:
        if not any(s["kind"] == step["kind"] and s["name"] == step["name"] for s in steps):
            steps.append(step)

    for s in gone["skills"]:
        add({"kind": "skill", "name": s["name"], "version": "", "from": s["from"]})
    for p in gone["packages"]:
        add({"kind": p["kind"], "name": p["name"], "version": p.get("version") or "",
             "from": p["from"]})
    progs = [p["name"] for p in gone["programs"]]
    for name in (found or {}).get("programs") or []:
        if name not in progs:
            progs.append(name)
    pkg_names = {p["name"].lower() for p in needs["packages"]}
    for prog in progs:
        if any(prog.lower() in n.split("/")[-1] or prog.lower() in n for n in pkg_names):
            continue        # one of its packages brings it
        if prog in ("npx", "npm") and has_program("node"):
            continue
        step = _system_step(prog)
        if step:
            add(step)
        elif not any(m["name"] == prog for m in gone["manual"]):
            manual.append(_manual_line(prog))
    for m in gone["manual"]:
        manual.append(_manual_line(m["name"], m.get("how", ""), m.get("url", "")))
    for mod in (found or {}).get("modules") or []:
        pkg = next((p["name"] for p in needs["packages"]
                    if p["kind"] == "pip" and _norm(p["name"]) == _norm(mod)), "") \
            or MODULE_PACKAGES.get(mod, "")
        if pkg and PIP_RE.match(pkg):
            add({"kind": "pip", "name": pkg, "version": "", "from": "error"})
        else:
            manual.append(f"The Python module {mod}: BuildBot does not know which package "
                          f"has it. Ask in chat to install it, or install it yourself.")
    for mod in (found or {}).get("node_modules") or []:
        known = any(p["kind"] == "npm" and p["name"] == mod for p in needs["packages"])
        if known:
            add({"kind": "npm", "name": mod, "version": "", "from": "declared"})
        else:
            manual.append(f"The npm package {mod}: it is not one its skill names. Ask in "
                          f"chat to install it, or install it yourself.")
    keys_out = [{"name": k["name"], "get_url": k.get("get_url", ""), "from": k["from"]}
                for k in gone["keys"]]
    plan = {"id": secrets.token_urlsafe(12), "skill": skill, "thing": thing,
            "steps": [{**s, "words": _step_words(s)} for s in steps][:NEEDS_MAX],
            "manual": list(dict.fromkeys(manual))[:NEEDS_MAX], "keys": keys_out,
            "made_at": time.time()}
    if plan["steps"]:
        with _PLANS_LOCK:
            now = time.time()
            for pid in [p for p, v in _PLANS.items() if now - v["made_at"] > PLAN_TTL_S]:
                _PLANS.pop(pid, None)
            while len(_PLANS) >= PLANS_MAX:
                _PLANS.pop(next(iter(_PLANS)))
            _PLANS[plan["id"]] = plan
    return plan


def public_plan(plan: dict) -> dict:
    """What the card shows: words only, plus the id her yes sends back."""
    return {"plan": plan["id"] if plan.get("steps") else "",
            "steps": [s["words"] for s in plan.get("steps") or []],
            "manual": list(plan.get("manual") or []),
            "keys": [{"name": k["name"], "get_url": k.get("get_url", "")}
                     for k in plan.get("keys") or []],
            "thing": plan.get("thing", ""), "skill": plan.get("skill", "")}


def plan_line(plan: dict) -> str:
    """One plain sentence about what is missing."""
    parts = []
    if plan.get("steps"):
        parts.append(f"{len(plan['steps'])} thing{'s' if len(plan['steps']) > 1 else ''} "
                     f"can be installed with one yes")
    if plan.get("keys"):
        names = ", ".join(k["name"] for k in plan["keys"])
        parts.append(f"it needs your key {names}")
    if plan.get("manual"):
        parts.append("some of it you install yourself")
    return ("Something it needs is missing: " + "; ".join(parts) + ".") if parts else ""


def get_plan(plan_id: Any) -> Optional[dict]:
    with _PLANS_LOCK:
        plan = _PLANS.get(str(plan_id or ""))
        if plan and time.time() - plan["made_at"] > PLAN_TTL_S:
            _PLANS.pop(plan["id"], None)
            return None
        return plan


# ── Drawbridge: one check per package ────────────────────────────────────────

KIND = "software_package"
CHECKER_VERSION = "1"
BLOCK_CODES = frozenset({"bad_name", "not_on_registry", "not_pinned", "no_wheel",
                         "yanked", "offsite_download", "not_listed", "unknown_manager"})


def package_rows(draft: dict) -> list[dict[str, str]]:
    """Drawbridge's checklist for one package. Reads only the record (the
    registry's answer was looked up before, by :func:`_registry_facts`)."""
    rows: list[dict[str, str]] = []
    manager = str(draft.get("manager") or "")
    name = str(draft.get("package") or "")
    facts = draft.get("facts") if isinstance(draft.get("facts"), dict) else {}

    def row(check: str, ok: bool, why: str, code: str) -> None:
        rows.append({"check": check, "result": "pass" if ok else "fail",
                     "why": "" if ok else why, "code": code})

    if manager in ("winget", "apt"):
        row("It is on BuildBot's own list of programs",
            any(r.get(manager) == name for r in SYSTEM_PROGRAMS.values()),
            "only programs on BuildBot's own list are installed this way", "not_listed")
        return rows
    if manager not in ("pip", "npm"):
        row("BuildBot knows how to install it", False, "unknown installer", "unknown_manager")
        return rows
    pattern = PIP_RE if manager == "pip" else NPM_RE
    row("Its name is a plain package name", bool(pattern.match(name)),
        "the name is not a plain package name", "bad_name")
    row(f"It is on {'pypi.org' if manager == 'pip' else 'the npm registry'}",
        bool(facts.get("exists")), "the registry does not know it", "not_on_registry")
    version = str(draft.get("version") or "")
    row("One exact version is pinned", bool(VERSION_RE.match(version)),
        "no exact version", "not_pinned")
    if manager == "pip":
        row("A ready-built copy exists, so no setup code runs", bool(facts.get("wheel")),
            "there is no ready-built copy of this version", "no_wheel")
        row("That version was not pulled back", not facts.get("yanked"),
            "its makers pulled that version back", "yanked")
    else:
        row("Its download comes from the npm registry itself", bool(facts.get("tarball_ok")),
            "its download is somewhere else", "offsite_download")
        if facts.get("scripts"):
            rows.append({"check": "Its install scripts are skipped (they never run)",
                         "result": "note", "why": "", "code": "scripts_skipped"})
    return rows


def connect() -> list[str]:
    """Register the package checklist with Drawbridge (factory_wiring)."""
    from puffpai import drawbridge
    drawbridge.register_checker(KIND, package_rows, block_codes=BLOCK_CODES,
                                version=CHECKER_VERSION)
    return [f"drawbridge:{KIND}"]


def _registry_facts(step: dict) -> tuple[str, dict]:
    """``(pinned version, facts)`` from the package's registry. Version "" when
    it cannot be found."""
    from puffpai.finding import net
    name, version = step["name"], step.get("version") or ""
    if step["kind"] == "pip":
        path = urllib.parse.quote(name) + (f"/{urllib.parse.quote(version)}" if version else "")
        ans = net.get(f"https://{PYPI}/pypi/{path}/json", hosts=[PYPI],
                      service=REGISTRY_SERVICE, cache_s=0)
        data = ans.json() if ans.ok else None
        if not isinstance(data, dict) or not isinstance(data.get("info"), dict):
            return "", {"exists": False}
        pinned = str(data["info"].get("version") or "")
        files = data.get("urls") if isinstance(data.get("urls"), list) else []
        return pinned, {
            "exists": True,
            "wheel": any(isinstance(f, dict) and f.get("packagetype") == "bdist_wheel"
                         for f in files),
            "yanked": bool(data["info"].get("yanked"))
            or (bool(files) and all(isinstance(f, dict) and f.get("yanked") for f in files))}
    enc = urllib.parse.quote(name, safe="@")
    ans = net.get(f"https://{NPM}/{enc}/{urllib.parse.quote(version or 'latest')}",
                  hosts=[NPM], service=REGISTRY_SERVICE, cache_s=0)
    data = ans.json() if ans.ok else None
    if not isinstance(data, dict) or data.get("name") != name:
        return "", {"exists": False}
    tarball = str((data.get("dist") or {}).get("tarball") or "") \
        if isinstance(data.get("dist"), dict) else ""
    scripts = data.get("scripts") if isinstance(data.get("scripts"), dict) else {}
    return str(data.get("version") or ""), {
        "exists": True,
        "tarball_ok": tarball.startswith(f"https://{NPM}/"),
        "scripts": sorted(k for k in scripts if k in ("preinstall", "install", "postinstall"))}


def check_step(step: dict) -> tuple[dict, str]:
    """``(verdict, pinned version)``: Drawbridge's verdict for one install step."""
    from puffpai import drawbridge
    version, facts = "", {}
    if step["kind"] in ("pip", "npm"):
        version, facts = _registry_facts(step)
    draft = {"kind": KIND, "id": f"{step['kind']}:{step['name']}", "name": step["name"],
             "manager": step["kind"], "package": step["name"], "version": version,
             "facts": facts}
    return drawbridge.check_addition(draft, by="install_missing"), version


# ── installing ────────────────────────────────────────────────────────────────

INSTALL_SECONDS = 600


def _run_installer(argv: list[str], extra_env: Optional[dict] = None) -> dict:
    """Start one installer. A seam: the tests replace it, nothing is
    installed while they run."""
    from puffpai.security import host_programs
    kw = host_programs.run_kwargs()
    env = dict(kw["env"])
    env.update(extra_env or {})
    try:
        done = subprocess.run(argv, cwd=kw["cwd"], env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=INSTALL_SECONDS,
                              stdin=subprocess.DEVNULL, shell=False, **procflags.hidden())
    except subprocess.TimeoutExpired:
        return {"code": None, "output": "it took too long"}
    except OSError as exc:
        return {"code": None, "output": type(exc).__name__}
    return {"code": done.returncode, "output": (done.stdout or "")[-2000:]
            + (done.stderr or "")[-2000:]}


def installer_argv(step: dict, version: str, target: Optional[str] = None) -> Optional[list[str]]:
    """The exact argument list for one step, or None when this computer
    cannot run it. Names were checked by Drawbridge first. *target* overrides
    where a pip or npm package lands (used for a skill's own isolated CLI copy,
    :mod:`puffpai.security.skill_programs`); the default is the shared folder."""
    from puffpai.security import host_programs
    kind, name = step["kind"], step["name"]
    if kind == "pip":
        if not (PIP_RE.match(name) and VERSION_RE.match(version)):
            return None
        return [sys.executable, "-m", "pip", "install", "--isolated",
                "--disable-pip-version-check", "--no-input", "--only-binary=:all:",
                "--upgrade", "--target", target or py_target(),
                "--index-url", "https://pypi.org/simple", f"{name}=={version}"]
    if kind == "npm":
        if not (NPM_RE.match(name) and VERSION_RE.match(version)):
            return None
        node = host_programs.path("node")
        cli = host_programs.npm_cli(node) if node else None
        if not (node and cli):
            return None
        return [node, cli, "install", "--prefix", target or node_prefix(), "--ignore-scripts",
                "--no-audit", "--no-fund", "--save-exact",
                f"--registry=https://{NPM}/", f"{name}@{version}"]
    if kind == "winget":
        exe = host_programs.path("winget")
        if not exe or not any(r.get("winget") == name for r in SYSTEM_PROGRAMS.values()):
            return None
        return [exe, "install", "--id", name, "--exact", "--source", "winget", "--silent",
                "--accept-package-agreements", "--accept-source-agreements",
                "--disable-interactivity"]
    if kind == "apt":
        exe = host_programs.path("apt-get")
        if not exe or not _is_root() or not any(r.get("apt") == name
                                                 for r in SYSTEM_PROGRAMS.values()):
            return None
        return [exe, "install", "-y", "--no-install-recommends", name]
    return None


def _audit(skill: str, step: dict, version: str, result: str, device: str) -> None:
    try:
        from puffpai.security import paired_devices
        from puffpai.security.audit_log import AuditLog
        AuditLog().log(action="install_missing", user="owner", result=result,
                       details=f"{skill or 'work folder'}: {step['kind']} {step['name']} "
                               f"{version}".strip(),
                       extra={"device": paired_devices.label(device) if device
                              else "this computer"})
    except Exception:  # noqa: BLE001 - the log line never stops the install
        pass


def _record(step: dict, version: str, skill: str) -> None:
    from puffpai import fsutil
    path = tools_dir("installed.json")
    data = fsutil.read_json(path, default=None)
    rows = data.get("installed") if isinstance(data, dict) else None
    rows = [r for r in (rows or []) if isinstance(r, dict)
            and not (r.get("kind") == step["kind"] and r.get("name") == step["name"])]
    rows.append({"kind": step["kind"], "name": step["name"], "version": version,
                 "for": skill, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    os.makedirs(tools_dir(), exist_ok=True)
    fsutil.atomic_write_json(path, {"installed": rows[-500:]})


def _switch_off() -> bool:
    try:
        from puffpai.security import outside_switches
        return outside_switches.is_off(REGISTRY_SERVICE)
    except Exception:  # noqa: BLE001 - cannot tell: off
        return True


def run_plan(plan_id: Any, *, said_yes: bool, device: str = "",
             progress: Callable[[str], None] = lambda _l: None) -> dict[str, Any]:
    """Install the steps of plan *plan_id* (only after her yes).

    ``{"ok", "lines", "done": [...], "failed": [...]}``. Each package is
    checked by Drawbridge first; nothing is installed with any key in its
    environment; one install at a time."""
    if not said_yes:
        return {"ok": False, "lines": ["Nothing was installed: there was no yes."],
                "done": [], "failed": []}
    plan = get_plan(plan_id)
    if plan is None:
        return {"ok": False, "code": "stale", "done": [], "failed": [],
                "lines": ["That list is out of date. Here is the new one."]}
    lines: list[str] = []

    def say(line: str) -> None:
        lines.append(line)
        try:
            progress(line)
        except Exception:  # noqa: BLE001
            pass

    done: list[str] = []
    failed: list[str] = []
    # Who it is for, in the security log: the skill, else the thing (an app
    # that runs on this computer), else the work folder.
    who = plan.get("skill") or plan.get("thing") or ""
    with _PLANS_LOCK:
        _PLANS.pop(plan["id"], None)       # one yes, one run
    if not _RUN_LOCK.acquire(timeout=5):
        return {"ok": False, "lines": ["Another install is running. Try again in a minute."],
                "done": [], "failed": [], "code": "busy"}
    try:
        for step in plan["steps"]:
            name = step["name"]
            if step["kind"] == "skill":
                got = _install_sibling(plan.get("skill") or "", name, say)
                (done if got else failed).append(name)
                _audit(who, step, "", "success" if got else "failed", device)
                continue
            if step["kind"] in ("pip", "npm") and _switch_off():
                say(f"{name}: not installed, because the package registries are switched "
                    f"off on What leaves your computer.")
                failed.append(name)
                _audit(who, step, "", "refused", device)
                continue
            say(f"Drawbridge is checking {name}.")
            verdict, version = check_step(step)
            if verdict.get("verdict") not in ("allow", "warn"):
                say(f"{name}: not installed. {verdict.get('line') or ''}".strip())
                failed.append(name)
                _audit(who, step, version, "refused", device)
                continue
            argv = installer_argv(step, version)
            if argv is None:
                say(f"{name}: this computer cannot install it this way. Install it yourself.")
                failed.append(name)
                _audit(who, step, version, "failed", device)
                continue
            say(f"Installing {name}" + (f" {version}." if version else "."))
            extra = {"DEBIAN_FRONTEND": "noninteractive"} if step["kind"] == "apt" else None
            got = _run_installer(argv, extra)
            if got.get("code") == 0:
                done.append(name)
                _record(step, version, who)
                _audit(who, step, version, "success", device)
                say(f"{name} is installed.")
                # Its command line programs, path and sha256, kept in the vault:
                # only a recorded, unchanged program may get a skill's key.
                try:
                    from puffpai.security import skill_programs
                    skill_programs.record_installed(step, version, plan.get("skill") or "", say)
                except Exception as exc:  # noqa: BLE001 - no record means no key
                    logger.info("deps: programs not recorded (%s)", type(exc).__name__)
            else:
                failed.append(name)
                _audit(who, step, version, "failed", device)
                why = _short_reason(got.get("output") or "")
                say(f"{name} did not install{': ' + why if why else '.'}")
    finally:
        _RUN_LOCK.release()
    try:
        from puffpai import agent_activity
        if done:
            agent_activity.record("toolsmith", f"Installed {', '.join(done)[:120]}")
        if failed:
            agent_activity.record("toolsmith", f"Could not install {', '.join(failed)[:120]}",
                                  status="error")
    except Exception:  # noqa: BLE001
        pass
    return {"ok": not failed, "lines": lines, "done": done, "failed": failed}


# ── the mcp package tool servers need (a build setup row) ─────────────────────
#
# requirements.txt declares ``mcp>=1.24,<2``; a computer that lacks it gets it
# from the build's setup row ("Install the mcp package tool servers need"),
# under her one Yes: the registry switch, Drawbridge's check, a binary wheel
# of a 1.x version from PyPI, into its OWN folder (never the shared tools
# folder skills install into), which mcp_client puts last on its import path.

MCP_SDK_MIN = (1, 24)
MCP_SDK_BELOW = 2


def mcp_sdk_dir() -> str:
    return tools_dir("mcp-sdk")


def _mcp_version_ok(version: str) -> bool:
    m = re.match(r"^(\d+)\.(\d+)", str(version or ""))
    if not m:
        return False
    major, minor = int(m.group(1)), int(m.group(2))
    return major < MCP_SDK_BELOW and (major, minor) >= MCP_SDK_MIN


def newest_mcp_1x(releases: Any) -> str:
    """The newest final release in requirements.txt's range (1.24 <= v < 2)
    that has a wheel not yanked, from PyPI's ``releases`` map; "" when none.
    Pre-releases (letters in the version) are never picked."""
    best: tuple = ()
    pick = ""
    for version, files in (releases or {}).items() if isinstance(releases, dict) else ():
        v = str(version)
        if not re.fullmatch(r"\d+(?:\.\d+){1,3}", v) or not _mcp_version_ok(v):
            continue
        if not any(isinstance(f, dict) and f.get("packagetype") == "bdist_wheel"
                   and not f.get("yanked") for f in files or []):
            continue
        key = tuple(int(x) for x in v.split("."))
        if key > best:
            best, pick = key, v
    return pick


def _pypi_releases(name: str) -> dict:
    """PyPI's ``releases`` map for *name* (the registry switch's own path)."""
    from puffpai.finding import net
    ans = net.get(f"https://{PYPI}/pypi/{urllib.parse.quote(name)}/json", hosts=[PYPI],
                  service=REGISTRY_SERVICE, cache_s=0)
    data = ans.json() if ans.ok else None
    rel = data.get("releases") if isinstance(data, dict) else None
    return rel if isinstance(rel, dict) else {}


def install_mcp_sdk(*, device: str = "", progress: Callable[[str], None] = lambda _l: None
                    ) -> dict[str, Any]:
    """Install the mcp package (only after her Yes on a list that showed its
    row). ``{"ok", "line"}``. Fails closed: registry switch off, Drawbridge
    not allowing it, or no 1.x version -> nothing installed."""
    step = {"kind": "pip", "name": "mcp"}
    if _switch_off():
        _audit("mcp-sdk", step, "", "refused", device)
        return {"ok": False, "line": "the mcp package was not installed, because the package "
                                     "registries are switched off on What leaves your "
                                     "computer"}
    if not _RUN_LOCK.acquire(timeout=5):
        return {"ok": False, "line": "another install is running; try again in a minute"}
    try:
        # requirements.txt keeps mcp on 1.x (2.x moves to httpx2 and more
        # packages): the newest 1.x with a wheel, never PyPI's "latest".
        try:
            wanted = newest_mcp_1x(_pypi_releases("mcp"))
        except Exception:  # noqa: BLE001 - registry unreadable: nothing installed
            wanted = ""
        if not wanted:
            _audit("mcp-sdk", step, "", "refused", device)
            return {"ok": False, "line": "PyPI has no 1.x release of the mcp package with a "
                                         "wheel BuildBot can install, so it was not installed"}
        step = {**step, "version": wanted}
        progress("Drawbridge is checking mcp.")
        verdict, version = check_step(step)
        if verdict.get("verdict") not in ("allow", "warn"):
            _audit("mcp-sdk", step, version, "refused", device)
            return {"ok": False, "line": f"the mcp package was not installed. "
                                         f"{verdict.get('line') or ''}".strip()}
        if not _mcp_version_ok(version):
            _audit("mcp-sdk", step, version, "refused", device)
            return {"ok": False, "line": f"the mcp package BuildBot is made for (1.x) is not "
                                         f"the one PyPI offers ({version or 'none'}), so it "
                                         f"was not installed"}
        argv = installer_argv(step, version, target=mcp_sdk_dir())
        if argv is None:
            return {"ok": False, "line": "this computer cannot install the mcp package"}
        os.makedirs(mcp_sdk_dir(), exist_ok=True)
        progress(f"Installing mcp {version}.")
        got = _run_installer(argv)
        if got.get("code") != 0:
            _audit("mcp-sdk", step, version, "failed", device)
            why = _short_reason(got.get("output") or "")
            return {"ok": False, "line": f"the mcp package did not install"
                                         f"{': ' + why if why else ''}"}
        _record(step, version, "mcp-sdk")
        _audit("mcp-sdk", step, version, "success", device)
        return {"ok": True, "line": f"mcp {version} is installed."}
    finally:
        _RUN_LOCK.release()


def _short_reason(output: str) -> str:
    try:
        from puffpai.brain_commands import hide_secrets
        output = hide_secrets(output)
    except Exception:  # noqa: BLE001
        output = ""
    last = [ln.strip() for ln in output.splitlines() if ln.strip()]
    for ln in reversed(last):
        if "error" in ln.lower():
            return skills_mod._one_line(ln, 200).replace("—", ",")  # noqa: SLF001
    return skills_mod._one_line(last[-1], 200) if last else ""  # noqa: SLF001


def _install_sibling(parent: str, name: str, say: Callable[[str], None]) -> bool:
    try:
        from puffpai.finding import install
        got = install.install_companion(parent, name, say)
    except Exception as exc:  # noqa: BLE001
        logger.info("deps: sibling skill not installed (%s)", type(exc).__name__)
        say(f"{name}: it could not be installed.")
        return False
    return bool(got.get("ok"))


# ── a program an app needs, under the build's one Yes ────────────────────────
#
# Docker is a dependency people need for now; if it is
# not there it is installed, with anything else an app needs. Windows: winget
# (Docker Desktop asks Windows for an admin yes once). Mac: Docker's own
# Docker Desktop download. Linux: Docker's own convenience script, which needs
# admin. A restart or an admin prompt is said plainly.

DOCKER_WAIT_S = 300
_DOCKER_DMG = "https://desktop.docker.com/mac/main/{arch}/Docker.dmg"
_DOCKER_SCRIPT = "https://get.docker.com"
#: What each install says up front (the setup row's words).
INSTALL_NOTES = {
    "docker": {"win32": "Windows asks you for an admin yes once. Docker Desktop may ask you "
                        "to restart the computer; restart, then press Check again.",
               "darwin": "BuildBot downloads Docker Desktop from Docker and copies it to your "
                         "Applications folder. The first start asks for your Mac password.",
               "linux": "This uses Docker's own install script and needs admin (root or sudo)."},
}
#: An install that failed is not tried again for this long (no repeat spam).
_FAILED: dict[str, tuple[float, str]] = {}
FAILED_TTL_S = 600


def _platform() -> str:
    return "win32" if _is_windows() else ("darwin" if sys.platform == "darwin" else "linux")


def install_note(name: str) -> str:
    return INSTALL_NOTES.get(name, {}).get(_platform(), "")


def _docker_path() -> Optional[str]:
    from puffpai.security import host_programs
    return host_programs.path("docker")


def can_install(name: str) -> bool:
    """Can BuildBot install *name* on this computer by itself?"""
    from puffpai.security import host_programs
    if name == "docker":
        if _is_windows():
            return bool(host_programs.path("winget"))
        if sys.platform == "darwin":
            return bool(host_programs.web_program("curl"))
        return bool(host_programs.web_program("curl")) and (_is_root() or _sudo_ok())
    return _system_step(name) is not None


def _sudo_ok() -> bool:
    if not os.path.isfile("/usr/bin/sudo"):
        return False
    return _run_installer(["/usr/bin/sudo", "-n", "true"]).get("code") == 0


def docker_answers() -> bool:
    """``docker info`` works (Docker is installed AND running)."""
    docker = _docker_path()
    if not docker:
        return False
    env = {"PATH": os.path.dirname(docker) + os.pathsep + os.environ.get("PATH", "")}
    if not _is_windows():
        env["DOCKER_CONFIG"] = os.path.join(os.path.expanduser("~"), ".docker")
    got = _run_installer([docker, "info", "--format", "{{.ServerVersion}}"], env)
    return got.get("code") == 0


def _start_docker() -> None:
    """Start Docker Desktop (never changes its settings), with no window."""
    if _is_windows():
        for var in ("ProgramFiles", "ProgramW6432"):
            base = os.environ.get(var) or ""
            exe = os.path.join(base, "Docker", "Docker", "Docker Desktop.exe")
            if base and os.path.isfile(exe):
                subprocess.Popen([exe], cwd=os.path.dirname(exe), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 **procflags.hidden(0x00000008))     # DETACHED_PROCESS
                return
    elif sys.platform == "darwin":
        if os.path.isfile("/usr/bin/open"):
            _run_installer(["/usr/bin/open", "-g", "-a", "Docker"])
    else:
        ctl = "/usr/bin/systemctl"
        if os.path.isfile(ctl):
            _run_installer(([ctl] if _is_root() else ["/usr/bin/sudo", "-n", ctl])
                           + ["start", "docker"])


#: Seams for the tests.
_sleep = time.sleep
_clock = time.monotonic


def ensure_docker_running(progress: Callable[[str], None] = lambda _l: None,
                          wait_s: Optional[float] = None) -> dict[str, Any]:
    """Docker answers, starting Docker Desktop and waiting for it when it is
    installed but not running. ``{"ok", "line"}``."""
    if not _docker_path():
        return {"ok": False, "line": "Docker is not on this computer yet."}
    if docker_answers():
        return {"ok": True, "line": "Docker is running."}
    progress("Starting Docker (the first start can take a few minutes).")
    try:
        _start_docker()
    except OSError as exc:
        return {"ok": False, "line": f"Docker could not be started ({type(exc).__name__}). "
                                     "Start Docker Desktop yourself, then press Check again."}
    end = _clock() + (DOCKER_WAIT_S if wait_s is None else wait_s)
    while _clock() < end:
        _sleep(5)
        if docker_answers():
            return {"ok": True, "line": "Docker is running."}
    return {"ok": False, "line": "Docker did not start in time. Open Docker Desktop yourself "
                                 "(it may ask you to accept its terms or to restart), then "
                                 "press Check again."}


def _download(url: str, dest: str) -> Optional[str]:
    """curl one of Docker's own files to *dest* (behind the Bridge's app
    downloads switch). The reason it did not work, or None."""
    from puffpai.security import host_programs
    try:
        from puffpai.security import outside_switches
        if outside_switches.is_off("code_download"):
            return "app downloads are switched off on What leaves your computer"
    except Exception:  # noqa: BLE001 - cannot tell: off
        return "app downloads are switched off on What leaves your computer"
    curl = host_programs.web_program("curl")
    if not curl:
        return "this computer has no curl"
    got = _run_installer([curl, "-fsSL", "--proto", "=https", "-o", dest, url])
    try:
        from puffpai.security import egress_ledger
        egress_ledger.record("code_download", host=urllib.parse.urlsplit(url).hostname or "",
                             outcome="done" if got.get("code") == 0 else "failed",
                             method="GET", path="", code="", what="install_program")
    except Exception:  # noqa: BLE001
        pass
    if got.get("code") == 0:
        return None
    return _short_reason(got.get("output") or "") or "the download did not work"


def _install_docker(say: Callable[[str], None]) -> dict[str, Any]:
    import tempfile
    if _is_windows():
        argv = installer_argv({"kind": "winget", "name": "Docker.DockerDesktop"}, "")
        if not argv:
            return {"ok": False, "line": "Windows' own installer (winget) is not on this "
                                         "computer. Install Docker Desktop yourself from "
                                         + SYSTEM_PROGRAM_LINKS["docker"]}
        say("Installing Docker Desktop with winget. Windows asks you for an admin yes.")
        got = _run_installer(argv)
        out = str(got.get("output") or "")
        if got.get("code") != 0 and not _docker_path():
            return {"ok": False, "line": "Docker Desktop did not install: "
                    + (_short_reason(out) or "winget stopped") + "."}
        restart = bool(re.search(r"restart|reboot", out, re.I))
        return {"ok": True, "restart": restart,
                "line": "Docker Desktop is installed." + (
                    " Windows asks for a restart to finish; restart, then press Check again."
                    if restart else "")}
    work = tempfile.mkdtemp(prefix="bb-docker-")
    if sys.platform == "darwin":
        import platform
        arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"
        dmg = os.path.join(work, "Docker.dmg")
        say("Downloading Docker Desktop from Docker.")
        why = _download(_DOCKER_DMG.format(arch=arch), dmg)
        if why:
            return {"ok": False, "line": f"Docker Desktop was not downloaded: {why}."}
        mnt = os.path.join(work, "mnt")
        got = _run_installer(["/usr/bin/hdiutil", "attach", "-nobrowse", "-quiet",
                              "-mountpoint", mnt, dmg])
        if got.get("code") != 0:
            return {"ok": False, "line": "Docker Desktop's download could not be opened."}
        try:
            got = _run_installer(["/bin/cp", "-R", os.path.join(mnt, "Docker.app"),
                                  "/Applications/"])
        finally:
            _run_installer(["/usr/bin/hdiutil", "detach", "-quiet", mnt])
        if got.get("code") != 0:
            return {"ok": False, "line": "Docker Desktop could not be copied to Applications "
                                         "(it needs an admin account). Open the download and "
                                         "drag Docker to Applications yourself."}
        return {"ok": True, "line": "Docker Desktop is in your Applications folder."}
    script = os.path.join(work, "get-docker.sh")
    say("Downloading Docker's own install script (it needs admin).")
    why = _download(_DOCKER_SCRIPT, script)
    if why:
        return {"ok": False, "line": f"Docker's install script was not downloaded: {why}."}
    argv = ["/bin/sh", script] if _is_root() else ["/usr/bin/sudo", "-n", "/bin/sh", script]
    got = _run_installer(argv)
    if got.get("code") != 0:
        return {"ok": False, "line": "Docker did not install (it needs admin). Run this "
                                     "yourself, then press Check again: curl -fsSL "
                                     "https://get.docker.com | sudo sh"}
    return {"ok": True, "line": "Docker is installed."}


def install_program(name: str, progress: Callable[[str], None] = lambda _l: None,
                    *, device: str = "") -> dict[str, Any]:
    """Install one program an app needs (``docker``, ``node``, ``git``,
    ``python``, ``php``), the way this computer installs it. Only names on
    :data:`SYSTEM_PROGRAMS`. ``{"ok", "line", "restart"?}``; never raises.
    Docker is also started and waited for until it answers. One that did
    not install is not tried again for :data:`FAILED_TTL_S` (same answer)."""
    if name not in SYSTEM_PROGRAMS:
        return {"ok": False, "line": f"BuildBot does not install {name}."}
    words = SYSTEM_PROGRAMS[name].get("words", name)
    failed = _FAILED.get(name)
    if failed and _clock() - failed[0] < FAILED_TTL_S:
        return {"ok": False, "line": failed[1]}
    with _RUN_LOCK:
        try:
            if name == "docker":
                got = {"ok": True, "line": ""} if _docker_path() else _install_docker(progress)
                if got.get("ok") and not got.get("restart"):
                    run = ensure_docker_running(progress)
                    if not run.get("ok"):
                        got = run
            else:
                step = _system_step(name)
                argv = installer_argv(step, "") if step else None
                if not argv:
                    url = SYSTEM_PROGRAM_LINKS.get(name, "")
                    got = {"ok": False, "line": f"BuildBot cannot install {words} on this "
                                                f"computer. Install it yourself"
                                                + (f" ({url})" if url else "") + "."}
                else:
                    progress(f"Installing {words}.")
                    ran = _run_installer(argv, {"DEBIAN_FRONTEND": "noninteractive"}
                                         if step["kind"] == "apt" else None)
                    ok = ran.get("code") == 0
                    got = {"ok": ok, "line": f"{words} is installed." if ok else
                           f"{words} did not install: "
                           + (_short_reason(ran.get("output") or "") or "it stopped") + "."}
        except Exception as exc:  # noqa: BLE001 - said plainly, never raised
            got = {"ok": False, "line": f"{words} did not install ({type(exc).__name__})."}
    _audit("app", {"kind": "program", "name": name}, "",
           "success" if got.get("ok") else "failed", device)
    if got.get("ok"):
        _FAILED.pop(name, None)
    else:
        _FAILED[name] = (_clock(), str(got.get("line") or ""))
    if got.get("line"):
        progress(str(got["line"]))
    return got


__all__ = ["BLOCK_CODES", "FORBIDDEN_ENV", "KIND", "MODULE_PACKAGES", "SYSTEM_PROGRAMS",
           "check_step", "command_paths", "connect", "env_name_ok", "get_plan", "get_url_for",
           "has_package", "has_program", "installer_argv", "key_names", "keys_in_text", "make_plan",
           "missing", "missing_from_output", "package_rows", "plan_line", "public_plan",
           "publisher", "read", "read_skill", "run_plan", "sibling_find", "siblings_in",
           "siblings_offered"]
