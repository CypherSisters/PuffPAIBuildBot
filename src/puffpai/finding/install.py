"""``install_thing``: show what a find is, ask, install it pinned, undo.

Copied from OpenClaw's ``skills verify`` then ``install`` and Hermes's
``tools/skills_hub_clawhub.py`` / ``skills_hub_skillssh.py``:

1. :func:`preview` says what it is, where it comes from, what it runs (its
   scripts, the commands it shows, the websites it names) and the shelf's own
   safety check. The caller asks her yes.
2. :func:`install_thing` then installs, by type:

   * a **skill** into ``<state>/skills/<name>/`` (ClawHub: its ZIP at the
     version it checked; skills.sh and the Hermes list: the files of its
     folder at one GitHub commit), with the source, version or commit and every
     file's fingerprint written to ``<state>/skills/.lock.json``. The
     sibling skills its text names ("use the `runware-run` skill") come with
     it from the same publisher, each through the same install and checks
     (:func:`install_companion`). The same skill installed again (same
     shelf, publisher and name) is updated in its own folder
     (:func:`update_skill`), so a key kept for it stays;
   * an **MCP tool server** onto the tool list, through
     ``puffpai.mcp_client.add_server``;
   * an **app or code project** through the existing integrate path
     (browser apps and data), which downloads it at one pinned commit, checks
     it and adds it, or holds it for a yes;
   * a **book** from the free libraries through the knowledge part
     (``puffpai.knowledge.find_more``): a text book is ordered and fetched in
     the background, a Kiwix file is downloaded and checked; either is then
     learned, and becomes one of the bot's things;
   * anything else: a plain "look at it yourself" with the reason.

3. Every install is one entry in ``<state>/finding/installed.json``, and
   :func:`undo` takes it away again.

Only one thing is refused: what the shelf itself marks malicious.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import shutil
import threading
import time
import types
import urllib.parse
import zipfile
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from puffpai import skills as skills_mod
from puffpai.finding import find as find_mod
from puffpai.finding import net, sites
from puffpai.finding import wording as W
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

MAX_FILES = 200
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
ZIP_MAX_BYTES = 25 * 1024 * 1024
TREE_MAX_BYTES = 8 * 1024 * 1024
SCRIPT_EXT = (".py", ".sh", ".bash", ".zsh", ".js", ".mjs", ".cjs", ".ts", ".ps1", ".bat",
              ".cmd", ".rb", ".pl", ".php", ".exe", ".bin", ".dart", ".go", ".rs", ".java",
              ".kt", ".swift", ".lua", ".r", ".jl", ".c", ".cc", ".cpp", ".cs", ".ex", ".exs",
              ".scala", ".groovy", ".nim", ".zig", ".deno", ".jar", ".wasm", ".applescript")
#: Files ClawHub writes into every download itself, so its check lists other
#: bytes for them (seen: google-finance's _meta.json).
CLAWHUB_OWN_FILES = ("_meta.json", "skill-card.md")
#: A skill that only works inside OpenClaw with one of its plugins or paired
#: services (ClawLink, ...): BuildBot cannot give it that.
_NEEDS_OPENCLAW_RE = re.compile(
    r"(?i)(clawlink|install the \S+ plugin in openclaw|openclaw plugins? install|"
    r"plugin in openclaw|requires? (the )?openclaw|pair(ing)? with clawlink)")
NEEDS_OPENCLAW_WHY = ("it only works inside OpenClaw with one of its own plugins or paid "
                      "services, which BuildBot does not have.")
_BOX_CHARS_RE = re.compile(r"[\u2500-\u257f\u2580-\u259f]")
_COMMAND_START_RE = re.compile(r"^[A-Za-z0-9_.~/\\-][\w.@/\\:+-]*(\s|$)")
_ENTRY_RE = re.compile(r"^t_[0-9a-f]{10}$")
_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── the entries she can undo ────────────────────────────────────────────────

def _entries_path() -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), "finding", "installed.json")


def entries() -> list[dict]:
    try:
        with open(_entries_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_entries(rows: list[dict]) -> None:
    path = _entries_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=1)
    os.replace(tmp, path)


def _add_entry(entry: dict) -> dict:
    entry = {"entry": "t_" + secrets.token_hex(5), "installed_at": _now(), **entry}
    # The capability it was found for (finding.capabilities) goes with it.
    found = find_mod.recall(entry.get("id")) or {}
    caps = [str(c) for c in found.get("caps") or [] if isinstance(c, str)]         or ([str(found["cap"])] if found.get("cap") else [])
    if caps and "caps" not in entry:
        entry["caps"] = caps
    with _LOCK:
        rows = entries()
        rows.append(entry)
        _save_entries(rows)
    try:
        # The bot just learned something: its personality's intro says so.
        from puffpai import persona
        persona.refresh_soon()
    except Exception:  # noqa: BLE001
        pass
    return entry


def tag_capability(entry_id: Any, cap: Any) -> None:
    """Add one more capability an install covers (the same thing found again
    for another of the bot's jobs)."""
    if not cap:
        return
    with _LOCK:
        rows = entries()
        for e in rows:
            if e.get("entry") == entry_id:
                caps = e.get("caps") if isinstance(e.get("caps"), list) else []
                if cap in caps:
                    return
                e["caps"] = [*caps, str(cap)]
                _save_entries(rows)
                return


def already(find_id: Any) -> Optional[dict]:
    """The install of this same find that is still there, or None. A skill
    counts only while its folder is there."""
    fid = str(find_id or "")
    for e in reversed(entries()):
        if e.get("id") != fid:
            continue
        how = e.get("undo") or {}
        if how.get("kind") == "skill":
            folder = str(how.get("folder") or "")
            if not (skills_mod.safe_name(folder)
                    and os.path.isdir(os.path.join(skills_mod.skills_dir(), folder))):
                continue
        return e
    return None


def _real_name(entry: dict) -> str:
    """The name its tile and page use (an app renamed when it was added), or ""."""
    try:
        from puffpai import things
        tid = things.id_for_install(entry.get("undo"))
        return str((things.get(tid) or {}).get("name") or "") if tid else ""
    except Exception:  # noqa: BLE001 - the name it was found under is kept
        return ""


def installed() -> list[dict]:
    """What was installed from finds, newest first, as she sees it: by the
    same name as its tile."""
    out = []
    for e in reversed(entries()):
        row = {k: e.get(k) for k in ("entry", "id", "name", "type", "type_words", "from",
                                     "pinned", "installed_at", "line")}
        name, was = _real_name(e), str(row.get("name") or "")
        if name and was and name != was:
            row["line"] = str(row.get("line") or "").replace(was, name, 1)
            row["name"] = name
        out.append(row)
    return out


# ── reading a skill's text for "what it runs" ───────────────────────────────

_URL_RE = re.compile(r"https?://([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_FENCE_RE = re.compile(r"```(bash|sh|shell|console|zsh|powershell|ps1|cmd)?\s*\n(.*?)```",
                       re.S | re.I)


def _looks_like_command(line: str) -> bool:
    """A line that reads like a command, not a drawing or a sentence."""
    if not line or line.startswith("#") or _BOX_CHARS_RE.search(line):
        return False
    if not _COMMAND_START_RE.match(line):
        return False
    letters = sum(c.isalnum() for c in line)
    return letters >= max(2, len(line) // 3)


def what_it_runs(skill_md: str, files: list[str]) -> dict:
    """``{"scripts", "commands", "websites"}`` named by a skill (read, never run)."""
    scripts = [f for f in files if f.lower().endswith(SCRIPT_EXT)][:20]
    commands: list[str] = []
    for lang, block in _FENCE_RE.findall(skill_md or ""):
        for line in block.splitlines():
            line = line.strip().lstrip("$> ").strip()
            if _looks_like_command(line) and line not in commands:
                commands.append(line[:160])
            if len(commands) >= 8:
                break
    hosts = []
    for h in _URL_RE.findall(skill_md or ""):
        h = h.lower()
        if h not in hosts:
            hosts.append(h)
    return {"scripts": scripts, "commands": commands, "websites": hosts[:12]}


def runs_lines(runs: dict, files: list[str]) -> list[str]:
    out = []
    if files:
        shown = ", ".join(files[:8]) + (f" and {len(files) - 8} more" if len(files) > 8 else "")
        out.append(f"Files: {shown}.")
    if runs.get("scripts"):
        out.append("Scripts it holds: " + ", ".join(runs["scripts"][:8]) + ". BuildBot asks "
                   "you before running any command.")
    else:
        out.append("It holds no scripts: it is instructions your bot reads.")
    if runs.get("commands"):
        out.append("Commands it shows: " + "; ".join(runs["commands"][:5]) + ".")
    if runs.get("websites"):
        out.append("Websites it names: " + ", ".join(runs["websites"][:8]) + ".")
    return out


# ── getting a skill's files ────────────────────────────────────────────────

def _gh_commit(owner_repo: str, service: str) -> Optional[str]:
    url = f"https://{sites.GITHUB_API}/repos/{owner_repo}/commits/HEAD"
    ans = net.get(url, hosts=[sites.GITHUB_API], service=service, max_bytes=4096,
                  accept="application/vnd.github.sha", cache_s=600)
    sha = (ans.get("body") or b"").decode("ascii", errors="replace").strip() if ans.ok else ""
    return sha if re.fullmatch(r"[0-9a-f]{40}", sha) else None


def _gh_tree(owner_repo: str, sha: str, service: str) -> list[dict]:
    url = f"https://{sites.GITHUB_API}/repos/{owner_repo}/git/trees/{sha}?recursive=1"
    ans = net.get(url, hosts=[sites.GITHUB_API], service=service, max_bytes=TREE_MAX_BYTES,
                  accept="application/vnd.github+json")
    data = ans.json() if ans.ok else None
    return [t for t in (data or {}).get("tree") or [] if isinstance(t, dict)]


def skill_folder_in(tree: list[dict], skill: str, hint: str = "") -> Optional[str]:
    """The folder that holds the skill's ``SKILL.md`` in a project ("" = top)."""
    paths = [t.get("path") for t in tree if t.get("type") == "blob"
             and str(t.get("path") or "").endswith("SKILL.md")]
    folders = [p[:-len("SKILL.md")].rstrip("/") for p in paths if isinstance(p, str)]
    if hint and hint.strip("/") in folders:
        return hint.strip("/")
    for want in (f"skills/{skill}", skill, f".claude/skills/{skill}", f".agents/skills/{skill}"):
        if want in folders:
            return want
    for f in folders:
        if f.rsplit("/", 1)[-1] == skill:
            return f
    if "" in folders and len(folders) == 1:
        return ""
    return None


def github_skill(owner_repo: str, skill: str, *, service: str, hint: str = "",
                 sha: Optional[str] = None) -> dict:
    """``{"ok", "files": {path: bytes}, "commit", "folder", "why"}`` for a skill
    kept in a GitHub project, every file read at one commit."""
    if not re.fullmatch(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}", owner_repo):
        return {"ok": False, "why": "that is not a GitHub project name"}
    sha = sha or _gh_commit(owner_repo, service)
    if not sha:
        return {"ok": False, "why": "GitHub did not say which version is current"}
    tree = _gh_tree(owner_repo, sha, service)
    folder = skill_folder_in(tree, skill, hint)
    if folder is None:
        return {"ok": False, "why": "its SKILL.md was not found in the project"}
    prefix = folder + "/" if folder else ""
    blobs = [t for t in tree if t.get("type") == "blob"
             and str(t.get("path") or "").startswith(prefix)]
    if not folder:   # a skill at the top of a project: only its own text files
        blobs = [t for t in blobs if "/" not in t["path"]]
    files: dict[str, bytes] = {}
    total = 0
    for t in blobs[:MAX_FILES]:
        rel = t["path"][len(prefix):]
        size = int(t.get("size") or 0)
        if size > MAX_FILE_BYTES or total + size > MAX_TOTAL_BYTES:
            continue
        url = (f"https://{sites.GITHUB_RAW}/{owner_repo}/{sha}/"
               + urllib.parse.quote(t["path"], safe="/"))
        ans = net.get(url, hosts=[sites.GITHUB_RAW], service=service,
                      max_bytes=MAX_FILE_BYTES, cache_s=0)
        if not ans.ok:
            if rel == "SKILL.md":
                return {"ok": False, "why": "its SKILL.md could not be read"}
            continue
        files[rel] = ans["body"]
        total += len(ans["body"])
    if "SKILL.md" not in files:
        return {"ok": False, "why": "its SKILL.md could not be read"}
    return {"ok": True, "files": files, "commit": sha, "folder": folder}


def _clawhub_ref(find_id: str) -> tuple[str, str]:
    ref = find_id.split(":", 1)[1]
    owner, _, slug = ref.rpartition("/")
    return owner, slug


def clawhub_skill(slug: str, owner: str, version: str) -> dict:
    """``{"ok", "files", "zip_sha256", "why"}``: the skill's ZIP from ClawHub at
    *version*. A GitHub hand-off answer is followed to the named commit."""
    params = {"slug": slug}
    if version:
        params["version"] = version
    if owner:
        params["ownerHandle"] = owner
    url = f"https://{sites.CLAWHUB}/api/v1/download?" + urllib.parse.urlencode(params)
    ans = net.get(url, hosts=[sites.CLAWHUB], service="shelf_clawhub",
                  max_bytes=ZIP_MAX_BYTES, cache_s=0)
    if ans.get("status") == 403:
        return {"ok": False, "malicious": True, "why": "ClawHub refused to hand it out"}
    if not ans.ok:
        return {"ok": False, "why": "ClawHub did not hand it out"}
    body = ans["body"]
    if body[:2] != b"PK":
        handoff = ans.json() if body[:1] == b"{" else None
        if isinstance(handoff, dict) and handoff.get("repo") and handoff.get("commit"):
            got = github_skill(str(handoff["repo"]), slug, service="shelf_clawhub",
                               hint=str(handoff.get("path") or ""),
                               sha=str(handoff["commit"]))
            if got.get("ok"):
                got["zip_sha256"] = ""
            return got
        return {"ok": False, "why": "ClawHub's answer was not a skill"}
    files = unzip(body)
    if not files.get("ok"):
        return files
    files["zip_sha256"] = hashlib.sha256(body).hexdigest()
    return files


def unzip(body: bytes) -> dict:
    """The files of a skill ZIP, checked name by name, read in memory."""
    from puffpai.security import safe_archive
    try:
        zf = zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile:
        return {"ok": False, "why": "the download was not a ZIP file"}
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > MAX_FILES:
        return {"ok": False, "why": "it holds too many files"}
    names = [i.filename for i in infos]
    top = names[0].split("/", 1)[0] + "/" if names and all(
        "/" in n and n.split("/", 1)[0] == names[0].split("/", 1)[0] for n in names) else ""
    files: dict[str, bytes] = {}
    total = 0
    for info in infos:
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            continue                       # a link: never unpacked
        if safe_archive.check_member_name(info.filename):
            return {"ok": False, "why": "a file name in it is not safe to unpack"}
        if info.file_size > MAX_FILE_BYTES:
            continue
        total += info.file_size
        if total > MAX_TOTAL_BYTES:
            return {"ok": False, "why": "it is too big"}
        rel = info.filename[len(top):] if top else info.filename
        data = zf.read(info)
        if len(data) > MAX_FILE_BYTES:
            continue
        files[rel] = data
    if "SKILL.md" not in files:
        return {"ok": False, "why": "it has no SKILL.md"}
    return {"ok": True, "files": files}


def _lock_path() -> str:
    return os.path.join(skills_mod.skills_dir(), ".lock.json")


def _read_lock() -> dict:
    try:
        with open(_lock_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_lock(lock: dict) -> None:
    path = _lock_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(lock, fh, indent=1)
    os.replace(tmp, path)


def write_skill(name_hint: str, files: dict[str, bytes], origin: dict) -> str:
    """Put a skill's files in ``<state>/skills/<name>/`` and pin it in the lock
    file. Returns the folder name."""
    from puffpai.security import safe_archive
    meta, _ = skills_mod.parse_frontmatter(files["SKILL.md"].decode("utf-8", errors="replace"))
    name = skills_mod.safe_name(meta.get("name")) or skills_mod.safe_name(name_hint) or "skill"
    root = skills_mod.skills_dir(create=True)
    folder, n = name, 2
    while os.path.exists(os.path.join(root, folder)):
        folder = f"{name}-{n}"[:skills_mod.NAME_MAX]
        n += 1
    try:
        from puffpai.security import keys
        keys.remove_skill_key(folder, by="install")   # keys of an earlier copy, if any
    except Exception as exc:  # noqa: BLE001 - identity binding still refuses them
        logger.info("finding: old skill keys not cleared (%s)", type(exc).__name__)
    staging = os.path.join(root, ".staging", folder + "-" + secrets.token_hex(3))
    os.makedirs(staging)
    try:
        for rel, data in files.items():
            if safe_archive.check_member_name(rel):
                raise ValueError("unsafe name")
            path = os.path.join(staging, *rel.split("/"))
            if not os.path.realpath(path).startswith(os.path.realpath(staging) + os.sep):
                raise ValueError("outside")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(data)
        os.replace(staging, os.path.join(root, folder))
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    lock = _read_lock()
    lock[folder] = {**origin, "installed_at": _now(),
                    "files": {rel: hashlib.sha256(d).hexdigest()
                              for rel, d in sorted(files.items())}}
    _write_lock(lock)
    return folder


#: The lock entry fields that say which skill it is (not which version);
#: the same list ``security.keys`` ties a skill's key to.
_SOURCE_FIELDS = ("source", "owner", "slug", "repo", "folder")


def _source_of(origin: dict) -> dict:
    return {k: origin.get(k) for k in _SOURCE_FIELDS if origin.get(k)}


def same_source_folder(origin: dict) -> Optional[str]:
    """The installed skill folder that came from the same place as *origin*
    (same shelf, publisher and name, or same GitHub project and folder), or
    None. That skill installed again is an update, not a second copy."""
    want = _source_of(origin or {})
    if not want.get("source") or not (want.get("slug") or want.get("repo")):
        return None
    root = skills_mod.skills_dir()
    for folder, entry in _read_lock().items():
        if isinstance(entry, dict) and _source_of(entry) == want \
                and skills_mod.safe_name(folder) == folder \
                and os.path.isfile(os.path.join(root, folder, skills_mod.SKILL_FILE)):
            return folder
    return None


def update_skill(folder: str, files: dict[str, bytes], origin: dict) -> str:
    """Put a newer copy of the same skill into its existing folder.

    The folder itself stays (its files are swapped), so the skill's key in
    the vault stays with it (``security.keys.skill_identity`` counts where a
    skill came from, not its version). The old files go to ``.removed``."""
    from puffpai.security import safe_archive
    root = skills_mod.skills_dir()
    target = os.path.join(root, folder)
    if not (skills_mod.safe_name(folder) == folder and os.path.isdir(target)):
        raise ValueError("no such skill")
    if _source_of(origin) != _source_of(_read_lock().get(folder) or {}):
        raise ValueError("not the same skill")
    staging = os.path.join(root, ".staging", folder + "-" + secrets.token_hex(3))
    os.makedirs(staging)
    try:
        for rel, data in files.items():
            if safe_archive.check_member_name(rel):
                raise ValueError("unsafe name")
            path = os.path.join(staging, *rel.split("/"))
            if not os.path.realpath(path).startswith(os.path.realpath(staging) + os.sep):
                raise ValueError("outside")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(data)
        old = os.path.join(root, ".removed", f"{folder}-{int(time.time())}-{secrets.token_hex(2)}")
        os.makedirs(old)
        for name in os.listdir(target):
            os.replace(os.path.join(target, name), os.path.join(old, name))
        for name in os.listdir(staging):
            os.replace(os.path.join(staging, name), os.path.join(target, name))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    lock = _read_lock()
    lock[folder] = {**origin, "installed_at": _now(),
                    "files": {rel: hashlib.sha256(d).hexdigest()
                              for rel, d in sorted(files.items())}}
    _write_lock(lock)
    return folder


def remove_skill(folder: str) -> bool:
    root = skills_mod.skills_dir()
    path = os.path.join(root, folder)
    if not skills_mod.safe_name(folder) or not os.path.isdir(path):
        return False
    gone = os.path.join(root, ".removed", f"{folder}-{int(time.time())}")
    os.makedirs(os.path.dirname(gone), exist_ok=True)
    os.replace(path, gone)
    lock = _read_lock()
    lock.pop(folder, None)
    _write_lock(lock)
    try:
        from puffpai.security import keys
        keys.remove_skill_key(folder, by="owner")   # its keys leave with it
    except Exception as exc:  # noqa: BLE001 - the skill itself is gone
        logger.info("finding: skill keys not cleared (%s)", type(exc).__name__)
    return True


def needs_lines(skill_md: str, origin: Optional[dict] = None) -> list[str]:
    """The plain "needs" lines for a skill's text, before or after install:
    programs this computer is missing, keys it asks for (its frontmatter,
    :func:`puffpai.skills.requirements`), and what its text names
    (packages, keys, sibling skills; :func:`puffpai.deps.read`)."""
    from puffpai import deps
    try:
        meta, _ = skills_mod.parse_frontmatter(skill_md or "")
        req = skills_mod.requirements(meta)
        needs = deps.read(skill_md or "", origin=origin or {})
    except Exception:  # noqa: BLE001 - a line we cannot work out is left out
        return []
    out: list[str] = []
    if req["bins"] or req["any_bins"] or req["env"]:
        line = skills_mod.needs_line(req, skills_mod.missing_programs(req), {})
        if req["env"]:
            line += " You can put it in your vault from the skill's page."
        if line:
            out.append(line)
    text_keys = [k["name"] for k in needs["keys"] if k["from"] == "text"]
    if text_keys:
        out.append(f"Its text also names the key{'s' if len(text_keys) > 1 else ''} "
                   f"{skills_mod._and_list(text_keys)}; you can put "  # noqa: SLF001
                   f"{'them' if len(text_keys) > 1 else 'it'} in your vault from its page.")
    pkgs = [p["name"] for p in needs["packages"]]
    if pkgs:
        out.append(f"It uses {skills_mod._and_list(pkgs[:6])}; "  # noqa: SLF001
                   "its page can install what is missing, after your yes.")
    if needs["skills"]:
        names = skills_mod._and_list([s["name"] for s in needs["skills"]])  # noqa: SLF001
        out.append(f"It works with the skills {names}. BuildBot installs the ones the same "
                   f"publisher has, with it, each checked the same way.")
    return out


# ── the MCP tool list ─────────────────────────────────────────────────────

def _mcp_client():
    """The MCP tool server list, ``puffpai.mcp_client`` (a seam for tests)."""
    from puffpai import mcp_client
    return mcp_client


# ── preview ─────────────────────────────────────────────────────────────────

def _clawhub_owner_of(f: dict, verified: dict, slug: str) -> str:
    """The ClawHub owner of a find whose id has none: the search row's own
    owner, else the owner in ClawHub's page address for it (``/<owner>/skills/
    <slug>``), else ""."""
    owner = str((f.get("raw") or {}).get("owner") or "")
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", owner):
        return owner
    for url in (verified.get("page"), f.get("url")):
        parts = urllib.parse.urlsplit(str(url or ""))
        bits = [b for b in parts.path.split("/") if b]
        if (parts.hostname == sites.CLAWHUB and len(bits) == 3 and bits[1] == "skills"
                and bits[2] == slug and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", bits[0])):
            return bits[0]
    return ""


def _skill_source(f: dict) -> tuple[str, dict]:
    """("clawhub"|"github"|"", details) for a skill find."""
    fid, raw = f["id"], f.get("raw") or {}
    if fid.startswith("clawhub:"):
        owner, slug = _clawhub_ref(fid)
        return "clawhub", {"owner": owner, "slug": slug}
    if fid.startswith("skills.sh:"):
        ref = fid.split(":", 1)[1]
        owner, repo, skill = ref.split("/", 2)
        return "github", {"repo": f"{owner}/{repo}", "skill": skill, "hint": "",
                          "service": "shelf_skills_sh"}
    if fid.startswith("hermes:") and raw.get("repo") and raw.get("path"):
        return "github", {"repo": raw["repo"], "skill": str(raw["path"]).rsplit("/", 1)[-1],
                          "hint": raw["path"], "service": "shelf_hermes"}
    return "", {}


def preview(find_id: Any) -> dict:
    """What she is asked about before an install. ``{"ok", "id", "name", "type",
    "type_words", "from", "lines", "verdict", "verdict_words", "can",
    "refuse", "question"}``."""
    f = find_mod.recall(find_id)
    if f is None:
        return {"ok": False, "line": W.NOT_FOUND}
    if f["type"] == "book":
        return _book_preview(f)
    article = "an" if f["type_words"][:1].lower() in "aeiou" or f["type"] == "mcp" else "a"
    lines = [f"{f['name']}: {article} {f['type_words']} from {f['site_name']}."]
    if f.get("what"):
        lines.append(f["what"])
    if f.get("url"):
        lines.append(f"Where it comes from: {f['url']}")
    verdict, verdict_words = f.get("verdict") or "", f.get("verdict_words") or ""
    extra: dict[str, Any] = {}
    origin = _origin_of(f)
    if origin:
        extra["origin"] = origin
        line = sites.origin_line(origin)
        if line:
            lines.append(line)
    skill_text = ""
    if f["type"] == "skill" and f["can"] == "install":
        kind, ref = _skill_source(f)
        if kind == "clawhub":
            v = sites.clawhub_verify(ref["slug"], ref["owner"])
            if v:
                verdict = v["verdict"] or verdict
                verdict_words = W.verdict_words("ClawHub", verdict)
                files = [x["path"] for x in v["files"]]
                text = _clawhub_skill_md(ref["slug"], ref["owner"], v.get("version", ""))
                skill_text = text
                lines += _says_lines(text, f)
                lines += runs_lines(what_it_runs(text, files), files)
                lines += needs_lines(text)
                if v.get("summary"):
                    lines.append(f"ClawHub says: {v['summary']}")
                extra["version"] = v.get("version", "")
        elif kind == "github":
            sha = _gh_commit(ref["repo"], ref["service"])
            tree = _gh_tree(ref["repo"], sha, ref["service"]) if sha else []
            folder = skill_folder_in(tree, ref["skill"], ref["hint"])
            if folder is not None:
                prefix = folder + "/" if folder else ""
                files = [t["path"][len(prefix):] for t in tree if t.get("type") == "blob"
                         and t["path"].startswith(prefix)
                         and (folder or "/" not in t["path"])]
                text = ""
                ans = net.get(f"https://{sites.GITHUB_RAW}/{ref['repo']}/{sha}/"
                              + urllib.parse.quote(prefix + "SKILL.md", safe="/"),
                              hosts=[sites.GITHUB_RAW], service=ref["service"],
                              max_bytes=MAX_FILE_BYTES, cache_s=600)
                if ans.ok:
                    text = ans["body"].decode("utf-8", errors="replace")
                skill_text = text
                lines += _says_lines(text, f)
                lines += runs_lines(what_it_runs(text, files), files)
                lines += needs_lines(text)
                lines.append(f"Version: GitHub commit {sha[:12]}.")
                extra["commit"] = sha
                _keep_preview_commit(f, sha)
    elif f["can"] == "add_mcp":
        start = _mcp_start_of(f)
        lines.append(start.get("runs") or "It runs a tool server.")
    elif f["can"] == "integrate":
        app = _prepare_app(f)
        lines += app["lines"]
        if app.get("look"):
            extra["look_why"] = app["look"]
        if app.get("needs"):
            extra["needs_programs"] = list(app["needs"])
        if app.get("missing"):
            extra["missing"] = dict(app["missing"])
        if app.get("commands"):
            extra["commands"] = list(app["commands"])
    if verdict_words:
        lines.append(verdict_words + ".")
    elif f["type"] == "skill":
        lines.append("This shelf shows no safety check for it.")
    if f.get("can_trade"):
        lines.append(W.TRADE_NOTE)
    refuse = verdict == "malicious"
    can, can_words = f["can"], f["can_words"]
    if extra.get("look_why"):
        # Checked, and it cannot be added as it is (Drawbridge stopped it, or
        # it needs a program this computer does not have): no yes to give.
        can, can_words = "look", f"{W.CAN['look']}: {extra.pop('look_why')}"
    if skill_text and _NEEDS_OPENCLAW_RE.search(skill_text):
        can, can_words = "look", f"{W.CAN['look']}: {NEEDS_OPENCLAW_WHY}"
        lines.append(f"BuildBot cannot use it: {NEEDS_OPENCLAW_WHY}")
        _keep_preview_look(f, NEEDS_OPENCLAW_WHY)
    question = "" if refuse or can == "look" else W.ASK_LINE
    return {"ok": True, "id": f["id"], "name": f["name"], "type": f["type"],
            "type_words": f["type_words"], "from": f.get("url", ""), "lines": lines,
            "verdict": verdict, "verdict_words": verdict_words, "can": can,
            "can_words": can_words, "refuse": refuse, "question": question, **extra}


BOOK_ASK = "Shall BuildBot download it for your bot?"


def _origin_of(f: dict) -> dict:
    """Who made a find on GitHub and what language its words are in, or {}
    (so no one installs an app without knowing its words are all Chinese). Kept on the find, so ranking uses the README's language."""
    repo, service = "", sites.SERVICE["github"]
    if f["id"].startswith("github:"):
        repo = f["id"].split(":", 1)[1]
    elif f["type"] == "skill":
        kind, ref = _skill_source(f)
        if kind == "github":
            repo, service = ref["repo"], ref["service"]
    if not repo:
        return {}
    try:
        origin = sites.github_origin(repo, service=service)
    except Exception:  # noqa: BLE001 - the card goes on without it
        logger.info("finding: could not read where %s comes from", f["id"])
        return {}
    lang = origin.get("words_language") or ""
    if lang and lang != f.get("words_language"):
        f["words_language"] = lang          # later saves of this find keep it
        find_mod.remember([dict(f)])
    return origin


def _book_preview(f: dict) -> dict:
    """A book's card: what it is, its size, licence, where from, and the yes."""
    from puffpai.knowledge import find_more
    cand = (f.get("raw") or {}).get("candidate") or {}
    article = "an" if f["type_words"][:1].lower() in "aeiou" else "a"
    lines = [f"{f['name']}: {article} {f['type_words']} from {f['site_name']}."]
    lines += [line.strip() for line in find_more.consent_lines(cand)[1:]
              if not line.strip().startswith("To add it:")]
    lines.append(f"Format: {f.get('format_words') or 'plain text'}.")
    return {"ok": True, "id": f["id"], "name": f["name"], "type": f["type"],
            "type_words": f["type_words"], "from": f.get("url", ""), "lines": lines,
            "verdict": "", "verdict_words": "", "can": f["can"],
            "can_words": f["can_words"], "refuse": False, "question": BOOK_ASK}


def _says_lines(skill_md: str, f: dict) -> list[str]:
    """The skill's own one-line description, when the find did not show it
    (skills.sh shows none)."""
    try:
        meta, _ = skills_mod.parse_frontmatter(skill_md or "")
    except Exception:  # noqa: BLE001
        return []
    desc = sites.plain(meta.get("description"), 300) if meta else ""
    if not desc or desc.rstrip(".").lower() in str(f.get("what") or "").lower():
        return []
    return [f"It says: {desc.rstrip('.')}."]


def _keep_preview_commit(f: dict, sha: str) -> None:
    """The commit she was shown is the one installed."""
    if f.get("preview_commit") != sha:
        find_mod.remember([dict(f, preview_commit=sha)])


def _keep_preview_look(f: dict, why: str) -> None:
    """A find that turned out to be one BuildBot cannot use stays that way."""
    if f.get("look_after_preview") != why:
        find_mod.remember([dict(f, look_after_preview=why)])


def _clawhub_skill_md(slug: str, owner: str, version: str) -> str:
    params = {"path": "SKILL.md"}
    if version:
        params["version"] = version
    if owner:
        params["ownerHandle"] = owner
    url = (f"https://{sites.CLAWHUB}/api/v1/skills/{urllib.parse.quote(slug)}/file?"
           + urllib.parse.urlencode(params))
    ans = net.get(url, hosts=[sites.CLAWHUB], service="shelf_clawhub",
                  max_bytes=MAX_FILE_BYTES, cache_s=600)
    return ans["body"].decode("utf-8", errors="replace") if ans.ok else ""


def _mcp_start_of(f: dict) -> dict:
    raw = f.get("raw") or {}
    if isinstance(raw.get("server"), dict):
        return sites.mcp_start(raw["server"])
    if f["site"] == "npm" and raw.get("name"):
        cmd = f"npx -y {raw['name']}@{raw['version']}" if raw.get("version") else \
            f"npx -y {raw['name']}"
        return {"ok": True, "command": cmd,
                "runs": f"It runs this command on this computer: {cmd}"}
    if f["site"] == "pypi" and raw.get("name"):
        cmd = f"uvx {raw['name']}=={raw['version']}" if raw.get("version") else \
            f"uvx {raw['name']}"
        return {"ok": True, "command": cmd,
                "runs": f"It runs this command on this computer: {cmd}"}
    return {"ok": False, "why": W.LOOK_WHY["mcp_start"]}


# ── install ─────────────────────────────────────────────────────────────────

def _result(status: str, line: str, lines: list[str], **extra: Any) -> dict:
    return {"ok": status in ("installed", "added"), "status": status, "line": line,
            "lines": lines, **extra}


def install_thing(find_id: Any, *, progress: Callable[[str], None] = lambda _l: None,
                  via: str = "chat", companions: Optional[list] = None) -> dict:
    """Install one find she said yes to. Returns ``{"ok", "status", "line",
    "lines", "entry"}``; status is installed, added, held, look, refused or failed.
    *companions*: for a skill, install only these of the sibling skills the
    one publisher rule offers (the build's setup list showed exactly these);
    None installs every one it offers."""
    out = _install_thing(find_id, progress=progress, via=via, companions=companions)
    _log_install(find_id, out)
    return out


def _log_install(find_id: Any, out: dict) -> None:
    """The install on the team page: Toolsmith installs, and a find the check
    stopped is Drawbridge's."""
    from puffpai import agent_activity
    f = find_mod.recall(find_id) or {}
    name = str(f.get("name") or "a find")
    status = str(out.get("status") or "")
    if status in ("refused", "look"):
        agent_activity.record("sentinel", f"Checked {name} and did not install it",
                              status="completed")
    elif out.get("ok"):
        agent_activity.record("sentinel", f"Checked {name} before it was installed")
        agent_activity.record("toolsmith", f"Installed {name}")
    elif status == "failed":
        agent_activity.record("toolsmith", f"Could not install {name}", status="error")


def _install_thing(find_id: Any, *, progress: Callable[[str], None],
                   via: str, companions: Optional[list] = None) -> dict:
    lines: list[str] = []

    def say(line: str) -> None:
        lines.append(line)
        try:
            progress(line)
        except Exception:  # noqa: BLE001 - a screen never stops an install
            pass

    f = find_mod.recall(find_id)
    if f is None:
        say(W.NOT_FOUND)
        return _result("failed", W.NOT_FOUND, lines)
    name = f["name"]
    same = already(f["id"])
    if same is not None:
        # The same thing again (a model that retries, a second tap) reuses
        # the install instead of copying it under a new name.
        line = W.ALREADY.format(name=name)
        say(line)
        for cap in f.get("caps") or [f.get("cap")]:
            tag_capability(same.get("entry"), cap)
        folder = (same.get("undo") or {}).get("folder")
        return _with_page(_result("installed" if same.get("type") == "skill" else "added",
                                  line, lines, entry=same.get("entry"), already=True,
                                  **({"folder": folder} if folder else {})))
    if f.get("verdict") == "malicious":
        line = W.REFUSED_MALICIOUS.format(name=name, shelf=f["site_name"])
        say(line)
        return _result("refused", line, lines)
    if f.get("look_after_preview"):
        line = W.LOOK_YOURSELF.format(name=name, url=f.get("url") or f["site_name"],
                                      why=f["look_after_preview"])
        say(line)
        return _result("look", line, lines)
    try:
        if f["type"] == "book":
            return _with_page(_install_book(f, say, lines, via))
        if f["type"] == "skill" and f["can"] == "install":
            return _with_page(_install_skill(f, say, lines, only=companions))
        if f["can"] == "add_mcp":
            return _with_page(_install_mcp(f, say, lines))
        if f["can"] == "integrate":
            return _with_page(_install_app(f, say, lines, via))
    except Exception as exc:  # noqa: BLE001 - one install never stops the rest
        logger.warning("finding: install failed (%s)", type(exc).__name__)
        line = W.DOWNLOAD_FAILED.format(name=name)
        say(line)
        return _result("failed", line, lines)
    why = f["can_words"].split(": ", 1)[1] if ": " in f["can_words"] else W.LOOK_WHY["unknown"]
    line = W.LOOK_YOURSELF.format(name=name, url=f.get("url") or f["site_name"], why=why)
    say(line)
    return _result("look", line, lines)


def _with_page(out: dict) -> dict:
    """After an install: the new thing goes on her home page and Toolsmith
    makes its page (``puffpai.things``). Adds ``page``, its address."""
    if not out.get("ok") or not out.get("entry"):
        return out
    try:
        from puffpai import things
        hit = next((e for e in entries() if e.get("entry") == out["entry"]), None)
        address = things.after_install((hit or {}).get("undo"))
    except Exception as exc:  # noqa: BLE001 - the install itself is done
        logger.info("finding: no page made (%s)", type(exc).__name__)
        address = ""
    return {**out, "page": address} if address else out


def _switch_off_line(f: dict, service: str) -> Optional[str]:
    try:
        from puffpai.security import outside_switches
        if outside_switches.is_off(service):
            return W.SWITCHED_OFF.format(name=f["name"], site=f["site_name"])
    except Exception:  # noqa: BLE001
        return None
    return None


def _install_skill(f: dict, say: Callable[[str], None], lines: list[str], *,
                   with_companions: bool = True, only: Optional[list] = None) -> dict:
    name = f["name"]
    kind, ref = _skill_source(f)
    if kind == "clawhub":
        off = _switch_off_line(f, "shelf_clawhub")
        if off:
            say(off)
            return _result("failed", off, lines)
        say(f"Checking {name} on ClawHub.")
        v = sites.clawhub_verify(ref["slug"], ref["owner"]) or {}
        if v.get("verdict") == "malicious":
            line = W.REFUSED_MALICIOUS.format(name=name, shelf="ClawHub")
            say(line)
            return _result("refused", line, lines)
        if v.get("verdict"):
            say(W.verdict_words("ClawHub", v["verdict"]) + ".")
        if not ref["owner"]:
            # A find with a short id (``clawhub:news``): its owner is recorded
            # now, so the lock, its page's plan and the installer agree on
            # its publisher.
            ref = {**ref, "owner": _clawhub_owner_of(f, v, ref["slug"])}
        version = v.get("version") or ""
        say(f"Downloading {name}" + (f" version {version}." if version else "."))
        got = clawhub_skill(ref["slug"], ref["owner"], version)
        if got.get("malicious"):
            line = W.REFUSED_MALICIOUS.format(name=name, shelf="ClawHub")
            say(line)
            return _result("refused", line, lines)
        expected = {x["path"]: x["sha256"] for x in v.get("files") or []
                    if x.get("sha256") and x["path"] not in CLAWHUB_OWN_FILES}
        if got.get("ok") and expected:
            for rel, data in got["files"].items():
                if rel in expected and hashlib.sha256(data).hexdigest() != expected[rel]:
                    got = {"ok": False, "why": "its files are not the version ClawHub checked"}
                    break
        origin = {"source": "clawhub", "id": f["id"], "owner": ref["owner"],
                  "slug": ref["slug"], "version": version, "url": f.get("url", ""),
                  "verdict": v.get("verdict", ""), "zip_sha256": got.get("zip_sha256", "")}
        pinned = f"version {version}" if version else "latest version"
    elif kind == "github":
        off = _switch_off_line(f, ref["service"])
        if off:
            say(off)
            return _result("failed", off, lines)
        say(f"Downloading {name} from GitHub ({ref['repo']}).")
        got = github_skill(ref["repo"], ref["skill"], service=ref["service"], hint=ref["hint"],
                           sha=f.get("preview_commit") or None)
        origin = {"source": "github", "id": f["id"], "repo": ref["repo"],
                  "commit": got.get("commit", ""), "folder": got.get("folder", ""),
                  "url": f.get("url", ""), "verdict": f.get("verdict", "")}
        pinned = f"commit {str(got.get('commit') or '')[:12]}"
    else:
        line = W.LOOK_YOURSELF.format(name=name, url=f.get("url") or f["site_name"],
                                      why=W.LOOK_WHY["shelf"])
        say(line)
        return _result("look", line, lines)
    if not got.get("ok"):
        line = W.DOWNLOAD_FAILED.format(name=name).rstrip(".") + f": {got.get('why')}."
        say(line)
        return _result("failed", line, lines)
    text = (got["files"].get("SKILL.md") or b"").decode("utf-8", errors="replace")
    if _NEEDS_OPENCLAW_RE.search(text):
        line = W.LOOK_YOURSELF.format(name=name, url=f.get("url") or f["site_name"],
                                      why=NEEDS_OPENCLAW_WHY)
        say(line)
        return _result("look", line, lines)
    if kind == "github" and not origin.get("folder"):
        origin["folder"] = ref.get("skill") or ""
    earlier = same_source_folder(origin)
    if earlier:
        # The same skill again (a newer version): updated in place, so its
        # key in the vault stays with it.
        folder = update_skill(earlier, got["files"], origin)
        say(f"Updated {folder} in your skills folder ({pinned}). Its key, if you kept one, "
            f"stays in your vault.")
    else:
        folder = write_skill(ref.get("slug") or ref.get("skill") or name, got["files"], origin)
        say(f"Saved in your skills folder as {folder}.")
    entry = _add_entry({"id": f["id"], "name": name, "type": "skill", "type_words": "skill",
                        "from": f.get("url", ""), "pinned": pinned, "what": f.get("what", ""),
                        "undo": {"kind": "skill", "folder": folder},
                        "line": f"{name} (skill, {pinned})"})
    line = W.INSTALLED.format(name=name)
    for need in needs_lines(text, origin):
        say(need)
    if with_companions:
        _install_companions(folder, text, say, only=only)
    say(line)
    return _result("installed", line, lines, entry=entry["entry"], folder=folder)


def _companion_find(parent: dict, name: str) -> Optional[dict]:
    """A find for the sibling skill *name* from the same place as the skill
    whose lock entry is *parent*, or None (a skill from somewhere else). The
    one publisher rule, shared with the plan: :func:`puffpai.deps.sibling_find`."""
    from puffpai import deps
    return deps.sibling_find(parent, name)


def _install_companions(parent_folder: str, text: str,
                        say: Callable[[str], None], *,
                        only: Optional[list] = None) -> list[str]:
    """Install the sibling skills a skill names, from its own publisher,
    with the same checks (one level: their own siblings are offered on their
    pages). *only*: just these names (what her yes covered). Returns the
    folders installed."""
    from puffpai import deps
    parent = _read_lock().get(parent_folder) or {}
    done: list[str] = []
    wanted = None if only is None else {str(n) for n in only}
    for name in deps.siblings_offered(text, parent_folder, parent):
        if wanted is not None and name not in wanted:
            continue
        if skills_mod.find(name) is not None:
            continue
        try:
            got = install_companion(parent_folder, name, say, parent=parent)
        except Exception as exc:  # noqa: BLE001 - a sibling never undoes its parent
            logger.info("finding: sibling skill not installed (%s)", type(exc).__name__)
            say(f"{name}: it could not be installed.")
            continue
        if got.get("ok") and got.get("folder"):
            done.append(got["folder"])
    return done


def install_companion(parent_folder: str, name: str, say: Callable[[str], None], *,
                      parent: Optional[dict] = None) -> dict:
    """Install the sibling skill *name* of the installed skill
    *parent_folder* from the same publisher (ClawHub owner or GitHub
    project), through the normal skill install and its checks. A name the
    publisher does not have is skipped with a plain line."""
    parent = parent if parent is not None else (_read_lock().get(parent_folder) or {})
    f = _companion_find(parent, name)
    if f is None:
        line = f"{name}: it is not from the same publisher, so BuildBot did not install it."
        say(line)
        return {"ok": False, "line": line}
    if f["id"].startswith("clawhub:"):
        owner, slug = _clawhub_ref(f["id"])
        if not sites.clawhub_verify(slug, owner):
            line = f"{name}: the same publisher has no skill by that name on ClawHub."
            say(line)
            return {"ok": False, "line": line}
    find_mod.remember([f])
    sub: list[str] = []
    out = _install_skill(f, say, sub, with_companions=False)
    if out.get("ok"):
        _with_page(out)
    return out


def _install_book(f: dict, say: Callable[[str], None], lines: list[str], via: str) -> dict:
    """A book she said yes to: a text book is ordered (the background learner
    fetches it, then learns it); a Kiwix file is downloaded and checked now,
    then learned in the background. Before her bot is made (the wizard's
    finding step), either one waits for the build. Either way it becomes a
    book thing."""
    from puffpai.knowledge import find_more
    name = f["name"]
    cand = (f.get("raw") or {}).get("candidate") or {}
    ident = str(cand.get("id") or "")
    how = {"chat": "chat", "wizard": "wizard"}.get(via, "add_page")
    if not ident:
        say(W.NOT_FOUND)
        return _result("failed", W.NOT_FOUND, lines)
    if find_more.recorded_posture() is None:
        # Said yes while finding things, before the bot is made: it waits
        # for the build, which makes the knowledge folder, then it is
        # fetched and learned in the background.
        try:
            book = find_more.order_book(ident, name, via=how, cand=cand)
        except find_more.OrderBlocked as exc:
            say(exc.line)
            return _result("refused", exc.line, lines)
        line = (f"{name} is on your list. BuildBot fetches it once your bot is made, "
                f"then your bot learns it.")
    elif find_more.is_text_book(ident):
        try:
            book = find_more.start_in_background(ident, name, via=how, cand=cand)
        except find_more.OrderBlocked as exc:
            say(exc.line)
            return _result("refused", exc.line, lines)
        line = (f"{name} is on its way: BuildBot fetches it in the background, then your "
                f"bot reads it to answer questions.")
    else:
        say(f"Downloading {name} ({f.get('size_words') or 'size not known'}).")
        got = find_more.add(ident, size_acknowledged=True, via=how)
        if not got.get("ok"):
            line = W.DOWNLOAD_FAILED.format(name=name).rstrip(".") + \
                f": {got.get('reason') or 'no reason given'}."
            say(line)
            return _result("failed", line, lines)
        from puffpai.knowledge.fts_index import library_id_for
        book = library_id_for(str((got.get("result") or {}).get("path") or ident))
        try:
            from puffpai.knowledge.auto_index import start_background_index
            start_background_index(str(got.get("folder") or ""))
        except Exception as exc:  # noqa: BLE001 - it is learned on the next start
            logger.info("finding: book not started learning (%s)", type(exc).__name__)
        line = f"{name} is downloaded and checked. Your bot is learning it now."
    entry = _add_entry({"id": f["id"], "name": name, "type": "book",
                        "type_words": f["type_words"], "from": f.get("url", ""),
                        "pinned": str(cand.get("source_catalog") or ""),
                        "what": f.get("what", ""), "undo": {"kind": "book", "book": book},
                        "line": f"{name} ({f['type_words']}, {f.get('size_words') or ''})"})
    say(line)
    return _result("added", line, lines, entry=entry["entry"])


def _install_mcp(f: dict, say: Callable[[str], None], lines: list[str]) -> dict:
    start = _mcp_start_of(f)
    if not start.get("ok"):
        line = W.LOOK_YOURSELF.format(name=f["name"], url=f.get("url") or f["site_name"],
                                      why=start.get("why") or W.LOOK_WHY["mcp_start"])
        say(line)
        return _result("look", line, lines)
    client = _mcp_client()
    server_name = (skills_mod.safe_name(f["name"]) or "tool")[:40].strip("-") or "tool"
    slug = getattr(client, "slug", None)
    if callable(slug):
        server_name = slug(f["name"]) or server_name   # the tool list's own names
    target = start.get("url") or start.get("command")
    say(start.get("runs") or "")
    added = client.add_server(server_name, target, headers=start.get("headers"),
                              label=f["name"], source=f.get("url") or f["id"])
    if isinstance(added, dict) and added.get("ok") is False:
        line = f"{f['name']} could not be added: {added.get('message') or 'no reason given'}"
        say(line)
        return _result("failed", line, lines)
    server = (added or {}).get("server") if isinstance(added, dict) else None
    real = (server or {}).get("name") if isinstance(server, dict) else (
        (added or {}).get("name") if isinstance(added, dict) else None)
    if isinstance(added, dict) and added.get("message"):
        say(str(added["message"]))
    entry = _add_entry({"id": f["id"], "name": f["name"], "type": "mcp",
                        "type_words": W.type_words("mcp"), "from": f.get("url", ""),
                        "pinned": target, "what": f.get("what", ""), "undo": {"kind": "mcp",
                                                   "server": real or server_name},
                        "line": f"{f['name']} (MCP tool server)"})
    line = W.ADDED_TOOLS.format(name=f["name"])
    say(line)
    return _result("added", line, lines, entry=entry["entry"])


def _round3_found(f: dict) -> Optional[dict]:
    """A ``found/v1`` record for a GitHub or Codeberg project, with its
    pin and details read (the existing integrate path's own readers)."""
    from puffpai.catalogs import search_budget, where_to_look
    item = (f.get("raw") or {}).get("item") or {}
    if f["site"] == "github":
        from puffpai.catalogs.searchers import github as G
        found = G.found_from_item(item, part="code-app")
    elif f["site"] == "codeberg":
        from puffpai.catalogs.searchers import codeberg as C
        found = C.found_from_item(item, part="code-app")
    else:
        return None
    if not isinstance(found, dict):
        return None
    budget = search_budget.new_budget(wizard=False)
    return where_to_look.details(found, budget=budget)


#: The one yes for a code project covers exactly the card that was shown:
#: the checked download (its integration id), Drawbridge's rows and the plan.
APP_CHECK_AGAIN = ("BuildBot checked {name} again just now. Look at its card once more "
                   "and say yes there; nothing was set up.")


def _app_item(f: dict, found: dict) -> str:
    from puffpai.wizard import find_parts
    item_id = "find:" + hashlib.sha256(f["id"].encode()).hexdigest()[:16]
    with find_parts._LOCK:  # noqa: SLF001 - the one store the integrate path reads
        find_parts._FOUND[item_id] = found  # noqa: SLF001
    return item_id


def _prepare_app(f: dict) -> dict:
    """Download and check a code project and read how it runs, adding
    nothing: ``{"lines", "look"?, "needs"?, "commands"?}`` for its yes card.
    Kept on the find (``prepared``), so asking again shows the same card."""
    from puffpai.integrate import apply as r3_apply
    kept = f.get("prepared") if isinstance(f.get("prepared"), dict) else {}
    card = r3_apply.prepared_card(str(kept.get("iid") or "")) if kept else None
    if card is None:
        found = _round3_found(f)
        if not found or not (found.get("repo") or {}).get("pinned_commit"):
            return {"lines": [], "look": "BuildBot could not pin its current version."}
        ctx = types.SimpleNamespace(progress=lambda _l: None, online=True, shared={},
                                    via="add_page")
        out = r3_apply.prepare_code_project({"id": _app_item(f, found), "label": f["name"]},
                                            ctx)
        if out.get("status") != "checked":
            why = " ".join(str(out.get("line") or "").split(": ", 1)[-1].split()) \
                or W.LOOK_WHY["unknown"]
            why = why[0].lower() + why[1:]
            if out.get("status") == "refused":
                _keep_preview_look(f, why)      # Drawbridge stopped it: it stays stopped
            return {"lines": [], "look": why}
        card = out["card"]
        kept = {"iid": out["integration_id"], "code_sha": card.get("code_sha") or "",
                "host_sha": card.get("host_sha") or ""}
        find_mod.remember([dict(f, prepared=kept)])
    host = card.get("host") or {}
    got: dict = {"lines": list(card.get("lines") or []),
                 "commands": list(host.get("commands") or [])}
    if host and not host.get("how"):
        why = f"BuildBot cannot run it on this computer: {host.get('why_not')}."
        got["look"] = why
        # Nothing to say yes to: the checked download is not kept, and the
        # find stays "look" so it is not downloaded again for nothing.
        try:
            from puffpai.integrate import records
            records.remove(str(kept.get("iid") or ""), by="buildbot")
        except Exception:  # noqa: BLE001
            pass
        _keep_preview_look(f, why)
    elif host.get("missing"):
        from puffpai.integrate import run_here
        hook = run_here.install_missing({"find": f["id"]}, list(host["missing"]))
        got["look"] = hook["line"][0].lower() + hook["line"][1:]
        got["needs"] = list(host["missing"])
        card = hook.get("card") or {}
        if card.get("plan") or card.get("manual"):
            got["missing"] = card       # the same "Install what's missing" card
    return got


def _install_app(f: dict, say: Callable[[str], None], lines: list[str], via: str) -> dict:
    from puffpai.integrate import apply as r3_apply
    from puffpai.integrate.wording import HELD_WAITING   # no find screen has that yes
    kept = f.get("prepared") if isinstance(f.get("prepared"), dict) else {}
    if not kept or r3_apply.prepared_card(str(kept.get("iid") or "")) is None:
        # No card was shown for this exact check (fail closed): check it now,
        # and her yes comes on the new card. Nothing runs before that.
        _prepare_app(f)
        line = APP_CHECK_AGAIN.format(name=f["name"])
        say(line)
        return _result("held", line, lines)

    def say_app(line: str) -> None:
        if str(line).strip() != HELD_WAITING:
            say(line)
    out = r3_apply.finish_prepared(str(kept["iid"]), code_sha=str(kept.get("code_sha") or ""),
                                   host_sha=str(kept.get("host_sha") or ""), progress=say_app)
    status = str(out.get("status") or "failed")
    line = " ".join(str(out.get("line") or "").replace(HELD_WAITING, "").split())
    if line and (not lines or lines[-1] != line):
        say(line)
    if status != "added":
        if status == "held" and out.get("needs"):
            # It needs a program this computer does not have: kept, never
            # removed, and set up once that program is here.
            return _result("held", line, lines)
        if status == "held" and out.get("integration_id"):
            # Held at a later stage (the app host found something new) for a
            # yes no find screen gives: nothing is kept.
            try:
                from puffpai.integrate import records
                records.remove(str(out["integration_id"]), by="owner")
            except Exception:  # noqa: BLE001
                pass
            why = line.split(": ", 1)[-1].strip() or W.LOOK_WHY["unknown"]
            line = W.LOOK_YOURSELF.format(name=f["name"], url=f.get("url") or f["site_name"],
                                          why=why[0].lower() + why[1:])
            say(line)
            return _result("look", line, lines)
        if status == "refused" or not out.get("integration_id"):
            return _result("look" if status == "refused" else status, line, lines)
        return _result(status, line, lines)
    found = {"repo": {"pinned_commit": ""}}
    try:
        from puffpai.integrate import records
        found["repo"]["pinned_commit"] = str(
            ((records.get(str(kept["iid"])) or {}).get("from") or {}).get("commit") or "")
    except Exception:  # noqa: BLE001
        pass
    pinned = f"commit {str((found.get('repo') or {}).get('pinned_commit') or '')[:12]}"
    entry = _add_entry({"id": f["id"], "name": f["name"], "type": f["type"],
                        "type_words": f["type_words"], "from": f.get("url", ""),
                        "pinned": pinned, "what": f.get("what", ""),
                        "undo": {"kind": "app", "integration": out.get("integration_id")},
                        "line": f"{f['name']} ({f['type_words']}, {pinned})"})
    return _result("added", line, lines, entry=entry["entry"])


# ── undo ────────────────────────────────────────────────────────────────────

def undo(entry_id: Any) -> dict:
    """Take one install away again. ``{"ok", "line"}``."""
    entry_id = str(entry_id or "")
    if not _ENTRY_RE.match(entry_id):
        return {"ok": False, "line": "There is nothing by that name to remove."}
    with _LOCK:
        rows = entries()
        hit = next((e for e in rows if e.get("entry") == entry_id), None)
        if hit is None:
            return {"ok": False, "line": "There is nothing by that name to remove."}
        how = hit.get("undo") or {}
        name = str(hit.get("name") or "It")
        if how.get("kind") == "book":
            # A book stays until it is taken out where books are kept.
            from puffpai import things
            return {"ok": False, "line": f"{name} stays. {things.BOOK_REMOVE}"}
        try:
            if how.get("kind") == "skill":
                remove_skill(str(how.get("folder") or ""))
            elif how.get("kind") == "mcp":
                _mcp_client().remove_server(str(how.get("server") or ""))
            elif how.get("kind") == "app":
                from puffpai.integrate import records
                records.remove(str(how.get("integration") or ""), by="owner")
        except Exception as exc:  # noqa: BLE001
            logger.warning("finding: undo failed (%s)", type(exc).__name__)
            return {"ok": False, "line": W.UNDO_FAILED.format(name=name,
                                                              why="something went wrong.")}
        _save_entries([e for e in rows if e.get("entry") != entry_id])
    return {"ok": True, "line": W.UNDONE.format(name=name)}


__all__ = ["clawhub_skill", "entries", "github_skill", "install_companion", "install_thing",
           "installed", "preview", "remove_skill", "runs_lines", "same_source_folder",
           "skill_folder_in", "undo", "unzip", "update_skill", "what_it_runs", "write_skill"]
