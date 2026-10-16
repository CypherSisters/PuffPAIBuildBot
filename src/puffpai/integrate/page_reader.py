"""Read what an app on this computer shows, the way a person sees it.

``read(url) -> {"title", "text", "links"} | None``: a headless Chromium-family
browser (the owner's default when it is one, :func:`browser_path`; its own empty
profile; no window) opens *url*, which must be on 127.0.0.1, and BuildBot
waits in real time while the page fills itself in (an app that loads its
news, prices or lists after the first paint), until its visible text stops
growing or :data:`WAIT_S` passes. Then the page's own visible text
(``innerText``), title and links are read through the browser's DevTools
connection on 127.0.0.1. Nothing else is done on the page.

Python's standard library only: the DevTools socket is a few lines of
WebSocket here, on loopback, never anywhere else.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from typing import Any, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

#: How long a page may take to fill itself in.
WAIT_S = 45.0
POLL_S = 2.0
#: The text is settled when it has not grown for this many polls in a row.
STEADY_POLLS = 3
TEXT_MAX = 60_000
LINKS_MAX = 40
_LOOPBACK = ("127.0.0.1", "localhost", "::1")
_READ = ("JSON.stringify({title: document.title, "
         f"text: (document.body ? document.body.innerText : '').slice(0, {TEXT_MAX}), "
         "links: [...document.querySelectorAll('a[href]')].slice(0, 200)"
         ".map(a => [(a.innerText || '').trim().slice(0, 60), a.getAttribute('href')])"
         f".filter(x => x[0]).slice(0, {LINKS_MAX})}})")


class _Socket:
    """A minimal WebSocket client for the browser's DevTools on 127.0.0.1."""

    def __init__(self, ws_url: str, timeout: float = 15.0):
        parts = urllib.parse.urlsplit(ws_url)
        if parts.hostname not in _LOOPBACK:
            raise OSError("DevTools must be on this computer")
        self.sock = socket.create_connection((parts.hostname, parts.port or 80), timeout)  # lan-egress: the browser's DevTools on 127.0.0.1 only (checked above)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {parts.path} HTTP/1.1\r\nHost: {parts.hostname}:{parts.port}"
                           f"\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
                          .encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise OSError("DevTools closed the connection")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise OSError("DevTools did not open a connection")
        self.buf = head.split(b"\r\n\r\n", 1)[1]
        self.n = 0

    def _recv(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise OSError("DevTools closed the connection")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, method: str, params: Optional[dict] = None) -> int:
        self.n += 1
        data = json.dumps({"id": self.n, "method": method, "params": params or {}}).encode()
        mask = os.urandom(4)
        size = len(data)
        head = bytes([0x81]) + (bytes([0x80 | size]) if size < 126 else
                                bytes([0x80 | 126]) + struct.pack(">H", size) if size < 65536
                                else bytes([0x80 | 127]) + struct.pack(">Q", size))
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
        return self.n

    def frame(self) -> tuple[int, bytes]:
        b1, b2 = self._recv(2)
        size = b2 & 0x7F
        if size == 126:
            size = struct.unpack(">H", self._recv(2))[0]
        elif size == 127:
            size = struct.unpack(">Q", self._recv(8))[0]
        if size > 32 * 1024 * 1024:
            raise OSError("DevTools sent too much")
        return b1 & 0x0F, self._recv(size)

    def call(self, method: str, params: Optional[dict] = None) -> dict:
        want = self.send(method, params)
        parts: list[bytes] = []
        while True:
            op, data = self.frame()
            if op == 0x8:
                raise OSError("DevTools closed the connection")
            if op in (0x1, 0x0):
                parts.append(data)
                try:
                    msg = json.loads(b"".join(parts))
                except ValueError:
                    continue        # a message in several frames
                parts = []
                if msg.get("id") == want:
                    return msg.get("result") or {}

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


#: Chromium-family browsers (they can be driven headless through DevTools), in
#: the order tried when the owner's default browser is not one of them.
#: (key, Windows exe, Windows folder, Mac app name, Linux program names)
_CHROMIUM = (
    ("chrome", "chrome.exe", ("Google", "Chrome"), "Google Chrome",
     ("google-chrome", "google-chrome-stable")),
    ("edge", "msedge.exe", ("Microsoft", "Edge"), "Microsoft Edge",
     ("microsoft-edge", "microsoft-edge-stable")),
    ("chromium", "chromium.exe", ("Chromium",), "Chromium", ("chromium", "chromium-browser")),
    ("brave", "brave.exe", ("BraveSoftware", "Brave-Browser"), "Brave Browser",
     ("brave-browser", "brave")),
    ("vivaldi", "vivaldi.exe", ("Vivaldi",), "Vivaldi", ("vivaldi", "vivaldi-stable")),
    ("opera", "opera.exe", ("Opera",), "Opera", ("opera",)),
)
_LINUX_DIRS = ("/usr/bin", "/usr/local/bin", "/snap/bin", "/opt/google/chrome",
               "/opt/microsoft/msedge", "/opt/brave.com/brave")


