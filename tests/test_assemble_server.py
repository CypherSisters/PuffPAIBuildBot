"""The assembled app server: run_here plan type ``assembled``.

It runs as its own small process on 127.0.0.1 (in a Job Object, watched like
every app here), serves the scaffold, the spec, settings, its actions and an
OpenAPI description, and asks BuildBot's gateway for everything outside.
Declared sources are fetched by the sources code (``api.fetch_source``, replaced here
with a recorded answer); stream pieces through the Bridge (also recorded).
Nothing here reaches the internet.
"""
from __future__ import annotations

import json
import os
import sys
import types
import urllib.error
import urllib.request

import pytest

from puffpai import drawbridge
from puffpai.assemble import server as assembled
from puffpai.assemble import toolsmith_loop as tl
from puffpai.assemble.glue_runtime import check
from puffpai.assemble.server import app as server_app
from puffpai.assemble.server import gateway, host
from puffpai.integrate import run_here

APP = "earth_pictures"
FEED = "app:earth_pictures:feed"
LIVE = "app:earth_pictures:live"
GLUE = '''def tides(place="home"):
    return [{"when": "06:10", "title": place}, {"when": "18:31", "title": place}]
'''
SPEC = {
    "schema": "assembled_app/v1", "id": APP, "title": "Earth pictures", "caps": ["cap-earth"],
    "settings": [{"key": "home_town", "kind": "place"},
                 {"key": "nasa_key", "kind": "key", "service": "nasa"},
                 {"key": "size", "kind": "choice", "choices": ["small", "large"]}],
    "sources": [
        {"id": "pics", "kind": "http_json", "door": FEED, "service": "photos",
         "url": "https://api.example.org/natural/{date}", "items": "$[*]",
         "fields": {"title": "caption", "when": "date"}},
        {"id": "tides", "kind": "glue", "module": "glue/tides.py", "fn": "tides",
         "args": {"place": "Worthing"}},
        {"id": "live", "kind": "m3u", "door": LIVE, "service": "live",
         "url": "https://live.example.org/earth/index.m3u8", "items": "$[*]",
         "fields": {"stream": "url"}}],
    "views": [{"type": "gallery", "source": "pics", "search": True},
              {"type": "player", "source": "live", "mode": "hls"}],
    "actions": [{"id": "search_pictures", "source": "pics", "input": {"q": "string"},
                 "label": "Search the Earth pictures"},
                {"id": "next_tides", "source": "tides", "input": {}}],
    "checks": [{"cap": "cap-earth", "ask": "Show me pictures of Earth",
                "action": "search_pictures", "input": {"q": "earth"},
                "expect": {"min_items": 1, "fields": ["title"]}}]}
ROWS = [{"title": "Earth over Africa", "when": "2026-10-07"},
        {"title": "Earth over the Pacific", "when": "2026-10-07"}]
PLAYLIST = (b"#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI=\"key.bin\"\nseg1.ts\n"
            b"https://live.example.org/earth/seg2.ts\n")


class Resp:
    def __init__(self, status, ctype, body):
        self.status_code, self.headers, self._body = status, {"Content-Type": ctype}, body

    def iter_content(self, size):
        yield self._body

    def close(self):
        pass


