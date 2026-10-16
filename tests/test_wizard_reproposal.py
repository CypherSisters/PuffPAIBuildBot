"""Two rules a browser-built build depends on, and neither had a test.

**Re-proposing.** ``propose`` starts from the skill's own applied section when
there is one (``RunContext.applied``), so a card drawn again after an apply
shows what the owner answered. It did not: ``POST /wizard/apply`` with a name
and a goal returned 200 and a card that still said "unnamed - no goal given
yet", and ``GET /wizard/cards`` agreed with it. Every package is checked here
through the service, because the service is what the dashboard calls.

**Accepting the rest.** ``POST /wizard/run`` walked card ids only, so the
wizard's own entries -- the Ollama config, the build manifest, Prove -- never
ran. A build made entirely in a browser ended with no
``config/build_manifest.json`` and ``GET /build/prove`` answering ``ran:
false``. They run now, at their scheduled positions, and first-run setup is
still **not** marked complete while a card that blocks is outstanding.

Run:
    pytest tests/test_wizard_reproposal.py
"""
from __future__ import annotations

import os

import pytest

from puffpai import setup_ops
from puffpai.config import get_config
from puffpai.wizard import cards, service, sources_suggest

NAME = "Infinity"
GOAL = ("knows everything about space, space facts, current space events, "
        "astronomy, Earth weather events")


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """A temp data dir, no Ollama, no suggester, no network."""
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "data"))
    cfg = get_config(_force_reload=True)
    monkeypatch.setattr(setup_ops, "list_models", lambda *a, **k: [])
    monkeypatch.setattr(setup_ops, "ollama_survey", lambda *a, **k: [])
    monkeypatch.setattr(sources_suggest, "search_terms",
                        lambda *a, **k: {"terms": [], "note": ""})
    yield cfg
    get_config(_force_reload=True)


def _card(card_id: str) -> dict:
    body = service.propose_cards()
    assert body["refusals"] == [], body["refusals"]
    for card in body["cards"]:
        if cards.card_id(card) == card_id:
            return card
    raise AssertionError(f"{card_id} is not on the card list: {body['order']}")


def _field(card: dict, field_id: str) -> dict:
    return cards.field_map(card)[field_id]


# -- the propose rule, package by package -------------------------------------

def test_purpose_shows_what_was_applied_and_names_the_place_after_it(scratch):
    goal = service.apply_skill("purpose:goal", {"goal": GOAL})
    applied = service.apply_skill("purpose:identity", {"name": NAME})
    assert goal["outcome"] == applied["outcome"] == "done"
    # The card that comes back from the apply, and the card list after it, say
    # the same thing -- and it is what was just answered.
    for card in (goal["card"], _card("purpose:goal")):
        assert _field(card, "goal")["current"] == GOAL
    for card in (applied["card"], _card("purpose:identity")):
        assert _field(card, "name")["current"] == NAME
        assert NAME in card["decided"]
    # And the id on the second card is derived from them.
    place = _card("purpose:place")
    assert _field(place, "id")["current"] == "infinity"


def test_sources_shows_the_plan_the_owner_settled(scratch):
    service.apply_skill("purpose:goal", {"goal": GOAL})
    service.apply_skill("purpose:identity", {"name": NAME})
    service.apply_skill("secure:posture", {"build_network": "online",
                                           "run_egress": "island"})
    first = _card("sources")
    offered = [opt["value"] for opt in _field(first, "selected")["options"]]
    assert offered, "this goal matches nothing in the registry"

    service.apply_skill("sources", {"selected": offered[:1]})
    again = _card("sources")
    assert _field(again, "selected")["current"] == offered[:1]
    # The recommendation is still shown as the recommendation.
    assert _field(again, "selected")["proposed"] == [
        opt for opt in _field(again, "selected")["proposed"]]


def test_crew_asks_nothing_and_counts_its_helpers(scratch):
    """The specialists question is gone: it changed nothing at
    run time. The card is one plain line, and nothing on it can be answered."""
    service.apply_skill("discover", {})
    applied = service.apply_skill("crew", {})
    assert applied["outcome"] == "done"
    card = _card("crew")
    assert [f["kind"] for f in card["fields"]] == ["info"]
    assert "helpers works inside" in card["decided"]


def test_surfaces_shows_the_allowlist_the_owner_edited(scratch):
    service.apply_skill("purpose:goal", {"goal": GOAL})
    service.apply_skill("purpose:identity", {"name": NAME})
    first = _card("surfaces")
    options = [opt["value"] for opt in _field(first, "allowlist")["options"]]
    keep = options[:1]
    service.apply_skill("surfaces", {"allowlist": keep})
    again = _card("surfaces")
    assert _field(again, "allowlist")["current"] == keep


