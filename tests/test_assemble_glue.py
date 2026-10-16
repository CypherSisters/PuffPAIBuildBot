"""Toolsmith's coding loop and the glue rules (the Assemble step).

The owner's rules: outside code runs on her computer after ONE yes per
assembled app, and that yes covers Toolsmith's rewrites within the budget;
keys only from the vault; our own outbound calls go through the Bridge.

Nothing here reaches the internet. The model is a scripted fake; glue runs
are real Python processes (contained like every run), and the Bridge call is
replaced with a recorded answer.
"""
from __future__ import annotations

import json
import os
import sys
import types

import pytest

from puffpai import drawbridge
from puffpai.assemble import toolsmith_loop as tl
from puffpai.assemble.glue_runtime import check, runner, skeletons
from puffpai.assemble.server import gateway, host

APP = "earth_pictures"
DOOR = "app:earth_pictures:feed"

SPEC = {"schema": "assembled_app/v1", "id": APP, "title": "Earth pictures",
        "caps": ["cap-earth"],
        "settings": [{"key": "nasa_key", "kind": "key", "service": "nasa"}],
        "sources": [{"id": "pics", "kind": "glue", "module": "glue/pics.py", "fn": "pics",
                     "args": {"q": "input:q"}},
                    {"id": "api", "kind": "http_json", "door": DOOR, "service": "photos",
                     "url": "https://api.example.org/items?d={date}", "items": "$[*]",
                     "fields": {"title": "caption"}}],
        "views": [{"type": "gallery", "source": "pics"}],
        "actions": [{"id": "search_pictures", "source": "pics", "input": {"q": "string"}}],
        "checks": [{"cap": "cap-earth", "ask": "Show me pictures of Earth",
                    "action": "search_pictures", "input": {"q": "earth"},
                    "expect": {"min_items": 1, "fields": ["title"]}}]}

GOOD = '''"""Pictures."""
def pics(q="earth"):
    return [{"title": q + " " + str(i)} for i in range(3)]
'''
BROKEN = '''def pics(q="earth"):
    data = {"items": []}
    return [{"title": data["caption"]}]
'''
FETCHING = '''import urllib.parse
import puff_glue

def pics(q="earth"):
    data = puff_glue.fetch_json("https://api.example.org/items?" + urllib.parse.urlencode({"q": q}))
    return [{"title": d["caption"]} for d in data]
'''
KEYED = FETCHING.replace('{"q": q}))', '{"q": q}), key="nasa_key")')
assert KEYED != FETCHING
BY_SOURCE = '''import puff_glue

def pics(q="earth"):
    return [{"title": r["title"].upper()} for r in puff_glue.fetch_source("api", q=q)]
'''


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(drawbridge, "_CHECKERS", {})
    monkeypatch.setattr(drawbridge, "_REGISTRY", {"closed": False})
    # The owner's one yes for this app (the gate itself: test_assemble_consent.py).
    from puffpai.assemble import consent
    monkeypatch.setattr(consent, "has_yes", lambda app_id: True)
    check.connect()
    os.makedirs(host.app_folder(APP), exist_ok=True)
    with open(host.spec_path(APP), "w", encoding="utf-8") as fh:
        json.dump(SPEC, fh)
    yield tmp_path
    gateway.stop()


def activity(tmp_path) -> list[dict]:
    path = tmp_path / "data" / "agent-activity.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]


# ── the glue checklist ───────────────────────────────────────────────────────

