"""The offer of a place to keep lists is never hidden by a keyword hit.

The scaffold was offered only when the catalog
matched NOTHING, so "a garden and greenhouse assistant for a small family farm"
- which hits Wikipedia's tags, and whose owner obviously wants a plantings list -
was the one goal never asked. A knowledge pack is not somewhere to write a tomato
down.

The offer is now unconditional in both wizards. What the match changes is the
DEFAULT: no when a shipped household app that already keeps lists matched, yes
otherwise. The suggested list names are the owner's own nouns out of their own
goal, never words we invented.

Run:
    pytest tests/test_scaffold_offer_always.py
"""
from __future__ import annotations

import pytest

from puffpai import api_server, build_interview as bi, cli, persona_scaffold

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}

GARDEN_GOAL = ("a garden and greenhouse assistant for a small family farm: "
               "remind me when to water, keep a planting list, and tell me "
               "what the frost forecast looks like")


# ---------------------------------------------------------------------------
# the exact defect
# ---------------------------------------------------------------------------

def test_the_garden_goal_matched_something_and_is_still_offered():
    """The regression in one test: a match used to mean no offer."""
    proposal = bi.propose(GARDEN_GOAL)
    assert proposal["flat"], "the garden goal is supposed to match packs"
    assert proposal["scaffold_offer"] is True
    offer = bi.scaffold_offer(proposal)
    assert offer["offered"] is True
    assert offer["default_yes"] is True
    assert offer["question"] == "Want a place to keep lists for this?"
    assert "not somewhere to write things down" in offer["reason"]


def test_a_no_match_goal_is_offered_too_and_says_it_is_the_whole_of_it():
    offer = bi.scaffold_offer(bi.propose("a bot about zzxq and qwph"))
    assert offer["offered"] is True and offer["default_yes"] is True
    assert "Nothing we ship matches" in offer["reason"]


def test_a_household_goal_is_offered_and_still_defaults_to_yes():
    """The place is what puts the bot in the menu, so the default is yes.

    The reason still says the matched app keeps lists of its own, and how to
    decline. The old default of no left an owner who pressed Enter with a bot
    they could not find.
    """
    proposal = bi.propose("a family calendar and reminders and a shopping list")
    offer = bi.scaffold_offer(proposal)
    assert offer["offered"] is True
    assert offer["default_yes"] is True
    assert offer["matched_household"]
    assert "keeps lists of its own" in offer["reason"]
    assert "Say no if you only want" in offer["reason"]


def test_a_goal_that_only_brushes_a_household_app_defaults_to_yes():
    """'current space events' matches the calendar; a space bot still gets a place."""
    proposal = bi.propose("knows everything about space, space facts, current "
                          "space events, astronomy, Earth weather events")
    assert bi.scaffold_offer(proposal)["default_yes"] is True


# ---------------------------------------------------------------------------
# suggested list names come from the owner's own words
# ---------------------------------------------------------------------------

def test_suggested_lists_are_the_owners_own_nouns():
    names = bi.suggested_lists(GARDEN_GOAL)
    assert names, "a concrete goal should suggest something"
    assert len(names) <= bi.MAX_SUGGESTED_LISTS
    lowered = GARDEN_GOAL.lower()
    for name in names:
        assert name in lowered, name
        assert name not in bi._STOPWORDS


def test_an_all_filler_goal_suggests_nothing_rather_than_guessing():
    assert bi.suggested_lists("a bot that helps with stuff and things") == []
    assert bi.suggested_lists("") == []


def test_suggested_lists_are_legal_list_names():
    from puffpai import persona_scaffold

    names = bi.suggested_lists(GARDEN_GOAL)
    assert persona_scaffold.parse_lists(names) == [
        persona_scaffold.list_key(n) for n in names]


# ---------------------------------------------------------------------------
# both wizards make the same offer
# ---------------------------------------------------------------------------

