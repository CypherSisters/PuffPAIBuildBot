"""PB-003: the recovery code typed back is not echoed, and the screen is cleared.

An outside test ran the terminal wizard over SSH inside tmux. The code the
owner typed back was echoed, so it sat in the scroll history a second time,
and the screen said the code was "gone from the screen and from memory",
which a terminal with scroll history (and tmux) makes false.

So: the typed-back code is read the way the PIN is read (no echo), the
screen and its scroll history are cleared after it matches (best effort, and
only on a terminal, never into a pipe or a log), and the warning says only
what is true.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import io
import logging
import subprocess
import types

import pytest

from puffpai import cli, plain_words, setup_ops, zone_vault
from puffpai.wizard import render_cli, runner as wizard_runner, secure_vault

#: Not a real code: a stand-in that only has to be findable in output.
CODE = "TESTC-ODE00-00000-00000-00001"


@pytest.fixture
def no_vault(monkeypatch):
    """Code checks without a vault: same text matches, nothing rotates."""
    made = []

    def rotate(code, pin, vault=None):
        made.append(code)
        return f"NEW-{len(made)}"
    monkeypatch.setattr(setup_ops, "rotate_vault_recovery", rotate)
    monkeypatch.setattr(setup_ops, "confirm_vault_recovery",
                        lambda shown, typed: shown == typed)
    return made


class Face:
    """A face whose clear-text reader must never be used for the code."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.events: list[tuple[str, object]] = []
        self.prompts: list[str] = []

    def show(self, lines):
        self.events.append(("show", list(lines)))

    def ask(self, prompt):
        raise AssertionError("the code typed back was read with echo on")

    def ask_secret(self, prompt):
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else ""

    def clear(self):
        self.events.append(("clear", None))

    def cleared(self) -> int:
        return sum(1 for kind, _ in self.events if kind == "clear")


def _ctx(face):
    return wizard_runner.RunContext(
        args=types.SimpleNamespace(), cfg=types.SimpleNamespace(),
        non_interactive=True, shared={}, sections={}, package=None,
        show_once=face)


# -- the read is hidden -------------------------------------------------------

def test_the_code_typed_back_goes_through_the_hidden_reader(no_vault):
    face = Face([CODE])
    assert secure_vault.show_and_confirm(CODE, "p", None, sink=face,
                                         non_interactive=False) is True
    assert len(face.prompts) == 1
    assert secure_vault.HIDDEN_NOTE in face.prompts[0]


def test_a_face_that_can_only_echo_is_nobody(no_vault):
    class EchoOnly:
        def __init__(self):
            self.shown = []

        def show(self, lines):
            self.shown.extend(lines)

        def ask(self, prompt):
            return CODE

    face = EchoOnly()
    assert secure_vault.face_of(_ctx(face)) is None
    assert secure_vault.can_show(_ctx(face)) is False
    assert _ctx(face).face() is None
    # Called directly anyway: refused before the code is put on the screen.
    assert secure_vault.show_and_confirm(CODE, "p", None, sink=face,
                                         non_interactive=False) is False
    assert face.shown == []


