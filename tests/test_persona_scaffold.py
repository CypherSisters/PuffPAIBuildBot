"""A persona scaffolded from a goal nothing ships for, and used with no model.

The validator's walk: goal "a garden and greenhouse assistant for a small family
farm". Three defects, all of them here as tests.

1. **The interview was dishonest.** It proposed Calendar, because the word
   FAMILY was a tag on the calendar app. Household filler is now stopwords, the
   tag is gone, and a goal nothing matches says exactly that and offers the
   scaffold instead of "add things by hand".
2. **There was no scaffold.** ``puffpai persona new`` writes one app and one
   pack, into the OWNER's directories rather than the checkout, validated by the
   real validator before anything is written.
3. **Chat could not reach it without a model.** "add tomatoes to the plantings
   list" routed to apps, found a media-only fastpath, and 503'd. The four list
   verbs are now deterministic, manifest-driven, and verified by reading the
   document back off disk.

No Ollama and no network: the model is never called on these paths, and one test
asserts that by making the call fail the test if it happens.

Run:
    pytest tests/test_persona_scaffold.py
"""
from __future__ import annotations

import json
import os

import pytest

from puffpai import (
    api_server,
    app_data as app_data_mod,
    app_fastpath,
    app_registry,
    build_interview,
    cli,
    persona_scaffold,
)
from puffpai.app_data import AppData

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}

GARDEN_GOAL = ("a garden and greenhouse assistant for a small family farm: "
               "remind me when to water, keep a planting list, and tell me "
               "what the frost forecast looks like")


@pytest.fixture(autouse=True)
def _fresh_registry():
    """Every test in this file starts and ends with a re-read app registry.

    The manifest cache is process-wide, and these tests install an app into a
    temporary directory. Without this, a scaffolded GardenBot stays in the cache
    after its folder has gone - which is both a false pass here and a stray
    sixth app in whatever test file runs next.
    """
    app_registry.rescan()
    yield
    app_registry.rescan()


@pytest.fixture
def owner(tmp_path, monkeypatch):
    """An owner's own apps/packs directories, and an app_data dir to match.

    ``PUFFPAI_STATE_DIR`` is where :func:`app_registry.user_apps_dir` reads, so
    pointing it at tmp_path is the whole isolation: a scaffolded app cannot leak
    into the repo, and cannot leak into the next test either.
    """
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(app_data_mod, "_DATA_DIR", str(tmp_path / "app_data"))
    app_registry.rescan()
    return tmp_path


# ---------------------------------------------------------------------------
# 1. the interview tells the truth
# ---------------------------------------------------------------------------

def test_household_filler_cannot_carry_a_match_on_its_own():
    """The exact defect: FAMILY was a tag, so a farm goal was offered Calendar."""
    for word in ("family", "families", "small", "everyday", "kids"):
        assert word in build_interview._STOPWORDS, word
    calendar = next(item for item in build_interview.load_catalog()["items"]
                    if item["id"] == "app:calendar")
    assert "family" not in calendar["tags"]

    # A goal that is ONLY household filler has told us nothing, and gets the
    # better question rather than a menu.
    proposal = build_interview.propose("a bot for my small family")
    assert proposal["flat"] == [], proposal["flat"]
    assert "could mean almost anything" in proposal["message"].lower()

    # A concrete goal whose only overlap with the catalog WAS the word family
    # now matches nothing, and is told so.
    proposal = build_interview.propose("a family bookkeeping ledger for invoices")
    assert proposal["flat"] == [], proposal["flat"]
    assert "nothing we ship matches" in proposal["message"].lower()


def test_a_family_calendar_goal_still_finds_the_calendar():
    """Dropping the tag must not cost the app the goals it really answers."""
    ids = {e["id"] for e in
           build_interview.propose("a family calendar and reminders")["flat"]}
    assert "app:calendar" in ids


