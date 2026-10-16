"""Brain as one card: the menu, the pull, and the check that it answers.

The machine is pinned the way ``tests/test_wizard_discover.py`` pins it, and
Ollama is stubbed everywhere: no test here reaches a model, a registry or a
socket. The control for "did it change what it writes" is the pair of Phase 1
steps (``cli._setup_brain_step`` and ``cli._setup_brain_ready_step``) run with
the same scripted inputs.

Two rules this file is really about:

* **The pull only happens where it happened before** -- the owner said yes on
  the card and the posture allows a download. A ``--yes`` run starts no
  download, and an offline build is offered none.
* **A missing model is a gap, not a stop.** Brain may block and
  today's wizard uses that for nothing, so neither does this.

Run:
    pytest tests/test_wizard_brain.py
"""
from __future__ import annotations

import argparse
import copy
import types

import pytest

from puffpai import brain_health, cli, hardware_census, setup_ops
from puffpai.config import get_config
from puffpai.wizard import cards, loader, runner as wizard_runner

INSTALLED = "qwen2.5:7b-instruct"
URL = "http://127.0.0.1:11434"


@pytest.fixture
def steady(monkeypatch, tmp_path):
    """One census, one data dir, and an Ollama that answers from a dict."""
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path))
    cfg = get_config(_force_reload=True)
    census = hardware_census.run_census()
    monkeypatch.setattr(hardware_census, "run_census",
                        lambda *a, **k: copy.deepcopy(census))
    monkeypatch.setattr(setup_ops, "list_models", lambda *a, **k: [INSTALLED])
    monkeypatch.setattr(setup_ops, "brain_status", lambda url, model, **k: {
        "status": (brain_health.CONNECTED if model == INSTALLED
                   else brain_health.MODEL_MISSING),
        "installed": [INSTALLED]})
    yield cfg
    get_config(_force_reload=True)


def _args(**kw):
    base = dict(yes=True, model=None)
    base.update(kw)
    return argparse.Namespace(**base)


def _module():
    packages, refusals = loader.load_builtin_packages()
    assert refusals == []
    return loader.load_module(packages["brain"]), packages["brain"]


def _shared(cfg, **over):
    census = hardware_census.run_census()
    shared = {"census": census, "ollama_url": URL,
              "installed_models": [INSTALLED],
              "gates": setup_ops.posture_gates(
                  setup_ops.assess_posture("online", "island", []))}
    shared.update(over)
    return shared


def _ctx(cfg, package, *, args=None, sections=None, shared=None,
         non_interactive=True):
    return wizard_runner.RunContext(
        args=args or _args(), cfg=cfg, non_interactive=non_interactive,
        shared=_shared(cfg) if shared is None else shared,
        sections=sections if sections is not None else {}, package=package)


# -- propose ------------------------------------------------------------------

def test_propose_returns_a_card_that_validates_and_writes_nothing(steady):
    module, package = _module()
    ctx = _ctx(steady, package)
    card = cards.validate_card(module.propose(ctx)["card"])
    assert card["skill"] == "brain" and card["section"] == "brain"
    assert card["title"] == "Its brain"
    assert "model" in cards.field_map(card)
    assert ctx._sections == {}, "propose wrote a section"


def test_the_card_says_what_the_tier_costs(steady):
    module, package = _module()
    card = cards.validate_card(module.propose(_ctx(steady, package))["card"])
    # What this brain cannot do, in the profile's own plain sentence -- and
    # no internal words like "routing tier" on a screen a person reads.
    from puffpai import model_profile
    tier = cards.field_map(card)["tier"]["current"]
    assert model_profile.limits_sentence(card["decided"].split(",")[0].split(" ")[0]) in tier \
        or "one job at a time" in tier or "multi-step" in tier
    assert "tier" not in tier.lower()


def test_an_installed_model_costs_nothing_and_is_not_consequential(steady):
    module, package = _module()
    ctx = _ctx(steady, package, args=_args(model=INSTALLED))
    card = cards.validate_card(module.propose(ctx)["card"])
    assert card["estimate"] == {"bytes": 0, "seconds": 0, "basis": "measured"}
    assert card["consequential"] is False
    assert "download_now" not in cards.field_map(card)


