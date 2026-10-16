"""The app fastpath needs a LIST operation, not a goal word.

On a GardenBot build, "what is the greenhouse night temperature and the
code word?" was answered:

    Which list - plantings, harvest log or seed inventory?

The sentence is a question about a greenhouse and a vault word. It was grabbed
because "greenhouse" is one of the app's vocabulary keywords - and a scaffolded
persona's keywords are DERIVED FROM THE GOAL, so they are exactly the words that
appear in every ordinary question somebody asks a garden bot. An app keyword is
not evidence of a list operation.

The second half of this file is the ask-back that should have been an answer:
"which lists do you keep for me?" is a question the build can answer from its own
manifests and stores, and it was returning the ambiguous-list question instead -
answering a question with the question.

The 25 ordinary sentences are the regression net. Every ask-back this module has
is re-checked against them, because each one was added to stop a sentence falling
through to the model and each one is a chance to grab a sentence that should.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import pytest

from puffpai import app_fastpath

#: The build: one scaffolded persona, three lists, and a vocabulary
#: derived from "a greenhouse bot for my garden".
GARDENBOT = {
    "schema": "puffpai_app/v1",
    "id": "gardenbot",
    "name": "GardenBot",
    "data": {"default": {"plantings": [], "harvest_log": [],
                         "seed_inventory": []}},
    "vocabulary": {"keywords": ["gardenbot", "plantings", "harvest log",
                                "seed inventory", "greenhouse", "garden"]},
}

#: Ordinary things somebody says to a garden bot. Not one of them asks for a list
#: operation, so not one of them is this module's business.
ORDINARY = [
    "what is the greenhouse night temperature and the code word?",
    "what is the weather like today",
    "will it frost tonight",
    "tell me about my garden",
    "what is a greenhouse",
    "how do I keep aphids off tomatoes",
    "why are my basil leaves turning yellow",
    "what can I cook with zucchini",
    "give me a recipe for zucchini bread",
    "how much water does a tomato plant need",
    "summarise my garden so far",
    "when should I start seeds indoors",
    "is it too late to plant garlic",
    "what is the difference between a greenhouse and a cold frame",
    "do you know anything about companion planting",
    "how hot does it get in a greenhouse in july",
    "what do you think of my garden plan",
    "tell me a joke about farming",
    "who invented the greenhouse",
    "what time is sunset",
    "read me the news",
    "how many days until the first frost",
    "what is on the roof of the greenhouse",
    "show me the garden",
    "I watered the tomatoes today",
]


class Store:
    """The app store, in memory. Same two methods AppData exposes."""

    def __init__(self, documents=None):
        self.documents = documents or {}

    def get_data(self, app_id):
        return self.documents.setdefault(app_id, {})

    def set_data(self, app_id, document):
        self.documents[app_id] = document
        return True


def handle(message, store=None, manifests=None):
    return app_fastpath.handle(message, store or Store(),
                              manifests if manifests is not None else [GARDENBOT])


# ── 25 ordinary sentences, and not one of them is ours ──────────────────────

@pytest.mark.parametrize("message", ORDINARY)
def test_an_ordinary_sentence_is_left_to_the_model(message):
    assert handle(message) is None, message


@pytest.mark.parametrize("message", ORDINARY)
def test_the_same_holds_for_a_build_with_exactly_one_list(message):
    """One list used to be treated as "unambiguous", which skipped the gate."""
    one = dict(GARDENBOT)
    one["data"] = {"default": {"plantings": []}}
    assert handle(message, manifests=[one]) is None, message


def test_a_goal_keyword_alone_is_not_a_list_operation():
    assert app_fastpath._list_operation("what is the greenhouse temperature") is False
    assert app_fastpath._list_operation("what is on my list") is True
    assert app_fastpath._list_operation("add basil") is True


# ── the grammar that IS ours still works ───────────────────────────────────

def test_adding_to_a_named_list_still_writes_it_down():
    store = Store()
    reply = handle("add tomatoes to the plantings list", store)
    assert reply is not None and "Added tomatoes to the plantings list" in reply
    assert store.documents["gardenbot"]["plantings"][0]["item"] == "tomatoes"


def test_reading_a_named_list_still_works():
    store = Store({"gardenbot": {"plantings": [{"item": "basil"}]}})
    assert handle("what is on the plantings list", store) == \
        "On the plantings list: basil."


def test_a_named_list_with_no_verb_still_asks_what_to_do():
    reply = handle("basil for the plantings list")
    assert reply is not None and "what would you like me to do with it?" in reply


def test_a_bare_imperative_still_asks_which_list():
    reply = handle("add basil")
    assert reply is not None and reply.startswith("Which list - ")
    assert "seed inventory" in reply


def test_the_bare_word_list_still_asks_which_one():
    reply = handle("show me the list")
    assert reply is not None and reply.startswith("Which list - ")


# ── "which lists do you keep for me?" is answered, not asked back ──────────

@pytest.mark.parametrize("message", [
    "which lists do you keep for me?",
    "what lists do you keep?",
    "what lists are there?",
    "what do you keep track of?",
    "how many lists do you keep?",
])
def test_the_inventory_question_names_the_lists(message):
    store = Store({"gardenbot": {"plantings": [{"item": "basil"},
                                               {"item": "tomatoes"}],
                                 "harvest_log": [],
                                 "seed_inventory": [{"item": "peas"}]}})
    reply = handle(message, store)

    assert reply is not None, message
    assert not reply.startswith("Which list"), reply
    assert "I keep 3 lists for GardenBot" in reply
    assert "plantings (2 things on it)" in reply
    assert "harvest log (empty)" in reply
    assert "seed inventory (one thing on it)" in reply


def test_the_inventory_counts_come_off_disk_not_out_of_the_manifest():
    store = Store({"gardenbot": {"plantings": [{"item": "basil"}]}})
    reply = handle("which lists do you keep?", store)
    assert reply is not None and "plantings (one thing on it)" in reply


def test_an_unreadable_store_is_reported_as_unreadable_not_as_empty():
    class Broken(Store):
        def get_data(self, app_id):
            raise OSError("disk")

    reply = handle("which lists do you keep?", Broken())
    assert reply is not None and "could not read it off disk" in reply
    assert "empty" not in reply


def test_the_inventory_names_the_app_when_two_personas_keep_lists():
    other = {"schema": "puffpai_app/v1", "id": "beebot", "name": "BeeBot",
             "data": {"default": {"hive_checks": []}},
             "vocabulary": {"keywords": ["beebot", "hive"]}}
    reply = handle("what lists do you keep?", Store(),
                   manifests=[GARDENBOT, other])
    assert reply is not None
    assert "GardenBot's plantings" in reply and "BeeBot's hive checks" in reply


def test_a_hand_written_apps_words_still_win():
    """FreshTable owns "shopping list"; this module does not answer for it."""
    fresh = {"schema": "puffpai_app/v1", "id": "freshtable",
             "name": "FreshTable", "data": {"default": {"shopping": []}},
             "vocabulary": {"keywords": ["shopping list", "pantry"]}}
    assert handle("what lists do you keep for my shopping list",
                  manifests=[GARDENBOT, fresh]) is None


def test_a_build_with_no_declarative_app_answers_nothing_here():
    assert handle("which lists do you keep?", manifests=[]) is None
