"""The /wizard/* routes: the dashboard's half of "one card set, two renderers".

Three questions only, because the rules live in the runner and are tested
there: is the gate the same as every neighbour, is GET really read-only, and
does a bad answer come back as something a caller can act on rather than as a
gap in the owner's build. Plus the one that matters most: no route echoes a
secret.

Run:
    pytest tests/test_wizard_api.py
"""
from __future__ import annotations

import os
import tempfile

# Isolate on-disk state BEFORE importing the app: module-level singletons in
# api_server resolve their paths at import time (same pattern as test_api.py).
_TMP_DATA_DIR = tempfile.mkdtemp(prefix="puffpai_wizard_api_test_")
os.environ["PUFFPAI_DATA_DIR"] = _TMP_DATA_DIR
os.environ.setdefault("PUFFPAI_API_KEY", "b" * 64)

import copy  # noqa: E402
import json  # noqa: E402

import pytest  # noqa: E402

from puffpai import api_server, hardware_census, setup_ops  # noqa: E402
from puffpai.config import get_config  # noqa: E402
from puffpai.wizard import service  # noqa: E402

API_KEY = api_server._API_KEY

_ROUTES = [("get", "/wizard/cards"), ("post", "/wizard/apply"),
           ("post", "/wizard/run"), ("get", "/wizard/state"),
           ("post", "/wizard/resume"),
           # The conversation: same gate, no exceptions.
           ("get", "/wizard/flow"), ("post", "/wizard/summary"),
           ("post", "/wizard/approve"), ("post", "/wizard/vault/skip"),
           ("get", "/wizard/build"), ("post", "/wizard/build")]


def _auth():
    return {"Authorization": f"Bearer {API_KEY}"}


@pytest.fixture
def client():
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client()


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """An isolated data dir and a machine that does not move."""
    prev = os.environ.get("PUFFPAI_DATA_DIR")
    os.environ["PUFFPAI_DATA_DIR"] = str(tmp_path)
    get_config(_force_reload=True)
    census = hardware_census.run_census()
    monkeypatch.setattr(hardware_census, "run_census",
                        lambda *a, **k: copy.deepcopy(census))
    monkeypatch.setattr(setup_ops, "list_models", lambda *a, **k: ["qwen2.5:7b"])
    monkeypatch.setattr(setup_ops, "ollama_survey", lambda *a, **k: [])
    try:
        yield tmp_path
    finally:
        if prev is not None:
            os.environ["PUFFPAI_DATA_DIR"] = prev
        else:
            os.environ.pop("PUFFPAI_DATA_DIR", None)
        get_config(_force_reload=True)


# -- the gate -----------------------------------------------------------------

@pytest.mark.parametrize("method,path", _ROUTES)
def test_every_route_requires_the_app_api_key(client, method, path):
    assert getattr(client, method)(path).status_code == 401


@pytest.mark.parametrize("method,path", _ROUTES)
def test_the_api_key_is_accepted_on_every_route(client, scratch, method, path):
    resp = getattr(client, method)(path, headers=_auth(), json={})
    assert resp.status_code != 401


def test_the_dashboard_proxy_is_allowed_to_reach_these():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "dashboard", "app", "_lib", "server",
                        "proxyAllowlist.ts")
    with open(path, encoding="utf-8") as handle:
        assert '"wizard"' in handle.read()


# -- reading is reading -------------------------------------------------------

def test_cards_are_ordered_and_change_nothing(client, scratch):
    before = sorted(os.listdir(get_config().CONFIG_DIR)) if os.path.isdir(
        get_config().CONFIG_DIR) else []
    resp = client.get("/wizard/cards", headers=_auth())
    assert resp.status_code == 200
    body = resp.get_json()
    # The order is the runner's schedule, carried explicitly, with the part
    # for the skill that asks three questions.
    from puffpai.wizard import runner as wizard_runner
    scheduled = [f"{step.skill}:{part}" if part else step.skill
                 for step in wizard_runner.SCHEDULE
                 for part in [wizard_runner.lifecycle_part(step.part)]
                 if part is not None and step.skill]
    assert body["order"] == scheduled
    assert {"secure:posture", "secure:vault", "secure:bridge"} <= set(
        body["order"]), "only one secure part is offered"
    assert [row["skill"] for row in body["not_converted"]] == ["assemble", "prove"]
    assert body["not_converted"][0]["title"]
    titles = {card["skill"]: card["title"] for card in body["cards"]}
    assert titles["discover"] == "This computer"
    assert body["refusals"] == []
    after = sorted(os.listdir(get_config().CONFIG_DIR)) if os.path.isdir(
        get_config().CONFIG_DIR) else []
    assert before == after, "a read-only route wrote something"


