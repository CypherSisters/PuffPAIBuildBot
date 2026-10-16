"""The wizard never proposes a brain this machine cannot run.

Both wizard faces fell back on the configured default (``qwen2.5:7b-instruct``,
8 GB floor) whenever the recommendation could not be used. On a machine below
the catalog's floor that meant a 1.5 GB board was told "Using brain:
qwen2.5:7b-instruct", and the card offered to download it. The rule now
(``model_catalog.fitting_fallback``): the configured model only if this
machine's RAM does not exclude it, else the fitted recommendation, else no
model at all and the menu's own sentence saying nothing fits.

Ollama is stubbed; nothing here reaches a model or a socket.
"""
from __future__ import annotations

import argparse

import pytest

from puffpai import brain_health, cli, hardware_census, model_catalog, setup_ops
from puffpai.config import get_config
from puffpai.wizard import cards, loader, runner as wizard_runner

URL = "http://127.0.0.1:11434"
FLOOR_MESSAGE = ("This machine is below the floor of our smallest local model. "
                 "Add memory, or point the wizard at a board with more RAM.")


def _census(ram):
    return hardware_census.apply_stated(hardware_census.run_census(),
                                        total_ram_gb=ram)


def _min_ram(model_id):
    rows = {m["id"]: m for m in model_catalog.load_catalog()["models"]}
    return float(rows[model_id]["min_ram_gb"])


def _gates(network="online"):
    return setup_ops.posture_gates(
        setup_ops.assess_posture(network, "island", []))


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path))
    config = get_config(_force_reload=True)
    monkeypatch.setattr(setup_ops, "list_models", lambda *a, **k: [])
    monkeypatch.setattr(setup_ops, "brain_status", lambda url, model, **k: {
        "status": brain_health.MODEL_MISSING, "installed": []})
    yield config
    get_config(_force_reload=True)


def test_the_configured_default_does_not_fit_below_eight_gb(cfg):
    # The premise: the default the old fallback used needs 8 GB.
    assert cfg.ollama.model == "qwen2.5:7b-instruct"
    assert _min_ram(cfg.ollama.model) == 8


# -- the rule itself ------------------------------------------------------------

def test_below_the_floor_there_is_no_fallback(cfg):
    menu = model_catalog.fit_menu(_census(1.5))
    assert menu["message"] == FLOOR_MESSAGE
    assert model_catalog.fitting_fallback(menu, "qwen2.5:7b-instruct") == ""


def test_a_configured_model_too_big_for_this_ram_gives_way_to_the_fit(cfg):
    menu = model_catalog.fit_menu(_census(4))
    pick = model_catalog.fitting_fallback(menu, "qwen2.5:7b-instruct")
    assert pick == menu["recommended"]["id"]
    assert _min_ram(pick) <= 4


def test_a_configured_model_that_fits_is_kept(cfg):
    menu = model_catalog.fit_menu(_census(16))
    assert model_catalog.fitting_fallback(menu, "llama3.2:3b") == "llama3.2:3b"


def test_unknown_memory_keeps_the_configured_model(cfg):
    census = hardware_census.run_census()
    census["memory"] = {"total_gb": None}
    menu = model_catalog.fit_menu(census)
    assert model_catalog.fitting_fallback(menu, "qwen2.5:7b-instruct") \
        == "qwen2.5:7b-instruct"


# -- the CLI step -----------------------------------------------------------------

def _cli_pick(cfg, ram, network="online", capsys=None):
    return cli._setup_brain_step(
        argparse.Namespace(yes=True, model=None), _census(ram), URL, cfg,
        _gates(network), non_interactive=True, installed_models=[])


def test_cli_below_the_floor_picks_nothing_and_says_so(cfg, capsys):
    model, choice = _cli_pick(cfg, 1.5)
    out = capsys.readouterr().out
    assert model == "" and choice["model"] == ""
    assert "below the floor of our smallest local model" in out
    assert "No brain picked: nothing on this build's list fits this computer." in out
    assert "Using brain" not in out


@pytest.mark.parametrize("ram", [2, 2.5, 3, 4, 5, 6, 7.5, 8, 12, 16, 24, 32, 64])
@pytest.mark.parametrize("network", ["online", "offline"])
def test_cli_never_uses_a_model_this_ram_excludes(cfg, capsys, ram, network):
    model, _ = _cli_pick(cfg, ram, network)
    assert model, f"{ram} GB is above the floor, so a brain should be picked"
    assert _min_ram(model) <= ram, f"{model} does not fit {ram} GB"


# -- the brain card ---------------------------------------------------------------

def _card_ctx(cfg, ram, network="online"):
    packages, refusals = loader.load_builtin_packages()
    assert refusals == []
    module = loader.load_module(packages["brain"])
    ctx = wizard_runner.RunContext(
        args=argparse.Namespace(yes=True, model=None), cfg=cfg,
        non_interactive=True,
        shared={"census": _census(ram), "ollama_url": URL,
                "installed_models": [], "gates": _gates(network)},
        sections={}, package=packages["brain"])
    return module, ctx


def test_card_below_the_floor_proposes_nothing_and_offers_no_download(cfg):
    module, ctx = _card_ctx(cfg, 1.5)
    proposal = module.propose(ctx)
    card = cards.validate_card(proposal["card"])
    fields = cards.field_map(card)
    assert proposal["model"] == ""
    assert "download_now" not in fields
    assert card["status"] == "needs_you"
    assert FLOOR_MESSAGE in fields["none"]["current"]


def test_card_apply_below_the_floor_records_why(cfg):
    module, ctx = _card_ctx(cfg, 1.5)
    ctx.proposal = module.propose(ctx)
    result = module.apply(ctx, {})
    assert result["data"]["model"] == ""
    assert result["shared"]["brain_ready"]["ready"] is False
    assert result["shared"]["brain_ready"]["reason"] == FLOOR_MESSAGE
    assert result["gap"]["why"] == FLOOR_MESSAGE


@pytest.mark.parametrize("ram", [2, 3, 4, 5, 7.5, 8, 16, 32])
@pytest.mark.parametrize("network", ["online", "offline"])
def test_card_never_proposes_a_model_this_ram_excludes(cfg, ram, network):
    module, ctx = _card_ctx(cfg, ram, network)
    model = module.propose(ctx)["model"]
    assert model and _min_ram(model) <= ram, f"{model!r} proposed at {ram} GB"


def test_an_empty_model_is_not_written_over_the_config(cfg):
    setup_ops.set_ollama_config("llama3.2:1b", URL)
    setup_ops.set_ollama_config("", URL)
    assert get_config(_force_reload=True).ollama.model == "llama3.2:1b"
