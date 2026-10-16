"""Privacy: folded names and times.

Every probe here is offline: the outbound screen, the goal reader, a spied
online search and a live card's address are read on this computer, and
nothing is sent anywhere.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import re

import pytest

from puffpai.catalogs import goal_words as gw
from puffpai.catalogs import personal_words as pw
from puffpai.security import outbound_query as oq


def sent(goal: str, phrases: list[str]) -> list[str]:
    return [s.phrase for s in oq.screen(phrases, goal=goal).sendable]


def words_out(goal: str) -> set[str]:
    """Every word and pair of neighbours of the goal the screen lets out,
    as if a search phrase used it (the validator's h.py), plus the goal's
    own search words and its subject."""
    ws = re.findall(r"[a-z0-9']+", goal.lower())
    phrases = ws + [f"{a} {b}" for a, b in zip(ws, ws[1:])]
    out = {w for p in sent(goal, phrases) for w in p.split()}
    out |= {w for p in sent(goal, gw.search_terms(goal)) for w in p.split()}
    subject = oq.subject_sendable(goal)
    if subject is not None:
        out |= set(subject.phrase.split())
    return out


def leaks(goal: str, never: set[str]) -> set[str]:
    return {w for w in words_out(goal) if any(w == f or w.startswith(f) for f in never)}


# -- names typed with look-alike letters and invisible characters ----------------

@pytest.mark.parametrize("text,look,sound,drop", [
    ("DеShawn", "DeShawn", "DeShawn", "DShawn"),
    ("Li​am", "Liam", "Liam", "Liam"),
    ("Li­am", "Liam", "Liam", "Liam"),
    ("Maгia", "Maria", "Magia", "Maia"),
    ("Ꮮiam", "Liam", "Liam", "Iam"),
    ("Ｌｉａｍ", "Liam", "Liam", "Liam"),
    ("Kayłeigh", "Kayleigh", "Kayleigh", "Kayleigh"),
    ("Зoe", "3oe", "Zoe", "Oe"),
])
def test_fold_text_reads_look_alike_letters_and_drops_invisible_ones(text, look, sound, drop):
    assert gw.fold_text(text) == look
    assert gw.fold_text(text, other_letters="sound") == sound
    assert gw.fold_text(text, other_letters="drop") == drop


@pytest.mark.parametrize("goal,model,kept", [
    ("A soccer practice bot for DеShawn",
     ["dshawn soccer", "deshawn soccer", "shawn soccer", "de shawn soccer"], set()),
    ("A soccer practice bot for my son Li​am", ["liam soccer", "li am soccer"], set()),
    ("A soccer practice bot for my son Li­am", ["liam soccer"], set()),
    ("A reading bot for my daughter Maгia",
     ["maia reading", "magia reading", "maria reading", "reading tracker"], {"reading tracker"}),
    ("A chore bot for my kids l i a m", ["liam chores", "li am chores", "chore chart"],
     {"chore chart"}),
    ("A chore bot for my kids L.I.A.M", ["liam chores"], set()),
    ("A soccer practice bot for my kid Kayłeigh",
     ["kayleigh soccer", "kay eigh soccer", "eigh soccer", "kay leigh soccer"], set()),
    ("A soccer practice bot for Зoe", ["zoe soccer", "oe soccer"], set()),
    ("A soccer practice bot for my son Ꮮiam", ["liam soccer"], set()),
])
def test_a_name_in_any_form_is_held_in_every_reading(goal, model, kept):
    assert set(sent(goal, model)) == kept


def test_every_reading_of_a_name_is_a_piece_of_it_not_a_new_listed_name():
    found = pw.find("A reading bot for my daughter Maгia")
    assert [h["word"] for h in found.held] == ["maria"]
    assert {"magia", "maia"} <= found.pieces["maria"]


# -- the time of her plans: the whole class, whoever words it ---------------------

@pytest.mark.parametrize("goal,model", [
    ("A packing bot for our trip in the fall", ["autumn packing", "fall trip packing"]),
    ("A packing bot for our trip in the autumn", ["fall packing"]),
    ("A packing bot for our trip on Turkey Day", ["thanksgiving packing", "turkey day packing"]),
    ("A packing bot for our trip over the summer hols",
     ["summertime packing", "summer holiday packing"]),
    ("A packing bot for our NYE trip", ["new years eve packing", "nye packing"]),
    ("A packing bot for our trip at the end of the school year",
     ["end school year trip", "school year packing", "end of school year trip"]),
    ("A packing bot for our Xmas trip to see my parents",
     ["christmas packing", "yule packing", "trip packing"]),
    ("A packing bot for our Yuletide trip", ["christmas packing", "yule packing"]),
    ("A packing bot for our trip in Sept with the kids", ["september packing", "fall packing"]),
])
def test_a_models_other_word_for_her_plans_time_is_held(goal, model):
    assert sent(goal, model + ["packing list"]) == ["packing list"]


def test_the_budget_goal_holds_back_to_school_and_the_trip():
    goal = "Save up for a summer trip and back to school shopping"
    assert sent(goal, ["family budget trip school", "trip school shopping",
                       "savings tracker"]) == ["savings tracker"]


GLUED = ["springbreak", "springbreaktrip", "springtrip", "springcamp", "springtime",
         "fallbreak", "falltrip", "fallcamp", "summerbreak", "summertrip", "autumnbreak",
         "wintercamp", "xmastrip", "thanksgivingtrip", "easterbreak", "julytrip",
         "augusttrip", "maytrip", "marchbreak", "halloweenparty", "newyearsparty",
         "springbreak27", "spring-break", "spring_break", "Spring.Break", "decembertrip"]


@pytest.mark.parametrize("glued", GLUED)
def test_a_glued_time_word_never_leaves(glued):
    goal = f"a packing bot for our {glued} with the kids"
    assert gw.search_terms(goal) == ["packing"]
    low = re.sub(r"[^a-z0-9 ]", " ", glued.lower())
    assert sent(goal, [low, f"{low} packing", "packing list"]) == ["packing list"]


def test_a_glued_time_word_from_a_model_is_held_but_ordinary_words_are_not():
    assert pw.glued_time("falltrip") and pw.glued_time("newyearsparty")
    for word in ("fallout", "springboot", "mayonnaise", "summary", "decorate"):
        assert not pw.glued_time(word), word
    assert sent("a packing bot", ["julytrip ideas", "packing list"]) == ["packing list"]


def test_half_term_sends_neither_half():
    goal = "a rota bot for the half-term holidays"
    assert set(gw.search_terms(goal)) == {"rota"}
    assert not ({"half", "term"} & words_out(goal))


#: Ten new goals (prove the plan-time rule on
#: ten goals nobody else wrote), each with the times that must stay home.
OWN_TIME_GOALS = [
    ("a meal prep bot for our Hanukkah week with the grandparents", {"hanukkah", "week"}),
    ("a chore rota bot for the kids over the winter break", {"winter", "break"}),
    ("a playlist bot for our road trip next spring", {"spring"}),
    ("a gift list bot for Mother's Day brunch", {"mother", "day"}),
    ("a camping checklist bot for our Labor Day weekend", {"labor", "day", "weekend"}),
    ("a budget bot for our autumn getaway to the lake", {"autumn", "getaway"}),
    ("a costume planner bot for our Halloween party", {"halloween"}),
    ("a reading log bot for my son over the summer holidays", {"summer", "holiday"}),
    ("a packing bot for our Easter trip to see grandma", {"easter", "trip"}),
    ("a potluck planner bot for Friendsgiving at our place", {"friendsgiving"}),
]


@pytest.mark.parametrize("goal,never", OWN_TIME_GOALS)
def test_ten_new_goals_keep_the_time_of_her_plans_home(goal, never):
    assert not leaks(goal, never), (goal, leaks(goal, never))
    # A model's other word for the same time is held too.
    assert not {p for p in sent(goal, ["christmas ideas", "summertime fun",
                                       "autumn ideas", "holiday ideas"])}


# -- over-holding and subjects ---------------------------------------------------

@pytest.mark.parametrize("goal,kept", [
    ("a knitting pattern bot for a winter scarf", "winter"),
    ("a bot for tracking autumn leaf peeping spots", "autumn"),
    ("a bot that tracks the stock market after the fall", "fall"),
    ("a winter tire reminder bot for my car", "winter"),
    ("a summer reading list bot for kids", "summer"),
])
def test_a_time_word_that_names_a_thing_is_still_searched(goal, kept):
    assert kept in gw.search_terms(goal) or kept in pw.find(goal).subject.split()
    assert kept in words_out(goal)


@pytest.mark.parametrize("goal,subject", [
    ("a Black Friday deal finder bot", "deal finder"),
    ("a May Day festival planner bot", "festival planner"),
    ("a Cyber Monday price tracker bot", "price tracker"),
])
def test_a_day_with_a_weekday_or_month_leaves_the_subject_whole(goal, subject):
    assert pw.subject_of(goal) == subject


@pytest.mark.parametrize("goal,never", [
    ("a packing bot for our trip in the fall", {"fall", "autumn"}),
    ("a summer camp finder bot", set()),
])
def test_a_plan_time_is_only_sent_as_the_subject(goal, never):
    assert not leaks(goal, never)
    if "summer camp" in goal:
        assert pw.subject_of(goal) == "summer camp finder"


# -- sensitive words and reason clauses: 0 leaks ------------------------------------

FRESH = [
    ("a reminder bot for my wife's lithium blood tests", {"lithium"}),
    ("a recovery meal planner after my husband's heart bypass", {"bypass", "heart"}),
    ("a calendar bot to track my IUD check-ups", {"iud"}),
    ("a budget bot because we signed a prenup", {"prenup"}),
    ("a planner for our annulment paperwork", {"annulment"}),
    ("a checklist bot since there is a warrant out for my brother", {"warrant"}),
    ("a mood journal bot since my husband tried to kill himself", {"kill"}),
    ("a planner for my trial separation from my husband", {"separation", "trial"}),
    ("a reminder bot for my court-ordered anger management classes", {"court", "anger"}),
    ("A bot to track the rash on my son's arm", {"rash", "arm"}),
]


@pytest.mark.parametrize("goal,never", FRESH)
def test_fresh_sensitive_goals_leak_nothing(goal, never):
    assert not leaks(goal, never), (goal, leaks(goal, never))


#: Words no list names, in every reason shape the acceptance probed.
REASON_WORDS = ["zorblat", "bypass", "warrant", "prenup", "separation", "fibroids",
                "shingles", "iud", "lithium", "loan", "pawn", "inheritance"]
REASON_SHAPES = ["a reminder bot since my wife started {w}",
                 "a reminder bot since my wife started {w} last month",
                 "a reminder bot because my wife takes {w}",
                 "a reminder bot now that my dad is on {w}",
                 "a reminder bot because we got a {w}",
                 "a reminder bot because my husband has {w}",
                 "a reminder bot given that my sister needs {w}",
                 "a reminder bot because Liam has {w}"]


@pytest.mark.parametrize("shape", REASON_SHAPES)
@pytest.mark.parametrize("word", REASON_WORDS)
def test_a_reason_clause_about_her_people_is_her_story_whatever_the_verb(shape, word):
    goal = shape.format(w=word)
    never = {word, gw.singular(word)}
    assert not leaks(goal, never), (goal, leaks(goal, never))


@pytest.mark.parametrize("word", ["bypass", "warrant", "prenup", "separation", "fibroids",
                                  "iud"])
def test_her_relatives_listed_private_word_is_held(word):
    goal = f"a reminder bot for my wife's {word}"
    assert not leaks(goal, {word, gw.singular(word)})


@pytest.mark.parametrize("goal,kept", [
    ("a chore bot because my kids have two dogs and a cat", "dog"),
    ("a trivia bot because my husband has his own quiz night", "quiz"),
    ("a garden bot because my tomatoes keep dying", "tomato"),
    ("a bot since my old laptop broke", "laptop"),
    ("a lithium battery tracker bot for my ebike", "lithium"),
    ("a bot for my son's minecraft server", "minecraft"),
])
def test_owning_things_said_the_usual_way_is_still_searched(goal, kept):
    assert kept in gw.search_terms(goal) or kept in pw.find(goal).subject.split(), \
        (goal, gw.search_terms(goal))


def test_a_verb_after_her_person_is_not_listed_as_a_name():
    rows = {h["word"]: h["why"] for h in pw.find("a bot because my son plays chess").held}
    assert "plays" not in rows
    assert rows.get("chess") != "name"


# -- the Add page and the chat's "look online" -------------------------------------

@pytest.mark.parametrize("words,never", [
    ("books about DеShawn soccer", {"dshawn", "deshawn", "shawn"}),
    ("books for my daughter Maгia about horses", {"maia", "magia", "maria"}),
    ("books about José and baseball", {"jose", "jos"}),
    ("books on grief after mom passed away last Thanksgiving",
     {"after", "passed", "away", "last", "thanksgiving"}),
    ("books about the rash on my son's arm", {"rash", "arm"}),
    ("books on how to cope when my wife has bipolar disorder", {"cope", "disorder", "bipolar"}),
    ("books about Li​am and soccer", {"liam", "li", "am"}),
])
def test_screen_words_holds_what_the_add_page_used_to_send(words, never):
    terms = gw.goal_terms(words)
    out = oq.screen_words(terms, goal=words)
    assert not (set(out) & never), out


def test_screen_words_keeps_ordinary_words_and_holds_pairs():
    assert oq.screen_words(["book", "dinosaur"], goal="books about dinosaurs") == \
        ["book", "dinosaur"]
    # "passed away": each word of the pair is held.
    got = oq.screen_words(["passed", "away", "garden"], goal="a garden bot")
    assert "passed" not in got and "away" not in got and "garden" in got


def test_screen_words_fails_closed(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("cannot read")
    monkeypatch.setattr(oq, "_Rules", boom)
    assert oq.screen_words(["book", "dinosaur"], goal="books about dinosaurs") == []


@pytest.fixture
def online_spy(monkeypatch):
    from puffpai import add_more
    from puffpai.knowledge import find_more
    calls: list[str] = []

    def fake_search(words, *, live=None, **kw):
        calls.append(str(words))
        return {"searched": "live", "candidates": []}

    monkeypatch.setattr(find_more, "search", fake_search)
    monkeypatch.setattr(find_more, "_learn_rest_in_background", lambda f: None, raising=False)
    monkeypatch.setattr(add_more, "online_allowed", lambda: True)
    add_more.reset()
    yield calls
    add_more.reset()


@pytest.mark.parametrize("words,never,still", [
    ("books about DеShawn soccer", {"dshawn", "deshawn"}, {"soccer"}),
    ("books about José and baseball", {"jose"}, {"baseball"}),
    ("books about the rash on my son's arm", {"rash", "arm"}, set()),
    ("books on grief after mom passed away last Thanksgiving", {"passed", "away", "after"},
     set()),
    ("books about dinosaurs", set(), {"dinosaur"}),
])
def test_the_add_page_online_search_sends_only_screened_words(online_spy, words, never, still):
    from puffpai import add_more
    session = add_more.find(words, via="page", client="", online=True, only=["knowledge"])
    sent_words = {w for q in online_spy for w in q.lower().split()}
    offered = set(session.get("sends") or [])
    assert not (sent_words & never) and not (offered & never), (online_spy, offered)
    assert still <= sent_words | offered


# -- live cards -------------------------------------------------------------------

CARD = {"id": "t_pictures", "card_url": "https://images.example.org/search?q={topic}",
        "card_topic": "saturn", "match_terms": ["picture", "pictures", "photo", "space"]}


@pytest.mark.parametrize("goal", [
    "A bot with pictures about my miscarriage",
    "A bot to help my kid with his autism",
    "A photo bot for my son DeShawn",
    "A bot with pictures for our trip to Maui next summer",
    "A bot that shows pictures to cheer up my wife during her chemo",
])
def test_a_live_cards_subject_is_never_a_held_word(goal):
    from puffpai import feed_generator as fg
    topic = fg.topic_for(goal, dict(CARD))
    assert topic in ("saturn", "picture", "photo", "pictures"), topic
    _tmpl, _params, look_at = fg.card_address(dict(CARD), topic)
    for word in ("miscarriage", "autism", "deshawn", "maui", "summer", "chemo", "wife"):
        assert word not in look_at.lower()


def test_a_live_card_still_asks_about_an_ordinary_subject():
    from puffpai import feed_generator as fg
    assert fg.topic_for("A space pictures bot", dict(CARD)) in ("space", "picture")


def test_the_apis_list_no_longer_claims_the_look_carries_nothing():
    import json
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "..", "config", "catalog_seeds", "apis.json"),
              encoding="utf-8") as fh:
        note = json.load(fh)["_maintainer_note"]
    assert "The look before the plan always uses sample_endpoint" not in note
    assert "outbound privacy screen" in note


# -- the bot's tagline -----------------------------------------------------------

WEDDING = ("I want to build a wedding planner bot. My brother just got engaged in "
           "June and there is a lot to do.")


def test_the_tagline_is_the_plain_subject_not_her_sentence():
    from puffpai import persona_scaffold as ps
    man = ps.build_app_manifest("weddingbot", "WeddingBot", WEDDING, ["guests"])
    assert man["blurb"] == "A wedding planner bot."
    assert "brother" not in man["blurb"].lower()
    assert ps.goal_of(man).startswith("I want to build a wedding planner bot")


def test_the_tagline_without_a_subject_says_nothing_about_her():
    from puffpai import persona_scaffold as ps
    assert ps.tagline("my sister Anna just got engaged", "AnnaBot") == \
        "AnnaBot, made from your goal."
    assert ps.tagline("an orchid care bot") == "An orchid care bot."


def test_an_app_made_before_moves_its_goal_out_of_the_tagline():
    import json
    from puffpai import app_registry
    from puffpai import persona_scaffold as ps
    ps.scaffold("weddingbot", name="WeddingBot", goal=WEDDING, lists="guests")
    path, man = ps._read_manifest_file("weddingbot")  # noqa: SLF001
    man.pop("goal", None)
    man["blurb"] = ps._GOAL_BLURB_PREFIX + WEDDING[:150]  # noqa: SLF001
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(man, fh)
    app_registry.rescan()
    rows = {r["id"]: r for r in ps.owner_apps()}
    row = rows["weddingbot"]
    assert "brother" not in row["blurb"].lower() and "brother" not in row["goal"].lower()
    assert row["goal"] == "A wedding planner bot."
    _p, fixed = ps._read_manifest_file("weddingbot")  # noqa: SLF001
    assert fixed["blurb"] == "A wedding planner bot."
    assert ps.goal_of(fixed).startswith("I want to build a wedding planner bot")


# -- turned-down backgrounds and look colours --------------------------------------

@pytest.mark.parametrize("words", ["clouds? no way", "clouds: no", "clouds? yuck",
                                   "clouds? hard pass", "clouds - no thank you",
                                   "clouds? ugh no", "clouds? absolutely not",
                                   "clouds -- nope", "clouds: never"])
def test_a_turned_down_background_is_never_named(words):
    from puffpai.looks import find as lf
    assert lf.names_background(words) is None
    assert "clouds" in lf.turned_down_backgrounds(words)


@pytest.mark.parametrize("words,named", [("clouds, no stars", "clouds"),
                                         ("clouds? no way, stars", "stars"),
                                         ("clouds - yes", "clouds"), ("stars: yes please", "stars")])
def test_a_no_about_another_background_is_about_that_one(words, named):
    from puffpai.looks import find as lf
    assert lf.names_background(words) == named


@pytest.mark.parametrize("words,line", [
    ("romantic garden wedding, soft blush pink", "pink on"),
    ("pink", "pink on"),
    ("red", "red on"),
    ("navy", "blue on"),
])
def test_a_made_look_is_described_with_the_colours_it_has(words, line):
    from puffpai.looks import find as lf
    from puffpai.looks import package as P
    got = P.colours_line(lf.made_from_colour_words(words))
    assert line in got and "grey" not in got.split(" on ")[0], got


@pytest.mark.parametrize("name,word", [("red", "red"), ("crimson", "red"), ("orange", "orange"),
                                       ("gold", "gold"), ("blue", "blue"), ("navy", "blue"),
                                       ("purple", "purple"), ("hotpink", "pink"),
                                       ("pink", "pink"), ("teal", "teal")])
def test_plain_colour_words_match_the_named_colours(name, word):
    from puffpai.looks import colour as C
    assert C.hue_word(C.named(name)) == word


# -- device lines -----------------------------------------------------------------

@pytest.mark.parametrize("goal,ids", [
    ("a bot that dims the hue lights", {"device:ha:hue"}),
    ("a bot for my hue lamps", {"device:ha:hue"}),
    ("a bot to turn the Sonos down at night", {"device:ha:sonos"}),
    ("a bot that mutes the Sonos", {"device:ha:sonos"}),
    ("a bot that locks the August lock", {"device:ha:august"}),
    ("a bot that turns up the Sonos and turns on the Hue lights and locks the August lock",
     {"device:ha:sonos", "device:ha:hue", "device:ha:august"}),
    ("a bot that brightens my Nanoleaf panels and mutes the Sonos",
     {"device:ha:nanoleaf", "device:ha:sonos"}),
])
def test_device_lines_for_lower_case_makers_and_any_verb(goal, ids):
    from puffpai.catalogs import live
    got = {i["id"] for i in live.proposal_items(goal) if i["section"] == "devices"}
    assert ids <= got, got


@pytest.mark.parametrize("goal", ["a bot that tells the Smiths about dinner",
                                  "a bot that dims the lights",
                                  "a bot to help the kids with homework"])
def test_a_name_after_the_is_not_made_a_device(goal):
    from puffpai.catalogs import live
    assert not [i for i in live.proposal_items(goal) if i["section"] == "devices"]
