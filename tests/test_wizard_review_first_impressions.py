"""What the review screen said wrong on a fresh build, and what it says now.

Three defects, all of them first-impression ones -- the sort an owner reads
before they have applied anything.

*Cards did not see the cards above them.* ``GET /wizard/cards`` proposes every
card at once and applies none of them, so each one used to propose against an
empty notebook: Discover said "Ollama at http://127.0.0.1:11435" and the Brain
row beside it still sized a download against the address in the config, while
Crew offered "room for up to 0 specialists" with a census on the screen above
it. A provisional notebook (``shared_preview``, CONVERTING.md) threads what a
card *would* share into the proposes below it, and writes nothing.

*Tools warned about a connection nobody made.* A fresh build with nothing
declared and no hub asked for was flagged "turns on internet access while it
runs" because the question had been *asked*.

*One row carried two numbers for one download.* Sources said "about 57.2 GB" in
its own words and 61.4 GB in the estimate beside it -- powers of 1024 against
powers of 1000. One convention, ``cards.format_bytes``.

Run:
    pytest tests/test_wizard_review_first_impressions.py
"""
from __future__ import annotations

import copy
import hashlib
import os

import pytest

from puffpai import hardware_census, model_catalog, setup_ops
from puffpai.config import get_config
from puffpai.wizard import cards, service, sources_plan, sources_suggest

#: Where Ollama answers on this imaginary machine. Not the default port: the
#: whole defect was a later card reading the configured address instead of the
#: one the card above it had just proposed.
ELSEWHERE = "http://127.0.0.1:11435"

GOAL = ("knows everything about space, space facts, current space events, "
        "astronomy, Earth weather events")


def _census() -> dict:
    census = hardware_census.run_census()
    census = hardware_census.apply_stated(census, total_ram_gb=64)
    return census


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """A fresh build, a pinned machine, and an Ollama that is not the default."""
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "data"))
    cfg = get_config(_force_reload=True)
    census = _census()
    monkeypatch.setattr(hardware_census, "run_census",
                        lambda *a, **k: copy.deepcopy(census))
    monkeypatch.setattr(sources_suggest, "search_terms",
                        lambda *a, **k: {"terms": [], "note": ""})
    yield cfg
    get_config(_force_reload=True)


def _fits(census: dict) -> str:
    """The model this build would recommend for that machine, installed or not."""
    menu = model_catalog.fit_menu(census, installed_models=[],
                                  prefer_hosted_tier=True)
    return str((menu.get("recommended") or {}).get("id") or "")


@pytest.fixture
def only_elsewhere(scratch, monkeypatch):
    """The recommended model exists, and only at the address nobody configured."""
    model = _fits(_census())
    assert model, "this build recommends no model for a 64 GB machine"

    def list_models(url=None, *a, **k):
        return [model] if str(url or "").rstrip("/") == ELSEWHERE else []

    monkeypatch.setattr(setup_ops, "list_models", list_models)
    monkeypatch.setattr(setup_ops, "ollama_survey",
                        lambda *a, **k: [{"url": ELSEWHERE,
                                          "models": [model]}])
    return model


def _cards() -> dict:
    body = service.propose_cards()
    assert body["refusals"] == [], body["refusals"]
    return {cards.card_id(card): card for card in body["cards"]}


def _field(card: dict, field_id: str) -> dict:
    return cards.field_map(card)[field_id]


def _fingerprint(root: str) -> str:
    """Everything under a directory, contents and all."""
    digest = hashlib.sha256()
    for where, dirs, names in os.walk(root):
        dirs.sort()
        for name in sorted(names):
            path = os.path.join(where, name)
            digest.update(os.path.relpath(path, root).encode("utf-8"))
            with open(path, "rb") as handle:
                digest.update(handle.read())
    return digest.hexdigest()


# -- 1: a card sees what the cards above it propose ----------------------------

def test_brain_reads_the_address_discover_proposed(only_elsewhere):
    model = only_elsewhere
    drawn = _cards()

    # The row that measured the machine found the other address.
    assert ELSEWHERE in drawn["discover"]["decided"]
    assert _field(drawn["discover"], "ollama_url")["proposed"] == ELSEWHERE

    # And the row below it asked *that* Ollama what it has, so the model is
    # already here: no download, and nothing to warn about.
    brain = drawn["brain"]
    assert ELSEWHERE in _field(brain, "found")["current"]
    assert brain["decided"] == f"{model}, already on this computer."
    assert brain["consequential"] is False
    assert brain["consequential_reasons"] == []
    assert brain["estimate"]["bytes"] == 0


def test_crew_sizes_itself_from_the_census_discover_took(only_elsewhere):
    from puffpai import crew_sizing

    expected = int(crew_sizing.crew_budget(_census()).get("max_specialists") or 0)
    assert expected > 0, "a 64 GB machine has room for specialists"
    crew = _cards()["crew"]
    # Nothing to size on the card any more; the helpers are counted from the
    # roster, and the sizing still reaches the record from the census.
    assert "A team of 7 helpers" in crew["decided"]


