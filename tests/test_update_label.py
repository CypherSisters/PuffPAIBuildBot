"""The daily update is named from the goal's topic, the same way the bot is.

"I love dinosaurs, I want a bot that knows every
dinosaur" was suggested DinosaurBot, and its update was labelled "A daily love
update at 8 am"; the car bot's was "A daily 2014 update". The update now skips
the same words the name suggester skips, and sits right after the brain on the
summary instead of last.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import pytest

from puffpai import offers, persona_scaffold
from puffpai.wizard import proposal

G2 = "I love dinosaurs, I want a bot that knows every dinosaur"
G3 = "Help me keep my 2014 Honda Civic running - maintenance, recalls, how-tos"


@pytest.mark.parametrize("goal,terms,label", [
    (G2, ["love", "dinosaurs", "dinosaur"], "A daily dinosaur update at 8 am"),
    (G2, persona_scaffold.goal_keywords(G2), "A daily dinosaur update at 8 am"),
    # "2014 Honda Civic" was typed as one phrase, so it is never cut to
    # "Honda" or to "2014" alone.
    (G3, ["2014", "honda", "civic"], "A daily 2014 Honda Civic update at 8 am"),
])
def test_the_update_is_named_from_the_topic(goal, terms, label):
    assert offers.proposal_items(goal, terms)[0]["label"] == label


def test_the_name_and_the_update_agree():
    from puffpai.wizard.packages.purpose.skill import suggest_name
    assert suggest_name(G2) == "DinosaurBot"
    assert persona_scaffold.topic_word(G2) == "dinosaur"


def test_updates_come_straight_after_the_brain():
    assert proposal.SECTIONS[:2] == ("thinking", "updates")
