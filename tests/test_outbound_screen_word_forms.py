"""Word forms of sensitive words, and related gaps.

* sensitive words written in another form than the listed one went out:
  "-ing" and "-ity"/"-ism" forms ("relapsing", "molesting", "overdosing",
  "homosexuality", "lesbianism"), hyphen splits ("non-binary" searched as
  "non binary identity", "ex-convict" as "convict job application") and a
  few missing words ("bpd", "aa", "closeted", "cremated");
* a plain noun after "our" ("our office", "our kitchen") matched a Home
  Assistant connection on one ordinary word of its maker's name (Met
  Office, "Everything but the Kitchen Sink");
* no caller passed her home town to the screen, so a home-town subject
  ("a Tucson garden bot") was searched with the town in it;
* her words for a made look ("deep blue with clouds") lost to an older
  background choice, with no line saying so;
* a sensitive subject that is not a health one needs its own plain note
  (the wizard's wording belongs elsewhere; this file pins the signal).

Goal-agnostic: made-up sentences in many shapes, never one goal's case.
Offline: the conftest socket guard is on for every test here.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from puffpai.catalogs import live, personal_words as pw, wikipedia_topics
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


# -- sensitive words in every form (security major) ------------------------------------

#: One word per line of the validator's report, in the form she might type it.
INFLECTED = ["relapsing", "overdosing", "molesting", "assaulting", "homosexuality",
             "lesbianism", "cremated", "closeted", "bpd", "aa", "bisexuality",
             "worshiping", "incarcerated", "bankrupted", "abusiveness", "detoxing",
             "vaped", "grieved", "mourner", "molester"]


@pytest.mark.parametrize("word", INFLECTED)
def test_an_inflected_sensitive_word_is_never_sent(word):
    got = oq.screen([word, f"notes {word}", f"{word} tracker"], goal="a notes bot")
    assert _sent(got) == []
    assert pw.sensitive_category(word)


@pytest.mark.parametrize("phrase", ["non binary identity", "notes non binary",
                                    "self harm log", "ex convict job", "home sexual"])
def test_a_sensitive_word_split_in_two_is_never_sent(phrase):
    got = oq.screen([phrase], goal="a notes bot")
    assert _sent(got) == [], phrase


@pytest.mark.parametrize("word", ["non-binary", "ex-convict", "self-harm", "anti-anxiety"])
def test_a_hyphenated_sensitive_word_has_its_category(word):
    assert pw.sensitive_category(word)


#: The directive's probes (must still hold).
DIRECTIVE_PROBES = ("anxiety sobriety herpes abortion alcoholism opioid methadone aids rape "
                    "abuse transgender gay therapy hospice grief divorce bankruptcy "
                    "immigration eviction religion salary custody parole").split() + [
                        "domestic violence"]


@pytest.mark.parametrize("word", DIRECTIVE_PROBES)
def test_the_directive_probes_still_hold(word):
    assert _sent(oq.screen([word, f"notes {word}"], goal="a notes bot")) == []


def test_every_inflection_the_spelling_list_knows_of_a_listed_word_is_held():
    pw._dictionary()
    missed = []
    for form, bases in pw._INFLECTED_FROM.items():
        if bases & set(pw.SENSITIVE_WORDS) and not pw.sensitive_category(form):
            missed.append(form)
    assert missed == []


@pytest.mark.parametrize("phrase", ["gravity", "gravies", "heroines", "refuges", "probe",
                                    "creme brulee", "wags", "raps", "sexist jokes",
                                    "godly", "dated photos", "study aids"])
def test_an_ordinary_word_that_only_looks_like_one_is_not_held(phrase):
    """The endings rule reads the spelling list: "gravity" does not come from
    "grave", "heroines" not from "heroin", "refuges" not from "refugee"."""
    assert _sent(oq.screen([phrase], goal="a notes bot")) == [phrase]


VALIDATOR_SENTENCES = [
    ("I want a diary bot. I'll need to be able to log each time he is molesting or "
     "assaulting me and keep the photos", {"molesting", "assaulting"}),
    ("I want a helper bot. I'll need to be able to track my overdosing risk and my BPD "
     "moods", {"overdosing", "bpd", "risk", "moods"}),
    ("I want a journal bot. I'll need to be able to log my AA meetings and track how "
     "often I am relapsing", {"aa", "relapsing", "meetings"}),
    ("I want a journal bot. I'll need to be able to track my non-binary identity and my "
     "binder sizes", {"non", "binary", "identity", "binder"}),
    ("I want a planner bot. I'll need to be able to track my ex-convict job applications "
     "and my relapsing days", {"convict", "job", "application", "relapsing"}),
    ("I want a family bot. My husband hits me and I need to log the dates",
     {"date", "dates", "hits"}),
    ("I want a tracker bot to track my wifes affair and her cheating",
     {"affair", "cheating"}),
]


@pytest.mark.parametrize("goal,private", VALIDATOR_SENTENCES)
def test_the_real_needs_path_sends_only_the_subject(goal, private, monkeypatch):
    needs = _optional("puffpai.wizard.needs")
    if needs is None:
        pytest.skip("the needs step is not in this build yet")
    monkeypatch.setattr(needs, "_brain", lambda cfg: ("", ""))
    got = needs.sends(needs.run(goal, cfg=None, deadline_s=3), goal=goal)
    words = {w for s in got for w in s.phrase.split()}
    assert not words & private, (goal, [s.phrase for s in got])
    assert all(s.subject for s in got), [s.phrase for s in got]


@pytest.mark.parametrize("goal,private", VALIDATOR_SENTENCES)
def test_the_screen_holds_every_word_of_the_story(goal, private):
    got = oq.screen(sorted(private), goal=goal)
    assert _sent(got) == []


def test_a_battery_word_alone_does_not_make_the_whole_goal_private():
    """"aa" is held on its own ("AA meetings" says the story), but "AA
    batteries" does not make every other word of the goal private."""
    goal = "I want a flashlight bot that reminds me to buy AA batteries and check the bulbs"
    assert pw.private_goal_words(goal) == {}
    got = oq.screen(["battery reminder", "aa battery"], goal=goal)
    assert _sent(got) == ["battery reminder"]


