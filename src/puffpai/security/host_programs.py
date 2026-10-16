"""The programs BuildBot starts on this computer, found by full path only.

Why this exists: a program found by NAME can be
swapped. Windows looks in the current folder before ``PATH`` for a bare name
in several APIs, and ``shutil.which`` walks a ``PATH`` any installer may have
changed. A ``wsl.exe`` dropped into a download folder, or a ``node.exe``
earlier on ``PATH``, would then run with BuildBot's rights.

So every program BuildBot starts that can reach the internet or the locked
box comes from :func:`path`, which answers a fixed, absolute location or
``None``:

* ``wsl``, ``taskkill``, ``tasklist``, ``powershell``, ``icacls``: the Windows system
  folder, found by asking Windows itself (``GetSystemDirectoryW``), not from
  the environment.
* ``edge``, ``chrome``: the browser's own registration (``App Paths`` in the
  registry, machine first, then the user), else its usual install folder.
* ``node``: the ``node.exe`` (``node`` on Linux and macOS) the installer
  wrote down in ``<install root>/node_path.txt``, and only when that is a
  full path to a real file outside the install, state and apps folders.
  Windows without a record: nodejs.org's own fixed folder
  (``%ProgramFiles%\nodejs\node.exe``, writable only by an administrator).
  Linux and macOS without a record fall back to the fixed places the
  official packages install to (below).
* Linux and macOS: fixed ``/usr/bin`` paths (plus, on macOS only, the two
  places the nodejs.org package and Homebrew put ``node``), or ``None``.
* ``docker``: only to run a Docker compose project she said yes to
  (``integrate.run_here``), never to change Docker's own settings. Docker
  Desktop's own install folder on Windows, fixed ``/usr/bin`` and
  ``/usr/local/bin`` places on Linux; on macOS Docker Desktop's link in
  ``/usr/local/bin``, Homebrew's ``/opt/homebrew/bin``, or the program inside
  ``/Applications/Docker.app``; or ``None``. Docker is never required.
* ``git``: Git for Windows' own folder; ``/usr/bin/git`` on Linux; on macOS
  Homebrew's (``/opt/homebrew/bin``, ``/usr/local/bin``) or the real program
  inside Apple's command line tools or Xcode, never ``/usr/bin/git``: on a Mac
  without those tools that is a stand-in that opens an "install the tools"
  box instead of running.
* ``winget`` (Windows only): the App Installer's own alias in the signed-in
  user's ``%LOCALAPPDATA%`` folder, ``Microsoft/WindowsApps``, where Windows itself
  puts it. Used only by "Install what's missing" (:mod:`puffpai.deps`), only
  for a program on BuildBot's own list, after the owner's yes.
* ``apt-get`` (Linux only): ``/usr/bin/apt-get``. Same rule.
* ``bwrap`` (bubblewrap, the Linux jail): ``/usr/bin/bwrap`` on Linux only.
  On Windows it is always ``None``: there "/usr/bin/bwrap" would mean
  ``C:\\usr\\bin\\bwrap.exe``, a folder any signed-in user may create.

npm is not started as a program at all: :func:`npm_cli` answers npm's own
``npm-cli.js`` inside the trusted node's install, and the caller runs it with
that node. No ``npm.cmd`` batch file, so no ``cmd.exe`` reading the
arguments either.

Never ``shutil.which``, never a bare name, never the current folder. Every
start also uses :func:`run_kwargs`: a fixed working folder (:data:`SAFE_CWD`)
and a small, fixed environment, so a program cannot pick up a DLL, a config
file or a proxy setting from wherever BuildBot happened to be standing.

``tests/test_outside_single_exit.py`` fails on any subprocess launch of a
network program by bare name or through ``shutil.which``.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import os
import sys
from typing import Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

#: The names :func:`path` knows. Anything else is a programming error.
NAMES = ("wsl", "edge", "chrome", "node", "taskkill", "tasklist", "powershell",
         "bwrap", "icacls", "docker", "winget", "apt-get", "git")

#: Where the three web-request programs live when the operating system or its
#: official package ships them. Fixed absolute places only, never ``PATH``,
#: never a folder a BuildBot command can write. ``curl`` ships with Windows
#: (System32) and with macOS; ``wget`` and ``httpie`` do not, so on Windows
#: they are always ``None`` and a key run that needs them is refused.
#: Answered by :func:`web_program` (not :func:`path`, whose list stays closed).
#: Used only by :mod:`puffpai.security.skill_key_use` to run a skill's own
#: request with its key; the key goes in the request's own argument, not here.
_WEB_LINUX = {
    "curl": ("/usr/bin/curl", "/bin/curl"),
    "wget": ("/usr/bin/wget", "/bin/wget"),
    "httpie": ("/usr/bin/http", "/usr/local/bin/http"),
}
_WEB_MAC = {
    "curl": ("/usr/bin/curl", "/opt/homebrew/bin/curl", "/usr/local/bin/curl"),
    "wget": ("/opt/homebrew/bin/wget", "/usr/local/bin/wget"),
    "httpie": ("/opt/homebrew/bin/http", "/usr/local/bin/http"),
}

_IS_WINDOWS = sys.platform == "win32"


def _windows_dir() -> str:
    """``C:\\Windows`` as Windows reports it (not from the environment)."""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(520)
        n = ctypes.windll.kernel32.GetSystemWindowsDirectoryW(buf, 520)
        if 0 < n < 520 and os.path.isabs(buf.value):
            return buf.value
    except Exception:  # pragma: no cover - only if kernel32 is unusable
        pass
    return os.environ.get("SystemRoot") or r"C:\Windows"


def _system32() -> str:
    """The 64-bit system folder (``%SystemRoot%\\System32``)."""
    windir = _windows_dir()
    # A 32-bit Python is redirected from System32 to SysWOW64; Sysnative is
    # the real System32 for it. A 64-bit Python has no Sysnative.
    native = os.path.join(windir, "Sysnative")
    if os.path.isdir(native):  # pragma: no cover - 32-bit Python only
        return native
    return os.path.join(windir, "System32")


if _IS_WINDOWS:
    SYSTEM32: str = _system32()
    #: Where every program BuildBot starts is started from.
    SAFE_CWD: str = SYSTEM32
else:
    SYSTEM32 = ""
    SAFE_CWD = "/"


def _file(candidate: Optional[str]) -> Optional[str]:
    """*candidate* when it is a full path to an existing regular file."""
    if not isinstance(candidate, str) or not candidate or "\0" in candidate:
        return None
    if not os.path.isabs(candidate):
        return None
    try:
        return candidate if os.path.isfile(candidate) else None
    except (OSError, ValueError):
        return None


def _app_paths(exe: str) -> Optional[str]:
    """The browser's own registration: ``App Paths\\<exe>``, HKLM then HKCU."""
    try:
        import winreg
    except ImportError:  # pragma: no cover - not Windows
        return None
    sub = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths" + "\\" + exe
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, sub) as key:
                value, kind = winreg.QueryValueEx(key, "")
        except OSError:
            continue
        if kind == winreg.REG_EXPAND_SZ and isinstance(value, str):
            value = winreg.ExpandEnvironmentStrings(value)
        if not isinstance(value, str):
            continue
        value = value.strip().strip('"').strip()
        if os.path.basename(value).lower() != exe.lower():
            continue
        found = _file(value)
        if found:
            return found
    return None


