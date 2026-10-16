"""A rejected --vault-pin must not end in "Setup complete".

Running `puffpai setup --vault-pin 1234
--yes` printed the policy refusal, carried on, and finished with "Setup
complete. Your bot is ready." over a build with no vault on disk and no PIN.
Same class of lie as calling a brainless build finished, and the manifest agreed
with the closing screen rather than with the filesystem.

A PIN handed in on the command line was never checked against policy before it
reached the vault, so the only signal was an exception the step swallowed.

Skipping the vault is still a legitimate, complete build: it is a decision. A
REFUSED PIN is not.

Run:
    pytest tests/test_setup_rejected_vault_pin.py
"""
from __future__ import annotations

import argparse
import io
import os
from contextlib import redirect_stdout

import pytest

from puffpai import cli, setup_ops
from puffpai.config import get_config

# ten digits, passes policy. (9182736455 no longer does: it is two runs
# woven together, 9-8-7-6-5 and 1-2-3-4-5.)
GOOD_PIN = "8316047295"
SHORT_PIN = "1234"               # too short
COMMON_PIN = "1111111111"        # ten digits, but a published-list favourite


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
    base = dict(yes=True, vault_password=None, skip_vault=False)
    base.update(kw)
    return argparse.Namespace(**base)


def _vault_step(pin, *, non_interactive=True):
    buf = io.StringIO()
    with redirect_stdout(buf):
        block = cli._setup_vault_step(
            _args(vault_password=pin), get_config(),
            non_interactive=non_interactive)
    return block, buf.getvalue()


# --- the two PINs policy actually rejects ---------------------------------


@pytest.mark.parametrize("pin", [SHORT_PIN, COMMON_PIN])
def test_a_rejected_pin_is_not_a_ready_build(scratch, pin):
    ok, _reason = setup_ops.validate_vault_pin(pin)
    assert not ok, f"{pin!r} is supposed to fail policy; this test is stale."
    block, out = _vault_step(pin)
    assert block["ready"] is False
    assert block["rejected"] is True
    assert block["configured"] is False
    assert block["sealed"] is False
    assert cli.VAULT_REJECTED_LINE in out


def test_the_reason_is_printed_not_just_the_refusal(scratch):
    _block, out = _vault_step(SHORT_PIN)
    assert "That PIN cannot be used:" in out
    assert "at least 10 digits" in out


def test_no_vault_is_left_on_disk_after_a_rejected_pin(scratch):
    _vault_step(SHORT_PIN)
    vdir = cli._vault_dir(get_config())
    from puffpai.security.vault import Vault
    assert not Vault(vault_dir=vdir).is_initialized


# --- an accepted PIN, and a skipped vault, are both still fine ------------


def test_a_good_pin_still_produces_a_ready_sealed_vault(scratch):
    block, _out = _vault_step(GOOD_PIN)
    assert block["ready"] is True
    assert block["rejected"] is False
    assert block["configured"] is True
    assert block["sealed"] is True


def test_skipping_the_vault_is_a_decision_not_a_failure(scratch):
    block, out = _vault_step(None)
    assert block["ready"] is True
    assert block["rejected"] is False
    assert block["configured"] is False
    assert cli.VAULT_REJECTED_LINE not in out


def test_skip_vault_flag_is_also_a_decision(scratch):
    buf = io.StringIO()
    with redirect_stdout(buf):
        block = cli._setup_vault_step(
            _args(skip_vault=True), get_config(), non_interactive=True)
    assert block["ready"] is True
    assert block["rejected"] is False


# --- interactive re-asks rather than refusing -----------------------------


def test_an_interactive_run_re_asks_after_a_rejected_flag_pin(
        scratch, monkeypatch):
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(cli, "_prompt_for_pin", lambda: GOOD_PIN)
    monkeypatch.setattr(cli, "_recovery_ceremony", lambda *a, **k: True)
    buf = io.StringIO()
    with redirect_stdout(buf):
        block = cli._setup_vault_step(
            _args(vault_password=SHORT_PIN, yes=False), get_config(),
            non_interactive=False)
    out = buf.getvalue()
    assert "That PIN cannot be used:" in out
    assert "Set one here instead." in out
    assert block["ready"] is True
    assert block["sealed"] is True
    assert block["rejected"] is False


def test_an_interactive_run_that_then_skips_is_not_a_rejection(
        scratch, monkeypatch):
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(cli, "_prompt_for_pin", lambda: "")
    buf = io.StringIO()
    with redirect_stdout(buf):
        block = cli._setup_vault_step(
            _args(vault_password=SHORT_PIN, yes=False), get_config(),
            non_interactive=False)
    assert block["ready"] is True
    assert block["rejected"] is False

