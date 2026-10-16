"""Sensitive words, names and over-holding in the outbound screen.

* sensitive words: sweeps (129 words, 106 phrases, a
  second set of lists and "groomed") hold every word in three goal shapes, with two named
  exceptions that are everyday words ("weed" in a garden, "plan b");
* no part of a held name is searched or sent (apostrophes, camel case,
  hyphens, a hyphen or name after a line break, glued or cut-off forms);
* over-holding: ordinary words are not listed as kept private;
* device lines: every thing in her list gets its line;
* the right plain note for a sensitive subject, end to end in the wizard;
* turned-down backgrounds are never named;
* the time of her own plans (seasons, holidays, weekends, days such as
  "Memorial Day") is never sent unless it is the bot's subject (discovery
  bar m3: "summer" was sent for "saving for a summer trip").

Goal-agnostic: made-up sentences in many shapes. Offline: the conftest
socket guard is on for every test here, and the model is a recorded or
written reply.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from puffpai.catalogs import goal_words as gw, live, personal_words as pw
from puffpai.security import outbound_query as oq

FIX = Path(__file__).parent / "fixtures"


def _optional(modname: str):
    try:
        return importlib.import_module(modname)
    except ModuleNotFoundError as e:
        if e.name is not None and (e.name == modname or modname.startswith(e.name + ".")):
            return None
        raise


def _sent(result) -> list[str]:
    return [s.phrase for s in result.sendable]


def _forms(words) -> set[str]:
    out: set[str] = set()
    for w in words:
        out |= {w, gw.singular(w)}
    return out


# -- sensitive words: the sweeps (security major) ---------------------------------------

#: The validator's p1 sweep (the directive's 24 probe words and more).
P1 = """anxiety sobriety herpes abortion alcoholism opioid methadone aids rape abuse transgender
gay therapy hospice grief divorce bankruptcy immigration eviction religion salary custody
parole depression schizophrenia bipolar ptsd hiv std chlamydia miscarriage infertility ivf
lesbian bisexual nonbinary queer muslim jewish mormon atheist catholic hindu probation felony
arrest lawsuit deportation asylum undocumented foreclosure unemployed debts loans payday
welfare stalker assault battered funeral widow widowed chemo chemotherapy cancer dementia
alzheimers autism adhd rehab relapse overdose suicide selfharm incarcerated jail prison lgbt
lgbtq trans pregnant pregnancy sexting stds hivpositive gambling addicted addict cocaine heroin
meth marijuana insulin dialysis abused raped divorced evicted bankrupt deported paroled
grieving mourning overdosed cheated affair bankruptcies incest molested pedophile colonoscopy
vasectomy hysterectomy psychiatrist counseling counselling antidepressants prozac zoloft xanax
adderall suboxone narcan hepatitis tuberculosis lupus epilepsy seizures groomed""".split()

#: The validator's p3 sweep (phrases), word for word.
P3 = ["gastric bypass", "tummy tuck", "lap band", "c section", "egg freezing",
      "hip replacement", "knee replacement", "radiation treatment", "breast implants",
      "facelift", "botox", "liposuction", "sex change", "top surgery", "hormone therapy",
      "restraining order", "dui", "court date", "food stamps", "snap benefits", "wic",
      "unemployment", "laid off", "got fired", "wage garnishment", "repossession",
      "debt collectors", "homeless", "homeless shelter", "nursing home", "memory care",
      "sperm donor", "surrogate", "birth control", "morning after pill", "std test",
      "pap smear", "mammogram", "prostate", "erectile dysfunction", "viagra", "hearing aids",
      "wheelchair", "amputation", "prosthetic", "bedwetting", "incontinence", "hemorrhoids",
      "lice", "scabies", "eating disorder", "bulimia", "binge eating", "cutting", "self harm",
      "suicidal", "ptsd", "sober living", "aa meetings", "twelve steps", "12 step",
      "halfway house", "juvie", "juvenile detention", "child protective services", "cps",
      "foster kids", "adoption", "green card", "visa overstay", "daca", "refugee", "quran",
      "torah", "hijab", "ramadan", "mosque", "synagogue", "kosher", "halal", "coming out",
      "polyamorous", "kink", "bdsm", "onlyfans", "porn", "strip club", "dating app", "tinder",
      "grindr", "sugar daddy", "loans", "cheated", "affair", "stillborn", "stillbirth",
      "widower", "bereavement", "obituary", "cremation", "burial", "hit me", "beats me",
      "yells at me"]

#: A wider set of lists, so nothing earlier slips back.
ROUND4 = ["miscarried", "transitioning", "incest", "pedophile", "kidnapped", "murdered",
          "prostitution", "polyamory", "swinger", "dyke", "fag", "mastectomy", "biopsy",
          "prognosis", "euthanized", "orphaned", "medicaid", "sabbath", "communion",
          "kabbalah", "satanism", "episcopal", "orthodox", "public defender", "credit score",
          "hysterectomy", "tonsillectomy", "colonoscopy", "rhinoplasty", "bronchitis",
          "melanoma", "leukemia", "fibromyalgia", "agoraphobia", "remission", "metastatic",
          "hospitalized", "anorexia", "ocd", "adultery", "detransitioning", "polygamy",
          "shabbat", "eucharist", "scientology", "tithing", "defendant", "burglary",
          "extortion", "guardianship", "arraigned", "medicare", "furloughed", "homicide",
          "manslaughter", "abducted", "stabbed", "gaslighting", "eulogy", "autopsy",
          "euthanasia", "social security", "food bank", "sex worker", "hate crime",
          "foster care", "heart attack", "chronic pain", "bar mitzvah", "life support",
          "terminally ill"]

#: Everyday words in these sweeps: "weed" (a garden), "plan b" (a backup
#: plan). In her
#: story shapes they are held too, by the sentence's shape, not a list.
EVERYDAY = {"weed", "plan b"}

SWEEP = list(dict.fromkeys(P1 + P3 + ROUND4 + sorted(EVERYDAY)))

TEMPLATES = [
    "a meal planner for our family because my wife deals with {w}",
    "a simple helper bot for our home because of my {w}",
    "I want a garden planner bot. My wife and I are dealing with {w} lately.",
]
#: The validator's own sentence for its p3 phrases ("got fired", "yells at
#: me", "cutting"): the other shapes do not read as English for them.
P3_TEMPLATE = TEMPLATES[1]


def test_the_sweep_is_the_size_the_directive_measured_and_more():
    assert len(SWEEP) >= 157 + 24
    assert EVERYDAY <= set(SWEEP)


@pytest.mark.parametrize("item", SWEEP)
def test_a_sensitive_word_or_phrase_never_leaves_in_any_goal_shape(item):
    words = item.split()
    private = _forms(words)
    shapes = TEMPLATES if item in P1 or item in ROUND4 or item in EVERYDAY else [P3_TEMPLATE]
    for template in shapes:
        goal = template.format(w=item)
        terms = gw.search_terms(goal)
        assert not set(terms) & private, (goal, terms)
        phrases = [item, f"{item} garden", f"meal {item}"] + words + terms
        sent = _sent(oq.screen(phrases, goal=goal))
        leaked = [s for s in sent if _forms(s.split()) & private]
        assert not leaked, (goal, leaked)


@pytest.mark.parametrize("goal,private", [
    ("a meal planner bot because my wife smokes weed every night", {"weed"}),
    ("a simple helper bot for our home because of my weed", {"weed"}),
    ("a budget bot, we buy weed gummies too often", {"weed", "gummies"}),
    ("a journal bot for my morning after pill and my cycle", {"morning", "pill"}),
    ("a meal planner because of my cutting and my drinking", {"cutting"}),
    ("a chore bot since my husband got fired from the plant", {"fired"}),
    ("a chat bot, I met him on tinder last year", {"tinder"}),
])
def test_the_private_use_of_an_everyday_word_is_held(goal, private):
    terms = gw.search_terms(goal)
    assert not set(terms) & private, terms
    sent = _sent(oq.screen(sorted(private) + [" ".join(sorted(private))] + terms, goal=goal))
    assert not [s for s in sent if set(s.split()) & private], sent


@pytest.mark.parametrize("goal,kept", [
    ("A garden bot to tell me when to pull weeds and water the tomatoes.", "weed"),
    ("A woodworking bot, I need a new cutting board design.", "cutting"),
    ("A kiln firing schedule bot for my pottery, the pieces get fired twice.", "pottery"),
    ("A library bot that tracks book loans and due dates.", "loan"),
    ("A chess bot that catches cheating in online games.", "cheating"),
    ("A database helper bot that explains surrogate keys and joins.", "surrogate"),
    ("A whale facts bot for kids, sperm whales and orcas.", "sperm"),
    ("A vet clinic bot to schedule surgeries and checkups for dogs.", "checkup"),
    ("A pet adoption finder bot for our local shelter.", "adoption"),
    ("A survival skills bot, how to find dry tinder and start a fire.", "tinder"),
    ("A recipe bot for spring rolls and dumplings.", "spring"),
    ("A code bot that explains spring boot settings.", "spring"),
    ("A bot that tells me when the kids fall asleep.", "fall"),
])
def test_an_everyday_word_in_an_ordinary_goal_is_still_searched(goal, kept):
    """Over-holding (directive): ordinary words are not held."""
    assert kept in gw.search_terms(goal) or kept in pw.find(goal).subject.split(), \
        (goal, gw.search_terms(goal), pw.find(goal).subject)


def test_a_sensitive_subject_read_from_her_own_thing_is_not_searched():
    """"... helper bot ... because of my dating app": the last bot noun is
    her own app, not the bot. A sensitive subject there sends nothing."""
    goal = "a simple helper bot for our home because of my dating app"
    assert pw.find(goal).subject == ""
    assert oq.subject_sendable(goal) is None
    assert _sent(oq.screen(["dating", "dating app", "home"], goal=goal)) == []
    goal = "a meal planner for our family because my wife deals with dating app"
    assert pw.find(goal).subject == ""
    assert "dating" not in gw.search_terms(goal)
    # One bot noun: the sensitive subject is searched with its plain note.
    goal = "a dating safety app for single parents"
    assert pw.find(goal).subject == "dating safety"
    assert pw.subject_note_kind(goal) == "private"


# -- no part of a held name leaves (security major) -------------------------------------

@pytest.mark.parametrize("goal,pieces", [
    ("a bot for my daughter O'Brien", ["brien", "obrien", "o brien"]),
    ("a bot for my daughter O’Brien", ["brien", "obrien"]),
    ("a chess coach for my son D'Angelo", ["angelo", "dangelo"]),
    ("a garden bot for my daughter O'Neil", ["neil", "oneil"]),
    ("a homework bot for my grandson DeShawn", ["shawn", "deshawn", "de shawn"]),
    ("a chess bot for my son\nDeShawn", ["shawn", "deshawn"]),
    ("a soccer bot for my son D'Andre who is 9", ["andre", "dandre"]),
    ("My son LeBron wants a basketball stats bot", ["bron", "lebron"]),
    ("a homework bot for my grandson McKenzie-Lee", ["kenzie", "mckenzie", "lee"]),
    ("a reading bot for my daughter\nMary-Kate", ["mary", "kate", "marykate"]),
    ("a garden bot for my daughter Rose-\nMarie", ["rose", "marie", "rosemarie"]),
    ("a garden bot for my daughter Rose-\r\n  Marie", ["rose", "marie"]),
    ("a coding bot for my daughter\nJean-Luc\nand her friends", ["jean", "luc", "jeanluc"]),
    ("a piano bot for my niece Jo-Ellen.\nShe is 7", ["ellen", "joellen"]),
    ("a garden bot for my daughter Rose-Marie", ["rose", "marie", "rosemarie"]),
])
def test_no_piece_of_a_held_name_is_searched_or_sent(goal, pieces):
    terms = gw.search_terms(goal)
    flat = {p for piece in pieces for p in piece.split() if len(p) > 1}
    assert not set(terms) & flat, (goal, terms)
    sent = _sent(oq.screen(pieces + [f"{p} homework" for p in pieces], goal=goal))
    assert not sent, (goal, sent)


@pytest.mark.parametrize("goal,glued", [
    ("Grace\nis my daughter, chess tutor bot please", ["graceis", "grac"]),
    ("a homework bot for my daughter Zoë, zoës homework", ["zo", "zoes"]),
    ("a party planner bot for my daughter Emma, plan emmasbirthday", ["emmasbirthday", "emm"]),
    ("a chore bot for my son Liam", ["liamschores", "myliam"]),
])
def test_a_held_name_glued_to_a_word_or_cut_short_is_held(goal, glued):
    assert _sent(oq.screen(glued, goal=goal)) == []
    assert not set(gw.search_terms(goal)) & set(glued)


def test_ordinary_words_near_a_held_name_still_leave():
    goal = "a chess bot for my son Leo"
    assert _sent(oq.screen(["leopard", "chess"], goal=goal)) == ["leopard", "chess"]
    goal = "a reading bot for my daughter Anna"
    assert _sent(oq.screen(["an", "reading"], goal=goal)) == ["an", "reading"]


def test_a_ticked_name_lets_out_only_the_word_she_ticked():
    goal = "a homework bot for my grandson DeShawn"
    got = oq.screen(["deshawn", "shawn"], goal=goal, allowed_words=["deshawn"])
    assert _sent(got) == ["deshawn"]


# -- over-holding (minor) -----------------------------------------------------------------

@pytest.mark.parametrize("goal,listed,not_listed", [
    ("a budget bot since we filed for Bankruptcy last year", {"bankruptcy"}, {"last"}),
    ("a calendar bot for custody weekends with my ex after the divorce",
     {"custody", "divorce"}, set()),
    ("a reminder bot for my sons parole check-ins", {"parole"}, {"check", "ins"}),
    ("a bible study bot for our church group", {"church"}, {"group"}),
    ("a bot for my immigration paperwork and green card interview",
     {"immigration", "green card"}, {"green", "card", "paperwork", "interview"}),
    ("a support bot for my gay son coming out", {"gay", "coming out"}, {"coming"}),
    ("a bot for my son's autism therapy schedule", {"autism", "therapy"}, {"schedule"}),
    ("a tracker for my daughter's chemo appointments", {"chemo"}, {"appointments"}),
    ("a mood tracker for my depression and panic attacks", {"depression", "panic attack"},
     {"panic"}),
    ("I'm in nursing school and want flashcards for drug names, a study buddy bot",
     set(), {"names"}),
    ("a party planner bot for my daughter Emma, plan her party", {"emma"}, {"plan"}),
])
def test_only_the_real_personal_words_are_listed(goal, listed, not_listed):
    words = {h["word"] for h in pw.find(goal).held}
    assert listed <= words, (goal, words)
    assert not words & not_listed, (goal, words)


def test_a_listed_pair_still_holds_each_of_its_words():
    goal = "a bot for my immigration paperwork and green card interview"
    assert _sent(oq.screen(["green", "card", "green card", "paperwork"], goal=goal)) == []


def test_the_custody_weekend_is_held_as_a_time_of_her_plans():
    goal = "a calendar bot for custody weekends with my ex after the divorce"
    rows = {h["word"]: h for h in pw.find(goal).held}
    assert rows["weekends"]["why"] == "date" and not rows["weekends"]["can_allow"]


# -- device lines (minor) -----------------------------------------------------------------

def _devices(goal: str) -> list[str]:
    return [i["id"] for i in live.proposal_items(goal) if i["section"] == "devices"]


@pytest.mark.parametrize("goal,want", [
    ("a bot for my Hue lights, Sonos speakers and Ring doorbell",
     ["device:ha:hue", "device:ha:sonos", "device:ha:ring"]),
    ("a bot for my Hue lights", ["device:ha:hue"]),
    ("a bot for our Hue bulbs and Nest thermostat", ["device:ha:hue", "device:ha:nest"]),
    ("a bot for our google nest thermostat", ["device:ha:nest"]),
    ("a bot for our lights. Also Ring doorbell", ["device:ha:ring"]),
    ("a bot for my Sonos. Also our Hue lights", ["device:ha:sonos", "device:ha:hue"]),
    ("a bot for my Hue lights. Tell me the weather", ["device:ha:hue"]),
])
def test_every_thing_in_her_list_gets_one_line(goal, want):
    assert _devices(goal) == want, (goal, _devices(goal))


@pytest.mark.parametrize("goal", ["a bot for my Google Home", "a bot for our thermostat",
                                  "a bot for our IKEA blinds", "our wedding bot. Also Ring the bell",
                                  "a bot for my wedding. Also the cake"])
def test_no_line_is_a_guess(goal):
    """A connection whose setup name is her maker word but whose name holds
    an everyday word she did not say ("Google Calendar") is not hers."""
    assert _devices(goal) == [], (goal, _devices(goal))


# -- the plain note for a sensitive subject (minor), end to end ---------------------------

@pytest.mark.parametrize("goal,key", [
    ("a divorce planner bot", "needs_private_note"),
    ("a custody schedule bot", "needs_private_note"),
    ("a bible study bot", "needs_private_note"),
    ("a debt payoff bot", "needs_private_note"),
    ("a grief journal bot", "needs_private_note"),
    ("a transgender support bot", "needs_private_note"),
    ("a juvie visit tracker bot", "needs_private_note"),
    ("a food bank finder bot", "needs_private_note"),
    ("a sobriety tracker bot", "needs_health_note"),
    ("an anxiety journal bot", "needs_health_note"),
    ("a lice treatment tracker bot", "needs_health_note"),
    ("a hip replacement recovery bot", "needs_health_note"),
    ("a mastectomy recovery bot", "needs_health_note"),
])
def test_the_wizard_shows_the_right_plain_note(goal, key):
    service = _optional("puffpai.wizard.service")
    wording = _optional("puffpai.wizard.wording")
    if service is None or wording is None or not hasattr(service, "_sensitive_note_key"):
        pytest.skip("the wizard's note step is not in this build")
    assert "health" in pw.find(goal).subject_notes       # the pinned warn flag
    assert service._sensitive_note_key(goal) == key
    text = wording.say(key, wording.STANDARD)
    assert ("a private subject" in text) == (key == "needs_private_note")
    assert ("a health word" in text) == (key == "needs_health_note")


@pytest.mark.parametrize("goal", ["a pet adoption finder bot", "a wedding planner bot",
                                  "a summer camp finder bot"])
def test_an_ordinary_subject_gets_no_sensitive_note(goal):
    assert pw.subject_note_kind(goal) == ""
    assert "health" not in pw.find(goal).subject_notes


# -- turned-down backgrounds (minor) ------------------------------------------------------

@pytest.mark.parametrize("words,named,down", [
    ("stars would be too much, pastel", None, {"stars"}),
    ("clouds are too busy, soft pink", None, {"clouds"}),
    ("deep blue, the clouds feel too childish", None, {"clouds"}),
    ("stars too please, and clouds", "stars", set()),
    ("I love stars, not too dark", "stars", set()),
])
def test_a_background_she_turns_down_is_never_named(words, named, down):
    from puffpai.looks import find as F
    got = F._background_words(words)
    assert got == (named, down), (words, got)


# -- the time of her own plans (discovery bar m3) -----------------------------------------

#: Ten new goals written for this rule, plus the m3 family-budget sentence.
#: Each names the time of her own plans outside the bot's subject.
PLAN_TIMES = [
    ("Help me build a family budget bot. My partner and I have two kids, and we are "
     "saving for a summer trip to visit my parents.", {"summer"}),
    ("I want a packing list bot. We are driving to the lake over Labor Day weekend with "
     "the dogs.", {"labor", "weekend"}),
    ("A meal prep bot for our Thanksgiving dinner, we host twelve people this year.",
     {"thanksgiving"}),
    ("Build me a reading tracker bot for the winter break so the boys keep reading.",
     {"winter"}),
    ("A chore chart bot for the kids during spring break at their grandma's house.",
     {"spring"}),
    ("I want a gift list bot to keep track of what I buy everyone for Christmas and "
     "Hanukkah.", {"christmas", "hanukkah"}),
    ("A workout bot, I want to be ready for our beach vacation in the fall.", {"fall"}),
    ("A costume ideas bot for Halloween, my son wants to be a dinosaur again.",
     {"halloween"}),
    ("A budget helper bot so we can afford the Memorial Day cookout at the cabin.",
     {"memorial"}),
    ("A playlist bot for our New Year's Eve party and the Valentine's Day dinner after.",
     {"valentine", "valentines"}),
    ("A garden planner bot. We leave every weekend in autumn, so plan watering around it.",
     {"weekend", "autumn"}),
]


@pytest.mark.parametrize("goal,times", PLAN_TIMES)
def test_the_time_of_her_plans_is_never_sent(goal, times):
    terms = gw.search_terms(goal)
    assert not _forms(terms) & times, (goal, terms)
    tries = sorted(times) + [f"{t} trip" for t in times] + [f"family budget {t} trip"
                                                             for t in times]
    sent = _sent(oq.screen(tries + terms, goal=goal))
    assert not [s for s in sent if _forms(s.split()) & times], (goal, sent)
    # Held, never tickable: a tick changes nothing.
    got = oq.screen(tries, goal=goal, allowed_words=sorted(times))
    assert not [s.phrase for s in got.sendable if _forms(s.phrase.split()) & times]


@pytest.mark.parametrize("goal", ["A playlist bot for our New Year's Eve party.",
                                  "A budget helper bot so we can afford the Memorial Day "
                                  "cookout at the cabin."])
def test_a_day_of_the_year_of_two_words_is_held_as_a_whole(goal):
    seqs = pw.find(goal).date_seqs
    assert seqs
    for seq in seqs:
        assert _sent(oq.screen([" ".join(seq) + " party"], goal=goal)) == []


def test_memorial_day_is_a_date_not_a_private_story():
    goal = "A budget helper bot so we can afford the Memorial Day cookout at the cabin."
    assert pw.private_goal_words(goal) == {}
    assert "cookout" in gw.search_terms(goal)


@pytest.mark.parametrize("goal,subject,time", [
    ("I want a summer camp finder bot for families.", "summer camp finder", "summer"),
    ("A Christmas cookie recipe bot.", "christmas cookie recipe", "christmas"),
    ("A Halloween costume idea bot.", "halloween costume idea", "halloween"),
    ("A winter weather alert bot.", "winter weather alert", "winter"),
    ("A weekend meal prep bot.", "weekend meal prep", "weekend"),
])
def test_a_time_that_is_the_bots_subject_is_searched(goal, subject, time):
    assert pw.find(goal).subject == subject
    sub = oq.subject_sendable(goal)
    assert sub is not None and sub.phrase == subject
    assert time in gw.search_terms(goal)
    assert time not in {h["word"] for h in pw.find(goal).held}


def test_a_month_never_joins_the_subject():
    assert pw.find("a June wedding planner bot").subject == "wedding planner"


def test_the_m3_goal_through_the_real_needs_path(monkeypatch):
    """The model's own phrases for the m3 goal, as it wrote them, through the
    needs step and its screen: "summer" never leaves."""
    needs = _optional("puffpai.wizard.needs")
    needs_llm = _optional("puffpai.wizard.needs_llm")
    if needs is None or needs_llm is None:
        pytest.skip("the needs step is not in this build")
    goal = PLAN_TIMES[0][0]
    raw = json.dumps({"kind": "family budget bot",
                      "asked": ["save up for a summer trip to visit my parents",
                                "track spending"],
                      "also_needed": [],
                      "jobs": [{"name": "Save up for a summer trip to visit my parents",
                                "does": "set a savings goal",
                                "parts": [{"kind": "code-app",
                                           "search": ["savings goal tracker",
                                                      "family budget summer trip",
                                                      "summer trip visit"]}]},
                               {"name": "track spending", "does": "log spending",
                                "parts": [{"kind": "code-app",
                                           "search": ["spending tracker app",
                                                      "summer budget"]}]}]})

    class _Brain:
        def chat_stream(self, *a, **k):
            yield raw

    monkeypatch.setattr(needs_llm, "make_backend", lambda *a, **k: _Brain())
    needs._CACHE.clear()
    try:
        got = needs.run(goal, cfg={"ollama_url": "http://127.0.0.1:11435",
                                   "chosen_model": "qwen2.5:7b-instruct"})
        sent = [s.phrase for s in needs.sends(got, goal=goal)]
    finally:
        needs._CACHE.clear()
    assert sent and not [s for s in sent if "summer" in s.split()], sent


def test_the_recorded_budget_goal_keeps_its_holiday_job_but_not_the_holiday(monkeypatch):
    """The recorded model reply for "plan for Christmas and summer camp":
    neither word leaves, and the holiday job is still searched by its other
    words (a date is held, not hard, so the job is not dropped)."""
    needs = _optional("puffpai.wizard.needs")
    needs_llm = _optional("puffpai.wizard.needs_llm")
    path = FIX / "needs_model_raw" / "budget.json"
    if needs is None or needs_llm is None or not path.exists():
        pytest.skip("the needs step or its recording is not in this build")
    goal = next(g["text"] for g in json.loads((FIX / "tuning_goals.json").read_text(
        encoding="utf-8"))["goals"] if g["id"] == "budget")
    raw = json.loads(path.read_text(encoding="utf-8"))["raw"]

    class _Brain:
        def chat_stream(self, *a, **k):
            yield raw

    monkeypatch.setattr(needs_llm, "make_backend", lambda *a, **k: _Brain())
    needs._CACHE.clear()
    try:
        got = needs.run(goal, cfg={"ollama_url": "http://127.0.0.1:11435",
                                   "chosen_model": "qwen2.5:7b-instruct"})
        sent = [s.phrase for s in needs.sends(got, goal=goal)]
    finally:
        needs._CACHE.clear()
    words = {w for s in sent for w in s.split()}
    assert not words & {"christmas", "summer"}, sent
    # A model's other word for the
    # time of her plans must not go out: "holiday", the model's word for her
    # Christmas, is held too. The job is still searched by its other words.
    assert "holiday" not in words, sent
    assert {"activity budget", "gift budget spreadsheet"} <= set(sent), sent



# =========================================================================================
# The sentence's shape, not only a list; the time
# of her plans read word by word; a tick lets out the whole word only; verbs are not names;
# device lists after "And"/";"; backgrounds turned down in a question or a later part.
# =========================================================================================

SWEEP2 = json.loads((FIX / "private_subject_items.json").read_text(encoding="utf-8"))


def test_the_second_sweep_is_the_validators_size():
    assert len(SWEEP2["items"]) == 310 and len(SWEEP2["templates"]) == 4


@pytest.mark.parametrize("item", SWEEP2["items"])
def test_second_sweep_no_piece_of_the_item_leaves_in_her_story_shapes(item):
    """Health, sex, identity, legal, money and grief items the lists never
    named ("psoriasis", "kratom", "probate", "sickle cell", ...) in four
    story shapes: nothing of the item leaves, whatever the word is."""
    private = _forms(item.split())
    for template in SWEEP2["templates"]:
        goal = template.format(w=item)
        terms = gw.search_terms(goal)
        sent = _sent(oq.screen(terms + [item], goal=goal))
        assert not [s for s in sent if _forms(s.split()) & private], (goal, sent)


@pytest.mark.parametrize("goal,private", [
    # The validator's realistic goals.
    ("a reminder bot for my psoriasis cream and my thyroid pills", {"psoriasis", "thyroid"}),
    ("a meal planner that helps with my PCOS and my husband's gout", {"pcos", "pco", "gout"}),
    ("a calendar for my son's cystic fibrosis treatments", {"cystic", "fibrosis"}),
    ("a shopping list bot, I need to reorder lube and a new vibrator discreetly",
     {"lube", "vibrator"}),
    ("a mood journal bot since my hallucinations and paranoia got worse",
     {"hallucination", "hallucinations", "paranoia"}),
    ("a bot that reminds me to take my lithium and my Mounjaro shot", {"lithium", "mounjaro"}),
    ("a tracker for my kratom use so I can taper off", {"kratom"}),
    ("a homework helper for my daughter since the cyberbullying started", {"cyberbullying"}),
    ("a bot to organize papers for probate after my mom passed", {"probate"}),
    ("a meal plan bot for my kid with sickle cell", {"sickle", "cell"}),
    ("a savings bot for my gender reassignment surgery", {"gender", "reassignment"}),
    ("a reminder bot for my stent follow up and cardiology visits", {"stent", "cardiology"}),
    # New goals written for this round, in other shapes.
    ("a reminder bot for my eczema flare ups and my son's asthma inhaler",
     {"eczema", "asthma", "inhaler"}),
    ("a journal bot since my panic disorder came back", {"panic", "disorder"}),
    ("a budget bot because we are dealing with my husband's gambling debt",
     {"gambling", "debt"}),
    ("a meal planner bot for my wife with ulcerative colitis", {"ulcerative", "colitis"}),
    ("a chore bot while I recover from my hysterectomy", {"hysterectomy"}),
    ("a savings bot, my partner is living with long covid", {"long", "covid"}),
    ("a schedule bot for my mom who is battling ovarian cancer", {"ovarian", "cancer"}),
    ("a homework bot for my son who was diagnosed with dysgraphia", {"dysgraphia"}),
    ("a shopping list bot, I need pregnancy tests and prenatal vitamins",
     {"pregnancy", "prenatal"}),
    ("a bot for my dog with hip dysplasia", {"hip", "dysplasia"}),
    ("a mood bot since my vaginismus started", {"vaginismus"}),
    ("a reminder bot for my premature ejaculation meds", {"premature", "ejaculation"}),
    ("a notes bot for our family about narcotics and school", {"narcotics", "narcotic"}),
    ("a budget bot, my son has an arrest warrant", {"arrest", "warrant"}),
])
def test_a_realistic_private_goal_sends_nothing_private(goal, private):
    terms = gw.search_terms(goal)
    assert not _forms(terms) & private, (goal, terms)
    tries = sorted(private) + [" ".join(sorted(private))] + [f"{p} help" for p in private]
    sent = _sent(oq.screen(tries + terms, goal=goal))
    assert not [s for s in sent if _forms(s.split()) & private], (goal, sent)
    # Held for her sensitive details, never tickable.
    got = oq.screen(sorted(private), goal=goal, allowed_words=sorted(private))
    assert not _sent(got), (goal, _sent(got))


@pytest.mark.parametrize("goal,kind", [
    ("a vibrator review bot", "private"),
    ("a lithium level tracker bot", "health"),
    ("a kratom taper bot", "health"),
    ("a psoriasis diet bot", "health"),
    ("a thyroid medication reminder bot", "health"),
    ("a probate paperwork helper bot", "private"),
    ("a cyberbullying report bot", "private"),
    ("a sickle cell meal planner bot", "health"),
    ("a cardiology appointment bot", "health"),
])
def test_a_newly_known_sensitive_subject_gets_its_note(goal, kind):
    assert pw.subject_note_kind(goal) == kind
    assert "health" in pw.find(goal).subject_notes


@pytest.mark.parametrize("goal,kept", [
    ("a bot to help me deal with my inbox", "inbox"),
    ("a bot for going through my photos", "photo"),
    ("a garden bot because my tomatoes keep dying", "tomato"),
    ("a bot since my old laptop broke", "laptop"),
    ("a quiz bot for my kids with dinosaur facts", "dinosaur"),
    ("a chess bot for my son who struggles with endgames", "endgame"),
    ("a homework helper for my daughter who struggles with fractions", "fraction"),
    ("a sorting bot to sort out my lego bricks", "lego"),
    ("a lithium battery tracker bot for my ebike", "lithium"),
    ("a bike maintenance bot, I need chain lube", "lube"),
    ("a roofing estimate bot for asphalt shingles", "shingle"),
    ("a bot for my ice cream shop", "cream"),
    ("a code bot that explains spring boot settings", "spring"),
    ("a bot that tells me when the kids fall asleep", "fall"),
    ("a weather bot that warns me before a fall frost", "frost"),
])
def test_the_story_shapes_leave_ordinary_goals_alone(goal, kept):
    """Over-holding: the story shapes are narrow ("deal with my inbox" is a
    task, "because my tomatoes keep dying" is a plain reason)."""
    assert kept in gw.search_terms(goal) or kept in pw.find(goal).subject.split(), \
        (goal, gw.search_terms(goal), pw.find(goal).subject)


# -- the time of her plans, word by word ------------------------------------------------

#: Shapes: a hyphen, a glued year, a slash, holidays and
#: "the holiday season", and time words glued into one.
VALIDATOR_TIMES = [
    ("a family budget bot, we are saving for a summer-trip to visit my parents", {"summer"}),
    ("a family budget bot, we are saving for our summer-time trip to visit my parents",
     {"summer", "time"}),
    ("a savings bot for a trip in summer2026 with my parents", {"summer", "summer2026"}),
    ("a packing bot for our trip, summer/fall, with my sister", {"summer", "fall"}),
    ("a cleaning bot for the holidays when my in-laws visit", {"holiday", "holidays"}),
    ("a gift budget bot for the holiday season with my family", {"holiday", "season"}),
    ("a cleaning bot for summertime when my in-laws visit", {"summertime"}),
    ("a cleaning bot for wintertime when my in-laws visit", {"wintertime"}),
    ("a cleaning bot for midsummer when my in-laws visit", {"midsummer"}),
    ("a cleaning bot for the solstice when my in-laws visit", {"solstice"}),
    ("a cleaning bot for Christmas break when my in-laws visit", {"christmas", "break"}),
    ("a cleaning bot for Chinese New Year when my in-laws visit",
     {"chinese", "new", "year"}),
    ("a cleaning bot for Good Friday when my in-laws visit", {"good", "friday"}),
    ("a cleaning bot for Super Bowl Sunday when my in-laws visit", {"super", "bowl"}),
    ("a cleaning bot for Presidents Day when my in-laws visit", {"presidents", "day"}),
    ("a cleaning bot for Martin Luther King Day when my in-laws visit",
     {"martin", "luther", "king", "day"}),
    ("a cleaning bot for Mother's Day when my in-laws visit", {"day"}),
    ("a cleaning bot for the long weekend when my in-laws visit", {"long", "weekend"}),
    ("a cleaning bot for summer vacation when my in-laws visit", {"summer", "vacation"}),
    ("a planner for my Valentine's Day date night", {"valentine", "day"}),
]

#: Twelve new goals written for this round (the directive asks for ten),
#: each with the time of her own plans outside the bot's subject.
NEW_PLAN_TIMES = [
    ("a packing list bot for our midsummer cabin weekend with my sister",
     {"midsummer", "weekend"}),
    ("a grocery budget bot, my in-laws come for the Lunar New Year and again at Easter-time",
     {"lunar", "new", "year", "easter", "time"}),
    ("a chore rota bot for spring/summer when the twins are home from college",
     {"spring", "summer"}),
    ("a savings bot for our winter2027 ski trip to see my brother", {"winter", "winter2027"}),
    ("a meal plan bot for my dad's visit over Veterans Day", {"veterans", "day"}),
    ("a party checklist bot for my son's Halloween-weekend sleepover",
     {"halloween", "weekend"}),
    ("a gift tracker bot, we do Secret Santa every holiday season with my cousins",
     {"holiday", "season"}),
    ("a cleaning schedule bot before my mother-in-law arrives for Thanksgiving break",
     {"thanksgiving", "break"}),
    ("a carpool bot for summertime soccer camp with the neighbors", {"summertime"}),
    ("a garden bot, I want the beds ready by the solstice or at least by fall",
     {"solstice", "fall"}),
    ("a budget bot for our anniversary trip over the long weekend in autumn",
     {"long", "weekend", "autumn"}),
    ("a to-do bot for my Good Friday fish fry at home", {"good", "friday"}),
]


@pytest.mark.parametrize("goal,times", VALIDATOR_TIMES + NEW_PLAN_TIMES)
def test_the_time_of_her_plans_is_never_sent_in_any_spelling(goal, times):
    terms = gw.search_terms(goal)
    assert not _forms(terms) & times, (goal, terms)
    tries = sorted(times) + [" ".join(sorted(times))] + [f"{t} trip" for t in times]
    sent = _sent(oq.screen(tries + terms, goal=goal))
    assert not [s for s in sent if _forms(s.split()) & times], (goal, sent)
    got = oq.screen(tries, goal=goal, allowed_words=sorted(times))
    assert not [s.phrase for s in got.sendable if _forms(s.phrase.split()) & times]


def test_the_plan_itself_is_still_searched():
    """The time is held, the plan is not: "barbecue", "camp"."""
    assert "barbecue" in gw.search_terms("a to-do bot for my Labor Day barbecue")
    assert "camp" in gw.search_terms("a carpool bot for summertime soccer camp")


@pytest.mark.parametrize("goal,subject", [
    ("a holiday cookie recipe bot", "holiday cookie recipe"),
    ("a summer reading list bot for kids", "summer reading list"),
    ("a weekend hiking trail finder bot", "weekend hiking trail finder"),
])
def test_a_time_that_names_the_bot_is_still_its_subject(goal, subject):
    sub = oq.subject_sendable(goal)
    assert sub is not None and sub.phrase == subject


# -- a tick lets out the whole word only --------------------------------------------------

@pytest.mark.parametrize("goal,tick,whole,pieces", [
    ("a chore bot for my son O'Connor", "o'connor", ["oconnor", "o connor chores"],
     ["connor"]),
    ("a bot for my son DeMarcus and his soccer games", "DeMarcus",
     ["demarcus", "demarcus soccer", "de marcus"], ["marcus", "de"]),
    ("a birthday planner for my niece Mary-Kate", "mary-kate",
     ["marykate", "mary kate birthday"], ["mary", "kate", "kate mary"]),
    ("a recipe bot for my wife D’Arcy", "D’Arcy", ["darcy"], ["arcy"]),
    ("a homework bot for my grandson DeShawn", "deshawn", ["deshawn"], ["shawn"]),
])
def test_a_tick_lets_out_the_whole_word_never_a_piece(goal, tick, whole, pieces):
    assert _sent(oq.screen(whole + pieces, goal=goal)) == []
    sent = _sent(oq.screen(whole + pieces, goal=goal, allowed_words=[tick]))
    assert sent == whole, (goal, sent)


def test_a_piece_tick_never_lets_out_the_name():
    goal = "a bot for my son DeMarcus and his soccer games"
    assert _sent(oq.screen(["demarcus", "marcus"], goal=goal, allowed_words=["marcus"])) \
        == []


# -- verbs after "my <relative>" are not names (validator, over-holding) -----------------

@pytest.mark.parametrize("verb", ["plays", "joined", "went", "forgets", "finished",
                                  "struggles", "learned", "retired", "skipped", "turned",
                                  "started"])
def test_a_verb_after_one_of_her_people_is_not_listed_as_a_name(verb):
    goal = f"a bot because my son {verb} chess"
    assert verb not in {h["word"] for h in pw.find(goal).held}


@pytest.mark.parametrize("goal,name", [
    ("a chess bot for my son river", "river"),
    ("a bot for my son sky and his friends", "sky"),
    ("a piano bot for my daughter grace", "grace"),
    ("a reading bot for my son miles", "miles"),
    ("I want a tutor bot to teach my kid fractions", "fractions"),
])
def test_a_plain_word_after_one_of_her_people_is_still_held_as_a_maybe_name(goal, name):
    rows = [h for h in pw.find(goal).held if h["word"] == name]
    assert rows and rows[0]["why"] == "name"


def test_the_chemo_sentence_lists_only_the_health_word():
    goal = "a grocery list bot since my husband started chemo on Mondays"
    assert {h["word"] for h in pw.find(goal).held} == {"chemo"}


# -- device lists after "And", ";" and a verb -------------------------------------------

@pytest.mark.parametrize("goal,want", [
    ("a bot for our lights. And the Ecobee thermostat", ["device:ha:ecobee"]),
    ("a bot for my Hue lights; Sonos speakers; Ring doorbell",
     ["device:ha:hue", "device:ha:sonos", "device:ha:ring"]),
    ("a bot that reads my Nest thermostat and Sonos speakers and turns on the Hue lights",
     ["device:ha:nest", "device:ha:sonos", "device:ha:hue"]),
    ("a bot for our lights. Plus our Sonos speakers", ["device:ha:sonos"]),
])
def test_every_thing_in_her_list_gets_a_line_in_more_shapes(goal, want):
    assert _devices(goal) == want, (goal, _devices(goal))


@pytest.mark.parametrize("goal", ["a bot for my Hue lights; I also want jokes",
                                  "a bot for my Hue lights and I want jokes"])
def test_a_new_thought_after_her_list_is_not_a_device(goal):
    assert _devices(goal) == ["device:ha:hue"], (goal, _devices(goal))


# -- backgrounds turned down in a question or a later part (validator) -------------------

@pytest.mark.parametrize("words,named,down", [
    ("stars? too childish. dots instead", None, {"stars"}),
    ("stars are tacky", None, {"stars"}),
    ("stars would be overkill", None, {"stars"}),
    ("stars feel a little over the top", None, {"stars"}),
    ("stars?? nah", None, {"stars"}),
    ("clouds? nope, stars", "stars", {"clouds"}),
    ("stars? yes!", "stars", set()),
    ("I love stars. Too much blue though", "stars", set()),
])
def test_a_background_turned_down_in_any_shape_is_never_named(words, named, down):
    from puffpai.looks import find as F
    assert F._background_words(words) == (named, down), words


# == more shapes ================================================

# -- plan times: "spring"/"fall" are a time unless a verb or a thing ----------------------

#: The validator's goals (p_times, p_times2), and new ones in the same shapes.
FIX3_PLAN_TIMES = [
    ("A meal bot for the week of spring recess at home", {"spring"}),
    ("A homework bot the kids can use over spring recess", {"spring"}),
    ("a bot for our fall 2026 kitchen remodel", {"fall"}),
    ("A bot for our fall-2026 kitchen remodel budget", {"fall"}),
    ("Plan our Cinco de Mayo party bot", {"cinco", "mayo"}),
    ("A list bot for my son's summercamp packing", {"summer", "summercamp"}),
    ("a bot for packing for our wintertrip", {"winter", "wintertrip"}),
    ("A chore bot, the kids are home all Christmastime", {"christmas", "christmastime"}),
    ("A countdown bot to my daughter's quinceañera in the springtime",
     {"spring", "springtime", "quinceanera"}),
    ("A packing bot for our trip in mid-July to Maine.", {"mid"}),
    # New goals written for this round.
    ("a lawn care bot for our yard this fall before the first frost", {"fall"}),
    ("a chore chart bot for spring 2027 when my stepson moves in", {"spring"}),
    ("a road trip bot for the fall of 2026 with my sister", {"fall"}),
    ("our spring garden planting bot for the kids", {"spring"}),
    ("a babysitter schedule bot for my son's midsummer camp", {"summer", "midsummer"}),
    ("a snack list bot for our falltime hayride", {"fall", "falltime"}),
    ("a closet swap bot for when the kids go back to school in the fall", {"fall"}),
    ("my Easter brunch planner bot", {"easter"}),
]


@pytest.mark.parametrize("goal,times", FIX3_PLAN_TIMES)
def test_fix3_the_time_of_her_plans_never_leaves(goal, times):
    terms = gw.search_terms(goal)
    assert not _forms(terms) & times, (goal, terms)
    tries = sorted(times) + [f"{t} trip" for t in times] + terms
    for allowed in ((), sorted(times)):
        sent = _sent(oq.screen(tries, goal=goal, allowed_words=allowed))
        assert not [s for s in sent if _forms(s.split()) & times], (goal, sent)


@pytest.mark.parametrize("goal,word", [
    ("A bot that helps me fall asleep with calm stories.", "fall"),
    ("A fall detection alert bot for grandma's room.", "fall"),
    ("A physics homework bot that explains spring constants and pendulums.", "spring"),
    ("A spring roll recipe bot for my food truck.", "spring"),
    ("A history bot about the fall of Rome.", "fall"),
    ("A leaf spring and coil spring calculator bot for my truck.", "spring"),
    ("A woodworking bot, I need a new cutting board design and a spring clamp list.",
     "spring"),
    ("A weather bot that warns me before a fall frost.", "fall"),
])
def test_fix3_a_season_word_used_as_a_verb_or_a_thing_is_still_searched(goal, word):
    """Over-holding (directive): "fall asleep", "spring rolls" are not her plans."""
    assert word in gw.search_terms(goal), (goal, gw.search_terms(goal))
    assert word not in {h["word"] for h in pw.find(goal).held}


def test_fix3_a_product_named_spring_is_not_a_season():
    goal = "A bot for my Spring Boot project that writes tests."
    rows = {h["word"]: h for h in pw.find(goal).held}
    assert rows["spring"]["why"] != "date" and rows["spring"]["can_allow"]


def test_fix3_her_own_plan_is_not_the_bots_kind():
    """A subject that is hers holds her plan's time; a kind of bot does not."""
    assert pw.subject_of("Plan our Cinco de Mayo party bot") == "party"
    assert pw.subject_of("my Easter brunch planner bot") == "brunch planner"
    assert pw.subject_of("a Christmas cookie recipe bot") == "christmas cookie recipe"
    assert oq.subject_sendable("a summer camp finder bot").phrase == "summer camp finder"


