"""General causes of failed goals.

Every rule here is grammar or search shape, never a topic: the goals below are
invented sentences of the same shapes the measurement used. No network.
"""
from __future__ import annotations

import contextlib

import pytest

from puffpai.catalogs import needs_rules


# -- the bot's subject ----------------------------------------------------------------
def test_a_bot_to_be_her_coach_has_no_subject():
    # "I want a bot to be her coach" gave the subject "bot be", and GitHub was
    # searched for "bot be" (betting bots, checkout bots) instead of her jobs.
    goal = "My nephew joined a robotics club and I want a bot to be his mentor."
    assert needs_rules.kind_words(goal) == []
    assert needs_rules.usable_subject("bot be") == ""


@pytest.mark.parametrize("said, usable", [
    ("tutor", ""), ("coach", ""), ("bot be", ""), ("be tutor", ""),
    ("wedding planner", "wedding planner"), ("home fitness coach", "home fitness coach"),
    ("time tracker", "time tracker"), ("garden", "garden"), ("", ""),
])
def test_only_a_subject_that_says_what_the_bot_is_about_is_used(said, usable):
    assert needs_rules.usable_subject(said) == usable


def test_a_role_only_subject_is_never_sent(monkeypatch):
    # The real outbound screen names "tutor" the subject of "a tutor bot"; the
    # needs step does not send it: it says what the bot is, not what about.
    from puffpai.security import outbound_query as oq
    from puffpai.wizard import needs
    goal = "I want a tutor bot with vocabulary flashcards and grammar drills."
    subject_seen = oq.subject_sendable(goal)
    doc = {"goal_digest": needs.goal_digest(goal), "held": [], "kind": "tutor",
           "subject": {"phrase": needs_rules.usable_subject(
               subject_seen.phrase if subject_seen else ""), "notes": [], "sendable": False},
           "jobs": [{"id": "j1", "name": "Vocabulary flashcards", "ticked": True,
                     "parts": [{"kind": "code-app", "search": [
                         {"phrase": "vocabulary flashcards", "state": "out", "why": []}]}]}]}
    sent = needs.sends(doc, goal=goal)
    assert [s.phrase for s in sent] == ["vocabulary flashcards"]
    assert not any(s.subject or s.source_hint == "subject" for s in sent)


def test_a_real_subject_is_still_sent_first():
    from puffpai.wizard import needs
    goal = "I want to build a wedding planner bot. I'll need to be able to plan the budget."
    doc = {"goal_digest": needs.goal_digest(goal), "held": [], "kind": "wedding planner",
           "subject": {"phrase": "wedding planner", "notes": [], "sendable": True},
           "jobs": [{"id": "j1", "name": "Plan the budget", "ticked": True,
                     "parts": [{"kind": "code-app", "search": [
                         {"phrase": "wedding budget", "state": "out", "why": []}]}]}]}
    sent = needs.sends(doc, goal=goal)
    assert sent[0].phrase == "wedding planner" and sent[0].subject


# -- her own list -------------------------------------------------------------------
def test_what_the_bot_is_is_not_one_of_her_jobs():
    goal = ("I want a bot to be my study partner. It should quiz me on vocabulary, "
            "give me reading practice, and keep a log of my scores.")
    items = needs_rules.explicit_items(goal)
    assert not any(i.lower().startswith("be ") for i in items)
    assert "give me reading practice" in items           # the verb is kept


def test_a_verb_the_list_does_not_know_is_not_replaced_by_the_one_before():
    goal = ("It should track vet visits, look up any recalls, and help me understand "
            "what a symptom means.")
    items = needs_rules.explicit_items(goal)
    assert "understand what a symptom means" in items
    assert not any(i.startswith("look understand") for i in items)


def test_things_she_wants_named_directly_are_read_as_her_list():
    goal = ("We just moved to a farmhouse. I want reminders on when to feed the chickens "
            "and what to plant each month, and help figuring out why the hens stopped laying.")
    items = needs_rules.explicit_items(goal)
    assert len(items) == 2
    assert items[0].startswith("reminders on when to feed")