def test_a_garden_goal_matches_real_things_with_cited_reasons():
    """Reminders, knowledge and weather - each citing the word that matched."""
    proposal = build_interview.propose(GARDEN_GOAL)
    ids = {entry["id"] for entry in proposal["flat"]}
    assert {"app:alarmflow", "integration:weather", "pack:wikipedia"} <= ids

    surfaces = build_interview.derive_surfaces(proposal)
    assert {"comms.timers", "daily.weather", "knowledge.library"} <= set(
        surfaces["surfaces"])
    for surface, reason in surfaces["reasons"].items():
        assert "because you asked about" in reason, (surface, reason)
        # Every cited term is first-party catalog vocabulary, never the typing.
        cited = reason.split("'")[1]
        assert cited in {t for item in build_interview.load_catalog()["items"]
                         for t in item["tags"]} | {"weather", "garden"}


def test_nothing_matched_says_so_and_offers_the_scaffold():
    proposal = build_interview.propose("a bot about zzxq and qwph")
    assert proposal["flat"] == []
    assert proposal["scaffold_offer"] is True
    message = proposal["message"]
    assert message.startswith("Nothing we ship matches that yet.")
    assert "scaffold a blank persona" in message
    assert build_interview.SCAFFOLD_STEP in message


# ---------------------------------------------------------------------------
# 2. the scaffold
# ---------------------------------------------------------------------------

def test_persona_new_writes_a_valid_app_and_pack_into_the_owners_dirs(owner):
    result = persona_scaffold.scaffold(
        "gardenbot", name="GardenBot", goal=GARDEN_GOAL,
        lists="plantings,harvest log")

    assert result["app_path"].startswith(str(owner))
    assert result["pack_path"].startswith(str(owner))
    assert app_registry.user_apps_dir() in result["app_path"]
    assert result["lists"] == ["plantings", "harvest_log"]
    assert result["installed"] is True and result["pack_installed"] is True

    with open(result["app_path"], encoding="utf-8") as fh:
        manifest = json.load(fh)
    assert app_registry.validate_manifest(manifest) is None
    # Its OWN surface, not a borrowed one: whether this bot shows up in the
    # nav must not depend on whether an unrelated surface is on the allowlist.
    assert manifest["surfaces"] == ["persona.gardenbot"]
    assert manifest["ui"]["route"] == "/dashboard/apps/gardenbot"
    assert manifest["data"]["default"] == {"plantings": [], "harvest_log": []}
    # Vocabulary from the lists and the goal's real words; no filler.
    keywords = manifest["vocabulary"]["keywords"]
    assert "plantings" in keywords and "harvest log" in keywords
    assert "garden" in keywords and "greenhouse" in keywords
    for filler in ("small", "family", "me", "the"):
        assert filler not in keywords

    with open(result["pack_path"], encoding="utf-8") as fh:
        pack = json.load(fh)
    assert app_registry.validate_pack(pack, {"gardenbot"}) is None


def test_the_scaffolded_app_is_installed_without_touching_the_checkout(owner):
    persona_scaffold.scaffold("gardenbot", name="GardenBot", lists="plantings")
    ids = app_registry.installed_ids(app_registry.rescan())
    assert "gardenbot" in ids
    assert "gardenbot" not in os.listdir(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "apps"))
    assert [p["id"] for p in persona_scaffold.personas()] == ["gardenbot"]


def test_a_scaffolded_apps_store_lands_outside_the_install(owner):
    """The defect: a persona's own data was written inside the checkout.

    ``app_data/<id>.json`` resolved against the package directory, so an owner
    who scaffolded GardenBot got their plantings list inside the install - one
    stray ``git add`` from being published, and unwritable on a pip install.
    """
    persona_scaffold.scaffold("gardenbot", name="GardenBot", lists="plantings")
    ad = AppData()

    store = ad._files["gardenbot"]
    assert os.path.dirname(store) == app_data_mod._state_data_dir()
    assert store.startswith(str(owner / "state"))
    assert not store.startswith(str(owner / "app_data"))

    ad.set_data("gardenbot", {"plantings": [{"item": "tomatoes"}]})
    assert os.path.isfile(store)
    # And nothing at all was written into the directory inside the install.
    assert not os.path.isdir(owner / "app_data")


