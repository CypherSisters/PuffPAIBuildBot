"""The leak test: a random 40-character canary key, through a LOCAL stub vendor.

Not DEMO_KEY (it is in public docs and fixtures, so grepping for it proves
nothing). The canary is made fresh for this run, stored through the terminal
and the routes, used by chat searches (streamed and not), echoed back by the
vendor, refused by the vendor, broken by a transport error that names it, and
pasted into chat. Then the whole state folder, every log (DEBUG on, urllib3
included), the outside-traffic log, the saved chats, the config folder, every
response body and all captured output are searched for it: zero hits. The
vault files hold it only as ciphertext, so they are searched too.
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys

import pytest

from keys_support import (PIN, canary, fast_vault, grep_tree, install_vendor,
                          machine_key_file, reset_keys, set_posture)
from puffpai import api_server, cli
from puffpai.config import get_config, get_state_dir
from puffpai.security import egress_doors, egress_ledger, keys

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}
DEV = {**AUTH, "X-Puff-Device-Id": "deviceleak000001"}


@pytest.fixture
def world(monkeypatch, tmp_path):
    for d in egress_doors.DOORS:
        monkeypatch.delenv(d.env_var, raising=False)
    reset_keys()
    set_posture("enumerated")
    vault_dir = cli._vault_dir(get_config())  # noqa: SLF001 - the one the CLI uses
    v = fast_vault(monkeypatch, vault_dir)
    monkeypatch.setattr(api_server, "_vault_instance", v)
    keys.set_vault_provider(lambda: v)
    machine_key_file(monkeypatch, tmp_path)
    log_dir = os.path.join(get_state_dir(create=True), "logs")
    os.makedirs(log_dir, exist_ok=True)
    handler = logging.FileHandler(os.path.join(log_dir, "debug.log"),
                                  encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    for name in ("urllib3", "requests", "puffpai", "werkzeug"):
        logging.getLogger(name).setLevel(logging.DEBUG)
    api_server.app.config.update(TESTING=True)
    yield {"vault": v, "client": api_server.app.test_client(),
           "log_dir": log_dir, "vault_dir": vault_dir}
    root.removeHandler(handler)
    handler.close()
    root.setLevel(old_level)
    reset_keys()


def test_the_canary_key_leaves_no_trace(world, monkeypatch, capsys):
    brave_key = canary(40)
    marg_key = canary(40)
    assert len(brave_key) == 40 and brave_key != marg_key
    vendor = install_vendor(monkeypatch)
    vendor.accept("X-Subscription-Token", brave_key)
    vendor.accept("API-Key", marg_key)
    client = world["client"]
    monkeypatch.setattr(api_server, "_web_search_model",
                        lambda messages: "An answer from the results.")
    bodies: list[str] = []

    # 1. The terminal: `puffpai keys add marginalia_search --stdin --yes`.
    world["vault"].lock()
    monkeypatch.setattr(sys, "stdin", io.StringIO(f"{marg_key}\n{PIN}\n"))
    assert cli.main(["keys", "add", "marginalia_search", "--stdin", "--yes",
                     "--keep-working-when-locked"]) == 0
    world["vault"].unlock(PIN)

    # 2. The page: confirm, then paste (Brave, a card-backed key).
    minted = client.post("/keys/brave_search/confirm", headers=DEV, json={})
    bodies.append(minted.get_data(as_text=True))
    added = client.post("/keys/brave_search", headers=DEV,
                        json={"value": brave_key,
                              "ticket": minted.get_json()["ticket"]})
    bodies.append(added.get_data(as_text=True))
    assert added.status_code == 200, added.get_json()

    # 3. Chat searches: plain, streamed, with an echoing vendor.
    for message in ("search the web for comets", "look up comets online"):
        r = client.post("/chat/send", headers=AUTH,
                        json={"message": message, "conversation_id": "leak01"})
        bodies.append(r.get_data(as_text=True))
    vendor.echo = True
    r = client.post("/v1/chat/completions", headers=AUTH,
                    json={"model": "puffpai", "stream": True,
                          "messages": [{"role": "user",
                                        "content": "search the web for moons"}]})
    bodies.append(r.get_data(as_text=True))
    vendor.echo = False
    # Locked vault: Brave pauses, Marginalia (ticked, free) carries on.
    world["vault"].lock()
    r = client.post("/chat/send", headers=AUTH,
                    json={"message": "search the web for asteroids",
                          "conversation_id": "leak02"})
    bodies.append(r.get_data(as_text=True))
    assert "Brave search is paused" in r.get_json()["response"]
    assert "I searched Marginalia for: asteroids" in r.get_json()["response"]
    world["vault"].unlock(PIN)

    # 4. A transport error that names the key, then a refusal.
    vendor.fail_with = ConnectionError(f"boom {brave_key}")
    r = client.post("/chat/send", headers=AUTH,
                    json={"message": "search the web for planets",
                          "conversation_id": "leak03"})
    bodies.append(r.get_data(as_text=True))
    vendor.fail_with = None
    vendor.good.pop("X-Subscription-Token")
    r = client.post("/keys/brave_search/check", headers=DEV)
    bodies.append(r.get_data(as_text=True))

    # 5. The key pasted into chat by mistake.
    r = client.post("/chat/send", headers=AUTH,
                    json={"message": f"my brave api key is {brave_key}",
                          "conversation_id": "leak04"})
    bodies.append(r.get_data(as_text=True))
    assert "Please do not paste keys" in r.get_json()["response"]

    # 6. The list, then remove both.
    bodies.append(client.get("/keys", headers=AUTH).get_data(as_text=True))
    bodies.append(client.post("/keys/brave_search/remove",
                              headers=DEV).get_data(as_text=True))
    assert cli.main(["keys", "remove", "marginalia_search"]) == 0

    # The vendor really did receive the keys (the test is not vacuous).
    sent = vendor.header_values()
    assert brave_key in sent and marg_key in sent

    captured = capsys.readouterr()
    for key in (brave_key, marg_key):
        for body in bodies:
            assert key not in body
        assert key not in captured.out and key not in captured.err
        roots = [get_state_dir(), world["log_dir"], get_config().CONFIG_DIR,
                 api_server._get_chat_history_dir(), world["vault_dir"],
                 os.path.dirname(egress_ledger.log_path())]
        for root in roots:
            assert grep_tree(root, key) == [], root
    with open(egress_ledger.log_path(), "r", encoding="utf-8") as fh:
        ledger = fh.read()
    assert "api.search.brave.com" in ledger and "comets" not in ledger
    with open(os.path.join(world["log_dir"], "debug.log"), "r",
              encoding="utf-8") as fh:
        assert fh.read()  # DEBUG logging really ran
    saved = json.dumps([m for c in api_server._chat_history
                        for m in c.get("messages", [])])
    assert brave_key not in saved and marg_key not in saved
