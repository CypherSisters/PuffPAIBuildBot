"""No route logs, stores, echoes or queues a pasted key.

``/agents/task`` echoed a pasted key and wrote it word for
word to agent-activity.jsonl and task-queue.jsonl. Only the chat routes ran
the pasted-key check. Now one before-request check
(``puffpai.security.pasted_key_guard``) reads every request that can change
something, and a field that looks like a key or a password is refused before
the route runs.

The walk below posts a canary key to EVERY POST/PUT/PATCH/DELETE route in the
Flask url_map, in every field the route reads and a few more, and proves the
canary is never in a response, a log line or a file. The keys page's own key
field (and the vault's secrets) are the allow-listed exceptions.
"""
from __future__ import annotations

import inspect
import json
import os
import re

import pytest

from puffpai import api_server, plain_words, web_search
from puffpai.security import pasted_key_guard as guard

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}

#: Looks like a key (a long random token); not a credential for anything.
CANARY = "q8ZrT2vLx9KmW4pNc7Hs1YdQ3Rt"

#: Field names every route gets, on top of the ones its own code reads.
GENERIC = ["task", "text", "message", "label", "name", "words", "title",
           "note", "town", "goal", "prompt", "content", "query", "key",
           "description", "say", "speak", "body", "value", "question",
           "summary", "reason", "comment"]

#: The routes the finding named. The walk must reach every one of them.
NAMED = {"agents_submit_task", "routines.routines_add", "routines.routines_speak",
         "add_more.add_find", "looks.looks_find", "keys.keys_add",
         "routines.home_set", "persona_edit", "persona_scaffold_route",
         "app_put_data", "sentinel_consent_resolve"}

_FIELD = re.compile(r"""(?:\.get\(\s*|\[\s*)['"]([A-Za-z_][\w-]*)['"]""")


def _fields_read_by(view) -> set[str]:
    try:
        source = inspect.getsource(view)
    except (OSError, TypeError):
        return set()
    return set(_FIELD.findall(source))


def _body(endpoint: str, view) -> dict:
    names = (_fields_read_by(view) | set(GENERIC)) - {"POST", "PUT", "PATCH",
                                                      "DELETE", "GET"}
    body: dict = {}
    for name in sorted(names):
        if (endpoint, name) in guard.SECRET_FIELDS or guard._machine(name):  # noqa: SLF001
            continue
        body[name] = CANARY
    # Nested the way the dashboard sends some of them.
    body["messages"] = [{"role": "user", "content": CANARY}]
    body["props"] = {"title": CANARY}
    body["answers"] = {"name": CANARY, "goal": [CANARY]}
    body["params"] = {"text": CANARY}
    return body


def _url(rule) -> str:
    return re.sub(r"<(?:[^:<>]+:)?([^<>]+)>", "t1", rule.rule)


def _mutating_rules():
    for rule in api_server.app.url_map.iter_rules():
        methods = sorted((rule.methods or set()) & guard.METHODS)
        if methods and rule.endpoint != "static":
            yield rule, methods


def _files_with(root: str, needle: bytes) -> list[str]:
    found = []
    for folder, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(folder, name)
            try:
                with open(path, "rb") as fh:
                    if needle in fh.read():
                        found.append(path)
            except OSError:
                pass
    return found


@pytest.fixture
def walk(monkeypatch, tmp_path):
    """The real app. The model is spied on; the agent logs go to tmp_path."""
    seen: list[str] = []

    def _model(*a, **k):
        seen.append(json.dumps([a, k], default=str))
        return "a model answer"
    for name in ("_ollama_chat", "_query_ollama_direct"):
        monkeypatch.setattr(api_server, name, _model)
    monkeypatch.setattr(api_server, "_stream_ollama_tokens",
                        lambda *a, **k: iter([_model(*a, **k)]))
    monkeypatch.setattr(api_server, "_ACTIVITY_LOG",
                        str(tmp_path / "agent-activity.jsonl"))
    monkeypatch.setattr(api_server, "_TASK_QUEUE",
                        str(tmp_path / "task-queue.jsonl"))
    monkeypatch.setenv("PUFFPAI_AGENT_TEAM_ENABLED", "0")
    monkeypatch.setattr(api_server, "_rate_limit", lambda: None)
    api_server._rate_limit_store.clear()
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client(), seen


def test_the_canary_looks_like_a_key():
    assert web_search.looks_like_pasted_key(CANARY)