def test_a_download_is_sized_from_the_catalog_and_never_timed(steady):
    module, package = _module()
    ctx = _ctx(steady, package, shared=_shared(steady, installed_models=[]))
    card = cards.validate_card(module.propose(ctx)["card"])
    estimate = card["estimate"]
    assert estimate["basis"] in ("calibrated", "unknown")
    assert estimate["seconds"] is None, "a time nobody measured was offered"
    if estimate["basis"] == "calibrated":
        assert estimate["bytes"] > 0
        assert card["consequential_reasons"] == ["heavy_bytes"]
        assert cards.field_map(card)["download_now"]["proposed"] is False


def test_an_offline_build_is_offered_no_download(steady):
    module, package = _module()
    gates = setup_ops.posture_gates(
        setup_ops.assess_posture("offline", "island", []))
    ctx = _ctx(steady, package,
               shared=_shared(steady, gates=gates, installed_models=[]))
    card = cards.validate_card(module.propose(ctx)["card"])
    fields = cards.field_map(card)
    assert "download_now" not in fields
    offered = {opt["value"] for opt in (fields.get("model") or
                                        {"options": []})["options"]}
    assert offered <= {INSTALLED}


def test_a_run_with_no_census_yet_is_a_card_and_not_an_exception(steady):
    module, package = _module()
    ctx = _ctx(steady, package, shared={})
    card = cards.validate_card(module.propose(ctx)["card"])
    assert card["status"] in ("ready", "needs_you")


# -- apply --------------------------------------------------------------------

def test_apply_writes_what_the_two_old_steps_wrote(steady, capsys):
    module, package = _module()
    ctx = _ctx(steady, package, args=_args(model=INSTALLED))
    ctx.proposal = module.propose(ctx)
    new = module.apply(ctx, {})

    shared = _shared(steady)
    old_model, old_choice = cli._setup_brain_step(
        _args(model=INSTALLED), shared["census"], URL, steady, shared["gates"],
        non_interactive=True, installed_models=[INSTALLED])
    old_ready = cli._setup_brain_ready_step(
        _args(model=INSTALLED), URL, old_model, old_choice, shared["gates"],
        non_interactive=True)
    old_choice["ready"] = bool(old_ready.get("ready"))
    old_choice["brain_status"] = old_ready.get("status")
    capsys.readouterr()

    assert new["section"] == "brain"
    assert new["data"]["model"] == old_model
    assert new["data"]["choice"] == old_choice
    assert new["shared"]["brain_ready"] == old_ready


def test_a_yes_run_never_starts_a_download(steady, monkeypatch):
    pulls = []
    monkeypatch.setattr(brain_health, "pull_model",
                        lambda *a, **k: pulls.append(a) or {"ok": True})
    module, package = _module()
    ctx = _ctx(steady, package, args=_args(model="not-here:8b"),
               shared=_shared(steady, installed_models=[]))
    ctx.proposal = module.propose(ctx)
    result = module.apply(ctx, {})

    assert pulls == [], "a non-interactive run started a download"
    assert result["data"]["ready"] is False
    # The fix is the real download command, not "run the wizard again".
    assert result["gap"]["fix"] == {"kind": "command", "id": "model.pull",
                                    "args": {"model": "not-here:8b"}} \
        or result["gap"]["fix"]["id"] == "model.pull_at"
    assert result["shared"]["brain_ready"]["pull_command"]


def test_an_offline_build_defers_the_pull_with_a_gap(steady, monkeypatch):
    pulls = []
    monkeypatch.setattr(brain_health, "pull_model",
                        lambda *a, **k: pulls.append(a) or {"ok": True})
    module, package = _module()
    gates = setup_ops.posture_gates(
        setup_ops.assess_posture("offline", "sealed", []))
    ctx = _ctx(steady, package, args=_args(model="not-here:8b"),
               shared=_shared(steady, gates=gates, installed_models=[]),
               non_interactive=False)
    ctx.proposal = module.propose(ctx)
    result = module.apply(ctx, {"download_now": True})

    assert pulls == [], "an offline build downloaded a model"
    assert "offline" in result["gap"]["impact"]
    assert result["gap"]["fix"]["id"] in ("model.pull", "model.pull_at")


