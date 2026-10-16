"""The live-data routes: behind the API key, fail closed, serve only checked
pictures, and answer the chat question from the stored card with no model."""

from __future__ import annotations

import json

import pytest

from livedata_support import (PNG_1PX, FakeResponse, candidate, fake_network,
                              feed_manifest, fixture_probe, install,
                              sample_bytes, set_posture)
from puffpai import api_server, feed_generator, feeds
from puffpai.security import egress_doors

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv(egress_doors.APPS_KILL_ENV, raising=False)
    egress_doors.clear_app_doors()
    egress_doors.reset_cache()
    api_server.app.config.update(TESTING=True)
    yield api_server.app.test_client()
    egress_doors.clear_app_doors()
    egress_doors.reset_cache()


def test_every_live_route_needs_the_key(client):
    set_posture("enumerated")
    install(feed_manifest())
    for method, path in (("get", "/apps/skyshots/feed"),
                         ("post", "/apps/skyshots/feed/allow"),
                         ("post", "/apps/skyshots/feed/refresh"),
                         ("get", "/apps/skyshots/feed/image/" + "a" * 64 + ".png"),
                         ("get", "/apps/feed-drafts"),
                         ("post", "/apps/feed-drafts/x/install")):
        assert getattr(client, method)(path).status_code == 401, path


def test_card_allow_fetch_image_and_chat(client, monkeypatch):
    set_posture("enumerated")
    install(feed_manifest(card={"kind": "image", "title": "Pictures",
                                "image": "item.pic", "headline": "item.name"}))
    net = fake_network(monkeypatch)
    net.on("https://api.example.org/", FakeResponse(
        200, json.dumps({"item": {"pic": "https://img.example.org/a.png",
                                  "name": "Ring"}}).encode()))
    net.on("https://img.example.org/", FakeResponse(200, PNG_1PX, "image/png"))

    shut = client.get("/apps/skyshots/feed", headers=AUTH).get_json()
    assert shut["allowed"] is False and shut["card"] is None and net.calls == []
    assert shut["how_to_fix"] == "puffpai apps allow skyshots"

    # Switching an outside service on needs a one-time yes bound to this
    # paired device (security/consent_tickets.py); without it, nothing opens.
    assert client.post("/apps/skyshots/feed/allow", headers=AUTH).status_code == 403
    device = {**AUTH, "X-Puff-Device-Id": "livedatadevice01"}
    ticket = client.post("/outside/card-skyshots/confirm",
                         headers=device).get_json()["ticket"]
    allowed = client.post("/apps/skyshots/feed/allow", headers=device,
                          json={"ticket": ticket}).get_json()
    assert allowed["state"] == "open"
    card = client.get("/apps/skyshots/feed", headers=AUTH).get_json()
    assert card["status"] == "ok" and card["card"]["headline"] == "Ring"
    name = card["card"]["image"]

    image = client.get(f"/apps/skyshots/feed/image/{name}", headers=AUTH)
    assert image.status_code == 200 and image.data == PNG_1PX
    assert image.headers["Content-Type"] == "image/png"
    assert image.headers["X-Content-Type-Options"] == "nosniff"
    for bad in ("..%2Flatest.json", "x.svg", "A" * 64 + ".png"):
        assert client.get(f"/apps/skyshots/feed/image/{bad}",
                          headers=AUTH).status_code == 404

    monkeypatch.setattr(api_server, "_get_rag_context", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "_save_conversation", lambda conv: None,
                        raising=False)
    reply = client.post("/chat/send", headers=AUTH,
                        json={"message": "show me the latest sky shots"}).get_json()
    text = json.dumps(reply)
    assert "The latest picture from Sky Shots: Ring" in text
    assert "Data: Example Data" in text

    denied = client.post("/apps/skyshots/feed/deny", headers=AUTH).get_json()
    assert denied["state"] == "said_no"


def test_unknown_apps_and_bad_choices_are_refused(client):
    set_posture("enumerated")
    install(feed_manifest())
    assert client.get("/apps/nothere/feed", headers=AUTH).status_code == 404
    assert client.post("/apps/nothere/feed/allow", headers=AUTH).status_code == 404
    assert client.post("/apps/skyshots/feed/choice", headers=AUTH,
                       json={"name": "topic", "value": "x"}).status_code == 400


def test_drafts_list_show_install_and_discard(client):
    set_posture("enumerated")
    for cid in ("pbdb_taxa", "noaa_swpc_kp"):
        cand = candidate(cid)
        feed_generator.draft_feed_app(cand, build_online=True,
                                      probe=fixture_probe(sample_bytes(cand)))
    listed = client.get("/apps/feed-drafts", headers=AUTH).get_json()["drafts"]
    ids = {d["id"] for d in listed}
    assert ids == {"paleobiology_database", "noaa_space_weather_kp_index"}
    one = client.get("/apps/feed-drafts/paleobiology_database", headers=AUTH).get_json()
    assert len(one["review"]) == 7 and one["what_leaves"].startswith("A request")
    assert "sample_fingerprints" not in one and "manifest" not in one
    device = {**AUTH, "X-Puff-Device-Id": "deviceaaaa000001"}
    ticket = client.post("/apps/feed-drafts/paleobiology_database/confirm",
                         headers=device).get_json()["ticket"]
    done = client.post("/apps/feed-drafts/paleobiology_database/install",
                       headers=device,
                       json={"allow": True, "ticket": ticket}).get_json()
    assert done["installed"] and done["service"]["state"] == "open"
    gone = client.delete("/apps/feed-drafts/noaa_space_weather_kp_index",
                         headers=AUTH).get_json()
    assert gone == {"removed": True}
    assert client.get("/apps/feed-drafts", headers=AUTH).get_json()["drafts"] == []
    assert client.get("/apps/feed-drafts/nothere", headers=AUTH).status_code == 404
