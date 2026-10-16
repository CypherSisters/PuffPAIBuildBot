"""A daily task: the tool loop once a day, after her yes.

* ``schedule_routine`` asks Yes or No (the same card as run_command) before
  anything is saved, and saving records her yes in the consent ledger;
* a run goes through the chat's tool loop with the skill loaded and last
  time's note, keeps the result (home page and the skill's page) and builds
  day N on day N-1;
* a command inside a run waits for her yes from any paired device; no answer
  means it does not run.
"""
from __future__ import annotations

import json
import threading

import pytest

from puffpai import api_server, brain_loop, brain_tools, brain_tools_builtin, routines


@pytest.fixture(autouse=True)
def _tools():
    brain_tools.clear()
    brain_tools_builtin.reset_for_tests()
    brain_tools_builtin.register_all()
    yield
    brain_tools.clear()
    brain_tools_builtin.reset_for_tests()
    routines.set_prompt_runner(None)


ARGS = {"prompt": "Give me a short piano lesson", "time": "07:30"}


def test_schedule_routine_asks_first_and_saves_after_a_yes():
    asked = []
    no = brain_tools.call("schedule_routine", ARGS,
                          {"caller": "owner", "ask": lambda q: asked.append(q) or False})
    assert no["ok"] is False and routines.list_routines() == []
    assert asked[0]["title"] == "May I do this every day?"
    assert "07:30" in asked[0]["detail"] and "piano lesson" in asked[0]["detail"]
    yes = brain_tools.call("schedule_routine", ARGS,
                           {"caller": "owner", "ask": lambda q: True})
    assert yes["ok"] is True
    row = routines.list_routines()[0]
    assert row["kind"] == "daily_prompt" and row["daily_at"] == "07:30"
    assert routines.is_runnable(row)          # the ledger holds her yes


def test_no_ask_means_nothing_is_saved():
    got = brain_tools.call("schedule_routine", ARGS, {"caller": "owner"})
    assert got["ok"] is False and routines.list_routines() == []


def test_day_two_builds_on_day_one_and_results_are_kept(monkeypatch):
    seen = []

    def runner(row):
        seen.append(routines.last_note(row))
        return f"Lesson {len(seen)}: scales in C."
    routines.set_prompt_runner(runner)
    from puffpai import announcer
    said = []
    monkeypatch.setattr(announcer, "announce", lambda text, **k: said.append(text) or {"ok": True})
    import os
    from puffpai import skills
    folder = os.path.join(skills.skills_dir(create=True), "piano")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: piano\ndescription: Piano lessons.\n---\n\n# piano\n")
    row = routines.add_routine("daily_prompt", daily_at="07:30",
                               params={"prompt": "piano lesson", "skill": "piano"})
    routines.run_daily_prompt(row["id"])
    routines.run_daily_prompt(row["id"])
    assert seen == ["", "Lesson 1: scales in C."]
    after = routines.get_routine(row["id"])
    assert after["memory"]["day"] == 2 and after["last_text"] == "Lesson 2: scales in C."
    assert [r["day"] for r in after["results"]] == [2, 1]
    assert "(day 2) is ready" in said[-1]
    assert routines.results_for_skill("piano")[0]["text"] == "Lesson 2: scales in C."


def test_execute_starts_it_in_its_own_thread(monkeypatch):
    done = threading.Event()
    routines.set_prompt_runner(lambda row: done.set() or "ok")
    from puffpai import announcer
    monkeypatch.setattr(announcer, "announce", lambda *a, **k: {"ok": True})
    row = routines.add_routine("daily_prompt", daily_at="07:30", params={"prompt": "x"})
    got = routines.execute(row["id"])
    assert got["ok"] and got["started"]
    assert done.wait(5)