def test_the_interview_route_always_offers_with_an_id_and_list_names():
    client = api_server.app.test_client()
    response = client.post("/setup/interview",
                           json={"goal": GARDEN_GOAL, "name": "GardenBot"},
                           headers=AUTH)
    assert response.status_code == 200
    scaffold = response.get_json()["scaffold"]
    assert scaffold["offered"] is True
    assert scaffold["default_yes"] is True
    assert scaffold["question"] == bi.SCAFFOLD_QUESTION
    assert scaffold["suggested_id"] == "gardenbot"
    assert scaffold["suggested_lists"] == bi.suggested_lists(GARDEN_GOAL)


def test_the_route_still_names_an_id_when_nobody_typed_a_name():
    """No name falls back to the GOAL, as the CLI does - not straight to mybot.

    `mybot` is what an owner gets when neither their name nor their goal makes a
    word, and a route that reached for it while the goal was sitting right there
    named a bot nobody would recognise.
    """
    client = api_server.app.test_client()
    scaffold = client.post("/setup/interview", json={"goal": GARDEN_GOAL},
                           headers=AUTH).get_json()["scaffold"]
    assert scaffold["offered"] is True
    assert scaffold["suggested_id"] == persona_scaffold.suggest_id(
        "", GARDEN_GOAL)
    assert scaffold["suggested_id"] != persona_scaffold.FALLBACK_ID


def test_the_typed_name_is_the_id_the_route_suggests():
    client = api_server.app.test_client()
    scaffold = client.post("/setup/interview",
                           json={"goal": GARDEN_GOAL, "name": "Sprout"},
                           headers=AUTH).get_json()["scaffold"]
    assert scaffold["suggested_id"] == "sprout"


def test_the_fallback_is_only_for_a_name_and_a_goal_that_make_nothing():
    client = api_server.app.test_client()
    scaffold = client.post("/setup/interview", json={"goal": "!!!", "name": "!"},
                           headers=AUTH).get_json()["scaffold"]
    assert scaffold["suggested_id"] == persona_scaffold.FALLBACK_ID


def test_the_cli_asks_the_question_on_a_goal_that_matched(monkeypatch, capsys,
                                                         tmp_path):
    """The wizard step itself, driven with the answers typed in."""
    import argparse

    from puffpai import app_data as app_data_mod, app_registry, persona_scaffold

    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(app_data_mod, "_DATA_DIR", str(tmp_path / "app_data"))
    app_registry.rescan()

    asked: list[tuple[str, bool]] = []

    def _fake_yes_no(question, default=True):
        asked.append((question, default))
        return True

    monkeypatch.setattr(cli, "_ask_yes_no", _fake_yes_no)
    monkeypatch.setattr(cli, "_ask",
                        lambda prompt, default="", **kw: default or "gardenbot")

    args = argparse.Namespace(scaffold=False, lists=None)
    result = cli._maybe_scaffold_persona(
        args, GARDEN_GOAL, bi.propose(GARDEN_GOAL), bot_name="GardenBot",
        non_interactive=False)

    assert asked == [(bi.SCAFFOLD_QUESTION, True)]
    assert result is not None and result["scaffolded"] is True
    # The lists came from the suggestion, which came from the owner's own goal.
    assert result["lists"] == [persona_scaffold.list_key(n)
                               for n in bi.suggested_lists(GARDEN_GOAL)]
    app_registry.rescan()


def test_the_cli_defaults_to_yes_on_a_household_goal(monkeypatch):
    import argparse

    seen: list[bool] = []
    monkeypatch.setattr(cli, "_ask_yes_no",
                        lambda q, default=True: seen.append(default) or False)
    args = argparse.Namespace(scaffold=False, lists=None)
    result = cli._maybe_scaffold_persona(
        args, "a family calendar and a shopping list",
        bi.propose("a family calendar and a shopping list"),
        bot_name="HomeBot", non_interactive=False)
    assert seen == [True]
    assert result is None