# -- device lines: one ordinary word of a maker's name is no match ------------------------

@pytest.mark.parametrize("goal", ["a holiday party planner bot for our office",
                                  "a recipe bot for our kitchen",
                                  "a bot for my thermostat",
                                  "a chores bot for my robot vacuum",
                                  "a bot for my weather station"])
def test_a_plain_noun_never_brings_a_makers_connection(goal):
    ids = [i["id"] for i in live.proposal_items(goal) if i["section"] == "devices"]
    assert not [i for i in ids if i.startswith("device:ha:")], (goal, ids)


@pytest.mark.parametrize("goal,domain", [
    ("a bot for my Philips Hue lights", "hue"),
    ("I want a bot to run my smart lights and my Ring doorbell", "ring"),
    ("a bot that checks my Roomba", "roomba"),
])
def test_a_named_device_still_gets_its_connection(goal, domain):
    ids = [i["id"] for i in live.proposal_items(goal) if i["section"] == "devices"]
    assert f"device:ha:{domain}" in ids


def test_the_holiday_goal_keeps_only_real_fits():
    ids = [i["id"] for i in live.proposal_items("a holiday party planner bot for our office")]
    assert "device:ha:metoffice" not in ids


# -- her saved home town is read by the screen itself --------------------------------------

@pytest.fixture
def home_tucson(monkeypatch):
    from puffpai import home_location
    monkeypatch.setattr(home_location, "get_home", lambda: {
        "label": "Tucson, Arizona, United States", "latitude": 32.2,
        "longitude": -110.9, "timezone": "America/Phoenix", "source": "town"})


def test_the_screen_holds_her_saved_town_with_no_home_passed(home_tucson):
    got = oq.screen(["tucson garden", "garden tips", "arizona cactus"],
                    goal="a Tucson garden bot")
    assert _sent(got) == ["garden tips"]


def test_the_wikipedia_book_leaves_her_saved_town_out(home_tucson):
    books = wikipedia_topics.candidates_for("a Tucson garden bot")
    assert books
    for book in books:
        assert "tucson" not in json.dumps(book).lower()
        assert book["sends"] == ["garden"]
    assert wikipedia_topics.candidates_for("a Tucson bot") == []


def test_a_saved_home_that_cannot_be_read_sends_nothing(monkeypatch):
    from puffpai import home_location

    def broken():
        raise RuntimeError("config unreadable")
    monkeypatch.setattr(home_location, "get_home", broken)
    got = oq.screen(["garden tips"], goal="a garden bot")
    assert _sent(got) == []
    assert oq.subject_sendable("a garden bot") is None
    assert wikipedia_topics.candidates_for("a garden bot") == []