def test_state_answers_before_anything_has_run(client, scratch):
    body = client.get("/wizard/state", headers=_auth()).get_json()
    assert body["found"] is False
    assert body["order"][0] == "discover"
    assert body["skills"]["discover"]["outcome"] == "not_run"


# -- applying -----------------------------------------------------------------

def test_apply_validates_applies_and_reports_what_went_stale(client, scratch):
    # The address, not the memory: what this machine reported about itself is
    # an ``info`` row now, and the one thing on this card a person can know
    # better than the census is where Ollama answers.
    resp = client.post("/wizard/apply", headers=_auth(),
                       json={"skill": "discover",
                             "answers": {"ollama_url": "http://127.0.0.1:11435"}})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["changed"] == {"ollama_url": "http://127.0.0.1:11435"}
    assert body["stale"] == ["integrate", "brain", "prove", "handoff"]
    assert body["card"]["skill"] == "discover"
    assert body["verified"]["status"] in ("pass", "gap")

    state = client.get("/wizard/state", headers=_auth()).get_json()
    assert state["found"] is True
    assert state["skills"]["discover"]["outcome"] == "done"


@pytest.mark.parametrize("payload,code", [
    ({}, 400),
    ({"skill": "nothing_like_this"}, 404),
    # Secure asks three questions; naming it without saying which would apply
    # whichever one the run happened to be up to.
    ({"skill": "secure"}, 404),
    ({"skill": "secure:nothing_like_this"}, 404),
    ({"skill": "secure:vault", "answers": {"no_such_field": 1}}, 400),
    ({"skill": "discover", "answers": {"no_such_field": 1}}, 400),
    ({"skill": "discover", "answers": {"memory_gb": "lots"}}, 400),
    ({"skill": "discover", "answers": {"machine": "rewritten"}}, 400),
])
def test_an_answer_this_card_did_not_ask_for_is_a_refusal(client, scratch,
                                                          payload, code):
    resp = client.post("/wizard/apply", headers=_auth(), json=payload)
    assert resp.status_code == code
    assert resp.get_json()["success"] is False
    assert resp.get_json()["message"]


def test_a_refused_answer_records_no_gap_in_the_build(client, scratch):
    client.post("/wizard/apply", headers=_auth(),
                json={"skill": "discover", "answers": {"memory_gb": "lots"}})
    state = client.get("/wizard/state", headers=_auth()).get_json()
    assert state["gaps"] == []


# -- secrets ------------------------------------------------------------------

def test_no_route_returns_a_secret_entry_value(client, scratch, monkeypatch):
    """A card never carries one, and an answer to one is never echoed back."""
    from puffpai.wizard import cards

    card = {"skill": "discover", "section": "machine", "title": "A PIN",
            "decided": "", "status": "needs_you", "consequential": False,
            "estimate": {"bytes": 0, "seconds": 0, "basis": "measured"},
            "fields": [{"id": "vault_pin", "kind": "secret_entry",
                        "label": "Vault PIN"}]}
    monkeypatch.setattr(service, "propose_one",
                        lambda *a, **k: cards.validate_card(card))
    resp = client.post("/wizard/apply", headers=_auth(),
                       json={"skill": "discover",
                             "answers": {"vault_pin": "correct-horse-1"}})
    assert resp.status_code == 200
    assert b"correct-horse-1" not in resp.data
    assert resp.get_json()["changed"] == {
        "vault_pin": "(entered, not recorded)"}


# -- which field a refusal is about (spec D.2) --------------------------------

def test_a_refusal_names_the_field_it_is_about(client, scratch):
    resp = client.post("/wizard/apply", headers=_auth(),
                       json={"skill": "discover",
                             "answers": {"memory_gb": "lots"}})
    body = resp.get_json()
    assert resp.status_code == 400
    assert body["field"] == "memory_gb"
    # The message is the one the card raised, not a reworded one.
    assert "memory_gb" in body["message"]


def test_a_refusal_about_no_particular_field_names_none(client, scratch):
    body = client.post("/wizard/apply", headers=_auth(),
                       json={"skill": 12}).get_json()
    assert body["field"] is None


