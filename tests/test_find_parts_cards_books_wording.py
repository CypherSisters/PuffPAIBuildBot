"""find_parts: live cards, books and pictures by meaning, the
no-jobs fallback, wording, evidence, the judge and "Similar projects also have".

Fakes follow the pinned signatures (see test_find_parts_plan_items); the live-card
and book lines use BuildBot's real saved lists (no network).
"""
from __future__ import annotations

import copy
import json
import sys

import pytest

from puffpai.catalogs import meaning
from puffpai.wizard import find_parts, needs
from tests.test_find_parts_plan_items import (BUDGET_APP, BUDGET_JOB, GOAL, FakeWhere, _job, _needs,
                                      lanes, repo_find)  # noqa: F401  (lanes is a fixture)

HELPER = pytest.mark.skipif(meaning.helper_state() != "ready",
                            reason="BuildBot's reading helper is not on this computer")


# -- the no-jobs fallback -----------------------------------------------------------------
def test_no_jobs_keeps_the_books_offered_before_and_no_live_cards(lanes, monkeypatch):
    from puffpai.wizard import factory
    lanes(FakeWhere())
    seen = []

    def books(ctx):
        seen.append(("books", ctx.goal, ctx.internet))
        return [{"id": "knowledge:kiwix:x", "section": "knowledge", "label": "A book"}]
    monkeypatch.setattr(factory, "knowledge_items", books)
    out = find_parts.plan_items(_needs([]), goal=GOAL, online=True, allow_send=False, cfg={})
    # Live cards are ingredients of a job in the build plan.
    assert [i["id"] for i in out["items"]] == ["knowledge:kiwix:x"]
    assert seen == [("books", GOAL, True)]
    assert find_parts.last_evidence()["fallback"] is True


def test_unticked_jobs_are_not_the_no_jobs_case(lanes, monkeypatch):
    from puffpai.wizard import factory
    lanes(FakeWhere())
    monkeypatch.setattr(factory, "knowledge_items", lambda ctx: pytest.fail("fallback used"))
    job = copy.deepcopy(BUDGET_JOB)
    job["ticked"] = False
    out = find_parts.plan_items(_needs([job]), goal=GOAL, online=False, allow_send=False, cfg={})
    assert out["items"] == []


# -- wording ------------------------------------------------------------------------------
def test_a_detail_never_ends_mid_word_and_ends_with_a_full_stop():
    text = ("A seating chart and guest list tool that keeps every table, every guest and "
            "every meal choice in one place for the planner")
    got = find_parts._sentence(text, 60)
    assert len(got) <= 60 and got.endswith(".")
    assert text.startswith(got[:-1]) and text[len(got) - 1] in " ,"


def test_long_dashes_and_broken_characters_never_reach_her():
    assert find_parts._clean("Wedyesday — wedding planning", 80) == \
        "Wedyesday, wedding planning"
    assert "�" not in find_parts._clean("Wed�yesday", 80)


def test_a_note_never_has_an_empty_address():
    row = {"id": "mcp:x", "source": "mcp", "name": "budget-tracker", "url": "",
           "mode_guess": "mcp_server", "integrable": False}
    note = find_parts._note(row)
    assert "(," not in note and "( " not in note
    assert note == f"budget-tracker ({meaning.NOTE_OTHER})"


def test_repeated_progress_lines_are_said_once(lanes):
    lines: list[str] = []
    where = FakeWhere(online={"j1": [BUDGET_APP]})

    def find_online(job, phrases, *, kind, budget, progress=None):
        if progress:
            progress("Looking on Codeberg is not in this build yet, so nothing was sent there.")
        return []
    where.mod.find_online = find_online
    lanes(where)
    jobs = [copy.deepcopy(BUDGET_JOB), _job("j2", "Guest list", "Keeps the guest list.",
                                            "usual", ["wedding guest list"])]
    find_parts.plan_items(_needs(jobs), goal=GOAL, online=True, allow_send=True, cfg={},
                          on_line=lines.append)
    assert lines.count("Looking on Codeberg is not in this build yet, so nothing was sent "
                       "there.") == 1