REFUSED = {
    "socket": "import socket\ndef f():\n    s = socket.socket()\n    return []\n",
    "subprocess": "import subprocess\ndef f():\n    subprocess.run(['x'])\n    return []\n",
    "os": "import os\ndef f():\n    return [{'x': os.environ.get('HOME')}]\n",
    "eval": "def f():\n    return eval('[1]')\n",
    "exec": "def f():\n    exec('x=1')\n    return []\n",
    "compile": "def f():\n    return [compile('1', 'x', 'eval')]\n",
    "__import__": "def f():\n    return [__import__('os')]\n",
    "getattr on a module": "import json\ndef f():\n    return [getattr(json, 'decoder')]\n",
    "__subclasses__": "def f():\n    return ().__class__.__base__.__subclasses__()\n",
    "__globals__": "def f():\n    return [f.__globals__]\n",
    "__builtins__": "def f():\n    return [__builtins__]\n",
    "module through module": "import urllib.parse\ndef f():\n    return [urllib.parse.sys]\n",
    "json.decoder": "import json\ndef f():\n    return [json.decoder]\n",
    "importlib": "import importlib\ndef f():\n    return []\n",
    "ctypes": "import ctypes\ndef f():\n    return []\n",
    "open": "def f():\n    return [open('C:/x').read()]\n",
    "8.8.8.8 without a proxy": ("import urllib.request\ndef f():\n"
                                "    o = urllib.request.build_opener(urllib.request.ProxyHandler({}))\n"
                                "    return [o.open('http://8.8.8.8/').read()]\n"),
    "a private helper part": "import puff_glue\ndef f():\n    return [puff_glue._GATEWAY]\n",
    "a format string attribute": "def f():\n    return ['{0.__class__}'.format(1)]\n",
    "code on load": "X = [1]\nX.append(2)\ndef f():\n    return []\n",
    "two functions": "def f():\n    return []\ndef g():\n    return []\n",
    "a class": "def f():\n    class A:\n        pass\n    return []\n",
    "an unplanned package": "import requests\ndef f():\n    return []\n",
}


@pytest.mark.parametrize("why", sorted(REFUSED))
def test_drawbridge_blocks_glue_that_breaks_a_rule(world, why):
    verdict = drawbridge.check_addition({"kind": "glue", "id": "t", "name": "Connecting code",
                                         "code": REFUSED[why], "packages": []}, record=False)
    assert verdict["verdict"] == drawbridge.BLOCK, why


def test_skeletons_and_good_glue_pass(world):
    for name, code in list(skeletons.ALL.items()) + [("good", GOOD), ("fetching", FETCHING),
                                                     ("keyed", KEYED), ("source", BY_SOURCE)]:
        verdict = drawbridge.check_addition({"kind": "glue", "id": "t", "name": "Code",
                                             "code": code, "packages": ["tides"]},
                                            record=False)
        assert verdict["verdict"] == drawbridge.ALLOW, (name, verdict["results"])


def test_a_planned_package_never_unlocks_a_denied_module():
    assert check.clean_packages(["os", "sys", "socket", "requests"]) == {"requests"}
    assert not check.passes("import requests\ndef f():\n    return [requests.sessions.os]\n",
                            ["requests"])


# ── write_glue and run_glue ──────────────────────────────────────────────────

def test_write_glue_writes_only_what_drawbridge_passed(world):
    bad = tl.write_glue(APP, "pics", REFUSED["socket"])
    assert bad["ok"] is False and "Drawbridge stopped it" in bad["line"]
    assert not os.path.exists(os.path.join(host.glue_dir(APP), "pics.py"))
    good = tl.write_glue(APP, "pics", GOOD)
    assert good == {"ok": True, "file": "glue/pics.py", "fn": "pics"}
    assert host.checked_files(APP)["glue/pics.py"] == check.code_sha(GOOD)
    tasks = [r["task"] for r in activity(world)]
    assert "Checked it: passed" in tasks
    # A name that would stand in for a real module is refused.
    assert tl.write_glue(APP, "json", GOOD)["ok"] is False


def test_run_glue_returns_rows_from_its_own_process(world):
    assert tl.write_glue(APP, "pics", GOOD)["ok"]
    got = tl.run_glue(APP, "pics", {"q": "moon"})
    assert got["ok"] is True, got
    assert got["rows"] == [{"title": "moon 0"}, {"title": "moon 1"}, {"title": "moon 2"}]
    # Nothing of the run is left behind (request, log, scratch folder).
    assert os.listdir(os.path.join(host.app_folder(APP), "runs")) == []


def test_run_glue_hands_back_the_error_tail(world):
    assert tl.write_glue(APP, "pics", BROKEN)["ok"]
    got = tl.run_glue(APP, "pics", {})
    assert got["ok"] is False and got["line"] == "It stopped with an error."
    assert "KeyError" in got["output"] and "caption" in got["output"]


