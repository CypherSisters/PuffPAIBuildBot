"""Toolsmith's zone: the places it may touch, and the places it never may.

Owner, 2026-10-14: "Toolsmith shouldn't have access to the internal parts, as
it's the agent that touches the outside." Toolsmith downloads and runs
strangers' code and may talk to a cloud model, so whatever it reads may leave
the computer. It belongs to zone 3.

* **Allowed**: the current try's folder (a ``host_apps/<id>`` folder, or the
  owner's chosen folder of her code), its own logs and scratch folders, and
  READ-only the toolchains (the folders of node, npm, python, git ...) and
  their package caches.
* **Never** (wins over everything, even her yes for a step outside the app's
  folder): BuildBot's state folder, the vault, the data folder (now and
  before) and linked folders, BuildBot's own code, other apps' folders, and
  her sign-in keys, browser profiles, startup items and personal folders. A
  folder that HOLDS one of them is refused too (``find ~``, ``-v C:\\Users\\x:/x``).
* Everything else is "outside": the old rule (it waits for her yes).

The terminal check here reads the command the way a shell would (``~``,
``$HOME``, ``%APPDATA%``, ``$env:...``, ``..``, quotes, redirections, ``cd``,
copy targets, Docker mounts). It is a boundary for the model's own commands,
not a sandbox: a native program Toolsmith runs still runs as the owner
(AGENTS.md, "Toolsmith's zone").

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: The one line the model gets back for a refused path (no real path in it).
REFUSED = "That path is outside what Toolsmith may touch ({category})."

STATE = "BuildBot's own files"
VAULT = "your vault"
DATA = "your data folder"
LINKED = "a folder you linked"
CODE = "BuildBot's own code"
OTHER_APP = "another app's folder"
KEYS = "your sign-in keys and passwords"
BROWSER = "your browser profiles"
STARTUP = "your startup items"
PERSONAL = "your personal folders"
WHOLE = "your whole home folder or a whole drive"
ENGINE = "the Docker engine itself, which can reach every folder"

#: A container that holds the Docker engine (its socket or pipe) or runs
#: privileged can mount any folder on this computer: refused like a denied place.
_ENGINE = re.compile(r"(?i)docker\.sock\b|[\\/]{2}\.[\\/]pipe[\\/]docker_engine|"
                     r"--privileged\b|--pid[ =]host\b|--cap-add[ =]\S*sys_admin|"
                     r"^\s*privileged\s*:\s*true\b|^\s*pid\s*:\s*[\"']?host\b", re.M)

#: Categories where a folder she chose for her code may sit INSIDE (only that
#: subfolder is then allowed): her data folder, a linked folder, Documents.
_INSIDE_OK = (DATA, LINKED, PERSONAL)

ALLOW, DENY, OUTSIDE = "allow", "deny", "outside"


def refused_line(category: str) -> str:
    return REFUSED.format(category=category)


# ── places ───────────────────────────────────────────────────────────────────

def canon(path: str) -> str:
    """The real, case-folded absolute path ("" when it cannot be read)."""
    try:
        return os.path.normcase(os.path.realpath(os.path.abspath(path)))
    except (OSError, ValueError):
        return ""


def inside_or_equal(child: str, parent: str) -> bool:
    if not child or not parent:
        return False
    if child == parent:
        return True
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:              # another drive
        return False


def home_dir() -> str:
    env = os.environ.get
    return (env("USERPROFILE") if os.name == "nt" else None) or os.path.expanduser("~")


def user_name() -> str:
    """The owner's account name (what is scrubbed from outgoing text)."""
    name = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if not name:
        name = os.path.basename(home_dir().rstrip("\\/"))
    return name.strip()


