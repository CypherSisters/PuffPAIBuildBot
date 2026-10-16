"""Privacy of names and owned things.

* an everyday-meaning word ("stroke", "cutting", "cheating", "spectrum")
  left when its neighbour sat in another clause, or inside a story about one
  of her people; and a sensitive subject left with a person attached;
* a child's name at the start of a sentence or alone on a line was sent, on
  the wizard and on the Add page;
* "broken knob dryer" went out from "my mom's dryer";
* over-holding: "rehearse" read as "re" + "hearse", "me through" as "meth"
  + "rough", "3d" held as a number, "minecraft" held although the bot is
  about it, "debt" held in a family budget bot;
* the job after a condition went out ("stop crying", "count calories");
* device lines, turned-down backgrounds, names typed with web encodings and
  an RTL override, and "married" missing from the "Kept private" line.

Every probe is offline: the goal reader, the outbound screen, the device
lines and the needs step read files on this computer; the Add page's online
search is replaced by a spy; nothing is sent anywhere.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import re

import pytest

from puffpai.catalogs import goal_words as gw
from puffpai.catalogs import live
from puffpai.catalogs import personal_words as pw
from puffpai.looks import find as looks_find
from puffpai.security import outbound_query as oq


def _forms(words) -> set[str]:
    out: set[str] = set()
    for w in words:
        out |= {w, gw.singular(w)}
    return out


def leaked(goal: str, private: list[str], extra: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    """The validator's harness (z_harness.leaks): the goal's search words,
    every word and pair of neighbours of the goal, run through the screen
    with the goal; any private word in what is sent, in the search words, or
    in the subject is a leak."""
    pf = _forms(private)
    terms = gw.search_terms(goal)
    ws = re.findall(r"[a-z0-9']+", goal.lower())
    phrases = (list(terms) + ws + [f"{a} {b}" for a, b in zip(ws, ws[1:])]
               + [" ".join(private)] + list(extra))
    result = oq.screen(phrases, goal=goal)
    out: list[tuple[str, str]] = []
    for t in terms:
        if _forms(t.split()) & pf:
            out.append(("terms", t))
    for s in result.sendable:
        if _forms(s.phrase.split()) & pf:
            out.append(("sent", s.phrase))
    sub = oq.subject_sendable(goal)
    if sub is not None and _forms(sub.phrase.split()) & pf:
        out.append(("subject", sub.phrase))
    return out


def sent(goal: str, phrases: list[str]) -> list[str]:
    return [s.phrase for s in oq.screen(phrases, goal=goal).sendable]


# -- sensitive words with a neighbour in another clause ------------------------------

V10_LEAKS = [
    ("my mom had a mild stroke, a painting bot for her", ["stroke"]),
    ("my mom had a mild stroke, a paint by numbers bot for her", ["stroke"]),
    ("my son is on the spectrum, a radio bot", ["spectrum"]),
    ("my son is on the spectrum, a rainbow colors bot", ["spectrum"]),
    ("my daughter is cutting, a paper craft bot", ["cutting"]),
    ("my daughter has been cutting, a hair care bot", ["cutting"]),
    ("she was cutting with paper clips, a mood bot", ["cutting"]),
    ("a mood bot for my girl, she was cutting with paper clips", ["cutting"]),
    ("my wife is cheating, a card game bot", ["cheating"]),
    ("a bot to track whether my wife is cheating online", ["cheating"]),
    # The validator's other v10 sweep lines.
    ("a stroke recovery exercise bot for my husband", ["stroke"]),
    ("a homework bot for my son after the lockdown drill thing", ["lockdown"]),
    ("my dad had a stroke while golfing, a golf score bot", ["stroke"]),
    ("a swim lesson bot for my uncle who had a stroke", ["stroke"]),
    ("my son keeps cutting his arms, a mood bot", ["cutting"]),
    ("my kid is somewhere on the spectrum, a routine bot", ["spectrum"]),
    ("a chore bot. My wife found out I was cheating.", ["cheating"]),
    ("Bot for my son.\nHe has been cutting.", ["cutting"]),
]


@pytest.mark.parametrize("goal,private", V10_LEAKS)
def test_the_v10_goals_keep_their_sensitive_word(goal, private):
    assert leaked(goal, private) == []


@pytest.mark.parametrize("goal,word", [
    ("a swim stroke technique bot", "stroke"),
    ("a kanji stroke order practice bot", "stroke"),
    ("a golf stroke counter bot", "stroke"),
    ("a paper cutting crafts bot", "cutting"),
    ("a lawn cutting schedule bot", "cutting"),
    ("a chess cheating detection bot", "cheating"),
    ("a light spectrum analyzer bot", "spectrum"),
    ("a swim stroke technique bot for my son", "stroke"),
    ("a paper cutting crafts bot for my daughter", "cutting"),
])
def test_the_neighbour_in_its_own_clause_still_lets_the_everyday_word_out(goal, word):
    assert word in gw.search_terms(goal) or gw.singular(word) in gw.search_terms(goal)
    sub = oq.subject_sendable(goal)
    assert sub is not None and word in sub.phrase


def _plain_near(word: str) -> list[str]:
    _cat, near = pw.PERSONAL_SENSITIVE[word]
    out = set()
    for n in near:
        n = n.lstrip("<>")
        if (n.isalpha() and len(n) > 1 and n not in pw.FUNCTION_WORDS and n not in pw.PRONOUNS
                and n not in pw.POSSESSIVES and not pw.is_hard_word(n)):
            out.add(n)
    return sorted(out)


#: A sensitive word about one of her people in one clause, its everyday
#: neighbour in another.
_OTHER_CLAUSE = (
    "my mom had {w}, a {n} bot for her",
    "a {n} bot for my dad; he has been {w}",
    "My wife was {w} again.\nA {n} bot for her.",
)


@pytest.mark.parametrize("word", sorted(w for w in pw.PERSONAL_SENSITIVE if _plain_near(w)))
def test_sweep_no_neighbour_in_another_clause_releases_a_sensitive_word(word):
    bad = []
    for n in _plain_near(word):
        for shape in _OTHER_CLAUSE:
            goal = shape.format(w=word, n=n)
            terms = gw.search_terms(goal)
            got = sent(goal, [word, f"{word} {n}", f"{n} {word}"])
            sub = oq.subject_sendable(goal)
            if word in terms or got or (sub is not None and word in sub.phrase.split()):
                bad.append((goal, terms, got))
    assert bad == []


def test_a_sensitive_subject_with_a_person_attached_is_no_subject():
    for goal in ("a stroke recovery exercise bot for my husband",
                 "an anxiety journal bot for my daughter", "her anxiety journal bot",
                 "a cutting tracker bot for my daughter"):
        assert pw.find(goal).subject == "", goal
        assert oq.subject_sendable(goal) is None, goal


def test_her_own_sensitive_subject_is_still_searched_with_its_note():
    for goal in ("a sobriety tracker bot", "a sobriety tracker bot for me",
                 "an anxiety journal bot for teens"):
        found = pw.find(goal)
        assert found.subject and found.subject_notes == ["health"], goal
    # A diet subject is searched as before, whoever it is for.
    assert pw.find("a vegan meal planner bot for my family").subject == "vegan meal planner"


# -- names at the start of a sentence or alone on a line -----------------------------

_N = ["Liam", "Noah", "Emma", "Ava", "Mia", "Lucas", "Isla", "Theo"]
_LIST_FORMS = [
    "A chores bot.\nKids:\n{a}\n{b}",
    "A chores bot.\nKids:\n- {a}\n- {b}",
    "A chores bot.\nKids: {a}, {b}",
    "A chores bot\n{a}\n{b}",
    "Chores bot for:\n{a}\n{b}\n",
    "A chores bot. The kids are:\n1. {a}\n2. {b}",
    "A chores bot.\nMy kids:\n{a} (9)\n{b} (6)",
    "A chores bot.\nWho: {a} and {b}",
]
NAME_FORMS = [(f.format(a=_N[(2 * i) % 8], b=_N[(2 * i + 1) % 8]),
               [_N[(2 * i) % 8], _N[(2 * i + 1) % 8]]) for i, f in enumerate(_LIST_FORMS)]
NAME_FORMS += [
    ("Liam needs a chores bot.", ["Liam"]),
    ("Liam and Noah need a chore chart bot.", ["Liam", "Noah"]),
    ("Emma's chores bot", ["Emma"]),
    ("Help Ava with chores. A chores bot.", ["Ava"]),
    ("A chores bot. Lucas keeps forgetting his chores.", ["Lucas"]),
    ("Theo, Isla and I want a chores bot.", ["Theo", "Isla"]),
    ("Make a chores bot. Mateo is 9.", ["Mateo"]),
    ("A chores bot for Saanvi.", ["Saanvi"]),
    ("A chores bot that reminds Kenji", ["Kenji"]),
    ("A chores bot; kids = Wren + Bodhi", ["Wren", "Bodhi"]),
]


@pytest.mark.parametrize("goal,names", NAME_FORMS)
def test_all_18_name_forms_stay_on_this_computer(goal, names):
    assert len(NAME_FORMS) == 18
    phrases = [f"{n.lower()} chores" for n in names] + ["chore chart"]
    got = sent(goal, phrases)
    assert got == ["chore chart"], (goal, got)
    for words in (goal, "books about chores. " + goal):
        terms = gw.search_terms(words)
        assert not ({n.lower() for n in names} & set(terms)), (words, terms)
    sub = oq.subject_sendable(goal)
    assert sub is None or not ({n.lower() for n in names} & set(sub.phrase.split()))


def test_a_subject_word_at_the_start_of_a_sentence_is_still_the_subject():
    assert pw.find("Minecraft server bot for my kids").subject == "minecraft server"


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


@pytest.mark.parametrize("words,names", [
    ("Liam needs books about chores", {"liam"}),
    ("Theo, Isla and I want books on chores", {"theo", "isla"}),
    ("books about chores.\nKids:\nLiam\nNoah", {"liam", "noah"}),
    ("books about soccer for my son &#76;iam", {"liam", "iam"}),
    ("books about soccer for my son %4Ciam", {"liam", "iam"}),
    ("books about soccer for my son L‮мaiL", {"liam", "lmail", "lliam"}),
])
def test_the_add_page_never_sends_the_name(online_spy, words, names):
    from puffpai import add_more
    session = add_more.find(words, via="page", client="", online=True, only=["knowledge"])
    sent_words = {w for q in online_spy for w in q.lower().split()}
    offered = set(session.get("sends") or [])
    assert not (sent_words & names) and not (offered & names), (online_spy, offered)


# -- encoded names -------------------------------------------------------------------

@pytest.mark.parametrize("typed", ["&#76;iam", "&#x4C;iam", "%4Ciam", "&#37;4Ciam",
                                   "L‮мaiL", "L⁧iam⁩", "Li‏am"])
def test_a_name_typed_with_a_web_encoding_or_a_direction_control_is_held(typed):
    goal = "A soccer practice bot for my son " + typed
    assert sent(goal, ["liam soccer", "iam soccer", "soccer drills"]) == ["soccer drills"]


def test_decoding_reads_only_real_encodings():
    assert gw.decode_encoded("50% off at AT&T &amp; Barnes&Noble 100%") == \
        "50% off at AT&T & Barnes&Noble 100%"
    assert gw.decode_encoded("Zo%C3%AB") == "Zoë"
    assert gw.decode_encoded("%0Aliam") == " liam"


# -- the discovery run's real leak: her mom's dryer ----------------------------------

H3 = ("I bought a used Ender 3 printer at a garage sale in Duluth for $80 and I want to "
      "print replacement parts, like the broken knob on my mom's dryer. My mom Carol is "
      "78 and on a fixed income, so fixing things beats buying new. Right now the first "
      "layer keeps peeling up and I don't know what settings to change.")


def test_a_thing_her_people_own_is_held_until_she_ticks_it():
    held = {h["word"]: h for h in pw.find(H3).held}
    assert held["dryer"]["can_allow"] is True
    assert sent(H3, ["broken knob dryer", "dryer knob", "replacement part"]) == \
        ["replacement part"]
    # Her tick lets it out.
    got = oq.screen(["dryer knob"], goal=H3, allowed_words=["dryer"]).sendable
    assert [s.phrase for s in got] == ["dryer knob"]
    assert "barn" in [h["word"] for h in pw.find(
        "a band bot. We rehearse in my brother Gil's barn").held]


def test_the_job_heading_phrases_are_screened_with_the_goal():
    from puffpai.wizard import needs
    rows = needs._screen(["broken knob dryer", "knob replacement"], goal=H3, home=None,
                         allowed=[], hint="j2")
    states = {r["phrase"]: r["state"] for r in rows}
    assert states["broken knob dryer"] == "held"
    assert states["knob replacement"] == "out"
    fitness = ("I want a home fitness coach bot with reminders so I do it before my "
               "daughter Nadia gets home from work.")
    minecraft = ("I need the bot to walk me through setting it up on the old computer in our "
                 "den in Bakersfield.")
    assert sent(fitness, ["nadia reminder", "workout reminder"]) == ["workout reminder"]
    assert sent(minecraft, ["bakersfield server", "server setup"]) == ["server setup"]


# -- over-holding --------------------------------------------------------------------

BAND = ("I play bass in a cover band called The Loose Ends and we rehearse on Wednesdays "
        "in my brother Gil's barn outside Asheville. I'd like a band bot for our setlists "
        "and chord charts, who has learned which songs, a metronome and a tuner, gig "
        "dates, and dividing up the pay after each show.")
MINECRAFT = ("My grandson Rowan is turning 11 on March 14 and I promised him his own "
             "Minecraft server for the family to play on. I don't know anything about this "
             "stuff, so I need the bot to walk me through setting it up on the old computer "
             "in our den in Bakersfield and keep it running.")
BUDGET = ("My wife Tasha and I are drowning a little after the twins were born, and we "
          "want a family budget bot. We'd import our bank statements, set monthly limits "
          "by category, save for daycare and a new minivan, pay off $9,000 of credit card "
          "debt, and see simple charts of where the money goes.")


def test_ordinary_music_words_are_never_held():
    assert pw.private_goal_words(BAND) == {}
    held = [h["word"] for h in pw.find(BAND).held]
    assert "rehearse" not in held
    phrases = ["setlist manager", "chord chart generator", "metronome app", "digital tuner",
               "rehearsal schedule", "gig calendar"]
    assert sent(BAND, phrases) == phrases
    assert pw.dict_kind("rehearse") == "common"


def test_a_pronoun_and_a_word_are_never_one_glued_sensitive_word():
    assert pw.sensitive_phrase_hits(["me", "through"]) == []
    assert pw.sensitive_phrase_hits(["non", "binary"]) == [(("non", "binary"), "sex")]
    assert pw.sensitive_phrase_hits(["self", "harm"]) == [(("self", "harm"), "health")]


def test_the_brand_a_bot_is_about_is_its_subject():
    found = pw.find(MINECRAFT)
    assert found.subject == "minecraft server" and found.subject_notes == ["brand"]
    assert "through" not in [h["word"] for h in found.held]
    phrases = ["minecraft server setup", "minecraft server security", "minecraft whitelist"]
    assert sent(MINECRAFT, phrases) == phrases
    assert leaked(MINECRAFT, ["rowan", "bakersfield", "hazel", "owen", "11", "14"]) == []


@pytest.mark.parametrize("goal", [
    "a bot for my Liam soccer games",
    "I want a bot for the Kenji soccer team schedule",
    "I need help planning the Kenji birthday party",
    "Help Theo Smith with math, the bot should quiz him",
])
def test_a_name_is_never_read_as_a_brand_subject(goal):
    subject = pw.find(goal).subject
    assert not ({"liam", "kenji", "theo", "smith"} & set(subject.split())), subject


def test_3d_is_a_kind_of_thing_not_a_number():
    assert sent(H3, ["3d print part", "3d printer settings", "ender 3"]) == \
        ["3d print part", "3d printer settings"]
    # Her own "4D" apartment is still a number about her.
    assert sent("a chores bot for apartment 4D", ["4d chores"]) == []


def test_money_words_of_her_own_job_are_a_budget_bots_subject():
    found = pw.find(BUDGET)
    assert found.subject == "family budget" and "debt" in found.subject_also
    phrases = ["bank statement importer", "monthly budget template", "spending limit setter",
               "daycare savings spreadsheet", "debt payoff planner"]
    assert sent(BUDGET, phrases) == phrases
    assert leaked(BUDGET, ["tasha", "twins", "9", "000"]) == []


@pytest.mark.parametrize("goal", [
    "a budget bot, we are in debt",
    "a budget bot to pay off my brothers gambling debt",
    "a budget bot to pay off our debt",
    "a family budget bot. My husband has debt from his bankruptcy.",
    "a recipe bot, I have debt",
])
def test_a_money_story_stays_private(goal):
    assert sent(goal, ["debt payoff planner"]) == [], goal


# -- the job after a condition -------------------------------------------------------

@pytest.mark.parametrize("goal,private", [
    ("a bot to help my sister with anorexia count calories", ["calories", "anorexia"]),
    ("a bot to help my son with ocd count his rituals", ["rituals"]),
    ("a bot to help my brother with alcoholism avoid bars", ["bars", "alcoholism"]),
    ("a bot to help my wife with depression stop crying", ["crying"]),
    ("a bot to help my husband with ptsd avoid fireworks", ["fireworks"]),
    ("a bot to help my teen with bulimia stop purging", ["purging"]),
    ("a bot to help my brother with gambling problems avoid casinos", ["casinos", "casino"]),
    ("a bot for my kid with diabetes to count carbs", ["carbs"]),
    ("a bot to help my dad with schizophrenia ignore voices", ["voices"]),
])
def test_the_job_after_her_persons_condition_stays_private(goal, private):
    assert leaked(goal, private) == []


def test_the_job_is_searched_when_it_is_the_subject():
    goal = "a tomato growing bot for my uncle with arthritis"
    assert pw.find(goal).subject == "tomato growing"
    assert sent(goal, ["tomato growing"]) == ["tomato growing"]


# -- "Kept private" lists every word held --------------------------------------------

def test_a_relatives_news_is_held_and_listed():
    from puffpai.wizard import needs
    goal = ("I want to build a wedding planner bot. My brother just got engaged and "
            "there is a lot to do and plan.")
    p = needs._personal(goal, None)
    assert p["private"] == ["brother", "engaged"]
    assert sent(goal, ["engaged couple gifts", "wedding planner"]) == ["wedding planner"]
    # Her own news and the words elsewhere stay ordinary words.
    assert pw.find("We just moved, a moving checklist bot").held == []
    assert pw.find("a retirement planner bot for my dad").held == []


# -- device lines --------------------------------------------------------------------

def _devices(goal: str) -> list[str]:
    return [i.get("id") for i in live.proposal_items(goal)]


@pytest.mark.parametrize("goal", ["my devices: Hue, Sonos, Ecobee",
                                  "my devices: Hue / Sonos / Ecobee",
                                  "my devices: Hue; Sonos and Ecobee"])
def test_a_heading_and_its_list_on_one_line(goal):
    assert _devices(goal) == ["device:ha:hue", "device:ha:sonos", "device:ha:ecobee"]


@pytest.mark.parametrize("goal", [
    "chores:\n- clean the ring\n- water the nest\n- feed the echo",
    "todo: clean the ring, water the nest",
    "shopping: apples, ring pops, nest eggs",
    "my devices: grandmas ring, birds nest",
])
def test_chores_and_shopping_lists_get_no_line(goal):
    assert _devices(goal) == []


def test_a_verb_that_starts_a_task_is_not_what_the_bot_is_about():
    assert live._task_verbs("chores:\n- clean the ring\n- water the nest") == \
        {live.stem("clean"), live.stem("water")}


# -- turned-down backgrounds ---------------------------------------------------------

@pytest.mark.parametrize("words,bg", [
    ("stars no way", "stars"), ("clouds no no", "clouds"), ("stars nope not for me", "stars"),
    ("clouds nah never", "clouds"), ("stars nope absolutely not", "stars"),
    ("clouds no thanks", "clouds"),
])
def test_a_no_said_again_still_turns_it_down(words, bg):
    named, down = looks_find._background_words(words)
    assert named is None and bg in down


def test_a_no_before_another_background_is_about_that_one():
    named, down = looks_find._background_words("clouds no stars")
    assert named == "clouds" and down == {"stars"}


# -- follow-ups found while replaying the acceptance goals ---------------------------

def test_her_childrens_news_holds_the_children_too():
    found = {h["word"] for h in pw.find(BUDGET).held}
    assert {"twins", "born"} <= found
    assert leaked(BUDGET, ["twins"]) == []


@pytest.mark.parametrize("goal,thing", [
    ("I want the bot to help plan a stargazing night for his Cub Scout den.", "den"),
    ("a garden bot. My dad Walt needs a new grip for his walker", "walker"),
    ("whether my mom's pill cabinet has been opened today, a dashboard bot", "cabinet"),
])
def test_a_household_thing_after_his_or_her_is_held_until_ticked(goal, thing):
    rows = {h["word"]: h for h in pw.find(goal).held}
    assert rows[thing]["can_allow"] is True
    assert sent(goal, [f"{thing} ideas"]) == []


@pytest.mark.parametrize("goal,searched", [
    ("plan my son's birthday party, a party planner bot", ["birthday party"]),
    ("a recital planner bot for my daughter's piano recital", ["piano recital"]),
    ("fix his old car, a car repair bot", ["car repair"]),
])
def test_her_peoples_events_and_the_subject_stay_searchable(goal, searched):
    assert sent(goal, searched) == searched


def test_an_ordinary_verb_at_the_start_of_a_sentence_is_not_a_name():
    assert pw.dict_kind("remind") == "common"
    assert pw.find("Remind me when to water the plants").held == []


def test_relatives_in_general_are_an_audience_not_her_people():
    goal = "a dating safety app for single parents"
    assert pw.find(goal).subject == "dating safety"
    assert pw.find("a dating safety app for my parents").subject == ""


# -- held-out replay: what the owned thing is, and which baby it is ------------------

def test_the_words_in_front_of_an_owned_thing_are_held_with_it():
    goal = ("I want a simple screen for the kitchen tablet that shows the thermostat, the "
            "porch lights, and whether my mom's pill cabinet has been opened today.")
    rows = {h["word"]: h for h in pw.find(goal).held}
    assert rows["pill"]["can_allow"] is True and rows["cabinet"]["can_allow"] is True
    assert sent(goal, ["pill reminder", "cabinet sensor", "kitchen tablet dashboard"]) == \
        ["kitchen tablet dashboard"]


def test_which_baby_her_people_are_having_is_held():
    goal = ("My granddaughter Maisie is having her first baby in April, and I want to knit a "
            "blanket and a little hat before then.")
    assert sent(goal, ["first baby", "baby blanket pattern"]) == ["baby blanket pattern"]
    # Which of her own babies it is ("my second
    # baby") is held too;
    # the kind of bot is still searched.
    goal = "I'm having my first baby, a baby name bot"
    assert sent(goal, ["first baby", "baby name"]) == ["baby name"]


# -- the subject rule for health: her own named subject goes, a story stays ---------

PREGNANCY = ("I'm 14 weeks pregnant with my second baby and my due date is April 9. I had "
             "gestational diabetes with my first, Mason, so I want help keeping track of my "
             "blood sugar numbers, my appointments with Dr. Okafor in Fresno, and what I "
             "should be eating each week. My partner Jordan works nights, so reminders would "
             "really help.")


def test_her_own_health_subject_is_searched_with_its_note():
    # Owner's answer 3: health subject words are sent with the plain note.
    goal = "a pregnancy tracker bot for me"
    found = pw.find(goal)
    assert found.subject == "pregnancy tracker" and "health" in found.subject_notes
    assert sent(goal, ["pregnancy tracker", "pregnancy meal planner"]) == \
        ["pregnancy tracker", "pregnancy meal planner"]


def test_her_own_health_story_with_no_kind_of_bot_sends_its_subject_with_the_note():
    # The subject rule for every held class:
    # the goal names no
    # kind of bot, but her jobs are about her own health ("keeping track of
    # my blood sugar numbers"), so her health subject words are sent with the
    # plain note; only her details (names, the doctor, the town, dates,
    # which baby) are held. The earlier restrictive reading held 15 of 25
    # phrases, "pregnancy meal planner" among them.
    found = pw.find(PREGNANCY)
    assert found.subject == "blood sugar" and found.subject_notes == ["health"]
    assert sent(PREGNANCY, ["blood sugar tracker", "pregnancy meal planner",
                            "meal planner app"]) == [
        "blood sugar tracker", "pregnancy meal planner", "meal planner app"]
    assert leaked(PREGNANCY, ["mason", "okafor", "fresno", "jordan", "partner",
                              "april", "second"]) == []
    # One of her people's pregnancy is never a subject either.
    assert sent("my wife is pregnant, a pregnancy tracker bot", ["pregnancy tracker"]) == []
