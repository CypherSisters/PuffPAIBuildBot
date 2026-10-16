"""A pasted key is never logged, sent or saved, whatever shape it arrives in.

Each case below is kept as a regression test:

* a key sent as OpenAI content PARTS (``[{"type": "text", "text": KEY}]``)
  on /v1/chat/completions and /api/chat was printed in the "[Voice API]" log
  line, because the key check only read strings. Every chat route now reads a
  message as text first, and the log line never shows a message that holds a
  key.
* /chat/save checked only the question, not the answer; an assistant turn
  in an OpenAI caller's copy of the chat reached the model unchecked.
* the check missed symbol passwords, keys after 20,000 characters,
  full-width keys and keys typed in groups, and the reply claimed more than
  the check catches.
"""
from __future__ import annotations

import json
import os

import pytest

from puffpai import api_server, plain_words, web_search

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}
KEY = "q8ZrT2vLx9KmW4pNc7Hs1YdQ3"


@pytest.fixture
def spied(monkeypatch, capsys):
    """The real routes; the chat pipeline and the model are spied on."""
    seen: list[str] = []

    def _chat(message, history=None, *a, **k):
        seen.append(json.dumps([message, history, a, k], default=str))
        return {"response": "an answer", "sources": [], "suggestions": []}

    def _voice(message, history=None, *a, **k):
        seen.append(json.dumps([message, history, a, k], default=str))
        return "an answer"

    def _model(*a, **k):
        seen.append(json.dumps([a, k], default=str))
        return "a model answer"
    monkeypatch.setattr(api_server, "_process_chat_query", _chat)
    monkeypatch.setattr(api_server, "_process_voice_query", _voice)
    for name in ("_ollama_chat", "_query_ollama_direct"):
        monkeypatch.setattr(api_server, name, _model)
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client(), seen


def _printed(capsys) -> str:
    out = capsys.readouterr()
    return out.out + out.err


# ── content parts are read as text before anything logs them ────────────

@pytest.mark.parametrize("route", ["/v1/chat/completions", "/api/chat"])
@pytest.mark.parametrize("content", [
    [{"type": "text", "text": KEY}],
    [{"type": "text", "text": "here it is"}, {"type": "text", "text": KEY}],
    {"type": "text", "text": KEY},
])
def test_a_key_sent_as_content_parts_is_never_logged_or_sent(spied, capsys, route, content):
    client, seen = spied
    r = client.post(route, headers=AUTH, json={
        "messages": [{"role": "user", "content": content}], "stream": False})
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    assert KEY not in r.get_data(as_text=True)
    # Answered before the pipeline, or the pipeline got the placeholder.
    body = r.get_data(as_text=True)
    assert ("Sign-up keys page" in body if not seen
            else web_search.KEY_PASTE_PLACEHOLDER in seen[-1])
    printed = _printed(capsys)
    assert KEY not in printed
    assert "looked like a key or a password" in printed  # the placeholder was logged
    assert all(KEY not in s for s in seen)


def test_the_log_preview_never_shows_a_key():
    for message in (KEY, f"here {KEY}", [{"type": "text", "text": KEY}],
                    "Xk9#mP2$vL7@qR4!zT8%"):
        shown = api_server._pii_log_preview(message)
        assert KEY not in shown and "Xk9#" not in shown
        assert "not shown" in shown
    # A password said as one is hidden by the privacy scan's log redaction.
    shown = api_server._pii_log_preview("my password is Tr0ub4dor&3xK!pL9zQ#wV2")
    assert "Tr0ub4dor" not in shown
    assert api_server._pii_log_preview("what is a comet?") == "what is a comet?"
    # A list is read as its text, never printed as a Python list.
    assert api_server._pii_log_preview([{"type": "text", "text": "hi"}]) == "hi"


def test_content_parts_are_read_as_their_text_only():
    assert api_server._content_text([{"type": "text", "text": "a"},
                                     {"type": "image_url", "image_url": {"url": "x"}},
                                     {"type": "input_text", "text": "b"}]) == "a b"
    assert api_server._content_text(None) == ""
    assert api_server._content_text(42) == ""
    assert api_server._content_text({"type": "text", "text": "c"}) == "c"