def _sensitive(home: str) -> list[tuple[str, str]]:
    """Her own sensitive places: keys, browser profiles, startup, personal."""
    env = os.environ.get
    j = os.path.join
    out: list[tuple[str, str]] = []
    for name in (".ssh", ".gnupg", ".aws", ".azure", ".kube", ".git-credentials", ".netrc",
                 "_netrc", ".npmrc", ".pypirc", ".claude", ".claude.json", ".docker",
                 j(".config", "gh"), j(".config", "gcloud"), j(".local", "share", "keyrings"),
                 j("Library", "Keychains")):
        out.append((j(home, name), KEYS))
    for name in ("Documents", "Desktop", "Pictures", "Downloads", "Videos", "Music",
                 j("OneDrive", "Documents"), j("OneDrive", "Desktop"),
                 j("OneDrive", "Pictures")):
        out.append((j(home, name), PERSONAL))
    for rel in (j(".config", "google-chrome"), j(".config", "chromium"),
                j(".config", "BraveSoftware"), j(".config", "microsoft-edge"), ".mozilla",
                j("Library", "Application Support", "Google"),
                j("Library", "Application Support", "Firefox"),
                j("Library", "Application Support", "BraveSoftware"),
                j("Library", "Application Support", "Microsoft Edge"), j("Library", "Safari"),
                j("Library", "Cookies")):
        out.append((j(home, rel), BROWSER))
    for rel in (j(".config", "autostart"), j("Library", "LaunchAgents")):
        out.append((j(home, rel), STARTUP))
    appdata, local = env("APPDATA"), env("LOCALAPPDATA")
    if appdata:
        out += [(j(appdata, "Mozilla"), BROWSER), (j(appdata, "Opera Software"), BROWSER),
                (j(appdata, "Microsoft", "Credentials"), KEYS),
                (j(appdata, "Microsoft", "Protect"), KEYS),
                (j(appdata, "Microsoft", "Crypto"), KEYS),
                (j(appdata, "Microsoft", "SystemCertificates"), KEYS),
                (j(appdata, "GitHub CLI"), KEYS), (j(appdata, "gcloud"), KEYS),
                (j(appdata, "Microsoft", "Windows", "Start Menu"), STARTUP)]
    if local:
        out += [(j(local, "Google"), BROWSER), (j(local, "Microsoft", "Edge"), BROWSER),
                (j(local, "BraveSoftware"), BROWSER), (j(local, "Mozilla"), BROWSER),
                (j(local, "Chromium"), BROWSER),
                (j(local, "Microsoft", "Credentials"), KEYS),
                (j(local, "Microsoft", "Vault"), KEYS)]
    pd = env("ProgramData")
    if pd:
        out.append((j(pd, "Microsoft", "Windows", "Start Menu", "Programs", "StartUp"),
                    STARTUP))
    return out


#: Settings already reported as unreadable (one log line each per process).
_UNREADABLE: set[str] = set()


def _unreadable(what: str) -> None:
    """One plain log line when a setting cannot be read (no prompt to her)."""
    if what not in _UNREADABLE:
        _UNREADABLE.add(what)
        logger.warning("Toolsmith's zone: %s could not be read; its usual place stays "
                       "off limits to Toolsmith.", what)


def _repo_root() -> str:
    """The install folder this file sits in (src/puffpai/security/...)."""
    d = os.path.dirname
    return d(d(d(d(os.path.abspath(__file__)))))


def _state_fallbacks() -> list[str]:
    """Where the state folder would be when its setting cannot be read, the
    most likely first (the order ``config.get_state_dir`` uses)."""
    env = os.environ.get
    out: list[str] = []
    if env("PUFFPAI_STATE_DIR"):
        out.append(env("PUFFPAI_STATE_DIR") or "")
    if env("PUFFPAI_DATA_DIR"):
        out.append(os.path.join(env("PUFFPAI_DATA_DIR") or "", "state"))
    try:
        from puffpai.config import _user_state_root
        out.append(_user_state_root())
    except Exception:  # noqa: BLE001 - the copies below still hold
        pass
    home = os.path.expanduser("~")
    out += [os.path.join(env("LOCALAPPDATA") or home, "PuffPAI"),
            os.path.join(home, "Library", "Application Support", "PuffPAI"),
            os.path.join(env("XDG_STATE_HOME") or os.path.join(home, ".local", "state"),
                         "puffpai")]
    return out


def _data_fallbacks() -> list[str]:
    """Where BuildBot's data folder (DATA_DIR) is: the loaded setting when it
    reads, and the places it would be by default."""
    out: list[str] = []
    try:
        from puffpai.config import get_config
        out.append(str(get_config().DATA_DIR or ""))
    except Exception:  # noqa: BLE001
        pass
    out.append(os.environ.get("PUFFPAI_DATA_DIR") or "")
    try:
        from puffpai.config import _find_repo_root
        out.append(_find_repo_root())
    except Exception:  # noqa: BLE001
        pass
    out.append(_repo_root())
    return [p for p in out if p]


def _vault_fallbacks() -> list[str]:
    """Where the vault would be: the place it recorded for itself, else
    ``vault_data`` in each default data folder."""
    out: list[str] = []
    try:
        from puffpai.security.vault import read_place
        out.append(read_place() or "")
    except Exception:  # noqa: BLE001
        pass
    out += [os.path.join(d, "vault_data") for d in _data_fallbacks()]
    return [p for p in out if p]


