"""A tool call that did not work says why on its activity line (a run_command and a schedule_routine error were logged
with no reason). Secrets are hidden; arguments are never logged."""
from __future__ import annotations

import json

import pytest

from puffpai import agent_activity, brain_commands, brain_loop, brain_tools, brain_tools_builtin

OBJ = {"type": "object", "properties": {}, "additionalProperties": False}
SECRET = "sk-test-1234567890abcdef"


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    brain_tools.clear()
    brain_tools_builtin.reset_for_tests()
    where = str(tmp_path / "activity.jsonl")
    agent_activity.use_path(lambda: where)
    monkeypatch.setattr(brain_commands, "_known_secrets", lambda: [SECRET])
    yield
    agent_activity.use_path(None)
    brain_tools.clear()
    brain_tools_builtin.reset_for_tests()


def _rows():
    with open(agent_activity.path(), encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


class Brain:
    def __init__(self, *replies):
        self.replies = list(replies)

    def chat(self, messages, temperature=0.7, max_tokens=2048, tools=None):
        reply = self.replies.pop(0) if self.replies else "Done."
        if isinstance(reply, list):
            return {"content": "", "tool_calls": [
                {"function": {"name": n, "arguments": a}} for n, a in reply]}
        return {"content": reply, "tool_calls": []}


def _raise(msg):
    def fn(_a, _c):
        raise ValueError(msg)
    return fn


def test_an_error_keeps_its_reason_with_secrets_hidden():
    brain_tools.register("boom", "Fails.", OBJ, _raise(f"there is no time, like 08:00 ({SECRET})"))
    brain_loop.run([{"role": "user", "content": "go"}], ctx={"caller": "owner"},
                   backend=Brain([("boom", {})]))
    row = _rows()[-1]
    assert row["status"] == "error" and row["tool"] == "boom"
    assert "there is no time, like 08:00" in row["reason"]
    assert SECRET not in json.dumps(row)
    assert brain_commands.HIDDEN in row["reason"]


def test_a_failed_result_keeps_its_reason():
    brain_tools.register("cmd", "Runs.", OBJ, lambda a, c: {
        "worked": False, "exit_code": 127, "output": "bash: songsee: command not found",
        "note": "The command did not work (exit code 127)."})
    brain_loop.run([{"role": "user", "content": "go"}], ctx={"caller": "owner"},
                   backend=Brain([("cmd", {})]))
    row = _rows()[-1]
    assert "command not found" in row["reason"]


def test_a_tool_that_worked_has_no_reason():
    brain_tools.register("fine", "Works.", OBJ, lambda a, c: "ok")
    brain_loop.run([{"role": "user", "content": "go"}], ctx={"caller": "owner"},
                   backend=Brain([("fine", {})]))
    row = _rows()[-1]
    assert row["status"] == "completed" and "reason" not in row


def test_the_team_card_rows_carry_the_reason():
    agent_activity.record("chat", "Used boom", status="error", tool="boom",
                          reason="no skill called x is installed")
    got = agent_activity.recent(_rows(), "chat")
    assert got[0]["reason"] == "no skill called x is installed"
