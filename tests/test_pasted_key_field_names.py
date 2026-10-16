"""A key typed as a NAME is never printed, logged or answered back.

The pasted-key check read every mapping key as
text, and its one log line printed the field's name -- so when the key WAS
the name (``{"task": "hi", "q8Zr...": "1"}``), the refusal printed the key
word for word. Form field names and ``?`` names were not read at all.

Now a name that looks like a key is refused like any other text and the log
line says "a field name"; every other name goes through the same key-safe
preview before it is shown. The same holds for the other security log lines
added later that print a name someone else chose (the home-network
guard's refused host, the outside-traffic log, consent tickets, app and door
ids, outside switches, the sign-up key list).
"""
from __future__ import annotations

import logging

import pytest

from puffpai import api_server, web_search
from puffpai.security import pasted_key_guard as guard

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}

#: Looks like a key (a long random token); not a credential for anything.
CANARY = "q8ZrT2vLx9KmW4pNc7Hs1YdQ3"
#: The same, as an id or a host name would carry it (lower case).
LOWER = CANARY.lower()


def test_the_canaries_look_like_keys():
    assert web_search.looks_like_pasted_key(CANARY)
    assert web_search.looks_like_pasted_key(LOWER)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(api_server, "_ACTIVITY_LOG",
                        str(tmp_path / "agent-activity.jsonl"))
    monkeypatch.setattr(api_server, "_TASK_QUEUE",
                        str(tmp_path / "task-queue.jsonl"))
    monkeypatch.setenv("PUFFPAI_AGENT_TEAM_ENABLED", "0")
    monkeypatch.setattr(api_server, "_rate_limit", lambda: None)
    api_server._rate_limit_store.clear()
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client()


#: (how, request kwargs). Every place a sender chooses a NAME.
NAMED_BY_SENDER = [
    ("mapping key (the finding's repro)",
     dict(path="/agents/task", json={"task": "hi", CANARY: "1"})),
    ("nested mapping key",
     dict(path="/agents/task",
          json={"task": "hi", "props": {"lists": {"groceries": {CANARY: []}}}})),
    ("mapping key inside a list",
     dict(path="/agents/task", json={"task": "hi", "items": [{CANARY: "x"}]})),
    ("mapping key that ends like an id",
     dict(path="/agents/task", json={"task": "hi", CANARY + "_id": "1"})),
    ("mapping key that ends like a folder",
     dict(path="/agents/task", json={"task": "hi", CANARY + "_dir": "C:/x"})),
    ("form field name",
     dict(path="/agents/task", data={"task": "hi", CANARY: "1"})),
    ("query name",
     dict(path=f"/agents/task?{CANARY}=1", json={"task": "hi"})),
    ("query name with no value",
     dict(path=f"/agents/task?{CANARY}", json={"task": "hi"})),
]


@pytest.mark.parametrize("how,kwargs", NAMED_BY_SENDER,
                         ids=[h for h, _ in NAMED_BY_SENDER])
def test_a_key_typed_as_a_name_is_refused_and_never_printed(
        client, capsys, caplog, tmp_path, how, kwargs):
    caplog.set_level(logging.DEBUG)
    r = client.post(headers=AUTH, **kwargs)
    assert r.status_code == 400, how
    assert r.get_json()["key_paste"] is True
    assert r.get_json()["error"] == guard.REFUSAL
    assert CANARY not in r.get_data(as_text=True)
    out = capsys.readouterr()
    assert CANARY not in out.out and CANARY not in out.err
    assert CANARY not in caplog.text
    assert "[Security] Refused POST agents_submit_task: a field name looked " \
           "like a key or a password (not shown)." in out.out
    assert not (tmp_path / "agent-activity.jsonl").exists()
    assert not (tmp_path / "task-queue.jsonl").exists()


def test_a_key_in_a_path_segment_is_refused_and_never_printed(client, capsys,
                                                              caplog):
    caplog.set_level(logging.DEBUG)
    r = client.patch(f"/persona/{CANARY}", headers=AUTH, json={})
    assert r.status_code == 400 and r.get_json()["key_paste"] is True
    assert CANARY not in r.get_data(as_text=True)
    out = capsys.readouterr()
    assert CANARY not in out.out and CANARY not in out.err
    assert CANARY not in caplog.text
    assert "[Security] Refused PATCH" in out.out


def test_a_field_whose_name_cannot_be_shown_is_named_plainly(client, capsys):
    # The value is the key; the name is someone's e-mail address.
    r = client.post("/agents/task", headers=AUTH,
                    json={"task": "hi", "someone@example.com": CANARY})
    assert r.status_code == 400
    printed = capsys.readouterr().out
    assert CANARY not in printed and "someone@example.com" not in printed
    assert "a field (its name not shown) looked like" in printed


def test_an_ordinary_field_is_still_named(client, capsys):
    r = client.post("/agents/task", headers=AUTH,
                    json={"task": f"use my key {CANARY}"})
    assert r.status_code == 400
    printed = capsys.readouterr().out
    assert "the 'task' field looked like a key or a password" in printed
    assert CANARY not in printed


def test_an_ordinary_request_with_ordinary_names_goes_through(client):
    r = client.post("/agents/task?source=kiosk", headers=AUTH,
                    json={"task": "check the garden lights tonight",
                          "props": {"lists": {"groceries": ["milk"]}}})
    assert r.status_code == 200