def test_saying_yes_pulls_through_the_path_that_already_exists(steady,
                                                               monkeypatch):
    pulled = {}

    def fake_pull(model, url=None, **kw):
        pulled.update({"model": model, "url": url})
        return {"ok": True, "message": f"{model} is installed."}

    monkeypatch.setattr(brain_health, "pull_model", fake_pull)
    module, package = _module()
    ctx = _ctx(steady, package, args=_args(model="not-here:8b"),
               shared=_shared(steady, installed_models=[]),
               non_interactive=False)
    ctx.proposal = module.propose(ctx)
    result = module.apply(ctx, {"download_now": True})

    assert pulled == {"model": "not-here:8b", "url": URL}
    assert result["data"]["ready"] is True
    assert result["shared"]["brain_ready"]["status"] == brain_health.CONNECTED


def test_a_failed_pull_is_a_gap_and_never_reports_ready(steady, monkeypatch):
    monkeypatch.setattr(brain_health, "pull_model", lambda *a, **k: {
        "ok": False, "message": "The download did not finish: connection lost."})
    module, package = _module()
    ctx = _ctx(steady, package, args=_args(model="not-here:8b"),
               shared=_shared(steady, installed_models=[]),
               non_interactive=False)
    ctx.proposal = module.propose(ctx)
    result = module.apply(ctx, {"download_now": True})
    assert result["data"]["ready"] is False
    assert "did not finish" in result["gap"]["why"]


def test_an_unreachable_ollama_records_the_remedy(steady, monkeypatch):
    monkeypatch.setattr(setup_ops, "brain_status", lambda *a, **k: {
        "status": brain_health.UNREACHABLE, "installed": []})
    module, package = _module()
    ctx = _ctx(steady, package, args=_args(model=INSTALLED))
    ctx.proposal = module.propose(ctx)
    result = module.apply(ctx, {})
    assert result["shared"]["brain_ready"]["remedy"]
    assert result["data"]["ready"] is False


def test_a_typed_model_is_the_one_that_is_recorded(steady):
    module, package = _module()
    ctx = _ctx(steady, package)
    ctx.proposal = module.propose(ctx)
    data = module.apply(ctx, {"model": "qwen2.5:14b"})["data"]
    assert data["model"] == "qwen2.5:14b"
    assert data["choice"]["chosen_by"] == "owner"


# -- verify -------------------------------------------------------------------

def test_verify_asks_the_ollama_and_not_the_proposal(steady):
    module, package = _module()
    sections = {"machine": {"ollama_url": URL},
                "brain": {"model": INSTALLED, "choice": {"model": INSTALLED}}}
    verdict = module.verify(_ctx(steady, package, sections=sections))
    assert verdict["status"] == "pass"
    assert INSTALLED in verdict["evidence"]


def test_a_model_that_is_not_installed_is_a_gap_not_a_stop(steady):
    module, package = _module()
    sections = {"machine": {"ollama_url": URL},
                "brain": {"model": "not-here:8b"}}
    verdict = module.verify(_ctx(steady, package, sections=sections))
    assert verdict["status"] == "gap"
    from puffpai.wizard import fixes
    assert fixes.fix_line(fixes.gap(**{k: verdict["gap"][k] for k in (
        "skill", "what", "why", "impact", "fix")})).startswith(
        "ollama pull not-here:8b")


def test_an_unreachable_ollama_is_a_gap_and_not_an_exception(steady,
                                                              monkeypatch):
    monkeypatch.setattr(setup_ops, "brain_status", lambda *a, **k: {
        "status": brain_health.UNREACHABLE, "installed": []})
    module, package = _module()
    sections = {"machine": {"ollama_url": URL}, "brain": {"model": INSTALLED}}
    verdict = module.verify(_ctx(steady, package, sections=sections))
    assert verdict["status"] == "gap"
    assert verdict["gap"]["skill"] == "brain"


def test_no_model_recorded_is_a_gap_with_a_fix(steady):
    module, package = _module()
    verdict = module.verify(_ctx(steady, package,
                                 sections={"machine": {"ollama_url": URL},
                                           "brain": {}}))
    assert verdict["status"] == "gap"
    assert verdict["gap"]["fix"]["id"] == "setup.rerun"


# -- through the runner -------------------------------------------------------

