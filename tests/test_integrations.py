"""Phase 6 integration tests: Home Assistant, media, voice, and the UI.

None of these touch a live service. The HA bridge is driven by a fake REST
session, the device watcher by a fake bridge, media by an unconfigured bridge,
and voice by the built-in stub engines. The UI test only imports the package
and (best-effort) constructs the toolkit headlessly, skipping when no display
is available.

Run:
    pytest tests/test_integrations.py
"""
from __future__ import annotations

import os

import pytest
import requests


# ---------------------------------------------------------------------------
# Home Assistant bridge — driven by a fake REST session (no live HA)
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, payload, status=200, content=b"ok"):
        self._payload = payload
        self.status_code = status
        self.ok = 200 <= status < 300
        self.content = content
        self.text = ""

    def raise_for_status(self):
        if not self.ok:
            raise requests.exceptions.HTTPError(response=self)

    def json(self):
        return self._payload


class _FakeSession:
    """Routes HA REST calls to canned payloads and records POSTs."""

    def __init__(self, states):
        self._states = states
        self.headers: dict = {}
        self.posts: list = []

    def get(self, url, **kw):
        if url.endswith("/api/states"):
            return _FakeResp(self._states)
        if url.rstrip("/").endswith("/api"):
            return _FakeResp({"message": "API running."})
        if "area_registry" in url:
            return _FakeResp([])
        if "/api/states/" in url:
            eid = url.rsplit("/", 1)[-1]
            for s in self._states:
                if s["entity_id"] == eid:
                    return _FakeResp(s)
            return _FakeResp({})
        return _FakeResp({})

    def post(self, url, json=None, **kw):
        self.posts.append((url, json))
        return _FakeResp([])

    def delete(self, url, **kw):
        return _FakeResp({})


def _make_bridge(states):
    from puffpai.ha_bridge import HABridge
    bridge = HABridge(url="http://ha.test:8123", token="tok")
    bridge._enabled = True
    bridge._session = _FakeSession(states)
    return bridge


def test_ha_bridge_lists_entities():
    states = [
        {"entity_id": "light.desk", "state": "on",
         "attributes": {"friendly_name": "Desk Lamp"}},
        {"entity_id": "sensor.temp", "state": "21",
         "attributes": {"friendly_name": "Temp"}},
    ]
    bridge = _make_bridge(states)
    assert bridge.is_connected() is True
    lights = bridge.list_devices(domain="light")
    assert [d.entity_id for d in lights] == ["light.desk"]
    assert lights[0].friendly_name == "Desk Lamp"
    assert bridge.get_entity_state("light.desk")["state"] == "on"


def test_ha_bridge_calls_service():
    bridge = _make_bridge([
        {"entity_id": "light.desk", "state": "off", "attributes": {}}])
    result = bridge.control_device("light.desk", "turn_on", brightness_pct=80)
    assert result == {"success": True, "entity_id": "light.desk", "action": "turn_on"}
    # The service call reached the (fake) REST API as a POST.
    posts = bridge._session.posts
    assert any("services/light/turn_on" in url for url, _ in posts)
    payload = next(body for url, body in posts if "services/light/turn_on" in url)
    assert payload["entity_id"] == "light.desk"
    assert payload["brightness_pct"] == 80


def test_ha_bridge_disabled_degrades():
    from puffpai.ha_bridge import HABridge
    bridge = HABridge(url="http://ha.test:8123", token="")
    bridge._enabled = False
    assert bridge.is_connected() is False


# ---------------------------------------------------------------------------
# Device watcher → consent-gate pending flow
# ---------------------------------------------------------------------------

class _FakeHABridge:
    def __init__(self, states):
        self._states = states

    def get_all_states(self, *, force=False):
        return self._states


def _ledger(tmp_path):
    from puffpai.security.consent_gate import ConsentLedger
    return ConsentLedger(
        state_path=os.path.join(tmp_path, "state.json"),
        audit_path=os.path.join(tmp_path, "audit.jsonl"),
    )


def test_device_watcher_records_pending_and_approves(tmp_path):
    from puffpai.ha_device_watcher import HADeviceWatcher
    from puffpai.security.consent_gate import PENDING, APPROVED

    ledger = _ledger(str(tmp_path))
    states = [
        {"entity_id": "light.new", "state": "on",
         "attributes": {"friendly_name": "New Light", "manufacturer": "Acme"}},
        {"entity_id": "automation.foo", "state": "on",
         "attributes": {"friendly_name": "Foo"}},  # internal → auto-recorded
    ]
    watcher = HADeviceWatcher(bridge=_FakeHABridge(states), ledger=ledger,
                              data_dir=str(tmp_path))

    found = watcher.check_for_new_devices()
    assert [d.entity_id for d in found] == ["light.new"]
    # New device is pending in the ledger; the internal helper is not surfaced.
    assert ledger.status("light.new") == PENDING
    assert ledger.pending() == ["light.new"]
    assert watcher.get_stats()["auto_recorded"] == 1

    # A human approves → ledger records approval, queue clears.
    watcher.approve_device("light.new")
    assert ledger.status("light.new") == APPROVED
    assert watcher.get_pending_count() == 0


