"""What kind of project an unpacked download is, and the facts Drawbridge reads.

:func:`scan_tree` walks the unpacked tree once (never following a link or a
reparse point, never running or importing anything) and records plain facts:
code bytes, the entry page and what it loads, lock files, install scripts,
untouched framework starter files, hidden-connection code, a page that
hides encoded code and runs it (:mod:`hidden_loader`), the README's
opening and its feature lines. :func:`classify` turns those facts into one
mode:

``static_web``     an ``index.html`` at the top, in ``docs/``, ``dist/`` or
                   ``public/`` that loads only files that are there (plain
                   JavaScript) or ``https`` CDN files: nothing to build.
``web_build``      a ``package.json`` with a ``build`` script and a lock file.
``cli_tool``       a command package (normally arrives as apply kind
                   ``cli_tool``).
``data_template``  data files (csv, tsv, json) dominate.
``picture_set``    raster pictures dominate.
``other``          server apps, MCP servers, libraries: "look at it yourself".

Everything here is goal-agnostic: no project, topic or goal is named.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import os
import re
import stat
from typing import Any

from puffpai.integrate import hidden_loader

MODES = ("static_web", "web_build", "cli_tool", "data_template", "picture_set", "other")

#: Files whose bytes count as code for the scaffold check.
CODE_EXTS = frozenset({".js", ".mjs", ".ts", ".tsx", ".jsx", ".py", ".go", ".rs",
                       ".java", ".kt", ".rb", ".php", ".cs", ".vue", ".svelte"})
_NEEDS_BUILD_EXTS = frozenset({".ts", ".tsx", ".jsx", ".vue", ".svelte"})
_DATA_EXTS = frozenset({".csv", ".tsv", ".json"})
_RASTER_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})
_TEXT_EXTS = CODE_EXTS | {".html", ".htm", ".md", ".txt", ".json", ".yml", ".yaml",
                          ".toml", ".sh", ".bash", ".ps1", ".bat", ".cmd", ".cfg",
                          ".ini", ".cjs", ".env", ""}
_SKIP_DIRS = frozenset({".git", "node_modules"})
_LOCKFILES = ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock",
              "pnpm-lock.yaml", "bun.lockb", "bun.lock")
_SERVER_MARKERS = ("dockerfile", "docker-compose.yml", "docker-compose.yaml",
                   "compose.yml", "compose.yaml", "manage.py", "go.mod", "gemfile",
                   "composer.json", "pom.xml", "build.gradle", "cargo.toml",
                   "procfile", "artisan", "fly.toml")
_INSTALL_SCRIPT_KEYS = ("preinstall", "install", "postinstall", "prepare", "prepublish")
_INSTALL_FILES = ("setup.py", "install.sh", "install.ps1", "install.bat")

MAX_TEXT_READ = 1024 * 1024
MAX_WALK_FILES = 20000

#: Untouched framework starter files: (path suffix, text that is still there).
STARTERS = (
    ("app/page.tsx", "Get started by editing"),
    ("app/page.jsx", "Get started by editing"),
    ("app/page.js", "Get started by editing"),
    ("pages/index.tsx", "Get started by editing"),
    ("pages/index.js", "Get started by editing"),
    ("src/App.tsx", "Edit <code>src/App"),
    ("src/App.jsx", "Edit <code>src/App"),
    ("src/App.js", "Edit <code>src/App"),
)

_SCRIPT_TAG_RE = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.I | re.S)
_SRC_RE = re.compile(r"""\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I)
#: Real tunnel tools, USED in code: a way in from the internet past her
#: router. Only use counts (owner, 2026-10-12: no false alarms): importing
#: or starting ngrok, localtunnel or cloudflared's tunnel, or an ssh tunnel
#: to a public tunnel host. A link to a tool's page (GrowthBook's webhook
#: screen links to ngrok's npm page), a "curl | sh" install step, or a README
#: that explains a tunnel is not a hidden connection.
_TUNNEL_RE = re.compile(
    r"""(?:require\s*\(|import\s*\(|from\s+|import\s+)\s*['"](?:@ngrok/ngrok|ngrok|"""
    r"""pyngrok(?:\.ngrok)?|localtunnel)['"]"""
    r"|\bfrom\s+pyngrok\b|\bimport\s+pyngrok\b"
    r"|\bngrok\s+(?:http|tcp|tls|start)\b|\bngrok\.(?:connect|forward)\s*\("
    r"|\blocaltunnel\s*\(|\blt\s+--port\b"
    r"|\bcloudflared(?:\.exe)?\s+(?:tunnel|access\s+tcp)\b"
    r"|\bssh\b[^\n]{0,80}\s-R\s[^\n]{0,80}(?:serveo\.net|localhost\.run|bore\.pub)"
    r"|\bbore\s+local\b", re.I)
_TUNNEL_NAME_RE = _TUNNEL_RE


