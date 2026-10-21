"""Secure as three cards: posture, vault, bridge -- and what never leaks.

The control for every "did it change what it writes" test is the Phase 1 step
itself (``cli._setup_posture_step`` / ``_setup_vault_step`` /
``_setup_bridge_step``) run with the same scripted answers in its own temp data
dir. The wording on the owner's screen may change; the section contents may
not.

The load-bearing tests here are the ones with no assertion about wording at all:

* :func:`test_the_pin_and_the_code_are_in_none_of_the_places_a_run_writes`
  greps the card, the answers the runner logged, ``wizard_run.json``, the
  captured output and the API-shaped response for both values.
* :func:`test_a_vault_is_not_created_when_nothing_can_show_the_recovery_code`
  is the fail-closed rule: no face, no vault.

Run:
    pytest tests/test_wizard_secure.py
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import types

import pytest

from puffpai import cli, hardware_census, setup_ops
from puffpai.config import get_config
from puffpai.wizard import cards, loader, render_cli, runner as wizard_runner
from puffpai.wizard import service

#: A PIN that passes both policies, and one that passes neither.
GOOD_PIN = "orbit-lantern-77"
WEAK_PIN = "123456"

#: What a recovery code looks like on screen: five groups of five
#: (``zone_vault.RECOVERY_GROUPS``), so a test can find the one that was shown
#: without the skill ever handing it back.
CODE_RE = re.compile(r"^[A-Z0-9]{5}(?:-[A-Z0-9]{5}){4}$")


def _code_in(lines) -> str:
    for line in lines:
        token = str(line).strip()
        if CODE_RE.match(token):
            return token
    return ""


@pytest.fixture
def steady(monkeypatch, tmp_path):
    """One machine, one data dir, and nobody watching the show-once channel."""
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "live"))
    cfg = get_config(_force_reload=True)
    census = hardware_census.run_census()
    monkeypatch.setattr(hardware_census, "run_census",
                        lambda *a, **k: copy.deepcopy(census))
    monkeypatch.setattr(setup_ops, "list_models", lambda *a, **k: ["qwen2.5:7b"])
    _FACE.clear()
    yield cfg
    _FACE.clear()
    get_config(_force_reload=True)


def _args(**kw):
    base = dict(yes=True, build_network=None, run_egress=None, egress_door=[],
                skip_vault=False, vault_password=None, skip_bridge=False,
                bridge_posture=None, ha_url=None, has_firewall=False,
                has_managed_lan=False)
    base.update(kw)
    return argparse.Namespace(**base)


def _module():
    packages, refusals = loader.load_builtin_packages()
    assert refusals == []
    return loader.load_module(packages["secure"]), packages["secure"]


#: The show-once face these tests are running with, if any. A list, not a
#: module attribute on the code under test: the face is per run now, and the
#: point is that nothing registers one process-wide.
_FACE: list = []


def _face():
    return _FACE[0] if _FACE else None


def _ctx(cfg, package, *, part="", args=None, sections=None, shared=None,
         non_interactive=True, show_once=False):
    return wizard_runner.RunContext(
        args=args or _args(), cfg=cfg, non_interactive=non_interactive,
        shared=shared if shared is not None else {},
        sections=sections if sections is not None else {}, package=package,
        # The runner passes this from the schedule entry (``card:vault``).
        # Without it the part is inferred from the run's progress, which is
        # what the runner path exercises below.
        part=part,
        show_once=_face() if show_once is False else show_once)


def _fake_sink(asked=("skip",)):
    """A face that records what it was shown and answers from a script."""

    class Sink:
        def __init__(self):
            self.shown = []
            self.script = list(asked)

        def show(self, lines):
            self.shown.extend(str(line) for line in lines)

        def ask(self, prompt):
            raise AssertionError("the code typed back was read with echo on")

        def ask_secret(self, prompt):
            return self.script.pop(0) if self.script else ""

    sink = Sink()
    _FACE[:] = [sink]
    return sink


# -- which part ---------------------------------------------------------------

def test_the_part_is_posture_before_anything_and_never_after_the_vault(steady):
    module, package = _module()
    assert module.part_of(_ctx(steady, package)) == "posture"
    assert module.part_of(_ctx(steady, package,
                               shared={"posture": {"run_egress": "island"}})) \
        == "vault"
    assert module.part_of(_ctx(steady, package, shared={
        "posture": {"run_egress": "island"}, "vault_block": {}})) == "bridge"
    assert module.part_of(_ctx(steady, package, part="posture", shared={
        "posture": {"run_egress": "island"}, "vault_block": {}})) == "posture"


# -- posture ------------------------------------------------------------------

def test_the_posture_card_validates_and_writes_nothing(steady):
    module, package = _module()
    ctx = _ctx(steady, package, part="posture")
    card = cards.validate_card(module.propose(ctx)["card"])
    assert card["skill"] == "secure" and card["section"] == "posture"
    assert {f["id"] for f in card["fields"]} >= {"build_network", "run_egress"}
    assert ctx._sections == {}, "propose wrote a section"


def test_an_offline_build_is_offered_no_egress_doors(steady):
    module, package = _module()
    ctx = _ctx(steady, package, part="posture",
               args=_args(build_network="offline"))
    card = cards.validate_card(module.propose(ctx)["card"])
    ids = {field["id"] for field in card["fields"]}
    assert "doors" not in ids, "a sealed/island build was offered doors"
    assert "doors_note" in ids
    assert card["consequential"] is False


def test_the_door_table_appears_only_at_the_enumerated_tier(steady):
    module, package = _module()
    ctx = _ctx(steady, package, part="posture", args=_args(
        run_egress="enumerated", egress_door=["weather"]))
    card = cards.validate_card(module.propose(ctx)["card"])
    field = cards.field_map(card)["doors"]
    assert field["kind"] == "multi_choice"
    assert field["proposed"] == ["weather"]
    assert card["consequential_reasons"] == ["runtime_egress"]


def test_apply_writes_the_posture_the_old_step_wrote(steady, tmp_path,
                                                     monkeypatch, capsys):
    module, package = _module()
    ctx = _ctx(steady, package, part="posture",
               args=_args(build_network="offline"))
    ctx.proposal = module.propose(ctx)
    new = module.apply(ctx, {})

    with monkeypatch.context() as other:
        other.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "control"))
        get_config(_force_reload=True)
        old = cli._setup_posture_step(_args(build_network="offline"),
                                      non_interactive=True)
    capsys.readouterr()

    assert new["section"] == "posture"
    assert new["data"]["posture"] == old
    assert new["shared"]["gates"] == setup_ops.posture_gates(old)
    assert new["shared"]["build_network"] == "offline"


def test_an_unanswered_axis_is_recorded_as_defaulted_not_chosen(steady):
    module, package = _module()
    ctx = _ctx(steady, package, part="posture")
    ctx.proposal = module.propose(ctx)
    posture = module.apply(ctx, {})["data"]["posture"]
    assert posture["build_network_source"] == "defaulted"
    assert posture["run_egress_source"] == "defaulted"


def test_an_answered_axis_is_recorded_as_stated(steady):
    module, package = _module()
    ctx = _ctx(steady, package, part="posture")
    ctx.proposal = module.propose(ctx)
    posture = module.apply(ctx, {"build_network": "offline"})["data"]["posture"]
    assert posture["build_network"] == "offline"
    assert posture["build_network_source"] == "stated"


def test_the_enumerated_tier_with_no_door_named_is_a_gap_and_opens_nothing(
        steady):
    module, package = _module()
    ctx = _ctx(steady, package, part="posture")
    ctx.proposal = module.propose(ctx)
    result = module.apply(ctx, {"run_egress": "enumerated"})
    assert result["data"]["posture"]["enabled_switches"] == []
    assert result["gap"]["skill"] == "secure"


# -- vault --------------------------------------------------------------------

def test_the_vault_card_asks_for_a_pin_and_carries_none(steady):
    _fake_sink()
    module, package = _module()
    card = cards.validate_card(
        module.propose(_ctx(steady, package, part="vault"))["card"])
    field = cards.field_map(card)["pin"]
    assert field["kind"] == "secret_entry"
    assert field["current"] == "" and field["proposed"] == ""
    assert card["consequential_reasons"] == ["vault_pin_unset"]


def test_apply_seals_the_same_vault_block_the_old_step_wrote(
        steady, tmp_path, monkeypatch, capsys):
    _fake_sink()
    module, package = _module()
    ctx = _ctx(steady, package, part="vault")
    ctx.proposal = module.propose(ctx)
    new = module.apply(ctx, {"pin": GOOD_PIN})["data"]["vault"]

    with monkeypatch.context() as other:
        other.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "control-vault"))
        # A separate install has its own state folder too: the vault records
        # where it lives there, and one install never gets a second vault.
        other.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "control-vault-state"))
        get_config(_force_reload=True)
        old = cli._setup_vault_step(_args(vault_password=GOOD_PIN),
                                    get_config(), non_interactive=True)
    capsys.readouterr()

    assert _settled(new) == _settled(old)
    assert new["ready"] is True and new["rejected"] is False
    assert new["recovery_confirmed"] is False


def _settled(block: dict) -> dict:
    return {k: v for k, v in block.items() if not k.endswith("_at")}


def test_a_weak_pin_is_refused_with_todays_reason_and_creates_nothing(steady):
    _fake_sink()
    module, package = _module()
    ctx = _ctx(steady, package, part="vault")
    ctx.proposal = module.propose(ctx)
    result = module.apply(ctx, {"pin": WEAK_PIN})

    _ok, reason = setup_ops.validate_vault_pin(WEAK_PIN)
    assert result["data"]["vault"]["rejected"] is True
    assert result["data"]["vault"]["rejected_reason"] == reason
    assert result["gap"]["why"] == reason
    assert cli.VAULT_REJECTED_LINE in result["gap"]["what"]
    _vdir, sealed = module._vault_state(ctx)
    assert sealed is False, "a refused PIN still created a vault"


def test_two_entries_that_do_not_match_create_nothing(steady):
    _fake_sink()
    module, package = _module()
    ctx = _ctx(steady, package, part="vault")
    ctx.proposal = module.propose(ctx)
    result = module.apply(ctx, {"pin": GOOD_PIN, "pin_again": GOOD_PIN + "x"})
    assert result["data"]["vault"]["configured"] is False
    assert "did not match" in result["gap"]["why"]
    assert module._vault_state(ctx)[1] is False


def test_a_vault_is_not_created_when_nothing_can_show_the_recovery_code(steady):
    """Fail closed: a sealed vault whose code was shown to nobody is a trap."""
    module, package = _module()
    ctx = _ctx(steady, package, part="vault")
    ctx.proposal = module.propose(ctx)
    assert ctx.face() is None
    result = module.apply(ctx, {"pin": GOOD_PIN})
    assert result["data"]["vault"]["rejected"] is True
    assert "show" in result["gap"]["why"]
    assert module._vault_state(ctx)[1] is False


def test_the_card_says_so_before_the_owner_types_a_pin(steady):
    module, package = _module()
    card = cards.validate_card(
        module.propose(_ctx(steady, package, part="vault"))["card"])
    assert "pin" not in cards.field_map(card)
    # Not a gap: this is a build that can still set a PIN, just not from a
    # screen that cannot show a recovery code. It says where instead.
    assert card["status"] == "needs_you"
    assert "next screen" in card["decided"]


def test_the_recovery_code_is_shown_once_and_typed_back(steady):
    """The ceremony, with a face that reads the code off what it was shown."""
    module, package = _module()

    class TypingSink:
        def __init__(self):
            self.shown = []

        def show(self, lines):
            self.shown.extend(str(line) for line in lines)

        def ask(self, prompt):
            raise AssertionError("the code typed back was read with echo on")

        def ask_secret(self, prompt):
            return _code_in(self.shown) or "skip"

    sink = TypingSink()
    _FACE[:] = [sink]
    ctx = _ctx(steady, package, part="vault", non_interactive=False)
    ctx.proposal = module.propose(ctx)
    block = module.apply(ctx, {"pin": GOOD_PIN})["data"]["vault"]

    assert block["recovery_confirmed"] is True
    assert block["recovery_issued"] is True
    assert any("That matches" in line for line in sink.shown)


def test_an_existing_vault_is_left_alone_and_gets_no_new_code(steady):
    _fake_sink()
    module, package = _module()
    ctx = _ctx(steady, package, part="vault")
    ctx.proposal = module.propose(ctx)
    module.apply(ctx, {"pin": GOOD_PIN})

    again = _ctx(steady, package, part="vault")
    again.proposal = module.propose(again)
    card = cards.validate_card(again.proposal["card"])
    assert "pin" not in cards.field_map(card)
    block = module.apply(again, {})["data"]["vault"]
    assert block["configured"] is True and block["ready"] is True


# -- bridge -------------------------------------------------------------------

def test_the_bridge_card_offers_the_whole_ladder(steady):
    module, package = _module()
    ctx = _ctx(steady, package, part="bridge",
               shared={"posture": {"run_egress": "island"}})
    card = cards.validate_card(module.propose(ctx)["card"])
    offered = {opt["value"]
               for opt in cards.field_map(card)["bridge_posture"]["options"]}
    assert {"open_yard", "fenced", "drawbridge_up"} <= offered
    assert "moat" not in offered, "the per-visit asking choice was dropped"


def test_apply_writes_the_bridge_block_the_old_step_wrote(steady, tmp_path,
                                                          monkeypatch, capsys):
    module, package = _module()
    posture = setup_ops.assess_posture("offline", None, [])
    ctx = _ctx(steady, package, part="bridge", shared={"posture": posture})
    ctx.proposal = module.propose(ctx)
    new = module.apply(ctx, {})["data"]["bridge"]

    with monkeypatch.context() as other:
        other.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "control-bridge"))
        get_config(_force_reload=True)
        old = cli._setup_bridge_step(_args(), posture, non_interactive=True)
    capsys.readouterr()

    assert _settled(new) == _settled(old)


def test_a_sealed_build_gets_the_stricter_of_the_two(steady):
    module, package = _module()
    posture = setup_ops.assess_posture("offline", "sealed", [])
    ctx = _ctx(steady, package, part="bridge", shared={"posture": posture})
    ctx.proposal = module.propose(ctx)
    block = module.apply(ctx, {"bridge_posture": "open_yard"})["data"]["bridge"]
    assert block["posture"] == "drawbridge_up"
    assert block["reconciled"] is True


# -- verify -------------------------------------------------------------------

def test_verify_observes_the_vault_on_disk_and_not_the_record(steady):
    module, package = _module()
    sections = {"posture": {
        "posture": setup_ops.assess_posture("offline", "island", []),
        "vault": setup_ops.vault_block(configured=True, sealed=True)}}
    verdict = module.verify(_ctx(steady, package, sections=sections))
    assert verdict["status"] == "gap"
    assert "no initialised vault" in verdict["gap"]["why"]


def test_verify_passes_when_the_vault_is_really_there(steady):
    _fake_sink()
    module, package = _module()
    ctx = _ctx(steady, package, part="vault")
    ctx.proposal = module.propose(ctx)
    block = module.apply(ctx, {"pin": GOOD_PIN})["data"]["vault"]
    sections = {"posture": {
        "posture": setup_ops.assess_posture("offline", "island", []),
        "vault": block}}
    verdict = module.verify(_ctx(steady, package, sections=sections))
    assert verdict["status"] == "pass", verdict
    assert "initialised on disk" in verdict["evidence"]


def test_verify_refuses_a_record_that_carries_a_key(steady):
    module, package = _module()
    block = setup_ops.vault_block(configured=True, sealed=True)
    block["recovery_code"] = "ABCD-2345"
    sections = {"posture": {"posture": setup_ops.assess_posture(
        "offline", "island", []), "vault": block}}
    verdict = module.verify(_ctx(steady, package, sections=sections))
    assert verdict["status"] == "gap"


def test_verify_checks_every_installed_package_against_the_posture(steady):
    from puffpai.wizard import secure_audit

    posture = setup_ops.assess_posture("offline", "sealed", [])
    result = secure_audit.baseline(posture)
    assert result["checked"] >= 12
    assert result["violations"] == [], result
    assert "none asks for more than that" in result["evidence"]


def test_a_package_that_wants_more_than_the_posture_allows_is_a_gap(steady):
    from puffpai.wizard import secure_audit

    greedy = types.SimpleNamespace(manifest={
        "permissions": {"network_hosts": ["api.example.com"], "files": ["data"],
                        "ha_domains": [], "secrets": []},
        "secrets": ["vault:openweather_key"], "egress": "runtime"})
    posture = setup_ops.assess_posture("offline", "sealed", [])
    result = secure_audit.baseline(posture, packages={"greedy": greedy})
    whys = " ".join(row["why"] for row in result["violations"])
    assert "reaches nothing at all" in whys and "credential handle" in whys
    gaps = secure_audit.gaps_for(result)
    assert gaps and gaps[0]["fix"]["id"] == "setup.rerun"


# -- nothing leaks ------------------------------------------------------------

def test_the_pin_and_the_code_are_in_none_of_the_places_a_run_writes(
        steady, tmp_path, capsys):
    """One grep, over every artefact a run produces."""
    sink = _fake_sink()
    module, package = _module()
    cfg = types.SimpleNamespace(CONFIG_DIR=str(tmp_path / "run-config"),
                                DATA_DIR=steady.DATA_DIR, paths=steady.paths,
                                ollama=steady.ollama)
    run = wizard_runner.Runner(
        args=_args(), cfg=cfg, non_interactive=True, show_once=_face(),
        schedule=(wizard_runner.Step("secure.posture", "secure",
                                     wizard_runner.V2_PART),
                  wizard_runner.Step("secure.vault", "secure",
                                     wizard_runner.V2_PART)),
        renderer=lambda card, **kw: (
            {"pin": GOOD_PIN} if "pin" in cards.field_map(card) else {}))
    run.run()

    assert run.sections["posture"]["vault"]["configured"] is True
    code = _code_in(sink.shown)
    assert code, "the recovery code was never shown to anybody"

    with open(run.state_path(), encoding="utf-8") as handle:
        written = handle.read()
    printed = capsys.readouterr().out
    state = json.loads(written)
    api_shaped = json.dumps(service.run_state(args=_args(), cfg=cfg))
    card_json = json.dumps([cards.validate_card(
        module.propose(_ctx(steady, package, part=part))["card"])
        for part in ("posture", "vault", "bridge")])

    for secret in (GOOD_PIN, code):
        for name, blob in (("run state", written), ("captured output", printed),
                           ("API response", api_shaped), ("cards", card_json)):
            assert secret not in blob, f"the secret reached the {name}"
    assert state["log"][-1]["changed"] == {"pin": "(entered, not recorded)"}


def test_the_answers_the_runner_logs_say_a_pin_was_given_and_not_which(steady,
                                                                       tmp_path):
    _fake_sink()
    cfg = types.SimpleNamespace(CONFIG_DIR=str(tmp_path / "c2"),
                                DATA_DIR=steady.DATA_DIR, paths=steady.paths,
                                ollama=steady.ollama)
    run = wizard_runner.Runner(
        args=_args(), cfg=cfg, non_interactive=True, show_once=_face(),
        schedule=(wizard_runner.Step("secure.posture", "secure",
                                     wizard_runner.V2_PART),
                  wizard_runner.Step("secure.vault", "secure",
                                     wizard_runner.V2_PART)),
        renderer=lambda card, **kw: (
            {"pin": GOOD_PIN} if "pin" in cards.field_map(card) else {}))
    run.run()
    changed = run.log[-1]["changed"]
    assert changed == {"pin": "(entered, not recorded)"}


def test_the_runner_walks_all_three_parts_in_order(steady, tmp_path):
    _fake_sink()
    cfg = types.SimpleNamespace(CONFIG_DIR=str(tmp_path / "c3"),
                                DATA_DIR=steady.DATA_DIR, paths=steady.paths,
                                ollama=steady.ollama)
    run = wizard_runner.Runner(
        args=_args(build_network="offline"), cfg=cfg, non_interactive=True,
        show_once=_face(),
        schedule=(wizard_runner.Step("secure.posture", "secure",
                                     wizard_runner.V2_PART),
                  wizard_runner.Step("secure.vault", "secure",
                                     wizard_runner.V2_PART),
                  wizard_runner.Step("secure.bridge", "secure",
                                     wizard_runner.V2_PART)),
        renderer=lambda card, **kw: {})
    run.run()

    assert [row["shown"]["title"] for row in run.log] == [
        "What it may reach", "Your private stuff", "When it asks first"]
    assert [row["outcome"] for row in run.log] == ["done", "done", "done"]
    section = run.sections["posture"]
    assert set(section) == {"posture", "vault", "bridge"}
    assert section["posture"]["build_network"] == "offline"


def test_changing_the_egress_tier_makes_the_right_skills_stale(steady, tmp_path):
    _fake_sink()
    cfg = types.SimpleNamespace(CONFIG_DIR=str(tmp_path / "c4"),
                                DATA_DIR=steady.DATA_DIR, paths=steady.paths,
                                ollama=steady.ollama)
    run = wizard_runner.Runner(
        args=_args(), cfg=cfg, non_interactive=True, show_once=_face(),
        schedule=(wizard_runner.Step("secure.posture", "secure",
                                     wizard_runner.V2_PART),),
        renderer=lambda card, **kw: {"run_egress": "sealed"})
    run.run()
    assert run.stale[:3] == ["integrate", "tools", "surfaces"]
    assert run.stale[-2:] == ["prove", "handoff"]
