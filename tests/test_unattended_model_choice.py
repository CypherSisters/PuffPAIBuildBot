"""W6 - `--yes` must pick a brain worth having, without a download nobody chose.

On a 63.7 GB box with nothing
installed and Ollama unreachable, `puffpai setup --yes` chose Qwen3 30B - "about
19 GB to download" - and neither the docs nor the closing screen said the size
out loud. The behaviour is defensible for a person who chose it; it is not
defensible as a machine's choice made while nobody is watching.

The correction that followed the first fix: the floor is a CAPABILITY tier, not
a size. Picking the smallest model overall dropped a 64 GB box to a 1.5B `rails`
brain that can only follow set phrasings - the opposite mistake. So an
unattended run takes the best model in the least demanding ROUTING tier
(config/model_profiles.json) the hardware still hosts comfortably: `guided`
wherever a guided model fits, `rails` only where none does, never `light`.

Four things are pinned here:
  * an installed model still wins (that is the behaviour the launch kit sells);
  * with nothing installed, `--yes` lands in the guided tier on any box that
    can host one, and only drops to rails on hardware that cannot;
  * an interactive run still takes the most capable model that fits;
  * `--model` overrides, and the closing screen states the download's size and
    how long it takes.

Run:
    pytest tests/test_unattended_model_choice.py
"""
from __future__ import annotations

import argparse
import io
import json
import os
from contextlib import redirect_stdout

import pytest

from puffpai import brain_health, cli, model_catalog, model_profile, setup_ops
from puffpai.config import get_config

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


#: The rule "9B or larger where the computer can
#: run it, otherwise the smallest model that still calls tools" replaced the
#: routing-tier cap these tests pin. The cap's code is parked in fit_menu (it
#: runs only when no fitting model calls tools). The new rule's tests:
#: tests/test_tool_model_pick.py.
PARKED_TIER_CAP = pytest.mark.skip(
    reason="parked: the routing-tier cap is replaced by the tool-model rule; "
           "see tests/test_tool_model_pick.py")


def _box(ram: float) -> dict:
    return {"memory": {"total_gb": ram}, "hardware_class": "test"}


