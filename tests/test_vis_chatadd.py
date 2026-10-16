"""Adding from chat goes through the tool loop: built-in apps first.

The owner typed "Can you add an interactive calendar view to my dashboard?"
and got the old add shortcut (shelves, "look online"), never the built-in
Calendar. With the tool loop on, an add request now reaches the model, which
has ``list_builtin_apps`` and ``put_on_home``.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "integrate"))
import integrate_support as sup  # noqa: E402

from puffpai import (api_server, brain_loop, brain_tools, brain_tools_builtin,  # noqa: E402
                     home_things, things)

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}
PAIRED = {**AUTH, "X-Puff-Device-Id": "deviceaaaa000001"}
ASK = "Can you add an interactive calander view to my dashboard?"
OLD_WORDS = ("book lists saved on this computer", "It sends only these words",
             "look online", "I can't make new tools yet")


class FakeBrain:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def chat(self, messages, temperature=0.7, max_tokens=2048, tools=None):
        self.calls.append({"messages": [dict(m) for m in messages],
                           "tools": None if tools is None else list(tools)})
        reply = self.replies.pop(0) if self.replies else "All done."
        if isinstance(reply, list):
            return {"content": "", "tool_calls": [
                {"function": {"name": n, "arguments": a}} for n, a in reply]}
        return {"content": reply, "tool_calls": []}


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_TOOL_LOOP", "on")
    brain_tools.clear()
    brain_tools_builtin.reset_for_tests()
    w = sup.setup_chat(monkeypatch, tmp_path, brain=sup.Brain())
    monkeypatch.setattr(api_server, "_LOOP_UNSUPPORTED", {})
    monkeypatch.setattr(api_server, "_grounding_context", lambda *a, **k: ("", []))
    monkeypatch.setattr(api_server, "_query_ollama_direct",
                        lambda *a, **k: {"response": "Parked answer.", "sources": [],
                                         "suggestions": []} if k.get("enriched")
                        else "Parked answer.")
    monkeypatch.setattr(api_server, "_stream_ollama_tokens",
                        lambda *a, **k: iter(["Parked answer."]))
    # A trimmed build that never loaded the calendar's page.
    from puffpai import build_manifest
    record = {"surfaces": {"allowlist": ["knowledge.library"], "derived_from_goal": True}}
    monkeypatch.setattr(build_manifest, "read_manifest", lambda *a, **k: record)
    monkeypatch.setattr(build_manifest, "write_manifest",
                        lambda rec, *a, **k: record.update(rec) or "build.json")
    w["record"] = record
    w["loop"] = FakeBrain()
    monkeypatch.setattr(api_server, "_loop_backend", lambda limits=None: w["loop"])
    api_server.app.config.update(TESTING=True)
    yield w
    brain_tools.clear()
    brain_tools_builtin.reset_for_tests()


def _chunks(body: str) -> list[dict]:
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: {")]


def _content(chunks) -> str:
    return "".join((c["choices"][0]["delta"] or {}).get("content", "") for c in chunks)


def _tool_results(brain) -> list[str]:
    return [m["content"] for call in brain.calls for m in call["messages"]
            if m.get("role") == "tool"]


def test_a_typo_still_finds_the_built_in_calendar():
    found = home_things.search_builtin("interactive calander view")
    assert found and found[0]["id"] == "calendar"
    assert home_things.find_builtin("Calander")["id"] == "calendar"
    assert home_things.search_builtin("zzqx") == []


def test_add_a_calendar_on_the_dashboard_offers_the_built_in_calendar(world):
    world["loop"].replies = [[("list_builtin_apps", {"words": "interactive calander view"})],
                             "I already have a Calendar app. Want it on your home page?"]
    client = api_server.app.test_client()
    body = client.post("/v1/chat/completions", headers=PAIRED, json={
        "model": "puffpai", "stream": True,
        "messages": [{"role": "user", "content": ASK}]}).get_data(as_text=True)
    said = _content(_chunks(body))
    assert said == "I already have a Calendar app. Want it on your home page?"
    for old in OLD_WORDS:
        assert old not in said
    first = world["loop"].calls[0]
    names = {t["function"]["name"] for t in first["tools"]}
    assert {"list_builtin_apps", "put_on_home", "find_things"} <= names
    assert brain_loop.ADD_NOTE in [m["content"] for m in first["messages"]]
    result = _tool_results(world["loop"])[0]
    # Its name and words, never a page address the model would read back.
    assert "[calendar] Calendar" in result and "/dashboard" not in result


def _tile(thing_id):
    return next((t for t in things.listing() if t["id"] == thing_id), None)


def test_her_yes_puts_the_calendar_on_her_home_page(world):
    world["loop"].replies = [[("put_on_home", {"thing": "calendar"})],
                             "Calendar is on your home page now."]
    got = api_server._process_chat_query("yes please, put it on my home page", [])
    assert got["response"] == "Calendar is on your home page now."
    # a tile in "Your <Bot>'s things", opening the app's own page
    tile = _tile("builtin-calendar")
    assert tile and tile["on_home"] and tile["kind"] == "app"
    assert tile["page"] == "/dashboard/apps/calendar" and tile["name"] == "Calendar"
    assert things.page("builtin-calendar")["open"] == "/dashboard/apps/calendar"
    # the build's page list now carries the calendar, so its page shows
    assert "comms.calendar" in world["record"]["surfaces"]["allowlist"]
    off = home_things.put_on_home("calendar", on=False)
    assert off["ok"] and _tile("builtin-calendar")["on_home"] is False
    # removing it from its tile only takes it off the home page
    assert things.remove("builtin-calendar")["ok"]
    assert _tile("builtin-calendar") is None
    assert home_things.find_builtin("calendar") is not None


def test_an_installed_thing_goes_on_home_through_the_things_list(world, monkeypatch):
    from puffpai.finding import install
    row = {"id": "skill-guests", "kind": "skill", "name": "Guest list", "icon": "x",
           "blurb": "Keeps a guest list.", "from": "", "ref": "guests"}
    monkeypatch.setattr(things, "_skills", lambda: [dict(row)])
    monkeypatch.setattr(install, "entries", lambda: [
        {"id": "gh:someone/guests", "entry": "e1", "name": "Guest list",
         "type": "skill", "undo": {"kind": "skill", "folder": "guests"}}])
    assert home_things.list_things() == things.listing()
    off = home_things.put_on_home("e1", on=False)  # by its install entry
    assert off["ok"] and off["page"] == "/dashboard/things/skill-guests"
    assert _tile("skill-guests")["on_home"] is False
    assert home_things.put_on_home("Guest list")["ok"]  # by its name
    assert _tile("skill-guests")["on_home"] is True
    assert not home_things.put_on_home("nothing-like-this")["ok"]


def test_the_old_shortcut_no_longer_answers_add_requests_on_the_loop_path(world):
    world["loop"].replies = ["From the loop."]
    assert api_server._process_chat_query("add a calendar", [])["response"] == "From the loop."
    world["loop"].replies = ["From the loop."]
    assert api_server._process_chat_query(
        "I want a guest list page", [])["response"] == "From the loop."


def test_with_the_loop_off_the_old_add_step_still_answers(world, monkeypatch):
    from puffpai import add_more
    monkeypatch.setenv("PUFFPAI_TOOL_LOOP", "off")
    seen = []
    monkeypatch.setattr(add_more, "chat_turn",
                        lambda msg, **kw: seen.append(kw) or "Old add answer.")
    assert api_server._process_chat_query("add a calendar", [])["response"] == "Old add answer."
    assert seen[0]["new_requests"] is True
    assert world["loop"].calls == []