def test_an_id_that_is_not_an_id_writes_nothing(owner):
    for bad in ("Garden Bot", "../evil", "9lives", "_hidden", "x"):
        with pytest.raises(persona_scaffold.ScaffoldError):
            persona_scaffold.scaffold(bad, name="Nope")
    assert not os.path.isdir(app_registry.user_apps_dir())


def test_scaffolding_over_an_existing_persona_is_refused(owner):
    persona_scaffold.scaffold("gardenbot", name="GardenBot", lists="plantings")
    with pytest.raises(persona_scaffold.ScaffoldError) as exc:
        persona_scaffold.scaffold("gardenbot", name="Another")
    assert "already" in str(exc.value)


def test_a_shipped_app_id_cannot_be_shadowed(owner):
    with pytest.raises(persona_scaffold.ScaffoldError) as exc:
        persona_scaffold.scaffold("calendar", name="Mine")
    assert "already installed" in str(exc.value)


def test_the_cli_verbs_print_what_they_made(owner, capsys):
    args = cli.build_parser().parse_args(
        ["persona", "new", "gardenbot", "--name", "GardenBot",
         "--goal", GARDEN_GOAL, "--lists", "plantings,harvest log"])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "Made GardenBot (gardenbot)." in out
    assert "plantings, harvest log" in out
    assert "/dashboard/apps/gardenbot" in out

    args = cli.build_parser().parse_args(["persona", "list"])
    assert args.func(args) == 0
    assert "gardenbot" in capsys.readouterr().out

    args = cli.build_parser().parse_args(["apps", "list"])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "gardenbot" in out and "calendar" in out
    assert app_registry.user_apps_dir() in out

    args = cli.build_parser().parse_args(["apps", "rescan"])
    assert args.func(args) == 0
    assert "Rescanned." in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 3. the four verbs, with no model anywhere
# ---------------------------------------------------------------------------

@pytest.fixture
def client(owner, monkeypatch):
    """A Flask client over a build carrying GardenBot, and NO model.

    ``_query_ollama_direct`` is replaced with a test failure: every assertion
    below is about a path that must not consult a model at all, so a call is a
    defect rather than a slow test.
    """
    persona_scaffold.scaffold("gardenbot", name="GardenBot", goal=GARDEN_GOAL,
                              lists="plantings,harvest log")
    ad = AppData()
    assert "gardenbot" in ad.VALID_APPS
    monkeypatch.setattr(api_server, "_get_app_data", lambda: ad)

    def _no_model(*a, **k):
        raise AssertionError("the model was consulted for a declarative app")

    monkeypatch.setattr(api_server, "_query_ollama_direct", _no_model)
    return api_server.app.test_client()


def _say(client, message: str) -> str:
    resp = client.post("/chat/send", json={"message": message}, headers=AUTH)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["response"]


def test_the_greenhouse_example_end_to_end_without_ollama(client):
    """The whole validator scenario, through /chat/send, with no brain."""
    assert "Added tomatoes to the plantings list" in _say(
        client, "add tomatoes to the plantings list")
    assert "tomatoes" in _say(client, "what's on the plantings list")

    # ...and it is really on disk, not merely in a sentence.
    stored = client.get("/apps/gardenbot/data", headers=AUTH).get_json()
    assert [row["item"] for row in stored["plantings"]] == ["tomatoes"]

    assert "One thing" in _say(
        client, "how many things are on the plantings list")
    assert "Took tomatoes off" in _say(
        client, "remove tomatoes from the plantings list")
    assert _say(client, "what is on the plantings list").endswith("is empty.")


def test_each_list_is_its_own_list(client):
    _say(client, "add tomatoes to the plantings list")
    _say(client, "add four crates of squash to the harvest log")
    assert "squash" in _say(client, "show me the harvest log")
    assert "squash" not in _say(client, "what is on the plantings list")


def test_an_ambiguous_sentence_asks_instead_of_guessing(client):
    answer = _say(client, "add compost to the list")
    assert answer == "Which list - plantings or harvest log?"
    stored = client.get("/apps/gardenbot/data", headers=AUTH).get_json()
    assert stored["plantings"] == [] and stored["harvest_log"] == []