def test_i_want_a_bot_is_not_read_as_a_list():
    assert needs_rules.explicit_items("I want a garden helper bot for our raised beds.") == []


def test_the_article_of_a_name_is_not_kept_private():
    goal = "I sing in a choir called The Blue Notes and we rehearse on Thursdays."
    private = needs_rules.hard_words(goal)
    assert "the" not in private
    assert "blue" in private and "notes" in private


# -- GitHub data searches -------------------------------------------------------------
def test_a_data_job_spends_one_search_unless_there_is_spare_allowance():
    from puffpai.catalogs.searchers import github as G
    from puffpai.security import outbound_query as oq
    goal = "I want a bot to keep a planting calendar for my garden."
    got = oq.screen(["planting calendar"], goal=goal, source_hint="j1")
    assert got.sendable
    job = {"id": "j1", "name": "Planting calendar", "parts": [
        {"kind": "data-template", "search": [{"phrase": "planting calendar"}]}]}
    shapes = G._shapes(job, got.sendable, kind="data-template", row={"query_template": ""})
    first = [u for u, _p, tier in shapes if tier == 0]
    assert len(first) == 1 and "+dataset+" in first[0]
    later = [u for u, _p, tier in shapes if G._is_later_data_word(u)]
    assert len(later) == 2 and all(t >= 1 for u, _p, t in shapes if u in later)


# -- Codeberg -------------------------------------------------------------------------
def test_a_codeberg_search_waits_only_a_short_time(monkeypatch):
    from puffpai import source_fetch
    from puffpai.catalogs.searchers import codeberg
    seen: list[float] = []

    def fake_fetch(url, **kw):
        seen.append(source_fetch.request_timeout())
        return {"status": 200, "body": b'{"data": []}'}
    monkeypatch.setattr(codeberg.S, "fetch", fake_fetch)
    row = {"query_template": "https://codeberg.org/api/v1/repos/search?q={words}"
                             "&includeDesc=true&limit={per_page}&sort=stars&order=desc"}
    codeberg._run_search("garden", part="code-app", budget=None, row=row, progress=None)
    assert seen == [codeberg.SEARCH_TIMEOUT_S]
    assert source_fetch.request_timeout() == source_fetch.TIMEOUT_S   # only inside the search


# -- closer looks -------------------------------------------------------------------------
def _gh(size_kb, language="JavaScript", stars=0):
    return {"id": "github:o/r", "source": "github", "url": "https://github.com/o/r",
            "repo": {"host": "github.com", "owner": "o", "name": "r", "size_kb": size_kb,
                     "language": language, "stars": stars}}


def test_a_small_project_is_looked_at_before_a_big_one_of_the_same_fit():
    from puffpai.wizard import find_parts
    small = find_parts._look_worth(_gh(400), 0.55)
    big = find_parts._look_worth(_gh(90_000), 0.55)
    assert small - big == pytest.approx(find_parts.BIG_LOOK_PENALTY)


def test_a_listed_server_only_app_is_looked_at_last(monkeypatch):
    from puffpai.catalogs.searchers import awesome_selfhosted
    from puffpai.wizard import find_parts
    lists = {"https://github.com/o/php-app": {"platforms": ["PHP", "Docker"]},
             "https://github.com/o/js-app": {"platforms": ["Nodejs", "Docker"]},
             "https://github.com/o/docker-app": {"platforms": ["Docker"]}}
    monkeypatch.setattr(awesome_selfhosted, "listed", lambda url: lists.get(url))

    def row(name):
        return {"id": f"awesome_selfhosted:{name}", "source": "awesome_selfhosted",
                "url": f"https://github.com/o/{name}", "repo": {"host": "github.com"}}
    assert find_parts._listed_server_only(row("php-app"))
    assert not find_parts._listed_server_only(row("js-app"))
    assert not find_parts._listed_server_only(row("docker-app"))     # cannot tell
    assert find_parts._look_worth(row("php-app"), 0.6) \
        < find_parts._look_worth(row("js-app"), 0.6)


