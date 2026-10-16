"""Tools, converted: the integrations question plus the Tools v1 list.

Two things are being held here at once.

*The question is the same one.* ``test_apply_writes_what_the_old_step_wrote``
runs ``cli._setup_ha_step`` with scripted stdin in a temp state directory as
the control, and asserts the converted skill wrote the same config file with
the same contents for the same answers.

*The token is nowhere it should not be.* The card carries no value for it, the
answers the runner logs carry "(entered, not recorded)", and the token string
does not appear anywhere in ``wizard_run.json`` after a full lifecycle.

Run:
    pytest tests/test_wizard_tools.py
"""
from __future__ import annotations

import argparse
import io
import json
import types

import pytest

from puffpai import cli, setup_ops
from puffpai.config import get_config
from puffpai.wizard import cards, loader, runner as wizard_runner

TOKEN = "eyJ-not-a-real-token-0123456789-abcdefghijklmnop"
HUB = "http://192.168.1.50:8123"


@pytest.fixture
def steady(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    from puffpai.security import lan_posture
    cfg = get_config(_force_reload=True)
    # This run declared 'island': a home hub is reachable, the wider internet
    # is not. Without it a fresh config dir means "no manifest", which
    # write_path_posture reads as sealed -- correct, and not the posture the
    # integrations question is ever asked under.
    lan_posture.set_active_posture("island")
    yield cfg
    lan_posture.clear_active_posture()
    get_config(_force_reload=True)


def _args(**kw):
    kw.setdefault("skip_ha", False)
    kw.setdefault("ha_url", None)
    kw.setdefault("ha_token", None)
    return argparse.Namespace(yes=True, **kw)


def _module():
    packages, refusals = loader.load_builtin_packages()
    assert refusals == []
    return loader.load_module(packages["tools"]), packages["tools"]


def _ctx(cfg, package, *, sections=None, shared=None, non_interactive=True,
         args=None):
    return wizard_runner.RunContext(
        args=args or _args(), cfg=cfg, non_interactive=non_interactive,
        shared=shared if shared is not None else {},
        sections=sections if sections is not None else {}, package=package)


def _goal_sections(*ids):
    flat = [{"id": item, "label": item, "kind": item.split(":", 1)[0]}
            for item in ids]
    return {"persona": {"interview": {"goal": "a goal",
                                      "proposal": {"flat": flat}}}}


# -- propose ------------------------------------------------------------------

def test_propose_returns_a_card_that_validates(steady):
    module, package = _module()
    ctx = _ctx(steady, package, sections=_goal_sections("integration:home_assistant"))
    card = cards.validate_card(module.propose(ctx)["card"])

    assert card["skill"] == "tools" and card["section"] == "tools"
    assert card["title"] == "What it can reach for"
    assert ctx._sections == _goal_sections("integration:home_assistant"), (
        "propose changed a section")


def test_the_declared_list_marks_a_hub_that_is_not_configured_as_door_shut(steady):
    module, package = _module()
    ctx = _ctx(steady, package,
               sections=_goal_sections("integration:home_assistant"))
    rows = {row["id"]: row for row in module.propose(ctx)["rows"]}
    assert rows["integration:home_assistant"]["status"] == module.DOOR_SHUT


def test_a_keyed_api_is_declared_with_no_key_path_and_is_never_enabled(steady):
    module, package = _module()
    ctx = _ctx(steady, package, sections=_goal_sections("integration:media"))
    proposal = module.propose(ctx)
    rows = {row["id"]: row for row in proposal["rows"]}
    assert rows["integration:media"]["status"] == module.NO_KEY

    card = cards.validate_card(proposal["card"])
    kinds = {field["kind"] for field in card["fields"]}
    assert "toggle" not in kinds or not any(
        "media" in field["id"] for field in card["fields"]
        if field["kind"] == "toggle"), "a keyed API was made switchable"


def test_a_tool_nothing_here_does_is_said_plainly(steady):
    module, package = _module()
    ctx = _ctx(steady, package, sections=_goal_sections("integration:telescope"))
    rows = module.propose(ctx)["rows"]
    assert rows[0]["status"] == module.NOTHING


def test_an_installed_app_is_available(steady):
    module, package = _module()
    ctx = _ctx(steady, package, sections=_goal_sections("app:calendar"))
    rows = module.propose(ctx)["rows"]
    assert rows[0]["status"] == module.AVAILABLE


def test_the_token_field_carries_no_value_ever(steady):
    module, package = _module()
    ctx = _ctx(steady, package, non_interactive=False)
    card = cards.validate_card(module.propose(ctx)["card"])
    token = {field["id"]: field for field in card["fields"]}["hub_token"]
    assert token["kind"] == "secret_entry"
    assert token["current"] == "" and token["proposed"] == ""
    assert TOKEN not in json.dumps(card)


def test_a_sealed_build_is_not_offered_the_hub(steady):
    module, package = _module()
    ctx = _ctx(steady, package, non_interactive=False,
               shared={"gates": {"may_offer_lan_integrations": False}})
    card = cards.validate_card(module.propose(ctx)["card"])
    ids = {field["id"] for field in card["fields"]}
    assert "hub_token" not in ids and "sealed" in ids


# -- apply --------------------------------------------------------------------

def test_apply_writes_what_the_old_step_wrote(steady, monkeypatch, capsys):
    module, package = _module()
    ctx = _ctx(steady, package, non_interactive=False)
    ctx.proposal = module.propose(ctx)
    new = module.apply(ctx, {"connect_hub": True, "hub_url": HUB,
                             "hub_token": TOKEN})
    new_config = json.load(open(new["shared"]["ha_path"], encoding="utf-8"))

    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(f"y\n{HUB}\n{TOKEN}\n"))
    old_path = cli._setup_ha_step(_args(), {}, non_interactive=False,
                                  posture={})
    capsys.readouterr()
    old_config = json.load(open(old_path, encoding="utf-8"))

    assert new["shared"]["ha_path"] == old_path
    assert new_config == old_config
    assert new["data"]["home_assistant_configured"] is True


