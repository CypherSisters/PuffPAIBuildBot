"""BuildBot's gateway for assembled apps: a small listener on 127.0.0.1 only.

Assembled app servers and glue runs never reach the internet and never hold
the backend's API key. They ask here instead, with a per-app token:

    POST /assemble/<id>/fetch    {"source", "input"}         glue runs and the app server
                                 or {"url", "key", "accept"}
    POST /assemble/<id>/action   {"action", "input"}        the app server only
    GET  /assemble/<id>/media?u= (Range answered)           the app server only
    GET  /assemble/<id>/settings  -> {"values"}              the app server only
    POST /assemble/<id>/settings  {"key", "value"}           the app server only

``fetch`` and ``media`` go out through the Bridge, never by a path of their
own: a declared source by ``api.fetch_source`` (under the source's
own switch); an address glue asks for, only on a site a declared source or a
found part names, under that site's switch (:func:`.host.backend_fetch`); a
key is added from the vault, bound to its own sites, and never handed back.
Each request writes its line on "What leaves your computer".

Tokens: the app server's token is a file in the app's folder (made by
:func:`puffpai.assemble.server.host.server_token`); each glue run gets its
own token, good only for ``fetch`` and only while that run lives. Requests
must be addressed to 127.0.0.1 on this port (421 otherwise), bodies are at
most 64 KB, and the port is written to ``<state>/assemble/gateway.json`` so
app servers find it after BuildBot restarts.

Why a listener of its own: the backend's routes all want the API key, which
an app must never hold. This listener answers exactly these routes.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import hmac
import http.server
import json
import os
import re
import secrets
import threading
import time
import urllib.parse
from typing import Any, Optional

from puffpai import fsutil
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {"httpd": None, "port": 0, "thread": None}
#: Live glue-run tokens: token -> (app id, expires at (monotonic)).
_RUN_TOKENS: dict[str, tuple[str, float]] = {}
_MAX_BODY = 64 * 1024
_PATH_RE = re.compile(r"^/assemble/([a-z0-9][a-z0-9_-]{0,63})/(fetch|action|media|settings)\Z")


def gateway_file() -> str:
    from puffpai.assemble.server import host
    return os.path.join(host.assemble_root(), "gateway.json")


def new_run_token(app_id: str, seconds: float) -> str:
    """A token for one glue run: ``fetch`` only, for *seconds*."""
    token = secrets.token_urlsafe(32)
    with _LOCK:
        now = time.monotonic()
        for t in [t for t, (_a, until) in _RUN_TOKENS.items() if until < now]:
            _RUN_TOKENS.pop(t, None)
        _RUN_TOKENS[token] = (app_id, now + max(1.0, float(seconds)))
    return token


def end_run_token(token: str) -> None:
    with _LOCK:
        _RUN_TOKENS.pop(token, None)


def _run_token_ok(app_id: str, token: str) -> bool:
    with _LOCK:
        found = _RUN_TOKENS.get(token)
    return bool(found) and found[0] == app_id and found[1] >= time.monotonic()


def _server_token_ok(app_id: str, token: str) -> bool:
    from puffpai.assemble.server import host
    try:
        want = host.read_server_token(app_id)
    except (OSError, ValueError):
        return False
    return bool(want) and hmac.compare_digest(want.encode(), token.encode())


def authorised(app_id: str, route: str, token: str) -> bool:
    """May *token* use *route* of *app_id*? Glue run tokens: ``fetch`` only.
    Never for an app without the owner's one yes (``assemble.consent``)."""
    if not token or len(token) > 200:
        return False
    if not (_server_token_ok(app_id, token)
            or (route == "fetch" and _run_token_ok(app_id, token))):
        return False
    from puffpai.assemble import consent
    return consent.has_yes(app_id)


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "BuildBotGateway/1"
    sys_version = ""

    def log_message(self, *_a):
        pass

    def _send(self, status: int, body: bytes, ctype: str = "application/json",
              extra: Optional[dict] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj: Any) -> None:
        if status >= 400:
            self._drain()
            if isinstance(obj, dict) and "line" in obj:
                obj.setdefault("error", obj["line"])
        self._send(status, json.dumps(obj).encode("utf-8"))

    def _drain(self) -> None:
        """Read a small unread body before an error answer (else Windows
        drops the connection and the caller never sees the answer)."""
        if getattr(self, "_drained", False):
            return
        self._drained = True
        try:
            size = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            size = 0
        if 0 < size <= _MAX_BODY:
            self.rfile.read(size)

    def _route(self) -> Optional[tuple[str, str, str]]:
        values = self.headers.get_all("Host") or []
        port = int(self.server.server_address[1])
        if len(values) != 1 or values[0].strip().lower() not in (
                f"127.0.0.1:{port}", f"localhost:{port}"):
            self._json(421, {"ok": False, "line": "Wrong address."})
            return None
        parts = urllib.parse.urlsplit(self.path)
        m = _PATH_RE.match(parts.path)
        if not m:
            self._json(404, {"ok": False, "line": "Not here."})
            return None
        app_id, route = m.group(1), m.group(2)
        if not authorised(app_id, route, str(self.headers.get("X-Puff-Token") or "")):
            self._json(403, {"ok": False, "line": "Not allowed."})
            return None
        return app_id, route, parts.query

    def _body(self) -> Optional[dict]:
        try:
            size = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            size = -1
        if size < 0 or size > _MAX_BODY:
            self._json(413, {"ok": False, "line": "Too big."})
            return None
        self._drained = True
        try:
            data = json.loads(self.rfile.read(size).decode("utf-8") or "{}") if size else {}
        except ValueError:
            data = None
        if not isinstance(data, dict):
            self._json(400, {"ok": False, "line": "That was not JSON."})
            return None
        return data

    def do_POST(self) -> None:
        got = self._route()
        if got is None:
            return
        app_id, route, _q = got
        if route == "media":
            return self._json(405, {"ok": False, "line": "Use GET."})
        data = self._body()
        if data is None:
            return
        from puffpai.assemble.server import host
        try:
            if route == "settings":
                out = host.save_setting(app_id, data.get("key"), data.get("value"))
            elif route == "fetch" and data.get("source"):
                inp = data.get("input")
                out = host.fetch_source(app_id, str(data.get("source") or ""),
                                        inp if isinstance(inp, dict) else {})
            elif route == "fetch":
                out = host.backend_fetch(app_id, str(data.get("url") or ""),
                                         key=str(data.get("key") or ""),
                                         accept=str(data.get("accept") or ""))
            else:
                inp = data.get("input")
                out = host.call_action(app_id, str(data.get("action") or ""),
                                       inp if isinstance(inp, dict) else {})
        except Exception as exc:  # noqa: BLE001 - one bad request never stops the gateway
            logger.warning("assemble gateway: %s failed: %s", route, type(exc).__name__)
            out = {"ok": False, "line": "BuildBot could not do that.", "rows": []}
        self._json(200, out)

    def do_GET(self) -> None:
        got = self._route()
        if got is None:
            return
        app_id, route, query = got
        from puffpai.assemble.server import host
        if route == "settings":
            return self._json(200, host.settings_view(app_id))
        if route != "media":
            return self._json(405, {"ok": False, "line": "Use POST."})
        url = dict(urllib.parse.parse_qsl(query)).get("u", "")
        try:
            status, headers, body = host.media(app_id, url, self.headers.get("Range") or "")
        except Exception as exc:  # noqa: BLE001
            logger.warning("assemble gateway: media failed: %s", type(exc).__name__)
            status, headers, body = 502, {"Content-Type": "text/plain"}, b"not fetched"
        ctype = headers.pop("Content-Type", "application/octet-stream")
        headers.pop("Content-Length", None)
        self._send(status, body, ctype, headers)