def _internal() -> list[tuple[str, str]]:
    """BuildBot's own places: state, apps, vault, data, linked, its code.

    Fail closed: each place is read on its own, and a setting that cannot be
    read keeps its default places (``fallback``) off limits instead of
    dropping them. On a computer where every setting reads, nothing changes.
    """
    out: list[tuple[str, str]] = []

    def put(got: Any, category: str) -> None:
        for p in ([got] if isinstance(got, str) else list(got or [])):
            if isinstance(p, str) and p.strip():
                out.append((p, category))

    def add(get: Any, category: str, what: str = "", fallback: Any = None) -> None:
        try:
            got = get()
        except Exception:  # noqa: BLE001 - keep the default places refused
            _unreadable(what or category)
            try:
                got = fallback() if fallback else []
            except Exception:  # noqa: BLE001
                got = []
        put(got, category)

    def state_dir() -> str:
        from puffpai.config import get_state_dir
        return get_state_dir()

    def config() -> Any:
        from puffpai.config import get_config
        return get_config()

    def apps_of(dirs: list[str]) -> list[str]:
        return [os.path.join(d, "host_apps") for d in dirs]

    add(state_dir, STATE, "the state folder", _state_fallbacks)
    add(lambda: os.path.join(state_dir(), "host_apps"), OTHER_APP, "the state folder",
        lambda: apps_of(_state_fallbacks()))

    def vaults() -> list[str]:
        folders: list[str] = []
        for name in ("configured_folder", "default_folder", "vault_dir"):
            try:
                from puffpai.security import owner_folders, vault_place
                mod = owner_folders if name == "vault_dir" else vault_place
                folders.append(getattr(mod, name)())
            except Exception:  # noqa: BLE001 - keep the default places refused
                _unreadable("the vault folder")
                folders += _vault_fallbacks()
        got = [f for f in folders if isinstance(f, str) and f.strip()]
        for f in list(got):
            got += [f + ".inbox"]
            try:
                from puffpai.security import vault_place
                got.append(vault_place.inbox_of(f))
            except Exception:  # noqa: BLE001 - f + ".inbox" is the same place
                pass
        return got
    add(vaults, VAULT, "the vault folder", lambda: [
        p for f in _vault_fallbacks() for p in (f, f + ".inbox")])

    def data_dir() -> str:
        from puffpai.knowledge import my_data
        return my_data.data_dir()

    def previous() -> list[str]:
        from puffpai.knowledge import my_data
        return my_data.previous_dirs()

    def pack_dir() -> str:
        from puffpai import setup_ops
        return setup_ops.default_pack_dir()

    def pack_fallback() -> list[str]:
        return [os.path.join(d, "knowledge_packs") for d in _data_fallbacks()]

    add(data_dir, DATA, "the data folder", pack_fallback)
    add(previous, DATA, "the earlier data folders")
    add(pack_dir, DATA, "the data folder", pack_fallback)

    def linked() -> list[str]:
        from puffpai.knowledge import my_data
        return [str(r.get("path")) for r in my_data.linked_folders()]
    add(linked, LINKED, "the linked folders")

    def install_root() -> str:
        from puffpai.build import apps
        return apps.install_root()
    add(install_root, CODE, "BuildBot's install folder", lambda: [_repo_root()])
    add(lambda: config().REPO_ROOT, CODE, "BuildBot's install folder", lambda: [_repo_root()])
    # DATA_DIR defaults to the install folder; where it was moved, it is data.
    add(lambda: config().DATA_DIR, DATA, "the data folder", _data_fallbacks)
    return out


_TOOL_PROGRAMS = ("node", "npm", "npx", "pnpm", "yarn", "corepack", "python", "python3", "py",
                  "pip", "pip3", "go", "ruby", "gem", "bundle", "docker", "git", "bun", "deno",
                  "java", "cargo", "uv")