def _tunnel_in(rel: str, text: str) -> bool:
    name = rel.lower().rsplit("/", 1)[-1]
    if name.endswith((".md", ".txt", ".rst", ".adoc", ".markdown")):
        return False
    return bool(_TUNNEL_RE.search(text))


_AI_NOTICE_RE = re.compile(
    r"\bai[- ](?:generated|written|maintained)\b|"
    r"\b(?:generated|written|maintained|built|created|coded)\s+(?:entirely\s+|fully\s+|"
    r"mostly\s+|completely\s+)?(?:by|with|using)\s+(?:an?\s+)?(?:ai|llm|chatgpt|gpt[-\w.]*|"
    r"claude|copilot|gemini|cursor|codex)\b|"
    r"\bnot\s+(?:been\s+)?(?:audited|reviewed\s+by\s+a\s+(?:human|person))\b",
    re.I)
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*)$")
_FEATURE_HEADING_RE = re.compile(r"feature|what it does|highlights|what you can do", re.I)
_BULLET_RE = re.compile(r"^\s{0,6}(?:[-*+]|\d{1,3}[.)])\s+\S")
_TABLE_ROW_RE = re.compile(r"^\s*\|(?!\s*:?-{2,}).+\|\s*$")


def _is_reparse(st: os.stat_result) -> bool:
    attrs = getattr(st, "st_file_attributes", 0) or 0
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _read(path: str, limit: int = MAX_TEXT_READ) -> str:
    try:
        with open(path, "rb") as fh:
            return fh.read(limit).decode("utf-8", "replace")
    except OSError:
        return ""


def _walk(root: str):
    """Yield ``(rel_posix_path, abs_path, size)`` for regular files only."""
    count = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        keep = []
        for d in dirnames:
            full = os.path.join(dirpath, d)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if d in _SKIP_DIRS or stat.S_ISLNK(st.st_mode) or _is_reparse(st):
                continue
            keep.append(d)
        dirnames[:] = sorted(keep)
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode) or _is_reparse(st):
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            yield rel, full, st.st_size
            count += 1
            if count >= MAX_WALK_FILES:
                return


def readme_feature_lines(text: str) -> list[str]:
    """List items and table rows under a features heading (all list items
    when the README has no such heading), at most 60."""
    lines = str(text or "").splitlines()
    under, found, all_bullets = False, [], []
    header_seen = False
    for line in lines:
        m = _HEADING_RE.match(line)
        if m:
            under = bool(_FEATURE_HEADING_RE.search(m.group(1)))
            header_seen = False
            continue
        is_item = bool(_BULLET_RE.match(line))
        is_row = bool(_TABLE_ROW_RE.match(line))
        if is_row and not header_seen:
            header_seen = True  # the table's header row is not a feature
            continue
        if is_item:
            all_bullets.append(line.strip())
        if under and (is_item or is_row):
            found.append(line.strip())
    return (found or all_bullets)[:60]


def _entry_html(files: dict[str, int]) -> str:
    for folder in ("", "docs/", "dist/", "public/"):
        if f"{folder}index.html" in files:
            return f"{folder}index.html"
    return ""


def _script_facts(entry: str, html: str, files: dict[str, int]) -> dict[str, Any]:
    base = entry.rsplit("/", 1)[0] + "/" if "/" in entry else ""
    srcs, missing, needs_build, cdn, plain_http = [], [], False, [], False
    for attrs, _body in _SCRIPT_TAG_RE.findall(html):
        m = _SRC_RE.search(attrs)
        if not m:
            continue
        src = next(g for g in m.groups() if g is not None).strip()
        srcs.append(src)
        low = src.lower()
        if low.startswith("https://") or low.startswith("//"):
            cdn.append(src[:200])
            continue
        if re.match(r"^[a-z][a-z0-9+.-]*:", low):
            plain_http = True  # http:, data:, javascript: ...
            continue
        path = src.split("?", 1)[0].split("#", 1)[0]
        rel = path.lstrip("/") if path.startswith("/") else (base + path)
        rel = os.path.normpath(rel).replace(os.sep, "/")
        ext = os.path.splitext(rel)[1].lower()
        if ext in _NEEDS_BUILD_EXTS:
            needs_build = True
        if rel not in files and path.lstrip("/") not in files:
            missing.append(src[:200])
    return {"scripts": len(srcs), "cdn": cdn[:20], "missing": missing[:20],
            "needs_build": needs_build or bool(missing), "odd_scheme": plain_http}


