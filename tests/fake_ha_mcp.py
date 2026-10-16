"""A small fake of Home Assistant's own MCP Server, on this computer only.

Home Assistant's "Model Context Protocol Server" integration serves its Assist
tools at ``/api/mcp`` (Streamable HTTP) behind a long-lived token. This fake
offers the three tools that matter for lights, with Home Assistant's names:

* ``GetLiveContext``: the lights and whether each is on (the lights list),
* ``HassTurnOn`` / ``HassTurnOff``: by ``name`` (and optional ``area``).

Used two ways:

* in a test, ``with FakeHA(token="...") as ha:`` serves it on 127.0.0.1 with a
  free port, and ``ha.url`` is the ``.../api/mcp`` address;
* as a program, ``python fake_ha_mcp.py --stdio`` speaks MCP on stdin/stdout,
  and ``python fake_ha_mcp.py --port 8123 --token t`` serves it over HTTP for
  a hand test of the dashboard.

Never the owner's live Home Assistant: nothing here reaches past this computer.
"""

from __future__ import annotations

import argparse
import socket
import threading
import time
from typing import Optional

from mcp.server.fastmcp import FastMCP


def make_server(lights: Optional[dict] = None) -> tuple[FastMCP, dict]:
    state = lights if lights is not None else {
        "Porch light": {"area": "Outside", "on": False},
        "Kitchen light": {"area": "Kitchen", "on": True},
        "Bedroom lamp": {"area": "Bedroom", "on": False},
    }
    server = FastMCP("Home Assistant", stateless_http=True,
                     streamable_http_path="/api/mcp")

    def _find(name: str, area: str) -> list[str]:
        want = (name or "").strip().lower()
        where = (area or "").strip().lower()
        return [n for n, v in state.items()
                if (not want or want in n.lower())
                and (not where or where == v["area"].lower())]

    @server.tool(name="GetLiveContext",
                 description="Provides real-time information about the CURRENT "
                             "state of devices, like which lights are on.")
    def get_live_context() -> str:
        lines = ["Live Context: An overview of the areas and the devices in "
                 "this smart home:"]
        for n, v in state.items():
            lines.append(f"- names: {n}\n  domain: light\n  state: "
                         f"'{'on' if v['on'] else 'off'}'\n  areas: {v['area']}")
        return "\n".join(lines)

    @server.tool(name="HassTurnOn",
                 description="Turns on/opens a device or entity.")
    def hass_turn_on(name: str = "", area: str = "", domain: str = "") -> str:
        hits = _find(name, area)
        if not hits:
            return f"No device named {name or area} was found."
        for n in hits:
            state[n]["on"] = True
        return "Turned on: " + ", ".join(hits)

    @server.tool(name="HassTurnOff",
                 description="Turns off/closes a device or entity.")
    def hass_turn_off(name: str = "", area: str = "", domain: str = "") -> str:
        hits = _find(name, area)
        if not hits:
            return f"No device named {name or area} was found."
        for n in hits:
            state[n]["on"] = False
        return "Turned off: " + ", ".join(hits)

    return server, state


class _TokenCheck:
    """Home Assistant answers 401 without the right Bearer token."""

    def __init__(self, app, token: str):
        self.app = app
        self.token = token
        self.seen: list[str] = []

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            auth = dict(scope.get("headers") or []).get(b"authorization", b"")
            self.seen.append(auth.decode("latin-1"))
            if auth != f"Bearer {self.token}".encode("latin-1"):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"401: Unauthorized"})
                return
        await self.app(scope, receive, send)


class FakeHA:
    """Serve the fake on 127.0.0.1 in a background thread."""

    def __init__(self, token: str = "fake-ha-token", lights: Optional[dict] = None,
                 port: int = 0):
        self.token = token
        self._want_port = port
        self.mcp, self.lights = make_server(lights)
        self.guard = _TokenCheck(self.mcp.streamable_http_app(), token)
        self._server = None
        self._thread = None
        self.port = 0

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def url(self) -> str:
        return self.base + "/api/mcp"

    def __enter__(self) -> "FakeHA":
        import uvicorn
        try:  # sse_starlette keeps one exit event per process, bound to the
            from sse_starlette.sse import AppStatus  # first server's loop
            AppStatus.should_exit_event = None
        except ImportError:
            pass
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", self._want_port))
        self.port = sock.getsockname()[1]
        config = uvicorn.Config(self.guard, log_level="warning", lifespan="on")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run,
                                        kwargs={"sockets": [sock]}, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 15
        while not self._server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        if not self._server.started:
            raise RuntimeError("the fake Home Assistant did not start")
        return self

    def __exit__(self, *exc) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(10)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stdio", action="store_true")
    parser.add_argument("--port", type=int, default=8123)
    parser.add_argument("--token", default="fake-ha-token")
    args = parser.parse_args()
    if args.stdio:
        server, _ = make_server()
        server.run("stdio")
        return
    with FakeHA(args.token, port=args.port) as ha:
        print(f"Fake Home Assistant MCP at {ha.url} (token {args.token})", flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
