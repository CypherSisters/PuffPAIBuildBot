"""``puffpai remove``: take BuildBot off this computer, and nothing else.

The owner deleted a test install's folder by hand while the bot was running; the
delete failed on a file the running web page held. Regular people will do the
same, so there is one plain way to do it: stop the bot, say exactly what will
be deleted (with sizes), ask once, delete.

The same plan is used by ``Remove BuildBot.cmd`` (install.ps1 writes it: it
copies ``remove-buildbot.ps1`` to %TEMP% and runs it from there) and by
``./install.sh remove``. On Windows, when this Python itself runs from inside
the BuildBot folder, the removal hands over to that script in a new window,
because Windows cannot delete a program that is running.

What it will never do:

* follow a link or junction: links are removed as links, their targets are
  never entered;
* delete outside the recorded roots (the install folder, the state folder,
  the apps folder or only this install's own app folders in it, and a box
  folder under the box root), or a root that is a link, a whole drive, the
  profile or one of its main folders, or anything holding one of those;
* take anything of another BuildBot's: an app folder or a box project goes
  only when its name is an id this install issued (:func:`own_ids`); the
  whole apps folder only when no other BuildBot is known and it is BuildBot's
  own place or holds nothing but this install's apps;
* delete the locked box's folder as part of the state folder: the box goes
  only through ``box.driver.remove_box`` (the unregister guard and the claim
  count), and stays when another BuildBot uses it;
* kill a process by its command-line text: only processes whose program file
  is inside the install folder, whose number is in a run record
  (:data:`RUN_RECORDS`) and started before that record was written, or that
  the operating system says has a program file loaded from a folder about to
  be deleted;
* delete anything while Windows still holds a file in it: it checks first,
  and deletes the program files before the settings and the vault, keeping
  what a second run needs until last. The vault's saved entry goes only
  after the vault folder itself.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess

from puffpai import procflags
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from puffpai.box import wording as W

_REPARSE = 0x400
_WEAK_KEYRINGS = ("keyring.backends.fail", "keyring.backends.null")
KEYRING_SERVICE_VAULT = "puffpai-buildbot"
KEYRING_SERVICE_KEYS = "puffpai"
RETRY_SECONDS = 30.0


def _optional(modname: str):
    try:
        return importlib.import_module(modname)
    except ModuleNotFoundError as e:
        if e.name is not None and (e.name == modname or modname.startswith(e.name + ".")):
            return None          # not in this build yet
        raise                    # exists but broken: fail loudly


# ── paths ────────────────────────────────────────────────────────────────────

def _canon(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _flat(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _inside_or_equal(child: str, parent: str) -> bool:
    if child == parent:
        return True
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:
        return False


def is_link(path: str) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & _REPARSE)


def _protected() -> list:
    """The profile, its main folders and the system places: never a root to
    delete, and never inside one."""
    env = os.environ.get
    out = []
    home = (env("USERPROFILE") if os.name == "nt" else None) or os.path.expanduser("~")
    out += [home, os.path.dirname(home)]
    try:
        from puffpai.security import owner_folders
        for paths in owner_folders.known_folders().values():
            out += paths
    except Exception:
        pass
    for name in ("Pictures", "Documents", "Music", "Videos", "Desktop", "Downloads"):
        out.append(os.path.join(home, name))
    for var in ("LOCALAPPDATA", "APPDATA", "TEMP", "TMP", "SystemRoot",
                "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        if env(var):
            out.append(env(var))
    if os.name != "nt":
        out += ["/", "/home", "/usr", "/etc", "/var", "/opt", "/tmp"]
    seen, result = set(), []
    for p in out:
        for form in (_flat(p), _canon(p)):
            if form not in seen:
                seen.add(form)
                result.append(form)
    return result


#: Files and folders only a BuildBot state folder holds. A state folder with
#: none of them is refused: a mistaken setting (say, a state folder pointed at
#: the owner's .ssh) must never be deleted after the yes.
STATE_SIGNS = ("buildbot.env", "build_id.json",
               os.path.join("config", "build_manifest.json"), "api_key.txt",
               "addition_checks.jsonl", "running.json", "pairing")


def env_file_value(path: str, key: str) -> str:
    """``KEY=value`` from an installer's env file, "" when absent."""
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            lines = fh.read(200_000).splitlines()
    except OSError:
        return ""
    for line in lines:
        k, sep, v = line.partition("=")
        if sep and k.strip() == key and not k.lstrip().startswith("#"):
            return v.strip()
    return ""


def state_owner(state_dir: str) -> str:
    """The install folder the state folder's own ``buildbot.env`` names."""
    return env_file_value(os.path.join(state_dir, "buildbot.env"), "PUFFPAI_CHECKOUT")


def root_problem(path: str, *, kind: str = "") -> str:
    """Why *path* may not be a root to delete ("" when it may)."""
    if is_link(path):
        return W.REMOVE_WHY_LINK
    c = _canon(path)
    if os.path.dirname(c) == c:
        return W.REMOVE_WHY_DRIVE
    for p in _protected():
        if _inside_or_equal(p, c):
            return W.REMOVE_WHY_MAIN_FOLDER
    if kind == "folder" and os.path.isdir(path):
        signs = (".puffpai-install.json", "install.ps1", "install.sh",
                 os.path.join("src", "puffpai", "__init__.py"))
        if not any(os.path.exists(os.path.join(path, s)) for s in signs):
            return W.REMOVE_WHY_NOT_BUILDBOT
    if kind == "state" and os.path.isdir(path):
        if not any(os.path.lexists(os.path.join(path, s)) for s in STATE_SIGNS):
            return W.REMOVE_WHY_NOT_BUILDBOT
    return ""


# ── deleting, never through a link ──────────────────────────────────────────

class StillInUse(OSError):
    """A file stayed locked for the whole retry time."""

    def __init__(self, path: str):
        super().__init__(path)
        self.path = path


class NameNotBack(OSError):
    """The busy check renamed *path* and could not put the name back."""

    def __init__(self, path: str, now: str):
        super().__init__(path)
        self.path = path
        self.now = now


class Changed(OSError):
    """Something under a folder being deleted stopped being what it was (a
    folder became a link, or moved out of its root). Deleting stops there."""

    def __init__(self, path: str, code: str = ""):
        super().__init__(path)
        self.path = path
        self.code = code