def _toolchains(home: str) -> list[str]:
    """Read-only places: the folders of the toolchain programs and their caches."""
    env = os.environ.get
    j = os.path.join
    out: list[str] = []
    for name in _TOOL_PROGRAMS:
        exe = shutil.which(name)
        if not exe:
            continue
        for p in {exe, os.path.realpath(exe)}:
            d = os.path.dirname(p)
            out.append(d)
            if os.path.basename(d).lower() in ("bin", "cmd", "scripts"):
                out.append(os.path.dirname(d))
    out += [j(home, ".npm"), j(home, ".cache", "pip"), j(home, ".cache", "yarn"),
            j(home, ".cache", "pnpm"), j(home, ".local", "share", "pnpm"),
            j(home, "go", "pkg", "mod"), j(home, ".gem"), j(home, ".cargo", "registry"),
            j(home, "Library", "Caches", "pip"), j(home, "Library", "Caches", "Yarn")]
    appdata, la = env("APPDATA") or "", env("LOCALAPPDATA") or ""
    if appdata:
        out += [j(appdata, "npm-cache"), j(appdata, "npm")]
    if la:
        out += [j(la, "npm-cache"), j(la, "pip", "cache"), j(la, "pnpm"), j(la, "pnpm-cache"),
                j(la, "Yarn", "Cache"), j(la, "go-build")]
    for var in ("GOMODCACHE", "GOPATH", "PIP_CACHE_DIR", "npm_config_cache"):
        if env(var):
            out.append(env(var) or "")
    return out


@dataclass
class Zone:
    """One try's boundary. ``carve``: the try's own places (allowed even
    inside a denied area, like ``host_apps/<id>`` in the state folder)."""
    folder: str
    carve: list[str]
    deny: list[tuple[str, str]]
    toolchain: list[str]
    home: str
    scratch: str = ""
    env: dict = field(default_factory=dict)

    def classify(self, path: str, *, write: bool = False) -> tuple[str, str]:
        """``(ALLOW | DENY | OUTSIDE, category)`` for one path."""
        real = canon(path)
        if not real:
            return OUTSIDE, ""
        if any(inside_or_equal(real, c) for c in self.carve):
            return ALLOW, ""
        hits = [(p, cat) for p, cat in self.deny if inside_or_equal(real, p)]
        if hits:
            return DENY, max(hits, key=lambda h: len(h[0]))[1]
        # A folder that HOLDS one of them (the home folder, a drive, AppData).
        if os.path.dirname(real) == real or inside_or_equal(canon(self.home), real):
            return DENY, WHOLE
        held = [cat for p, cat in self.deny if inside_or_equal(p, real)]
        if held:
            return DENY, held[0]
        if not write and any(inside_or_equal(real, t) for t in self.toolchain):
            return ALLOW, ""
        return OUTSIDE, ""

    def refusal(self, path: str, *, write: bool = False) -> str:
        """The plain line when *path* is denied, else ""."""
        how, cat = self.classify(path, write=write)
        return refused_line(cat) if how == DENY else ""


def zone_for(folder: str, *, logs: str = "", scratch: str = "") -> Zone:
    """The boundary of a try working in *folder* (its *logs* and *scratch*
    folders allowed too)."""
    home = home_dir()
    deny = [(canon(p), cat) for p, cat in _internal() + _sensitive(home)]
    deny = [(p, cat) for p, cat in deny if p]
    tools = [canon(p) for p in _toolchains(home) if p]
    zone = Zone(folder=canon(folder) if folder else "", carve=[], deny=deny,
                toolchain=[t for t in tools if t], home=home, scratch=scratch,
                env=dict(os.environ))
    # Only a real try folder is allowed: one try folder directly under
    # host_apps (its -logs and -tmp beside it), or a folder of her code that
    # is not and does not hold a denied place. Anything else allows nothing.
    root = _apps_root()
    real = zone.folder
    if real and (_is_try_folder(real, root) or not folder_problem(real, zone)):
        for p in (folder, logs, scratch):
            c = canon(p) if p else ""
            if c and (inside_or_equal(c, real) or _is_try_folder(c, root)):
                zone.carve.append(c)
    return zone


def _apps_root() -> str:
    """``host_apps`` in the state folder. When the state folder's setting
    cannot be read, the most likely place (so a try still has its folder;
    every other place there stays refused)."""
    try:
        from puffpai.config import get_state_dir
        return canon(os.path.join(get_state_dir(), "host_apps"))
    except Exception:  # noqa: BLE001
        _unreadable("the state folder")
        return canon(os.path.join(_state_fallbacks()[0], "host_apps"))


def _is_try_folder(real: str, root: str) -> bool:
    """A folder directly under host_apps that is not one of BuildBot's own."""
    return bool(root) and os.path.dirname(real) == root and not         os.path.basename(real).startswith(".")


def folder_problem(path: str, zone: Optional[Zone] = None) -> str:
    """A refused line when a folder of her code cannot be Toolsmith's folder:
    inside or holding a denied place (inside her data folder, a linked folder
    or Documents is fine: only that subfolder is used). "" when it can."""
    real = canon(path)
    z = zone or zone_for("")
    if not real:
        return refused_line(WHOLE)
    if os.path.dirname(real) == real or inside_or_equal(canon(z.home), real):
        return refused_line(WHOLE)
    for p, cat in z.deny:
        if inside_or_equal(real, p) and (cat not in _INSIDE_OK or real == p):
            return refused_line(cat)
        if inside_or_equal(p, real) and real != p:
            return refused_line(cat)
    return ""