@pytest.mark.parametrize("route,field", [("/chat/send", "message"),
                                         ("/chat/stream", "message"),
                                         ("/conversation", "text")])
def test_a_key_in_parts_on_the_other_chat_routes_is_swapped_too(spied, capsys, route, field):
    client, seen = spied
    r = client.post(route, headers=AUTH,
                    json={field: [{"type": "text", "text": KEY}]})
    assert r.status_code == 200
    assert KEY not in r.get_data(as_text=True)
    assert KEY not in _printed(capsys)
    assert all(KEY not in s for s in seen)


# ── answers are checked too ────────────────────────────────────────────────

def _saved(conversation_id: str) -> str:
    path = os.path.join(api_server._get_chat_history_dir(), f"{conversation_id}.json")
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_chat_save_never_saves_a_key_in_the_answer(spied):
    client, _ = spied
    cid = "wave3saveanswer"
    r = client.post("/chat/save", headers=AUTH, json={
        "conversation_id": cid, "user_message": "hi",
        "assistant_message": f"your key {KEY}"})
    assert r.status_code == 200
    saved = _saved(cid)
    assert KEY not in saved
    assert web_search.KEY_ANSWER_PLACEHOLDER in saved
    with api_server._chat_history_lock:
        assert KEY not in json.dumps(list(api_server._chat_history))


def test_a_key_in_an_assistant_turn_of_the_callers_history_never_reaches_the_model(spied, capsys):
    client, seen = spied
    r = client.post("/v1/chat/completions", headers=AUTH, json={"messages": [
        {"role": "user", "content": "remember"},
        {"role": "assistant", "content": KEY},
        {"role": "user", "content": "what did you say"}]})
    assert r.status_code == 200
    assert seen and all(KEY not in s for s in seen)
    assert web_search.KEY_ANSWER_PLACEHOLDER in seen[-1]
    assert KEY not in _printed(capsys)


def test_the_conversion_checks_both_turns_and_reads_parts():
    message, history = api_server._convert_openai_messages_to_history([
        {"role": "user", "content": [{"type": "text", "text": f"use {KEY}"}]},
        {"role": "assistant", "content": [{"type": "text", "text": f"ok {KEY}"}]},
        {"role": "user", "content": "what is a comet?"}])
    assert message == "what is a comet?"
    assert history == [{"user": web_search.KEY_PASTE_PLACEHOLDER,
                        "assistant": web_search.KEY_ANSWER_PLACEHOLDER}]


def test_the_saved_chat_scrub_checks_answers_too():
    conv = {"messages": [{"role": "user", "content": "hi"},
                         {"role": "assistant", "content": f"here: {KEY}"}]}
    web_search.history_scrub(conv)
    assert conv["messages"][1]["content"] == web_search.KEY_ANSWER_PLACEHOLDER


# ── what the check catches, and what the reply claims ─────────────────

CAUGHT = {
    "symbol password alone": "Xk9#mP2$vL7@qR4!zT8%",
    "symbol password in a sentence": "use Tr0ub4dor&3xK!pL9zQ#wV2 for it",
    "P@ssw0rd style": "try P@ssw0rd!9 on the router",
    "stripe": "sk_live_51HqLyjWDarjtT1zdp7dcXYZabc",
    "typed in groups": "here it is: AB12 CD34 EF56 GH78 IJ90 KL12 MN34",
    "16 characters in a sentence": "use q8ZrT2vLx9KmW4pN",
    "anthropic": "sk-ant-api03-abcDEF123ghiJKL456mnoPQR789",
    "passcode with no 'is'": "passcode 7731aa",
    "another language": "mot de passe: Soleil!2024",
    "after 20,000 characters": ("hello " * 3400) + " q8ZrT2vLx9KmW4pNc7Hs1Yd",
    "after 200,000 characters": ("hello " * 34000) + " q8ZrT2vLx9KmW4pNc7Hs1Yd",
    "full-width": "ｑ８ＺｒＴ２ｖＬｘ９ＫｍＷ４ｐＮｃ７Ｈｓ",
    "zero-width spaces inside": "q8ZrT2vL\u200bx9KmW4pN\u200bc7Hs1YdQ3",
    "after a colon": "Brave:q8ZrT2vLx9KmW4pNc7Hs1Yd",
    "base64": "dGhpcyBpcyBhIHNlY3JldCBrZXkgdmFsdWU=",
}