def test_glue_changed_after_its_check_does_not_run(world):
    assert tl.write_glue(APP, "pics", GOOD)["ok"]
    with open(os.path.join(host.glue_dir(APP), "pics.py"), "a", encoding="utf-8") as fh:
        fh.write("\n# changed by hand\n")
    got = tl.run_glue(APP, "pics", {})
    assert got["ok"] is False and "changed after Drawbridge checked it" in got["line"]


def fake_lane(monkeypatch, name, **fns):
    """The module with only *fns* replaced (the rest stays real: the
    spec check reads shape.path_ok)."""
    import importlib
    mod = types.ModuleType(f"puffpai.assemble.{name}")
    try:
        mod.__dict__.update({k: v for k, v in vars(importlib.import_module(
            f"puffpai.assemble.{name}")).items() if not k.startswith("__")})
    except ImportError:
        pass
    for k, v in fns.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, f"puffpai.assemble.{name}", mod)
    return mod


def test_glue_fetches_only_named_sites_under_their_switch(world, monkeypatch):
    sent = []

    def fetch_sample(door, url, *, getter=None, max_bytes=0):
        sent.append((door, url))
        return {"ok": True, "door": door, "url": url,
                "text": json.dumps([{"caption": "Blue marble"}])}
    fake_lane(monkeypatch, "shape", fetch_sample=fetch_sample)
    assert tl.write_glue(APP, "pics", FETCHING)["ok"]
    got = tl.run_glue(APP, "pics", {"q": "earth"})
    assert got["ok"] is True, got
    assert got["rows"] == [{"title": "Blue marble"}]
    assert sent == [(DOOR, "https://api.example.org/items?q=earth")]
    # A site no source or part names is refused before the Bridge is asked.
    out = host.backend_fetch(APP, "https://elsewhere.example.net/x")
    assert out["ok"] is False and out["code"] == "not_listed" and len(sent) == 1


def test_a_found_parts_site_is_fetched_under_the_parts_own_switch(world, monkeypatch):
    sent = []
    fake_lane(monkeypatch, "shape", fetch_sample=lambda door, url, **kw: sent.append(
        (door, url)) or {"ok": True, "text": "[]"})
    host.record_parts(APP, [{"name": "Tides", "url": "https://tides.example.org/api",
                             "door": "app:tides:feed", "service": "tides"},
                            {"name": "No switch", "url": "https://loose.example.org/"}])
    assert host.backend_fetch(APP, "https://tides.example.org/today")["ok"] is True
    assert sent == [("app:tides:feed", "https://tides.example.org/today")]
    assert host.backend_fetch(APP, "https://loose.example.org/x")["code"] == "not_listed"


def test_a_switched_off_site_comes_back_with_a_fix(world, monkeypatch):
    fake_lane(monkeypatch, "shape", fetch_sample=lambda door, url, **kw: {
        "ok": False, "switch_off": True, "reason": "switched off"})
    out = host.backend_fetch(APP, "https://api.example.org/items")
    assert out["ok"] is False
    assert out["fix"] == {"kind": "switch_on", "door": DOOR, "service": "photos"}


def test_the_key_is_added_by_buildbot_and_never_reaches_glue(world, monkeypatch):
    from puffpai import source_fetch
    seen = []

    def fetch_small(url, *, allowed_hosts, max_bytes, service, key_id=None, **kw):
        seen.append({"url": url, "hosts": allowed_hosts, "service": service, "key": key_id})
        return {"reason": "ok", "status": 200,
                "body": json.dumps([{"caption": "Keyed"}]).encode()}
    monkeypatch.setattr(source_fetch, "fetch_small", fetch_small)
    assert tl.write_glue(APP, "pics", KEYED)["ok"]
    got = tl.run_glue(APP, "pics", {"q": "earth"})
    assert got["ok"] is True, got
    assert got["rows"] == [{"title": "Keyed"}]
    assert seen == [{"url": "https://api.example.org/items?q=earth",
                     "hosts": {"api.example.org"}, "service": "nasa", "key": "nasa"}]
    out = host.backend_fetch(APP, "https://api.example.org/x", key="not_a_setting")
    assert out["ok"] is False and out["fix"] == {"kind": "key", "key": "not_a_setting"}


