"""Tool servers (MCP): the client, Home Assistant, the vault, the switch, the log.

Every server here runs on this computer: the fake Home Assistant
(tests/fake_ha_mcp.py) over Streamable HTTP on 127.0.0.1, and the same fake
started as a command (stdio). Nothing reaches past this computer.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from fake_ha_mcp import FakeHA  # noqa: E402
from keys_support import fast_vault, machine_key_file, reset_keys  # noqa: E402
from puffpai import mcp_client, outside_services  # noqa: E402
from loop_tools import clear_mcp_tools, mcp_tools  # noqa: E402
from puffpai.security import bridge_guard, egress_ledger, keys  # noqa: E402

TOKEN = "eyJhbGciOiJIUzI1NiJ9.fake-ha-token-for-tests.c2lnbmF0dXJl"
FAKE = str(Path(__file__).resolve().parent / "fake_ha_mcp.py")


@pytest.fixture
def vault(tmp_path, monkeypatch):
    machine_key_file(monkeypatch, tmp_path)
    reset_keys()
    v = fast_vault(monkeypatch, tmp_path / "vault")
    keys.set_vault_provider(lambda: v)
    clear_mcp_tools()
    yield v
    keys.set_vault_provider(None)
    clear_mcp_tools()


@pytest.fixture
def ha():
    with FakeHA(TOKEN) as server:
        yield server


def _tool(name):
    return mcp_tools()[name]["fn"]


def _ledger():
    return egress_ledger.read(1.0, limit=100)


def _stdio_command():
    """The fake, started as a command, with this test run's import paths."""
    paths = [p for p in sys.path if p]
    code = ("import sys, runpy; sys.path[:0] = " + repr(paths)
            + "; sys.argv = ['fake_ha_mcp', '--stdio']; runpy.run_path("
            + repr(FAKE) + ", run_name='__main__')")
    return [sys.executable, "-c", code]


# ── reading what she typed ──────────────────────────────────────────────────

@pytest.mark.parametrize("text,kind", [
    ("http://192.168.1.20:8123/api/mcp", "http"),
    ("https://tools.example.org/mcp", "http"),
    ("http://localhost:9000/sse", "sse"),
])
def test_a_web_address_is_read(text, kind):
    assert mcp_client.parse_target(text) == {"kind": kind, "url": text}


def test_a_command_is_read_into_words():
    got = mcp_client.parse_target('npx -y "@modelcontextprotocol/server-everything"')
    assert got == {"kind": "stdio", "command": "npx",
                   "args": ["-y", "@modelcontextprotocol/server-everything"]}
    assert mcp_client.parse_target(["uvx", "mcp-server-time"]) == {
        "kind": "stdio", "command": "uvx", "args": ["mcp-server-time"]}


@pytest.mark.parametrize("bad", ["", "   ", "http://user:pass@host/mcp", "http://"])
def test_unusable_targets_get_a_plain_sentence(bad):
    with pytest.raises(mcp_client.McpError) as err:
        mcp_client.parse_target(bad)
    assert err.value.message.endswith(".")


@pytest.mark.parametrize("typed", [
    "homeassistant.local:8123", "http://homeassistant.local:8123/",
    "http://homeassistant.local:8123/api", "http://homeassistant.local:8123/api/mcp",
])
def test_the_home_assistant_address_becomes_its_mcp_address(typed):
    assert mcp_client.home_assistant_url(typed) == \
        "http://homeassistant.local:8123/api/mcp"


def test_names_slugs_and_long_tool_names():
    assert mcp_client.slug("My 3D Printer!") == "my_3d_printer"
    assert mcp_client.slug("3dprinter") == "t_3dprinter"
    assert mcp_client.tool_name("home_assistant", "HassTurnOn") == \
        "mcp__home_assistant__HassTurnOn"
    long = mcp_client.tool_name("a_very_long_server_name", "x" * 80)
    assert len(long) == 64 and long == mcp_client.tool_name(
        "a_very_long_server_name", "x" * 80)


def test_input_schemas_are_repaired():
    assert mcp_client._schema(None) == {"type": "object", "properties": {},
                                        "required": []}
    got = mcp_client._schema({"properties": {"a": {"type": "string"}},
                              "required": ["a", "ghost"]})
    assert got["type"] == "object" and got["required"] == ["a"]


# ── Home Assistant through its MCP server ───────────────────────────────────

