"""Browser apps live in headless Edge (and Chrome when present): isolation, compiled actions.

Runs only with ``PUFFPAI_R3_LIVE=1``, on Windows. The runner needs Node 22.4 or
newer: set ``PUFFPAI_R3_NODE`` to the full path of ``node.exe`` from a dev
checkout. Run with ``--basetemp`` inside a scratch folder: the recorded
budget tracker and todo app have their outside parts saved while adding them
(real downloads through ``source_fetch.fetch_small``, service ``app_parts``).
Set ``PUFFPAI_R3_PROOF_DIR`` to write the transcripts there (``L8_F_live_*``,
``L8_webrtc.txt``).

* the isolation probe: a hostile app makes 27+ attempts to reach this
  computer's other services (IPv4 and IPv6 loopback, the Ollama port, another
  app's address); nothing reaches the canaries;
* the three recorded GitHub apps install with compiled add and read actions
  that pass Drawbridge; the wedding planner also gets a row remove (remove a
  guest) and an edit that carries the RSVP; those run for real on the bot's
  copy (a remove only with the owner's yes);
* the synthetic fixtures' row controls compile to remove, change and edit;
* whether ``webrtc 'block'`` stops ICE gathering is measured with the probe,
  with and without the directive, in each browser.
"""
from __future__ import annotations

import base64
import http.client
import json
import os
import secrets
import shutil
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from puffpai import source_fetch

#: The real (pinned) transport, captured before the suite's autouse fixture
#: replaces it with a refusal; put back only inside the tests that save parts.
_REAL_TRANSPORT = source_fetch._default_transport

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("PUFFPAI_R3_LIVE") != "1",
                       reason="live check: set PUFFPAI_R3_LIVE=1"),
    pytest.mark.skipif(sys.platform != "win32", reason="written for Windows"),
]

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(os.path.dirname(HERE), "fixtures", "webapps")
sys.path.insert(0, FIX)
import app_host_kit  # noqa: E402

PORT = 4386
RECORDED = os.environ.get("PUFFPAI_R3_APPBRIDGE", "")


def _id(stem: str) -> str:
    return f"{stem}-" + base64.b32encode(secrets.token_bytes(5)).decode("ascii").lower()


