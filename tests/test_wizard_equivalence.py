"""One card set, one run log: what the wizard is, after the legacy path went.

This file used to run the same scripted answers through the package runner and
through ``cli._run_setup_legacy`` and compare every manifest section. The legacy
straight line is deleted, so that comparison is gone; what it was protecting --
*the wizard does not change what it writes by accident* -- moved to
``tests/test_wizard_golden.py``, which pins a checked-in manifest and a
checked-in console transcript for two scripted runs.

What stays here is the half that was never about the old path: the run log says
something true about all twelve skills, the manifest's sections come from the
packages that own them, and the CLI and the browser enumerate one card set from
one source of truth (the runner's schedule, carried to the browser as ``order``
on ``GET /wizard/cards``).

Run:
    pytest tests/test_wizard_equivalence.py
"""
from __future__ import annotations

import copy
import json
import os

import pytest

from puffpai import build_manifest, cli, hardware_census, setup_ops
from puffpai.config import get_config
from puffpai.wizard import cards, runner, service

ARGV = ["setup", "--yes", "--skip-ha", "--skip-knowledge",
        "--build-network", "offline", "--goal", "a space bot for my telescopes"]


@pytest.fixture
def steady_machine(monkeypatch):
    """One census, so nothing here depends on free disk space moving."""
    census = hardware_census.run_census()
    monkeypatch.setattr(hardware_census, "run_census",
                        lambda *a, **k: copy.deepcopy(census))
    monkeypatch.setattr(setup_ops, "list_models", lambda *a, **k: [])


def _run_in(path, monkeypatch) -> dict:
    """Run the wizard with its data dir at *path* and return the manifest."""
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(path))
    get_config(_force_reload=True)
    assert cli.main(list(ARGV)) == 0
    manifest = build_manifest.read_manifest()
    assert manifest, "the wizard wrote no manifest"
    return manifest


def _settled(value):
    """A copy with the moving parts dropped: timestamps and generation stamps."""
    if isinstance(value, dict):
        return {k: _settled(v) for k, v in value.items()
                if not (k.endswith("_at") or k in ("generated_at", "expires",
                                                   "uptime_s"))}
    if isinstance(value, list):
        return [_settled(v) for v in value]
    return value


def test_the_run_log_records_all_twelve_and_is_honest_about_four(
        tmp_path, steady_machine, monkeypatch):
    _run_in(tmp_path / "new", monkeypatch)
    state_path = os.path.join(get_config().CONFIG_DIR, "wizard_run.json")
    with open(state_path, encoding="utf-8") as handle:
        state = json.load(handle)

    from puffpai.wizard.runner import SKILL_ORDER
    assert list(state["skills"]) == list(SKILL_ORDER)
    outcomes = {k: v["outcome"] for k, v in state["skills"].items()}
    # The four that wrap nothing yet say so in the record, and say it nowhere
    # else: an owner is not shown a gap they cannot act on.
    assert state["sections"]["prove"]["verdict"] in ("pass", "gap", "fail")
    for skill in SKILL_ORDER:
        assert outcomes[skill] == "done", f"{skill} did not finish"
    assert state["refusals"] == []

    # No step failed, and every gap this run records is one of the four honest
    # ones below -- asserted by skill and fix id, not by the gap being absent,
    # because an offline run with no model and no persona folder HAS gaps and
    # the whole point of the record is that it says so. Anything outside this
    # list is a regression.
    allowed = {
        # This ARGV passes --skip-knowledge: nothing was offered, so nothing
        # was chosen, and the honest line is that the step did not run. It used
        # to be ("prove", "source.fetch") - one "chosen and never downloaded"
        # gap per goal-matched pack, on a run where the owner was never shown a
        # pack at all.
        ("prove", "setup.rerun"),
        # This ARGV scaffolds no persona. The goal DOES reach the model now --
        # it is recorded in the manifest's persona block either way, which is
        # what ``build_context.collect_goal`` reads -- so what is left to say is
        # that there is nowhere to keep a list, with the command that makes one.
        ("purpose", "setup.scaffold"),
        # A source checkout has no installed launchers, so the closing screen's
        # own check says what is not on this machine.
        ("handoff", "setup.rerun"),
        # An offline box with no model: brain says so rather than pretending,
        # and names the real download command (not "run the wizard again").
        ("brain", "setup.rerun"),
        ("brain", "model.pull"),
        ("brain", "model.pull_at"),
    }
    for gap in state["gaps"]:
        assert (gap["skill"], gap["fix"]["id"]) in allowed, gap
        assert gap["fix_command"], gap


