"""MCP Apps (SEP-1865): a tool's _meta is kept, its ui:// screen is read from
the same server in the same session, and app-only tools stay away from the
model. A fake session stands in for the server: no network, no mcp package.
"""
from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace as NS

import pytest

from puffpai import api_server, brain_tools, mcp_client

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}
PAIRED = {**AUTH, "X-Puff-Device-Id": "deviceaaaa000001"}
URI = "ui://weather/dashboard"
HTML = "<!doctype html><html><body><script>1</script>Weather</body></html>"


def _tool(name, meta=None):
    return NS(name=name, description=f"{name} tool", inputSchema={"type": "object"}, meta=meta)


class FakeSession:
    def __init__(self, contents=None, result_meta=None):
        self.contents = contents
        self.result_meta = result_meta
        self.read = []
        self.called = []

    async def call_tool(self, tool, args):
        self.called.append((tool, args))
        return NS(content=[NS(type="text", text="Sunny, 21 C")],
                  structuredContent={"temp": 21}, isError=False, meta=self.result_meta)

    async def read_resource(self, uri):
        self.read.append(str(uri))
        return NS(contents=self.contents if self.contents is not None else [
            NS(uri=URI, mimeType="text/html;profile=mcp-app", text=HTML, meta=None)])


@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    rows = mcp_client._tool_rows(NS(tools=[  # noqa: SLF001
        _tool("forecast", {"ui": {"resourceUri": URI}}),
        _tool("refresh", {"ui": {"resourceUri": URI, "visibility": ["app"]}}),
        _tool("old_style", {"ui/resourceUri": URI}),
        _tool("plain"),
    ]))
    mcp_client._write({"weather": {"name": "weather", "label": "Weather", "kind": "http",  # noqa: SLF001
                                   "url": "https://example.invalid/mcp",
                                   "has_password": False, "tools": rows}})
    monkeypatch.setattr(mcp_client, "_switch_is_off", lambda name: False)
    monkeypatch.setattr(mcp_client, "_log", lambda *a, **k: None)
    box = {"session": FakeSession()}

    def fake_run(rec, headers, work, timeout):
        return asyncio.run(work(box["session"]))
    monkeypatch.setattr(mcp_client, "_run", fake_run)
    api_server.app.config.update(TESTING=True)
    return box


def test_meta_is_kept_on_the_tool_list(server):
    rows = {t["name"]: t for t in mcp_client.list_tools("weather")}
    assert rows["forecast"]["meta"] == {"ui": {"resourceUri": URI}}
    assert mcp_client.ui_resource_uri(rows["old_style"]["meta"]) == URI
    assert "meta" not in rows["plain"]


def test_an_oversized_meta_keeps_only_its_ui_part():
    big = {"ui": {"resourceUri": URI}, "junk": "x" * 10_000}
    assert mcp_client._meta_of(NS(meta=big)) == {"ui": {"resourceUri": URI}}  # noqa: SLF001


@pytest.mark.parametrize("uri", ["https://evil.example/x", "ui://a b", "file:///etc/passwd", ""])
def test_only_a_ui_address_counts(uri):
    assert mcp_client.ui_resource_uri({"ui": {"resourceUri": uri}}) == ""


def test_an_app_only_tool_is_not_handed_to_the_model(server):
    rec = mcp_client._read()["weather"]  # noqa: SLF001
    try:
        names = mcp_client._register_server_tools(rec)  # noqa: SLF001
        assert mcp_client.tool_name("weather", "forecast") in names
        assert mcp_client.tool_name("weather", "refresh") not in names
        assert brain_tools.find(mcp_client.tool_name("weather", "refresh"),
                                {"caller": "owner"}) is None
    finally:
        mcp_client._unregister_server_tools(rec)  # noqa: SLF001


def test_the_screen_is_read_from_the_same_server_in_the_same_session(server):
    got = mcp_client.call_tool_with_ui("weather", "forecast", {"city": "Raleigh"})
    assert got["ui"]["uri"] == URI and got["ui"]["html"] == HTML
    assert got["result"]["structuredContent"] == {"temp": 21}
    assert server["session"].read == [URI]
    assert "Sunny" in got["text"]


def test_a_screen_named_by_the_result_is_read_too(server):
    server["session"] = FakeSession(result_meta={"ui": {"resourceUri": URI}})
    got = mcp_client.call_tool_with_ui("weather", "plain", {})
    assert got["ui"]["html"] == HTML


def test_a_base64_screen_is_decoded(server):
    server["session"] = FakeSession(contents=[NS(
        uri=URI, mimeType="text/html;profile=mcp-app", text=None,
        blob=base64.b64encode(HTML.encode()).decode(), meta=None)])
    assert mcp_client.call_tool_with_ui("weather", "forecast", {})["ui"]["html"] == HTML


