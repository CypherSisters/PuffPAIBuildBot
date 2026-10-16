"""The tool loop in chat: the registry, the loop, the yes questions.

Every model here is a fake that emits scripted tool calls; nothing reaches
Ollama or the network.
"""
from __future__ import annotations

import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "integrate"))
import integrate_support as sup  # noqa: E402

from puffpai import brain_commands, brain_loop, brain_tools, brain_tools_builtin  # noqa: E402

DEV = "device:deviceaaaa000001"
OBJ = {"type": "object", "properties": {}, "additionalProperties": False}


class FakeBrain:
    """Scripted replies: a string is words, a list is tool calls
    ``[(name, args), ...]``. Records every call it gets."""

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


class StreamingBrain(FakeBrain):
    """The same, through ``chat_turn`` (word by word, with thinking)."""

    def chat_turn(self, messages, tools=None, on_token=None, on_thinking=None):
        out = self.chat(messages, tools=tools)
        if on_thinking:
            on_thinking("hmm")
        for word in (out.get("content") or "").split(" "):
            if word and on_token:
                on_token(word + " ")
        return out


@pytest.fixture(autouse=True)
def clean_registry():
    brain_tools.clear()
    brain_tools_builtin.reset_for_tests()
    yield
    brain_tools.clear()
    brain_tools_builtin.reset_for_tests()


def _q(text):
    return [{"role": "system", "content": "You are a helper."},
            {"role": "user", "content": text}]


# -- the registry -----------------------------------------------------------------

def test_register_and_available_with_when_and_providers():
    brain_tools.register("echo", "Say it back.", {"type": "object", "properties": {
        "text": {"type": "string"}}, "required": ["text"]}, lambda a, c: a["text"])
    brain_tools.register("owner_only", "Only the owner.", OBJ, lambda a, c: 1,
                         when=lambda c: c.get("caller") == "owner")
    brain_tools.register_provider("more", lambda c: [
        brain_tools.make("from_provider", "Comes and goes.", OBJ, lambda a, c: 2)])
    names = [t.name for t in brain_tools.available({"caller": "owner"})]
    assert names == ["echo", "owner_only", "from_provider"]
    assert [t.name for t in brain_tools.available({})] == ["echo", "from_provider"]
    spec = brain_tools.specs(brain_tools.available({}))[0]
    assert spec == {"type": "function", "function": {
        "name": "echo", "description": "Say it back.",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}},
                       "required": ["text"]}}}


def test_a_broken_provider_never_sinks_the_list():
    brain_tools.register("echo", "Say it back.", OBJ, lambda a, c: "x")
    brain_tools.register_provider("broken", lambda c: 1 / 0)
    assert [t.name for t in brain_tools.available({})] == ["echo"]


@pytest.mark.parametrize("name, schema", [
    ("bad name!", OBJ), ("", OBJ), ("ok", {"type": "array"}),
    ("ok", {"type": "object", "properties": {}, "required": ["x"]}),
])
def test_bad_tools_are_refused(name, schema):
    with pytest.raises(ValueError):
        brain_tools.register(name, "A tool.", schema, lambda a, c: 1)


def test_call_checks_arguments_and_reports_plainly():
    brain_tools.register("echo", "Say it back.", {"type": "object", "properties": {
        "text": {"type": "string"}}, "required": ["text"],
        "additionalProperties": False}, lambda a, c: a["text"])
    assert brain_tools.call("echo", {"text": "hi"}, {}) == {
        "ok": True, "result": "hi", "asked": False}
    assert brain_tools.call("echo", '{"text": "json"}', {})["result"] == "json"
    assert "missing argument" in brain_tools.call("echo", {}, {})["error"]
    assert "unknown argument" in brain_tools.call("echo", {"text": "a", "x": 1}, {})["error"]
    assert "should be a string" in brain_tools.call("echo", {"text": 3}, {})["error"]
    assert "no tool called nope" in brain_tools.call("nope", {}, {})["error"]


