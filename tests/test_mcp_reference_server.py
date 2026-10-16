"""Proof against a REAL reference MCP server, run on this computer.

``mcp-server-time`` is one of the Model Context Protocol project's own
reference servers (https://github.com/modelcontextprotocol/servers, MIT). It
is not a BuildBot requirement, so this test runs only where it is installed
(``pip install mcp-server-time``) and is skipped everywhere else. It speaks
stdio and reaches nothing outside this computer.
"""

from __future__ import annotations

import json
import sys

import pytest

pytest.importorskip("mcp")
pytest.importorskip("mcp_server_time",
                    reason="the reference server mcp-server-time is not installed here")

from puffpai import mcp_client  # noqa: E402
from loop_tools import clear_mcp_tools, mcp_tools  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_mcp_tools()
    yield
    clear_mcp_tools()


def _command():
    """``python -m mcp_server_time`` with this run's import paths (a started
    program gets only a short list of settings, so it is handed them)."""
    paths = [p for p in sys.path if p]
    code = ("import sys, runpy; sys.path[:0] = " + repr(paths)
            + "; sys.argv = ['mcp_server_time', '--local-timezone', 'UTC']; "
              "runpy.run_module('mcp_server_time', run_name='__main__')")
    return [sys.executable, "-c", code]


def test_the_reference_time_server_adds_lists_and_answers():
    out = mcp_client.add_server("time", _command(), label="Time")
    assert out["ok"], out
    assert set(out["server"]["tools"]) >= {"get_current_time", "convert_time"}
    fn = mcp_tools()["mcp__time__get_current_time"]["fn"]
    said = fn({"timezone": "Europe/London"})
    data = json.loads(said)
    assert data["timezone"] == "Europe/London" and "datetime" in data
    converted = mcp_client.call_tool("time", "convert_time", {
        "source_timezone": "UTC", "time": "12:00",
        "target_timezone": "Asia/Tokyo"})
    assert "Asia/Tokyo" in converted
    bad = mcp_client.call_tool("time", "get_current_time", {"timezone": "Nowhere/Land"})
    assert bad.startswith("The tool said there was a problem")
    assert mcp_client.remove_server("time")["ok"]