def _proof(name: str, lines: list[str]) -> None:
    out = os.environ.get("PUFFPAI_R3_PROOF_DIR")
    print("\n".join(lines))
    if out and os.path.isdir(out):
        with open(os.path.join(out, name), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


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


@pytest.fixture
def live(tmp_path, monkeypatch):
    from puffpai import drawbridge
    from puffpai.integrate import actions as integrate_actions
    from puffpai.webapps import host, runner_host
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", str(tmp_path / "apps"))
    monkeypatch.setenv("PUFFPAI_WEBAPPS_PORT", str(PORT))
    app_host_kit.seal_key(monkeypatch, tmp_path)
    monkeypatch.setattr(drawbridge, "_CHECKERS", {})
    monkeypatch.setattr(drawbridge, "_REGISTRY", {"closed": False})
    monkeypatch.setattr(integrate_actions, "_SOURCES", {})
    given = os.environ.get("PUFFPAI_R3_NODE", "").strip()
    if given:
        assert os.path.isabs(given) and os.path.isfile(given), "PUFFPAI_R3_NODE must be a full path"
        real = runner_host.program
        monkeypatch.setattr(runner_host, "program", lambda n: given if n == "node" else real(n))
    ok, line = runner_host.availability()
    if not ok:
        pytest.skip("the runner cannot start here: " + line)
    host.start(None)
    yield tmp_path
    runner_host.stop()
    host.stop()
    shutil.rmtree(str(tmp_path / "apps"), ignore_errors=True)


def _install(app_id: str, src: str, name: str, lines: list[str]) -> dict:
    from puffpai.webapps import install
    t0 = time.monotonic()
    said: list[str] = []
    res = install.install_static(app_id, src, source={"repo": f"example/{name}"}, name=name,
                                 purpose="lists", jobs=["keep a list"], progress=said.append)
    lines.append(f"== {name}: {res['lines']} drivable={res['drivable']} "
                 f"verdict={res['verdict']['verdict']} ({time.monotonic() - t0:.0f} s)")
    for a in res["actions"]:
        lines.append(f"   {a['id'].split('__', 1)[1]:32} {a['effect']:8} undoable={a['undoable']!s:5} "
                     f"| {a['says']}")
    return res


def _by_name(res: dict) -> dict:
    return {a["id"].split("__", 1)[1]: a for a in res["actions"]}


# ---------------------------------------------------------------------------
# 1. the isolation probe
# ---------------------------------------------------------------------------

def test_the_isolation_probe_reaches_nothing(live):
    from puffpai.webapps import host, runner_host
    c4, c6 = _canary(), _canary(v6=True)
    assert c4 is not None
    p4 = c4.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", p4, timeout=5)      # positive control
    conn.request("GET", "/control")
    assert conn.getresponse().status == 200
    conn.close()
    c4.hits.clear()
    other = _id("other-app")
    targets = [f"http://127.0.0.1:{p4}/leak", f"http://localhost:{p4}/leak",
               f"http://x.localhost:{p4}/leak", f"http://127.0.0.2:{p4}/leak",
               f"http://0.0.0.0:{p4}/leak", f"http://[::ffff:127.0.0.1]:{p4}/leak",
               "http://127.0.0.1:11435/api/tags", f"http://{other}.localhost:{PORT}/"]
    if c6 is not None:
        targets.append(f"http://[::1]:{c6.server_address[1]}/leak")
    probe = _id("probe")
    web = app_host_kit.place_app(probe, "probe")
    with open(os.path.join(web, "targets.json"), "w", encoding="utf-8") as fh:
        json.dump(targets, fh)
    app_host_kit.place_app(other, "rows_delete")
    lines = []
    try:
        r = runner_host.ensure(host.installed_ids())
        lines.append(f"runner: {r.product}")
        r.call("open", label=probe)
        st = {}
        for _ in range(120):
            st = r.call("storage", label=probe).get("localStorage") or {}
            if st.get("probe.done"):
                break
            time.sleep(0.5)
        res = json.loads(st.get("probe.results") or "[]")
        attempts = [x for x in res if x["what"].split(" ")[0] in ("fetch", "bare", "img")]
        leaks = [x for x in res if x["ok"] and x["what"] not in ("parent storage",)]
        for x in res:
            lines.append(f"  {'LEAK' if x in leaks else 'ok  '} {x['what']} | {x['detail']}")
        time.sleep(1.0)
        lines.append(f"attempts: {len(attempts)}; leaks: {len(leaks)}; canary hits: v4={len(c4.hits)} "
                     f"v6={len(c6.hits) if c6 else 'n/a'}")
        assert len(attempts) >= 25
        assert not leaks and c4.hits == [] and (c6 is None or c6.hits == [])
    finally:
        _proof("L8_F_live_isolation.txt", lines)
        for srv in (c4, c6):
            if srv is not None:
                srv.shutdown()
                srv.server_close()


# ---------------------------------------------------------------------------
# 2. the recorded apps, and the wedding planner's row actions run for real
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not os.path.isdir(RECORDED), reason="the recorded apps are not on this machine")
def test_the_recorded_apps_install_with_checked_actions_and_run(live, monkeypatch):
    from puffpai.webapps import actions, mirror
    monkeypatch.setattr(source_fetch, "_default_transport", _REAL_TRANSPORT)
    lines: list[str] = []
    try:
        for name, sub in (("budget", "budget"), ("todos", os.path.join("todos", "dist"))):
            res = _install(_id(name), os.path.join(RECORDED, sub), name, lines)
            got = _by_name(res)
            assert res["drivable"], res["lines"]
            assert any(a["effect"] == "changes" for a in got.values())
            assert any(a["effect"] == "reads" for a in got.values())
        app = _id("bunnys-big-day")
        res = _install(app, os.path.join(RECORDED, "wedding"), "wedding", lines)
        got = _by_name(res)
        assert {"add_guest", "read_guest_list", "remove_guest", "edit_guest"} <= set(got)
        assert got["remove_guest"]["effect"] == "removes" and got["edit_guest"]["effect"] == "changes"
        assert "rsvp_status" in got["edit_guest"]["args"]["properties"]
        # the app's choices are never in the tool list the AI reads:
        # they come from the compiled recipe itself
        rsvp = actions._find(got["edit_guest"]["id"])[1]["args"]["rsvp_status"]["enum"]
        lines.append(f"RSVP choices the app offers: {rsvp}")

        def guests():
            raw = mirror.get(app)["data"].get("bunnysBigDay.v1") or "{}"
            return {g.get("name"): g for g in (json.loads(raw).get("guests") or [])}

        add = actions.run(got["add_guest"], {"guest_name": "Leo Park"}, {})
        lines.append(f"add Leo Park: ok={add['ok']} | {add['line']}")
        assert add["ok"] and "Leo Park" in guests()
        target = "Attending" if "Attending" in rsvp else rsvp[-1]
        edit = actions.run(got["edit_guest"], {"guest_name": "Leo Park", "rsvp_status": target}, {})
        lines.append(f"change Leo Park's RSVP to {target}: ok={edit['ok']} | {edit['line']}")
        assert edit["ok"] and guests()["Leo Park"].get("rsvp") == target
        read = actions.run(got["read_guest_list"], {}, {})
        lines.append(f"read: {read['line']}")
        no_yes = actions.run(got["remove_guest"], {"guest_name": "Leo Park"}, {})
        lines.append(f"remove without her yes: ok={no_yes['ok']} | {no_yes['line']}")
        assert no_yes["ok"] is False and "Leo Park" in guests()
        gone = actions.run(got["remove_guest"], {"guest_name": "Leo Park"}, {"yes": True})
        lines.append(f"remove with her yes: ok={gone['ok']} | {gone['line']}")
        assert gone["ok"] and "Leo Park" not in guests()
    finally:
        _proof("L8_F_live_recorded.txt", lines)


