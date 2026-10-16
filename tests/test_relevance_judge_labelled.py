"""Relevance: tool, integrable, fit, with the real reading helper.

Labelled set v2 = ``relevance_judge_labels.json`` (124 items, 4 goals, hand labels and the
recorded qwen2.5:7b judge answers) + ``relevance_wedding_finds.json`` (46 recorded
wedding finds with the architect's kind/fit labels and 2 synthetic negatives).
Skipped with the reason when the reading helper is not on this computer.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from puffpai.catalogs import meaning

FIX = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    meaning.helper_state() != "ready",
    reason="BuildBot's reading helper (all-MiniLM-L6-v2) is not on this computer")


def _jobs(gid: str) -> tuple[str, list[dict]]:
    rec = json.loads((FIX / "needs_model_raw" / f"{gid}.json").read_text(encoding="utf-8"))["parsed"]
    return rec["kind"], [{"id": f"j{n}", "name": j["name"], "does": j["does"],
                          "parts": j["parts"], "ticked": True, "learn": False, "source": "usual"}
                         for n, j in enumerate(rec["jobs"], 1)]


def _v2() -> list[dict]:
    return json.loads((FIX / "relevance_wedding_finds.json").read_text(encoding="utf-8"))["items"]


@pytest.fixture(scope="module")
def wedding_scored() -> dict[str, dict]:
    kind, jobs = _jobs("wedding")
    return {f["id"]: f for f in meaning.score(_v2(), jobs, kind=kind, stage=2)}


def test_anchor_small_static_planner_might_help(wedding_scored):
    f = wedding_scored["github:SubchiBeats/wedding-planner"]
    assert f["trust"]["path"] == "static_small"
    assert f["meaning"]["fit"] == pytest.approx(0.535, abs=0.03)
    assert f["meaning"]["state"] == "might_help"


def test_anchor_server_app_is_a_note(wedding_scored):
    f = wedding_scored["github:Cirx08/WeddingShare"]
    assert f["meaning"]["fit"] == pytest.approx(0.57, abs=0.04)
    assert f["meaning"]["state"] == "note"
    assert meaning.note_reason(f) == meaning.NOTE_SERVER


def test_anchor_seating_planner_might_help(wedding_scored):
    f = wedding_scored["github:gabriel1ll7/Seating-Planner"]
    assert f["meaning"]["fit"] == pytest.approx(0.435, abs=0.03)
    assert f["meaning"]["state"] == "might_help"


def test_every_flagged_page_is_dropped(wedding_scored):
    from puffpai.catalogs.page_check import looks_like_page
    flagged = [f for f in wedding_scored.values()
               if looks_like_page(f["name"], f["what_it_is"], (f.get("repo") or {}).get("topics"))]
    assert len(flagged) >= 16
    for f in flagged:
        assert f["meaning"]["state"] == "dropped" and f["meaning"]["why_dropped"] == "page"


def test_no_page_is_ticked_or_might_help(wedding_scored):
    for f in wedding_scored.values():
        if f["label"]["kind"] == "page":
            assert f["meaning"]["state"] not in ("ticked", "might_help"), f["id"]


def test_no_fit_labelled_integrable_tool_is_dropped(wedding_scored):
    fit_tools = [f for f in wedding_scored.values()
                 if f["label"]["kind"] == "tool" and f["label"]["fit"] == "FIT"]
    dropped = sorted(f["id"] for f in fit_tools if f["meaning"]["state"] == "dropped")
    integrable_dropped = [i for i in dropped if wedding_scored[i]["integrable"]]
    # Honest record: two FIT-labelled server apps (not addable yet) score under the
    # 0.35 blurb floor with the pinned rule and are dropped; they are reported, not hidden.
    print("FIT tools dropped:", dropped)
    assert integrable_dropped == []
    assert set(dropped) <= {"github:web-codings123/EazyWed", "github:pinry/pinry"}


def test_the_synthetic_article_and_showcase_are_never_ticked(wedding_scored):
    article = wedding_scored["knowledge:wikipedia:wedding-planner"]
    showcase = wedding_scored["github:Prajktac210/wedding_planner"]
    assert article["meaning"]["state"] == "dropped"
    assert showcase["meaning"]["state"] == "dropped"


def test_nothing_is_ticked_for_the_wedding_without_five_stars(wedding_scored):
    for f in wedding_scored.values():
        if f["meaning"]["state"] == "ticked":
            assert f["trust"]["pretick_ok"], f["id"]


def _nasa_rows() -> list[dict]:
    seeds = {r["id"]: r for r in json.loads(
        (ROOT / "config" / "catalog_seeds" / "apis.json").read_text(encoding="utf-8"))["apis"]}
    # NASA's image feeds are picture sources: scored against jobs with a pictures part.
    return [{"schema": "found/v1", "id": f"seeds:{sid}", "source": "seeds", "part": "pictures",
             "name": seeds[sid]["name"], "what_it_is": seeds[sid]["what_it_is"],
             "topics": seeds[sid].get("topics") or [], "mode_guess": "other", "integrable": True,
             "license": "public domain",
             "trust": {"floor_ok": True, "path": "listed", "pretick_ok": True, "reasons": []}}
            for sid in ("nasa_images", "nasa_epic")]


@pytest.mark.parametrize("gid", ["wedding", "garden", "fitness", "bakery"])
def test_nasa_is_dropped_for_goals_that_are_not_about_space(gid):
    kind, jobs = _jobs(gid)
    for f in meaning.score(_nasa_rows(), jobs, kind=kind, stage=2):
        assert f["meaning"]["state"] == "dropped", (gid, f["id"], f["meaning"])


def test_nasa_is_kept_for_the_space_goal():
    kind, jobs = _jobs("space")
    states = [f["meaning"]["state"] for f in meaning.score(_nasa_rows(), jobs, kind=kind, stage=2)]
    assert all(s != "dropped" for s in states), states


def test_knowledge_rows_only_meet_learn_jobs():
    kind, jobs = _jobs("spanish")
    row = {"schema": "found/v1", "id": "kiwix:spanish_grammar", "source": "kiwix",
           "part": "knowledge", "name": "Spanish grammar",
           "what_it_is": "A guide to Spanish grammar with exercises.", "integrable": False,
           "trust": {"floor_ok": True, "path": "", "pretick_ok": False, "reasons": []}}
    no_learn = meaning.score([row], jobs, kind=kind, stage=2)[0]
    assert no_learn["meaning"]["why_dropped"] == "not for these jobs"
    for job in jobs:
        job["learn"] = True
    learn = meaning.score([row], jobs, kind=kind, stage=2)[0]
    assert learn["meaning"]["why_dropped"] != "not for these jobs"
    assert learn["meaning"]["state"] != "ticked"


def test_tick_precision_on_labelled_set_v2_with_the_pinned_thresholds():
    """(ticked and FIT and tool) / ticked >= 0.95; v1 rows have no README, so fit = blurb - 0.05."""
    rows: list[tuple[float, bool, bool]] = []
    v1 = json.loads((FIX / "relevance_judge_labels.json").read_text(encoding="utf-8"))
    for g in v1["goals"].values():
        blurbs = (meaning.embed([i["text"] for i in g["items"]])
                  @ meaning.embed(g["jobs"]).T).max(axis=1)
        for item, b in zip(g["items"], blurbs):
            rows.append((float(b) - meaning.NO_LINES_PENALTY, item["judge"] != "no",
                         item["fit"] == "FIT"))
    kind, jobs = _jobs("wedding")
    for f in meaning.score(_v2(), jobs, kind=kind, stage=2):
        if f["meaning"]["fit"] is not None and meaning.is_integrable(f) \
                and meaning.has_licence(f) and f["trust"]["pretick_ok"]:
            rows.append((f["meaning"]["fit"], True,
                         f["label"]["fit"] == "FIT" and f["label"]["kind"] == "tool"))
    ticked = [r for r in rows if r[0] >= meaning.TICK_FIT and r[1]]
    good = sum(1 for r in rows if r[2])
    tp = sum(1 for r in ticked if r[2])
    precision = tp / len(ticked) if ticked else 1.0
    print(f"tick threshold {meaning.TICK_FIT}: ticked {len(ticked)}, precision {precision:.3f}, "
          f"recall {tp / good:.3f} ({tp} of {good})")
    assert len(rows) >= 124 and ticked
    assert precision >= 0.95


def test_stage_one_without_readme_lines_uses_the_blurb_less_a_little(wedding_scored):
    kind, jobs = _jobs("wedding")
    # Without a whole-plan job, the find is filed under the job it fits best
    # (with one, a whole wedding planner is filed there).
    jobs = [j for j in jobs if not meaning.WHOLE_RE.search(str(j.get("name") or ""))]
    f = next(r for r in _v2() if r["id"] == "github:SubchiBeats/wedding-planner")
    s1 = meaning.score([f], jobs, kind=kind, stage=1)[0]["meaning"]
    assert s1["does"] is None
    assert s1["fit"] == pytest.approx(s1["blurb"] - 0.05, abs=0.002)


def test_feature_lines_drop_commands_and_markdown():
    md = ("# App\n\n- **Guest list** with RSVP tracking\n- Run `npm install` first\n"
          "1. Budget per category and totals\n- [Seating](http://x) chart builder\n- ok\n")
    assert meaning.feature_lines(md) == ["Guest list with RSVP tracking",
                                         "Budget per category and totals",
                                         "Seating chart builder"]


def test_vectors_are_cached_on_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path))
    meaning.embed(["a wedding budget tracker"])
    files = list((tmp_path / "cache" / "meaning").glob("*.f16"))
    assert len(files) == 1 and files[0].stat().st_size == 384 * 2


def test_words_only_without_the_helper(monkeypatch):
    monkeypatch.setattr(meaning, "helper_state", lambda: "missing")
    kind, jobs = _jobs("wedding")
    listed = {"schema": "found/v1", "id": "github:a/budget", "source": "awesome_selfhosted",
              "part": "code-app", "name": "wedding budget planner",
              "what_it_is": "A wedding budget planner for couples.", "integrable": True,
              "mode_guess": "static_web", "license": "MIT",
              "trust": {"floor_ok": True, "path": "listed", "pretick_ok": True, "reasons": []}}
    starred = dict(listed, id="github:b/budget",
                   trust={"floor_ok": True, "path": "stars", "pretick_ok": True, "reasons": []})
    out = meaning.score([listed, starred], jobs, kind=kind, stage=2)
    assert out[0]["meaning"]["state"] == "ticked"
    assert out[1]["meaning"]["state"] == "might_help"
    assert out[0]["meaning"]["fit"] is None


def test_plan_choices_are_logged_without_goal_text(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path))
    meaning.log_choice({"id": "apps_code:github:a/b", "need": "j2"}, False,
                       {"meaning": {"blurb": 0.5, "does": 0.6, "fit": 0.55, "state": "ticked"},
                        "trust": {"path": "stars"}})
    rows = [json.loads(x) for x in
            (tmp_path / "logs" / "plan_choices.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows == [{"item_id": "apps_code:github:a/b", "need": "j2", "blurb": 0.5, "does": 0.6,
                     "fit": 0.55, "trust_path": "stars", "state_offered": "ticked",
                     "owner_on": False}]


# -- finds whose source names no licence are still looked at ---------
def _code(source, host, lic, **repo):
    return {"id": f"{source}:o/r", "source": source, "part": "code-app", "name": "r",
            "mode_guess": "unknown",
            "repo": dict({"host": host, "license_reported": lic, "license_read": None,
                          "stars": 1, "language": "JavaScript"}, **repo)}


def test_a_codeberg_find_with_no_licence_named_is_worth_a_closer_look():
    # Codeberg's search names no licence for most projects (LibreWeddingPlanner
    # too); without this, none of them is ever read.
    assert meaning.closer_look_tier(_code("codeberg", "codeberg.org", None)) == 1
    assert meaning.closer_look_tier(_code("github", "github.com", None)) == 2
    assert meaning.closer_look_tier(_code("github", "github.com", "MIT")) == 0


def test_a_note_about_a_find_never_read_says_so():
    unread = _code("github", "github.com", "MIT")
    assert meaning.note_reason(unread) == meaning.NOTE_NOT_READ
    assert meaning.note_reason(_code("codeberg", "codeberg.org", None)) == meaning.NOTE_NOT_READ
    assert meaning.note_reason(_code("github", "github.com", None)) == meaning.NOTE_NO_LICENCE
    read = dict(unread, mode_guess="server_app")
    assert meaning.note_reason(read) == meaning.NOTE_SERVER


def test_a_find_is_preticked_only_under_a_job_she_listed(monkeypatch):
    # "a terminal notes and reminder app" was pre-ticked on the
    # wedding plan under the brain's own "Reminder system" job.
    import numpy as np
    jobs = [{"id": "j1", "name": "Plan the budget", "source": "asked", "parts": []},
            {"id": "j2", "name": "Reminder system", "source": "usual", "parts": []}]
    app = {"id": "github:o/r", "source": "github", "part": "code-app", "name": "r",
           "what_it_is": "An app.", "mode_guess": "static_web", "integrable": True,
           "repo": {"stars": 50, "license_reported": "MIT"},
           "trust": {"floor_ok": True, "path": "stars", "pretick_ok": True, "reasons": []}}
    monkeypatch.setattr(meaning, "helper_state", lambda: "ready")

    def fake(blurbs):
        monkeypatch.setattr(meaning, "embed", lambda texts: np.eye(2, meaning.DIM,
                                                                   dtype=np.float32))
        monkeypatch.setattr(meaning, "_candidate_vectors",
                            lambda rows: np.array([[blurbs[0], blurbs[1]] + [0.0] *
                                                   (meaning.DIM - 2)], dtype=np.float32))
    fake((0.30, 0.80))                 # fits only the brain's job: shown, not ticked
    got = meaning.score([app], jobs, kind="wedding planner", stage=1)[0]["meaning"]
    assert got["state"] == "might_help" and got["own_job"] == "j1"
    fake((0.62, 0.80))                 # fits her job strongly too: ticked under hers
    got = meaning.score([app], jobs, kind="wedding planner", stage=1)[0]["meaning"]
    assert got["state"] == "ticked" and got["job"] == "j1"
    fake((0.58, 0.62))                 # strongly enough, though better the brain's: hers
    got = meaning.score([app], jobs, kind="wedding planner", stage=1)[0]["meaning"]
    assert got["state"] == "ticked" and got["job"] == "j1"
    # A trusted app that fits the brain's job strongly
    # and one of hers moderately (at least a "might help" fit) is ticked, shown
    # under the job it really fits (the brain is asked about that one).
    # (Stage 1 has no README lines: each fit is its blurb less 0.05.)
    fake((0.53, 0.57))
    got = meaning.score([app], jobs, kind="wedding planner", stage=1)[0]["meaning"]
    assert got["state"] == "ticked" and got["job"] == "j2"
    for brain_fit in (0.56, 0.60, 0.90):
        fake((0.47, brain_fit))
        got = meaning.score([app], jobs, kind="wedding planner", stage=1)[0]["meaning"]
        assert got["state"] == "ticked" and got["job"] == "j2", brain_fit
        assert got["own_job"] == "j1"
    # Under a "might help" fit on every job of hers it is never ticked, however
    # strongly it fits the brain's job (a notes app pre-ticked
    # under the brain's "Reminder system" on a wedding plan).
    for brain_fit in (0.56, 0.60, 0.90):
        fake((0.44, brain_fit))
        got = meaning.score([app], jobs, kind="wedding planner", stage=1)[0]["meaning"]
        assert got["state"] == "might_help", brain_fit
    for brain_fit in (0.30, 0.52, 0.60, 0.90):
        fake((0.58, brain_fit))
        got = meaning.score([app], jobs, kind="wedding planner", stage=1)[0]["meaning"]
        assert got["state"] == "ticked" and got["job"] == "j1", brain_fit
    fake((0.45, 0.48))                 # strong on no job: shown, not ticked
    got = meaning.score([app], jobs, kind="wedding planner", stage=1)[0]["meaning"]
    assert got["state"] == "might_help"
    fake((0.40, 0.57))                 # strong on the brain's, too weak on hers
    got = meaning.score([app], jobs, kind="wedding planner", stage=1)[0]["meaning"]
    assert got["state"] == "might_help"
    for j in jobs:
        j["source"] = "usual"          # she listed nothing: the best job may tick
    got = meaning.score([app], jobs, kind="wedding planner", stage=1)[0]["meaning"]
    assert got["state"] == "ticked" and got["job"] == "j2"


def test_a_tick_is_judged_on_the_job_it_is_ticked_under():
    # The same find got "yes" in one run and "no" in
    # the next because it was judged on its best job, a job the brain added
    # that changed between runs. A tick is judged on the job it is shown under.
    jobs = [{"id": "j1", "name": "Play practice games"}, {"id": "j2", "name": "Offer analysis"}]
    rows = [{"id": "github:o/tick", "source": "github", "part": "code-app", "name": "tick",
             "what_it_is": "x", "meaning": {"job": "j1", "best_job": "j2", "state": "ticked",
                                            "fit": 0.6}},
            {"id": "github:o/might", "source": "github", "part": "code-app", "name": "might",
             "what_it_is": "x", "meaning": {"job": "j1", "best_job": "j2",
                                            "state": "might_help", "fit": 0.45}}]
    seen: list = []

    def ask(messages, **kw):
        seen.append(messages[1]["content"])
        return '{"answers": {"1": "yes"}}'
    meaning.judge(rows, jobs, ask)
    assert '"Play practice games"' in seen[0] and '"Offer analysis"' in seen[1]


def test_the_closer_looks_do_not_depend_on_the_order_things_were_found():
    import random
    rows = [{"id": f"github:o/r{n}", "source": "github", "part": "code-app", "name": f"r{n}",
             "repo": {"stars": 10, "license_reported": "MIT", "language": "JavaScript"},
             "meaning": {"job": f"j{n % 3 + 1}", "blurb": 0.5 if n % 2 else 0.6,
                         "state": "might_help"}} for n in range(30)]
    want = [f["id"] for f in meaning.for_details(rows, per_job=4, cap=10)]
    for seed in range(5):
        shuffled = rows[:]
        random.Random(seed).shuffle(shuffled)
        assert [f["id"] for f in meaning.for_details(shuffled, per_job=4, cap=10)] == want


def test_the_judge_asks_nothing_after_its_stop_time():
    import time as _t
    asked: list = []
    rows = [{"id": f"github:o/r{n}", "source": "github", "part": "code-app", "name": f"r{n}",
             "meaning": {"job": "j1", "state": "ticked", "fit": 0.6}} for n in range(3)]
    meaning.judge(rows, [{"id": "j1", "name": "Plan"}],
                  lambda m, **kw: asked.append(1) or '{"answers": {"1": "no"}}',
                  stop_at=_t.monotonic() - 1.0)
    assert asked == [] and all(r["meaning"]["state"] == "ticked" for r in rows)


def _line(fid, state, fit, *, part="code-app", source="github"):
    return {"id": fid, "source": source, "part": part, "name": fid, "what_it_is": "x",
            "meaning": {"job": "j1", "state": state, "fit": fit, "judge": None}}


def test_would_be_ticks_may_be_asked_about_until_their_own_stop_time():
    import time as _t
    asked: list = []
    rows = [{"id": "github:o/tick", "source": "github", "part": "code-app", "name": "tick",
             "meaning": {"job": "j1", "state": "ticked", "fit": 0.6}},
            {"id": "github:o/might", "source": "github", "part": "code-app", "name": "might",
             "meaning": {"job": "j1", "state": "might_help", "fit": 0.45}}]
    meaning.judge(rows, [{"id": "j1", "name": "Plan"}],
                  lambda m, **kw: asked.append(m[1]["content"]) or '{"answers": {"1": "yes"}}',
                  stop_at=_t.monotonic() - 1.0, ticks_stop_at=_t.monotonic() + 60)
    assert len(asked) == 1 and '"tick"' in asked[0]
    assert rows[0]["meaning"]["judge"] == "yes" and rows[1]["meaning"].get("judge") is None


def test_the_judge_asks_about_code_lines_before_books_and_pictures():
    rows = [_line("book", "might_help", 0.7, part="knowledge", source="gutenberg"),
            _line("low", "might_help", 0.42), _line("tick", "ticked", 0.6),
            _line("high", "might_help", 0.48)]
    assert [f["id"] for f in meaning._judge_order(rows)] == ["tick", "high", "low", "book"]


def test_the_judge_reads_the_job_alone():
    # With the kind
    # of bot added, the brain said "no" to fitting recipe managers and trackers.
    seen: list = []
    rows = [_line("gardener", "might_help", 0.44)]
    meaning.judge(rows, [{"id": "j1", "name": "Remind me when to water"}],
                  lambda m, **kw: seen.append(m[1]["content"]) or '{"answers": {"1": "no"}}',
                  kind="garden helper")
    # Her job without the people in it: "me" dropped.
    assert '"Remind when to water"' in seen[0] and "garden helper bot" not in seen[0]
    assert rows[0]["meaning"]["state"] == "dropped"


def test_with_a_judge_a_moderate_code_line_it_never_checked_is_left_out():
    rows = [_line("unasked", "might_help", 0.45), _line("strong", "might_help", 0.55),
            _line("yes", "might_help", 0.45), _line("book", "might_help", 0.45,
                                                     part="knowledge", source="gutenberg")]
    rows[2]["meaning"]["judge"] = "yes"
    meaning.settle([dict(r, meaning=dict(r["meaning"])) for r in rows])      # no judge: kept
    got = meaning.settle(rows, judged=True)
    states = {f["id"]: f["meaning"]["state"] for f in got}
    assert states == {"unasked": "dropped", "strong": "might_help", "yes": "might_help",
                      "book": "might_help"}
    assert got[0]["meaning"]["why_dropped"] == meaning.NOT_CHECKED


def test_with_a_judge_a_code_tick_it_never_checked_is_shown_unticked():
    # The brain had half a second after the second
    # look, and a tick it never checked stayed on in one run and not another.
    rows = [_line("unchecked", "ticked", 0.6), _line("yes", "ticked", 0.6),
            _line("unsure", "ticked", 0.6), _line("card", "ticked", 0.6,
                                                    part="live-info", source="seeds")]
    rows[1]["meaning"]["judge"] = "yes"
    rows[2]["meaning"]["judge"] = "unsure"
    alone = meaning.settle([dict(r, meaning=dict(r["meaning"])) for r in rows])
    assert all(f["meaning"]["state"] == "ticked" for f in alone)     # no judge: as before
    got = {f["id"]: f["meaning"]["state"] for f in meaning.settle(rows, judged=True)}
    assert got == {"unchecked": "might_help", "yes": "ticked", "unsure": "ticked",
                   "card": "ticked"}


def test_apps_for_other_programs_are_notes():
    for what in ("Dynamic Firefox theme that uses the photo of the day.",
                 "A scheduled GitHub action that looks for reminders in issues.",
                 "Web-based GUI for Kubernetes cluster installations.",
                 "D&D 5e SRD MCP server, monster search and spell lookup."):
        assert meaning.runs_elsewhere({"name": "x", "what_it_is": what}), what
    assert not meaning.runs_elsewhere({"name": "x", "what_it_is": "A dice roller for the web."})


def test_a_web_app_that_also_names_a_phone_is_still_a_web_app():
    assert not meaning.runs_elsewhere({"name": "x", "what_it_is":
                                       "A PWA metronome that works on Android and iOS."})
    assert not meaning.runs_elsewhere({"name": "x", "what_it_is":
                                       "Budget planner for the web, tested in Chrome."})
    assert meaning.runs_elsewhere({"name": "x", "what_it_is":
                                   "A reminder application for the macOS menubar."})


def test_a_find_with_no_description_is_never_preticked():
    # Final discovery run: "Garden-AI/garden" (no description, a science
    # platform) was pre-ticked for a garden helper on its name alone.
    app = {"id": "github:o/garden", "source": "github", "part": "code-app", "name": "garden",
           "what_it_is": "", "mode_guess": "static_web", "integrable": True,
           "repo": {"stars": 41, "license_reported": "MIT"},
           "trust": {"floor_ok": True, "path": "stars", "pretick_ok": True, "reasons": []},
           "meaning": {}}
    assert meaning._classify_why(app, 0.6)[0] == "might_help"
    assert meaning._classify_why(dict(app, what_it_is="A garden planner."), 0.6)[0] == "ticked"


def test_more_words_for_other_programs():
    for what in ("Semantic chess intelligence MCP for Claude.",
                 "Simple Coding Time Tracker in Visual Studio Code.",
                 "A very simple tuner app built using Electron."):
        assert meaning.runs_elsewhere({"name": "x", "what_it_is": what}), what


# -- closer looks go where they can end in a line -------
def test_a_find_no_look_can_make_trusted_is_not_looked_at():
    import datetime
    old = (datetime.date.today() - datetime.timedelta(days=900)).isoformat()
    new = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    hopeless = _code("github", "github.com", "MIT", stars=2, pushed_at=old)
    assert meaning.trust_hopeless(hopeless)
    # Real use, a recent change (a small page may still pass) or unknown facts: not hopeless.
    assert not meaning.trust_hopeless(_code("github", "github.com", "MIT", stars=5,
                                            pushed_at=old))
    assert not meaning.trust_hopeless(_code("github", "github.com", "MIT", stars=2,
                                            pushed_at=new))
    assert not meaning.trust_hopeless(_code("github", "github.com", "MIT", stars=2))
    assert not meaning.trust_hopeless(_code("github", "github.com", "MIT", stars=2,
                                            pushed_at=old, in_awesome_selfhosted=True))
    # It is left out with its trust reason, never a "did not get to look" note.
    assert meaning.note_reason(hopeless) != meaning.NOTE_NOT_READ
    trust = {"floor_ok": False, "path": "", "pretick_ok": False,
             "reasons": ["few_stars", "not_listed", "not_small_static"]}
    state, why = meaning._classify_why(dict(hopeless, trust=trust), 0.7)
    assert state == "dropped" and why.startswith("trust")


def test_closer_looks_skip_weak_first_looks_and_hopeless_finds():
    import datetime
    old = (datetime.date.today() - datetime.timedelta(days=900)).isoformat()

    def row(n, blurb, **repo):
        f = _code("github", "github.com", "MIT", **dict({"stars": 10}, **repo))
        f["id"] = f"github:o/r{n}"
        f["meaning"] = {"job": "j1", "blurb": blurb, "why_dropped": None}
        return f
    picked = meaning.for_details([row(1, 0.62), row(2, meaning.LOOK_MIN_BLURB - 0.01),
                                  row(3, 0.55, stars=1, pushed_at=old), row(4, 0.41)])
    assert [f["id"] for f in picked] == ["github:o/r1", "github:o/r4"]