def test_a_failing_tool_becomes_a_plain_error():
    def boom(a, c):
        raise RuntimeError("the printer is off")
    brain_tools.register("print_it", "Print.", OBJ, boom)
    got = brain_tools.call("print_it", {}, {})
    assert got["ok"] is False and "the printer is off" in got["error"]


def test_needs_yes_asks_first_and_never_runs_without_it():
    ran = []
    brain_tools.register("wipe", "Wipe.", OBJ, lambda a, c: ran.append(c.get("said_yes")) or "ok",
                         needs_yes=True)
    got = brain_tools.call("wipe", {}, {})                       # nobody to ask
    assert got["ok"] is False and got["error"] == brain_tools.NO_ASK_LINE and ran == []
    asked = []
    got = brain_tools.call("wipe", {}, {"ask": lambda q: asked.append(q) or False})
    assert got == {"ok": False, "error": brain_tools.SAID_NO_LINE, "asked": True}
    assert ran == [] and asked[0]["title"] == "May I use wipe?"
    got = brain_tools.call("wipe", {}, {"ask": lambda q: True})
    assert got["ok"] is True and ran == [True]


def test_a_long_result_is_cut_for_the_model():
    text = brain_tools.result_text("x" * (brain_tools.MAX_RESULT_CHARS + 500))
    assert len(text) < brain_tools.MAX_RESULT_CHARS + 40 and text.endswith("(cut short)")
    assert brain_tools.result_text({"a": 1}) == '{"a": 1}'


# -- the loop ---------------------------------------------------------------------

def test_no_tool_call_is_a_plain_answer():
    brain = FakeBrain("Hello there.")
    got = brain_loop.run(_q("hi"), ctx={}, backend=brain, tools=[])
    assert got == {"text": "Hello there.", "steps": 1, "used": []}
    assert brain.calls[0]["tools"] == []   # tool-shaped call even with no tools


def test_a_line_said_with_a_tool_call_and_again_in_the_answer_is_kept_once():
    """The Calendar offer showed twice."""
    brain_tools.register("list_builtin_apps", "List apps.", OBJ, lambda a, c: ["Calendar"])
    ask = "I already have a Calendar app built into this bot. Want it on your home page?"
    brain = FakeBrain({"content": ask, "tool_calls": [
        {"function": {"name": "list_builtin_apps", "arguments": {}}}]},
        ask + " It shows your events and to-dos.")
    got = brain_loop.run(_q("add a calendar view"), ctx={}, backend=brain)
    assert got["text"] == ask + " It shows your events and to-dos."


def test_the_model_sees_the_result_and_goes_on():
    seen = []
    brain_tools.register("count_guests", "How many guests.", OBJ,
                         lambda a, c: seen.append(1) or {"guests": 42})
    brain_tools.register("add_note", "Write a note.", {"type": "object", "properties": {
        "text": {"type": "string"}}, "required": ["text"]}, lambda a, c: "saved")
    brain = FakeBrain([("count_guests", {})], [("add_note", {"text": "42 guests"})],
                      "You have 42 guests, and I wrote that down.")
    events = []
    got = brain_loop.run(_q("how many guests? note it"), ctx={}, backend=brain,
                         emit=events.append)
    assert got["text"] == "You have 42 guests, and I wrote that down."
    assert got["used"] == ["count_guests", "add_note"] and got["steps"] == 3
    second = brain.calls[1]["messages"]
    assert second[-2]["role"] == "assistant" and second[-2]["tool_calls"]
    assert second[-1] == {"role": "tool", "content": '{"guests": 42}',
                          "tool_name": "count_guests"}
    assert brain.calls[2]["messages"][-1]["content"] == "saved"
    lines = [e["line"] for e in events if e["type"] == "progress"]
    assert lines == ["Using count guests ...", "Using add note ..."]
    assert [e["text"] for e in events if e["type"] == "token"] == [got["text"]]


