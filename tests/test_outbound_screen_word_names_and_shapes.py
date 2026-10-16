"""The outbound screen's word names and shapes.

* names that are ordinary words ("river", "sky", "willow") after a relative
  went out after an over-holding fix narrowed the rule (blocker);
* two shape words ("pictures and photos") brought NASA pictures back to
  non-space goals;
* addiction, medicine, sexual identity and money-hardship words passed the
  screen ("drinking", "xanax", "grindr", "garnishment", "self harm"), and the
  word right after one ("self-harm urges") went out on its own;
* names with accented letters split into pieces ("Noémie" -> "no", "mie");
* a sensitive subject that is not a health one got a note the wizard
  never showed;
* a news verb ("we are opening a bakery") named the Wikipedia book;
* "soft pink" counted as asking for the soft background.

Goal-agnostic: made-up sentences in many shapes, never one goal's case.
Offline: the conftest socket guard is on for every test here.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from puffpai.catalogs import goal_words, live, personal_words as pw
from puffpai.security import outbound_query as oq

ROOT = Path(__file__).resolve().parents[1]


def _sent(result) -> list[str]:
    return [s.phrase for s in result.sendable]


# -- names that are ordinary words, after a person (blocker) ---------------------------

RELS = ["son", "daughter", "wife", "husband", "grandson", "friend", "niece"]
WORD_NAMES = ("river sky juniper willow hazel wren sage jade ruby pearl brooke reed clay "
              "ivy daisy poppy storm journey honor faith").split()


@pytest.mark.parametrize("rel", RELS)
def test_a_name_that_is_an_ordinary_word_after_a_relative_never_leaves(rel):
    leaks = []
    for name in WORD_NAMES:
        goal = f"I want a garden helper bot for my {rel} {name} and me"
        res = oq.screen([f"{name} garden"], goal=goal)
        if res.sendable or name in goal_words.search_terms(goal):
            leaks.append(name)
    assert leaks == []


@pytest.mark.parametrize("goal,phrase", [
    ("a chess bot for my son river", "river chess"),
    ("I want a chess bot for my son sky. sky loves chess", "sky chess"),
    ("a reading buddy bot for my daughter willow and her friends", "willow reading"),
])
def test_the_name_is_held_wherever_it_comes_back(goal, phrase):
    name = phrase.split()[0]
    assert _sent(oq.screen([phrase], goal=goal)) == []
    assert name not in goal_words.search_terms(goal)
    row = next(h for h in pw.find(goal).held if h["word"] == name)
    # Her own tick can still let it out: over-holding is hers to undo.
    assert row["why"] == "name" and row["can_allow"]
    assert _sent(oq.screen([phrase], goal=goal, allowed_words=[name])) == [phrase]


# -- NASA: shape words are never enough ---------------------------------------------------

NON_SPACE_PICTURE_GOALS = [
    "a scrapbook bot for my kids photos and pictures",
    "a wedding photo album bot that sorts our pictures and images",
    "an art class helper bot with pictures and photos of paintings",
    "a real estate listing bot with photos and pictures of houses",
    "a recipe bot with food pictures and photos",
    "I want a wedding planner bot. Track themes from pictures and photos, and keep a "
    "calendar of dates",
    "a birthday party planner bot with a calendar, a list of dates and pictures",
    "a plant identification bot from photos and images",
    "a photo calendar bot for our family pictures",
]


@pytest.mark.parametrize("goal", NON_SPACE_PICTURE_GOALS)
def test_pictures_and_photos_never_bring_nasa_or_holidays(goal):
    refs = [str(i["apply"].get("ref")) for i in live.proposal_items(goal)
            if i.get("section") == "live_info"]
    assert not [r for r in refs if "nasa" in r or "nager_holidays" in r], refs


def test_two_shape_words_are_not_a_fit_but_one_real_word_is():
    cand = {"name": "NASA Image and Video Library",
            "what_it_is": "Pictures and photos from NASA missions.",
            "match_terms": ["space", "nasa", "picture", "photo", "image"]}
    goal = "a scrapbook bot with pictures and photos"
    assert not live.fits_goal(goal, cand, ["picture", "photo"])
    assert not live.fits_goal(goal, cand, ["pictures", "photos", "images"])
    space = "a space bot with pictures of planets"
    assert live.fits_goal(space, cand, ["space", "picture"])


def test_the_space_goal_still_gets_nasa():
    goal = ("I'm Leo, I'm 10 and I want a space bot! Show me cool pictures of planets "
            "and rockets, tell me when the space station flies over Dayton.")
    refs = [str(i["apply"].get("ref")) for i in live.proposal_items(goal)
            if i.get("section") == "live_info"]
    assert any("nasa" in r for r in refs), refs


# -- sensitive words ------------------------------------------------------------------------

#: Words that once passed the screen.
VALIDATOR_WORDS = ["drinking", "alcohol", "drugs", "marijuana", "cannabis", "gambling",
                   "xanax", "adderall", "stillbirth", "testosterone", "hrt", "grindr",
                   "homophobia", "garnishment", "self harm"]


@pytest.mark.parametrize("word", VALIDATOR_WORDS)
def test_the_validators_sensitive_words_are_held_on_a_neutral_goal(word):
    got = oq.screen([word, f"journal {word}", "journal ideas"], goal="I want a journal bot")
    assert _sent(got) == ["journal ideas"], word


@pytest.mark.parametrize("word", VALIDATOR_WORDS)
def test_the_validators_sensitive_words_are_held_inside_the_goal(word):
    goal = f"I want a journal bot. I need to track my {word} every day"
    got = oq.screen([word, f"journal {word}"], goal=goal, allowed_words=word.split())
    assert _sent(got) == []
    assert not set(goal_words.search_terms(goal)) & set(word.split())


@pytest.mark.parametrize("goal,neighbour", [
    ("I want a journal bot. I'll need to track self-harm urges and log my mood", "urges"),
    ("I want a planner bot. Prepare for the custody hearing and pack lunches", "hearing"),
    ("I want a planner bot. Remember my parole meeting and my chores", "meeting"),
    ("I want a journal bot. Note my xanax doses", "doses"),
    ("I want a journal bot. Track herpes outbreaks", "outbreaks"),
])
def test_the_word_right_after_a_sensitive_word_is_held_too(goal, neighbour):
    # In a goal that tells a
    # private story every word but the subject's is held already, so the
    # neighbour is no longer listed one by one; where it is listed, it can
    # never be ticked. It is never sent and never searched either way.
    found = pw.find(goal)
    rows = [h for h in found.held if h["word"] == neighbour]
    assert rows or pw.private_goal_words(goal), found.held
    assert all(not h["can_allow"] for h in rows), found.held
    single = goal_words.singular(neighbour)
    assert _sent(oq.screen([f"journal {single}", f"planner {single}"], goal=goal)) == []
    assert single not in goal_words.search_terms(goal)


def test_a_comma_ends_the_neighbour_rule():
    goal = "I want a journal bot. Track my anxiety, garden chores and recipes"
    assert "garden" not in {h["word"] for h in pw.find(goal).held}
    # The goal tells a mental
    # health story that is not the bot's subject, so every word of it except
    # the subject's stays here; words that are not hers still go out.
    assert _sent(oq.screen(["garden chores", "journal", "mood tracker"], goal=goal)) == [
        "journal", "mood tracker"]


def test_a_sensitive_subject_keeps_its_own_jobs():
    goal = "I want a sobriety tracker bot. Track my sobriety milestones"
    assert "milestones" not in {h["word"] for h in pw.find(goal).held}
    assert _sent(oq.screen(["sobriety milestone"], goal=goal)) == ["sobriety milestone"]


def test_the_rule_only_needs_path_sends_none_of_it():
    from puffpai.wizard import needs
    goal = ("I want a journal bot. I'll need to be able to track self-harm urges, log my "
            "drinking, and note my xanax doses")
    old = needs._brain
    needs._brain = lambda cfg: ("", "")
    try:
        sent = [s.phrase for s in needs.sends(needs.run(goal, cfg=None, deadline_s=5),
                                              goal=goal)]
    finally:
        needs._brain = old
    words = {w for p in sent for w in p.split()}
    assert not words & {"self", "harm", "urge", "urges", "drinking", "xanax", "dose",
                        "doses"}, sent


@pytest.mark.parametrize("goal", [
    "a divorce planner bot", "a bankruptcy planner bot", "a grief journal bot",
    "a bible study bot", "a gambling recovery journal bot", "an anxiety journal bot",
    "a Minecraft server helper bot",
])
def test_subject_notes_stay_inside_the_pinned_set(goal):
    notes = pw.find(goal).subject_notes
    assert notes and set(notes) <= {"brand", "health"}
    assert set(oq.screen([pw.subject_of(goal)], goal=goal).subject_notes) <= {"brand",
                                                                            "health"}


def test_the_wizard_shows_the_note_for_a_non_health_sensitive_subject(monkeypatch):
    from puffpai.wizard import needs, service, wording
    monkeypatch.setattr(needs, "_brain", lambda cfg: ("", ""))
    monkeypatch.setattr(service, "_source_labels", lambda n: ["GitHub"])
    # The service cuts the labels to the places really
    # asked (_asked_labels); keep the fake place so the note line is drawn.
    monkeypatch.setattr(service, "_asked_labels", lambda n, s, labels: labels,
                        raising=False)
    goal = "a divorce planner bot"
    view = service.needs_words(needs.run(goal, cfg=None, deadline_s=5), goal=goal,
                               mode=wording.STANDARD, internet=True, engine=needs)
    line = next(b for b in view["before_button"] if "divorce planner" in b)
    assert "Only look on this computer" in line


# -- accented names ---------------------------------------------------------------------------

ACCENTED = [("Noémie", "noemie"), ("Björn", "bjorn"), ("Søren", "soren"),
            ("Łukasz", "lukasz"), ("Siobhán", "siobhan"), ("Zoë", "zoe"),
            ("Ingrid Müller", "muller"), ("Nguyễn", "nguyen"), ("Ærin", "aerin"),
            ("Straße", "strasse")]


@pytest.mark.parametrize("name,plain", ACCENTED)
def test_an_accented_name_is_held_whole_and_no_piece_goes_out(name, plain):
    from puffpai import offers
    from puffpai.wizard import factory
    goal = f"I want a soccer coach bot for my son {name}. Track {name}'s practice schedule"
    held = {h["word"] for h in pw.find(goal).held}
    assert plain in held, held
    assert _sent(oq.screen([f"{plain} soccer schedule"], goal=goal)) == []
    extractors = {
        "search_terms": goal_words.search_terms(goal),
        "knowledge": factory._knowledge_terms(goal),
        "live": live.terms_from_goal(goal),
        "offers": offers.goal_terms(goal),
    }
    for where, terms in extractors.items():
        assert {str(t).lower() for t in terms} <= {"soccer", "coach", "track", "practice",
                                                  "schedule"}, (
            where, terms)


def test_fold_text_keeps_words_whole():
    assert goal_words.fold_text("Noémie Søren Łukasz Müller") == "Noemie Soren Lukasz Muller"
    assert goal_words.raw_words("Noémie's café") == ["noemie", "s", "cafe"]
    # A letter with no plain spelling is dropped inside its word, never a split.
    assert goal_words.raw_words("Ωmar") == ["mar"]
    assert goal_words.fold_text("plain words") == "plain words"


@pytest.mark.parametrize("typed,name", [("emmass", "emma"), ("annases", "anna"),
                                        ("jakess", "jake"), ("emmas", "emma")])
def test_typed_out_endings_on_a_name_are_the_name(typed, name):
    goal = f"I want a party planner bot for my daughter {name.title()}."
    assert _sent(oq.screen([f"{typed} party"], goal=goal)) == []


def test_a_pronoun_in_the_goal_does_not_hold_a_longer_word():
    goal = "I want a wedding planner bot. Track themes and hers and plan them"
    assert _sent(oq.screen(["wedding themes"], goal=goal)) == ["wedding themes"]
    assert "theme" in goal_words.search_terms(goal)


# -- news words and the Wikipedia book --------------------------------------------------------

@pytest.mark.parametrize("goal,news,kept", [
    ("My mom and I are opening a small bakery. I want a bot for recipes", "opening",
     "bakery"),
    ("He just joined the chess club. I want a chess coach bot", "joined", "chess"),
    ("We're moving soon and I want a packing checklist bot", "moving", "packing"),
    ("A chess coach bot to learn the openings", "", "opening"),
])
def test_a_news_verb_is_not_a_search_word(goal, news, kept):
    terms = goal_words.search_terms(goal)
    if news:
        assert news not in terms and goal_words.singular(news) not in terms
    assert kept in terms


def test_a_goal_with_no_bot_kind_names_its_book_without_news_words():
    from puffpai.catalogs import wikipedia_topics as wt
    goals = json.loads((ROOT / "tests" / "fixtures" / "tuning_goals.json")
                       .read_text(encoding="utf-8"))["goals"]
    bakery = next(g["text"] for g in goals if g["id"] == "bakery")
    words = wt.topic_words(bakery)
    assert words and "opening" not in words
    for cand in wt.candidates_for(bakery):
        assert "opening" not in cand["id"] and "opening" not in cand["name"]


def test_the_book_title_is_the_plan_line_without_its_size_note():
    """The plan line is the book's name plus the size note every knowledge line
    carries (knowledge_resolver); the saved book's title is that
    name exactly (a title saying "(up to 25 MB)" would be wrong once saved)."""
    from puffpai.catalogs import knowledge_resolver, wikipedia_topics as wt
    goal = "I want a knitting patterns bot"
    items = [i for i in knowledge_resolver.proposal_items(goal, goal_words.search_terms(goal))
             if str(i["apply"]["ref"]).startswith("wikipedia:")]
    assert items
    for item in items:
        title = wt.book_meta(item["apply"]["ref"])["title"]
        cand = wt.candidate_by_id(item["apply"]["ref"])
        assert item["label"] == f"{title} ({knowledge_resolver.download_words(cand)})"


# -- looks: a colour word is not a background --------------------------------------------------

@pytest.mark.parametrize("words,bg", [
    ("soft pink", None), ("plain blue", None), ("soft colours", None),
    ("something plain and warm", None), ("soft", "soft"), ("soft colour", "soft"),
    ("a soft background", "soft"), ("a plain page", "plain"), ("with stars", "stars"),
    ("clouds please", "clouds"),
])
def test_only_a_named_background_counts(words, bg):
    from puffpai.looks import find as F
    assert F.names_background(words) == bg