def test_glue_reads_a_declared_source_through_lane_a1(world, monkeypatch):
    calls = []
    fake_lane(monkeypatch, "api", fetch_source=lambda app, sid, inp: calls.append(
        (app, sid, inp)) or {"ok": True, "rows": [{"title": "blue marble"}]})
    assert tl.write_glue(APP, "pics", BY_SOURCE)["ok"]
    got = tl.run_glue(APP, "pics", {"q": "earth"})
    assert got["ok"] is True and got["rows"] == [{"title": "BLUE MARBLE"}], got
    assert calls == [(APP, "api", {"q": "earth"})]


def _plant(app: str, name: str, code: str) -> None:
    """A file that skipped the checklist (to test the run limits behind it)."""
    os.makedirs(host.glue_dir(app), exist_ok=True)
    with open(os.path.join(host.glue_dir(app), f"{name}.py"), "w", encoding="utf-8") as fh:
        fh.write(code)
    host.record_checked(app, f"glue/{name}.py", check.code_sha(code))


@pytest.mark.parametrize("code,words", [
    # TEST-NET-1, never routed: refused by the hook before any packet is sent.
    ("import socket\ndef planted():\n    s = socket.socket()\n    s.settimeout(1)\n"
     "    s.connect(('192.0.2.1', 9))\n    return []\n", "may only talk to BuildBot"),
    ("import socket\ndef planted():\n    socket.create_connection(('192.0.2.1', 9), 1)\n"
     "    return []\n", "may not look up names"),
    ("import socket\ndef planted():\n    socket.getaddrinfo('example.org', 443)\n"
     "    return []\n", "may not look up names"),
    ("import subprocess, sys\ndef planted():\n    subprocess.run([sys.executable, '-c', '1'])\n"
     "    return []\n", "subprocess.Popen"),
    ("def planted():\n    open('../outside.txt', 'w').write('x')\n    return []\n",
     "may not write files here"),
])
def test_the_run_refuses_what_the_checklist_would_have_stopped(world, monkeypatch, code, words):
    monkeypatch.setattr(check, "passes", lambda c, p=(): True)
    _plant(APP, "planted", code)
    got = tl.run_glue(APP, "planted", {})
    assert got["ok"] is False, got
    assert words in got["output"], got["output"]
    assert not os.path.exists(os.path.join(host.app_folder(APP), "runs", "outside.txt"))


def test_the_hook_allows_only_the_callback_port_on_a_literal_loopback_address(tmp_path):
    hook = runner.make_hook(str(tmp_path), [sys.prefix], 8123)
    hook("socket.connect", (None, ("127.0.0.1", 8123)))
    hook("socket.connect", (None, ("::1", 8123, 0, 0)))
    hook("socket.getaddrinfo", ("127.0.0.1", 8123, 0, 0, 0))
    hook("socket.sendmsg", (None, None))            # an already connected socket
    for event, args in [("socket.connect", (None, ("8.8.8.8", 53))),
                        ("socket.connect", (None, ("127.0.0.1", 11435))),     # Ollama
                        ("socket.connect", (None, ("127.0.0.1", 11434))),
                        ("socket.connect", (None, ("127.0.0.2", 8123))),
                        ("socket.connect", (None, ("localhost", 8123))),
                        ("socket.connect", (None, ("127.probe.1.1.1.1.nip.io", 8123))),
                        ("socket.connect", (None, ("::ffff:127.0.0.1", 8123))),
                        ("socket.connect", (None, ("127.0.0.1", True))),
                        ("socket.sendto", (None, ("127.0.0.1", 53))),
                        ("socket.bind", (None, ("127.0.0.1", 0))),
                        ("socket.getaddrinfo", ("localhost", 80, 0, 0, 0)),
                        ("socket.getaddrinfo", ("127.probe.1.1.1.1.nip.io", 80, 0, 0, 0)),
                        ("socket.getaddrinfo", ("dns.google", 53, 0, 0, 0)),
                        ("socket.gethostbyname", ("127.x.example",)),
                        ("socket.gethostbyaddr", ("127.0.0.1",)),
                        ("subprocess.Popen", ("x", [], None, None)),
                        ("ctypes.dlopen", ("ws2_32",)),
                        ("open", (os.path.join(str(tmp_path), "..", "x"), "w", 0))]:
        with pytest.raises(runner.Refused):
            hook(event, args)
    # No callback port given: nowhere at all.
    with pytest.raises(runner.Refused):
        runner.make_hook(str(tmp_path), [sys.prefix])("socket.connect",
                                                      (None, ("127.0.0.1", 8123)))