# ---------------------------------------------------------------------------
# 3. the synthetic fixtures' row controls
# ---------------------------------------------------------------------------

def test_the_synthetic_row_controls_compile_and_a_row_change_runs(live):
    from puffpai.webapps import actions, mirror
    lines: list[str] = []
    try:
        want = {"rows_delete": ("remove_item", "removes"), "row_select": ("change_status", "changes"),
                "edit_form": ("edit_item", "changes")}
        apps = {}
        for fixture, (name, effect) in want.items():
            app = _id(fixture.replace("_", "-"))
            res = _install(app, os.path.join(FIX, fixture), fixture, lines)
            got = _by_name(res)
            assert got[name]["effect"] == effect, (fixture, sorted(got))
            apps[fixture] = (app, got)
        app, got = apps["row_select"]
        assert actions.run(got["add_item"], {"item_name": "Tea"}, {})["ok"]
        ch = actions.run(got["change_status"], {"item_name": "Tea", "new_value": "Got it"}, {})
        stored = mirror.get(app)["data"]
        data = json.loads(stored[got["change_status"]["id"] and next(iter(stored))])
        lines.append(f"row choice: ok={ch['ok']} undo={ch['undo_token']} | status now "
                     f"{[i.get('status') for i in data['items'] if i.get('name') == 'Tea']}")
        assert ch["ok"] and any(i.get("status") == "Got it" for i in data["items"] if i.get("name") == "Tea")
        assert mirror.restore(app, ch["undo_token"]["version"])["ok"]
    finally:
        _proof("L8_F_live_rows.txt", lines)


