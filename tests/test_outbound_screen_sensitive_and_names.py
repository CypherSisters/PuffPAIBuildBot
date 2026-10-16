"""The outbound screen.

These cases were found wrong:

* sensitive words other than the classifier's health list went out
  ("journal anxiety", "custody hearing", "parole meeting");
* a held name typed as a plural or possessive without the apostrophe went
  out ("emmas birthday party", "wedding annas");
* a hyphenated name after a relative went out in parts ("rose", "marie");
* a line break between a relative and a name removed the cue, and
  ``raw_words`` glued "Grace" and "is" into "graceis";
* ordinary words ("opening", "married", "getting") were listed as "Kept
  private".

Every test here is goal-agnostic: the goals are made-up sentences in many
shapes, never a special case.
"""
from __future__ import annotations

import pytest

from puffpai.catalogs import goal_words, personal_words as pw
from puffpai.security import outbound_query as oq

NEUTRAL = "I want a journal bot."

#: Probes; every one must be held when it is
#: not the bot's subject.
PROBES = ["anxiety", "sobriety", "herpes", "abortion", "alcoholism", "opioid",
          "methadone", "aids", "rape", "abuse", "domestic violence", "transgender",
          "gay", "therapy", "hospice", "grief", "divorce", "bankruptcy",
          "immigration", "eviction", "religion", "salary", "custody", "parole"]


def _sent(result) -> list[str]:
    return [s.phrase for s in result.sendable]


# -- sensitive categories ------------------------------------------------------------

@pytest.mark.parametrize("word", PROBES)
def test_every_sensitive_probe_is_held_when_it_is_not_the_subject(word):
    got = oq.screen([f"journal {word}", word, "journal ideas"], goal=NEUTRAL)
    assert _sent(got) == ["journal ideas"], word
    assert len(got.held) == 2


@pytest.mark.parametrize("word", PROBES)
def test_a_sensitive_word_in_the_goal_is_held_never_tickable(word):
    goal = f"I want a journal bot. I need to write about {word} every week."
    found = pw.find(goal)
    # A sensitive pair is listed as one row ("domestic violence": one line
    # per detail).
    rows = [h for h in found.held if h["word"] in word.split() or h["word"] == word]
    assert rows, (word, found.held)
    assert all(not h["can_allow"] and h["why"] in ("health", "private") for h in rows)
    # Ticking it changes nothing: it can never be allowed out.
    got = oq.screen([f"journal {word}"], goal=goal, allowed_words=word.split())
    assert _sent(got) == []
    assert not (set(goal_words.search_terms(goal)) & set(word.split()))


# Every sensitive subject carries the pinned "health" note
# (design I-1: subject_notes is a subset of brand/health; the wizard showed
# no note at all for "private"). The category itself stays readable.
@pytest.mark.parametrize("goal,subject,note,category", [
    ("I want a sobriety tracker bot", "sobriety tracker", "health", "health"),
    ("an anxiety journal bot", "anxiety journal", "health", "health"),
    ("a divorce planner bot", "divorce planner", "health", "legal"),
    ("a grief journal bot", "grief journal", "health", "grief"),
    ("a bankruptcy planner bot", "bankruptcy planner", "health", "money"),
    ("a bible study bot", "bible study", "health", "belief"),
    ("an immigration paperwork bot", "immigration paperwork", "health", "legal"),
    ("a mental health journal bot", "mental health journal", "health", ""),
])
def test_a_sensitive_subject_still_searches_with_a_note(goal, subject, note, category):
    found = pw.find(goal)
    assert found.subject == subject
    assert found.subject_notes == [note]
    assert set(found.subject_notes) <= {"brand", "health"}
    if category:
        assert pw.sensitive_category(subject.split()[0]) == category
    s = oq.subject_sendable(goal)
    assert s is not None and s.phrase == subject and s.subject
    res = oq.screen([subject, f"{subject} app"], goal=goal)
    assert _sent(res) == [subject, f"{subject} app"]
    assert note in res.subject_notes


def test_the_subject_exemption_is_only_for_its_own_words():
    goal = "a divorce planner bot"
    # "custody" is not the subject: held even on a divorce bot.
    assert _sent(oq.screen(["divorce planner custody"], goal=goal)) == []


@pytest.mark.parametrize("phrase", ["dad passed away", "end of life care",
                                    "birth control tracker", "coming out story",
                                    "food stamps budget"])
