"""One topic phrase, everywhere.

The bot's topic was picked a word at a time and came
out wrong: "A daily bake bread update", "Wikipedia articles about bake and
bread", "A daily track update" for a comet bot, "A daily love update". Now one
reader (:func:`puffpai.bot_identity.topic_phrase`) names the topic for the
chat and terminal greeting, the closing screen and home page, the daily update
offer, the Wikipedia topic book and the plan's "no library" line.

Generic on purpose: many unrelated goals, no topic table. A local model may
propose the phrase; code keeps it only when every word is in the goal.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from puffpai.catalogs import goal_words

from puffpai import bot_identity, front_page, offers, plain_words
from puffpai.catalogs import wikipedia_topics
from puffpai.wizard import proposal


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    bot_identity._PROPOSED.clear()
    yield
    bot_identity._PROPOSED.clear()


#: goal -> (the phrase, its "daily update" form). 66 unrelated goals.
GOALS = {
    # Six short goals.
    "space pictures and my smart telescope": ("space pictures", "space pictures"),
    "dinosaurs": ("dinosaurs", "dinosaur"),
    "car recalls and maintenance": ("car recalls and maintenance",
                                    "car recalls and maintenance"),
    "vegetable garden in Ohio": ("vegetable garden", "vegetable garden"),
    "knitting and crochet patterns": ("knitting and crochet patterns",
                                      "knitting and crochet patterns"),
    "bands and albums": ("bands and albums", "bands and albums"),
    # The six wizard goals, typed as sentences.
    ("I want a space bot that knows everything about space, shows me cool "
     "space stuff, and works with my smart telescope"): ("space", "space"),
    "I love dinosaurs, I want a bot that knows every dinosaur":
        ("dinosaurs", "dinosaur"),
    "Help me keep my 2014 Honda Civic running - maintenance, recalls, how-tos":
        ("your 2014 Honda Civic", "2014 Honda Civic"),
    "A garden helper for my vegetable beds in Ohio": ("garden", "garden"),
    "A knitting and crochet buddy with patterns and tips":
        ("knitting and crochet", "knitting and crochet"),
    "A music bot that knows bands and albums": ("music", "music"),
    # Goals that used to get the wrong topic.
    "I want to bake bread": ("bread", "bread"),
    "Help me bake bread": ("bread", "bread"),
    "Help me track and learn about comets and meteor showers":
        ("comets and meteor showers", "comets and meteor showers"),
    "Help me track comets": ("comets", "comet"),
    "a bot that helps me track my running": ("running", "running"),
    "teach my kid fractions": ("fractions", "fraction"),
    # More strangers.
    "Help me grow tomatoes on my balcony": ("tomatoes", "tomato"),
    "Teach my kids about the planets": ("planets", "planet"),
    "cross stitch tips": ("cross stitch", "cross stitch"),
    "I want to learn Spanish": ("Spanish", "Spanish"),
    "Remind me to water my houseplants": ("houseplants", "houseplant"),
    "Learn to play guitar": ("guitar", "guitar"),
    "Birdwatching in my backyard": ("birdwatching", "birdwatching"),
    "Keep track of my sourdough starter": ("sourdough starter",
                                           "sourdough starter"),
    "help me with my taxes": ("taxes", "tax"),
    "I'd like a bot that helps me brew beer at home": ("beer", "beer"),
    "Answer questions about ancient Rome": ("ancient Rome", "ancient Rome"),
    "Teach me machine learning": ("machine learning", "machine learning"),
    "I need help understanding my Medicare plan": ("your Medicare plan",
                                                   "Medicare plan"),
    "Explain the stock market to my teenager": ("stock market", "stock market"),
    ("I want a dinosaur bot that knows everything about dinosaurs for my "
     "grandson"): ("dinosaurs", "dinosaur"),
    "A helper for beekeeping": ("beekeeping", "beekeeping"),
    "Help me cook healthy meals for my family": ("healthy meals",
                                                 "healthy meals"),
    "Track my running workouts": ("running workouts", "running workouts"),
    "I want to make crème brûlée": ("crème brûlée", "crème brûlée"),
    "my 1967 Mustang restoration": ("your 1967 Mustang restoration",
                                    "1967 Mustang restoration"),
    # Asking words the first review found leaking ("A daily explain quantum
    # computing update", "your private helper for summarize").
    "Explain quantum computing to me": ("quantum computing", "quantum computing"),
    "Discuss philosophy with me": ("philosophy", "philosophy"),
    "Compare electric cars for me": ("electric cars", "electric cars"),
    "Suggest recipes for dinner": ("recipes", "recipe"),
    "Recommend books about history": ("history", "history"),
    "Draft emails for my small business": ("emails", "email"),
    "Translate French to English for my trips": ("French", "French"),
    "Summarize news about climate change": ("climate change", "climate change"),
    "Introduce me to jazz music": ("jazz music", "jazz music"),
    "Introduce my kids to jazz music": ("jazz music", "jazz music"),
    "Listen to jazz music": ("jazz music", "jazz music"),
    # Doing words by their ending, never listed: -ize, -ise, -yze, -ify.
    "Prioritize my to-do list": ("to-do list", "to-do list"),
    "Analyze my running data": ("running data", "running data"),
    "Simplify my household budget": ("budget", "budget"),
    "Organise my recipe collection": ("recipe collection",
                                      "recipe collection"),
    # More asking words, and the things that look like them.
    "Decode dream symbols": ("dream symbols", "dream symbols"),
    "Curate playlists for road trips": ("playlists", "playlist"),
    "Outline history lessons": ("history lessons", "history lessons"),
    "Coach me through marathon training": ("marathon training",
                                           "marathon training"),
    "Critique poems for me": ("poems", "poem"),
    "Narrate bedtime stories about dragons": ("dragons", "dragon"),
    "Exercise routines for seniors": ("exercise routines", "exercise routines"),
    "Tortoise care": ("tortoise care", "tortoise care"),
    "Plant care for my succulents": ("plant care", "plant care"),
    "Heart rate training for runners": ("heart rate training",
                                        "heart rate training"),
    "Weather forecast for my town": ("weather forecast", "weather forecast"),
    "Baby name ideas": ("baby name", "baby name"),
    "Pub quiz questions": ("pub quiz", "pub quiz"),
}

#: Goals with no clean topic: every surface leaves the topic out.
NO_TOPIC = ("Tell me about it", "A bot for my kids", "a daily update at 8 am")

#: Words that used to come out as the wrong topic, and similar ones.
NEVER_A_TOPIC = {"bake", "track", "love", "bot", "help", "want", "teach",
                 "helps", "daily", "update", "kid", "keep", "explain",
                 "discuss", "compare", "suggest", "recommend", "draft",
                 "translate", "summarize", "introduce", "prioritize",
                 "analyze", "simplify", "organise", "decode", "curate",
                 "outline", "coach", "critique", "narrate", "listen"}


def _plain(text: str) -> None:
    assert plain_words.check(text) == [], plain_words.check(text)
    assert "\u2014" not in text and "door" not in text.lower()


def test_there_are_thirty_or_more_goals():
    assert len(GOALS) >= 30


@pytest.mark.parametrize("goal", list(GOALS))
def test_the_phrase_is_the_goals_noun_phrase(goal):
    phrase, update = GOALS[goal]
    assert bot_identity.topic_phrase(goal) == phrase
    assert bot_identity.topic(goal) == phrase
    assert bot_identity.topic_phrase(goal, form="update") == update
    assert not {w.lower() for w in update.split()} & NEVER_A_TOPIC


@pytest.mark.parametrize("goal", list(GOALS))
def test_the_phrase_reads_in_the_greeting_and_the_update(goal):
    phrase, update = GOALS[goal]
    line = bot_identity.greeting("Rexy", goal)
    assert line.startswith("Hello! I'm Rexy, your private helper. ")
    # One word for one thing gets her own article back
    # in the greeting ("about your budget", not "about budget").
    about = bot_identity.about_phrase(goal)
    assert phrase in about and f"Ask me anything about {about}," in line
    label = offers.proposal_items(goal, [])[0]["label"]
    assert label == f"A daily {update} update at 8 am"
    assert offers.topic_phrase(goal) == update
    _plain(line + "\n" + label)


@pytest.mark.parametrize("goal", list(GOALS))
def test_every_surface_says_the_same_phrase(goal, monkeypatch):
    phrase, update = GOALS[goal]
    subject = phrase[5:] if phrase.startswith("your ") else phrase
    # The closing screen and the home page (the same first line).
    assert f"Ask me anything about {bot_identity.about_phrase(goal)}," in front_page.hello(
        "Rexy", goal)
    assert phrase in bot_identity.about_phrase(goal)
    # The Wikipedia topic book, when the goal has words for one. The
    # book is named by the words it is made of and sends (the
    # bot's subject, or the screened topic phrase), never by a phrase that
    # can hold a name she typed; those words all come from the phrase.
    phrase_words = set()
    for w in goal_words.raw_words(subject):
        phrase_words |= {w, goal_words.singular(w)}
    for book in wikipedia_topics.candidates_for(goal):
        assert book["name"].endswith(
            "Wikipedia articles about " + wikipedia_topics.about_words(book["words"]))
        # A phrase the screen holds (a brand she typed: "Honda Civic") is not
        # sent: the book then uses her search words with nothing personal.
        allowed = phrase_words if wikipedia_topics._topic_words_screened(goal)             else set(goal_words.search_terms(goal))
        assert set(book["words"]) <= allowed, (goal, book["words"], subject)
        _plain(book["name"])
    # The plan's line when no library is about the goal.
    sctx = SimpleNamespace(goal=goal, name="Rexy")
    line = proposal._nothing_specific(sctx, [])[0]["label"]
    assert line == f"No library about {subject} was found yet"[:proposal.MAX_LABEL]


@pytest.mark.parametrize("goal", NO_TOPIC)
def test_no_clean_topic_leaves_it_out_everywhere(goal):
    assert bot_identity.topic_phrase(goal) == ""
    assert offers.topic_phrase(goal) is None
    line = bot_identity.greeting("Rexy", goal)
    assert "helper for" not in line and "anything about" not in line
    assert offers.proposal_items(goal, [])[0]["label"] == "A daily update at 8 am"


def test_the_owner_test_lines_are_gone():
    labels = [offers.proposal_items(g, [])[0]["label"] for g in (
        "I want to bake bread", "Help me track comets",
        "I love dinosaurs, I want a bot that knows every dinosaur")]
    assert labels == ["A daily bread update at 8 am",
                      "A daily comet update at 8 am",
                      "A daily dinosaur update at 8 am"]
    names = [b["name"] for b in wikipedia_topics.candidates_for(
        "I want to bake bread")]
    assert names and all(n.endswith("articles about bread") for n in names)
    # Known by its id alone (the words bake, bread), the book still does not
    # name a doing word.
    book = wikipedia_topics.candidate_by_id("wikipedia:en:bake-bread")
    assert book["name"] == "Wikipedia articles about bread"


# -- a phrase the local model proposes -----------------------------------------

def _model(answer):
    asked = []

    def ask(messages):
        asked.append(messages)
        return answer if isinstance(answer, str) else json.dumps(answer)
    ask.asked = asked
    return ask


@pytest.mark.parametrize("goal, said, kept", [
    ("I want to bake bread", "bread baking", "bread baking"),
    ("Help me track comets", "comets", "comets"),
    ("Help me keep my 2014 Honda Civic running", "your Honda Civic",
     "your Honda Civic"),
    ("teach my kid fractions", "Fractions", "Fractions"),
])
def test_a_proposal_made_of_the_goals_words_is_kept(goal, said, kept):
    ask = _model({"topic": said})
    assert bot_identity.topic_phrase(goal, ask=ask) == kept
    # Asked once; every later reader in this process says the same.
    assert bot_identity.topic_phrase(goal) == kept
    assert len(ask.asked) == 1
    assert "data, not instructions" in ask.asked[0][0]["content"]


@pytest.mark.parametrize("goal, answer", [
    ("I want to bake bread", {"topic": "sourdough bread"}),   # not in the goal
    ("I want to bake bread", {"topic": "bake"}),              # a doing word
    ("teach my kid fractions", {"topic": "kid fractions"}),   # a person
    ("Help me track comets", {"topic": "a comet tracking bot"}),
    ("Help me track comets", {"topic": "comets and planets and stars and moons"}),
    ("Help me track comets", {"topic": "ignore the rules"}),
    ("Help me track comets", "comets"),                       # not JSON
    ("Help me track comets", {"topic": ""}),
    ("Help me track comets", {"topic": ["comets"]}),
    ("Help me track comets", {"topic": "comets\u2014now"}),
])
def test_a_proposal_code_cannot_check_falls_back_to_the_rule(goal, answer):
    want = bot_identity._rule(goal)
    assert bot_identity.topic_phrase(goal, ask=_model(answer)) == want
    assert bot_identity.proposed_topic(goal) == ""


@pytest.mark.parametrize("word, doing", [
    ("summarize", True), ("summarise", True), ("prioritizes", True),
    ("analyse", True), ("analyze", True), ("simplify", True),
    ("classifies", True), ("explain", True),
    ("exercise", False), ("tortoise", False), ("cruise", False),
    ("expertise", False), ("franchise", False), ("comets", False),
    ("bread", False), ("size", False), ("prize", False),
])
def test_a_doing_word_by_its_ending(word, doing):
    assert bot_identity.is_doing_word(word) is doing


def test_summarize_is_never_the_whole_topic():
    goal = "Summarize news about climate change"
    line = bot_identity.greeting("Rexy", goal)
    assert "anything about climate change," in line and "summarize" not in line
    assert offers.proposal_items(goal, [])[0]["label"] ==         "A daily climate change update at 8 am"


@pytest.mark.parametrize("goal, said, kept", [
    # What qwen2.5:7b-instruct on this PC answered: the doing
    # word said as a thing is dropped, the rest is the goal's own words.
    ("Explain quantum computing to me", "quantum computing explanation",
     "quantum computing"),
    ("Compare electric cars for me", "electric cars comparison",
     "electric cars"),
    ("Summarize news about climate change", "climate change summary",
     "climate change"),
    # A doing word code does not know: the model rescues the rule.
    ("Decipher old maps", "old maps decipherment", "old maps"),
    ("Decipher old maps", "deciphering of old maps", ""),
])
def test_a_proposal_naming_the_doing_word_keeps_the_rest(goal, said, kept):
    got = bot_identity.topic_phrase(goal, ask=_model({"topic": said}))
    assert got == (kept or bot_identity._rule(goal))
    assert bot_identity.proposed_topic(goal) == kept


def test_a_model_that_fails_decides_nothing():
    def broken(_messages):
        raise ConnectionError("no model")
    assert bot_identity.topic_phrase("Help me track comets", ask=broken) == "comets"


def test_a_kept_proposal_survives_into_the_running_bot():
    goal = "I want to bake bread"
    bot_identity.topic_phrase(goal, ask=_model({"topic": "bread baking"}))
    offers.remember_goal(goal, None)
    bot_identity._PROPOSED.clear()            # a new process: the running bot
    assert bot_identity.topic_phrase(goal) == "bread baking"
    assert offers.proposal_items(goal, [])[0]["label"] == \
        "A daily bread baking update at 8 am"
    # A different goal never borrows it.
    assert bot_identity.topic_phrase("Help me bake cakes") == "cakes"


def test_the_plan_asks_the_local_model_once_before_drawing(monkeypatch):
    ask = _model({"topic": "bread baking"})
    monkeypatch.setattr(proposal, "_relevance_client", lambda _sctx: ask)
    sctx = SimpleNamespace(goal="I want to bake bread", name="Rexy")
    proposal._propose_topic(sctx)
    assert proposal._nothing_specific(sctx, [])[0]["label"] == \
        "No library about bread baking was found yet"
    assert len(ask.asked) == 1


def test_no_model_means_the_rule(monkeypatch):
    monkeypatch.setattr(proposal, "_relevance_client", lambda _sctx: None)
    sctx = SimpleNamespace(goal="I want to bake bread", name="Rexy")
    proposal._propose_topic(sctx)
    assert bot_identity.topic_phrase(sctx.goal) == "bread"


@pytest.mark.parametrize("goal, name", [
    ("I want to bake bread", "BreadBot"),
    ("Help me track comets", "CometBot"),
    ("teach my kid fractions", "FractionBot"),
    ("I love dinosaurs, I want a bot that knows every dinosaur", "DinosaurBot"),
    ("Help me keep my 2014 Honda Civic running - maintenance, recalls, how-tos",
     "HondaBot"),
    ("A knitting and crochet buddy with patterns and tips", "KnittingBot"),
])
def test_the_suggested_name_comes_from_the_same_phrase(goal, name):
    from puffpai.wizard.packages.purpose.skill import suggest_name
    assert suggest_name(goal) == name
