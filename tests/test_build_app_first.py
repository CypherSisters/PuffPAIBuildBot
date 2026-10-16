"""App first: "most of these tasks have entire apps or
plugins already available with their own UIs ... use the apps".

* every job is first looked up in the app list (awesome-selfhosted, the
  copy shipped in config/catalog_snapshots, refreshed weekly);
* an app that can start on this computer (Node.js, Python; Docker compose
  only with Docker) is an APP; one that runs only with Docker on a computer
  without it stays an option with a "needs Docker" row;
* decide: a fitting app (meaning plus subject, no brain judge for listed
  apps) sets route A; only without one B, C, D;
* check: a code find is read at its current commit and downloaded by the
  integrate path; a project that cannot start here as an app is left out
  so the next app is tried;
* prove for A is light: running on 127.0.0.1 and its page answers.

Nothing here goes online.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import gzip
import http.server
import json
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "build"))

from fakes import ScriptedBrain, concept_embed, job, live  # noqa: E402

from puffpai.build import check as check_mod  # noqa: E402
from puffpai.build import classify, decide, prove as prove_mod, setup as setup_mod  # noqa: E402
from puffpai.build import find as find_mod  # noqa: E402
from puffpai.build import schema as S  # noqa: E402
from puffpai.finding import app_catalog, sites  # noqa: E402


def _row(slug: str) -> dict:
    rid = "awesome_selfhosted:" + slug
    row = next((r for r in app_catalog.rows() if r["id"] == rid), None)
    if row is None:
        pytest.skip(f"{slug} is not in this build's app list")
    return row


def listed(slug: str, **kw) -> dict:
    """A candidate exactly as the build makes it from the app list."""
    row = _row(slug)
    f = sites.make(site="awesome-selfhosted", type_="server_app",
                   id=f"awesome-selfhosted:{slug}", name=row["name"],
                   what=row["description"], url=row.get("source_code_url") or "",
                   popularity={"stars": row.get("stars")}, raw={"row_id": row["id"]})
    f["platforms"], f["tags"] = list(row["platforms"]), list(row["tags"])
    return {**find_mod.from_find(f), **kw}


def gh_app(ref: str, name: str, what: str, **kw) -> dict:
    return {"ref": ref, "name": name, "type": "browser_app", "what": what,
            "source": "github", "hosts": [], "needs": [], **kw}


@pytest.fixture
def docker(monkeypatch):
    state = {"here": True}
    monkeypatch.setattr(classify, "docker_here", lambda: state["here"])
    return state


# ── classify: can it start on this computer? ───────────────────────────────

def test_run_hint_reads_the_lists_platforms(docker):
    assert classify.run_hint(listed("mealie")) == classify.NATIVE          # Python
    assert classify.run_hint(listed("fork-recipes")) == classify.DOCKER    # Docker only
    assert classify.run_hint(listed("cookcli")) == classify.NO_START       # Rust only
    assert classify.run_hint({"language": "TypeScript"}) == classify.NATIVE
    assert classify.run_hint({}) == ""                                     # the download decides
    for slug in ("mealie", "fork-recipes", "recipesage"):
        assert classify.classify(listed(slug)) == S.APP


def test_an_app_that_cannot_start_here_is_not_used_with_a_plain_why(docker):
    # 2026-10-13: a project on GitHub is tried by Toolsmith; the program it
    # needs (Rust) is asked for on the setup list, never a refusal.
    assert classify.not_usable(listed("cookcli")) == ""
    why = classify.not_usable(dict(listed("cookcli"), url="https://example.org/cookcli"))
    assert why == "It runs on Rust, which BuildBot cannot start on this computer yet."
    assert classify.not_usable(listed("mealie")) == ""
    # A listed server app that also has a phone app is still a server app.
    assert classify.not_usable(listed("myfin-budget")) == ""


def test_docker_only_app_without_docker_stays_an_option_with_a_setup_row(docker):
    docker["here"] = False
    app = listed("fork-recipes")
    assert classify.needs_docker(app) and not classify.not_usable(app)
    j = dict(job("Keep my recipes", "Save recipes and plan meals.", "do", "action",
                 ["none"], ["recipe"]), id="recipes")
    got = decide.decide([j], [app], embed=concept_embed)
    assert got["jobs"][0]["route"] == S.A
    app["class"] = S.APP
    rows = setup_mod.rows_for(got["jobs"], {app["ref"]: app})
    row = next(r for r in rows if r["id"] == "program-needs-docker")
    from puffpai import deps
    if deps.can_install("docker"):
        # Docker is installed after the one Yes where BuildBot can.
        assert row["label"] == "Install Docker Desktop, to run Fork Recipes"
        assert not row.get("manual") and row["install"] == "docker"
    else:
        assert row["label"] == "Docker, to run Fork Recipes" and row["manual"]
        assert "install Docker Desktop yourself" in row["detail"]
    assert not setup_mod.her_part(row), "an information row never blocks the Yes"
    docker["here"] = True
    assert classify.needs_docker(app) is False


# ── decide: a fitting app sets route A ─────────────────────────────────────

RECIPES = dict(job("Keep my recipes", "Save recipes and plan meals for the week.", "do",
                   "text", ["none"], ["recipe", "meal"]), id="recipes")


def test_a_fitting_app_wins_over_a_closer_data_source(docker):
    data = live("seed:recipes", "Recipe list", "Keep recipes, plan meals, make dishes.",
                "recipes.example.org")
    app = listed("mealie", text="Mealie. Keep recipes and meals, with design themes.")
    got = decide.decide([dict(RECIPES)], [data, app], embed=concept_embed)
    sims = got["sims"]["recipes"]
    assert sims[data["ref"]] >= sims[app["ref"]], "the data source is the closer fit"
    j = got["jobs"][0]
    assert j["route"] == S.A and j["pick"]["ref"] == app["ref"]
    assert j["view"] == S.OWN_SCREEN


def test_a_code_site_app_not_found_by_apps_py_is_judged(docker):
    toy = gh_app("github:x/recipe-toy", "Recipe toy", "Keep recipes and plan meals, a toy "
                 "page made in a weekend.", judge_no=["*"])
    brain = ScriptedBrain(cands=[toy])
    got = decide.decide([dict(RECIPES)], [toy], brain=brain, embed=concept_embed)
    assert got["jobs"][0]["route"] != S.A
    assert "judge" in brain.calls


def _direct(ref: str, what: str, rank: int, jid: str = "recipes") -> dict:
    return gh_app(ref, ref.split("/")[-1], what, direct=True, for_jobs=[jid],
                  job_rank={jid: rank})


def test_decide_keeps_apps_py_rank_order_for_its_apps(docker):
    # 2026-10-13: apps.py ranks once; decide never re-scores, judges or drops
    # them. Pick = its first, alternates = the rest, in that order, even
    # when meaning would put another first and the brain says no to all.
    far = _direct("github:a/far", "A spreadsheet for anything.", 0)
    near = _direct("github:b/near", "Keep recipes and plan meals for the week.", 1)
    mid = _direct("github:c/mid", "Recipes.", 2)
    brain = ScriptedBrain(cands=[far, near, mid])
    for c in (far, near, mid):
        c["judge_no"] = ["*"]
    got = decide.decide([dict(RECIPES)], [mid, near, far], brain=brain, embed=concept_embed)
    j = got["jobs"][0]
    assert j["route"] == S.A and j["pick"]["ref"] == far["ref"]
    assert j["alternates"] == [near["ref"], mid["ref"]]
    assert not got["dropped"]


def test_a_job_with_no_app_but_code_is_made_from_the_code(docker):
    code = [{"ref": "code:x/recipe-lib", "full_name": "x/recipe-lib", "name": "recipe-lib",
             "url": "https://github.com/x/recipe-lib", "what": "A recipe parser library."}]
    got = decide.decide([dict(RECIPES, code=code)], [], embed=concept_embed)
    j = got["jobs"][0]
    assert j["route"] == S.A and j["pick"]["ref"] == "code:x/recipe-lib"
    assert j["view"] == S.OWN_SCREEN and j["alternates"] == []
    # without code it is still nothing found
    assert decide.decide([dict(RECIPES)], [], embed=concept_embed)["jobs"][0]["route"] == S.D


def test_reminders_stay_the_bots_own_routines_even_with_an_app(docker):
    remind = dict(job("Remind me to water", "Remind me every day to water.", "remind",
                      "action", ["time"], ["water"]), id="water")
    app = listed("hortusfox", text="HortusFox. Water reminders for every plant.")
    got = decide.decide([remind], find_mod.find([remind], catalog=lambda j, g: [app]),
                        embed=concept_embed)
    assert got["jobs"][0]["route"] == S.B
    assert got["jobs"][0]["pick"]["ref"] == "builtin:routine"


# ── the matrix: 8 wildcard goals over the real app list ────────────────────

MATRIX = {
    # goal: (job, a word the picked app's own words must have, or None when
    # the list has no app for it)
    "recipes": (job("Keep my recipes", "Save recipes and plan meals for the week.", "do",
                    "action", ["none"], ["recipe", "meal"]), "recipe"),
    "guitar": (job("Learn guitar chords", "Show chords and help me practice songs.", "learn",
                   "text", ["none"], ["guitar", "chord"]), None),
    "houseplants": (job("Keep track of my houseplants", "Track each plant, its care and "
                        "when it needs water.", "do", "action", ["none"], ["plant", "water"]),
                    "plant"),
    "soccer": (job("Run my soccer team", "Keep the roster, the games and the scores.", "do",
                   "action", ["none"], ["soccer", "team"]), None),
    "road trip": (job("Plan a road trip", "Plan the route, stops and places to stay.", "do",
                      "places", ["none"], ["trip", "route"]),
                  "trip"),
    "biology": (job("Study biology", "Make flashcards and quiz me on biology.", "learn",
                    "text", ["none"], ["biology", "flashcard"]), None),
    "home budget": (job("Track the home budget", "Track spending against a monthly budget.",
                        "do", "numbers_over_time", ["none"], ["budget", "spending"]),
                    "budget"),
    "wedding": (job("Plan the wedding", "Keep the guest list, RSVPs and the to-do list.",
                    "do", "action", ["none"], ["wedding", "guest"]), None),
}


def _real_embed():
    from puffpai.knowledge import reading_helper
    if not reading_helper.is_ready():
        pytest.skip("the reading helper is not on this computer")
    return decide.default_embed()


@pytest.mark.parametrize("goal", list(MATRIX))
def test_matrix_route_a_when_the_app_list_has_a_fitting_app(goal, docker):
    embed = _real_embed()
    jb, category = MATRIX[goal]
    j = dict(jb, id="j1")
    finds = sites.awesome_selfhosted(" ".join(j["subject"]), 6)
    assert finds, "the app list is asked first for every job"
    assert all(f["platforms"] is not None for f in finds)
    # A data source about the job too: an app that fits still wins over it.
    data = live("seed:decoy", f"{j['name']} data", f"{j['does']} {' '.join(j['subject'])}",
                "data.example.org")
    cands = [find_mod.from_find(f) for f in finds] + [data]
    got = decide.decide([j], cands, brain=None, embed=embed)
    out = got["jobs"][0]
    by = {c["ref"]: c for c in cands}
    if category is None:
        assert out["route"] != S.A, (goal, out["pick"])
        assert not any(by[r].get("listed") for r in out["ingredients"])
        return
    assert out["route"] == S.A, (goal, got["sims"])
    pick = by[out["pick"]["ref"]]
    assert pick.get("listed") and category in pick["text"].lower(), (goal, pick)
    assert out["view"] == S.OWN_SCREEN and out["ingredients"][0] == pick["ref"]


# ── find: the app list comes first and says how each app runs ──────────────

def test_the_app_list_is_the_first_site_and_keeps_platforms():
    from puffpai.finding import find as finding_find
    assert finding_find.ALL_SITES[0] == "awesome-selfhosted"
    finds = sites.awesome_selfhosted("recipe", 8)
    assert finds and all(f["type"] == "server_app" for f in finds)
    assert all(isinstance(f["platforms"], list) for f in finds)
    for f in finds:
        row = _row(f["id"].split(":", 1)[1])
        assert not set(row["tags"]) <= {"Self-hosting Solutions"}
        assert not row.get("nonfree") and not row.get("archived")
    c = find_mod.from_find(finds[0])
    assert c["listed"] is True and c["platforms"] == finds[0]["platforms"]
    assert c["text"].startswith(c["name"])


def test_the_weekly_fresh_copy_is_used_once_fetched(monkeypatch):
    calls: list = []
    monkeypatch.setattr(app_catalog, "_switch_on", lambda: True)

    def get_archive(url, *, allowed_hosts, max_bytes, dest_path, service, max_redirects,
                    expect_sha256="unset"):
        assert expect_sha256 is None, "the weekly list has no fingerprint, on purpose"
        calls.append((url, allowed_hosts, service))
        with open(dest_path, "wb") as fh:
            fh.write(b"archive")
        return {"path": dest_path}

    def build(tar, out, *, generated):
        snap = {"schema": app_catalog.SCHEMA, "generated": generated, "rows": [
            {"id": "awesome_selfhosted:pantry", "name": "Pantry", "description": "Food.",
             "tags": ["Recipe Management"], "platforms": ["Python"]}]}
        with gzip.open(out, "wt", encoding="utf-8") as fh:
            json.dump(snap, fh)

    assert app_catalog.due()
    assert app_catalog.refresh(get_archive=get_archive, build=build) == {"ok": True, "why": ""}
    assert calls == [(app_catalog.ARCHIVE_URL, {"codeload.github.com"}, "code_download")]
    assert [r["name"] for r in app_catalog.rows()] == ["Pantry"]
    assert not app_catalog.due()
    assert not os.path.exists(os.path.join(app_catalog.folder(), "download.tar.gz"))


def test_no_fetch_while_code_downloads_are_switched_off(monkeypatch):
    monkeypatch.setattr(app_catalog, "_switch_on", lambda: False)
    assert app_catalog.refresh() == {"ok": False, "why": "switched_off"}
    assert app_catalog.refresh_soon() is False
    assert len(app_catalog.rows()) > 1000, "the copy shipped with the build"


# ── check: read, download, and can it start as an app? ─────────────────────

def _card(how: str, missing=(), why_not: str = "") -> dict:
    return {"status": "checked", "integration_id": "app-abcdefgh",
            "card": {"code_sha": "c" * 64, "host_sha": "h" * 64, "mode": "other",
                     "verdict": "allow", "lines": ["Checked."],
                     "host": {"how": how, "why_not": why_not, "missing": list(missing)}}}


def test_check_reads_a_listed_app_and_keeps_its_run_plan(monkeypatch, docker):
    asked: list = []
    monkeypatch.setattr(find_mod, "code_item", lambda c: "find:0123456789abcdef")
    app = dict(listed("fork-recipes"), **{"class": S.APP})
    got = check_mod.check(app, prepare=lambda item, ctx: asked.append(item["id"])
                          or _card("compose", ["docker"]))
    assert asked == ["find:0123456789abcdef"]
    assert got["ok"] and got["prepared"]["how"] == "compose"
    assert got["prepared"]["needs"] == ["docker"]


@pytest.mark.parametrize("how,why_not,expect", [
    ("", "BuildBot could not tell how this project starts",
     "BuildBot cannot start it on this computer: BuildBot could not tell how this project "
     "starts"),
    ("python_cli", "", "It has no screen of its own to open."),
    ("library", "", "It has no screen of its own to open."),
])
def test_a_project_that_cannot_start_as_an_app_is_left_out(monkeypatch, docker, how,
                                                            why_not, expect):
    monkeypatch.setattr(find_mod, "code_item", lambda c: "find:0123456789abcdef")
    monkeypatch.setattr(check_mod, "_forget", lambda iid: None)
    got = check_mod.check(dict(listed("mealie"), **{"class": S.APP}),
                          prepare=lambda item, ctx: _card(how, why_not=why_not))
    assert got["ok"] is False and got["reason"] == expect


def test_a_code_find_that_cannot_be_read_is_a_plain_no(monkeypatch, docker):
    monkeypatch.setattr(find_mod, "code_item", lambda c: "")
    got = check_mod.check(dict(listed("mealie"), **{"class": S.APP}),
                          prepare=lambda item, ctx: pytest.fail("nothing downloaded"))
    assert got == {"ok": False, "reason": "BuildBot could not read that project's current "
                                          "version"}


def test_the_pipeline_keeps_app_list_projects_in_order_for_toolsmith(monkeypatch, docker):
    """2026-10-13: an app from the app list with its code on GitHub is not
    downloaded and checked while planning: Toolsmith downloads it after the
    Yes (Drawbridge checks it then) and the next one follows when one cannot
    start."""
    from puffpai.build import pipeline
    first, second = listed("mealie"), listed("recipesage")
    tried: list = []

    def check(c):
        tried.append(c["ref"])
        return {"ok": False, "reason": "It has no screen of its own to open."}
    first["text"] = "Mealie. Recipes, meals and a cookbook for every recipe."
    second["text"] = "RecipeSage. Recipes and meals."
    build = pipeline.assemble("recipes", [dict(RECIPES)], [first, second],
                              {"brain": None, "embed": concept_embed, "check": check})
    j = build["jobs"][0]
    assert tried == []
    assert j["route"] == S.A and j["pick"]["ref"] == first["ref"]
    assert j["alternates"] == [second["ref"]]
    row = next(r for r in build["setup"] if r.get("make") == setup_mod.MAKE_APP)
    assert row["label"] == "Download Mealie and start it on this computer"


class _Page(http.server.BaseHTTPRequestHandler):
    body = b"<!doctype html><html><body>Recipes</body></html>"
    ctype = "text/html; charset=utf-8"

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", self.ctype)
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *a):
        pass


def _serve(handler) -> tuple[str, http.server.HTTPServer]:
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}/", srv


def test_the_apps_page_answers():
    url, srv = _serve(_Page)
    try:
        assert prove_mod.page_answers(url, wait_s=0) == ""
    finally:
        srv.shutdown()


def test_an_empty_script_shell_counts_only_once_its_scripts_show_content(monkeypatch):
    class Shell(_Page):
        body = b'<!doctype html><html><body><div id="root"></div>' \
               b'<script type="module" src="/main.js"></script></body></html>'
    url, srv = _serve(Shell)
    try:
        monkeypatch.setattr(prove_mod, "_render", lambda u: '<html><body><div id="root">'
                                                            '</div></body></html>')
        assert prove_mod.page_answers(url, wait_s=0) == \
            "its page stayed empty (it showed no content of its own)"
        monkeypatch.setattr(prove_mod, "_render", lambda u: '<html><body><h1>My workouts'
                                                            '</h1><button>Add</button></body>'
                                                            '</html>')
        assert prove_mod.page_answers(url, wait_s=0) == ""
    finally:
        srv.shutdown()


def test_a_page_with_no_content_and_no_scripts_is_not_yet():
    class Empty(_Page):
        body = b"<!doctype html><html><body>  </body></html>"
    url, srv = _serve(Empty)
    try:
        assert "stayed empty" in prove_mod.page_answers(url, wait_s=0)
    finally:
        srv.shutdown()


def test_a_page_that_is_not_a_page_is_not_yet():
    class Json(_Page):
        body, ctype = b'{"ok": true}', "application/json"
    url, srv = _serve(Json)
    try:
        assert prove_mod.page_answers(url, wait_s=0) == \
            "its page answered without a page (HTTP 200)"
    finally:
        srv.shutdown()


def test_only_this_computer_is_asked():
    assert prove_mod.page_answers("http://example.org/", wait_s=0) == \
        "its page is not on this computer"


def test_route_a_needs_no_sample_ask(monkeypatch):
    made = {"ok": True, "screen": {"kind": "app", "integration_id": "rs-abcdefgh",
                                   "url": "http://127.0.0.1:5999/"},
            "made": {"integration_id": "rs-abcdefgh", "servers": ["rs"]}}
    got = prove_mod.prove({"id": "recipes", "route": "A"}, {}, made=made, hooks={
        "status": lambda i: {"can_run": True, "state": "running",
                             "url": "http://127.0.0.1:5999/"},
        "page": lambda url: "",
        "ask": lambda *a: pytest.fail("no sample ask for an app used as it is")})
    assert got["state"] == "working"


# ── downloads come with the one Yes ────────────────────────────────────────

def test_before_the_yes_a_found_app_is_checked_after_it(monkeypatch, docker):
    monkeypatch.setattr(check_mod, "downloads_now", lambda: False)
    monkeypatch.setattr(find_mod, "code_item", lambda c: pytest.fail("nothing read yet"))
    app = dict(listed("mealie"), **{"class": S.APP})
    got = check_mod.check(app)
    assert got == {"ok": True, "reason": check_mod.APP_LATER, "later": True}
    # make's own re-check (no download) keeps it for the download after the Yes
    app["check"] = {"ok": True, "reason": got["reason"], "later": True}
    assert check_mod.check(app, fetch=False)["ok"] is True
    assert check_mod.check(dict(app, check={"ok": True}), fetch=False)["ok"] is False


def _later_build(docker_state):
    from puffpai.build import pipeline
    first = listed("mealie", text="Mealie. Keep recipes and plan meals.")
    second = listed("recipesage", text="RecipeSage. Keep recipes and plan meals.")
    third = listed("tandoor-recipes", text="Tandoor. Keep recipes and plan meals.")
    later = lambda c: {"ok": True, "reason": check_mod.APP_LATER, "later": True}  # noqa: E731
    return pipeline.assemble("recipes", [dict(RECIPES)], [first, second, third],
                             {"brain": None, "embed": concept_embed, "check": later}), \
        (first, second, third)


def test_the_list_names_the_download_its_backups_and_the_switch(docker):
    build, (first, second, third) = _later_build(docker)
    j = build["jobs"][0]
    assert j["route"] == S.A and len(j["alternates"]) == 2  # three apps fit: the pick and two
    assert set(j["alternates"]) <= set(build["parts"]), "backups are kept as parts"
    assert not {d["ref"] for d in build["dropped"]} & set(j["alternates"])
    row = next(r for r in build["setup"] if r.get("make") == setup_mod.MAKE_APP)
    pick = build["parts"][j["pick"]["ref"]]["name"]
    assert row["label"] == f"Download {pick} and start it on this computer"
    assert row["refs"] == [j["pick"]["ref"]] + j["alternates"]
    assert "If it cannot start here, BuildBot tries" in row["detail"]
    switch = next(r for r in build["setup"] if r["id"] == "switch-code-download")
    assert switch["label"] == "Turn on app downloads" and switch["service"] == "code_download"


def test_after_the_yes_toolsmith_tries_the_pick_then_the_next_apps(docker, monkeypatch):
    """2026-10-13: every app whose code is on GitHub goes to Toolsmith, the
    pick then its backups in order (never the old install path)."""
    from puffpai.build import make as make_mod
    from puffpai.build import toolsmith_run
    build, _apps = _later_build(docker)
    j = build["jobs"][0]
    build["yes"] = {"given": True}
    seen = {}

    def run_job(job, cands, progress=None, approved=(), **_kw):
        seen["refs"] = [c["ref"] for c in cands]
        return {"ok": True, "app": {"app_id": "recipesage-abc123", "name": "RecipeSage",
                                    "url": "http://127.0.0.1:5998/", "ref": cands[1]["ref"]}}

    monkeypatch.setattr(toolsmith_run, "run_job", run_job)
    monkeypatch.setattr(make_mod, "_make_later",
                        lambda *a, **k: pytest.fail("the old install path"))
    import puffpai.build.yes as yes_mod
    monkeypatch.setattr(yes_mod, "covers", lambda b: True)
    got = make_mod.make(dict(j), build, hooks={"check": lambda c: {"ok": True}})
    assert seen["refs"] == [j["pick"]["ref"]] + j["alternates"][:2]
    assert got["ok"] and got["screen"]["url"] == "http://127.0.0.1:5998/"
