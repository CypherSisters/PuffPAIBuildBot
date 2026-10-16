"""A daily task uses a skill only when it fits.

Her "daily piano lesson" was tied to music-memory, a cloud practice journal
that needs an outside key. Now a skill is used only when it is installed, has
what it needs, and is about the request; otherwise the task runs without one
and the card says why. Her own words are kept in the saved request, and she
can change the skill on the task's card.
"""
from __future__ import annotations

import os

import pytest

from puffpai import brain_tools, brain_tools_builtin, routines, skills


@pytest.fixture(autouse=True)
def _tools():
    brain_tools.clear()
    brain_tools_builtin.reset_for_tests()
    brain_tools_builtin.register_all()
    yield
    brain_tools.clear()
    brain_tools_builtin.reset_for_tests()


def _skill(folder, description):
    path = os.path.join(skills.skills_dir(create=True), folder)
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write(f"---\nname: {folder}\ndescription: {description}\n---\n\n# {folder}\n")


JOURNAL = ("A practice journal that remembers the music, recordings and lesson notes "
           "stored in BlueColumn. Requires a BlueColumn API key (bc_live_*).")
TUTOR = "Teaches piano: scales, which keys to press and which fingers to use."


def _schedule(args, said=""):
    asked = []
    got = brain_tools.call("schedule_routine", args, {
        "caller": "owner", "said": said, "ask": lambda q: asked.append(q) or True})
    return got, asked[0]


def test_a_skill_that_needs_an_outside_key_is_left_out():
    _skill("music-memory", JOURNAL)
    got, card = _schedule({"prompt": "daily piano lesson", "time": "08:00",
                           "skill": "music-memory"})
    assert got["ok"]
    row = routines.list_routines()[0]
    assert row["params"]["skill"] is None
    assert "without a skill" in card["detail"] and "music-memory" in card["detail"]
    assert "outside account or key" in got["result"]["no_skill"]


def test_a_skill_that_is_not_about_the_request_is_left_out():
    _skill("seo-coach", "Helps a website rank in search engines.")
    _, card = _schedule({"prompt": "daily piano lesson", "time": "08:00",
                         "skill": "seo-coach"})
    assert routines.list_routines()[0]["params"]["skill"] is None
    assert "does not look like it does this" in card["detail"]


def test_a_missing_skill_is_left_out():
    _, card = _schedule({"prompt": "daily piano lesson", "time": "08:00",
                         "skill": "no-such-skill"})
    assert routines.list_routines()[0]["params"]["skill"] is None
    assert "no skill called no-such-skill is installed" in card["detail"]


def test_a_skill_that_fits_is_used_and_shown_on_the_card():
    _skill("piano-tutor", TUTOR)
    got, card = _schedule({"prompt": "daily piano lesson", "time": "08:00",
                           "skill": "piano-tutor"})
    assert routines.list_routines()[0]["params"]["skill"] == "piano-tutor"
    assert "with the skill piano-tutor" in card["detail"]
    assert "no_skill" not in got["result"]


def test_her_own_words_are_kept_in_the_saved_request():
    said = ("make me a daily piano lesson at 8am that shows the keys to press and "
            "which hand and finger to use")
    _, card = _schedule({"prompt": "daily piano lesson", "time": "08:00"}, said=said)
    prompt = routines.list_routines()[0]["params"]["prompt"]
    assert prompt.startswith("daily piano lesson")
    assert "which hand and finger to use" in prompt
    assert "which hand and finger" in card["detail"]
    assert len(prompt) <= routines.PROMPT_MAX


def test_her_words_are_not_repeated_when_the_prompt_has_them():
    _schedule({"prompt": "Give me a piano lesson", "time": "08:00"},
              said="give me a piano lesson")
    assert routines.list_routines()[0]["params"]["prompt"] == "Give me a piano lesson"


def test_she_can_change_the_skill_on_the_card():
    _skill("piano-tutor", TUTOR)
    row = routines.add_routine("daily_prompt", daily_at="08:00",
                               params={"prompt": "piano lesson"})
    assert routines.set_skill(row["id"], "piano-tutor")["params"]["skill"] == "piano-tutor"
    assert routines.set_skill(row["id"], "")["params"]["skill"] is None
    with pytest.raises(routines.RoutineError):
        routines.set_skill(row["id"], "not-installed")
    assert routines.set_skill("no_such_task", "piano-tutor") is None
    assert {"folder": "piano-tutor", "name": "piano-tutor"} in routines.skill_choices()


def test_the_routes_list_skills_and_change_one():
    from puffpai import api_server
    _skill("piano-tutor", TUTOR)
    row = routines.add_routine("daily_prompt", daily_at="08:00",
                               params={"prompt": "piano lesson"})
    client = api_server.app.test_client()
    h = {"Authorization": f"Bearer {api_server._API_KEY}"}
    listed = client.get("/routines", headers=h).get_json()
    assert any(s["folder"] == "piano-tutor" for s in listed["skills"])
    got = client.post(f"/routines/{row['id']}/skill", json={"skill": "piano-tutor"}, headers=h)
    assert got.status_code == 200 and got.get_json()["routine"]["skill"] == "piano-tutor"
    bad = client.post(f"/routines/{row['id']}/skill", json={"skill": "nope"}, headers=h)
    assert bad.status_code == 400


def test_a_bound_skill_that_is_not_ready_is_left_out_when_it_runs(monkeypatch):
    """Her piano task saved before the needs check, still bound to
    music-memory: each run checks again, runs without it, and the card says
    so. The binding is not changed behind her back."""
    from puffpai import announcer
    from puffpai.routines_api import _row_view
    monkeypatch.setattr(announcer, "announce", lambda text, **k: {"ok": True})
    _skill("music-memory", JOURNAL)
    seen = []
    routines.set_prompt_runner(lambda row: seen.append(row["params"]["skill"]) or "Lesson.")
    try:
        row = routines.add_routine("daily_prompt", daily_at="08:00",
                                   params={"prompt": "daily piano lesson",
                                           "skill": "music-memory"})
        assert routines.run_daily_prompt(row["id"]) == "Lesson."
        after = routines.get_routine(row["id"])
        assert seen == [None]
        assert after["params"]["skill"] == "music-memory"        # still hers
        assert after["skill_note"].startswith("Ran without music-memory last time:")
        assert "outside account or key" in after["skill_note"]
        assert _row_view(after)["skill_note"] == after["skill_note"]
        assert routines.results_for_skill("music-memory") == []
        # A skill that is ready runs bound, and the note goes away.
        _skill("piano-tutor", TUTOR)
        routines.set_skill(row["id"], "piano-tutor")
        assert routines.get_routine(row["id"])["skill_note"] == ""
        routines.run_daily_prompt(row["id"])
        assert seen == [None, "piano-tutor"]
        assert routines.get_routine(row["id"])["skill_note"] == ""
        assert routines.results_for_skill("piano-tutor")[0]["text"] == "Lesson."
    finally:
        routines.set_prompt_runner(None)
