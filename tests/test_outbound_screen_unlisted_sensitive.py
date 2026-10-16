"""Sensitive words no list names, and related findings.

* sensitive words from the directive's categories still went out when no
  list named them ("mastectomy", "incest", "transitioning", "medicaid",
  "sabbath", "polyamorous", "murdered", "public defender", ...) or when
  only a related form was listed ("miscarriage" listed, "miscarried" sent);
* a device line guessed a maker: "our Ecobee thermostat" and "our Nest
  thermostat" got the eQ-3 Bluetooth thermostat on the shared word
  "thermostat", "our thermostat" always got the eQ-3, the label cut off the
  connection's name, and "Hue lights and Ring doorbell" found only Hue;
* her words for a made look named a background she turned down ("deep
  blue, no clouds" gave clouds and "It keeps the clouds background you
  asked for");
* over-holding: "my husband hits me sometimes" held "sometimes"; "a
  meeting tracker bot, log my AA meetings" listed "meeting" as kept private
  while searching "meeting tracker".

Goal-agnostic: made-up sentences in many shapes, never one goal's case.
Offline: the conftest socket guard is on for every test here.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from puffpai.catalogs import goal_words as gw, live, personal_words as pw
from puffpai.security import outbound_query as oq

ROOT = Path(__file__).resolve().parents[1]


def _optional(modname: str):
    try:
        return importlib.import_module(modname)
    except ModuleNotFoundError as e:
        if e.name is not None and (e.name == modname or modname.startswith(e.name + ".")):
            return None
        raise


def _sent(result) -> list[str]:
    return [s.phrase for s in result.sendable]


# -- sensitive words (security major) ---------------------------------------------------

#: The validator's word sweep, word for word.
VALIDATOR_WORDS = ["miscarried", "transitioning", "incest", "pedophile", "kidnapped",
                   "murdered", "prostitution", "polyamory", "swinger", "dyke", "fag",
                   "mastectomy", "biopsy", "prognosis", "euthanized", "orphaned", "medicaid",
                   "sabbath", "communion", "kabbalah", "satanism", "episcopal", "orthodox",
                   "public defender", "credit score"]

#: More of the same categories, so the fix is not only the validator's list:
#: health (operations, conditions by word shape, mental health), sex life
#: and identity, belief, legal trouble, money hardship, violence, grief.
MORE_WORDS = ["hysterectomy", "tonsillectomy", "colonoscopy", "rhinoplasty", "bronchitis",
              "hepatitis", "tuberculosis", "scoliosis", "melanoma", "lymphoma", "leukemia",
              "fibromyalgia", "neuropathy", "pediatric", "psychiatry", "agoraphobia",
              "prognoses", "remission", "metastatic", "hospitalized", "amputation",
              "anorexia", "ocd", "epileptic", "dyslexia", "arthritis", "adultery",
              "detransitioning", "crossdressing", "polygamy", "celibacy", "shabbat",
              "eucharist", "presbyterian", "scientology", "baptized", "tithing",
              "defendant", "burglary", "extortion", "guardianship", "arraigned",
              "prosecuting", "medicare", "foreclosure", "hardship", "furloughed", "homicide",
              "manslaughter", "abducted", "stabbed", "gaslighting", "coerced", "eulogy",
              "autopsy", "euthanasia", "social security", "food bank", "sex worker",
              "hate crime", "foster care", "heart attack", "chronic pain", "bar mitzvah",
              "life support", "terminally ill"]


@pytest.mark.parametrize("word", VALIDATOR_WORDS + MORE_WORDS)
def test_a_sensitive_word_is_never_sent_nor_searched(word):
    goal = f"I want a garden planner bot. My wife and I are dealing with {word} lately."
    got = oq.screen([f"garden {word}", word, f"{word} garden"], goal=goal)
    assert _sent(got) == [], (word, _sent(got))
    assert not set(gw.search_terms(goal)) & set(word.split()), gw.search_terms(goal)


@pytest.mark.parametrize("word", VALIDATOR_WORDS + MORE_WORDS)
def test_a_sensitive_word_makes_no_phrase_sendable_in_an_ordinary_goal(word):
    """The screen holds the word even when her goal says nothing about it (a
    model made up the phrase)."""
    got = oq.screen([f"notes {word}", f"{word} tracker"], goal="a notes bot")
    assert _sent(got) == []


def test_search_terms_leave_out_a_murder_story():
    goal = "I want a journal bot. My brother was murdered and I need to track the trial"
    assert "murdered" not in gw.search_terms(goal)
    assert set(gw.search_terms(goal)) <= {"journal"}


#: The validator's real needs-path sentences, with what must never leave.
NEEDS_SENTENCES = [
    ("I want a journal bot. I'll need to be able to track my feelings since my wife "
     "miscarried in the spring", {"miscarried", "feeling", "feelings", "spring", "since"}),
    ("I want a planner bot. I'll need to be able to plan appointments while my son is "
     "transitioning", {"transitioning", "appointment", "appointments"}),
    ("I want a journal bot. I'll need to be able to write about the incest in my "
     "childhood", {"incest", "childhood"}),
    ("I want a calendar bot. I'll need to be able to track my mastectomy and biopsy "
     "appointments", {"mastectomy", "biopsy", "appointment"}),
    ("I want a budget bot. I'll need to be able to track our medicaid and food bank "
     "visits", {"medicaid", "food", "bank"}),
    ("I want a planner bot. I'll need to be able to plan our sabbath dinners and "
     "communion", {"sabbath", "communion", "dinner"}),
    ("I want a scheduler bot. I'll need to be able to juggle my polyamorous partners' "
     "dates", {"polyamorous", "juggle", "date"}),
    ("I want a journal bot. My brother was murdered and I need to track the trial",
     {"murdered", "trial"}),
]


@pytest.mark.parametrize("goal,private", NEEDS_SENTENCES)
def test_the_real_needs_path_sends_only_the_subject(goal, private, monkeypatch):
    needs = _optional("puffpai.wizard.needs")
    if needs is None:
        pytest.skip("the needs step is not in this build yet")
    monkeypatch.setattr(needs, "_brain", lambda cfg: ("", ""))
    got = needs.sends(needs.run(goal, cfg=None, deadline_s=3), goal=goal)
    words = {w for s in got for w in s.phrase.split()}
    assert not words & private, (goal, [s.phrase for s in got])
    assert all(s.subject for s in got), [s.phrase for s in got]


@pytest.mark.parametrize("goal,private", NEEDS_SENTENCES)
def test_the_screen_holds_every_word_of_the_story(goal, private):
    assert _sent(oq.screen(sorted(private), goal=goal)) == []


@pytest.mark.parametrize("word", ["miscarried", "miscarrying", "aborted", "prosecuting",
                                  "incarcerating", "harassing", "coerced"])
def test_a_verb_form_of_a_listed_noun_is_held(word):
    assert pw.sensitive_category(word), word


#: Everyday words that only look like the new rules: a medical-looking
#: ending ("osmosis", "diploma", "purposes"), a verb whose listed noun has a
#: second meaning ("visits"/"visitation", "garnishing" a cake,
#: "naturalizing" bulbs) and words next to the new pairs.
ORDINARY = ["osmosis", "meiosis", "mitosis", "symbiosis", "metamorphosis", "diploma",
            "aroma", "nostalgia", "academia", "dichotomy", "microscopy",
            "chooses", "purposes", "exposes", "visits", "visiting", "garnishing",
            "garnished", "naturalizing", "seminars", "communes", "virgins", "terrors",
            "community", "traffic", "transition", "meeting", "hospital", "trial", "bank",
            "credit", "score", "food", "social", "security", "defender", "public",
            "transplant", "stroke", "grooming", "killer", "shooting",
            "bishop", "pawn", "attackers", "stripper", "illegal"]


@pytest.mark.parametrize("word", ORDINARY)
def test_an_everyday_word_is_still_sendable(word):
    goal = f"I want a garden planner bot. I'd like help with the {word} too."
    assert _sent(oq.screen([f"garden {word}", word], goal=goal)) == [f"garden {word}", word]
    assert pw.private_goal_words(goal) == {}


def test_a_joined_neighbour_never_reads_as_a_medical_word():
    """"the" + "osmosis" is "theosmosis", which ends like "-osis": two words
    are only joined back when they make a LISTED word ("non binary")."""
    assert pw.sensitive_phrase_hits(["the", "osmosis"]) == []
    assert pw.sensitive_phrase_hits(["non", "binary"])


def test_the_fixture_goals_are_not_made_private():
    """None of the twelve measuring goals tells a private story: the wider
    lists must not turn any of them into "search only the subject"."""
    data = json.loads((ROOT / "tests" / "fixtures" / "tuning_goals.json").read_text(
        encoding="utf-8"))
    goals = data["goals"] if isinstance(data, dict) else data
    for g in goals:
        assert pw.private_goal_words(g["text"]) == {}, g["text"][:60]


def test_a_long_term_condition_holds_its_word_but_not_the_whole_goal():
    """Conditions the data classifier lists ("diabetic") stay held words;
    as before, they do not make the rest of a meal plan private."""
    goal = "I want a meal planner bot. My son is diabetic and I want weekly dinner plans"
    assert pw.private_goal_words(goal) == {}
    assert _sent(oq.screen(["diabetic dinner", "weekly dinner"], goal=goal)) == [
        "weekly dinner"]


# -- over-holding (minor) ----------------------------------------------------------------

def test_a_how_often_word_is_not_part_of_the_story():
    held = {h["word"] for h in pw.find("a journal bot. my husband hits me sometimes").held}
    assert "sometimes" not in held
    assert "hits" in held


def test_a_subject_word_is_not_listed_as_kept_private():
    got = pw.find("a meeting tracker bot, log my AA meetings")
    held = {h["word"] for h in got.held}
    assert got.subject == "meeting tracker"
    assert "aa" in held
    assert not held & {"meeting", "meetings"}
    screened = oq.screen(["meeting tracker", "aa meeting"], goal="a meeting tracker bot, "
                         "log my AA meetings")
    assert _sent(screened) == ["meeting tracker"]


def test_a_pair_holds_no_joining_words():
    held = {h["word"] for h in pw.find("a notes bot. My dad had a stroke last year").held}
    assert "stroke" in held
    assert not held & {"a", "had"}


# -- device lines only on real fit (major) ------------------------------------------------

def _devices(goal: str) -> list[dict]:
    return [i for i in live.proposal_items(goal) if i["section"] == "devices"]


@pytest.mark.parametrize("goal,domain", [
    ("a bot for our Ecobee thermostat", "ecobee"),
    ("a bot for our Nest thermostat", "nest"),
    ("a bot that works with my Nest thermostat", "nest"),
    ("a bot for my smart Nest thermostat", "nest"),
    ("a cleaning bot for our Roomba", "roomba"),
    ("a bot for my roomba", "roomba"),
    ("a bot for my Google Wifi", "google_wifi"),
])
def test_the_maker_she_names_is_the_connection(goal, domain):
    ids = [i["id"] for i in _devices(goal)]
    assert ids == [f"device:ha:{domain}"], (goal, ids)


@pytest.mark.parametrize("goal", ["a bot to control our thermostat", "a bot for my thermostat",
                                  "A Bot For Our Smart Thermostat",
                                  "a bot that works with my Google Home",
                                  "a bot to vacuum with our robot vacuum",
                                  "a bot for our printer"])
def test_no_maker_named_means_no_guess(goal):
    """Several makers' connections could be hers: no line at all, neither a
    guessed maker nor "no ready way" (there may well be one)."""
    assert _devices(goal) == [], (goal, [i["id"] for i in _devices(goal)])


@pytest.mark.parametrize("goal", [
    "a bot for my Philips Hue lights and Ring doorbell",
    "a smart home bot for our Philips Hue lights, Sonos speakers and Ring doorbell",
])
def test_every_thing_in_her_list_gets_its_line(goal):
    ids = {i["id"] for i in _devices(goal)}
    assert {"device:ha:hue", "device:ha:ring"} <= ids
    if "Sonos" in goal:
        assert "device:ha:sonos" in ids


def test_a_list_does_not_run_on_into_the_next_sentence():
    labels = [p["label"] for p in live.device_phrases("a bot for my Hue lights. Tell me the "
                                                       "weather")]
    assert labels == ["Hue lights"]


def test_the_label_always_shows_the_connections_name():
    corpus, _ = live._integration_corpus()
    cand = next(c for c in corpus if c.get("source_catalog") == "ha_integrations"
                and live.ha_domain(c) == "roomba")
    phrase = {"label": "Extremely Long Custom Named Robot Vacuum", "words": ["vacuum"],
              "cue": True, "brand": []}
    item = live._device_item(phrase, (cand, ["vacuum"]))
    name = live.clean_text(cand.get("name"), 40)
    assert item["label"].endswith(f"({name})")
    assert len(item["label"]) <= 80
    assert "if you have Home Assistant" in item["label"]


def test_every_device_label_names_the_connection_it_ticks():
    for goal in ["a bot for our Ecobee thermostat", "a bot for our Nest thermostat",
                 "a bot for my Philips Hue lights and Ring doorbell"]:
        for item in _devices(goal):
            if item["id"].startswith("device:ha:"):
                assert item["label"].endswith(")") and "(" in item["label"], item["label"]


# -- a made look keeps the background her words name, never one she turns down (minor) ---

GOOD = ('{"name": "Night Sky", "day": {"bg": "#0b1a3a", "card": "#13244a", '
        '"text": "#f0f4ff", "accent": "#6fa8ff", "accent2": "#ffcf6f"}, '
        '"night": null, "background": "clouds"}')


@pytest.mark.parametrize("words,named", [
    ("deep blue, no clouds", None), ("clouds are ugly, deep blue", None),
    ("deep blue without stars", None), ("I don't want clouds", None),
    ("I didn't like the stars", None), ("get rid of the clouds", None),
    ("dark with a starry sky", "stars"), ("deep blue with clouds", "clouds"),
    ("clouds not stars", "clouds"), ("with clouds but no stars", "clouds"),
    ("no pink, just clouds", "clouds"),
])
def test_her_words_name_only_a_background_she_wants(words, named):
    from puffpai.looks import find as F
    assert F.names_background(words) == named


@pytest.mark.parametrize("words,refused", [
    ("deep blue, no clouds", "clouds"), ("clouds are ugly, deep blue", "clouds"),
    ("deep blue without stars", "stars"),
])
def test_a_made_look_never_wears_a_background_she_turned_down(words, refused):
    from puffpai.looks import find as F
    made = F.read_model_palette(GOOD, words=words)
    assert made is not None and made["background"] != refused


def test_keep_it_never_claims_a_background_she_turned_down():
    from puffpai.looks import find as F, package as P, store
    store.set_background("clouds")
    words = "deep blue, no clouds"
    made = F.read_model_palette(GOOD, words=words)
    store.remember_found(words, [made])
    tried, note = store.wearing_chosen(made)
    assert tried["background"] != "clouds" and note == ""
    got = store.use(made["id"])
    assert got["ok"] and got["changed"]
    assert "clouds background you asked for" not in got["message"]
    state = store.state()
    assert state["background"] != "clouds"
    assert state["chosen_background"] == "clouds"      # her own choice is untouched
    assert state["vars"] == P.summary(tried)["vars"]    # try-on equals keep


# -- the note a sensitive subject needs (minor; the wizard owns the words) -------

@pytest.mark.parametrize("goal,kind", [
    ("a divorce support tracker bot", "private"),
    ("a transgender support tracker bot", "private"),
    ("a religion study bot", "private"),
    ("a debt payoff tracker bot", "private"),
    ("a grief support bot", "private"),
    ("a mastectomy recovery tracker bot", "health"),
    ("a sabbath planner bot", "private"),
    ("a garden planner bot", ""),
])
def test_every_sensitive_subject_names_its_own_note(goal, kind):
    """The signal the wizard reads to pick the plain note: "private" for sex,
    belief, legal, money, violence and grief subjects, "health" for health
    ones. The pinned ``subject_notes`` stay brand/health (design I-1)."""
    assert pw.subject_note_kind(goal) == kind
    assert ("health" in pw.find(goal).subject_notes) == bool(kind)
