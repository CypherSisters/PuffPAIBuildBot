"""Home Assistant's voice key.

Caller classes: the voice key reads and makes undoable changes, never
removes; a paired device or the terminal does everything (removes after a
yes); no key gets ordinary chat only: no app action, no app data, no list
read or write, whatever it sends. The voice key opens no other route.
"""
from __future__ import annotations

import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "integrate"))
import integrate_support as sup  # noqa: E402

from puffpai import api_server, app_fastpath, app_registry  # noqa: E402
from puffpai.integrate import actions, chat, voice_key, wording  # noqa: E402

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}
DEVICE = "deviceaaaa000001"
PAIRED = {**AUTH, "X-Puff-Device-Id": DEVICE}
A = sup.IID

GARDEN = {
    "schema": "puffpai_app/v1", "id": "gardenbot", "name": "GardenBot",
    "data": {"default": {"plantings": [], "harvest_log": []}},
    "vocabulary": {"keywords": ["gardenbot", "plantings", "harvest log"]},
}


class Store:
    """The list store in memory (the two methods AppData has)."""

    def __init__(self):
        self.documents = {"gardenbot": {"plantings": [{"item": "basil"}], "harvest_log": []}}
        self.writes = 0

    def get_data(self, app_id):
        import copy
        return copy.deepcopy(self.documents.get(app_id, {}))

    def set_data(self, app_id, document):
        import copy
        self.writes += 1
        self.documents[app_id] = copy.deepcopy(document)
        return True


@pytest.fixture
def world(monkeypatch, tmp_path):
    brain = sup.Brain(*[(f"{A}__add_guest", {"guest_name": "Leo Park"})] * 3)
    w = sup.setup_chat(monkeypatch, tmp_path, brain=brain)
    voice_key.forget_cache()
    store = Store()
    monkeypatch.setattr(api_server, "_get_app_data", lambda: store)
    monkeypatch.setattr(app_registry, "current_manifests", lambda: [GARDEN])
    monkeypatch.setattr(api_server, "_query_ollama_direct",
                        lambda *a, **k: ({"response": "Ordinary chat.", "sources": [],
                                          "suggestions": []} if k.get("enriched")
                                         else "Ordinary chat."))
    monkeypatch.setattr(api_server, "_stream_ollama_tokens",
                        lambda *a, **k: iter(["Ordinary chat."]))
    api_server.app.config.update(TESTING=True)
    w["store"] = store
    yield w
    voice_key.forget_cache()


@pytest.fixture
def client():
    return api_server.app.test_client()


def _post(client, message, headers, path="/v1/chat/completions"):
    if path == "/api/chat":
        body = {"messages": [{"role": "user", "content": message}]}
    else:
        body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": message}]}
    resp = client.post(path, headers=headers, json=body)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    return (data.get("message") or {}).get("content") if path == "/api/chat" else \
        data["choices"][0]["message"]["content"]


# -- the key itself ----------------------------------------------------------------

def test_there_is_no_voice_key_until_the_owner_makes_one(world):
    assert voice_key.current() == "" and voice_key.identity() is None
    assert not voice_key.matches("x" * 60)


def test_a_new_key_is_kept_off_the_logs_and_replaces_the_old_one(world, caplog):
    caplog.set_level(logging.DEBUG)
    first = voice_key.new()
    assert len(first) >= 40 and voice_key.matches(first)
    assert first != api_server._API_KEY
    ident = voice_key.identity()
    second = voice_key.new()
    assert not voice_key.matches(first) and voice_key.matches(second)
    assert not voice_key.is_identity(ident) and voice_key.is_identity(voice_key.identity())
    voice_key.forget_cache()                    # read back from where it is kept
    assert voice_key.matches(second)
    assert first not in caplog.text and second not in caplog.text
    voice_key.off()
    assert voice_key.current() == "" and not voice_key.matches(second)
    voice_key.forget_cache()
    assert voice_key.current() == ""            # every copy is gone


def test_the_voice_key_opens_no_other_route(world, client):
    key = voice_key.new()
    bearer = {"Authorization": f"Bearer {key}"}
    for method, path in (("get", "/integrations"), ("post", "/chat/send"),
                         ("get", "/integrations/voice-key"), ("get", "/keys")):
        assert getattr(client, method)(path, headers=bearer, json={}).status_code == 401, path


