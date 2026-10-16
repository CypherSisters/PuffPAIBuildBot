"""The "which agents do I have?" answer is the roster, not a memory of it.

The voice and HA-Assist routes both land on
``api_server._maybe_puffpai_facts_answer``. It used to answer that the team was
"Cortex, Bridge and Drawbridge" - three agents, two of them names this product
retired - because the paragraph was hand-written and nothing tied it to
``puffpai.agents.registry.ROSTER``. These tests tie it.

Run:
    pytest tests/test_agent_roster_faq.py
"""
from __future__ import annotations

import re

import pytest

from puffpai import api_server
from puffpai.agents import registry

#: Names the product used before the six-agent roster. Saying either of these
#: out loud is the regression.
RETIRED_NAMES = ("Cortex", "Bridge")

QUESTIONS = ("what agents do i have", "who is on my team")


def _answers():
    for question in QUESTIONS:
        for rich in (False, True):
            answer = api_server._maybe_puffpai_facts_answer(question, rich=rich)
            assert answer, f"no answer for {question!r} (rich={rich})"
            yield question, rich, answer


@pytest.mark.parametrize("entry", registry.ROSTER, ids=lambda e: e.name)
def test_the_faq_names_every_agent_on_the_roster(entry):
    expected = entry.display_name or entry.name.capitalize()
    for question, rich, answer in _answers():
        assert expected in answer, (
            f"{expected} missing from the answer to {question!r} (rich={rich})")


def test_the_faq_never_says_a_retired_name():
    for question, rich, answer in _answers():
        for retired in RETIRED_NAMES:
            # \b so "Drawbridge" does not read as "Bridge".
            assert not re.search(rf"\b{retired}\b", answer), (
                f"retired name {retired} in the answer to {question!r} "
                f"(rich={rich})")


def test_the_faq_counts_the_agents_it_actually_lists():
    """"three agents" outlived the three-agent team. The count is derived."""
    spelled = api_server._spell_small_number(len(registry.ROSTER))
    for _question, _rich, answer in _answers():
        assert f"{spelled} " in answer.lower()


def test_every_roster_entry_carries_the_copy_the_answer_is_built_from():
    """A new agent with no blurb would silently shrink the answer."""
    for name, role, blurb in registry.roster_blurbs():
        assert name and role and blurb.endswith("."), f"incomplete roster copy: {name}"
