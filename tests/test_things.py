"""Your bot's things: one list, a page each, the home page switch and Remove.

A skill and a tool server laid out the way the find step installs them (a
SKILL.md in the skills folder, a saved tool server with its tool list), a
fake brain for the page recipe and the "Try it" answer. Nothing goes online.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os

import pytest

from puffpai import api_server, things

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}
DEVICE = "deviceaaaa000001"
PAIRED = {**AUTH, "X-Puff-Device-Id": DEVICE}

SKILL_MD = """---
name: Wedding Planner
description: "Plan weddings with budget guardrails, guest-list scenarios and vendor scorecards."
metadata: {"clawdbot":{"emoji":"\U0001F48D"}}
---

## When to Use

Use when a person is planning a wedding.

## Budget Control

Keep a ceiling and track deposits. The secret word is Marigold.

## Guest List Scenarios

A, B and C lists.

## Vendor Scorecards

Score each vendor.
"""

TOOLS = [
    {"name": "search_nc_wedding_directory",
     "description": "Search nearly 4,000 North Carolina wedding venues and vendors by "
                    "category, city, or free text. Returns listing URLs.",
     "input_schema": {"type": "object", "properties": {
         "city": {"type": "string", "description": "A city"},
         "limit": {"type": "integer", "default": 10}}, "required": []}},
    {"name": "get_nc_wedding_vendor", "description": "Get details for one vendor.",
     "input_schema": {"type": "object", "properties": {"slug": {"type": "string"}},
                      "required": ["slug"]}},
]


class FakeBrain:
    """Answers with what it is given; records what it was asked."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def chat(self, messages, temperature=0.7, max_tokens=2048, tools=None, format=None):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        reply = self.replies.pop(0) if self.replies else "All done."
        if tools is not None:
            return {"content": reply, "tool_calls": []} if isinstance(reply, str) else reply
        return reply


@pytest.fixture
def state(monkeypatch, tmp_path):
    root = tmp_path / "state"
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(root))
    folder = root / "skills" / "wedding-planner"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    (root / "skills" / ".lock.json").write_text(json.dumps({"wedding-planner": {
        "url": "https://clawhub.ai/ivangdavila/skills/wedding-planner"}}), encoding="utf-8")
    (root / "mcp_servers.json").write_text(json.dumps({
        "schema": "puffpai_mcp_servers/v1", "servers": {"nc_wedding_guide": {
            "name": "nc_wedding_guide", "label": "NC Wedding Guide", "kind": "http",
            "url": "https://example.invalid/mcp", "has_password": False,
            "source": "https://github.com/example/nc-wedding", "tools": TOOLS}}}),
        encoding="utf-8")
    ran = []
    monkeypatch.setattr(things, "_start", lambda work: ran.append(work) or work())
    api_server.app.config.update(TESTING=True)
    return root


@pytest.fixture
def client():
    return api_server.app.test_client()


# ── the list ───────────────────────────────────────────────────────────────

def test_the_list_has_every_thing_in_the_pinned_shape(state, client):
    rows = client.get("/things", headers=PAIRED).get_json()
    assert isinstance(rows, list)
    by_id = {r["id"]: r for r in rows}
    assert set(by_id) == {"skill-wedding-planner", "tool-nc_wedding_guide"}
    for r in rows:
        assert set(r) == {"id", "kind", "name", "icon", "blurb", "on_home", "page"}
        assert r["on_home"] is True
        assert r["page"] == f"/dashboard/things/{r['id']}"
    skill = by_id["skill-wedding-planner"]
    assert skill["kind"] == "skill" and skill["name"] == "Wedding Planner"
    assert skill["icon"] == "\U0001F48D"
    assert skill["blurb"].startswith("Plan weddings with budget guardrails")
    tool = by_id["tool-nc_wedding_guide"]
    assert tool["kind"] == "tool_server" and tool["name"] == "NC Wedding Guide"