# -- evidence -----------------------------------------------------------------------------
@HELPER
def test_evidence_holds_every_raw_find_and_why_it_was_left_out(lanes):
    page = repo_find("acme", "our-wedding", "Our wedding website", topics=["wedding-website"])
    lanes(FakeWhere(online={"j1": [BUDGET_APP, page]}))
    find_parts.plan_items(_needs([copy.deepcopy(BUDGET_JOB)]), goal=GOAL, online=True,
                          allow_send=True, cfg={})
    ev = find_parts.last_evidence()
    by_id = {f["id"]: f for f in ev["finds"]}
    assert by_id["github:acme/our-wedding"]["meaning"]["why_dropped"] == "page"
    assert "j1:code-app:online" in by_id["github:acme/wedding-budget"]["found_by"]
    assert GOAL not in json.dumps(ev)


# -- a static tool that stores nothing is a tool -------------------------------------------
@HELPER
def test_a_static_tool_that_stores_nothing_is_offered(lanes):
    timer = repo_find("acme", "focus-timer", "A pomodoro timer for focused study sessions.",
                      keeps=False, lines=["Pomodoro timer for study sessions",
                                          "Short and long breaks between focus sessions"])
    job = _job("j1", "Pomodoro timer", "Times focused study sessions with breaks.", "asked",
               ["pomodoro timer"])
    lanes(FakeWhere(online={"j1": [timer]}))
    out = find_parts.plan_items(_needs([job]), goal=GOAL, online=True, allow_send=True, cfg={})
    assert [i["apply"]["ref"] for i in out["items"]] == ["github:acme/focus-timer"]


# -- live cards and books by meaning ------------------------------------------------------
def _seed_row(sid: str) -> dict:
    from puffpai.wizard import factory
    cand = factory.candidate(sid)
    assert cand, sid
    return {"schema": "found/v1", "id": sid, "source": "seeds", "part": "live-info",
            "name": cand["name"], "what_it_is": cand.get("what_it_is") or "",
            "url": cand.get("url") or "", "mode_guess": "other", "integrable": False,
            "trust": {"floor_ok": True, "path": "", "pretick_ok": False, "reasons": []}}


def _a_seed_id(word: str) -> str:
    from puffpai.catalogs import live
    corpus, _idf = live._live_corpus()
    for cand in corpus:
        if cand.get("source_catalog") == "api_seeds" and word in str(cand.get("name")).lower():
            return str(cand["id"])
    pytest.skip(f"no saved live-info row about {word}")


@HELPER
def test_a_live_info_find_is_never_a_plan_line(lanes):
    """It is an ingredient of a job in the build plan, made after the one Yes."""
    sid = _a_seed_id("recall")
    recalls = _job("j1", "Look up recalls", "Checks safety recalls for a car.", "asked",
                   ["car recall"], ("live-info",))
    budget = copy.deepcopy(BUDGET_JOB)
    lanes(FakeWhere(local={"j1": [_seed_row(sid)], "j2": [_seed_row(sid)]}))
    budget["id"] = "j2"
    out = find_parts.plan_items(_needs([recalls, budget]), goal=GOAL, online=False,
                                allow_send=False, cfg={})
    assert not [i for i in out["items"] if i["section"] == "live_info"
                or (i.get("apply") or {}).get("kind") in ("live_feed", "live_source")]


@HELPER
def test_a_live_card_meets_no_job_without_a_live_info_part(lanes):
    sid = _a_seed_id("recall")
    job = _job("j1", "Look up recalls", "Checks safety recalls for a car.", "asked",
               ["car recall"], ("code-app",))
    lanes(FakeWhere(local={"j1": [_seed_row(sid)]}))
    out = find_parts.plan_items(_needs([job]), goal=GOAL, online=False, allow_send=False, cfg={})
    assert not [i for i in out["items"] if i["section"] == "live_info"]


@HELPER
def test_books_only_for_learn_jobs_and_never_general_encyclopedias(lanes):
    learn = _job("j1", "Learn the openings", "Teaches chess openings.", "asked",
                 ["chess opening"], ("code-app", "knowledge"))
    learn["learn"] = True
    plain = _job("j2", "Keep a game record", "Keeps a record of chess games.", "asked",
                 ["chess game record"], ("code-app", "knowledge"))
    lanes(FakeWhere())
    doc = _needs([learn, plain])
    doc["kind"] = "chess coach"
    out = find_parts.plan_items(doc, goal=GOAL, online=False, allow_send=False, cfg={})
    books = [i for i in out["items"] if i["section"] == "knowledge"]
    assert books, "a chess book was expected for the learn job"
    assert {b["need"] for b in books} == {"j1"}
    assert all(b["apply"]["kind"] == "knowledge.add" for b in books)
    assert not any("wikipedia" in b["id"] or "vikidia" in b["id"] for b in books)
    assert len(books) <= find_parts.MAX_BOOKS


