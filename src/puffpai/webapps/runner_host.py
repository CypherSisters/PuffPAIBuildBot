"""Starts and talks to the bot's hidden browser (``runner/runner.mjs``).

The runner is Node running ``runner.mjs``, which starts Edge or Chrome (by
absolute path, never ``PATH``) with its own profile under
``<state>/webapps/runner-profile`` and ``--remote-debugging-pipe``: there is no
debugging port, no ``--remote-allow-origins``, and no TCP control server. This
module writes one JSON command per line to the runner's stdin and reads one
JSON reply per line from its stdout. The per-boot runner secret goes to the
runner over that stdin, never on a command line.

The wall (:data:`WALL_FLAGS` plus :func:`wall_flags`): every request the
browser makes that is not to an installed app host is sent to a refuse-all
proxy on an ephemeral loopback port, which answers 403 and records the host in
the outside log (:mod:`puffpai.security.egress_ledger`, service ``app_runner``,
outcome ``refused``, what ``request``, one line per host a day: it is the
browser's own background traffic, never an app's). Name lookups for anything but the app hosts and the proxy
fail. WebRTC may not use UDP outside a proxy. The runner restarts when the list
of app hosts changes, because the browser reads these flags only at start.
On every tab the runner also fails any request whose origin is not that tab's
app (see ``runner.mjs``).

Needs Node 22.4 or newer; otherwise the bot can open apps for the owner but
cannot use them by itself, and says so.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import os
import queue
import re
import socket
import socketserver
import subprocess

from puffpai import procflags
import sys
import threading
import time
from typing import Any, Optional

from puffpai import webapps
from puffpai.webapps import wording

RUNNER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runner")
RUNNER_JS = os.path.join(RUNNER_DIR, "runner.mjs")
MIN_NODE = (22, 4)
IDLE_CLOSE_S = 600

#: The browser's own background features switched off (one flag: Chromium
#: keeps only the last ``--disable-features``). The pinned four first; the
#: rest were measured to cut Edge's own background calls (Bing, Office,
#: SmartScreen, Rewards) by about half; names a browser does not know are
#: ignored by it.
DISABLED_FEATURES = (
    "AutofillServerCommunication", "OptimizationHints", "MediaRouter", "Translate",
    "DialMediaRouteProvider", "CertificateTransparencyComponentUpdater",
    "InterestFeedContentSuggestions", "msSmartScreenProtection", "msEdgeTranslate",
    "msEdgeShopping", "msShoppingTrigger", "msEdgeCollections", "msEdgeSidebar",
    "msUndersideButton", "msEdgeDiscover", "msRewards", "msEdgeRewards", "msBingChat",
    "msEdgeBingChat", "msOfficeNtp", "msNtpFeeds", "msEdgeNtp", "msEdgeWorkspaces",
    "msDiagnosticData", "msEdgeSync", "EdgeWelcome", "msImplicitSignin", "msEdgeSignin",
    "msAccountSignin",
    # final fix: network time, Bing language detection and the Editor's
    # endpoint check (measured with the others below: 13 refused calls in
    # one learning run went to 5)
    "NetworkTimeServiceQuerying", "msDetectLanguageByBingAPI",
    "msEdgeEditorEnvironmentEndpointDetection", "PreconnectToSearch",
    "SearchPrefetchServicePrefetching",
)

#: The hidden browser's own settings, written into its profile before every
#: start: no guessing ahead (preconnect, prefetch), no spelling or writing
#: service, no translation, no search suggestions, no safe-browsing lookups.
#: Measured with the flags above; what Edge still tries (edge.microsoft.com,
#: www.bing.com, turing-writingassistance.edge.microsoft.com) goes only to
#: the refusing proxy on this computer and is never sent anywhere.
QUIET_PREFS: dict = {
    "net": {"network_prediction_options": 2},
    "browser": {"enable_editor_proofing": False, "enable_editor_synonyms": False,
                "enable_spellchecking": False},
    "spellcheck": {"use_spelling_service": False, "enabled": False},
    "edge": {"compose_inline_enabled": False, "handwriting_to_text": False},
    "translate": {"enabled": False},
    "search": {"suggest_enabled": False},
    "alternate_error_pages": {"enabled": False},
    "safebrowsing": {"enabled": False},
    "autofill": {"enabled": False, "profile_enabled": False, "credit_card_enabled": False},
    "credentials_enable_service": False,
}


def quiet_profile(profile: str) -> None:
    """Write :data:`QUIET_PREFS` into the hidden browser's own profile
    (``Default/Preferences``), keeping whatever else is there."""
    path = os.path.join(profile, "Default", "Preferences")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, encoding="utf-8") as fh:
            prefs = json.load(fh)
        if not isinstance(prefs, dict):
            prefs = {}
    except (OSError, ValueError):
        prefs = {}

    def merge(into: dict, add: dict) -> None:
        for k, v in add.items():
            if isinstance(v, dict):
                cur = into.get(k)
                if not isinstance(cur, dict):
                    cur = into[k] = {}
                merge(cur, v)
            else:
                into[k] = v
    merge(prefs, QUIET_PREFS)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(prefs, fh)
    os.replace(tmp, path)

#: Flags every runner browser starts with. The proxy, bypass and resolver
#: flags are added per start by :func:`wall_flags` (they name ports and hosts).
#: Whatever background calls the browser still makes go to the refusing proxy
#: and are stopped; they are the browser's own, never logged as an app's.
WALL_FLAGS: tuple[str, ...] = (
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--disable-background-networking",
    "--disable-features=" + ",".join(DISABLED_FEATURES),
    "--no-first-run",
    "--no-default-browser-check",
    "--headless=new",
    "--disable-component-update",
    "--disable-sync",
    "--disable-default-apps",
    "--disable-extensions",
    "--disable-client-side-phishing-detection",
    "--safebrowsing-disable-auto-update",
    "--disable-domain-reliability",
    "--disable-breakpad",
    "--disable-crash-reporter",
    "--no-service-autorun",
    "--disable-field-trial-config",
    "--disable-component-extensions-with-background-pages",
    "--disable-search-engine-choice-screen",
    "--edge-skip-compat-layer-relaunch",
    "--metrics-recording-only",
    "--no-pings",
    "--dns-prefetch-disable",
)

_HOST_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}\.localhost:\d{1,5}$")


def wall_flags(proxy_port: int, app_hosts: list[str]) -> list[str]:
    """The full flag list for one start. ``app_hosts`` are ``<label>.localhost:<port>``."""
    hosts = sorted({h for h in app_hosts if _HOST_RE.fullmatch(h)})
    names = sorted({h.rsplit(":", 1)[0] for h in hosts})
    # "<-loopback>" removes the browser's own "loopback never uses a proxy"
    # rule, so localhost:<other port>, 127.0.0.1 and [::1] go to the refusing
    # proxy too; only the exact app hosts are reached directly.
    bypass = ";".join(["<-loopback>"] + hosts)
    rules = ", ".join(["MAP * ~NOTFOUND"] + [f"EXCLUDE {n}" for n in names]
                      + ["EXCLUDE 127.0.0.1"])
    return [f"--proxy-server=http://127.0.0.1:{int(proxy_port)}",
            f"--proxy-bypass-list={bypass}",
            f"--host-resolver-rules={rules}",
            *WALL_FLAGS]


# ---------------------------------------------------------------------------
# host programs (security.host_programs when present; the same rule inline)
# ---------------------------------------------------------------------------

def _edge_from_registry() -> Optional[str]:
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:
        return None
    key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, key) as k:
                value, _ = winreg.QueryValueEx(k, "")
        except OSError:
            continue
        if isinstance(value, str) and os.path.isabs(value) and os.path.isfile(value):
            return value
    return None


def _program_fallback(name: str) -> Optional[str]:
    if name == "edge":
        found = _edge_from_registry()
        if found:
            return found
        for base in (os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)",
                     os.environ.get("ProgramFiles") or r"C:\Program Files"):
            p = os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe")
            if os.path.isfile(p):
                return p
        return None
    if name == "chrome":
        for base in (os.environ.get("ProgramFiles") or r"C:\Program Files",
                     os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"):
            p = os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
            if os.path.isfile(p):
                return p
        return None
    if name == "node":
        try:
            from puffpai.config import get_config
            root = get_config().REPO_ROOT
        except Exception:  # noqa: BLE001
            return None
        try:
            with open(os.path.join(root, "node_path.txt"), encoding="utf-8") as fh:
                value = fh.read().strip()
        except OSError:
            return None
        if os.path.isabs(value) and os.path.isfile(value):
            return value
        return None
    return None


def program(name: str) -> Optional[str]:
    hp = webapps._optional("puffpai.security.host_programs")
    if hp is not None and hasattr(hp, "path"):
        found = hp.path(name)
        return found if found and os.path.isabs(found) else None
    return _program_fallback(name)


def _run_kwargs() -> dict:
    hp = webapps._optional("puffpai.security.host_programs")
    if hp is not None and hasattr(hp, "run_kwargs"):
        return dict(hp.run_kwargs())
    cwd = webapps.state_root()
    os.makedirs(cwd, exist_ok=True)
    return {"cwd": cwd, "env": _clean_env()}


_ENV_KEEP = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP", "USERPROFILE",
             "LOCALAPPDATA", "APPDATA", "PROGRAMDATA", "PROGRAMFILES",
             "PROGRAMFILES(X86)", "COMMONPROGRAMFILES", "HOME", "LANG", "TZ")


def _clean_env() -> dict:
    env = {k: v for k, v in os.environ.items() if k.upper() in _ENV_KEEP}
    sysroot = env.get("SYSTEMROOT") or env.get("SystemRoot") or r"C:\Windows"
    env["PATH"] = os.path.join(sysroot, "System32") if sys.platform == "win32" else "/usr/bin:/bin"
    return env


def node_version(node: str) -> Optional[tuple[int, int]]:
    kw = _run_kwargs()
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=15,
                             cwd=kw.get("cwd"), env=kw.get("env"), **procflags.hidden())
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"v(\d+)\.(\d+)", (out.stdout or "").strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def availability() -> tuple[bool, str]:
    """(True, "") when the runner can start; else (False, plain line)."""
    node = program("node")
    if not node:
        return False, wording.NODE_TOO_OLD
    ver = node_version(node)
    if ver is None or ver < MIN_NODE:
        return False, wording.NODE_TOO_OLD
    if not (program("edge") or program("chrome")):
        return False, wording.NO_BROWSER
    return True, ""


# ---------------------------------------------------------------------------
# the refuse-all logging proxy
# ---------------------------------------------------------------------------

class _ProxyHandler(socketserver.StreamRequestHandler):
    timeout = 10

    def handle(self) -> None:
        try:
            line = self.rfile.readline(8192).decode("latin-1", "replace").strip()
        except (OSError, ValueError):
            line = ""
        if line:
            # a connection that closed without a request (the browser opens
            # spare ones) asked for nothing: it is not recorded anywhere
            parts = line.split(" ")
            method = parts[0].upper()[:16]
            target = parts[1] if len(parts) > 1 else ""
            host = ""
            if target and not target.startswith("/"):
                host = _host_part(target)
            else:
                # an origin-form request names its host only in the header
                host = _host_part(self._host_header())
            self.server.owner.refused(method, host)
        try:
            self.wfile.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        except OSError:
            pass


    def _host_header(self) -> str:
        """The Host header's value (read at most 50 lines of 8 KB each)."""
        for _ in range(50):
            try:
                raw = self.rfile.readline(8192).decode("latin-1", "replace")
            except (OSError, ValueError):
                return ""
            if raw in ("", "\r\n", "\n"):
                return ""
            name, _sep, value = raw.partition(":")
            if name.strip().lower() == "host":
                return value.strip()
        return ""