def test_nothing_here_adds_a_word_that_is_not_hers_to_a_query():
    # The data shapes add only the fixed data words; nothing from a job's
    # name or a README ever enters a query.
    from puffpai.catalogs.searchers import github as G
    from puffpai.security import outbound_query as oq
    goal = "I want a bot to keep a planting calendar for my garden."
    got = oq.screen(["planting calendar"], goal=goal, source_hint="j1")
    job = {"id": "j1", "name": "Planting calendar PPTEST-JOBNAME", "parts": [
        {"kind": "data-template", "search": [{"phrase": "planting calendar"}]}]}
    for url, _part, _tier in G._shapes(job, got.sendable, kind="data-template",
                                        row={"query_template": ""}):
        assert "pptest" not in url.lower()


# -- GitHub subject searches ------------------------------------------------------------
@pytest.mark.parametrize("goal, subject, any_language_tier", [
    ("I want a beekeeping bot for my hives.", "beekeeping", 1),
    ("I want to build a wedding planner bot.", "wedding planner", 0),
])
def test_one_word_subject_searches_every_language_only_on_spare_allowance(
        goal, subject, any_language_tier):
    from puffpai.catalogs.searchers import github as G
    from puffpai.security import outbound_query as oq
    subj = oq.subject_sendable(goal)
    assert subj is not None and subj.phrase == subject
    template = ("https://api.github.com/search/repositories?q={words}+stars:>={min_stars}"
                "+archived:false+fork:false&sort=stars&order=desc&per_page={per_page}")
    shapes = G._shapes({"id": "subject"}, [subj], kind="subject",
                       row={"query_template": template})
    any_language = G._search_url(template, oq.url_quote(subj))
    assert [t for u, _p, t in shapes if u == any_language] == [any_language_tier]
    assert shapes[0][2] == 0                     # the recent best match always runs


# -- GitHub archives -------------------------------------------------------------------
def test_an_archive_that_never_answers_pauses_archives_then_stops_them(monkeypatch):
    # The archive host stopped answering after a
    # burst, and every GitHub look waited 20 s on it. Now one archive waits at
    # most ZIP_TIMEOUT_S, archives pause for ZIP_COOLDOWN_S (the looks read
    # file by file), and after a second one they stop for this build.
    from puffpai import source_fetch
    from puffpai.catalogs import search_budget
    from puffpai.catalogs.searchers import github as G
    budget = search_budget.SearchBudget(wizard=True)
    now = [1000.0]
    monkeypatch.setattr(G.time, "monotonic", lambda: now[0])
    calls: list[float] = []

    def hung(url, **kw):
        calls.append(source_fetch.request_timeout())
        return {"status": None, "reason": "no_answer", "body": None}
    monkeypatch.setattr(G.S, "fetch", hung)
    url = G.zip_branch_url("o", "r", "main")

    def read():
        return G.zip_reader(url, host=G.CODELOAD_HOST, pool=G.POOL_ZIP, owner="o", name="r",
                            sha=None, budget=budget)
    assert read() is None and calls == [G.ZIP_TIMEOUT_S]
    assert read() is None and len(calls) == 1          # paused: not asked again
    now[0] += G.ZIP_COOLDOWN_S + 1
    assert read() is None and len(calls) == 2          # asked once more after the pause
    assert budget.later(G.POOL_ZIP)                     # second time: no more archives
    now[0] += 3600
    assert read() is None and len(calls) == 2


def test_a_refused_archive_is_not_a_hang(monkeypatch):
    from puffpai.catalogs import search_budget
    from puffpai.catalogs.searchers import github as G
    budget = search_budget.SearchBudget(wizard=True)
    monkeypatch.setattr(G.S, "fetch", lambda url, **kw: {"status": None,
                                                         "reason": "switched_off"})
    url = G.zip_branch_url("o", "r", "main")
    for _ in range(3):
        G.zip_reader(url, host=G.CODELOAD_HOST, pool=G.POOL_ZIP, owner="o", name="r",
                     sha=None, budget=budget)
    assert not budget.later(G.POOL_ZIP)
    assert not G._zip_paused(budget, G.POOL_ZIP)