def test_an_explicit_home_is_used_as_given(home_tucson):
    """A caller's own ``home`` wins; ``{}`` means none is known."""
    got = oq.screen(["tucson garden"], goal="a Tucson garden bot", home={})
    assert _sent(got) == ["tucson garden"]


def test_with_no_home_saved_nothing_changes():
    assert oq.saved_home() is None
    assert _sent(oq.screen(["garden tips"], goal="a garden bot")) == ["garden tips"]


# -- the note a sensitive subject needs ---------------------------------------------------

@pytest.mark.parametrize("goal,kind", [
    ("a bible study bot", "private"),
    ("a divorce planner bot", "private"),
    ("a grief journal bot", "private"),
    ("a bankruptcy helper bot", "private"),
    ("a sobriety tracker bot", "health"),
    ("an anxiety journal bot", "health"),
    ("a wedding planner bot", ""),
])
def test_the_subject_note_kind_names_the_right_note(goal, kind):
    assert pw.subject_note_kind(goal) == kind
    # The pinned signal (design I-1) stays "health" for every sensitive
    # subject, so a wizard that knows only brand/health still warns.
    notes = pw.find(goal).subject_notes
    assert ("health" in notes) == bool(kind)


def test_the_private_note_is_plain():
    from puffpai import plain_words
    assert not plain_words.check(pw.PRIVATE_SUBJECT_NOTE)
    assert "health" not in pw.PRIVATE_SUBJECT_NOTE


# -- her words for a made look win over an older choice ------------------------------------

GOOD = ('{"name": "Night Sky", "day": {"bg": "#EEF2FF", "card": "#ffffff", '
        '"text": "#1e1b4b", "accent": "#4f46e5", "accent2": "#f59e0b"}, '
        '"night": {"bg": "#0b1026", "card": "#141a3a", "text": "#e0e7ff", '
        '"accent": "#818cf8", "accent2": "#fbbf24"}, "background": "stars"}')


def test_her_words_name_the_background_not_the_model():
    from puffpai.looks import find as F
    made = F.read_model_palette(GOOD, words="deep blue with clouds")
    assert made is not None and made["background"] == "clouds"


def test_a_look_made_for_words_naming_a_background_keeps_it():
    from puffpai import plain_words
    from puffpai.looks import find as F, package as P, store
    store.set_background("stars")
    made = F.read_model_palette(GOOD, words="deep blue with clouds")
    store.remember_found("deep blue with clouds", [made])
    tried, note = store.wearing_chosen(made)
    assert tried["background"] == "clouds" and note == ""
    got = store.use(made["id"])
    assert got["ok"] and got["changed"]
    assert store.WORDED_LINE.format(background="clouds") in got["message"]
    assert not plain_words.check(got["message"])
    state = store.state()
    assert state["background"] == "clouds"
    assert state["chosen_background"] == "clouds"
    assert state["vars"] == P.summary(tried)["vars"]      # try-on equals keep
    store.undo()
    assert store.state()["background"] == "stars"
    assert store.state()["chosen_background"] == "stars"


def test_words_that_name_no_background_still_wear_her_choice():
    from puffpai.looks import find as F, store
    store.set_background("stars")
    made = F.read_model_palette(GOOD, words="deep blue and gold")
    store.remember_found("deep blue and gold", [made])
    assert store.use(made["id"])["ok"]
    assert store.state()["background"] == "stars"
    assert store.state()["chosen_background"] == "stars"


def test_with_no_older_choice_the_named_background_is_just_worn():
    from puffpai.looks import find as F, store
    made = F.read_model_palette(GOOD, words="deep blue with clouds")
    store.remember_found("deep blue with clouds", [made])
    got = store.use(made["id"])
    assert got["ok"] and store.state()["background"] == "clouds"
    assert store.WORDED_LINE.split("{")[0] not in got["message"]


def test_the_owner_strings_list_the_new_line():
    lines = (ROOT / "tests" / "fixtures" / "ui_strings" / "privacy_and_looks.txt").read_text(
        encoding="utf-8").splitlines()
    from puffpai.looks import store
    assert store.WORDED_LINE in lines
