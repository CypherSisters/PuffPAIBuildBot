"""OllamaBackend: ``format``, ``tools``, streamed ``on_line`` and prewarm.

No network: ``requests.post`` inside the backend is replaced by a recorder.
"""
from __future__ import annotations

import json

import pytest

from puffpai.inference import ollama_backend as ob


class _Resp:
    def __init__(self, body=None, lines=None, status=200):
        self._body = body or {}
        self._lines = lines or []
        self.status_code = status

    def raise_for_status(self):
        return None

    def json(self):
        return self._body

    def iter_lines(self):
        for line in self._lines:
            yield json.dumps(line).encode("utf-8")


@pytest.fixture
def calls(monkeypatch):
    seen: list[dict] = []
    replies: list[_Resp] = []

    def post(url, json=None, **kw):  # noqa: A002 - requests' own name
        seen.append({"url": url, "json": json, "kw": kw})
        return replies.pop(0)

    monkeypatch.setattr(ob.requests, "post", post)
    return seen, replies


def _backend():
    return ob.OllamaBackend(url="http://127.0.0.1:11435", model="qwen2.5:7b-instruct")


def test_chat_without_format_or_tools_is_unchanged(calls):
    seen, replies = calls
    replies.append(_Resp({"message": {"role": "assistant", "content": "hi"}}))
    out = _backend().chat([{"role": "user", "content": "x"}])
    assert out == "hi"
    payload = seen[0]["json"]
    assert "format" not in payload and "tools" not in payload
    assert set(payload) == {"model", "messages", "stream", "keep_alive", "options"}
    assert payload["stream"] is False


def test_chat_sends_the_schema_as_format(calls):
    seen, replies = calls
    replies.append(_Resp({"message": {"role": "assistant", "content": "{}"}}))
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    assert _backend().chat([], format=schema) == "{}"
    assert seen[0]["json"]["format"] == schema


def test_chat_with_tools_returns_the_whole_message(calls):
    seen, replies = calls
    call = {"function": {"name": "add_guest", "arguments": {"guest_name": "A"}}}
    replies.append(_Resp({"message": {"role": "assistant", "content": "", "tool_calls": [call]}}))
    tools = [{"type": "function", "function": {"name": "add_guest", "parameters": {}}}]
    out = _backend().chat([{"role": "user", "content": "x"}], tools=tools)
    assert seen[0]["json"]["tools"] == tools
    assert out == {"role": "assistant", "content": "", "tool_calls": [call]}


def test_chat_with_empty_tools_still_sends_the_empty_list(calls):
    seen, replies = calls
    replies.append(_Resp({"message": {"role": "assistant", "content": "ok"}}))
    out = _backend().chat([], tools=[])
    assert seen[0]["json"]["tools"] == []
    assert out["tool_calls"] == [] and out["content"] == "ok"


def test_chat_with_tools_reports_errors_as_a_message(monkeypatch):
    def boom(*a, **k):
        raise ob.requests.exceptions.ConnectionError()
    monkeypatch.setattr(ob.requests, "post", boom)
    out = _backend().chat([], tools=[])
    assert out["content"].startswith("[Error]") and out["tool_calls"] == []


def test_chat_stream_format_and_on_line(calls):
    seen, replies = calls
    lines = [{"message": {"content": '{"a":'}}, {"message": {"content": '"b"}'}},
             {"done": True, "eval_count": 10, "eval_duration": 500_000_000}]
    replies.append(_Resp(lines=lines))
    got: list[dict] = []
    schema = {"type": "object"}
    text = "".join(_backend().chat_stream([], format=schema, on_line=got.append))
    assert text == '{"a":"b"}'
    assert seen[0]["json"]["format"] == schema and seen[0]["json"]["stream"] is True
    assert got == lines


def test_chat_stream_without_new_arguments_is_unchanged(calls):
    seen, replies = calls
    replies.append(_Resp(lines=[{"message": {"content": "x"}}, {"done": True}]))
    assert "".join(_backend().chat_stream([])) == "x"
    assert "format" not in seen[0]["json"]


def test_on_line_errors_are_ignored(calls):
    seen, replies = calls
    replies.append(_Resp(lines=[{"message": {"content": "x"}}, {"done": True}]))

    def bad(_chunk):
        raise RuntimeError("no")
    assert "".join(_backend().chat_stream([], on_line=bad)) == "x"


def test_prewarm_is_an_empty_chat_kept_ten_minutes(calls):
    seen, replies = calls
    replies.append(_Resp({}))
    assert _backend().prewarm() is True
    payload = seen[0]["json"]
    assert payload == {"model": "qwen2.5:7b-instruct", "messages": [], "keep_alive": "10m"}


def test_prewarm_never_raises(monkeypatch):
    def boom(*a, **k):
        raise OSError("down")
    monkeypatch.setattr(ob.requests, "post", boom)
    assert _backend().prewarm() is False
