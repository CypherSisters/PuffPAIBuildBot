"""Surfaces as a card: propose, apply, verify -- and the allowlist it writes.

The load-bearing test is :func:`test_apply_writes_what_the_old_step_wrote`:
spec F allows the wording to change in Phase 2, not the object. Today's
``cli._setup_surfaces_step`` is the control, run against the same interview.

The other one worth reading is
:func:`test_the_persona_surface_is_not_a_box_the_owner_can_untick`. The
persona always gets a rail entry, a home card and search, and no gate may
remove it -- so its surface is stated on the card and is not one of the
choices, and unticking everything else leaves it on the list.

Run:
    pytest tests/test_wizard_surfaces.py
"""
from __future__ import annotations

import argparse
import json
import os

import pytest

from puffpai import build_interview as bi, cli, persona_scaffold, setup_ops
from puffpai.config import get_config
from puffpai.wizard import cards, loader, runner as wizard_runner

GOAL = "a space bot for my telescopes and the night sky"


def _args(**kw):
    base = dict(yes=True, add_surface=None, drop_surface=None)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """One temp data dir per test, so a scaffolded persona is this test's."""
    monkeypatch.setattr(setup_ops, "list_models", lambda *a, **k: [])
    prev = {k: os.environ.get(k) for k in ("PUFFPAI_DATA_DIR", "PUFFPAI_STATE_DIR")}
    os.makedirs(tmp_path / "run", exist_ok=True)
    os.environ["PUFFPAI_DATA_DIR"] = str(tmp_path / "run")
    os.environ["PUFFPAI_STATE_DIR"] = str(tmp_path / "run")
    get_config(_force_reload=True)
    yield tmp_path
    for key, value in prev.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    get_config(_force_reload=True)


def _module():
    packages, refusals = loader.load_builtin_packages()
    assert refusals == []
    return loader.load_module(packages["surfaces"]), packages["surfaces"]


def _ctx(package, *, args=None, sections=None, shared=None,
         non_interactive=True):
    return wizard_runner.RunContext(
        args=args or _args(), cfg=get_config(), non_interactive=non_interactive,
        shared=shared or {}, sections=sections if sections is not None else {},
        package=package)


def _interview(persona=None):
    """What Purpose records for a goal, with an optional scaffolded persona."""
    proposal = bi.propose(GOAL)
    return {"goal": proposal["goal"],
            "selected": [e["id"] for e in proposal["flat"]],
            "proposal": proposal, "persona": persona}


def _with_persona(fresh):
    made = persona_scaffold.scaffold("infinity", name="Infinity", goal=GOAL,
                                     lists="targets")
    return _interview({"app": made["id"], "name": made["name"],
                       "surfaces": made["surfaces"]})


def _sections(interview):
    return {"persona": {"interview": interview}}


# -- propose ------------------------------------------------------------------

def test_propose_returns_a_card_that_validates_and_writes_nothing(fresh):
    module, package = _module()
    ctx = _ctx(package, sections=_sections(_interview()))
    proposal = module.propose(ctx)
    card = cards.validate_card(proposal["card"])

    assert card["skill"] == "surfaces" and card["section"] == "surfaces"
    assert card["title"] == "Pages your bot will have"
    assert ctx._sections.get("surfaces") is None, "propose wrote a section"
    assert [f for f in card["fields"] if f["kind"] == "secret_entry"] == []
    assert wizard_runner.json_safe(card) == card
    assert json.dumps(card)


def test_the_choices_are_every_surface_this_build_loads_in_the_owners_words(fresh):
    module, package = _module()
    card = cards.validate_card(module.propose(
        _ctx(package, sections=_sections(_interview())))["card"])
    field = {f["id"]: f for f in card["fields"]}["allowlist"]

    taxonomy = bi.surface_taxonomy()
    assert field["kind"] == "multi_choice"
    assert {o["value"] for o in field["options"]} <= set(taxonomy)
    assert field["current"], "a space goal proposes nothing"
    for option in field["options"]:
        assert option["label"] != option["value"], option
        assert taxonomy[option["value"]]["label"] in option["label"]


def test_a_goal_that_matched_nothing_still_draws_a_card(fresh):
    module, package = _module()
    card = cards.validate_card(module.propose(
        _ctx(package, sections=_sections({})))["card"])
    field = {f["id"]: f for f in card["fields"]}["allowlist"]
    assert field["current"] == []
    assert card["status"] == "ready"


def test_the_persona_surface_is_stated_and_is_not_offered_as_a_choice(fresh):
    module, package = _module()
    interview = _with_persona(fresh)
    proposal = module.propose(_ctx(package, sections=_sections(interview)))
    card = cards.validate_card(proposal["card"])
    surface = persona_scaffold.persona_surface("infinity")

    assert proposal["always"] == [surface]
    field = {f["id"]: f for f in card["fields"]}["allowlist"]
    assert surface not in {o["value"] for o in field["options"]}
    assert surface not in field["current"]
    info = [f for f in card["fields"] if f["label"] == "Always included"]
    assert info and "your bot's own page" in info[0]["current"].lower()
    assert surface not in info[0]["current"]      # plain words, never the id