def test_saying_no_writes_nothing_just_like_the_old_step(steady, monkeypatch,
                                                         capsys):
    module, package = _module()
    ctx = _ctx(steady, package, non_interactive=False)
    ctx.proposal = module.propose(ctx)
    new = module.apply(ctx, {"connect_hub": False})

    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("n\n"))
    old_path = cli._setup_ha_step(_args(), {}, non_interactive=False,
                                  posture={})
    capsys.readouterr()

    assert new["shared"]["ha_path"] == old_path == ""
    assert new["data"]["home_assistant_configured"] is False


def test_a_non_interactive_run_skips_it_the_same_way(steady, capsys):
    module, package = _module()
    ctx = _ctx(steady, package)
    ctx.proposal = module.propose(ctx)
    new = module.apply(ctx, {})
    old_path = cli._setup_ha_step(_args(), {}, non_interactive=True, posture={})
    capsys.readouterr()
    assert new["shared"]["ha_path"] == old_path == ""


def test_a_command_line_pair_the_posture_forbids_still_refuses_the_run(steady):
    module, package = _module()
    args = _args(ha_url="http://10.0.0.5:8123", ha_token=TOKEN)
    ctx = _ctx(steady, package, args=args,
               shared={"posture": {"run_egress": "sealed"}})
    ctx.proposal = module.propose(ctx)
    with pytest.raises(cli.SetupRefused):
        module.apply(ctx, {})


def test_a_typed_address_the_posture_forbids_is_dropped_without_a_token(steady):
    module, package = _module()
    ctx = _ctx(steady, package, non_interactive=False,
               shared={"posture": {"run_egress": "sealed"}})
    ctx.proposal = module.propose(ctx)
    result = module.apply(ctx, {"connect_hub": True,
                                "hub_url": "http://10.0.0.5:8123",
                                "hub_token": TOKEN})
    assert result["gap"]["skill"] == "tools"
    assert result["shared"]["ha_path"] == ""
    assert TOKEN not in json.dumps(result)


def test_missing_tools_are_recorded_as_gaps_with_a_fix(steady):
    module, package = _module()
    ctx = _ctx(steady, package, sections=_goal_sections("integration:media"))
    ctx.proposal = module.propose(ctx)
    gaps = module.apply(ctx, {})["gaps"]
    assert gaps and gaps[0]["fix"]["id"] == "setup.rerun"
    assert module.NO_KEY in gaps[0]["why"]


# -- verify -------------------------------------------------------------------

def test_verify_observes_the_config_on_disk(steady):
    module, package = _module()
    setup_ops.set_ha_config(HUB, TOKEN, enabled=True)
    sections = {"tools": {"home_assistant_configured": True}}
    verdict = module.verify(_ctx(steady, package, sections=sections))
    assert verdict["status"] == "pass"


def test_a_hub_recorded_and_not_on_disk_is_a_gap(steady):
    module, package = _module()
    sections = {"tools": {"home_assistant_configured": True}}
    verdict = module.verify(_ctx(steady, package, sections=sections))
    assert verdict["status"] == "gap"
    assert verdict["gap"]["fix"]["id"] == "setup.rerun"


# -- through the runner -------------------------------------------------------

def test_the_token_reaches_the_config_and_nothing_else(steady, tmp_path):
    cfg = types.SimpleNamespace(CONFIG_DIR=str(tmp_path / "config"),
                                DATA_DIR=steady.DATA_DIR)
    run = wizard_runner.Runner(
        args=_args(), cfg=cfg, non_interactive=False,
        schedule=(wizard_runner.Step("tools.card", "tools",
                                     wizard_runner.V2_PART),),
        renderer=lambda card, **kw: {"connect_hub": True, "hub_url": HUB,
                                     "hub_token": TOKEN})
    run.run()

    assert run.sections["tools"]["home_assistant_configured"] is True
    row = run.log[-1]
    assert row["outcome"] == "done"
    assert row["changed"]["hub_token"] == "(entered, not recorded)"

    record = open(run.state_path(), encoding="utf-8").read()
    assert TOKEN not in record
    assert TOKEN not in json.dumps(run.state())
    assert TOKEN not in json.dumps(run.sections)
