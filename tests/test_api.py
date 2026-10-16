"""Tests for the REST API server (puffpai.api_server).

Runs entirely against the Flask test client with no live services: the
inference backend, RAG, and vault are either injected as isolated instances or
monkeypatched. On-disk state (vault, consent ledger, activity log) is redirected
to a temp directory via PUFFPAI_DATA_DIR before the app is imported.
"""

import os
import tempfile

# Isolate all on-disk state BEFORE importing the app (module-level singletons
# resolve their paths at import time).
_TMP_DATA_DIR = tempfile.mkdtemp(prefix="puffpai_api_test_")
os.environ["PUFFPAI_DATA_DIR"] = _TMP_DATA_DIR

import pytest  # noqa: E402

from puffpai import api_server  # noqa: E402
from puffpai.security.vault import Vault  # noqa: E402

API_KEY = api_server._API_KEY


def _auth(extra=None):
    headers = {"Authorization": f"Bearer {API_KEY}"}
    if extra:
        headers.update(extra)
    return headers


@pytest.fixture
def client():
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client()


# ── Auth ────────────────────────────────────────────────────────────────────

def test_requires_api_key_on_protected_endpoint(client):
    """Non-public endpoints reject requests with no API key."""
    resp = client.get("/system/status")
    assert resp.status_code == 401


def test_health_is_public_and_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


# ── Ingest + app-data routes are NOT public (require the API key) ────────────

@pytest.mark.parametrize("method,path,kwargs", [
    ("post", "/ingest/upload", {}),
    ("get", "/ingest/search?q=x", {}),
    ("delete", "/ingest/document/nope", {}),
    ("post", "/ingest/reindex", {}),
    ("get", "/ingest/stats", {}),
    ("get", "/ingest/list", {}),
    ("put", "/apps/calendar/data", {"json": {"x": 1}}),
    ("get", "/apps/calendar/data", {}),
])
def test_ingest_and_apps_require_api_key(client, method, path, kwargs):
    """These used to be reachable with no key — they must 401 without it."""
    resp = getattr(client, method)(path, **kwargs)  # no auth header
    assert resp.status_code == 401


