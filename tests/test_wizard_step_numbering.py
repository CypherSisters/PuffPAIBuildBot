"""W3 - the wizard's step numbers and the doc's step numbers are one list.

docs/GETTING_STARTED.md numbered the
zones 6/7/8/9 while the screen printed 4/5/6/7, five sections printed no step
header at all (so the wizard appeared to jump Step 0 -> Step 4, which on camera
reads as a cut recording), and line 174 called the brain check "Step 3" while
the screen called it "Step 8".

The fix is one ordered list, ``cli.SETUP_STEPS``, rendered by
``cli.step_banner``. This file guards that list and its banners.

The fourteen numbered steps are retired from what an owner sees: the
wizard is three questions and one summary, and docs/GETTING_STARTED.md
describes that instead. The doc half of this file went with it;
tests/test_docs_truth.py now fails if the docs describe the old steps again.

Run:
    pytest tests/test_wizard_step_numbering.py
"""
from __future__ import annotations

import os

import pytest

from puffpai import cli

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --- the list itself ------------------------------------------------------


def test_there_are_fourteen_steps():
    assert len(cli.SETUP_STEPS) == 14
    assert len(set(cli.SETUP_STEPS)) == 14


def test_the_banner_numbers_from_one_and_names_the_total():
    assert cli.step_banner("Posture") == "Step 1 of 14 - Posture."
    assert cli.step_banner("The closing screen") == "Step 14 of 14 - The closing screen."
    # A name that already ends in punctuation is not given a second period.
    assert cli.step_banner("Brain ready?") == "Step 12 of 14 - Brain ready?"
    assert cli.step_banner("Integrations", "And then some.") == (
        "Step 11 of 14 - Integrations. And then some.")


def test_a_step_the_list_does_not_know_is_an_error_not_a_wrong_number():
    with pytest.raises(ValueError):
        cli.step_banner("Step we renamed and forgot")


# --- and the wizard must actually print them ------------------------------


def test_every_step_name_is_printed_somewhere_in_the_wizard():
    with open(os.path.join(_REPO_ROOT, "src", "puffpai", "cli.py"),
              "r", encoding="utf-8") as fh:
        source = fh.read()
    # The closing screen is the handoff card now, which prints no
    # numbered header; the old one that did was never called and is gone.
    for name in [n for n in cli.SETUP_STEPS if n != "The closing screen"]:
        assert f'step_banner("{name}"' in source, (
            f"No step_banner call for {name!r}; the wizard would walk a step "
            "the doc promises and print no header for it.")
