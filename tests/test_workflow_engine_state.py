"""Tests for /n8n/status - the three states the Workflows page must tell apart.

A build ships with no workflow engine (``n8n.enabled`` is False by default), so
"not set up" is the ordinary case. It has to read differently from "set up but
not answering", and neither may be confused with "we could not tell". The
dashboard page renders straight off these three facts, so they are pinned here.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from types import SimpleNamespace

import pytest

from puffpai import api_server
from puffpai.config import N8nConfig, N8nWebhooks
from puffpai.n8n_client import N8nClient


@pytest.fixture
def client():
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client()


def _auth():
    return {"Authorization": f"Bearer {api_server._API_KEY}"}


@pytest.fixture
def engine(monkeypatch):
    """Drive the two independent facts: is it configured, is it answering."""
    def _set(*, enabled: bool, reachable: bool):
        cfg = SimpleNamespace(n8n=N8nConfig(enabled=enabled,
                                            webhooks=N8nWebhooks()))
        monkeypatch.setattr(api_server, "_get_puffpai_config", lambda: cfg)
        monkeypatch.setattr(N8nClient, "is_reachable", lambda self: reachable)
    return _set


def _status(client):
    resp = client.get("/n8n/status", headers=_auth())
    assert resp.status_code == 200
    return resp.get_json()


# ── The endpoint answers at all ─────────────────────────────────────────────

def test_status_returns_a_status(client, engine):
    """Regression: this endpoint used to raise on every single call.

    It asked N8nClient for 'block_device' and 'network_scan', which are not
    fields on N8nWebhooks, so webhook_url() raised ValueError and the route
    always fell into its except branch. The page therefore never saw a real
    status - it only ever saw ``success: false``.
    """
    engine(enabled=False, reachable=False)
    body = _status(client)
    assert body["success"] is True
    assert "error" not in body


def test_status_requires_the_api_key(client):
    assert client.get("/n8n/status").status_code == 401


# ── State 1: no engine configured ───────────────────────────────────────────

def test_no_engine_configured(client, engine):
    engine(enabled=False, reachable=False)
    body = _status(client)
    assert body["configured"] is False
    assert body["connected"] is False
    assert body["engine"] == "n8n"


def test_unconfigured_engine_is_not_probed(client, engine, monkeypatch):
    """No engine means no network call - a 3s timeout per page load with no
    information in it is a cost, not a check."""
    probed = []

    cfg = SimpleNamespace(n8n=N8nConfig(enabled=False, webhooks=N8nWebhooks()))
    monkeypatch.setattr(api_server, "_get_puffpai_config", lambda: cfg)
    monkeypatch.setattr(N8nClient, "is_reachable",
                        lambda self: probed.append(1) or True)

    assert _status(client)["connected"] is False
    assert probed == []


# ── State 2: configured but unreachable ─────────────────────────────────────

def test_configured_but_unreachable(client, engine):
    engine(enabled=True, reachable=False)
    body = _status(client)
    assert body["configured"] is True, "the owner did set an engine up"
    assert body["connected"] is False, "but it is not answering"


def test_unreachable_is_distinguishable_from_absent(client, engine):
    """Both are 'connected: false'. Only `configured` tells them apart, which
    is the whole reason the page can show two different messages."""
    engine(enabled=False, reachable=False)
    absent = _status(client)
    engine(enabled=True, reachable=False)
    down = _status(client)

    assert absent["connected"] == down["connected"] is False
    assert absent["configured"] != down["configured"]


# ── State 3: connected ──────────────────────────────────────────────────────

def test_configured_and_reachable(client, engine):
    engine(enabled=True, reachable=True)
    body = _status(client)
    assert body["configured"] is True
    assert body["connected"] is True


def test_reachable_engine_is_still_not_connected_when_unconfigured(client, engine):
    """Something answering on the default port is not consent to use it."""
    engine(enabled=False, reachable=True)
    assert _status(client)["connected"] is False


# ── What is actually offered ────────────────────────────────────────────────

def test_every_reported_webhook_resolves(client, engine):
    """The page renders these URLs, so none of them may be a name that raises."""
    engine(enabled=True, reachable=True)
    body = _status(client)
    n8n_client = N8nClient()
    for name, url in body["webhooks"].items():
        assert n8n_client.webhook_url(name) == url


def test_removed_phantom_webhooks_are_gone(client, engine):
    """'block_device' and 'network_scan' were listed on the page and offered a
    Run button, but no such webhook exists anywhere in the config."""
    engine(enabled=True, reachable=True)
    webhooks = _status(client)["webhooks"]
    assert "block_device" not in webhooks
    assert "network_scan" not in webhooks


def test_templates_are_the_ones_this_build_ships(client, engine):
    engine(enabled=True, reachable=True)
    body = _status(client)
    templates = body["templates"]
    assert templates, "the repo ships n8n_workflows/*.json"
    # Every offered template has a webhook path to listen on. A template with
    # nowhere to listen is half a workflow and must not be offered as a whole.
    for name in templates:
        assert name in body["webhooks"], f"{name} has no webhook path"


def test_a_webhook_path_alone_is_not_a_workflow(client, engine):
    """weather_alerts and puffpai_voice have paths but no shipped template, so
    they are not in `templates` and the page does not offer them."""
    engine(enabled=True, reachable=True)
    body = _status(client)
    assert "weather_alerts" in body["webhooks"]
    assert "weather_alerts" not in body["templates"]


def test_templates_are_reported_even_with_no_engine(client, engine):
    """The empty state says how many templates are waiting, so it needs them
    before anything is connected."""
    engine(enabled=False, reachable=False)
    assert len(_status(client)["templates"]) > 0


# ── State 4: unknown ────────────────────────────────────────────────────────

def test_unknown_state_is_reported_as_unknown(client, monkeypatch):
    """A failure to determine the state is reported as `success: false`, which
    the page renders as 'cannot tell' - never as 'you have no engine'."""
    def boom(self):
        raise RuntimeError("config exploded")

    monkeypatch.setattr(N8nClient, "is_reachable", boom)
    cfg = SimpleNamespace(n8n=N8nConfig(enabled=True, webhooks=N8nWebhooks()))
    monkeypatch.setattr(api_server, "_get_puffpai_config", lambda: cfg)

    body = client.get("/n8n/status", headers=_auth()).get_json()
    assert body["success"] is False
    assert body.get("configured") is None, (
        "an unknown state must not assert a value for `configured`"
    )
