"""What Toolsmith sends Anthropic: a health statement about a person loses
the person and the amounts; the health word, the app's own text, URLs, repo
names and commands all go as they are."""

from __future__ import annotations

import json

from puffpai.security import toolsmith_cloud as tc
from puffpai.security.data_classifier import scrub_health_statements

MARK = "[health details kept private]"

#: The verifier's repro: ToolsmithRun.task() is one block holding the job,
#: the candidate's name and URL, and its README run steps.
TASK = (
    "The job: Insulin log: track my insulin doses. I have diabetes and my dose "
    "is 10 units twice a day.\n"
    "Candidates, best first:\n"
    "- insulin-tracker (https://github.com/acme/insulin-tracker; 12 stars). "
    "Its README says to run: git clone https://github.com/acme/insulin-tracker; "
    "cd insulin-tracker; docker compose up -d\n"
    "Start with download on the first one.")


def test_task_keeps_the_app_and_its_commands_and_loses_her_details():
    out = tc.clean(TASK)
    for kept in ("https://github.com/acme/insulin-tracker",
                 "- insulin-tracker (", "cd insulin-tracker;",
                 "docker compose up -d", "Insulin log", "insulin doses",
                 "diabetes"):
        assert kept in out, kept
    assert MARK in out
    for gone in ("my insulin", "I have", "my dose", "10 units", "twice a day"):
        assert gone not in out, gone


def test_the_goal_keeps_its_subject_for_the_search_words():
    ask = json.dumps({"goal": "an app to track my insulin doses",
                      "jobs": [{"id": "j1", "name": "Insulin log",
                                "does": "logs insulin doses"}]})
    body = tc.body_for(tc.DEFAULT_MODEL, tc.clean("s"), [],
                       [{"role": "user", "content": ask}], find=True)
    sent = json.loads(body["messages"][0]["content"])
    assert sent["goal"] == "an app to track insulin doses"
    assert "insulin" in sent["goal"] and sent["jobs"][0]["name"] == "Insulin log"


def test_asthma_diary():
    out = tc.clean("An asthma diary for my son. He has asthma and uses his "
                   "inhaler 2 puffs twice a day.")
    assert "asthma diary" in out and "inhaler" in out
    for gone in ("my son", "He has", "his inhaler", "2 puffs", "twice a day"):
        assert gone not in out, gone


def test_readme_text_about_no_one_goes_unchanged():
    for readme in ("Our app tracks blood sugar. Blood sugar charts live at /charts.",
                   "This app tracks heart rate, sleep and blood pressure for anyone.",
                   "A medication tracker. Supports insulin pens and inhalers."):
        assert tc.clean(readme) == readme


def test_a_medicine_and_its_dose():
    out = tc.clean("I'm on metformin 500mg twice a day")
    assert "metformin" in out and "500mg" not in out and "I'm" not in out


def test_command_lines_and_code_tokens_are_never_edited():
    command = "pip install my-asthma-tool && my inhaler --dose 2"
    assert scrub_health_statements(command) == command
    out = scrub_health_statements("see C:/Users/me/my_insulin.json for my insulin log")
    assert out == "see C:/Users/me/my_insulin.json for insulin log"


def test_command_output_about_her_is_cleaned():
    turn = {"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": "toolu_1",
        "content": "notes.txt: She takes amoxicillin 250mg every 8 hours"}]}
    out = tc._clean_turn(turn)["content"][0]["content"]  # noqa: SLF001
    assert "She takes" not in out and "250mg" not in out
    assert "every 8 hours" not in out


def test_clean_is_still_idempotent():
    for text in (TASK, "I have asthma and my inhaler is blue, 2 puffs daily"):
        once = tc.clean(text)
        assert tc.clean(once) == once
        assert scrub_health_statements(once) == once