# -- pictures -----------------------------------------------------------------------------
def test_a_job_about_her_pictures_gets_buildbots_own_two_lines(lanes):
    job = _job("j1", "Track themes from pictures", "Collects colours and themes from photos.",
               "asked", ["wedding theme picture"], ("pictures",))
    lanes(FakeWhere())
    out = find_parts.plan_items(_needs([job]), goal=GOAL, online=False, allow_send=False, cfg={})
    own = [i for i in out["items"] if ":buildbot:" in i["id"]]
    assert [i["apply"]["kind"] for i in own] == ["folder_palette", "folder_palette"]
    assert all(i["default_on"] and i["section"] == "pictures" and i["need"] == "j1" for i in own)
    assert own[1]["label"] == find_parts.PALETTE
    assert find_parts.NOTHING_FOUND not in out["notes"].get("j1", [])


def test_a_job_that_only_mentions_pictures_gets_no_palette(lanes):
    job = _job("j1", "Show pictures of planets", "Shows photos of planets.", "asked",
               ["planet pictures"], ("pictures",))
    lanes(FakeWhere())
    out = find_parts.plan_items(_needs([job]), goal=GOAL, online=False, allow_send=False, cfg={})
    assert not [i for i in out["items"] if ":buildbot:" in i["id"]]


@HELPER
def test_stock_pictures_are_offered_unticked(lanes):
    pic = {"schema": "found/v1", "id": "commons:File:Wedding_table.jpg", "source": "commons",
           "part": "pictures", "name": "Wedding table decoration",
           "what_it_is": "Photo of a wedding table with flowers and candles, a theme idea.",
           "url": "https://commons.wikimedia.org/wiki/File:Wedding_table.jpg",
           "picture": {"license": "CC-BY-4.0", "host": "upload.wikimedia.org"},
           "mode_guess": "other", "integrable": False,
           "trust": {"floor_ok": True, "path": "", "pretick_ok": False, "reasons": []}}
    job = _job("j1", "Collect wedding theme ideas", "Collects wedding decoration and theme "
               "ideas from photos.", "asked", ["wedding theme"], ("pictures",))
    lanes(FakeWhere(online={"j1": [pic]}))
    out = find_parts.plan_items(_needs([job]), goal=GOAL, online=True, allow_send=True, cfg={})
    stock = [i for i in out["items"] if i["apply"]["kind"] == "picture_set"]
    assert len(stock) == 1 and stock[0]["default_on"] is False
    assert stock[0]["section"] == "pictures"


# -- the judge ----------------------------------------------------------------------------
@HELPER
def test_the_judge_only_takes_a_tick_away(lanes, monkeypatch):
    asked: list = []

    def ask(messages):
        # The judge says "no"; the purpose question (a second, different
        # question) is not sure, so the judge is alone.
        if messages[0]["content"] == meaning.PURPOSE_SYSTEM:
            return json.dumps({"answers": {"1": "unsure"}})
        asked.append(messages)
        return json.dumps({"answers": {"1": "no"}})
    monkeypatch.setattr(find_parts, "_judge_client", lambda cfg: ask)
    lanes(FakeWhere(online={"j1": [BUDGET_APP]}))
    out = find_parts.plan_items(_needs([copy.deepcopy(BUDGET_JOB)]), goal=GOAL, online=True,
                                allow_send=True, cfg={})
    item = next(i for i in out["items"] if i["apply"]["ref"] == BUDGET_APP["id"])
    assert item["default_on"] is False and item["might_help"] is True
    rec = find_parts.found_for(item["id"])
    assert rec["meaning"]["judge"] == "no"
    # Asked ahead (after the first look) and again after the second; the
    # real client keeps answers, so the brain answers it once (next test).
    assert 1 <= len(asked) <= 2 and all(m == asked[0] for m in asked)