def test_every_route_needs_the_key_and_a_paired_device(state, client):
    routes = (("get", "/things"), ("get", "/things/skill-wedding-planner"),
              ("post", "/things/skill-wedding-planner/home"),
              ("delete", "/things/skill-wedding-planner"),
              ("post", "/things/tool-nc_wedding_guide/run"),
              ("post", "/things/skill-wedding-planner/ask"))
    for method, path in routes:
        assert getattr(client, method)(path).status_code == 401, path
        got = getattr(client, method)(path, headers=AUTH, json={})
        assert got.status_code == 403, path
        assert got.get_json()["message"] == "Open this from a browser that is paired with your bot."


def test_on_my_home_page_switch(state, client):
    r = client.post("/things/skill-wedding-planner/home", headers=PAIRED, json={"on": False})
    assert r.status_code == 200
    assert r.get_json()["message"] == "Wedding Planner is off your home page."
    rows = {x["id"]: x for x in client.get("/things", headers=PAIRED).get_json()}
    assert rows["skill-wedding-planner"]["on_home"] is False
    assert rows["tool-nc_wedding_guide"]["on_home"] is True
    assert client.post("/things/skill-wedding-planner/home", headers=PAIRED,
                       json={"on": "yes"}).status_code == 400
    assert client.post("/things/skill-nope/home", headers=PAIRED,
                       json={"on": True}).status_code == 404


# ── the page recipe ────────────────────────────────────────────────────────

def test_a_skill_page_is_made_on_first_visit_from_its_words(state, client):
    page_file = state / "things" / "pages" / "skill-wedding-planner.json"
    assert not page_file.exists()
    body = client.get("/things/skill-wedding-planner", headers=PAIRED).get_json()
    assert page_file.exists()
    assert body["kind"] == "skill" and body["kind_words"] == "skill"
    assert body["from"] == "https://clawhub.ai/ivangdavila/skills/wedding-planner"
    recipe = body["recipe"]
    assert recipe["made_by"] == "words" and recipe["title"] == "Wedding Planner"
    assert recipe["can"] == ["Budget Control", "Guest List Scenarios", "Vendor Scorecards"]
    assert recipe["try"][0] == "Help me with budget control"
    assert 3 <= len(recipe["try"]) <= 4
    # the skill's own folder is untouched
    assert os.listdir(state / "skills" / "wedding-planner") == ["SKILL.md"]


def test_a_tool_server_page_has_a_form_per_tool(state, client):
    body = client.get("/things/tool-nc_wedding_guide", headers=PAIRED).get_json()
    assert [t["name"] for t in body["tools"]] == ["search_nc_wedding_directory",
                                                  "get_nc_wedding_vendor"]
    assert body["tools"][0]["input_schema"]["properties"]["limit"]["type"] == "integer"
    assert body["tools"][0]["title"] == "Search nc wedding directory"
    assert body["recipe"]["try"][0] == "Use NC Wedding Guide to search nc wedding directory"
    assert body["from"] == "https://github.com/example/nc-wedding"


def test_the_brain_makes_the_words_once(state, client, monkeypatch):
    monkeypatch.setenv("PUFFPAI_THINGS_BRAIN", "on")
    brain = FakeBrain(json.dumps({
        "icon": "\U0001F470", "line": "Plans your wedding \u2014 budget, guests and vendors.",
        "try": ["Make me a wedding budget for 120 guests", "Help me choose a venue",
                "Open the door to planning"], "can": ["Budgets", "Guest lists"]}))
    monkeypatch.setattr(things, "_backend", lambda: brain)
    body = client.get("/things/skill-wedding-planner", headers=PAIRED).get_json()
    recipe = body["recipe"]
    assert recipe["made_by"] == "brain" and recipe["icon"] == "\U0001F470"
    assert recipe["line"] == "Plans your wedding, budget, guests and vendors."
    assert recipe["try"] == ["Make me a wedding budget for 120 guests", "Help me choose a venue"]
    assert "Marigold" in brain.calls[0]["messages"][1]["content"]
    # asked once: the next visit keeps it
    client.get("/things/skill-wedding-planner", headers=PAIRED)
    assert len(brain.calls) == 1
    tile = {x["id"]: x for x in client.get("/things", headers=PAIRED).get_json()}
    assert tile["skill-wedding-planner"]["blurb"] == recipe["line"]