# -- accept the rest (spec D.1, E.3) ------------------------------------------

def _run(client, acknowledged=()):
    return client.post("/wizard/run", headers=_auth(),
                       json={"acknowledged": list(acknowledged)})


def _results(body):
    return {row["card"]: row for row in body["results"]}


def test_run_applies_the_light_rows_and_refuses_the_heavy_ones(client, scratch):
    body = _run(client).get_json()
    assert body["success"] is True
    rows = _results(body)

    # The vault is never sealed on the owner's behalf: it is consequential,
    # nobody acknowledged it, and over HTTP there is no face to show a recovery
    # code to anyway.
    assert rows["secure:vault"]["applied"] is False
    assert rows["secure:vault"]["outcome"] == "needs_you"
    assert rows["secure:vault"]["why"]

    # Something was applied: this is "accept the rest", not "refuse the lot".
    assert any(row["applied"] for row in rows.values())
    for row in rows.values():
        if not row["applied"]:
            assert row["why"], row


def test_run_never_accepts_a_consequential_row_that_was_not_acknowledged(
        client, scratch):
    offered = client.get("/wizard/cards", headers=_auth()).get_json()
    heavy = [card for card in offered["cards"] if card["consequential"]]
    ids = [f"{c['skill']}:{c['part']}" if c["part"] else c["skill"]
           for c in heavy]
    assert ids, "this build proposed no consequential row to test with"

    rows = _results(_run(client).get_json())
    for card_id in ids:
        row = rows.get(card_id)
        if row is None:          # never reached in schedule order
            continue
        assert row["applied"] is False, f"{card_id} was accepted unread"
        assert row["outcome"] == "needs_you"


def test_an_acknowledged_id_this_build_does_not_have_is_refused(client, scratch):
    for bad in ("nothing_like_this", "secure:nothing", "discover:x", 17,
                "../../etc/passwd"):
        resp = _run(client, [bad])
        assert resp.status_code == 400, bad
        assert resp.get_json()["success"] is False


def test_only_one_run_at_a_time(client, scratch):
    from puffpai.wizard import service as wizard_service

    cfg = get_config()
    held = wizard_service._take_lock(cfg)
    try:
        resp = _run(client)
        assert resp.status_code == 409
        assert resp.get_json()["success"] is False
    finally:
        wizard_service._drop_lock(held)
    # And the lock is gone afterwards, so the next run is not wedged.
    assert _run(client).status_code == 200


def test_a_finished_run_leaves_no_lock_behind(client, scratch):
    assert _run(client).status_code == 200
    from puffpai.wizard import service as wizard_service
    assert not os.path.exists(wizard_service._lock_path(get_config()))


def test_run_does_not_accept_answers_at_all(client, scratch):
    """No answers on this route, so no secret can arrive on it."""
    body = client.post("/wizard/run", headers=_auth(),
                       json={"acknowledged": [],
                             "answers": {"pin": "1234567890"}}).get_json()
    assert body["success"] is True
    assert "1234567890" not in json.dumps(body)


# -- the attempt limiter (spec E.2) -------------------------------------------

def _secret_card(monkeypatch):
    """A card that asks for a secret, whatever this build's cards ask today.

    No built-in card carries a ``secret_entry`` over HTTP right now -- the
    vault card refuses to from a screen with no show-once face, and the Home
    Assistant token is not offered on this path. The limiter exists for the day
    one does (spec A.5's credential path), so the test builds the situation
    rather than waiting for it.
    """
    from puffpai.wizard import cards

    card = {"skill": "discover", "section": "machine", "title": "A token",
            "decided": "", "status": "needs_you", "consequential": False,
            "estimate": {"bytes": 0, "seconds": 0, "basis": "measured"},
            "fields": [{"id": "hub_token", "kind": "secret_entry",
                        "label": "Long-lived access token"}]}
    monkeypatch.setattr(service, "propose_one",
                        lambda *a, **k: cards.validate_card(card))


def test_answers_carrying_a_secret_are_rate_limited(client, scratch,
                                                    monkeypatch):
    from puffpai import api_server as server

    _secret_card(monkeypatch)
    server._wizard_secret_store.clear()
    payload = {"skill": "discover", "answers": {"hub_token": "not-a-real-one"}}
    codes = [client.post("/wizard/apply", headers=_auth(),
                         json=payload).status_code
             for _ in range(server._WIZARD_SECRET_LIMIT + 2)]
    assert 429 in codes, codes
    assert codes[0] != 429, "the first attempt was refused"
    assert codes.count(429) == 2, codes
    server._wizard_secret_store.clear()