def test_connect_home_assistant_puts_the_token_in_the_vault_and_tools_in_the_loop(
        vault, ha):
    out = mcp_client.connect_home_assistant(ha.base, TOKEN)
    assert out["ok"], out
    assert "3 tools" in out["message"]
    server = out["server"]
    assert server["name"] == "home_assistant" and server["has_password"]
    assert server["address"] == ha.url
    # The token is in the vault, never in the saved list.
    saved = Path(mcp_client._path()).read_text(encoding="utf-8")
    assert TOKEN not in saved
    assert keys.tool_secret("home_assistant") == {"Authorization": f"Bearer {TOKEN}"}
    # Its tools joined the loop with Hermes-style names.
    names = set(mcp_tools())
    assert {"mcp__home_assistant__GetLiveContext", "mcp__home_assistant__HassTurnOn",
            "mcp__home_assistant__HassTurnOff"} <= names
    assert not mcp_tools()["mcp__home_assistant__HassTurnOn"]["needs_yes"]
    # "what lights are on?" then "turn on the porch light"
    assert "Porch light" in _tool("mcp__home_assistant__GetLiveContext")({})
    assert ha.lights["Porch light"]["on"] is False
    said = _tool("mcp__home_assistant__HassTurnOn")({"name": "porch light"})
    assert said == "Turned on: Porch light"
    assert ha.lights["Porch light"]["on"] is True
    assert ha.guard.seen and all(h == f"Bearer {TOKEN}" for h in ha.guard.seen)


def test_every_use_writes_one_log_line_and_never_the_token(vault, ha):
    mcp_client.connect_home_assistant(ha.base, TOKEN)
    _tool("mcp__home_assistant__HassTurnOff")({"name": "kitchen"})
    lines = [r for r in _ledger() if r["service"] == "mcp_home_assistant"]
    assert [r["what"] for r in lines][:2] == ["mcp_call", "mcp_tools"]
    assert all(r["host"] == "127.0.0.1" and r["outcome"] == "done" for r in lines)
    log_text = Path(egress_ledger.log_path()).read_text(encoding="utf-8")
    assert TOKEN not in log_text and "kitchen" not in log_text
    words = outside_services.log_rows()
    assert words[0]["service"] == "Tool server: Home Assistant"
    assert words[0]["what"] == "used a tool from a tool server"


def test_a_wrong_token_keeps_nothing(vault, ha):
    out = mcp_client.connect_home_assistant(ha.base, "not-the-token")
    assert not out["ok"] and out["code"] == "password"
    assert out["message"] == "127.0.0.1 did not accept the password."
    assert mcp_client.servers() == []
    assert not keys.has_tool_secret("home_assistant")
    assert mcp_tools() == {}


def test_no_token_says_where_to_make_one(vault):
    out = mcp_client.connect_home_assistant("homeassistant.local:8123", "")
    assert not out["ok"] and "Long-lived access tokens" in out["message"]


def test_home_assistant_without_its_mcp_server_says_how_to_turn_it_on(vault, ha):
    out = mcp_client.add_server("home_assistant", ha.base + "/api/nothing_here",
                                {"Authorization": f"Bearer {TOKEN}"})
    assert not out["ok"]
    out = mcp_client.connect_home_assistant(ha.base + "/wrong", TOKEN)
    assert not out["ok"] and "Model Context Protocol Server" in out["message"]


def test_the_house_still_answers_while_the_vault_is_locked(vault, ha):
    assert mcp_client.connect_home_assistant(ha.base, TOKEN)["ok"]
    vault.lock()
    assert _tool("mcp__home_assistant__HassTurnOn")({"name": "bedroom"}) == \
        "Turned on: Bedroom lamp"


def test_without_the_locked_copy_a_locked_vault_is_said_plainly(vault, ha):
    out = mcp_client.connect_home_assistant(ha.base, TOKEN, keep_working_locked=False)
    assert out["ok"] and "while your vault is open" in out["message"]
    vault.lock()
    said = _tool("mcp__home_assistant__HassTurnOn")({"name": "bedroom"})
    assert "your vault is locked" in said.lower()
    assert ha.lights["Bedroom lamp"]["on"] is False