def test_two_rows_of_the_same_name_are_both_left_alone(live, monkeypatch):
    """Two items called Tea: removing "Tea" touches neither, first on the
    stored data (before any click), then on the page itself (the runner's own
    refusal, with the stored-data check switched off); one Rice is removed."""
    from puffpai.webapps import actions, mirror, wording
    from puffpai.webapps import recipe_check as rc
    lines: list[str] = []
    try:
        app = _id("rows-delete")
        got = _by_name(_install(app, os.path.join(FIX, "rows_delete"), "rows_delete", lines))
        for n in ("Tea", "Tea", "Rice"):
            assert actions.run(got["add_item"], {"item_name": n}, {})["ok"], n
        before = mirror.get(app)["data"]
        r1 = actions.run(got["remove_item"], {"item_name": "Tea"}, {"yes": True})
        lines.append(f"remove Tea (stored check): ok={r1['ok']} | {r1['line']}")
        assert r1["ok"] is False and r1["line"] == wording.MORE_THAN_ONE
        assert mirror.get(app)["data"] == before
        with monkeypatch.context() as m:
            m.setattr(rc, "matching_records", lambda *a, **k: 1)
            r2 = actions.run(got["remove_item"], {"item_name": "Tea"}, {"yes": True})
        lines.append(f"remove Tea (page check only): ok={r2['ok']} | {r2['line']}")
        assert r2["ok"] is False and r2["line"] == wording.MORE_THAN_ONE
        assert mirror.get(app)["data"] == before
        r3 = actions.run(got["remove_item"], {"item_name": "Rice"}, {"yes": True})
        names = [i.get("name") for v in mirror.get(app)["data"].values()
                 for i in (json.loads(v).get("items") or []) if isinstance(i, dict)]
        lines.append(f"remove Rice: ok={r3['ok']} | {r3['line']} | left {names}")
        assert r3["ok"] is True and sorted(names) == ["Tea", "Tea"]
    finally:
        _proof("L8_F_live_duplicates.txt", lines)


# ---------------------------------------------------------------------------
# 4. does webrtc 'block' stop ICE gathering? (measured, recorded)
# ---------------------------------------------------------------------------

def test_measure_whether_the_webrtc_directive_stops_ice_gathering(live, monkeypatch):
    from puffpai.webapps import headers, host, runner_host
    real_csp = headers.csp
    flags = tuple(f for f in runner_host.WALL_FLAGS if "webrtc" not in f)
    lines = ["Probe: RTCPeerConnection({iceServers: []}), one data channel, createOffer, "
             "setLocalDescription, 1.5 s for candidates. The runner's own WebRTC flags are LEFT OUT "
             "here, so only the page policy is measured (the owner's own browser has no such flags).",
             ""]
    measured = 0
    browsers = [("edge", runner_host.program("edge")), ("chrome", runner_host.program("chrome"))]
    try:
        for label_b, path in browsers:
            if not path:
                lines.append(f"{label_b}: not installed on this computer")
                continue
            monkeypatch.setattr(runner_host, "program", (lambda n, p=path, b=label_b, real=runner_host.program:
                                                         p if n in ("edge", "chrome") else real(n)))
            monkeypatch.setattr(runner_host, "WALL_FLAGS", flags)
            for variant in ("with webrtc 'block'", "control: without the directive"):
                if variant.startswith("control"):
                    monkeypatch.setattr(headers, "csp", lambda port: real_csp(port).replace("webrtc 'block'; ", ""))
                else:
                    monkeypatch.setattr(headers, "csp", real_csp)
                probe = _id("rtc")
                web = app_host_kit.place_app(probe, "probe")
                with open(os.path.join(web, "targets.json"), "w", encoding="utf-8") as fh:
                    json.dump([], fh)
                runner_host.stop()
                r = runner_host.ensure(host.installed_ids())
                r.call("open", label=probe)
                st = {}
                for _ in range(60):
                    st = r.call("storage", label=probe).get("localStorage") or {}
                    if st.get("probe.done"):
                        break
                    time.sleep(0.5)
                res = {x["what"]: x for x in json.loads(st.get("probe.results") or "[]")}
                rtc = res.get("webrtc") or {"ok": None, "detail": "no answer"}
                lines.append(f"{label_b} ({r.product}), {variant}: candidates gathered={rtc['ok']} "
                             f"({rtc['detail']})")
                measured += 1
                runner_host.stop()
                host.refresh()
    finally:
        monkeypatch.setattr(headers, "csp", real_csp)
        _proof("L8_webrtc.txt", lines)
    assert measured >= 2
