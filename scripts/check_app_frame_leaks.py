"""Security acceptance, live: the owner's app frame and the runner secret.

Two things need a live check: exfiltration
from the owner's app frame through prerender, preconnect and dns-prefetch (and
every other way the browser reaches an address by itself), and the scope of
the runner secret. This script runs both, live, and keeps the record.

Part A, the owner frame. A hostile app page is served through the real app
host and framed exactly as the dashboard frames it (same sandbox, the
dashboard's policy), in headless Edge with a throwaway profile. One loopback
listener per route counts what reaches it. Every route runs alone (the
browser runs one prerender per frame, so a combined page could hide one):
resource hints in markup and from script, Speculation Rules, frames made
every way, a service worker, moves of the page itself (location, assign,
replace, navigation.navigate, window.open, a form, a link in a closed shadow
root, a meta refresh) and moves of frames (src, setAttribute, markup,
frames[i].location, a named window.open, a form or link aimed at a frame),
object and embed; then the gate's own hostile page with every route at once,
WebRTC included. Fails on ANY connection, even a bare one with no request.

Part B, the runner secret. The real runner (Node, headless Edge, its wall)
opens two apps on the real app host, while a bot action holds them:

* a hostile app whose script, in the runner's own tab, writes the app's
  storage, fetches ``/__puffpai/files/``, then moves the tab to
  ``/__puffpai/files/x.txt``;
* a plain app the runner adds a row to, once with a save and once with a
  save followed at once by a reload (the app's own).

Every request the host sees is recorded with whether it carried the runner
secret and whether a browser made it. Fails when any browser request reached
``/__puffpai/*`` with the secret, when the app's own write counted as the
bot's (it must wait, 423), when the files route answered the app, or when the
runner's own saves did not land.

Usage (Windows)::

    py -3.14 scripts/check_app_frame_leaks.py --out <folder> [--scratch <folder>]
        [--edge <msedge.exe>] [--node <node.exe>] [--only A|B] [--secs 8]

Everything is on 127.0.0.1 with scratch state and apps folders and free
ports (never 8100 or 3100, which belong to another install). Writes
``<out>/fx-choice_r2_sec_owner_frame.json`` and ``.txt``. Exit 0 only when
every check holds.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import shutil
import socket
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests", "fixtures", "webapps"))

NEVER_PORTS = {8100, 3100}
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def free_port() -> int:
    while True:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        if p not in NEVER_PORTS:
            return p


def scratch_env(root: str) -> None:
    os.makedirs(root, exist_ok=True)
    os.environ.update(
        PUFFPAI_STATE_DIR=os.path.join(root, "state"), PUFFPAI_APPS_ROOT=os.path.join(root, "apps"),
        PUFFPAI_WEBAPPS_PORT=str(free_port()), PUFFPAI_DASHBOARD_PORT=str(free_port()),
        PUFFPAI_WEBAPPS_SEAL_KEY_FILE=os.path.join(root, "seal", "recipe_seal.key"),
        PYTHON_KEYRING_BACKEND="keyring.backends.fail.Keyring")
    os.makedirs(os.path.join(root, "seal"), exist_ok=True)
    assert not NEVER_PORTS & {int(os.environ["PUFFPAI_WEBAPPS_PORT"]),
                              int(os.environ["PUFFPAI_DASHBOARD_PORT"])}


# ---------------------------------------------------------------------------
# Part A
# ---------------------------------------------------------------------------

def part_a(edge: str, scratch: str, secs: float, log) -> dict:
    import frame_leak_probe as probe
    from puffpai.webapps import host
    rows = []
    for name in sorted(probe.VECTORS):
        scratch_env(os.path.join(scratch, "a", name))
        host.start(None)
        lis = probe.Listeners([name])
        try:
            page, files = probe.vector_page([name], lis.ports)
            probe.run(edge, page, files, seconds=secs)
        finally:
            lis.close()
            host.stop()
        hits = dict(lis.hits)
        rows.append({"route": name, "where": probe.VECTORS[name][0], "hits": hits, "ok": not hits})
        log(f"A  {'ok  ' if not hits else 'FAIL'} {name:16} {hits or 'nothing reached its listener'}")
    scratch_env(os.path.join(scratch, "a", "_acceptance_page"))
    host.start(None)
    lis = probe.Listeners(probe.EVIL_TCP, probe.EVIL_UDP)
    try:
        probe.run(edge, probe.evil_page(lis.ports), {}, seconds=max(secs, 13))
    finally:
        lis.close()
        host.stop()
    hits = dict(lis.hits)
    rows.append({"route": "the gate's hostile page (every route at once, WebRTC too)",
                 "where": "page", "hits": hits, "ok": not hits})
    log(f"A  {'ok  ' if not hits else 'FAIL'} acceptance page  {hits or 'nothing reached any listener'}")
    return {"routes": rows, "ok": all(r["ok"] for r in rows)}


# ---------------------------------------------------------------------------
# Part B
# ---------------------------------------------------------------------------

HOSTILE = """<!doctype html><html><head><meta charset="utf-8"><title>Hostile</title></head>
<body><h1>Hostile</h1><script>
(async function () {
  var C = window.__PUFFPAI__ || {}, out = {runner_mode: C.runner === true};
  try {
    var r = await fetch("/__puffpai/storage", {method: "PUT",
      headers: {"Content-Type": "application/json", "X-PuffPAI-Nonce": C.nonce},
      body: JSON.stringify({base: (C.store || {}).v || 0, data: {"probe.data": "from the app's script"}})});
    out.put = r.status;
  } catch (e) { out.put = "error"; }
  try { var f = await fetch("/__puffpai/files/"); out.files = f.status; } catch (e) { out.files = "error"; }
  localStorage.setItem("probe.out", JSON.stringify(out));
  setTimeout(function () { location.href = "/__puffpai/files/x.txt"; }, 1500);
})();
</script></body></html>"""

SAVER = """<!doctype html><html><head><meta charset="utf-8"><title>Saver</title></head>
<body><input id="n" aria-label="Name"><button id="add">Add</button>
<button id="addreload">Add and reload</button><script>
function list() { try { return JSON.parse(localStorage.getItem("items") || "[]"); } catch (e) { return []; } }
document.getElementById("add").onclick = function () {
  var l = list(); l.push(document.getElementById("n").value); localStorage.setItem("items", JSON.stringify(l)); };
