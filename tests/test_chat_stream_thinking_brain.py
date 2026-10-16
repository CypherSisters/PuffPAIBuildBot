"""/chat/stream with a thinking brain (qwen3.5).

The streamed path read only ``message.content`` and sent no ``think``
setting, so a thinking model spent its whole budget thinking and streamed
nothing; the claim guard then turned the empty reply into "I have not
changed anything in your apps." for every question.

Now every native chat call sends ``"think": false``, and an empty reply is
said plainly ("The brain gave no answer. Try again."), never as a change
claim. Ollama is mocked; nothing goes online.
"""
from __future__ import annotations

import json

import requests

from puffpai import api_server
from puffpai.integrate import chat, wording


class _Resp:
    status_code = 200

    def __init__(self, lines=()):
        self._lines = [json.dumps(x).encode() for x in lines]

    def raise_for_status(self):
        return None

    def iter_lines(self):
        return iter(self._lines)

    def json(self):
        return {"message": {"content": "ok"}}


def test_every_native_chat_call_turns_thinking_off(monkeypatch):
    sent = []

    def fake_post(url, json=None, **kw):  # noqa: A002
        sent.append(json)
        return _Resp()

    monkeypatch.setattr(requests, "post", fake_post)
    api_server._ollama_chat([{"role": "user", "content": "hi"}], stream=True)
    api_server._ollama_chat([{"role": "user", "content": "hi"}], num_predict=400)
    assert [p["think"] for p in sent] == [False, False]
    # None leaves the model's own default (the key is not sent at all).
    api_server._ollama_chat([{"role": "user", "content": "hi"}], think=None)
    assert "think" not in sent[-1]


def test_the_streamed_path_asks_with_thinking_off_and_skips_thinking_tokens(monkeypatch):
    seen = {}

    def fake_chat(messages, **kw):
        seen.update(kw)
        return _Resp([{"message": {"role": "assistant", "content": "", "thinking": "hmm"}},
                      {"message": {"role": "assistant", "content": "Paris."}},
                      {"done": True}])

    monkeypatch.setattr(api_server, "_ollama_chat", fake_chat)
    monkeypatch.setattr(api_server, "_grounding_context", lambda *a, **k: ("", []))
    monkeypatch.setattr(api_server, "_maybe_identity_answer", lambda m: None)
    out = list(api_server._stream_ollama_tokens("capital of France?", [], "persona"))
    assert out == ["Paris."]
    assert seen.get("think", False) is False


def test_an_empty_stream_is_said_plainly_never_as_a_change_claim():
    for tokens in ([], ["", ""], ["  ", "\n"]):
        out = "".join(chat.guard_app_claims(iter(tokens), asked="what is the capital of France?"))
        assert out.strip() == wording.NO_BRAIN_ANSWER
        assert wording.NO_APP_CHANGE not in out


def test_the_full_stream_guard_says_the_same_for_an_empty_reply():
    facts = {"names": [], "before": None, "rows": [], "status": "", "honest": ""}
    out = "".join(api_server._guarded_stream(iter([]), [], "hello there", facts))
    assert out == wording.NO_BRAIN_ANSWER


def test_a_real_answer_passes_and_a_false_claim_is_still_replaced():
    ok = "".join(chat.guard_app_claims(iter(["Paris is ", "the capital."]),
                                       asked="what is the capital of France?"))
    assert ok == "Paris is the capital."
    lie = "".join(chat.guard_app_claims(iter(["Leo has been kicked off."]),
                                        asked="kick Leo off the guest list",
                                        names=["guest list"]))
    assert wording.NO_BRAIN_ANSWER not in lie
    assert "have not changed anything" in lie