def test_a_brain_answer_that_is_not_usable_keeps_the_words(state, monkeypatch):
    monkeypatch.setenv("PUFFPAI_THINGS_BRAIN", "on")
    monkeypatch.setattr(things, "_backend", lambda: FakeBrain("sorry, no json here"))
    recipe = things.make_recipe("skill-wedding-planner")
    assert recipe["made_by"] == "words"


# ── Run, Try it, Remove ────────────────────────────────────────────────────

def test_run_a_tool_from_its_form(state, client, monkeypatch):
    from puffpai import mcp_client
    seen = []
    monkeypatch.setattr(mcp_client, "call_tool_with_ui",
                        lambda server, tool, args, from_app=False:
                        seen.append((server, tool, args))
                        or {"text": "Found 2 venues in Raleigh.", "result": None, "ui": None})
    r = client.post("/things/tool-nc_wedding_guide/run", headers=PAIRED,
                    json={"tool": "search_nc_wedding_directory",
                          "args": {"city": "Raleigh", "limit": 2, "query": ""}})
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "result": "Found 2 venues in Raleigh."}
    assert seen == [("nc_wedding_guide", "search_nc_wedding_directory",
                     {"city": "Raleigh", "limit": 2})]
    r = client.post("/things/tool-nc_wedding_guide/run", headers=PAIRED,
                    json={"tool": "delete_everything", "args": {}})
    assert r.status_code == 404


def _chunks(resp) -> list[dict]:
    body = resp.get_data(as_text=True)
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: {")]


def _content(chunks) -> str:
    return "".join((c["choices"][0]["delta"] or {}).get("content", "") for c in chunks)


@pytest.fixture
def loop(monkeypatch):
    monkeypatch.setenv("PUFFPAI_TOOL_LOOP", "on")
    monkeypatch.setattr(api_server, "_LOOP_UNSUPPORTED", {})
    monkeypatch.setattr(api_server, "_grounding_context", lambda *a, **k: ("", []))
    brain = FakeBrain()
    monkeypatch.setattr(api_server, "_loop_backend", lambda limits=None: brain)
    return brain


def test_try_it_answers_through_the_chat_stream_with_the_skill_loaded(state, client, loop):
    loop.replies = ["Set a ceiling first, then track every deposit."]
    r = client.post("/things/skill-wedding-planner/ask", headers=PAIRED,
                    json={"text": "Help me with budget control"})
    assert r.status_code == 200 and r.mimetype == "text/event-stream"
    chunks = _chunks(r)
    assert _content(chunks) == "Set a ceiling first, then track every deposit."
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    sent = loop.calls[0]["messages"]
    system = "\n".join(m["content"] for m in sent if m["role"] == "system")
    assert 'invoked the "Wedding Planner" skill' in system and "Marigold" in system
    assert "do not run commands" not in system
    assert sent[-1] == {"role": "user", "content": "Help me with budget control"}
    # The chat's own tools, the ones that ask for a yes too: the page shows
    # the question ("needs your yes first").
    offered = {t["function"]["name"] for t in loop.calls[0]["tools"]}
    assert "run_command" in offered


def test_a_skill_script_runs_after_one_yes_on_its_page(state, client, loop):
    folder = state / "skills" / "spool-keeper"
    (folder / "scripts").mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: Spool Keeper\ndescription: Keeps a list of spools.\n---\n"
        "To list spools run `python3 scripts/inventory.py`.\n", encoding="utf-8")
    (folder / "scripts" / "inventory.py").write_text(
        "print('3 spools: PLA red, PETG black, TPU clear')\n", encoding="utf-8")
    loop.replies = [
        {"content": "", "tool_calls": [{"function": {"name": "run_command", "arguments": {
            "command": "python3 scripts/inventory.py", "skill": "spool-keeper"}}}]},
        "You have 3 spools: PLA red, PETG black and TPU clear."]
    resp = client.post("/things/skill-spool-keeper/ask", headers=PAIRED,
                       json={"text": "List all spools"})
    seen, asks = [], []
    for piece in resp.response:
        text = piece.decode() if isinstance(piece, bytes) else piece
        for line in text.splitlines():
            if not line.startswith("data: {"):
                continue
            chunk = json.loads(line[6:])
            seen.append(chunk)
            if "puffpai_ask" in chunk:
                asks.append(chunk["puffpai_ask"])
                assert len(loop.calls) == 1        # nothing ran before her yes
                ok = client.post("/chat/approve", headers=PAIRED,
                                 json={"id": chunk["puffpai_ask"]["id"], "yes": True})
                assert ok.status_code == 200 and ok.get_json()["success"] is True
    assert len(asks) == 1
    assert asks[0]["command"] == "python3 scripts/inventory.py"
    assert os.path.normcase(asks[0]["folder"]) == os.path.normcase(str(folder))
    assert any(c.get("puffpai_answered", {}).get("yes") for c in seen)
    fed = loop.calls[1]["messages"][-1]
    assert fed["role"] == "tool" and "PLA red, PETG black, TPU clear" in fed["content"]
    assert _content(seen) == "You have 3 spools: PLA red, PETG black and TPU clear."
    assert seen[-1]["puffpai_meta"]["tools_used"] == ["run_command"]


