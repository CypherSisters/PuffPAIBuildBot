"""The shipped live-info list, and discovery run over the shipped lists for six goals.

The seed list is DATA spanning many topics; nothing in code knows a topic.
These tests pin the shape of that data and prove the matching is generic:
six unrelated goals (space, dinosaurs, a car, a garden, knitting, music) each
get sensible results - or honest silence - from the same code.

They read the real files in ``config/`` and never go online.

Run:
    pytest tests/test_catalogs_seeds_goals.py
"""
from __future__ import annotations

import json
import os
import re

import pytest

from puffpai.catalogs import api_seeds, live
from puffpai import plain_words  # noqa: E402  (the one banned list)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEEDS = os.path.join(REPO, "config", "catalog_seeds", "apis.json")

GOALS = {
    "G1": "I want a space bot that knows everything about space, shows me cool space stuff, "
          "and works with my smart telescope",
    "G2": "I love dinosaurs, I want a bot that knows every dinosaur",
    "G3": "Help me keep my 2014 Honda Civic running - maintenance, recalls, how-tos",
    "G4": "A garden helper for my vegetable beds in Ohio",
    "G5": "A knitting and crochet buddy with patterns and tips",
    "G6": "A music bot that knows bands and albums and plays my records",
}


@pytest.fixture(autouse=True)
def _real_lists(monkeypatch, tmp_path):
    # Shipped snapshots only: nothing a developer refreshed on this machine.
    monkeypatch.setattr(live, "state_snapshot_dir", lambda: str(tmp_path / "none"))
    live.clear_caches()
    yield
    live.clear_caches()


def _raw():
    with open(SEEDS, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# the seed file
# ---------------------------------------------------------------------------

def test_seed_file_is_broad_and_every_row_is_usable():
    raw = _raw()
    assert raw["schema"] == "api_seeds/v1"
    rows = raw["apis"]
    assert len(rows) >= 30
    assert len({t for r in rows for t in r["topics"]}) >= 12
    assert [api_seeds.row_problem(r) for r in rows] == [None] * len(rows)
    assert len(api_seeds.load_rows(SEEDS)) == len(rows)
    assert len({r["id"] for r in rows}) == len(rows)


def test_seed_samples_carry_no_key_and_no_owner_data():
    for row in _raw()["apis"]:
        sample = row["sample_endpoint"].lower()
        assert not re.search(r"(api_?key|token|secret|password|demo_key)=", sample), row["id"]
        assert row["auth"] == "none", row["id"]
        assert "commercial_ok" in row and row["commercial_ok"] in (True, False, None)
        assert row.get("rate_limit"), row["id"]
        assert row.get("license"), row["id"]


def test_most_seeds_were_checked_live_and_dates_are_real():
    rows = _raw()["apis"]
    checked = [r for r in rows if r["verified_at"]]
    assert len(checked) >= 30
    for row in checked:
        assert re.match(r"^2026-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00$", row["verified_at"]), row["id"]


def test_the_topics_live_in_data_not_code():
    # No topic word appears as a string literal in the matching code (docstrings
    # may use them as examples; code may not branch on them).
    for name in ("live.py", "api_seeds.py", "apis_guru.py", "public_apis.py",
                 "ha_integrations.py", "mcp_registry.py"):
        with open(os.path.join(REPO, "src", "puffpai", "catalogs", name), encoding="utf-8") as fh:
            code = fh.read().lower()
        for word in ("dinosaur", "telescope", "honda", "knitting", "nasa", "musicbrainz",
                     "paleobiodb", "garden", "music"):
            assert f'"{word}' not in code and f"'{word}" not in code, (name, word)


# ---------------------------------------------------------------------------
# six goals, one code path
# ---------------------------------------------------------------------------

def _top(goal, n=3):
    return [c["id"] for c in live.discover_live(live.terms_from_goal(goal), limit=n)]


def test_g1_space_gets_space_services():
    top = _top(GOALS["G1"])
    assert set(top) <= {"seed:seventimer", "seed:nasa_images", "seed:launch_library",
                        "seed:nasa_epic", "seed:jpl_close_approach", "seed:spaceflight_news",
                        "seed:iss_position", "seed:swpc_kp"}
    assert len(top) == 3


def test_g2_dinosaurs_get_fossil_services():
    assert set(_top(GOALS["G2"], 2)) == {"seed:pbdb", "seed:idigbio"}


def test_g3_car_gets_recalls():
    assert _top(GOALS["G3"], 1) == ["seed:nhtsa_recalls"]


def test_g4_garden_gets_growing_zone_and_weather():
    top = _top(GOALS["G4"])
    assert top[0] == "seed:hardiness_zone"
    assert "ohio" not in live.terms_from_goal(GOALS["G4"])


def test_g5_knitting_is_honest_about_little():
    items = [i for i in live.proposal_items(GOALS["G5"]) if i["section"] == "live_info"]
    assert len(items) <= 1  # nothing invented for a topic the lists barely cover


def test_g6_music_gets_music_services():
    assert _top(GOALS["G6"], 1) == ["seed:musicbrainz"]


def test_devices_lines_for_the_three_owned_things():
    def devices(goal):
        return [i["label"] for i in live.proposal_items(goal) if i["section"] == "devices"]
    assert devices(GOALS["G1"]) == ["Your smart telescope: no ready way to connect found yet"]
    assert devices(GOALS["G3"]) == ["Your 2014 Honda Civic: no ready way to connect found yet"]
    assert devices(GOALS["G6"]) == ["Your records: no ready way to connect found yet"]
    assert devices(GOALS["G2"]) == [] and devices(GOALS["G4"]) == [] and devices(GOALS["G5"]) == []


def test_a_real_home_assistant_match_from_the_shipped_list():
    items = [i for i in live.proposal_items("A music bot that works with my Sonos speakers")
             if i["section"] == "devices"]
    assert items and items[0]["apply"] == {"kind": "ha_integration", "ref": "sonos"}
    assert "home network" in items[0]["detail"]


def test_brand_words_do_not_pull_in_unrelated_directory_rows():
    labels = [i["label"] for i in live.proposal_items(GOALS["G3"]) if i["section"] == "live_info"]
    assert labels == ["Live info from NHTSA recalls"]


def test_every_goal_uses_plain_words():
    for goal in GOALS.values():
        for item in live.proposal_items(goal):
            assert plain_words.is_plain(item["label"]), item["label"]
            if item["id"].startswith("live:api_seeds") or item["section"] == "devices":
                assert plain_words.is_plain(item["detail"]), item["detail"]