def test_the_runner_walks_the_lifecycle_and_records_what_it_showed(steady,
                                                                   tmp_path):
    cfg = types.SimpleNamespace(CONFIG_DIR=str(tmp_path / "config"),
                                ollama=steady.ollama, DATA_DIR=steady.DATA_DIR,
                                paths=steady.paths)
    run = wizard_runner.Runner(
        args=_args(), cfg=cfg, non_interactive=True,
        schedule=(wizard_runner.Step("brain.card", "brain",
                                     wizard_runner.V2_PART),),
        renderer=lambda card, **kw: {"model": INSTALLED})
    run.shared.update(_shared(steady))
    run.sections["machine"] = {"ollama_url": URL}
    run.run()

    row = run.log[-1]
    assert row["outcome"] == "done"
    assert row["shown"]["title"] == "Its brain"
    assert row["verified"]["status"] == "pass"
    assert run.sections["brain"]["model"] == INSTALLED
    assert run.stale[:1] == ["context"] and run.stale[-2:] == ["prove",
                                                               "handoff"]


# -- the address that has the brain ---------------------------------

BIG = "qwen3.5:9b"
OTHER = "http://127.0.0.1:11435"


def _two_ollamas(monkeypatch):
    """11434 answers with one small model; the chosen brain is only on 11435."""
    held = {URL: [INSTALLED], OTHER: [BIG, INSTALLED]}

    def _models(url, *a, **k):
        for where, names in held.items():
            if brain_health._same_server(url, where):
                return list(names)
        return None
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setattr(setup_ops, "list_models", _models)
    monkeypatch.setattr(setup_ops, "brain_status", lambda url, model, **k: {
        "status": (brain_health.CONNECTED
                   if brain_health.is_installed(model, _models(url))
                   else brain_health.MODEL_MISSING),
        "installed": _models(url) or []})


def test_best_address_prefers_the_one_that_has_the_model():
    looked = [{"url": URL, "models": [INSTALLED]},
              {"url": OTHER, "models": [BIG]}]
    assert brain_health.best_address(looked) == URL
    assert brain_health.best_address(looked, model=BIG) == OTHER
    assert brain_health.best_address(looked, model="nowhere:1b") == URL


def test_the_brain_step_moves_to_the_address_that_has_the_brain(steady,
                                                                monkeypatch):
    _two_ollamas(monkeypatch)
    module, package = _module()
    ctx = _ctx(steady, package, args=_args(model=BIG))
    ctx.proposal = module.propose(ctx)
    # The card already knows the brain is here (no download offered), and
    # apply still saves the address that has it, not 11434.
    assert ctx.proposal["machine"]["url"] == OTHER
    assert not any(f["id"] == "download_now" for f in ctx.proposal["card"]["fields"])
    result = module.apply(ctx, {})
    assert result["shared"]["ollama_url"] == OTHER
    assert result["data"]["ollama_url"] == OTHER
    assert result["data"]["ready"] is True
    assert OTHER in result["note"]
    assert "gap" not in result


def test_the_brain_step_stays_when_the_brain_is_already_there(steady,
                                                              monkeypatch):
    _two_ollamas(monkeypatch)
    module, package = _module()
    ctx = _ctx(steady, package, args=_args(model=INSTALLED))
    ctx.proposal = module.propose(ctx)
    result = module.apply(ctx, {})
    assert "ollama_url" not in result["shared"]


def test_an_address_typed_with_the_flag_is_kept(steady, monkeypatch):
    _two_ollamas(monkeypatch)
    module, package = _module()
    ctx = _ctx(steady, package, args=_args(model=BIG, ollama_url=URL))
    ctx.proposal = module.propose(ctx)
    result = module.apply(ctx, {})
    assert "ollama_url" not in result["shared"]
    assert result["data"]["ready"] is False


def test_verify_checks_the_address_the_brain_step_settled_on(steady,
                                                             monkeypatch):
    _two_ollamas(monkeypatch)
    module, package = _module()
    sections = {"machine": {"ollama_url": URL},
                "brain": {"model": BIG, "ollama_url": OTHER}}
    verdict = module.verify(_ctx(steady, package, sections=sections))
    assert verdict["status"] == "pass"
    assert OTHER in verdict["evidence"]