# ── reading a command the way a shell would ──────────────────────────────────

_LISTS = frozenset({"PATH", "PYTHONPATH", "NODE_PATH", "PSMODULEPATH", "PATHEXT",
                    "MANPATH", "INFOPATH", "LD_LIBRARY_PATH"})
_VAR = re.compile(r"\$\{env:(\w+)\}|\$env:(\w+)|\$\{(\w+)(?::?-([^}]*))?\}|\$(\w+)|%(\w+)%",
                  re.I)


def expand(text: str, zone: Zone, cwd: str, extra: Optional[dict] = None) -> str:
    """``~``, ``$HOME``, ``${X:-d}``, ``$env:X``, ``%X%`` and ``$PWD`` put in.
    Unknown names stay as they are."""
    env = {k.upper(): v for k, v in (zone.env or os.environ).items()}
    env.update({k.upper(): str(v) for k, v in (extra or {}).items()})
    env.setdefault("HOME", zone.home)
    env["PWD"] = cwd
    if zone.scratch:
        for k in ("TMP", "TEMP", "TMPDIR"):
            env[k] = zone.scratch

    def var(m: Any) -> str:
        name = next(g for g in (m.group(1), m.group(2), m.group(3), m.group(5), m.group(6))
                    if g)
        if name.upper() in _LISTS:
            return m.group(0)       # a search path list names no file to touch
        if name.upper() in env:
            return env[name.upper()]
        if m.group(4) is not None:
            return m.group(4)
        return m.group(0)
    text = _VAR.sub(var, text)
    return re.sub(r"(?:(?<=^)|(?<=[\s\"'=:(]))~(?=[\\/\s\"';|&)]|$)", lambda _m: zone.home,
                  text)


_OPS = re.compile(r"\|\||&&|>>|[12&]?>&?[12]?|>\||[|;&()<>\n`]")


def _lex(text: str) -> list[tuple[str, str]]:
    """``[(kind, text)]``: ``word`` (quotes taken off, backslashes kept) or ``op``."""
    out: list[tuple[str, str]] = []
    i, n = 0, len(text)
    word: Optional[str] = None
    while i < n:
        ch = text[i]
        if ch in " \t\r":
            if word is not None:
                out.append(("word", word))
                word = None
            i += 1
            continue
        if ch in "\"'":
            end = text.find(ch, i + 1)
            end = n if end < 0 else end
            word = (word or "") + text[i + 1:end]
            i = end + 1
            continue
        if text.startswith("$(", i):
            if word is not None:
                out.append(("word", word))
                word = None
            out.append(("op", "("))
            i += 2
            continue
        m = _OPS.match(text, i)
        if m and not (ch.isdigit() and word is not None):
            if word is not None:
                out.append(("word", word))
                word = None
            out.append(("op", m.group(0)))
            i = m.end()
            continue
        word = (word or "") + ch
        i += 1
    if word is not None:
        out.append(("word", word))
    return out


_CD = frozenset({"cd", "pushd", "chdir", "set-location", "sl", "push-location"})
_COPY = frozenset({"cp", "mv", "copy", "move", "xcopy", "robocopy", "rsync", "scp", "install",
                   "ln", "copy-item", "move-item", "cpi", "mi", "tee", "tee-object",
                   "out-file", "set-content", "add-content", "new-item", "ni", "mkdir", "md",
                   "touch", "rm", "del", "erase", "rmdir", "rd", "remove-item", "ri"})
_WRITE_FLAGS = frozenset({"-o", "--output", "-O", "--output-document", "-C", "--directory",
                          "-f", "--file", "-OutFile", "-outfile", "-Destination",
                          "-destination", "-Path", "-path", "-FilePath", "-filepath",
                          "--prefix", "--target", "-t", "--cache", "--cache-dir"})
_MOUNT_FLAGS = frozenset({"-v", "--volume", "--mount"})
_DRIVE = re.compile(r"^[A-Za-z]:(?:[\\/]|$)")
_GLOB = re.compile(r"[*?\[]")


