"""The consent ledger, the consent requests and the audit log never keep a key.

Three older stores kept text a
caller gave them with no key check, and cut it short FIRST. A key that
straddled the cut left a piece too short to look like a key, so no later
check could catch it, and a piece of a key is still a leak.

Now every free-text field is checked WHOLE for something that looks like a
key or a password before anything is cut; such text is replaced by one plain
placeholder; what is kept after a cut is checked again. A dict (``extra``,
``context``, ``result``) is checked leaf by leaf, key names too. An entity
id with Home Assistant's shape is kept exactly, with no key check (real
device ids read as keys to the check); any other id, and any action, that
looks like a key is refused and written nowhere, and a refused target is
denied. Stored decisions are never rewritten because of the check. Callers
that cut text before handing it over now let the store cut it (or cut it
the key-safe way).

Every test reads the written FILE BYTES and asserts that no 8-character piece
of the key is in them.
"""
from __future__ import annotations

import json
import types

import pytest

from puffpai import web_search
from puffpai.security import consent_gate, consent_requests
from puffpai.security.audit_log import AuditLog
from puffpai.security.consent_gate import ConsentLedger

K = "q8ZrT2vLx9KmW4pNc7Hs1YdQ3"
PLACEHOLDER = "[text that looked like a key or a password, not kept]"


