"""Routes and chat hooks for routines, offers, announcements and home.

Flask test client only; nothing binds a port and no model is called: every
chat message used here is answered by the routines code before the model.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import tempfile

os.environ.setdefault("PUFFPAI_DATA_DIR", tempfile.mkdtemp(prefix="puffpai_routines_api_"))

import pytest  # noqa: E402

from puffpai import announcer, api_server, build_manifest, routines  # noqa: E402
from puffpai.security import consent_gate  # noqa: E402

API_KEY = api_server._API_KEY
H = {"Authorization": f"Bearer {API_KEY}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    led = consent_gate.ConsentLedger(state_path=str(tmp_path / "c.json"),
                                     audit_path=str(tmp_path / "c.jsonl"))
    monkeypatch.setattr(consent_gate, "_default_ledger", led)
    routines.set_feed_reader(lambda: [{"id": "low", "name": "Tonight's low",
                                       "value": 2.0, "unit": "°C"}])
    routines.set_library_picker(lambda terms: None)
    path = build_manifest.manifest_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"build": {"name": "GardenBot"},
                   "surfaces": {"goal": "A garden helper for my vegetable beds in Ohio"}}, fh)
    api_server.app.config.update(TESTING=True)
    yield api_server.app.test_client()
    routines.set_feed_reader(None)
    routines.set_library_picker(None)


def test_routes_need_the_key(client):
    assert client.get("/announcements").status_code == 401
    assert client.get("/offers").status_code == 401


def test_offer_route_and_accept(client):
    offer = client.get("/offers", headers=H).get_json()["offer"]
    assert offer["question"] == "Would you like a daily garden update at 8 am?"
    r = client.post(f"/offers/{offer['id']}/accept", headers=H, json={})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert client.get("/offers", headers=H).get_json()["offer"] is None
    listed = client.get("/routines", headers=H).get_json()
    assert listed["routines"][0]["when"] == "every day at 8 am"
    assert listed["routines"][0]["on"] is True


def test_announcements_route(client):
    announcer.announce("Good morning! Tonight's low: 2 °C.", source="routine:daily_update",
                       title="Daily update")
    body = client.get("/announcements?limit=3", headers=H).get_json()
    assert body["announcements"][0]["text"] == "Good morning! Tonight's low: 2 °C."
    assert body["speak_aloud"] is False


def test_speak_switch(client):
    body = client.post("/routines/speak", headers=H, json={"on": True}).get_json()
    assert body["speak_aloud"] is True
    assert routines.settings()["speak_aloud"] is True


def test_home_by_typed_position_and_no_default(client):
    assert client.get("/home", headers=H).get_json()["home"] is None
    r = client.post("/home", headers=H, json={"latitude": 39.759, "longitude": -84.19,
                                              "label": "Dayton", "timezone": "America/New_York"})
    assert r.get_json()["home"] == {"label": "Dayton", "latitude": 39.8, "longitude": -84.2,
                                    "timezone": "America/New_York",
                                    "source": "typed position"}


def test_home_town_lookup_refused_on_a_keep_it_local_build(client):
    r = client.post("/home", headers=H, json={"town": "Dayton", "allow_lookup": True})
    assert r.status_code == 409
    assert "can't look towns up online" in r.get_json()["message"]


def test_add_a_reminder_and_switch_it(client):
    r = client.post("/routines", headers=H, json={"kind": "reminder", "at": "21:00",
                                                  "message": "Water the plants"})
    rid = r.get_json()["routine"]["id"]
    assert client.post(f"/routines/{rid}/off", headers=H).get_json()["routine"]["on"] is False
    assert client.post(f"/routines/{rid}/on", headers=H).get_json()["routine"]["on"] is True
    assert client.delete(f"/routines/{rid}", headers=H).get_json()["ok"] is True


def test_chat_send_offer_yes_and_while_you_were_away(client):
    r = client.post("/chat/send", headers=H,
                    json={"message": "can you send me daily updates?"}).get_json()
    assert r["response"].startswith("Would you like a daily garden update at 8 am?")
    r = client.post("/chat/send", headers=H, json={"message": "yes"}).get_json()
    assert r["response"].startswith("Done. You'll get a short update every day at 8 am.")
    announcer.announce("Good morning! Tonight's low: 2 °C.", source="routine:daily_update")
    r = client.post("/chat/send", headers=H,
                    json={"message": "what routines do I have?"}).get_json()
    assert r["response"].startswith(
        "While you were away: Good morning! Tonight's low: 2 °C.\n\n")
    assert "Daily update: every day at 8 am, switched on" in r["response"]
    r = client.post("/chat/send", headers=H,
                    json={"message": "what routines do I have?"}).get_json()
    assert "While you were away" not in r["response"]


def test_openai_route_answers_and_carries_the_catch_up(client):
    announcer.announce("Reminder: Water the plants", source="routine:reminder_1")
    body = {"model": "puffpai", "messages": [{"role": "user",
                                              "content": "what workflows do I have"}]}
    r = client.post("/v1/chat/completions", headers=H, json=body).get_json()
    content = r["choices"][0]["message"]["content"]
    assert content.startswith("While you were away: Reminder: Water the plants\n\n")
    assert "no routines yet" in content
    assert "Bill Reminders" not in content


def test_openai_stream_carries_the_catch_up_first(client):
    announcer.announce("Reminder: Feed the cat", source="routine:reminder_1")
    body = {"model": "puffpai", "stream": True,
            "messages": [{"role": "user", "content": "stop daily updates"}]}
    raw = client.post("/v1/chat/completions", headers=H, json=body).get_data(as_text=True)
    chunks = [json.loads(line[6:]) for line in raw.splitlines()
              if line.startswith("data: {")]
    text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    assert text.startswith("While you were away: Reminder: Feed the cat\n\n")
    assert "Daily updates are already off." in text


def _stream_events(raw):
    return [json.loads(line[6:]) for line in raw.splitlines() if line.startswith("data: {")]


def test_chat_stream_carries_the_catch_up_and_the_offer(client):
    announcer.announce("Reminder: Close the cold frame", source="routine:reminder_1")
    raw = client.post("/chat/stream", headers=H,
                      json={"message": "what routines do I have?"}).get_data(as_text=True)
    events = _stream_events(raw)
    text = "".join(e.get("delta", "") for e in events)
    assert text.startswith("While you were away: Reminder: Close the cold frame\n\n")
    assert "no routines yet" in text
    tail = text.split("no routines yet", 1)[1]
    assert "\n\nWould you like a daily garden update at 8 am?" in tail
    done = [e for e in events if e.get("done")]
    assert len(done) == 1 and done[0]["full_response"] == text
    assert all(e.get("conversation_id") == done[0]["conversation_id"] for e in events)
    # shown once only
    raw = client.post("/chat/stream", headers=H,
                      json={"message": "what routines do I have?"}).get_data(as_text=True)
    again = "".join(e.get("delta", "") for e in _stream_events(raw))
    assert "While you were away" not in again
    assert "Would you like a daily garden update" not in again


def test_ollama_style_chat_carries_the_catch_up(client):
    announcer.announce("Reminder: Pick the beans", source="routine:reminder_1")
    body = {"model": "puffpai", "messages": [{"role": "user",
                                              "content": "what routines do I have?"}]}
    r = client.post("/api/chat", headers=H, json=body).get_json()
    assert r["message"]["content"].startswith("While you were away: Reminder: Pick the beans\n\n")
    announcer.announce("Reminder: Water the squash", source="routine:reminder_1")
    body["stream"] = True
    raw = client.post("/api/chat", headers=H, json=body).get_data(as_text=True)
    lines = [json.loads(x) for x in raw.splitlines() if x.strip()]
    assert lines[0]["message"]["content"].startswith(
        "While you were away: Reminder: Water the squash\n\n")
    assert lines[-1]["done"] is True


def test_keyless_chat_never_touches_routines_offers_or_catch_up(client, monkeypatch):
    """The Home Assistant voice pair needs no key, so a keyless chat must not
    list routines, use up the catch-up, switch updates or answer the offer."""
    monkeypatch.setattr(api_server, "_maybe_identity_answer",
                        lambda message: "stub answer")
    client.post("/routines", headers=H, json={"kind": "reminder", "at": "09:00",
                                              "message": "Take my heart pills"})
    client.post("/chat/send", headers=H,
                json={"message": "can you send me daily updates?"})
    client.post("/chat/send", headers=H, json={"message": "yes"})
    announcer.announce("Reminder: take your heart medication", source="routine:reminder_1")
    for path, body in (
            ("/v1/chat/completions", {"model": "puffpai", "messages": [
                {"role": "user", "content": "what routines do I have"}]}),
            ("/api/chat", {"model": "puffpai", "stream": False, "messages": [
                {"role": "user", "content": "what routines do I have"}]}),
            ("/v1/chat/completions", {"model": "puffpai", "messages": [
                {"role": "user", "content": "stop daily updates"}]})):
        raw = client.post(path, json=body).get_data(as_text=True)
        assert "heart" not in raw and "While you were away" not in raw
        assert "stopped your daily updates" not in raw
        if path == "/v1/chat/completions":
            assert "stub answer" in raw
    # nothing was switched or used up: the owner still sees both
    listed = client.get("/routines", headers=H).get_json()["routines"]
    assert all(r["on"] for r in listed)
    r = client.post("/chat/send", headers=H,
                    json={"message": "what routines do I have?"}).get_json()
    assert r["response"].startswith("While you were away: Reminder: take your heart")
