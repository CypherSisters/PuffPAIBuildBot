"""add_intent: which chat sentences are add requests, and the reply grammar.

Positives come from several unrelated goals (the factory is generic); the
negatives include three private sentences, list sentences and
how-questions. Pure functions: no state, no network.
"""
from __future__ import annotations

import pytest

from puffpai import add_intent

POSITIVE = [
    # G1 space
    ("add space pictures to you", "space pictures"),
    ("can you add telescope star maps to yourself", "telescope star maps"),
    ("please put rocket launch times on my bot", "rocket launch times"),
    ("teach yourself about black holes", "black holes"),
    # G2 dinosaurs
    ("add dinosaur fossils to you", "dinosaur fossils"),
    ("add every dinosaur to Rexy", "every dinosaur"),
    ("could you add some dinosaur books to Rexy please", "dinosaur books"),
    ("can you learn about the Jurassic period", "Jurassic period"),
    ("I want to add fossil finds to my bot", "fossil finds"),
    # G3 car
    ("add car recalls to Wrench", "car recalls"),
    ("could you put a service schedule on this bot", "service schedule"),
    ("add a maintenance log to my bot", "maintenance log"),
    ("I'd like to add Honda Civic how-tos to you", "Honda Civic how-tos"),
    ("teach yourself more about brake pads", "brake pads"),
    # G4 garden
    ("add frost dates to my helper", "frost dates"),
    ("load vegetable growing guides into you", "vegetable growing guides"),
    ("let's add Ohio planting times to the bot", "Ohio planting times"),
    ("learn about tomato blight", "tomato blight"),
    # G5 knitting
    ("add knitting patterns to you", "knitting patterns"),
    ("can you also add crochet stitches to yourself", "crochet stitches"),
    ("please install yarn weight charts on my buddy", "yarn weight charts"),
    ("could you learn more about cable knitting", "cable knitting"),
    # G6 music
    ("add album reviews to my assistant", "album reviews"),
    ("add band histories to you too", "band histories"),
    ("will you add new music releases to yourself", "new music releases"),
    ("train yourself about punk bands", "punk bands"),
    # other goals
    ("add bird songs to you!", "bird songs"),
    ("stick Minecraft crafting recipes onto my bot", "Minecraft crafting recipes"),
    ("add the weather to your library", "weather"),
    ("can you teach yourself about bees?", "bees"),
]

NEGATIVE = [
    # privacy sentences: never a request to go and look
    "I want you to know that my son Jake is allergic to peanuts and has epilepsy",
    "I want you to have my divorce lawyer Susan Park number",
    "can you also tell me a joke",
    "I want you to know about dinosaurs",
    "tell me about dinosaurs",
    # list sentences name somewhere else
    "add milk to the shopping list",
    "add basil",
    "add a maintenance log to the garage list",
    "put eggs on my grocery list",
    "add 2 cups flour to the recipe",
    # how-questions are answered by the bot's description of itself
    "how do I add more to you",
    "how can I add knitting patterns to you?",
    "what can I add to you",
    "is it possible to add car recalls to you",
    # small talk and questions
    "hello",
    "what is the biggest dinosaur?",
    "thanks!",
    "add it to you",
    "add that to my bot",
    "add a to you",
    "add stuff to you",
    "learn about it",
    "",
    "   ",
]


@pytest.mark.parametrize("message,words", POSITIVE)
def test_add_requests_are_recognised(message, words):
    found = add_intent.detect(message, bot_name="Rexy" if "Rexy" in message
                              else "Wrench")
    assert found is not None, message
    assert found["words"] == words


@pytest.mark.parametrize("message", NEGATIVE)
def test_everything_else_falls_through(message):
    assert add_intent.detect(message, bot_name="Rexy",
                             ability_missing=lambda w: True) is None


@pytest.mark.parametrize("message,words", [
    # A user's own words (this went to the model, which said
    # "Sure, I can add some cross stitch tips" and nothing was added)
    ("add me some cross stitch tips too", "cross stitch tips"),
    ("add us some bird songs", "bird songs"),
    ("can you add me some knitting patterns please", "knitting patterns"),
    ("please add me some tips on the history of quilting",
     "tips on the history of quilting"),
])
def test_add_me_with_no_destination_is_an_add_request(message, words):
    found = add_intent.detect(message, bot_name="Loopy",
                              is_list_name=lambda t: False)
    assert found == {"words": words, "shape": "add"}