# -- the page and its routes -------------------------------------------------------------

def test_the_key_page_is_for_a_paired_browser_only(world, client):
    assert client.get("/integrations/voice-key", headers=AUTH).status_code == 403
    assert client.post("/integrations/voice-key/new", headers=AUTH).status_code == 403
    assert voice_key.current() == ""
    page = client.get("/integrations/voice-key", headers=PAIRED).get_json()
    assert page["on"] is False and page["key"] == ""
    assert page["line"] == wording.VOICE_KEY_OFF_LINE
    resp = client.post("/integrations/voice-key/new", headers=PAIRED)
    made = resp.get_json()
    assert resp.headers["Cache-Control"] == "no-store"
    assert made["on"] and voice_key.matches(made["key"])
    assert made["line"] == ("To let Home Assistant voice use your apps, give Home "
                            "Assistant this voice key.")
    off = client.post("/integrations/voice-key/off", headers=PAIRED).get_json()
    assert off["on"] is False and off["key"] == "" and voice_key.current() == ""


# -- integrated app actions per caller class ---------------------------------------

def test_voice_key_reads_and_makes_undoable_changes_only(world, client, monkeypatch):
    monkeypatch.setattr(app_registry, "current_manifests", lambda: [])
    key = voice_key.new()
    bearer = {"Authorization": f"Bearer {key}", "X-Client": "home-assistant"}
    got = _post(client, "who is on the guest list in Big Day?", bearer)
    assert "Zanzibar Quill" in got
    got = _post(client, "add Leo Park to the guest list in Big Day", bearer)
    assert "undo" in got.lower()
    runs = [r[0] for r in world["source"].runs]
    assert runs == [f"{A}__read_guests", f"{A}__add_guest"]
    assert {r[2]["surface"] for r in world["source"].runs} == {"voice"}
    assert all(r[2]["device_id"] == voice_key.identity() for r in world["source"].runs)


def test_voice_key_never_removes(world, client, monkeypatch):
    monkeypatch.setattr(app_registry, "current_manifests", lambda: [])
    world["brain"].picks[:] = [(f"{A}__remove_guest", {"guest_name": "Zanzibar Quill"})]
    key = voice_key.new()
    got = _post(client, "take Zanzibar Quill off the guest list in Big Day",
                {"Authorization": f"Bearer {key}"})
    assert "Open your dashboard to confirm removing that." in got
    assert world["source"].runs == [] and chat._pending == {}  # noqa: SLF001


def test_voice_identity_is_voice_only(world):
    voice_key.new()
    ident = voice_key.identity()
    assert chat.try_answer("who is on the guest list in Big Day?", surface="chat",
                           device_id=ident) is None
    assert actions.run(f"{A}__read_guests", {}, {"device_id": ident,
                                                 "surface": "chat"})["ok"] is False
    assert world["source"].runs == []


@pytest.mark.parametrize("headers", [
    {}, {"X-Client": "home-assistant"}, {"Authorization": "Bearer " + "z" * 48},
    {"X-Puff-Device-Id": DEVICE}])
def test_no_key_gets_no_app_action_and_no_app_data(world, client, headers):
    voice_key.new()
    for message in ("who is on the guest list in Big Day?",
                    "add Leo Park to the guest list in Big Day"):
        got = _post(client, message, headers)
        assert "Zanzibar" not in got
    assert world["source"].runs == [] and world["brain"].calls == []


def test_an_old_or_switched_off_key_is_no_key(world, client):
    old = voice_key.new()
    voice_key.new()
    _post(client, "who is on the guest list in Big Day?", {"Authorization": f"Bearer {old}"})
    now = voice_key.current()
    voice_key.off()
    _post(client, "who is on the guest list in Big Day?", {"Authorization": f"Bearer {now}"})
    assert world["source"].runs == []


def test_the_ollama_route_ignores_the_voice_key(world, client):
    key = voice_key.new()
    _post(client, "who is on the guest list in Big Day?",
          {"Authorization": f"Bearer {key}"}, path="/api/chat")
    assert world["source"].runs == []