@pytest.mark.parametrize("contents", [
    [NS(uri=URI, mimeType="application/javascript", text="alert(1)", meta=None)],
    [NS(uri="ui://other/page", mimeType="text/html", text=HTML, meta=None)],
    [NS(uri=URI, mimeType="text/html", text="x" * (mcp_client.MAX_UI_BYTES + 1), meta=None)],
    [],
])
def test_a_screen_that_is_not_one_html_page_is_not_shown(server, contents):
    server["session"] = FakeSession(contents=contents)
    got = mcp_client.call_tool_with_ui("weather", "forecast", {})
    assert got["ui"] is None and "Sunny" in got["text"]


def test_a_call_from_the_screen_never_reads_another_screen(server):
    got = mcp_client.call_tool_with_ui("weather", "refresh", {}, from_app=True)
    assert got["ui"] is None and server["session"].read == []


def test_a_screen_may_not_call_a_tool_hidden_from_apps(server, monkeypatch):
    rec = mcp_client._read()  # noqa: SLF001
    for t in rec["weather"]["tools"]:
        if t["name"] == "plain":
            t["meta"] = {"ui": {"visibility": ["model"]}}
    mcp_client._write(rec)  # noqa: SLF001
    got = mcp_client.call_tool_with_ui("weather", "plain", {}, from_app=True)
    assert got.get("refused") and server["session"].called == []


def test_the_page_route_hands_the_screen_to_the_page(server):
    client = api_server.app.test_client()
    r = client.post("/things/tool-weather/run", headers=PAIRED,
                    json={"tool": "forecast", "args": {"city": "Raleigh"}})
    body = r.get_json()
    assert r.status_code == 200 and body["ui"]["html"] == HTML
    assert body["app_result"]["content"][0]["text"] == "Sunny, 21 C"
    page = client.get("/things/tool-weather", headers=PAIRED).get_json()
    flags = {t["name"]: t["has_screen"] for t in page["tools"]}
    assert flags["forecast"] is True and flags["plain"] is False
    r = client.post("/things/tool-weather/run", headers=PAIRED,
                    json={"tool": "refresh", "args": {}, "from_app": True})
    assert r.status_code == 200 and "ui" not in r.get_json()
    assert json.dumps(r.get_json())


def test_an_app_screen_runs_inside_the_locked_host_document():
    """The lock is on the frame, not on the page, so it holds however the
    page was reached (a client-side navigation keeps the first page's
    policy). Proved in headless Edge for direct and soft navigation; this
    pins the shape."""
    import os
    dash = os.path.join(os.path.dirname(__file__), "..", "dashboard")
    route = open(os.path.join(dash, "app", "mcp-app-host", "route.ts"), encoding="utf-8").read()
    for rule in ("default-src 'none'", "connect-src 'none'", "frame-src 'none'",
                 "child-src 'none'", "frame-ancestors 'self'", "sandbox allow-scripts"):
        assert rule in route, rule
    assert 'inner.setAttribute("sandbox", "allow-scripts")' in route
    mw = open(os.path.join(dash, "middleware.ts"), encoding="utf-8").read()
    assert 'const MCP_APP_HOST = "/mcp-app-host";' in mw
    assert "pathname !== MCP_APP_HOST &&" in mw
    frame = open(os.path.join(dash, "app", "dashboard", "things", "[id]", "McpAppFrame.tsx"),
                 encoding="utf-8").read()
    body = frame.split("export default")[1]
    assert 'sandbox="allow-scripts"' in body and "allow-same-origin" not in body
    assert "src={HOST}" in body and "srcDoc" not in body


def test_tool_server_routes_need_a_paired_device(server):
    client = api_server.app.test_client()
    for method, path in (("get", "/mcp/servers"), ("post", "/mcp/servers"),
                         ("post", "/mcp/servers/weather/remove"),
                         ("post", "/mcp/servers/weather/refresh"),
                         ("post", "/mcp/home-assistant"), ("get", "/mcp/learn"),
                         ("post", "/mcp/learn"), ("post", "/mcp/learn/x/keep"),
                         ("post", "/mcp/learn/x/discard")):
        r = getattr(client, method)(path, headers=AUTH,
                                    json={"name": "x", "target": "npx -y something"})
        assert r.status_code == 403 and r.get_json()["code"] == "not_paired", path
    assert "weather" in {s["name"] for s in mcp_client.servers()}


def test_the_added_message_counts_only_tools_chat_can_use(server, monkeypatch):
    rows = mcp_client._read()["weather"]["tools"]  # noqa: SLF001
    monkeypatch.setattr(mcp_client, "_fetch_tools", lambda rec, headers: rows)
    monkeypatch.setattr(mcp_client, "_set_switch", lambda *a, **k: None)
    monkeypatch.setattr(mcp_client, "_register_server_tools", lambda rec: [])
    out = mcp_client.add_server("weather2", "https://example.invalid/mcp")
    assert out["ok"] and "It has 3 tools" in out["message"], out["message"]
    assert len(rows) == 4