def test_every_route_that_takes_text_never_keeps_or_echoes_a_pasted_key(
        walk, capsys, caplog, tmp_path):
    client, seen = walk
    reached, refused, own = set(), [], []
    problems = []
    for rule, methods in _mutating_rules():
        view = api_server.app.view_functions[rule.endpoint]
        body = _body(rule.endpoint, view)
        for method in methods:
            response = client.open(_url(rule), method=method, headers=AUTH,
                                   json=body)
            text = response.get_data(as_text=True)
            reached.add(rule.endpoint)
            if CANARY in text:
                problems.append(f"{method} {rule.rule} echoed it")
            if rule.endpoint in guard.OWN_CHECK:
                own.append(rule.endpoint)
                continue
            if response.status_code != 400 or not (response.get_json(silent=True)
                                                   or {}).get("key_paste"):
                problems.append(f"{method} {rule.rule} was not refused "
                                f"({response.status_code})")
            else:
                refused.append(rule.endpoint)
    assert not problems, problems
    assert NAMED <= reached, NAMED - reached
    assert set(guard.OWN_CHECK) <= set(own)
    # Nothing printed, logged, kept or sent anywhere.
    out = capsys.readouterr()
    assert CANARY not in out.out and CANARY not in out.err
    assert CANARY not in caplog.text
    assert all(CANARY not in s for s in seen)
    for root in {str(tmp_path), os.environ["PUFFPAI_STATE_DIR"],
                 os.environ["PUFFPAI_DATA_DIR"],
                 api_server._get_chat_history_dir()}:
        assert not _files_with(root, CANARY.encode()), root
    with api_server._chat_history_lock:
        assert CANARY not in json.dumps(list(api_server._chat_history))
    assert len(refused) > 100


def test_the_agents_task_route_the_finding_named(walk, tmp_path, capsys):
    client, _ = walk
    r = client.post("/agents/task", headers=AUTH,
                    json={"task": f"use my key {CANARY} for the weather"})
    assert r.status_code == 400
    assert r.get_json()["error"] == guard.REFUSAL
    assert CANARY not in r.get_data(as_text=True)
    assert not (tmp_path / "agent-activity.jsonl").exists()
    assert not (tmp_path / "task-queue.jsonl").exists()
    printed = capsys.readouterr().out
    assert CANARY not in printed
    assert "agents_submit_task" in printed and "'task' field" in printed
    # An ordinary task still goes through, and is logged and queued.
    r = client.post("/agents/task", headers=AUTH,
                    json={"task": "check the garden lights tonight"})
    assert r.status_code == 200
    assert "garden lights" in (tmp_path / "task-queue.jsonl").read_text(encoding="utf-8")


def test_a_key_split_over_lines_is_refused_too(walk, tmp_path):
    client, _ = walk
    r = client.post("/agents/task", headers=AUTH,
                    json={"task": "AB12\r\nCD34\nEF56 \tGH78\nIJ90\nKL12\nMN34"})
    assert r.status_code == 400
    assert not (tmp_path / "task-queue.jsonl").exists()


@pytest.mark.parametrize("where", ["path", "query", "form"])
def test_a_key_in_the_address_or_a_form_is_refused(walk, where):
    client, _ = walk
    if where == "path":
        r = client.patch(f"/persona/{CANARY}", headers=AUTH, json={})
    elif where == "query":
        r = client.post(f"/agents/task?task={CANARY}", headers=AUTH,
                        json={"task": "hello"})
    else:
        r = client.post("/agents/task", headers=AUTH, data={"task": CANARY})
    assert r.status_code == 400 and r.get_json()["key_paste"] is True
    assert CANARY not in r.get_data(as_text=True)


def test_the_keys_pages_own_key_field_is_let_through():
    assert guard.first_key_field("keys.keys_add", {"value": CANARY}) is None
    # ... but not the page's other fields.
    assert guard.first_key_field("keys.keys_add",
                                 {"value": "x", "note": CANARY}) == "note"
    # The vault takes a secret as the value, not as the item's name.
    assert guard.first_key_field("vault_store", {"key": "wifi", "value": CANARY}) is None
    assert guard.first_key_field("vault_store", {"key": CANARY, "value": "x"}) == "key"
    # A secret field on one route is not a secret field on another.
    assert guard.first_key_field("agents_submit_task", {"value": CANARY}) == "value"


def test_fields_a_program_fills_in_are_not_read():
    for field in ("ticket", "id", "conversation_id", "session_token",
                  "image_b64", "audio_base64", "commit", "taskId", "eventIds",
                  "zim_dir", "source_path"):
        assert guard.first_key_field("agents_submit_task", {field: CANARY}) is None, field
    assert guard.first_key_field(
        "avatar_custom_upload", {"image": "data:image/png;base64," + CANARY}) is None
    # The calendar app's own data (ids made by the app) is not refused.
    assert guard.first_key_field("app_put_data", {"events": [
        {"id": "t1783671a9f", "taskId": "t1783671q8ZrT2vLx9KmW4pNc7",
         "title": "dentist"}]}) is None
    # A folder with a random id in its name is a folder.
    assert guard.first_key_field("setup_set_knowledge", {
        "zim_dir": r"C:\Temp\e6c71f1c-e0a3-4303-980f-e1ace328ad3f\packs"}) is None
    # Words that merely end in "id" are still read.
    assert guard.first_key_field("app_put_data", {"said": CANARY}) == "said"
    # ... but a key typed as a mapping's name is read (and never returned).
    assert guard.first_key_field("app_put_data",
                                 {"lists": {CANARY: []}}) == guard.KEY_IN_NAME