def test_a_wrong_tool_is_told_to_the_model_which_can_recover():
    brain_tools.register("echo", "Say it back.", OBJ, lambda a, c: "ok")
    brain = FakeBrain([("made_up_tool", {})], [("echo", {})], "Fixed it.")
    got = brain_loop.run(_q("x"), ctx={}, backend=brain)
    assert got["text"] == "Fixed it."
    assert "no tool called made_up_tool" in brain.calls[1]["messages"][-1]["content"]


def test_the_step_limit_ends_with_one_call_without_tools():
    brain_tools.register("again", "Again.", OBJ, lambda a, c: "more")
    brain = FakeBrain(*([[("again", {})]] * 3), "Here is what I found so far.")
    got = brain_loop.run(_q("loop forever"), ctx={}, backend=brain, max_steps=3)
    assert got["text"] == "Here is what I found so far."
    assert len(brain.calls) == 4 and brain.calls[-1]["tools"] == []
    assert brain.calls[-1]["messages"][-1]["content"] == brain_loop.OUT_OF_STEPS_NOTE
    assert brain_loop.MAX_STEPS == 8


def test_a_model_that_never_stops_calling_tools_gets_a_plain_line():
    brain_tools.register("again", "Again.", OBJ, lambda a, c: "more")
    brain = FakeBrain(*([[("again", {})]] * 10))
    got = brain_loop.run(_q("loop forever"), ctx={}, backend=brain, max_steps=2)
    assert got["text"] == brain_loop.EMPTY_ANSWER_LINE


def test_an_empty_answer_after_tools_is_asked_for_once_more():
    brain_tools.register("seat", "Seat guests.", OBJ, lambda a, c: {"link": "https://x/1"})
    brain = FakeBrain({"content": "Let me start:", "tool_calls": [
        {"function": {"name": "seat", "arguments": {}}}]}, "", "Done: https://x/1")
    got = brain_loop.run(_q("seat them"), ctx={}, backend=brain)
    assert got["text"] == "Let me start:\n\nDone: https://x/1"
    last = brain.calls[-1]["messages"]
    assert last[-2] == {"role": "assistant", "content": "(empty)"}
    assert last[-1] == {"role": "user", "content": brain_loop.AFTER_TOOLS_NOTE}
    # twice empty: a plain line, never an empty bubble
    brain = FakeBrain([("seat", {})], "", "")
    got = brain_loop.run(_q("seat them"), ctx={}, backend=brain)
    assert got["text"] == brain_loop.EMPTY_ANSWER_LINE


def test_after_her_no_nothing_more_is_asked_in_that_message():
    asked = []
    brain_tools.register("risky", "Risky.", OBJ, lambda a, c: "done",
                         needs_yes=lambda a, c: {"title": "May I?", "command": "x"})
    brain = FakeBrain([("risky", {})], [("risky", {})], "Okay, nothing was run.")
    got = brain_loop.run(_q("go"), ctx={"ask": lambda q: asked.append(q) or False},
                         backend=brain)
    assert len(asked) == 1 and got["text"] == "Okay, nothing was run."
    assert brain_loop.STOP_ASKING_LINE in brain.calls[2]["messages"][-1]["content"]


def test_tool_results_are_kept_for_the_next_message_of_that_chat():
    brain_tools.register("make_project", "Make it.", OBJ, lambda a, c: {"project": "p-77"})
    brain = FakeBrain([("make_project", {})], "Made p-77.")
    brain_loop.run(_q("make it"), ctx={"conversation_id": "conv-a"}, backend=brain)
    note = brain_loop.earlier_results_note("conv-a")
    assert note.startswith(brain_loop.EARLIER_HEAD) and "p-77" in note
    assert brain_loop.earlier_results_note("conv-b") == ""


