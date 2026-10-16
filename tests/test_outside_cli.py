"""``puffpai outside``: the terminal's own wording, the same switches."""

from __future__ import annotations

import io

import pytest

from livedata_support import jargon_in, set_posture
from puffpai import cli
from puffpai.security import (consent_tickets, egress_doors, egress_ledger,
                              outside_switches)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for d in egress_doors.DOORS:
        monkeypatch.delenv(d.env_var, raising=False)
    outside_switches.forget()
    consent_tickets.reset()
    egress_doors.reset_cache()
    yield
    consent_tickets.reset()
    egress_doors.reset_cache()


def _run(argv, capsys):
    code = cli.main(argv)
    return code, capsys.readouterr().out


def test_list_numbers_every_service_in_plain_words(capsys):
    set_posture("enumerated")
    code, out = _run(["outside"], capsys)
    assert code == 0
    assert "What leaves your computer" in out
    assert " 1. Weather forecast - Switched off." in out
    assert "puffpai outside on 1" in out
    assert "Ollama" in out and "cannot see or stop" in out
    assert not jargon_in(out.replace("puffpai outside", ""))


def test_off_takes_one_command(capsys):
    set_posture("enumerated")
    outside_switches.set_switch("weather", True, by="owner")
    egress_doors.reset_cache()
    code, out = _run(["outside", "off", "weather forecast"], capsys)
    assert code == 0 and "Switched off." in out
    assert egress_doors.egress_allowed("weather")[0] is False


def test_on_prints_the_sentence_and_needs_a_typed_yes(capsys, monkeypatch):
    set_posture("enumerated")
    monkeypatch.setattr("sys.stdin", io.StringIO(""))  # not a terminal
    code, out = _run(["outside", "on", "1"], capsys)
    assert code == 1 and "What leaves your computer" in out
    assert "Nothing was switched on" in out
    assert egress_doors.egress_allowed("weather")[0] is False


def test_on_with_yes_switches_exactly_that_service(capsys):
    set_posture("enumerated")
    code, out = _run(["outside", "on", "1", "--yes"], capsys)
    assert code == 0 and "Weather forecast: Switched on." in out
    assert egress_doors.egress_allowed("weather")[0] is True
    assert egress_doors.egress_allowed("air_quality")[0] is False


def test_the_log_prints_plain_lines(capsys):
    set_posture("enumerated")
    code, out = _run(["outside", "log"], capsys)
    assert code == 0 and "no requests" in out
    egress_ledger.record("geocode", host="geocoding-api.open-meteo.com",
                         outcome="done", what="town_lookup", bytes_in=900)
    code, out = _run(["outside", "log"], capsys)
    assert "Town lookup - looked up a town name - less than 1 KB" in out


def test_an_unknown_service_is_named_plainly(capsys):
    set_posture("enumerated")
    code, out = _run(["outside", "off", "99"], capsys)
    assert code == 1 and "There is no outside service" in out
