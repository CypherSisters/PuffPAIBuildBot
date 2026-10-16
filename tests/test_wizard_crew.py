"""Agents (package id ``crew``), converted: propose, apply, verify.

The load-bearing test is :func:`test_apply_writes_what_the_old_step_wrote`: the
card may say anything spec F allows, and what it *writes* is the old step's
output for the same census.

The other one worth naming is
:func:`test_the_card_states_the_counted_agent_count`. The count is settled (seven
master agents), so the card states the count -- and
the test holds that the number is ``len(registry.ROSTER)`` counted on this
machine, not a literal anyone can drift.

Run:
    pytest tests/test_wizard_crew.py
"""
from __future__ import annotations

import argparse
import copy
import re
import types

import pytest

from puffpai import cli, crew_sizing, hardware_census
from puffpai.config import get_config
from puffpai.wizard import cards, loader, runner as wizard_runner


@pytest.fixture
def steady(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    cfg = get_config(_force_reload=True)
    census = hardware_census.run_census()
    monkeypatch.setattr(hardware_census, "run_census",
                        lambda *a, **k: copy.deepcopy(census))
    yield cfg
    get_config(_force_reload=True)


def _args(**kw):
    return argparse.Namespace(yes=True, **kw)


def _module():
    packages, refusals = loader.load_builtin_packages()
    assert refusals == []
    return loader.load_module(packages["crew"]), packages["crew"]


def _ctx(cfg, package, sections=None, shared=None):
    return wizard_runner.RunContext(
        args=_args(), cfg=cfg, non_interactive=True,
        shared=shared if shared is not None else {"census": hardware_census.run_census()},
        sections=sections if sections is not None else {}, package=package)


def _card(ctx, module):
    ctx.proposal = module.propose(ctx)
    return cards.validate_card(ctx.proposal["card"])


# -- propose ------------------------------------------------------------------

def test_propose_returns_a_card_that_validates_and_writes_nothing(steady):
    module, package = _module()
    ctx = _ctx(steady, package)
    card = _card(ctx, module)

    assert card["skill"] == "crew" and card["section"] == "crew"
    assert card["title"] == "Its helpers"
    # No specialists question any more: the number had no effect
    # on the running bot. The card is one plain line and asks nothing.
    assert {f["id"] for f in card["fields"]} == {"team"}
    assert all(f["kind"] == "info" for f in card["fields"])
    assert ctx._sections == {}, "propose wrote a section"


def test_the_card_names_no_agent_the_builder_has_not_met_yet(steady):
    """The helpers are met on the Agent Team page, by name, not in the wizard.

    Drawbridge is an agent's name; the plain-words rule allows it on the Agent
    Team page only, so the wizard's card says how many and where to meet them.
    """
    module, package = _module()
    card = _card(_ctx(steady, package), module)
    text = " ".join(f"{f['label']} {f.get('current') or ''}"
                    for f in card["fields"])
    assert "Drawbridge" not in text and "Sentinel" not in text
    assert "Agent Team page" in text


def test_the_card_states_the_counted_agent_count(steady):
    from puffpai.agents import registry

    module, package = _module()
    card = _card(_ctx(steady, package), module)

    # The roster in this tree is seven; the card's number is that roster's
    # length, counted, so adding an eighth agent moves the card with it.
    assert len(registry.ROSTER) == 7
    match = re.match(r"A team of (\d+) helpers works inside ", card["decided"])
    assert match, card["decided"]
    assert int(match.group(1)) == len(registry.ROSTER) == 7


def test_the_card_shows_no_autonomy_ceiling_because_this_tree_has_none(steady):
    module, package = _module()
    card = _card(_ctx(steady, package), module)
    text = " ".join(f"{f['label']} {f.get('current') or ''}"
                    for f in card["fields"]).lower()
    assert "ceiling" not in text and "autonomy" not in text


def test_an_unreadable_roster_is_a_gap_on_the_card_not_a_crash(steady,
                                                               monkeypatch):
    module, package = _module()
    monkeypatch.setattr(module, "_roster", lambda: [])
    card = _card(_ctx(steady, package), module)
    assert card["status"] == "gap"
    assert "could not be read" in card["decided"]


# -- apply --------------------------------------------------------------------

def test_apply_writes_what_the_old_step_wrote(steady, capsys):
    module, package = _module()
    census = hardware_census.run_census()
    ctx = _ctx(steady, package, shared={"census": census})
    ctx.proposal = module.propose(ctx)
    new = module.apply(ctx, {})

    old = cli._setup_crew_step(census)
    capsys.readouterr()

    assert new["section"] == "crew"
    assert new["data"]["sizing"] == old
    assert new["shared"]["crew"] == old


def test_a_lowered_figure_is_still_recorded_as_the_owners(steady):
    """The card no longer asks, but an answer that arrives (an old browser, a
    script) is still recorded honestly as the owner's."""
    module, package = _module()
    ctx = _ctx(steady, package)
    ctx.proposal = module.propose(ctx)
    budget = ctx.proposal["budget"]
    if not budget.get("max_specialists"):
        pytest.skip("this machine's class carries no specialists to give up")
    written = module.apply(ctx, {"specialists": 0})["data"]["sizing"]
    assert written["max_specialists"] == 0
    assert written["basis"] == "owner-stated"


def test_apply_records_who_owns_what(steady):
    module, package = _module()
    ctx = _ctx(steady, package)
    ctx.proposal = module.propose(ctx)
    ownership = module.apply(ctx, {})["data"]["ownership"]
    assert {row["agent"] for row in ownership} == {
        agent["name"] for agent in module._roster()}
    assert all(row["area"] for row in ownership)


# -- verify -------------------------------------------------------------------

def test_verify_observes_the_live_roster(steady):
    module, package = _module()
    sections = {"crew": {"ownership": [{"agent": "Drawbridge",
                                        "area": "Privacy and security"}]}}
    verdict = module.verify(_ctx(steady, package, sections=sections))
    assert verdict["status"] == "pass"


def test_an_agent_that_is_not_on_the_roster_is_a_gap(steady):
    module, package = _module()
    sections = {"crew": {"ownership": [{"agent": "Nobody", "area": "nothing"}]}}
    verdict = module.verify(_ctx(steady, package, sections=sections))
    assert verdict["status"] == "gap"
    assert verdict["gap"]["fix"]["id"] == "setup.rerun"


def test_an_unreadable_roster_verifies_as_a_gap_not_an_exception(steady,
                                                                 monkeypatch):
    module, package = _module()
    monkeypatch.setattr(module, "_roster", lambda: [])
    verdict = module.verify(_ctx(steady, package, sections={"crew": {}}))
    assert verdict["status"] == "gap"


# -- through the runner -------------------------------------------------------

def test_the_runner_walks_the_lifecycle(steady, tmp_path):
    cfg = types.SimpleNamespace(CONFIG_DIR=str(tmp_path / "config"),
                                DATA_DIR=steady.DATA_DIR)
    run = wizard_runner.Runner(
        args=_args(), cfg=cfg, non_interactive=True,
        schedule=(wizard_runner.Step("crew.card", "crew",
                                     wizard_runner.V2_PART),),
        renderer=lambda card, **kw: {})
    run.shared["census"] = hardware_census.run_census()
    run.run()

    assert run.sections["crew"]["sizing"] == crew_sizing.crew_budget(
        run.shared["census"])
    row = run.log[-1]
    assert row["outcome"] == "done"
    assert row["shown"]["title"] == "Its helpers"
    assert row["verified"]["status"] == "pass"
