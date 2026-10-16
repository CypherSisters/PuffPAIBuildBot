"""A bot knows its topic out of the box, and the plan lists books that fit.

In a run, in five of six builds every book was left out, so
DinosaurBot shipped knowing no dinosaurs; the space plan listed 25 lines
including Medical Sciences Q&A; the knitting plan found nothing at all.

Offline: the shipped library list only.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from puffpai.wizard import proposal, sources_plan

G1 = "I want a space bot that shows me cool space stuff and works with my smart telescope"
G2 = "I love dinosaurs, I want a bot that knows every dinosaur"
G3 = "Help me keep my 2014 Honda Civic running - maintenance, recalls, how-tos"
G5 = "A knitting and crochet buddy with patterns and tips"


@pytest.mark.parametrize("goal,book", [
    (G2, "kiwix:ted_mul_dinosaurs"),
    (G3, "kiwix:mechanics.stackexchange.com_en_all"),
    (G5, "kiwix:crafts.stackexchange.com_en_all"),
])
def test_the_best_small_match_starts_ticked(goal, book):
    plan = sources_plan.plan_rows(goal, [])
    assert sources_plan.default_selection(plan["items"], goal) == [book]


def test_only_one_book_is_ticked_and_never_a_big_one():
    plan = sources_plan.plan_rows(G1, [])
    picked = sources_plan.default_selection(plan["items"], G1)
    assert len(picked) == 1
    size = next(i["bytes"] for i in plan["items"] if i["id"] == picked[0])
    assert size <= sources_plan.DEFAULT_ON_BYTES


def test_a_match_on_a_model_word_alone_is_not_ticked():
    item = {"id": "kiwix:medical", "selectable": True, "bytes": 10_000_000,
            "matched_terms": ["science"], "selected_by": []}
    assert sources_plan.default_selection([item], G1) == []


def test_a_pack_the_owner_chose_still_wins():
    chosen = {"id": "a", "selectable": True, "bytes": 5, "matched_terms": [],
              "selected_by": ["pack:x"]}
    other = {"id": "b", "selectable": True, "bytes": 5,
             "matched_terms": ["dinosaurs"], "selected_by": []}
    assert sources_plan.default_selection([chosen, other], G2) == ["a"]


def _sctx(goal, items, selected=()):
    return SimpleNamespace(previews={"sources": {"items": items,
                                                 "selected": list(selected)}},
                           internet=True, args=SimpleNamespace(), goal=goal,
                           terms=[], mode="standard", name="StargazerBot")


def _row(ident, matched, size=100):
    return {"id": ident, "label": ident, "selectable": True, "bytes": size,
            "matched_terms": matched, "selected_by": [], "license": "CC-BY-SA-4.0",
            "what_it_is": "A book.", "kiwix_book": True}


def test_loose_matches_are_counted_not_listed():
    rows = ([_row(f"space{n}", ["space"]) for n in range(5)]
            + [_row(f"sci{n}", ["science"]) for n in range(4)]
            + [_row("wiki", [])])
    items = proposal._knowledge(_sctx(G1, rows))
    listed = [i["id"] for i in items if i["apply"]["kind"] == "source_fetch"]
    assert listed == ["knowledge:registry:space0", "knowledge:registry:space1",
                      "knowledge:registry:space2", "knowledge:registry:wiki"]
    more = next(i for i in items if i["id"] == "knowledge:more")
    assert more["label"] == "6 more books matched more loosely"
    assert "Find more knowledge" in more["detail"]


def test_no_book_about_the_goal_is_said_plainly():
    items = proposal._knowledge(_sctx("a zorbing helper",
                                      [_row("wiki", [])]))
    note = next(i for i in items if i["id"] == "knowledge:none_found")
    assert note["label"] == "No library about zorbing was found yet"
    assert "Documents page" in note["detail"] and note["apply"]["kind"] == "none"


def test_a_search_that_did_not_run_says_nothing_about_finding():
    assert proposal._knowledge(_sctx(G5, [])) == []