@pytest.mark.parametrize("message", [
    "add me to the family calendar",
    "add me as a user",
    "add me milk to the shopping list",
    "add me eggs on my shopping list",
    "add me eggs to my groceries",
    "add me some",
    "add me it",
    "how do I add me some tips",
])
def test_add_me_that_names_a_place_or_a_person_is_not_ours(message):
    found = add_intent.detect(message, bot_name="Loopy",
                              is_list_name=lambda t: t.lower() == "groceries")
    assert found is None


def test_the_bot_name_is_a_destination_only_for_this_bot():
    assert add_intent.detect("add frogs to Rexy", bot_name="Rexy")
    assert add_intent.detect("add frogs to Rexy", bot_name="Wrench") is None


def test_a_destination_that_is_one_of_the_owners_lists_is_never_taken():
    found = add_intent.detect("add frogs to Garden", bot_name="Garden",
                              is_list_name=lambda t: t.lower() == "garden")
    assert found is None


def test_show_me_also_only_when_the_bot_does_not_have_it():
    msg = "can you also show me the phases of the moon"
    assert add_intent.detect(msg) is None  # nobody said it is missing
    assert add_intent.detect(msg, ability_missing=lambda w: False) is None
    got = add_intent.detect(msg, ability_missing=lambda w: True)
    assert got == {"words": "phases of the moon", "shape": "show"}
    assert add_intent.detect("can you show me the moon",
                             ability_missing=lambda w: True) is None


REPLIES = [
    ("yes", [1, 2, 3], {"yes": [1, 2, 3]}),
    ("Yes please!", [1, 2, 3], {"yes": [1, 2, 3]}),
    ("all", [1, 2, 5], {"yes": [1, 2, 5]}),
    ("add them all", [1, 2], {"yes": [1, 2]}),
    ("both", [1, 2], {"yes": [1, 2]}),
    ("1", [1, 2, 3], {"yes": [1]}),
    ("just 1", [1, 2, 3], {"yes": [1]}),
    ("1 and 3", [1, 2, 3], {"yes": [1, 3]}),
    ("1, 3", [1, 2, 3], {"yes": [1, 3]}),
    ("yes 2 and 5", [1, 2, 5], {"yes": [2, 5]}),
    ("just the first", [1, 2, 3], {"yes": [1]}),
    ("the first and third", [1, 2, 3], {"yes": [1, 3]}),
    ("not 2", [1, 2, 3], {"yes": [1, 3]}),
    ("all but 2", [1, 2, 3], {"yes": [1, 3]}),
    ("all except 1", [1, 2], {"yes": [2]}),
    ("no", [1, 2], {"no": True}),
    ("no thanks", [1, 2], {"no": True}),
    ("nope", [1], {"no": True}),
    ("look online", [1, 2], {"online": True}),
    ("yes, look online too", [1, 2], {"online": True}),
    ("switch it on", [1], {"switch_on": True}),
    ("yes add it", [1], {"yes": [1]}),
]


@pytest.mark.parametrize("message,shown,expected", REPLIES)
def test_the_reply_grammar(message, shown, expected):
    assert add_intent.read_reply(message, shown) == expected


@pytest.mark.parametrize("message", [
    "4", "just 4", "1234", "12", "what about the moon?", "tell me more",
    "add dinosaurs to you", "one", "the one", "maybe"])
def test_anything_else_is_not_a_reply(message):
    # a number that was not shown (a PIN-shaped number included) is never a yes
    assert add_intent.read_reply(message, [1, 2, 3]) is None


def test_a_count_means_one_to_count():
    assert add_intent.read_reply("yes", 2) == {"yes": [1, 2]}
    assert add_intent.read_reply("3", 2) is None


@pytest.mark.parametrize("text,expected", [
    ("just 1", True), ("1 and 3", True), ("not 2", True), ("the second", True),
    ("yes", False), ("no", False), ("yes please", False), ("tell me a joke", False),
    ("1 cup of flour", False), ("", False),
])
def test_a_numbered_pick_is_only_a_pick_by_number(text, expected):
    assert add_intent.is_numbered_pick(text) is expected
