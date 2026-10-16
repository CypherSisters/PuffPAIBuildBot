"""The wizard's two wordings, and the age that picks between them.

Age only changes how the wizard words its questions (plainer words and fewer
choices for a young builder), and the finished bot is unaffected. Only a band is kept, never the number.

Run:
    pytest tests/test_wizard_wording.py
"""
from __future__ import annotations

import re

import pytest

from puffpai.wizard import wording


@pytest.mark.parametrize("typed,band", [
    ("10", wording.UNDER_13), ("I'm 8", wording.UNDER_13), (12, wording.UNDER_13),
    ("13", wording.TEEN), ("15 years", wording.TEEN), ("17", wording.TEEN),
    ("18", wording.ADULT), ("44", wording.ADULT), ("99", wording.ADULT),
    ("", None), (None, None), ("old enough", None), ("200", None), ("2", None),
    (True, None),
])
def test_an_age_becomes_a_band_or_nothing(typed, band):
    assert wording.band_for_age(typed) == band


def test_only_a_young_builder_gets_the_simple_wording():
    assert wording.mode_for_band(wording.UNDER_13) == wording.SIMPLE
    assert wording.mode_for_band(wording.TEEN) == wording.STANDARD
    assert wording.mode_for_band(wording.ADULT) == wording.STANDARD
    assert wording.mode_for_band(None) == wording.STANDARD


def test_the_mode_is_read_from_the_run():
    assert wording.mode_of({"wording": "simple"}) == wording.SIMPLE
    assert wording.mode_of({"age_band": wording.UNDER_13}) == wording.SIMPLE
    assert wording.mode_of({}) == wording.STANDARD
    assert wording.mode_of(None) == wording.STANDARD
    assert wording.mode_of({"wording": "shouting"}) == wording.STANDARD


def test_every_sentence_has_both_versions():
    for key, entry in wording.TEXT.items():
        assert set(entry) == {wording.STANDARD, wording.SIMPLE}, key
        assert all(isinstance(text, str) and text for text in entry.values()), key


def test_the_simple_version_is_short_sentences():
    """One idea per line: no line of the simple wording runs past 16 words."""
    long = []
    for key, entry in wording.TEXT.items():
        for line in entry[wording.SIMPLE].split("\n"):
            for sentence in re.split(r"(?<=[.!?])\s+", line):
                if len(sentence.split()) > 16:
                    long.append(f"{key}: {sentence}")
    assert not long, "\n".join(long)


def test_say_fills_in_and_never_raises():
    assert wording.say("summary_title", name="StargazerBot") == \
        "Here is the plan for StargazerBot"
    # A missing value leaves the sentence readable rather than crashing.
    assert "{name}" in wording.say("summary_title")
    assert wording.say("no_such_key") == "no_such_key"
    assert wording.lines("checked_offline", wording.SIMPLE) == [
        "The internet does not work here.", "So we will not download anything."]


def test_the_three_questions_are_the_plain_ones():
    assert wording.say("goal_question") == (
        "What kind of bot do you want? Say it in your own words.")
    assert wording.say("name_question") == "What should we call it?"
    assert wording.say("age_question") == (
        "How old are you? Only needed if you are under 13, so I can use simpler "
        "words. Everyone else can leave it empty.")
