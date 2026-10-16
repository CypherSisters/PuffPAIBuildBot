"""Three sample goals under the wizard's goal question.

Each says what the bot is for, then what it should do (the shape that builds
well); they come from the one wording list, in both wordings, and ride on the
goal field as ``examples`` (the browser's buttons fill the box). A card may
carry at most three, each a short plain sentence.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import pytest

from puffpai import plain_words
from puffpai.wizard import cards, wording
from test_wizard_purpose import GOAL, _args, _ctx, _module, fresh  # noqa: F401


@pytest.mark.parametrize("mode", wording.MODES)
def test_three_plain_sample_goals_in_each_wording(mode):
    examples = wording.goal_examples(mode)
    assert len(examples) == 3 and len(set(examples)) == 3
    for example in examples:
        what, _, does = example.partition(": ")
        assert what and does, example                 # what it is for: what it does
        assert len(example) <= cards.MAX_HELP
        assert not plain_words.check(example), example
        assert "—" not in example


def test_the_goal_card_carries_them(fresh):  # noqa: F811
    module, package = _module()
    card = cards.validate_card(module.propose(
        _ctx(package, args=_args(goal=GOAL), part="goal"))["card"])
    goal = {f["id"]: f for f in card["fields"]}["goal"]
    assert goal["examples"] == wording.goal_examples(wording.STANDARD)


@pytest.mark.parametrize("bad", ["one", ["a", "b", "c", "d"], ["fine", ""], [3]])
def test_a_card_refuses_bad_examples(bad):
    raw = {"skill": "purpose", "section": "persona", "title": "Your bot",
           "status": "needs_you", "consequential": False,
           "estimate": {"bytes": 0, "seconds": 0, "basis": "measured"},
           "fields": [{"id": "goal", "kind": "text", "label": "What kind of bot?",
                       "examples": bad}]}
    with pytest.raises(cards.CardError):
        cards.validate_card(raw)