def test_sensitive_pairs_drop_the_phrase(phrase):
    assert _sent(oq.screen([phrase], goal=NEUTRAL)) == []


def test_ordinary_uses_stay_sendable():
    goal = "I want a nursing school study buddy bot"
    got = oq.screen(["study aids", "flashcard app", "passed exam tracker"], goal=goal)
    assert _sent(got) == ["study aids", "flashcard app", "passed exam tracker"]
    assert _sent(oq.screen(["hearing aids"], goal=goal)) == []


def test_the_rule_only_needs_path_sends_no_sensitive_word(monkeypatch):
    """A reproduction, through the real needs step."""
    needs = pytest.importorskip("puffpai.wizard.needs")
    monkeypatch.setattr(needs, "_brain", lambda cfg: ("", ""))
    goal = ("I want a journal bot. I'll need to be able to track my anxiety, log my "
            "sobriety days, and note my herpes outbreaks")
    out = needs.run(goal, cfg=None, deadline_s=3)
    phrases = [s.phrase for s in needs.sends(out, goal=goal)]
    assert phrases, "the subject still searches"
    joined = " ".join(phrases)
    for word in ("anxiety", "sobriety", "herpe", "herpes"):
        assert word not in joined.split(), phrases
    goal2 = ("I want a planner bot. I'll need to be able to check the abortion cost, "
             "prepare for the custody hearing, and remember the parole meeting")
    out2 = needs.run(goal2, cfg=None, deadline_s=3)
    joined2 = " ".join(s.phrase for s in needs.sends(out2, goal=goal2)).split()
    assert not ({"abortion", "custody", "parole"} & set(joined2)), joined2


# -- held names in every form --------------------------------------------------------

@pytest.mark.parametrize("goal,phrase", [
    ("I want a party planner bot for my daughter Emma. I'll need to be able to plan "
     "emmas birthday party", "emmas birthday party"),
    ("a wedding bot for my sister Anna", "wedding annas"),
    ("a birth plan bot for my wife Sofia", "sofias checklist"),
    ("a garden bot for my son Jake", "jakes garden"),
])
def test_a_plural_or_possessive_without_the_apostrophe_is_the_name(goal, phrase):
    assert _sent(oq.screen([phrase], goal=goal)) == []
    name = phrase.split()[0] if phrase.split()[0].endswith("s") else phrase.split()[1]
    assert name not in goal_words.search_terms(goal)


def test_ticking_the_name_lets_its_forms_out_too():
    goal = "a wedding bot for my sister Anna"
    assert _sent(oq.screen(["wedding annas"], goal=goal, allowed_words=["anna"])) == \
        ["wedding annas"]


@pytest.mark.parametrize("goal,parts", [
    ("a garden bot for my daughter Rose-Marie", {"rose", "marie"}),
    ("a garden bot for my son Jean-Luc", {"jean", "luc"}),
    ("a garden bot for my son jean-luc", {"jean", "luc"}),
    ("a chess bot for my son Will-Bob", {"will", "bob"}),
])
def test_each_part_of_a_joined_name_is_held(goal, parts):
    terms = set(goal_words.search_terms(goal))
    assert not (terms & parts), terms
    for part in parts:
        assert _sent(oq.screen([f"{part} garden", f"{part} chess"], goal=goal)) == []


def test_a_joined_name_that_is_the_subject_keeps_the_subject():
    goal = "a rose garden bot for my daughter Rose-Marie"
    s = oq.subject_sendable(goal)
    assert s is not None and s.phrase == "rose garden"
    assert _sent(oq.screen(["marie garden"], goal=goal)) == []


@pytest.mark.parametrize("sep", ["\n", "\r\n", "\n\n", "\n \n", "\t", " ", "  "])
def test_a_line_break_never_ends_a_name_cue(sep):
    goal = f"I want a garden bot for my daughter{sep}Rose"
    assert "rose" not in goal_words.search_terms(goal)
    assert _sent(oq.screen(["rose garden"], goal=goal)) == []
    goal2 = f"a chess tutor bot for my daughter{sep}{sep}Grace"
    assert _sent(oq.screen(["grace chess"], goal=goal2)) == []


def test_a_line_break_never_glues_two_words():
    goal = "Grace\nis my daughter, chess tutor bot please"
    terms = goal_words.search_terms(goal)
    assert "graceis" not in terms and "grace" not in terms
    assert goal_words.raw_words("a\nb\tc\rd") == ["a", "b", "c", "d"]
    assert _sent(oq.screen(["grace chess"], goal=goal)) == []