# -- the brain's check -------------------------------------------------------------------
@pytest.mark.parametrize("name, asked", [
    ("Give her puzzles to work through", "Give puzzles to work through"),
    ("Runs a pomodoro timer for us", "Runs a pomodoro timer"),
    ("Keep our session notes", "Keep session notes"),
    ("Plan the budget", "Plan the budget"),
    ("Remind me about flea and heartworm meds", "Remind about flea and heartworm meds"),
    ("her", "her"),                              # nothing left: asked as it is
])
def test_the_brain_is_asked_about_the_job_without_the_people_in_it(name, asked):
    from puffpai.catalogs import meaning
    assert meaning.judge_job_words(name) == asked


def test_a_tick_is_judged_on_its_job_without_pronouns():
    from puffpai.catalogs import meaning
    names = {"j1": "Give her puzzles to work through"}
    f = {"meaning": {"state": "ticked", "job": "j1"}}
    assert meaning._judge_jobs(f, names) == "Give puzzles to work through"


def _tick(alt):
    return {"id": "github:o/review", "source": "github", "part": "code-app", "name": "review",
            "what_it_is": "Review your games and play against an engine.",
            "meaning": {"state": "ticked", "job": "j1", "fit": 0.6, "judge": None,
                        "alt_job": alt}}


@pytest.mark.parametrize("second, state, job", [
    ("yes", "ticked", "j2"), ("unsure", "ticked", "j2"), ("no", "might_help", "j1")])
def test_a_refused_tick_is_asked_once_about_the_next_job_it_fits(second, state, job):
    from puffpai.catalogs import meaning
    jobs = [{"id": "j1", "name": "Play practice games on a real board"},
            {"id": "j2", "name": "Analyze her games"}]
    asked: list[str] = []
    answers = iter(["no", second])

    def ask(messages, **kw):
        asked.append(messages[1]["content"])
        return '{"answers": {"1": "%s"}}' % next(answers)
    row = _tick("j2")
    meaning.judge([row], jobs, ask)
    assert len(asked) == 2 and '"Analyze games"' in asked[1]     # her job, no pronoun
    assert row["meaning"]["state"] == state and row["meaning"]["job"] == job


def test_a_refused_tick_with_no_other_strong_job_is_shown_unticked():
    from puffpai.catalogs import meaning
    asked: list[str] = []

    def ask(messages, **kw):
        asked.append(messages[1]["content"])
        return '{"answers": {"1": "no"}}'
    row = _tick(None)
    meaning.judge([row], [{"id": "j1", "name": "Play games"}], ask)
    assert len(asked) == 1 and row["meaning"]["state"] == "might_help"


def test_the_next_strong_job_is_recorded_when_scoring(monkeypatch):
    import numpy as np
    from puffpai.catalogs import meaning
    jobs = [{"id": "j1", "name": "Play practice games", "source": "asked", "parts": []},
            {"id": "j2", "name": "Analyze games", "source": "usual", "parts": []},
            {"id": "j3", "name": "Log tournaments", "source": "asked", "parts": []}]
    app = {"id": "github:o/r", "source": "github", "part": "code-app", "name": "r",
           "what_it_is": "An app.", "mode_guess": "static_web", "integrable": True,
           "repo": {"stars": 50, "license_reported": "MIT"},
           "trust": {"floor_ok": True, "path": "stars", "pretick_ok": True, "reasons": []}}
    monkeypatch.setattr(meaning, "helper_state", lambda: "ready")
    monkeypatch.setattr(meaning, "embed", lambda texts: np.eye(3, meaning.DIM, dtype=np.float32))
    monkeypatch.setattr(meaning, "_candidate_vectors", lambda rows: np.array(
        [[0.70, 0.62, 0.30] + [0.0] * (meaning.DIM - 3)], dtype=np.float32))
    got = meaning.score([app], jobs, kind="chess coach", stage=1)[0]["meaning"]
    assert got["state"] == "ticked" and got["job"] == "j1" and got["alt_job"] == "j2"