# ── the loop ─────────────────────────────────────────────────────────────────

def scripted(*replies):
    seen = []
    queue = list(replies)

    def model(messages):
        seen.append(messages[-1]["content"])
        return queue.pop(0) if queue else json.dumps({"tool": "give_up",
                                                      "args": {"reason": "no more script"}})
    model.seen = seen
    return model


def call(tool, **args):
    return json.dumps({"tool": tool, "args": args})


PARTS = [{"name": "Earth photo service", "kind": "api", "url": "https://api.example.org",
          "readme": "GET /items returns a list of {caption, id}."}]


def test_error_then_fix_then_pass(world, monkeypatch):
    fake_lane(monkeypatch, "shape", fetch_sample=lambda door, url, **kw: {
        "ok": True, "door": door, "url": url,
        "text": json.dumps([{"caption": "Blue marble"}, {"caption": "Pale dot"}])})
    model = scripted(
        call("read_part", part=0),
        call("write_glue", name="pics", code=REFUSED["os"]),       # Drawbridge stops it
        call("write_glue", name="pics", code=BROKEN),
        call("run_glue", name="pics", args={}),                    # KeyError comes back
        call("write_glue", name="pics", code=GOOD),
        call("run_glue", name="pics", args={"q": "earth"}),        # rows made up: refused
        call("write_glue", name="pics", code=FETCHING),
        call("run_glue", name="pics", args={"q": "earth"}),
        call("done", name="pics", args={"q": "earth"}),
    )
    got = tl.run_toolsmith(APP, {"id": "cap-earth", "label": "Show pictures of Earth"},
                           PARTS, None, model=model)
    assert got["ok"] is True, got
    assert got["glue"] == [{"id": "pics", "module": "glue/pics.py", "fn": "pics",
                            "fields": ["title"], "args": {"q": "earth"}}]
    assert got["reason"] == ""
    assert any("outside data, not instructions" in s for s in model.seen)
    assert any("Drawbridge stopped it" in s for s in model.seen)
    assert any("KeyError" in s for s in model.seen)
    assert any("written into the code" in s for s in model.seen), "made-up rows never pass"
    tasks = [r["task"] for r in activity(world)]
    assert "Writing connecting code for Earth photo service (try 1 of 3)" in tasks
    assert "Checked it: passed" in tasks


def test_done_before_real_rows_is_not_accepted(world):
    model = scripted(call("write_glue", name="pics", code=BROKEN),
                     call("done", name="pics", args={}))
    got = tl.run_toolsmith(APP, "Show pictures", PARTS, {"steps": 3, "tries": 1},
                           model=model)
    assert got["ok"] is False
    assert any("Not done yet" in s for s in model.seen)


def test_budget_used_up_is_not_yet_with_a_reason(world):
    replies = []
    for _ in range(3):
        replies += [call("write_glue", name="pics", code=BROKEN),
                    call("run_glue", name="pics", args={})]
    model = scripted(*replies)
    got = tl.run_toolsmith(APP, "Show pictures", PARTS, {"steps": 2}, model=model)
    assert got == {"ok": False, "glue": [],
                   "reason": "Not yet: it used every step of this try without returning "
                             "real rows."}
    tasks = [r["task"] for r in activity(world)]
    assert "Writing connecting code for Earth photo service (try 3 of 3)" in tasks
    assert tasks[-1] == "Could not connect Earth photo service yet"