def test_a_write_that_did_not_land_is_not_reported_as_saved(client, monkeypatch):
    """Evidence or silence: no read-back, no claim of success."""
    ad = api_server._get_app_data()
    monkeypatch.setattr(ad, "set_data", lambda *a, **k: False)
    answer = _say(client, "add tomatoes to the plantings list")
    assert "could not save" in answer.lower()
    assert "added" not in answer.lower()


def test_the_hand_written_apps_are_left_alone(owner):
    """FreshTable's shopping list is FreshTable's sentence, not this module's."""
    persona_scaffold.scaffold("gardenbot", name="GardenBot",
                              lists="plantings,harvest log")
    ad = AppData()
    assert app_fastpath.handle("add milk to the shopping list", ad) is None
    assert app_fastpath.handle("set a timer for ten minutes", ad) is None
    assert app_fastpath.handle("what is on my calendar tomorrow", ad) is None
    assert app_fastpath.handle("tell me a joke", ad) is None
    assert app_fastpath.handle("", ad) is None


def test_a_build_with_no_declarative_app_never_answers_here():
    """Nothing scaffolded, nothing claimed - the five shipped apps are excluded."""
    ad = AppData()
    assert app_fastpath.handle("add tomatoes to the plantings list", ad) is None


# ---------------------------------------------------------------------------
# 4. the API answer the dashboard (and the validator) looked for
# ---------------------------------------------------------------------------

def test_apps_installed_answers_without_rescanning(client):
    payload = client.get("/apps/installed", headers=AUTH).get_json()
    assert "gardenbot" in payload["apps"]
    assert "gardenbot" in payload["packs"]
    assert payload["rescanned"] is False
    assert payload["user_apps_dir"] == app_registry.user_apps_dir()


def test_apps_installed_needs_the_api_key():
    assert api_server.app.test_client().get("/apps/installed").status_code == 401


# ---------------------------------------------------------------------------
# 5. the same persona, built from the dashboard instead of the terminal
# ---------------------------------------------------------------------------

@pytest.fixture
def bare(owner, monkeypatch):
    """A client over a build with NO persona yet - the builder page's starting
    point. AppData is the shared instance the routes reload in place."""
    ad = AppData()
    monkeypatch.setattr(api_server, "_get_app_data", lambda: ad)
    return api_server.app.test_client()


def test_preview_shows_the_words_without_writing_anything(bare):
    resp = bare.post("/persona/preview",
                     json={"name": "GardenBot", "goal": GARDEN_GOAL,
                           "lists": ["plantings", "harvest log"]}, headers=AUTH)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()

    assert body["id"] == "gardenbot"
    assert body["lists"] == ["plantings", "harvest_log"]
    assert body["list_labels"] == ["plantings", "harvest log"]
    assert "garden" in body["keywords"] and "plantings" in body["keywords"]
    assert body["route"] == "/dashboard/apps/gardenbot"
    assert body["taken"] is False
    assert not os.path.exists(body["app_path"])

    # The preview is the manifest that would be written, word for word.
    written = bare.post("/persona/scaffold",
                        json={"name": "GardenBot", "goal": GARDEN_GOAL,
                              "lists": ["plantings", "harvest log"]},
                        headers=AUTH).get_json()
    assert written["written"] is True
    assert written["persona"]["keywords"] == body["keywords"]
    assert written["persona"]["app_path"] == body["app_path"]
    assert "gardenbot" in written["apps"]


def test_the_route_refuses_what_the_cli_refuses(bare):
    for payload, fragment in (
        ({"name": "Nope", "id": "Garden Bot"}, "cannot be an app id"),
        ({"name": "Nope", "id": "../evil"}, "cannot contain"),
        ({"name": "x" * 41, "id": "okbot"}, "as long as a name"),
        ({"name": "Nope", "id": "okbot",
          "lists": [f"l{n}" for n in range(9)]}, "9 lists"),
        ({"name": "Nope", "id": "okbot", "lists": ["a" * 33]}, "characters"),
        ({"name": "Nope", "id": "okbot", "lists": ["../etc"]}, "cannot contain"),
        ({"name": "Mine", "id": "calendar"}, "already installed"),
    ):
        resp = bare.post("/persona/scaffold", json=payload, headers=AUTH)
        assert resp.status_code == 400, (payload, resp.get_data(as_text=True))
        body = resp.get_json()
        assert fragment in body["error"], (payload, body["error"])
        assert body["written"] is False
    assert not os.path.isdir(os.path.join(app_registry.user_apps_dir(), "okbot"))