def test_a_limited_attempt_never_echoes_what_was_sent(client, scratch,
                                                      monkeypatch):
    from puffpai import api_server as server

    _secret_card(monkeypatch)
    server._wizard_secret_store.clear()
    payload = {"skill": "discover", "answers": {"hub_token": "swordfish-42"}}
    last = None
    for _ in range(server._WIZARD_SECRET_LIMIT + 2):
        last = client.post("/wizard/apply", headers=_auth(), json=payload)
    assert last.status_code == 429
    assert b"swordfish-42" not in last.data
    server._wizard_secret_store.clear()


def test_the_limiter_guards_a_real_card_and_not_only_a_stubbed_one(client,
                                                                   scratch):
    """Item 7: the hub question is asked over HTTP, so the limiter is live.

    Every other test in this section stubs a secret field onto a card, because
    no HTTP-served card had one -- the tools card refused to ask on a
    non-interactive run and the vault card refuses by design, so the limiter
    guarded nothing on a real build. The dashboard's own Smart home step has
    always taken a Home Assistant URL and token (POST /setup/ha); the card asks
    for the same two now, the toggle first and the token second.
    """
    from puffpai import api_server as server

    server._wizard_secret_store.clear()
    opened = client.post("/wizard/apply", headers=_auth(),
                         json={"skill": "tools",
                               "answers": {"connect_hub": True}})
    assert opened.status_code == 200
    token_field = [field for field in opened.get_json()["card"]["fields"]
                   if field["kind"] == "secret_entry"]
    assert token_field, "the card never asks for the token"
    assert token_field[0]["current"] == "" and token_field[0]["proposed"] == ""

    payload = {"skill": "tools",
               "answers": {"connect_hub": True, "hub_url": "127.0.0.1:8123",
                           "hub_token": "swordfish-42"}}
    codes = [client.post("/wizard/apply", headers=_auth(),
                         json=payload).status_code
             for _ in range(server._WIZARD_SECRET_LIMIT + 2)]
    assert 429 in codes, codes
    last = client.post("/wizard/apply", headers=_auth(), json=payload)
    assert last.status_code == 429
    assert b"swordfish-42" not in last.data
    state = client.get("/wizard/state", headers=_auth())
    assert b"swordfish-42" not in state.data
    server._wizard_secret_store.clear()


def test_run_skips_a_card_that_asks_for_a_secret(client, scratch, monkeypatch):
    """Nobody typed it, so accepting it would record a decision nobody made."""
    _secret_card(monkeypatch)
    rows = _results(_run(client).get_json())
    assert rows["discover"]["applied"] is False
    assert "only you can type" in rows["discover"]["why"]


def test_an_answer_with_no_secret_in_it_is_not_limited(client, scratch):
    from puffpai import api_server as server

    server._wizard_secret_store.clear()
    codes = [client.post("/wizard/apply", headers=_auth(),
                         json={"skill": "discover",
                               "answers": {"ollama_url": "127.0.0.1:11435"}}
                         ).status_code
             for _ in range(server._WIZARD_SECRET_LIMIT + 3)]
    assert 429 not in codes, codes


# -- no face over HTTP (spec E.1) ---------------------------------------------

def test_an_http_apply_has_no_face_even_after_a_console_render(client, scratch):
    """The show-once face is per run. A server cannot inherit one.

    A console render in this same process used to install one for the rest of
    the process, which would have made the vault card believe somebody was
    watching a browser request.
    """
    from puffpai import cli as puffpai_cli
    from puffpai.security.vault import Vault
    from puffpai.wizard import render_cli

    card = {"skill": "secure", "section": "posture", "part": "vault",
            "title": "Zone 1", "decided": "", "status": "ready",
            "consequential": False,
            "estimate": {"bytes": 0, "seconds": 0, "basis": "measured"},
            "fields": [{"id": "note", "kind": "info", "label": "Note",
                        "current": "nothing"}]}
    render_cli.render(card, non_interactive=True, out=lambda line: None)

    body = client.get("/wizard/cards", headers=_auth()).get_json()
    vault = next(c for c in body["cards"]
                 if c["skill"] == "secure" and c["part"] == "vault")
    assert vault["status"] == "needs_you"
    assert "next screen" in vault["decided"]
    assert not any(f["kind"] == "secret_entry" for f in vault["fields"])

    resp = client.post("/wizard/apply", headers=_auth(),
                       json={"skill": "secure:vault", "answers": {}})
    assert resp.status_code == 200
    sealed = Vault(vault_dir=puffpai_cli._vault_dir(get_config())).is_initialized
    assert sealed is False, "an HTTP apply created a vault with no way back in"