def _unlink_link(path: str) -> None:
    try:
        os.rmdir(path)          # a directory link or junction: the link only
    except OSError:
        os.unlink(path)


def _unlink_file(path: str) -> None:
    try:
        os.unlink(path)
    except PermissionError:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)   # read-only files (git keeps some)
        os.unlink(path)


def _rmdir(path: str) -> None:
    try:
        os.rmdir(path)
    except PermissionError:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        os.rmdir(path)


def delete_tree(path: str, *, allowed_roots: list, keeps: list = (),
                sleep: Callable[[float], None] = time.sleep,
                clock: Callable[[], float] = time.monotonic,
                wait_s: float = RETRY_SECONDS) -> dict:
    """Delete *path* without ever entering a link, and leave *keeps* alone.

    A link or junction is removed as a link. A folder that holds something
    kept stays (with only that inside). A file Windows still holds is tried
    again once a second for *wait_s* seconds, then :class:`StillInUse`.
    ``{"removed": int, "links_unlinked": int, "kept": int}``.

    Every folder that holds nothing kept goes through
    ``safe_rmtree``, which opens each child relative to its parent's open
    handle, so a junction swapped in at any moment is removed as a link and
    never entered. When it fails there is NO path-based walk after it: a lock
    is retried whole and then reported (:class:`StillInUse`), and a refusal
    (a link or a folder that moved out of its root) stops the delete
    (:class:`Changed`).
    """
    stats = {"removed": 0, "links_unlinked": 0, "kept": 0}
    if not os.path.lexists(path):
        return stats
    if not any(_inside_or_equal(_canon(path), _canon(a)) for a in allowed_roots):
        raise ValueError("outside the allowed roots")

    def retry(fn: Callable[[], Any], where: str) -> Any:
        until = clock() + wait_s
        while True:
            try:
                return fn()
            except FileNotFoundError:
                return None
            except OSError:
                if clock() >= until:
                    raise StillInUse(where)
                sleep(1.0)

    if is_link(path):
        retry(lambda: _unlink_link(path), path)
        stats["links_unlinked"] += 1
        return stats

    keep_forms = set()
    for k in keeps or ():
        keep_forms.add(_flat(k))
        keep_forms.add(_canon(k))

    def kept(c: str) -> bool:
        return any(_inside_or_equal(c, k) for k in keep_forms)

    def holds_kept(c: str) -> bool:
        return any(_inside_or_equal(k, c) for k in keep_forms)

    sa = _optional("puffpai.security.safe_archive")
    top = os.path.realpath(path)
    top_c = os.path.normcase(top)

    def safe(p: str) -> None:
        """One whole handle-based removal; retried as a whole while locked."""
        got = {}

        def once() -> None:
            got.clear()
            got.update(sa.safe_rmtree(p, allowed_roots=[top], _retry_seconds=0.0,
                                      _sleeper=sleep) or {})
        try:
            retry(once, p)
        except sa.SafeArchiveError as exc:
            raise Changed(p, getattr(exc, "code", "")) from None
        stats["removed"] += int(got.get("removed", 0))
        stats["links_unlinked"] += int(got.get("links_unlinked", 0))
        if os.path.lexists(p):
            raise StillInUse(p)

    def walk(p: str) -> None:
        if is_link(p):
            retry(lambda: _unlink_link(p), p)
            stats["links_unlinked"] += 1
            return
        c = os.path.normcase(p)
        if kept(c):
            stats["kept"] += 1
            return
        try:
            is_dir = stat.S_ISDIR(os.lstat(p).st_mode)
        except FileNotFoundError:
            return
        if not is_dir:
            retry(lambda: _unlink_file(p), p)
            stats["removed"] += 1
            return
        if not holds_kept(c) and sa is not None:
            safe(p)     # never followed by a path-based walk
            return
        # Only a folder that holds something kept (or a build without
        # safe_archive) is walked by path. Its real place is checked first,
        # so a folder swapped for a junction since the look above stops the
        # delete instead of being walked through.
        real_c = os.path.normcase(os.path.realpath(p))
        if real_c != c or not _inside_or_equal(real_c, top_c):
            raise Changed(p, "changed")
        names = retry(lambda: os.listdir(p), p) or []
        for name in names:
            walk(os.path.join(p, name))
        if holds_kept(c):
            return
        retry(lambda: _rmdir(p), p)
        stats["removed"] += 1

    walk(top)
    return stats


# ── processes ────────────────────────────────────────────────────────────────

def _processes_windows() -> list:
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                               wintypes.LPWSTR,
                                               ctypes.POINTER(wintypes.DWORD)]
    k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)
    if not snap or snap == wintypes.HANDLE(-1).value:
        return []
    out = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            pid, ppid = int(entry.th32ProcessID), int(entry.th32ParentProcessID)
            exe, created = "", None
            h = k32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
            if h:
                try:
                    buf = ctypes.create_unicode_buffer(32768)
                    size = wintypes.DWORD(32768)
                    if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                        exe = buf.value
                    times = [wintypes.FILETIME() for _ in range(4)]
                    if k32.GetProcessTimes(h, *[ctypes.byref(t) for t in times]):
                        ft = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
                        created = (ft - 116444736000000000) / 1e7
                finally:
                    k32.CloseHandle(h)
            out.append({"pid": pid, "ppid": ppid, "exe": exe, "created": created})
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return out


def _processes_linux() -> list:
    out = []
    try:
        btime = 0.0
        with open("/proc/stat", "r", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("btime "):
                    btime = float(line.split()[1])
        tck = os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError):
        return out
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        pid = int(name)
        try:
            exe = os.readlink(f"/proc/{pid}/exe")
        except OSError:
            exe = ""
        try:
            with open(f"/proc/{pid}/stat", "r", encoding="ascii", errors="replace") as fh:
                rest = fh.read().rsplit(")", 1)[1].split()
            ppid, created = int(rest[1]), btime + int(rest[19]) / tck
        except (OSError, IndexError, ValueError):
            ppid, created = 0, None
        out.append({"pid": pid, "ppid": ppid, "exe": exe, "created": created})
    return out


def list_processes() -> list:
    """``[{"pid","ppid","exe","created"}]``: program file and start time only.
    Command lines are never read."""
    try:
        return _processes_windows() if os.name == "nt" else _processes_linux()
    except Exception:
        return []


