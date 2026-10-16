"""Release gate: CORS answers the configured dashboard origin, nothing else.

The backend used to echo ``Access-Control-Allow-Origin`` for ANY
``http://localhost:<port>`` or ``http://127.0.0.1:<port>`` origin. "localhost"
is not a trust boundary — every dev server, Electron app and stray page a
browser has open shares it — so that made every one of them a permitted
cross-origin caller the moment it learned the key.

Nothing legitimate needed it: the dashboard's browser code calls its own
same-origin ``/api/pai/[...path]`` route, which injects the key server-side.

Run:
    pytest tests/test_release_cors.py
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from puffpai import api_server

ALLOWED = ["http://localhost:3003", "http://127.0.0.1:3003"]
# Ports a dev machine really does have open. None of them is the dashboard.
REFUSED = [
    "http://localhost:3000", "http://127.0.0.1:5173", "http://localhost:1337",
    "http://localhost:9999", "http://evil.example.com", "https://evil.test",
    "http://localhost.evil.test", "null", "http://[::1]:3003",
]


@pytest.fixture
def client():
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client()


@pytest.mark.parametrize("origin", ALLOWED)
def test_the_dashboard_origin_is_answered(client, origin):
    resp = client.get("/health", headers={"Origin": origin})
    assert resp.headers.get("Access-Control-Allow-Origin") == origin


@pytest.mark.parametrize("origin", REFUSED)
def test_every_other_origin_gets_no_cors_header(client, origin):
    resp = client.get("/health", headers={"Origin": origin})
    assert "Access-Control-Allow-Origin" not in resp.headers, (
        f"{origin} was granted cross-origin access")


def test_preflight_from_the_dashboard_still_works(client):
    resp = client.open(
        "/health", method="OPTIONS",
        headers={"Origin": "http://localhost:3003",
                 "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "Authorization"})
    assert resp.headers.get("Access-Control-Allow-Origin") == "http://localhost:3003"
    assert "POST" in resp.headers.get("Access-Control-Allow-Methods", "")
    assert "Authorization" in resp.headers.get("Access-Control-Allow-Headers", "")


def test_preflight_from_anywhere_else_is_given_nothing(client):
    resp = client.open(
        "/health", method="OPTIONS",
        headers={"Origin": "http://localhost:4444",
                 "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "Authorization"})
    assert "Access-Control-Allow-Origin" not in resp.headers
    assert "Access-Control-Allow-Methods" not in resp.headers


def test_the_answer_varies_on_origin(client):
    resp = client.get("/health", headers={"Origin": "http://localhost:3003"})
    assert "origin" in resp.headers.get("Vary", "").lower()


def test_no_origin_header_means_no_cors_header(client):
    resp = client.get("/health")
    assert "Access-Control-Allow-Origin" not in resp.headers


# ── the override, and its limits ────────────────────────────────────────────

def test_an_owner_can_add_an_origin_by_configuration(monkeypatch, client):
    monkeypatch.setenv("PUFFPAI_ALLOWED_ORIGINS", "http://kiosk.lan:3003")
    resp = client.get("/health", headers={"Origin": "http://kiosk.lan:3003"})
    assert resp.headers.get("Access-Control-Allow-Origin") == "http://kiosk.lan:3003"


def test_a_literal_star_is_never_honoured(monkeypatch, client):
    monkeypatch.setenv("PUFFPAI_ALLOWED_ORIGINS", "*")
    assert "*" not in api_server._allowed_origins()
    resp = client.get("/health", headers={"Origin": "http://localhost:4444"})
    assert "Access-Control-Allow-Origin" not in resp.headers


def test_the_allowlist_holds_no_wildcard_and_no_bare_scheme():
    for origin in api_server._allowed_origins():
        assert "*" not in origin
        assert origin.count(":") >= 2, f"{origin} has no explicit port"


# ── regression guards on the source itself ──────────────────────────────────

def test_no_port_wildcarding_survives_in_the_cors_hook():
    source = inspect.getsource(api_server.add_cors)
    assert "startswith" not in source, (
        "prefix matching on the Origin header is how the wildcard-port hole "
        "was written the first time")


def test_no_endpoint_ships_a_wildcard_allow_origin():
    text = Path(api_server.__file__).read_text(encoding="utf-8")
    assert "'Access-Control-Allow-Origin'] = '*'" not in text
    assert '"Access-Control-Allow-Origin"] = "*"' not in text