def test_the_judge_asks_ahead_and_the_brain_answers_each_question_once(lanes, monkeypatch):
    # The judge after the second look had half a second left; the
    # questions are asked while the closer looks run, and kept.
    from puffpai.wizard import relevance
    asked: list = []

    def fake_client(url, model, **kw):
        def ask(messages, format=None):
            if messages[0]["content"] == meaning.JUDGE_SYSTEM:   # not the warm-up
                asked.append(json.dumps(messages))
            if messages[0]["content"] == meaning.PURPOSE_SYSTEM:
                return json.dumps({"answers": {"1": "unsure"}})
            return json.dumps({"answers": {"1": "no"}})
        return ask
    monkeypatch.setattr(relevance, "client", fake_client)
    relevance._MEMO.clear()
    lanes(FakeWhere(online={"j1": [BUDGET_APP]}))
    try:
        out = find_parts.plan_items(_needs([copy.deepcopy(BUDGET_JOB)]), goal=GOAL, online=True,
                                    allow_send=True,
                                    cfg={"ollama_url": "http://127.0.0.1:11435",
                                         "chosen_model": "qwen2.5:7b-instruct",
                                         "brain_tok_s": 30})
    finally:
        relevance._MEMO.clear()
    item = next(i for i in out["items"] if i["apply"]["ref"] == BUDGET_APP["id"])
    assert find_parts.found_for(item["id"])["meaning"]["judge"] == "no"
    assert item["default_on"] is False
    assert len(asked) == len(set(asked)) >= 1


def test_answers_given_ahead_are_read_after_the_stop_time(lanes, monkeypatch):
    # The second look ended after the judge's stop
    # time, so the answers the brain had given ahead were never read.
    from puffpai.wizard import relevance
    asked: list = []

    def fake_client(url, model, **kw):
        def ask(messages, format=None):
            if messages[0]["content"] == meaning.JUDGE_SYSTEM:   # not the warm-up
                asked.append(1)
            if messages[0]["content"] == meaning.PURPOSE_SYSTEM:
                return json.dumps({"answers": {"1": "unsure"}})
            return json.dumps({"answers": {"1": "no"}})
        return ask
    monkeypatch.setattr(relevance, "client", fake_client)
    cfg = {"ollama_url": "http://127.0.0.1:11435", "chosen_model": "qwen2.5:7b-instruct",
           "brain_tok_s": 30}
    relevance._MEMO.clear()
    lanes(FakeWhere(online={"j1": [BUDGET_APP]}))
    try:
        find_parts.plan_items(_needs([copy.deepcopy(BUDGET_JOB)]), goal=GOAL, online=True,
                              allow_send=True, cfg=cfg)
        first = len(asked)
        find_parts._MEMO.clear()
        monkeypatch.setattr(find_parts, "JUDGE_RESERVE_S", 10_000.0)   # always too late
        out = find_parts.plan_items(_needs([copy.deepcopy(BUDGET_JOB)]), goal=GOAL,
                                    online=True, allow_send=True, cfg=cfg)
    finally:
        relevance._MEMO.clear()
    assert first >= 1 and len(asked) == first          # nothing asked the second time
    item = next(i for i in out["items"] if i["apply"]["ref"] == BUDGET_APP["id"])
    assert find_parts.found_for(item["id"])["meaning"]["judge"] == "no"


def test_the_judge_ahead_asks_nothing_once_stopped():
    import threading
    started, release = threading.Event(), threading.Event()
    asked: list = []

    def slow(messages, **kw):
        asked.append(1)
        started.set()
        release.wait(5)
        return '{"answers": {"1": "yes"}}'
    rows = [{"id": f"github:o/r{n}", "source": "github", "part": "code-app", "name": f"r{n}",
             "what_it_is": "x", "meaning": {"job": "j1", "state": "ticked", "fit": 0.6}}
            for n in range(5)]
    import time as _t
    ahead = find_parts._JudgeAhead(rows, [{"id": "j1", "name": "Plan"}], slow,
                                   stop_at=_t.monotonic() + 60, kind="")
    assert started.wait(5)
    ahead._halt.set()            # what stop() does first
    release.set()
    ahead.stop()
    assert len(asked) == 1 and not ahead._thread.is_alive()