def test_ingest_and_apps_reachable_with_key(client, monkeypatch):
    monkeypatch.setattr("puffpai.ingestion.search_indexed_docs", lambda q: [])
    monkeypatch.setattr("puffpai.ingestion.delete_ingested_doc",
                        lambda doc_id: False)

    # Search: auth passes → 200 with (empty) results.
    resp = client.get("/ingest/search?q=test", headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json()["results"] == []

    # Delete unknown: auth passes → 200, success False.
    resp = client.delete("/ingest/document/does-not-exist", headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json()["success"] is False

    # Upload with key but no file: auth passes → 400 (a validation error, not 401).
    resp = client.post("/ingest/upload", headers=_auth())
    assert resp.status_code == 400

    # App-data PUT with key: auth passes → 200.
    resp = client.put("/apps/calendar/data", json={"events": []},
                      headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True


# ── Excluded (network-edge) route groups are gone ───────────────────────────

@pytest.mark.parametrize("path", [
    "/firewall/status",
    "/firewall/rules",
    "/zone3/policy",
    "/zone3/status",
    "/network/scan",
    "/network/connections",
    "/sentinel/scan",
    "/sentinel/alerts",
])
def test_excluded_routes_are_404(client, path):
    """Firewall / zone3 / network / device-sentinel routes were dropped."""
    resp = client.get(path, headers=_auth())
    assert resp.status_code == 404


# ── Vault: lock / unlock / store / retrieve ─────────────────────────────────

def test_vault_flow(client, monkeypatch, tmp_path):
    vault = Vault(vault_dir=str(tmp_path / "vault"))
    monkeypatch.setattr(api_server, "_vault_instance", vault)
    api_server._vault_sessions.clear()

    # Storing before unlock is refused.
    resp = client.post("/vault/store", json={"key": "k", "value": "v"},
                       headers=_auth())
    assert resp.status_code == 403

    # First-time unlock initializes the vault and returns a session token.
    resp = client.post("/vault/unlock",
                       # The Vault page's create route holds to the PIN
                       # rules (no spaces).
                       json={"password": "Tulip-9-Harbor", "initialize": True},
                       headers=_auth())
    assert resp.status_code == 200
    body = resp.get_json()
    token = body["session_token"]
    vault_headers = _auth({"X-Vault-Token": token})

    # Initialization also issues a recovery code, returned exactly once. A vault
    # created without one has no second way in, so this is part of the
    # first-time contract rather than an optional follow-up step. The previously
    # asserted fields are unchanged; these are additions.
    assert body["message"] == "Vault unlocked"
    assert body["mfa_enabled"] is False
    assert body["recovery_issued"] is True
    assert body["recovery_code"]
    assert "write this code down" in body["recovery_code_notice"].lower()

    # Store an item.
    resp = client.post("/vault/store",
                       json={"key": "secret1", "value": "hunter2",
                             "type": "password"},
                       headers=vault_headers)
    assert resp.status_code == 200

    # It shows up in contents.
    resp = client.get("/vault/contents", headers=vault_headers)
    assert resp.status_code == 200
    keys = [item["key"] for item in resp.get_json()["items"]]
    assert "secret1" in keys

    # And round-trips on read.
    resp = client.get("/vault/item/secret1", headers=vault_headers)
    assert resp.status_code == 200
    assert resp.get_json()["content"] == "hunter2"

    # Lock invalidates the session.
    resp = client.post("/vault/lock", headers=_auth())
    assert resp.status_code == 200
    resp = client.get("/vault/contents", headers=vault_headers)
    assert resp.status_code == 403


# ── Knowledge search on an empty / unavailable index ────────────────────────

def test_knowledge_search_empty_is_clean(client, monkeypatch):
    monkeypatch.setattr(api_server, "_get_rag_context", lambda *a, **k: None)
    resp = client.get("/knowledge/search?q=anything", headers=_auth())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["results"] == []


# ── Agents ──────────────────────────────────────────────────────────────────

def test_agents_status(client):
    resp = client.get("/agents/status", headers=_auth())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert "team" in body
    assert "bus" in body


# ── Chat send with a mocked inference backend ───────────────────────────────

def test_chat_send_with_mocked_backend(client, monkeypatch):
    monkeypatch.setattr(
        api_server, "_process_chat_query",
        lambda message, history=None: {
            "response": "hi there", "sources": [], "suggestions": []})
    # Don't touch disk for chat history in this test.
    monkeypatch.setattr(api_server, "_save_conversation", lambda conv: None)

    resp = client.post("/chat/send", json={"message": "hello"}, headers=_auth())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["response"] == "hi there"
    assert body["conversation_id"]


# ── Consent pending / approve flow ──────────────────────────────────────────

def test_consent_pending_and_approve(client):
    from puffpai.security import consent_requests as creq
    from puffpai.security import consent_gate

    entity = "light.consent_test_room"
    creq.create_request(entity, "Consent Test Light", "test_action",
                        "unit test request")

    # It appears in the open list.
    resp = client.get("/sentinel/consent/requests", headers=_auth())
    assert resp.status_code == 200
    open_ids = [r["entity_id"] for r in resp.get_json()["requests"]]
    assert entity in open_ids

    # Approving it succeeds and records the verdict in the consent ledger.
    resp = client.post(f"/sentinel/consent/requests/{entity}",
                       json={"approve": True}, headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    assert consent_gate.get_ledger().status(entity) == "approved"


# ── Home Assistant: not-connected endpoints return a clean envelope ─────────

@pytest.mark.parametrize("path,method_name", [
    ("/ha/entities", "get_all_states"),
    ("/ha/scenes", "get_scenes"),
    ("/ha/automations", "get_automations"),
    ("/ha/devices/overview", "get_all_states"),
])
def test_ha_endpoints_not_connected_return_clean_503(client, monkeypatch,
                                                     path, method_name):
    """With HA unreachable (the default for a fresh install), HA routes must
    return a clean {success:false, connected:false} envelope with 503 — NOT a
    500 NameError from a missing error helper."""
    from puffpai import ha_bridge as _hab

    bridge = api_server._get_ha_bridge()

    def _boom(*a, **k):
        raise _hab.HABridgeError("Cannot connect to Home Assistant")

    monkeypatch.setattr(bridge, method_name, _boom)
    resp = client.get(path, headers=_auth())
    assert resp.status_code == 503, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["success"] is False
    assert body["connected"] is False
    assert "message" in body


# ── Profile: AI personality settings are readable (GET) ─────────────────────

def test_profile_ai_get_returns_settings(client):
    """GET /profile/ai returns the current AI identity/personality config.

    Previously only PUT was defined, so the Profile page saw 405 Method Not
    Allowed."""
    resp = client.get("/profile/ai", headers=_auth())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert isinstance(body.get("settings"), dict)
    # Defaults are surfaced on a fresh install.
    assert "ai_name" in body["settings"]


# ── Fitness: workout program falls back to the packaged default library ─────

def test_workout_library_packaged_default_loads():
    """The generic default library ships as package data and loads even with no
    data-dir override present."""
    lib = api_server._load_workout_library()
    programs = lib.get("programs") or {}
    assert "beginner_general" in programs
    assert programs["beginner_general"]["sessions"]


def test_fitness_program_returns_200_with_default(client, monkeypatch):
    """GET /fitness/program returns 200 with the packaged default program — not
    a 500 from a missing workout_library.json."""
    monkeypatch.setattr(
        api_server, "_load_json_file",
        lambda path, default=None: (
            {"profiles": [{"id": "u1", "display_name": "Alex",
                           "preferences": {"workout_level": "beginner"}}],
             "last_active_profile": "u1"}
            if "profile" in str(path).lower() else (default if default is not None else {})
        ),
    )
    resp = client.get("/fitness/program", headers=_auth())
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["program"]
    assert body["session"]
    assert body["session"]["exercises"]


def test_apikey_cli_prints_active_key(tmp_path):
    """`puffpai apikey` exits 0 and prints the 64-hex active app API key."""
    import subprocess as _sp, sys as _sys, re as _re, puffpai as _p
    src_dir = os.path.dirname(os.path.dirname(os.path.abspath(_p.__file__)))
    env = {**os.environ, "PUFFPAI_DATA_DIR": str(tmp_path),
           "PYTHONPATH": src_dir + os.pathsep + os.environ.get("PYTHONPATH", "")}
    r = _sp.run([_sys.executable, "-m", "puffpai", "apikey"],
                capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert _re.fullmatch(r"[0-9a-f]{64}", r.stdout.strip()), r.stdout
