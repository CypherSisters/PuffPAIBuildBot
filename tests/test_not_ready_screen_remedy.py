"""W2 - the NOT-READY screen must not tell you to pull from a dead server.

With no Ollama running, Step 8 correctly
said "install Ollama and start it", and fourteen lines later the closing screen
said ``ollama pull qwen3:30b-a3b    (on the Ollama at http://localhost:19999)``
-- a command that cannot succeed, against the address that just failed to answer.
docs/TROUBLESHOOTING.md already says an ``unreachable`` status means "start
Ollama or fix the address rather than pulling anything".

The reachable-but-empty branch was correct and is pinned here verbatim, because
it is the launch kit's Shot 4.

Run:
    pytest tests/test_not_ready_screen_remedy.py
"""
from __future__ import annotations

import argparse
import io
import os
from contextlib import redirect_stdout

import pytest

from puffpai import brain_health, cli, setup_ops
from puffpai.config import get_config

DEAD = "http://localhost:19999"
LIVE = "http://localhost:11434"


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


# --- the helper -----------------------------------------------------------


def test_the_remedy_is_the_string_troubleshooting_promises():
    assert brain_health.unreachable_remedy(DEAD) == (
        "Ollama is not reachable at http://localhost:19999 - install or start "
        "Ollama (or pass --ollama-url), then re-run `puffpai setup`"
    )


def test_the_remedy_names_the_default_address_when_none_is_given():
    assert brain_health.DEFAULT_URL in brain_health.unreachable_remedy()


# --- branch 1: Ollama unreachable ----------------------------------------


def test_the_unreachable_step_hands_the_closing_screen_a_remedy(
        scratch, monkeypatch):
    monkeypatch.setattr(setup_ops, "brain_status",
                        lambda *a, **k: {"status": brain_health.UNREACHABLE})
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = cli._setup_brain_ready_step(
            _args(), DEAD, "qwen3:30b-a3b", {"disk_gb": 19},
            {"may_download_models": True}, non_interactive=True)
    assert result["status"] == brain_health.UNREACHABLE
    assert result["remedy"] == brain_health.unreachable_remedy(DEAD)


# --- branch 2: Ollama reachable but empty (the launch kit's Shot 4) -------


def test_the_model_missing_step_carries_no_remedy(scratch, monkeypatch):
    monkeypatch.setattr(
        setup_ops, "brain_status",
        lambda *a, **k: {"status": brain_health.MODEL_MISSING, "installed": []})
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = cli._setup_brain_ready_step(
            _args(), LIVE, "qwen3:30b-a3b", {"disk_gb": 19},
            {"may_download_models": True}, non_interactive=True)
    assert not result.get("remedy")
    assert result["pull_command"].startswith("ollama pull qwen3:30b-a3b")
