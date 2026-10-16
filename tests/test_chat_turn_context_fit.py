"""Chat answers one turn behind.

A turn that reads several skills piles up tool results until the prompt is
longer than the model holds. Ollama then keeps the system messages and drops
the oldest others, so her question went first and the model answered the
earlier request still visible in the system notes. The fake brain here
truncates the same way; nothing reaches Ollama.
"""
from __future__ import annotations

import pytest

from puffpai import brain_loop, brain_tools, brain_tools_builtin, chat_memory

OBJ = {"type": "object", "properties": {}, "additionalProperties": False}
PROMPT = 6000          # tokens the fake model can hold of a prompt


class TruncatingBrain:
    """Ollama's rule: system messages stay, the oldest others drop until it
    fits. Asks for a big skill N times, then answers the newest question it
    can still see (or greets, as a model with no question does)."""

    def __init__(self, reads):
        self.reads = reads
        self.seen: list[list[dict]] = []

    def chat(self, messages, temperature=0.7, max_tokens=2048, tools=None):
        kept = [dict(m) for m in messages]
        while chat_memory.message_tokens(kept) > PROMPT:
            i = next(i for i, m in enumerate(kept) if m["role"] != "system")
            del kept[i]
        self.seen.append(kept)
        if self.reads:
            self.reads -= 1
            return {"content": "", "tool_calls": [
                {"function": {"name": "skill_big", "arguments": {}}}]}
        users = [m["content"] for m in kept if m["role"] == "user"]
        return {"content": f"Answer to: {users[-1]}" if users else "Hi there!",
                "tool_calls": []}


@pytest.fixture(autouse=True)
def big_tool():
    brain_tools.clear()
    brain_tools_builtin.reset_for_tests()
    brain_tools.register("skill_big", "Read a long skill.", OBJ,
                         lambda a, c: "x" * brain_tools.MAX_RESULT_CHARS)
    yield
    brain_tools.clear()
    brain_tools_builtin.reset_for_tests()


def _messages():
    return [{"role": "system", "content": "Rules. " * 600},
            {"role": "user", "content": "give me a piano lesson"},
            {"role": "assistant", "content": "Here is a lesson."},
            {"role": "user", "content": "help me compose a song"}]


def test_without_a_budget_the_question_is_lost():
    """The repro: the old loop sent everything and her question dropped."""
    brain = TruncatingBrain(reads=5)
    got = brain_loop.run(_messages(), ctx={"caller": "owner"}, backend=brain)
    assert "compose a song" not in got["text"]


def test_the_question_survives_a_turn_of_big_tool_results():
    brain = TruncatingBrain(reads=5)
    got = brain_loop.run(_messages(), ctx={"caller": "owner"}, backend=brain,
                         prompt_tokens=PROMPT)
    assert got["text"] == "Answer to: help me compose a song"
    for sent in brain.seen:
        assert chat_memory.message_tokens(sent) <= PROMPT
        assert any(m["content"] == "help me compose a song" for m in sent)
        assert sent[0]["role"] == "system"


def test_streaming_passes_the_budget():
    brain = TruncatingBrain(reads=5)
    events = list(brain_loop.run_streaming(
        _messages(), ctx={"caller": "owner", "device_id": "device:x"},
        backend=brain, prompt_tokens=PROMPT))
    done = [e for e in events if e["type"] == "done"][0]
    assert done["text"] == "Answer to: help me compose a song"


def test_fit_cuts_tool_results_before_older_turns_and_keeps_the_newest():
    chat = _messages() + [
        {"role": "assistant", "content": "", "tool_calls": []},
        {"role": "tool", "content": "a" * 6000},
        {"role": "assistant", "content": "", "tool_calls": []},
        {"role": "tool", "content": "b" * 6000}]
    out = brain_loop.fit_turn(chat, 4000, 3)
    assert chat_memory.message_tokens(out) <= 4000
    assert out[1]["content"] == "give me a piano lesson"       # older turn kept
    assert out[5]["content"].endswith(brain_loop.ROOM_CUT)       # oldest cut
    assert chat[5]["content"] == "a" * 6000                      # input untouched


def test_fit_drops_older_turns_when_cutting_is_not_enough():
    chat = _messages() + [{"role": "tool", "content": "c" * 9000}]
    room = chat_memory.message_tokens(
        [chat[0], chat[3], {"content": "c" * brain_loop.STUB_RESULT_CHARS + brain_loop.ROOM_CUT}])
    out = brain_loop.fit_turn(chat, room, 3)
    assert [m["content"] for m in out if m["role"] == "user"] == ["help me compose a song"]
    assert out[0]["role"] == "system"


def test_fit_leaves_a_short_turn_alone():
    chat = _messages()
    assert brain_loop.fit_turn(chat, 100000, 3) is chat
    assert brain_loop.fit_turn(chat, None, 3) is chat