def test_a_page_ask_carries_the_page_talk_and_the_firm_run_command_note(state, client, loop):
    """PrintingBot check (finding 2): "Add a spool ..." got the command written
    out with "Would you like me to run this?", and "Yes, run it." then lost
    the context. The page sends its last few turns, and the note says to call
    run_command, never to ask in words."""
    loop.replies = ["Okay."]
    history = [{"user": f"q{i}", "assistant": f"a{i}"} for i in range(6)]
    history[-1] = {"user": "Add a spool: brand Polymaker",
                   "assistant": "Would you like me to run this?"}
    r = client.post("/things/skill-wedding-planner/ask", headers=PAIRED,
                    json={"text": "Yes, run it.", "history": [*history[:-1], "junk", history[-1]]})
    assert r.status_code == 200
    _chunks(r)
    sent = loop.calls[0]["messages"]
    talk = [(m["role"], m["content"]) for m in sent if m["role"] in ("user", "assistant")]
    assert ("user", "Add a spool: brand Polymaker") in talk
    assert ("assistant", "Would you like me to run this?") in talk
    assert ("user", "q1") not in talk and ("user", "q2") in talk     # the last 4 turns
    assert talk[-1] == ("user", "Yes, run it.")
    system = "\n".join(m["content"] for m in sent if m["role"] == "system")
    assert "call run_command" in system and "Never write a command out" in system


def test_a_page_ask_without_the_tool_loop_says_ask_in_chat(state, client, monkeypatch):
    monkeypatch.setenv("PUFFPAI_TOOL_LOOP", "off")
    r = client.post("/things/skill-wedding-planner/ask", headers=PAIRED, json={"text": "hi"})
    assert r.status_code == 503
    assert r.get_json()["message"].endswith("so ask in chat instead.")


def test_remove_goes_through_the_find_undo(state, client):
    entries = [{"entry": "t_0123456789", "id": "clawhub:x/wedding-planner",
                "name": "Wedding Planner", "type": "skill",
                "undo": {"kind": "skill", "folder": "wedding-planner"}}]
    (state / "finding").mkdir()
    (state / "finding" / "installed.json").write_text(json.dumps(entries), encoding="utf-8")
    client.get("/things/skill-wedding-planner", headers=PAIRED)
    r = client.delete("/things/skill-wedding-planner", headers=PAIRED)
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert not (state / "skills" / "wedding-planner").exists()
    assert json.loads((state / "finding" / "installed.json").read_text()) == []
    assert not (state / "things" / "pages" / "skill-wedding-planner.json").exists()
    ids = [x["id"] for x in client.get("/things", headers=PAIRED).get_json()]
    assert ids == ["tool-nc_wedding_guide"]
    assert client.delete("/things/skill-wedding-planner", headers=PAIRED).status_code == 404


def test_after_an_install_the_thing_is_on_the_home_page_with_a_page(state):
    from puffpai.finding import install
    entry = install._add_entry({"id": "clawhub:x/wedding-planner", "name": "Wedding Planner",
                                "type": "skill",
                                "undo": {"kind": "skill", "folder": "wedding-planner"}})
    things.set_home("skill-wedding-planner", False)
    out = install._with_page({"ok": True, "status": "installed", "entry": entry["entry"]})
    assert out["page"] == "/dashboard/things/skill-wedding-planner"
    row = next(r for r in things.listing() if r["id"] == "skill-wedding-planner")
    assert row["on_home"] is True
    assert (state / "things" / "pages" / "skill-wedding-planner.json").exists()
    assert install._with_page({"ok": False, "status": "failed"}) == {"ok": False,
                                                                    "status": "failed"}