_MAC_PREFS = ("~/Library/Preferences/com.apple.LaunchServices/"
              "com.apple.launchservices.secure.plist")


def _system() -> str:
    """"windows", "mac" or "linux" (anything else is treated as linux)."""
    if os.name == "nt":
        return "windows"
    return "mac" if sys.platform == "darwin" else "linux"


def _key_for(hint: str) -> str:
    """Which Chromium-family browser *hint* (a ProgId, bundle id, .desktop
    name or program path) names, or "" (Firefox, Safari, unknown)."""
    low = (hint or "").lower()
    for key in ("chromium", "edge", "brave", "vivaldi", "opera", "chrome"):
        if key in low:
            return key
    return ""


def _default_hint() -> str:
    """The owner's default web browser as this system names it, or "".

    Windows: the https UserChoice ProgId plus its open command; Mac: the
    LaunchServices handler's bundle id; Linux: ``xdg-settings``."""
    try:
        if _system() == "windows":
            import winreg
            sub = (r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations"
                   r"\https\UserChoice")
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub) as key:
                prog_id = str(winreg.QueryValueEx(key, "ProgId")[0] or "")
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,
                                    prog_id + r"\shell\open\command") as key:
                    cmd = str(winreg.QueryValueEx(key, "")[0] or "")
            except OSError:
                cmd = ""
            return f"{prog_id}\t{cmd}"
        if _system() == "mac":
            import plistlib
            path = os.path.expanduser(_MAC_PREFS)
            with open(path, "rb") as fh:
                handlers = plistlib.load(fh).get("LSHandlers") or []
            for h in handlers:
                if h.get("LSHandlerURLScheme") in ("https", "http"):
                    return str(h.get("LSHandlerRoleAll") or "")
            return ""
        for exe in ("/usr/bin/xdg-settings", "/usr/local/bin/xdg-settings"):
            if os.path.isfile(exe):
                from puffpai import procflags
                return subprocess.run([exe, "get", "default-web-browser"],
                                      capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
                                      stdin=subprocess.DEVNULL,
                                      **procflags.hidden()).stdout.strip()
    except (OSError, ValueError, subprocess.SubprocessError, ImportError):
        pass
    return ""


def _windows_exe_from(cmd: str) -> str:
    """The program a Windows open command starts (its first, maybe quoted, word)."""
    cmd = (cmd or "").strip()
    if cmd.startswith('"'):
        return cmd[1:].split('"', 1)[0]
    return cmd.split(" ", 1)[0]


def _installed() -> list[tuple[str, str]]:
    """(key, full path) of every Chromium-family browser found, in order."""
    from puffpai.security import host_programs
    found: list[tuple[str, str]] = []
    for key, exe, folder, app, names in _CHROMIUM:
        path = ""
        if _system() == "windows":
            if key in ("edge", "chrome"):
                path = host_programs.path(key) or ""
            path = path or host_programs._app_paths(exe) or ""  # noqa: SLF001
            for base in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
                root = os.environ.get(base) or ""
                for tail in ((*folder, "Application", exe), ("Programs", *folder, exe)):
                    cand = os.path.join(root, *tail)
                    if not path and os.path.isabs(cand) and os.path.isfile(cand):
                        path = cand
        elif _system() == "mac":
            for root in ("/Applications", os.path.expanduser("~/Applications")):
                cand = os.path.join(root, app + ".app", "Contents", "MacOS", app)
                if not path and os.path.isfile(cand):
                    path = cand
        else:
            dirs = list(_LINUX_DIRS) + [d for d in (os.environ.get("PATH") or "").split(
                os.pathsep) if os.path.isabs(d)]
            for name in names:
                for d in dirs:
                    cand = os.path.join(d, name)
                    if not path and os.path.isfile(cand) and os.access(cand, os.X_OK):
                        path = cand
        if path:
            found.append((key, path))
    return found


def browser_path() -> Optional[str]:
    """The browser the reader drives headless: the owner's default browser
    when it is Chromium-family (Chrome, Edge, Chromium, Brave, Vivaldi,
    Opera), else the first of those found, else None. Firefox and Safari
    cannot be driven this way; the caller then reads the page as plain HTML."""
    hint = _default_hint()
    if _system() == "windows" and "\t" in hint:
        exe = _windows_exe_from(hint.split("\t", 1)[1])
        if (exe and os.path.isabs(exe) and os.path.isfile(exe)
                and _key_for(os.path.basename(exe))):
            return exe
    found = _installed()
    want = _key_for(hint)
    for key, path in found:
        if key == want:
            return path
    return found[0][1] if found else None


def _devtools_port(profile: str, proc: Any, timeout: float = 20.0) -> int:
    """The DevTools port the browser wrote into its profile folder.

    Waits the whole *timeout* even after *proc* exits: Edge's launcher writes
    the file and exits within a second while the real browser (its own
    process, the same profile folder) keeps running."""
    path = os.path.join(profile, "DevToolsActivePort")
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with open(path, encoding="utf-8") as fh:
                return int(fh.readline().strip())
        except (OSError, ValueError):
            time.sleep(0.2)
    raise OSError("the browser did not start")