def _program_files(*parts: str) -> Optional[str]:
    for var in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(var)
        if base and os.path.isabs(base):
            found = _file(os.path.join(base, *parts))
            if found:
                return found
    return None


def _install_root() -> str:
    try:
        from puffpai.config import get_config
        root = get_config().REPO_ROOT
        if root:
            return os.path.abspath(root)
    except Exception:
        pass
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _under(root: str, candidate: str) -> bool:
    try:
        r = os.path.normcase(os.path.realpath(root))
        c = os.path.normcase(os.path.realpath(candidate))
    except (OSError, ValueError):
        return True  # cannot tell: treat as inside, so it is refused
    return c == r or c.startswith(r.rstrip("\\/") + os.sep)


def _forbidden_roots_for_node() -> list[str]:
    roots = [_install_root()]
    try:
        from puffpai.config import get_state_dir
        roots.append(get_state_dir())
    except Exception:
        pass
    try:
        from puffpai.security import safe_archive
        roots.append(safe_archive.configured_root())
    except Exception:
        pass
    return roots


def _outside_buildbot(found: str) -> Optional[str]:
    """*found* unless it sits inside the install, state or apps folder."""
    for root in _forbidden_roots_for_node():
        if _under(root, found):
            logger.warning("a node program inside a BuildBot folder was not used")
            return None
    return found


def _node_recorded(exe: str) -> Optional[str]:
    """The node program named in ``<install root>/node_path.txt``, checked."""
    record = os.path.join(_install_root(), "node_path.txt")
    try:
        with open(record, "r", encoding="utf-8") as fh:
            text = fh.read(4096)
    except OSError:
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) != 1:
        return None
    candidate = lines[0].strip('"')
    if os.path.basename(candidate).lower() != exe:
        return None
    found = _file(candidate)
    if not found:
        return None
    return _outside_buildbot(found)