# -- the failure proof -----------------------------------------------------------------
def _harness():
    import importlib.util
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("r3_acceptance_fix1",
                                                  root / "scripts" / "measure_goals.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["r3_acceptance_fix1"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_a_find_the_plan_skips_on_purpose_is_proven_trust_blocked():
    # An unread find with 0 stars, last changed in
    # 2016, can never pass the trust floor and the plan does not read it on
    # purpose; the proof said "not looked at in time", so a goal where nothing
    # trusted exists could never be proven.
    h = _harness()
    old = {"id": "github:o/old-tracker", "source": "github", "part": "code-app",
           "mode": "unknown", "trust": {"floor_ok": False, "path": "", "reasons": []},
           "facts": {"stars": 0, "license_reported": "MIT", "pushed_at": "2016-02-17"},
           "meaning": {"fit": 0.56}}
    import datetime
    recent = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    new = dict(old, id="github:o/new-tracker", facts=dict(old["facts"], pushed_at=recent))
    assert h.proof_reason(old, set()).startswith("trust:")
    assert h.proof_reason(new, set()) == "not looked at in time"
    assert h._address_of(old) == "https://github.com/o/old-tracker"
    assert h._address_of({"id": "kiwix:x"}) == ""


@pytest.mark.parametrize("name, halves", [
    ("Metronome and tuner", ["Metronome", "Tuner"]),
    ("Track oil changes and tire rotations", ["Track oil changes", "Track tire rotations"]),
    ("Store chord charts and lyrics", ["Store chord charts", "Store lyrics"]),
    ("Plan the budget", []),
    ("Track initiative and hit points and spell slots", []),
])
def test_an_and_job_is_asked_in_halves(name, halves):
    from puffpai.catalogs import meaning
    assert meaning.job_halves(name) == halves


@pytest.mark.parametrize("answers, state", [(["no", "no", "yes"], "ticked"),
                                            (["no", "no", "no"], "might_help")])
def test_a_refused_tick_for_an_and_job_is_asked_about_each_half(answers, state):
    from puffpai.catalogs import meaning
    asked: list[str] = []
    replies = iter(answers)

    def ask(messages, **kw):
        asked.append(messages[1]["content"])
        return '{"answers": {"1": "%s"}}' % next(replies)
    row = _tick(None)
    meaning.judge([row], [{"id": "j1", "name": "Metronome and tuner"}], ask)
    assert len(asked) == 3 and '"Metronome"' in asked[1] and '"Tuner"' in asked[2]
    assert row["meaning"]["state"] == state and row["meaning"]["job"] == "j1"


def test_a_fit_recorded_at_the_tick_line_is_decided_at_the_tick_line(monkeypatch):
    import numpy as np
    from puffpai.catalogs import meaning
    jobs = [{"id": "j1", "name": "Keep time", "source": "asked", "parts": []}]
    app = {"id": "github:o/r", "source": "github", "part": "code-app", "name": "r",
           "what_it_is": "An app.", "mode_guess": "static_web", "integrable": True,
           "repo": {"stars": 50, "license_reported": "MIT"},
           "trust": {"floor_ok": True, "path": "stars", "pretick_ok": True, "reasons": []}}
    monkeypatch.setattr(meaning, "helper_state", lambda: "ready")
    monkeypatch.setattr(meaning, "embed", lambda texts: np.eye(1, meaning.DIM, dtype=np.float32))
    monkeypatch.setattr(meaning, "_candidate_vectors", lambda rows: np.array(
        [[0.54996] + [0.0] * (meaning.DIM - 1)], dtype=np.float32))
    got = meaning.score([app], jobs, kind="", stage=1)[0]["meaning"]
    assert got["fit"] == 0.5 and got["state"] == "ticked"