def scan_tree(root: str) -> dict[str, Any]:
    """Plain facts about the unpacked tree at *root* (see module doc)."""
    files: dict[str, int] = {}
    code_bytes = 0
    code_files = 0
    data_files = 0
    raster_files = 0
    other_files = 0
    total = 0
    tunnel = False
    hidden = ""
    starters: list[str] = []
    for rel, full, size in _walk(root):
        files[rel] = size
        total += size
        name = rel.rsplit("/", 1)[-1]
        ext = os.path.splitext(name)[1].lower()
        low = name.lower()
        if ext in CODE_EXTS:
            code_bytes += size
            code_files += 1
        elif ext in _DATA_EXTS and not (low.startswith(".") or "lock" in low
                                        or "config" in low or low in (
                                            "package.json", "composer.json",
                                            "manifest.json", "tsconfig.json")
                                        or low.startswith("tsconfig")):
            data_files += 1
        elif ext in _RASTER_EXTS:
            raster_files += 1
        elif not (low.startswith(".") or low.startswith("readme")
                  or low.startswith("licen") or low.startswith("copying")
                  or ext in (".md", ".txt")):
            other_files += 1
        text = None
        if ext in (".html", ".htm"):
            text = _read(full)
            code_bytes += sum(len(body.encode("utf-8", "replace"))
                              for attrs, body in _SCRIPT_TAG_RE.findall(text)
                              if not _SRC_RE.search(attrs))
        if ext in _TEXT_EXTS or low in ("dockerfile", "makefile"):
            text = text if text is not None else _read(full)
            if not tunnel and _tunnel_in(rel, text):
                tunnel = True
            if not hidden and hidden_loader.in_file(rel, text):
                hidden = rel
        for suffix, marker in STARTERS:
            if rel == suffix or rel.endswith("/" + suffix):
                text = text if text is not None else _read(full)
                if marker in text and rel not in starters:
                    starters.append(rel)
    top = {rel.lower(): rel for rel in files if "/" not in rel}
    readme_name = next((top[k] for k in sorted(top) if k.startswith("readme")), "")
    readme = _read(os.path.join(root, readme_name), 64 * 1024) if readme_name else ""
    pkg: dict[str, Any] = {}
    if "package.json" in files:
        try:
            parsed = json.loads(_read(os.path.join(root, "package.json"), 512 * 1024))
            pkg = parsed if isinstance(parsed, dict) else {}
        except ValueError:
            pkg = {}
    scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
    entry = _entry_html(files)
    entry_facts = (_script_facts(entry, _read(os.path.join(root, entry)), files)
                   if entry else {})
    install_scripts = sorted(
        [f"package.json:{k}" for k in _INSTALL_SCRIPT_KEYS if k in scripts]
        + [f for f in _INSTALL_FILES if f in top.values()])
    pyproject = _read(os.path.join(root, "pyproject.toml"), 256 * 1024) \
        if "pyproject.toml" in files else ""
    return {
        "files": len(files), "bytes": total, "code_bytes": code_bytes,
        "code_files": code_files, "data_files": data_files,
        "raster_files": raster_files, "other_files": other_files,
        "entry_html": entry, "entry": entry_facts,
        "has_build_script": isinstance(scripts.get("build"), str),
        "lockfile": next((f for f in _LOCKFILES if f in files), ""),
        "package_bin": bool(pkg.get("bin")),
        "python_scripts": bool(re.search(r"^\[(?:project\.scripts|tool\.poetry\.scripts)\]",
                                         pyproject, re.M)),
        "server_markers": sorted(m for m in _SERVER_MARKERS if m in top)
        + sorted(rel for rel in top.values() if rel.lower().endswith((".csproj", ".sln"))),
        "install_scripts": install_scripts,
        "starters": starters,
        "tunnel": tunnel,
        "hidden_loader": hidden,
        "readme_file": readme_name,
        "readme_head": readme[:2048],
        "readme_feature_lines": len(readme_feature_lines(readme)),
        "ai_notice": bool(_AI_NOTICE_RE.search(readme)),
    }


def classify(facts: dict[str, Any]) -> str:
    """One mode from :func:`scan_tree` facts."""
    entry = facts.get("entry") or {}
    if facts.get("entry_html") and not entry.get("needs_build") \
            and not entry.get("odd_scheme") and not facts.get("has_build_script"):
        return "static_web"
    if facts.get("has_build_script") and facts.get("lockfile"):
        return "web_build"
    if facts.get("entry_html") and not entry.get("needs_build") \
            and not entry.get("odd_scheme") and not facts.get("lockfile"):
        # A build script with no lock file cannot be rebuilt from a recorded
        # lock; a page that runs as it is still opens as it is.
        return "static_web"
    if facts.get("server_markers"):
        return "other"
    if facts.get("package_bin") or facts.get("python_scripts"):
        return "cli_tool"
    code = int(facts.get("code_files") or 0)
    data = int(facts.get("data_files") or 0)
    # One data file and no code at all is a data project too.
    if data >= 1 and (code == 0 or data >= 2 * code):
        return "data_template"
    pics = int(facts.get("raster_files") or 0)
    rest = int(facts.get("other_files") or 0) + data
    if pics >= 3 and code == 0 and pics >= 4 * max(1, rest):
        return "picture_set"
    return "other"


def classify_tree(root: str) -> tuple[str, dict[str, Any]]:
    facts = scan_tree(root)
    return classify(facts), facts


__all__ = ["CODE_EXTS", "MODES", "STARTERS", "classify", "classify_tree",
           "readme_feature_lines", "scan_tree"]