#: A host name or an address the outside log can show: letters, digits,
#: dots and hyphens, or an IPv6 address in brackets.
_HOST_OK = re.compile(r"^(?:[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9])?\.)*[a-z0-9_]"
                      r"(?:[a-z0-9_-]{0,61}[a-z0-9])?\.?$|^\[[0-9a-f:.]{2,45}\]$")


def _host_part(target: str) -> str:
    """The host of ``host:port``, ``user@host/path`` or ``[v6]:port``, lower
    case, or "" when there is none a person could read."""
    target = target or ""
    if "://" in target:
        target = target.split("://", 1)[1]
    host = target.split("/", 1)[0].rsplit("@", 1)[-1]
    if host.startswith("["):
        host = host.split("]", 1)[0] + "]"
    else:
        host = host.split(":", 1)[0]
    host = host.strip().lower()
    return host if len(host) <= 253 and _HOST_OK.match(host) else ""


class _Proxy(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False

    def handle_error(self, request, client_address) -> None:  # noqa: D401 - quiet
        return


#: (UTC day, host) pairs already written to the outside log. The browser's
#: own background calls repeat all day; the log gets one line per host a day.
_logged_today: set[tuple[str, str]] = set()
_logged_lock = threading.Lock()


def _log_once_a_day(host: str, method: str) -> None:
    day = time.strftime("%Y-%m-%d", time.gmtime())
    with _logged_lock:
        if (day, host) in _logged_today:
            return
        for old in [k for k in _logged_today if k[0] != day]:
            _logged_today.discard(old)
        _logged_today.add((day, host))
    try:
        from puffpai.security import egress_ledger
        # "request", never "app_traffic": what reaches this proxy is the hidden
        # browser's own background call (an app's requests are stopped per tab
        # before they are sent, see runner.mjs), and it was stopped here.
        egress_ledger.record("app_runner", host=host, outcome="refused", method=method,
                             code="not_allowed", what="request")
    except Exception:  # noqa: BLE001 - the refusal stands whatever the log does
        pass


class RefuseProxy:
    """Refuses every request and records the host (never a path or a value).

    Everything that reaches it is the runner browser's own background traffic
    (updates, safety checks, sign-in, search): an app's own requests are
    failed per tab by the runner before they leave the page. It is all
    refused; the outside log gets one line per host a day under the runner's
    name, never as an app's traffic (otherwise about 380 such lines a
    session would be logged as the app's).
    """

    def __init__(self) -> None:
        self.server = _Proxy(("127.0.0.1", 0), _ProxyHandler)
        self.server.owner = self
        self.port = self.server.server_address[1]
        self.seen: list[dict] = []
        self.counts: dict[str, int] = {}
        self.unreadable = 0
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self.server.serve_forever, name="webapps-wall",
                                        daemon=True)
        self._thread.start()

    def refused(self, method: str, host: str) -> None:
        host = _host_part(host) if isinstance(host, str) else ""
        if not host:
            # refused all the same, but a request with no readable host is
            # never a row on the owner's "what leaves your computer" page
            with self._lock:
                self.unreadable += 1
            return
        with self._lock:
            self.seen.append({"method": method, "host": host, "t": time.time()})
            del self.seen[:-200]
            if host in self.counts or len(self.counts) < 200:
                self.counts[host] = self.counts.get(host, 0) + 1
        _log_once_a_day(host, method)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


