"""Capabilities first: the jobs worked out from her goal are kept as what the
bot can do, every find and install is tagged with the job it covers, and the
brain, the dashboard and the closing screen all read the same map. Live cards
never promise a search they cannot do, keep their links, and can be taken out.

Nothing goes online: the brain is a fake answer, the network is fake.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json

import pytest

from livedata_support import FakeResponse, fake_network, feed_manifest, install, set_posture
from puffpai import feeds, things
from puffpai.finding import capabilities, find
from puffpai.finding import install as install_mod
from puffpai.security import egress_doors

ANSWER = {"kind": "piano tutor", "jobs": [
    {"name": "Teach a piano lesson", "does": "Shows which keys to press, one lesson a day.",
     "parts": [{"kind": "code-app", "search": ["piano lesson app"]}]},
    {"name": "Make music from words", "does": "Turns a description into a short song.",
     "parts": [{"kind": "code-app", "search": ["music from text"]}]},
]}

SKILL_MD = """---
name: Piano Coach
description: "Daily piano lessons with keys and fingering."
---

## Lessons

One a day.
"""


@pytest.fixture
def state(monkeypatch, tmp_path):
    root = tmp_path / "state"
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(root))
    folder = root / "skills" / "piano-coach"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    monkeypatch.setattr(things, "brain_on", lambda: False)
    return root


def _quiet_search(monkeypatch):
    """find_for_goal without any site: the jobs it would search are kept."""
    searched: list[list[dict]] = []
    monkeypatch.setattr(find, "_switched_off", lambda _site: True)
    monkeypatch.setattr(find.books, "off", lambda: True)

    def fake_search(jobs, **_kw):
        searched.append([dict(j) for j in jobs])
        return {"jobs": [], "notes": [], "count": 0}
    monkeypatch.setattr(find, "search_jobs", fake_search)
    return searched


def _skill_entry(cap: str) -> None:
    install_mod._save_entries([{"entry": "t_0000000001", "id": "clawhub:x/piano-coach",
                                "name": "Piano Coach", "type": "skill", "caps": [cap],
                                "undo": {"kind": "skill", "folder": "piano-coach"}}])


# ── the capabilities are kept, and their finds tagged ──────────────────────

def test_the_wizards_jobs_are_kept_as_capabilities(state, monkeypatch):
    searched = _quiet_search(monkeypatch)
    find.find_for_goal("a bot that teaches me piano", asker=lambda g: ANSWER, keep=True)
    caps = capabilities.load()
    assert [c["name"] for c in caps] == ["Teach a piano lesson", "Make music from words"]
    assert caps[0]["does"].startswith("Shows which keys")
    # The whole-bot search is not a capability; the jobs searched carry their tag.
    tags = {j["name"]: j.get("cap") for j in searched[0]}
    assert tags["Everything for a piano tutor bot"] is None
    assert tags["Teach a piano lesson"] == caps[0]["id"]


def test_a_plain_search_never_replaces_the_bots_capabilities(state, monkeypatch):
    _quiet_search(monkeypatch)
    find.find_for_goal("a bot that teaches me piano", asker=lambda g: ANSWER, keep=True)
    other = {"kind": "garden", "jobs": [{"name": "Water the plants", "parts": []}]}
    find.find_for_goal("a garden helper for my plants", asker=lambda g: other)
    assert capabilities.load()[0]["name"] == "Teach a piano lesson"


def test_jobs_past_the_search_cap_are_still_capabilities(state, monkeypatch):
    searched = _quiet_search(monkeypatch)
    many = {"kind": "piano tutor", "jobs": [
        {"name": f"Job number {i}", "parts": [{"kind": "code-app", "search": [f"words {i}"]}]}
        for i in range(9)]}
    find.find_for_goal("a piano bot", asker=lambda g: many, keep=True)
    assert len(capabilities.load()) == 9
    assert len(searched[0]) == find.MAX_JOBS


def test_one_capabilitys_search_tags_its_finds(state, monkeypatch):
    searched = _quiet_search(monkeypatch)
    find.find_for_goal("a bot that teaches me piano", asker=lambda g: ANSWER, keep=True)
    cap = capabilities.load()[1]["id"]
    find.find_things("music from text", cap=cap)
    assert searched[-1][0]["cap"] == cap
    find.find_things("music from text", cap="cap-not-one-of-hers")
    assert "cap" not in searched[-1][0]


def test_an_install_keeps_the_capability_it_was_found_for(state):
    find.remember([{"id": "clawhub:x/piano-coach", "name": "Piano Coach", "cap": "cap-a"}])
    entry = install_mod._add_entry({"id": "clawhub:x/piano-coach", "name": "Piano Coach",
                                    "type": "skill"})
    assert entry["caps"] == ["cap-a"]
    # Found again for another job: the same install covers both.
    install_mod.tag_capability(entry["entry"], "cap-b")
    assert install_mod.entries()[-1]["caps"] == ["cap-a", "cap-b"]
    # A later plain search of the same find keeps its tag.
    find.remember([{"id": "clawhub:x/piano-coach", "name": "Piano Coach"}])
    assert find.recall("clawhub:x/piano-coach")["cap"] == "cap-a"


# ── coverage: the dashboard, the closing screen, the brain ─────────────────

def test_coverage_names_the_thing_and_the_gap(state, monkeypatch):
    _quiet_search(monkeypatch)
    find.find_for_goal("a bot that teaches me piano", asker=lambda g: ANSWER, keep=True)
    lesson, music = capabilities.load()
    _skill_entry(lesson["id"])
    rows = {r["id"]: r for r in capabilities.coverage()}
    assert rows[lesson["id"]]["covered"] is True
    assert rows[lesson["id"]]["things"][0]["id"] == "skill-piano-coach"
    assert rows[lesson["id"]]["things"][0]["how"] == "a skill your bot follows"
    assert rows[music["id"]]["covered"] is False
    assert rows[music["id"]]["line"] == capabilities.NOTHING_YET
    assert rows[music["id"]]["find"] == "Make music from words"
    lines = capabilities.report_lines()
    assert lines[0].startswith("Teach a piano lesson: Piano Coach")
    assert things.page("skill-piano-coach")["covers"] == ["Teach a piano lesson"]


def test_the_brain_gets_the_map_in_its_system_notes(state, monkeypatch):
    _quiet_search(monkeypatch)
    find.find_for_goal("a bot that teaches me piano", asker=lambda g: ANSWER, keep=True)
    _skill_entry(capabilities.load()[0]["id"])
    block = capabilities.prompt_block()
    assert block.startswith("## What this bot does")
    assert 'Teach a piano lesson: skill piano-coach (load it with skill_view("piano-coach"))' in block
    assert "Make music from words: nothing yet" in block
    from puffpai import brain_loop
    assert block in brain_loop.system_notes([])


def test_a_thing_taken_out_stops_covering(state, monkeypatch):
    _quiet_search(monkeypatch)
    find.find_for_goal("a bot that teaches me piano", asker=lambda g: ANSWER, keep=True)
    _skill_entry(capabilities.load()[0]["id"])
    import shutil
    shutil.rmtree(state / "skills" / "piano-coach")
    assert all(not r["covered"] for r in capabilities.coverage())


def test_an_older_bot_with_no_capabilities_shows_nothing(state):
    assert capabilities.coverage() == []
    assert capabilities.prompt_block() == ""
    assert capabilities.report_lines() == []
    assert "covers" not in things.page("skill-piano-coach")


def test_the_capabilities_route(state, monkeypatch):
    from puffpai import api_server
    auth = {"Authorization": f"Bearer {api_server._API_KEY}",
            "X-Puff-Device-Id": "deviceaaaa000001"}
    monkeypatch.setattr("puffpai.things_api._device", lambda: "device:x")
    _quiet_search(monkeypatch)
    find.find_for_goal("a bot that teaches me piano", asker=lambda g: ANSWER, keep=True)
    client = api_server.app.test_client()
    got = client.get("/things/capabilities", headers=auth).get_json()
    assert [c["name"] for c in got["capabilities"]] == ["Teach a piano lesson",
                                                       "Make music from words"]


# ── live cards: no dead ends ────────────────────────────────────────────────

LIST_CARD = {"kind": "list", "title": "Artists", "items": "data", "item_title": "name",
             "max_items": 5}
ROWS = {"data": [{"name": "A Player", "link": "https://www.example.org/artist/1"},
                 {"name": "B Player", "link": "https://evil.example.com/artist/2"}]}


@pytest.fixture
def card(monkeypatch):
    monkeypatch.delenv(egress_doors.APPS_KILL_ENV, raising=False)
    egress_doors.clear_app_doors()
    egress_doors.reset_cache()
    monkeypatch.setattr(things, "brain_on", lambda: False)
    monkeypatch.setattr(feeds, "_seed_lookup", lambda host: None)
    set_posture("enumerated")
    yield
    try:
        things.remove("feed-cardx")
    except Exception:  # noqa: BLE001
        pass
    egress_doors.clear_app_doors()
    egress_doors.reset_cache()


def test_rows_keep_their_own_page_on_the_services_site(card):
    feed = feed_manifest("cardx", card=LIST_CARD, image_host=None)["feed"]
    items = feeds.extract(ROWS, feed)["items"]
    assert items[0]["link"] == "https://www.example.org/artist/1"
    assert "link" not in items[1]       # another website is never linked


def test_a_card_that_cannot_search_never_offers_a_lookup(card):
    install(feed_manifest("cardx", card=LIST_CARD, image_host=None))
    thing = things.get("feed-cardx")
    assert not any("up" in t.lower() for t in things.fallback_recipe(thing)["try"])
    # A page saved before: its lookup promise is dropped when shown.
    things._write_recipe("feed-cardx", {**things.fallback_recipe(thing), "try": [
        "What is new on Sky Shots?", "Look something up on Sky Shots for me",
        "Search for a band"]})
    page = things.page("feed-cardx")
    assert page["recipe"]["try"] == ["What is new on Sky Shots?"]
    assert page["feed"]["looks_up"] is False
    got = things.lookup("feed-cardx", "jazz")
    assert got["ok"] is False and "cannot look" in got["message"]


def test_a_card_that_can_search_looks_up_through_its_own_service(card, monkeypatch):
    install(feed_manifest("cardx", card=LIST_CARD, image_host=None,
                          lookup_url="https://api.example.org/search?q={topic}"))
    feeds.set_allowed("cardx", True)
    monkeypatch.setattr(feeds, "_first_fetch_starter", lambda _id: False)
    thing = things.get("feed-cardx")
    assert any("Look something up" in t for t in things.fallback_recipe(thing)["try"])
    net = fake_network(monkeypatch)
    net.on("https://api.example.org/search", FakeResponse(200, json.dumps(ROWS).encode()))
    got = things.lookup("feed-cardx", "jazz")
    assert got["ok"] is True
    assert got["items"][0] == {"title": "A Player", "link": "https://www.example.org/artist/1"}
    assert [c["url"] for c in net.calls] == ["https://api.example.org/search?q=jazz"]


def test_a_live_card_can_be_taken_out(card):
    install(feed_manifest("cardx", card=LIST_CARD, image_host=None))
    assert things.get("feed-cardx") is not None
    out = things.remove("feed-cardx")
    assert out["ok"] is True
    assert things.get("feed-cardx") is None
    assert "cardx" not in [m["id"] for m, _f in feeds.feed_apps()]


def test_the_deezer_row_can_search_and_link():
    from puffpai.catalogs import api_seeds
    row = {r["id"]: r for r in api_seeds.load_rows()}["deezer"]
    assert api_seeds.row_problem(row) is None
    assert "{topic}" in row["lookup_url"]
    assert row["card"]["item_link"] == "link"


# ── finds not searched for one capability are tagged by meaning ────────────

_TOPICS = ("piano", "music", "weather")


def _fake_embed(texts):
    """A stand-in for the reading helper: one direction per topic word."""
    import numpy as np
    out = np.zeros((len(texts), 384), dtype=np.float32)
    for i, text in enumerate(texts):
        low = str(text).lower()
        for k, word in enumerate(_TOPICS):
            if word in low:
                out[i, k] = 1.0
        if not out[i].any():
            out[i, 383] = 1.0
    return out / np.linalg.norm(out, axis=1, keepdims=True)


@pytest.fixture
def meaning_fake(monkeypatch):
    from puffpai.catalogs import meaning
    monkeypatch.setattr(meaning, "embed", _fake_embed)


def _two_caps(monkeypatch):
    _quiet_search(monkeypatch)
    find.find_for_goal("a bot that teaches me piano", asker=lambda g: ANSWER, keep=True)
    return capabilities.load()


def test_whole_bot_finds_are_tagged_with_the_capability_they_fit(state, monkeypatch,
                                                                   meaning_fake):
    lesson, music = _two_caps(monkeypatch)
    finds = [{"id": "clawhub:x/piano-teacher", "name": "AI teacher", "what": "Lessons"},
             {"id": "github:x/tuner", "name": "Tuner", "what": "Piano and music practice"},
             {"id": "npm:left-pad", "name": "left-pad", "what": "Pads strings"},
             {"id": "github:x/song", "name": "Song", "what": "Music", "cap": music["id"]}]
    find._caps_by_meaning(finds)
    # Its own short name counts (a name in another language, an English slug).
    assert finds[0]["cap"] == lesson["id"] and finds[0]["caps"] == [lesson["id"]]
    assert set(finds[1]["caps"]) == {lesson["id"], music["id"]}
    assert "cap" not in finds[2]                     # fits nothing: no tag
    assert finds[3]["cap"] == music["id"] and "caps" not in finds[3]
    # Installing it covers every capability it fits.
    find.remember(finds)
    entry = install_mod._add_entry({"id": "github:x/tuner", "name": "Tuner", "type": "skill"})
    assert set(entry["caps"]) == {lesson["id"], music["id"]}


def test_no_capabilities_or_no_helper_means_no_tags(state, monkeypatch):
    finds = [{"id": "clawhub:x/piano-teacher", "name": "Piano teacher"}]
    find._caps_by_meaning(finds)                     # an older bot: nothing kept
    assert "cap" not in finds[0]
    _two_caps(monkeypatch)
    from puffpai.catalogs import meaning

    def no_helper(_texts):
        raise RuntimeError("the reading helper is not on this computer")
    monkeypatch.setattr(meaning, "embed", no_helper)
    find._caps_by_meaning(finds)
    assert "cap" not in finds[0]


def test_an_untagged_install_still_covers_what_it_fits(state, monkeypatch, meaning_fake):
    lesson, music = _two_caps(monkeypatch)
    install_mod._save_entries([{"entry": "t_0000000001", "id": "clawhub:x/piano-coach",
                                "name": "Piano Coach", "type": "skill",
                                "undo": {"kind": "skill", "folder": "piano-coach"}}])
    rows = {r["id"]: r for r in capabilities.coverage()}
    assert rows[lesson["id"]]["covered"] is True
    assert rows[lesson["id"]]["things"][0]["id"] == "skill-piano-coach"
    assert rows[music["id"]]["line"] == capabilities.NOTHING_YET
    assert "Teach a piano lesson: skill piano-coach" in capabilities.prompt_block()


def test_a_live_card_covers_what_it_fits(state, card, monkeypatch, meaning_fake):
    lesson, music = _two_caps(monkeypatch)
    manifest = feed_manifest("cardx", card=LIST_CARD, image_host=None)
    manifest["name"] = "Music charts"
    install(manifest)
    rows = {r["id"]: r for r in capabilities.coverage()}
    assert [t["id"] for t in rows[music["id"]]["things"]] == ["feed-cardx"]
    assert rows[music["id"]]["things"][0]["how"] == "a live card"
    assert rows[lesson["id"]]["covered"] is False


def test_the_real_helper_tags_a_piano_teacher_and_not_a_library(state, monkeypatch):
    """The reading helper itself, when it is on this computer (measured
    on a real piano build: the teacher 0.56, the library 0.44)."""
    from puffpai.catalogs import meaning
    if meaning.helper_state() != "ready":
        pytest.skip("the reading helper is not on this computer")
    capabilities.save("a bot that teaches me piano every day", [
        {"name": "teaches piano", "does": "Provides daily piano lessons and practice tips."},
        {"name": "sends reminders", "does": "Sends notifications to remind the user to practice."}])
    teacher = {"id": "clawhub:bettermen/piano-teacher", "name": "AI\u94a2\u7434\u8001\u5e08",
               "what": "AI Piano Teacher covering music theory, technique, repertoire "
                       "analysis, practice plans, exam prep, sight reading, ear training"}
    library = {"id": "npm:piano-analytics-js", "name": "piano-analytics-js",
               "what": "JavaScript library for Piano Analytics"}
    find._caps_by_meaning([teacher, library])
    assert teacher["cap"] == "cap-teaches-piano"
    assert "cap" not in library