class Lane:
    """``api.fetch_source`` and the Bridge's GET, recorded."""

    def __init__(self):
        self.fetched = []
        self.got = []
        self.answer = {"ok": True, "rows": ROWS}

    def fetch_source(self, app_id, source_id, inp):
        self.fetched.append((app_id, source_id, dict(inp)))
        if not self.answer.get("ok"):
            return self.answer
        q = str(inp.get("q") or "").lower()
        return {"ok": True, "rows": [r for r in ROWS if q in r["title"].lower()]}

    def get(self, door, url, *, timeout, stream):
        self.got.append((door, url))
        if url.endswith(".m3u8"):
            return Resp(200, "application/vnd.apple.mpegurl", PLAYLIST)
        return Resp(200, "video/mp2t", bytes(range(256)) * 4)


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
    lane = Lane()
    api = types.ModuleType("puffpai.assemble.api")
    api.fetch_source = lane.fetch_source
    monkeypatch.setitem(sys.modules, "puffpai.assemble.api", api)
    monkeypatch.setattr(host, "_media_get", lane.get)
    monkeypatch.setattr(host, "_MEDIA_CACHE", {})
    os.makedirs(host.app_folder(APP), exist_ok=True)
    with open(host.spec_path(APP), "w", encoding="utf-8") as fh:
        json.dump(SPEC, fh)
    with open(os.path.join(host.app_folder(APP), "settings.json"), "w", encoding="utf-8") as fh:
        json.dump({"home_town": {"name": "Worthing", "lat": 50.8, "lon": -0.1}}, fh)
    assert tl.write_glue(APP, "tides", GLUE)["ok"]
    yield lane
    try:
        run_here.stop(host.host_id(APP), by="test")
    except Exception:  # noqa: BLE001
        pass
    gateway.stop()


def _open(req):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=60) as resp:
            return resp.status, dict(resp.headers.items()), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read()


def get(port, path, host_header=None, headers=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers or {})
    if host_header:
        req.add_unredirected_header("Host", host_header)
    return _open(req)


def post(port, path, obj):
    return _open(urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="POST",
                                        data=json.dumps(obj).encode(),
                                        headers={"Content-Type": "application/json"}))


# ── the seams the sources code calls ──────────────────────────────────────────────────

def test_lane_a1_reaches_start_and_call_from_the_package():
    assert callable(assembled.start_assembled) and callable(assembled.call_action)


def test_actions_use_lane_a1_for_sources_and_run_glue(world):
    got = assembled.call_action(APP, "search_pictures", {"q": "pacific"})
    assert got["ok"] is True
    assert got["rows"] == [{"title": "Earth over the Pacific", "when": "2026-10-07"}]
    assert world.fetched == [(APP, "pics", {"q": "pacific"})]
    tides = assembled.call_action(APP, "next_tides", {})
    assert tides["ok"] is True and tides["rows"][0] == {"when": "06:10", "title": "Worthing"}


def test_a_refused_source_brings_its_reason_and_fix(world, monkeypatch):
    world.answer = {"ok": False, "rows": [], "reason": "the Bridge switch for photos is off",
                    "fix": {"kind": "switch_on", "door": FEED, "service": "photos"}}
    # The part's switch (a live card's) is the outside list's card row; the
    # page gets the chat's card shape (puffpai.fix_api), never the switch id.
    from puffpai import outside_services
    row = {"id": "card-earth_pictures", "name": "Earth pictures", "state": "off",
           "can_switch": True}
    monkeypatch.setattr(outside_services, "row", lambda sid: row if sid == row["id"] else None)
    monkeypatch.setattr(outside_services, "proposal", lambda sid: "Switch on Earth pictures. "
                        "What leaves your computer: the date. It goes to api.example.org.")
    got = assembled.call_action(APP, "search_pictures", {})
    assert got["ok"] is False and got["rows"] == []
    assert got["reason"] == got["error"] == "the Bridge switch for photos is off"
    assert got["fix"]["kind"] == "switch_on" and got["fix"]["service"] == "card-earth_pictures"
    assert got["fix"]["label"] == "Switch on Earth pictures now?"
    assert got["fix"]["detail"].startswith("Switch on Earth pictures.")
    assert FEED not in json.dumps(got["fix"])
    # Nothing she can switch from here: the reason, and no button.
    row["can_switch"] = False
    got = assembled.call_action(APP, "search_pictures", {})
    assert got["reason"] == "the Bridge switch for photos is off" and "fix" not in got
    assert assembled.call_action(APP, "delete_everything", {})["code"] == "unknown_action"