# ---------------------------------------------------------------------------
# the runner process
# ---------------------------------------------------------------------------

class RunnerError(RuntimeError):
    pass


class Runner:
    """One runner process: a browser with the wall, driven over stdin/stdout."""

    def __init__(self, labels: list[str]) -> None:
        from puffpai.webapps import host
        ok, line = availability()
        if not ok:
            raise RunnerError(line)
        secret = host.runner_secret()
        if not secret:
            raise RunnerError(wording.RUNNER_FAILED)
        browser = program("edge") or program("chrome")
        node = program("node")
        self.labels = sorted(set(labels))
        self.port = host.port()
        self.proxy = RefuseProxy()
        hosts = [f"{lb}.localhost:{self.port}" for lb in self.labels]
        profile = os.path.join(webapps.state_root(), "runner-profile")
        try:
            quiet_profile(profile)
        except OSError:
            pass                  # the flags still hold; the proxy refuses the rest
        kw = _run_kwargs()
        self.proc = subprocess.Popen(
            [node, RUNNER_JS], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, cwd=kw.get("cwd"), env=kw.get("env"),
            **procflags.hidden())
        self._replies: dict[int, dict] = {}
        self._cv = threading.Condition()
        self._events: "queue.Queue[dict]" = queue.Queue()
        self._next = 0
        self._send_lock = threading.Lock()
        self._reader = threading.Thread(target=self._read, name="webapps-runner", daemon=True)
        self._reader.start()
        forget = sorted(host.removed_ids())[:500]
        setup = {"browser": browser, "profile": profile, "flags": wall_flags(self.proxy.port, hosts),
                 "secret": secret, "port": self.port, "labels": self.labels, "forget": forget}
        self._write(setup)
        try:
            ev = self._events.get(timeout=40)
        except queue.Empty:
            ev = {"event": "failed"}
        if ev.get("event") != "ready":
            self.close()
            raise RunnerError(wording.RUNNER_FAILED)
        self.product = str(ev.get("product") or "")
        self.last_used = time.time()

    def _write(self, obj: dict) -> None:
        data = (json.dumps(obj) + "\n").encode("utf-8")
        with self._send_lock:
            if self.proc.stdin is None:
                raise RunnerError(wording.RUNNER_FAILED)
            try:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
            except (OSError, ValueError) as exc:
                # the runner died between alive() and this write (a broken
                # pipe, or a closed stdin): one error the callers handle
                raise RunnerError(wording.RUNNER_FAILED) from exc

    def _read(self) -> None:
        out = self.proc.stdout
        if out is None:
            return
        for raw in out:
            try:
                msg = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if "event" in msg:
                self._events.put(msg)
                continue
            if isinstance(msg.get("id"), int):
                with self._cv:
                    self._replies[msg["id"]] = msg
                    self._cv.notify_all()
        with self._cv:
            self._cv.notify_all()

    def alive(self) -> bool:
        return self.proc.poll() is None

    def call(self, verb: str, timeout: float = 60.0, **kw: Any) -> dict:
        if not self.alive():
            raise RunnerError(wording.RUNNER_FAILED)
        with self._cv:
            self._next += 1
            rid = self._next
        self._write({"id": rid, "verb": verb, **kw})
        end = time.time() + timeout
        with self._cv:
            while rid not in self._replies:
                left = end - time.time()
                if left <= 0 or not self.alive():
                    raise RunnerError(wording.RUNNER_BUSY)
                self._cv.wait(min(left, 0.5))
            reply = self._replies.pop(rid)
        self.last_used = time.time()
        return reply

    def close(self) -> None:
        try:
            if self.alive():
                try:
                    self._write({"id": 0, "verb": "quit"})
                except (OSError, RunnerError):
                    pass
                try:
                    self.proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        finally:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
            except OSError:
                pass
            self.proxy.close()