def _to_host(word: str, cwd: str, zone: Zone, *, mount: bool = False) -> Optional[str]:
    """The host path a word names, or None when it is not a path here."""
    w = word.strip()
    if not w or "://" in w or w.startswith(("-", "@", "$")) or re.match(r"^[\w.-]+@[\w.-]+:", w):
        return None
    g = _GLOB.search(w)
    if g:
        w = w[:g.start()]
        w = w if w.endswith(("/", "\\")) or not w else os.path.dirname(w) or "."
    if os.name == "nt":
        m = re.match(r"^/(?:mnt/)?([A-Za-z])(?:/|$)(.*)$", w)
        if m:
            return f"{m.group(1).upper()}:\\" + m.group(2).replace("/", "\\")
        if re.match(r"^/tmp(?:/|$)", w):
            return os.path.join(zone.scratch or cwd, w[5:]) if zone.scratch else None
        if w == "/":
            return os.path.abspath(os.sep)      # Git's root reaches every drive
        if w.startswith("/") and not w.startswith("//"):
            return None             # Git's own files or a container path
        if _DRIVE.match(w):
            return w if len(w) > 2 else w + "\\"
        if w.startswith(("\\\\", "//")):
            return w
    elif w.startswith("/"):
        return w
    if mount and not w.startswith((".", "~")) and not re.search(r"[\\/]", w):
        return None                 # a named volume
    if not (re.search(r"[\\/]", w) or w in (".", "..") or w.startswith(".")):
        return os.path.join(cwd, w)
    return os.path.join(cwd, w)


def _mount_sources(flag: str, value: str) -> list[str]:
    if flag == "--mount":
        m = re.search(r"(?:^|,)(?:source|src)=([^,]+)", value)
        return [m.group(1)] if m else []
    m = re.match(r"^([A-Za-z]:[\\/][^:]*|[^:]+)(?::|$)", value)
    return [m.group(1)] if m else []


def _split_list(part: str) -> list[str]:
    """A path list split: ``;`` (Windows) and ``:``, never a drive's colon."""
    if os.name != "nt":
        return part.split(":")
    out: list[str] = []
    for chunk in part.split(";"):
        cur = ""
        for i, ch in enumerate(chunk):
            drive = len(cur) == 1 and cur.isalpha() and chunk[i + 1:i + 2] in ("\\", "/", "")
            if ch == ":" and not drive:
                out.append(cur)
                cur = ""
            else:
                cur += ch
        out.append(cur)
    return out


def _pieces(word: str) -> list[str]:
    """A word and its parts: ``--opt=value``, ``A=B``, ``a:b`` path lists."""
    out = [word]
    if "=" in word:
        out.append(word.split("=", 1)[1])
    for part in list(out):
        out += [b for b in _split_list(part) if b and b != part]
    return list(dict.fromkeys(out))


def _home_or_posix_rule(zone: Zone, path: str) -> bool:
    """On Linux and macOS an absolute path outside the home folder (a
    container path, /usr, /tmp) is not "outside" here (as before); denied
    places are still denied."""
    return os.name == "nt" or inside_or_equal(canon(path), canon(zone.home))


_OWN_CLI = re.compile(r"(?i)(?:^|[\s;&|(])(?:puffpai(?:\.cmd|\.exe)?(?=\s|$)|"
                      r"-m\s+puffpai\b|import\s+puffpai\b|from\s+puffpai\b)")