def test_long_dashes_never_reach_her():
    events = []
    got = brain_loop.run(_q("x"), ctx={}, backend=StreamingBrain(
        "Bambu PLA \u2014 Black, 10\u201312 days"), tools=[], emit=events.append)
    assert "\u2014" not in got["text"] and "\u2013" not in got["text"]
    assert got["text"] == "Bambu PLA - Black, 10-12 days"
    assert all("\u2014" not in e.get("text", "") for e in events)


def test_home_assistant_tools_get_a_plain_note():
    tool = brain_tools.make("mcp__home_assistant__HassTurnOn", "Turn on.", OBJ,
                            lambda a, c: "ok")
    assert brain_loop.HOME_NOTE in brain_loop.system_notes([tool])


def test_a_model_that_cannot_call_tools_or_answer_raises():
    with pytest.raises(brain_loop.ToolsUnsupported):
        brain_loop.run(_q("x"), ctx={}, backend=FakeBrain(
            {"error": "registry.ollama.ai/library/gemma3:4b does not support tools"}), tools=[])
    with pytest.raises(brain_loop.BrainError):
        brain_loop.run(_q("x"), ctx={}, backend=FakeBrain(
            {"content": "[Error] Cannot connect to Ollama. Is it running?"}), tools=[])


def test_words_stream_through_chat_turn():
    brain_tools.register("echo", "Say it back.", OBJ, lambda a, c: "ok")
    brain = StreamingBrain([("echo", {})], "two words")
    events = []
    got = brain_loop.run(_q("x"), ctx={}, backend=brain, emit=events.append)
    assert got["text"] == "two words"
    assert [e["text"] for e in events if e["type"] == "token"] == ["two ", "words "]
    assert {"type": "progress", "line": brain_loop.THINKING_LINE} in events


def test_system_notes_add_search_first_only_with_the_find_tool():
    assert brain_loop.system_notes([]) == [brain_loop.TOOL_NOTE]
    find = brain_tools.make("find_things", "Find.", OBJ, lambda a, c: [])
    assert brain_loop.SEARCH_FIRST_NOTE in brain_loop.system_notes([find])


def test_a_clear_daily_request_is_told_to_go_straight_to_the_card():
    # A "do X every day at 8" request goes straight to the Yes or No card, not
    # to a plan in words that needs a second yes.
    assert brain_loop.SCHEDULE_NOTE not in brain_loop.system_notes([])
    sched = brain_tools.make("schedule_routine", "Daily.", OBJ, lambda a, c: "ok")
    assert brain_loop.SCHEDULE_NOTE in brain_loop.system_notes([sched])


# -- the yes question in the dashboard chat ---------------------------------------

def _yes_tool(ran):
    brain_tools.register("risky", "Risky.", OBJ, lambda a, c: ran.append(1) or "done",
                         needs_yes=lambda a, c: {"title": "May I run this command?",
                                                 "command": "echo hi"})


def test_streaming_asks_and_waits_for_her_tap():
    ran = []
    _yes_tool(ran)
    brain = FakeBrain([("risky", {})], "Ran it.")
    events = []
    for event in brain_loop.run_streaming(_q("go"), ctx={"device_id": DEV},
                                          backend=brain):
        events.append(event)
        if event["type"] == "ask":
            assert event["question"]["command"] == "echo hi"
            assert brain_loop.pending_ask(DEV) == event["id"]
            assert not brain_loop.answer_ask(event["id"], "device:other0000000002", True)
            assert brain_loop.answer_ask(event["id"], DEV, True)
    assert ran == [1]
    assert events[-1]["type"] == "done" and events[-1]["text"] == "Ran it."
    lines = [e.get("line") for e in events if e["type"] == "progress"]
    assert "Asked you before running echo hi ..." in lines


