"""Who app actions run for (M1, invariant 4).

Keyless callers of ``/v1/chat/completions`` (Home Assistant and any other
client) and every caller of ``/api/chat`` get no identity, whatever they
send: no action runs, no app data is read, the reply holds none of it. A
paired dashboard device and the terminal do get one (the positive controls,
so these tests cannot pass by the step being switched off).
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "integrate"))
import integrate_support as sup  # noqa: E402

from puffpai import api_server  # noqa: E402
from puffpai.security import consent_tickets  # noqa: E402

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}
DEVICE = "deviceaaaa000001"
SECRET = "Zanzibar Quill"
ASK = "who is on the guest list in Big Day?"
LOOPBACK = {"REMOTE_ADDR": "127.0.0.1"}


@pytest.fixture
def world(monkeypatch, tmp_path):
    brain = sup.Brain(*[(f"{sup.IID}__read_guests", {})] * 6)
    w = sup.setup_chat(monkeypatch, tmp_path, brain=brain)
    monkeypatch.setattr(api_server, "_query_ollama_direct",
                        lambda *a, **k: ({"response": "Ordinary chat.", "sources": [],
                                          "suggestions": []} if k.get("enriched")
                                         else "Ordinary chat."))
    monkeypatch.setattr(api_server, "_stream_ollama_tokens",
                        lambda *a, **k: iter(["Ordinary chat."]))
    api_server.app.config.update(TESTING=True)
    return w


@pytest.fixture
def client():
    return api_server.app.test_client()


def _text(resp) -> str:
    raw = resp.get_data(as_text=True)
    return raw


def _nothing_ran(world, resp):
    assert world["source"].runs == []
    assert world["brain"].calls == []
    assert SECRET not in _text(resp)


@pytest.mark.parametrize("headers", [
    {},                                                      # nothing at all
    {"X-Puff-Device-Id": DEVICE},                            # a device, no key
    {"X-Client": "home-assistant"},
])
@pytest.mark.parametrize("stream", [False, True])
def test_keyless_openai_route_runs_nothing(world, client, headers, stream):
    resp = client.post("/v1/chat/completions", headers=headers,
                       json={"model": "puffpai", "stream": stream,
                             "messages": [{"role": "user", "content": ASK}]})
    assert resp.status_code == 200
    _nothing_ran(world, resp)


@pytest.mark.parametrize("headers", [
    {}, {"X-Puff-Device-Id": DEVICE}, {**AUTH, "X-Puff-Device-Id": DEVICE}, AUTH])
def test_the_ollama_route_runs_nothing_whatever_it_sends(world, client, headers):
    resp = client.post("/api/chat", headers=headers,
                       json={"messages": [{"role": "user", "content": ASK}]})
    assert resp.status_code == 200
    _nothing_ran(world, resp)


def test_a_keyed_caller_without_a_device_runs_nothing(world, client):
    resp = client.post("/chat/send", headers=AUTH, json={"message": ASK})
    _nothing_ran(world, resp)
    resp = client.post("/v1/chat/completions", headers=AUTH,
                       json={"model": "puffpai", "stream": True,
                             "messages": [{"role": "user", "content": ASK}]})
    _nothing_ran(world, resp)


def test_a_lan_caller_naming_a_device_runs_nothing(world, client):
    resp = client.post("/chat/send", headers={**AUTH, "X-Puff-Device-Id": DEVICE},
                       json={"message": ASK},
                       environ_base={"REMOTE_ADDR": "192.168.1.77"})
    _nothing_ran(world, resp)


def test_positive_control_a_paired_dashboard_device(world, client):
    paired = {**AUTH, "X-Puff-Device-Id": DEVICE}
    resp = client.post("/chat/send", headers=paired, json={"message": ASK})
    assert SECRET in resp.get_json()["response"]
    resp = client.post("/v1/chat/completions", headers=paired,
                       json={"model": "puffpai", "stream": True,
                             "messages": [{"role": "user", "content": ASK}]})
    body = _text(resp)
    assert SECRET in body
    chunks = [json.loads(line[6:]) for line in body.splitlines()
              if line.startswith("data: {")]
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    resp = client.post("/chat/stream", headers=paired, json={"message": ASK})
    assert SECRET in _text(resp)
    assert len(world["source"].runs) == 3
    assert {r[2]["device_id"] for r in world["source"].runs} == {f"device:{DEVICE}"}


def test_positive_control_the_terminal(world):
    got = api_server._process_chat_query(ASK, [])
    assert SECRET in got["response"]
    assert world["source"].runs[0][2]["device_id"] == consent_tickets.terminal_device_id()


def test_the_identity_rules():
    app = api_server.app
    assert api_server._actions_identity(surface="voice") is None
    assert api_server._actions_identity() == consent_tickets.terminal_device_id()
    for path in ("/api/chat", "/voice/chat", "/anything"):
        with app.test_request_context(path, method="POST", environ_base=LOOPBACK,
                                      headers={**AUTH, "X-Puff-Device-Id": DEVICE}):
            app.preprocess_request()
            assert api_server._actions_identity() is None, path
    for path in ("/chat/send", "/chat/stream", "/v1/chat/completions"):
        with app.test_request_context(path, method="POST", environ_base=LOOPBACK,
                                      headers={**AUTH, "X-Puff-Device-Id": DEVICE}):
            app.preprocess_request()
            assert api_server._actions_identity() == f"device:{DEVICE}", path
        with app.test_request_context(path, method="POST", environ_base=LOOPBACK,
                                      headers={"X-Puff-Device-Id": DEVICE}):
            app.preprocess_request()  # keyless: 401 for /chat/*, public for /v1
            assert api_server._actions_identity() is None, path
        with app.test_request_context(path, method="POST", environ_base=LOOPBACK,
                                      headers={**AUTH, "X-Puff-Device-Id": DEVICE,
                                               "X-Puff-Anonymous": "1"}):
            app.preprocess_request()
            assert api_server._actions_identity() is None, path


# -- no key: no app data reaches the model either ------
#
# With collect_apps stubbed to a guest list, asking the real model
# "who is on my guest list so far?" with no key: every keyless route named the
# guests, because the system prompt carried every list. The prompt is now built
# per caller class: no key, no lists; the voice key and the owner's key, lists.

READ_ASK = "who is on my guest list so far?"


@pytest.fixture
def prompts(monkeypatch, tmp_path):
    from puffpai import build_context
    sup.setup_chat(monkeypatch, tmp_path, brain=sup.Brain())
    class _Seen(list):
        reads: list = []

    seen = _Seen()
    seen.reads = []

    def collect(app_data=None):
        seen.reads.append(1)
        return [{"id": "bigday", "name": "Big Day",
                 "lists": {"Guest list": ["Leo Park", SECRET]}}]

    monkeypatch.setattr(build_context, "collect_apps", collect)

    def direct(user_message, history, persona, enriched=False):
        seen.append(persona)
        return {"response": "Ordinary chat.", "sources": [], "suggestions": []} \
            if enriched else "Ordinary chat."

    def stream(user_message, history, persona, meta_out=None, keyless=True):
        seen.append(persona)
        return iter(["Ordinary chat."])

    monkeypatch.setattr(api_server, "_query_ollama_direct", direct)
    monkeypatch.setattr(api_server, "_stream_ollama_tokens", stream)
    monkeypatch.setattr(api_server, "_detect_route", lambda *a, **k: "rag")
    api_server.app.config.update(TESTING=True)
    return seen


@pytest.mark.parametrize("path,stream", [("/api/chat", False), ("/api/chat", True),
                                         ("/v1/chat/completions", False),
                                         ("/v1/chat/completions", True)])
@pytest.mark.parametrize("headers", [{}, {"X-Puff-Device-Id": DEVICE},
                                     {"X-Client": "home-assistant"}])
def test_no_key_no_lists_in_the_prompt(prompts, client, path, stream, headers):
    resp = client.post(path, headers=headers,
                       json={"model": "puffpai", "stream": stream,
                             "messages": [{"role": "user", "content": READ_ASK}]})
    assert resp.status_code == 200
    assert prompts, "the model was asked"
    assert all(SECRET not in p and "Leo Park" not in p for p in prompts), path
    assert SECRET not in resp.get_data(as_text=True)
    # Not even read: the prompt, the list names for the claim guard (also
    # inside a streamed response, after the request is gone) and the list
    # suggestions all skip her lists for a caller with no key.
    assert prompts.reads == [], path


def test_positive_control_the_owner_key_gets_the_lists(prompts, client):
    client.post("/chat/send", headers=AUTH, json={"message": READ_ASK})
    assert prompts and SECRET in prompts[-1]
    assert SECRET in api_server._system_prompt()           # the terminal


def test_the_voice_key_gets_the_lists(prompts, client):
    from puffpai.integrate import voice_key
    voice_key.forget_cache()
    key = voice_key.new()
    try:
        client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {key}"},
                    json={"model": "x", "messages": [
                        {"role": "user", "content": "what is a good first dance song?"}]})
        assert prompts and SECRET in prompts[-1]
    finally:
        voice_key.forget_cache()


def test_no_key_no_list_names_either(prompts):
    app = api_server.app
    with app.test_request_context("/api/chat", method="POST", environ_base=LOOPBACK):
        app.preprocess_request()
        assert api_server._owner_lists() == {}
        assert api_server._owner_thing_names() == []
    with app.test_request_context("/chat/send", method="POST", headers=AUTH,
                                  environ_base=LOOPBACK):
        app.preprocess_request()
        assert "Guest list" in api_server._owner_lists()
        assert "Big Day" in api_server._owner_thing_names()


def test_no_key_cannot_read_a_saved_chat(prompts, client, monkeypatch):
    seen = []
    monkeypatch.setattr(api_server, "_stored_history",
                        lambda cid: seen.append(cid) or [{"user": "u", "assistant": SECRET}])
    client.post("/v1/chat/completions", json={"model": "x", "conversation_id": "abc",
                                              "messages": [{"role": "user", "content": "hi"}]})
    assert seen == []
    client.post("/v1/chat/completions", headers=AUTH,
                json={"model": "x", "conversation_id": "abc",
                      "messages": [{"role": "user", "content": "hi"}]})
    assert seen == ["abc"]