_LOCK = threading.RLock()
_runner: dict[str, Optional[Runner]] = {"r": None}


def ensure(labels: list[str]) -> Runner:
    """The running runner whose wall names exactly ``labels`` (restarts if not)."""
    want = sorted(set(labels))
    with _LOCK:
        r = _runner["r"]
        if r is not None and r.alive() and r.labels == want:
            return r
        if r is not None:
            r.close()
            _runner["r"] = None
        try:
            r = Runner(want)
        except RunnerError:
            # the last browser may still hold its profile for a moment
            time.sleep(2.0)
            r = Runner(want)
        _runner["r"] = r
        return r


def current() -> Optional[Runner]:
    with _LOCK:
        r = _runner["r"]
        return r if r is not None and r.alive() else None


def stop() -> None:
    with _LOCK:
        r = _runner["r"]
        _runner["r"] = None
    if r is not None:
        r.close()


# ---------------------------------------------------------------------------
# the driver the compiler and Drawbridge's check use, and throwaway copies
# ---------------------------------------------------------------------------

class DriverError(RuntimeError):
    pass


class RunnerDriver:
    """The compiler's and the checker's view of the runner (see compile_form.Driver)."""

    def __init__(self, runner: Runner) -> None:
        self.runner = runner

    def _call(self, verb: str, timeout: float = 60.0, **kw: Any) -> dict:
        try:
            reply = self.runner.call(verb, timeout=timeout, **kw)
        except RunnerError as exc:
            raise DriverError(str(exc)) from exc
        if not reply.get("ok"):
            raise DriverError(str(reply.get("error") or "the runner refused"))
        return reply

    def open(self, label: str, fresh: bool = False) -> None:
        self._call("open", label=label, fresh=bool(fresh))

    def reload(self, label: str) -> None:
        self._call("reload", label=label)

    def snapshot(self, label: str) -> dict:
        r = self._call("snapshot", label=label, max=400)
        return {"title": r.get("title") or "", "controls": r.get("controls") or [],
                "headings": r.get("headings") or []}

    def click(self, label: str, ref: str, *, accept_dialog: bool = False) -> dict:
        return self._call("click", label=label, ref=ref, accept_dialog=bool(accept_dialog))

    def storage(self, label: str) -> dict:
        r = self._call("storage", label=label)
        ls = r.get("localStorage") if isinstance(r.get("localStorage"), dict) else {}
        return {"localStorage": {str(k): str(v) for k, v in ls.items()},
                "indexedDB": [str(x) for x in (r.get("indexedDB") or [])][:20]}

    def row_controls(self, label: str, text: str) -> Optional[dict]:
        """The repeated row showing ``text`` and its controls (check copies only)."""
        r = self._call("row_controls", label=label, text=str(text)[:200])
        info = r.get("info")
        return info if isinstance(info, dict) else None

    def count(self, label: str, sel: str) -> Optional[int]:
        r = self._call("count", label=label, sel=str(sel))
        return int(r["count"]) if isinstance(r.get("count"), int) else None

    def run(self, label: str, recipe: dict, args: dict) -> dict:
        try:
            r = self.runner.call("run", timeout=90, label=label, recipe=recipe, args=args)
        except RunnerError as exc:
            return {"ok": False, "error": str(exc), "results": {}, "dialogs": [], "clicked": []}
        return {"ok": bool(r.get("ok")), "error": r.get("error"),
                "results": r.get("results") or {}, "dialogs": r.get("dialogs") or [],
                "clicked": [str(x) for x in (r.get("clicked") or [])]}

    def close(self, label: str) -> bool:
        """Close the app's page. True only when the runner says it is closed."""
        try:
            r = self.runner.call("close", timeout=20, label=label)
        except RunnerError:
            return False
        return isinstance(r, dict) and bool(r.get("ok")) and r.get("closed") == label