@pytest.mark.parametrize("goal", [
    "Rose is 8 and wants a garden bot",
    "Rose turns 8 soon, so a garden bot",
    "Rose just turned 8. She wants a garden bot",
    "Rose, 8, loves flowers. A garden bot please",
])
def test_a_name_followed_by_an_age_is_held(goal):
    assert "rose" not in goal_words.search_terms(goal)
    assert _sent(oq.screen(["rose garden"], goal=goal)) == []
    assert oq.subject_sendable(goal).phrase == "garden"


def test_a_number_after_an_ordinary_word_is_not_an_age():
    goal = "Budget is 5000 for everything. I want a wedding planner bot"
    assert "budget" in goal_words.search_terms(goal)
    assert _sent(oq.screen(["wedding budget"], goal=goal)) == ["wedding budget"]


def test_capitals_at_the_start_of_a_line_are_not_names():
    goal = "I want a wedding planner bot.\nI need:\n- Guest list\n- Budget\n- Seating chart"
    held = {h["word"] for h in pw.find(goal).held}
    assert not ({"guest", "budget", "seating"} & held), held
    assert {"guest", "budget", "seating"} <= set(goal_words.search_terms(goal))


# -- over-holding --------------------------------------------------------------------

@pytest.mark.parametrize("goal,ordinary", [
    ("My grandson Theo is 8 and I want a chess coach bot for him. I'll need to be able "
     "to play practice games, work through puzzles, and learn the openings.",
     {"opening", "openings"}),
    # "engaged" is a relative's news: held and listed. "got" is an
    # ordinary word.
    ("I want to build a wedding planner bot. My brother just got engaged.",
     {"got"}),
    ("My mom and I are opening a bakery. I want a bakery helper bot.", {"opening"}),
])
def test_ordinary_words_are_never_kept_private(goal, ordinary):
    found = pw.find(goal)
    shown = set(found.hard) | {h["word"] for h in found.held}
    assert not (shown & ordinary), shown & ordinary


def test_the_chess_openings_are_searchable():
    goal = ("My grandson Theo is 8 and I want a chess coach bot for him. I'll need to "
            "be able to learn the openings.")
    assert "opening" in goal_words.search_terms(goal)
    assert _sent(oq.screen(["chess openings"], goal=goal)) == ["chess openings"]
    # His name and age still never leave.
    assert _sent(oq.screen(["theo chess", "chess for 8"], goal=goal)) == []


def test_a_news_word_can_name_the_bot():
    assert pw.subject_of("a retirement planner bot") == "retirement planner"
    assert pw.subject_of("a graduation party planner bot") == "graduation party planner"


# Reversed. An ordinary word right after a
# person may be a name ("my son river", "my daughter sky"), so it is held,
# fail closed, and stays tickable: she can let it out herself.
@pytest.mark.parametrize("goal,ordinary", [
    ("I want a tutor bot to teach my kid fractions", "fractions"),
    ("a lesson bot for my daughter piano lessons", "piano"),
    ("a soccer coach bot for my son soccer drills", "soccer"),
])
def test_an_ordinary_word_after_a_person_is_held_but_tickable(goal, ordinary):
    rows = [h for h in pw.find(goal).held if h["word"] == ordinary]
    subject = pw.subject_of(goal).split()
    if ordinary in subject:
        # A weak name that is the bot's own subject word is searched.
        assert not rows
        return
    assert rows and rows[0]["why"] == "name" and rows[0]["can_allow"], rows
    assert goal_words.singular(ordinary) not in goal_words.search_terms(goal)
    assert _sent(oq.screen([f"{ordinary} help"], goal=goal)) == []
    assert _sent(oq.screen([f"{ordinary} help"], goal=goal,
                           allowed_words=[ordinary])) == [f"{ordinary} help"]


@pytest.mark.parametrize("goal,name", [
    ("my kids grace and mark need a chess bot", "grace"),
    ("my kids grace and mark need a chess bot", "mark"),
    ("a garden bot for my daughter rose", "rose"),
    ("a chess bot for my son jayden", "jayden"),
    ("our puppy biscuit needs a feeding bot", "biscuit"),   # pet names are ordinary words
])
def test_a_word_that_could_be_a_name_after_a_person_or_pet_is_held(goal, name):
    assert name in {h["word"] for h in pw.find(goal).held}
    assert name not in goal_words.search_terms(goal)