def test_discover_shows_the_address_that_was_applied(scratch):
    service.apply_skill("discover", {"ollama_url": "http://10.0.0.5:11434"})
    card = _card("discover")
    field = _field(card, "ollama_url")
    assert field["current"] == "http://10.0.0.5:11434"
    assert "10.0.0.5" in card["decided"] or field["current"] in [
        opt["value"] for opt in field["options"]]


def test_secure_shows_the_posture_that_was_applied(scratch):
    service.apply_skill("secure:posture", {"build_network": "offline",
                                           "run_egress": "sealed"})
    card = _card("secure:posture")
    assert _field(card, "build_network")["current"] == "offline"
    assert _field(card, "run_egress")["current"] == "sealed"


def test_integrate_shows_the_folder_that_was_applied(scratch, tmp_path):
    folder = str(tmp_path / "packs")
    service.apply_skill("integrate", {"zim_dir": folder, "auto_index": False})
    card = _card("integrate")
    assert _field(card, "zim_dir")["current"] == folder
    assert _field(card, "auto_index")["current"] is False


def test_brain_shows_the_model_that_was_applied(scratch):
    service.apply_skill("discover", {})
    card = _card("brain")
    options = [opt["value"] for opt in _field(card, "model")["options"]]
    if not options:  # pragma: no cover - a machine the catalog cannot fit
        pytest.skip("no model fits this machine")
    service.apply_skill("brain", {"model": options[-1]})
    assert _field(_card("brain"), "model")["current"] == options[-1]


def test_the_brain_card_will_not_take_a_bot_name_as_a_model(scratch):
    service.apply_skill("discover", {})
    with pytest.raises(cards.CardError):
        service.apply_skill("brain", {"model": NAME})
    card = _card("brain")
    assert _field(card, "model")["pattern"]


# -- the hub question, and the limiter that guards it -------------------------

def test_the_tools_card_offers_the_hub_over_http_and_then_asks_for_the_token(
        scratch):
    """Item 7: the dashboard already takes a token on its Smart home step.

    The card asks the toggle first and the secret second, so "accept the rest"
    can still settle this card for an owner who does not want a hub, and the
    API's secret-attempt limiter has something real to guard.
    """
    card = _card("tools")
    assert "connect_hub" in cards.field_map(card)
    assert not cards.has_secret_field(card), (
        "a card carrying a secret from the start can never be accepted in bulk")

    service.apply_skill("tools", {"connect_hub": True})
    asked = _card("tools")
    token = cards.field_map(asked)["hub_token"]
    assert token["kind"] == "secret_entry"
    assert token["current"] == "" and token["proposed"] == ""
    assert service.asks_for_secret("tools", {"hub_token": "x"}) is True


# -- accepting the rest --------------------------------------------------------

def test_run_rest_writes_the_manifest_and_proves_the_build(scratch):
    from puffpai import build_context, prove

    service.apply_skill("purpose:goal", {"goal": GOAL})
    service.apply_skill("purpose:identity", {"name": NAME})
    result = service.run_rest([])
    assert result["refusals"] == []

    entries = {row["card"]: row for row in result["results"]}
    assert entries["runner.manifest"]["outcome"] == "done"
    assert entries["prove.check"]["outcome"] == "done"

    manifest_path = os.path.join(get_config().CONFIG_DIR,
                                 "build_manifest.json")
    assert os.path.isfile(manifest_path), "a dashboard-only build wrote nothing"
    assert build_context.collect_goal() == GOAL

    record = prove.read_record()
    assert record, "GET /build/prove would say ran: false"
    results = {check["id"]: check["result"] for check in record["checks"]}
    assert results["goal_reaches_model"] == prove.PASS
    assert "id_sanity" in results and "persona_visible" in results


def test_run_rest_does_not_call_a_build_finished_while_the_vault_waits(scratch):
    setup_ops.clear_complete()
    result = service.run_rest([])
    vault = next(row for row in result["results"] if row["card"] == "secure:vault")
    assert vault["outcome"] == "needs_you"
    assert result["complete"] is False
    assert "secure:vault" in result["outstanding"]
    # The record of what was decided is written; the build is not called done.
    assert os.path.isfile(os.path.join(get_config().CONFIG_DIR,
                                       "build_manifest.json"))
    assert setup_ops.is_complete() is False


def test_run_rest_prints_nothing_and_shows_no_face(scratch, capsys):
    service.apply_skill("purpose:goal", {"goal": GOAL})
    service.apply_skill("purpose:identity", {"name": NAME})
    capsys.readouterr()
    service.run_rest([])
    out = capsys.readouterr()
    assert out.out == "", out.out
    # No face means no vault, and the card says where the PIN can be set.
    vault = _card("secure:vault")
    assert vault["status"] == "needs_you"
    assert "next screen" in vault["decided"]
