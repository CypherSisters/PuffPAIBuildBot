"""Tests for the first-run setup operations and the /setup/* REST endpoints.

Covers puffpai.setup_ops (config writes, vault creation, HA test-connection,
status transitions) against an isolated scratch data dir, and the API surface
that backs the graphical onboarding wizard (auth, vault creation, completion
flag, and the guarantee that passwords/tokens never appear in responses).

Run:
    pytest tests/test_setup.py
"""
from __future__ import annotations

import os

import pytest
import requests

from puffpai import setup_ops
from puffpai.config import get_config
from puffpai.security.vault import Vault


@pytest.fixture
def scratch(tmp_path):
    """Point the config singleton at an isolated data dir for one test.

    Restores the previous PUFFPAI_DATA_DIR and rebuilds the singleton on
    teardown so other test modules (which resolve paths from the same singleton)
    are never left pointing at a deleted temp dir.
    """
    prev = os.environ.get("PUFFPAI_DATA_DIR")
    os.environ["PUFFPAI_DATA_DIR"] = str(tmp_path)
    get_config(_force_reload=True)
    try:
        yield tmp_path
    finally:
        if prev is not None:
            os.environ["PUFFPAI_DATA_DIR"] = prev
        else:
            os.environ.pop("PUFFPAI_DATA_DIR", None)
        get_config(_force_reload=True)


# ── setup_ops: config writing ────────────────────────────────────────────────

def test_write_main_config_merges_and_reloads(scratch):
    setup_ops.set_ollama_config("llama3.1:8b", "http://localhost:11434")
    cfg = get_config()
    assert cfg.ollama.model == "llama3.1:8b"
    # A second section write must not clobber the first.
    setup_ops.set_knowledge_config("/packs", auto_index=False)
    cfg = get_config()
    assert cfg.ollama.model == "llama3.1:8b"
    assert cfg.knowledge.zim_dir == "/packs"
    assert cfg.knowledge.auto_index is False


def test_set_knowledge_creates_dir_when_requested(scratch):
    target = str(scratch / "packs")
    setup_ops.set_knowledge_config(target, auto_index=True, create_dir=True)
    assert os.path.isdir(target)
    assert get_config().knowledge.zim_dir == target


# ── setup_ops: vault ─────────────────────────────────────────────────────────

def test_create_vault_makes_unlockable_vault(scratch):
    assert setup_ops.vault_initialized() is False
    setup_ops.create_vault("correct horse battery staple")
    assert setup_ops.vault_initialized() is True

    # A fresh handle on the same dir can unlock with the password and is locked
    # (create_vault does not leave a dangling unlocked session).
    vault = Vault(vault_dir=setup_ops._vault_dir())
    assert vault.is_unlocked is False
    vault.unlock("correct horse battery staple")
    assert vault.is_unlocked is True


def test_create_vault_refuses_second_create(scratch):
    from puffpai.security.vault import VaultError
    setup_ops.create_vault("first-password-here")
    with pytest.raises(VaultError):
        setup_ops.create_vault("second-password-here")


# ── setup_ops: HA test-connection (mocked requests) ──────────────────────────

def test_test_ha_connection_unreachable(monkeypatch, scratch):
    def _boom(*a, **k):
        raise requests.exceptions.ConnectionError("no route")
    monkeypatch.setattr(setup_ops.requests, "get", _boom)
    result = setup_ops.test_ha_connection("http://homeassistant.local:8123", "tok")
    assert result["ok"] is False
    assert "error" in result


def test_test_ha_connection_success(monkeypatch, scratch):
    class _Resp:
        status_code = 200
        ok = True

        def json(self):
            return {"version": "2024.12.0", "location_name": "Home"}

    monkeypatch.setattr(setup_ops.requests, "get", lambda *a, **k: _Resp())
    result = setup_ops.test_ha_connection("http://homeassistant.local:8123", "tok")
    assert result["ok"] is True
    assert result["version"] == "2024.12.0"
    # The token must never be reflected back.
    assert "tok" not in str(result)


def test_test_ha_connection_requires_both(scratch):
    assert setup_ops.test_ha_connection("", "tok")["ok"] is False
    assert setup_ops.test_ha_connection("http://x", "")["ok"] is False


# ── setup_ops: status transitions + completion flag ──────────────────────────

