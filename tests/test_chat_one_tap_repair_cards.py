"""Fix it now: the chat's one-tap cards (puffpai.fix_api) and POST /fix/apply.

The chat said a switched-off service "needs to be
enabled from the terminal". Now a tool result that says something is off or
not saved yet carries a card with one button, the brain never sends her to a
terminal, and the tap goes through the same consent ticket as the outside page.
No test goes online; the model is a fake.
"""
from __future__ import annotations

import json

import pytest

from livedata_support import FakeResponse, fake_network, set_posture
from puffpai import api_server, brain_loop, brain_tools, brain_tools_builtin, fix_api
from puffpai import plain_words, weather_service
from puffpai.security import consent_tickets, egress_doors, outside_switches

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}
DEV_A = {**AUTH, "X-Puff-Device-Id": "deviceaaaa000001"}
OBJ = {"type": "object", "properties": {}, "additionalProperties": False}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv(egress_doors.APPS_KILL_ENV, raising=False)
    for d in egress_doors.DOORS:
        monkeypatch.delenv(d.env_var, raising=False)
    egress_doors.clear_app_doors()
    outside_switches.forget()
    consent_tickets.reset()
    egress_doors.reset_cache()
    brain_tools.clear()
    brain_tools_builtin.reset_for_tests()
    yield
    brain_tools.clear()
    brain_tools_builtin.reset_for_tests()
    consent_tickets.reset()
    egress_doors.reset_cache()


@pytest.fixture
def client():
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client()


@pytest.fixture
def audit(monkeypatch):
    lines: list[dict] = []
    from puffpai.security import audit_log

    def _log(self, action, user="system", result="success", details="", extra=None):
        lines.append({"action": action, "user": user, "result": result,
                      "details": details})
        return {}
    monkeypatch.setattr(audit_log.AuditLog, "log", _log)
    return lines


class JsonResponse(FakeResponse):
    def json(self):
        return json.loads(self._body)


class FakeBrain:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[list] = []

    def chat(self, messages, temperature=0.7, max_tokens=2048, tools=None):
        self.calls.append([dict(m) for m in messages])
        reply = self.replies.pop(0) if self.replies else "All done."
        if isinstance(reply, list):
            return {"content": "", "tool_calls": [
                {"function": {"name": n, "arguments": a}} for n, a in reply]}
        return {"content": reply, "tool_calls": []}


# -- each kind, from a realistic tool failure ----------------------------------------

def test_the_weather_tool_switched_off_and_no_town_gives_two_cards():
    set_posture("enumerated")
    said = weather_service.state("chat")["message"]   # what get_weather gives back
    assert "switched off" in said and "home town" in said
    fixes = fix_api.fixes_for({"ok": True, "result": said})
    kinds = {f["kind"]: f for f in fixes}
    assert set(kinds) == {"switch_on", "setting"}
    sw = kinds["switch_on"]
    assert sw["service"] == "weather" and sw["label"] == "Switch on Weather forecast now?"
    assert "api.open-meteo.com" in sw["detail"]          # the server's own sentence
    assert kinds["setting"]["key"] == "home" and kinds["setting"]["input"] == "place"
    assert kinds["setting"]["label"] == "Enter your town now?"


def test_a_bridge_refusal_names_the_switch():
    set_posture("enumerated")
    with pytest.raises(egress_doors.EgressBlocked) as exc:
        egress_doors.require_open("news_rss")
    got = {"ok": False, "error": brain_tools.FAILED_LINE.format(name="headlines",
                                                                why=str(exc.value))}
    (fix,) = fix_api.fixes_for(got)
    assert fix["kind"] == "switch_on" and fix["service"] == "news_rss"
    # and the service's own sentence, said by a tool in its own words
    (again,) = fix_api.fixes_for({"ok": True, "result": {
        "said": egress_doors.settings_hint("air_quality")}})
    assert again["service"] == "air_quality"


def test_no_switch_card_when_it_is_on_or_cannot_be_switched_here():
    set_posture("enumerated")
    outside_switches.set_switch("weather", True, by="owner")
    egress_doors.reset_cache()
    said = "The weather forecast is switched off, so I can't check it."
    assert fix_api.fixes_for({"ok": True, "result": said}) == []
    set_posture("sealed")
    assert [f for f in fix_api.fixes_for({"ok": True, "result": said})
            if f["kind"] == "switch_on"] == []


