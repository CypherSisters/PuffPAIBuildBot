"""The tool loop on the real chat routes.

The dashboard chat (``/v1/chat/completions`` from a paired device),
``/chat/send`` and the terminal go through puffpai.brain_loop with a fake
model that emits tool calls. Voice, keyless callers, the loop switched off
and a model that cannot call tools keep the parked path.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "integrate"))
import integrate_support as sup  # noqa: E402

from puffpai import api_server, brain_loop, brain_tools, brain_tools_builtin  # noqa: E402
from puffpai.security import consent_tickets  # noqa: E402

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}
DEVICE = "deviceaaaa000001"
PAIRED = {**AUTH, "X-Puff-Device-Id": DEVICE}
SECRET = "Zanzibar Quill"
READ = f"{sup.IID}__read_guests"
REMOVE = f"{sup.IID}__remove_guest"


class FakeBrain:
    """Scripted replies: a string is words, a list is tool calls."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def chat(self, messages, temperature=0.7, max_tokens=2048, tools=None):
        self.calls.append({"messages": [dict(m) for m in messages],
                           "tools": None if tools is None else list(tools)})
        reply = self.replies.pop(0) if self.replies else "All done."
        if isinstance(reply, dict):
            return reply
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
    parked = []
    monkeypatch.setattr(api_server, "_query_ollama_direct",
                        lambda *a, **k: parked.append(a[0]) or (
                            {"response": "Parked answer.", "sources": [], "suggestions": []}
                            if k.get("enriched") else "Parked answer."))
    monkeypatch.setattr(api_server, "_stream_ollama_tokens",
                        lambda *a, **k: parked.append(a[0]) or iter(["Parked answer."]))
    w["parked"] = parked
    w["loop"] = FakeBrain()
    monkeypatch.setattr(api_server, "_loop_backend", lambda limits=None: w["loop"])
    api_server.app.config.update(TESTING=True)
    yield w
    brain_tools.clear()
    brain_tools_builtin.reset_for_tests()


@pytest.fixture
def client():
    return api_server.app.test_client()


