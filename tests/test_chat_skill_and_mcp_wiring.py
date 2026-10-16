"""Chat tools, wired end to end on the real dashboard chat route.

The tool loop (brain_loop + brain_tools), the find and install tools
(puffpai.finding) and the tool servers (puffpai.mcp_client) in one build:

* chat finds a skill, asks her before installing it, installs it on her yes,
  and on the next message the skill is in the system notes and the model
  reads it with skill_view;
* Home Assistant connected through MCP (a fake HA on 127.0.0.1): its tools
  are offered in the next chat turn and a call turns a light on.

A scripted model stands in for Ollama; recorded shelf answers stand in for
the sites. Every request is faked; none leaves this computer.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "integrate"))
import integrate_support as sup  # noqa: E402

from finding_fakes import Sites  # noqa: E402
from puffpai import api_server, brain_tools, brain_tools_builtin  # noqa: E402

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}
DEVICE = "deviceaaaa000001"
PAIRED = {**AUTH, "X-Puff-Device-Id": DEVICE}
WEDDING = "clawhub:ivangdavila/wedding-planner"


class FakeBrain:
    """Scripted replies: a string is words, a list is tool calls."""

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
    from puffpai.finding import hermes_index, net
    sites = Sites()
    monkeypatch.setattr(net, "_send", sites)
    hermes_index.forget()
    monkeypatch.setattr(api_server, "_LOOP_UNSUPPORTED", {})
    monkeypatch.setattr(api_server, "_grounding_context", lambda *a, **k: ("", []))
    w["loop"] = FakeBrain()
    monkeypatch.setattr(api_server, "_loop_backend", lambda limits=None: w["loop"])
    api_server.app.config.update(TESTING=True)
    yield w
    hermes_index.forget()
    brain_tools.clear()
    brain_tools_builtin.reset_for_tests()


@pytest.fixture
def client():
    return api_server.app.test_client()


def _chunks(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: {")]


def _content(chunks) -> str:
    return "".join((c["choices"][0]["delta"] or {}).get("content", "") for c in chunks)


def _chat(client, text, *, yes=True):
    """One dashboard chat message; every question is answered ``yes``."""
    resp = client.post("/v1/chat/completions", headers=PAIRED, json={
        "model": "puffpai", "stream": True, "messages": [{"role": "user", "content": text}]})
    seen, asked = [], []
    for piece in resp.response:
        part = piece.decode() if isinstance(piece, bytes) else piece
        for chunk in _chunks(part):
            seen.append(chunk)
            if "puffpai_ask" in chunk:
                asked.append(chunk["puffpai_ask"])
                ok = client.post("/chat/approve", headers=PAIRED,
                                 json={"id": chunk["puffpai_ask"]["id"], "yes": yes})
                assert ok.status_code == 200
    return seen, asked


def _tool_names(call) -> set[str]:
    return {t["function"]["name"] for t in call["tools"] or []}


def test_chat_finds_asks_installs_and_uses_a_skill_on_the_next_message(world, client):
    from puffpai import skills
    loop = world["loop"]
    loop.replies = [
        [("find_things", {"words": "wedding planner", "kinds": ["skill"]})],
        [("install_thing", {"id": WEDDING})],
        "Wedding Planner is installed.",
        [("skill_view", {"name": "wedding-planner"})],
        "Here is your wedding plan.",
    ]
    seen, asked = _chat(client, "find me something to plan my wedding")
    first = loop.calls[0]
    assert {"find_things", "install_thing", "skill_view", "run_command",
            "connect_home_assistant"} <= _tool_names(first)
    notes = " ".join(m["content"] for m in first["messages"] if m["role"] == "system")
    assert "find_things" in notes                      # search before saying you can't
    found = loop.calls[1]["messages"][-1]
    assert found["role"] == "tool" and f"[{WEDDING}]" in found["content"]
    assert asked and asked[0]["title"] == "Install Wedding Planner?"
    assert "ClawHub check: passed" in asked[0]["detail"]
    assert _content(seen) == "Wedding Planner is installed."
    assert [r["folder"] for r in skills.installed()] == ["wedding-planner"]

    seen, _ = _chat(client, "help me plan the day")
    nxt = loop.calls[3]
    notes = " ".join(m["content"] for m in nxt["messages"] if m["role"] == "system")
    # The installed block names it; its description is in skill_view's own.
    assert "## Installed on this computer" in notes and "wedding-planner" in notes
    read = loop.calls[4]["messages"][-1]
    assert read["role"] == "tool" and "SKILL.md" in read["content"]
    assert _content(seen) == "Here is your wedding plan."


def test_a_no_tap_installs_nothing(world, client):
    from puffpai import skills
    world["loop"].replies = [
        [("find_things", {"words": "wedding planner", "kinds": ["skill"]})],
        [("install_thing", {"id": WEDDING})], "Nothing was installed."]
    _, asked = _chat(client, "find me a wedding planner", yes=False)
    assert asked and skills.installed() == []
    assert brain_tools.SAID_NO_LINE in world["loop"].calls[2]["messages"][-1]["content"]


def test_home_assistant_through_mcp_joins_the_chat_and_turns_a_light_on(
        world, client, monkeypatch, tmp_path):
    pytest.importorskip("mcp")
    from fake_ha_mcp import FakeHA
    from keys_support import fast_vault, machine_key_file, reset_keys
    from puffpai import mcp_client
    from puffpai.security import keys
    token = "eyJhbGciOiJIUzI1NiJ9.fake-ha-token-for-wiring.c2lnbmF0dXJl"
    machine_key_file(monkeypatch, tmp_path)
    reset_keys()
    vault = fast_vault(monkeypatch, tmp_path / "vault")
    keys.set_vault_provider(lambda: vault)
    try:
        with FakeHA(token) as ha:
            out = mcp_client.connect_home_assistant(ha.base, token)
            assert out["ok"], out
            world["loop"].replies = [
                [("mcp__home_assistant__HassTurnOn", {"name": "porch light"})],
                "The porch light is on."]
            seen, asked = _chat(client, "turn on the porch light")
            assert "mcp__home_assistant__HassTurnOn" in _tool_names(world["loop"].calls[0])
            assert asked == []                         # HA tools run without a question
            fed = world["loop"].calls[1]["messages"][-1]
            assert fed["role"] == "tool" and "Turned on: Porch light" in fed["content"]
            assert ha.lights["Porch light"]["on"] is True
            assert _content(seen) == "The porch light is on."
            mcp_client.remove_server("home_assistant")
    finally:
        keys.set_vault_provider(None)
        reset_keys()