def _filler(n: int) -> str:
    """Ordinary words, exactly *n* characters, ending in a space."""
    return ("tea and toast " * (n // 14 + 2))[:n - 1] + " "


def _leaks(data: str, key: str = K) -> list[str]:
    """Every 8-character piece of *key* found in *data*."""
    return [key[i:i + 8] for i in range(len(key) - 7) if key[i:i + 8] in data]


def _bytes(path) -> str:
    with open(path, "rb") as fh:
        return fh.read().decode("utf-8")


# ── fixtures: everything writes under tmp_path ──────────────────────────────

@pytest.fixture
def audit(tmp_path):
    path = tmp_path / "audit.jsonl"
    return AuditLog(log_file=path), path


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    state, trail = tmp_path / "consent_state.json", tmp_path / "consent_ledger.jsonl"
    led = ConsentLedger(state_path=str(state), audit_path=str(trail))
    monkeypatch.setattr(consent_gate, "_default_ledger", led)
    return led, state, trail


@pytest.fixture
def creq(tmp_path, monkeypatch, ledger):
    reqs, feed = tmp_path / "consent_requests.json", tmp_path / "agent-activity.jsonl"
    monkeypatch.setattr(consent_requests, "REQUESTS_PATH", str(reqs))
    monkeypatch.setattr(consent_requests, "ACTIVITY_LOG", str(feed))
    return reqs, feed, ledger


def _all(*paths) -> str:
    out = []
    for p in paths:
        try:
            out.append(_bytes(p))
        except FileNotFoundError:
            pass
    return "\n".join(out)


# ── the placeholder ─────────────────────────────────────────────────────────

def test_the_placeholder_is_plain_and_does_not_look_like_a_key():
    assert web_search.KEY_TEXT_PLACEHOLDER == PLACEHOLDER
    assert PLACEHOLDER in web_search._PLACEHOLDERS  # noqa: SLF001
    assert not web_search.looks_like_pasted_key(PLACEHOLDER)
    assert not web_search.looks_like_pasted_key("a note: " + PLACEHOLDER + " ok")


# ── the audit log ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("where", ["inside", "straddle", "past"])
def test_audit_details_never_keep_a_key(audit, where):
    log, path = audit
    details = {"inside": "the key is " + K + " ok",
               "straddle": _filler(490) + K + " and more words",
               "past": _filler(600) + K}[where]
    log.log("bus_send", details=details)
    data = _bytes(path)
    assert not _leaks(data), where
    assert PLACEHOLDER in data
    assert json.loads(data.splitlines()[-1])["details"] == PLACEHOLDER


@pytest.mark.parametrize("field,limit", [("action", 100), ("user", 100),
                                         ("result", 50)])
def test_audit_short_fields_never_keep_a_key(audit, field, limit):
    log, path = audit
    for value in ("x " + K, _filler(limit - 10) + K):
        log.log(**{"action": "a", field: value})
    data = _bytes(path)
    assert not _leaks(data), field
    assert data.count(PLACEHOLDER) == 2


def test_audit_extra_key_as_value_name_and_nested(audit):
    log, path = audit
    log.log("a", extra={"note": "here " + K})
    log.log("a", extra={K: "a value"})
    log.log("a", extra={"outer": {"inner": [1, "deep " + K, {"x": K}]}})
    data = _bytes(path)
    assert not _leaks(data)
    lines = [json.loads(line) for line in data.splitlines()]
    assert lines[0]["extra"] == {"note": PLACEHOLDER}
    assert list(lines[1]["extra"]) == [PLACEHOLDER]
    assert lines[1]["extra"][PLACEHOLDER] == "a value"
    assert lines[2]["extra"] == {"outer": {"inner": [1, PLACEHOLDER,
                                                     {"x": PLACEHOLDER}]}}


def test_audit_extra_key_straddling_the_2000_cut(audit):
    log, path = audit
    log.log("a", extra={"a": "short", "notes": _filler(1975) + K + " tail"})
    data = _bytes(path)
    assert not _leaks(data)
    extra = json.loads(data.splitlines()[-1])["extra"]
    assert extra["notes"] == PLACEHOLDER and extra["a"] == "short"


def test_audit_extra_raw_fallback_never_keeps_a_piece_of_a_key(audit):
    """The old fallback kept ``str(extra)[:200]``: a key near 190 was cut."""
    log, path = audit
    log.log("a", extra={"notes": _filler(179) + K + " " + _filler(2100)})
    data = _bytes(path)
    assert not _leaks(data)
    assert PLACEHOLDER in data


def test_audit_extra_too_long_stays_valid_json_and_bounded(audit):
    log, path = audit
    extra = {"a": _filler(1500), "b": _filler(1500), "n": 7, "flag": True}
    entry = log.log("a", extra=extra)
    line = json.loads(_bytes(path).splitlines()[-1])
    assert isinstance(line["extra"], dict)
    assert len(json.dumps(line["extra"], default=str)) <= 2000
    assert line["extra"] == entry["extra"]
    for k, v in line["extra"].items():
        if isinstance(v, str):  # a cut keeps whole words of the original
            assert extra[k].startswith(v)


def test_audit_extra_ordinary_is_kept_exactly(audit):
    log, path = audit
    extra = {"zones_queried": ["knowledge", "vault"], "kid_profile": None,
             "authenticated": True, "n": 3, "reasons": ["ok: all fine"],
             "envelope": {"id": "abc123", "payload_keys": ["targets"]}}
    log.log("archivist_retrieve", extra=extra)
    assert json.loads(_bytes(path).splitlines()[-1])["extra"] == extra


def test_audit_ordinary_fields_are_kept_exactly(audit):
    log, path = audit
    details = "concierge->sentinel:review " + _filler(300)
    log.log("bus_send", user="owner", result="success", details=details)
    line = json.loads(_bytes(path).splitlines()[-1])
    assert line["details"] == details
    assert (line["action"], line["user"], line["result"]) == (
        "bus_send", "owner", "success")


def test_audit_ordinary_long_details_are_cut_at_a_word(audit):
    log, path = audit
    details = _filler(700)
    log.log("a", details=details)
    kept = json.loads(_bytes(path).splitlines()[-1])["details"]
    assert len(kept) <= 500 and details.startswith(kept)
    assert details[len(kept)] == " "  # no part of a word is kept


# ── the consent ledger ──────────────────────────────────────────────────────

@pytest.mark.parametrize("where", ["inside", "straddle", "past"])
def test_consent_record_context_never_keeps_a_key(ledger, where):
    led, state, trail = ledger
    context = {"inside": "turn on " + K,
               "straddle": _filler(190) + K + " more",
               "past": _filler(300) + K}[where]
    led.record("light.kitchen", context=context)
    data = _all(state, trail)
    assert not _leaks(data), where
    assert led.all()["light.kitchen"]["context"] == PLACEHOLDER


def test_consent_record_source_never_keeps_a_key(ledger):
    led, state, trail = ledger
    led.record("light.kitchen", source="agent " + K)
    data = _all(state, trail)
    assert not _leaks(data)
    assert PLACEHOLDER in _bytes(state) and PLACEHOLDER in _bytes(trail)


@pytest.mark.parametrize("where", ["inside", "straddle", "past"])
def test_consent_gate_task_text_and_name_never_keep_a_key(ledger, where):
    led, state, trail = ledger
    task = {"inside": "please " + K,
            "straddle": _filler(190) + K + " more",
            "past": _filler(300) + K}[where]
    result = consent_gate.gate_targets(["light.kitchen"], name="auto " + K,
                                       task_text=task, authored_by="agent " + K)
    data = _all(state, trail)
    assert not _leaks(data), where
    assert result["task_text"] == PLACEHOLDER
    assert result["automation_name"] == PLACEHOLDER
    assert result["authored_by"] == PLACEHOLDER


@pytest.mark.parametrize("where", ["inside", "straddle", "past"])
def test_sentinel_review_task_text_never_keeps_a_key(ledger, where):
    led, state, trail = ledger
    task = {"inside": "please " + K,
            "straddle": _filler(190) + K + " more",
            "past": _filler(300) + K}[where]
    result = consent_gate.sentinel_review({"alias": "x", K: 1}, task_text=task)
    data = _all(state, trail)
    assert not _leaks(data), where
    assert result["task_text"] == PLACEHOLDER
    assert result["automation_keys"] == sorted(["alias", PLACEHOLDER])


def test_consent_decision_by_never_keeps_a_key(ledger):
    led, state, trail = ledger
    consent_gate.approve("light.kitchen", by="owner " + K)
    consent_gate.deny("light.hall", by=_filler(50) + K)
    data = _all(state, trail)
    assert not _leaks(data)
    assert led.all()["light.kitchen"]["decided_by"] == PLACEHOLDER


def test_consent_ordinary_text_is_kept_exactly(ledger):
    led, state, trail = ledger
    task = "turn on the kitchen light at 7 please " + _filler(100)
    result = consent_gate.gate_targets(["light.kitchen"], name="Morning lights",
                                       task_text=task, authored_by="planner")
    row = led.all()["light.kitchen"]
    assert row["context"] == task and row["source"] == "planner"
    assert result["task_text"] == task
    assert result["automation_name"] == "Morning lights"
    led.approve("light.kitchen", by="owner")
    assert led.all()["light.kitchen"]["decided_by"] == "owner"
    assert led.status("light.kitchen") == consent_gate.APPROVED


# ── consent requests ────────────────────────────────────────────────────────

_REQ = dict(entity_id="camera.front_door", label="Front door",
            action="dashboard_add_camera", reason="no consent yet",
            requested_by="sentinel", context={"task_text": "show the door"})


@pytest.mark.parametrize("field", ["label", "reason", "requested_by"])
def test_consent_request_text_fields_never_keep_a_key(creq, field):
    reqs, feed, _ = creq
    kwargs = dict(_REQ, **{field: "words " + K})
    req = consent_requests.create_request(**kwargs)
    data = _all(reqs, feed)
    assert not _leaks(data), field
    assert req[field] == PLACEHOLDER
    assert PLACEHOLDER in _bytes(reqs)


@pytest.mark.parametrize("context", [
    {"task_text": "show " + K},
    {K: "a value"},
    {"nested": {"list": ["ok", "x " + K]}},
    {"task_text": _filler(190) + K + " more"},
])
def test_consent_request_context_never_keeps_a_key(creq, context):
    reqs, feed, _ = creq
    consent_requests.create_request(**dict(_REQ, context=context))
    data = _all(reqs, feed)
    assert not _leaks(data)
    assert PLACEHOLDER in _bytes(reqs)


def test_consent_request_activity_line_never_keeps_a_key(creq):
    reqs, feed, _ = creq
    consent_requests.create_request(**dict(_REQ, label="Front " + K,
                                           reason="because " + K))
    data = _bytes(feed)
    assert not _leaks(data)
    assert PLACEHOLDER in data
    consent_requests.log_agent_activity("sentinel " + K, "ev", "task " + K,
                                        "done", {"k": K, K: 1})
    assert not _leaks(_bytes(feed))


def test_consent_resolve_note_and_by_never_keep_a_key(creq):
    reqs, feed, (led, state, trail) = creq
    consent_requests.create_request(**_REQ)
    done = consent_requests.resolve("camera.front_door", approved=True,
                                    by="user " + K, note="ok " + K)
    assert done is not None
    data = _all(reqs, feed, state, trail)
    assert not _leaks(data)
    assert done["note"] == PLACEHOLDER and done["resolved_by"] == PLACEHOLDER
    assert led.status("camera.front_door") == consent_gate.APPROVED


def test_consent_request_action_that_looks_like_a_key_is_refused(creq):
    reqs, feed, _ = creq
    with pytest.raises(ValueError):
        consent_requests.create_request(**dict(_REQ, action="add_" + K))
    assert not _leaks(_all(reqs, feed))
    assert consent_requests.list_open() == []


def test_consent_resolve_of_an_unknown_id_that_looks_like_a_key(creq):
    reqs, feed, (led, state, trail) = creq
    assert consent_requests.resolve("camera." + K, approved=True) is None
    assert not _leaks(_all(reqs, feed, state, trail))


#: Real device ids the key check reads as keys: kept exactly as they are.
REAL_IDS = ["sensor.0x00158d0001a2b3c4_temperature",
            "switch.shelly1pm_9e8d7c6b5a41_relay",
            "light.wiz_rgbw_tunable_6c2f1d"]
#: Not Home Assistant's shape (upper case), and a key: refused.
BAD_ID = "camera." + K


def _boom(*_a, **_k):
    raise RuntimeError("the key check broke")


def _disk(root) -> str:
    """Every file under *root*, read as text."""
    import pathlib
    return "\n".join(p.read_bytes().decode("utf-8", "replace")
                     for p in pathlib.Path(root).rglob("*") if p.is_file())


def test_the_id_rule():
    for rid in REAL_IDS:
        assert web_search.looks_like_pasted_key(rid)  # why the shape rule
        assert web_search.is_ha_entity_id(rid)
        assert web_search.checked_id(rid) == rid
    for other in ("routine:reminder_1", "outside:brave_search", "(none)"):
        assert web_search.checked_id(other) == other
    assert not web_search.is_ha_entity_id(BAD_ID)
    assert web_search.checked_id(BAD_ID) is None
    assert not web_search.is_ha_entity_id("light." + "a" * 250)  # too long
    assert not web_search.is_ha_entity_id("light.kitchen\n")


def test_real_device_ids_round_trip_raw_through_the_ledger(ledger):
    led, state, trail = ledger
    a, b, c = REAL_IDS
    for rid in REAL_IDS:
        assert led.record(rid, context="seen") == consent_gate.PENDING
    assert led.approve(a) == {"entity_id": a, "status": consent_gate.APPROVED}
    assert led.deny(b) == {"entity_id": b, "status": consent_gate.DENIED}
    assert (led.status(a), led.status(b), led.status(c)) == (
        consent_gate.APPROVED, consent_gate.DENIED, consent_gate.PENDING)
    assert set(REAL_IDS) <= set(led.all()) and led.pending() == [c]
    both = consent_gate.gate_targets(REAL_IDS)
    assert both["action"] == "deny" and both["targets"] == sorted(REAL_IDS)
    assert consent_gate.gate_targets([a])["action"] == "allow"
    assert consent_gate.gate_targets([c])["action"] == "flag"
    data = _all(state, trail)
    for rid in REAL_IDS:
        assert rid in data


def test_real_device_ids_round_trip_raw_through_consent_requests(creq):
    reqs, feed, (led, state, trail) = creq
    for i, rid in enumerate(REAL_IDS):
        consent_requests.create_request(**dict(_REQ, entity_id=rid,
                                               label=f"Device {i}"))
    assert len(consent_requests.list_open()) == 3
    for rid in REAL_IDS:
        assert consent_requests.find_open(rid)["entity_id"] == rid
    assert consent_requests.resolve(REAL_IDS[0], approved=True)["entity_id"] \
        == REAL_IDS[0]
    assert consent_requests.resolve(REAL_IDS[1], approved=False) is not None
    assert led.status(REAL_IDS[0]) == consent_gate.APPROVED
    assert led.status(REAL_IDS[1]) == consent_gate.DENIED
    assert [r["entity_id"] for r in consent_requests.list_open()] == [REAL_IDS[2]]
    assert [r["entity_id"] for r in json.loads(_bytes(reqs))] == REAL_IDS
    # The feed lines keep the id: a Home Assistant id does not blank them.
    lines = [json.loads(x) for x in _bytes(feed).splitlines()]
    assert PLACEHOLDER not in _bytes(feed)
    for i, rid in enumerate(REAL_IDS):
        assert (f"Consent request: dashboard_add_camera for Device {i} ({rid})"
                in [x["task"] for x in lines])
    assert {"entity_id": REAL_IDS[0], "approved": True,
            "by": "user_via_chat"} in [x.get("result") for x in lines]


def test_an_id_that_looks_like_a_key_is_refused_and_written_nowhere(
        creq, monkeypatch, tmp_path):
    from puffpai import dashboard_registry
    reqs, feed, (led, state, trail) = creq
    monkeypatch.setattr(dashboard_registry, "_AUDIT_PATH",
                        str(tmp_path / "dash_audit.jsonl"))
    led.approve("light.kitchen")
    assert led.record(BAD_ID, context="seen") == consent_gate.DENIED
    assert led.approve(BAD_ID)["status"] == consent_gate.DENIED
    led.deny(BAD_ID)
    assert led.status(BAD_ID) == consent_gate.DENIED
    result = consent_gate.gate_targets([BAD_ID, "light.kitchen"],
                                       task_text="turn it on")
    assert result["action"] == "deny"
    assert result["targets"] == ["light.kitchen"]
    assert consent_gate.gate_targets([BAD_ID])["action"] == "deny"
    with pytest.raises(ValueError):
        consent_requests.create_request(**dict(_REQ, entity_id=BAD_ID))
    assert consent_requests.resolve(BAD_ID, approved=True) is None
    ok, _msg = dashboard_registry.sentinel_check(BAD_ID, "planner",
                                                 task_text="show it")
    assert not ok
    assert list(led.all()) == ["light.kitchen"]
    assert not _leaks(_disk(tmp_path) + json.dumps(result))


def test_a_check_that_breaks_while_loading_changes_no_decision(tmp_path,
                                                               monkeypatch):
    """Nothing is rewritten because of the key check: an owner's no stays a
    no even when the check breaks while the ledger is read."""
    state, trail = tmp_path / "consent_state.json", tmp_path / "trail.jsonl"
    led = ConsentLedger(state_path=str(state), audit_path=str(trail))
    led.approve("light.kitchen")
    led.deny("lock.front_door")
    before = state.read_bytes()
    monkeypatch.setattr(web_search, "looks_like_pasted_key", _boom)
    fresh = ConsentLedger(state_path=str(state), audit_path=str(trail))
    assert fresh.gate(["lock.front_door"])["action"] == "deny"
    monkeypatch.undo()
    assert fresh.gate(["lock.front_door"])["action"] == "deny"
    assert fresh.status("light.kitchen") == consent_gate.APPROVED
    assert state.read_bytes() == before


def test_a_check_that_breaks_on_another_id_refuses_it(ledger, monkeypatch):
    led, state, trail = ledger
    monkeypatch.setattr(web_search, "looks_like_pasted_key", _boom)
    other = "routine:reminder_1"
    assert led.record(other) == consent_gate.DENIED
    assert led.approve(other)["status"] == consent_gate.DENIED
    assert consent_gate.gate_targets([other])["action"] == "deny"
    assert other not in led.all()
    # A Home Assistant id needs no check, so it still works.
    assert led.record("light.kitchen") == consent_gate.PENDING


def test_an_approval_in_the_ui_reaches_the_camera_add_unchanged(
        creq, monkeypatch):
    """/sentinel/consent/requests/<id> with the Flask test client (no server,
    no network): a Home Assistant id reaches add_camera_validated as it is."""
    from puffpai import api_server, dashboard_registry
    reqs, feed, (led, state, trail) = creq
    rid = REAL_IDS[0].replace("sensor.", "camera.")
    consent_requests.create_request(**dict(_REQ, entity_id=rid))
    seen = []

    def _add(bridge, query, **kwargs):
        seen.append(query)
        return True, "added"
    monkeypatch.setattr(dashboard_registry, "add_camera_validated", _add)
    monkeypatch.setattr(api_server, "_get_ha_bridge", lambda: object())
    api_server.app.config.update(TESTING=True)
    resp = api_server.app.test_client().post(
        f"/sentinel/consent/requests/{rid}", json={"approve": True},
        headers={"Authorization": f"Bearer {api_server._API_KEY}"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["request"]["entity_id"] == rid
    assert seen == [rid]
    assert led.status(rid) == consent_gate.APPROVED


def test_consent_request_ordinary_is_kept_exactly(creq):
    reqs, feed, _ = creq
    req = consent_requests.create_request(**_REQ)
    stored = json.loads(_bytes(reqs))[0]
    for k in ("entity_id", "label", "action", "reason", "requested_by",
              "context"):
        assert stored[k] == _REQ[k] == req[k]
    line = json.loads(_bytes(feed).splitlines()[-1])
    assert line["task"] == ("Consent request: dashboard_add_camera for "
                            "Front door (camera.front_door)")
    assert line["result"] == {"entity_id": "camera.front_door",
                              "reason": "no consent yet"}
    done = consent_requests.resolve("camera.front_door", approved=False,
                                    by="user_via_ui", note="not now")
    assert done["note"] == "not now" and done["resolved_by"] == "user_via_ui"


# ── callers that cut text before handing it over ────────────────────────────

def test_keys_audit_does_not_cut_a_key_in_half(tmp_path, monkeypatch):
    from puffpai.security import keys
    monkeypatch.setattr(keys, "_state_path",
                        lambda name: str(tmp_path / name))
    keys._audit("key_add", "owner", "success",  # noqa: SLF001
                _filler(190) + K + " tail")
    keys._audit("key_add", _filler(50) + K, "success", "brave_search")  # noqa: SLF001
    data = _bytes(tmp_path / "security_logs" / "keys_audit.jsonl")
    assert not _leaks(data)
    assert data.count(PLACEHOLDER) == 2


def test_keys_audit_ordinary_details_kept(tmp_path, monkeypatch):
    from puffpai.security import keys
    monkeypatch.setattr(keys, "_state_path",
                        lambda name: str(tmp_path / name))
    keys._audit("credential_use", "planner", "done",  # noqa: SLF001
                "brave_search api.search.brave.com 2xx")
    line = json.loads(_bytes(tmp_path / "security_logs" /
                             "keys_audit.jsonl").splitlines()[-1])
    assert line["details"] == "brave_search api.search.brave.com 2xx"
    assert line["user"] == "planner"


def test_sentinel_review_does_not_cut_task_text_before_the_ledger(tmp_path, ledger):
    from puffpai.agents.envelope import Envelope
    from puffpai.agents.sentinel_agent import SentinelAgent
    led, state, trail = ledger
    log_path = tmp_path / "audit.jsonl"
    agent = SentinelAgent(audit=AuditLog(log_file=log_path))
    env = Envelope(intent="turn_on", state_changing=True,
                   payload={"targets": ["light.kitchen"],
                            "task_text": _filler(190) + K + " more"})
    agent.review(env)
    data = _all(state, trail, log_path)
    assert not _leaks(data)
    assert led.all()["light.kitchen"]["context"] == PLACEHOLDER


def test_vault_audit_line_does_not_cut_a_key_in_half(tmp_path):
    from puffpai.security.vault import Vault
    path = tmp_path / "audit.jsonl"
    holder = types.SimpleNamespace(_audit=AuditLog(log_file=path))
    Vault._log(holder, "vault_get", "success",  # noqa: SLF001
               _filler(90) + K + " more")
    data = _bytes(path)
    assert not _leaks(data)
    assert PLACEHOLDER in data


def test_dashboard_consent_request_context_does_not_cut_a_key(creq, monkeypatch,
                                                              tmp_path):
    from puffpai import dashboard_registry
    reqs, feed, _ = creq
    monkeypatch.setattr(dashboard_registry, "_AUDIT_PATH",
                        str(tmp_path / "dash_audit.jsonl"))
    ok, _msg = dashboard_registry.sentinel_check(
        "camera.front_door", "planner",
        task_text=_filler(190) + K + " more", label="Front door")
    assert not ok
    data = _all(reqs, feed, tmp_path / "dash_audit.jsonl")
    assert not _leaks(data)
    assert PLACEHOLDER in _bytes(reqs)
    assert PLACEHOLDER in _bytes(tmp_path / "dash_audit.jsonl")


@pytest.mark.parametrize("where", ["whole", "inside", "straddle", "past"])
def test_dashboard_tile_review_log_never_keeps_a_key(creq, monkeypatch,
                                                     tmp_path, where):
    """A key straddling or past the 300 cut."""
    from puffpai import dashboard_registry
    dash = tmp_path / "dash_audit.jsonl"
    monkeypatch.setattr(dashboard_registry, "_AUDIT_PATH", str(dash))
    task = {"whole": "note " + K + " end",
            "inside": _filler(100) + K + " end",
            "straddle": _filler(290) + K + " more words",
            "past": _filler(400) + K}[where]
    ok, _msg = dashboard_registry.sentinel_check("camera.front", "user",
                                                 task_text=task)
    assert not ok
    data = _bytes(dash)
    assert not _leaks(data), where
    lines = [json.loads(x) for x in data.splitlines()]
    assert lines and all(x["task_text"] == PLACEHOLDER for x in lines)
    assert all(x["entity_id"] == "camera.front" for x in lines)


def test_dashboard_tile_review_log_other_fields_and_ordinary_text(
        creq, monkeypatch, tmp_path):
    from puffpai import dashboard_registry
    dash = tmp_path / "dash_audit.jsonl"
    monkeypatch.setattr(dashboard_registry, "_AUDIT_PATH", str(dash))
    dashboard_registry._audit("deny", REAL_IDS[0],  # noqa: SLF001
                              ["ledger said " + K], "user " + K, "hi")
    task = _filler(400)
    dashboard_registry._audit("allow", "camera.front", ["ok"],  # noqa: SLF001
                              "planner", task)
    lines = [json.loads(x) for x in _bytes(dash).splitlines()]
    assert not _leaks(_bytes(dash))
    assert lines[0]["entity_id"] == REAL_IDS[0]
    assert lines[0]["reasons"] == [PLACEHOLDER]
    assert lines[0]["requested_by"] == PLACEHOLDER
    kept = lines[1]["task_text"]
    assert len(kept) <= 300 and task.startswith(kept)
    assert lines[1]["requested_by"] == "planner" and lines[1]["reasons"] == ["ok"]


# ── a cut never keeps part of a word ──────────
# A key the check misses (glued between letters of its own case, so the
# glued-key rule cannot take the letters off) was cut through by the hard
# cut used when no space was inside the limit: the part kept was too short
# to look like a key. Now only whole words that fit are kept; when none
# does, the marker below.

K3 = "fGwW0FPYT0Q3RCmj8uDh"  # made up: 20 characters, starts and ends with letters
MARKER = "[long text not kept]"
REPRO = "x" * 88 + K3 + "x" * 50


def _glued(limit: int, tail: int = 50) -> str:
    """One run with no space in it; the limit falls 12 characters into K3."""
    return "x" * (limit - 12) + K3 + "x" * tail


def test_the_long_text_marker_is_plain_and_does_not_look_like_a_key():
    assert web_search.LONG_TEXT_NOT_KEPT == MARKER
    assert not web_search.looks_like_pasted_key(MARKER)
    assert not web_search.looks_like_pasted_key("x " + MARKER + " y")


@pytest.mark.parametrize("limit", [40, 50, 60, 100, 200, 300, 500])
def test_a_run_with_no_space_is_never_kept_in_part(limit):
    kept = web_search.key_safe_text(_glued(limit), limit)
    assert kept in (MARKER, PLACEHOLDER)
    assert not _leaks(kept, K3)


@pytest.mark.parametrize("field,limit", [("action", 100), ("user", 100),
                                         ("result", 50)])
def test_audit_short_fields_never_keep_part_of_a_missed_key(audit, field,
                                                             limit):
    log, path = audit
    log.log(**{"action": "a", field: REPRO})
    log.log(**{"action": "a", field: _glued(limit)})
    data = _bytes(path)
    assert not _leaks(data, K3), field
    for line in data.splitlines():
        assert json.loads(line)[field] in (MARKER, PLACEHOLDER)


def test_audit_details_never_keep_part_of_a_missed_key(audit):
    log, path = audit
    log.log("a", details=_glued(500))
    data = _bytes(path)
    assert not _leaks(data, K3)
    assert json.loads(data)["details"] in (MARKER, PLACEHOLDER)


def test_audit_extra_shortening_never_keeps_part_of_a_missed_key(audit):
    """The stepped shortening cuts the string at 500, inside K3."""
    log, path = audit
    log.log("a", extra={"n": _glued(500, tail=2000), "ok": "fine"})
    data = _bytes(path)
    assert not _leaks(data, K3)
    extra = json.loads(data)["extra"]
    assert extra["n"] in (MARKER, PLACEHOLDER) and extra["ok"] == "fine"
    assert len(json.dumps(extra)) <= 2000


def test_vault_audit_line_never_keeps_part_of_a_missed_key(tmp_path):
    from puffpai.security.vault import Vault
    path = tmp_path / "audit.jsonl"
    holder = types.SimpleNamespace(_audit=AuditLog(log_file=path))
    Vault._log(holder, "vault_get", "success", REPRO)  # noqa: SLF001
    data = _bytes(path)
    assert not _leaks(data, K3)
    assert json.loads(data)["details"] in (MARKER, PLACEHOLDER)


@pytest.mark.parametrize("text", [REPRO, _glued(40)])
def test_routine_ledger_by_never_keeps_part_of_a_missed_key(ledger, text):
    from puffpai import routines
    led, state, trail = ledger
    by = routines._ledger_by(text)  # noqa: SLF001
    assert by in (MARKER, PLACEHOLDER)
    led.approve("routine:reminder_1", by=by)
    assert not _leaks(_all(state, trail), K3)


def test_keys_audit_never_keeps_part_of_a_missed_key(tmp_path, monkeypatch):
    from puffpai.security import keys
    monkeypatch.setattr(keys, "_state_path", lambda name: str(tmp_path / name))
    keys._audit("key_add", _glued(60), "success", _glued(200))  # noqa: SLF001
    data = _bytes(tmp_path / "security_logs" / "keys_audit.jsonl")
    assert not _leaks(data, K3)
    line = json.loads(data)
    assert line["user"] in (MARKER, PLACEHOLDER)
    assert line["details"] in (MARKER, PLACEHOLDER)


def test_consent_task_text_and_context_never_keep_part_of_a_missed_key(ledger):
    led, state, trail = ledger
    text = _glued(200)
    led.record("light.kitchen", context=text)
    result = consent_gate.gate_targets(["light.hall"], task_text=text)
    review = consent_gate.sentinel_review({"alias": "x"}, task_text=text)
    data = _all(state, trail) + json.dumps([result, review])
    assert not _leaks(data, K3)
    assert led.all()["light.kitchen"]["context"] in (MARKER, PLACEHOLDER)
    assert result["task_text"] in (MARKER, PLACEHOLDER)
    assert review["task_text"] in (MARKER, PLACEHOLDER)


def test_dashboard_logs_never_keep_part_of_a_missed_key(creq, monkeypatch,
                                                        tmp_path):
    from puffpai import dashboard_registry
    reqs, feed, _ = creq
    dash = tmp_path / "dash_audit.jsonl"
    monkeypatch.setattr(dashboard_registry, "_AUDIT_PATH", str(dash))
    ok, _msg = dashboard_registry.sentinel_check(
        "camera.front", "user", task_text=_glued(300))
    assert not ok
    assert not _leaks(_all(dash, reqs, feed), K3)
    assert json.loads(_bytes(dash))["task_text"] in (MARKER, PLACEHOLDER)
    context = json.loads(_bytes(reqs))[0]["context"]
    assert context["task_text"] in (MARKER, PLACEHOLDER)


def test_a_long_ordinary_run_with_no_space_keeps_the_marker(audit):
    """A 150-character web address in action: the marker, not a prefix."""
    log, path = audit
    url = ("https://www.example.com/articles/2026/09/24/"
           "how-to-winterize-your-garden-hose-and-outdoor-faucets-before-the-"
           "first-hard-freeze-arrives-this-year-in-the-north")
    assert len(url) > 140 and " " not in url
    assert not web_search.looks_like_pasted_key(url)
    log.log(url)
    assert json.loads(_bytes(path))["action"] == MARKER


def test_ordinary_text_with_spaces_keeps_its_whole_words():
    text = "turn on the kitchen light " * 20
    kept = web_search.key_safe_text(text, 100)
    assert kept == text[:100].rsplit(" ", 1)[0].rstrip()
    assert text.startswith(kept) and text[len(kept)] == " "
    # The word the limit falls inside is dropped whole.
    assert web_search.key_safe_text("aaaa bbbbbbbbbb", 8) == "aaaa"
    # A word that ends exactly at the limit fits.
    assert web_search.key_safe_text("aaaa bbb ccc", 8) == "aaaa bbb"
    # Within the limit: unchanged.
    assert web_search.key_safe_text("a short note", 100) == "a short note"
