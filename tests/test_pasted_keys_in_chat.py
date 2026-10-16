"""A key pasted into chat is never sent to the model and never saved.

The check needed one of the words api, key, token or
subscription next to a long token with digits in it. A bare pasted Brave key,
other wording ("here you go"), another language ("voici ma cle"), "password"
or a letters-only key went to the model and into saved chats. Now any long
random-looking token counts.
"""
from __future__ import annotations

import json
import os
import random
import string

import pytest

from puffpai import api_server, plain_words, web_search

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}

#: Examples that slipped past the check.
VERIFIER = [
    "BSAq1w2e3r4t5y6u7i8o9p0asdfghj",
    "here you go: BSAq1w2e3r4t5y6u7i8o9p0asdfghj",
    "voici ma clé BSAq1w2e3r4t5y6u7i8o9p0asdfghj",
    "password BSAq1w2e3r4t5y6u7i8o9p0asdfghj",
    "api key: BSAqwertyuiopasdfghjklzxcvbnm",
]

#: Ordinary messages with long words, names, addresses and numbers in them.
ORDINARY = [
    "Supercalifragilisticexpialidocious is a long word",
    "what is pneumonoultramicroscopicsilicovolcanoconiosis",
    "Rindfleischetikettierungsueberwachungsaufgabenuebertragungsgesetz",
    "what does getElementsByClassName do",
    "iPhone15ProMaxAccessories",
    "#ThrowbackThursdayVibes2026",
    "IMG_20260924_123456.jpg",
    "read https://en.wikipedia.org/wiki/Tyrannosaurus_rex please",
    "https://en.wikipedia.org/wiki/List_of_dinosaur_genera_of_the_Cretaceous",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "state-of-the-art-telescopes",
    "Llanfairpwllgwyngyllgogerychwyrndrobwllllantysiliogogogoch",
    "what maintenance does my 2014 Honda Civic need at 100000 miles?",
    "qwen2.5:7b-instruct is the brain",
    "PuffPAIBuildBotFactory",
    "StargazerBotAstronomyQuestions",
    "call me at 555-123-4567",
    "my order number is 1234567890123456",
    "C:/Users/you/Documents/garden_plans_2026_final_version.docx",
    "add books about dinosaurs and pterosaurs to my bot please",
    "ChatGPTvsClaudeComparison2026",
    "MississippiRiverSteamboatHistory",
]


def _keys(rng: random.Random) -> dict[str, str]:
    alnum = string.ascii_letters + string.digits

    def r(n, alphabet=alnum):
        return "".join(rng.choice(alphabet) for _ in range(n))
    return {
        "brave": "BSA" + r(27), "openai": "sk-proj-" + r(48),
        "github": "ghp_" + r(36), "google": "AIza" + r(35),
        "aws_secret": r(40, alnum + "+/"), "hex": "".join(rng.choice("0123456789abcdef") for _ in range(40)),
        "slack": "xoxb-" + r(11, string.digits) + "-" + r(24),
        "random32": r(32), "jwt": "eyJhbGciOiJIUzI1NiJ9." + r(30) + "." + r(43),
    }


@pytest.mark.parametrize("message", VERIFIER)
def test_the_verifiers_pastes_are_caught(message):
    assert web_search.looks_like_pasted_key(message)


def test_a_bare_key_of_every_common_shape_is_caught_in_any_wording():
    rng = random.Random(20260924)
    wordings = ["{}", "here you go: {}", "voici ma clé {}", "mi clave es {}",
                "the thing from the website is {} ok?", "{} <- that one"]
    missed = []
    for _ in range(60):
        for name, key in _keys(rng).items():
            for wording in wordings:
                if not web_search.looks_like_pasted_key(wording.format(key)):
                    missed.append((name, wording))
    # A handful of random draws can read like words; all but a few percent of
    # every shape must be caught, and the well-known shapes all of them.
    assert len(missed) <= 0.03 * 60 * 9 * len(wordings), missed[:10]
    assert not [m for m in missed if m[0] in ("brave", "openai", "github",
                                              "google", "slack", "jwt", "hex")]