def test_the_time_budget_ends_the_loop(world):
    now = [0.0]

    def clock():
        now[0] += 400.0          # each look at the clock is almost 7 minutes
        return now[0]
    model = scripted(*[call("read_part", part=0)] * 30)
    got = tl.run_toolsmith(APP, "Show pictures", PARTS, None, model=model, clock=clock)
    assert got["ok"] is False and "ran out of time" in got["reason"]


def test_a_budget_never_grows_past_what_the_yes_covered():
    assert tl._budget({"steps": 50, "tries": 9, "minutes": 600}) == tl.BUDGET
    # The other spelling of the same budget.
    assert tl._budget({"steps_per_try": 8, "tries": 3, "seconds": 1200}) == tl.BUDGET
    assert tl._budget({"steps_per_try": 2, "seconds": 120}) == {"steps": 2, "tries": 3,
                                                                "minutes": 2}


def test_parse_call_reads_fenced_and_thinking_answers():
    assert tl.parse_call('<think>hm</think>```json\n{"tool": "done", "args": {"name": "x"}}\n```') \
        == {"tool": "done", "args": {"name": "x"}}
    assert tl.parse_call("I will write code now") is None
    assert tl.parse_call('{"tool": "rm", "args": {}}') is None


# ── the yes covers checked rewrites, nothing else ────────────────────────────

def test_the_plan_fingerprint_covers_a_rewrite(world):
    from puffpai.integrate import run_here
    assert tl.write_glue(APP, "pics", GOOD)["ok"]
    plan = host.install(APP)
    hid = host.host_id(APP)
    first = run_here.read_state(hid)["plan_sha"]
    assert plan["assembled"]["glue_sha"] == host.glue_sha(APP)
    host.prepare_start(hid, plan)                       # as checked: fine
    # A checked Toolsmith rewrite moves the fingerprint with it.
    assert tl.write_glue(APP, "pics", GOOD.replace("range(3)", "range(4)"))["ok"]
    st = run_here.read_state(hid)
    assert st["plan_sha"] != first
    assert st["plan_sha"] == run_here.plan_sha(st["plan"])
    assert st["plan"]["assembled"]["glue_sha"] == host.glue_sha(APP)
    # A change nobody checked: the app does not start.
    with open(os.path.join(host.glue_dir(APP), "pics.py"), "a", encoding="utf-8") as fh:
        fh.write("\n")
    with pytest.raises(host.AssembleError) as err:
        host.prepare_start(hid, st["plan"])
    assert err.value.code == "changed"
    got = run_here.start(hid, by="test")
    assert got["ok"] is False and got["code"] == "changed"


# ── Toolsmith is registered on Windows ───────────────────────────────────────

def test_toolsmith_has_the_glue_tools_without_bubblewrap(world, monkeypatch):
    from puffpai.agents.envelope import Envelope
    from puffpai.agents.toolsmith import ToolsmithAgent
    monkeypatch.setattr("puffpai.agents.toolsmith.sandbox_available", lambda: False)
    agent = ToolsmithAgent()
    assert {"write_glue", "run_glue"} <= set(agent.tools())
    assert "workspace_exec" not in agent.tools()
    unreviewed = agent.handle(Envelope(intent="run_glue", payload={"app_id": APP}))
    assert unreviewed["ok"] is False and unreviewed["requires_review"] is True
    wrote = agent.handle(Envelope(intent="write_glue", state_changing=True,
                                  payload={"app_id": APP, "name": "pics", "code": GOOD}))
    assert wrote["ok"] is True
    ran = agent.handle(Envelope(intent="run_glue", state_changing=True,
                                payload={"app_id": APP, "name": "pics", "args": {}}))
    assert ran["ok"] is True and ran["count"] == 3 and ran["untrusted"] is True
    nope = agent.handle(Envelope(intent="run_glue", state_changing=True,
                                 payload={"app_id": "never_assembled", "name": "pics"}))
    assert nope["ok"] is False and "has not been put together" in nope["error"]
