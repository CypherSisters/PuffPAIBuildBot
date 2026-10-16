"""Declining to type the recovery code back keeps the code.

Six runs answered "skip" and got a new code each
time, three times, then "the code was not confirmed" - the owner was left with
no code they had kept. Declining (Enter or "skip") now keeps the code on the
screen and moves on; only wrong answers lead to a fresh code.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import pytest

from puffpai import cli, setup_ops
from puffpai.wizard import secure_vault


class Face:
    def __init__(self, answers):
        self.answers = list(answers)
        self.shown: list[str] = []

    def show(self, lines):
        self.shown.extend(lines)

    def ask(self, prompt):
        return self.answers.pop(0) if self.answers else ""


@pytest.fixture
def rotations(monkeypatch):
    made = []

    def rotate(code, pin, vault=None):
        made.append(code)
        return f"NEW-{len(made)}"
    monkeypatch.setattr(setup_ops, "rotate_vault_recovery", rotate)
    monkeypatch.setattr(setup_ops, "confirm_vault_recovery",
                        lambda shown, typed: shown == typed)
    return made


@pytest.mark.parametrize("answer", ["", "skip", "later"])
def test_declining_keeps_the_code(rotations, answer):
    face = Face([answer])
    assert secure_vault.show_and_confirm("CODE-1", "1234567890", None, sink=face,
                                         non_interactive=False) is False
    assert rotations == []
    text = "\n".join(face.shown)
    assert "That code still works" in text
    assert "now dead" not in text and "fresh one" not in text
    assert text.count("CODE-1") == 1


def test_a_right_answer_confirms(rotations):
    face = Face(["CODE-1"])
    assert secure_vault.show_and_confirm("CODE-1", "p", None, sink=face,
                                         non_interactive=False) is True
    assert rotations == []


def test_only_wrong_answers_make_a_fresh_code(rotations):
    wrong = ["nope"] * cli._RECOVERY_MAX_MISMATCHES
    face = Face(wrong + ["NEW-1"])
    assert secure_vault.show_and_confirm("CODE-1", "p", None, sink=face,
                                         non_interactive=False) is True
    assert rotations == ["CODE-1"]
    assert any("Here is a fresh one" in line for line in face.shown)


def test_a_young_builder_is_told_to_get_a_grown_up(rotations):
    face = Face(["skip"])
    secure_vault.show_and_confirm("CODE-1", "p", None, sink=face,
                                  non_interactive=False, young=True)
    assert any("grown-up" in line for line in face.shown)
    face = Face(["skip"])
    secure_vault.show_and_confirm("CODE-1", "p", None, sink=face,
                                  non_interactive=False)
    assert not any("grown-up" in line for line in face.shown)


def test_the_terminal_ceremony_runs_the_same_policy(rotations, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_read_line", lambda prompt: "skip")
    assert cli._recovery_ceremony("CODE-1", "p", None) is False
    out = capsys.readouterr().out
    assert rotations == [] and "That code still works" in out