# -- the declarative lists (the keyless fastpath list writes) ----------------------

@pytest.mark.parametrize("path", ["/v1/chat/completions", "/api/chat"])
def test_no_key_no_list_read_or_write(world, client, path):
    got = _post(client, "add tomatoes to the plantings list", {}, path=path)
    assert world["store"].writes == 0
    assert "Added tomatoes" not in got
    got = _post(client, "what is on the plantings list", {}, path=path)
    assert "basil" not in got


def test_voice_key_adds_to_a_list_and_can_undo_it(world, client):
    key = voice_key.new()
    bearer = {"Authorization": f"Bearer {key}"}
    got = _post(client, "add tomatoes to the plantings list", bearer)
    assert "Added tomatoes" in got and 'Say "undo" to take it back.' in got
    rows = world["store"].documents["gardenbot"]["plantings"]
    assert [r.get("item") for r in rows] == ["basil", "tomatoes"]
    assert "basil" in _post(client, "what is on the plantings list", bearer)
    got = _post(client, "undo", bearer)
    assert got.startswith("Undone.")
    assert [r.get("item") for r in world["store"].documents["gardenbot"]["plantings"]] == [
        "basil"]


def test_voice_key_never_removes_from_a_list(world, client):
    key = voice_key.new()
    got = _post(client, "remove basil from the plantings list",
                {"Authorization": f"Bearer {key}"})
    assert "Open your dashboard to confirm removing that." in got
    assert world["store"].writes == 0
    assert world["store"].documents["gardenbot"]["plantings"] == [{"item": "basil"}]


def test_the_owner_keeps_the_lists_as_before(world, client):
    resp = client.post("/chat/send", headers=PAIRED,
                       json={"message": "add tomatoes to the plantings list"})
    assert "Added tomatoes" in resp.get_json()["response"]
    assert world["store"].writes == 1


def test_undo_of_a_list_that_changed_since_is_refused(world):
    store = world["store"]
    voice_key.new()
    ident = voice_key.identity()
    before = store.get_data("gardenbot")
    after = {**before, "plantings": before["plantings"] + [{"item": "kale"}]}
    store.set_data("gardenbot", after)
    actions.remember_list_change(ident, "gardenbot", before, after, store=store)
    changed = {**after, "plantings": after["plantings"] + [{"item": "leek"}]}
    store.set_data("gardenbot", changed)
    got = actions.undo_last(ident)
    assert got["ok"] is False and got["line"] == wording.UNDO_CHANGED
    assert store.get_data("gardenbot") == changed


def test_caller_classes():
    app = api_server.app
    assert api_server._app_caller_class() == "terminal"
    key = voice_key.new()
    try:
        cases = [("/v1/chat/completions", {"Authorization": f"Bearer {key}"}, "voice"),
                 ("/api/chat", {"Authorization": f"Bearer {key}"}, None),
                 ("/v1/chat/completions", {}, None),
                 ("/v1/chat/completions", AUTH, "owner")]
        for path, headers, want in cases:
            with app.test_request_context(path, method="POST", headers=headers,
                                          environ_base={"REMOTE_ADDR": "127.0.0.1"}):
                app.preprocess_request()
                assert api_server._app_caller_class() == want, (path, want)
    finally:
        voice_key.off()
        voice_key.forget_cache()


def test_fastpath_is_not_asked_for_a_keyless_caller(world, client, monkeypatch):
    seen = []
    monkeypatch.setattr(app_fastpath, "handle", lambda *a, **k: seen.append(a) or None)
    _post(client, "add tomatoes to the plantings list", {})
    _post(client, "add tomatoes to the plantings list", {}, path="/api/chat")
    assert seen == []


def test_the_dry_run_store_has_no_other_way_in(world):
    staged = api_server._StagedAppData(world["store"])
    with pytest.raises(AttributeError):
        staged.documents                      # the real store's insides are not reachable
    with pytest.raises(AttributeError):
        staged.delete_data("gardenbot")
    staged.set_data("gardenbot", {"plantings": []})
    assert world["store"].writes == 0         # a staged write writes nothing


# -- voice wording and changes that are not adds -------

