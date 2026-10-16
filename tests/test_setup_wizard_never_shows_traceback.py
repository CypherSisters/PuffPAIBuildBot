"""No wizard step may hand the owner a Python traceback.

Running `puffpai setup
--run-egress island --ollama-url http://8.8.8.8:11434 --yes` raised an uncaught
PostureViolation. The traceback landed AFTER the vault step had already run and
printed its one-time recovery code; steps 9-14 never rendered and the exit code
was 1 with no explanation a person could act on.

Two fixes, both pinned here:
  (a) the Ollama address is judged against the run posture at the step that
      CAPTURES it (Step 3), before anything has been created, and a refusal is
      a plain screen with the remedy;
  (b) a floor under every step: any unexpected exception renders
      "Setup stopped at Step N", writes the traceback to a log, and exits 2.

Run:
    pytest tests/test_setup_wizard_never_shows_traceback.py
"""
from __future__ import annotations

import argparse
import io
import os
from contextlib import redirect_stdout

import pytest

from puffpai import cli, setup_ops
from puffpai.config import get_config
from puffpai.security import lan_posture

PUBLIC = "http://8.8.8.8:11434"
LAN = "http://192.168.1.50:11434"
LOOPBACK = "http://127.0.0.1:11434"


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


def _ollama_step(monkeypatch, url, egress, *, non_interactive=True):
    monkeypatch.setattr(setup_ops, "list_models", lambda *a, **k: [])
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = cli._setup_ollama_url_step(
            _args(ollama_url=url), get_config(),
            non_interactive=non_interactive,
            posture={"run_egress": egress})
    return result, buf.getvalue()


# --- (a) the address is judged where it is captured ------------------------


def test_a_public_ollama_under_island_is_refused_at_step_three(
        scratch, monkeypatch):
    with pytest.raises(cli.SetupRefused) as caught:
        _ollama_step(monkeypatch, PUBLIC, "island")
    refusal = caught.value
    assert "posture" in refusal.headline
    assert "8.8.8.8" in refusal.message
    assert any("--run-egress island" in line for line in refusal.remedy)
    assert any("sealed" in line and "loopback" in line
               for line in refusal.remedy)


def test_a_lan_ollama_under_sealed_is_refused(scratch, monkeypatch):
    with pytest.raises(cli.SetupRefused):
        _ollama_step(monkeypatch, LAN, "sealed")


def test_a_lan_ollama_under_island_is_allowed(scratch, monkeypatch):
    (url, _models), _out = _ollama_step(monkeypatch, LAN, "island")
    assert url == LAN


def test_loopback_is_allowed_under_every_posture(scratch, monkeypatch):
    for egress in ("sealed", "island", "enumerated"):
        (url, _models), _out = _ollama_step(monkeypatch, LOOPBACK, egress)
        assert url == LOOPBACK, egress


def test_the_refusal_screen_names_the_step_and_offers_the_remedy(scratch):
    cli.step_banner("The Ollama address")
    violation = lan_posture.check_url("ollama", PUBLIC, "island", resolve=False)
    assert violation is not None
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli._print_refusal(cli._refuse_for_posture(violation))
    out = buf.getvalue()
    assert "Setup stopped -" in out
    assert "Stopped at Step 3 of 14 - The Ollama address" in out
    assert "What to do:" in out
    assert "--run-egress island" in out
    assert "Traceback" not in out


def test_the_refusal_happens_before_the_vault_step():
    # Step ordering is the whole point of fixing it at capture time.
    assert (cli.SETUP_STEPS.index("The Ollama address")
            < cli.SETUP_STEPS.index("Zone 1, the Vault"))


def test_the_whole_wizard_refuses_cleanly_and_exits_one(scratch, monkeypatch):
    monkeypatch.setattr(setup_ops, "list_models", lambda *a, **k: [])
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli._cmd_setup(argparse.Namespace(
            yes=True, ollama_url=PUBLIC, model=None, run_egress="island",
            build_network=None, egress_door=None))
    out = buf.getvalue()
    assert code == 1
    assert "Setup stopped -" in out
    assert "Traceback" not in out
    # Nothing got as far as the vault.
    assert "Zone 1, the Vault" not in out


# --- (b) the floor under every step ---------------------------------------


def test_an_unexpected_exception_never_reaches_the_owner_raw(
        scratch, monkeypatch):
    boom = RuntimeError("a bug nobody predicted\nsecond line of noise")

    def explode(*a, **k):
        cli.step_banner("Crew sizing")
        raise boom

    monkeypatch.setattr(cli, "_run_setup", explode)
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli._cmd_setup(_args())
    out = buf.getvalue()
    assert code == 2
    assert "Setup stopped at Step 5 of 14 - Crew sizing" in out
    assert "RuntimeError: a bug nobody predicted" in out
    # One line on screen, not a stack, and not the second line either.
    assert "Traceback" not in out
    assert "second line of noise" not in out
    assert "setup-crash-" in out


def test_a_crash_before_any_step_still_reads_as_english(scratch, monkeypatch):
    def explode(*a, **k):
        raise RuntimeError("died on import")

    monkeypatch.setattr(cli, "_run_setup", explode)
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert cli._cmd_setup(_args()) == 2
    out = buf.getvalue()
    assert "Setup stopped before the first step" in out
    assert "at before the first step" not in out


def test_the_crash_log_holds_the_traceback_the_screen_withheld(scratch):
    cli.step_banner("Crew sizing")
    try:
        raise RuntimeError("kaboom")
    except RuntimeError as exc:
        path = cli._crash_log_path(exc)
    assert path and os.path.exists(path)
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    assert "Traceback" in text
    assert "kaboom" in text
    assert "Step 5 of 14 - Crew sizing" in text


def test_ctrl_c_is_a_clean_stop_not_a_crash(scratch, monkeypatch):
    def interrupt(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_run_setup", interrupt)
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli._cmd_setup(_args())
    out = buf.getvalue()
    assert code == 1
    assert "Stopped." in out
    assert "Traceback" not in out


def test_a_deliberate_refusal_is_exit_one_and_a_bug_is_exit_two(
        scratch, monkeypatch):
    monkeypatch.setattr(cli, "_run_setup", lambda *a, **k: (_ for _ in ()).throw(
        cli.SetupRefused("nope", "because", ["do this"])))
    with redirect_stdout(io.StringIO()):
        assert cli._cmd_setup(_args()) == 1
    monkeypatch.setattr(cli, "_run_setup", lambda *a, **k: (_ for _ in ()).throw(
        ZeroDivisionError("oops")))
    with redirect_stdout(io.StringIO()):
        assert cli._cmd_setup(_args()) == 2


def test_the_step_label_tracks_whatever_step_last_printed():
    cli.step_banner("Posture")
    assert cli.current_step_label() == "Step 1 of 14 - Posture"
    cli.step_banner("Brain ready?")
    assert cli.current_step_label() == "Step 12 of 14 - Brain ready"
