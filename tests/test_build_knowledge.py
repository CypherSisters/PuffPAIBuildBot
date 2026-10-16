"""The build's one knowledge step (owner: "The agents also need to search for
publicly available and downloadable materials for the knowledge base").

Every build searches the knowledge sites (find_more.search, mocked here: no
network), lists the best few on the same one Yes, downloads the ticked ones
after the Yes (find_more.add, mocked), and /installed lists what arrived.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "build"))

from fakes import ScriptedBrain, concept_embed, job, live  # noqa: E402

from puffpai.build import knowledge, pipeline, schema as S, yes as yes_mod  # noqa: E402
from puffpai.security import consent_tickets  # noqa: E402

DEVICE = "device:knowledge12345678"
GOAL = "a world news bot that shows the news on a map"
JOBS = [job("World news map", "Show the latest world news on a map.", "show", "places",
            ["none"], ["news", "world"])]
CATALOG = [live("seed:gdelt", "GDELT", "World news events with places, updated every hour.",
                "api.gdeltproject.org")]
GB = 1024 ** 3


def _cand(ident, name, size, score, **kw):
    return {"id": ident, "name": name, "what_it_is": name, "size_bytes": size,
            "fetchable": True, "score": score, "license_plain": "Free to share",
            "source_catalog": "Project Gutenberg" if ident.startswith("gutenberg:") else "",
            **kw}


FOUND = [_cand("gutenberg:1", "A History of the World", 2_000_000, 120),
         _cand("wikipedia_en_world_2025", "Wikipedia: World", 5 * GB, 110),
         _cand("openstax:world", "World History", 30_000_000, 90),
         _cand("wikipedia_en_all", "Wikipedia", 100 * GB, 50, general=True)]


@pytest.fixture
def state(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "data"))
    consent_tickets.reset()
    from puffpai import deps
    monkeypatch.setattr(deps, "has_program", lambda name: False)
    monkeypatch.setattr(deps, "has_package", lambda kind, name: False)
    from puffpai.build import setup as setup_mod
    monkeypatch.setattr(setup_mod, "_home_set", lambda: False)
    asked: list[str] = []

    def search(words):
        asked.append(words)
        return {"candidates": [dict(c) for c in FOUND]}
    monkeypatch.setattr(knowledge, "SEARCH", search)
    monkeypatch.setattr(knowledge, "IN_BACKGROUND", False)
    yield asked
    consent_tickets.reset()


def _plan():
    ctx = {"brain": ScriptedBrain({GOAL: JOBS}, CATALOG), "use_brain": True,
           "embed": concept_embed, "catalog": lambda jobs, goal: [dict(c) for c in CATALOG],
           "check": None}
    return pipeline.plan(GOAL, ctx)


def test_the_knowledge_step_lists_finds_on_the_yes_card(state):
    build = _plan()
    assert state, "the knowledge sites were searched"
    items = build["knowledge"]
    assert [i["id"] for i in items][:3] == ["gutenberg:1", "wikipedia_en_world_2025",
                                            "openstax:world"]
    assert items[-1]["id"] == "wikipedia_en_all", "a general encyclopedia comes last"
    first = items[0]
    assert first["title"] == "A History of the World" and first["size_words"]
    assert first["site"] and first["consent"], "its consent text from find_more"
    assert not any(line.startswith("To add it") for line in first["consent"])
    # the ticked lines are part of what her Yes covers
    before = yes_mod.sha(build)
    S.save_build(build)
    got = knowledge.set_tick("openstax:world", False)
    assert yes_mod.sha(got) != before
    from puffpai.build import api
    assert [i["id"] for i in api.status_view()["knowledge"]] == [i["id"] for i in items]


def test_an_item_over_2_gb_starts_unticked(state):
    items = {i["id"]: i for i in _plan()["knowledge"]}
    assert items["gutenberg:1"]["ticked"] is True
    assert items["wikipedia_en_world_2025"]["ticked"] is False
    assert "GB" in items["wikipedia_en_world_2025"]["size_words"]
    got = knowledge.set_tick("wikipedia_en_world_2025", True)    # she can still tick it
    assert "wikipedia_en_world_2025" in knowledge.ticked(got)


def test_after_the_yes_only_ticked_items_are_added(state, monkeypatch):
    _plan()
    knowledge.set_tick("openstax:world", False)
    knowledge.set_tick("wikipedia_en_all", True)        # she ticked the big one
    calls = []

    def add(ident, *, size_acknowledged, via):
        calls.append((ident, size_acknowledged, via))
        if ident == "wikipedia_en_all":
            return {"ok": False, "reason": "The library did not answer. Try later."}
        return {"ok": True}
    monkeypatch.setattr(knowledge, "ADD", add)
    knowledge.start_downloads()
    assert calls == [], "nothing is downloaded before her Yes"
    yes_mod.give(DEVICE)
    knowledge.start_downloads()
    assert [c[0] for c in calls] == ["gutenberg:1", "wikipedia_en_all"]
    assert all(c[1] is True and c[2] == "build" for c in calls)
    by_id = {i["id"]: i for i in S.load_build()["knowledge"]}
    assert by_id["gutenberg:1"]["status"] == "added"
    assert by_id["wikipedia_en_all"]["status"] == "failed"
    assert by_id["wikipedia_en_all"]["reason"] == "The library did not answer."
    assert by_id["openstax:world"]["status"] == ""
    # /installed lists what arrived, as knowledge, ready
    from puffpai import installed
    rows = [r for r in installed.listing() if r["kind"] == "knowledge"]
    assert [(r["id"], r["status"]) for r in rows] == [("knowledge:gutenberg:1", "ready")]
    assert "A History of the World" in installed.prompt_block()


def test_a_site_error_never_fails_the_build(state, monkeypatch):
    def broken(words):
        raise TimeoutError("no answer")
    monkeypatch.setattr(knowledge, "SEARCH", broken)
    build = _plan()
    assert build["jobs"] and not build.get("knowledge")


def test_knowledge_alone_can_be_approved_when_every_job_found_nothing(state, monkeypatch):
    def nothing(jobs, goal):
        return []
    ctx = {"brain": ScriptedBrain({GOAL: JOBS}, []), "use_brain": True,
           "embed": concept_embed, "catalog": nothing, "check": None}
    build = pipeline.plan(GOAL, ctx)
    assert all(j["route"] == "D" for j in build["jobs"]) and build["knowledge"]
    calls = []
    monkeypatch.setattr(knowledge, "ADD", lambda ident, **kw: calls.append(ident) or {"ok": True})
    got = yes_mod.give(DEVICE)
    assert yes_mod.covers(got)
    knowledge.start_downloads()
    assert "gutenberg:1" in calls


def test_the_consent_sentence_keeps_every_title(state):
    build = _plan()
    titles = [i["title"] for i in build["knowledge"] if i["ticked"]]
    build["setup"] = [{"id": f"r{n}", "kind": "program", "label": "x" * 80} for n in range(20)]
    words = yes_mod.sentence(build)
    assert words.startswith("Build all of this? Add to the knowledge base: ")
    assert all(t in words for t in titles)
    assert len(words) <= yes_mod.SENTENCE_MAX
