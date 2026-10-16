"""The home page and sidebar layout file, and the brain's arrange_dashboard tool.

One plain JSON file in the state folder (``dashboard_layout.json``): a fresh
layout keeps the household tiles in the hidden tray, PUT replaces only the
sections sent, and the brain's pin / hide / show / move / rename writes the
same file the page reads.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json

import pytest

from puffpai import dashboard_widgets as dw
from puffpai import home_things


@pytest.fixture
def layout_file(monkeypatch, tmp_path):
    path = tmp_path / "state" / "dashboard_layout.json"
    monkeypatch.setattr(dw, "_LAYOUT_PATH", str(path))
    return path


def _seen(**tiles):
    return {"tiles": {"chat": "Chat", "jobs": "Build jobs", "find-more": "Find more",
                      **tiles},
            "sidebar": {"/dashboard": "Dashboard", "/dashboard/chat": "Chat",
                        "/dashboard/knowledge": "Knowledge"}}


def test_fresh_layout_hides_the_household_tiles(layout_file):
    got = dw.get_layout()
    assert not layout_file.exists()
    assert got["tiles"]["order"] == ["chat", "jobs", "find-more"]
    for tid in ("weather", "jetson", "smart-home", "cameras", "schedule",
                "reminders", "meals", "workout"):
        assert tid in got["tiles"]["hidden"]
    assert "chat" not in got["tiles"]["hidden"]


def test_put_replaces_only_the_sections_sent(layout_file):
    dw.save_layout({"tiles": {"order": ["jobs", "chat"], "hidden": [],
                              "sizes": {"jobs": {"w": 99, "h": 3}},
                              "names": {"jobs": "  What   I do "}}})
    dw.save_layout({"seen": _seen()})
    on_disk = json.loads(layout_file.read_text(encoding="utf-8"))
    assert on_disk["tiles"]["order"] == ["jobs", "chat"]
    assert on_disk["tiles"]["sizes"]["jobs"] == {"w": 12, "h": 3}
    assert on_disk["tiles"]["names"]["jobs"] == "What I do"
    assert on_disk["seen"]["sidebar"]["/dashboard/chat"] == "Chat"
    assert on_disk["updated_by"] == "owner"


def test_broken_file_reads_as_defaults(layout_file):
    layout_file.parent.mkdir(parents=True)
    layout_file.write_text("{not json", encoding="utf-8")
    assert dw.get_layout()["tiles"]["order"] == ["chat", "jobs", "find-more"]


def test_brain_moves_a_tile_first_by_its_name(layout_file):
    dw.save_layout({"seen": _seen(**{"app:wnm": "World News Map"})})
    said = home_things.tool_arrange_dashboard(
        {"action": "move", "thing": "the world news map", "position": "first"})
    assert said["ok"], said
    assert "first" in said["said"]
    got = dw.get_layout()
    assert got["tiles"]["order"][0] == "app:wnm"
    assert got["updated_by"] == "brain"


def test_brain_hides_and_shows_a_tile(layout_file):
    dw.save_layout({"tiles": {"hidden": []}, "seen": _seen(cameras="Cameras")})
    assert home_things.tool_arrange_dashboard({"action": "hide", "thing": "cameras"})["ok"]
    assert "cameras" in dw.get_layout()["tiles"]["hidden"]
    assert home_things.tool_arrange_dashboard({"action": "show", "thing": "Cameras"})["ok"]
    assert "cameras" not in dw.get_layout()["tiles"]["hidden"]


def test_brain_pins_and_renames_and_reaches_the_sidebar(layout_file):
    dw.save_layout({"seen": _seen()})
    out = dw.arrange("pin", "knowledge", place="sidebar")
    assert out["ok"] and out["place"] == "sidebar"
    assert dw.get_layout()["sidebar"]["pinned"] == ["/dashboard/knowledge"]
    out = dw.arrange("rename", "chat", place="home", name="Talk to me")
    assert out["ok"] and dw.get_layout()["tiles"]["names"]["chat"] == "Talk to me"
    # Her new name finds it again; an empty name gives the own name back.
    assert dw.arrange("rename", "talk to me", place="home", name="")["ok"]
    assert "chat" not in dw.get_layout()["tiles"]["names"]


def test_unknown_thing_or_action_changes_nothing(layout_file):
    out = home_things.tool_arrange_dashboard({"action": "hide", "thing": "space laser"})
    assert not out["ok"] and "could not find" in out["said"]
    assert not dw.arrange("explode", "chat")["ok"]
    assert not layout_file.exists()
    assert "—" not in out["said"]


def test_tool_is_registered_and_runs_through_the_registry(layout_file):
    from puffpai import brain_tools
    home_things.register_all()
    try:
        assert brain_tools.find("arrange_dashboard") is not None
        dw.save_layout({"seen": _seen()})
        out = brain_tools.call("arrange_dashboard", {"action": "move", "thing": "jobs",
                                                     "position": "last"})
        assert out.get("ok") is not False, out
        assert dw.get_layout()["tiles"]["order"][-1] == "jobs"
    finally:
        for name in ("list_builtin_apps", "put_on_home", "arrange_dashboard"):
            brain_tools.unregister(name)


def test_routes_read_and_write_the_file(layout_file):
    from puffpai import api_server
    api_server.app.config.update(TESTING=True)
    client = api_server.app.test_client()
    auth = {"Authorization": f"Bearer {api_server._API_KEY}"}
    got = client.get("/dashboard/layout", headers=auth).get_json()
    assert got["ok"] and "weather" in got["layout"]["tiles"]["hidden"]
    put = client.put("/dashboard/layout", headers=auth,
                     json={"sidebar": {"hidden": ["/dashboard/talk"]}})
    assert put.status_code == 200
    assert json.loads(layout_file.read_text(encoding="utf-8"))["sidebar"]["hidden"] == [
        "/dashboard/talk"]
    assert client.put("/dashboard/layout", headers=auth, data="[]",
                      content_type="application/json").status_code == 400
