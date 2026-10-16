"""The runner's wall, live in headless Edge or Chrome (invariant 11).

Runs only with ``PUFFPAI_R3_LIVE=1``. The runner needs Node 22.4 or newer: an
installed BuildBot finds it through ``node_path.txt`` (written by the
installer) or ``host_programs``; from a dev checkout set
``PUFFPAI_R3_NODE`` to the full path of ``node.exe``, otherwise the test is
skipped and says why.

Hostile apps inside the runner try to reach places they must never reach:

* a small "canary" web server on this computer (IPv4 and IPv6 loopback)
  stands in for the backend and Ollama; every request it gets is a leak;
* the requests are made both as page requests (fetch, a fetch from a fresh
  frame without the shim, images) and as the tab moving ITSELF
  (``location``), a link click, a form post and a popup. The page policy does not cover a
  tab moving itself, and Chromium never sends loopback through the proxy, so
  for those only the runner's per-tab rule (CDP ``Fetch.failRequest``) stands
  in the way: its own record of what it stopped must name them;
* the other installed app's address (which IS on the runner's proxy bypass
  list) is watched on the app host itself;
* the runner's whole process tree must listen on no TCP port.
"""
from __future__ import annotations

import base64
import http.client
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("PUFFPAI_R3_LIVE") != "1",
                       reason="live check: set PUFFPAI_R3_LIVE=1"),
]

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(os.path.dirname(HERE), "fixtures", "webapps")
sys.path.insert(0, FIX)
import app_host_kit  # noqa: E402

PORT = 4382


def _id(stem: str) -> str:
    return f"{stem}-" + base64.b32encode(secrets.token_bytes(5)).decode("ascii").lower()