def test_stream_pieces_only_from_source_sites_with_range(world):
    status, headers, body = host.media(APP, "https://live.example.org/earth/index.m3u8")
    assert status == 200 and headers["Content-Type"] == "application/vnd.apple.mpegurl"
    text = body.decode()
    assert "/media?url=https%3A%2F%2Flive.example.org%2Fearth%2Fseg1.ts" in text
    assert 'URI="/media?url=https%3A%2F%2Flive.example.org%2Fearth%2Fkey.bin"' in text
    status, headers, body = host.media(APP, "https://live.example.org/earth/seg2.ts",
                                       "bytes=10-19")
    assert status == 206 and body == bytes(range(10, 20))
    assert headers["Content-Range"] == "bytes 10-19/1024"
    assert world.got[-1] == (LIVE, "https://live.example.org/earth/seg2.ts")
    assert host.media(APP, "https://live.example.org/earth/seg2.ts", "bytes=5000-")[0] == 416
    assert host.media(APP, "https://evil.example.net/x.ts")[0] == 403
    assert host.media(APP, "http://live.example.org/earth/seg2.ts")[0] == 403


def test_settings_keep_her_values_and_never_a_key(world):
    assert host.settings_view(APP)["values"]["nasa_key"] == {"set": False}
    assert host.save_setting(APP, "size", "large") == {"ok": True, "value": "large"}
    assert host.save_setting(APP, "size", "huge")["error"] == "Pick one of the choices."
    placed = host.save_setting(APP, "home_town", {"name": " Hove ", "lat": "50.83",
                                                  "lon": -0.17})
    assert placed == {"ok": True, "value": {"name": "Hove", "lat": 50.8, "lon": -0.2}}
    assert "error" in host.save_setting(APP, "home_town", {"name": "", "lat": 95, "lon": 0})
    key = host.save_setting(APP, "nasa_key", "sk-123456789")
    assert key["fix"]["kind"] == "key" and key["fix"]["label"] == "Add your nasa key now?"
    assert "vault" in key["error"]
    with open(os.path.join(host.app_folder(APP), "settings.json"), encoding="utf-8") as fh:
        assert "sk-123456789" not in fh.read()
    assert host.save_setting(APP, "nope", "x")["code"] == "unknown_setting"


def test_openapi_lists_each_action_as_a_read():
    doc = server_app.openapi_for(SPEC)
    assert sorted(doc["paths"]) == ["/api/next_tides", "/api/search_pictures"]
    op = doc["paths"]["/api/search_pictures"]["get"]
    assert op["summary"] == "Search the Earth pictures"
    assert op["parameters"] == [{"name": "q", "in": "query", "required": False,
                                 "schema": {"type": "string"}}]


# ── the gateway ──────────────────────────────────────────────────────────────

def test_the_gateway_wants_its_token_its_address_and_the_right_route(world):
    port = gateway.ensure_running()
    token = host.server_token(APP)

    def call(route, tok, body, host_header=None):
        req = urllib.request.Request(f"http://127.0.0.1:{port}/assemble/{APP}/{route}",
                                     data=json.dumps(body).encode(), method="POST",
                                     headers={"X-Puff-Token": tok,
                                              "Content-Type": "application/json"})
        if host_header:
            req.add_unredirected_header("Host", host_header)
        status, _h, raw = _open(req)
        return status, json.loads(raw or b"{}")

    act = {"action": "next_tides", "input": {}}
    assert call("action", "wrong", act)[0] == 403
    assert call("action", token, act, host_header=f"evil.example:{port}")[0] == 421
    run_token = gateway.new_run_token(APP, 30)
    assert call("action", run_token, act)[0] == 403          # a glue run may only fetch
    status, out = call("fetch", run_token, {"source": "pics", "input": {"q": "africa"}})
    assert status == 200 and out["rows"][0]["title"] == "Earth over Africa"
    gateway.end_run_token(run_token)
    assert not gateway.authorised(APP, "fetch", run_token)
    status, out = call("action", token, act)
    assert status == 200 and out["ok"] is True and out["count"] == 2


# ── the server, started through run_here ─────────────────────────────────────

