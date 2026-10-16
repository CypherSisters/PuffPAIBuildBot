"""The docs quote the wizard. This file stops the quotes from rotting.

Two agents worked on BuildBot the same night: one rewrote the setup wizard's
closing screen, the other rewrote the docs that quote it verbatim. They landed
independently, and the docs quoted the *old* screen -- telling a stranger to run
``npm run dev`` while the wizard printed ``npm run build && npm run start``.
That is the exact class of bug a stranger hits and we never do, because we never
read our own getting-started page.

So the strings that appear in both places are pinned here, from the source of
truth (the constants and the handoff skill that prints them), against the shipped
Markdown. If someone edits one side, this fails.

Run:
    pytest tests/test_docs_quote_wizard_screen.py
"""
from __future__ import annotations

import os

from puffpai import cli

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(relpath: str) -> str:
    with open(os.path.join(_REPO_ROOT, relpath), "r", encoding="utf-8") as fh:
        return fh.read()


# --- the dashboard start command ------------------------------------------


def test_getting_started_quotes_the_dashboard_start_command_verbatim():
    assert cli.DASHBOARD_START_COMMAND in _read("docs/GETTING_STARTED.md"), (
        "docs/GETTING_STARTED.md no longer quotes "
        f"cli.DASHBOARD_START_COMMAND ({cli.DASHBOARD_START_COMMAND!r}). "
        "The wizard and the docs have drifted -- update the doc, not this test."
    )


def test_the_docs_do_not_tell_a_stranger_to_run_the_dev_server_to_finish_setup():
    # `npm run dev` is still legitimately mentioned in the "developing on the
    # dashboard" section; what must not come back is a *setup* instruction to
    # start the dashboard with it.
    text = _read("docs/GETTING_STARTED.md")
    assert "cd dashboard && npm install && npm run dev" not in text


# --- the closing screen ---------------------------------------------------
#
# The closing screen an owner sees is the handoff card ("How to start
# it"). The doc quotes a real run of it; these pin the labels it quotes to the
# code that prints them.


def test_the_documented_closing_labels_are_the_ones_the_handoff_prints():
    text = _read("docs/GETTING_STARTED.md")
    with open(os.path.join(_REPO_ROOT, "src", "puffpai", "wizard", "packages",
                           "handoff", "skill.py"), encoding="utf-8") as fh:
        source = fh.read()
    for label in ("How to start it", "Where this build got to",
                  "Added for you", "Learning in the background"):
        assert f'"{label}"' in source, f"the handoff no longer prints {label!r}"
        assert label in text, f"GETTING_STARTED.md no longer quotes {label!r}"


# --- the README no longer claims fixed things are broken -------------------


def test_readme_known_gaps_does_not_list_rows_that_are_fixed():
    text = _read("README.md")
    gaps = text[text.index("Known gaps:") :]
    gaps = gaps[: gaps.index("\n\n")]
    for row in (
        "BB-BLD-01",
        "BB-LLM-02",
        "BB-SYS-01",
        "BB-CEN-04",
        "BB-CEN-05",
        "BB-WIZ-S10",
    ):
        assert row not in gaps, (
            f"README still lists {row} as a known gap, but it was fixed. "
            "A README that claims a fixed bug is as wrong as one that hides "
            "a real one."
        )
