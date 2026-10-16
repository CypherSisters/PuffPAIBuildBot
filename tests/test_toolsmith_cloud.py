"""Toolsmith's cloud model (security/toolsmith_cloud.py, build/toolsmith_cloud_loop.py).

Toolsmith is the ONLY agent that may use a cloud
model (Anthropic); everything else runs locally. A stubbed Anthropic answers
at the egress seam (``egress_doors._send``); nothing goes online.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import threading
from typing import Any

import pytest

from keys_support import PIN, fast_vault, grep_tree, reset_keys, set_posture
from puffpai import api_server, brain_tools
from puffpai.build import toolsmith_cloud_loop as L
from puffpai.build import toolsmith_run as T
from puffpai.security import egress_doors, egress_ledger, keys
from puffpai.security import toolsmith_cloud as tc

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}
DEV = {**AUTH, "X-Puff-Device-Id": "devicetsmith0001"}
KEY = "sk-ant-api03-" + "Q" * 40 + "_test"
SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "puffpai"


class Resp:
    def __init__(self, status: int, body: Any, headers: dict | None = None):
        self.status_code = status
        self.content = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        self.raw = None
        self.request = type("Req", (), {"headers": {}, "body": None})()

    def close(self):
        pass


class FakeAnthropic:
    """api.anthropic.com: /v1/models for the key check, /v1/messages scripted."""

    def __init__(self):
        self.calls: list[dict] = []
        self.replies: list[tuple] = []
        self.offline = False

    def __call__(self, method, url, *, pin=None, **kw):
        if self.offline:
            raise ConnectionError("network is down")
        headers = dict(kw.get("headers") or {})
        data = kw.get("data")
        self.calls.append({"method": method, "url": url, "headers": headers,
                           "body": json.loads(data) if data else None})
        if headers.get("x-api-key") != KEY:
            return Resp(401, {"type": "error", "error": {"type": "authentication_error"}})
        if url.endswith("/v1/models"):
            return Resp(200, {"data": [{"id": "claude-opus-5-5"}]})
        status, body, hdrs = self.replies.pop(0) if self.replies else (
            200, msg([{"type": "text", "text": "done"}], "end_turn"), {})
        return Resp(status, body, hdrs)

    @property
    def posts(self) -> list[dict]:
        return [c for c in self.calls if c["method"] == "POST"]


def msg(content: list, stop: str, usage: dict | None = None, **extra) -> dict:
    return {"id": "msg_1", "type": "message", "role": "assistant", "content": content,
            "stop_reason": stop, "usage": usage or {"input_tokens": 100, "output_tokens": 20,
                                                     "cache_read_input_tokens": 0,
                                                     "cache_creation_input_tokens": 0},
            **extra}


@pytest.fixture
def cloud(monkeypatch, tmp_path):
    for d in egress_doors.DOORS:
        monkeypatch.delenv(d.env_var, raising=False)
    reset_keys()
    set_posture("enumerated")
    v = fast_vault(monkeypatch, tmp_path / "vault")
    monkeypatch.setattr(api_server, "_vault_instance", v)
    keys.set_vault_provider(lambda: v)
    for name in (tc.SETTING_FILE, tc.USAGE_FILE, tc.KEEP_FILE):
        try:
            os.unlink(tc._path(name))  # noqa: SLF001
        except OSError:
            pass
    tc._no_fallback.clear()  # noqa: SLF001
    tc.drop_lease("idle")
    monkeypatch.setattr(tc, "_active_runs", 0)
    monkeypatch.setattr(tc, "_queue_busy", None)
    monkeypatch.setattr(tc, "_wizard_busy", None)
    monkeypatch.setattr(tc, "_resumers", [])
    monkeypatch.setattr(tc, "_listening", set())
    fake = FakeAnthropic()
    monkeypatch.setattr(egress_doors, "_send", fake)
    monkeypatch.setattr(tc, "_sleep", lambda s: fake.calls.append({"method": "SLEEP",
                                                                    "s": s}))
    api_server.app.config.update(TESTING=True)
    fake.client = api_server.app.test_client()
    fake.vault, fake.tmp = v, tmp_path
    yield fake
    tc.drop_lease("idle")
    for name in (tc.SETTING_FILE, tc.USAGE_FILE, tc.KEEP_FILE):
        try:
            os.unlink(tc._path(name))  # noqa: SLF001
        except OSError:
            pass
    reset_keys()


def _turn_on(cloud, model: str = "claude-opus-5-5") -> dict:
    r = cloud.client.post("/toolsmith/model", headers=DEV,
                          json={"where": "cloud", "model": model, "key": KEY})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _scope(job_id: str = "job1"):
    run = T.Run({"id": job_id, "name": "News"}, [{"ref": "r1", "name": "NewsApp"}],
                lambda _l: None)
    run.use_cloud = True
    return run._cloud_scope()  # noqa: SLF001


def _tools(record: list, fail: set = frozenset()) -> list:
    def make(name):
        def fn(args, ctx=None):
            record.append((name, args))
            if name in fail:
                return {"ok": False, "said": f"{name} failed"}
            return {"ok": True, "said": f"{name} ran", "output": args.get("command", "")}
        return brain_tools.Tool(name, f"the {name} tool", {
            "type": "object", "properties": {"command": {"type": "string"}},
            "additionalProperties": False}, fn, source="toolsmith")
    return [make("terminal"), make("download"), make("give_up")]


def _ctx(tools: list) -> dict:
    T._register_provider()  # noqa: SLF001
    return {"conversation_id": "toolsmith-test", "caller": "owner",
            T.TOOLS_KEY: tools, "cancelled": threading.Event()}


# -- the scope guard ------------------------------------------------------------

def test_a_caller_that_is_not_toolsmith_never_gets_the_cloud_path(cloud):
    _turn_on(cloud)
    with pytest.raises(tc.ScopeRefused):
        tc.toolsmith_scope("job1")                        # this test module, not toolsmith_run
    for fake in (None, "toolsmith", object(), object.__new__(tc.Scope)):
        with pytest.raises(tc.ScopeRefused):
            tc.send(fake, system="s", tools=[], messages=[{"role": "user", "content": "x"}])
    with pytest.raises(tc.ScopeRefused):
        tc.Scope(object(), "job1", "x")
    assert cloud.posts == []                              # nothing was sent
    # keyed_post is for the security package only
    with pytest.raises(PermissionError):
        keys.keyed_post(keys.handle("anthropic"), url="https://api.anthropic.com/v1/messages",
                        caller="chat", data=b"{}")


def test_only_toolsmith_run_mints_the_scope_and_runs_the_cloud_loop():
    """Fails if any other module names the scope or the cloud loop: chat,
    voice, interpret, ranking, decide, Drawbridge, Archivist, routines and
    every other agent always use the local model."""
    import ast
    minting, looping, keyed, where = [], [], [], set()
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        if "toolsmith_scope" in text and rel != "security/toolsmith_cloud.py":
            minting.append(rel)
            for fn in ast.walk(ast.parse(text)):
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for node in ast.walk(fn):
                        if isinstance(node, ast.Call) and "toolsmith_scope" in ast.unparse(
                                node.func):
                            where.add((rel, fn.name))
        if re.search(r"toolsmith_cloud_loop", text) and rel != "build/toolsmith_cloud_loop.py":
            looping.append(rel)
        if "keyed_post(" in text and rel not in ("security/keys.py",
                                                  "security/toolsmith_cloud.py"):
            keyed.append(rel)
    # Toolsmith's install loop, and its own finding work in build/apps.py
    # (search_words and rank) only. Never add another.
    assert sorted(minting) == ["build/apps.py", "build/toolsmith_run.py"]
    assert {fn for rel, fn in where if rel == "build/apps.py"} == {"search_words", "rank"}
    assert looping == ["build/toolsmith_run.py"]
    assert keyed == []
    assert tc.SCOPE_CALLER == "puffpai.build.toolsmith_run"
    assert tc.FIND_CALLERS == {"puffpai.build.apps": frozenset({"search_words", "rank"})}


def _minted_from(module: str, func: str):
    """toolsmith_scope called from a function *func* in a module named *module*."""
    code = f"def {func}():\n    return tc.toolsmith_scope('j', 'J')\n"
    space = {"__name__": module, "tc": tc}
    exec(compile(code, f"<{module}>", "exec"), space)  # noqa: S102 - the test's own text
    return space[func]()


def test_the_find_scope_is_only_for_apps_rank_and_search_words(cloud):
    assert _minted_from("puffpai.build.apps", "rank").kind == "find"
    assert _minted_from("puffpai.build.apps", "search_words").kind == "find"
    assert _minted_from("puffpai.build.toolsmith_run", "anything").kind == "install"
    for module, func in (("puffpai.build.apps", "for_job"), ("puffpai.build.apps", "look"),
                         ("puffpai.build.knowledge", "rank"), ("puffpai.chat", "rank"),
                         ("puffpai.build.interpret", "search_words"),
                         ("puffpai.build.decide", "rank")):
        with pytest.raises(tc.ScopeRefused):
            _minted_from(module, func)
    _turn_on(cloud)
    find = _minted_from("puffpai.build.apps", "rank")
    with pytest.raises(tc.ScopeRefused):          # a finding ask runs no tools
        tc.send(find, system="s", tools=[{"name": "run", "input_schema": {}}],
                messages=[{"role": "user", "content": "x"}])
    with pytest.raises(tc.ScopeRefused):          # the install scope is not for finding
        tc.ask_json(_scope(), system="s", user="x", purpose="p")
    assert cloud.posts == []


# -- a locked vault while saving the key (2026-10-14) -------------------------------

def test_a_locked_vault_gives_423_then_her_pin_then_the_retry_saves(cloud):
    cloud.vault.lock()
    ask = {"where": "cloud", "model": "claude-opus-5-5", "key": KEY}
    r = cloud.client.post("/toolsmith/model", headers=DEV, json=ask)
    body = r.get_json()
    assert r.status_code == 423 and body["code"] == "vault_locked"
    assert body["message"] == "Your vault is locked. Type your vault PIN to save the key."
    assert KEY not in r.get_data(as_text=True) and not tc.in_use()
    u = cloud.client.post("/vault/unlock", headers=AUTH, json={"pin": PIN})
    assert u.status_code == 200 and u.get_json()["success"] is True
    again = cloud.client.post("/toolsmith/model", headers=DEV, json=ask)
    assert again.status_code == 200 and again.get_json()["where"] == "cloud"
    assert keys._record_for(cloud.vault, "anthropic")["value"] == KEY  # noqa: SLF001
    assert tc.in_use()


def test_before_the_plan_is_approved_the_save_says_so_plainly(cloud, monkeypatch):
    from puffpai import outside_services

    def refuse(*_a, **_k):
        raise keys.KeyRefused("This bot has not been set up yet, so nothing can be "
                              "switched on.", "cannot")
    monkeypatch.setattr(outside_services, "_has_posture_record", lambda: False)
    monkeypatch.setattr(keys, "add_key", refuse)
    r = cloud.client.post("/toolsmith/model", headers=DEV,
                          json={"where": "cloud", "model": "claude-opus-5-5", "key": KEY})
    assert r.status_code == 409 and r.get_json()["code"] == "before_setup"
    assert r.get_json()["message"] == tc.BEFORE_SETUP_LINE and not tc.in_use()


# -- Toolsmith's own finding work on the cloud model (2026-10-14) -------------------

def _repo(name, stars, desc, topics=()):
    return {"full_name": f"o/{name}", "name": name, "stargazers_count": stars,
            "language": "TypeScript", "description": desc, "topics": list(topics),
            "html_url": f"https://github.com/o/{name}", "default_branch": "main"}


def _no_local_brain(*_a, **_k):
    raise AssertionError("the local brain was asked while Toolsmith's cloud answered")


def test_cloud_ranking_keeps_the_app_keeps_the_library_as_code_drops_the_demo(
        cloud, monkeypatch):
    from puffpai.build import apps
    _turn_on(cloud)
    demo = _repo("opentelemetry-demo", 9000, "Astronomy Shop: an OpenTelemetry "
                 "observability demo", ["observability", "demo", "astronomy"])
    real = _repo("stellarium-web", 800, "Planetarium in your browser: see the night sky",
                 ["astronomy", "planetarium"])
    lib = _repo("astropy-lite", 400, "Astronomy calculations: rise and set times, "
                "planet positions", ["astronomy", "library"])
    cloud.replies = [(200, msg([{"type": "text", "text": json.dumps(
        {"apps": [1], "code": [2], "dropped": [0]})}], "end_turn"), {})]
    job = {"id": "sky", "name": "Night sky", "does": "show tonight's stars and planets"}
    # Each README: the app runs with compose; the library has no way to run as an app.
    plans = {"stellarium-web": ("readme", {"steps": ["docker compose up -d"],
                                           "how": "docker", "found": "compose"}),
             "astropy-lite": ("readme", {"steps": ["docker build ."], "how": "docker",
                                         "found": "Dockerfile"})}
    monkeypatch.setattr(apps, "run_plan", lambda it: plans[it["name"]])
    monkeypatch.setattr(apps, "_cand", lambda it, plan, n: {
        "ref": it["full_name"], "full_name": it["full_name"], "name": it["name"]})
    monkeypatch.setattr(apps, "_beyond", lambda *a, **k: [])
    monkeypatch.setattr(apps, "hers_code", lambda j: [])
    monkeypatch.setattr(apps, "catalog_items", lambda j: [])
    got = apps.for_job(job, "Help me learn astronomy.", brain=_no_local_brain,
                       words={"goal": [], "jobs": {"sky": [["astronomy"]]}},
                       found={("astronomy",): [demo, real, lib]})
    assert [c["name"] for c in got] == ["stellarium-web"]          # the app, as an app
    assert [c["name"] for c in job["code"]] == ["astropy-lite"]    # the library, as code
    assert job["code"][0]["ref"] == "code:o/astropy-lite" and apps.CLOUD_CODE not in job["code"][0]
    assert all("opentelemetry" not in c["name"] for c in got + job["code"])   # dropped
    post = cloud.posts[-1]
    b = post["body"]
    assert post["url"] == "https://api.anthropic.com/v1/messages"
    assert "tools" not in b and "thinking" not in b and "tool_choice" not in b
    assert b["max_tokens"] == tc.FIND_MAX_TOKENS
    assert '"Astronomy Shop" observability demo is not an astronomy app' in b["system"]
    sent = json.loads(b["messages"][0]["content"])
    assert sent["job"]["name"] == "Night sky" and sent["goal"].startswith("Help me learn")
    assert sent["projects"][1]["topics"] == ["astronomy", "planetarium"]
    assert sent["projects"][0]["stars"] == 9000
    assert set(sent["projects"][0]) == {"n", "name", "full_name", "description", "stars",
                                        "topics", "language"}
    row = tc.read_usage()["jobs"]["sky"]
    assert row["calls"] == 1 and row["last"]["purpose"] == "Toolsmith ranking for Night sky"
    assert KEY not in json.dumps(tc.read_usage())


def test_cloud_search_words_and_a_cloud_failure_falls_back_to_this_computer(cloud):
    from puffpai.build import apps
    _turn_on(cloud)
    cloud.replies = [(200, msg([{"type": "text", "text": "Here: " + json.dumps(
        {"goal": ["night sky"], "jobs": [{"id": "sky", "phrases": ["planetarium"]}]})}],
        "end_turn"), {})]
    jobs = [{"id": "sky", "name": "Night sky", "does": "show the stars"}]
    words = apps.search_words(jobs, "Help me learn astronomy.", _no_local_brain)
    assert words == {"goal": ["night", "sky"], "jobs": {"sky": [["planetarium"]]}}
    cloud.offline = True
    asked = []

    def local(messages, fmt):
        asked.append(messages[0]["content"][:20])
        return json.dumps({"order": [0]})
    got = apps.rank({"id": "sky", "name": "Night sky"},
                    [_repo("a", 5, "x"), _repo("b", 9, "y")], local)
    assert asked and [i["name"] for i in got] == ["b", "a"]   # local order kept both


def test_with_cloud_off_the_ranking_never_leaves_this_computer(cloud):
    from puffpai.build import apps
    got = apps.rank({"id": "sky", "name": "Night sky"}, [_repo("a", 5, "x")],
                    lambda m, f: json.dumps({"order": [0]}))
    assert [i["name"] for i in got] == ["a"] and cloud.calls == []


def test_a_run_handed_its_own_brain_never_goes_to_the_cloud(cloud):
    _turn_on(cloud)
    run = T.Run({"id": "j", "name": "x"}, [{"ref": "r1", "name": "A"}], lambda _l: None)
    assert run._cloud_scope() is None                     # noqa: SLF001 - use_cloud unset
    run.use_cloud = True
    assert isinstance(run._cloud_scope(), tc.Scope)       # noqa: SLF001


# -- the setting and the key -----------------------------------------------------

def test_local_is_the_default_and_the_key_is_kept_only_in_the_vault(cloud, caplog):
    caplog.set_level("DEBUG")
    assert tc.read_setting() == {"where": "local"} and not tc.in_use()
    st = cloud.client.get("/toolsmith/model", headers=DEV).get_json()
    assert st["where"] == "local" and st["consent_text"] == tc.CONSENT_TEXT
    assert "—" not in tc.CONSENT_TEXT
    got = _turn_on(cloud)
    assert got["where"] == "cloud" and got["model"] == "claude-opus-5-5"
    assert KEY not in json.dumps(got)
    assert keys._record_for(cloud.vault, "anthropic")["value"] == KEY  # noqa: SLF001
    from puffpai.config import get_state_dir
    assert grep_tree(get_state_dir(), KEY) == []
    assert grep_tree(str(cloud.tmp / "vault"), KEY) == []           # encrypted at rest
    assert KEY not in caplog.text
    # The key check went to api.anthropic.com with the key in a header only.
    check = cloud.calls[0]
    assert check["url"] == "https://api.anthropic.com/v1/models"
    assert check["headers"]["anthropic-version"] == "2023-06-01"
    # A key in the address is refused; off is one click.
    r = cloud.client.post(f"/toolsmith/model?key={KEY}", headers=DEV, json={"where": "local"})
    assert r.status_code == 400 and KEY not in r.get_data(as_text=True)
    assert cloud.client.post("/toolsmith/model", headers=DEV,
                             json={"where": "local"}).get_json()["where"] == "local"
    assert not tc.in_use() and not egress_doors.egress_allowed("toolsmith_cloud")[0]


def test_the_routes_need_a_paired_device(cloud):
    assert cloud.client.get("/toolsmith/model", headers=AUTH).status_code == 403
    r = cloud.client.post("/toolsmith/model", headers=AUTH,
                          json={"where": "cloud", "model": "claude-opus-5-5", "key": KEY})
    assert r.status_code == 403 and not tc.in_use()


def test_a_cloud_setting_without_her_yes_reads_as_local(cloud):
    from puffpai import fsutil
    fsutil.atomic_write_json(tc._path(tc.SETTING_FILE), {  # noqa: SLF001
        "schema": tc.SCHEMA, "where": "cloud", "provider": "anthropic",
        "model": "claude-opus-5-5"})
    assert not tc.in_use()
    fsutil.atomic_write_json(tc._path(tc.SETTING_FILE), {  # noqa: SLF001
        "schema": tc.SCHEMA, "where": "cloud", "provider": "anthropic",
        "model": "claude-haiku-9", "consent": {"text": tc._sha(tc.CONSENT_TEXT)[:16]}})
    assert not tc.in_use()


# -- the request shape -----------------------------------------------------------

def test_the_body_and_headers_match_the_current_api(cloud):
    _turn_on(cloud)
    tc.send(_scope(), system="You are Toolsmith.", tools=[
        {"name": "zeta", "description": "z", "input_schema": {"type": "object"}},
        {"name": "alpha", "description": "a", "input_schema": {"type": "object"}}],
        messages=[{"role": "user", "content": [{"type": "text", "text": "the job"}]}],
        app_name="NewsApp")
    post = cloud.posts[-1]
    assert post["url"] == "https://api.anthropic.com/v1/messages"
    h = post["headers"]
    assert h["x-api-key"] == KEY and h["anthropic-version"] == "2023-06-01"
    assert h["content-type"] == "application/json"
    assert h["anthropic-beta"] == "server-side-fallback-2026-07-01"
    b = post["body"]
    assert b["model"] == "claude-opus-5-5" and b["max_tokens"] == 16000
    assert b["system"] == "You are Toolsmith."
    assert [t["name"] for t in b["tools"]] == ["alpha", "zeta"]
    assert b["cache_control"] == {"type": "ephemeral"}
    assert b["output_config"] == {"effort": "high"} and b["fallbacks"] == "default"
    assert "thinking" not in b and "tool_choice" not in b and "budget_tokens" not in json.dumps(b)


def test_a_model_that_refuses_fallbacks_is_asked_again_without_them(cloud):
    _turn_on(cloud, "claude-sonnet-5-5")
    cloud.replies = [(400, {"type": "error", "error": {"message": "fallbacks not supported"}}, {}),
                     (200, msg([{"type": "text", "text": "ok"}], "end_turn"), {}),
                     (200, msg([{"type": "text", "text": "ok"}], "end_turn"), {})]
    scope = _scope()
    tc.send(scope, system="s", tools=[], messages=[{"role": "user", "content": "x"}])
    tc.send(scope, system="s", tools=[], messages=[{"role": "user", "content": "x"}])
    bodies = [p["body"] for p in cloud.posts]
    assert "fallbacks" in bodies[0] and "fallbacks" not in bodies[1] \
        and "fallbacks" not in bodies[2]
    assert "anthropic-beta" not in cloud.posts[2]["headers"]


def test_busy_and_server_errors_are_retried_honoring_retry_after(cloud):
    _turn_on(cloud)
    cloud.replies = [(429, {"error": {}}, {"Retry-After": "7"}),
                     (529, {"error": {}}, {}),
                     (200, msg([{"type": "text", "text": "ok"}], "end_turn"), {})]
    got = tc.send(_scope(), system="s", tools=[], messages=[{"role": "user", "content": "x"}])
    assert got["stop_reason"] == "end_turn"
    sleeps = [c["s"] for c in cloud.calls if c["method"] == "SLEEP"]
    assert sleeps[0] == 7.0 and len(sleeps) == 2


def test_a_key_that_is_not_accepted_says_so_plainly(cloud):
    _turn_on(cloud)
    cloud.replies = [(401, {"type": "error"}, {})]
    with pytest.raises(tc.CloudError) as err:
        tc.send(_scope(), system="s", tools=[], messages=[{"role": "user", "content": "x"}])
    assert err.value.message == ("Toolsmith's Anthropic key was not accepted. "
                                 "Check it in Settings.")


def test_no_network_falls_back_to_the_local_model_for_that_try(cloud, monkeypatch):
    _turn_on(cloud)
    cloud.offline = True
    with pytest.raises(tc.CloudOffline):
        tc.send(_scope(), system="s", tools=[], messages=[{"role": "user", "content": "x"}])
    # In Run.go: that try runs on the local model, with one plain line.
    from puffpai import brain_loop
    local_calls, lines = [], []
    monkeypatch.setattr(brain_loop, "run", lambda *a, **k: local_calls.append(1) or
                        {"text": "", "steps": 1})
    monkeypatch.setattr(T, "taken_ports", lambda extra=(): [])
    run = T.Run({"id": "j", "name": "News"}, [{"ref": "r1", "name": "NewsApp"}],
                lines.append)
    run.use_cloud = True
    run.go(object())
    assert local_calls and tc.OFFLINE_LINE in lines


# -- the loop ------------------------------------------------------------------------

THINK = {"type": "thinking", "thinking": "Let me look at the README first.",
         "signature": "EqQBCkYIBxgCKkBzaWduYXR1cmUtYnl0ZXMtdGhhdC1tdXN0LXN0YXk="}


def test_assistant_blocks_go_back_exactly_and_history_is_append_only(cloud):
    _turn_on(cloud)
    first = [THINK, {"type": "text", "text": "Downloading."},
             {"type": "tool_use", "id": "toolu_01A", "name": "download",
              "input": {"command": "x"}}]
    second = [{"type": "thinking", "thinking": "", "signature": "Zm9v"},
              {"type": "tool_use", "id": "toolu_02B", "name": "terminal",
               "input": {"command": "npm install"}}]
    cloud.replies = [(200, msg(first, "tool_use"), {}), (200, msg(second, "tool_use"), {}),
                     (200, msg([{"type": "text", "text": "Started."}], "end_turn"), {})]
    record: list = []
    tools = _tools(record)
    conv = L.Conversation("You are Toolsmith.", "The job: news")
    got = L.run(conv, scope=_scope(), ctx=_ctx(tools), tools=tools, max_steps=10,
                app_name="NewsApp")
    assert got["stop"] == "end_turn" and got["text"] == "Started."
    posts = [p["body"]["messages"] for p in cloud.posts]
    assert len(posts) == 3
    # byte for byte: the assistant turn sent back is the block list returned
    assert json.dumps(posts[1][1]["content"]) == json.dumps(first)
    assert json.dumps(posts[2][3]["content"]) == json.dumps(second)
    # every earlier request is an unchanged prefix of the next one
    for a, b in zip(posts, posts[1:]):
        assert [json.dumps(m, sort_keys=True) for m in b[:len(a)]] == \
               [json.dumps(m, sort_keys=True) for m in a]
    # KEEP_GOING appends; nothing earlier changes
    before = json.dumps(conv.messages)
    conv.add_user_text(T.KEEP_GOING)
    assert json.dumps(conv.messages).startswith(before[:-1])


def test_all_tool_results_go_back_in_one_user_message(cloud):
    _turn_on(cloud)
    calls = [{"type": "tool_use", "id": "toolu_A", "name": "terminal",
              "input": {"command": "ls"}},
             {"type": "tool_use", "id": "toolu_B", "name": "download",
              "input": {"command": "y"}}]
    cloud.replies = [(200, msg(calls, "tool_use"), {}),
                     (200, msg([{"type": "text", "text": "ok"}], "end_turn"), {})]
    record: list = []
    tools = _tools(record, fail={"download"})
    conv = L.Conversation("s", "task")
    L.run(conv, scope=_scope(), ctx=_ctx(tools), tools=tools, max_steps=5)
    assert [r[0] for r in record] == ["terminal", "download"]
    last = cloud.posts[1]["body"]["messages"][-1]
    assert last["role"] == "user" and len(last["content"]) == 2
    a, b = last["content"]
    assert (a["type"], a["tool_use_id"], b["tool_use_id"]) == ("tool_result", "toolu_A",
                                                                "toolu_B")
    assert "is_error" not in a and b["is_error"] is True


def test_a_refusal_ends_the_try_with_a_plain_line(cloud):
    _turn_on(cloud)
    cloud.replies = [(200, msg([], "refusal", stop_details={"category": "cyber"}), {})]
    tools = _tools([])
    got = L.run(L.Conversation("s", "t"), scope=_scope(), ctx=_ctx(tools), tools=tools,
                max_steps=5)
    assert got["stop"] == "refusal" and "would not help" in got["text"] and "cyber" in got["text"]


def test_max_tokens_goes_on_once(cloud):
    _turn_on(cloud)
    cloud.replies = [(200, msg([{"type": "text", "text": "part"}], "max_tokens"), {}),
                     (200, msg([{"type": "text", "text": "more"}], "max_tokens"), {})]
    tools = _tools([])
    got = L.run(L.Conversation("s", "t"), scope=_scope(), ctx=_ctx(tools), tools=tools,
                max_steps=5)
    assert len(cloud.posts) == 2 and got["stop"] == "end_turn"
    assert cloud.posts[1]["body"]["messages"][-1]["content"][-1]["text"] == L.CONTINUE_LINE


def test_a_try_that_grows_too_large_ends_rather_than_trimming(cloud):
    _turn_on(cloud)
    big = {"input_tokens": tc.CONTEXT_TOKENS, "output_tokens": 10}
    cloud.replies = [(200, msg([{"type": "tool_use", "id": "toolu_C", "name": "terminal",
                                 "input": {"command": "ls"}}], "tool_use", usage=big), {})]
    tools = _tools([])
    got = L.run(L.Conversation("s", "t"), scope=_scope(), ctx=_ctx(tools), tools=tools,
                max_steps=5)
    assert got["stop"] == "full" and len(cloud.posts) == 1


# -- what leaves ------------------------------------------------------------------

def test_outgoing_text_is_screened_for_keys_and_private_details(cloud):
    _turn_on(cloud)
    secret = "ghp_" + "A1b2C3d4" * 5
    calls = [{"type": "tool_use", "id": "toolu_D", "name": "terminal",
              "input": {"command": "cat .env"}}]
    cloud.replies = [(200, msg(calls, "tool_use"), {}),
                     (200, msg([{"type": "text", "text": "ok"}], "end_turn"), {})]

    def leaky(args, ctx=None):
        return {"ok": True, "output": f"GITHUB_TOKEN={secret}\ncall 555-867-5309"}
    tools = [brain_tools.Tool("terminal", "t", {"type": "object", "properties": {
        "command": {"type": "string"}}}, leaky, source="toolsmith")]
    L.run(L.Conversation("s", f"task with {secret}"), scope=_scope(), ctx=_ctx(tools),
          tools=tools, max_steps=5)
    sent = json.dumps([p["body"] for p in cloud.posts])
    assert secret not in sent and "555-867-5309" not in sent and KEY not in sent
    assert "[held in vault]" in sent


def test_each_call_writes_one_outside_line_and_a_usage_row_without_content(cloud):
    _turn_on(cloud)
    usage = {"input_tokens": 1000, "output_tokens": 500, "cache_read_input_tokens": 2000,
             "cache_creation_input_tokens": 400}
    cloud.replies = [(200, msg([{"type": "text", "text": "secret words"}], "end_turn",
                               usage=usage), {})]
    tc.send(_scope("jobX"), system="s", tools=[],
            messages=[{"role": "user", "content": "the readme text"}], app_name="NewsApp")
    lines = [r for r in egress_ledger.read(hours=1) if r.get("service") == "toolsmith_cloud"]
    assert lines and lines[-1]["host"] == "api.anthropic.com"
    assert lines[-1]["what"] == "toolsmith_install" and lines[-1]["bytes_out"] > 0
    row = tc.read_usage()["jobs"]["jobX"]
    assert row["last"]["purpose"] == "Toolsmith install of NewsApp"
    assert row["last"]["model"] == "claude-opus-5-5" and row["last"]["provider"] == "anthropic"
    assert row["tokens"]["cache_read_input_tokens"] == 2000
    stored = open(tc._path(tc.USAGE_FILE), encoding="utf-8").read()  # noqa: SLF001
    assert "readme text" not in stored and "secret words" not in stored
    assert "about $" in tc.status()["spent_line"]


def test_the_cost_estimate(cloud):
    u = {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
         "cache_read_input_tokens": 1_000_000, "cache_creation_input_tokens": 1_000_000}
    assert tc.cost_usd("claude-opus-5-5", u) == pytest.approx(4 + 20 + 0.20 + 5.0)
    assert tc.cost_usd("claude-sonnet-5-5", u) == pytest.approx(2 + 10 + 0.20 + 2.5)
    assert tc.cost_usd("claude-sonnet-5-5", {"output_tokens": 1000}) == pytest.approx(0.01)
    assert tc.cost_usd("other-model", u) == 0.0


# -- local mode unchanged ---------------------------------------------------------

def test_local_mode_uses_the_local_loop_and_never_the_cloud(cloud, monkeypatch):
    from puffpai import brain_loop
    seen = []
    monkeypatch.setattr(brain_loop, "run", lambda messages, **k: seen.append(
        [m["role"] for m in messages]) or {"text": "", "steps": 1})
    monkeypatch.setattr(tc, "send", lambda *a, **k: pytest.fail("cloud used in local mode"))
    monkeypatch.setattr(T, "taken_ports", lambda extra=(): [])
    run = T.Run({"id": "j", "name": "News"}, [{"ref": "r1", "name": "NewsApp"}],
                lambda _l: None)
    run.use_cloud = True                      # asked for, but the setting says local
    run.go(object())
    assert seen and seen[0] == ["system", "user"]
    # and with the cloud on, a brain handed in still stays local
    _turn_on(cloud)
    seen.clear()
    monkeypatch.setattr(T.Run, "go", lambda self, backend: (
        seen.append(getattr(self, "use_cloud", None)), None)[1])
    T.run_job({"id": "j", "name": "News"}, [{"ref": "r1", "name": "NewsApp"}],
              brain=object())
    assert seen == [False]


# -- the build-scoped key lease (2026-10-14) ----------------------------------------

ASK = {"system": "s", "tools": [], "messages": [{"role": "user", "content": "x"}]}


def _idle_lock(cloud) -> None:
    cloud.vault._lock(reason="idle")  # noqa: SLF001 - what the idle watchdog does
    assert not cloud.vault.is_unlocked


def _ready_line(line: str) -> bool:
    return bool(re.fullmatch(r"Toolsmith's key is ready until \d{1,2}:\d\d [AP]M( tomorrow)?\. "
                             r"Lock the vault to stop it\.", line))


def _audit_text() -> str:
    path = pathlib.Path(tc._path("security_logs")) / "toolsmith_cloud_audit.jsonl"  # noqa: SLF001
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_the_lease_survives_the_idle_lock_and_ends_when_she_locks_the_vault(cloud):
    _turn_on(cloud)
    scope = _scope()
    tc.run_started(scope)
    assert tc.lease_active()
    assert _ready_line(tc.status()["lease_line"])
    _idle_lock(cloud)
    assert tc.lease_active()                                  # idle does not end it
    got = tc.send(scope, **ASK)
    assert got["stop_reason"] == "end_turn" and cloud.posts[-1]["headers"]["x-api-key"] == KEY
    # Every other vault item stays locked: no lease, no key.
    with pytest.raises(keys.KeyUnavailable) as exc:
        keys.keyed_get(keys.handle("anthropic"), url="https://api.anthropic.com/v1/models",
                       caller="chat")
    assert exc.value.reason == "locked"
    # "Lock vault now" (already idle-locked): the lease ends, the next call pauses.
    r = cloud.client.post("/vault/lock", headers=AUTH)
    assert r.status_code == 200
    assert not tc.lease_active() and tc.status()["lease_line"] == ""
    with pytest.raises(tc.CloudError) as err:
        tc.send(scope, **ASK)
    assert err.value.code == "locked"
    # A lock by hand while open is heard too.
    cloud.vault.unlock(PIN)
    assert tc.take_lease(scope) and tc.lease_active()
    cloud.vault.lock()
    assert not tc.lease_active()
    tc.run_ended(scope)
    log = _audit_text()
    assert tc.LEASE_ON_LINE in log and "you locked the vault" in log


def test_the_lease_ends_on_cloud_off_a_new_key_and_the_cap(cloud, monkeypatch):
    _turn_on(cloud)
    scope = _scope()
    assert tc.take_lease(scope)
    tc.use_local()                                            # cloud Toolsmith off
    assert not tc.lease_active()
    _turn_on(cloud)
    assert tc.take_lease(scope)
    old = tc._lease  # noqa: SLF001
    _turn_on(cloud)                                           # the key pasted again
    assert tc.lease_active() and tc._lease is not old          # a new lease, the new key
    assert not any(old._buf)  # noqa: SLF001 - the old one zeroed
    assert tc.take_lease(scope)
    keys.remove_key("anthropic", vault=cloud.vault)          # the key removed
    assert not tc.lease_active()
    _turn_on(cloud)
    assert tc.take_lease(scope)
    index = keys._read_index()  # noqa: SLF001 - another process replaced it
    index["anthropic"]["stored_at"] = "2026-10-14T00:00:00+00:00"
    keys._write_index(index)  # noqa: SLF001
    _idle_lock(cloud)
    with pytest.raises(tc.CloudError):
        tc.send(scope, **ASK)
    assert tc._lease is None  # noqa: SLF001
    cloud.vault.unlock(PIN)
    # The queue going idle no longer ends it: the key stays for the session.
    tc.run_started(scope)
    tc.run_ended(scope)
    tc.queue_idle()
    assert tc.lease_active()
    # The 24-hour hard cap, even "until I lock the vault".
    tc.drop_lease("lock")
    tc.set_keep("lock")
    now = {"t": 1000.0}
    monkeypatch.setattr(tc, "_clock", lambda: now["t"])
    assert tc.take_lease(scope)
    now["t"] += tc.LEASE_MAX_S - 1
    assert tc.lease_active()
    now["t"] += 2
    assert not tc.lease_active()
    log = _audit_text()
    for why in ("you turned cloud Toolsmith off", "its key changed", "24 hours passed"):
        assert why in log
    for line in (tc.LEASE_CARD_LINE, tc.LEASE_ON_LINE, tc.VAULT_WAIT_LINE, tc.KEEP_QUESTION,
                 *tc.KEEP_LABELS.values()):
        assert "—" not in line


def test_the_leased_key_never_reaches_disk_logs_or_answers(cloud, caplog):
    from puffpai.config import get_state_dir
    caplog.set_level("DEBUG")
    _turn_on(cloud)
    scope = _scope()
    tc.run_started(scope)
    held = tc._lease  # noqa: SLF001
    assert KEY not in repr(held) and KEY not in str(held)
    _idle_lock(cloud)
    tc.send(scope, **ASK)
    st = cloud.client.get("/toolsmith/model", headers=DEV)
    assert KEY not in st.get_data(as_text=True)
    assert _ready_line(st.get_json()["lease_line"])
    tc.run_ended(scope)                                       # the session goes on
    assert tc.lease_active()
    cloud.vault.unlock(PIN)                                   # taken again at unlock: same lease
    assert tc._lease is held  # noqa: SLF001
    tc.set_keep("4h")
    assert cloud.client.post("/vault/lock", headers=AUTH).status_code == 200
    assert not tc.lease_active() and not any(held._buf)  # noqa: SLF001 - zeroed
    assert KEY not in caplog.text
    assert "Toolsmith let go of its Anthropic key: you locked the vault." in caplog.text
    assert grep_tree(get_state_dir(), KEY) == []
    assert grep_tree(cloud.tmp, KEY) == []


def test_only_the_key_broker_and_toolsmith_reach_the_lease(cloud):
    _turn_on(cloud)
    scope = _scope()
    assert tc.take_lease(scope)
    held = tc._lease  # noqa: SLF001
    with pytest.raises(PermissionError):
        keys.read_for_lease("anthropic", vault=cloud.vault)  # not toolsmith_cloud
    with pytest.raises(tc.ScopeRefused):
        tc.lease_record(held, "anthropic", "toolsmith", "")  # not the key broker
    with pytest.raises(tc.ScopeRefused):
        tc._Lease(object(), KEY, ["api.anthropic.com"], "")  # noqa: SLF001
    for fake in (None, "toolsmith", object(), object.__new__(tc.Scope)):
        with pytest.raises(tc.ScopeRefused):
            tc.take_lease(fake)
        with pytest.raises(tc.ScopeRefused):
            tc.run_started(fake)
    _idle_lock(cloud)
    # Another caller handing the live lease to the broker still gets nothing.
    for caller in ("chat", "voice", "skills"):
        with pytest.raises(keys.KeyUnavailable) as exc:
            keys._keyed(keys.handle("anthropic"),  # noqa: SLF001
                        url="https://api.anthropic.com/v1/models", caller=caller, lease=held)
        assert exc.value.reason == "locked"
    with pytest.raises(keys.KeyUnavailable):                  # a forged lease object
        keys._keyed(keys.handle("anthropic"),  # noqa: SLF001
                    url="https://api.anthropic.com/v1/messages", caller="toolsmith",
                    lease=object(), method="POST", data=b"{}")
    assert not [p for p in cloud.posts if p["url"].endswith("/v1/messages")]


def _locked_run(cloud, monkeypatch, lines):
    monkeypatch.setattr(T, "taken_ports", lambda extra=(): [])
    monkeypatch.setattr(tc, "WAIT_POLL_S", 0.05)
    _turn_on(cloud)
    tc.drop_lease("lock")                                     # saving the key took one
    _idle_lock(cloud)                                         # locked before the run: no lease
    run = T.Run({"id": "j", "name": "News"}, [{"ref": "r1", "name": "NewsApp"}],
                lines.append)
    run.use_cloud = True
    return run


def test_a_locked_vault_pauses_in_place_then_goes_on_when_she_unlocks(cloud, monkeypatch):
    lines: list = []
    run = _locked_run(cloud, monkeypatch, lines)
    box: dict = {}
    t = threading.Thread(target=lambda: box.update(got=run.go(object())), daemon=True)
    t.start()
    for _ in range(1000):
        if tc.VAULT_WAIT_LINE in lines and tc.waiting_for_vault():
            break
        t.join(0.01)
    assert tc.VAULT_WAIT_LINE in lines and tc.waiting_for_vault()
    assert cloud.posts == [] and run.failed == [] and run.gave_up == []
    cloud.vault.unlock(PIN)                                   # the unlock event wakes it
    t.join(30)
    assert not t.is_alive()
    assert any(str(line).startswith("Your vault is open. Toolsmith goes on with")
               for line in lines)
    assert [p for p in cloud.posts if p["url"].endswith("/v1/messages")]
    assert run.failed == [] and not run.vault_wait and not tc.waiting_for_vault()
    assert "Trying next" not in " ".join(map(str, lines))
    assert tc.lease_active()                                  # kept for the session


def test_a_run_still_waiting_after_the_cap_marks_nothing_tried(cloud, monkeypatch):
    lines: list = []
    run = _locked_run(cloud, monkeypatch, lines)
    monkeypatch.setattr(tc, "WAIT_LIMIT_S", 0.15)
    assert run.go(object()) is None
    assert run.vault_wait and run.failed == [] and run.gave_up == []
    assert cloud.posts == [] and "Trying next" not in " ".join(map(str, lines))


# -- the session key lease (item 13, 2026-10-14) --------------------------------------

def _fake_clock(monkeypatch) -> dict:
    now = {"t": 5000.0}
    monkeypatch.setattr(tc, "_clock", lambda: now["t"])
    return now


def test_the_key_survives_idle_locks_across_two_runs_ten_minutes_apart(cloud, monkeypatch):
    now = _fake_clock(monkeypatch)
    _turn_on(cloud)
    first = _scope("job1")
    tc.run_started(first)
    tc.send(first, **ASK)
    tc.run_ended(first)
    tc.queue_idle()                                           # the queue went idle
    _idle_lock(cloud)                                         # 5 idle minutes: the vault locks
    now["t"] += 10 * 60
    second = _scope("job2")
    tc.run_started(second)                                    # 10 minutes later, vault locked
    assert tc.lease_active() and not cloud.vault.is_unlocked
    got = tc.send(second, **ASK)
    assert got["stop_reason"] == "end_turn"
    tc.run_ended(second)
    sent = [p for p in cloud.posts if p["url"].endswith("/v1/messages")]
    assert len(sent) == 2 and all(p["headers"]["x-api-key"] == KEY for p in sent)
    # Every other vault item stays on the 5-minute lock.
    with pytest.raises(keys.KeyUnavailable) as exc:
        keys.keyed_get(keys.handle("anthropic"), url="https://api.anthropic.com/v1/models",
                       caller="chat")
    assert exc.value.reason == "locked"


def test_the_key_is_let_go_after_her_chosen_quiet_time(cloud, monkeypatch, caplog):
    caplog.set_level("INFO")
    now = _fake_clock(monkeypatch)
    _turn_on(cloud)
    scope = _scope()
    assert tc.keep_choice() == "1h" and tc.status()["keep"] == "1h"      # the default
    assert tc.take_lease(scope)
    _idle_lock(cloud)
    now["t"] += 59 * 60
    assert tc.lease_active()
    tc.send(scope, **ASK)                                     # Toolsmith activity: starts again
    now["t"] += 59 * 60
    assert tc.lease_active()
    now["t"] += 61
    assert not tc.lease_active()                              # an hour with no work
    assert "Toolsmith had no work for 1 hour" in _audit_text()
    assert "Toolsmith let go of its Anthropic key: Toolsmith had no work for 1 hour."         in caplog.text
    # A run going counts as work, however long it takes.
    cloud.vault.unlock(PIN)
    tc.run_started(scope)
    _idle_lock(cloud)
    now["t"] += 3 * 60 * 60
    assert tc.lease_active()
    tc.run_ended(scope)
    now["t"] += 60 * 60
    assert not tc.lease_active()
    # 4 hours.
    cloud.vault.unlock(PIN)
    tc.set_keep("4h")
    assert tc.lease_active()
    now["t"] += 4 * 60 * 60 - 1
    assert tc.lease_active()
    now["t"] += 2
    assert not tc.lease_active()
    # Until I lock: no quiet end, still the 24-hour cap.
    cloud.vault.unlock(PIN)
    tc.set_keep("lock")
    now["t"] += 23 * 60 * 60
    assert tc.lease_active()
    now["t"] += 60 * 60
    assert not tc.lease_active() and "24 hours passed" in _audit_text()
    # A shorter choice applies at once.
    cloud.vault.unlock(PIN)
    now["t"] += 2 * 60 * 60
    assert tc.lease_active()
    tc.set_keep("1h")
    assert tc._lease is None  # noqa: SLF001


def test_unlocking_with_cloud_toolsmith_on_makes_the_key_ready(cloud, monkeypatch, caplog):
    caplog.set_level("INFO")
    _turn_on(cloud)
    tc.drop_lease("lock")
    _idle_lock(cloud)
    cloud.vault.unlock(PIN)                                   # once, at the start
    assert tc.lease_active()
    assert _ready_line(tc.lease_line())
    assert "you unlocked the vault" in _audit_text() and tc.LEASE_ON_LINE in caplog.text
    _idle_lock(cloud)
    scope = _scope()
    tc.run_started(scope)
    assert tc.send(scope, **ASK)["stop_reason"] == "end_turn"
    tc.run_ended(scope)
    # Cloud Toolsmith off: unlocking holds nothing.
    tc.use_local()
    _idle_lock(cloud)
    cloud.vault.unlock(PIN)
    assert tc._lease is None and tc.lease_line() == ""  # noqa: SLF001
    assert KEY not in caplog.text


def test_a_build_yes_with_the_vault_open_makes_the_key_ready(cloud, monkeypatch):
    from puffpai.build import schema as S
    from puffpai.build import yes as Y
    from puffpai.security import consent_tickets
    _turn_on(cloud)
    tc.drop_lease("lock")
    monkeypatch.setattr(consent_tickets, "usable_device", lambda d: True)
    monkeypatch.setattr(consent_tickets, "mint", lambda *a, **k: "t")
    monkeypatch.setattr(consent_tickets, "redeem", lambda *a, **k: (True, "ok"))
    monkeypatch.setattr(S, "load_build", lambda: {"jobs": [{"id": "j"}]})
    monkeypatch.setattr(S, "update_build", lambda fn: (fn({}), {"yes": {"given": True}})[1])
    monkeypatch.setattr(Y, "_open_jobs", lambda b: [{"id": "j"}])
    monkeypatch.setattr(Y, "sha", lambda b: "d")
    monkeypatch.setattr(Y, "covers", lambda b: False)
    monkeypatch.setattr(Y, "sentence", lambda b: "s")
    monkeypatch.setattr(Y, "_audit", lambda *a: None)
    Y.give("devicetsmith0001")
    assert tc.lease_active() and "you said yes to the build" in _audit_text()
    # With the vault locked a yes takes nothing (the run pauses for it later).
    tc.drop_lease("lock")
    _idle_lock(cloud)
    Y.give("devicetsmith0001")
    assert tc._lease is None  # noqa: SLF001


def test_the_keep_choice_route_needs_a_paired_device_and_a_known_choice(cloud):
    _turn_on(cloud)
    assert cloud.client.post("/toolsmith/model/keep", headers=AUTH,
                             json={"keep": "4h"}).status_code == 403
    bad = cloud.client.post("/toolsmith/model/keep", headers=DEV, json={"keep": "forever"})
    assert bad.status_code == 400 and tc.keep_choice() == "1h"
    r = cloud.client.post("/toolsmith/model/keep", headers=DEV, json={"keep": "lock"})
    body = r.get_json()
    assert r.status_code == 200 and body["keep"] == "lock" and tc.keep_choice() == "lock"
    assert [c["id"] for c in body["keep_choices"]] == ["1h", "4h", "lock"]
    assert body["keep_question"] == "Keep Toolsmith's key ready while I'm building"
    # A junk file reads as the default.
    pathlib.Path(tc._path(tc.KEEP_FILE)).write_text("{not json", encoding="utf-8")  # noqa: SLF001
    assert tc.keep_choice() == "1h"


def test_the_lease_timer_lets_go_on_time(cloud, monkeypatch):
    now = _fake_clock(monkeypatch)
    _turn_on(cloud)
    assert tc.take_lease(_scope())
    assert tc._timer is not None and tc._timer.daemon  # noqa: SLF001
    now["t"] += 60 * 60 + 1
    tc._on_timer()  # noqa: SLF001 - what the timer does when it fires
    assert tc._lease is None and tc._timer is None  # noqa: SLF001


# -- the key ready from the wizard (2026-10-15) ---------------------------------------
#
# Owner: she saved the key in the wizard with the vault open, the vault
# idle-locked while she went on, and the first install paused for a vault she
# could not open from the wizard ("a normal user wouldn't know to do this").

def test_saving_the_key_with_the_vault_open_makes_it_ready_and_it_survives_idle(
        cloud, monkeypatch, caplog):
    caplog.set_level("DEBUG")
    _fake_clock(monkeypatch)
    _turn_on(cloud)                                           # the vault is open: key saved
    assert tc.lease_active() and _ready_line(tc.lease_line())
    assert "you saved its key" in _audit_text()
    _idle_lock(cloud)                                         # 5 idle minutes later
    assert tc.lease_active()
    scope = _scope()
    tc.run_started(scope)
    assert tc.send(scope, **ASK)["stop_reason"] == "end_turn"
    tc.run_ended(scope)
    assert cloud.posts[-1]["headers"]["x-api-key"] == KEY
    # Every other vault item keeps the idle lock.
    with pytest.raises(keys.KeyUnavailable) as exc:
        keys.keyed_get(keys.handle("anthropic"), url="https://api.anthropic.com/v1/models",
                       caller="chat")
    assert exc.value.reason == "locked"
    from puffpai.config import get_state_dir
    assert KEY not in caplog.text and grep_tree(get_state_dir(), KEY) == []


def test_a_locked_vault_at_save_takes_nothing(cloud):
    cloud.vault.lock()
    r = cloud.client.post("/toolsmith/model", headers=DEV,
                          json={"where": "cloud", "model": "claude-opus-5-5", "key": KEY})
    assert r.status_code == 423 and tc._lease is None  # noqa: SLF001


def test_a_new_key_swaps_the_lease_with_no_old_key_held(cloud, monkeypatch):
    new = "sk-ant-api03-" + "R" * 40 + "_test"
    _turn_on(cloud)
    old = tc._lease  # noqa: SLF001
    assert old is not None and bytes(old._buf).decode() == KEY  # noqa: SLF001
    seen: list = []
    real_ends, real_starts = keys._lease_ends, keys._lease_starts  # noqa: SLF001

    def on_file() -> str:
        return keys._record_for(cloud.vault, "anthropic")["value"]  # noqa: SLF001

    def ends(sid):
        real_ends(sid)
        # Let go while the OLD key is still the one on file: before the write.
        seen.append(("ends", on_file() == KEY, tc._lease is None, not any(old._buf)))  # noqa: SLF001

    def starts(sid, v):
        seen.append(("starts", on_file() == new, tc._lease is None))  # noqa: SLF001
        real_starts(sid, v)
    monkeypatch.setattr(keys, "_lease_ends", ends)
    monkeypatch.setattr(keys, "_lease_starts", starts)
    # The fake Anthropic accepts only KEY; let the new key pass its check too.
    real_send = egress_doors._send

    def send(method, url, **kw):
        h = dict(kw.get("headers") or {})
        if h.get("x-api-key") == new:
            kw["headers"] = {**h, "x-api-key": KEY}
        return real_send(method, url, **kw)
    monkeypatch.setattr(egress_doors, "_send", send)
    r = cloud.client.post("/toolsmith/model", headers=DEV,
                          json={"where": "cloud", "model": "claude-opus-5-5", "key": new})
    assert r.status_code == 200, r.get_json()
    assert seen == [("ends", True, True, True), ("starts", True, True)]
    held = tc._lease  # noqa: SLF001
    assert held is not None and held is not old and not any(old._buf)  # noqa: SLF001
    assert bytes(held._buf).decode() == new  # noqa: SLF001
    log = _audit_text()
    assert log.index("its key changed") < log.rindex("you saved its key")
    # A key replaced from another page (the Keys page: keys.add_key) swaps it too.
    before = tc._lease  # noqa: SLF001
    keys.add_key("anthropic", KEY, vault=cloud.vault)
    assert tc.lease_active() and tc._lease is not before  # noqa: SLF001
    assert bytes(tc._lease._buf).decode() == KEY and not any(before._buf)  # noqa: SLF001
    with pytest.raises(tc.ScopeRefused):
        tc.key_saved(cloud.vault)                             # only the key broker says so


def test_a_wizard_build_running_keeps_the_key_ready_past_her_keep_time(cloud, monkeypatch):
    now = _fake_clock(monkeypatch)
    wizard = {"going": True}
    tc.set_wizard_probe(lambda: wizard["going"])              # the wizard's build runs
    _turn_on(cloud)                                           # saved at the Toolsmith step
    _idle_lock(cloud)
    now["t"] += 3 * 60 * 60                                   # a long build: 3 hours
    assert tc.lease_active()
    tc._on_timer()  # noqa: SLF001 - the timer re-arms instead of letting go
    assert tc._lease is not None  # noqa: SLF001
    wizard["going"] = False                                   # the wizard's build ended
    assert tc.lease_active()
    now["t"] += 60 * 60 - 1
    assert tc.lease_active()
    now["t"] += 2
    assert not tc.lease_active()                              # then her 1 hour runs out
    assert "Toolsmith had no work for 1 hour" in _audit_text()
    # The 24-hour cap still holds while the wizard is under way.
    cloud.vault.unlock(PIN)
    wizard["going"] = True
    assert tc.lease_active()
    now["t"] += tc.LEASE_MAX_S + 1
    assert not tc.lease_active() and "24 hours passed" in _audit_text()
    # A probe that breaks is not activity.
    cloud.vault.unlock(PIN)
    tc.set_wizard_probe(lambda: 1 / 0)
    now["t"] += 60 * 60 + 1
    assert not tc.lease_active()


def test_locking_by_hand_cloud_off_and_removing_the_key_end_it_even_in_the_wizard(cloud):
    tc.set_wizard_probe(lambda: True)
    _turn_on(cloud)
    _idle_lock(cloud)
    assert tc.lease_active()
    assert cloud.client.post("/vault/lock", headers=AUTH).status_code == 200
    assert tc._lease is None  # noqa: SLF001
    cloud.vault.unlock(PIN)
    assert tc.lease_active()
    tc.use_local()
    assert tc._lease is None  # noqa: SLF001
    _turn_on(cloud)
    assert tc.lease_active()
    keys.remove_key("anthropic", vault=cloud.vault)
    assert tc._lease is None  # noqa: SLF001
    log = _audit_text()
    for why in ("you locked the vault", "you turned cloud Toolsmith off", "its key changed"):
        assert why in log


def test_the_plan_approved_or_its_build_started_makes_the_key_ready(cloud, monkeypatch):
    from puffpai.wizard import proposal, service
    _turn_on(cloud)
    tc.drop_lease("lock")
    run = type("Run", (), {"shared": {}, "sections": {}, "entries": {}, "log": [],
                           "save": lambda self: None})()
    for name, fn in (("_take_lock", lambda cfg: None), ("_drop_lock", lambda lock: None),
                     ("_saved", lambda cfg, args: run), ("_ensure_checked", lambda r: None),
                     ("_needs_settled", lambda r: True),
                     ("_switch_on_named_shelves", lambda state: None),
                     ("_summary_state", lambda r: {"approved": True}),
                     ("_args", lambda *a: None), ("_cfg", lambda: None)):
        monkeypatch.setattr(service, name, fn)
    monkeypatch.setattr(proposal, "summary_for", lambda **k: {})
    monkeypatch.setattr(proposal, "decide", lambda shown, changes=None, args=None:
                        {"approved": True, "items": []})
    monkeypatch.setattr(proposal, "settle_interview", lambda a, b: None)
    service.approve({})
    assert tc.lease_active() and "you approved the plan" in _audit_text()
    # The build starting does the same.
    tc.drop_lease("lock")
    monkeypatch.setattr(service, "run_rest", lambda *a, **k: {})
    monkeypatch.setattr(service, "_REST", {"thread": None, "result": None, "error": "",
                                           "run": None})
    service.start_rest()
    assert tc.lease_active()
    service._REST["thread"].join(5)  # noqa: SLF001
    # With the vault locked, neither takes anything (the run pauses later).
    tc.drop_lease("lock")
    _idle_lock(cloud)
    service.approve({})
    assert tc._lease is None  # noqa: SLF001
    assert tc.take_for_plan() is False


def test_the_wizard_registers_its_probe_and_counts_its_run_and_build(monkeypatch):
    from puffpai.wizard import service
    monkeypatch.setattr(tc, "_wizard_busy", None)
    service._hook_toolsmith_lease()  # noqa: SLF001
    assert tc._wizard_busy is service._wizard_going  # noqa: SLF001
    monkeypatch.setattr(service, "setup_in_progress", lambda *a, **k: False)
    monkeypatch.setattr(service, "_REST", {"thread": None})
    assert service._wizard_going() is False  # noqa: SLF001
    # A wizard only under way is not a running build: her steps count instead.
    monkeypatch.setattr(service, "setup_in_progress", lambda *a, **k: True)
    assert service._wizard_going() is False  # noqa: SLF001
    alive = threading.Event()
    t = threading.Thread(target=alive.wait, daemon=True)
    t.start()
    monkeypatch.setattr(service, "_REST", {"thread": t})
    assert service._wizard_going() is True  # noqa: SLF001
    alive.set()
    t.join(5)
    assert service._wizard_going() is False  # noqa: SLF001


def test_an_abandoned_wizard_lets_go_after_her_keep_time_from_her_last_step(
        cloud, monkeypatch):
    """Coordinator, 2026-10-15: a saved goal with the plan never approved must
    not hold the key up to 24 hours when she chose 1 hour."""
    from puffpai.wizard import service
    now = _fake_clock(monkeypatch)
    monkeypatch.setattr(tc, "_wizard_busy", service._wizard_going)  # noqa: SLF001
    monkeypatch.setattr(service, "setup_in_progress", lambda *a, **k: True)  # under way
    monkeypatch.setattr(service, "_REST", {"thread": None})  # no build running
    _turn_on(cloud)                                           # saved in the wizard
    _idle_lock(cloud)
    for _ in range(3):                                        # she keeps taking steps
        now["t"] += 50 * 60
        service.note_step("POST", "/wizard/summary")
    assert tc.lease_active()                                  # 150 minutes, steps 50 apart
    now["t"] += 30 * 60
    service.note_step("GET", "/wizard/flow")                  # a page read is not a step
    service.note_step("GET", "/wizard/build")                 # nor a poll with no build
    now["t"] += 30 * 60 + 1
    assert not tc.lease_active()                              # 1 hour after her last step
    assert "Toolsmith had no work for 1 hour" in _audit_text()
    # A step after that does not bring it back.
    service.note_step("POST", "/wizard/summary")
    assert tc._lease is None  # noqa: SLF001
    # Polling the build while it runs counts as a step.
    _idle_lock(cloud)
    cloud.vault.unlock(PIN)
    alive = threading.Event()
    t = threading.Thread(target=alive.wait, daemon=True)
    t.start()
    monkeypatch.setattr(service, "_REST", {"thread": t})
    now["t"] += 50 * 60
    service.note_step("GET", "/wizard/build")
    alive.set()
    t.join(5)
    now["t"] += 50 * 60
    assert tc.lease_active()
    now["t"] += 10 * 60 + 1
    assert not tc.lease_active()
    # 4 hours: counted from her last step the same way.
    _idle_lock(cloud)
    cloud.vault.unlock(PIN)
    tc.set_keep("4h")
    now["t"] += 3 * 60 * 60
    service.note_step("POST", "/wizard/summary")
    now["t"] += 4 * 60 * 60 - 1
    assert tc.lease_active()
    now["t"] += 2
    assert not tc.lease_active()


def test_the_build_views_ask_for_her_pin_only_while_toolsmith_needs_the_vault(
        cloud, monkeypatch):
    from puffpai.build import api as build_api
    from puffpai.wizard import service
    _turn_on(cloud)
    wait = tc.VAULT_WAIT_LINE
    job = {"status": "waiting_yes", "reason": wait}
    assert tc.vault_wait_line(True) == ""                     # vault open, key ready
    _idle_lock(cloud)
    assert tc.vault_wait_line(True) == ""                     # locked, but the lease holds it
    tc.drop_lease("lock")
    assert tc.vault_wait_line(False) == ""                    # nothing needs it
    assert tc.vault_wait_line(True) == wait
    assert build_api._vault_wait_line({"jobs": [job]}) == wait  # noqa: SLF001
    assert build_api._vault_wait_line({"jobs": [{"status": "building"}]}) == wait  # noqa: SLF001
    assert build_api._vault_wait_line({"jobs": [{"status": "working"}]}) == ""  # noqa: SLF001
    monkeypatch.setattr(tc, "_waiting", 1)                    # a run waits in place
    assert tc.vault_wait_line(False) == wait
    monkeypatch.setattr(service, "_REST", {"thread": None, "result": None, "error": "",
                                           "run": None})
    assert service.rest_status()["vault_wait"] == wait
    monkeypatch.setattr(tc, "_waiting", 0)
    assert service.rest_status()["vault_wait"] == ""
    cloud.vault.unlock(PIN)
    assert tc.vault_wait_line(True) == ""
    tc.use_local()                                            # cloud off: never asked
    tc.drop_lease("lock")
    _idle_lock(cloud)
    assert tc.vault_wait_line(True) == ""
    assert "—" not in wait and "door" not in wait.lower() and "beta" not in wait.lower()


def test_the_inline_unlock_is_on_every_build_view_and_uses_the_vault_route():
    root = pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "app"
    box = (root / "_components" / "Jobs" / "VaultWaitUnlock.tsx").read_text(encoding="utf-8")
    assert '"/vault/unlock"' in box and "{ pin: p }" in box
    assert "initialize" not in box                            # never makes a vault
    assert "console." not in box and "localStorage" not in box and "sessionStorage" not in box
    assert 'type="password"' in box and "Unlock" in box
    assert "—" not in box and "beta" not in box.lower()
    for rel in ("onboarding/Conversation.tsx", "onboarding/BuildHandoff.tsx",
                "_components/Jobs/JobsHome.tsx", "dashboard/apps/[id]/BotApps.tsx",
                "dashboard/jobs/[id]/page.tsx"):
        assert "<VaultWaitUnlock" in (root / rel).read_text(encoding="utf-8"), rel
