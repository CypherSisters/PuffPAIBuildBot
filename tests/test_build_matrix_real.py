"""The build's decide step over REAL finds.

``tests/fixtures/build/real/<goal>.json`` holds what the real brain made of
four goals and what the real shelves, code sites and data lists answered for
them (``live_f/record_real.py``: nothing installed, nothing switched on). The
same rules as a live build run over them, with the real meaning matcher and
no brain judge, so the thresholds stand on real catalog results and not only
on the scripted matrix. Skipped when the reading helper is not on this
computer.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import os

import pytest

from puffpai.build import classify, decide, interpret
from puffpai.build import schema as S

HERE = os.path.join(os.path.dirname(__file__), "fixtures", "build", "real")
GOALS = ("space", "wedding", "garden", "music")


def _embed():
    from puffpai.knowledge import reading_helper
    if not reading_helper.is_ready():
        pytest.skip("the reading helper is not on this computer")
    return decide.default_embed()


def run(name: str) -> tuple[dict, list[dict], dict]:
    with open(os.path.join(HERE, name + ".json"), encoding="utf-8") as fh:
        rec = json.load(fh)
    jobs = interpret.tidy([interpret.grounded(dict(j), rec["goal"]) for j in rec["jobs"]],
                          rec["goal"])
    cands = [dict(c) for c in rec["cands"]]
    got = decide.decide(jobs, cands, brain=None, embed=_embed())
    return rec, got["jobs"], {c["ref"]: c for c in cands}


def job_like(jobs: list[dict], *words: str) -> dict:
    for j in jobs:
        text = f"{j['name']} {j['does']} {' '.join(j['subject'])}".lower()
        if any(w in text for w in words):
            return j
    raise AssertionError(f"no job about {words}: {[j['name'] for j in jobs]}")


@pytest.mark.parametrize("name", GOALS)
def test_real_finds_follow_the_rules(name):
    rec, jobs, by_ref = run(name)
    assert 1 <= len(jobs) <= interpret.MAX_JOBS
    for j in jobs:
        assert "_" not in j["name"], "plain names, never code names"
        for ref in j["ingredients"]:
            c = by_ref[ref]
            assert not classify.not_usable(c), (j["name"], ref, classify.not_usable(c))
        if j["route"] == S.A:
            c = by_ref[j["pick"]["ref"]]
            assert j["pick"]["score"] >= decide.floor_for(c)
        if j["action"] == "remind":
            assert j["pick"]["ref"] == "builtin:routine", "reminders are the bot's own routines"
        if j["pick"]["class"] in S.ANSWER_CLASSES:
            assert j["action"] in ("learn", "ask")


def test_real_space_bot():
    _rec, jobs, _ = run("space")
    pictures = job_like(jobs, "earth", "picture", "views")
    assert pictures["route"] == S.C and pictures["pick"]["ref"] == "seed:nasa_epic"
    sky = job_like(jobs, "sky", "night")
    assert sky["route"] == S.C and "seed:seventimer" in sky["ingredients"], \
        "the stargazing forecast for her town is part of the night sky job"
    launches = job_like(jobs, "launch")
    assert launches["route"] == S.C and launches["pick"]["ref"] == "seed:launch_library"
    assert launches["action"] != "remind", "she did not ask to be reminded"


def test_real_wedding_bot_drops_lookalikes():
    _rec, jobs, by_ref = run("wedding")
    used = {r for j in jobs for r in j["ingredients"]}
    assert not any(r.startswith("apis_guru:azure.com") for r in used), \
        "a cloud monitoring API about 'guest' settings is no guest list"
    assert "github:nextcloud/guests" not in used


def test_real_garden_bot_drops_lookalikes():
    _rec, jobs, _ = run("garden")
    used = {r for j in jobs for r in j["ingredients"]}
    assert "github:jocull/ZenGardenFarmer" not in used, "a game helper is not a garden helper"
    assert "github:lambdaisland/garden-watcher" not in used, "a code library named garden"


def test_real_music_bot():
    _rec, jobs, _ = run("music")
    reminder = job_like(jobs, "remind", "practice")
    assert reminder["pick"]["ref"] == "builtin:routine"
    tutor = job_like(jobs, "guitar", "chord")
    assert tutor["pick"]["ref"] != "github:hitherejoe/Fret", "made for another assistant"