def test_the_judge_ahead_also_asks_the_tick_question_for_a_likely_tick():
    # A line that became a tick only after the
    # second look had its tick question (about her job) never asked ahead.
    import time as _t
    asked: list = []
    rows = [{"id": "github:o/r", "source": "github", "part": "code-app", "name": "r",
             "what_it_is": "x", "meaning": {"job": "j2", "best_job": "j2", "own_job": "j1",
                                            "state": "might_help", "fit": 0.47,
                                            "blurb": 0.55}},
            {"id": "github:o/low", "source": "github", "part": "code-app", "name": "low",
             "what_it_is": "x", "meaning": {"job": "j2", "best_job": "j2", "own_job": "j1",
                                            "state": "might_help", "fit": 0.42,
                                            "blurb": 0.45}}]
    jobs = [{"id": "j1", "name": "Listed job"}, {"id": "j2", "name": "Added job"}]
    ahead = find_parts._JudgeAhead(
        rows, jobs, lambda m, **kw: asked.append(m[1]["content"]) or '{"answers": {"1": "yes"}}',
        stop_at=_t.monotonic() + 60, kind="")
    ahead._thread.join(10)
    assert '"Listed job"' in asked[0] and '"r"' in asked[0]          # the tick question first
    assert sum('"Listed job"' in a for a in asked) == 1                # not for the weak line
    assert rows[0]["meaning"]["state"] == "might_help"              # the plan's rows untouched


def test_the_kept_answers_never_keep_an_error(monkeypatch):
    from puffpai.wizard import relevance
    replies = ["[Error] Ollama request timed out.", '{"answers": {"1": "yes"}}',
               '{"answers": {"1": "no"}}']
    monkeypatch.setattr(relevance, "client",
                        lambda url, model, **kw: lambda m, format=None: replies.pop(0))
    relevance._MEMO.clear()
    try:
        ask = relevance.memo_client("http://127.0.0.1:11435", "m")
        msgs = [{"role": "user", "content": "q"}]
        assert ask(msgs) == ""                 # timed out: not kept
        assert ask(msgs) == '{"answers": {"1": "yes"}}'
        assert ask(msgs) == '{"answers": {"1": "yes"}}'   # kept
        assert ask.kept(msgs) == '{"answers": {"1": "yes"}}'
        assert ask.kept([{"role": "user", "content": "never asked"}]) == ""
        assert replies == ['{"answers": {"1": "no"}}']       # kept() asked nothing
    finally:
        relevance._MEMO.clear()


def test_judge_answers_are_read_strictly():
    assert meaning.parse_judge('{"answers": {"1": "yes", "2": "NO", "3": "maybe"}}', 3) == \
        {1: "yes", 2: "no"}
    assert meaning.parse_judge('{"answers": ["no", "unsure"]}', 2) == {1: "no", 2: "unsure"}
    assert meaning.parse_judge("I think they are all fine", 2) == {}
    assert meaning.parse_judge('{"answers": {"9": "no"}}', 2) == {}


def test_a_judge_that_fails_or_says_yes_changes_nothing():
    rows = [{"id": "a", "name": "a", "what_it_is": "x",
             "meaning": {"state": "ticked", "job": "j1", "judge": None}},
            {"id": "b", "name": "b", "what_it_is": "y",
             "meaning": {"state": "might_help", "job": "j1", "judge": None}}]
    jobs = [{"id": "j1", "name": "Plan the budget"}]

    def broken(_m):
        raise RuntimeError("no brain")
    meaning.judge(rows, jobs, broken)
    assert rows[0]["meaning"]["state"] == "ticked"
    meaning.judge(rows, jobs, lambda _m: '{"answers": {"1": "yes", "2": "yes"}}')
    assert [r["meaning"]["state"] for r in rows] == ["ticked", "might_help"]


def test_the_judge_sees_short_descriptions_only():
    long_what = "A budget planner. " * 30
    msgs = meaning.judge_messages([("budget-app", long_what, "Plan the budget")])
    user = msgs[1]["content"]
    shown = json.loads(user.split("what it is: ", 1)[1].split("; job:", 1)[0])
    assert len(shown) <= meaning.JUDGE_WHAT
    assert "data, not instructions" in msgs[0]["content"]