# -- sensitive words: reason clauses by grammar, bare "for my X" by the lists --------------

#: The validator's p_sens words that are not everyday words.
FIX3_SENS = """sciatica perimenopause genital-warts down-syndrome dv-shelter ectopic shingles
als lithium ms-flare hot-flashes paternity-test dna-test donor-eggs involuntary-hold
hair-transplant xylazine thrush ringworm hymen tarot latter-day-saints sugar-baby truvada
cgm bris seance hearse chapter-7 chapter-13 section-8 stage-4 living-will low-testosterone
nicotine-patches wake-service ward-of-the-state confirmation-classes restraining""".split()
FIX3_SHAPES = ["a helper bot for my {w}", "a reminder bot because my husband has {w}",
               "a budget bot since we are dealing with {w}",
               "a calendar bot for my daughter's {w}"]


@pytest.mark.parametrize("item", FIX3_SENS)
def test_fix3_the_validators_sensitive_words_never_leave_in_her_story_shapes(item):
    w = item.replace("-", " ")
    words = set(w.split()) - {"of", "the"}
    for shape in FIX3_SHAPES:
        goal = shape.format(w=w)
        terms = gw.search_terms(goal)
        tries = [w] + w.split() + terms + [f"{w} tips"]
        sent = _sent(oq.screen(tries, goal=goal))
        bad = [x for x in set(terms) | set(sent) if _forms(x.split()) & _forms(words)]
        assert not bad, (goal, bad)


