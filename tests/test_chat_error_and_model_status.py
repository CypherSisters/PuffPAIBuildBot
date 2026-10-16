"""BB-SYS-01 — an error is never an answer, and 'up' is never 'ready'.

The transcript, verbatim: with the configured model never pulled,

    curl -X POST .../chat/send -d '{"message":"hello"}'
    -> {"response":"Error: Ollama returned status 404","success":true,...}

and, on the same box at the same moment,

    /system/status -> "inference":{"status":"connected"}

Both statements were false, and both were false in the same direction: a build
with no brain reported itself working. These tests hold that shut.

Run:
    pytest tests/test_chat_error_and_model_status.py
"""

import os
import tempfile

_TMP_DATA_DIR = tempfile.mkdtemp(prefix="puffpai_bbsys01_")
os.environ.setdefault("PUFFPAI_DATA_DIR", _TMP_DATA_DIR)

import pytest  # noqa: E402

from puffpai import api_server, brain_health  # noqa: E402

API_KEY = api_server._API_KEY


def _auth():
    return {"Authorization": f"Bearer {API_KEY}"}


@pytest.fixture
def client():
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client()


class _Resp:
    """The 404 Ollama returns for a model it does not have."""

    status_code = 404

    def json(self):
        return {"error": "model not found"}


# ── /chat/send ──────────────────────────────────────────────────────────────

def test_a_404_from_ollama_is_not_a_successful_chat_reply(client, monkeypatch):
    """The exact validator repro: "hello" against an uninstalled model."""
    monkeypatch.setattr(api_server, "_ollama_chat",
                        lambda *a, **k: _Resp())
    monkeypatch.setattr(api_server, "_should_skip_rag", lambda msg: True)
    monkeypatch.setattr(api_server, "_save_conversation", lambda conv: None)

    resp = client.post("/chat/send", json={"message": "hello"}, headers=_auth())

    body = resp.get_json()
    assert resp.status_code == 503
    assert body["success"] is False
    assert body["error"]["code"] == "model_not_installed"
    # The human message names the model and carries the command that fixes it.
    assert "ollama pull" in body["message"]
    assert api_server.OLLAMA_MODEL in body["message"]
    # And the old string is gone from the reply position entirely.
    assert "response" not in body


def test_the_failed_turn_is_not_written_into_the_conversation(client, monkeypatch):
    """An error the assistant never said must not appear in its history."""
    saved = []
    monkeypatch.setattr(api_server, "_ollama_chat", lambda *a, **k: _Resp())
    monkeypatch.setattr(api_server, "_should_skip_rag", lambda msg: True)
    monkeypatch.setattr(api_server, "_save_conversation", lambda conv: saved.append(conv))

    client.post("/chat/send", json={"message": "hello"}, headers=_auth())
    assert saved == []


def test_a_real_reply_still_succeeds(client, monkeypatch):
    """The honesty guard must not have broken the working path."""
    monkeypatch.setattr(
        api_server, "_process_chat_query",
        lambda message, history=None: {
            "response": "hi there", "sources": [], "suggestions": []})
    monkeypatch.setattr(api_server, "_save_conversation", lambda conv: None)

    resp = client.post("/chat/send", json={"message": "hello"}, headers=_auth())
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["success"] is True
    assert body["response"] == "hi there"


# ── /system/status ──────────────────────────────────────────────────────────

def test_status_reports_model_missing_when_ollama_is_up_without_the_model(
        client, monkeypatch):
    """Ollama up + model absent is `model_missing`, never `connected`."""
    monkeypatch.setattr(brain_health, "list_models",
                        lambda url, timeout=3: ["some-other-model:8b"])

    resp = client.get("/system/status", headers=_auth())
    inference = resp.get_json()["inference"]

    assert inference["status"] == "model_missing"
    assert inference["model"] == api_server.OLLAMA_MODEL
    assert inference["pull_command"].startswith("ollama pull ")
    assert api_server.OLLAMA_MODEL in inference["message"]


def test_status_reports_unreachable_when_nothing_answers(client, monkeypatch):
    monkeypatch.setattr(brain_health, "list_models", lambda url, timeout=3: None)

    resp = client.get("/system/status", headers=_auth())
    inference = resp.get_json()["inference"]

    assert inference["status"] == "unreachable"
    assert "puffpai setup --ollama-url" in inference["message"]


def test_status_reports_connected_only_when_the_model_is_there(client, monkeypatch):
    monkeypatch.setattr(brain_health, "list_models",
                        lambda url, timeout=3: [api_server.OLLAMA_MODEL])

    resp = client.get("/system/status", headers=_auth())
    inference = resp.get_json()["inference"]

    assert inference["status"] == "connected"
    assert inference["message"] == ""


# ── the classifier underneath ───────────────────────────────────────────────

def test_connection_failure_and_model_absence_are_different_facts():
    assert brain_health.probe("http://nowhere.invalid:1", "x",
                              installed=None)["status"] == "unreachable"
    assert brain_health.probe("http://x", "qwen2.5:7b-instruct",
                              installed=[])["status"] == "model_missing"
    assert brain_health.probe("http://x", "qwen2.5:7b-instruct",
                              installed=["qwen2.5:7b-instruct"])["status"] == "connected"


def test_an_implicit_latest_tag_still_counts_as_installed():
    """`llama3.2:1b` in config vs `llama3.2:1b:latest` in tags is one model."""
    assert brain_health.is_installed("llama3.2:1b", ["llama3.2:1b:latest"])
    assert brain_health.is_installed("llama3.2:1b:latest", ["llama3.2:1b"])
    assert not brain_health.is_installed("llama3.2:1b", ["llama3.2:3b"])