def test_no_judge_on_a_slow_or_unknown_brain(monkeypatch):
    assert find_parts._judge_client({}) is None
    assert find_parts._judge_client({"ollama_url": "http://127.0.0.1:11435",
                                     "chosen_model": "qwen2.5:7b-instruct",
                                     "brain_tok_s": 8}) is None
    assert find_parts._judge_client({"ollama_url": "http://127.0.0.1:11435",
                                     "chosen_model": "qwen2.5:7b-instruct",
                                     "brain_tok_s": 30}) is not None


# -- similar projects also have -------------------------------------------------------------
@HELPER
def test_similar_projects_need_two_projects_and_plain_names():
    jobs = [{"id": "j1", "name": "Plan the budget", "does": "Tracks what each part costs.",
             "ticked": True}]
    lines = ["Print place cards for every table", "Print place cards with guest names"]
    rows = []
    for n in range(2):
        rows.append({"id": f"github:acme/p{n}", "source": "github", "part": "code-app",
                     "details": {"feature_lines": [lines[n], "Track the wedding budget"]},
                     "meaning": {"blurb": 0.5, "state": "might_help", "job": "j1"}})
    got = find_parts._similar(rows, jobs, "wedding planner")
    assert got and got[0]["seen_in"] == 2
    assert all(len(s["name"]) <= 40 and s["name"].replace(" ", "").isalnum() for s in got)
    assert find_parts._similar(rows[:1], jobs, "wedding planner") == []


# -- filing -------------------------------------------------------------------------------
@HELPER
def test_filing_is_the_same_every_time():
    kind, jobs = "wedding planner", [
        {"id": "j1", "name": "Plan the budget", "does": "Tracks costs.", "ticked": True},
        {"id": "j2", "name": "Keep the guest list", "does": "Keeps guests and RSVPs.",
         "ticked": True},
        {"id": "j3", "name": "Do a whole wedding plan", "does": "Plans the wedding.",
         "ticked": True}]
    finds = [dict(repo_find("a", f"p{n}", what), trust={"floor_ok": True, "path": "stars",
                                                           "pretick_ok": True, "reasons": []})
             for n, what in enumerate(["Wedding budget tracker", "Guest list and RSVP app",
                                       "Wedding planner with budget and guests",
                                       "Wedding seating chart", "Wedding checklist"])]
    first = [f["meaning"]["job"] for f in meaning.score(finds, jobs, kind=kind, stage=1)]
    again = [f["meaning"]["job"] for f in meaning.score(list(reversed(finds)), jobs, kind=kind,
                                                        stage=1)]
    assert first == list(reversed(again))
    assert len(set(first)) >= 2


# -- a library, an extension or a plugin is at best a note ----------------------------------
def test_a_library_is_never_preticked():
    # A library is not a line she can tick at all (a wrapper for
    # macOS Reminders was shown for a wedding), only a note with its reason.
    lib = {"name": "chess.js", "what_it_is": "A TypeScript chess library for move generation.",
           "mode_guess": "web_build", "integrable": True, "source": "github",
           "repo": {"stars": 4400, "license_reported": "MIT"},
           "trust": {"floor_ok": True, "path": "stars", "pretick_ok": True, "reasons": []},
           "meaning": {}}
    assert meaning.not_a_tool(lib)
    assert meaning._classify_why(lib, 0.7)[0] == "note"
    assert meaning.note_reason(lib) == meaning.NOTE_LIBRARY
    phone = dict(lib, name="soon", what_it_is="A reminder application for the macOS menubar.")
    assert meaning.runs_elsewhere(phone) and meaning._classify_why(phone, 0.7)[0] == "note"
    assert meaning.note_reason(phone) == meaning.NOTE_OTHER
    app = dict(lib, name="chess-trainer", what_it_is="Play chess against the computer.")
    assert not meaning.not_a_tool(app)
    assert meaning._classify_why(app, 0.7)[0] == "ticked"
    data = dict(lib, mode_guess="data_template", what_it_is="A library of chess openings.")
    assert not meaning.not_a_tool(data)


