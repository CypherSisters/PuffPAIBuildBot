"""Discover, the first converted skill: propose, apply, verify.

The machine is pinned the same way ``tests/test_wizard_equivalence.py`` pins
it -- one census, a stubbed model probe -- so nothing here depends on the box
the suite runs on.

The load-bearing test is :func:`test_apply_writes_what_the_two_old_steps_wrote`.
The design allows Discover's owner-visible wording to change in Phase 2; it does
not allow what it writes to change, and the two Phase 1 steps are the control.

Run:
    pytest tests/test_wizard_discover.py
"""
from __future__ import annotations

import argparse
import copy
import types

import os

import pytest

from puffpai import cli, hardware_census, setup_ops
from puffpai.config import get_config
from puffpai.wizard import cards, loader, runner as wizard_runner


#: An address an owner might type instead of the one that was probed.
TYPED_URL = "http://10.0.0.5:11434"


@pytest.fixture
def steady(monkeypatch, tmp_path):
    """One census, one answer from every Ollama probe, one data dir."""
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path))
    cfg = get_config(_force_reload=True)
    census = hardware_census.run_census()
    monkeypatch.setattr(hardware_census, "run_census",
                        lambda *a, **k: copy.deepcopy(census))
    monkeypatch.setattr(setup_ops, "list_models", lambda *a, **k: ["qwen2.5:7b"])
    monkeypatch.setattr(setup_ops, "ollama_survey", lambda *a, **k: [])
    yield cfg
    get_config(_force_reload=True)


def _args(**kw):
    return argparse.Namespace(yes=True, stated_ram=None, ollama_url=None, **kw)


def _module():
    packages, refusals = loader.load_builtin_packages()
    assert refusals == []
    return loader.load_module(packages["discover"]), packages["discover"]


def _ctx(cfg, package, sections=None, shared=None):
    return wizard_runner.RunContext(
        args=_args(), cfg=cfg, non_interactive=True, shared=shared or {},
        sections=sections if sections is not None else {}, package=package)


# -- propose ------------------------------------------------------------------

def test_propose_returns_a_card_that_validates_and_writes_nothing(steady):
    module, package = _module()
    ctx = _ctx(steady, package)
    proposal = module.propose(ctx)
    card = cards.validate_card(proposal["card"])

    assert card["skill"] == "discover" and card["section"] == "machine"
    assert card["title"] == "This computer"
    assert {f["id"] for f in card["fields"]} >= {"machine", "memory_gb",
                                                 "ollama_url"}
    assert ctx._sections == {}, "propose wrote a section"
    assert card["estimate"]["bytes"] == 0


def test_propose_offers_the_addresses_it_looked_at(steady, monkeypatch):
    monkeypatch.setattr(setup_ops, "list_models", lambda *a, **k: None)
    monkeypatch.setattr(setup_ops, "ollama_survey", lambda *a, **k: [
        {"url": "http://127.0.0.1:11434", "models": []},
        {"url": "http://127.0.0.1:11435", "models": ["qwen2.5:7b"]}])
    module, package = _module()
    card = cards.validate_card(module.propose(_ctx(steady, package))["card"])
    field = {f["id"]: f for f in card["fields"]}["ollama_url"]
    offered = {option["value"] for option in field["options"]}
    assert "http://127.0.0.1:11435" in offered
    assert field["free_text"] and field["pattern"]


# -- apply --------------------------------------------------------------------

def test_apply_writes_what_the_two_old_steps_wrote(steady, capsys):
    module, package = _module()
    ctx = _ctx(steady, package)
    ctx.proposal = module.propose(ctx)
    new = module.apply(ctx, {})

    old_census = cli._setup_hardware_step(_args(), non_interactive=True)
    old_url, old_models = cli._setup_ollama_url_step(
        _args(), steady, non_interactive=True, posture={})
    capsys.readouterr()

    assert new["section"] == "machine"
    assert new["data"]["census"] == old_census
    assert new["data"]["ollama_url"] == old_url
    assert new["data"]["installed_models"] == list(old_models or [])
    assert new["shared"]["census"] == old_census


