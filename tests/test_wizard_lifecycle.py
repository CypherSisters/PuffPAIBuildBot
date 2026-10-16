"""The runner's Phase 2 path: propose -> card -> apply -> verify, and mixed mode.

The rules under test are the runner's, so every package here is a stub. What
matters is that the runner refuses on the package's behalf -- a card that does
not validate is not shown, an answer nobody was asked for is not applied, a
verify that finds nothing records a gap -- and that a package which was never
converted keeps working beside a converted one, because that is what lets the
wizard work at every commit while the twelve are converted one at a time.

Run:
    pytest tests/test_wizard_lifecycle.py
"""
from __future__ import annotations

import argparse
import json
import types

import pytest

from puffpai.wizard import loader
from puffpai.wizard import runner as wizard_runner
from puffpai.wizard.runner import Runner, Step, V2_PART


@pytest.fixture
def cfg(tmp_path):
    return types.SimpleNamespace(CONFIG_DIR=str(tmp_path / "config"))


def _args(**kw):
    return argparse.Namespace(yes=True, **kw)


def _stub(monkeypatch, modules: dict[str, dict]):
    """``{skill: {"propose": fn, "part_name": fn, ...}}`` as package code."""

    def fake_load_module(package):
        module = types.SimpleNamespace()
        for name, func in (modules.get(package.id) or {}).items():
            setattr(module, name, func)
        return module

    monkeypatch.setattr(loader, "load_module", fake_load_module)


def _card(skill="discover", section="machine", **over):
    card = {"skill": skill, "section": section, "title": "What was found",
            "decided": "a line", "status": "ready", "consequential": False,
            "estimate": {"bytes": 0, "seconds": 0, "basis": "measured"},
            "fields": [{"id": "thing", "kind": "text", "label": "A thing",
                        "current": "before"}]}
    card.update(over)
    return card


def _lifecycle(card=None, applied=None, verdict=None):
    card = card or _card()

    def propose(ctx):
        return {"card": card, "seen": True}

    def apply(ctx, answers):
        return applied(ctx, answers) if applied else {
            "section": "machine",
            "data": {"thing": answers.get("thing", "before")}}

    def verify(ctx):
        return verdict or {"status": "pass", "evidence": "looked and saw"}

    return {"propose": propose, "apply": apply, "verify": verify}


# -- mixed mode ---------------------------------------------------------------

def test_a_converted_skill_and_a_wrapped_one_run_side_by_side(cfg, monkeypatch):
    seen = []

    def part_name(ctx):
        seen.append("wrapped")
        return {"section": "persona", "data": {"bot_name": "Infinity"}}

    _stub(monkeypatch, {"discover": _lifecycle(),
                        "purpose": {"part_name": part_name}})
    run = Runner(args=_args(), cfg=cfg, non_interactive=True,
                 schedule=(Step("discover.card", "discover", V2_PART),
                           Step("purpose.name", "purpose", "name")),
                 renderer=lambda card, **kw: {})
    run.run()

    assert seen == ["wrapped"]
    assert run.sections["machine"] == {"thing": "before"}
    assert run.sections["persona"] == {"bot_name": "Infinity"}
    assert [row["outcome"] for row in run.log] == ["done", "done"]


def test_a_package_scheduled_for_a_card_without_one_is_a_gap_not_a_crash(
        cfg, monkeypatch):
    _stub(monkeypatch, {"discover": {}})
    run = Runner(args=_args(), cfg=cfg, non_interactive=True,
                 schedule=(Step("discover.card", "discover", V2_PART),))
    run.run()
    assert run.log[-1]["outcome"] == "failed"
    assert run.gaps and run.gaps[0]["skill"] == "discover"


# -- the runner refuses on the package's behalf -------------------------------

def test_a_card_that_does_not_validate_is_never_shown(cfg, monkeypatch):
    shown = []
    _stub(monkeypatch, {"discover": _lifecycle(card=_card(status="fine"))})
    run = Runner(args=_args(), cfg=cfg, non_interactive=True,
                 schedule=(Step("discover.card", "discover", V2_PART),),
                 renderer=lambda card, **kw: shown.append(card) or {})
    run.run()
    assert shown == []
    assert run.log[-1]["outcome"] == "failed"


def test_a_card_for_someone_elses_section_is_refused(cfg, monkeypatch):
    _stub(monkeypatch, {"discover": _lifecycle(
        card=_card(skill="discover", section="brain"))})
    run = Runner(args=_args(), cfg=cfg, non_interactive=True,
                 schedule=(Step("discover.card", "discover", V2_PART),))
    run.run()
    assert run.log[-1]["outcome"] == "failed"
    assert "brain" not in run.sections