def test_things_go_by_the_shelf_name_and_the_shelf_line(state, client):
    """A skill whose SKILL.md name is its folder showed as
    "wedding-skill" (the shelf calls it "Wedding.skill"), and a tool server's
    tile and page read its first tool's text instead of what it is for."""
    folder = state / "skills" / "wedding-skill"
    folder.mkdir()
    (folder / "SKILL.md").write_text("---\nname: wedding-skill\ndescription: A co-pilot.\n"
                                     "---\n# wedding.skill\n", encoding="utf-8")
    entries = [{"entry": "t_aaaaaaaaaa", "id": "clawhub:x/wedding", "name": "Wedding.skill",
                "type": "skill", "undo": {"kind": "skill", "folder": "wedding-skill"}},
               {"entry": "t_bbbbbbbbbb", "id": "mcp:example/nc", "name": "NC Wedding Guide",
                "type": "mcp", "what": "Plan a North Carolina wedding. Venues and costs.",
                "undo": {"kind": "mcp", "server": "nc_wedding_guide"}}]
    (state / "finding").mkdir()
    (state / "finding" / "installed.json").write_text(json.dumps(entries), encoding="utf-8")
    by_id = {r["id"]: r for r in things.listing()}
    assert by_id["skill-wedding-skill"]["name"] == "Wedding.skill"
    assert by_id["skill-wedding-planner"]["name"] == "Wedding Planner"   # no shelf entry
    tool = by_id["tool-nc_wedding_guide"]
    assert tool["blurb"] == "Plan a North Carolina wedding."
    page = client.get("/things/tool-nc_wedding_guide", headers=PAIRED).get_json()
    assert page["recipe"]["line"] == tool["blurb"]
    assert page["recipe"]["title"] == "NC Wedding Guide"
    # A recipe saved under the folder name still shows the real name.
    (state / "things" / "pages").mkdir(parents=True, exist_ok=True)
    (state / "things" / "pages" / "skill-wedding-skill.json").write_text(json.dumps(
        {"title": "wedding-skill", "icon": "", "line": "A co-pilot.", "try": [], "can": [],
         "made_by": "brain"}), encoding="utf-8")
    page = client.get("/things/skill-wedding-skill", headers=PAIRED).get_json()
    assert page["recipe"]["title"] == page["name"] == "Wedding.skill"
    from puffpai import persona
    assert "Wedding.skill" in persona.things()


def test_the_finds_list_names_a_thing_like_its_tile(state):
    """Any-bot check: "Added from these finds" said the repo name while the
    tile and its page said the name it was given when it was added."""
    from puffpai.finding import install
    entries = [{"entry": "t_cccccccccc", "id": "mcp:example/nc", "name": "nc-wedding-mcp",
                "type": "mcp", "line": "nc-wedding-mcp (MCP tool server)",
                "undo": {"kind": "mcp", "server": "nc_wedding_guide"}}]
    (state / "finding").mkdir()
    (state / "finding" / "installed.json").write_text(json.dumps(entries), encoding="utf-8")
    row = install.installed()[0]
    assert row["name"] == "NC Wedding Guide"
    assert row["line"] == "NC Wedding Guide (MCP tool server)"


def test_the_page_ask_box_keeps_and_sends_its_talk():
    """The thing page's Ask box is a small talk: its last few turns show on
    the page and go with each ask (finding 2 of the PrintingBot check)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "dashboard", "app", "dashboard", "things", "[id]",
                           "page.tsx"), encoding="utf-8") as fh:
        page = fh.read()
    with open(os.path.join(root, "dashboard", "app", "_lib", "services",
                           "chatService.ts"), encoding="utf-8") as fh:
        service = fh.read()
    assert "const KEEP_TURNS = 4;" in page
    assert "history.slice(-KEEP_TURNS)" in page
    assert "{ text, history }" in service