def test_scaffolding_from_the_route_installs_it_for_chat_at_once(bare):
    """The loop her test walks: build it in the browser, then talk to it."""
    bare.post("/persona/scaffold",
              json={"name": "GardenBot", "goal": GARDEN_GOAL,
                    "lists": ["plantings"]}, headers=AUTH)

    said = bare.post("/chat/send",
                     json={"message": "add tomatoes to the plantings list"},
                     headers=AUTH).get_json()["response"]
    assert "Added tomatoes to the plantings list" in said
    stored = bare.get("/apps/gardenbot/data", headers=AUTH).get_json()
    assert [row["item"] for row in stored["plantings"]] == ["tomatoes"]


def test_persona_list_carries_the_lists_and_the_try_line(bare):
    bare.post("/persona/scaffold",
              json={"name": "GardenBot", "lists": ["plantings", "harvest log"]},
              headers=AUTH)
    body = bare.get("/persona/list", headers=AUTH).get_json()

    assert [p["id"] for p in body["personas"]] == ["gardenbot"]
    app = next(a for a in body["apps"] if a["id"] == "gardenbot")
    assert app["lists"] == ["plantings", "harvest_log"]
    assert app["try"] == "add tomatoes to the plantings list"
    assert app["route"] == "/dashboard/apps/gardenbot"
    # A shipped app is not a persona and is not listed as one.
    assert "calendar" not in [a["id"] for a in body["apps"]]


def test_delete_needs_the_id_typed_back_and_then_it_is_gone(bare):
    bare.post("/persona/scaffold",
              json={"name": "GardenBot", "lists": ["plantings"]}, headers=AUTH)
    bare.post("/chat/send", json={"message": "add tomatoes to the plantings list"},
              headers=AUTH)
    store = AppData()._files["gardenbot"]
    assert os.path.isfile(store)

    # No confirmation, or the wrong one: nothing happens.
    for body in ({}, {"confirm": "gardenbo"}):
        resp = bare.delete("/persona/gardenbot", json=body, headers=AUTH)
        assert resp.status_code == 400
        assert resp.get_json()["removed"] is False
    assert "gardenbot" in app_registry.installed_ids(app_registry.current_manifests())

    resp = bare.delete("/persona/gardenbot", json={"confirm": "gardenbot"},
                       headers=AUTH)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["removed"] is True
    assert resp.get_json()["installed"] is False
    assert not os.path.isdir(os.path.join(app_registry.user_apps_dir(), "gardenbot"))
    assert not os.path.isdir(os.path.join(app_registry.user_packs_dir(), "gardenbot"))
    assert not os.path.isfile(store)


def test_a_shipped_app_cannot_be_deleted_this_way(bare):
    resp = bare.delete("/persona/calendar", json={"confirm": "calendar"},
                       headers=AUTH)
    assert resp.status_code == 403
    assert "not a persona you made" in resp.get_json()["error"]
    assert "calendar" in app_registry.installed_ids(app_registry.current_manifests())
    assert persona_scaffold.is_user_persona("calendar") is False