@pytest.mark.parametrize("word", ["prep", "pep", "terminal", "sanctuary"])
def test_fix3_an_everyday_word_in_the_bare_for_my_shape_is_still_searched(word):
    """Named limit: grammar cannot tell "for my terminal" (a computer) from a
    health word, and the directive says ordinary words are not held. Said
    as a reason ("because my husband has X") it is held."""
    assert word in gw.search_terms(f"a helper bot for my {word}")
    assert word not in gw.search_terms(f"a reminder bot because my husband has {word}")


@pytest.mark.parametrize("goal", [
    "a reminder bot because my husband has zorblatosis flares",
    "a stretching bot since my wife still has glimmerfoot",
    "a meal bot because I have quenditis",
    "a pill bot because she is on vexaprilin",
    "a routine bot for my son who has kelvorn syndrome",
])
def test_fix3_a_reason_clause_holds_her_story_whatever_the_word(goal):
    made_up = [w for w in gw.raw_words(goal) if w.startswith(
        ("zorb", "glimm", "quend", "vexa", "kelv"))]
    assert made_up
    terms = gw.search_terms(goal)
    assert not set(made_up) & set(terms), (goal, terms)
    assert _sent(oq.screen(made_up, goal=goal)) == []
    rows = {h["word"]: h for h in pw.find(goal).held}
    assert all(not rows[w]["can_allow"] for w in made_up if w in rows)


