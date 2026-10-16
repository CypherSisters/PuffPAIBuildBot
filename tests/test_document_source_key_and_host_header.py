"""Her uploaded documents need the key, and a foreign Host is refused.

Without these checks, ``/knowledge/library/<name>/source`` is public, so
anything that reaches the backend can read her uploaded originals with no
key; and with no route looking at the Host header, CORS is the only thing in
front of the loopback backend (DNS rebinding). Both are closed here.
"""
from __future__ import annotations

import json
import os

import pytest

from puffpai import api_server

API_KEY = api_server._API_KEY


def _auth(extra=None):
    headers = {"Authorization": f"Bearer {API_KEY}"}
    headers.update(extra or {})
    return headers


@pytest.fixture
def client():
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client()


@pytest.fixture
def uploaded(monkeypatch, tmp_path):
    """One uploaded document whose original is kept on disk."""
    sources = tmp_path / "library_sources"
    sources.mkdir()
    original = sources / "letter.txt"
    original.write_text("Dear Maya, the venue deposit is due Friday.", encoding="utf-8")
    entry = {"name": "letter", "collection": "letter_c",
             "source_file": str(original), "source_mime": "text/plain"}
    monkeypatch.setattr(api_server, "_LIBRARY_SOURCES_DIR", str(sources))
    monkeypatch.setattr(api_server, "_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(api_server, "_find_manifest_entry",
                        lambda name: entry if name == "letter" else None)
    return entry


# -- the source route --------------------------------------------------------

def test_the_source_route_is_not_public_any_more():
    assert "knowledge_serve_source" not in api_server._PUBLIC_ENDPOINTS


def test_a_keyless_read_of_an_uploaded_document_is_refused(client, uploaded):
    for path in ("/knowledge/library/letter/source",
                 "/knowledge/library/letter/source?format=text"):
        got = client.get(path)
        assert got.status_code == 401, path
        assert b"venue deposit" not in got.data


def test_with_the_key_the_document_is_still_served(client, uploaded):
    got = client.get("/knowledge/library/letter/source", headers=_auth())
    assert got.status_code == 200
    assert b"venue deposit" in got.data


def test_the_dashboard_proxy_sends_the_key_for_the_embed():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    route = open(os.path.join(root, "dashboard", "app", "api", "knowledge",
                              "source", "route.ts"), encoding="utf-8").read()
    assert 'process.env.PUFFPAI_API_KEY' in route
    assert "Authorization: `Bearer ${API_KEY}`" in route
    assert "NEXT_PUBLIC" not in route.replace("never NEXT_PUBLIC_", "")
    assert "guardCredentialedRoute(req)" in route


# -- the Host header ----------------------------------------------------------

@pytest.mark.parametrize("host", ["localhost", "localhost:8000", "127.0.0.1:8000",
                                  "[::1]:8000", "LOCALHOST:8000", "localhost.:8000"])
def test_this_computers_own_names_are_answered(client, host):
    assert client.get("/health", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("host", [
    "evil.example", "evil.example:8000", "127.0.0.1.nip.io:8000",
    "localhost.evil.example", "192.168.1.20:8000", "[::2]:8000",
    "", "local host", "localhost:8000/x",
])
def test_a_foreign_host_is_refused_before_any_route(client, host):
    got = client.get("/health", headers={"Host": host})
    assert got.status_code == 421
    assert got.get_json()["code"] == "wrong_host"
    # Public routes and keyed routes alike, and with the key as well.
    assert client.get("/v1/models", headers={"Host": host}).status_code == 421
    assert client.get("/system/status",
                      headers=_auth({"Host": host})).status_code == 421


def test_two_names_in_one_host_value_are_refused():
    # Two Host headers reach a WSGI app joined by a comma.
    for value in ("localhost:8000, evil.example", "localhost,localhost"):
        assert api_server._plain_host_name(value) is None


def test_a_foreign_host_cannot_read_a_document_even_with_the_key(client, uploaded):
    got = client.get("/knowledge/library/letter/source",
                     headers=_auth({"Host": "rebind.evil.example:8000"}))
    assert got.status_code == 421 and b"venue deposit" not in got.data


def test_a_preflight_from_a_foreign_host_is_refused_too(client):
    got = client.open("/chat/send", method="OPTIONS",
                      headers={"Host": "evil.example:8000",
                               "Origin": "http://evil.example:8000",
                               "Access-Control-Request-Method": "POST"})
    assert got.status_code == 421


def test_names_set_for_the_home_network_are_answered(client, monkeypatch):
    monkeypatch.setenv("PUFFPAI_LAN_NAMES", "puffbox.lan, 192.168.1.20 ,bad name,0.0.0.0")
    names = api_server.allowed_host_names()
    assert {"puffbox.lan", "192.168.1.20"} <= names
    assert "bad name" not in names and "0.0.0.0" not in names
    assert client.get("/health", headers={"Host": "puffbox.lan:8000"}).status_code == 200
    assert client.get("/health", headers={"Host": "192.168.1.20:8000"}).status_code == 200
    assert client.get("/health", headers={"Host": "puffbox.lan.evil.example"}
                      ).status_code == 421


@pytest.mark.parametrize("bind, added", [("192.168.1.20", True), ("0.0.0.0", False),
                                         ("::", False), ("127.0.0.1", True)])
def test_the_address_it_listens_on_is_answered_unless_it_is_everywhere(
        monkeypatch, bind, added):
    cfg = api_server._get_puffpai_config()
    monkeypatch.setattr(cfg.api_server, "host", bind)
    names = api_server.allowed_host_names()
    assert (bind in names) is added or bind == "127.0.0.1"
    assert "0.0.0.0" not in names and "::" not in names


def test_the_refusal_is_plain_words():
    from puffpai import plain_words
    assert not plain_words.check(api_server.WRONG_HOST_LINE)
    assert "door" not in api_server.WRONG_HOST_LINE.lower()