def test_no_town_card_once_the_town_is_saved():
    set_posture("enumerated", home={"label": "Dayton, Ohio", "latitude": 39.8,
                                    "longitude": -84.2, "timezone": "America/New_York"})
    said = "I don't know your home town yet, so I don't know where to check."
    assert fix_api.fixes_for({"ok": True, "result": said}) == []


def test_a_missing_key_gives_a_key_card():
    said = ("NASA needs a free key: 1) open https://api.nasa.gov/ 2) sign up "
            "3) paste the key on the Sign-up keys page. Never paste a key into the chat.")
    (fix,) = fix_api.fixes_for({"ok": False, "error": said})
    assert fix == {"kind": "key", "name": "NASA", "label": "Add your NASA key now?",
                   "get_url": "https://api.nasa.gov/"}


def test_a_missing_program_gives_the_install_card(monkeypatch):
    from puffpai import brain_commands
    seen = {}

    def _card(skill, output, code, command=""):
        seen["output"] = output
        return {"plan": "p1", "line": "It needs ffmpeg.", "steps": ["ffmpeg"],
                "manual": [], "keys": [], "thing": ""}
    monkeypatch.setattr(brain_commands, "missing_card", _card)
    (fix,) = fix_api.fixes_for({"ok": True, "result": {
        "worked": False, "output": "bash: ffmpeg: command not found"}})
    assert fix["kind"] == "install" and fix["card"]["plan"] == "p1"
    assert "command not found" in seen["output"]
    # A command that already carries its own card does not get a second one.
    assert fix_api.fixes_for({"ok": True, "result": {
        "missing": {"plan": "x"}, "output": "ffmpeg: command not found"}}) == []


def test_a_tools_own_fix_is_checked():
    set_posture("enumerated")
    result = {"said": "Not yet.", "fix": {"kind": "switch_on", "service": "weather"}}
    (fix,) = fix_api.fixes_for({"ok": True, "result": result})
    assert fix["service"] == "weather" and "fix" not in result
    assert fix_api.fixes_for({"ok": True, "result": {
        "fix": {"kind": "switch_on", "service": "../etc"}}}) == []
    assert fix_api.fixes_for({"ok": True, "result": {
        "fix": {"kind": "setting", "key": "anything"}}}) == []


def test_card_words_are_plain():
    set_posture("enumerated")
    said = weather_service.state("chat")["message"]
    for fix in fix_api.fixes_for({"ok": True, "result": said}):
        text = f"{fix['label']}\n{fix.get('detail', '')}"
        assert plain_words.check(text) == [], text
        assert "\u2014" not in text and "terminal" not in text.lower()


# -- the brain loop -------------------------------------------------------------------

def test_the_loop_sends_the_card_and_tells_the_model():
    set_posture("enumerated")
    brain_tools.register("get_weather", "The weather.", OBJ,
                         lambda a, c: weather_service.state("chat")["message"])
    brain = FakeBrain([("get_weather", {})], "The weather is switched off. Switch it on now?")
    events: list[dict] = []
    brain_loop.run([{"role": "user", "content": "weather tomorrow?"}],
                   ctx={"caller": "owner"}, backend=brain, emit=events.append)
    fixes = [e["fix"] for e in events if e.get("type") == "fix"]
    assert [f["kind"] for f in fixes] == ["switch_on", "setting"]
    tool_row = next(m for m in brain.calls[1] if m.get("role") == "tool")
    assert "card in the chat" in tool_row["content"]
    assert "terminal" not in tool_row["content"].replace("Do not send her to a terminal", "")
    assert "terminal" in brain_loop.TOOL_NOTE and "card in the chat" in brain_loop.TOOL_NOTE


def test_one_card_per_thing_in_a_turn():
    set_posture("enumerated")
    said = "The weather forecast is switched off, so I can't check it."
    brain_tools.register("get_weather", "The weather.", OBJ, lambda a, c: said)
    brain = FakeBrain([("get_weather", {})], [("get_weather", {})], "Off.")
    events: list[dict] = []
    brain_loop.run([{"role": "user", "content": "weather?"}], ctx={},
                   backend=brain, emit=events.append)
    assert len([e for e in events if e.get("type") == "fix"]) == 1


def test_no_brain_facing_line_says_terminal():
    for state in ("enumerated", "island"):
        set_posture(state)
        assert "terminal" not in weather_service.state("chat")["message"].lower()
    for act_id, spec in plain_words.ACTIONS.items():
        args = {k: ("x1" if kind in ("token", "number") else "Weather forecast")
                for k, kind in spec["args"].items()}
        args = {k: ("1" if spec["args"][k] == "number" else v) for k, v in args.items()}
        chat = plain_words.render_action({"id": act_id, "args": args}, "chat")
        assert "terminal" not in chat.lower(), act_id
    for d in egress_doors.DOORS:
        assert "terminal" not in d.settings_hint.lower(), d.id


