"""W8 - the wizard must not print the developers' board id on a stranger's box.

On a Windows laptop with an RTX 2000
Ada, `puffpai setup` printed

    Routing tier: light (measured:jetson_agx_orin_64gb).

which is honest about where the number came from and reads to a stranger as
"this tool is hard-coded to the authors' hardware" - the opposite of what a
hardware census exists to say.

The measured label now appears only on a machine that really is that board.
Everywhere else the printed provenance is "conservative default". The raw
`basis` is unchanged and still goes into the build manifest.

Run:
    pytest tests/test_routing_tier_board_name.py
"""
from __future__ import annotations

import argparse
import io
import os
from contextlib import redirect_stdout

import pytest

from puffpai import cli, model_catalog, model_profile
from puffpai.config import get_config

CATALOG = {
    "schema": "model_catalog/v1",
    "silicon": {
        "jetson_agx_orin_64gb": {
            "label": "Jetson AGX Orin 64GB",
            "hardware_class": "workstation",
        },
    },
    "models": [
        {"id": "qwen3:30b-a3b", "label": "Qwen3 30B", "params": "30B",
         "min_ram_gb": 24, "rec_ram_gb": 32, "disk_gb": 19, "strength": "",
         "benchmarks": {}},
    ],
}

# A Windows laptop: 63.7 GB, so the same "workstation" class as the Orin.
WINDOWS_LAPTOP = {
    "hardware_class": "workstation",
    "memory": {"total_gb": 63.7},
    "cpu": {"model": "Intel64 Family 6 Model 186 Stepping 2, GenuineIntel"},
    "accelerator": {"kind": "nvidia_dgpu", "name": "NVIDIA RTX 2000 Ada "
                                                   "Generation Laptop GPU"},
    "soc": {"model": None, "is_jetson": False},
}

REFERENCE_ORIN = {
    "hardware_class": "workstation",
    "memory": {"total_gb": 61.4},
    "cpu": {"model": "ARMv8 Processor rev 1 (v8l)"},
    "accelerator": {"kind": "jetson_integrated",
                    "name": "NVIDIA integrated (Tegra)"},
    "soc": {"model": "NVIDIA Jetson AGX Orin Developer Kit", "is_jetson": True},
}


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


# --- the label ------------------------------------------------------------


def test_the_measured_board_is_not_named_on_a_machine_that_is_not_it():
    assert model_profile.basis("qwen3:30b-a3b") == "measured:jetson_agx_orin_64gb"
    label = model_profile.basis_label("qwen3:30b-a3b", WINDOWS_LAPTOP,
                                      catalog=CATALOG)
    assert label == "conservative default"
    assert "jetson" not in label.lower()


def test_a_matching_class_alone_is_not_enough():
    # Both boxes are class "workstation". Class match is necessary, not
    # sufficient - the census has to actually be that silicon.
    assert (WINDOWS_LAPTOP["hardware_class"]
            == CATALOG["silicon"]["jetson_agx_orin_64gb"]["hardware_class"])
    assert model_profile.basis_label("qwen3:30b-a3b", WINDOWS_LAPTOP,
                                     catalog=CATALOG) == "conservative default"


def test_the_reference_board_still_gets_its_measured_credit():
    assert model_profile.basis_label("qwen3:30b-a3b", REFERENCE_ORIN,
                                     catalog=CATALOG) == (
        "measured on Jetson AGX Orin 64GB")


def test_a_smaller_orin_still_counts_as_an_orin():
    smaller = dict(REFERENCE_ORIN, memory={"total_gb": 30.0},
                   hardware_class="workstation")
    assert model_profile.basis_label("qwen3:30b-a3b", smaller,
                                     catalog=CATALOG).startswith("measured on")


def test_a_conservative_default_profile_reads_the_same_everywhere():
    for census in (WINDOWS_LAPTOP, REFERENCE_ORIN, None, {}):
        assert model_profile.basis_label("qwen2.5:7b-instruct", census,
                                         catalog=CATALOG) == (
            "conservative default")


def test_an_unknown_silicon_id_degrades_to_the_default_rather_than_leaking_it():
    profiles = {"profiles": {"x:1b": {"basis": "measured:board_we_dropped"}}}
    assert model_profile.basis_label("x:1b", REFERENCE_ORIN, profiles=profiles,
                                     catalog=CATALOG) == "conservative default"


def test_no_census_means_no_measured_claim():
    assert model_profile.basis_label("qwen3:30b-a3b", None,
                                     catalog=CATALOG) == "conservative default"


# --- what the wizard prints ----------------------------------------------


def _brain_step(monkeypatch, census, model):
    monkeypatch.setattr(model_catalog, "load_catalog", lambda *a, **k: CATALOG)
    args = argparse.Namespace(yes=True, ollama_url=None, model=model)
    buf = io.StringIO()
    with redirect_stdout(buf):
        chosen, choice = cli._setup_brain_step(
            args, census, "http://localhost:11434", get_config(),
            {"may_download_models": True}, non_interactive=True,
            installed_models=[])
    return choice, buf.getvalue()


def test_the_wizard_prints_conservative_default_on_a_windows_laptop(
        scratch, monkeypatch):
    choice, out = _brain_step(monkeypatch, WINDOWS_LAPTOP, "qwen3:30b-a3b")
    assert "Routing tier: light (conservative default)." in out
    assert "jetson_agx_orin_64gb" not in out
    # The manifest keeps the true provenance, unchanged.
    assert choice["profile_basis"] == "measured:jetson_agx_orin_64gb"


def test_the_wizard_names_the_board_on_the_board(scratch, monkeypatch):
    choice, out = _brain_step(monkeypatch, REFERENCE_ORIN, "qwen3:30b-a3b")
    assert "Routing tier: light (measured on Jetson AGX Orin 64GB)." in out
    assert choice["profile_basis"] == "measured:jetson_agx_orin_64gb"
