"""Names and held words in the outbound screen.

* a child's name at the start of a sentence, alone, or in lower or upper case
  was sent (the subject of "X needs/wants/is/and Y"); a word used as a
  person's name anywhere is now held everywhere;
* over-holding starved discovery: the bot's own subject words of every held
  class (health with the plain note, diet, the model of the car, the jobs of
  a job hunt) are sent, only her personal details are held; a held word never
  leaves a fragment behind;
* the over-holding control ("my uncle with arthritis grow tomatoes") sent
  nothing again; a relative who died ("my late aunt") and "our tenth
  anniversary next Friday" leaked; "old den" left from "in our den";
* the "Kept private" line listed numbers and words she could tick.

Every probe is offline: the model is a fake that repeats the private words,
the outbound screen and the needs step read files on this computer, nothing
is sent anywhere.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json

import pytest

from puffpai.catalogs import goal_words as gw
from puffpai.catalogs import personal_words as pw
from puffpai.security import outbound_query as oq

CFG = {"ollama_url": "http://127.0.0.1:11435", "chosen_model": "qwen2.5:7b-instruct"}


def sent(goal: str, phrases: list[str]) -> list[str]:
    return [s.phrase for s in oq.screen(phrases, goal=goal).sendable]


class _Echo:
    def __init__(self, raw: str) -> None:
        self.raw = raw

    def chat_stream(self, *a, **k):
        yield self.raw

    def prewarm(self, *a, **k):
        return None


@pytest.fixture
def wizard(monkeypatch):
    """needs.run with a model that answers *searches* for one job."""
    from puffpai.wizard import needs, needs_llm

    def run(goal: str, searches: list[str], kind: str = "helper bot") -> tuple[dict, list[str]]:
        raw = json.dumps({"kind": kind, "asked": ["job one"], "also_needed": [],
                          "jobs": [{"name": "job one", "does": "does it",
                                    "parts": [{"kind": "code-app", "search": searches}]}]})
        monkeypatch.setattr(needs_llm, "make_backend", lambda *a, **k: _Echo(raw))
        monkeypatch.setattr(needs_llm, "choose_prompt", lambda m, s: needs_llm.PROMPT_V5)
        needs._CACHE.clear()
        n = needs.run(goal, cfg=CFG)
        return n, [s.phrase for s in needs.sends(n, goal=goal)]
    yield run
    needs._CACHE.clear()


# -- names at the start of a sentence, in any case ---------------------------------------

#: Held-out names and sentence-start names.
START_NAMES = [
    ("Mia and Leo need a reading bot", ["mia", "leo"], "reading"),
    ("liam needs a chores bot", ["liam"], "chores"),
    ("LIAM NEEDS A CHORES BOT", ["liam"], "chores"),
    ("River needs a swim training bot.", ["river"], "swim training"),
    ("Sage keeps losing her retainer. A reminder bot please.", ["sage"], "reminder"),
    ("Mason builds Lego every night, make a Lego bot.", ["mason"], "lego"),
    ("Hunter and Brooks share a homework bot.", ["hunter", "brooks"], "homework"),
    ("Juniper loves horses. A horse facts bot.", ["juniper"], "horse facts"),
    ("Bear wants a dinosaur bot.", ["bear"], "dinosaur"),
    ("Willow and I want a baking bot.", ["willow"], "baking"),
    ("Rowan practices violin. A violin practice bot.", ["rowan"], "violin"),
    ("Phoenix wants a Pokemon bot.", ["phoenix"], "pokemon"),
    ("Ivy and Hazel fight over chores. Chores bot!", ["ivy", "hazel"], "chores"),
    ("Ezra, Ottilie and Rafe need a chores bot.", ["ezra", "ottilie", "rafe"], "chores"),
    ("Help Sage remember her retainer, a reminder bot", ["sage"], "reminder"),
]


@pytest.mark.parametrize("goal,names,topic", START_NAMES)
def test_a_name_that_starts_the_sentence_is_held(goal, names, topic):
    held = {h["word"] for h in pw.find(goal).held}
    assert set(names) <= held, (goal, held)
    phrases = [f"{n} {topic}" for n in names] + [f"{topic} for {names[-1]}", topic]
    got = sent(goal, phrases)
    assert not any(set(p.split()) & set(names) for p in got), got


@pytest.mark.parametrize("goal,names,topic", START_NAMES[:6])
def test_the_wizard_never_sends_a_sentence_start_name(wizard, goal, names, topic):
    _n, sends = wizard(goal, [f"{n} {topic}" for n in names] + [topic])
    assert not any(set(p.split()) & set(names) for p in sends), sends


def test_a_word_used_as_a_name_is_held_everywhere_even_in_the_subject():
    goal = "River needs help. A river safety bot."
    f = pw.find(goal)
    assert "river" not in f.subject.split()
    assert sent(goal, ["river safety", "water safety"]) == ["water safety"]


@pytest.mark.parametrize("goal,word", [
    ("Track oil changes and tire rotations", "track"),
    ("Students need a study timer bot", "students"),
    ("Help plan the wedding, a wedding planner bot", "plan"),
    ("Chore charts for my kids, a chores bot", "chore"),
    ("Everyone needs a budget bot", "everyone"),
    ("Remind me to stretch, a fitness bot", "remind"),
])
def test_an_ordinary_word_at_the_start_is_not_a_name(goal, word):
    assert word not in {h["word"] for h in pw.find(goal).held}


# -- a relative who died, an anniversary, "next" ----------------------------------------

def test_a_late_relative_is_held_with_what_a_model_says_about_it(wizard):
    goal = ("A bot to sell my late aunt's sewing machines. I'll need to be able to price "
            "my late aunt's machines and list them online.")
    rows = {h["word"]: h for h in pw.find(goal).held}
    assert rows["late"]["can_allow"] is False
    assert sent(goal, ["late sewing", "estate sale", "sewing machine resale"]) == [
        "sewing machine resale"]
    _n, sends = wizard(goal, ["late aunt sewing machines", "estate sale", "late sewing"])
    assert not any({"late", "estate", "aunt"} & set(p.split()) for p in sends), sends
    # A late bus is not a death.
    assert pw.find("a bot to warn me when the bus is late").held == []


def test_an_anniversary_count_and_next_never_leave():
    goal = "A bot for our tenth anniversary next Friday"
    assert {h["word"] for h in pw.find(goal).held} >= {"tenth"}
    assert sent(goal, ["tenth anniversary", "anniversary next", "anniversary ideas"]) == [
        "anniversary ideas"]
    assert oq.screen_words(["book", "tenth", "anniversary", "next"], goal=goal) == [
        "book", "anniversary"]
    assert sent("a golden anniversary party bot for my parents",
                ["golden anniversary", "party ideas"]) == ["party ideas"]


# -- places of hers, products, fragments -------------------------------------------------

def test_a_place_of_hers_is_held_and_leaves_no_fragment(wizard):
    goal = ("I need the bot to walk me through setting it up on the old computer in our den "
            "in Bakersfield, a minecraft server bot")
    assert "den" in {h["word"] for h in pw.find(goal).held}
    assert sent(goal, ["old den", "minecraft server old den", "minecraft server old",
                       "minecraft server setup"]) == ["minecraft server setup"]
    # "from our bank exports" and "holes in my lettuce" are no places.
    assert pw.find("a budget bot to track spending from our bank exports").held == []
    assert pw.find("a garden bot, something chews holes in my lettuce").held == []


@pytest.mark.parametrize("phrase", ["to deals", "chunky pattern aren", "worth pointing",
                                    "minecraft server old", "later kick count"])
def test_a_piece_of_a_phrase_is_never_sent(phrase):
    assert sent("a helper bot", [phrase]) == []


def test_a_held_word_comes_off_a_phrase_only_at_its_edge(monkeypatch):
    from puffpai.wizard import needs
    goal = "A study bot. I am a nursing student at Baylor"
    rows = needs._screen(["baylor study guide", "study baylor guide"], goal=goal, home=None,
                         allowed=[], hint="j1")
    out = [r["phrase"] for r in rows if r["state"] == "out"]
    assert "study guide" in out and not any("baylor" in p for p in out)
    assert len([p for p in out if p == "study guide"]) == 1


def test_home_assistant_is_the_product_not_the_kind_of_bot():
    goal = ("My husband Dev put Home Assistant on a little box in our house in Asheville, and "
            "now I'm the one stuck fixing the dashboard.")
    f = pw.find(goal)
    assert (f.subject, f.subject_notes) == ("home assistant", ["brand"])
    assert sent(goal, ["home assistant dashboard", "dev dashboard", "asheville dashboard",
                       "house dashboard"]) == ["home assistant dashboard"]
    assert pw.subject_of("a Home Assistant dashboard helper bot") == "home assistant dashboard"
    # Never a person's name: "for Grace Assistant" is no product.
    assert pw.find("a chores bot for my son Will. Ask Will Assistant").subject != "will assistant"


def test_prints_coming_out_stringy_is_not_a_private_story():
    goal = "my prints keep coming out stringy, a helper bot for 3d printing"
    assert pw.private_goal_words(goal) == {}
    assert pw.private_goal_words("a journal bot to log my coming out") != {}


# -- the over-holding control and the job after a condition ------------------------------

@pytest.mark.parametrize("goal,job", [
    ("a bot to help my uncle with arthritis grow tomatoes on the balcony",
     {"grow", "tomato", "balcony"}),
    ("a bot for my wife with migraines to find quiet hiking trails", {"hiking", "trail"}),
    ("a bot to help my mom with parkinsons sort her stamp collection", {"stamp", "collection"}),
    ("a bot to help my daughter with dyslexia read comic books", {"comic", "book"}),
    ("a bot for my dad with copd to plan easy fishing trips", {"fishing", "trip"}),
])
def test_a_pastime_after_a_condition_is_searched(goal, job):
    terms = set(gw.search_terms(goal))
    assert job <= terms, terms
    condition = goal.split(" with ")[1].split()[0]
    assert condition not in terms
    assert {h["word"] for h in pw.find(goal).held} == {condition}


@pytest.mark.parametrize("goal,story", [
    ("a bot to help my sister with anorexia count calories", {"calories", "calorie"}),
    ("a bot to help my brother with alcoholism avoid bars", {"bars", "bar"}),
    ("a bot to help my wife with depression stop crying", {"crying"}),
    ("a bot to help my teen with bulimia stop purging", {"purging"}),
    ("a bot for my son with adhd to take his pills", {"pills", "pill"}),
    ("a bot to help my dad with schizophrenia ignore voices", {"voices", "voice"}),
])
def test_a_job_that_manages_the_condition_stays_held(goal, story):
    assert not (set(gw.search_terms(goal)) & story)
    assert sent(goal, sorted(story)) == []


# -- the bot's subject words of every held class are sent --------------------------------

PREGNANCY = ("I'm 14 weeks pregnant with my second baby and my due date is April 9. I had "
             "gestational diabetes with my first, Mason, so I want help keeping track of my "
             "blood sugar numbers, my appointments with Dr. Okafor in Fresno, and what I "
             "should be eating each week. My partner Jordan works nights, so reminders would "
             "really help.")


def test_her_own_health_subject_is_sent_with_the_plain_note():
    f = pw.find(PREGNANCY)
    assert f.subject == "blood sugar" and f.subject_notes == ["health"]
    # The wizard shows the health note: the subject is not a "private" one.
    assert pw.subject_note_kind(PREGNANCY) != "private"
    wanted = ["pregnancy meal planner", "pregnancy reminder", "blood sugar tracker",
              "appointment reminder app", "diabetes log"]
    assert sent(PREGNANCY, wanted) == wanted
    # Her details stay hers.
    for p in ["mason pregnancy", "okafor appointment", "fresno clinic", "jordan reminder",
              "april due date", "14 weeks", "second baby"]:
        assert sent(PREGNANCY, [p]) == [], p


def test_the_kept_private_line_never_names_a_subject_word():
    from puffpai.wizard import needs
    p = needs._personal(PREGNANCY, None)
    assert "pregnant" not in p["private"] and "diabetes" not in p["private"]
    assert not any(ch.isdigit() for w in p["private"] for ch in w)


@pytest.mark.parametrize("goal,wanted,held", [
    ("I am a nursing student. My study partner Denise and I want a study buddy bot with a "
     "pomodoro timer, drug name flashcards, and a shared to-do list.",
     ["drug name flashcard", "pharmacology drug flashcards"], ["denise flashcards"]),
    ("We just adopted a rescue mutt named Pepper. I want a pet bot that tracks her vet "
     "visits, vaccines and medicine, and reminds my daughter to walk her.",
     ["pet medication reminder", "medicine tracker"], ["pepper medicine"]),
    ("I got laid off from my supervisor job at Hendricks Distribution in Joliet on August "
     "29. I want help rewriting my resume, finding openings, and practicing interview "
     "questions because I freeze up.",
     ["resume builder", "mock interview app", "interview preparation"],
     ["laid off resume", "hendricks interview", "interview freeze"]),
    ("I want a car bot for my 2016 Subaru Outback and my son 2020 Mazda3. Look up recalls.",
     ["mazda3 recall", "subaru outback recall"], ["2016 subaru outback", "son mazda3"]),
    ("I went vegan in June and I'd like quick weeknight dinners with enough protein.",
     ["vegan meal planner", "vegan dinner ideas"], ["june vegan"]),
])
def test_the_bots_subject_words_are_sent_and_her_details_held(goal, wanted, held):
    assert sent(goal, wanted) == wanted
    for p in held:
        assert sent(goal, [p]) == [], p


@pytest.mark.parametrize("goal,private", [
    ("a visual schedule bot for my nephew, spectrum kid", "spectrum"),
    ("a reading bot for my son, dyslexia support worksheets", "dyslexia"),
    ("a pet bot for my son with diabetes. Track insulin and treats", "insulin"),
    ("I have early Parkinsons. I want help planning my vegetable garden", "parkinsons"),
])
def test_a_sensitive_word_of_her_people_or_of_no_job_stays_held(goal, private):
    assert sent(goal, [f"{private} tracker", private]) == []


def test_a_private_story_with_a_kind_of_bot_still_sends_only_the_kind():
    goal = ("I want a planner bot. I'll need to plan my chemo appointments and my radiation "
            "schedule")
    assert sent(goal, ["chemo appointment", "appointment planner", "planner"]) == ["planner"]
    goal = ("I want a safety planner bot. My husband has beaten me. I'll need to find a "
            "shelter, keep a log of the bruises, and plan my escape")
    assert sent(goal, ["escape plan", "bruise log", "safety planner"]) == ["safety planner"]


# -- the "Kept private" line --------------------------------------------------------------

def test_the_line_lists_held_words_once_and_no_numbers():
    from puffpai.wizard import needs
    goal = ("I bought a used Ender 3 printer at a garage sale in Duluth for $80 and I want to "
            "print replacement parts, like the broken knob on my mom's dryer. My mom Carol is "
            "78 and on a fixed income.")
    p = needs._personal(goal, None)
    ticks = {h["word"] for h in p["held"]}
    assert set(p["private"]) == {"mom", "income"}
    assert ticks == {"duluth", "dryer", "carol"}
    assert not set(p["private"]) & ticks


def test_the_wedding_line_never_offers_a_word_it_keeps():
    from puffpai.wizard import needs
    goal = ("I want to build a wedding planner bot. My brother just got engaged and "
            "there is a lot to do and plan.")
    p = needs._personal(goal, None)
    assert p["private"] == ["brother", "engaged"]
    assert p["held"] == []


# -- lists under a people label; the Add page reads her words as typed --------------------

@pytest.mark.parametrize("goal,names", [
    ("A reading log bot.\nKids:\nMarsh\nOcean", {"marsh", "ocean"}),
    ("A chores bot. Players: Reed, Pearl and Sky.", {"reed", "pearl", "sky"}),
])
def test_a_list_under_a_people_label_is_names(goal, names):
    assert names <= {h["word"] for h in pw.find(goal).held}
    assert sent(goal, [f"{n} reading" for n in sorted(names)] + ["reading log"]) == [
        "reading log"]


def test_a_shopping_list_is_not_names():
    assert pw.find("Groceries: milk, eggs and bread. A shopping bot").held == []


def test_the_add_page_screens_her_words_as_typed(monkeypatch):
    """The Add page scrubs "chemo" before reading her words as jobs; the
    screen still reads them as typed, so "radiation" never leaves."""
    from puffpai import add_more
    from puffpai.wizard import find_parts
    goals: list[str] = []
    real = find_parts.plan_items

    def spy(needs, *, goal, **kw):
        goals.append(goal)
        return {"items": [], "notes": {}, "sends": []}
    monkeypatch.setattr(find_parts, "plan_items", spy)
    monkeypatch.setattr(add_more, "online_allowed", lambda: True)
    add_more.reset()
    words = ("I want a planner bot. I'll need to plan my chemo appointments and my "
             "radiation schedule")
    session = add_more.find(words, via="page", client="", online=True, only=["apps_code"])
    assert not any("radiation" in str(p) for p in session.get("sends") or [])
    assert goals and all("chemo" in g for g in goals)
    add_more.reset()
    assert real is not None


def test_a_job_word_that_is_searched_is_never_cut_out_of_a_phrase(monkeypatch):
    """"drug name flashcards": "drug" is a word of her job, searched, so the
    rule phrases keep it ("study drug name flashcard"), never the fragment
    "study name flashcard"."""
    from puffpai.wizard import needs, needs_llm
    monkeypatch.setattr(needs_llm, "choose_prompt", lambda m, s: "rules")
    needs._CACHE.clear()
    goal = ("My study partner Denise and I want a study buddy bot with a pomodoro timer, "
            "drug name flashcards, and a shared to-do list.")
    n = needs.run(goal, cfg=CFG)
    phrases = [s["phrase"] for j in n["jobs"] for p in j["parts"] for s in p["search"]
               if s["state"] == "out"]
    assert any("drug name flashcard" in p for p in phrases), phrases
    assert not any(p.endswith("name flashcard") and "drug" not in p for p in phrases), phrases
    assert not any("denise" in p for p in phrases)
    needs._CACHE.clear()