def test_a_password_needs_the_vault_open(vault, ha):
    vault.lock()
    out = mcp_client.connect_home_assistant(ha.base, TOKEN)
    assert not out["ok"] and out["code"] == "locked"
    assert mcp_client.servers() == []
    assert ha.guard.seen == []  # the token was never sent


# ── the switch on "What leaves your computer" ───────────────────────────────

def test_each_server_has_a_switch_that_stops_and_starts_it(vault, ha):
    mcp_client.connect_home_assistant(ha.base, TOKEN)
    row = outside_services.row("mcp_home_assistant")
    assert row["state"] == "on" and row["can_switch"] and row["kind"] == "mcp"
    assert row["name"] == "Tool server: Home Assistant"
    assert row["turned_on_by"] == "you"
    assert TOKEN not in json.dumps(row)
    outside_services.switch_off("mcp_home_assistant")
    said = _tool("mcp__home_assistant__HassTurnOn")({"name": "porch"})
    assert "switched off" in said and ha.lights["Porch light"]["on"] is False
    refused = [r for r in _ledger() if r["service"] == "mcp_home_assistant"][0]
    assert refused["outcome"] == "refused" and refused["code"] == "switched_off"
    assert outside_services.row("mcp_home_assistant")["state"] == "off"
    outside_services.switch_on("mcp_home_assistant")
    assert _tool("mcp__home_assistant__HassTurnOn")({"name": "porch"}) == \
        "Turned on: Porch light"


def test_removing_a_server_takes_its_tools_password_and_row(vault, ha):
    mcp_client.connect_home_assistant(ha.base, TOKEN)
    out = mcp_client.remove_server("home_assistant")
    assert out == {"ok": True, "message": "Home Assistant is removed."}
    assert mcp_client.servers() == [] and mcp_tools() == {}
    assert not keys.has_tool_secret("home_assistant")
    assert keys.tool_secret("home_assistant") is None
    assert outside_services.row("mcp_home_assistant") is None
    assert not os.path.exists(os.path.join(vault.vault_dir,
                                           keys.tool_file("home_assistant")))
    # an old log line keeps a plain name
    assert any(r["service"] == outside_services.MCP_LOG_NAME
               for r in outside_services.log_rows())
    assert mcp_client.remove_server("home_assistant")["ok"] is False


def test_a_removed_tool_record_is_cleared_from_the_vault(vault):
    keys.put_tool_secret("printer", {"X-Key": "abcdefgh"})
    keys.put_tool_secret("nas", {"X-Key": "ijklmnop"})
    keys._write_tool_names({"nas"})  # noqa: SLF001 - removed while locked
    assert keys.purge_removed() == 1
    assert keys.tool_secret("nas") == {"X-Key": "ijklmnop"}


# ── a command on this computer (stdio) ─────────────────────────────────────

def test_a_command_tool_server_works(vault):
    out = mcp_client.add_server("my house", _stdio_command(), label="My house")
    assert out["ok"], out
    assert out["server"]["name"] == "my_house"
    assert out["server"]["tool_count"] == 3
    said = _tool("mcp__my_house__HassTurnOn")({"name": "porch"})
    assert said == "Turned on: Porch light"
    row = outside_services.row("mcp_my_house")
    assert row["websites"] == ["this computer"]
    lines = [r for r in _ledger() if r["service"] == "mcp_my_house"]
    assert lines and all(r["host"] == "" for r in lines)


def test_a_command_that_does_not_exist_is_said_plainly(vault):
    out = mcp_client.add_server("nope", ["puffpai-no-such-program-xyz"])
    assert not out["ok"]
    assert mcp_client.servers() == []


def test_a_command_that_stops_says_its_own_reason(vault):
    """A tool server called filamental exited ("no vault path supplied") and she
    saw only "Connection closed"."""
    code = ("import sys; sys.stderr.write('npm warn deprecated x\\n'); "
            "sys.stderr.write('Error: no vault path supplied\\n'); sys.exit(1)")
    out = mcp_client.add_server("filamental", [sys.executable, "-c", code])
    assert not out["ok"]
    assert "no vault path supplied" in out["message"], out
    assert "npm warn" not in out["message"]


def test_a_command_cannot_take_a_password(vault):
    out = mcp_client.add_server("x", ["prog"], {"Authorization": "Bearer a"})
    assert not out["ok"] and out["code"] == "headers"


# ── without the mcp package ─────────────────────────────────────────────────