def test_the_run_uses_the_tool_loop_with_the_skill_and_last_note(monkeypatch):
    seen = {}

    def fake_loop(text, history, ctx, extra_core=None):
        seen.update(text=text, extra=extra_core, ask=ctx.get("ask"))
        return {"response": "Today: arpeggios."}
    monkeypatch.setattr(api_server, "_loop_answer", fake_loop)
    monkeypatch.setattr(api_server, "_tool_loop_enabled", lambda: True)
    row = routines.add_routine("daily_prompt", daily_at="07:30",
                               params={"prompt": "piano lesson"})
    routines._update(row["id"], memory={"day": 1, "last_note": "Scales in C."})  # noqa: SLF001
    out = api_server._run_daily_prompt(routines.get_routine(row["id"]))  # noqa: SLF001
    assert out == "Today: arpeggios."
    assert seen["text"] == "piano lesson"
    assert "day 2" in seen["extra"][-1] and "Scales in C." in seen["extra"][-1]
    assert callable(seen["ask"])


def test_a_command_in_a_run_waits_for_a_paired_yes(monkeypatch):
    monkeypatch.setattr(brain_loop, "ROUTINE_ASK_SECONDS", 5)
    ask = api_server._routine_ask("Piano")  # noqa: SLF001
    got = {}
    worker = threading.Thread(target=lambda: got.update(yes=ask(
        {"title": "May I run this command?", "command": "python3 lesson.py"})))
    worker.start()
    for _ in range(100):
        waiting = brain_loop.waiting_for_anyone()
        if waiting:
            break
        threading.Event().wait(0.02)
    assert waiting[0]["routine"] == "Piano" and waiting[0]["command"] == "python3 lesson.py"
    assert brain_loop.answer_ask(waiting[0]["id"], "terminal", True) is False   # not a paired page
    assert brain_loop.answer_ask(waiting[0]["id"], "device:abc", True) is True
    worker.join(5)
    assert got["yes"] is True


def test_no_answer_runs_nothing(monkeypatch):
    monkeypatch.setattr(brain_loop, "ROUTINE_ASK_SECONDS", 0.05)
    ask = api_server._routine_ask("Piano")  # noqa: SLF001
    assert ask({"title": "May I run this command?"}) is None


def test_the_waiting_route_lists_routine_questions():
    api_server.app.config.update(TESTING=True)
    client = api_server.app.test_client()
    ask_id = brain_loop.open_ask(brain_loop.ANY_DEVICE, {"title": "May I?", "routine": "Piano"})
    try:
        body = client.get("/routines/waiting",
                          headers={"Authorization": f"Bearer {api_server._API_KEY}"}).get_json()
        assert [w["id"] for w in body["waiting"]] == [ask_id]
    finally:
        brain_loop.cancel_asks([ask_id])
        brain_loop._wait(ask_id, 0)  # noqa: SLF001


def test_a_daily_task_needs_something_to_do():
    with pytest.raises(routines.RoutineError):
        routines.add_routine("daily_prompt", daily_at="07:30", params={"prompt": " "})
    assert json.loads(json.dumps(routines.kind("daily_prompt")))["schedule"] == "daily"


def test_a_run_started_from_a_console_keeps_its_own_ask(monkeypatch):
    """Start BuildBot runs the backend in a console, so stdin is a tty: a
    routine run's question must still go to her paired devices."""
    import sys

    class _Tty:
        def isatty(self):
            return True
    seen = {}

    def fake_run(messages, ctx=None, **_k):
        seen["ask"] = ctx.get("ask")
        raise RuntimeError("stop here")
    monkeypatch.setattr(sys, "stdin", _Tty())
    monkeypatch.setattr(api_server, "_loop_messages", lambda *a, **k: ([], None, [], {}))
    monkeypatch.setattr(brain_tools, "available", lambda ctx: [])
    monkeypatch.setattr(brain_loop, "run", fake_run)
    mine = api_server._routine_ask("Piano lesson")
    try:
        api_server._loop_answer("today's lesson", [], {"ask": mine})
    except Exception:
        pass
    assert seen["ask"] is mine
