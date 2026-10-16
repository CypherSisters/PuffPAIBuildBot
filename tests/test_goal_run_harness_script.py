"""scripts/measure_goals.py with fakes: dry runs, the keyless refusal, the
discovery bar, the capture and the integration walk. No network."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def harness():
    spec = importlib.util.spec_from_file_location("measure_goals",
                                                  ROOT / "scripts" / "measure_goals.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["measure_goals"] = mod
    spec.loader.exec_module(mod)
    return mod


GOALS = {"goals": [
    {"id": "g1", "text": "I want a metronome bot. I'll need to be able to keep time and "
                         "tune my guitar.", "personal": ["Theo"],
     "gold": ["metronome", "tuner"]},
    {"id": "g2", "text": "I want a garden bot for my husband Jake. Remind me to water.",
     "personal": ["husband", "Jake"], "gold": ["watering reminders"]}]}


def _needs_doc(goal: str, jobs: list[tuple[str, list[tuple[str, str]]]]) -> dict:
    from puffpai.wizard import needs
    return {"schema": "needs/v1", "prompt": "rules", "model": "",
            "goal_digest": needs.goal_digest(goal), "kind": "metronome",
            "kind_source": "rules", "subject": {"phrase": "metronome", "notes": [],
                                                "sendable": False},
            "jobs": [{"id": f"j{n}", "name": name, "does": "", "source": "asked", "seen_in": [],
                      "ticked": True, "learn": False,
                      "parts": [{"kind": "code-app",
                                 "search": [{"phrase": p, "state": s, "why": []}
                                            for p, s in phrases]}]}
                     for n, (name, phrases) in enumerate(jobs, 1)],
            "held": [], "private_left_out": [], "helper": "ready",
            "timings": {"rules_ms": 1.0, "first_job_s": None, "model_s": None}}


@pytest.fixture
def fakes(monkeypatch, harness, tmp_path):
    from puffpai.wizard import find_parts, needs
    seen: list = []

    def run(goal, *, cfg, on_line=None, on_job=None, deadline_s=60.0):
        seen.append(("needs", cfg))
        if on_line:
            on_line("Reading what you wrote ...")
        return _needs_doc(goal, [("Keep time", [("metronome keep time", "out")]),
                                 ("Tune my guitar", [("guitar tuner", "out"),
                                                     ("jake guitar", "held")])])

    def plan_items(doc, *, goal, online, allow_send, cfg, on_line=None):
        seen.append(("plan", online, allow_send))
        item = {"id": "apps_code:github:a/metro", "section": "apps_code", "label": "Metro",
                "detail": "A metronome.", "default_on": True, "need": "j1",
                "apply": {"kind": "code_project", "ref": "github:a/metro"}}
        item2 = dict(item, id="apps_code:github:a/tuner", default_on=False, need="j2",
                     apply={"kind": "code_project", "ref": "github:a/tuner"})
        return {"items": [item, item2], "notes": {}, "not_reached": [], "similar": [],
                "sends": [], "left_out": 3}
    monkeypatch.setattr(needs, "run", run)
    monkeypatch.setattr(find_parts, "plan_items", plan_items)
    monkeypatch.setattr(find_parts, "found_for", lambda i: {
        "trust": {"pretick_ok": True}, "meaning": {"fit": 0.6}})
    monkeypatch.setattr(find_parts, "last_evidence", lambda: {"finds": [], "jobs": []})
    goals = tmp_path / "goals.json"
    goals.write_text(json.dumps(GOALS), encoding="utf-8")
    for name in harness.TOKEN_ENV:
        monkeypatch.delenv(name, raising=False)
    return {"seen": seen, "goals": str(goals), "out": str(tmp_path / "out")}


def test_a_dry_run_on_two_goals_writes_both_reports(harness, fakes):
    said: list[str] = []
    assert harness.main(["--dry", "--goals", fakes["goals"], "--out", fakes["out"]],
                        sleep=lambda s: None, say=said.append) == 0
    data = json.loads((Path(fakes["out"]) / "report.json").read_text(encoding="utf-8"))
    assert [g["id"] for g in data["goals"]] == ["g1", "g2"]
    assert all(("plan", False, False) in fakes["seen"] for _ in data["goals"])
    md = (Path(fakes["out"]) / "report.md").read_text(encoding="utf-8")
    assert "## g1" in md and "Plan lines:" in md and "Metro" in md
    assert data["header"]["mode"] == "dry"


def test_keyless_refuses_with_a_token_set(harness, fakes, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_not_real")
    said: list[str] = []
    with pytest.raises(harness.Refused):
        harness.main(["--live", "--keyless", "--goals", fakes["goals"], "--out", fakes["out"]],
                     sleep=lambda s: None, say=said.append)
    assert said and "GITHUB_TOKEN is set" in said[0]
    assert fakes["seen"] == []           # nothing ran, nothing was sent


def test_discovery_is_keyless_and_writes_the_discovery_files(harness, fakes):
    pauses: list[float] = []
    harness.main(["--discovery", "--goals", fakes["goals"], "--out", fakes["out"],
                  "--run", "t1", "--pace", "65"], sleep=pauses.append, say=lambda s: None)
    data = json.loads((Path(fakes["out"]) / "F_discovery_t1.json").read_text(encoding="utf-8"))
    assert (Path(fakes["out"]) / "F_discovery_t1.md").exists()
    assert pauses == [65.0]              # paced between goals, not before the first
    bar = data["goals"][0]["bar"]
    assert bar["pass"] is True and bar["finds"] == 2 and bar["preticked"]
    assert data["header"]["keyless"] is True and data["header"]["bar_passed"] == 2


def test_the_bar_fails_with_one_find_or_a_slow_search(harness):
    base = {"plan": {"items": [{"id": "apps_code:x", "apply": {"kind": "code_project"},
                                "default_on": False}]},
            "timings": {"search_s": 12.0}, "personal_leaks": []}
    bar = harness.bar_for(base)
    assert bar["checks"]["two_finds"] is False and bar["pass"] is False
    slow = dict(base, timings={"search_s": 95.0})
    assert harness.bar_for(slow)["checks"]["within_90s"] is False


def test_buildbots_own_lines_are_not_counted_as_finds(harness):
    items = [{"id": "pictures:buildbot:palette-j1", "apply": {"kind": "folder_palette"}},
             {"id": "pictures:commons:x", "apply": {"kind": "picture_set"}},
             {"id": "knowledge:kiwix:y", "apply": {"kind": "knowledge.add"}}]
    assert [i["id"] for i in harness.addable(items)] == ["pictures:commons:x"]


def test_personal_words_in_a_sent_phrase_or_request_are_reported(harness):
    doc = _needs_doc("x", [("Tune", [("jake guitar", "out"), ("guitar tuner", "out")])])
    leaks = harness.personal_leaks({"personal": ["Jake"]}, doc,
                                   [{"url": "https://api.github.com/search?q=jake+tuner"}])
    assert len(leaks) == 2


def test_the_capture_records_names_of_headers_not_values(harness, monkeypatch):
    from puffpai import source_fetch
    monkeypatch.setattr(source_fetch, "_default_transport", lambda url, headers, timeout: "ok")
    cap = harness.Capture().install()
    try:
        assert source_fetch._default_transport("https://api.github.com/x",
                                               {"Accept": "a", "If-None-Match": "secret"}, 5) == "ok"
    finally:
        cap.uninstall()
    rows = cap.take()
    assert rows[0]["url"] == "https://api.github.com/x"
    assert rows[0]["headers"] == ["accept", "if-none-match"]
    assert "secret" not in json.dumps(rows)


def test_integrate_ticks_the_best_line_of_each_job_and_follows_the_build(harness):
    calls: list = []
    summary = {"sections": [{"items": [
        {"id": "apps_code:a", "need": "j1", "on": False, "apply": {"kind": "code_project"}},
        {"id": "apps_code:b", "need": "j1", "on": True, "apply": {"kind": "code_project"}},
        {"id": "knowledge:c", "need": "j1", "on": True, "apply": {"kind": "knowledge.add"}},
        {"id": "apps_code:d", "need": "j2", "on": False, "apply": {"kind": "data_import"}}]}]}
    answers = {"/wizard/needs": {"done": True, "phase": "parts"}, "/wizard/summary": summary,
               "/wizard/build": {"running": False, "status": "done", "lines": ["Added."]}}

    class FakeHttp:
        def call(self, method, path, body=None, timeout=600):
            calls.append((method, path, body))
            return 200, answers.get(path, {})
    out = harness.integrate({"text": "a goal"}, FakeHttp(), name="R3Bot", sleep=lambda s: None)
    approve = next(c for c in calls if c[1] == "/wizard/approve")
    assert approve[2] == {"changes": {"on": {"apps_code:b": True, "apps_code:d": True}}}
    assert out["build"]["status"] == "done"


def test_a_closer_look_at_a_find_is_not_a_leak_of_her_words(harness):
    doc = _needs_doc("x", [("Plan", [("wedding plan", "out")])])
    rows = [{"url": "https://raw.githubusercontent.com/someone/Lets-Get-Married/abc/README.md"},
            {"url": "https://github.com/someone/Lets-Get-Married.git/info/refs?service=git"}]
    assert harness.personal_leaks({"personal": ["married"]}, doc, rows) == []


def _find(fid, *, fit, mode="unknown", lic="MIT", integrable=False, floor=True, judge=None,
          why=None, part="code-app"):
    return {"id": fid, "source": "github", "part": part, "mode": mode, "integrable": integrable,
            "trust": {"floor_ok": floor, "reasons": [] if floor else ["few_stars"]},
            "facts": {"license_reported": lic},
            "meaning": {"fit": fit, "judge": judge, "why_dropped": why, "state": "dropped"}}


def test_a_failed_goal_is_proven_only_when_every_fitting_licensed_find_was_read(harness):
    r = {"plan": {"items": [{"apply": {"ref": "github:a/on"}}]},
         "evidence": {"finds": [
             _find("github:a/on", fit=0.7, mode="static_web", integrable=True),
             _find("github:a/nolic", fit=0.7, lic=None),
             _find("github:a/server", fit=0.6, mode="server_app"),
             _find("github:a/judge", fit=0.6, mode="static_web", integrable=True, judge="no"),
             _find("github:a/far", fit=0.3),
             _find("github:a/book", fit=0.9, part="knowledge")]}}
    proof = harness.failure_proof(r)
    assert proof["proven"] is True and proof["verdict"].startswith("nothing integrable")
    assert proof["counts"] == {"on the plan": 1, "no open licence": 1,
                               "cannot be added in round 3 (server_app)": 1,
                               "the brain says it does not do this job": 1}
    r["evidence"]["finds"].append(_find("github:a/unread", fit=0.55))
    proof = harness.failure_proof(r)
    assert proof["proven"] is False and "not proven: 1" in proof["verdict"]


def test_a_moderate_fit_need_not_be_preticked_but_a_strong_one_must(harness, monkeypatch):
    from puffpai.wizard import find_parts
    recs = {"a": {"trust": {"pretick_ok": True}, "meaning": {"fit": 0.43}},
            "b": {"trust": {"pretick_ok": True}, "meaning": {"fit": 0.62, "judge": "no"}}}
    monkeypatch.setattr(find_parts, "found_for", lambda i: recs.get(i))
    items = [{"id": k, "apply": {"kind": "code_project"}, "default_on": False} for k in "ac"]
    base = {"plan": {"items": items}, "timings": {"search_s": 50.0}, "personal_leaks": []}
    assert harness.bar_for(base)["checks"]["pretick_where_trust_allows"] is True
    assert harness.bar_for(base)["one_preticked"] is False       # what she sees: no tick
    items[1]["id"] = "b"          # strong, trust allows, the brain said no: still a fail
    assert harness.bar_for(base)["checks"]["pretick_where_trust_allows"] is False
    assert harness.bar_for(base)["pretick"] == "missed"


def test_a_pretick_check_that_was_never_tried_says_so(harness, monkeypatch):
    # With no strong find trust allows to pre-tick, the
    # check passes without being tried; the report must not show a plain "yes".
    from puffpai.wizard import find_parts
    recs = {"a": {"trust": {"pretick_ok": False, "path": "static_small",
                            "reasons": ["new, few stars"]}, "meaning": {"fit": 0.68}},
            "b": {"trust": {"pretick_ok": False, "path": "", "reasons": ["new, few stars"]},
                  "meaning": {"fit": 0.62}},
            "c": {"trust": {"pretick_ok": True, "path": "stars"}, "meaning": {"fit": 0.3}}}
    monkeypatch.setattr(find_parts, "found_for", lambda i: recs.get(i))
    items = [{"id": k, "apply": {"kind": "code_project"}, "default_on": False} for k in "abc"]
    base = {"id": "g", "plan": {"items": items}, "timings": {"search_s": 50.0},
            "personal_leaks": []}
    bar = harness.bar_for(base)
    assert bar["pass"] is True and bar["pretick"] == "not_tried"
    assert [h["id"] for h in bar["held_by_trust"]] == ["a", "b"]
    words = harness.pretick_words(bar)
    assert "not tried" in words and "2 strong finds kept unticked" in words
    assert "small app with few users (shown, never pre-ticked) x1" in words
    assert "trust: new, few stars x1" in words and "pretick_where_trust_allows=yes" not in words
    items[2]["default_on"] = True
    assert harness.bar_for(base)["pretick"] == "ticked"


def test_a_find_blocked_by_the_trust_floor_is_named_as_such(harness):
    r = {"plan": {"items": []}, "evidence": {"finds": [
        _find("github:a/new", fit=0.6, mode="web_build", integrable=True, floor=False)]}}
    proof = harness.failure_proof(r)
    assert proof["proven"] is True and proof["trust_blocked"] == 1
    assert "trust floor" in proof["verdict"]


def test_a_goal_with_no_pretick_says_which_rule_kept_each_tick_off(harness, monkeypatch):
    from puffpai.wizard import find_parts
    recs = {"mine": {"meaning": {"job": "j1", "fit": 0.6, "judge": "no"}, "what_it_is": "x"},
            "theirs": {"meaning": {"job": "j2", "fit": 0.58, "judge": "yes"},
                       "what_it_is": "A recipe manager."}}
    monkeypatch.setattr(find_parts, "found_for", lambda i: recs.get(i))
    r = {"needs": {"jobs": [{"id": "j1", "name": "Recipes and costing", "source": "asked"},
                            {"id": "j2", "name": "Recipe manager", "source": "usual"}]},
         "plan": {"items": [{"id": "mine"}, {"id": "theirs"}]}, "evidence": {"finds": []},
         "bar": {"checks": {"pretick_where_trust_allows": False},
                 "pretickable": ["mine", "theirs"]}}
    recs["late"] = {"meaning": {"job": "j1", "fit": 0.6, "judge": None}, "what_it_is": "y"}
    r["bar"]["pretickable"].append("late")
    rows = {row["id"]: row["why"] for row in harness.failure_proof(r)["pretick"]}
    assert rows["mine"] == "the brain said it does not do this job"
    assert rows["late"].startswith("the brain had no time to check it")
    assert "a job BuildBot added" in rows["theirs"] and "Recipe manager" in rows["theirs"]