@pytest.mark.parametrize("message", ORDINARY)
def test_ordinary_messages_are_left_alone(message):
    assert not web_search.looks_like_pasted_key(message)


def test_the_reply_is_plain_and_names_the_sign_up_keys_page():
    assert "Sign-up keys page" in web_search.KEY_PASTE_REPLY
    assert not plain_words.check(web_search.KEY_PASTE_REPLY)
    assert not plain_words.check(web_search.KEY_PASTE_PLACEHOLDER)


@pytest.fixture
def chat(monkeypatch):
    """The real chat routes, with the model and the privacy scan spied on."""
    seen = {"model": [], "scan": []}

    def _model(*a, **k):
        seen["model"].append(json.dumps([a, k], default=str))
        return "a model answer"
    real_scan = api_server._scan_and_redact_message

    def _scan(text, *a, **k):
        seen["scan"].append(text)
        return real_scan(text, *a, **k)
    for name in ("_ollama_chat", "_query_ollama_direct"):
        monkeypatch.setattr(api_server, name, _model)
    monkeypatch.setattr(api_server, "_stream_ollama_tokens",
                        lambda *a, **k: iter([_model(*a, **k)]))
    monkeypatch.setattr(api_server, "_scan_and_redact_message", _scan)
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client(), seen


def _saved(conversation_id: str) -> str:
    path = os.path.join(api_server._get_chat_history_dir(), f"{conversation_id}.json")
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _memory() -> str:
    with api_server._chat_history_lock:
        return json.dumps(list(api_server._chat_history))


@pytest.mark.parametrize("route", ["/chat/send", "/chat/stream"])
def test_a_bare_key_in_chat_is_not_sent_saved_or_scanned(chat, route):
    client, seen = chat
    key = "BSA" + "".join(random.Random(7).choice(string.ascii_letters + string.digits)
                          for _ in range(27))
    cid = f"pk{route.strip('/').replace('/', '')}"
    r = client.post(route, headers=AUTH,
                    json={"message": f"voici ma clé {key}", "conversation_id": cid})
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Sign-up keys page" in body and key not in body
    assert seen["model"] == []
    assert all(key not in t for t in seen["scan"])
    assert key not in _saved(cid) and key not in _memory()
    assert web_search.KEY_PASTE_PLACEHOLDER in _saved(cid)


def test_the_openai_route_never_sends_a_key_or_logs_it(chat, capsys):
    client, seen = chat
    key = "sk-proj-" + "Qz8vN3xT1pL6wR9mK2cY5hB7dF4gJ0sA" * 1
    r = client.post("/v1/chat/completions", headers=AUTH, json={
        "model": "puffpai",
        "messages": [{"role": "user", "content": f"use this {key}"}]})
    assert r.status_code == 200
    assert "Sign-up keys page" in r.get_data(as_text=True)
    assert seen["model"] == []
    out = capsys.readouterr()
    assert key not in out.out and key not in out.err


def test_a_key_left_in_earlier_turns_is_not_sent_to_the_model():
    history = api_server._convert_openai_messages_to_history([
        {"role": "user", "content": "here you go: BSAq1w2e3r4t5y6u7i8o9p0asdfghj"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "what is a comet?"}])[1]
    assert history[0]["user"] == web_search.KEY_PASTE_PLACEHOLDER


def test_the_saved_chat_scrub_catches_a_bare_key_too():
    conv = {"messages": [{"role": "user", "content": "BSAq1w2e3r4t5y6u7i8o9p0asdfghj"},
                         {"role": "assistant", "content": "hi"}]}
    web_search.history_scrub(conv)
    assert conv["messages"][0]["content"] == web_search.KEY_PASTE_PLACEHOLDER


def test_the_terminal_chat_answers_a_key_without_the_model(monkeypatch, capsys):
    from puffpai import cli
    monkeypatch.setattr(api_server, "_process_chat_query",
                        lambda *a, **k: pytest.fail("the chat pipeline was called"))
    assert cli.main(["chat", "--once", "voici ma clé BSAq1w2e3r4t5y6u7i8o9p0asdfghj"]) == 0
    out = capsys.readouterr().out
    assert "Sign-up keys page" in out and "BSAq1w2e3r4t5" not in out
