"""The /keys routes: pairing is the gate, a device-bound ticket is the yes.

No PIN anywhere in these routes. Storing needs the
server's vault open, because storing writes the vault.
"""

from __future__ import annotations

import json

import pytest

from keys_support import (canary, fast_vault, install_vendor, machine_key_file,
                          reset_keys, set_posture)
from puffpai import api_server
from puffpai.security import egress_doors, keys

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}
DEV_A = {**AUTH, "X-Puff-Device-Id": "deviceaaaa000001"}
DEV_B = {**AUTH, "X-Puff-Device-Id": "devicebbbb000002"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    for d in egress_doors.DOORS:
        monkeypatch.delenv(d.env_var, raising=False)
    reset_keys()
    set_posture("enumerated")
    v = fast_vault(monkeypatch, tmp_path / "vault")
    monkeypatch.setattr(api_server, "_vault_instance", v)
    keys.set_vault_provider(lambda: v)
    api_server.app.config.update(TESTING=True)
    c = api_server.app.test_client()
    c.vault = v
    yield c
    reset_keys()


def _confirm(client, sid="brave_search", headers=DEV_A, **body):
    return client.post(f"/keys/{sid}/confirm", headers=headers, json=body)


def test_every_keys_route_needs_the_api_key(client):
    for method, path in (("get", "/keys"), ("get", "/keys/brave_search/how"),
                         ("post", "/keys/brave_search/confirm"),
                         ("post", "/keys/brave_search"),
                         ("post", "/keys/brave_search/check"),
                         ("post", "/keys/brave_search/locked"),
                         ("post", "/keys/brave_search/remove")):
        assert getattr(client, method)(path).status_code == 401, path


def test_the_list_never_holds_a_value_and_shows_the_steps(client, monkeypatch):
    body = client.get("/keys", headers=AUTH).get_json()
    assert body["title"] == "Sign-up keys"
    ids = [k["id"] for k in body["keys"]]
    assert ids == ["brave_search", "marginalia_search"]
    brave = body["keys"][0]
    assert brave["state"] == "missing" and brave["state_line"] == "Not added yet."
    assert brave["walk_through"]["line"].startswith(
        "Brave Search needs a free key: 1) open https://api-dashboard.search."
        "brave.com/register 2) sign up 3) paste the key here.")
    assert "value" not in json.dumps(body).lower().replace("_value", "")


def test_an_unpaired_caller_cannot_get_a_yes(client):
    r = client.post("/keys/brave_search/confirm", headers=AUTH, json={})
    assert r.status_code == 403


def test_add_walks_the_whole_way_and_never_echoes_the_key(client, monkeypatch):
    vendor = install_vendor(monkeypatch)
    value = canary()
    vendor.accept("X-Subscription-Token", value)
    minted = _confirm(client).get_json()
    assert minted["ok"] and "Add your Brave Search key." in minted["sentence"]
    assert "Switch on Brave search" in minted["sentence"]
    r = client.post("/keys/brave_search", headers=DEV_A,
                    json={"value": value, "ticket": minted["ticket"]})
    assert r.status_code == 200, r.get_json()
    assert value not in r.get_data(as_text=True)
    listed = client.get("/keys", headers=AUTH).get_data(as_text=True)
    assert value not in listed
    assert json.loads(listed)["keys"][0]["state"] == "works"


def test_the_ticket_is_bound_to_the_device_and_used_once(client, monkeypatch):
    vendor = install_vendor(monkeypatch)
    value = canary()
    vendor.accept("X-Subscription-Token", value)
    ticket = _confirm(client).get_json()["ticket"]
    r = client.post("/keys/brave_search", headers=DEV_B,
                    json={"value": value, "ticket": ticket})
    assert r.status_code == 403 and vendor.calls == []
    r = client.post("/keys/brave_search", headers=DEV_A,
                    json={"value": value, "ticket": ticket})
    assert r.status_code == 403  # burned by the wrong-device try
    assert vendor.calls == []


def test_no_ticket_means_nothing_is_sent(client, monkeypatch):
    vendor = install_vendor(monkeypatch)
    r = client.post("/keys/brave_search", headers=DEV_A,
                    json={"value": canary()})
    assert r.status_code == 403 and vendor.calls == []


def test_a_ticket_for_keep_working_when_locked_does_not_fit_a_plain_add(
        client, monkeypatch, tmp_path):
    machine_key_file(monkeypatch, tmp_path)
    vendor = install_vendor(monkeypatch)
    value = canary()
    vendor.accept("API-Key", value)
    ticket = _confirm(client, "marginalia_search").get_json()["ticket"]
    r = client.post("/keys/marginalia_search", headers=DEV_A,
                    json={"value": value, "ticket": ticket, "background": True})
    assert r.status_code == 403 and vendor.calls == []


def test_a_key_in_the_address_is_refused_and_not_echoed(client, monkeypatch):
    vendor = install_vendor(monkeypatch)
    value = canary()
    r = client.post(f"/keys/brave_search?value={value}", headers=DEV_A, json={})
    assert r.status_code == 400
    assert value not in r.get_data(as_text=True)
    assert vendor.calls == []


def test_a_locked_vault_is_said_before_the_yes_is_spent(client, monkeypatch):
    vendor = install_vendor(monkeypatch)
    client.vault.lock()
    ticket = _confirm(client).get_json()["ticket"]
    r = client.post("/keys/brave_search", headers=DEV_A,
                    json={"value": canary(), "ticket": ticket})
    assert r.status_code == 409 and "locked" in r.get_json()["message"]
    assert vendor.calls == []


def test_a_bad_shape_is_refused_before_the_network(client, monkeypatch):
    vendor = install_vendor(monkeypatch)
    ticket = _confirm(client).get_json()["ticket"]
    r = client.post("/keys/brave_search", headers=DEV_A,
                    json={"value": "not a key", "ticket": ticket})
    assert r.status_code == 400
    assert r.get_json()["message"].startswith("That does not look like a key.")
    assert vendor.calls == []


def test_check_and_remove(client, monkeypatch):
    vendor = install_vendor(monkeypatch)
    value = canary()
    vendor.accept("X-Subscription-Token", value)
    ticket = _confirm(client).get_json()["ticket"]
    client.post("/keys/brave_search", headers=DEV_A,
                json={"value": value, "ticket": ticket})
    r = client.post("/keys/brave_search/check", headers=DEV_A)
    assert r.status_code == 200 and r.get_json()["message"] == "That key works."
    r = client.post("/keys/brave_search/remove", headers=DEV_A)
    assert r.status_code == 200
    assert r.get_json()["message"].startswith("Removed.")
    assert client.get("/keys", headers=AUTH).get_json()["keys"][0]["state"] == \
        "missing"


def test_keep_working_when_locked_needs_its_own_yes_and_never_for_brave(
        client, monkeypatch, tmp_path):
    machine_key_file(monkeypatch, tmp_path)
    vendor = install_vendor(monkeypatch)
    value = canary()
    vendor.accept("API-Key", value)
    ticket = _confirm(client, "marginalia_search").get_json()["ticket"]
    client.post("/keys/marginalia_search", headers=DEV_A,
                json={"value": value, "ticket": ticket})
    r = client.post("/keys/marginalia_search/locked", headers=DEV_A,
                    json={"on": True})
    assert r.status_code == 403
    minted = _confirm(client, "marginalia_search", only_locked=True).get_json()
    assert "Anyone who gets into this computer account" in minted["sentence"]
    r = client.post("/keys/marginalia_search/locked", headers=DEV_A,
                    json={"on": True, "ticket": minted["ticket"]})
    assert r.status_code == 200 and r.get_json()["key"]["background"] is True
    # Off needs nothing.
    r = client.post("/keys/marginalia_search/locked", headers=DEV_A,
                    json={"on": False})
    assert r.status_code == 200 and r.get_json()["key"]["background"] is False
    # Brave can never get this yes.
    assert _confirm(client, "brave_search", only_locked=True).status_code in (
        404, 409)


def test_the_generic_vault_item_route_refuses_a_key_record(client, monkeypatch):
    vendor = install_vendor(monkeypatch)
    value = canary()
    vendor.accept("X-Subscription-Token", value)
    ticket = _confirm(client).get_json()["ticket"]
    client.post("/keys/brave_search", headers=DEV_A,
                json={"value": value, "ticket": ticket})
    for path in ("/vault/item/__puffpai.key.brave_search", "/vault/contents"):
        r = client.get(path, headers={**AUTH, "X-Vault-Token": "anything"})
        assert value not in r.get_data(as_text=True)