class CopyPool:
    """Throwaway check copies of one app, each at its own never-reused address.

    Copies are made in batches because the runner's wall names every address
    it may reach and restarts when that list changes.
    """

    def __init__(self, app_id: str, size: int = 16) -> None:
        from puffpai.webapps import host
        self.app_id = app_id
        self.size = max(2, int(size))
        self.free: list[str] = []
        self.used: list[str] = []
        self._host = host
        self.driver: Optional[RunnerDriver] = None
        self._grow()

    def _grow(self) -> None:
        for _ in range(self.size):
            self.free.append(self._host.new_check_copy(self.app_id))
        labels = self._host.installed_ids() + list(self._host.check_copies())
        runner = ensure(labels)
        # one driver for the pool's life: it follows the runner across restarts
        if self.driver is None:
            self.driver = RunnerDriver(runner)
        else:
            self.driver.runner = runner

    def take(self) -> str:
        if not self.free:
            self._grow()
        label = self.free.pop(0)
        self.used.append(label)
        return label

    def close(self) -> None:
        for label in self.used + self.free:
            if self.driver is not None:
                self.driver.close(label)
            self._host.drop_check_copy(label)
        self.used, self.free = [], []


__all__ = ["WALL_FLAGS", "wall_flags", "availability", "ensure", "current", "stop",
           "Runner", "RunnerError", "RefuseProxy", "program", "node_version", "MIN_NODE",
           "RunnerDriver", "DriverError", "CopyPool"]
