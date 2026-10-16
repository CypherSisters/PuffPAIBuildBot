"""BB-WIZ-S10 — the vault PIN step must not hang on a non-tty stdin.

The isolated reproduction, verbatim:

    echo mypin123 | python -c "import getpass; getpass.getpass('PIN: ')"

under `timeout 8` was killed at 8 s and never returned, despite
``sys.stdin.isatty()`` correctly printing False first. Reached whenever
``--vault-pin`` is not passed, so:

    echo "1234567890" | python -m puffpai setup

hung indefinitely at "Set your vault PIN (blank to skip): " - which is exactly
the scripted/CI/stdin-fed approach the validation brief itself specified.

The test carries its own deadline, because a regression here is a HANG, and a
hanging test that has no timeout is the same bug wearing a different hat.

Run:
    pytest tests/test_vault_pin_prompt_without_tty.py
"""
from __future__ import annotations

import argparse
import os
import threading

import pytest

from puffpai import cli, setup_ops
from puffpai.config import get_config

TIMEOUT_SECONDS = 10.0


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
    base = dict(yes=False, skip_vault=False, vault_password=None)
    base.update(kw)
    return argparse.Namespace(**base)


def _within_deadline(fn):
    """Run ``fn`` on a worker and fail if it is still going at the deadline.

    A daemon thread is used deliberately: if the code under test really does
    hang on a blocking read, the test still finishes and the process still
    exits, instead of wedging the whole suite the way the bug wedged the
    whole install.
    """
    box: dict = {}

    def _run():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
            box["error"] = exc

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(TIMEOUT_SECONDS)
    if worker.is_alive():
        pytest.fail(f"the vault step blocked for more than {TIMEOUT_SECONDS}s "
                    f"on a non-tty stdin - BB-WIZ-S10 has regressed")
    if "error" in box:
        raise box["error"]
    return box["value"]


def test_a_piped_stdin_fails_fast_and_names_the_flag(scratch, monkeypatch, capsys):
    """The repro, as a test: no tty, no --vault-pin, and it must not hang."""
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: False)
    monkeypatch.setattr(
        cli, "_read_pin",
        lambda prompt: pytest.fail("getpass was reached on a non-tty stdin"))

    block = _within_deadline(lambda: cli._setup_vault_step(
        _args(), get_config(), non_interactive=False))

    out = capsys.readouterr().out
    assert block["configured"] is False
    assert "not a tty" in out
    assert "--vault-pin" in out
    assert "--skip-vault" in out


def test_the_pin_flag_still_works_without_a_tty(scratch, monkeypatch):
    """The documented automation path must be unaffected by the guard."""
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: False)
    sealed = {}
    monkeypatch.setattr(
        setup_ops, "seal_vault_with_recovery",
        lambda pin, vault=None: (sealed.setdefault("pin", pin) and None)
        or (setup_ops.vault_block(configured=True, sealed=True), "AAAA-BBBB-CCCC"))

    block = _within_deadline(lambda: cli._setup_vault_step(
        _args(vault_password="Telescope7x", yes=True), get_config(),
        non_interactive=True))

    assert sealed["pin"] == "Telescope7x"
    assert block["configured"] is True


def test_a_real_terminal_still_gets_the_prompt(scratch, monkeypatch):
    """The guard must not have taken the PIN prompt away from a person."""
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: True)
    asked = []
    monkeypatch.setattr(cli, "_prompt_for_pin",
                        lambda: asked.append(True) or "")

    block = _within_deadline(lambda: cli._setup_vault_step(
        _args(), get_config(), non_interactive=False))

    assert asked, "a real terminal was never asked for a PIN"
    assert block["configured"] is False   # blank answer = skipped, not hung


def test_skip_vault_short_circuits_before_any_tty_question(scratch, monkeypatch):
    monkeypatch.setattr(cli, "_stdin_is_tty",
                        lambda: pytest.fail("--skip-vault still asked about stdin"))
    block = _within_deadline(lambda: cli._setup_vault_step(
        _args(skip_vault=True), get_config(), non_interactive=False))
    assert block["configured"] is False


def test_the_tty_probe_never_raises(monkeypatch):
    """A closed or replaced stdin must read as "no terminal", not explode."""
    class _Broken:
        def isatty(self):
            raise ValueError("I/O operation on closed file")

    monkeypatch.setattr(cli.sys, "stdin", _Broken())
    assert cli._stdin_is_tty() is False

    monkeypatch.setattr(cli.sys, "stdin", None)
    assert cli._stdin_is_tty() is False