def test_first_key_field_never_returns_the_name_itself():
    for body in ({CANARY: "1"}, {"lists": {CANARY: []}},
                 {"a": [{"b": {CANARY + "_id": 1}}]}):
        assert guard.first_key_field("app_put_data", body) == guard.KEY_IN_NAME
    assert guard.first_key_field("app_put_data", None,
                                 form={CANARY: "x"}) == guard.KEY_IN_NAME
    assert guard.first_key_field("app_put_data", None,
                                 args={CANARY: ""}) == guard.KEY_IN_NAME
    assert guard.first_key_field("app_put_data",
                                 {"a b\nc": CANARY}) == guard.NAME_NOT_SHOWN
    assert guard.first_key_field("app_put_data", "x" + CANARY) == "(body)"


@pytest.mark.parametrize("name,shown", [
    ("task", "task"), ("taskId", "taskId"), ("new-password", "new-password"),
    (CANARY, guard.KEY_IN_NAME), (LOWER, guard.KEY_IN_NAME),
    ("e6c71f1c-e0a3-4303-980f-e1ace328ad3f", guard.KEY_IN_NAME),
    ("someone@example.com", guard.NAME_NOT_SHOWN),
    ("two words", guard.NAME_NOT_SHOWN), ("x" * 41, guard.NAME_NOT_SHOWN),
    ("line\x1b[31m", guard.NAME_NOT_SHOWN), ("", guard.NAME_NOT_SHOWN),
])
def test_shown_name(name, shown):
    assert guard.shown_name(name) == shown


def test_log_name_fails_closed(monkeypatch):
    assert web_search.log_name("api.open-meteo.com") == "api.open-meteo.com"
    assert web_search.log_name(LOWER + ".example.com") == web_search.NAME_NOT_SHOWN

    def _boom(_text):
        raise RuntimeError("broken")
    monkeypatch.setattr(web_search, "looks_like_pasted_key", _boom)
    assert web_search.log_name("weather") == web_search.NAME_NOT_SHOWN
    assert guard.shown_name("weather") == guard.NAME_NOT_SHOWN


# ── the other security log lines ──────────────────────────────────────────

def test_the_home_network_guard_never_logs_a_key_shaped_host(caplog, tmp_path):
    from puffpai.security import egress_ledger, lan_guard
    caplog.set_level(logging.DEBUG)
    ledger = tmp_path / "outside.jsonl"
    egress_ledger.set_path_override(str(ledger))
    try:
        for code in (lan_guard.LEDGER_CODE, lan_guard.PROXY_CODE):
            lan_guard._record_refusal((f"{LOWER}.example.com", 443),
                                      "puffpai/x.py", 1, code=code)
        lan_guard._record_refusal(("8.8.8.8", 443), "puffpai/x.py", 2)
    finally:
        egress_ledger.set_path_override(None)
    assert LOWER not in caplog.text
    assert "an address (not shown)" in caplog.text
    assert "8.8.8.8" in caplog.text           # an address is still shown
    kept = ledger.read_text(encoding="utf-8")
    assert LOWER not in kept and "8.8.8.8" in kept


def test_the_outside_traffic_log_keeps_no_key_shaped_name():
    from puffpai.security import egress_ledger
    rec = egress_ledger.build_record("weather", host=f"{LOWER}.example.com",
                                     outcome="done",
                                     path=f"/bot123:{CANARY}/getMe")
    assert rec is not None
    assert rec["host"] == "" and rec["path"] == ""
    assert egress_ledger.build_record(LOWER, host="api.open-meteo.com",
                                      outcome="done") is None
    ok = egress_ledger.build_record("weather", host="api.open-meteo.com",
                                    outcome="done", path="/v1/forecast")
    assert ok["host"] == "api.open-meteo.com" and ok["path"] == "/v1/forecast"


def test_a_consent_ticket_line_never_shows_what_the_caller_sent(caplog):
    from puffpai.security import consent_tickets
    caplog.set_level(logging.DEBUG)
    ok, verdict = consent_tickets.redeem("not-a-ticket", "terminal:x", CANARY,
                                         LOWER)
    assert not ok and verdict == "unknown"
    ok, _ = consent_tickets.redeem("not-a-ticket", "terminal:x", "outside_on",
                                   f"{CANARY} and more")
    assert not ok
    assert CANARY not in caplog.text and LOWER not in caplog.text
    assert "(not shown)" in caplog.text


def test_app_and_door_ids_are_shown_only_when_key_safe(caplog):
    from puffpai.security import egress_doors
    caplog.set_level(logging.DEBUG)
    saved = dict(egress_doors._APP_DOORS)
    try:
        egress_doors.register_app_doors([(LOWER, None), (CANARY, None)])
    finally:
        with egress_doors._APP_LOCK:
            egress_doors._APP_DOORS.clear()
            egress_doors._APP_DOORS.update(saved)
    assert LOWER not in caplog.text and CANARY not in caplog.text
    assert "(not shown)" in caplog.text


def test_an_outside_switch_line_never_shows_a_key_shaped_id(caplog, monkeypatch,
                                                           tmp_path):
    from puffpai.security import outside_switches
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path))
    outside_switches.forget()
    caplog.set_level(logging.DEBUG)
    try:
        outside_switches.set_switch(LOWER, False)
        outside_switches.set_switch("weather", False)
    finally:
        outside_switches.forget()
    assert LOWER not in caplog.text
    assert "outside service (not shown) switched off" in caplog.text
    assert "outside service weather switched off" in caplog.text


def test_the_key_list_names_no_key_shaped_host():
    from puffpai.security import keys
    row = dict(keys.catalog()["brave_search"])
    row["hosts"] = [f"{LOWER}.local"]
    problem = keys.row_problem(row)
    assert "public name" in problem and LOWER not in problem