def _chunks(body: str) -> list[dict]:
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: {")]


def _content(chunks) -> str:
    return "".join((c["choices"][0]["delta"] or {}).get("content", "") for c in chunks)


def _dashboard(client, text, headers=PAIRED):
    return client.post("/v1/chat/completions", headers=headers, json={
        "model": "puffpai", "stream": True, "messages": [{"role": "user", "content": text}]})


def test_the_dashboard_chat_calls_a_tool_and_answers_from_its_result(world, client):
    world["loop"].replies = [[(READ, {})], f"{SECRET} is coming."]
    chunks = _chunks(_dashboard(client, "who is on the guest list?").get_data(as_text=True))
    assert _content(chunks) == f"{SECRET} is coming."
    progress = [c["puffpai_progress"]["line"] for c in chunks if "puffpai_progress" in c]
    assert progress == ["Using Big Day ..."]
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    assert chunks[-1]["puffpai_meta"]["tools_used"] == [READ]
    # the model saw the rows the tool read
    fed = world["loop"].calls[1]["messages"][-1]
    assert fed["role"] == "tool" and SECRET in fed["content"]
    # the first call carried the tool rules first and the app's actions as tools
    first = world["loop"].calls[0]
    assert first["messages"][0]["content"] == brain_loop.TOOL_NOTE
    names = {t["function"]["name"] for t in first["tools"]}
    assert {READ, REMOVE, "run_command", "search_web"} <= names
    assert world["source"].runs[0][2]["device_id"] == f"device:{DEVICE}"
    assert world["parked"] == []


def test_the_dashboard_asks_before_a_remove_and_waits_for_the_tap(world, client):
    world["loop"].replies = [[(REMOVE, {"guest_name": "Ava Thompson"})], "Removed Ava."]
    resp = _dashboard(client, "remove Ava Thompson from the guest list")
    seen, asked = [], None
    for piece in resp.response:
        text = piece.decode() if isinstance(piece, bytes) else piece
        for chunk in _chunks(text):
            seen.append(chunk)
            if "puffpai_ask" in chunk:
                asked = chunk["puffpai_ask"]
                assert world["source"].runs == []          # nothing before her yes
                other = client.post("/chat/approve",
                                    headers={**AUTH, "X-Puff-Device-Id": "devicebbbb000002"},
                                    json={"id": asked["id"], "yes": True})
                assert other.status_code == 404           # another device cannot answer
                ok = client.post("/chat/approve", headers=PAIRED,
                                 json={"id": asked["id"], "yes": True})
                assert ok.status_code == 200 and ok.get_json()["success"] is True
    assert asked["title"] == "Remove this from Big Day?"
    assert "Ava Thompson" in asked["detail"]
    assert world["source"].runs[-1][0] == REMOVE
    assert _content(seen) == "Removed Ava."
    assert any(c.get("puffpai_answered", {}).get("yes") for c in seen)


def test_a_no_tap_runs_nothing(world, client):
    world["loop"].replies = [[(REMOVE, {"guest_name": "Ava Thompson"})], "Kept Ava."]
    resp = _dashboard(client, "remove Ava Thompson")
    for piece in resp.response:
        text = piece.decode() if isinstance(piece, bytes) else piece
        for chunk in _chunks(text):
            if "puffpai_ask" in chunk:
                client.post("/chat/approve", headers=PAIRED,
                            json={"id": chunk["puffpai_ask"]["id"], "yes": False})
    assert world["source"].runs == []
    assert brain_tools.SAID_NO_LINE in world["loop"].calls[1]["messages"][-1]["content"]


def test_approve_needs_a_paired_device_and_a_waiting_question(world, client):
    assert client.post("/chat/approve", headers=AUTH, json={"id": "x", "yes": True}
                       ).status_code == 403
    assert client.post("/chat/approve", headers=PAIRED, json={"id": "nope", "yes": True}
                       ).status_code == 404
    assert client.post("/chat/approve", json={"id": "x", "yes": True}).status_code == 401


@pytest.mark.parametrize("headers", [{}, {"X-Puff-Device-Id": DEVICE},
                                     {"X-Client": "home-assistant"}])
def test_keyless_callers_keep_the_parked_path(world, client, headers):
    body = _dashboard(client, "who is on the guest list?", headers=headers).get_data(as_text=True)
    assert world["loop"].calls == [] and world["source"].runs == []
    assert SECRET not in body


def test_switching_the_loop_off_keeps_the_parked_path(world, client, monkeypatch):
    monkeypatch.setenv("PUFFPAI_TOOL_LOOP", "off")
    monkeypatch.setattr(api_server, "_detect_route", lambda *a, **k: "rag")
    body = _dashboard(client, "tell me a joke").get_data(as_text=True)
    assert world["loop"].calls == [] and "Parked answer." in body


def test_a_model_that_cannot_call_tools_answers_the_old_way(world, client):
    world["loop"].replies = [{"error": "qwen:0.5b does not support tools"}]
    body = _dashboard(client, "tell me a joke").get_data(as_text=True)
    assert _content(_chunks(body)) == "Parked answer."
    assert api_server._LOOP_UNSUPPORTED == {api_server.OLLAMA_MODEL: True}
    assert api_server._tool_loop_enabled() is False


def test_a_brain_that_is_down_says_so_plainly(world, client):
    world["loop"].replies = [{"error": "Cannot connect to Ollama. Is it running?"}]
    text = _content(_chunks(_dashboard(client, "hello").get_data(as_text=True)))
    assert text == api_server._brain_failure(None)["message"]


@pytest.mark.parametrize("ask", ["how do you keep my things private?",
                                 "what model are you running?"])
def test_the_kept_shortcuts_answer_before_the_loop(world, client, ask):
    body = _dashboard(client, ask).get_data(as_text=True)
    assert world["loop"].calls == [] and _content(_chunks(body))


def test_a_pasted_key_never_reaches_the_loop(world, client):
    body = _dashboard(client, "q8ZrT2vLx9KmW4pNc7Hs1YdQ3").get_data(as_text=True)
    assert world["loop"].calls == []
    assert "q8ZrT2vLx9KmW4pNc7Hs1YdQ3" not in body


def test_send_and_the_terminal_use_the_loop(world, client):
    world["loop"].replies = [[(READ, {})], f"{SECRET} is coming.",
                             [(READ, {})], f"{SECRET} is still coming."]
    got = client.post("/chat/send", headers=PAIRED, json={"message": "who is coming?"})
    assert got.get_json()["response"] == f"{SECRET} is coming."
    term = api_server._process_chat_query("who is coming?", [])
    assert term["response"] == f"{SECRET} is still coming."
    assert term["tools_used"] == [READ]
    assert world["source"].runs[-1][2]["device_id"] == consent_tickets.terminal_device_id()


def test_without_a_page_to_ask_on_a_command_does_not_run(world, monkeypatch):
    monkeypatch.setattr(sys, "stdin", None)
    world["loop"].replies = [[("run_command", {"command": "echo hi"})], "I could not run it."]
    got = api_server._process_chat_query("run echo hi", [])
    assert got["response"] == "I could not run it."
    assert brain_tools.NO_ASK_LINE in world["loop"].calls[1]["messages"][-1]["content"]


def test_her_lists_are_one_tool(world, monkeypatch):
    from puffpai import app_fastpath, app_registry
    monkeypatch.setattr(app_registry, "current_manifests", lambda: [
        {"id": "garden", "name": "Garden", "data": {"default": {"plantings": []}}}])
    said = []
    monkeypatch.setattr(app_fastpath, "handle",
                        lambda sentence, store=None, manifests=None: said.append(sentence)
                        or "Added basil to plantings.")
    world["loop"].replies = [[("my_lists", {"sentence": "add basil to plantings"})], "Done."]
    got = api_server._process_chat_query("I planted basil", [])
    assert got["response"] == "Done." and said == ["add basil to plantings"]
    tools = {t["function"]["name"]: t for t in world["loop"].calls[0]["tools"]}
    assert "plantings" in tools["my_lists"]["function"]["description"]


def test_puffpai_chat_in_the_terminal_goes_through_the_loop(world, monkeypatch, capsys):
    from puffpai import app_fastpath, brain_health, cli, setup_ops
    from puffpai.inference.ollama_backend import OllamaBackend
    monkeypatch.setattr("puffpai.config.get_llm_backend",
                        lambda *a, **k: OllamaBackend(url="http://127.0.0.1:9",
                                                      model="qwen3.5:9b"))
    monkeypatch.setattr(setup_ops, "brain_status",
                        lambda: {"status": brain_health.CONNECTED, "message": ""})
    monkeypatch.setattr(cli, "_maybe_autoindex", lambda *a, **k: None)
    from puffpai import factory_wiring
    monkeypatch.setattr(factory_wiring, "connect", lambda *a, **k: [])  # the test's own app
    grammar = []
    monkeypatch.setattr(app_fastpath, "handle",
                        lambda *a, **k: grammar.append(a[0]) or "Added by the grammar.")
    world["loop"].replies = [[(READ, {})], f"{SECRET} is coming."]
    assert cli.main(["chat", "--once", "who is coming to the wedding?"]) == 0
    out = capsys.readouterr().out
    assert f"{SECRET} is coming." in out
    assert grammar == []          # the list grammar is parked when the loop is on
    assert world["source"].runs[0][0] == READ


def test_chat_follows_the_model_the_wizard_just_picked(monkeypatch):
    import types
    monkeypatch.setattr(api_server, "_models_at",
                        lambda url, timeout=2: ["qwen3.5:9b", "qwen2.5:7b-instruct"])
    monkeypatch.setattr(api_server, "OLLAMA_MODEL", "qwen2.5:7b-instruct")
    monkeypatch.setattr(api_server, "_MODEL_SEEN", {"model": "qwen2.5:7b-instruct",
                                                    "url": api_server.OLLAMA_URL})
    live = types.SimpleNamespace(ollama=types.SimpleNamespace(
        model="qwen3.5:9b", url=api_server.OLLAMA_URL))
    monkeypatch.setattr(api_server, "_get_puffpai_config", lambda: live)
    api_server._follow_config_model()
    assert api_server.OLLAMA_MODEL == "qwen3.5:9b"
    # a value other code set stays until the config itself changes again
    api_server.OLLAMA_MODEL = "other"
    api_server._follow_config_model()
    assert api_server.OLLAMA_MODEL == "other"


def test_answers_from_tools_name_no_library_source(world, client, monkeypatch):
    monkeypatch.setattr(api_server, "_grounding_context",
                        lambda *a, **k: ("TED text", [{"title": "TED ideas"}]))
    world["loop"].replies = [[(READ, {})], "Your guests are listed."]
    r = client.post("/chat/send", headers=PAIRED, json={"message": "who is coming?"})
    body = r.get_json()
    assert body.get("sources") in ([], None), body


# -- the brain at the wrong address ---------------------------------

_SMALL, _BIG = "qwen2.5:7b-instruct", "qwen3.5:9b"
_A, _B = "http://127.0.0.1:11434", "http://127.0.0.1:11435"
_HELD = {_A: [_SMALL], _B: [_BIG, _SMALL]}


def _fake_models(url, timeout=2):
    from puffpai import brain_health
    for where, names in _HELD.items():
        if brain_health._same_server(url, where):
            return list(names)
    return None


def _live(model, url):
    import types
    return types.SimpleNamespace(ollama=types.SimpleNamespace(model=model, url=url))


def test_a_switch_to_an_address_without_the_brain_finds_the_right_one(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setattr(api_server, "_models_at", _fake_models)
    monkeypatch.setattr(api_server, "OLLAMA_MODEL", _SMALL)
    monkeypatch.setattr(api_server, "OLLAMA_URL", _A)
    monkeypatch.setattr(api_server, "_MODEL_SEEN", {"model": _SMALL, "url": _A})
    monkeypatch.setattr(api_server, "_get_puffpai_config", lambda: _live(_BIG, _A))
    api_server._follow_config_model()
    assert (api_server.OLLAMA_MODEL, api_server.OLLAMA_URL) == (_BIG, _B)


def test_a_switch_to_a_brain_no_address_has_keeps_the_working_one(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setattr(api_server, "_models_at", _fake_models)
    monkeypatch.setattr(api_server, "OLLAMA_MODEL", _SMALL)
    monkeypatch.setattr(api_server, "OLLAMA_URL", _A)
    monkeypatch.setattr(api_server, "_MODEL_SEEN", {"model": _SMALL, "url": _A})
    monkeypatch.setattr(api_server, "_get_puffpai_config",
                        lambda: _live("nowhere:70b", _A))
    api_server._follow_config_model()
    assert (api_server.OLLAMA_MODEL, api_server.OLLAMA_URL) == (_SMALL, _A)


def test_a_not_found_reply_moves_to_the_address_with_the_brain_once(world, client,
                                                                    monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setattr(api_server, "_models_at", _fake_models)
    monkeypatch.setattr(api_server, "OLLAMA_MODEL", _BIG)
    monkeypatch.setattr(api_server, "OLLAMA_URL", _A)
    world["loop"].replies = [
        {"content": "", "tool_calls": [],
         "error": f"model '{_BIG}' not found, try pulling it first"},
        [(READ, {})], f"{SECRET} is coming."]
    r = client.post("/chat/send", headers=PAIRED, json={"message": "who is coming?"})
    assert r.status_code == 200
    assert SECRET in json.dumps(r.get_json())
    assert api_server.OLLAMA_URL == _B


def test_the_brain_error_names_the_model_and_the_address(world, client, monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setattr(api_server, "_models_at", lambda url, timeout=2: None)
    monkeypatch.setattr(api_server, "OLLAMA_MODEL", "nowhere:70b")
    monkeypatch.setattr(api_server, "OLLAMA_URL", _A)
    world["loop"].replies = [
        {"content": "", "tool_calls": [],
         "error": "model 'nowhere:70b' not found, try pulling it first"}] * 3
    r = client.post("/chat/send", headers=PAIRED, json={"message": "who is coming?"})
    body = json.dumps(r.get_json())
    assert "nowhere:70b" in body and "11434" in body


# -- the team page sees the chat's tool calls -----------------------

def test_the_chat_agent_card_shows_its_tool_calls(world, client):
    world["loop"].replies = [[(READ, {})], f"{SECRET} is coming."]
    _chunks(_dashboard(client, "who is on the guest list?").get_data(as_text=True))
    body = client.get("/agents/status", headers=AUTH).get_json()
    chat = next(row for row in body["team"] if row["name"] == "chat")
    assert chat["display_name"] == f"{api_server.AI_NAME} (the one you talk to)"
    assert chat["status"] == "completed"
    assert chat["recent"][0]["tool"] == READ
    # the tool name only: never what it was asked or what it gave back
    assert SECRET not in json.dumps(body)