@pytest.mark.parametrize("message", [
    "mark basil as done on the plantings list", "change basil to kale on the plantings list",
    "clear the plantings list", "the plantings list", "take basil off the plantings list",
    "delete the last one from the plantings list"])
def test_voice_is_never_told_to_say_a_removal(world, client, message):
    key = voice_key.new()
    got = _post(client, message, {"Authorization": f"Bearer {key}"})
    assert '"remove' not in got.lower(), got
    assert world["store"].writes == 0
    assert world["store"].documents["gardenbot"]["plantings"] == [{"item": "basil"}]


def test_voice_list_help_names_only_what_voice_may_do(world, client):
    key = voice_key.new()
    got = _post(client, "the plantings list", {"Authorization": f"Bearer {key}"})
    assert got == wording.VOICE_LIST_HELP.format(label="plantings"), got
    owner = client.post("/chat/send", headers=PAIRED,
                        json={"message": "the plantings list"}).get_json()["response"]
    assert '"remove tomatoes from the plantings list"' in owner   # the owner may


def test_voice_key_makes_a_change_that_is_not_an_add(monkeypatch, tmp_path, client):
    """Voice makes changes that can be undone, not only adds. The
    declarative lists know only add, read and remove (for every caller); an
    integrated app's own change actions ("mark a task done") run for voice."""
    mark = sup.action("mark_done", "Mark a task as done on the checklist", "changes",
                      props={"title": {"type": "string", "maxLength": 120}},
                      required=["title"])
    brain = sup.Brain((f"{A}__mark_done", {"title": "Book the florist"}))
    w = sup.setup_chat(monkeypatch, tmp_path, acts=sup.guest_actions() + [mark], brain=brain)
    voice_key.forget_cache()
    monkeypatch.setattr(app_registry, "current_manifests", lambda: [])
    api_server.app.config.update(TESTING=True)
    key = voice_key.new()
    try:
        got = _post(client, "mark Book the florist as done on the checklist in Big Day",
                    {"Authorization": f"Bearer {key}"})
        assert [r[0] for r in w["source"].runs] == [f"{A}__mark_done"]
        assert w["source"].runs[0][2]["surface"] == "voice"
        assert 'Say "undo" to take it back.' in got
    finally:
        voice_key.forget_cache()


# -- voice is never asked for a detail of a removal --------------------

@pytest.mark.parametrize("message", ["yes, remove", "yes, remove it", "yes remove them"])
def test_voice_cannot_confirm_a_removal_asked_for_elsewhere(world, client, monkeypatch,
                                                            message):
    """With a removal
    waiting on the terminal, voice "yes, remove" was answered "Tell me the
    guest name and I will do it in Big Day", a promise voice can never keep.
    Now it is the dashboard line, nothing runs, no model is asked and the
    terminal's own question is untouched."""
    from puffpai.security import consent_tickets
    monkeypatch.setattr(app_registry, "current_manifests", lambda: [])
    world["brain"].picks[:] = [(f"{A}__remove_guest", {"guest_name": "Zanzibar Quill"})] * 3
    term = consent_tickets.terminal_device_id()
    asked = chat.try_answer("take Zanzibar Quill off the guest list in Big Day",
                            surface="terminal", device_id=term)
    assert asked and asked["pending"], asked
    calls = len(world["brain"].calls)
    key = voice_key.new()
    got = _post(client, message, {"Authorization": f"Bearer {key}"})
    assert got == wording.VOICE_REMOVE, got
    assert "Tell me" not in got
    assert world["source"].runs == [] and len(world["brain"].calls) == calls
    assert chat._has_pending(term)                          # noqa: SLF001 - still hers


def test_voice_removal_without_its_detail_is_the_dashboard_line(world):
    """A removal the model picked for voice with no grounded argument: the
    dashboard line, never "Tell me the guest name"."""
    voice_key.new()
    world["brain"].picks[:] = [(f"{A}__remove_guest", {"guest_name": "Someone Else"})]
    got = chat.try_answer("take them off the guest list in Big Day", surface="voice",
                          device_id=voice_key.identity())
    assert got and got["reply"] == wording.VOICE_REMOVE, got
    assert world["source"].runs == []