document.getElementById("addreload").onclick = function () {
  var l = list(); l.push(document.getElementById("n").value + "-r");
  localStorage.setItem("items", JSON.stringify(l)); location.reload(); };
</script></body></html>"""


def _id(stem: str) -> str:
    return f"{stem}-" + base64.b32encode(secrets.token_bytes(5)).decode("ascii").lower()


def _place(app_id: str, html: str) -> None:
    from puffpai import webapps
    from puffpai.webapps import host
    web = webapps.web_dir(app_id)
    os.makedirs(web, exist_ok=True)
    with open(os.path.join(web, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(html)
    os.makedirs(webapps.app_state_dir(app_id), exist_ok=True)
    with open(os.path.join(webapps.app_state_dir(app_id), "web.json"), "w", encoding="utf-8") as fh:
        json.dump({"app_id": app_id, "status": "installed", "name": "Probe"}, fh)
    host.refresh()


def part_b(node: str, scratch: str, log) -> dict:
    from puffpai.webapps import host, mirror, runner_host
    scratch_env(os.path.join(scratch, "b"))
    real_program = runner_host.program
    runner_host.program = lambda n: node if n == "node" else real_program(n)
    ok, line = runner_host.availability()
    if not ok:
        return {"ok": False, "why": "the runner cannot start here: " + line}
    hostile, saver = _id("hostile"), _id("saver")
    _place(hostile, HOSTILE)
    _place(saver, SAVER)
    seen: list[dict] = []
    real_route = host._Handler._route_inner

    def recording(handler):
        secret = handler.server.secret
        given = handler.headers.get(host.RUNNER_HEADER)
        seen.append({"method": handler.command, "host": str(handler.headers.get("Host") or "")[:80],
                     "path": (handler.path or "").split("?", 1)[0][:120],
                     "dest": handler.headers.get("Sec-Fetch-Dest") or "",
                     "secret": bool(given) and given == secret,
                     # a browser always says what it fetches; the runner process does not
                     "from_browser": any(handler.headers.get(h) for h in (
                         "Sec-Fetch-Dest", "Sec-Fetch-Mode", "Sec-Fetch-Site", "Sec-Fetch-User"))})
        return real_route(handler)
    host._Handler._route_inner = recording
    host.start(None)
    checks: list[dict] = []

    def check(name: str, ok: bool, detail) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
        log(f"B  {'ok  ' if ok else 'FAIL'} {name} | {detail}")

    try:
        r = runner_host.ensure([hostile, saver])
        log(f"B  runner: {r.product}")
        # 1. the hostile app, in the runner's own tab, while a bot action holds it
        mirror.begin_bot_run(hostile)
        try:
            r.call("open", timeout=60, label=hostile)
            out = {}
            for _ in range(40):
                st = r.call("storage", label=hostile).get("localStorage") or {}
                if st.get("probe.out"):
                    out = json.loads(st["probe.out"])
                    break
                time.sleep(0.25)
            time.sleep(3.0)                       # the tab moves itself to /__puffpai/files/x.txt
        finally:
            mirror.end_bot_run(hostile)
        check("the page in the runner's tab is in runner mode (it never saves)",
              out.get("runner_mode") is True, out)
        check("the app's own storage write in the runner's tab waits like any page's (423)",
              out.get("put") == 423, out.get("put"))
        check("the app's script gets nothing from /__puffpai/files/", out.get("files") == 403,
              out.get("files"))
        check("the app's write never reached the bot's copy",
              "probe.data" not in mirror.get(hostile)["data"], sorted(mirror.get(hostile)["data"]))
        moved = [x for x in seen if x["path"] == "/__puffpai/files/x.txt"]
        check("the tab moving itself to /__puffpai/files/x.txt carried no runner secret",
              bool(moved) and not any(x["secret"] for x in moved), moved)
        # 2. the runner's own saves land, while a bot action holds the app
        recipe = {"steps": [{"do": "fill", "sel": "#n", "arg": "name"}, {"do": "click", "sel": "#add"}],
                  "args": {"name": {"type": "string", "maxLength": 40, "required": True}}}
        mirror.begin_bot_run(saver)
        try:
            r.call("open", timeout=60, label=saver)
            r.call("run", timeout=60, label=saver, recipe=recipe, args={"name": "Ana"})
            r.call("close", timeout=30, label=saver)
        finally:
            mirror.end_bot_run(saver)
        items = json.loads(mirror.get(saver)["data"].get("items") or "[]")
        check("the runner's own save landed while the bot held the app", items == ["Ana"], items)
        reload_recipe = {"steps": [{"do": "fill", "sel": "#n", "arg": "name"},
                                   {"do": "click", "sel": "#addreload"}, {"do": "wait", "ms": 1500}],
                         "args": recipe["args"]}
        mirror.begin_bot_run(saver)
        try:
            r.call("open", timeout=60, label=saver)
            r.call("reload", timeout=60, label=saver)
            r.call("run", timeout=60, label=saver, recipe=reload_recipe, args={"name": "Bo"})
            r.call("close", timeout=30, label=saver)
        finally:
            mirror.end_bot_run(saver)
        items = json.loads(mirror.get(saver)["data"].get("items") or "[]")
        check("a save the app follows at once with a reload is kept", items == ["Ana", "Bo-r"], items)
        # 3. where the secret was seen at all
        with_secret = [x for x in seen if x["secret"]]
        bad = [x for x in with_secret if x["from_browser"] and (
            x["path"].startswith("/__puffpai") or x["dest"] != "document" or x["method"] != "GET")]
        check("from a browser, the secret rode only on the tab's own page loads (GET, a document, "
              "never /__puffpai/*)", not bad, bad[:10])
        own = [x for x in with_secret if not x["from_browser"]]
        check("the runner process's own requests are only its storage saves",
              bool(own) and all(x["path"] == "/__puffpai/storage" and x["method"] == "PUT" for x in own),
              sorted({(x["method"], x["path"]) for x in own}))
    finally:
        runner_host.stop()
        host.stop()
        host._Handler._route_inner = real_route
        runner_host.program = real_program
    summary = {"requests_seen": len(seen),
               "with_secret_from_browser": sorted({(x["method"], x["path"], x["dest"])
                                                   for x in seen if x["secret"] and x["from_browser"]}),
               "with_secret_from_runner_process": sorted({(x["method"], x["path"])
                                                          for x in seen if x["secret"]
                                                          and not x["from_browser"]})}
    return {"checks": checks, "summary": summary, "ok": bool(checks) and all(c["ok"] for c in checks)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--scratch", default="")
    ap.add_argument("--edge", default=EDGE)
    ap.add_argument("--node", default=r"C:\Program Files\nodejs\node.exe")
    ap.add_argument("--only", choices=("A", "B"), default="")
    ap.add_argument("--secs", type=float, default=8.0)
    a = ap.parse_args()
    if sys.platform != "win32":
        print("written for Windows")
        return 2
    if not os.path.isfile(a.edge):
        print("Edge is not at " + a.edge)
        return 2
    os.makedirs(a.out, exist_ok=True)
    scratch = a.scratch or tempfile.mkdtemp(prefix="r3_sec_owner_frame_")
    if os.path.isdir(scratch):
        shutil.rmtree(scratch, ignore_errors=True)
    os.makedirs(scratch, exist_ok=True)
    lines: list[str] = []

    def log(s: str) -> None:
        lines.append(s)
        print(s, flush=True)

    t0 = time.time()
    log(f"check_app_frame_leaks: Edge {a.edge}; scratch {scratch}; never ports {sorted(NEVER_PORTS)}")
    record = {"edge": a.edge, "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if a.only in ("", "A"):
        record["A_owner_frame"] = part_a(a.edge, scratch, a.secs, log)
    if a.only in ("", "B"):
        record["B_runner_secret"] = part_b(a.node, scratch, log)
    parts = [v for k, v in record.items() if k.startswith(("A_", "B_"))]
    record["ok"] = bool(parts) and all(p.get("ok") for p in parts)
    record["seconds"] = round(time.time() - t0, 1)
    log(f"RESULT: {'PASS' if record['ok'] else 'FAIL'} in {record['seconds']} s")
    with open(os.path.join(a.out, "fx-choice_r2_sec_owner_frame.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=1)
    with open(os.path.join(a.out, "fx-choice_r2_sec_owner_frame.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return 0 if record["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