class _Canary(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self.server.hits.append((self.command, self.headers.get("Host"), self.path))
        body = b"canary"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_HEAD = do_GET  # noqa: N815

    def log_message(self, *args) -> None:  # noqa: D401
        return


class _CanaryV6(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def _canary(v6: bool = False):
    try:
        srv = (_CanaryV6 if v6 else ThreadingHTTPServer)(("::1" if v6 else "127.0.0.1", 0), _Canary)
    except OSError:
        return None
    srv.hits = []
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _node_or_skip(monkeypatch) -> None:
    from puffpai.webapps import runner_host
    given = os.environ.get("PUFFPAI_R3_NODE", "").strip()
    if given:
        assert os.path.isabs(given) and os.path.isfile(given), "PUFFPAI_R3_NODE must be a full path"
        real = runner_host.program
        monkeypatch.setattr(runner_host, "program", lambda n: given if n == "node" else real(n))
    ok, line = runner_host.availability()
    if not ok:
        pytest.skip("the runner cannot start here: " + line)


def _listening_in_tree(pid: int) -> str:
    ps = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell",
                      "v1.0", "powershell.exe")
    script = ("$n=%d; $all=Get-CimInstance Win32_Process; $ids=@($n); $grow=$true; "
              "while($grow){$grow=$false; foreach($p in $all){ if($ids -contains $p.ParentProcessId "
              "-and -not ($ids -contains $p.ProcessId)){$ids+=$p.ProcessId; $grow=$true}}}; "
              "$l=Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | "
              "Where-Object { $ids -contains $_.OwningProcess }; "
              "Write-Output (\"processes=\" + $ids.Count + \" listening=\" + (@($l).Count))") % pid
    out = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, timeout=120,
                         cwd=os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"))
    return out.stdout.strip()


def _place(app_id: str, fixture: str, files: dict) -> None:
    from puffpai import webapps
    web = app_host_kit.place_app(app_id, fixture)
    for name, obj in files.items():
        with open(os.path.join(web, name), "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
    assert os.path.isdir(webapps.web_dir(app_id))


def test_the_runner_wall_holds(tmp_path, monkeypatch):
    from puffpai.webapps import host, runner_host
    if sys.platform != "win32":
        pytest.skip("this live check is written for Windows")
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", str(tmp_path / "apps"))
    monkeypatch.setenv("PUFFPAI_WEBAPPS_PORT", str(PORT))
    app_host_kit.seal_key(monkeypatch, tmp_path)
    _node_or_skip(monkeypatch)
    c4, c6 = _canary(), _canary(v6=True)
    assert c4 is not None
    p4 = c4.server_address[1]
    # positive control: the canary does count what reaches it
    conn = http.client.HTTPConnection("127.0.0.1", p4, timeout=5)
    conn.request("GET", "/control")
    assert conn.getresponse().status == 200
    conn.close()
    assert len(c4.hits) == 1
    c4.hits.clear()

    other = _id("other-app")
    targets = [f"http://127.0.0.1:{p4}/leak", f"http://localhost:{p4}/leak", f"http://x.localhost:{p4}/leak",
               f"http://{other}.localhost:{PORT}/", "http://127.0.0.1:11435/api/tags"]
    if c6 is not None:
        targets.append(f"http://[::1]:{c6.server_address[1]}/leak")
    moves = [(t, "location") for t in targets] + [(targets[0], "anchor"), (targets[0], "form"),
                                                   (targets[0], "popup"), ("file:///C:/Windows/win.ini", "location")]
    labels = {}
    for i, (url, how) in enumerate(moves):
        label = _id(f"nav{i}")
        _place(label, "nav_probe", {"nav.json": {"url": url, "how": how}})
        labels[label] = (url, how)
    requests_probe = _id("probe")
    _place(requests_probe, "probe", {"targets.json": targets + ["file:///C:/Windows/win.ini"]})
    _place(other, "rows_delete", {})

    host_hits = []
    real_get = host._Handler.do_GET

    def counting(handler):
        host_hits.append(str(handler.headers.get("Host") or "").lower())
        return real_get(handler)
    monkeypatch.setattr(host._Handler, "do_GET", counting)
    host.start(None)
    lines = []
    try:
        r = runner_host.ensure(list(labels) + [requests_probe, other])
        lines.append(f"runner: {r.product}")
        # 1. page requests (fetch, fetch from a fresh frame, images)
        r.call("open", label=requests_probe)
        st = {}
        for _ in range(90):
            st = r.call("storage", label=requests_probe).get("localStorage") or {}
            if st.get("probe.done"):
                break
            time.sleep(0.5)
        res = json.loads(st.get("probe.results") or "[]")
        leaks = [x for x in res if x["ok"] and x["what"] != "parent storage"]
        for x in res:
            lines.append(f"  request {'LEAK' if x in leaks else 'ok  '} {x['what']} | {x['detail']}")
        assert res and not leaks
        # 2. the tab moving itself, a link, a form, a popup
        tab_rule_total = 0
        outcomes = {}
        for label, (url, how) in labels.items():
            try:
                r.call("open", timeout=30, label=label)
            except runner_host.RunnerError:
                pass
            rec = {}
            for _ in range(24):
                time.sleep(0.25)
                rec = r.call("blocked", label=label)
                if rec.get("blocked") or rec.get("left"):
                    break
            time.sleep(1.5)
            rec = r.call("blocked", label=label)
            opened = r.call("ping").get("open") or []
            outcomes[label] = rec
            tab_rule_total += int(rec.get("blocked") or 0)
            lines.append(f"  {how:8} {url} | stopped by the tab rule: {rec.get('blocked')} "
                         f"{rec.get('origins')} | tab closed for leaving: {rec.get('left')} "
                         f"| tab still open: {label in opened}")
        time.sleep(2.0)
        # nothing reached the canaries (the backend and Ollama stand-ins)
        assert c4.hits == [], c4.hits
        if c6 is not None:
            assert c6.hits == [], c6.hits
        # nothing reached the other installed app, though it is on the bypass list
        other_host = f"{other}.localhost:{PORT}"
        assert other_host not in host_hits, "the other app was reached"
        # the tab moving itself is stopped by the per-tab rule, by name (a link
        # click, a form post and a popup are stopped earlier: the shim's link
        # hand-off, the page policy's form-action, the popup closed while paused;
        # none of them reached anything, as the canaries show)
        # The page's guard now cancels a move away before the
        # browser starts it, so the tab rule may never see one; a move the
        # browser did start must still be stopped by the tab rule, by name.
        for label, (url, how) in labels.items():
            if how == "location" and not url.startswith("file:"):
                origin = url.split("/leak")[0].split("/api/")[0].rstrip("/")
                got = outcomes[label]
                started = bool(got.get("blocked")) or bool(got.get("left"))
                assert (not started) or (got.get("blocked", 0) >= 1
                                         and origin in got.get("origins", [])), (how, url, got)
        lines.append(f"moves the tab rule saw (the guard cancels the rest first): {tab_rule_total}")
        # 3. no TCP listening port anywhere in the runner's process tree
        tree = _listening_in_tree(r.proc.pid)
        lines.append(f"runner process tree: {tree}")
        assert tree.endswith("listening=0"), tree
        lines.append(f"stopped by the tab rule in total: {tab_rule_total}")
        lines.append(f"canary hits: v4={len(c4.hits)} v6={len(c6.hits) if c6 else 'n/a'}; "
                     f"other app reached: {other_host in host_hits}")
        lines.append("proxy refusals (hosts): " + ", ".join(sorted({x['host'] for x in r.proxy.seen})))
        lines.append(f"proxy refusals with no readable host (never logged): {r.proxy.unreadable}")
        # no blank row: every refusal the proxy keeps, and every runner line in
        # the outside log, names a host
        assert all(x["host"] for x in r.proxy.seen) and "" not in r.proxy.counts
        from puffpai.security import egress_ledger
        runner_lines = []
        if os.path.isfile(egress_ledger.log_path()):
            with open(egress_ledger.log_path(), encoding="utf-8") as fh:
                runner_lines = [json.loads(x) for x in fh if '"app_runner"' in x]
        lines.append(f"outside log runner lines: {len(runner_lines)}, blank hosts: "
                     f"{sum(1 for x in runner_lines if not x.get('host'))}")
        assert all(x.get("host") for x in runner_lines)
    finally:
        print("\n".join(lines))
        runner_host.stop()
        host.stop()
        for srv in (c4, c6):
            if srv is not None:
                srv.shutdown()
                srv.server_close()
        shutil.rmtree(str(tmp_path / "apps"), ignore_errors=True)
