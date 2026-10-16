"""The static scan: hosts by how they are used, run-time calls, WebRTC, storage.

The three recorded GitHub apps live in the research folder (read-only);
their tests are skipped on a machine without it. The synthetic fixtures are
in this repo.
"""
from __future__ import annotations

import os

import pytest

from puffpai.webapps import scan

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "webapps")
RECORDED = os.environ.get("PUFFPAI_R3_APPBRIDGE", "")
needs_recorded = pytest.mark.skipif(not os.path.isdir(RECORDED),
                                    reason="the recorded apps are not on this machine")


@needs_recorded
def test_recorded_wedding_planner():
    s = scan.scan(os.path.join(RECORDED, "wedding"))
    assert s["hosts"] == []                                   # none by name
    assert s["network_calls_with_runtime_address"] == {"index.html": 4}
    assert set(s["localStorage_keys"]) == {"bunnysBigDay.v1", "bunnysBigDay.session"}
    assert s["keeps_data"] and not s["indexeddb_only"] and not s["direct_connections"]
    assert "file button" in s["features"]


@needs_recorded
def test_recorded_budget_tracker_hosts_by_use():
    s = scan.scan(os.path.join(RECORDED, "budget"))
    by = {h["host"]: h for h in s["hosts"]}
    assert set(by) == {"cdn.jsdelivr.net", "unpkg.com", "fonts.googleapis.com"}
    assert by["cdn.jsdelivr.net"]["main_use"] == "code" and by["cdn.jsdelivr.net"]["for"] == "cdn"
    assert by["unpkg.com"]["unpinned_code"] is True               # lucide@latest
    assert by["fonts.googleapis.com"]["main_use"] == "lettering" and by["fonts.googleapis.com"]["for"] == "fonts"
    assert s["needs_internet_while_running"] == []
    assert sorted(s["parts_to_save"]) == ["cdn.jsdelivr.net", "fonts.googleapis.com", "unpkg.com"]
    assert "budget_transactions" in s["localStorage_keys"]


@needs_recorded
def test_recorded_todo_list():
    s = scan.scan(os.path.join(RECORDED, "todos", "dist"))
    by = {h["host"]: h for h in s["hosts"]}
    assert by["github.com"]["main_use"] == "link"
    assert by["fonts.googleapis.com"]["main_use"] == "lettering"
    assert s["localStorage_keys"] == ["projects"]
    assert "eval" in s["features"]
    assert any(u.startswith("https://github.com/") for u in s["links"])
    assert all("?" not in u and "#" not in u for u in s["links"])


def test_webrtc_is_flagged():
    s = scan.scan(os.path.join(FIX, "probe"))
    assert s["direct_connections"] is True
    assert "RTCPeerConnection" in s["features"]


def test_indexeddb_only_app():
    s = scan.scan(os.path.join(FIX, "idb_only"))
    assert s["indexeddb_only"] is True and s["keeps_data"] is True
    assert s["localStorage_keys"] == []


def test_synthetic_list_apps_keep_data_and_reach_nothing():
    for name in ("rows_delete", "row_select", "edit_form", "clear_all"):
        s = scan.scan(os.path.join(FIX, name))
        assert s["keeps_data"] and not s["indexeddb_only"], name
        assert s["hosts"] == [] and not s["direct_connections"], name


def test_hosts_are_classified_by_use_never_by_the_apps_words(tmp_path):
    (tmp_path / "index.html").write_text(
        '<html><head><title>T</title>'
        '<link rel="stylesheet" href="https://fonts.example-fonts.org/css?family=A">'
        '<script src="https://cdn.example-cdn.org/lib@1.2.3/x.js"></script></head>'
        '<body><a href="https://docs.example.org/help?x=1#top">help</a>'
        '<script>fetch("https://api.example-data.org/v1/items");'
        'const u = base + "/x"; fetch(u);</script></body></html>', encoding="utf-8")
    s = scan.scan(str(tmp_path))
    by = {h["host"]: h for h in s["hosts"]}
    assert by["api.example-data.org"]["main_use"] == "data"
    assert by["api.example-data.org"]["for"] == "api"
    assert by["api.example-data.org"]["needed_while_running"] is True
    assert by["cdn.example-cdn.org"]["for"] == "cdn" and not by["cdn.example-cdn.org"]["unpinned_code"]
    assert s["links"] == ["https://docs.example.org/help"]
    assert s["network_calls_with_runtime_address"] == {"index.html": 1}
    assert scan.FOR_WORDS[by["api.example-data.org"]["for"]] == "fetching or saving data online"


def test_links_inside_the_folder_are_not_followed(tmp_path):
    (tmp_path / "index.html").write_text("<script>localStorage.setItem('k','v')</script>")
    outside = tmp_path.parent / "outside_scan"
    outside.mkdir(exist_ok=True)
    (outside / "evil.js").write_text("new RTCPeerConnection()")
    try:
        os.symlink(str(outside), str(tmp_path / "linked"), target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot make links")
    s = scan.scan(str(tmp_path))
    assert not s["direct_connections"]


def test_junctions_inside_the_folder_are_not_followed(tmp_path):
    (tmp_path / "index.html").write_text("<script>localStorage.setItem('k','v')</script>")
    outside = tmp_path.parent / "outside_scan_j"
    outside.mkdir(exist_ok=True)
    (outside / "evil.js").write_text("new RTCPeerConnection()")
    try:
        import _winapi
        _winapi.CreateJunction(str(outside), str(tmp_path / "jdir"))
    except (ImportError, AttributeError, OSError):
        pytest.skip("no directory junctions here")
    s = scan.scan(str(tmp_path))
    assert not s["direct_connections"]


def test_a_tool_that_stores_nothing_is_still_a_tool_and_a_page_is_not():
    """Directive section 2: a chess board, a timer or dice keep nothing, yet
    they are tools. A page with no script and no controls is a document."""
    timer = scan.scan(os.path.join(FIX, "timer_tool"))
    assert timer["keeps_data"] is False and timer["interactive"] is True and timer["controls"] >= 2
    page = scan.scan(os.path.join(FIX, "doc_page"))
    assert page["keeps_data"] is False and page["interactive"] is False and page["controls"] == 0
