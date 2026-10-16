"""An assembled app's pages may be framed by the dashboard and nothing else.

The app used to answer ``frame-ancestors 'self'``, so the thing
page's "Use it here" frame was blocked. Now: exactly the dashboard's origins
(127.0.0.1 and localhost on its port), never 'self' or anything else, and
''none'' when the port is not known. Another site's POST is still refused.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import argparse
import http.server
import json
import threading
import urllib.error
import urllib.request

import pytest

from puffpai.assemble.server import app as app_server


@pytest.fixture
def serve(tmp_path):
    started = []

    def run(dashboard_port):
        folder = tmp_path / f"app{dashboard_port}"
        folder.mkdir()
        (folder / "spec.json").write_text(json.dumps({"title": "Earth", "actions": []}),
                                          encoding="utf-8")
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
        port = httpd.server_address[1]
        args = argparse.Namespace(port=port, app_id="earth", folder=str(folder),
                                  gateway_file=str(tmp_path / "gw.json"), scaffold="",
                                  dashboard_port=dashboard_port)
        httpd.RequestHandlerClass = app_server.make_handler(app_server.Server(args))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        started.append(httpd)
        return port
    yield run
    for httpd in started:
        httpd.shutdown()
        httpd.server_close()


def _get(port, path="/openapi.json", method="GET", headers=None, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                 data=body, headers=headers or {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=10) as resp:
            return resp.status, dict(resp.headers.items())
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items())


def _ancestors(headers):
    csp = headers.get("Content-Security-Policy", "")
    return [p.strip() for p in csp.split(";") if p.strip().startswith("frame-ancestors")]


def test_only_the_dashboard_may_frame_it(serve):
    port = serve(3140)
    for path in ("/openapi.json", "/", "/nothing-here"):
        status, headers = _get(port, path)
        assert _ancestors(headers) == [
            "frame-ancestors http://127.0.0.1:3140 http://localhost:3140"], (path, headers)
        assert "'self'" not in _ancestors(headers)[0]


def test_without_a_dashboard_port_nothing_may_frame_it(serve):
    port = serve(0)
    _status, headers = _get(port)
    assert _ancestors(headers) == ["frame-ancestors 'none'"]


def test_another_sites_request_is_still_refused(serve):
    port = serve(3140)
    status, _ = _get(port, "/settings", "POST",
                     {"Origin": "http://127.0.0.1:3140", "Content-Type": "application/json"},
                     b"{}")
    assert status == 403
    status, _ = _get(port, "/api/get_s1", "GET", {"Sec-Fetch-Site": "cross-site"})
    assert status == 403


def test_the_plan_hands_the_app_the_dashboard_port(monkeypatch, tmp_path):
    from puffpai.assemble.server import host
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PUFFPAI_DASHBOARD_PORT", "3140")
    monkeypatch.setattr(host, "load_spec", lambda app_id: {"sources": []})
    args = host.make_plan("earth_pictures", 45678)["start"]["args"]
    assert args[args.index("--dashboard-port") + 1] == "3140"