@pytest.mark.parametrize("goal,kept", [
    # "has a recital" is no longer an ordinary
    # thing she has. A reason clause about her people is her story whatever
    # follows the verb ("because we got a prenup"); only a count
    # or a possessive after a have verb is owning things the usual way.
    ("a chore bot because my kids have two dogs and a cat", "dog"),
    ("a trivia bot because my husband has his own quiz night", "quiz"),
    ("A chapter summary bot for my book club, we are on chapter 7.", "chapter"),
    ("a stage lighting cue bot for our school play at stage 4", "lighting"),
    ("A bird watching bot that logs every thrush and robin in the yard.", "thrush"),
])
def test_fix3_an_ordinary_thing_she_has_is_still_searched(goal, kept):
    assert kept in gw.search_terms(goal), (goal, gw.search_terms(goal))


def test_final_her_sons_event_in_a_reason_clause_is_held():
    goal = "A reminder bot because my son has a recital on Friday."
    assert "recital" not in gw.search_terms(goal)
    assert "reminder" in gw.search_terms(goal)


@pytest.mark.parametrize("goal,private", [
    ("a meal planner to ease my perimenopause hot flashes", {"perimenopause", "hot", "flash"}),
    ("a stretching reminder bot for my sciatica flare ups", {"sciatica"}),
    ("a daily routine bot for my son with Down syndrome", {"down", "syndrome"}),
    ("a budget bot now that we moved into a dv shelter with the kids", {"dv", "shelter"}),
    ("a pill reminder for my husband's Truvada", {"truvada"}),
    ("a calendar bot for our paternity test results and court dates", {"paternity", "test"}),
    ("a money bot since we filed chapter 7 last spring", {"chapter", "spring"}),
    ("a helper bot for my perimenopausal mood swings", {"perimenopausal"}),
    ("a dosing reminder for my promethazine", {"promethazine"}),
])
def test_fix3_a_realistic_private_goal_sends_nothing_private(goal, private):
    terms = gw.search_terms(goal)
    assert not _forms(terms) & private, (goal, terms)
    sent = _sent(oq.screen(sorted(private) + terms, goal=goal))
    assert not [s for s in sent if _forms(s.split()) & private], (goal, sent)


# -- backgrounds and device lines (validator minors) --------------------------------------

@pytest.mark.parametrize("words,named,down", [
    ("clouds? hmm no", None, {"clouds"}),
    ("clouds, but actually no", None, {"clouds"}),
    ("clouds are a bit much, maybe leaves", None, {"clouds"}),
    ("clouds? no thanks", None, {"clouds"}),
    ("stars? not for me", None, {"stars"}),
    ("clouds, no stars", "clouds", {"stars"}),
    ("stars, but maybe clouds", "stars", set()),
    ("clouds? hmm, sure", "clouds", set()),
])
def test_fix3_a_background_turned_down_after_a_pause_is_never_named(words, named, down):
    from puffpai.looks import find as F
    assert F._background_words(words) == (named, down), words


@pytest.mark.parametrize("goal,want", [
    ("a bot that dims the Hue lights", ["device:ha:hue"]),
    ("a bot that dims the Hue lights and turns off the Sonos",
     ["device:ha:hue", "device:ha:sonos"]),
    ("a bot to turn down the Sonos when the baby sleeps", ["device:ha:sonos"]),
])
def test_fix3_a_dimming_verb_gives_the_device_its_line(goal, want):
    assert _devices(goal) == want, (goal, _devices(goal))
