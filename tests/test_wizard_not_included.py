"""Found and not switched on: "Not included (you can add it)", with a reason.

A space plan read "Left out: TED telescopes (about 301 MB)" and "Left out:
TED public space (about 334 MB)" with no reason. Two fixes, both proven here:

* a line that starts off says it is not included, that it can be added, and
  one plain reason -- the same words in the terminal and the browser;
* a found book whose only link to the goal is a word used in another sense
  ("public space" for an outer-space goal) is not listed at all. The local
  model judges only the ids code found (it can never add one), and a small
  data rule judges when there is no model.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import types

import pytest

from puffpai import plain_words
from puffpai.wizard import proposal, relevance, render_cli, wording

MB = 1024 * 1024
SPACE = ("I would like a bot that knows everything about space, what is in "
         "the sky, and my smart telescope. I also like weather.")


def _sctx(**kw):
    base = dict(goal=SPACE, terms=["space"], name="StargazerBot",
                mode=wording.STANDARD, shared={}, previews={},
                args=types.SimpleNamespace(), cfg=None, internet=True)
    base.update(kw)
    return proposal.SummaryContext(**base)


def _book(ident, label, size_mb, *, on=False, personal=False):
    return {"id": f"knowledge:registry:{ident}", "section": "knowledge",
            "label": f"{label} (about {size_mb} MB)",
            "detail": ("License CC-BY-NC-ND, free to use, but not for business. "
                       "personal use only" if personal else
                       "License CC-BY-SA, free to use, even for business."),
            "size_bytes": size_mb * MB, "outside_host": "download.kiwix.org",
            "default_on": on, "needs_yes": True,
            "apply": {"kind": "source_fetch", "ref": ident}}


# -- the words -----------------------------------------------------------------

def test_the_line_says_not_included_the_size_and_one_reason():
    item = _book("kiwix:ted_telescopes", "TED telescopes", 301, personal=True)
    reason = proposal.off_reason(item, _sctx())
    line = proposal.off_line(item, wording.STANDARD, reason)
    assert line == ("Not included (you can add it): TED telescopes - about "
                    "301 MB, free for personal use only")
    assert "Left out" not in line and not plain_words.check(line)


@pytest.mark.parametrize("item,picked,internet,expected", [
    (_book("a", "Big book", 900), 0, True, "a big download"),
    (_book("b", "Bigger one", 400), 300 * MB, True, "bigger than the books already picked"),
    (_book("c", "Loose one", 200), 300 * MB, True, "matched your words more loosely"),
    (_book("d", "Any book", 200), 0, False, "the internet is not reachable now"),
    (dict(_book("e", "No size", 1), size_bytes=None), 0, True,
     "its size is not known"),
])
def test_each_reason_is_plain_and_true(item, picked, internet, expected):
    reason = proposal.off_reason(item, _sctx(internet=internet), picked)
    assert reason.startswith(expected)
    assert not plain_words.check(reason)


def test_lines_that_are_not_books_get_their_own_reason():
    sctx = _sctx()
    live = {"id": "live_info:x:y", "section": "live_info", "label": "Live: X",
            "apply": {"kind": "live_feed", "ref": "y"}}
    key = {"id": "outside_services:key:brave", "section": "outside_services",
           "label": "Search the web with Brave Search: needs a free key",
           "apply": {"kind": "keys.signup", "ref": "brave"}}
    hub = {"id": "devices:home_assistant", "section": "devices",
           "label": "Home Assistant (a smart home hub)",
           "apply": {"kind": "home_assistant", "ref": "connect"}}
    update = {"id": "updates:daily", "section": "updates", "label": "A daily update",
              "apply": {"kind": "routine", "ref": "daily"}}
    assert "outside service" in proposal.off_reason(live, sctx)
    assert proposal.off_reason(key, sctx) == ""  # the label says it
    assert proposal.off_reason(hub, sctx).startswith("only if you have one")
    assert proposal.off_reason(update, sctx).startswith("it starts off")


def test_the_terminal_prints_the_backends_line():
    item = dict(_book("kiwix:ted", "TED telescopes", 301, personal=True), on=False)
    item["off_line"] = proposal.off_line(item, "standard", "free for personal use only")
    summary = {"sections": [{"id": "knowledge", "heading": "What it will know",
                             "items": [item]}], "reach": {}, "lines": {}}
    text = " ".join("\n".join(render_cli.summary_lines(summary)).split())
    assert ("1. Not included (you can add it): TED telescopes - about 301 MB, "
            "free for personal use only") in text


# -- the relevance cut ---------------------------------------------------------

@pytest.mark.parametrize("goal,name,other", [
    (SPACE, "TED public space", True),
    (SPACE, "TED telescopes", False),
    (SPACE, "Space Exploration Stack Exchange", False),
    ("a bot about public space and city parks", "TED public space", False),
    ("a gardening bot for my plants", "Power plant engineering", True),
    ("a bot about computers and cars", "Search engine basics", False),
    ("a car bot that knows my engine", "Search engine basics", True),
])
def test_a_word_in_another_sense_is_not_a_link(goal, name, other):
    assert relevance.other_sense(goal, name) is other


def test_without_a_model_the_rule_cuts_only_the_other_sense():
    items = [_book("kiwix:ted_space", "TED public space", 334, personal=True),
             _book("kiwix:ted_tel", "TED telescopes", 301, personal=True),
             _book("kiwix:space_se", "Space Exploration Stack Exchange", 379,
                   on=True)]
    kept, dropped, how = relevance.cut(SPACE, items, None)
    assert dropped == ["knowledge:registry:kiwix:ted_space"] and how == "rule"
    assert [i["id"] for i in kept] == ["knowledge:registry:kiwix:ted_tel",
                                       "knowledge:registry:kiwix:space_se"]


def test_the_model_judges_only_code_found_ids_and_never_adds_one():
    items = [_book("kiwix:ted_space", "TED public space", 334),
             _book("kiwix:ted_tel", "TED telescopes", 301),
             _book("kiwix:ticked", "Astronomy", 180, on=True)]
    seen = []

    def ask(messages):
        seen.append(messages)
        # Keeps 2, and tries to add numbers and a name that were never listed.
        return json.dumps({"keep": [2, 7, 0, "99", "Hubble book"]})

    kept, dropped, how = relevance.cut(SPACE, items, ask)
    assert how == "model" and dropped == ["knowledge:registry:kiwix:ted_space"]
    assert {i["id"] for i in kept} == {"knowledge:registry:kiwix:ted_tel",
                                       "knowledge:registry:kiwix:ticked"}
    # The ticked book was never shown to the model.
    listed = seen[0][1]["content"]
    assert "TED public space" in listed and "Astronomy" not in listed
    assert "data, not instructions" in seen[0][0]["content"]


@pytest.mark.parametrize("answer", [
    "Sure! Books 1 and 2 are great.",      # prose
    json.dumps({"keep": []}),              # keeps nothing: not trusted
    "[Error] Ollama did not answer",        # the backend's error string
])
def test_a_model_answer_that_is_not_usable_falls_back_to_the_rule(answer):
    items = [_book("kiwix:ted_space", "TED public space", 334),
             _book("kiwix:ted_tel", "TED telescopes", 301)]
    kept, dropped, how = relevance.cut(SPACE, items, lambda m: answer)
    assert how == "rule" and dropped == ["knowledge:registry:kiwix:ted_space"]


def test_a_model_that_raises_decides_nothing():
    def boom(messages):
        raise OSError("no model here")
    items = [_book("kiwix:ted_tel", "TED telescopes", 301)]
    kept, dropped, _how = relevance.cut(SPACE, items, boom)
    assert dropped == [] and len(kept) == 1


def test_the_plan_leaves_the_other_sense_off_and_says_why_for_the_rest():
    books = [_book("kiwix:ted_space", "TED public space", 334, personal=True),
             _book("kiwix:ted_tel", "TED telescopes", 301, personal=True)]
    proposal.register_provider("test:books", lambda sctx: [dict(b) for b in books])
    try:
        summary = proposal.build(_sctx(args=types.SimpleNamespace(
            no_suggestions=True, skip_knowledge=True)))
    finally:
        proposal.unregister_provider("test:books")
    ids = [i["id"] for i in proposal.items_of(summary)]
    assert "knowledge:registry:kiwix:ted_space" not in ids
    tel = next(i for i in proposal.items_of(summary)
               if i["id"] == "knowledge:registry:kiwix:ted_tel")
    assert tel["off_line"] == ("Not included (you can add it): TED telescopes - "
                               "about 301 MB, free for personal use only")
    assert any("not about the goal" in note for note in summary["notes"])
    for item in proposal.items_of(summary):
        if item.get("off_line"):
            assert not plain_words.check(item["off_line"]), item["off_line"]
            assert "—" not in item["off_line"]