def test_a_body_nested_too_deep_is_refused(walk):
    client, _ = walk
    body: dict = {"task": "x"}
    for _ in range(40):
        body = {"inner": body}
    r = client.post("/agents/task", headers=AUTH, json=body)
    assert r.status_code == 400


def test_the_allow_list_is_exactly_the_places_a_secret_belongs():
    """Widening this list is a security decision: this test makes it one."""
    assert guard.SECRET_FIELDS == {
        ("keys.keys_add", "value"), ("vault_store", "value"),
        ("vault_unlock", "password"), ("vault_unlock", "pin"),
        ("vault_unlock", "recovery_code"),
        ("vault_recover", "code"), ("vault_recover", "recovery_code"),
        ("vault_recover", "new_password"), ("vault_recover", "new_pin"),
        ("setup_create_vault", "password"), ("setup_create_vault", "recovery_code"),
        ("setup_set_ha", "token"), ("setup_test_ha", "token"),
        ("mcp.mcp_home_assistant", "token"), ("mcp.mcp_add", "password"),
        # Vault place: the vault's own PIN (and the current one) on a switch
        # or a move, the same secret vault_unlock already takes.
        ("vault_place.vault_location_set", "pin"),
        ("vault_place.vault_location_set", "current_pin"),
        ("vault_place.vault_move", "pin"),
        # A skill's own key from its page, straight into the vault for that
        # skill only (tests/test_skill_vault_keys.py).
        ("things.thing_key", "value"),
        # Her GitHub token, straight into the vault
        # (tests/test_github_account.py).
        ("github_account.github_token", "token"),
        # Toolsmith's Anthropic key from Settings, straight into the vault
        # (tests/test_toolsmith_cloud.py).
        ("toolsmith_model.toolsmith_model_set", "key"),
    }
    endpoints = set(api_server.app.view_functions)
    assert {e for e, _ in guard.SECRET_FIELDS} <= endpoints
    assert set(guard.OWN_CHECK) <= endpoints


def test_the_refusal_is_plain_and_says_where_keys_go():
    assert "Sign-up keys page" in guard.REFUSAL
    assert not plain_words.check(guard.REFUSAL)


# ── the web server's own request log ────────────────────────────────────────

def _request_line(path: str, code: str = "400"):
    import logging
    return logging.LogRecord(
        "werkzeug", logging.INFO, __file__, 1,
        '127.0.0.1 - - [24/Sep/2026 15:32:52] "%s" %s %s',
        (f"POST {path} HTTP/1.1", code, "-"), None)


@pytest.mark.parametrize("path", [
    f"/agents/task?task={CANARY}",
    f"/knowledge/search?q=use+my+key+{CANARY}",
    "/agents/task?task=AB12%0D%0ACD34%0AEF56%20GH78%0AIJ90%0AKL12%0AMN34",
    f"/persona/{CANARY}",
])
def test_the_request_log_never_prints_an_address_with_a_key(path):
    import logging
    assert any(isinstance(f, guard.KeySafeRequestLog)
               for f in logging.getLogger("werkzeug").filters)
    record = _request_line(path)
    assert guard.KeySafeRequestLog().filter(record) is True   # kept, hidden
    line = record.getMessage()
    assert line == guard.HIDDEN_REQUEST_LINE
    assert CANARY not in line and "EF56" not in line


def test_an_ordinary_request_line_is_logged_as_it_was():
    record = _request_line("/agents/task", "200")
    guard.KeySafeRequestLog().filter(record)
    assert record.getMessage() == (
        '127.0.0.1 - - [24/Sep/2026 15:32:52] "POST /agents/task HTTP/1.1" 200 -')


@pytest.mark.parametrize("styled", [
    "\x1b[1m\x1b[31mPOST /agents/task HTTP/1.1\x1b[0m",       # a 400, bold red
    "\x1b[33mGET /missing HTTP/1.1\x1b[0m",                   # a 404, yellow
])
def test_a_coloured_request_line_is_not_taken_for_a_password(styled):
    import logging
    record = logging.LogRecord("werkzeug", logging.INFO, __file__, 1,
                               '127.0.0.1 - - [24/Sep/2026 15:32:52] "%s" %s %s',
                               (styled, "400", "-"), None)
    guard.KeySafeRequestLog().filter(record)
    assert record.getMessage() != guard.HIDDEN_REQUEST_LINE


def test_a_thing_page_history_with_an_id_the_bot_printed_is_not_a_pasted_key():
    """The bot's own earlier reply (a skill printed a spool's id) comes back as
    page history; it is not her paste. Her own turn is still read."""
    body = {"text": "What filament do I have?",
            "history": [{"user": "Add a spool: Polymaker PETG",
                         "assistant": "Added spool a06bfe8e-4448-4b52-96bd-3ea23b743cde."}]}
    assert guard.first_key_field("things.thing_ask", body) is None
    pasted = {"text": "hi", "history": [{"user": "sk-live-9f8e7d6c5b4a39281706f5e4d3c2b1a0",
                                         "assistant": "ok"}]}
    assert guard.first_key_field("things.thing_ask", pasted) is not None
