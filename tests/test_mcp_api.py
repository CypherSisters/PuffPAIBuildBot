"""The /mcp routes: Connect my Home Assistant, Add my own tool, learn a tool.

The fake Home Assistant runs on 127.0.0.1 (tests/fake_ha_mcp.py); nothing
reaches past this computer.
"""

from __future__ import annotations

import pytest

pytest.importorskip("mcp")

from fake_ha_mcp import FakeHA  # noqa: E402
from keys_support import fast_vault, machine_key_file, reset_keys  # noqa: E402
from puffpai import api_server, mcp_client, skill_learn  # noqa: E402
from loop_tools import clear_mcp_tools, mcp_tools  # noqa: E402
from puffpai.security import keys  # noqa: E402

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}
# Tool-server routes need a paired device too (conftest stands in for the store).
PAIRED = {**AUTH, "X-Puff-Device-Id": "deviceaaaa000001"}
TOKEN = "eyJhbGciOiJIUzI1NiJ9.route-test-token.c2lnbmF0dXJl"


@pytest.fixture
def client(monkeypatch, tmp_path):
    machine_key_file(monkeypatch, tmp_path)
    reset_keys()
    v = fast_vault(monkeypatch, tmp_path / "vault")
    monkeypatch.setattr(api_server, "_vault_instance", v)
    keys.set_vault_provider(lambda: v)
    clear_mcp_tools()
    api_server.app.config.update(TESTING=True)
    c = api_server.app.test_client()
    c.vault = v
    yield c
    reset_keys()
    clear_mcp_tools()


def test_every_mcp_route_needs_the_api_key(client):
    for method, path in (("get", "/mcp/servers"), ("post", "/mcp/servers"),
                         ("post", "/mcp/servers/x/remove"),
                         ("post", "/mcp/servers/x/refresh"),
                         ("post", "/mcp/home-assistant"), ("get", "/mcp/learn"),
                         ("post", "/mcp/learn"), ("post", "/mcp/learn/x/keep"),
                         ("post", "/mcp/learn/x/discard")):
        assert getattr(client, method)(path).status_code == 401, path


def test_connect_home_assistant_then_list_then_remove(client):
    with FakeHA(TOKEN) as ha:
        r = client.post("/mcp/home-assistant", headers=PAIRED,
                        json={"address": ha.base, "token": TOKEN})
        assert r.status_code == 200, r.get_json()
        assert TOKEN not in r.get_data(as_text=True)
        body = client.get("/mcp/servers", headers=PAIRED).get_json()
        assert body["ready"] is True and "home_assistant_address" in body
        [server] = body["servers"]
        assert server["name"] == "home_assistant" and server["tool_count"] == 3
        assert TOKEN not in str(body)
        r = client.post("/mcp/servers/home_assistant/refresh", headers=PAIRED)
        assert r.status_code == 200 and r.get_json()["ok"]
        outside = client.get("/outside", headers=PAIRED).get_data(as_text=True)
        assert "Tool server: Home Assistant" in outside and TOKEN not in outside
    r = client.post("/mcp/servers/home_assistant/remove", headers=PAIRED)
    assert r.get_json() == {"ok": True, "message": "Home Assistant is removed."}
    assert client.get("/mcp/servers", headers=PAIRED).get_json()["servers"] == []


def test_a_wrong_token_and_a_locked_vault_are_plain_refusals(client):
    with FakeHA(TOKEN) as ha:
        r = client.post("/mcp/home-assistant", headers=PAIRED,
                        json={"address": ha.base, "token": "nope"})
        assert r.status_code == 400
        assert r.get_json()["message"] == "127.0.0.1 did not accept the password."
        client.vault.lock()
        r = client.post("/mcp/home-assistant", headers=PAIRED,
                        json={"address": ha.base, "token": TOKEN})
        assert r.status_code == 409 and "vault is locked" in r.get_json()["message"]


def test_a_password_in_the_address_is_refused(client):
    r = client.post("/mcp/home-assistant?token=abc", headers=PAIRED,
                    json={"address": "http://127.0.0.1:1"})
    assert r.status_code == 400 and "never sent in the address" in r.get_json()["message"]


def test_add_my_own_tool_with_a_password_box(client):
    with FakeHA(TOKEN) as ha:
        r = client.post("/mcp/servers", headers=PAIRED,
                        json={"name": "Garage", "target": ha.url, "password": TOKEN})
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["server"]["name"] == "garage"
        assert keys.tool_secret("garage") == {"Authorization": f"Bearer {TOKEN}"}
        assert "mcp__garage__HassTurnOn" in mcp_tools()


def test_add_my_own_tool_says_what_went_wrong(client):
    r = client.post("/mcp/servers", headers=PAIRED, json={"name": "x", "target": ""})
    assert r.status_code == 400 and r.get_json()["message"] == \
        "Type a web address or a command."


def test_learn_keep_and_discard_routes(client, monkeypatch):
    class Brain:
        def chat(self, messages, **_):
            return "---\nname: nas-space\ndescription: Shows free disk space.\n---\n# NAS"

    monkeypatch.setattr(skill_learn, "_backend", lambda: Brain())
    r = client.post("/mcp/learn", headers=PAIRED, json={"text": "df -h"})
    assert r.status_code == 200 and r.get_json()["name"] == "nas-space"
    assert [d["name"] for d in client.get("/mcp/learn", headers=PAIRED).get_json()["drafts"]] \
        == ["nas-space"]
    r = client.post("/mcp/learn/nas-space/keep", headers=PAIRED, json={})
    assert r.status_code == 200 and "is added" in r.get_json()["message"]
    client.post("/mcp/learn", headers=PAIRED, json={"text": "df -h"})
    assert client.post("/mcp/learn/nas-space/discard", headers=PAIRED).status_code == 200
    assert client.post("/mcp/learn/nas-space/keep", headers=PAIRED).status_code == 404
