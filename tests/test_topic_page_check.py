"""The page check: pages about the topic are not tools.

``star_floor_finds.json`` holds the recorded star-floor survivors of ten goals
with ``page_expected`` from the measured prototype.
"""
from __future__ import annotations

import json
from pathlib import Path

from puffpai.catalogs.page_check import looks_like_page

FIX = Path(__file__).parent / "fixtures"


def _rows() -> list[dict]:
    return json.loads((FIX / "star_floor_finds.json").read_text(encoding="utf-8"))["rows"]


#: A link list is a page too. These two recorded finds are awesome
#: lists (topic "awesome-list"); the measured prototype did not flag them.
LINK_LISTS = {"brycejohnston/awesome-agriculture", "5e-bits/awesome-5e-srd"}


def _expected(row: dict) -> bool:
    return row["page_expected"] or row["full_name"] in LINK_LISTS


def _flag(row: dict) -> bool:
    return bool(looks_like_page(row["full_name"].split("/", 1)[1], row.get("description") or "",
                                row.get("topics") or []))


def test_flags_equal_the_measured_ones_for_all_rows():
    rows = _rows()
    assert len(rows) == 323
    diff = [r["full_name"] for r in rows if _flag(r) != _expected(r)]
    assert diff == []


def test_sixteen_wedding_pages_and_none_elsewhere():
    rows = _rows()
    wedding = [r for r in rows if r["goal"] == "wedding"]
    others = [r for r in rows if r["goal"] != "wedding"]
    assert len(wedding) == 44 and len(others) == 279
    assert sum(_flag(r) for r in wedding) == 16
    assert sum(_flag(r) for r in others) == len(LINK_LISTS)
    assert {r["full_name"] for r in others if _flag(r)} == LINK_LISTS


def _recorded(full_name: str) -> dict:
    items = json.loads((FIX / "relevance_wedding_finds.json").read_text(encoding="utf-8"))["items"]
    return next(i for i in items if i["id"] == "github:" + full_name)


def test_a_planning_platform_that_mentions_invitations_is_a_tool():
    row = _recorded("web-codings123/EazyWed")
    assert "invitation" in row["what_it_is"].lower() or "invit" in json.dumps(row).lower()
    assert looks_like_page(row["name"], row["what_it_is"], row["repo"]["topics"]) == []


def test_the_small_static_planner_is_a_tool():
    row = _recorded("SubchiBeats/wedding-planner")
    assert looks_like_page(row["name"], row["what_it_is"], row["repo"]["topics"]) == []


def test_strong_and_weak_hits():
    assert looks_like_page("our-wedding", "Our wedding website", [])
    assert looks_like_page("x", "A landing page for a bakery", [])
    assert looks_like_page("x", "Bootstrap template for restaurants", [])
    assert looks_like_page("netflix-clone", "", [])
    assert looks_like_page("x", "", ["portfolio-website"])
    assert looks_like_page("x", "Digital wedding invitation", [])
    assert looks_like_page("x", "Digital wedding invitation with RSVP tracker", []) == []
    assert looks_like_page("garden-planner", "Plan raised beds and watering", []) == []


def test_link_lists_and_articles_are_pages_but_stateless_tools_are_not():
    assert looks_like_page("awesome-chess", "A curated list of chess software", [])
    assert looks_like_page("x", "A list of useful resources for gardeners", [])
    assert looks_like_page("x", "Blog post about planning a budget", [])
    assert looks_like_page("x", "", ["awesome-list"])
    assert looks_like_page("pomodoro", "A simple pomodoro timer that runs in the browser", []) == []
    assert looks_like_page("metronome", "Web metronome with tap tempo", []) == []
    assert looks_like_page("betafish", "Chess engine you can play against in the browser", []) == []
    assert looks_like_page("recipes", "A list of recipes as JSON", []) == []
