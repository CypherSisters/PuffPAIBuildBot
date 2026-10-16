"""'What can you do?' in chat is short, about the goal, and agrees with itself.

For a 10-year-old's space bot, the first answer was about
40 lines - 11 can-do lines, 17 can't-do lines, the model id, a host name and a
double period. It listed a daily update under can-do AND can't-do, and said
7Timer was "ready to add" after the build's own check had refused it.
"""
from __future__ import annotations

import re

from puffpai import build_abilities as ba
from puffpai import feeds, plain_words, self_knowledge

GOAL = ("I want a space bot that knows everything about space, shows me cool "
        "space stuff, and works with my smart telescope")
MANIFEST = {"brain": {"model": "qwen2.5:7b-instruct"},
            "posture": {"run_egress": "enumerated"},
            "persona": {"goal": GOAL}}


def _rows():
    A, OFF, NO = ba.AVAILABLE, ba.NOT_SET_UP, ba.NOT_IN_THIS_BUILD
    return [
        ba.row("app:stargazerbot", "Use the StargazerBot app: its own page", A),
        ba.row("knowledge_library", "Look things up in an offline library on "
               "this computer. I will tell you how long it takes once I have "
               "timed some of it.", A),
        ba.row("knowledge.wikipedia", "Fetch Wikipedia articles about a "
               "subject and keep them as a book, when you say yes", A),
        ba.row("knowledge.gutenberg", "Fetch free old books from Project "
               "Gutenberg, when you say yes", A),
        ba.row("knowledge.openstax", "Fetch free OpenStax textbooks, when you "
               "say yes", A),
        ba.row("routines.feed_reader", "Send you messages on a schedule, like "
               "a daily update or a reminder", A),
        ba.row("live:ll2", "Show live Launch Library 2 on a card", A),
        ba.row("live:nasa", "Show live NASA Image and Video Library on a card", A),
        ba.row("reminders", "Say a reminder you wrote, at a time you pick", A),
        ba.row("knowledge.add_more", "Find and add more books", A),
        ba.row("looks", "Change how the dashboard looks from your words", A),
        ba.row("device_control", "Control smart-home devices like lights and "
               "plugs", OFF),
        ba.row("goal:smart-telescope", "Work with your smart telescope", NO),
        ba.row("page:instruments.telescope", "Work with telescope and sky "
               "feeds", NO),
        ba.row("routines.daily_update", "Send you a short update every day", OFF),
        ba.row("alerts", "Tell you when a live info number goes past a line "
               "you pick", OFF),
        ba.row("outside:geocode", "Use the outside website "
               "geocoding-api.open-meteo.com", OFF),
        ba.row("web_search", "Search the web when you ask me to", OFF),
        ba.row("outside:weather", "Get the weather forecast from a weather "
               "website", OFF),
    ]


def _answer():
    return "\n".join(self_knowledge.limits_summary(MANIFEST, _rows(),
                                                   for_chat=True))


def test_it_is_short():
    text = _answer()
    can = text.split("Things I can do now:")[1].split("Things I can't")[0]
    cannot = text.split("Things I can't do yet:")[1]
    assert len([ln for ln in can.splitlines() if ln.startswith("- ")]) <= 6
    assert len([ln for ln in cannot.splitlines() if ln.startswith("- ")]) <= 3
    assert len(text.splitlines()) <= 14, text


def test_no_model_id_no_host_names_no_double_period_and_plain():
    text = _answer()
    assert "qwen" not in text and "open-meteo" not in text
    assert not re.search(r"[a-z0-9-]+\.[a-z0-9-]+\.[a-z]{2,}", text)
    assert ".." not in text
    assert plain_words.check(text) == []


def test_it_does_not_say_one_thing_under_both_headings():
    text = _answer()
    assert "short update every day" not in text  # a schedule is a can-do


def test_the_goals_own_thing_is_the_first_cant():
    cannot = _answer().split("Things I can't do yet:")[1].splitlines()
    assert "smart telescope" in cannot[1]


def test_a_card_the_checks_stopped_is_not_ready_to_add(monkeypatch):
    from puffpai import drawbridge, feed_generator
    monkeypatch.setattr(feeds, "feed_apps", lambda: [])
    monkeypatch.setattr(feeds, "sync_doors", lambda *a, **k: None)
    monkeypatch.setattr(feed_generator, "list_drafts",
                        lambda: [{"id": "live_7timer", "name": "7Timer! forecast"}])
    monkeypatch.setattr(drawbridge, "latest_for",
                        lambda aid: {"verdict": drawbridge.BLOCK})
    rows = feeds.ability_rows()
    row = next(r for r in rows if r["id"] == "live:live_7timer")
    assert "ready to add" not in row["label"]
    assert "stopped by BuildBot's safety checks" in ba.absent_line(row)
