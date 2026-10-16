"""The find code with the real tool loop registry and the real MCP tool list.

With the tool loop (``puffpai.brain_tools``) and the MCP client
(``puffpai.mcp_client``) in the same build, they prove the three fit:
install_thing asks through the loop with its own words, a skill installed now
is in skill_view's list on the next turn, and an MCP find lands on the MCP
list and comes off it again. Recorded answers only, no network.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import pytest

from finding_fakes import Sites


@pytest.fixture
def sites(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    from puffpai.finding import hermes_index, net
    fake = Sites()
    monkeypatch.setattr(net, "_send", fake)
    hermes_index.forget()
    yield fake
    hermes_index.forget()


def test_install_thing_through_the_loop_registry(sites):
    from puffpai import brain_tools
    from puffpai.finding import find, tools
    tools.register_all()
    find.find_things("wedding planner", kinds=["skill"])
    fid = "clawhub:ivangdavila/wedding-planner"

    said_no = brain_tools.call("install_thing", {"id": fid}, {"ask": lambda q: False})
    assert not said_no.get("ok")
    from puffpai import skills
    assert skills.installed() == []

    asked = []
    out = brain_tools.call("install_thing", {"id": fid},
                           {"ask": lambda q: asked.append(q) or True})
    assert out["ok"], out
    assert asked and asked[0]["title"] == "Install Wedding Planner?"
    assert "ClawHub check: passed" in asked[0]["detail"]
    view = brain_tools.find("skill_view", {})
    assert view is not None and "wedding-planner" in view.description
    read = brain_tools.call("skill_view", {"name": "wedding-planner"}, {})
    assert read["ok"] and "wedding" in str(read.get("result")).lower()
    found = brain_tools.call("find_things", {"words": "wedding planner",
                                             "kinds": ["skill"]}, {})
    assert found["ok"] and "[clawhub:ivangdavila/wedding-planner]" in str(found["result"])


def test_mcp_find_lands_on_the_mcp_lanes_list(sites, monkeypatch):
    pytest.importorskip("mcp")
    from puffpai import mcp_client
    from puffpai.finding import find, install, sites as S
    monkeypatch.setattr(mcp_client, "_fetch_tools", lambda rec, headers: [
        {"name": "plan_seats", "description": "Make a seating plan.",
         "input_schema": {"type": "object", "properties": {}}}])
    server = {"remotes": [{"type": "streamable-http",
                           "url": "https://mcp.wedding-seat.com/mcp"}]}
    find.remember([S.make(site="mcp-registry", type_="mcp",
                          id="mcp:com.wedding-seat/wedding-planner", name="Wedding Seat",
                          can="add_mcp", raw={"server": server})])
    out = install.install_thing("mcp:com.wedding-seat/wedding-planner")
    assert out["status"] == "added", out
    assert "Wedding Seat" in [s.get("label") for s in mcp_client.servers()]
    assert install.undo(out["entry"])["ok"]
    assert "Wedding Seat" not in [s.get("label") for s in mcp_client.servers()]
