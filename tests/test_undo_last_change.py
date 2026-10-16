"""Undo.

"undo" within 10 minutes reverts THIS device's last change: a browser app
through ``webapps.mirror.restore(app_id, version)`` only when no newer write
exists ("The app changed after that ..."); after 10 minutes, or for another
device, there is nothing to undo; a change that cannot be undone is never
offered as undoable; the paired-browser route does the same.
"""
from __future__ import annotations

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "integrate"))
import integrate_support as sup  # noqa: E402

from puffpai.integrate import actions, chat, wording  # noqa: E402

DEV = "device:deviceaaaa000001"
DEV_B = "device:devicebbbb000002"
A = sup.IID


@pytest.fixture
def world(monkeypatch, tmp_path):
    brain = sup.Brain(*[(f"{A}__add_guest", {"guest_name": "Leo Park"})] * 4)
    w = sup.setup_chat(monkeypatch, tmp_path, brain=brain)
    restored = []
    state = {"ok": True}
    mirror = types.ModuleType("puffpai.webapps.mirror")

    def restore(app_id, version):
        restored.append((app_id, version))
        return {"ok": state["ok"]}
    mirror.restore = restore
    monkeypatch.setitem(sys.modules, "puffpai.webapps.mirror", mirror)
    w["restored"] = restored
    w["mirror_state"] = state
    return w


def _say(text, device=DEV):
    return chat.try_answer(text, surface="chat", device_id=device)


def test_undo_restores_the_version_from_before(world):
    got = _say("add Leo Park to the guest list")
    assert got["reply"].endswith('Say "undo" to take it back.')
    assert _say("undo")["reply"] == wording.UNDONE.format(app="Big Day")
    assert world["restored"] == [(A, 3)]
    # once undone, there is nothing more of this device's to undo
    assert _say("undo")["reply"] == wording.NO_UNDO


def test_a_newer_write_is_not_undone(world):
    world["mirror_state"]["ok"] = False
    _say("add Leo Park to the guest list")
    assert _say("undo")["reply"] == wording.UNDO_CHANGED


def test_another_device_cannot_undo_it(world):
    _say("add Leo Park to the guest list")
    assert _say("undo", device=DEV_B)["reply"] == wording.NO_UNDO
    assert world["restored"] == []


def test_after_ten_minutes_there_is_nothing_to_undo(world, monkeypatch):
    import time as _time
    _say("add Leo Park to the guest list")
    real = _time.monotonic
    monkeypatch.setattr(actions.time, "monotonic", lambda: real() + actions.UNDO_SECONDS + 1)
    assert _say("undo")["reply"] == wording.NO_UNDO
    assert world["restored"] == []


def test_a_change_that_cannot_be_undone_is_never_offered_as_one(monkeypatch, tmp_path):
    acts = [sup.action("add_note", "Add a note", "changes", undoable=False,
                       props={"text": {"type": "string"}}, required=["text"])]
    brain = sup.Brain((f"{A}__add_note", {"text": "call the florist"}))
    w = sup.setup_chat(monkeypatch, tmp_path, acts=acts, brain=brain)
    got = _say("add a note: call the florist")
    assert "undo" not in got["reply"].lower()
    assert not actions.has_undo(DEV) and len(w["source"].runs) == 1


def test_undo_route_for_a_paired_browser(world):
    from puffpai import api_server
    client = api_server.app.test_client()
    auth = {"Authorization": f"Bearer {api_server._API_KEY}"}
    _say("add Leo Park to the guest list")
    assert client.post("/integrations/undo", headers=auth).status_code == 403
    resp = client.post("/integrations/undo",
                       headers={**auth, "X-Puff-Device-Id": "deviceaaaa000001"})
    assert resp.status_code == 200 and resp.get_json()["ok"] is True
    assert world["restored"] == [(A, 3)]


def test_undo_needs_an_identity():
    for device in (None, "", "peer:1.2.3.4", "voice:ha-0000000000000000"):
        assert actions.undo_last(device)["ok"] is False
