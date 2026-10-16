"""BB-BLD-01 / BB-LLM-02 — the wizard finishes with a bot that answers, or says so.

A stranger completes
`puffpai setup`, does exactly what the closing screen says, and gets a 404,
because the closing screen never once says `ollama pull`. `git grep "ollama
pull" cli.py` was 0 hits.

And on a box with four installed models INCLUDING the one GETTING_STARTED told them
to pull, the wizard recommended and defaulted to Qwen3 30B, "about 19 GB to
download" — then never downloaded it.

Run:
    pytest tests/test_wizard_model_download_step.py
"""
from __future__ import annotations

import argparse
import os

import pytest

from puffpai import brain_health, build_manifest, cli, model_catalog, setup_ops
from puffpai.config import get_config


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


def _installed(monkeypatch, names):
    """Ollama is up, holding exactly ``names``."""
    monkeypatch.setattr(setup_ops, "list_models", lambda *a, **k: list(names))


# ── the "Brain ready?" step ─────────────────────────────────────────────────

def test_a_missing_model_is_not_a_finished_build(scratch, monkeypatch, capsys):
    _installed(monkeypatch, [])
    result = cli._setup_brain_ready_step(
        _args(), "http://localhost:11434", "qwen3:30b-a3b",
        {"disk_gb": 19}, {"may_download_models": True}, non_interactive=True)
    out = capsys.readouterr().out

    assert result["ready"] is False
    assert result["status"] == brain_health.MODEL_MISSING
    assert result["pull_command"] == "ollama pull qwen3:30b-a3b"
    assert "not installed" in out
    # The size is stated before anything is offered, not after it starts.
    assert "19 GB" in out


def test_an_installed_model_is_a_finished_build(scratch, monkeypatch, capsys):
    _installed(monkeypatch, ["qwen2.5:7b-instruct"])
    result = cli._setup_brain_ready_step(
        _args(), "http://localhost:11434", "qwen2.5:7b-instruct",
        {"disk_gb": 5}, {"may_download_models": True}, non_interactive=True)

    assert result["ready"] is True
    assert result["pull_command"] == ""
    assert "can answer" in capsys.readouterr().out


def test_a_non_interactive_run_never_starts_a_19_gb_download(
        scratch, monkeypatch, capsys):
    """--yes must not silently pull. It prints the command and stops."""
    _installed(monkeypatch, [])
    pulled = []
    monkeypatch.setattr(brain_health, "pull_model",
                        lambda *a, **k: pulled.append(a) or {"ok": True})

    result = cli._setup_brain_ready_step(
        _args(), "http://localhost:11434", "qwen3:30b-a3b",
        {"disk_gb": 19}, {"may_download_models": True}, non_interactive=True)

    assert pulled == []
    assert result["ready"] is False
    assert "ollama pull qwen3:30b-a3b" in capsys.readouterr().out


def test_declining_the_download_still_hands_over_the_command(
        scratch, monkeypatch, capsys):
    _installed(monkeypatch, [])
    monkeypatch.setattr(cli, "_ask_yes_no", lambda *a, **k: False)
    monkeypatch.setattr(brain_health, "pull_model",
                        lambda *a, **k: pytest.fail("pulled without a yes"))

    result = cli._setup_brain_ready_step(
        _args(yes=False), "http://localhost:11434", "qwen2.5:7b-instruct",
        {"disk_gb": 5}, {"may_download_models": True}, non_interactive=False)

    assert result["ready"] is False
    assert "ollama pull qwen2.5:7b-instruct" in capsys.readouterr().out


def test_accepting_the_download_pulls_and_reports_ready(
        scratch, monkeypatch, capsys):
    _installed(monkeypatch, [])
    monkeypatch.setattr(cli, "_ask_yes_no", lambda *a, **k: True)
    monkeypatch.setattr(
        brain_health, "pull_model",
        lambda *a, **k: {"ok": True, "message": "qwen2.5:7b-instruct is installed."})

    result = cli._setup_brain_ready_step(
        _args(yes=False), "http://localhost:11434", "qwen2.5:7b-instruct",
        {"disk_gb": 5}, {"may_download_models": True}, non_interactive=False)

    assert result["ready"] is True
    assert result["status"] == brain_health.CONNECTED


def test_a_failed_download_is_reported_as_failed(scratch, monkeypatch, capsys):
    """"The pull broke" must never render as "your bot is ready"."""
    _installed(monkeypatch, [])
    monkeypatch.setattr(cli, "_ask_yes_no", lambda *a, **k: True)
    monkeypatch.setattr(
        brain_health, "pull_model",
        lambda *a, **k: {"ok": False, "message": "The download did not finish: boom."})

    result = cli._setup_brain_ready_step(
        _args(yes=False), "http://localhost:11434", "qwen2.5:7b-instruct",
        {"disk_gb": 5}, {"may_download_models": True}, non_interactive=False)

    assert result["ready"] is False
    assert "did not finish" in capsys.readouterr().out


