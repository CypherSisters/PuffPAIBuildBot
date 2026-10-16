"""BB-CEN-05 / BB-CEN-04 — detection that fails must recover, not shrug.

WRECK §B2, verbatim: "when *RAM* detection fails the wizard asks the owner and
accepts a typed answer (BB-CEN-02, CONFIRMED twice). When *Ollama* detection
fails it shrugs and proceeds." Table row 7: "Ollama on a non-default port:
wizard detects failure, never asks for a URL, writes :11434 anyway."

And the nitpick that is really a validation hole: "stated-RAM accepts -5 and
999999" — the figure every model on the brain menu is fitted against.

Run:
    pytest tests/test_wizard_ollama_and_ram_detection.py
"""
from __future__ import annotations

import argparse
import os

import pytest

from puffpai import brain_health, cli, hardware_census, setup_ops
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
    base = dict(yes=False, ollama_url=None, stated_ram=None)
    base.update(kw)
    return argparse.Namespace(**base)


def _reachable(monkeypatch, mapping):
    """Only the URLs in ``mapping`` answer; everything else is dead."""
    monkeypatch.setattr(setup_ops, "list_models",
                        lambda url=None, timeout=3: mapping.get(url))


# ── BB-CEN-05: finding an Ollama that is not at the default address ─────────

def test_the_configured_address_is_used_when_it_answers(scratch, monkeypatch):
    _reachable(monkeypatch, {"http://localhost:11434": ["gemma3:4b"]})
    url, models = cli._setup_ollama_url_step(
        _args(), get_config(), non_interactive=False)
    assert url == "http://localhost:11434"
    assert models == ["gemma3:4b"]


def test_a_non_default_port_is_found_without_asking(scratch, monkeypatch, capsys):
    """The WSL/Docker/second-port case: probe before you interrogate."""
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    _reachable(monkeypatch, {"http://127.0.0.1:11435": ["gemma3:4b"]})

    url, models = cli._setup_ollama_url_step(
        _args(), get_config(), non_interactive=False)

    assert url == "http://127.0.0.1:11435"
    assert models == ["gemma3:4b"]
    assert "Found Ollama at http://127.0.0.1:11435" in capsys.readouterr().out


def test_ollama_host_is_honoured(scratch, monkeypatch):
    """Somebody who set OLLAMA_HOST has already answered the question."""
    monkeypatch.setenv("OLLAMA_HOST", "10.0.0.9:11434")
    _reachable(monkeypatch, {"http://10.0.0.9:11434": []})

    url, _ = cli._setup_ollama_url_step(
        _args(), get_config(), non_interactive=False)
    assert url == "http://10.0.0.9:11434"


def test_when_nothing_is_found_the_wizard_asks_and_accepts_the_answer(
        scratch, monkeypatch):
    """The B2 failure itself: it must ask, not write :11434 anyway."""
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    _reachable(monkeypatch, {"http://192.168.1.50:11434": ["gemma3:4b"]})
    asked = []

    def _ask(prompt, default="", **kw):
        asked.append(prompt)
        return "192.168.1.50:11434"

    monkeypatch.setattr(cli, "_ask", _ask)

    url, models = cli._setup_ollama_url_step(
        _args(), get_config(), non_interactive=False)

    assert asked, "the wizard never asked for an address"
    assert url == "http://192.168.1.50:11434"
    assert models == ["gemma3:4b"]


def test_a_typed_address_is_validated_before_it_is_used(scratch, monkeypatch, capsys):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    _reachable(monkeypatch, {"http://192.168.1.50:11434": []})
    answers = iter(["not a url at all", "192.168.1.50:11434"])
    monkeypatch.setattr(cli, "_ask", lambda *a, **k: next(answers))

    url, _ = cli._setup_ollama_url_step(
        _args(), get_config(), non_interactive=False)

    assert url == "http://192.168.1.50:11434"
    assert "is not an address I can use" in capsys.readouterr().out


def test_an_explicit_ollama_url_flag_is_never_second_guessed(scratch, monkeypatch):
    """A scripted install must land where it was pointed, or nowhere."""
    _reachable(monkeypatch, {"http://127.0.0.1:11435": ["gemma3:4b"]})
    monkeypatch.setattr(cli, "_ask",
                        lambda *a, **k: pytest.fail("asked despite --ollama-url"))

    url, models = cli._setup_ollama_url_step(
        _args(ollama_url="http://elsewhere:11434"), get_config(),
        non_interactive=False)

    assert url == "http://elsewhere:11434"
    assert models is None