# -- notes: projects people use first; unused ones at most one per job ---------------------
def test_notes_prefer_projects_people_use():
    def note(n, stars, fit):
        return {"id": f"github:a/n{n}", "source": "github", "repo": {"stars": stars},
                "trust": {}, "meaning": {"state": "note", "job": "j1", "fit": fit}}
    rows = [note(1, 0, 0.7), note(2, 40, 0.5), note(3, 0, 0.69), note(4, 9, 0.46)]
    meaning._caps(rows)
    kept = [r["id"] for r in rows if r["meaning"]["state"] == "note"]
    assert set(kept) == {"github:a/n2", "github:a/n4", "github:a/n1"}
    unused = [note(5, 0, 0.7), note(6, 1, 0.69), note(7, 2, 0.6)]
    meaning._caps(unused)
    assert [r["id"] for r in unused if r["meaning"]["state"] == "note"] == ["github:a/n5"]
    # A moderate fit with no licence that hardly anyone uses is
    # not shown at all (16 such lines read as a refusal list).
    weak = [note(8, 0, 0.47)]
    meaning._caps(weak)
    assert weak[0]["meaning"]["state"] == "dropped"


def test_tools_for_other_ai_apps_are_not_notes():
    mcp = {"id": "mcp:x", "source": "mcp", "name": "wedding-seat", "integrable": False,
           "trust": {}}
    assert meaning._classify_why(mcp, 0.8)[0] == "dropped"


# -- labels -------------------------------------------------------------------------------
def test_a_page_title_that_says_nothing_is_not_the_label():
    f = repo_find("SikamikanikoBG", "patzer", "Self-hosted game review: play vs Stockfish.")
    f["friendly"] = "Windows"
    assert not find_parts._names_it("Windows", f)
    f2 = repo_find("SubchiBeats", "wedding-planner", "Bunny's Big Day, a wedding planning board.")
    assert find_parts._names_it("Bunny's Big Day", f2)
    assert find_parts._names_it("Wedding Control Board", repo_find("HyVoid", "wedding-planner", ""))


def test_a_judge_no_leaves_out_a_might_help_line_and_never_promotes():
    rows = [{"id": "a", "name": "gardener-dashboard", "what_it_is": "Cluster web frontend",
             "meaning": {"state": "might_help", "job": "j1", "fit": 0.47, "judge": None}},
            {"id": "b", "name": "garden-planner", "what_it_is": "Plan raised beds",
             "meaning": {"state": "might_help", "job": "j1", "fit": 0.45, "judge": None}},
            {"id": "c", "name": "x", "what_it_is": "y",
             "meaning": {"state": "dropped", "job": "j1", "fit": 0.2, "judge": None}}]
    jobs = [{"id": "j1", "name": "Remind me when to water"}]
    meaning.judge(rows, jobs, lambda m: '{"answers": {"1": "%s"}}'
                  % ("yes" if "garden-planner" in m[1]["content"] else "no"))
    assert rows[0]["meaning"]["state"] == "dropped"
    assert rows[0]["meaning"]["why_dropped"] == "the brain says it does not do this job"
    assert rows[1]["meaning"]["state"] == "might_help"
    assert rows[2]["meaning"]["state"] == "dropped" and rows[2]["meaning"]["judge"] is None


def test_closer_looks_go_first_to_used_web_projects():
    web = repo_find("a", "timer", "A timer", stars=40)
    small = repo_find("a", "timer2", "A timer", stars=1)
    other = dict(repo_find("a", "timer3", "A timer", stars=40))
    other["repo"] = dict(other["repo"], language="Go")
    assert find_parts._look_worth(web, 0.5) > find_parts._look_worth(small, 0.5) \
        > find_parts._look_worth(other, 0.5)


def test_a_strong_might_help_line_survives_a_no_and_the_judge_sees_the_best_job():
    rows = [{"id": "a", "name": "initiative", "what_it_is": "DnD 5e combat tracker.",
             "meaning": {"state": "might_help", "job": "j2", "fit": 0.62, "judge": None,
                         "best_job": "j1", "best_jobs": ["j1", "j2"]}}]
    jobs = [{"id": "j1", "name": "Track initiative and hit points"},
            {"id": "j2", "name": "Character manager"}]
    seen = []

    def ask(messages):
        seen.append(messages[1]["content"])
        return '{"answers": {"1": "no"}}'
    meaning.judge(rows, jobs, ask)
    assert rows[0]["meaning"]["state"] == "might_help"
    assert "Track initiative and hit points" in seen[0] and "Character manager" not in seen[0]


