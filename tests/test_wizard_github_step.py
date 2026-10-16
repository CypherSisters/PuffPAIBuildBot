"""The browser wizard's "Do you have a GitHub account?" question.

It is asked once, after the goal and before the plan, and never blocks: the
flow's ``next`` is unchanged by it (the terminal asks the same steps in the
same order), and ``github.asked`` says whether the browser still has to ask.
Answering records only that it was asked.

Run:
    pytest tests/test_wizard_github_step.py
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_wizard_needs_step import install_fakes, pinned_machine  # noqa: E402


@pytest.fixture
def wizard(monkeypatch, tmp_path):
    from puffpai.wizard import service
    monkeypatch.setattr(service, "finding_engine", lambda: None)
    pinned_machine(monkeypatch, tmp_path)
    install_fakes(monkeypatch, "garden helper")
    return service


@pytest.mark.parametrize("choice", ["connected", "skipped"])
def test_asked_once_and_the_order_is_unchanged(wizard, choice):
    service = wizard
    assert service.flow()["github"] == {"asked": False}
    service.first_look_choose()
    assert service.flow()["next"] == "goal"
    service.apply_skill("purpose:goal", {"goal": "Help me water the garden."})
    flow = service.flow()
    assert flow["next"] == "identity", "the question never takes a step of its own"
    assert flow["github"]["asked"] is False
    assert service.github_choose(choice) == {"choice": choice}
    flow = service.flow()
    assert flow["github"]["asked"] is True
    assert flow["next"] == "identity"
    log = [row for row in service.run_state()["log"] if row.get("part") == "github"]
    assert len(log) == 1 and choice in log[0]["note"]


@pytest.mark.parametrize("bad", [None, "", "yes", "ghp_abc", 1, {"choice": "skipped"}])
def test_anything_else_is_refused(wizard, bad):
    from puffpai.wizard import cards
    with pytest.raises(cards.CardError):
        wizard.github_choose(bad)
    assert wizard.flow()["github"]["asked"] is False