def test_an_offline_build_is_never_offered_a_download(scratch, monkeypatch, capsys):
    _installed(monkeypatch, [])
    monkeypatch.setattr(brain_health, "pull_model",
                        lambda *a, **k: pytest.fail("offline build pulled"))

    result = cli._setup_brain_ready_step(
        _args(yes=False), "http://localhost:11434", "qwen2.5:7b-instruct",
        {"disk_gb": 5}, {"may_download_models": False}, non_interactive=False)

    out = capsys.readouterr().out
    assert result["ready"] is False
    assert "offline build" in out
    assert "puffpai build import" in out


def test_an_unreachable_ollama_is_reported_as_unreachable(
        scratch, monkeypatch, capsys):
    monkeypatch.setattr(setup_ops, "list_models", lambda *a, **k: None)
    result = cli._setup_brain_ready_step(
        _args(), "http://localhost:11434", "qwen2.5:7b-instruct",
        {"disk_gb": 5}, {"may_download_models": True}, non_interactive=True)

    assert result["ready"] is False
    assert result["status"] == brain_health.UNREACHABLE
    assert "Nothing is answering" in capsys.readouterr().out


# ── BB-LLM-02: the recommendation prefers an installed model that fits ──────

_CATALOG = {
    "schema": "model_catalog/v1",
    "silicon": {},
    "models": [
        {"id": "qwen3:30b-a3b", "label": "Qwen3 30B", "params": "30B (MoE)",
         "min_ram_gb": 24, "rec_ram_gb": 32, "disk_gb": 19, "benchmarks": {}},
        {"id": "qwen2.5:7b-instruct", "label": "Qwen2.5 7B", "params": "7B",
         "min_ram_gb": 8, "rec_ram_gb": 16, "disk_gb": 5, "benchmarks": {}},
        {"id": "dolphin-llama3:8b", "label": "Dolphin Llama3 8B", "params": "8B",
         "min_ram_gb": 8, "rec_ram_gb": 16, "disk_gb": 5, "benchmarks": {}},
        {"id": "gemma3:4b", "label": "Gemma3 4B", "params": "4B",
         "min_ram_gb": 5, "rec_ram_gb": 8, "disk_gb": 3, "benchmarks": {}},
        {"id": "llama3.2:1b", "label": "Llama3.2 1B", "params": "1B",
         "min_ram_gb": 2, "rec_ram_gb": 4, "disk_gb": 2, "benchmarks": {}},
    ],
}

_CENSUS = {"memory": {"total_gb": 64.0}, "hardware_class": "unknown"}


def test_four_installed_small_models_beat_one_uninstalled_large_one():
    """The exact box: 4 installed, the 19 GB one is not."""
    menu = model_catalog.fit_menu(
        _CENSUS,
        installed_models=["qwen2.5:7b-instruct", "dolphin-llama3:8b",
                          "gemma3:4b", "llama3.2:1b"],
        catalog=_CATALOG)

    rec = menu["recommended"]
    assert rec["id"] == "qwen2.5:7b-instruct"   # strongest INSTALLED fit
    assert rec["installed"] is True
    assert "nothing to download" in rec["reason"]
    # The bigger one is still on the menu, one keystroke away.
    assert "qwen3:30b-a3b" in [o["id"] for o in menu["options"]]


def test_with_nothing_installed_the_strongest_fit_is_still_recommended():
    menu = model_catalog.fit_menu(_CENSUS, installed_models=[], catalog=_CATALOG)
    assert menu["recommended"]["id"] == "qwen3:30b-a3b"


def test_an_installed_model_that_does_not_fit_is_not_recommended():
    """Install state is a tiebreak among models that fit, not an override."""
    small_box = {"memory": {"total_gb": 8.0}, "hardware_class": "unknown"}
    menu = model_catalog.fit_menu(
        small_box, installed_models=["qwen3:30b-a3b"], catalog=_CATALOG)
    assert menu["recommended"]["id"] != "qwen3:30b-a3b"


def test_the_yes_run_honours_the_installed_recommendation(scratch, monkeypatch):
    """--yes takes the recommendation, so --yes now takes the installed one."""
    monkeypatch.setattr(model_catalog, "load_catalog", lambda *a, **k: _CATALOG)
    chosen, choice = cli._setup_brain_step(
        _args(), _CENSUS, "http://localhost:11434", get_config(),
        {"may_download_models": True}, non_interactive=True,
        installed_models=["gemma3:4b"])

    assert chosen == "gemma3:4b"
    assert choice["chosen_by"] == "recommendation"


def test_the_manifest_records_whether_the_brain_was_ready(scratch, monkeypatch):
    """`puffpai build` and the dashboard can both read the refusal back."""
    monkeypatch.setattr(setup_ops, "list_models", lambda *a, **k: [])
    monkeypatch.setattr(cli, "_ask_paragraph", lambda *a, **k: "")
    rc = cli.main(["setup", "--yes", "--skip-ha", "--skip-knowledge",
                   "--skip-vault", "--stated-ram", "64"])
    assert rc == 0

    brain = (build_manifest.read_manifest() or {}).get("brain") or {}
    assert brain.get("ready") is False
    assert brain.get("brain_status") == brain_health.MODEL_MISSING