def test_the_chat_stream_carries_the_card(monkeypatch):
    from puffpai import fix_api as F
    fix = {"kind": "switch_on", "service": "weather", "name": "Weather forecast",
           "label": "Switch on Weather forecast now?", "detail": "Switch on ..."}
    out = F.public(fix)
    assert out["kind"] == "switch_on" and out["service"] == "weather"
    assert set(out) == {"kind", "label", "service", "name", "detail", "key", "input",
                        "get_url", "position"}
    src = open(api_server.__file__, encoding="utf-8").read()
    assert "puffpai_fix=fix_api.public(event.get('fix'))" in src


# -- POST /fix/apply ------------------------------------------------------------------

def test_apply_needs_the_key_and_a_paired_device(client):
    assert client.post("/fix/apply", json={"kind": "switch_on",
                                           "service": "weather"}).status_code == 401
    r = client.post("/fix/apply", headers=AUTH, json={"kind": "switch_on",
                                                      "service": "weather"})
    assert r.status_code == 403
    assert egress_doors.egress_allowed("weather")[0] is False


def test_apply_switches_on_with_the_same_ticket_and_audit(client, audit):
    set_posture("enumerated")
    r = client.post("/fix/apply", headers=DEV_A, json={"kind": "switch_on",
                                                       "service": "weather"})
    body = r.get_json()
    assert r.status_code == 200 and body["ok"] and body["again"] is True
    assert "switched on" in body["line"]
    assert egress_doors.egress_allowed("weather")[0] is True
    actions = [a["action"] for a in audit]
    assert "consent_ticket_mint" in actions and "consent_ticket_redeem" in actions
    assert {"action": "chat_fix_switch_on", "user": "owner", "result": "on",
            "details": "weather"} in audit
    # Already on: said plainly, nothing else.
    again = client.post("/fix/apply", headers=DEV_A, json={"kind": "switch_on",
                                                           "service": "weather"})
    assert again.status_code == 409 and "already" in again.get_json()["message"]


def test_apply_saves_the_town_through_the_lookup(client, audit, monkeypatch):
    set_posture("enumerated")
    net = fake_network(monkeypatch)
    net.on("https://geocoding-api.open-meteo.com/", JsonResponse(200, json.dumps(
        {"results": [{"name": "Dayton", "admin1": "Ohio", "country": "US",
                      "latitude": 39.7589, "longitude": -84.1916,
                      "timezone": "America/New_York"}]}).encode()))
    r = client.post("/fix/apply", headers=DEV_A, json={"kind": "setting", "key": "home",
                                                       "value": "Dayton"})
    body = r.get_json()
    assert r.status_code == 200 and body["ok"] and body["again"] is True
    assert "Dayton" in body["line"]
    from puffpai import home_location
    assert home_location.get_home()["latitude"] == 39.8
    assert len(net.calls) == 1
    assert {"action": "chat_fix_setting", "user": "owner", "result": "saved",
            "details": "home town"} in audit
    assert not any("Dayton" in a["details"] for a in audit)


def test_a_keep_it_local_bot_asks_for_a_rough_position(client, audit):
    set_posture("island")
    r = client.post("/fix/apply", headers=DEV_A, json={"kind": "setting", "key": "home",
                                                       "value": "Dayton"})
    assert r.status_code == 409 and r.get_json()["position"] is True
    r = client.post("/fix/apply", headers=DEV_A, json={
        "kind": "setting", "key": "home", "value": "Dayton",
        "latitude": "39.76", "longitude": "-84.19"})
    assert r.status_code == 200 and r.get_json()["ok"]
    from puffpai import home_location
    home = home_location.get_home()
    assert home["label"] == "Dayton" and home["longitude"] == -84.2


def test_apply_refuses_keys_and_unknown_kinds(client):
    r = client.post("/fix/apply", headers=DEV_A, json={"kind": "key", "name": "NASA"})
    assert r.status_code == 400 and "Sign-up keys page" in r.get_json()["message"]
    assert client.post("/fix/apply", headers=DEV_A,
                       json={"kind": "run", "command": "rm"}).status_code == 400
    assert client.post("/fix/apply", headers=DEV_A, json={
        "kind": "setting", "key": "api_key", "value": "x"}).status_code == 400