#: Said AS a password: the chat privacy scan owns these. It files the password
#: with the owner's private details and hides it in the saved chat and the log,
#: so the message is not dropped as a pasted key (tests/test_privacy_chat_paths
#: keeps that behaviour).
FILED = {
    "symbol password": ("my password is Tr0ub4dor&3xK!pL9zQ#wV2", "Tr0ub4dor&3xK!pL9zQ#wV2"),
    "said as a password": ("the wifi password is correcthorsebatterystaple2024",
                           "correcthorsebatterystaple2024"),
    "pin": ("my pin is 482913", "482913"),
}

LEFT_ALONE = [
    "look at https://github.com/cyphersisters/puffpai/blob/main/README.md",
    "I love my iPhone15-Pro!", "solve x^2+3x=10", "COVID-19 rules changed",
    "my email is bob.smith42@example.com", "meet at 10:30am on 2026-09-24",
    "the password is written on the fridge", "what is my wifi password?",
    "password manager recommendations", "my PIN is 1234",
    "Call Dr. O'Neil at (555) 123-4567 ext. 89", "Order #A1B2-C3D4 shipped",
    "Windows 11 Pro 23H2 build 22631", "use the C++17 standard",
    "PS5 Xbox360 N64 GTA5 games", "version 1.2.3-beta.4+build.5",
    "flight BA2490 seat 14C gate B32 terminal 5", "$19.99/month for Netflix",
    "C:\\Users\\you\\Documents\\report.docx", "The ISBN is 978-3-16-148410-0",
    "MAC address 00:1A:2B:3C:4D:5E", "My license plate is ABC 1234",
    "#ThrowbackThursdayVibes2026", ("hello there friend " * 3000).strip(),
]


@pytest.mark.parametrize("name", sorted(CAUGHT))
def test_the_verifiers_misses_are_caught_now(name):
    assert web_search.looks_like_pasted_key(CAUGHT[name]), name


@pytest.mark.parametrize("name", sorted(FILED))
def test_a_password_said_as_one_is_hidden_by_the_privacy_scan(name):
    from puffpai.security.chat_privacy import protect_text, safe_for_log
    message, secret = FILED[name]
    if web_search.looks_like_pasted_key(message):
        return  # dropped whole: also never kept
    assert secret not in protect_text(message, source="test", vault=None)["redacted_text"]
    assert secret not in safe_for_log(message, 200)


@pytest.mark.parametrize("message", LEFT_ALONE, ids=lambda m: m[:30])
def test_ordinary_messages_are_still_left_alone(message):
    assert not web_search.looks_like_pasted_key(message)


def test_the_placeholders_are_never_read_as_keys():
    for text in (web_search.KEY_PASTE_PLACEHOLDER, web_search.KEY_ANSWER_PLACEHOLDER):
        assert not web_search.looks_like_pasted_key(text)
        assert web_search.pasted_key_turn(text) == web_search.KEY_PASTE_REPLY


def test_the_reply_claims_only_what_is_caught_in_plain_words():
    reply = web_search.KEY_PASTE_REPLY
    assert "looks like a key or a password" in reply   # about THIS message
    assert "cannot spot every one" in reply           # not a promise about all
    assert "Sign-up keys page" in reply and "Please do not paste keys" in reply
    for text in (reply, web_search.KEY_PASTE_PLACEHOLDER,
                 web_search.KEY_ANSWER_PLACEHOLDER):
        assert not plain_words.check(text), text


def test_the_placeholders_survive_the_privacy_scan_unchanged():
    """The scan must not read the placeholder's own words as a password."""
    from puffpai.security.chat_privacy import protect_text
    for text in (web_search.KEY_PASTE_PLACEHOLDER, web_search.KEY_ANSWER_PLACEHOLDER):
        assert protect_text(text, source="test", vault=None)["redacted_text"] == text
