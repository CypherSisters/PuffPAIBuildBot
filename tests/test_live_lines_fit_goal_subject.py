"""No NASA pictures for a wedding bot.

An earlier version offered, pre-ticked and even sampled ``seed:nasa_epic`` and
``seed:nasa_images`` for the owner's wedding planner: the goal said "pictures",
a seed tag said "picture", our own checked list was exempt from the
two-word rule, and every live line was looked at, ticked or not.

Goal-agnostic: the 12 unrelated tuning goals in ``tests/fixtures/tuning_goals.json``.
"""
from __future__ import annotations

import json
import os

import pytest

from puffpai.catalogs import live

HERE = os.path.dirname(os.path.abspath(__file__))
GOALS = json.load(open(os.path.join(HERE, "fixtures", "tuning_goals.json"),
                       encoding="utf-8"))["goals"]
NOT_SPACE = [g for g in GOALS if g["id"] != "space"]
SPACE = next(g for g in GOALS if g["id"] == "space")


def _refs(items) -> list[str]:
    return [str(i.get("apply", {}).get("ref") or i.get("id")) for i in items]


@pytest.mark.parametrize("g", NOT_SPACE, ids=[g["id"] for g in NOT_SPACE])
def test_no_nasa_or_holiday_line_off_its_subject(g):
    ids = " ".join(i["id"] for i in live.proposal_items(g["text"]))
    assert "nasa_" not in ids and "nager_holidays" not in ids, (g["id"], ids)


def test_the_space_goal_keeps_a_nasa_line():
    ids = [i["id"] for i in live.proposal_items(SPACE["text"])]
    assert any("nasa_" in i for i in ids), ids


def test_a_shape_word_alone_is_not_evidence():
    cand = {"name": "Photo Archive", "what_it_is": "Old photos of anything.",
            "match_terms": ["photo", "picture"]}
    goal = "I want a wedding planner bot. Track themes from pictures."
    assert live.fits_goal(goal, cand, ["picture"]) is False
    assert live.GENERIC_TAGS >= {"picture", "photo", "image", "calendar", "news", "map"}


@pytest.mark.parametrize("g", GOALS, ids=[g["id"] for g in GOALS])
def test_every_live_line_fits_the_goal_words(g, monkeypatch):
    """Our own list gets no pass: every line passed the fit check."""
    seen: list[tuple[str, bool]] = []
    real = live.fits_goal

    def spy(goal, cand, matched):
        got = real(goal, cand, matched)
        seen.append((str(cand.get("id")), got))
        return got

    monkeypatch.setattr(live, "fits_goal", spy)
    items = [i for i in live.proposal_items(g["text"]) if i.get("section") == "live_info"]
    passed = {cid for cid, ok in seen if ok}
    for item in items:
        assert str(item["apply"]["ref"]) in passed, (g["id"], item["id"])


def test_a_person_is_never_a_device_line():
    goals = ["Build me a fitness coach bot. I train with my friend Dana.",
             "I want a meal planner bot for our family of five."]
    for goal in goals:
        labels = " ".join(str(i.get("label")) for i in live.proposal_items(goal))
        assert "Dana" not in labels and "family" not in labels.lower(), labels
    car = ("I want a car bot for my 2014 Honda Civic and my daughter's 2019 Toyota "
           "Corolla.")
    labels = [str(i.get("label")) for i in live.proposal_items(car)
              if i["id"].startswith("device")]
    assert any("Corolla" in x for x in labels)
    assert not any("daughter" in x.lower() for x in labels), labels


def test_a_verb_alone_never_makes_a_device_line():
    """"play practice games" matched "Epic Games Store" on the word "games"."""
    goal = ("I want a chess coach bot. I'll need to be able to play practice games on "
            "a board and work through puzzles.")
    assert not [i for i in live.proposal_items(goal) if i["id"].startswith("device")]
    # A thing that is really hers still gets its line.
    labels = [i["label"] for i in live.proposal_items("a bot for my smart telescope")
              if i["id"].startswith("device")]
    assert labels and "telescope" in labels[0]
