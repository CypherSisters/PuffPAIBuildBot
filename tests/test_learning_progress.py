"""How far along learning is: one file per book, words from plain_lines only.

Time left comes from characters done, and no time is given until it has been
measured on this computer (a fresh state shows no number).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from learning_support import jargon_in  # noqa: E402

from puffpai import fsutil  # noqa: E402
from puffpai.knowledge import progress  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(progress.time, "monotonic", c)
    return c


def _writer(total=10_000_000, articles=7040):
    w = progress.Writer("vikidia_en", "Vikidia")
    w.start_meaning(articles_total=articles, chars_total=total,
                    articles_done=0, chars_done=0)
    return w


def test_no_time_before_three_percent_and_twenty_seconds(clock):
    w = _writer()
    clock.t += 30
    w.meaning(100, 200_000)  # 2% after 30 s
    assert w.rec["meaning"]["seconds_left"] is None
    clock.t = 1000 + 10
    w2 = _writer()
    clock.t += 10
    w2.meaning(1000, 500_000)  # 5% but only 10 s
    assert w2.rec["meaning"]["seconds_left"] is None
    line = progress.plain_lines(dict(w2.rec, updated_at=datetime.now(timezone.utc).isoformat()))[1]
    assert "left" not in line and "timed" in line


def test_time_left_comes_from_characters_not_articles(clock):
    w = _writer(total=10_000_000, articles=100)
    clock.t += 30
    # 90 of 100 articles are done but only 10% of the text: characters rule.
    w.meaning(90, 1_000_000)
    left = w.rec["meaning"]["seconds_left"]
    assert w.rec["meaning"]["basis"] == "measured"
    assert 260 <= left <= 280  # 9M chars at ~33k chars/s


def test_the_lines_at_three_points(clock):
    w = _writer(total=11_000_000)
    clock.t += 1
    w.meaning(10, 20_000)
    fresh = dict(w.rec, updated_at=datetime.now(timezone.utc).isoformat())
    early = progress.plain_lines(fresh)
    clock.t += 60
    w.meaning(3120, 5_500_000)
    mid = progress.plain_lines(dict(w.rec, updated_at=fresh["updated_at"]))
    w.update(stage="done")
    end = progress.plain_lines(w.rec)
    assert early == ["Vikidia: ready to answer questions.",
                     "Learning Vikidia: 10 of 7,040 articles. I will tell you how "
                     "long it takes once I have timed some of it."]
    assert mid[1] == "Learning Vikidia: 3,120 of 7,040 articles, about 1 minute left."
    assert end == ["Vikidia: fully learned."]


def test_a_fresh_state_shows_no_time_for_a_book_not_started():
    assert progress.measured_rate() is None
    rec = {"id": "b", "name": "Repair guides", "stage": "not_started",
           "meaning": {"chars_total": 500_000_000}}
    lines = progress.plain_lines(rec, "terminal")
    assert not any(ch.isdigit() for ch in " ".join(lines[:-1]))
    progress.record_rate(40_000, cpu_share=0.5)
    lines = progress.plain_lines(rec, "terminal")
    assert "It would take about 3.5 hours on this computer." in lines


def test_a_record_nobody_writes_any_more_is_shown_stopped():
    old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    rec = {"id": "b", "name": "Vikidia", "stage": "meaning", "quick_ready": True,
           "updated_at": old, "meaning": {"chars_done": 41, "chars_total": 100}}
    lines = progress.plain_lines(rec, "browser")
    assert lines[1] == "Learning it fully stopped part way: 41% done."
    assert lines[2] == "Press Carry on to pick up where it left off."


def test_a_progress_write_that_fails_never_stops_learning(monkeypatch):
    def boom(*a, **k):
        raise PermissionError("held open")
    monkeypatch.setattr(fsutil, "atomic_write_json", boom)
    w = progress.Writer("b", "Book")
    w.update(force=True, stage="quick")  # no exception


def test_every_stage_and_surface_speaks_plain_words():
    now = datetime.now(timezone.utc).isoformat()
    for stage in ("quick", "quick_stopped", "ready", "meaning", "paused", "stopped",
                  "not_started", "waiting_helper", "done", "error"):
        for surface in progress.SURFACES:
            rec = {"id": "b", "name": "Vikidia", "stage": stage, "updated_at": now,
                   "quick": {"done": 5, "total": 10}, "quick_ready": True,
                   "resumed_from_pct": 41, "error": "file_incomplete",
                   "meaning": {"articles_done": 1, "articles_total": 2,
                               "chars_done": 1, "chars_total": 2,
                               "seconds_left": 120, "basis": "measured"}}
            lines = progress.plain_lines(rec, surface)
            assert lines, (stage, surface)
            for line in lines:
                assert not jargon_in(line), (stage, surface, line)


def test_the_bot_answers_are_you_done_learning_from_the_records(clock):
    assert progress.asks_about_learning("are you done learning?")
    assert progress.asks_about_learning("Are you still learning the books")
    assert not progress.asks_about_learning("what is learning")
    assert progress.chat_answer() is None
    w = _writer()
    w.update(force=True)
    assert "Learning Vikidia: 0 of 7,040 articles" in progress.chat_answer()


def test_duration_words():
    assert progress.duration_words(None) == ""
    assert progress.duration_words(30) == "less than a minute"
    assert progress.duration_words(61) == "about 1 minute"
    assert progress.duration_words(125) == "about 2 minutes"
    assert progress.duration_words(3.5 * 3600) == "about 3.5 hours"


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), 1e12, -5, "fast", None])
def test_a_damaged_speed_record_is_not_a_measurement(bad):
    fsutil.atomic_write_json(os.path.join(progress.learning_dir(), "rates.json"),
                             {"meaning_chars_per_s": bad})
    assert progress.measured_rate() is None
    assert progress.estimate_seconds(10_000_000) is None
    rec = {"id": "b", "name": "Repair guides", "stage": "not_started",
           "meaning": {"chars_total": 500_000_000}}
    assert not any(ch.isdigit() for ch in " ".join(progress.plain_lines(rec, "terminal")[:-1]))


def test_an_impossible_speed_is_never_recorded():
    progress.record_rate(float("inf"), cpu_share=0.5)
    progress.record_rate(float("nan"), cpu_share=0.5)
    assert progress.measured_rate() is None