def test_the_server_serves_the_scaffold_contract(world):
    started = assembled.start_assembled(APP)
    assert started["ok"] is True, started
    port = started["port"]
    hid = host.host_id(APP)
    assert run_here.wait_ready(hid, 30)["state"] == "running"
    st = run_here.read_state(hid)
    assert st["plan"]["how"] == "assembled" and st["plan"]["port"] == port
    assert st["plan"]["start"]["tool"] == "assembled_server"

    status, _h, body = get(port, "/spec.json")
    spec = json.loads(body)
    assert status == 200 and spec["title"] == "Earth pictures"
    assert spec["media_proxy"] == "/media"
    status, _h, body = get(port, "/settings.json")
    assert json.loads(body)["values"]["nasa_key"] == {"set": False}
    status, _h, body = post(port, "/settings", {"key": "size", "value": "small"})
    assert json.loads(body) == {"ok": True, "value": "small"}
    status, _h, body = post(port, "/api/search_pictures", {"q": "pacific"})
    assert json.loads(body)["rows"][0]["title"] == "Earth over the Pacific"
    status, _h, body = post(port, "/api/delete_everything", {})
    assert "error" in json.loads(body)
    status, _h, body = get(port, "/openapi.json")
    assert "/api/search_pictures" in json.loads(body)["paths"]
    status, headers, body = get(port, "/api/search_pictures?q=africa")
    assert status == 200 and json.loads(body)["rows"][0]["title"] == "Earth over Africa"
    csp = headers["Content-Security-Policy"]
    assert "media-src 'self' blob:" in csp and "worker-src 'self' blob:" in csp
    status, _h, body = get(port, "/media?url=" + urllib.request.quote(
        "https://live.example.org/earth/seg2.ts", safe=""), headers={"Range": "bytes=0-3"})
    assert status == 206 and body == bytes([0, 1, 2, 3])
    assert get(port, "/", host_header=f"evil.example:{port}")[0] == 421
    assert get(port, "/..%2f..%2fspec.json")[0] == 404

    # Another website open in her browser cannot change a setting or run an
    # action (its Origin, Sec-Fetch-Site or a body that is not JSON says so).
    def raw_post(path, body, headers):
        return _open(urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="POST",
                                            data=body, headers=headers))
    js = {"Content-Type": "application/json"}
    size = json.dumps({"key": "size", "value": "large"}).encode()
    assert raw_post("/settings", size, {**js, "Origin": "https://evil.example"})[0] == 403
    assert raw_post("/settings", size, {**js, "Sec-Fetch-Site": "cross-site"})[0] == 403
    assert raw_post("/settings", size, {**js, "Sec-Fetch-Site": "same-site"})[0] == 403
    assert raw_post("/settings", size, {"Content-Type": "text/plain"})[0] == 403
    assert raw_post("/api/search_pictures", b"{}", {**js, "Origin": "null"})[0] == 403
    assert get(port, "/api/search_pictures?q=a", headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert json.loads(get(port, "/settings.json")[2])["values"]["size"] == "small"
    status, _h, body = raw_post("/settings", size, {**js, "Origin": f"http://127.0.0.1:{port}",
                                                    "Sec-Fetch-Site": "same-origin"})
    assert status == 200 and json.loads(body) == {"ok": True, "value": "large"}

    # Brain tools for assembled apps come from the sources code only: the app tools
    # that read /openapi.json skip this app even with a ready record.
    from puffpai.integrate import host_tools, records
    rec = records.blank(hid, name="Earth pictures", by="test")
    rec.update(status="ready", mode="own_server")
    records.save(rec)
    assert host_tools._apps() == []

    stopped = run_here.stop(hid, by="test")
    assert stopped["status"]["state"] == "stopped"


def test_host_ids_fit_run_here_and_are_stable():
    assert host.host_id(APP) == host.host_id(APP)
    assert run_here._ID_RE.match(host.host_id(APP))
    assert host.host_id(APP) != host.host_id("earth_picture")
    for bad in ("../escape", "Earth", "a", "x" * 40, "earth-pictures"):
        with pytest.raises(host.AssembleError):
            host.host_id(bad)