# The shipped ladder is what a stranger actually meets, so the tier assertions
# run against it rather than a fixture. A synthetic catalog is used only where
# the test is about the ALGORITHM (tie-breaks, missing tiers).
def _shipped_catalog() -> dict:
    with open(os.path.join(_REPO_ROOT, "config", "model_catalog.json"),
              "r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def scratch(tmp_path):
    prev = os.environ.get("PUFFPAI_DATA_DIR")
    os.environ["PUFFPAI_DATA_DIR"] = str(tmp_path)
    get_config(_force_reload=True)
    try:
        yield tmp_path
    finally:
        if prev is not None:
            os.environ["PUFFPAI_DATA_DIR"] = prev
        else:
            os.environ.pop("PUFFPAI_DATA_DIR", None)
        get_config(_force_reload=True)


def _args(**kw):
    base = dict(yes=True, ollama_url=None, model=None)
    base.update(kw)
    return argparse.Namespace(**base)


def _unattended(ram: float) -> dict:
    return model_catalog.fit_menu(_box(ram), installed_models=[],
                                  catalog=_shipped_catalog(),
                                  prefer_hosted_tier=True)["recommended"]


# --- the three boxes the correction names --------------------------------


@PARKED_TIER_CAP
def test_a_16gb_box_gets_a_7b_class_guided_brain():
    rec = _unattended(16.0)
    assert rec["routing_tier"] == "guided"
    assert rec["id"] == "qwen2.5:7b-instruct"
    assert 4 <= float(rec["disk_gb"]) <= 5
    assert rec["fit"] == "comfortable"


@PARKED_TIER_CAP
def test_a_64gb_orin_still_gets_guided_not_the_19gb_light_model():
    rec = _unattended(63.7)
    assert rec["routing_tier"] == "guided"
    assert rec["id"] == "qwen2.5:7b-instruct"
    # The whole point: the 19 GB light model is on the menu and is not the pick.
    assert model_profile.routing_tier("qwen3:30b-a3b") == "light"
    assert rec["id"] != "qwen3:30b-a3b"


def test_a_3gb_board_drops_to_rails_because_it_cannot_host_guided():
    rec = _unattended(3.0)
    assert rec["routing_tier"] == "rails"
    assert rec["fit"] == "comfortable"
    menu = model_catalog.fit_menu(_box(3.0), installed_models=[],
                                  catalog=_shipped_catalog(),
                                  prefer_hosted_tier=True)
    assert not [o for o in menu["options"] if o["routing_tier"] == "guided"], (
        "A guided model fits a 3 GB board, so rails is no longer the honest "
        "answer for it - pick a smaller box for this test.")


@PARKED_TIER_CAP
def test_rails_is_never_chosen_where_guided_fits():
    for ram in (8.0, 16.0, 32.0, 63.7, 128.0):
        rec = _unattended(ram)
        assert rec["routing_tier"] == "guided", (
            f"{ram} GB fell to {rec['routing_tier']} ({rec['id']}); guided is "
            "the floor worth having wherever the hardware can host it.")


@PARKED_TIER_CAP
def test_light_is_never_the_unattended_pick():
    for ram in (2.0, 3.0, 6.0, 8.0, 16.0, 32.0, 63.7, 256.0):
        rec = _unattended(ram)
        if rec is None:
            continue
        assert rec["routing_tier"] != "light"


# --- the algorithm --------------------------------------------------------


@PARKED_TIER_CAP
def test_interactive_still_recommends_the_most_capable_model_that_fits():
    menu = model_catalog.fit_menu(_box(63.7), installed_models=[],
                                  catalog=_shipped_catalog())
    assert menu["recommended"]["id"] == "qwen3:30b-a3b"
    assert menu["recommended"]["routing_tier"] == "light"
    assert "most capable" in menu["recommended"]["reason"]


def test_an_installed_model_still_beats_the_tier_default():
    menu = model_catalog.fit_menu(_box(63.7),
                                  installed_models=["qwen3:30b-a3b"],
                                  catalog=_shipped_catalog(),
                                  prefer_hosted_tier=True)
    assert menu["recommended"]["id"] == "qwen3:30b-a3b"
    assert "Already on this computer" in menu["recommended"]["reason"]


@PARKED_TIER_CAP
def test_the_reason_line_names_the_tier_and_the_override():
    rec = _unattended(63.7)
    assert "guided tier" in rec["reason"]
    assert "--model" in rec["reason"]
    assert "without --yes" in rec["reason"]


def test_the_best_in_tier_prefers_a_comfortable_fit_over_a_tight_one():
    # An 8 GB box: the 7B is guided but only "tight" there (rec_ram 16), while
    # the 3B is guided and comfortable. Unattended runs do not take tight fits.
    rec = _unattended(8.0)
    assert rec["routing_tier"] == "guided"
    assert rec["fit"] == "comfortable"
    assert rec["id"] != "qwen2.5:7b-instruct"


def test_a_ladder_with_no_guided_row_falls_back_rather_than_failing():
    catalog = {"schema": "model_catalog/v1", "silicon": {}, "models": [
        {"id": "qwen3:30b-a3b", "label": "Big", "params": "30B",
         "min_ram_gb": 24, "rec_ram_gb": 32, "disk_gb": 19, "strength": "",
         "benchmarks": {}},
        {"id": "llama3.2:1b", "label": "Small", "params": "1B",
         "min_ram_gb": 2, "rec_ram_gb": 3, "disk_gb": 1, "strength": "",
         "benchmarks": {}},
    ]}
    rec = model_catalog.fit_menu(_box(63.7), installed_models=[],
                                 catalog=catalog,
                                 prefer_hosted_tier=True)["recommended"]
    assert rec["routing_tier"] == "rails"
    assert rec["id"] == "llama3.2:1b"


def test_every_option_carries_its_routing_tier():
    menu = model_catalog.fit_menu(_box(63.7), installed_models=[],
                                  catalog=_shipped_catalog())
    assert menu["options"]
    for opt in menu["options"]:
        assert opt["routing_tier"] in ("light", "guided", "rails")
        assert opt["routing_tier"] == model_profile.routing_tier(opt["id"])


# --- the wizard wires it to --yes, and --model overrides ------------------


def _brain_step(monkeypatch, *, non_interactive, ram=63.7, model=None):
    seen = {}
    real = model_catalog.fit_menu

    def spy(census, installed_models=None, catalog=None,
            prefer_hosted_tier=False):
        seen["prefer_hosted_tier"] = prefer_hosted_tier
        return real(census, installed_models=installed_models,
                    catalog=_shipped_catalog(),
                    prefer_hosted_tier=prefer_hosted_tier)

    monkeypatch.setattr(model_catalog, "fit_menu", spy)
    buf = io.StringIO()
    with redirect_stdout(buf):
        chosen, choice = cli._setup_brain_step(
            _args(model=model), _box(ram), "http://localhost:11434",
            get_config(), {"may_download_models": True},
            non_interactive=non_interactive, installed_models=[])
    return chosen, choice, seen, buf.getvalue()


@PARKED_TIER_CAP
def test_yes_asks_for_the_hosted_tier_and_lands_on_guided(scratch, monkeypatch):
    chosen, choice, seen, out = _brain_step(monkeypatch, non_interactive=True)
    assert seen["prefer_hosted_tier"] is True
    assert chosen == "qwen2.5:7b-instruct"
    assert choice["chosen_by"] == "recommendation"
    assert choice["routing_tier"] == "guided"
    assert "Routing tier: guided" in out


def test_model_overrides_and_turns_the_preference_off(scratch, monkeypatch):
    chosen, choice, seen, out = _brain_step(monkeypatch, non_interactive=True,
                                            model="qwen3:30b-a3b")
    assert seen["prefer_hosted_tier"] is False
    assert chosen == "qwen3:30b-a3b"
    assert choice["chosen_by"] == "owner"


def test_an_interactive_run_never_asks_for_the_cap(scratch, monkeypatch):
    monkeypatch.setattr(cli, "_ask", lambda *a, **k: "")
    chosen, choice, seen, out = _brain_step(monkeypatch, non_interactive=False)
    assert seen["prefer_hosted_tier"] is False
    # Her rule: the 9B tool model, not the 19 GB row.
    assert chosen == "qwen3.5:9b"


# --- the brain step states the size of the download -----------------------


def test_the_pull_line_says_what_the_download_costs(scratch, monkeypatch):
    monkeypatch.setattr(
        setup_ops, "brain_status",
        lambda *a, **k: {"status": brain_health.MODEL_MISSING, "installed": []})
    buf = io.StringIO()
    with redirect_stdout(buf):
        brain = cli._setup_brain_ready_step(
            _args(), "http://localhost:11434", "qwen2.5:7b-instruct",
            {"disk_gb": 5}, {"may_download_models": True},
            non_interactive=True)
    assert brain["download_estimate"] == brain_health.download_estimate(5)
    assert "5 GB" in brain["download_estimate"]


# --- and --help says so ---------------------------------------------------


def test_setup_help_documents_the_routing_tier_default(capsys):
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["setup", "--help"])
    # argparse re-wraps help text, so compare on whitespace-collapsed output.
    help_text = " ".join(capsys.readouterr().out.split())
    assert ("picks the best model in the LEAST DEMANDING ROUTING TIER your "
            "hardware hosts comfortably") in help_text
    assert ("guided wherever a guided model fits, rails only on hardware that "
            "cannot host guided") in help_text
    assert "Use --model to override." in help_text
    assert "including the --yes routing-tier default" in help_text