def test_without_the_mcp_package_it_says_how_to_add_it(vault, monkeypatch):
    monkeypatch.setattr(mcp_client, "sdk_available", lambda: False)
    out = mcp_client.add_server("x", "http://127.0.0.1:9/mcp")
    assert not out["ok"] and "pip install mcp" in out["message"]
    assert mcp_client.servers() == []


# ── the loop's registry ─────────────────────────────────────────────────────

def test_tools_go_to_brain_tools_and_leave_it_again(vault, ha):
    from puffpai import brain_tools
    mcp_client.connect_home_assistant(ha.base, TOKEN)
    tool = brain_tools.find("mcp__home_assistant__HassTurnOn", {})
    assert tool is not None and tool.source == "mcp" and not tool.needs_yes
    assert tool.description.endswith("(from Home Assistant)")
    assert tool.schema["type"] == "object" and "name" in tool.schema["properties"]
    out = brain_tools.call("mcp__home_assistant__HassTurnOn", {"name": "porch"}, {})
    assert out["ok"] and out["result"] == "Turned on: Porch light"
    mcp_client.remove_server("home_assistant")
    assert brain_tools.find("mcp__home_assistant__HassTurnOn", {}) is None
    assert mcp_tools() == {}


def test_tool_functions_take_arguments_any_way_the_loop_calls():
    fn = mcp_client._flex(lambda a: json.dumps(a, sort_keys=True))
    assert fn({"x": 1}) == fn({"x": 1}, {"ctx": True}) == fn(x=1) == '{"x": 1}'


def test_chat_tools_ask_for_a_yes_before_changing_anything(vault):
    names = mcp_client.register_chat_tools()
    tools = mcp_tools()
    assert names == ["connect_home_assistant", "add_tool_server",
                     "remove_tool_server", "list_tool_servers"]
    assert not tools["connect_home_assistant"]["needs_yes"]  # it adds nothing
    assert tools["add_tool_server"]["needs_yes"]
    assert tools["remove_tool_server"]["needs_yes"]
    assert not tools["list_tool_servers"]["needs_yes"]
    assert tools["list_tool_servers"]["fn"]({}) == "No tool servers are added yet."


def test_chat_takes_the_address_and_sends_the_token_to_the_smart_home_page(vault):
    """A pasted token never reaches the model (api_server._swap_pasted_key),
    so chat hands the token step to the Smart Home page, address filled in."""
    from puffpai import web_search
    mcp_client.register_chat_tools()
    said = _tool("connect_home_assistant")({"address": "homeassistant.local:8123"})
    assert "/dashboard/homeassistant" in said and "vault" in said
    assert "http://homeassistant.local:8123" in said
    assert mcp_client.home_assistant_address_hint() == "http://homeassistant.local:8123"
    said = _tool("connect_home_assistant")({
        "address": "homeassistant.local:8123",
        "token": web_search.KEY_PASTE_PLACEHOLDER})
    assert "Smart Home page" in said
    assert mcp_client.servers() == []
    assert "cannot be read" in _tool("connect_home_assistant")({"address": "http://"})


def test_chat_connect_with_a_token_never_repeats_it(vault, ha):
    mcp_client.register_chat_tools()
    said = _tool("connect_home_assistant")({"address": ha.base, "token": TOKEN})
    assert "Home Assistant is added" in said and TOKEN not in said
    listed = _tool("list_tool_servers")({})
    assert "Home Assistant (home_assistant): 3 tools, on" in listed


def test_saved_servers_rejoin_the_loop_after_a_restart(vault, ha):
    mcp_client.connect_home_assistant(ha.base, TOKEN)
    clear_mcp_tools()
    count = mcp_client.register_all()
    assert count == 3
    assert "mcp__home_assistant__GetLiveContext" in mcp_tools()
    assert "connect_home_assistant" in mcp_tools()


# ── the Bridge sees this listed exit ────────────────────────────────────────

def test_sessions_run_with_this_exit_on_the_stack(monkeypatch):
    assert "mcp_client.py" in bridge_guard.EXIT_FILES
    monkeypatch.setattr(mcp_client, "sdk_available", lambda: True)

    async def fake_session(rec, headers, work):
        return bridge_guard.exit_on_stack(sys._getframe())

    monkeypatch.setattr(mcp_client, "_with_session", fake_session)
    assert mcp_client._run({"name": "x"}, None, None, 5) is True