# -- apply: today's step is the control ---------------------------------------

def _new(package, interview, answers, *, args=None):
    module, _ = _module()
    ctx = _ctx(package, args=args, sections=_sections(interview))
    ctx.proposal = module.propose(ctx)
    return module.apply(ctx, answers)


def test_apply_writes_what_the_old_step_wrote(fresh, capsys):
    module, package = _module()
    interview = _interview()
    new = _new(package, interview, {})["data"]["surfaces"]
    old = cli._setup_surfaces_step(_args(), interview, non_interactive=True)
    capsys.readouterr()
    assert new == old


def test_apply_writes_what_the_old_step_wrote_for_a_scaffolded_persona(fresh,
                                                                       capsys):
    module, package = _module()
    interview = _with_persona(fresh)
    new = _new(package, interview, {})["data"]["surfaces"]
    old = cli._setup_surfaces_step(_args(), interview, non_interactive=True)
    capsys.readouterr()
    assert new == old
    assert persona_scaffold.persona_surface("infinity") in new["surfaces"]


def test_apply_writes_what_the_old_step_wrote_with_no_goal(fresh, capsys):
    module, package = _module()
    new = _new(package, {}, {})["data"]["surfaces"]
    old = cli._setup_surfaces_step(_args(), {}, non_interactive=True)
    capsys.readouterr()
    assert new == old


def test_the_flags_land_the_same_way_they_always_did(fresh, capsys):
    module, package = _module()
    interview = _interview()
    args = _args(add_surface=["house.lights"], drop_surface=["instruments.telescope"])
    new = _new(package, interview, {}, args=args)["data"]["surfaces"]
    old = cli._setup_surfaces_step(args, interview, non_interactive=True)
    capsys.readouterr()
    assert new == old
    assert "house.lights" in new["surfaces"]
    assert "instruments.telescope" not in new["surfaces"]


def test_what_the_owner_unticks_comes_off_the_list(fresh, capsys):
    module, package = _module()
    interview = _interview()
    proposed = cli._setup_surfaces_step(_args(), interview, non_interactive=True)
    capsys.readouterr()
    keep = proposed["surfaces"][:1]
    written = _new(package, interview, {"allowlist": keep})["data"]["surfaces"]
    assert written["surfaces"] == sorted(keep)
    assert written["owner_edited"] is True


def test_what_the_owner_adds_has_to_be_a_surface_this_build_loads(fresh):
    module, package = _module()
    ctx = _ctx(package, sections=_sections(_interview()))
    ctx.proposal = module.propose(ctx)
    with pytest.raises(cards.CardError):
        cards.validate_answers(ctx.proposal["card"],
                               {"allowlist": ["house.nonexistent"]})


def test_the_persona_surface_is_not_a_box_the_owner_can_untick(fresh):
    module, package = _module()
    interview = _with_persona(fresh)
    written = _new(package, interview, {"allowlist": []})["data"]["surfaces"]
    assert written["surfaces"] == [persona_scaffold.persona_surface("infinity")]


# -- verify -------------------------------------------------------------------

def test_verify_checks_every_chosen_surface_against_the_taxonomy(fresh):
    module, package = _module()
    sections = {"surfaces": {"surfaces": {"surfaces": ["house.lights"]}}}
    verdict = module.verify(_ctx(package, sections=sections))
    assert verdict["status"] == "pass"
    assert "taxonomy" in verdict["evidence"]


def test_a_surface_this_build_cannot_load_is_a_gap_and_not_an_exception(fresh):
    module, package = _module()
    sections = {"surfaces": {"surfaces": {"surfaces": ["house.lights",
                                                       "nothing.at.all"]}}}
    verdict = module.verify(_ctx(package, sections=sections))
    assert verdict["status"] == "gap"
    assert verdict["gap"]["fix"]["id"] == "setup.rerun"
    assert "nothing.at.all" in verdict["gap"]["why"]


def test_an_empty_section_verifies_rather_than_raising(fresh):
    module, package = _module()
    verdict = module.verify(_ctx(package, sections={"surfaces": {}}))
    assert verdict["status"] == "pass"


# -- through the runner -------------------------------------------------------

def test_the_runner_walks_the_lifecycle_and_records_what_it_showed(fresh):
    interview = _interview()
    run = wizard_runner.Runner(
        args=_args(), cfg=get_config(), non_interactive=True,
        schedule=(wizard_runner.Step("surfaces.card", "surfaces",
                                     wizard_runner.V2_PART),),
        renderer=lambda card, **kw: {"allowlist": ["house.lights"]})
    run.sections["persona"] = {"interview": interview}
    run.run()

    written = run.sections["surfaces"]["surfaces"]
    assert written["surfaces"] == ["house.lights"]
    row = run.log[-1]
    assert row["outcome"] == "done"
    assert row["shown"]["title"] == "Pages your bot will have"
    assert row["changed"] == {"allowlist": ["house.lights"]}
    assert row["verified"]["status"] == "pass"
    assert run.stale == ["context", "prove", "handoff"]