def test_her_no_means_nothing_runs():
    ran = []
    _yes_tool(ran)
    brain = FakeBrain([("risky", {})], "Okay, I did not run it.")
    out = []
    for event in brain_loop.run_streaming(_q("go"), ctx={"device_id": DEV}, backend=brain):
        out.append(event)
        if event["type"] == "ask":
            brain_loop.answer_ask(event["id"], DEV, False)
    assert ran == [] and out[-1]["text"] == "Okay, I did not run it."
    assert brain_tools.SAID_NO_LINE in brain.calls[1]["messages"][-1]["content"]
    lines = [e.get("line") for e in out if e["type"] == "progress"]
    assert brain_loop.SAID_NO_PROGRESS_LINE in lines and brain_loop.NO_ANSWER_LINE not in lines


def test_after_her_no_the_answer_says_plainly_it_did_not_run():
    """PrintingBot check (finding 4): the No reply began "I'll check your
    filament inventory for you." and asked for permission again."""
    ran = []
    _yes_tool(ran)
    brain = FakeBrain(
        {"content": "I'll check your filament inventory for you.",
         "tool_calls": [{"function": {"name": "risky", "arguments": {}}}]},
        "I'd be happy to check, but I'll need your permission first.")
    got = brain_loop.run(_q("go"), ctx={"ask": lambda q: False}, backend=brain)
    assert ran == []
    assert got["text"].startswith(brain_tools.SAID_NO_LINE)
    assert "I'll check your filament" not in got["text"]
    assert brain_loop.SAID_NO_NOTE in brain.calls[1]["messages"][-1]["content"]


def test_a_search_that_is_off_shows_no_step_line(monkeypatch):
    """PrintingBot check (finding 3): "Searching the web for ..." showed with
    web search off, though nothing went out."""
    from puffpai import web_search
    brain_tools_builtin.register_all()
    monkeypatch.setattr(web_search, "search", lambda words, vault=None: {
        "ok": False, "label": "", "results": [], "notes": ["Web search is off."],
        "left_out": ""})
    tool = brain_tools.find("search_web", {"caller": "owner"})
    monkeypatch.setattr(web_search, "ready", lambda: False)
    assert tool.progress_line({"query": "petg"}) == ""
    events = []
    brain = FakeBrain([("search_web", {"query": "petg"})], "Web search is off.")
    brain_loop.run(_q("look it up"), ctx={"caller": "owner"}, backend=brain,
                   emit=events.append)
    assert not [e for e in events if e["type"] == "progress" and e.get("line")]
    monkeypatch.setattr(web_search, "ready", lambda: True)
    assert tool.progress_line({"query": "petg"}) == 'Searching the web for "petg" ...'


def test_closing_the_page_turns_an_open_question_into_no():
    ran = []
    _yes_tool(ran)
    brain = FakeBrain([("risky", {})], "Stopped.")
    stream = brain_loop.run_streaming(_q("go"), ctx={"device_id": DEV}, backend=brain)
    for event in stream:
        if event["type"] == "ask":
            break
    stream.close()
    time.sleep(0.2)
    assert ran == [] and brain_loop.pending_ask(DEV) is None


def test_a_brain_error_comes_out_as_an_event():
    events = list(brain_loop.run_streaming(_q("x"), ctx={}, backend=FakeBrain(
        {"error": "model does not support tools"}), tools=[]))
    assert events == [{"type": "error", "error": "model does not support tools",
                       "unsupported": True}]


def test_an_unanswered_question_times_out_as_no(monkeypatch):
    ask_id = brain_loop.open_ask(DEV, {"title": "x"})
    assert brain_loop.wait_ask(ask_id, timeout=0.05) is False
    assert brain_loop.answer_ask(ask_id, DEV, True) is False   # too late