def test_an_answer_the_card_never_asked_for_is_not_applied(cfg, monkeypatch):
    applied = []
    _stub(monkeypatch, {"discover": _lifecycle(
        applied=lambda ctx, answers: applied.append(answers))})
    run = Runner(args=_args(), cfg=cfg, non_interactive=True,
                 schedule=(Step("discover.card", "discover", V2_PART),),
                 renderer=lambda card, **kw: {"run_this": "rm -rf /"})
    run.run()
    assert applied == []
    assert run.log[-1]["outcome"] == "failed"


def test_a_renderer_that_breaks_leaves_the_proposal_standing(cfg, monkeypatch):
    def boom(card, **kw):
        raise RuntimeError("no terminal")

    _stub(monkeypatch, {"discover": _lifecycle()})
    run = Runner(args=_args(), cfg=cfg, non_interactive=True,
                 schedule=(Step("discover.card", "discover", V2_PART),),
                 renderer=boom)
    run.run()
    assert run.sections["machine"] == {"thing": "before"}
    assert run.log[-1]["outcome"] == "done"


def test_an_apply_that_writes_someone_elses_section_is_refused(cfg, monkeypatch):
    _stub(monkeypatch, {"discover": _lifecycle(
        applied=lambda ctx, answers: {"section": "brain",
                                      "data": {"model": "smuggled"}})})
    run = Runner(args=_args(), cfg=cfg, non_interactive=True,
                 schedule=(Step("discover.card", "discover", V2_PART),),
                 renderer=lambda card, **kw: {})
    run.run()
    assert run.log[-1]["outcome"] == "refused"
    assert "brain" not in run.sections


# -- verify -------------------------------------------------------------------

def test_verify_sees_the_section_apply_just_wrote(cfg, monkeypatch):
    seen = {}

    def verify(ctx):
        seen.update(ctx.own())
        return {"status": "pass", "evidence": "read it back"}

    module = _lifecycle()
    module["verify"] = verify
    _stub(monkeypatch, {"discover": module})
    run = Runner(args=_args(), cfg=cfg, non_interactive=True,
                 schedule=(Step("discover.card", "discover", V2_PART),),
                 renderer=lambda card, **kw: {"thing": "after"})
    run.run()
    assert seen == {"thing": "after"}


def test_a_gap_from_verify_is_recorded_with_its_fix(cfg, monkeypatch):
    _stub(monkeypatch, {"discover": _lifecycle(verdict={
        "status": "gap", "evidence": "nothing answered",
        "gap": {"skill": "discover", "what": "Nothing answered.",
                "why": "the address did not respond",
                "impact": "your bot cannot answer",
                "fix": {"kind": "command", "id": "setup.rerun", "args": {}}}})})
    run = Runner(args=_args(), cfg=cfg, non_interactive=True,
                 schedule=(Step("discover.card", "discover", V2_PART),),
                 renderer=lambda card, **kw: {})
    run.run()
    assert run.log[-1]["verified"]["status"] == "gap"
    assert run.gaps[0]["fix_command"] == "puffpai setup"


# -- the secret ---------------------------------------------------------------

def test_a_secret_answer_reaches_apply_and_not_the_run_record(cfg, monkeypatch):
    got = {}
    card = _card(fields=[{"id": "vault_pin", "kind": "secret_entry",
                          "label": "Vault PIN"}])
    _stub(monkeypatch, {"discover": _lifecycle(
        card=card,
        applied=lambda ctx, answers: got.update(answers) or {
            "section": "machine", "data": {"pin_set": bool(answers)}})})
    run = Runner(args=_args(), cfg=cfg, non_interactive=True,
                 schedule=(Step("discover.card", "discover", V2_PART),),
                 renderer=lambda card, **kw: {"vault_pin": "correct-horse-1"})
    run.run()

    assert got == {"vault_pin": "correct-horse-1"}
    with open(run.state_path(), encoding="utf-8") as handle:
        written = handle.read()
    assert "correct-horse-1" not in written
    state = json.loads(written)
    assert state["log"][-1]["changed"] == {"vault_pin": "(entered, not recorded)"}
    assert state["sections"]["machine"] == {"pin_set": True}


# -- the schedule -------------------------------------------------------------

def test_discover_is_scheduled_as_one_card_where_its_steps_used_to_be():
    entries = [step.entry for step in wizard_runner.SCHEDULE]
    assert "discover.card" in entries
    assert "discover.census" not in entries and "discover.ollama" not in entries
    card_at = entries.index("discover.card")
    # This computer is checked first and shown on its own screen
    # (what it has, the thinking model that fits), then the goal and the name
    # are asked; the posture and the brain are applied after the one summary,
    # from what the owner approved.
    assert card_at == 0
    assert card_at < entries.index("runner.first_look") < entries.index(
        "purpose.goal") < entries.index("purpose.identity")
    assert card_at < entries.index("runner.summary") < entries.index(
        "secure.posture") < entries.index("brain.card")