def _node_windows() -> Optional[str]:
    """The recorded node, else the one nodejs.org's installer puts in its
    fixed Program Files folder (only an administrator can write there), so
    a computer that already has Node.js is never asked to install it again."""
    recorded = _node_recorded("node.exe")
    if recorded:
        return recorded
    found = _program_files("nodejs", "node.exe")
    return _outside_buildbot(found) if found else None


_LINUX_PATHS = {
    "edge": ("/usr/bin/microsoft-edge-stable", "/usr/bin/microsoft-edge"),
    "chrome": ("/usr/bin/google-chrome-stable", "/usr/bin/google-chrome",
               "/usr/bin/chromium", "/usr/bin/chromium-browser"),
    "node": ("/usr/bin/node",),
    "bwrap": ("/usr/bin/bwrap",),
    "docker": ("/usr/bin/docker", "/usr/local/bin/docker"),
    "apt-get": ("/usr/bin/apt-get",),
    "git": ("/usr/bin/git",),
}

#: macOS only: where the nodejs.org package (/usr/local) and Homebrew on
#: Apple silicon (/opt/homebrew) install node. Fixed places, never PATH.
_MAC_NODE = ("/opt/homebrew/bin/node", "/usr/local/bin/node")

#: macOS only, used INSTEAD of the Linux places: Docker Desktop's link,
#: Homebrew on Apple silicon, and the program inside Docker Desktop itself.
_MAC_DOCKER = ("/usr/local/bin/docker", "/opt/homebrew/bin/docker",
               "/Applications/Docker.app/Contents/Resources/bin/docker")

#: macOS only, used INSTEAD of the Linux place: Homebrew (Apple silicon, then
#: Intel), then the real git inside Apple's command line tools or Xcode.
#: Never /usr/bin/git, Apple's stand-in (see the module notes).
_MAC_GIT = ("/opt/homebrew/bin/git", "/usr/local/bin/git",
            "/Library/Developer/CommandLineTools/usr/bin/git",
            "/Applications/Xcode.app/Contents/Developer/usr/bin/git")


def path(name: str) -> Optional[str]:
    """The full path of *name* (one of :data:`NAMES`), or ``None``."""
    if name not in NAMES:
        raise ValueError(f"host_programs does not know a program called {name!r}")
    if not _IS_WINDOWS:
        if name == "node":
            recorded = _node_recorded("node")
            if recorded:
                return recorded
        candidates = _LINUX_PATHS.get(name, ())
        if name == "apt-get" and not sys.platform.startswith("linux"):
            return None
        if name == "node" and sys.platform == "darwin":
            candidates = candidates + _MAC_NODE
        if name == "docker" and sys.platform == "darwin":
            candidates = _MAC_DOCKER
        if name == "git" and sys.platform == "darwin":
            candidates = _MAC_GIT
        for candidate in candidates:
            found = _file(candidate)
            if found:
                return found
        return None
    if name == "wsl":
        return _file(os.path.join(SYSTEM32, "wsl.exe"))
    if name == "taskkill":
        return _file(os.path.join(SYSTEM32, "taskkill.exe"))
    if name == "tasklist":
        return _file(os.path.join(SYSTEM32, "tasklist.exe"))
    if name == "powershell":
        return _file(os.path.join(SYSTEM32, "WindowsPowerShell", "v1.0",
                                  "powershell.exe"))
    if name == "edge":
        return (_app_paths("msedge.exe")
                or _program_files("Microsoft", "Edge", "Application", "msedge.exe"))
    if name == "chrome":
        return (_app_paths("chrome.exe")
                or _program_files("Google", "Chrome", "Application", "chrome.exe"))
    if name == "bwrap":
        return None          # Linux only; never a path on the drive root here
    if name == "icacls":
        return _file(os.path.join(SYSTEM32, "icacls.exe"))
    if name == "docker":
        return _program_files("Docker", "Docker", "resources", "bin", "docker.exe")
    if name == "apt-get":
        return None
    if name == "git":
        return _program_files("Git", "cmd", "git.exe")
    if name == "winget":
        base = os.environ.get("LOCALAPPDATA") or ""
        if not os.path.isabs(base):
            return None
        alias = os.path.join(base, "Microsoft", "WindowsApps", "winget.exe")
        return alias if os.path.lexists(alias) else None
    return _node_windows()