def test_a_request_that_really_needs_the_model_says_so_and_how_to_fix_it(owner,
                                                                        monkeypatch):
    """The other half of the honesty: a list verb works, a question does not.

    The fastpath must not be allowed to make a brainless build LOOK complete.
    Anything outside the four verbs needs the language model, and what the owner
    gets is a sentence naming that and the remedy - not a 503, and not a
    cheerful non-answer.
    """
    from puffpai import brain_health

    persona_scaffold.scaffold("gardenbot", name="GardenBot", lists="plantings")
    ad = AppData()
    monkeypatch.setattr(api_server, "_get_app_data", lambda: ad)
    client = api_server.app.test_client()

    # The list verb still works, with no model anywhere: the fastpath answers
    # before anything is dispatched, which is why the sentence below is not
    # reached for it at all.
    assert "Added tomatoes" in client.post(
        "/chat/send", json={"message": "add tomatoes to the plantings list"},
        headers=AUTH).get_json()["response"]
    assert api_server._try_app_fastpath("why are my tomatoes not ripening") is None

    # A question the fastpath does not cover needs the model, and what comes
    # back names that and carries the fix - no 503, no cheerful non-answer.
    for status in (None, 404):
        message = api_server._brain_failure(status)["message"]
        assert message.startswith(brain_health.NEEDS_MODEL_SENTENCE), message
    assert "puffpai setup --ollama-url" in api_server._brain_failure(None)["message"]
    assert "ollama pull" in api_server._brain_failure(404)["message"]


@pytest.mark.parametrize("call", [
    lambda c: c.get("/persona/list"),
    lambda c: c.post("/persona/preview", json={"name": "x"}),
    lambda c: c.post("/persona/scaffold", json={"name": "x"}),
    lambda c: c.delete("/persona/x", json={"confirm": "x"}),
])
def test_every_persona_route_needs_the_api_key(call):
    assert call(api_server.app.test_client()).status_code == 401


# ---------------------------------------------------------------------------
# 5. the persona's own surface
# ---------------------------------------------------------------------------
#
# The nav, the home card and the search all gate on the surface allowlist, and
# the allowlist is a strict "not loaded" rule with no exceptions. So a persona
# has to pass it rather than be excepted from it: scaffolding one adds
# ``persona.<id>`` to the allowlist, renaming swaps it, deleting drops it, and a
# build made before this change is repaired on the next read.

TRIMMED = ["daily.notes", "knowledge.library"]


@pytest.fixture
def build_file(tmp_path, monkeypatch):
    """This build's manifest, in a temp file, with a real allowlist on it."""
    from puffpai import build_manifest

    path = str(tmp_path / "build_manifest.json")
    monkeypatch.setattr(build_manifest, "manifest_path", lambda: path)

    def write(allowlist, *, derived=True):
        record = build_manifest.assemble_manifest({}, surfaces={
            "allowlist": list(allowlist), "reasons": {}, "sensitive": [],
            "derived_from_goal": derived, "owner_edited": False,
            "note": "Surfaces outside the allowlist are not loaded.",
        })
        build_manifest.write_manifest(record)
        return path

    write.path = path
    write.read = lambda: build_manifest.read_manifest(path) or {}
    return write


def allowlist_of(build_file):
    return list((build_file.read().get("surfaces") or {}).get("allowlist") or [])


def test_scaffolding_puts_the_personas_surface_on_the_allowlist(owner, build_file):
    build_file(TRIMMED)
    result = persona_scaffold.scaffold("gardenbot", name="GardenBot",
                                       lists="plantings")

    assert result["surfaces"] == ["persona.gardenbot"]
    assert "persona.gardenbot" in allowlist_of(build_file)
    # Nothing else moved: an allowlist is a list of what this build loads, and
    # scaffolding a persona is not an opinion about the rest of it.
    assert set(TRIMMED) <= set(allowlist_of(build_file))
    reason = build_file.read()["surfaces"]["reasons"]["persona.gardenbot"]
    assert "GardenBot" in reason

    # Idempotent: scaffolding a second persona adds one entry, not two copies.
    persona_scaffold.scaffold("shedbot", name="ShedBot")
    assert allowlist_of(build_file).count("persona.gardenbot") == 1
    assert "persona.shedbot" in allowlist_of(build_file)


def test_an_untrimmed_build_is_left_alone(owner, build_file):
    """An empty allowlist means NOT trimmed - the dashboard shows everything.

    Writing one surface into it would flip the build to trimmed and hide every
    other page, so the persona would be the only thing left. There is nothing to
    add on such a build anyway: nothing is gated out.
    """
    build_file([], derived=False)
    persona_scaffold.scaffold("gardenbot", name="GardenBot")
    assert allowlist_of(build_file) == []


