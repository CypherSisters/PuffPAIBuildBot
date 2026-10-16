"""Truth tables for the build foundation: classify, produces, route, the
interpret word table and validator, build.json, the yes digest.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "build"))

from fakes import api_skill, app, book, cli_skill, guide, live, mcp  # noqa: E402

from puffpai.build import classify, decide, interpret, schema as S, setup, yes  # noqa: E402


@pytest.fixture
def state(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


# ── classify ───────────────────────────────────────────────────────────────

ALLOWED_BASH = ("---\nname: x\ndescription: d\nallowed-tools: Bash(sox *)\n---\n# x\n")
MCP_SKILL = ("---\nname: m\ndescription: d\n---\nConnect to https://tools.example.org/mcp "
             "for the calls.\n")
REMOTE = ("---\nname: r\ndescription: d\ncompatibility: No local command runtime is declared."
          "\n---\nCalls go to https://api.agentpmt.com/v1/tools with your account.\n")


@pytest.mark.parametrize("cand,want", [
    (app("a", "A", "x"), S.APP),
    (app("a", "A", "x", kind="server_app"), S.APP),
    ({"ref": "i", "name": "I", "type": "app", "mode": "static_web"}, S.APP),
    ({"ref": "i", "name": "I", "type": "x", "mode": "web_build"}, S.APP),
    ({"ref": "r", "name": "R", "type": "repo", "entry_html": "index.html", "start": "npm start"},
     S.APP),
    (mcp("m", "M", "x", "https://h.example/mcp", []), S.TOOL),
    ({"ref": "m", "name": "M", "type": "mcp", "mcp": {"command": "npx -y srv"}}, S.TOOL),
    (cli_skill("s", "S", "x", ["ffmpeg"]), S.TOOL),
    ({"ref": "s", "name": "S", "type": "skill", "skill_md": ALLOWED_BASH,
      "files": ["scripts/a.sh"]}, S.TOOL),
    ({"ref": "s", "name": "S", "type": "skill", "skill_md": MCP_SKILL, "files": []}, S.TOOL),
    ({"ref": "s", "name": "S", "type": "skill", "skill_md": REMOTE, "files": []}, S.DATA),
    (api_skill("s", "S", "x", "apis.tianapi.com"), S.DATA),
    (guide("g", "G", "Only words."), S.GUIDE),
    ({"ref": "s", "name": "S", "type": "skill", "skill_md": "---\nname: s\n---\nRun "
      "scripts/x.py", "files": ["scripts/x.py"]}, S.GUIDE),     # scripts, nothing declared
    ({"ref": "c", "name": "C", "type": "cli"}, S.TOOL),
    ({"ref": "c", "name": "C", "type": "x", "mode": "cli_tool"}, S.TOOL),
    ({"ref": "o", "name": "O", "type": "x", "operations": ["GET /a"]}, S.TOOL),
    (live("l", "L", "x", "h.example"), S.DATA),
    ({"ref": "f", "name": "F", "type": "feed"}, S.DATA),
    ({"ref": "d", "name": "D", "type": "data"}, S.DATA),
    ({"ref": "d", "name": "D", "type": "library"}, S.DATA),
    ({"ref": "d", "name": "D", "type": "x", "mode": "picture_set"}, S.DATA),
    (book("b", "B", "x"), S.TEXT),
    ({"ref": "z", "name": "Z", "type": "zim"}, S.TEXT),
    ({"ref": "builtin:routine", "name": "R", "type": "routine", "builtin": "routine"}, S.TOOL),
    ({"ref": "u", "name": "U", "type": "something-new"}, S.GUIDE),
])
def test_classify_truth_table(cand, want):
    assert classify.classify(dict(cand)) == want


def test_classify_reads_hosts_but_never_home_hosts():
    c = guide("g", "G", "See https://github.com/x/y and https://clawhub.ai/z")
    assert classify.classify(c) == S.GUIDE


# ── produces ───────────────────────────────────────────────────────────────

TOOL_PIC = mcp("t", "T", "x", "https://h.example/mcp",
               [{"name": "draw", "description": "Return an image"}])


@pytest.mark.parametrize("cls,output,action,cand,judged,want", [
    (S.APP, "action", "do", None, "", True),
    (S.APP, "pictures", "show", None, "", True),
    (S.TOOL, "pictures", "show", TOOL_PIC, "", True),
    (S.TOOL, "places", "show", TOOL_PIC, "", False),
    (S.TOOL, "places", "show", TOOL_PIC, "yes", True),
    (S.DATA, "pictures", "show", None, "", True),
    (S.DATA, "numbers_over_time", "show", None, "", True),
    (S.DATA, "action", "do", None, "", False),
    (S.DATA, "action", "remind", None, "", False),
    (S.TEXT, "text", "learn", None, "", True),
    (S.TEXT, "text", "ask", None, "", True),
    (S.TEXT, "text", "show", None, "", False),
    (S.TEXT, "pictures", "learn", None, "", False),
    (S.GUIDE, "text", "ask", None, "", True),
    (S.GUIDE, "action", "do", None, "", False),
    (S.TOOL, "action", "remind", {"builtin": "routine"}, "", True),
    (S.TOOL, "text", "show", {"builtin": "routine"}, "", False),
])
def test_produces_truth_table(cls, output, action, cand, judged, want):
    assert decide.produces(cls, output, action, cand, judged) is want


# ── route ──────────────────────────────────────────────────────────────────

def _c(ref, cls):
    return {"ref": ref, "name": ref, "class": cls}


def _job(action="show", output="text"):
    return S.new_job("A job", action=action, output=output, subject=["x"])


@pytest.mark.parametrize("fit,about,action,want_route,want_pick", [
    ([_c("app", S.APP), _c("tool", S.TOOL), _c("data", S.DATA)], [], "show", "A", "app"),
    ([_c("tool", S.TOOL), _c("data", S.DATA)], [], "show", "B", "tool"),
    ([_c("data", S.DATA)], [_c("book", S.TEXT)], "show", "C", "data"),
    ([], [_c("book", S.TEXT)], "show", "D", ""),          # a book never sets a route...
    ([_c("book", S.TEXT)], [], "learn", "B", "book"),      # ...unless learn or ask
    ([_c("guide", S.GUIDE), _c("data", S.DATA)], [], "ask", "B", "guide"),
    ([_c("book", S.TEXT), _c("guide", S.GUIDE)], [], "ask", "B", "book"),
    ([_c("data", S.DATA), _c("book", S.TEXT)], [], "show", "C", "data"),
    ([], [], "show", "D", ""),
])
def test_route_truth_table(fit, about, action, want_route, want_pick):
    j = _job(action)
    decide.route_job(j, [(0.6, c) for c in fit], [(0.6, c) for c in about])
    assert j["route"] == want_route
    assert j["pick"]["ref"] == want_pick
    if want_route == "D":
        assert j["ingredients"] == [] and j["reason"] == "Nothing found yet"
    else:
        assert j["ingredients"][0] == want_pick
        for c in about:
            assert c["ref"] in j["ingredients"]


def test_route_views():
    for output, view in S.VIEW.items():
        j = _job("show", output)
        decide.route_job(j, [(0.6, _c("d", S.DATA))], [])
        if output == "action":
            continue
        assert j["view"] == view
    j = _job("do", "action")
    decide.route_job(j, [(0.6, _c("a", S.APP))], [])
    assert j["view"] == S.OWN_SCREEN


def test_fits_thresholds_per_class():
    assert decide.passes_sim(S.DATA, 0.41, True, "meaning")
    assert not decide.passes_sim(S.APP, 0.41, True, "meaning")
    assert not decide.passes_sim(S.TEXT, 0.49, True, "meaning")
    assert not decide.passes_sim(S.APP, 0.50, False, "meaning")   # no shared word, not strong
    assert decide.passes_sim(S.APP, 0.56, False, "meaning")
    assert decide.passes_sim(S.APP, 0.0, True, "words")


# ── interpret ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,action,output,needs", [
    ("Show Earth photos", "show", "pictures", ["none"]),
    ("Remind me to water every day", "remind", "action", ["time"]),
    ("Find venues near me", "show", "places", ["place"]),
    ("Track spending over time", "do", "numbers_over_time", ["none"]),
    ("Learn music theory", "learn", "text", ["none"]),
    ("Play radio stations", "listen", "sound", ["none"]),
    ("Keep the guest list", "do", "action", ["none"]),
    ("Link my bank account", "show", "text", ["account_key"]),
])
def test_word_table(name, action, output, needs):
    j = interpret.from_words(name)
    assert (j["action"], j["output"], j["needs"]) == (action, output, needs)
    assert not S.job_problems(j)


def test_validator_names_each_problem():
    bad = {"name": "x", "action": "dance", "output": "smell", "needs": ["none", "place"],
           "subject": ["Two Words"]}
    got = " ".join(S.job_problems(bad))
    for word in ("name", "action", "output", "needs", "subject"):
        assert word in got


def test_interpret_without_a_brain_splits_the_goal():
    jobs, how = interpret.interpret("show me space pictures, remind me about launches",
                                    use_brain=False)
    assert how == "words" and len(jobs) == 2
    assert jobs[0]["output"] == "pictures" and jobs[1]["action"] == "remind"
    assert len({j["id"] for j in jobs}) == 2


def test_interpret_repairs_one_bad_field_from_the_table():
    import json

    def brain(messages, fmt):
        return json.dumps({"jobs": [{"name": "Show Earth photos", "does": "Photos.",
                                     "action": "gaze", "output": "pictures", "needs": ["none"],
                                     "subject": ["earth"], "ask": "Show me Earth"}]})
    jobs, how = interpret.interpret("earth", brain=brain)
    assert how == "brain+words"
    assert jobs[0]["action"] == "show" and jobs[0]["output"] == "pictures"


# ── build.json, setup, yes ─────────────────────────────────────────────────

def test_build_json_round_trip_and_contract_words(state):
    assert S.load_build()["schema"] == "build/v1" and not S.exists()
    b = S.empty_build("goal")
    b["jobs"] = [S.new_job("Show photos")]
    S.save_build(b)
    assert S.exists() and S.load_build()["jobs"][0]["page"] == "job-show-photos"
    assert set(S.WORDS["route"]) == set(S.ROUTES)
    assert set(S.WORDS["status"]) == set(S.STATUSES)
    assert S.WORDS["route"]["C"] == "Puts together a <view>"
    assert S.how_words({"route": "C", "view": "gallery"}) == "Puts together a picture gallery"
    for text in [*S.WORDS["status"].values(), *S.WORDS["route"].values()]:
        assert "door" not in text.lower() and "—" not in text and "beta" not in text.lower()


def test_job_ids_are_unique():
    assert S.job_id("Show photos", ["show-photos"]) == "show-photos-2"


def test_setup_rows_merge_by_id_and_status(monkeypatch):
    monkeypatch.setattr(setup, "_home_set", lambda: False)
    a = S.new_job("See the sky", needs=["place"], subject=["sky"])
    b = S.new_job("Find venues", needs=["place"], subject=["venue"])
    for j in (a, b):
        j["route"], j["ingredients"] = "C", ["seed:x"]
    parts = {"seed:x": {"ref": "seed:x", "name": "X", "class": S.DATA, "hosts": ["x.example"],
                        "needs": []}}
    # A data source that never asks where she lives needs no town row, even
    # for a job read as "near me".
    assert not [r for r in setup.rows_for([a, b], parts) if r["kind"] == "setting"]
    parts["seed:x"]["needs"] = ["home_location"]
    rows = setup.rows_for([a, b], parts)
    # A website from BuildBot's own list also names the switch for the one
    # look BuildBot takes before adding it, so the one Yes covers it too.
    assert sorted(r["kind"] for r in rows) == ["setting", "switch", "switch"]
    assert {r.get("service") for r in rows if r["kind"] == "switch"} == {"seed:x", "sample_look"}
    assert all(r["job_ids"] == [a["id"], b["id"]] for r in rows)
    assert setup.job_status(a, rows) == S.NEEDS_SETUP
    rows[[r["kind"] for r in rows].index("setting")]["done"] = True
    assert setup.job_status(a, rows) == S.WAITING_YES


def test_yes_digest_follows_the_list():
    b = S.empty_build()
    j = S.new_job("Show photos")
    j.update(route="C", pick={"ref": "x", "class": S.DATA, "score": 0.5, "why": ""},
             ingredients=["x"])
    b["jobs"] = [j]
    first = yes.sha(b)
    b["setup"] = [{"id": "switch-x", "kind": "switch", "label": "Turn on X", "job_ids": [j["id"]],
                   "done": False}]
    second = yes.sha(b)
    assert first != second
    b["setup"][0]["done"] = True
    assert yes.sha(b) == second, "completing a row is not a new list"
    b["yes"] = {"given": True, "sha": second, "at": "t", "by": "owner"}
    assert yes.covers(b)
    j["ingredients"].append("y")
    assert not yes.covers(b)


# ── what can run on this computer (one build foundation) ───────

@pytest.mark.parametrize("cand, says", [
    ({"ref": "github:x/rec", "type": "server_app", "source": "github",
      "what": "A simple audio recorder app for Android phones"}, "phone"),
    ({"ref": "github:x/fret", "type": "browser_app", "source": "github",
      "what": "A guitar chord tutor for google actions"}, "assistant"),
    ({"ref": "npm:x", "type": "library", "source": "npm",
      "what": "Parse and format star charts in your code"}, "code library"),
    ({"ref": "mcp:x", "type": "mcp", "source": "mcp-registry", "mcp": {"command": "stdio"},
      "what": "Upcoming rocket launches as tools"}, "cannot start"),
    ({"ref": "pypi:dialogue", "type": "data", "source": "pypi", "what": ""}, "too little"),
    ({"ref": "github:x/stars", "type": "cli", "source": "github",
      "what": "Command line star map printer for terminals"}, "command line"),
])
def test_not_usable_here(cand, says):
    from puffpai.build import classify
    assert says in classify.not_usable(cand)


def test_usable_here():
    from puffpai.build import classify
    assert classify.not_usable({"ref": "seed:x", "type": "live_data", "source": "api_seeds",
                                "what": "Daily photos of Earth taken from space."}) == ""
    assert classify.not_usable(dict(installed=1, ref="x", type="skill", source="installed")) == ""