# -- /wizard/resume -----------------------------------------------------------

def _stopped_record(cfg) -> str:
    """A run record that stopped part-way, written the way the runner writes it."""
    from puffpai.wizard import runner as wizard_runner
    import time

    os.makedirs(cfg.CONFIG_DIR, exist_ok=True)
    path = os.path.join(cfg.CONFIG_DIR, "wizard_run.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({
            "schema": wizard_runner.STATE_SCHEMA,
            "started_at": time.time() - 120,
            "entries": {"secure.posture": {"outcome": "done", "at": 1.0},
                        "discover.card": {"outcome": "done", "at": 2.0}},
            "log": [{"entry": "discover.card", "skill": "discover",
                     "part": "card", "outcome": "done", "started_at": 1.0,
                     "finished_at": 2.0,
                     "shown": {"title": "This computer"}}],
            "gaps": [], "sections": {}, "shared": {}}, handle)
    return path


def test_the_state_route_says_a_stopped_setup_is_there_to_continue(client,
                                                                   scratch):
    _stopped_record(get_config())
    body = client.get("/wizard/state", headers=_auth()).get_json()
    offer = body["resume"]
    assert offer["offer"] is True
    assert offer["stopped_card"], "the banner has to say where it stopped"
    assert any(row["card"] == "discover" for row in offer["decided"])
    assert "This computer" in [row["title"] for row in offer["decided"]]


def test_no_stopped_setup_is_an_offer_of_nothing(client, scratch):
    body = client.get("/wizard/state", headers=_auth()).get_json()
    assert body["resume"] == {"offer": False, "why": "There is no stopped setup to continue."}


def test_resume_refuses_a_choice_it_does_not_understand(client, scratch):
    _stopped_record(get_config())
    resp = client.post("/wizard/resume", headers=_auth(),
                       json={"choice": "whatever"})
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["success"] is False and body["field"] == "choice"
    assert "continue" in body["message"]


def test_resume_restart_sets_the_record_aside_without_deleting_it(client,
                                                                  scratch):
    path = _stopped_record(get_config())
    resp = client.post("/wizard/resume", headers=_auth(),
                       json={"choice": "restart"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True and body["resumed"] is False
    assert body["archived"] is True
    assert not os.path.exists(path)
    assert any(name.startswith("wizard_run.json.")
               for name in os.listdir(os.path.dirname(path)))
    # And the banner is gone.
    state = client.get("/wizard/state", headers=_auth()).get_json()
    assert state["resume"]["offer"] is False


def test_resume_continue_asks_the_vault_again_when_there_is_no_vault_on_disk(
        client, scratch):
    """The server applies the same rule the console does, for the same reason.

    The record below says the vault step finished. There is no vault on this
    disk, so it is asked again -- the file's word is not a vault.
    """
    from puffpai.wizard import runner as wizard_runner
    import time

    cfg = get_config()
    os.makedirs(cfg.CONFIG_DIR, exist_ok=True)
    path = os.path.join(cfg.CONFIG_DIR, "wizard_run.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({
            "schema": wizard_runner.STATE_SCHEMA,
            "started_at": time.time() - 120,
            "entries": {"secure.posture": {"outcome": "done", "at": 1.0},
                        "discover.card": {"outcome": "done", "at": 2.0},
                        "secure.vault": {"outcome": "done", "at": 3.0}},
            "log": [], "gaps": [], "sections": {}, "shared": {}}, handle)

    resp = client.post("/wizard/resume", headers=_auth(),
                       json={"choice": "continue"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["resumed"] is True
    assert "secure:vault" in body["reasked"]

    with open(path, encoding="utf-8") as handle:
        entries = json.load(handle)["entries"]
    assert "secure.vault" not in entries, "the vault is unanswered again"
    assert "discover.card" in entries, "a settled non-blocking card is kept"