def test_renaming_swaps_the_surface_in_both_places(owner, build_file):
    build_file(TRIMMED)
    persona_scaffold.scaffold("gardenbot", name="GardenBot", lists="plantings")
    persona_scaffold.rename_id("gardenbot", "farmbot")

    names = allowlist_of(build_file)
    assert "persona.farmbot" in names and "persona.gardenbot" not in names
    with open(os.path.join(app_registry.user_apps_dir(), "farmbot",
                           app_registry.MANIFEST_NAME), encoding="utf-8") as fh:
        assert json.load(fh)["surfaces"] == ["persona.farmbot"]


def test_deleting_a_persona_takes_its_surface_off(owner, build_file):
    build_file(TRIMMED)
    persona_scaffold.scaffold("gardenbot", name="GardenBot")
    persona_scaffold.remove("gardenbot")

    names = allowlist_of(build_file)
    assert "persona.gardenbot" not in names
    assert set(TRIMMED) <= set(names)


def _legacy_persona(app_id="gardenbot", name="GardenBot"):
    """A persona as an OLDER build wrote it: declaring daily.notes, on no list."""
    manifest = persona_scaffold.build_app_manifest(app_id, name, "", ["plantings"])
    manifest["surfaces"] = ["daily.notes"]
    folder = os.path.join(app_registry.user_apps_dir(), app_id)
    persona_scaffold._write_json(
        os.path.join(folder, app_registry.MANIFEST_NAME), manifest)
    persona_scaffold._write_json(
        os.path.join(app_registry.user_packs_dir(), app_id,
                     app_registry.PACK_MANIFEST_NAME),
        persona_scaffold.build_pack_manifest(app_id, name))
    app_registry.rescan()
    return folder


def test_a_build_made_before_this_change_is_repaired_on_the_next_read(
        owner, build_file):
    """The owner's real build: persona present, surface absent, bot invisible."""
    build_file(TRIMMED)
    folder = _legacy_persona()
    assert "persona.gardenbot" not in allowlist_of(build_file)

    # Reading the persona list is enough - no command to find and type.
    listed = persona_scaffold.personas()
    assert [p["id"] for p in listed] == ["gardenbot"]

    with open(os.path.join(folder, app_registry.MANIFEST_NAME),
              encoding="utf-8") as fh:
        assert json.load(fh)["surfaces"] == ["persona.gardenbot"]
    assert "persona.gardenbot" in allowlist_of(build_file)
    # And the lists the owner kept are still the lists the owner kept.
    assert persona_scaffold.owner_apps()[0]["lists"] == ["plantings"]


def test_the_repair_is_idempotent_and_never_overrides_a_removal(owner, build_file):
    build_file(TRIMMED)
    _legacy_persona()
    assert persona_scaffold.repair_surfaces()["apps"] == ["gardenbot"]

    # Nothing left to do, so nothing is written.
    again = persona_scaffold.repair_surfaces()
    assert again["apps"] == [] and again["build_manifest"] == ""

    # An owner who takes their persona off the list means it: a repair that put
    # it back on every read would make the allowlist unusable.
    build_file(TRIMMED)
    assert persona_scaffold.repair_surfaces()["build_manifest"] == ""
    assert "persona.gardenbot" not in allowlist_of(build_file)


def test_a_persona_surface_is_a_real_surface_to_the_registry(owner):
    """The taxonomy admits it because the persona is on disk, not because a
    manifest asked for it."""
    from puffpai import build_interview

    assert "persona.gardenbot" not in build_interview.surface_taxonomy()
    persona_scaffold.scaffold("gardenbot", name="GardenBot")
    assert "persona.gardenbot" in build_interview.surface_taxonomy()

    # An app that is not there cannot name one.
    stranger = persona_scaffold.build_app_manifest("stranger", "Stranger")
    stranger["surfaces"] = ["persona.notreal"]
    assert app_registry.validate_manifest(stranger) is not None
