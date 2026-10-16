"""Migration from a bot built before build.json (a StargazerBot copy,
secrets removed). The copy is taken into a temp folder first;
the original is only read.

Its 4 capabilities become 4 jobs; the 5 skills that only share a word with
space ("constellation" design, a horoscope, UI patterns) land under "Not
used"; the live cards become data ingredients of put-together jobs; nothing
is deleted.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "build"))

from fakes import ScriptedBrain, concept_embed  # noqa: E402

from puffpai.build import migrate, schema as S  # noqa: E402

SOURCE = os.environ.get("PUFFPAI_TEST_REVIEW_STATE", "")
SKIP_DIRS = {"knowledge_packs", "kiwix_rag_db", "data", "cache", "catalog_cache", "logs",
             "chat_history", "learning", "packs", "workspace", "security_logs", "app_data",
             "drafts", "looks", "command_bin"}

OFF_TOPIC = {
    "clawhub:zhangj85/horoscope-daily-china",
    "skills.sh:compass-brand/constellation/bmad-compass-trigger-mapping",
    "skills.sh:praxstack/skills-and-personas/constellation-team",
    "skills.sh:luncosim/space-engineering-skills/constellation-design",
    "skills.sh:phazurlabs/sumi/navigation-pattern-encyclopedia",
}
FEEDS = {"app:launch_library_2_the_space", "app:nasa_epic_earth_photos",
         "app:nasa_image_and_video_library", "app:noaa_space_weather"}
#: What the five off-topic skills say about themselves (their own descriptions).
OFF_WORDS = ("horoscope", "fortune", "effect mapping", "business goals", "cross-functional",
             "product manager", "navigation pattern", "satellite constellations")

pytestmark = pytest.mark.skipif(not os.path.isdir(SOURCE), reason="no saved bot state to migrate (PUFFPAI_TEST_REVIEW_STATE)")


def _judge(job: str, thing: str) -> str:
    """The brain's yes/no: off-topic things are no; outside weather tools do
    not tell space news or events; an encyclopedia is not news."""
    low = thing.lower()
    if any(w in low for w in OFF_WORDS):
        return "no"
    earth_weather = "weather" in low and "space weather" not in low
    if job.startswith(("Stay updated", "Identify")) and (earth_weather or "encyclopedia" in low):
        return "no"
    return "yes"


def _digest(root: str) -> str:
    h = hashlib.sha256()
    for base, _dirs, names in sorted(os.walk(root)):
        for n in sorted(names):
            p = os.path.join(base, n)
            h.update(p[len(root):].encode())
            h.update(str(os.path.getsize(p)).encode())
    return h.hexdigest()


@pytest.fixture
def old_state(monkeypatch, tmp_path):
    dest = tmp_path / "state"
    shutil.copytree(SOURCE, dest, ignore=lambda d, names: [n for n in names if n in SKIP_DIRS
                                                           and os.path.samefile(d, SOURCE)])
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(dest))
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "data"))
    from puffpai.build import setup as setup_mod
    monkeypatch.setattr(setup_mod, "_home_set", lambda: False)
    from puffpai import deps
    monkeypatch.setattr(deps, "has_program", lambda name: False)
    return dest


def test_stargazerbot_migrates(old_state):
    before = _digest(str(old_state))
    source_before = _digest(SOURCE)
    build = migrate.migrate({"brain": ScriptedBrain(judge=_judge), "use_brain": True,
                             "embed": concept_embed})
    jobs = build["jobs"]
    assert [j["cap"] for j in jobs] == [
        "cap-stay-updated-on-new-space-news", "cap-identify-constellations-and-space-events",
        "cap-suggest-viewing-locations-and-times", "cap-provide-comprehensive-space-information"]
    assert len({j["page"] for j in jobs}) == 4
    dropped = {d["ref"]: d for d in build["dropped"]}
    assert OFF_TOPIC <= set(dropped), sorted(dropped)
    for ref in OFF_TOPIC:
        assert dropped[ref].get("remove"), "Not used keeps the entry that removes it"
    used = {r for j in jobs for r in j["ingredients"]}
    assert not (OFF_TOPIC & used)
    c_ingredients = {r for j in jobs if j["route"] == "C" for r in j["ingredients"]}
    assert FEEDS <= c_ingredients, (sorted(c_ingredients), [(j["name"], j["route"]) for j in jobs])
    # books and text skills never set a route here (no learn or ask job)
    for j in jobs:
        if j["pick"]["class"] in (S.TEXT, S.GUIDE):
            assert j["action"] in ("learn", "ask")
    # the bot's switches are kept as they are: an open card needs no switch row
    switch_rows = {r["service"]: r for r in build["setup"] if r["kind"] == "switch"}
    for ref in FEEDS & c_ingredients:
        row = switch_rows.get(f"{ref}:feed")
        assert row is None or row["done"]
    # nothing deleted, nothing but build.json written; the original untouched
    assert S.exists()
    os.remove(S.build_path())
    os.rmdir(os.path.dirname(S.build_path()))
    assert _digest(str(old_state)) == before
    assert _digest(SOURCE) == source_before


def test_ensure_runs_once(old_state):
    first = migrate.ensure({"use_brain": False, "embed": concept_embed})
    assert first.get("migrated") and len(first["jobs"]) == 4
    first["jobs"][0]["name"] = "Changed by the owner"
    S.save_build(first)
    again = migrate.ensure({"use_brain": False, "embed": concept_embed})
    assert again["jobs"][0]["name"] == "Changed by the owner"
