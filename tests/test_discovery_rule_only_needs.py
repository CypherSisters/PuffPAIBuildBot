"""What a busy brain left behind.

The restarted discovery run met a local brain that answered nothing: needs fell
back to the rules, and no find was judged. These tests pin the rule-only
behaviour that run showed was weak. No network, no model.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from puffpai.catalogs import meaning, needs_rules

ROOT = Path(__file__).resolve().parents[1]


# -- the kind of bot ------------------------------------------------------------
def test_a_verb_and_particle_is_no_kind_of_bot():
    # "My husband Dave set up Home Assistant ...": "set up home" sent
    # "set thermostat display" and "set up garage door".
    assert needs_rules.usable_subject("set up home") == ""
    assert needs_rules.usable_subject("figure out") == ""


@pytest.mark.parametrize("subject", ["wedding planner", "budget tracker", "study", "plant care",
                                     "chess coach", "home fitness coach"])
def test_ordinary_kinds_stay(subject):
    assert needs_rules.usable_subject(subject) == subject


# -- the tool a job's verb names ---------------------------------------------------
@pytest.mark.parametrize("item, phrase", [
    ("Rolls dice", "dice roller"),
    ("Tracks initiative and hit points", "initiative tracker"),
    ("Builds encounters and treasure", "encounter builder"),
    ("Names characters", "character name generator"),
    ("Keeps my campaign notes organized", "campaign note organizer"),
    ("Tracks mileage and repairs", "mileage tracker"),
    ("Cost out recipes", "recipe cost calculator"),
    ("Plan the baking schedule", "baking schedule planner"),
    ("Track how many hours we each put in", "hour tracker"),
    ("Track themes from pictures", "theme tracker"),
])
def test_the_tool_named_by_the_verb(item, phrase):
    assert needs_rules.agent_phrase(item, {}) == phrase


@pytest.mark.parametrize("item", ["Shows the thermostat", "Keep it running", "Suggest ideas",
                                  "Keep the budget", "Dice"])
def test_no_tool_name_when_the_verb_names_none(item):
    assert needs_rules.agent_phrase(item, {}) == ""


def test_a_personal_word_never_joins_the_tool_name():
    assert needs_rules.agent_phrase("Find vendors around Dayton", {"dayton": "hard:place"}) \
        == "vendor finder"
    assert "maya" not in needs_rules.agent_phrase("Tracks Maya hit points", {"maya": "hard:name"})


def test_rule_needs_searches_the_tool_name_first():
    goal = ("I want a game master helper bot that rolls dice, tracks initiative and hit "
            "points, builds encounters and treasure, names characters, and keeps my "
            "campaign notes organized.")
    jobs = {j["name"]: j["search"] for j in needs_rules.rule_needs(goal)["jobs"]}
    assert jobs["Rolls dice"][0] == "dice roller"
    assert jobs["Tracks initiative and hit points"][0] == "initiative tracker"
    assert jobs["Keeps my campaign notes organized"][0] == "campaign note organizer"
    # the kind-led phrase stays as a backstop
    assert any(p.startswith("game master") for p in jobs["Rolls dice"])


def test_rule_phrases_lead_with_the_tool_name():
    assert needs_rules.rule_phrases(["car", "maintenance"], "Tracks mileage and repairs")[0] \
        == "mileage tracker"


# -- a line nobody asked the brain about ------------------------------------------------
def _code(fid: str, does, fit: float, judge=None, state: str = "might_help") -> dict:
    return {"id": fid, "source": "github", "part": "code-app",
            "what_it_is": "A tool.",
            "meaning": {"job": "j1", "blurb": fit, "does": does, "fit": fit, "judge": judge,
                        "state": state, "why_dropped": None}}


def test_an_unjudged_line_whose_readme_is_about_something_else_is_left_out():
    rows = [_code("github:a/tile-layout-planner", 0.257, 0.452),
            _code("github:a/garage-layout", 0.275, 0.52, state="ticked"),
            _code("github:a/dice-roller", 0.56, 0.57),
            _code("github:a/no-readme", None, 0.53)]
    meaning.settle(rows, judged=False)
    states = {r["id"]: r["meaning"]["state"] for r in rows}
    assert states["github:a/tile-layout-planner"] == "dropped"
    assert states["github:a/garage-layout"] == "dropped"
    assert rows[0]["meaning"]["why_dropped"] == meaning.OWN_PAGE_ELSE
    assert states["github:a/dice-roller"] == "might_help"
    assert states["github:a/no-readme"] == "might_help"


def test_the_brains_yes_keeps_a_line_with_a_thin_readme():
    rows = [_code("github:a/name-gen", 0.175, 0.52, judge="yes")]
    meaning.settle(rows, judged=True)
    assert rows[0]["meaning"]["state"] == "might_help"


def test_a_catalog_row_is_never_judged_by_its_readme():
    row = _code("seed:nhtsa_recalls", 0.1, 0.68)
    row.update(source="seed", part="live-info", id="seed:nhtsa_recalls")
    assert meaning.is_catalog_row(row)
    meaning.settle([row], judged=False)
    assert row["meaning"]["state"] == "might_help"


# -- the harness: her words, not the search site's syntax ------------------------------
@pytest.fixture(scope="module")
def harness():
    spec = importlib.util.spec_from_file_location("r3_acceptance_fx3",
                                                  ROOT / "scripts" / "measure_goals.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["r3_acceptance_fx3"] = mod
    spec.loader.exec_module(mod)
    return mod


GH = ("https://api.github.com/search/repositories?q={q}+in:name,description"
      "+language:JavaScript+archived:false+pushed:>=2024-10-03&per_page=100")


def test_in_of_mother_in_law_is_not_github_in_name(harness):
    goal = {"personal": ["mother-in-law", "Rosa", "2024"]}
    assert harness.personal_leaks(goal, {}, [{"url": GH.format(q="bakery+business")}]) == []


def test_a_real_word_is_still_caught(harness):
    goal = {"personal": ["Rosa"]}
    assert harness.personal_leaks(goal, {}, [{"url": GH.format(q="rosa+cakes")}])
    assert harness.personal_leaks(
        goal, {}, [{"url": "https://codeberg.org/api/v1/repos/search?q=rosa%20cakes"}])
    # a word of hers written like a qualifier is still hers
    assert harness.personal_leaks({"personal": ["Fresno"]}, {},
                                  [{"url": "https://x.org/search?q=fresno:clinic"}])


# -- Home Assistant: never "if you have" where it is not in use -----------------------
LIGHTS = "a bot that turns my living room lights off at night and tells me the thermostat setting"


def _plan_sctx(goal, **args):
    from types import SimpleNamespace
    from puffpai.wizard import proposal
    base = dict(egress_door=None, skip_ha=False, ha_url=None, ha_token=None, skip_vault=False)
    base.update(args)
    return proposal.SummaryContext(
        goal=goal, terms=proposal.goal_terms(goal), name="Lumen", mode="standard",
        shared={}, previews={}, args=SimpleNamespace(**base), cfg=None, internet=True)


@pytest.mark.parametrize("configured", [False, True])
def test_the_hub_line_never_says_if_you_have(monkeypatch, configured):
    from puffpai import setup_ops
    from puffpai.wizard import proposal
    monkeypatch.setattr(setup_ops, "ha_configured", lambda *a, **k: configured)
    items = proposal._devices(_plan_sctx(LIGHTS))
    assert len(items) == 1
    text = f"{items[0]['label']} {items[0]['detail']}"
    assert "if you have" not in text.lower()
    assert items[0]["label"] == ("Use your Home Assistant" if configured
                                 else "Home Assistant, a smart home hub")


def test_a_hub_given_now_is_hers(monkeypatch):
    from puffpai import setup_ops
    from puffpai.wizard import proposal
    monkeypatch.setattr(setup_ops, "ha_configured", lambda *a, **k: False)
    items = proposal._devices(_plan_sctx(LIGHTS, ha_url="http://hub.local:8123", ha_token="t"))
    assert items[0]["label"] == "Use your Home Assistant"
    assert items[0]["default_on"] is True


def test_no_hub_line_for_a_goal_without_home_gadgets(monkeypatch):
    from puffpai import setup_ops
    from puffpai.wizard import proposal
    monkeypatch.setattr(setup_ops, "ha_configured", lambda *a, **k: True)
    assert proposal._devices(_plan_sctx("a chess coach for practice games")) == []


def test_the_kind_keeps_its_own_word_order():
    # was "planner wedding vendor material" (a kind word that is also hers)
    assert needs_rules._phrases(["wedding", "planner"], ["wedding", "vendor", "material"])[0] \
        == "wedding planner vendor material"
    assert needs_rules._phrases(["game", "master"], ["dice"]) == ["game master dice"]


# -- what a held name owns is hers too ---------------------------------------------------
def test_the_thing_after_a_possessive_and_a_held_name_is_not_searched():
    marks = {"cub": "held:name", "scout": "held:name", "mom": "hard:relative"}
    assert "den" not in needs_rules.item_words(
        "Plan a stargazing night for his Cub Scout den in November", marks)
    assert "dryer" not in needs_rules.item_words("fix the knob on my mom's dryer", marks)
    assert needs_rules.rule_needs(
        "I want a bot to plan a stargazing night for his Cub Scout den.",
        personal=marks, kind=[])["jobs"][0]["search"] == ["stargazing night planner",
                                                          "stargazing night"]


def test_a_plain_possessive_keeps_its_thing():
    assert needs_rules.item_words("Keeps my campaign notes organized", {}) == ["campaign", "note"]


# -- another kind of the thing is left out, not only unticked ------------------------
def test_a_find_for_another_kind_of_printer_is_left_out():
    from puffpai.wizard import find_parts
    items = [{"id": "a", "need": "j1", "section": "apps_code", "default_on": False},
             {"id": "b", "need": "j2", "section": "apps_code", "default_on": False},
             {"id": "c", "need": "j1", "section": "pictures", "default_on": False}]
    rows = {"a": {"name": "printease", "what_it_is": "Easy control of your printer."},
            "b": {"name": "tower-tool", "what_it_is": "Calibration towers for a 3D printer."},
            "c": None}
    assert find_parts._other_kinds(items, rows, "3d printer") == 1
    assert [i["id"] for i in items] == ["b", "c"]


def test_no_kind_qualifier_leaves_nothing_out():
    from puffpai.wizard import find_parts
    items = [{"id": "a", "need": "j1", "section": "apps_code", "default_on": False}]
    rows = {"a": {"name": "printease", "what_it_is": "Easy control of your printer."}}
    assert find_parts._other_kinds(items, rows, "printer helper") == 0
    assert len(items) == 1


def test_a_tool_name_alone_is_never_sent_for_a_private_item():
    assert needs_rules.only_tool_words("logger")
    assert needs_rules.only_tool_words("name generator")
    assert not needs_rules.only_tool_words("dice roller")
    assert not needs_rules.only_tool_words("")
