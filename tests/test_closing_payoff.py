"""The build ends with what the bot already has, not only homework.

The closing screen was start commands, a pairing code
"expiring 05:25 UTC" in front of an 8-year-old, and nothing that showed the
live cards just approved.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from puffpai import cli
from puffpai.wizard.packages.handoff import skill as handoff


def test_the_pairing_clock_is_local_and_plain():
    moment = datetime(2026, 9, 24, 5, 25, tzinfo=timezone.utc)
    text = cli._local_clock(moment)
    assert "UTC" not in text and text.endswith(("am", "pm"))


def _ctx(goal, name):
    sections = {"persona": {"interview": {"goal": goal}}}
    return SimpleNamespace(shared={"bot_name": name},
                           read=lambda section: sections.get(section, {}))


def test_the_bot_says_hello_in_the_owner_s_words():
    line = handoff.first_line(_ctx("I love dinosaurs, I want a bot that knows "
                                   "every dinosaur", "DinosaurBot"))
    assert line.startswith("Hi! I'm DinosaurBot. Ask me anything about dinosaurs,")
    line = handoff.first_line(_ctx("A knitting and crochet buddy", "KnittingBot"))
    assert "about knitting and crochet," in line