def test_the_manifest_sections_come_from_the_packages_that_own_them(
        tmp_path, steady_machine, monkeypatch):
    manifest = _run_in(tmp_path / "new", monkeypatch)
    state_path = os.path.join(get_config().CONFIG_DIR, "wizard_run.json")
    with open(state_path, encoding="utf-8") as handle:
        sections = json.load(handle)["sections"]

    assert manifest["hardware"] == _settled(sections["machine"]["census"])
    assert manifest["posture"]["run_egress"] == (
        sections["posture"]["posture"]["run_egress"])
    assert manifest["build"]["name"] == sections["persona"]["bot_name"]


# -- one card set, two renderers ---------------------------------------------
#
# This was a strict xfail while the CLI walked fourteen hand-written steps and
# the dashboard eight. It is green now, and the reason matters more than the
# colour: neither renderer has a list of the wizard's steps any more. The
# runner's schedule is the only one, the API carries it to the browser as
# ``order`` on GET /wizard/cards, and the review screen sorts and keys its rows
# by what it was sent. A dashboard that drifted would have to be given the
# drift by the backend.

_REVIEW_MODEL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "dashboard", "app", "onboarding", "review", "reviewModel.ts")


def _review_model_source() -> str:
    with open(_REVIEW_MODEL, encoding="utf-8") as handle:
        return handle.read()


def test_the_dashboard_keeps_no_list_of_the_wizards_steps():
    """The drift is unrepresentable, not merely fixed.

    A second copy of the skill list in the browser is how fourteen steps and
    eight steps happened. There is no copy: if this file ever declares one
    again, this fails before anybody's build does.
    """
    source = _review_model_source()
    assert "SKILL_ORDER" not in source, (
        "reviewModel.ts declares its own order again")
    assert "OWNER_WORDS" not in source, (
        "reviewModel.ts declares its own titles again")
    for skill in runner.SKILL_ORDER:
        assert f'"{skill}"' not in source, (
            f"reviewModel.ts names the '{skill}' skill; the backend names the "
            f"skills")
    # And it takes the order as an argument rather than defaulting to one.
    assert "export function inSpecOrder(cards: WizardCard[], order: readonly string[])" in source


def test_both_renderers_cover_the_same_skills(tmp_path, monkeypatch):
    """The card set the CLI runs is the card set the browser is sent."""
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "cards"))
    get_config(_force_reload=True)
    offered = service.propose_cards()

    scheduled = [f"{step.skill}:{part}" if part else step.skill
                 for step in runner.SCHEDULE
                 for part in [runner.lifecycle_part(step.part)]
                 if part is not None and step.skill]
    # Same ids, same order, from the one schedule.
    assert offered["order"] == scheduled
    assert [cards.card_id(card) for card in offered["cards"]] == scheduled
    assert offered["refusals"] == []

    # Every one of the twelve is accounted for: it has a card, or the backend
    # says in so many words that it has none.
    named = {cards.split_card_id(card_id)[0] for card_id in offered["order"]}
    plain = {row["skill"] for row in offered["not_converted"]}
    assert named | plain == set(runner.SKILL_ORDER)
    assert not named & plain
    for row in offered["not_converted"]:
        assert row["title"], row


def test_the_only_skills_without_a_card_are_assemble_and_prove():
    """Assemble and Prove ask the owner nothing, so they have nothing to put
    on a card."""
    scheduled = {step.skill for step in runner.SCHEDULE
                 if runner.lifecycle_part(step.part) is not None}
    assert set(runner.SKILL_ORDER) - scheduled == {"assemble", "prove"}
