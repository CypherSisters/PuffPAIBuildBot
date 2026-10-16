"""The paired-device gate checks the pairing store, not just the id's shape.

Before: any loopback caller with the API key plus a made-up X-Puff-Device-Id
of 8 or more characters passed every "paired device" gate. Now the id must be
the one the dashboard proxy derives for a device in devices.json.

Each test builds a real pairing store in a temp folder (no network).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os

import pytest

from puffpai import api_server, brain_loop
from puffpai.security import paired_devices

pytestmark = pytest.mark.real_pairing

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}
SECRET = "ab" * 32
DEVICE_ID = "Kt7bQ2xY9mZa"


def _derived(device_id: str, secret: str = SECRET) -> str:
    """What pairing.ts deviceSessionId() asserts, computed independently."""
    return hmac.new(bytes.fromhex(secret), f"puff-chat-session:v1:{device_id}".encode(),
                    hashlib.sha256).hexdigest()[:32]


def _bump(path) -> None:
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 3 * 10**9))


@pytest.fixture
def store(tmp_path, monkeypatch):
    folder = tmp_path / "pairing"
    folder.mkdir()
    (folder / "pairing.json").write_text(json.dumps({
        "version": 1, "secret": SECRET, "code": "ABCDEFGH",
        "createdAt": "2026-10-01T00:00:00Z", "codeRotatedAt": "2026-10-01T00:00:00Z"}),
        encoding="utf-8")
    (folder / "devices.json").write_text(json.dumps({"version": 1, "devices": [
        {"id": DEVICE_ID, "name": "Kitchen tablet", "createdAt": "2026-10-01T00:00:00Z",
         "lastSeenAt": None}]}), encoding="utf-8")
    monkeypatch.setenv("PUFF_PAIRING_STATE", str(folder / "pairing.json"))
    monkeypatch.delenv("PUFF_PAIRED_DEVICES", raising=False)
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    paired_devices.forget()
    api_server.app.config.update(TESTING=True)
    return folder


@pytest.fixture
def client():
    return api_server.app.test_client()


def _as(device: str) -> dict:
    return {**AUTH, "X-Puff-Device-Id": device}


LOOP = {"REMOTE_ADDR": "127.0.0.1"}
MADE_UP = ("deviceaaaa000001", "a" * 32, "0123456789abcdef0123456789abcdef")


# ── the check itself ────────────────────────────────────────────────────────

def test_the_derived_id_of_a_listed_device_is_paired(store):
    hit = paired_devices.lookup(_derived(DEVICE_ID))
    assert hit and hit["id"] == DEVICE_ID and hit["name"] == "Kitchen tablet"


@pytest.mark.parametrize("made_up", MADE_UP)
def test_a_made_up_id_is_not_paired(store, made_up):
    assert paired_devices.lookup(made_up) is None


def test_the_raw_device_id_is_not_accepted_in_place_of_the_derived_one(store):
    assert paired_devices.lookup(DEVICE_ID) is None


def test_an_id_derived_under_another_secret_is_not_paired(store):
    assert paired_devices.lookup(_derived(DEVICE_ID, "cd" * 32)) is None


def test_unpairing_takes_effect_on_the_next_request(store, client):
    real = _as(_derived(DEVICE_ID))
    assert client.get("/things", headers=real).status_code == 200
    (store / "devices.json").write_text(json.dumps({"version": 1, "devices": []}),
                                        encoding="utf-8")
    _bump(store / "devices.json")
    assert client.get("/things", headers=real).status_code == 403


@pytest.mark.parametrize("damage", ["{not json", json.dumps({"version": 1}), ""])
def test_a_damaged_device_list_pairs_nobody(store, client, damage):
    (store / "devices.json").write_text(damage, encoding="utf-8")
    _bump(store / "devices.json")
    assert client.get("/things", headers=_as(_derived(DEVICE_ID))).status_code == 403


def test_a_missing_or_damaged_secret_pairs_nobody(store, client):
    (store / "pairing.json").write_text(json.dumps({"secret": "short"}), encoding="utf-8")
    _bump(store / "pairing.json")
    assert client.get("/things", headers=_as(_derived(DEVICE_ID))).status_code == 403


# ── every gate that says "paired device" ───────────────────────────────────

@pytest.mark.parametrize("made_up", MADE_UP)
def test_thing_pages_refuse_a_made_up_device(store, client, made_up):
    assert client.get("/things", headers=_as(made_up)).status_code == 403
    assert client.get("/things/skill-x/media/file?name=a.png",
                      headers=_as(made_up)).status_code == 403
    assert client.get("/things", headers=_as(_derived(DEVICE_ID))).status_code == 200


@pytest.mark.parametrize("made_up", MADE_UP)
def test_the_developer_terminal_refuses_a_made_up_device(store, client, made_up):
    got = client.post("/developer/terminal/open", headers=_as(made_up), json={})
    assert got.status_code == 403
    assert got.get_json()["code"] == "not_paired"


@pytest.mark.parametrize("made_up", MADE_UP)
def test_a_daily_task_question_cannot_be_answered_by_a_made_up_device(store, client, made_up):
    ask_id = brain_loop.open_ask(brain_loop.ANY_DEVICE, {"title": "May I run this?"})
    try:
        got = client.post("/chat/approve", headers=_as(made_up),
                          json={"id": ask_id, "yes": True})
        assert got.status_code == 403
        ok = client.post("/chat/approve", headers=_as(_derived(DEVICE_ID)),
                         json={"id": ask_id, "yes": False})
        assert ok.status_code == 200 and ok.get_json()["yes"] is False
    finally:
        brain_loop.take_answer(ask_id, brain_loop.ANY_DEVICE, "chat")


@pytest.mark.parametrize("made_up", MADE_UP)
def test_outside_switches_refuse_a_made_up_device(store, made_up):
    from puffpai import outside_api
    with api_server.app.test_request_context("/outside", headers=_as(made_up), environ_base=LOOP):
        assert outside_api.paired_device_id() == ""
    with api_server.app.test_request_context("/outside", headers=_as(_derived(DEVICE_ID)),
                                            environ_base=LOOP):
        assert outside_api.paired_device_id() == f"device:{_derived(DEVICE_ID)}"


def test_a_made_up_device_is_anonymous_not_a_shared_bucket(store):
    with api_server.app.test_request_context("/chat/send", headers=_as(MADE_UP[0]), environ_base=LOOP):
        first = api_server._get_chat_client_id()  # noqa: SLF001
    with api_server.app.test_request_context("/chat/send", headers=_as(MADE_UP[0]), environ_base=LOOP):
        second = api_server._get_chat_client_id()  # noqa: SLF001
    assert first.startswith("anon:") and second.startswith("anon:") and first != second


def test_a_direct_lan_caller_still_cannot_assert_a_device(store):
    with api_server.app.test_request_context(
            "/things", headers=_as(_derived(DEVICE_ID)),
            environ_base={"REMOTE_ADDR": "192.168.1.50"}):
        assert api_server._get_chat_client_id() == "peer:192.168.1.50"  # noqa: SLF001


# ── the label for logs ──────────────────────────────────────────────────────

def test_the_log_label_is_the_name_and_a_short_tag_never_the_id(store):
    sid = _derived(DEVICE_ID)
    shown = paired_devices.label(f"device:{sid}")
    assert shown.startswith("Kitchen tablet (device ") and shown.endswith(")")
    assert sid not in shown and DEVICE_ID not in shown
    assert paired_devices.label(f"device:{sid}") == shown          # stable
    assert paired_devices.label("device:" + "a" * 32) == paired_devices.UNKNOWN_LABEL


def test_the_terminal_audit_line_names_the_device(store):
    from puffpai import developer_terminal
    sid = _derived(DEVICE_ID)
    developer_terminal._audit("opened", "opened", f"device:{sid}")  # noqa: SLF001
    log = store.parent / "state" / "security_logs" / "audit.jsonl"
    rows = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    shown = rows[-1]["extra"]["device"]
    assert shown == paired_devices.label(f"device:{sid}")
    assert shown.startswith("Kitchen tablet (device ") and "looked like a key" not in shown
    assert sid not in log.read_text(encoding="utf-8")