def _loaded_windows(pid: int) -> list:
    """The program files (the program and every library) *pid* has loaded,
    from a Toolhelp module snapshot. [] when this user may not look."""
    import ctypes
    from ctypes import wintypes

    class MODULEENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("th32ModuleID", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("GlblcntUsage", wintypes.DWORD),
                    ("ProccntUsage", wintypes.DWORD), ("modBaseAddr", ctypes.c_void_p),
                    ("modBaseSize", wintypes.DWORD), ("hModule", wintypes.HMODULE),
                    ("szModule", ctypes.c_wchar * 256), ("szExePath", ctypes.c_wchar * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Module32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
    k32.Module32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    snap = k32.CreateToolhelp32Snapshot(0x8 | 0x10, int(pid))  # MODULE | MODULE32
    if not snap or snap == wintypes.HANDLE(-1).value:
        return []
    out = []
    try:
        entry = MODULEENTRY32W()
        entry.dwSize = ctypes.sizeof(MODULEENTRY32W)
        ok = k32.Module32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szExePath:
                out.append(entry.szExePath)
            ok = k32.Module32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return out


def _loaded_linux(pid: int) -> list:
    """The files *pid* has mapped (its program and libraries). Its working
    folder is not counted: a shell sitting in the folder is the owner's."""
    out = []
    try:
        with open(f"/proc/{int(pid)}/maps", "r", encoding="utf-8",
                  errors="replace") as fh:
            for line in fh:
                parts = line.split(None, 5)
                if len(parts) == 6 and parts[5].startswith("/"):
                    out.append(parts[5].rstrip("\n").removesuffix(" (deleted)"))
    except OSError:
        pass
    return out


def loaded_files(pid: int) -> list:
    """What program files *pid* runs from: the operating system's own record,
    never what the program was started with."""
    try:
        return _loaded_windows(pid) if os.name == "nt" else _loaded_linux(pid)
    except Exception:
        return []


def _ancestors(procs: list, pid: int) -> set:
    parent = {p["pid"]: p["ppid"] for p in procs}
    seen = {pid}
    while pid in parent and parent[pid] not in seen and parent[pid] > 0:
        pid = parent[pid]
        seen.add(pid)
    return seen


#: Where a start is written down: Start and ``puffpai start`` (running.json),
#: start-bot.sh (running.env), the run-backend.ps1 / run-dashboard.ps1
#: launchers the wizard's closing screen names (their own number, "pid"), and
#: start-ollama.sh (ollama.env: the Ollama program BuildBot's setup downloaded
#: on a Mac, started by the launchers).
RUN_RECORDS = ("running.json", "running.env", "running.bot.json", "running.page.json",
               "ollama.env")


def _recorded(state_dir: str) -> list:
    """``[(pid, written)]``: each recorded number with its own record's time."""
    out = []
    for name in RUN_RECORDS:
        pids, written = _recorded_pids(state_dir, names=(name,))
        out += [(pid, written) for pid in pids]
    return out


def _recorded_pids(state_dir: str, names: tuple = RUN_RECORDS) -> tuple:
    """PIDs from the run records, and the newest record's time."""
    pids, newest = [], 0.0
    for name in names:
        path = os.path.join(state_dir, name)
        try:
            text = open(path, "r", encoding="utf-8-sig").read()
            newest = max(newest, os.path.getmtime(path))
        except OSError:
            continue
        values: dict = {}
        if name.endswith(".json"):
            try:
                data = json.loads(text)
                values = data if isinstance(data, dict) else {}
            except ValueError:
                values = {}
        else:
            for line in text.splitlines():
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
        for key in ("dashboard_pid", "backend_pid", "pid", "ollama_pid"):
            try:
                pid = int(values.get(key) or 0)
            except (TypeError, ValueError):
                pid = 0
            if pid > 0 and pid not in pids:
                pids.append(pid)
    return pids, newest


def _runs_from(files: list, roots: list) -> bool:
    for f in files or ():
        if not isinstance(f, str) or not f:
            continue
        c = _flat(f.removeprefix("\\\\?\\"))
        if any(_inside_or_equal(c, r) for r in roots):
            return True
    return False


def select_processes(procs: list, install_root: str, state_dir: str,
                     *, me: Optional[int] = None, roots: Optional[list] = None,
                     loaded: Optional[Callable[[int], list]] = None) -> list:
    """Which processes Remove BuildBot stops, in this order:

    * recorded by a start (see :data:`RUN_RECORDS`) and started before that
      record was written (a reused number is never touched);
    * program file inside the install folder;
    * with *loaded*: a program file (the program or a library it loaded) from
      inside one of *roots*, the folders about to be deleted. That is how the
      web page is found when its node.exe lives outside the BuildBot folder
      but runs the page's own native parts, the files Windows then refuses
      to delete. The operating system's module list decides, never the text
      a program was started with.

    Never this process or the ones that started it."""
    me = os.getpid() if me is None else me
    mine = _ancestors(procs, me)
    root = _canon(install_root)
    by_pid = {p["pid"]: p for p in procs}
    chosen = []
    for pid, written in _recorded(state_dir):
        proc = by_pid.get(pid)
        if proc is None or pid in mine or pid in chosen:
            continue
        created = proc.get("created")
        if created is None or created > written + 5:
            continue       # a reused number: not the process Start recorded
        chosen.append(pid)
    for proc in procs:
        pid, exe = proc["pid"], proc.get("exe") or ""
        if pid in mine or pid in chosen or pid <= 4 or not exe:
            continue
        if _inside_or_equal(_canon(exe), root):
            chosen.append(pid)
    if loaded is not None:
        forms = []
        for r in (roots if roots is not None else [install_root, state_dir]):
            if r:
                forms += [_flat(r), _canon(r)]
        for proc in procs:
            pid = proc["pid"]
            if pid in mine or pid in chosen or pid <= 4:
                continue
            if _runs_from(loaded(pid), forms):
                chosen.append(pid)
    return chosen


def still_running(install_root: str, state_dir: str, roots: list, *,
                  me: Optional[int] = None,
                  loaded: Callable[[int], list] = None) -> list:
    """Program names of what still runs from *roots* (for the owner's line)."""
    loaded = loaded or loaded_files
    procs = list_processes()
    pids = select_processes(procs, install_root, state_dir, me=me, roots=roots,
                            loaded=loaded)
    names = []
    by_pid = {p["pid"]: p for p in procs}
    for pid in pids:
        exe = (by_pid.get(pid) or {}).get("exe") or ""
        name = os.path.basename(exe) or f"program number {pid}"
        if name not in names:
            names.append(name)
    return names


def _system_program(name: str) -> Optional[str]:
    hp = _optional("puffpai.security.host_programs")
    if hp is not None:
        return hp.path(name)
    root = os.environ.get("SystemRoot") or os.environ.get("SYSTEMROOT") or ""
    if not root:
        return None
    rel = {"taskkill": ("System32", "taskkill.exe"),
           "powershell": ("System32", "WindowsPowerShell", "v1.0", "powershell.exe")}[name]
    path = os.path.join(root, *rel)
    return path if os.path.isfile(path) else None


def kill(pid: int) -> None:
    """Stop one process and its children, by number."""
    if os.name == "nt":
        tk = _system_program("taskkill")
        if not tk:
            return
        root = os.environ.get("SystemRoot") or ""
        subprocess.run([tk, "/PID", str(int(pid)), "/T", "/F"], capture_output=True,
                       stdin=subprocess.DEVNULL, check=False,
                       cwd=os.path.join(root, "System32") if root else None,
                       **procflags.hidden())
        return
    import signal
    # Its group only when it leads one that is not ours (procflags.own_group).
    group = procflags.own_group(pid)
    try:
        if group is None:
            raise OSError("not its own group")
        os.killpg(group, signal.SIGTERM)
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


#: How long Remove waits for what it stopped to be gone.
STOP_WAIT_SECONDS = 10.0


def stop_everything(install_root: str, state_dir: str,
                    roots: Optional[list] = None, *,
                    sleep: Callable[[float], None] = time.sleep,
                    clock: Callable[[], float] = time.monotonic) -> list:
    """Stop every process this install started (see :func:`select_processes`)
    and wait until they are gone, so no file of theirs is still held."""
    procs = list_processes()
    pids = select_processes(procs, install_root, state_dir, roots=roots,
                            loaded=loaded_files)
    started = {p["pid"]: p.get("created") for p in procs}
    for pid in pids:
        kill(pid)
    until = clock() + STOP_WAIT_SECONDS
    while pids and clock() < until:
        alive = {(p["pid"], p.get("created")) for p in list_processes()}
        if not any((pid, started.get(pid)) in alive for pid in pids):
            break
        sleep(0.25)
    for name in RUN_RECORDS:
        try:
            os.unlink(os.path.join(state_dir, name))
        except OSError:
            pass
    return pids


# ── saved entries (never read out, never printed) ───────────────────────────

def _keyring():
    try:
        import keyring
    except Exception:
        return None
    getter = getattr(keyring, "get_keyring", None)
    if callable(getter):
        try:
            module = type(getter()).__module__
        except Exception:
            return None
        if any(module.startswith(w) for w in _WEAK_KEYRINGS):
            return None
    return keyring


def keyring_names(state_dir: str, vault_dir: str) -> list:
    """The saved entries this install made: ``[(service, name)]`` candidates."""
    from puffpai.security import api_key_store, vault
    names = [(KEYRING_SERVICE_VAULT, vault._keyring_user(vault_dir))]  # noqa: SLF001
    try:
        # The machine key that seals the Home Assistant token and Jellyfin key.
        from puffpai.security import keys as _keys
        names.append((KEYRING_SERVICE_VAULT, _keys.house_keyring_user(state_dir)))
    except Exception:
        pass
    try:
        names.append((KEYRING_SERVICE_KEYS, api_key_store.entry_name(state_dir)))
        names += [(KEYRING_SERVICE_KEYS, n) for n in api_key_store.old_entry_names(state_dir)]
    except Exception:
        pass
    out = []
    for item in names:
        if item not in out:
            out.append(item)
    return out


def keyring_present(state_dir: str, vault_dir: str) -> list:
    ring = _keyring()
    if ring is None:
        return []
    present = []
    for service, name in keyring_names(state_dir, vault_dir):
        try:
            found = bool(ring.get_password(service, name))
        except Exception:
            found = False
        if found:
            present.append((service, name))
    return present


def keyring_delete(entries: list) -> int:
    ring = _keyring()
    if ring is None:
        return 0
    n = 0
    for service, name in entries:
        try:
            ring.delete_password(service, name)
            n += 1
        except Exception:
            pass
    return n


# ── the vault copy ───────────────────────────────────────────────────────────

def _copy_no_links(src: str, dest: str) -> None:
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        dirnames[:] = [d for d in dirnames if not is_link(os.path.join(dirpath, d))]
        rel = os.path.relpath(dirpath, src)
        target = os.path.join(dest, rel) if rel != "." else dest
        os.makedirs(target, exist_ok=True)
        for name in filenames:
            path = os.path.join(dirpath, name)
            if is_link(path):
                continue
            shutil.copy2(path, os.path.join(target, name))


def copy_vault(vault_dir: str) -> str:
    """Copy the vault (and its inbox) into Documents; returns the new folder."""
    from puffpai.security import owner_folders
    docs = owner_folders.documents_folder()
    if not docs:
        raise OSError("no Documents folder")
    base = os.path.join(docs, "BuildBot vault copy " + time.strftime("%Y-%m-%d"))
    dest, n = base, 2
    while os.path.lexists(dest):
        dest = f"{base} ({n})"
        n += 1
    _copy_no_links(vault_dir, os.path.join(dest, os.path.basename(vault_dir) or "vault"))
    inbox = vault_dir + ".inbox"
    if os.path.isdir(inbox) and not is_link(inbox):
        _copy_no_links(inbox, os.path.join(dest, os.path.basename(inbox)))
    return dest


# ── the plan ─────────────────────────────────────────────────────────────────

def _size(path: str, keeps: list) -> int:
    keep_forms = [_canon(k) for k in keeps]
    total = 0
    if not os.path.isdir(path) or is_link(path):
        return 0
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        dirnames[:] = [d for d in dirnames
                       if not is_link(os.path.join(dirpath, d))
                       and not any(_inside_or_equal(_canon(os.path.join(dirpath, d)), k)
                                   for k in keep_forms)]
        for name in filenames:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
    return total


#: The id BuildBot gives every app and tool it adds (integrate.records.ID_RE
#: and webapps.ID_RE: a name, a dash, 8 random letters and digits). The
#: apps folder and the locked box name their parts by these ids.
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,29}-[a-z2-7]{8}\Z")
_ISSUED_MAX_BYTES = 4_000_000


def own_ids(state_dir: str) -> set:
    """The ids this install gave the apps and tools it added: every id it ever
    issued (``<state>/webapps/issued_ids.txt``, appended before anything is
    unpacked or put in the box) and every record folder it keeps
    (``integrations``, ``webapps``, ``apps``). This record, not a folder's
    name or place, is how Remove knows a part is this install's."""
    out = set()
    if not state_dir or not os.path.isdir(state_dir) or is_link(state_dir):
        return out
    issued = os.path.join(state_dir, "webapps", "issued_ids.txt")
    try:
        if not is_link(issued):
            with open(issued, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read(_ISSUED_MAX_BYTES)
            out |= {line.strip() for line in text.splitlines() if ID_RE.match(line.strip())}
    except OSError:
        pass
    for sub in ("integrations", "webapps", "apps"):
        folder = os.path.join(state_dir, sub)
        if is_link(folder):
            continue
        try:
            out |= {n for n in os.listdir(folder) if ID_RE.match(n)}
        except OSError:
            pass
    return out


@dataclass
class Plan:
    install_root: str
    state_dir: str
    data_dir: str
    vault_dir: str
    apps_root: str
    apps_delete: bool                              # the whole apps folder goes
    roots: list = field(default_factory=list)      # [(kind, path, bytes)] that exist
    keeps: list = field(default_factory=list)
    box: Optional[dict] = None                     # {"distro","bytes","last"}
    keyring: list = field(default_factory=list)    # [(service, name)]
    vault_goes: bool = False
    data_stays: bool = False
    state_stays: bool = False                      # it belongs to another install
    refused: list = field(default_factory=list)    # [(path, why)]
    box_projects: list = field(default_factory=list)  # this install's, in a box that stays
    apps_others: bool = False                      # the apps folder holds more than ours

    def keeps_for(self, kind: str) -> list:
        """What a delete of *kind* leaves alone: an own app folder ("app")
        lives in the apps folder that is otherwise kept."""
        if kind != "app":
            return list(self.keeps)
        apps = _canon(self.apps_root)
        return [k for k in self.keeps if _canon(k) != apps]

    def covered(self, path: str) -> bool:
        c = _canon(path)
        for kind, p, _b in self.roots:
            if _inside_or_equal(c, _canon(p)) and not any(
                    _inside_or_equal(c, _canon(k)) for k in self.keeps_for(kind)):
                return True
        return False

    def nothing(self) -> bool:
        return (not self.roots and not self.keyring and not self.box_projects
                and not (self.box and self.box["last"]))


def _apps_root_is_buildbots(apps: str, inst: str, state: str) -> bool:
    """The apps folder is deleted only where BuildBot itself would put it: the
    usual place, or inside this install's own folders. A setting pointed
    somewhere else (a photo folder, say) is never taken as permission."""
    from puffpai.security import owner_folders
    c = _canon(apps)
    return (c == _canon(owner_folders.default_apps_root())
            or any(_inside_or_equal(c, _canon(r)) and c != _canon(r) for r in (inst, state)))


def _own_app_folders(apps: str, ids: set) -> list:
    """This install's own app folders in the apps folder: real folders (never
    a link) named by an id this install issued."""
    try:
        names = sorted(os.listdir(apps))
    except OSError:
        return []
    out = []
    for name in names:
        path = os.path.join(apps, name)
        if name in ids and not is_link(path) and os.path.isdir(path):
            out.append(path)
    return out


def build_plan(*, look_in_box: bool = True) -> Plan:
    """What Remove BuildBot would do. *look_in_box* False skips the box (the
    keyring-only step, run after the folders are partly gone)."""
    from puffpai.box import driver
    from puffpai.config import get_config, get_state_dir
    from puffpai.security import owner_folders

    inst = driver.install_root()
    state = os.path.abspath(get_state_dir())
    data = os.path.abspath(get_config().DATA_DIR)
    vd = owner_folders.vault_dir()
    apps = owner_folders.apps_root()
    others = [r for r in driver.known_install_roots() if _canon(r) != _canon(inst)]
    plan = Plan(install_root=inst, state_dir=state, data_dir=data, vault_dir=vd,
                apps_root=apps, apps_delete=False)
    owner = state_owner(state) if os.path.isdir(state) else ""
    if owner and _canon(owner) != _canon(inst):
        # Its own env file names another BuildBot folder: those settings are
        # that install's, so they stay (and so do its saved entries).
        plan.state_stays = True
    ids = set() if plan.state_stays else own_ids(state)
    own_apps = _own_app_folders(apps, ids) if os.path.isdir(apps) and not is_link(apps) else []
    if os.path.lexists(apps) and not others:
        # The whole apps folder goes when no other BuildBot is known and it is
        # BuildBot's own place, or holds nothing but this install's apps.
        try:
            entries = [os.path.join(apps, n) for n in os.listdir(apps)]
        except OSError:
            entries = None
        only_ours = entries is not None and all(e in own_apps for e in entries)
        plan.apps_delete = _apps_root_is_buildbots(apps, inst, state) or only_ours
    plan.keeps = [driver.box_root()] + ([] if plan.apps_delete else [apps])
    if plan.state_stays:
        plan.keeps.append(state)
    wanted = [("folder", inst)] + ([] if plan.state_stays else [("state", state)])
    if plan.apps_delete:
        wanted.append(("apps", apps))
    elif own_apps:
        # Only this install's own app folders; the rest of the apps folder
        # stays (another BuildBot, or something that is not BuildBot's).
        if is_link(apps) or root_problem(apps, kind="apps"):
            wanted.append(("apps", apps))           # refused below, with its reason
        else:
            wanted += [("app", p) for p in own_apps]
    for kind, path in wanted:
        if not os.path.lexists(path):
            continue
        why = root_problem(path, kind=kind)
        if not why and kind == "apps" and not plan.apps_delete:
            why = W.REMOVE_WHY_LINK if is_link(path) else (
                root_problem(path) or W.REMOVE_WHY_NOT_BUILDBOT)
        if why:
            plan.refused.append((path, why))
            continue
        plan.roots.append((kind, path, _size(path, plan.keeps_for(kind))))
    if not plan.apps_delete and os.path.isdir(apps):
        try:
            plan.apps_others = any(os.path.join(apps, n) not in own_apps for n in os.listdir(apps))
        except OSError:
            plan.apps_others = True
    if look_in_box:
        try:
            plan.box = driver.plan_for_removal()
        except Exception:
            plan.box = None
        if ids and not (plan.box and plan.box.get("last")):
            # The box stays (another BuildBot uses it, or it is not this
            # install's to remove): take out only what this install put in it.
            try:
                present = driver.projects_in_box() or []
            except Exception:
                present = []
            plan.box_projects = sorted(p for p in present if p in ids)
    plan.keyring = keyring_present(state, vd)
    if plan.state_stays:
        plan.keyring = [e for e in plan.keyring if e[0] != KEYRING_SERVICE_KEYS]
    plan.vault_goes = os.path.isdir(vd) and plan.covered(vd)
    plan.data_stays = os.path.isdir(data) and not plan.covered(data)
    return plan


def plan_lines(plan: Plan) -> tuple:
    """The screen, in two parts: up to the vault question, and the rest."""
    first = [W.REMOVE_TITLE, "", W.REMOVE_INTRO]
    labels = {"folder": W.REMOVE_FOLDER, "state": W.REMOVE_STATE, "apps": W.REMOVE_APPS}
    for kind, path, n in plan.roots:
        if kind in labels:
            first.append(labels[kind].format(path=path, size=W.size_words(n)))
    own = [(p, n) for kind, p, n in plan.roots if kind == "app"]
    if own:
        tmpl = W.REMOVE_APP_OWN if len(own) == 1 else W.REMOVE_APPS_OWN
        first.append(tmpl.format(n=len(own), path=plan.apps_root,
                                 size=W.size_words(sum(n for _p, n in own))))
    if plan.box and plan.box.get("last"):
        first.append(W.REMOVE_BOX.format(distro=plan.box["distro"],
                                         size=W.size_words(plan.box.get("bytes", 0))))
    elif plan.box_projects:
        first.append(W.REMOVE_BOX_PROJECT if len(plan.box_projects) == 1
                     else W.REMOVE_BOX_PROJECTS.format(n=len(plan.box_projects)))
    if plan.keyring:
        first.append(W.keyring_line(len(plan.keyring), windows=os.name == "nt"))
    if plan.vault_goes:
        first += [W.REMOVE_VAULT, W.REMOVE_VAULT_ASK]
    rest = []
    if plan.state_stays:
        rest.append(W.REMOVE_STATE_STAYS.format(path=plan.state_dir))
    if plan.data_stays:
        rest.append(W.REMOVE_DATA_STAYS.format(path=plan.data_dir))
    if not plan.apps_delete and os.path.isdir(plan.apps_root) and plan.apps_others:
        has_own = any(kind == "app" for kind, _p, _n in plan.roots)
        rest.append((W.REMOVE_OTHER_APPS_STAY if has_own else W.REMOVE_APPS_STAY)
                    .format(path=plan.apps_root))
    rest += [W.REMOVE_OWN_FILES, "", W.REMOVE_CONFIRM]
    return first, rest


def facts(plan: Plan) -> dict:
    """What remove-buildbot.ps1 asks Python for (never a secret)."""
    return {"install_root": plan.install_root, "state_dir": plan.state_dir,
            "data_dir": plan.data_dir, "vault_dir": plan.vault_dir,
            "apps_root": plan.apps_root, "apps_delete": plan.apps_delete,
            "apps_own": [p for kind, p, _n in plan.roots if kind == "app"],
            "apps_others": plan.apps_others,
            "box": ({"distro": plan.box["distro"], "bytes": plan.box.get("bytes", 0)}
                    if plan.box and plan.box.get("last") else None),
            "box_shared": bool(plan.box and not plan.box.get("last")),
            "box_projects": len(plan.box_projects),
            "keyring": len(plan.keyring), "keeps": list(plan.keeps),
            "state_stays": plan.state_stays,
            "refused": [{"path": p, "why": w} for p, w in plan.refused]}


# ── doing it ─────────────────────────────────────────────────────────────────

def _say(text: str = "", *, end: str = "\n") -> None:
    print(text, end=end, flush=True)


def remove_box_parts(plan: Plan) -> list:
    """This install's own projects in a box that stays (named by its own
    ids), then the box itself through its guard and claim count. Returns the
    own projects still there."""
    from puffpai.box import driver
    left: list = []
    if plan.box_projects:
        try:
            left = driver.remove_projects(plan.box_projects)
        except Exception:
            left = list(plan.box_projects)
    if plan.box and not plan.box.get("last"):
        try:
            driver.drop_deny_list()     # its private folders' list, in a box that stays
        except Exception:
            pass
    try:
        got = driver.remove_box()
    except Exception:
        got = {"removed": False, "why": ""}
    if got.get("why") == W.SHARED_STAYS:
        _say(W.SHARED_STAYS)
    return left


def remove_entries(plan: Plan) -> None:
    """The saved entries (the vault's and the API key's). Run after the vault
    folder is gone: deleting the vault's entry first would leave a vault no
    one can open if the delete then stopped part way."""
    keyring_delete(plan.keyring)


def remove_box_and_entries(plan: Plan) -> None:
    """Both steps at once (older removal scripts call ``--only box,keyring``)."""
    remove_box_parts(plan)
    remove_entries(plan)


def _rename_free(path: str, *, sleep: Callable[[float], None] = time.sleep) -> bool:
    """Windows: is anything holding a file in *path* open, or sitting in it?

    Renaming a folder fails while any file inside it is open or any program
    works inside it; the name is put straight back. (A loaded program library
    does not stop a rename: those are found by :func:`select_processes`.)"""
    tmp = f"{path}.puffpai-check-{secrets.token_hex(4)}"
    try:
        os.rename(path, tmp)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    for _ in range(40):
        try:
            os.rename(tmp, path)
            return True
        except OSError:
            sleep(0.25)
    raise NameNotBack(path, tmp)


def busy_path(plan: Plan, *, sleep: Callable[[float], None] = time.sleep) -> str:
    """The first thing about to be deleted that Windows still holds, or ""."""
    if os.name != "nt":
        return ""   # other systems delete files that are open
    keeps: list = []

    def probe(p: str) -> str:
        if not os.path.lexists(p) or is_link(p):
            return ""
        c = _flat(p)
        if any(_inside_or_equal(c, k) for k in keeps):
            return ""
        if any(_inside_or_equal(k, c) for k in keeps):
            try:
                names = os.listdir(p)
            except OSError:
                return p
            for name in names:
                got = probe(os.path.join(p, name))
                if got:
                    return got
            return ""
        return "" if _rename_free(p, sleep=sleep) else p

    for kind, path, _n in plan.roots:
        forms = plan.keeps_for(kind)
        keeps[:] = [_canon(k) for k in forms] + [_flat(k) for k in forms]
        got = probe(path)
        if got:
            return got
    return ""


#: Kept until last in the BuildBot folder: what a second run needs to know
#: the folder is BuildBot's and to start (the removal files, the signs and,
#: for "bash install.sh remove", the Python it runs with).
FOLDER_LAST = ("Remove BuildBot.cmd", "remove-buildbot.ps1", ".puffpai-install.json",
               "install.ps1", "install.sh", os.path.join("src", "puffpai", "__init__.py"),
               "venv")


def _phases(plan: Plan) -> list:
    """``[(kind, path, keep_for_now)]`` in the order that leaves a re-runnable
    install at every step: the program files first, then the apps, then the
    settings, then the vault (what cannot come back goes last), then the
    saved entries (``("keyring", "", [])``: the vault's own entry goes only
    after the vault), then the signs and the removal files."""
    roots = {kind: path for kind, path, _n in plan.roots if kind != "app"}
    own_apps = [path for kind, path, _n in plan.roots if kind == "app"]
    folder, state, apps = roots.get("folder"), roots.get("state"), roots.get("apps")
    vault = [p for p in (plan.vault_dir, plan.vault_dir + ".inbox")
             if plan.vault_goes and os.path.lexists(p) and plan.covered(p)]
    folder_last = [os.path.join(folder, n) for n in FOLDER_LAST] if folder else []
    state_last = [os.path.join(state, s) for s in STATE_SIGNS] if state else []
    later = [p for p in (state, apps) if p] + own_apps
    out = []
    if folder:
        out.append(("folder", folder, later + vault + folder_last))
    if apps:
        out.append(("apps", apps, vault))
    out += [("app", p, vault) for p in own_apps]
    if own_apps and not apps:
        out.append(("apps_empty", plan.apps_root, []))
    if state:
        out.append(("state", state, [p for p in (apps,) if p] + own_apps + vault + state_last))
    out += [("vault", v, []) for v in vault]
    out.append(("keyring", "", []))
    if state:
        out.append(("state", state, []))
    if folder:
        out.append(("folder", folder, []))
    return out


def _say_left(plan: Plan) -> None:
    """What is still here, in plain words."""
    lines = []
    vault = [p for p in (plan.vault_dir, plan.vault_dir + ".inbox")
             if plan.vault_goes and os.path.lexists(p)]
    for kind, path, _n in plan.roots:
        if not os.path.lexists(path):
            continue
        if kind == "folder":
            lines.append(W.REMOVE_LEFT_FOLDER.format(path=path))
        elif kind == "state":
            lines.append(W.REMOVE_LEFT_STATE.format(path=path))
        elif kind == "app":
            line = W.REMOVE_LEFT_APPS.format(path=plan.apps_root)
            if line not in lines:
                lines.append(line)
        else:
            lines.append(W.REMOVE_LEFT_APPS.format(path=path))
    if vault:
        lines.append(W.REMOVE_LEFT_VAULT.format(path=vault[0]))
    if lines:
        _say(W.REMOVE_STILL_HERE)
        for line in lines:
            _say(line)


def _rmdir_if_empty(path: str) -> bool:
    """The apps folder, once this install's own apps are out of it, when
    nothing else is in it (rmdir never deletes anything inside a folder) and
    it is not a link or one of the owner's main places."""
    if not os.path.isdir(path) or is_link(path) or root_problem(path):
        return False
    try:
        os.rmdir(path)
        return True
    except OSError:
        return False


def _stop_roots(plan: Plan) -> list:
    return [path for _kind, path, _n in plan.roots]


def do_remove(plan: Plan, *, keep_vault: bool,
              sleep: Callable[[float], None] = time.sleep,
              clock: Callable[[], float] = time.monotonic) -> int:
    # This process must not sit in a folder it deletes.
    try:
        here = _flat(os.getcwd())
        if any(_inside_or_equal(here, _flat(p)) for p in _stop_roots(plan)):
            os.chdir(tempfile.gettempdir())
    except OSError:
        pass
    _say(W.REMOVE_STOPPING, end="")
    stop_everything(plan.install_root, plan.state_dir, _stop_roots(plan),
                    sleep=sleep, clock=clock)
    _say(W.REMOVE_DONE)
    # Nothing is deleted until nothing holds any of it: a bot left without its
    # settings and vault while its web page still runs is the worst end.
    try:
        busy = busy_path(plan, sleep=sleep)
        until = clock() + RETRY_SECONDS
        while busy and clock() < until:
            sleep(1.0)
            stop_everything(plan.install_root, plan.state_dir, _stop_roots(plan),
                            sleep=sleep, clock=clock)
            busy = busy_path(plan, sleep=sleep)
    except NameNotBack as exc:
        _say(W.REMOVE_NAME_NOT_BACK.format(path=exc.path, now=exc.now))
        return 1
    if busy:
        _say(W.REMOVE_IN_USE_WINDOWS.format(path=busy))
        names = still_running(plan.install_root, plan.state_dir, _stop_roots(plan))
        if names:
            _say(W.REMOVE_IN_USE_BY.format(names=", ".join(names)))
        return 1
    if keep_vault and plan.vault_goes:
        try:
            dest = copy_vault(plan.vault_dir)
        except Exception:
            _say(W.REMOVE_VAULT_COPY_FAILED)
            return 1
        _say(W.REMOVE_VAULT_COPIED.format(path=dest))
    # The box first (this install's own projects in it, then the box through
    # its guard): it is found through this install's records and folder
    # names, which are gone afterwards. The saved entries go after the vault.
    remove_box_parts(plan)
    _say(W.REMOVE_DELETING, end="")
    for kind, path, keep_now in _phases(plan):
        if kind == "keyring":
            remove_entries(plan)
            continue
        if kind == "apps_empty":
            _rmdir_if_empty(path)
            continue
        if not os.path.lexists(path):
            continue
        try:
            delete_tree(path, allowed_roots=[path], keeps=plan.keeps_for(kind) + keep_now,
                        sleep=sleep, clock=clock)
        except (StillInUse, Changed) as exc:
            _say("")
            if isinstance(exc, Changed):
                _say(W.REMOVE_PART_WAY_CHANGED.format(path=exc.path))
            else:
                where = (exc.path if os.path.isdir(exc.path)
                         else os.path.dirname(exc.path) or exc.path)
                tmpl = (W.REMOVE_PART_WAY_WINDOWS if os.name == "nt"
                        else W.REMOVE_PART_WAY_OTHER)
                _say(tmpl.format(path=where))
            _say_left(plan)
            again = (W.REMOVE_RUN_AGAIN_WINDOWS if os.name == "nt"
                     else W.REMOVE_RUN_AGAIN_OTHER)
            _say(again.format(path=plan.install_root))
            return 1
    _say(W.REMOVE_DONE)
    _say(W.REMOVE_FINISHED)
    return 0


def _running_from(root: str) -> bool:
    c = _canon(root)
    return any(_inside_or_equal(_canon(p), c) for p in (sys.executable, sys.prefix) if p)


def _handoff(plan: Plan, args: argparse.Namespace) -> int:
    """Windows: hand over to remove-buildbot.ps1, run from %TEMP% in a new window."""
    script = os.path.join(plan.install_root, "remove-buildbot.ps1")
    ps = _system_program("powershell")
    if not os.path.isfile(script) or not ps:
        _say(W.REMOVE_USE_CMD.format(path=plan.install_root))
        return 1
    tmpdir = tempfile.gettempdir()
    copy = os.path.join(tmpdir, f"puffpai-remove-{secrets.token_hex(6)}.ps1")
    shutil.copyfile(script, copy)
    argv = [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", copy,
            "-InstallRoot", plan.install_root]
    if getattr(args, "yes", False):
        argv.append("-Yes")
    if getattr(args, "keep_vault", False):
        argv.append("-KeepVault")
    subprocess.Popen(argv, cwd=tmpdir, close_fds=True,
                     creationflags=0x00000010 | 0x00000200)  # new console, new group
    _say(W.REMOVE_NEW_WINDOW)
    return 0


def _ask() -> str:
    try:
        return input()
    except EOFError:
        return ""


def cmd_remove(args: argparse.Namespace) -> int:
    only = getattr(args, "only", "") or ""
    if only == "keyring":
        # remove-buildbot.ps1's step after the vault is gone: the folders are
        # part way deleted, so only the saved entries are looked at.
        if not getattr(args, "yes", False):
            return 2
        remove_entries(build_plan(look_in_box=False))
        return 0
    plan = build_plan()
    if getattr(args, "facts", False):
        print(json.dumps(facts(plan)))
        return 0
    if only:
        if not getattr(args, "yes", False):
            return 2
        if only == "box":
            left = remove_box_parts(plan)
            print(json.dumps({"box_projects_left": len(left)}))
            return 0
        if only == "stop":
            # remove-buildbot.ps1's stop step: what Windows PowerShell cannot
            # see quickly (programs running this install's files from
            # elsewhere). Names only, never a path or a secret.
            pids = stop_everything(plan.install_root, plan.state_dir, _stop_roots(plan))
            still = still_running(plan.install_root, plan.state_dir, _stop_roots(plan))
            print(json.dumps({"stopped": len(pids), "still": still}))
            return 0
        if only != "box,keyring":
            return 2
        remove_box_and_entries(plan)
        return 0
    if plan.refused:
        for path, why in plan.refused:
            _say(W.REMOVE_REFUSED.format(path=path, why=why))
        return 2
    if plan.nothing():
        _say(W.REMOVE_NOTHING)
        return 0
    if os.name == "nt" and _running_from(plan.install_root):
        return _handoff(plan, args)
    first, rest = plan_lines(plan)
    for line in first:
        _say(line)
    keep = bool(getattr(args, "keep_vault", False))
    if not getattr(args, "yes", False):
        if plan.vault_goes:
            keep = _ask().strip().lower() == "k"
        for line in rest:
            _say(line)
        if _ask().strip().lower() not in ("y", "yes"):
            _say(W.REMOVE_KEPT)
            return 1
    else:
        for line in rest[:-2]:
            _say(line)
    return do_remove(plan, keep_vault=keep)


def add_parsers(subparsers: Any) -> None:
    sub = subparsers.add_parser(
        "remove", help="Remove BuildBot from this computer (it asks first)")
    _add_arguments(sub)
    sub.set_defaults(func=cmd_remove)


def _add_arguments(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--yes", action="store_true",
                     help="Do not ask (for scripts). The vault is deleted "
                          "unless --keep-vault is given too")
    sub.add_argument("--keep-vault", dest="keep_vault", action="store_true",
                     help="Keep a copy of the vault in Documents first")
    sub.add_argument("--facts", action="store_true", help=argparse.SUPPRESS)
    sub.add_argument("--only", default="", help=argparse.SUPPRESS)


def main(argv: Optional[list] = None) -> int:
    """``python -m puffpai.remove_cli``: the same command, used by the
    removal scripts so they work whatever else this build wires up."""
    from puffpai import install_pointer
    install_pointer.apply()
    parser = argparse.ArgumentParser(prog="puffpai remove")
    _add_arguments(parser)
    return cmd_remove(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["Changed", "ID_RE", "Plan", "RUN_RECORDS", "StillInUse", "add_parsers",
           "own_ids", "remove_box_parts", "remove_entries",
           "build_plan", "busy_path", "cmd_remove", "loaded_files", "still_running",
           "delete_tree", "do_remove", "facts", "keyring_names", "keyring_present",
           "env_file_value", "list_processes", "plan_lines", "root_problem",
           "select_processes", "state_owner", "STATE_SIGNS"]
