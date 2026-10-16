"""Lists nobody asked for no longer eat ordinary questions.

Two halves:

1. The wizard suggests a list only where the goal asks for one ("a planting
   list", "keep track of my harvests"). "A bot for my 2014 Honda Civic" used to
   become lists called 2014, honda and civic.
2. Builds that already have such lists: the list shortcut answers only when the
   list name ends the phrase ("show me my honda", "add a filter to my honda"),
   and "add me some ..." asks the bot for something rather than writing it down.

The three questions are the ones the acceptance strangers asked.
"""
from __future__ import annotations

import pytest

from puffpai import app_fastpath, build_interview as bi


def _bot(app_id, lists):
    return {"schema": "puffpai_app/v1", "id": app_id, "name": app_id,
            "data": {"default": {key: [] for key in lists}},
            "vocabulary": {"keywords": [app_id] + list(lists)}}


class Store:
    def __init__(self, documents=None):
        self.documents = documents or {}

    def get_data(self, app_id):
        return dict(self.documents.get(app_id, {}))

    def set_data(self, app_id, document):
        self.documents[app_id] = dict(document)
        return True


HONDA = _bot("hondabot", ["2014", "honda", "civic"])
GARDEN = _bot("gardenhelper", ["vegetable", "garden"])
KNIT = _bot("knitbuddy", ["knitting", "crochet", "buddy"])


@pytest.mark.parametrize("goal", [
    "I want a bot for my 2014 Honda Civic that knows maintenance and recalls",
    "a garden helper for my vegetable beds",
    "a knitting and crochet buddy that knows cross stitch too",
    "I want a space bot that knows everything about space, shows me cool "
    "space stuff, and works with my smart telescope",
])
def test_a_goal_that_asks_for_no_list_gets_no_list(goal):
    assert bi.suggested_lists(goal) == []


@pytest.mark.parametrize("goal,lists", [
    ("keep a planting list and tell me the frost", ["planting"]),
    ("a list of my seeds and keep track of my harvests", ["seeds", "harvests"]),
    ("a family calendar and a shopping list", ["shopping"]),
])
def test_a_goal_that_asks_for_a_list_gets_that_list(goal, lists):
    assert bi.suggested_lists(goal) == lists


@pytest.mark.parametrize("manifest,message", [
    (HONDA, "what maintenance does my 2014 Honda Civic need at 100000 miles?"),
    (GARDEN, "what does my vegetable garden need this week?"),
    (KNIT, "add me some cross stitch tips too"),
    (HONDA, "show me my honda civic's recall history"),
])
def test_the_strangers_questions_go_to_the_model(manifest, message):
    assert app_fastpath.handle(message, Store(), manifests=[manifest]) is None


def test_the_lists_still_answer_when_they_are_what_is_meant():
    store = Store({"hondabot": {"honda": []}})
    assert app_fastpath.handle("add oil filter to my honda", store,
                               manifests=[HONDA]).startswith("Added oil filter")
    assert "oil filter" in app_fastpath.handle("show me my honda", store,
                                               manifests=[HONDA])
    assert "oil filter" in app_fastpath.handle("what is on the honda list?",
                                               store, manifests=[HONDA])