def test_setup_status_transitions(scratch):
    status = setup_ops.setup_status(probe_ollama=False)
    assert status["complete"] is False
    assert status["vault"]["initialized"] is False
    assert status["ha"]["configured"] is False

    setup_ops.create_vault("a-strong-passphrase")
    setup_ops.set_knowledge_config("/data/packs", auto_index=True)
    status = setup_ops.setup_status(probe_ollama=False)
    assert status["vault"]["initialized"] is True
    assert status["knowledge"]["zim_dir"] == "/data/packs"
    assert status["complete"] is False

    setup_ops.mark_complete()
    assert setup_ops.is_complete() is True
    assert setup_ops.setup_status(probe_ollama=False)["complete"] is True

    setup_ops.clear_complete()
    assert setup_ops.is_complete() is False


def test_status_carries_no_secrets(monkeypatch, scratch):
    # A literal private address, not a .local name: set_ha_config is now
    # posture-checked and the check RESOLVES, so on a network whose resolver
    # answers .local names with a public address (wildcard DNS, or an IPv6-native
    # home handing back a global address) the write is correctly refused. This
    # test is about the token never being echoed, so it uses an address whose
    # classification cannot depend on the machine the suite runs on.
    # posture="island" is stated because the scratch install has no build
    # manifest, and "nothing known" now means `sealed` on the write path -- which
    # would refuse a LAN address. A caller that knows its posture says so; that
    # is the same pass-through the wizard and the onboarding route use.
    setup_ops.set_ha_config("http://192.168.1.50:8123", "super-secret-token",
                            posture="island")
    status = setup_ops.setup_status(probe_ollama=False)
    assert status["ha"]["configured"] is True
    assert "super-secret-token" not in str(status)


# ── API endpoints ────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from puffpai import api_server
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client()


def _auth():
    from puffpai import api_server
    return {"Authorization": f"Bearer {api_server._API_KEY}"}


def test_setup_status_requires_key(client):
    assert client.get("/setup/status?ollama=0").status_code == 401


def test_setup_status_ok_with_key(client, scratch):
    resp = client.get("/setup/status?ollama=0", headers=_auth())
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body) >= {"ollama", "vault", "ha", "knowledge", "complete"}


def test_setup_vault_creates_vault_and_hides_password(client, scratch, monkeypatch):
    from puffpai import api_server
    monkeypatch.setattr(api_server, "_vault_instance", None)

    resp = client.post("/setup/vault", json={"password": "onboarding-secret-99"},
                       headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json()["initialized"] is True
    assert "onboarding-secret-99" not in resp.get_data(as_text=True)
    assert setup_ops.vault_initialized() is True

    # Creating again is refused (409), not a silent overwrite.
    resp2 = client.post("/setup/vault", json={"password": "another-secret"},
                        headers=_auth())
    assert resp2.status_code == 409


def test_setup_vault_rejects_empty_password(client, scratch, monkeypatch):
    from puffpai import api_server
    monkeypatch.setattr(api_server, "_vault_instance", None)
    resp = client.post("/setup/vault", json={"password": ""}, headers=_auth())
    assert resp.status_code == 400


def test_setup_ha_hides_token(client, scratch):
    resp = client.post("/setup/ha",
                       # Literal private address: see the note in
                       # test_status_carries_no_secrets. This test is about the
                       # token not being echoed, not about name resolution.
                       json={"url": "http://192.168.1.50:8123",
                             "token": "llat-should-not-echo"},
                       headers=_auth())
    assert resp.status_code == 200
    assert "llat-should-not-echo" not in resp.get_data(as_text=True)
    assert setup_ops.ha_configured() is True


def test_setup_complete_flips_flag(client, scratch):
    assert client.get("/setup/status?ollama=0",
                      headers=_auth()).get_json()["complete"] is False
    resp = client.post("/setup/complete", headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json()["complete"] is True
    assert client.get("/setup/status?ollama=0",
                      headers=_auth()).get_json()["complete"] is True


def test_setup_knowledge_updates_status(client, scratch):
    resp = client.post("/setup/knowledge",
                       json={"zim_dir": "/srv/packs", "auto_index": False},
                       headers=_auth())
    assert resp.status_code == 200
    status = client.get("/setup/status?ollama=0", headers=_auth()).get_json()
    assert status["knowledge"]["zim_dir"] == "/srv/packs"
    assert status["knowledge"]["auto_index"] is False