def _uses_profile(cmdline: str, profile: str) -> bool:
    """Was this program started with exactly *profile* as its own profile folder?"""
    import re
    norm = os.path.normcase(os.path.normpath(profile))
    for m in re.finditer(r'--user-data-dir=(?:"([^"]*)"|(\S+))', cmdline or ""):
        got = m.group(1) if m.group(1) is not None else m.group(2)
        if os.path.normcase(os.path.normpath(got)) == norm:
            return True
    return False


def _profile_pids(profile: str) -> list[int]:
    """Every running browser process whose profile folder is *profile*.

    The folder is this read's own fresh temp folder, so no other browser
    window can match."""
    from puffpai import procflags
    rows: list[tuple[int, str]] = []
    try:
        if _system() == "windows":
            from puffpai.security import host_programs
            ps = host_programs.path("powershell")
            if not ps:
                return []
            script = ("Get-CimInstance Win32_Process -Filter \"CommandLine LIKE "
                      "'%user-data-dir%'\" | ForEach-Object { \"$($_.ProcessId)`t"
                      "$($_.CommandLine)\" }")
            out = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command", script],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                                 stdin=subprocess.DEVNULL, **procflags.hidden()).stdout
        elif os.path.isfile("/bin/ps") or os.path.isfile("/usr/bin/ps"):
            ps = "/bin/ps" if os.path.isfile("/bin/ps") else "/usr/bin/ps"
            out = subprocess.run([ps, "-axww", "-o", "pid=,command="],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                                 stdin=subprocess.DEVNULL, **procflags.hidden()).stdout
            out = "\n".join("\t".join(ln.strip().split(None, 1)) for ln in out.splitlines())
        else:
            return []
    except (OSError, subprocess.SubprocessError):
        return []
    for line in (out or "").splitlines():
        pid, _, cmd = line.partition("\t")
        if pid.strip().isdigit():
            rows.append((int(pid.strip()), cmd))
    return [pid for pid, cmd in rows if pid != os.getpid() and _uses_profile(cmd, profile)]


def stop_profile_browsers(profile: str) -> list[int]:
    """Stop the browser started with *profile* (the launcher may have exited
    and left the real browser and its helpers running). Returns the ids."""
    from puffpai import procflags
    pids = _profile_pids(profile)
    for pid in pids:
        try:
            if _system() == "windows":
                from puffpai.security import host_programs
                tk = host_programs.path("taskkill")
                if tk:
                    subprocess.run([tk, "/F", "/T", "/PID", str(pid)], capture_output=True,
                                   timeout=15, stdin=subprocess.DEVNULL, **procflags.hidden())
            else:
                import signal
                os.kill(pid, signal.SIGKILL)
        except (OSError, subprocess.SubprocessError):
            pass
    return pids


def read(url: str, *, wait_s: float = WAIT_S, shot_path: str = "") -> Optional[dict]:
    """The page at *url* (127.0.0.1 only) as a person sees it, or None."""
    from puffpai import procflags
    if (urllib.parse.urlsplit(url).hostname or "").lower() not in _LOOPBACK:
        return None
    browser = browser_path()
    if not browser:
        return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # lan-egress: 127.0.0.1 only (DevTools), no proxy
    with tempfile.TemporaryDirectory(prefix="bb-read-", ignore_cleanup_errors=True) as profile:
        proc = subprocess.Popen(
            [browser, "--headless=new", "--disable-gpu", "--no-first-run",
             "--no-default-browser-check", "--disable-extensions",
             "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
             f"--user-data-dir={profile}", "--window-size=1400,1000", "about:blank"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **procflags.hidden())
        ws = None
        try:
            port = _devtools_port(profile, proc)
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/json/new?{urllib.parse.quote(url, safe='')}",
                method="PUT")
            with opener.open(req, timeout=10) as resp:
                target = json.loads(resp.read(65536))
            ws = _Socket(str(target["webSocketDebuggerUrl"]))
            last, steady, got = -1, 0, None
            end = time.monotonic() + max(2.0, wait_s)
            while time.monotonic() < end:
                time.sleep(POLL_S)
                res = ws.call("Runtime.evaluate", {"expression": _READ, "returnByValue": True})
                got = json.loads(((res.get("result") or {}).get("value")) or "null")
                size = len((got or {}).get("text") or "")
                steady = steady + 1 if size == last and size > 0 else 0
                last = size
                if steady >= STEADY_POLLS:
                    break
            if shot_path and got is not None:
                # The page as it looks now (a picture for its owner's records).
                res = ws.call("Page.captureScreenshot", {"format": "png"})
                if res.get("data"):
                    with open(shot_path, "wb") as fh:
                        fh.write(base64.b64decode(res["data"]))
                    got["screenshot"] = shot_path
            return got
        except (OSError, ValueError, KeyError) as exc:
            logger.info("page reader: %s", type(exc).__name__)
            return None
        finally:
            if ws is not None:
                ws.close()
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            stop_profile_browsers(profile)
            time.sleep(0.5)       # its profile folder is free before it is removed


__all__ = ["WAIT_S", "browser_path", "read", "stop_profile_browsers"]