def test_the_terminal_asks_with_input(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    assert brain_loop.terminal_ask({"title": "May I?", "command": "ls"}) is True
    monkeypatch.setattr("builtins.input", lambda prompt="": "nah")
    assert brain_loop.terminal_ask({"title": "May I?"}) is False

    def _eof(prompt=""):
        raise EOFError
    monkeypatch.setattr("builtins.input", _eof)
    assert brain_loop.terminal_ask({"title": "May I?"}) is False


# -- run_command --------------------------------------------------------------------

@pytest.fixture
def state(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(brain_commands, "_vault_dir", lambda: str(tmp_path / "data" / "vault_data"))
    return tmp_path / "state"


def test_run_command_shows_the_exact_command_and_folder(state):
    q = brain_commands.question({"command": "  python make_list.py --out x  "}, {})
    assert q["command"] == "python make_list.py --out x"
    assert q["folder"] == os.path.abspath(str(state / "workspace"))
    assert q["title"] == "May I run this command?"


def test_run_command_runs_only_after_yes_in_the_work_folder(state):
    brain_commands.register()
    ctx = {"caller": "owner"}
    got = brain_tools.call("run_command", {"command": "echo hello"}, ctx)
    assert got["ok"] is False and got["error"] == brain_tools.NO_ASK_LINE
    asked = []
    got = brain_tools.call("run_command", {"command": "echo hello"},
                           dict(ctx, ask=lambda q: asked.append(q) or True))
    assert got["ok"] is True and got["result"]["exit_code"] == 0
    assert "hello" in got["result"]["output"]
    assert asked[0]["command"] == "echo hello"
    assert got["result"]["folder"] == os.path.abspath(str(state / "workspace"))


def test_run_command_is_for_the_owner_only(state):
    brain_commands.register()
    assert [t.name for t in brain_tools.available({"caller": None})] == []
    assert [t.name for t in brain_tools.available({"caller": "voice"})] == []
    assert [t.name for t in brain_tools.available({"caller": "terminal"})] == ["run_command"]


def test_run_command_uses_the_skill_folder(state):
    folder = state / "skills" / "bambu-local"
    folder.mkdir(parents=True)
    assert brain_commands.folder_for({"skill": "bambu-local"}) == os.path.abspath(str(folder))
    with pytest.raises(ValueError, match="no skill called nope"):
        brain_commands.folder_for({"skill": "nope"})
    with pytest.raises(ValueError):
        brain_commands.folder_for({"skill": "../escape"})


@pytest.mark.parametrize("command", [
    "type vault_data\\vault.enc", "cat ../api_key.txt", "", "   ",
])
def test_run_command_never_touches_the_vault_or_keys(state, command):
    with pytest.raises(ValueError):
        brain_commands.check_command(command)


def test_run_command_refuses_the_vault_path_itself(state, tmp_path):
    vault = str(tmp_path / "data" / "vault_data")
    with pytest.raises(ValueError):
        brain_commands.check_command(f'dir "{vault}"')


def test_run_command_without_yes_in_ctx_never_runs(state):
    with pytest.raises(ValueError):
        brain_commands.run({"command": "echo hi"}, {})


def _skill(state, name, files):
    folder = state / "skills" / name
    for rel, text in files.items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return folder


def test_a_skill_script_runs_in_its_own_folder_even_unnamed(state):
    folder = _skill(state, "filament-vault", {
        "SKILL.md": "---\nname: filament-vault\ndescription: spools\n---\n",
        "scripts/add_spool.py": "print('added')\n"})
    _skill(state, "other", {"SKILL.md": "---\nname: other\ndescription: x\n---\n"})
    got = brain_commands.folder_for({"command": "python3 scripts/add_spool.py --brand X"})
    assert got == os.path.abspath(str(folder))
    # by its own name too, and a plain command stays in the work folder
    assert brain_commands.folder_for({"skill": "filament-vault"}) == os.path.abspath(str(folder))
    assert brain_commands.folder_for({"command": "echo hi"}) == \
        os.path.abspath(str(state / "workspace"))


def test_python3_is_the_bots_python_and_home_is_the_work_folder(state):
    folder = _skill(state, "notes", {
        "SKILL.md": "---\nname: notes\ndescription: x\n---\n",
        "scripts/where.py": "import os, sys\nprint(os.path.expanduser('~'))\n"
                            "print(sys.version_info[0])\n"})
    got = brain_commands.run({"command": "python3 scripts/where.py", "skill": "notes"},
                             {"said_yes": True})
    assert got["exit_code"] == 0 and got["worked"] is True, got
    lines = got["output"].splitlines()
    assert os.path.normcase(os.path.abspath(lines[0])) == \
        os.path.normcase(os.path.abspath(str(state / "workspace")))
    assert lines[1] == "3"
    assert got["folder"] == os.path.abspath(str(folder))


def test_a_failed_command_says_plainly_it_did_not_work(state):
    got = brain_commands.run({"command": "exit 3"}, {"said_yes": True})
    assert got["exit_code"] == 3 and got["worked"] is False
    assert "did not work" in got["note"] and "Nothing" in got["note"]


def test_a_secret_a_command_prints_is_hidden(state):
    state.mkdir(parents=True, exist_ok=True)
    (state / "api_key.txt").write_text("FAKE-PROBE-API-KEY", encoding="utf-8")
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJ0ZXN0aW5nMTIzIn0.c2lnbmF0dXJlMTIzNDU2"
    got = brain_commands.run(
        {"command": f"python3 -c \"print(open('../api_key.tx' + 't').read()); print('{jwt}')\""},
        {"said_yes": True})
    assert "FAKE-PROBE-API-KEY" not in got["output"] and jwt not in got["output"]
    assert got["output"].count(brain_commands.HIDDEN) == 2
    with pytest.raises(ValueError):
        brain_commands.check_command("python -c \"import keyring; print(keyring.get_password('a','b'))\"")


def test_without_git_bash_on_windows_bash_is_refused(state, monkeypatch):
    monkeypatch.setattr(brain_commands.os, "name", "nt")
    monkeypatch.setattr(brain_commands, "git_bash", lambda: None)
    with pytest.raises(ValueError, match="WSL"):
        brain_commands.question({"command": "bash scripts/script.sh list"}, {})
    assert brain_commands.shell_name() == "the Windows command prompt"


def test_secrets_are_not_passed_to_commands(monkeypatch):
    monkeypatch.setenv("PUFFPAI_API_KEY", "x")
    monkeypatch.setenv("HA_TOKEN", "y")
    monkeypatch.setenv("SOME_SECRET", "z")
    monkeypatch.setenv("PLAIN_SETTING", "keep")
    env = brain_commands.clean_env()
    assert "PUFFPAI_API_KEY" not in env and "HA_TOKEN" not in env
    assert "SOME_SECRET" not in env and env.get("PLAIN_SETTING") == "keep"
    assert "PATH" in env or "Path" in env


# -- app actions as tools ----------------------------------------------------

@pytest.fixture
def world(monkeypatch, tmp_path):
    return sup.setup_chat(monkeypatch, tmp_path)


def test_app_actions_become_tools_for_her_device(world):
    brain_tools_builtin.register_all()
    names = [t.name for t in brain_tools.available(
        {"caller": "owner", "device_id": DEV, "surface": "chat"})]
    assert f"{sup.IID}__read_guests" in names and f"{sup.IID}__remove_guest" in names
    assert "run_command" in names and "search_web" in names
    keyless = [t.name for t in brain_tools.available({"caller": None})]
    assert keyless == []


def test_a_read_hands_the_rows_to_the_model(world):
    brain_tools_builtin.register_all()
    ctx = {"caller": "owner", "device_id": DEV, "surface": "chat"}
    brain = FakeBrain([(f"{sup.IID}__read_guests", {})], "Zanzibar Quill is coming.")
    got = brain_loop.run(_q("who is coming?"), ctx=ctx, backend=brain)
    assert got["text"] == "Zanzibar Quill is coming."
    fed = brain.calls[1]["messages"][-1]["content"]
    assert "Zanzibar Quill" in fed and '"ok": true' in fed


def test_a_change_runs_and_offers_undo(world):
    brain_tools_builtin.register_all()
    ctx = {"caller": "owner", "device_id": DEV, "surface": "chat"}
    got = brain_tools.call(f"{sup.IID}__add_guest", {"guest_name": "Leo Park"}, ctx)
    assert got["ok"] and got["result"]["ok"] and got["result"].get("can_undo")
    assert world["source"].runs[-1][1] == {"guest_name": "Leo Park"}
    assert "undo_last_change" in [t.name for t in brain_tools.available(ctx)]


def test_a_remove_asks_her_then_runs_with_a_ticket(world):
    brain_tools_builtin.register_all()
    ctx = {"caller": "owner", "device_id": DEV, "surface": "chat"}
    tool = f"{sup.IID}__remove_guest"
    got = brain_tools.call(tool, {"guest_name": "Ava Thompson"}, dict(ctx, ask=lambda q: False))
    assert got["ok"] is False and world["source"].runs == []
    asked = []
    got = brain_tools.call(tool, {"guest_name": "Ava Thompson"},
                           dict(ctx, ask=lambda q: asked.append(q) or True))
    assert got["ok"] and got["result"]["ok"], got
    assert asked[0]["title"] == "Remove this from Big Day?"
    assert "Ava Thompson" in asked[0]["detail"]
    assert world["source"].runs[-1][0] == tool


def test_the_builtins_register_once():
    brain_tools_builtin.register_all()
    brain_tools_builtin.register_all()
    owner = [t.name for t in brain_tools.available({"caller": "owner"})]
    assert owner.count("search_web") == 1 and "get_weather" in owner


def test_search_web_hands_back_found_links(monkeypatch):
    from puffpai import web_search
    brain_tools_builtin.register_all()
    monkeypatch.setattr(web_search, "search", lambda words, vault=None: {
        "ok": True, "label": "Wikipedia", "results": [
            {"title": "Fondant", "site": "en.wikipedia.org",
             "url": "https://en.wikipedia.org/wiki/Fondant", "snippet": "Icing."}],
        "notes": [], "left_out": ""})
    got = brain_tools.call("search_web", {"query": "fondant"}, {"caller": "owner"})
    assert got["result"]["found"][0]["link"] == "https://en.wikipedia.org/wiki/Fondant"


def test_answers_never_show_page_addresses():
    """Not "You can access it at /dashboard/apps/calendar"."""
    said = ("Great! The Calendar app is now on your home page. You can access it at "
            "/dashboard/apps/calendar to see your events. Enjoy!")
    assert brain_loop.plain_paths(said) == ("Great! The Calendar app is now on your "
                                            "home page. Enjoy!")
    assert brain_loop.plain_paths("Open it at `/dashboard/things/skill-x`. Have fun.") == (
        "It is on your home page. Have fun.")
    assert brain_loop.plain_paths("See [Books](/dashboard/knowledge) or /dashboard/knowledge.") \
        == "See Books or the Knowledge page."
    assert brain_loop.plain_paths("No addresses here.") == "No addresses here."


def test_no_answer_is_not_told_as_her_no():
    """A Yes/No card nobody answered runs nothing and never says she said no."""
    from puffpai import brain_tools
    calls = []
    brain_tools.register("t_no_answer_probe", "probe", {"type": "object", "properties": {}},
                         lambda args, ctx: calls.append(1) or "ran", needs_yes=True)
    try:
        got = brain_tools.call("t_no_answer_probe", {}, {"ask": lambda q: None})
        assert got["error"] == brain_tools.NO_ANSWER_LINE and got["asked"] and not calls
        got = brain_tools.call("t_no_answer_probe", {}, {"ask": lambda q: False})
        assert got["error"] == brain_tools.SAID_NO_LINE and not calls
    finally:
        brain_tools._TOOLS.pop("t_no_answer_probe", None) if hasattr(brain_tools, "_TOOLS") else None