def command_check(command: str, zone: Zone, cwd: str = "") -> tuple[str, list[str]]:
    """``(refused line, outside paths)`` for one terminal command: the line
    when a path it names (read, written, ``cd``, mounted) is denied; else ""
    and the paths outside the allowed places (they wait for her yes)."""
    cwd = cwd or zone.folder
    if _OWN_CLI.search(command):
        return refused_line(CODE), []
    if re.search(r"\b(?:docker|podman)\b", command) and _ENGINE.search(command):
        return refused_line(ENGINE), []
    toks = _lex(expand(command, zone, cwd))
    outside: list[str] = []
    seg: list[str] = []
    redirect = ""

    def check(path: Optional[str], *, write: bool = False, mount: bool = False) -> str:
        if not path:
            return ""
        how, cat = zone.classify(path, write=write)
        if how == DENY:
            return refused_line(cat)
        if mount and how != ALLOW:
            real = canon(path)
            if real and (os.path.dirname(real) == real
                         or inside_or_equal(canon(zone.home), real)):
                return refused_line(WHOLE)
        if how == OUTSIDE and _home_or_posix_rule(zone, path):
            outside.append(path)
        return ""

    def end_segment() -> str:
        nonlocal cwd
        if not seg:
            return ""
        prog = os.path.basename(seg[0].replace("\\", "/")).lower()
        prog = re.sub(r"\.(exe|cmd|bat)$", "", prog)
        words = seg[1:]
        if prog in _CD:
            target = next((w for w in words if not w.startswith("-")), zone.home)
            dest = _to_host(target, cwd, zone) or target
            why = check(dest)
            if why:
                return why
            if zone.classify(dest)[0] == ALLOW:
                cwd = dest
            return ""
        why = check(_to_host(seg[0], cwd, zone)) if re.search(r"[\\/~]", seg[0]) else ""
        if why:
            return why
        positional = [w for w in words if not w.startswith("-")]
        for k, w in enumerate(words):
            prev = words[k - 1] if k else ""
            is_mount = prev in _MOUNT_FLAGS or w.startswith(("--volume=", "--mount="))
            if is_mount:
                value = w.split("=", 1)[1] if w.startswith("--") and "=" in w \
                    and prev not in _MOUNT_FLAGS else w
                for src in _mount_sources("--mount" if "--mount" in (prev, w[:7]) else "-v",
                                          value):
                    why = check(_to_host(src, cwd, zone, mount=True), mount=True)
                    if why:
                        return why
                continue
            write = prev in _WRITE_FLAGS or bool(prog in _COPY and positional
                                                 and w == positional[-1])
            for piece in _pieces(w):
                why = check(_to_host(piece, cwd, zone), write=write)
                if why:
                    return why
        return ""

    for kind, text in toks:
        if kind == "op":
            if text in (">", ">>", ">|", "1>", "2>", "&>") or text.endswith(">") and \
                    "&" not in text[1:]:
                redirect = "w"
                continue
            if text == "<":
                redirect = "r"
                continue
            why = end_segment()
            if why:
                return why, []
            seg = []
            continue
        if redirect:
            why = check(_to_host(text, cwd, zone), write=redirect == "w")
            redirect = ""
            if why:
                return why, []
            continue
        seg.append(text)
    why = end_segment()
    if why:
        return why, []
    return "", list(dict.fromkeys(outside))


# ── compose files ────────────────────────────────────────────────────────────

def compose_refusal(text: str, compose_dir: str, zone: Zone,
                    extra_env: Optional[dict] = None) -> str:
    """The refused line when a compose file mounts or reads a denied place
    (or the whole home folder or a drive), else ""."""
    if _ENGINE.search(text):
        return refused_line(ENGINE)
    try:
        import yaml  # type: ignore[import-untyped]
        data = yaml.safe_load(text)
    except Exception:  # noqa: BLE001 - unreadable: the line check below still runs
        data = None
    sources: list[tuple[str, bool]] = []
    if isinstance(data, dict):
        for svc in (data.get("services") or {}).values() if isinstance(
                data.get("services"), dict) else []:
            if not isinstance(svc, dict):
                continue
            for v in svc.get("volumes") or []:
                if isinstance(v, str):
                    sources += [(s, True) for s in _mount_sources("-v", v)]
                elif isinstance(v, dict) and v.get("source"):
                    sources.append((str(v["source"]), True))
            env_files = svc.get("env_file") or []
            for e in [env_files] if isinstance(env_files, (str, dict)) else env_files:
                p = e.get("path") if isinstance(e, dict) else e
                if p:
                    sources.append((str(p), False))
            build = svc.get("build")
            ctx = build.get("context") if isinstance(build, dict) else build
            if isinstance(ctx, str) and "://" not in ctx:
                sources.append((ctx, True))
        for key in ("volumes", "secrets", "configs"):
            block = data.get(key)
            if isinstance(block, dict):
                for v in block.values():
                    if isinstance(v, dict):
                        opts = v.get("driver_opts") or {}
                        if isinstance(opts, dict) and opts.get("device"):
                            sources.append((str(opts["device"]), True))
                        if v.get("file"):
                            sources.append((str(v["file"]), False))
    # The plain-text look too (no YAML reader here, or a file it cannot read):
    # every list item that looks like a mount, every source/device/file line.
    for m in re.finditer(r"(?m)^\s*-\s*[\"']?([^\s\"'#]+:[^\s\"'#]+)", text):
        sources += [(s, True) for s in _mount_sources("-v", m.group(1))]
    for m in re.finditer(r"(?m)^\s*-?\s*(?:source|device|file|context|env_file|path)\s*:"
                         r"\s*[\"']?([^\s\"'#]+)", text):
        sources.append((m.group(1), True))
    for raw, mount in sources:
        src = expand(raw, zone, compose_dir, extra_env)
        if mount and not src.startswith((".", "~", "/", "\\")) and not _DRIVE.match(src):
            continue                                    # a named volume
        path = _to_host(src, compose_dir, zone, mount=True) if mount else \
            _to_host(src, compose_dir, zone)
        if not path:
            continue
        how, cat = zone.classify(path)
        if how == DENY:
            return refused_line(cat)
        real = canon(path)
        if mount and real and (os.path.dirname(real) == real
                               or inside_or_equal(canon(zone.home), real)):
            return refused_line(WHOLE)
    return ""