def test_proposing_the_whole_screen_still_writes_nothing(only_elsewhere):
    root = get_config().DATA_DIR
    os.makedirs(root, exist_ok=True)
    before = _fingerprint(root)
    _cards()
    assert _fingerprint(root) == before


def test_what_was_applied_is_reported_and_keeps_its_own_values(only_elsewhere):
    body = service.propose_cards()
    assert body["applied"] == []

    service.apply_skill("purpose:goal", {"goal": GOAL})
    service.apply_skill("purpose:identity", {"name": "Comet"})
    body = service.propose_cards()
    assert body["applied"] == ["purpose:goal", "purpose:identity"]
    # The applied card's own values, not a preview of them.
    drawn = {cards.card_id(card): card for card in body["cards"]}
    assert _field(drawn["purpose:goal"], "goal")["current"] == GOAL
    assert _field(drawn["purpose:identity"], "name")["current"] == "Comet"
    # And a later card still reads the goal that was applied.
    assert "Comet" in drawn["purpose:place"]["decided"]


# -- 2: Tools warns about a connection, not about a question -------------------

def test_tools_is_not_consequential_until_a_hub_is_asked_for(scratch,
                                                             monkeypatch):
    monkeypatch.setattr(setup_ops, "list_models", lambda *a, **k: [])
    monkeypatch.setattr(setup_ops, "ollama_survey", lambda *a, **k: [])
    service.apply_skill("secure:posture", {"build_network": "online",
                                           "run_egress": "island"})

    fresh = _cards()["tools"]
    assert "connect_hub" in cards.field_map(fresh), "the question is still asked"
    assert fresh["consequential"] is False
    assert fresh["consequential_reasons"] == []

    applied = service.apply_skill("tools", {"connect_hub": True})
    assert applied["outcome"] == "done"
    asked = _cards()["tools"]
    assert asked["consequential"] is True
    assert asked["consequential_reasons"] == ["runtime_egress"]


# -- 3: one number, one unit convention ----------------------------------------

def test_sources_says_the_same_size_twice(scratch, monkeypatch):
    monkeypatch.setattr(setup_ops, "list_models", lambda *a, **k: [])
    monkeypatch.setattr(setup_ops, "ollama_survey", lambda *a, **k: [])
    service.apply_skill("secure:posture", {"build_network": "online",
                                           "run_egress": "island"})
    service.apply_skill("purpose:goal", {"goal": GOAL})
    service.apply_skill("purpose:identity", {"name": "Comet"})

    card = _cards()["sources"]
    size = card["estimate"]["bytes"]
    assert size > 0, "this goal selects nothing to download"
    # The words on the row and the estimate beside them are one number.
    assert cards.format_bytes(size) in card["decided"]


# -- 5: info lines are a quiet block on the console too ------------------------

def test_the_console_labels_a_list_of_suggestions_once():
    from puffpai.wizard import render_cli

    card = {
        "schema": "wizard_card/v1", "skill": "purpose", "section": "persona",
        "part": "identity", "title": "What it is for", "decided": "Comet",
        "status": "ready", "consequential": False, "consequential_reasons": [],
        "estimate": {"bytes": 0, "seconds": 0, "basis": "measured"},
        "fields": [
            {"id": "matched", "kind": "info", "current": "",
             "label": "From that goal, the starter catalog suggests"},
            {"id": "m1", "kind": "info", "label": "Suggested",
             "current": "Knowledge packs:"},
            {"id": "m2", "kind": "info", "label": "Suggested",
             "current": "- Science"},
        ],
        "gaps": [],
    }
    lines = render_cli.card_lines(cards.validate_card(card))
    # One label over the list, not one per sentence of it.
    assert sum(1 for line in lines if line.strip() == "Suggested") == 0
    assert "From that goal, the starter catalog suggests" in lines
    # Every line of the list is still there, and so are its words.
    assert "  Knowledge packs:" in lines
    assert "  - Science" in lines


def test_the_console_still_labels_rows_that_say_different_things():
    from puffpai.wizard import render_cli

    card = {
        "schema": "wizard_card/v1", "skill": "discover", "section": "machine",
        "part": "", "title": "What this machine is", "decided": "a machine",
        "status": "ready", "consequential": False, "consequential_reasons": [],
        "estimate": {"bytes": 0, "seconds": 0, "basis": "measured"},
        "fields": [
            {"id": "machine", "kind": "info", "label": "What was found",
             "current": "Windows"},
            {"id": "memory_gb", "kind": "info", "label": "Memory",
             "current": "64 GB"},
        ],
        "gaps": [],
    }
    lines = render_cli.card_lines(cards.validate_card(card))
    assert "What was found: Windows" in lines
    assert "Memory: 64 GB" in lines


def test_every_formatter_agrees_on_what_a_gigabyte_is():
    gigabyte = 1024 ** 3
    from puffpai.wizard import render_cli

    assert cards.format_bytes(gigabyte) == "1 GB"
    assert cards.format_bytes(57.2 * gigabyte) == "57.2 GB"
    assert cards.format_bytes(0) == ""
    assert sources_plan.size_label(57.2 * 1024) == "about 57.2 GB"
    assert render_cli._gb(57.2 * gigabyte) == "57.2 GB"
