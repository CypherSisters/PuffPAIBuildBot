"""The page reader on this computer: it keeps waiting for the browser after
Edge's launcher exits, stops only the browser it started, and falls back to a
plain read of the page when no browser can be driven.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import http.server
import os
import threading
import time

from puffpai.integrate import host_tools, page_reader


class _Exited:
    """A launcher that already exited (Edge's does, about 0.2 s after start)."""

    def poll(self):
        return 0


def test_devtools_port_keeps_polling_after_the_launcher_exits(tmp_path):
    def later():
        time.sleep(0.6)
        (tmp_path / "DevToolsActivePort").write_text("51234\n/devtools/browser/x\n",
                                                     encoding="utf-8")
    threading.Thread(target=later, daemon=True).start()
    assert page_reader._devtools_port(str(tmp_path), _Exited(), timeout=5.0) == 51234  # noqa: SLF001


def test_devtools_port_gives_up_at_its_timeout(tmp_path):
    start = time.monotonic()
    try:
        page_reader._devtools_port(str(tmp_path), _Exited(), timeout=0.5)  # noqa: SLF001
    except OSError as exc:
        assert "did not start" in str(exc)
    else:
        raise AssertionError("no port file, yet a port")
    assert time.monotonic() - start >= 0.45


def test_only_the_browser_with_this_reads_own_profile_matches():
    prof = os.path.join("C:\\", "T", "bb-read-abc")
    assert page_reader._uses_profile(  # noqa: SLF001
        f'"msedge.exe" --headless=new --user-data-dir={prof} about:blank', prof)
    assert page_reader._uses_profile(f'msedge.exe --type=renderer "--user-data-dir={prof}"'  # noqa: SLF001
                                     .replace('"--user-data-dir=', '--user-data-dir="'), prof)
    assert not page_reader._uses_profile(  # noqa: SLF001
        f"msedge.exe --user-data-dir={prof}2 about:blank", prof)
    assert not page_reader._uses_profile("msedge.exe --profile-directory=Default", prof)  # noqa: SLF001


def test_stop_profile_browsers_stops_only_matching_ids(monkeypatch):
    monkeypatch.setattr(page_reader, "_profile_pids", lambda profile: [11, 12])
    killed: list = []
    monkeypatch.setattr(page_reader.subprocess, "run",
                        lambda argv, **kw: killed.append(argv))
    monkeypatch.setattr(page_reader.os, "kill", lambda pid, sig: killed.append(pid),
                        raising=False)
    assert page_reader.stop_profile_browsers("p") == [11, 12]
    flat = [str(x) for k in killed for x in (k if isinstance(k, list) else [k])]
    assert "11" in flat and "12" in flat


class _Page(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/missing":
            self.send_response(404)
            self.end_headers()
            return
        body = (b"<html><head><title>Little App</title><meta name='description' "
                b"content='Shows the weather for a town.'><script>var hidden = 1</script>"
                b"</head><body><h1>Today</h1><p>Sunny and warm</p></body></html>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # noqa: ANN002
        pass


def _serve():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Page)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def test_plain_read_when_no_browser_can_be_driven(monkeypatch):
    srv, base = _serve()
    try:
        monkeypatch.setattr(page_reader, "read", lambda url, wait_s=0: None)
        got = host_tools.read_page(base + "/")
        assert got["ok"] and got["title"] == "Little App"
        assert got["text"].splitlines()[0] == "Shows the weather for a town."
        assert "Sunny and warm" in got["text"] and "hidden" not in got["text"]
        assert host_tools.plain_read(base + "/missing") is None      # only a 200 page counts
    finally:
        srv.shutdown()
        srv.server_close()


# The browser the reader drives: the owner's default when it is Chromium-family.

def test_windows_default_browser_from_its_user_choice(monkeypatch, tmp_path):
    import sys
    import types
    exe = tmp_path / "brave.exe"
    exe.write_text("", encoding="utf-8")
    values = {"UserChoice": ("BraveHTML", 1), "command": (f'"{exe}" -- "%1"', 1)}

    class _Key:
        def __init__(self, sub):
            self.sub = sub

        def __enter__(self):
            return self

        def __exit__(self, *a):  # noqa: ANN002
            return False

    fake = types.SimpleNamespace(
        HKEY_CURRENT_USER=1, HKEY_CLASSES_ROOT=2,
        OpenKey=lambda hive, sub: _Key(sub),
        QueryValueEx=lambda key, name: values[
            "UserChoice" if key.sub.endswith("UserChoice") else "command"])
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(page_reader, "_system", lambda: "windows")
    assert page_reader._default_hint().startswith("BraveHTML\t")  # noqa: SLF001
    monkeypatch.setattr(page_reader, "_installed", lambda: [("edge", "C:/e/msedge.exe")])
    assert page_reader.browser_path() == str(exe)
    values["UserChoice"] = ("FirefoxURL-308046B0AF4A39CB", 1)          # Firefox: next one found
    values["command"] = (r'"C:\Program Files\Mozilla Firefox\firefox.exe" -url "%1"', 1)
    assert page_reader.browser_path() == "C:/e/msedge.exe"


def test_mac_default_browser_from_launch_services(monkeypatch, tmp_path):
    import plistlib
    prefs = tmp_path / "ls.plist"
    prefs.write_bytes(plistlib.dumps({"LSHandlers": [
        {"LSHandlerContentType": "public.html", "LSHandlerRoleAll": "com.apple.safari"},
        {"LSHandlerURLScheme": "https", "LSHandlerRoleAll": "com.brave.browser"}]}))
    monkeypatch.setattr(page_reader, "_system", lambda: "mac")
    monkeypatch.setattr(page_reader, "_MAC_PREFS", str(prefs))
    assert page_reader._default_hint() == "com.brave.browser"  # noqa: SLF001
    found = [("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
             ("brave", "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser")]
    monkeypatch.setattr(page_reader, "_installed", lambda: found)
    assert page_reader.browser_path() == found[1][1]
    prefs.write_bytes(plistlib.dumps({"LSHandlers": [
        {"LSHandlerURLScheme": "https", "LSHandlerRoleAll": "com.apple.safari"}]}))
    assert page_reader.browser_path() == found[0][1]                  # Safari: next one found


def test_linux_default_browser_from_xdg_settings(monkeypatch):
    import types
    real_isfile = os.path.isfile
    monkeypatch.setattr(page_reader, "_system", lambda: "linux")
    monkeypatch.setattr(page_reader.os.path, "isfile",
                        lambda p: p == "/usr/bin/xdg-settings" or real_isfile(p))
    seen: list = []

    def run(argv, **kw):
        seen.append(argv)
        return types.SimpleNamespace(stdout="chromium-browser.desktop\n")
    monkeypatch.setattr(page_reader.subprocess, "run", run)
    assert page_reader._default_hint() == "chromium-browser.desktop"  # noqa: SLF001
    assert seen[0] == ["/usr/bin/xdg-settings", "get", "default-web-browser"]
    found = [("chrome", "/usr/bin/google-chrome"), ("chromium", "/usr/bin/chromium-browser")]
    monkeypatch.setattr(page_reader, "_installed", lambda: found)
    assert page_reader.browser_path() == "/usr/bin/chromium-browser"


def test_no_chromium_browser_means_the_plain_read(monkeypatch):
    monkeypatch.setattr(page_reader, "_default_hint", lambda: "firefox.desktop")
    monkeypatch.setattr(page_reader, "_installed", lambda: [])
    monkeypatch.setattr(page_reader, "_system", lambda: "linux")
    assert page_reader.browser_path() is None
    assert page_reader.read("http://127.0.0.1:9/") is None      # read_page then reads plain HTML
