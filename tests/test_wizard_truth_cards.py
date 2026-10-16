"""The closing screen and the pages card tell the truth about what is missing.

A run once printed "Setup complete. Your bot is ready." above "6 things
are still missing", "5 checks ran and none failed" when one had passed, and a
telescope page that nothing stood behind. These tests hold the fixes for any
goal, not just the space bot's.
"""
from __future__ import annotations

import argparse

import pytest

from puffpai import build_abilities as ba
from puffpai import cli, prove
from puffpai.config import get_config
from puffpai.wizard import cards, loader, runner as wizard_runner

G1 = ("I want a space bot that knows everything about space, shows me cool "
      "space stuff, and works with my smart telescope")
G3 = "Help me keep my 2014 Honda Civic running - maintenance, recalls, how-tos"


@pytest.fixture
def steady(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    cfg = get_config(_force_reload=True)
    monkeypatch.setattr(cli, "installed_launchers", lambda *a, **k: None)
    monkeypatch.setattr(ba, "_PROVIDERS", [])
    monkeypatch.setattr(ba, "_manifests", lambda: [])
    yield cfg
    get_config(_force_reload=True)


def _module(pid):
    packages, refusals = loader.load_builtin_packages()
    assert refusals == []
    return loader.load_module(packages[pid]), packages[pid]


def _ctx(cfg, package, sections=None, shared=None):
    return wizard_runner.RunContext(
        args=argparse.Namespace(yes=True), cfg=cfg, non_interactive=True,
        shared=shared or {}, sections=sections or {}, package=package,
        show_once=None)


def _handoff(cfg, goal="", pages=(), ready=True):
    module, package = _module("handoff")
    sections = {"persona": {"goal": goal},
                "surfaces": {"surfaces": {"surfaces": list(pages)}}}
    ctx = _ctx(cfg, package, sections=sections,
               shared={"brain_ready": {"ready": ready}})
    proposal = module.propose(ctx)
    return cards.validate_card(proposal["card"]), proposal


def _field(card, fid):
    return {f["id"]: f for f in card["fields"]}.get(fid)


# ── the headline matches what is missing ─────────────────────────────────────

def test_ready_is_only_said_when_nothing_is_missing(steady):
    card, _ = _handoff(steady, goal=G3)
    assert _field(card, "verdict")["current"] == \
        "Setup complete. Your bot is ready."
    assert card["status"] == "ready"


def test_a_goal_thing_nothing_covers_takes_ready_off_the_headline(steady):
    card, proposal = _handoff(steady, goal=G1)
    verdict = _field(card, "verdict")["current"]
    assert "ready" not in verdict.lower().replace("already", "")
    assert verdict.startswith("Setup finished. Your bot can start, but 1 "
                              "thing is still missing")
    assert proposal["missing"] == [
        "Work with your smart telescope - nothing in this build does that "
        "yet. It can be added later."]
    assert _field(card, "missing_0")["label"] == "Not connected yet"
    assert card["status"] == "gap"
    assert "1 thing is still missing" in card["decided"]


def test_a_page_with_nothing_behind_it_is_listed_once(steady):
    card, proposal = _handoff(steady, goal=G1,
                              pages=["instruments.telescope",
                                     "knowledge.library"])
    assert len(proposal["missing"]) == 1          # the goal row says it
    card, proposal = _handoff(steady, goal="a sky watching bot",
                              pages=["instruments.telescope"])
    assert proposal["missing"] == [
        "Work with telescope and sky feeds - nothing in this build does that "
        "yet. It can be added later."]


def test_the_not_ready_sentences_are_unchanged(steady):
    card, _ = _handoff(steady, goal=G1, ready=False)
    assert _field(card, "verdict")["current"].startswith(
        "Setup finished, but it is not ready yet: the brain is missing.")


def test_the_missing_lines_are_plain(steady):
    _card, proposal = _handoff(steady, goal=G1, pages=["instruments.telescope"])
    for line in proposal["missing"]:
        for word in ("instruments.", "surface", "manifest", "door", "tier"):
            assert word not in line.lower()


# ── the proof line counts what really happened ───────────────────────────────

def test_the_proof_line_counts_passes_gaps_and_skips(steady):
    module, package = _module("handoff")
    record = {"checks": [{"result": prove.PASS}, {"result": prove.GAP},
                         {"result": prove.GAP},
                         {"result": prove.NOT_APPLICABLE},
                         {"result": prove.NOT_APPLICABLE}]}
    ctx = _ctx(steady, package, shared={"prove_record": record,
                                        "brain_ready": {"ready": True}})
    line = module._proof_line(ctx)
    assert line.startswith("Of 5 checks: 1 passed, 2 found something missing, "
                           "2 did not apply to this build.")
    assert "none failed" not in line


# ── the pages card ───────────────────────────────────────────────────────────

def _surfaces_card(cfg, goal):
    from puffpai import build_interview as bi
    module, package = _module("surfaces")
    proposal = bi.propose(goal)
    sections = {"persona": {"interview": {"goal": goal, "proposal": proposal}}}
    ctx = _ctx(cfg, package, sections=sections)
    return cards.validate_card(module.propose(ctx)["card"])


def test_the_pages_card_speaks_in_page_names_not_ids(steady):
    card = _surfaces_card(steady, "a space bot for my telescopes")
    assert card["title"] == "Pages your bot will have"
    shown = " ".join([card["decided"]] + [
        str(f.get("current")) for f in card["fields"] if f["kind"] == "info"])
    assert "Telescope and sky feeds" in shown
    assert "instruments.telescope" not in shown
    assert "surface" not in shown.lower()


def test_the_pages_card_says_which_pages_have_nothing_behind_them(steady):
    card = _surfaces_card(steady, "a space bot for my telescopes")
    missing = [f for f in card["fields"] if f["label"] == "Not connected yet"]
    assert missing and "Telescope and sky feeds" in missing[0]["current"]
    # The library page has real code behind it; it is not called a name only.
    assert not any("reference library" in f["current"] for f in missing)


# ── the values a card and the build summary print ────────────────────────────

def test_a_card_prints_what_was_picked_in_the_owners_words():
    from puffpai.wizard import render_cli
    pages = {"id": "allowlist", "kind": "multi_choice",
             "label": "Pages your bot will have",
             "options": [{"value": "instruments.telescope",
                          "label": "Telescope and sky feeds"},
                         {"value": "knowledge.library",
                          "label": "Offline reference library"}],
             "proposed": ["instruments.telescope", "knowledge.library"]}
    assert render_cli._shown(pages) == ("Telescope and sky feeds, "
                                        "Offline reference library")
    one = {"id": "net", "kind": "choice", "label": "While you build",
           "options": [{"value": "offline", "label": "Offline while building "
                        "-- no internet while you build"}],
           "proposed": "offline"}
    assert render_cli._shown(one) == "Offline while building"


def test_the_build_summary_names_pages_not_ids():
    from puffpai import build_manifest
    lines = build_manifest.manifest_summary_lines(
        {"surfaces": {"allowlist": ["instruments.telescope",
                                    "knowledge.library"]}})
    joined = "\n".join(lines)
    assert "instruments.telescope" not in joined
    assert "knowledge.library" not in joined
    assert "Pages it will have: Telescope and sky feeds" in joined