def ensure_running() -> int:
    """Start the gateway once per process; its port (also in gateway.json)."""
    with _LOCK:
        if _STATE["httpd"] is not None:
            port = int(_STATE["port"])
        else:
            httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            httpd.daemon_threads = True
            port = int(httpd.server_address[1])
            thread = threading.Thread(target=httpd.serve_forever, daemon=True,
                                      name="assemble-gateway")
            thread.start()
            _STATE.update(httpd=httpd, port=port, thread=thread)
    path = gateway_file()
    current = fsutil.read_json(path, default={}) or {}
    # The dashboard's port too: the app servers read it on every answer for
    # their frame-ancestors, so "Use it here" follows a dashboard that moved.
    from puffpai.assemble.server import host
    want = {"port": port, "dashboard_port": host._dashboard_port()}  # noqa: SLF001
    if current != want:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fsutil.atomic_write_json(path, want)
    return port


def stop() -> None:
    """Stop the gateway (tests)."""
    with _LOCK:
        httpd = _STATE["httpd"]
        _STATE.update(httpd=None, port=0, thread=None)
        _RUN_TOKENS.clear()
    if httpd is not None:
        httpd.shutdown()
        httpd.server_close()


def url() -> str:
    return f"http://127.0.0.1:{ensure_running()}"


__all__ = ["authorised", "end_run_token", "ensure_running", "gateway_file",
           "new_run_token", "stop", "url"]