def test_a_non_interactive_run_never_blocks_on_the_question(scratch, monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    _reachable(monkeypatch, {})
    monkeypatch.setattr(cli, "_ask",
                        lambda *a, **k: pytest.fail("asked in a --yes run"))

    url, models = cli._setup_ollama_url_step(
        _args(yes=True), get_config(), non_interactive=True)

    assert url == get_config().ollama.url
    assert models is None


def test_the_ollama_url_flag_is_documented_in_help():
    """WRECK: "the only fix is an undocumented flag"."""
    parser = cli.build_parser()
    setup = parser._subparsers._group_actions[0].choices["setup"]  # type: ignore[attr-defined]
    help_text = setup.format_help()
    assert "--ollama-url" in help_text
    assert "OLLAMA_HOST" in help_text
    assert "11435" in help_text


def test_url_shapes():
    assert brain_health.valid_url("localhost:11434") == "http://localhost:11434"
    assert brain_health.valid_url("http://box:11434/") == "http://box:11434"
    assert brain_health.valid_url("") is None
    assert brain_health.valid_url("   ") is None
    assert brain_health.valid_url("ftp://box:11434") is None
    assert brain_health.valid_url("http://") is None
    assert brain_health.valid_url("not-a-url") is None
    assert brain_health.valid_url("192.168.1.5:11434") == "http://192.168.1.5:11434"
    assert brain_health.valid_url("http://host") == "http://host"


def test_the_vault_pin_flag_documents_the_digit_minimum():
    """The vault PIN policy (10-digit minimum for digits-only PINs) is
    documented on the flag itself, not just discoverable by trial and error."""
    parser = cli.build_parser()
    setup = parser._subparsers._group_actions[0].choices["setup"]  # type: ignore[attr-defined]
    help_text = setup.format_help()
    assert "10 digits" in help_text


# ── BB-CEN-04: a stated RAM figure is checked ───────────────────────────────

@pytest.mark.parametrize("bad", ["-5", "999999", "0", "abc", "-0.5", "nan"])
def test_a_bad_stated_ram_is_refused(bad):
    value, reason = cli._valid_ram_gb(bad)
    assert value is None
    assert reason, f"{bad!r} was refused with no explanation"


@pytest.mark.parametrize("good,expected", [("8", 8.0), ("61.4", 61.4),
                                           ("4096", 4096.0), ("0.5", 0.5)])
def test_a_good_stated_ram_is_accepted(good, expected):
    value, reason = cli._valid_ram_gb(good)
    assert value == expected
    assert reason == ""


def test_the_flag_form_of_a_bad_figure_is_ignored_not_applied(
        scratch, monkeypatch, capsys):
    """`--stated-ram -5` must not reach the census."""
    monkeypatch.setattr(hardware_census, "run_census",
                        lambda: {"memory": {"total_gb": 16.0}, "notes": [],
                                 "hardware_class": "unknown"})
    monkeypatch.setattr(hardware_census, "census_summary_lines", lambda c: [])
    monkeypatch.setattr(hardware_census, "apply_stated",
                        lambda c, **k: pytest.fail("a refused figure was applied"))

    census = cli._setup_hardware_step(_args(stated_ram=-5.0),
                                      non_interactive=True)

    assert census["memory"]["total_gb"] == 16.0
    assert "Ignoring --stated-ram" in capsys.readouterr().out


def test_a_bad_typed_figure_is_re_asked(scratch, monkeypatch, capsys):
    """Same discipline as every other prompt: say why, then ask again."""
    monkeypatch.setattr(hardware_census, "run_census",
                        lambda: {"memory": {}, "notes": [],
                                 "hardware_class": "unknown"})
    monkeypatch.setattr(hardware_census, "census_summary_lines", lambda c: [])
    answers = iter(["-5", "999999", "16"])
    monkeypatch.setattr(cli, "_ask", lambda *a, **k: next(answers))

    census = cli._setup_hardware_step(_args(), non_interactive=False)

    assert census["memory"]["total_gb"] == 16.0
    out = capsys.readouterr().out
    assert "not an amount of memory" in out
    assert "past anything this fits a brain for" in out