def test_device_watcher_deny_flow(tmp_path):
    from puffpai.ha_device_watcher import HADeviceWatcher
    from puffpai.security.consent_gate import DENIED

    ledger = _ledger(str(tmp_path))
    states = [{"entity_id": "camera.unknown", "state": "idle",
               "attributes": {"friendly_name": "Unknown Cam"}}]
    watcher = HADeviceWatcher(bridge=_FakeHABridge(states), ledger=ledger,
                              data_dir=str(tmp_path))
    watcher.check_for_new_devices()
    watcher.deny_device("camera.unknown")
    assert ledger.status("camera.unknown") == DENIED


def test_device_watcher_no_ha_returns_empty(tmp_path):
    """When the bridge raises (HA unreachable), scanning degrades to []."""
    from puffpai.ha_device_watcher import HADeviceWatcher

    class _Broken:
        def get_all_states(self, *, force=False):
            raise RuntimeError("HA down")

    watcher = HADeviceWatcher(bridge=_Broken(), ledger=_ledger(str(tmp_path)),
                              data_dir=str(tmp_path))
    assert watcher.check_for_new_devices() == []


# ---------------------------------------------------------------------------
# Media bridge — clean degradation when unconfigured
# ---------------------------------------------------------------------------

def test_media_bridge_unconfigured_degrades():
    from puffpai.media_bridge import MediaBridge, MediaBridgeError

    bridge = MediaBridge()
    bridge._enabled = False
    status = bridge.get_status()
    assert status["enabled"] is False
    assert status["connected"] is False
    assert bridge.is_connected() is False
    # A search against a disabled bridge degrades to an empty list, not a crash.
    assert bridge.search_music("anything") == []
    # The low-level request path still surfaces a clear, typed error.
    with pytest.raises(MediaBridgeError):
        bridge._jf_request("/Items")


# ---------------------------------------------------------------------------
# Voice — component availability with the built-in stub engines
# ---------------------------------------------------------------------------

def test_voice_stub_engines_are_usable_but_report_unavailable():
    """The stub engines are the graceful fallback: constructible and callable,
    but they honestly report themselves as *not* an available real engine."""
    from puffpai.voice.stt import StubSTT
    from puffpai.voice.tts import StubTTS

    stt, tts = StubSTT(), StubTTS()
    assert stt.is_available() is False
    assert tts.is_available() is False
    # Callable without hardware/models present.
    assert stt.transcribe(b"") == ""
    assert tts.speak("hello") is None


def test_voice_real_engines_unavailable_without_models():
    from puffpai.voice.stt import VoskSTT
    from puffpai.voice.tts import PiperTTS

    # No model on disk / optional deps absent → available() is False, no raise.
    assert VoskSTT(model_path="/nonexistent/vosk").is_available() is False
    assert PiperTTS(model_path="/nonexistent/piper.onnx").is_available() is False


def test_voice_models_paths_resolved():
    from puffpai.config import get_voice_models

    models = get_voice_models()
    assert set(models) == {"vosk_model_dir", "piper_model_path",
                           "wake_word_model_path"}
    assert all(os.path.isabs(p) for p in models.values())


def test_audio_device_manager_degrades_without_sounddevice():
    from puffpai.voice.audio_devices import AudioDeviceManager

    mgr = AudioDeviceManager()
    # is_available is a property; list_devices must never raise regardless.
    assert isinstance(bool(mgr.is_available), bool)
    assert isinstance(mgr.list_devices(), list)


# ---------------------------------------------------------------------------
# Desktop UI — import + best-effort headless construction
# ---------------------------------------------------------------------------

def test_ui_module_imports_and_exposes_launch():
    pytest.importorskip("tkinter")
    from puffpai.ui.main_window import launch, PersonaRAGUI
    assert callable(launch)
    assert PersonaRAGUI.__name__ == "PersonaRAGUI"
    # The lazy package export still resolves.
    import puffpai.ui as ui
    assert ui.PersonaRAGUI is PersonaRAGUI


def test_ui_toolkit_constructs_headlessly_or_skips():
    """Probe that the GUI toolkit can build a root; skip when no display."""
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"no display available for headless UI construction: {exc}")
    else:
        root.withdraw()
        root.destroy()