def test_the_console_face_reads_back_with_the_pin_reader(monkeypatch):
    """ConsoleShowOnce.ask_secret is render_cli._read_secret, and on a
    terminal that is getpass (no echo), never input()."""
    hidden = []
    monkeypatch.setattr(render_cli, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(render_cli.getpass, "getpass",
                        lambda prompt="": hidden.append(prompt) or "typed")

    def no_input(*_a, **_k):
        raise AssertionError("input() echoes")
    monkeypatch.setattr("builtins.input", no_input)
    face = render_cli.ConsoleShowOnce(out=lambda line: None)
    assert face.ask_secret("type it back") == "typed"
    assert hidden == [render_cli.ANSWER_MARK]


def test_the_terminal_ceremony_reads_back_with_the_pin_reader(no_vault, monkeypatch,
                                                              capsys):
    hidden = []
    monkeypatch.setattr(cli, "_read_pin",
                        lambda prompt: hidden.append(prompt) or CODE)
    monkeypatch.setattr(cli, "_read_line", lambda prompt: "")
    monkeypatch.setattr(render_cli, "clear_terminal", lambda *a, **k: False)
    assert cli._recovery_ceremony(CODE, "p", None) is True
    assert len(hidden) == 1
    assert hidden[0].strip().startswith("Type the recovery code back in.")
    out = capsys.readouterr().out
    assert "That matches." in out
    # Shown once (the screen it is written down from), never echoed back.
    assert out.count(CODE) == 1


# -- the screen is cleared after a match, and only then ----------------------

def test_a_match_clears_the_screen_before_saying_so(no_vault):
    face = Face([CODE])
    secure_vault.show_and_confirm(CODE, "p", None, sink=face,
                                  non_interactive=False)
    kinds = [kind for kind, _ in face.events]
    at = kinds.index("clear")
    assert kinds.count("clear") == 1
    after = [line for kind, lines in face.events[at + 1:] if kind == "show"
             for line in lines]
    assert any("That matches" in line for line in after)
    assert not any(CODE in line for line in after)


@pytest.mark.parametrize("answers", [["skip"], [""],
                                     ["wrong"] * 20])
def test_no_clear_while_the_code_is_still_needed(no_vault, answers):
    """Declined or never matched: the code on the screen still opens the
    vault, so it is left there for the owner to copy."""
    face = Face(answers)
    assert secure_vault.show_and_confirm(CODE, "p", None, sink=face,
                                         non_interactive=False) is False
    assert face.cleared() == 0


def test_a_clear_that_fails_never_fails_the_ceremony(no_vault):
    face = Face([CODE])

    def boom():
        raise OSError("no terminal")
    face.clear = boom
    assert secure_vault.show_and_confirm(CODE, "p", None, sink=face,
                                         non_interactive=False) is True
    console = render_cli.ConsoleShowOnce(out=lambda line: None, clear=boom)
    console.clear()  # swallowed


class _Stream(io.StringIO):
    def __init__(self, tty):
        super().__init__()
        self._tty = tty

    def isatty(self):
        if isinstance(self._tty, Exception):
            raise self._tty
        return self._tty


def test_the_clear_sequence_goes_to_a_terminal(monkeypatch):
    monkeypatch.setattr(render_cli, "_windows_vt_on", lambda stream: True)
    stream = _Stream(True)
    assert render_cli.clear_terminal(stream, env={}) is True
    # Screen first, history last: a 2J after the 3J would put the screen
    # (and the code) back into the history on some terminals.
    assert stream.getvalue() == "\x1b[H\x1b[2J\x1b[3J"
    assert stream.getvalue().endswith("\x1b[3J")


@pytest.mark.parametrize("tty", [False, ValueError("closed")])
def test_the_clear_sequence_never_goes_to_a_pipe_or_a_log(monkeypatch, tty):
    monkeypatch.setattr(render_cli, "_windows_vt_on", lambda stream: True)
    ran = []
    stream = _Stream(tty)
    assert render_cli.clear_terminal(
        stream, env={"TMUX": "/tmp/tmux-1/default,1,0", "TMUX_PANE": "%1"},
        run=lambda *a, **k: ran.append(a), tmux=lambda: "/usr/bin/tmux") is False
    assert stream.getvalue() == ""
    assert ran == []


def test_a_windows_console_that_cannot_read_it_gets_nothing(monkeypatch):
    monkeypatch.setattr(render_cli, "_windows_vt_on", lambda stream: False)
    stream = _Stream(True)
    assert render_cli.clear_terminal(stream, env={}) is False
    assert stream.getvalue() == ""


def test_a_stream_that_breaks_is_left_alone(monkeypatch):
    monkeypatch.setattr(render_cli, "_windows_vt_on", lambda stream: True)

    class Broken(_Stream):
        def write(self, text):
            raise OSError("gone")
    assert render_cli.clear_terminal(Broken(True), env={}) is False


def test_under_tmux_the_pane_history_is_cleared_too(monkeypatch):
    monkeypatch.setattr(render_cli, "_windows_vt_on", lambda stream: True)
    ran = []
    render_cli.clear_terminal(
        _Stream(True), env={"TMUX": "/tmp/tmux-1/default,1,0", "TMUX_PANE": "%7"},
        run=lambda argv, **kw: ran.append((argv, kw)), tmux=lambda: "/usr/bin/tmux")
    assert len(ran) == 1
    argv, kw = ran[0]
    assert argv == ["/usr/bin/tmux", "clear-history", "-t", "%7"]
    # Nothing tmux says reaches the screen or a log; it never waits forever.
    assert kw["stdout"] is subprocess.DEVNULL and kw["stderr"] is subprocess.DEVNULL
    assert kw["stdin"] is subprocess.DEVNULL and kw["timeout"] <= 5


@pytest.mark.parametrize("pane", ["", "%7; rm -rf ~", "-t", "7"])
def test_an_odd_pane_id_is_not_handed_to_tmux(monkeypatch, pane):
    monkeypatch.setattr(render_cli, "_windows_vt_on", lambda stream: True)
    ran = []
    render_cli.clear_terminal(
        _Stream(True), env={"TMUX": "x", "TMUX_PANE": pane},
        run=lambda argv, **kw: ran.append(argv), tmux=lambda: "/usr/bin/tmux")
    assert ran == [["/usr/bin/tmux", "clear-history"]]


def test_no_tmux_no_tmux_call(monkeypatch):
    monkeypatch.setattr(render_cli, "_windows_vt_on", lambda stream: True)
    ran = []
    render_cli.clear_terminal(_Stream(True), env={},
                              run=lambda argv, **kw: ran.append(argv),
                              tmux=lambda: "/usr/bin/tmux")
    render_cli.clear_terminal(_Stream(True), env={"TMUX": "x"},
                              run=lambda argv, **kw: ran.append(argv),
                              tmux=lambda: None)
    assert ran == []


def test_tmux_failing_never_fails_the_wizard(monkeypatch):
    monkeypatch.setattr(render_cli, "_windows_vt_on", lambda stream: True)

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("tmux", 3)
    assert render_cli.clear_terminal(_Stream(True), env={"TMUX": "x"},
                                     run=boom, tmux=lambda: "/usr/bin/tmux") is True


def test_tmux_is_found_by_fixed_path_only():
    for place in render_cli._TMUX_PLACES:
        assert place.startswith("/") and place.endswith("/tmux")


# -- the words say only what is true ------------------------------------------

def test_the_warning_makes_no_false_claim():
    text = zone_vault.RECOVERY_SHOWN_ONCE_WARNING
    low = text.lower()
    assert "gone from the screen" not in low
    assert "from memory" not in low
    assert "scroll history" in low
    screen = " ".join(zone_vault.recovery_notice_lines()).lower()
    assert "gone from the screen" not in screen and "from memory" not in screen


def test_the_new_words_are_plain():
    for text in (zone_vault.RECOVERY_SHOWN_ONCE_WARNING, secure_vault.HIDDEN_NOTE):
        assert text.isascii()
        assert "—" not in text and "–" not in text
        assert "door" not in text.lower() and "beta" not in text.lower()
        assert plain_words.check(text) == [], plain_words.check(text)


# -- the code reaches no log --------------------------------------------------

def test_the_code_reaches_no_log(no_vault, caplog):
    caplog.set_level(logging.DEBUG)
    for answers in ([CODE], ["skip"], ["wrong"] * 20):
        secure_vault.show_and_confirm(CODE, "p", None, sink=Face(answers),
                                      non_interactive=False)
    assert CODE not in caplog.text