def test_the_judge_is_given_the_one_answer_shape():
    shapes = []

    def ask(messages, format=None):
        shapes.append(format)
        return '{"answers": {"1": "yes", "2": "no"}}'
    rows = [{"id": str(n), "name": "x", "what_it_is": "y",
             "meaning": {"state": "ticked", "job": "j1", "fit": 0.6}} for n in range(2)]
    meaning.judge(rows, [{"id": "j1", "name": "Plan the budget"}], ask)
    assert len(shapes) == 2                           # one find per question
    assert shapes[0]["properties"]["answers"]["required"] == ["1"]
    assert shapes[0]["properties"]["answers"]["properties"]["1"]["enum"] == ["no", "unsure", "yes"]


def test_a_moderate_code_find_needs_the_brains_yes():
    def row(fit, judge):
        return {"id": f"github:a/{fit}-{judge}", "source": "github", "part": "code-app",
                "meaning": {"state": "might_help", "fit": fit, "judge": judge}}
    rows = [row(0.38, "yes"), row(0.38, None), row(0.38, "no"), row(0.42, None)]
    meaning.settle(rows)
    assert [r["meaning"]["state"] for r in rows] == ["might_help", "dropped", "dropped",
                                                     "might_help"]


def test_a_wait_past_the_search_time_gives_that_search_up():
    import time as _t
    slept: list[float] = []
    bounded = find_parts._bounded_sleep(slept.append, _t.monotonic() + 5.0)
    bounded(1.0)
    assert slept == [1.0]
    with pytest.raises(find_parts.OutOfTime):
        bounded(40.0)
    assert slept == [1.0]


def test_a_look_that_ends_after_the_plan_is_put_together_raises_nothing():
    warm = find_parts._Warmer()
    warm.finish(wait=False)
    warm.add_texts(["a readme line that came too late"])       # no error in its thread


def test_the_judge_asks_with_the_needs_steps_context_size(monkeypatch):
    # A different context size made the brain load again before
    # the first question, and every question ran out of time.
    from puffpai.wizard import needs_llm, relevance
    got: dict = {}
    monkeypatch.setattr(relevance, "memo_client",
                        lambda url, model, **kw: got.update(kw) or (lambda *a, **k: ""))
    assert find_parts._judge_client({"ollama_url": "http://127.0.0.1:11435",
                                     "chosen_model": "qwen2.5:7b-instruct",
                                     "brain_tok_s": 30}) is not None
    assert got["num_ctx"] == needs_llm.NUM_CTX
    assert got["timeout"] == find_parts.JUDGE_TIMEOUT_S


@HELPER
def test_a_judge_that_answers_nothing_is_no_judge(lanes, monkeypatch):
    seen: list = []
    real = meaning.settle
    monkeypatch.setattr(meaning, "settle", lambda scored, **kw: seen.append(kw) or
                        real(scored, **kw))
    monkeypatch.setattr(find_parts, "_judge_client", lambda cfg: (lambda m, **k: ""))
    lanes(FakeWhere(online={"j1": [BUDGET_APP]}))
    find_parts.plan_items(_needs([copy.deepcopy(BUDGET_JOB)]), goal=GOAL, online=True,
                          allow_send=True, cfg={"x": 1})
    assert seen == [{"judged": False}]


def test_closer_looks_never_all_wait_on_one_website():
    # All six looks waited on Codeberg's once-a-second
    # pace for half a minute while GitHub's file hosts sat idle.
    import time as _t
    looks = find_parts._CloserLooks(object(), None, [], "", until=_t.monotonic() - 1.0,
                                    workers=1)
    looks.finish()
    rows = {f"codeberg:o/r{n}": {"id": f"codeberg:o/r{n}", "source": "codeberg"}
            for n in range(4)}
    rows["github:o/g"] = {"id": "github:o/g", "source": "github"}
    looks._rows = rows
    looks._cands = {"j1": [(0.9 - n / 100, rid) for n, rid in enumerate(rows)]}
    got = [looks._next()["id"] for _ in range(3)]
    assert got == ["codeberg:o/r0", "codeberg:o/r1", "github:o/g"]
    assert looks._next() is None                   # Codeberg is busy with two looks
    looks._done(rows["codeberg:o/r0"])
    assert looks._next()["id"] == "codeberg:o/r2"
    assert looks.looked_at() == {"codeberg:o/r0", "codeberg:o/r1", "codeberg:o/r2",
                                 "github:o/g"}