def registered(target: str) -> Optional[str]:
    """A program the owner added herself (``<state>/programs.json``, kept by
    :mod:`puffpai.programs`), by its id or its full path: that full path when
    it is in her list and still a real file, else ``None``. A separate list
    from :data:`NAMES` (which stays closed): the path she chose, never a
    lookup by name, never ``PATH`` at start time."""
    if not isinstance(target, str) or not target or "\0" in target:
        return None
    try:
        from puffpai import programs
        rows = programs.rows()
    except Exception:  # pragma: no cover - an unreadable list is no program
        return None
    want = os.path.normcase(target)
    for row in rows:
        full = str(row.get("path") or "")
        if target == row.get("id") or (os.path.isabs(target) and os.path.normcase(full) == want):
            if not os.path.isabs(full):
                return None
            if os.path.isfile(full) or (sys.platform == "darwin" and full.lower().endswith(".app")
                                        and os.path.isdir(full)):
                return full
            return None
    return None


def web_program(name: str) -> Optional[str]:
    """``curl``, ``wget`` or ``httpie`` from a fixed system place, or ``None``
    (and ``None`` for ``wget``/``httpie`` on Windows: they do not ship with
    it, and ``PATH`` is not trusted here)."""
    if name not in ("curl", "wget", "httpie"):
        raise ValueError(f"web_program does not know {name!r}")
    if _IS_WINDOWS:
        if name != "curl":
            return None
        return _file(os.path.join(SYSTEM32, "curl.exe"))
    places = (_WEB_MAC if sys.platform == "darwin" else _WEB_LINUX).get(name, ())
    for candidate in places:
        found = _file(candidate)
        if found:
            return found
    return None


#: Seam for the tests (a Homebrew link needs a real symlink otherwise).
_realpath = os.path.realpath


def npm_cli(node: Optional[str] = None) -> Optional[str]:
    """npm's own ``npm-cli.js`` inside the install of *node* (default: the
    trusted :func:`path` ``("node")``), or ``None``.

    Run it as ``[node, npm_cli(), ...]``: npm then runs on the node BuildBot
    trusts, and nothing named ``npm`` is looked up anywhere. Only the places
    npm's own packages put it next to that node are tried, and the answer
    must be a real file outside the install, state and apps folders.
    """
    node = node if node is not None else path("node")
    if not node or not _file(node):
        return None
    try:
        real = _realpath(node)
    except (OSError, ValueError):
        return None
    here = os.path.dirname(real)
    tail = ("npm", "bin", "npm-cli.js")
    if _IS_WINDOWS:
        # nodejs.org's Windows install: node.exe with node_modules/npm
        # beside it, in the same folder.
        candidates = [os.path.join(here, "node_modules", *tail)]
    else:
        # <prefix>/bin/node and <prefix>/lib/node_modules/npm (nodejs.org,
        # nvm); Debian's own npm package lives in /usr/share/nodejs.
        # Homebrew: <brew>/bin/node is a link into
        # <brew>/Cellar/node/<version>/bin/node, and npm lives in that
        # version's libexec/lib/node_modules, with a copy in
        # <brew>/lib/node_modules (the prefix of the link itself, not of
        # the file it points at).
        candidates = []
        for prefix in (os.path.dirname(here), os.path.dirname(os.path.dirname(node))):
            for rel in (("lib", "node_modules"), ("libexec", "lib", "node_modules")):
                candidate = os.path.join(prefix, *rel, *tail)
                if candidate not in candidates:
                    candidates.append(candidate)
        if here == "/usr/bin":
            candidates.append(os.path.join("/usr/share/nodejs", *tail))
    for candidate in candidates:
        found = _file(candidate)
        if found:
            return _outside_buildbot(found)
    return None


def run_kwargs() -> dict:
    """``{"cwd": SAFE_CWD, "env": <a small fixed environment>}`` for a start."""
    if _IS_WINDOWS:
        windir = _windows_dir()
        env = {
            "SystemRoot": windir,
            "PATH": os.pathsep.join([
                SYSTEM32, windir, os.path.join(SYSTEM32, "Wbem"),
                os.path.join(SYSTEM32, "WindowsPowerShell", "v1.0")]),
        }
        for var in ("TEMP", "TMP", "LOCALAPPDATA", "USERPROFILE"):
            value = os.environ.get(var)
            if value and os.path.isabs(value):
                env[var] = value
        return {"cwd": SAFE_CWD, "env": env}
    env = {"PATH": "/usr/bin:/bin"}
    for var in ("HOME", "TMPDIR"):
        value = os.environ.get(var)
        if value and os.path.isabs(value):
            env[var] = value
    return {"cwd": SAFE_CWD, "env": env}


__all__ = ["NAMES", "SAFE_CWD", "SYSTEM32", "npm_cli", "path", "registered", "run_kwargs",
           "web_program"]