def test_the_memory_figure_is_shown_and_not_asked_for(steady):
    """Retyping what the census just measured is not a question.

    ``--stated-ram`` is still the way to correct detection, and it goes through
    the same ``hardware_census.apply_stated``; the card reports the number and
    says where it came from.
    """
    module, package = _module()
    card = cards.validate_card(module.propose(_ctx(steady, package))["card"])
    field = cards.field_map(card)["memory_gb"]
    assert field["kind"] == "info"
    assert "measured on this computer" in field["current"]
    with pytest.raises(cards.CardError):
        cards.validate_answers(card, {"memory_gb": 12})

    ctx = _ctx(steady, package)
    ctx.args.stated_ram = 12
    ctx.proposal = module.propose(ctx)
    stated = cards.field_map(ctx.proposal["card"])["memory_gb"]
    assert "you said this" in stated["current"]
    written = module.apply(ctx, {})["data"]["census"]
    assert written["memory"]["total_gb"] == 12
    assert written["memory"]["source"] == "stated"


def test_a_typed_address_is_the_one_that_is_written(steady):
    module, package = _module()
    ctx = _ctx(steady, package)
    ctx.proposal = module.propose(ctx)
    data = module.apply(ctx, {"ollama_url": "http://10.0.0.5:11434"})["data"]
    assert data["ollama_url"] == "http://10.0.0.5:11434"


def test_an_address_the_posture_forbids_is_refused_without_a_keyboard(steady):
    module, package = _module()
    ctx = _ctx(steady, package, shared={"posture": {"run_egress": "sealed"}})
    ctx.proposal = module.propose(ctx)
    with pytest.raises(cli.SetupRefused):
        module.apply(ctx, {"ollama_url": "http://10.0.0.5:11434"})


def test_an_address_the_posture_forbids_becomes_a_gap_with_a_keyboard(steady):
    module, package = _module()
    ctx = _ctx(steady, package, shared={"posture": {"run_egress": "sealed"}})
    ctx.non_interactive = False
    ctx.proposal = module.propose(ctx)
    result = module.apply(ctx, {"ollama_url": "http://10.0.0.5:11434"})
    assert result["gap"]["skill"] == "discover"
    assert result["data"]["ollama_url"] != "http://10.0.0.5:11434"


# -- verify -------------------------------------------------------------------

def test_verify_observes_the_address_rather_than_the_proposal(steady):
    module, package = _module()
    sections = {"machine": {"ollama_url": "http://127.0.0.1:11434",
                            "census": {}}}
    verdict = module.verify(_ctx(steady, package, sections=sections))
    assert verdict["status"] == "pass"
    assert "answered" in verdict["evidence"]


def test_an_unreachable_ollama_is_a_gap_and_not_a_failure(steady, monkeypatch):
    monkeypatch.setattr(setup_ops, "list_models", lambda *a, **k: None)
    module, package = _module()
    sections = {"machine": {"ollama_url": "http://127.0.0.1:11434",
                            "census": {}}}
    verdict = module.verify(_ctx(steady, package, sections=sections))
    assert verdict["status"] == "gap"
    assert verdict["gap"]["fix"]["id"] == "setup.rerun"


@pytest.mark.skipif(os.name != "nt", reason="a drive-letter path")
def test_a_disk_that_is_not_there_is_a_gap(steady):
    module, package = _module()
    sections = {"machine": {"ollama_url": "http://127.0.0.1:11434",
                            "census": {"disk": {"path": "Q:\\nowhere-at-all"}}}}
    verdict = module.verify(_ctx(steady, package, sections=sections))
    assert verdict["status"] == "gap"
    assert "disk" in verdict["gap"]["what"].lower()


# -- through the runner -------------------------------------------------------

def test_the_runner_walks_the_lifecycle_and_records_what_it_showed(steady,
                                                                   tmp_path):
    cfg = types.SimpleNamespace(CONFIG_DIR=str(tmp_path / "config"),
                                ollama=steady.ollama, _raw=getattr(steady, "_raw", {}),
                                DATA_DIR=steady.DATA_DIR)
    run = wizard_runner.Runner(
        args=_args(), cfg=cfg, non_interactive=True,
        schedule=(wizard_runner.Step("discover.card", "discover",
                                     wizard_runner.V2_PART),),
        renderer=lambda card, **kw: {"ollama_url": TYPED_URL})
    run.run()

    assert run.sections["machine"]["census"]["memory"]["total_gb"]
    assert run.sections["machine"]["ollama_url"] == TYPED_URL
    row = run.log[-1]
    assert row["outcome"] == "done"
    assert row["shown"]["title"] == "This computer"
    assert row["changed"] == {"ollama_url": TYPED_URL}
    assert row["verified"]["status"] == "pass"
    assert run.stale[:1] == ["integrate"] and run.stale[-2:] == ["prove", "handoff"]
