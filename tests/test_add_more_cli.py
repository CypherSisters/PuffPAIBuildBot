"""``puffpai add``: the same loop from the terminal (G5, knitting).

Piped input never adds anything by default: ``--pick`` is required. The
terminal on this computer says yes by itself (no ticket to carry, no PIN).
"""
from __future__ import annotations

import io
import sys

import pytest

from add_more_support import built, guard_network
from puffpai import add_more, cli, plain_words, routines
from puffpai.security import consent_tickets


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    add_more.reset()
    consent_tickets.reset()
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))  # piped, not a keyboard
    yield
    add_more.reset()


def _run(capsys, *argv) -> tuple[int, str]:
    parser = cli.build_parser()
    args = parser.parse_args(["add", *argv])
    code = args.func(args)
    return code, capsys.readouterr().out


def _update_number(out: str) -> str:
    for line in out.splitlines():
        stripped = line.strip()
        if "A daily knitting update" in stripped and stripped[0].isdigit():
            return stripped.split(".", 1)[0]
    raise AssertionError(out)


def test_piped_input_needs_pick_and_adds_nothing(capsys, monkeypatch):
    built()
    seen = guard_network(monkeypatch)
    code, out = _run(capsys, "knitting and crochet patterns")
    assert code == 1
    assert "Nothing was added: choose with --pick 1,3 or --pick all." in out
    assert "Add to Rexy" in out and "What it will know:" in out
    assert plain_words.check(out) == []
    assert seen == [] and routines.get_routine("daily_update") is None


def test_g5_pick_adds_the_daily_update_and_lists_and_takes_it_out(capsys,
                                                                   monkeypatch):
    built(goal="A knitting helper")
    guard_network(monkeypatch)
    _code, out = _run(capsys, "knitting and crochet patterns")
    number = _update_number(out)
    code, out = _run(capsys, "knitting and crochet patterns", "--pick", number)
    assert code == 0, out
    assert "Added: a short update every day at 8" in out
    assert "puffpai add --take-out" in out
    assert plain_words.check(out) == []
    assert routines.get_routine("daily_update")["enabled"]
    code, out = _run(capsys, "--list")
    assert out.startswith("1. A daily knitting update")
    code, out = _run(capsys, "--take-out", "1")
    assert code == 0 and "Took out the daily update" in out
    assert routines.get_routine("daily_update") is None


def test_a_bad_pick_is_refused(capsys, monkeypatch):
    built()
    guard_network(monkeypatch)
    code, out = _run(capsys, "knitting and crochet patterns", "--pick", "99")
    assert code == 1 and "--pick takes numbers from 1 to" in out


def test_nothing_found_in_the_terminal(capsys, monkeypatch):
    built()
    guard_network(monkeypatch)
    code, out = _run(capsys, "zxqv blorf")
    assert code == 0
    assert "I can't make new tools yet." in out
    assert "puffpai add --forget-notes" in out
    code, out = _run(capsys, "--notes")
    assert "zxqv, blorf" in out
    code, out = _run(capsys, "--forget-notes")
    assert "Forgot 1 notes." in out


def test_no_words_says_how(capsys):
    code, out = _run(capsys)
    assert code == 1 and 'puffpai add "bird songs"' in out