# ── what leaves: her name out, and back in on the way in ─────────────────────

def _name_re(name: str) -> Optional[re.Pattern]:
    if not name or name.lower() == "you":
        return None
    if len(name) < 4:
        return re.compile(r"(?i)((?:[\\/]Users|[\\/]home)[\\/])" + re.escape(name)
                          + r"(?![\w-])")
    return re.compile(r"(?i)(?<![\w.-])()" + re.escape(name) + r"(?![\w-])")


def scrub(value: Any, name: Optional[str] = None) -> Any:
    """*value* (text, or dicts and lists of it) with her account name as "you"."""
    pat = _name_re(user_name() if name is None else name)
    if pat is None:
        return value

    def one(v: Any) -> Any:
        if isinstance(v, str):
            return pat.sub(lambda m: m.group(1) + "you", v)
        if isinstance(v, dict):
            return {k: one(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return type(v)(one(x) for x in v)
        return v
    return one(value)


def unscrub(value: Any, name: Optional[str] = None) -> Any:
    """The model's arguments with ``Users/you`` (as it was shown) back to her
    real home folder, so a path it copied still works here."""
    real = user_name() if name is None else name
    if _name_re(real) is None:
        return value
    pat = re.compile(r"(?i)((?:^|[\\/]|[\s\"'=:])(?:Users|home)[\\/])you(?=[\\/\s\"';|&)]|$)")

    def one(v: Any) -> Any:
        if isinstance(v, str):
            return pat.sub(lambda m: m.group(1) + real, v)
        if isinstance(v, dict):
            return {k: one(x) for k, x in v.items()}
        if isinstance(v, list):
            return [one(x) for x in v]
        return v
    return one(value)


# ── the shell Toolsmith gets ─────────────────────────────────────────────────

def shell_env(base: dict, scratch: str = "", shims: str = "") -> dict:
    """*base* without BuildBot's own command line and settings: no
    ``PUFFPAI_*`` names, no BuildBot shim folder or code on the path, its
    temp folders in the try's scratch folder."""
    from puffpai.config import get_state_dir
    state = canon(get_state_dir())
    try:
        from puffpai.build import apps
        code = canon(apps.install_root())
    except Exception:  # noqa: BLE001
        code = ""
    env = {k: v for k, v in base.items() if not k.upper().startswith("PUFFPAI")}
    for key in [k for k in env if k.upper() in ("PATH", "PYTHONPATH", "NODE_PATH")]:
        keep = [p for p in str(env[key]).split(os.pathsep)
                if p and not inside_or_equal(canon(p), state)
                and not (code and inside_or_equal(canon(p), code))]
        if key.upper() == "PATH" and shims:
            keep.insert(0, shims)
        if keep:
            env[key] = os.pathsep.join(keep)
        else:
            env.pop(key)
    if scratch:
        os.makedirs(scratch, exist_ok=True)
        for k in ("TMP", "TEMP", "TMPDIR"):
            env[k] = scratch
    return env


def python_shims(folder: str, exe: str) -> str:
    """A folder with ``python`` and ``python3`` only (never BuildBot's own
    ``puffpai`` command line); its path."""
    import sys
    exe = exe or sys.executable or "python"
    os.makedirs(folder, exist_ok=True)
    texts = {}
    for name in ("python3", "python"):
        texts[name] = '#!/bin/sh\nexec "{}" "$@"\n'.format(exe.replace("\\", "/"))
        if os.name == "nt":
            texts[name + ".cmd"] = '@"{}" %*\r\n'.format(exe)
    for name, text in texts.items():
        path = os.path.join(folder, name)
        try:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
            if os.name != "nt":
                os.chmod(path, 0o755)
        except OSError:
            pass
    return folder


__all__ = ["ALLOW", "BROWSER", "CODE", "DATA", "DENY", "KEYS", "LINKED", "OTHER_APP",
           "OUTSIDE", "PERSONAL", "REFUSED", "STARTUP", "STATE", "VAULT", "WHOLE", "Zone",
           "command_check", "compose_refusal", "expand", "folder_problem", "python_shims",
           "refused_line", "scrub", "shell_env", "unscrub", "zone_for"]

