"""Using a look: find -> use -> undo -> reset, over the routes and the terminal.
Ids only (colours in a request are ignored), plain words, zero outside requests.
"""

from __future__ import annotations

import argparse
import io
import json
import os

import pytest

from puffpai import api_server, plain_words
from puffpai.looks import cli as looks_cli
from puffpai.looks import store
from puffpai.security import egress_ledger

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}


@pytest.fixture
def client():
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client()


def test_every_looks_route_needs_the_key(client):
    for method, path in (("get", "/looks"), ("post", "/looks/find"),
                         ("post", "/looks/use"), ("post", "/looks/undo"),
                         ("post", "/looks/reset")):
        assert getattr(client, method)(path).status_code == 401, path


def test_find_use_undo_reset_over_the_routes(client):
    body = client.get("/looks", headers=AUTH).get_json()
    assert body["current"] is None and body["can_undo"] is False
    found = client.post("/looks/find", headers=AUTH,
                        json={"words": "dinosaur jungle colours"}).get_json()
    first = found["looks"][0]
    assert first["id"] == "tweakcn:kodama-grove" and first["kind"] == "list"
    assert set(first["vars"]) == {"day", "night"}

    used = client.post("/looks/use", headers=AUTH, json={"id": first["id"]}).get_json()
    assert used["ok"] and used["current"]["id"] == "tweakcn:kodama-grove"
    assert used["can_undo"] is True

    again = client.post("/looks/use", headers=AUTH,
                        json={"id": "tweakcn:cosmic-night"}).get_json()
    assert again["current"]["id"] == "tweakcn:cosmic-night"
    back = client.post("/looks/undo", headers=AUTH).get_json()
    assert back["current"]["id"] == "tweakcn:kodama-grove"
    reset = client.post("/looks/reset", headers=AUTH).get_json()
    assert reset["current"] is None and reset["can_undo"] is True
    assert client.post("/looks/undo", headers=AUTH).get_json()["current"]["id"] == \
        "tweakcn:kodama-grove"
    for text in (used["message"], back["message"], reset["message"],
                 body["pages_line"], body["pages_own"]):
        assert not plain_words.check(text), text


def test_colours_in_a_request_are_ignored_and_unknown_ids_refused(client):
    r = client.post("/looks/use", headers=AUTH, json={
        "id": "tweakcn:nature",
        "day": {"bg": "url(https://evil.test/x)"}, "vars": {"--bg": "red"}})
    body = r.get_json()
    assert r.status_code == 200
    assert "evil" not in json.dumps(body) and body["current"]["vars"]["day"]["--bg"] != "red"
    for bad in ("tweakcn:nope", "made:0123456789ab", "../../etc", 42, None, "x" * 500):
        r = client.post("/looks/use", headers=AUTH, json={"id": bad})
        assert r.status_code == 404 and r.get_json()["ok"] is False


def test_a_made_look_can_be_used_only_after_it_was_found(client):
    found = client.post("/looks/find", headers=AUTH,
                        json={"words": "dark green and orange"}).get_json()
    made = [f for f in found["looks"] if f["kind"] == "made"][0]
    assert client.post("/looks/use", headers=AUTH, json={"id": made["id"]}).status_code == 200


def test_find_refuses_empty_or_long_words(client):
    assert client.post("/looks/find", headers=AUTH, json={"words": ""}).status_code == 400
    assert client.post("/looks/find", headers=AUTH,
                       json={"words": "x" * 201}).status_code == 400
    nothing = client.post("/looks/find", headers=AUTH,
                          json={"words": "a banana"}).get_json()
    assert nothing["looks"] == [] and not plain_words.check(nothing["note"])


def test_a_tampered_current_file_is_not_trusted(client):
    client.post("/looks/use", headers=AUTH, json={"id": "tweakcn:nature"})
    path = os.path.join(store._dir(), "current.json")
    data = json.load(open(path, encoding="utf-8"))
    data["look"]["day"]["bg"] = "url(https://evil.test/x)"
    json.dump(data, open(path, "w", encoding="utf-8"))
    assert client.get("/looks", headers=AUTH).get_json()["current"] is None


def test_history_keeps_ten():
    ids = ["tweakcn:nature", "tweakcn:cosmic-night"] * 7
    for look_id in ids:
        store.use(look_id)
    rows = json.load(open(os.path.join(store._dir(), "history.json"), encoding="utf-8"))
    assert len(rows) == store.HISTORY_MAX


def test_no_outside_request_while_finding_and_using(client, monkeypatch):
    before = egress_ledger.read(hours=1)
    for words in ("outer space", "dinosaur jungle colours", "cozy yarn shop",
                  "clean and dark"):
        client.post("/looks/find", headers=AUTH, json={"words": words})
    client.post("/looks/use", headers=AUTH, json={"id": "tweakcn:cosmic-night"})
    client.post("/looks/undo", headers=AUTH)
    assert egress_ledger.read(hours=1) == before


# -- the terminal ----------------------------------------------------------------

def _run(action, *words, brain=False):
    out = io.StringIO()
    args = argparse.Namespace(looks_action=action, words=list(words), no_brain=not brain)
    code = looks_cli.cmd_looks(args, out=out)
    return code, out.getvalue()


def test_the_terminal_round_trip_in_plain_words():
    code, shown = _run("show")
    assert code == 0 and "its own look" in shown
    code, found = _run("find", "outer", "space")
    assert code == 0 and found.splitlines()[1].startswith(" 1  Cosmic Night:")
    assert "puffpai looks use <number>" in found
    code, used = _run("use", "1")
    assert code == 0 and "Cosmic Night" in used and "Reload" in used
    code, shown = _run("show")
    assert "Cosmic Night" in shown and "puffpai looks undo" in shown
    code, undone = _run("undo")
    assert code == 0 and "its own look" in undone
    code, bad = _run("use", "9")
    assert code == 2 and "Pick a number" in bad
    code, reset = _run("reset")
    assert code == 0 and "already" in reset and "Reload" not in reset
    for text in (found, used, shown, undone, bad, reset):
        assert not plain_words.check(text), text
        assert "tweakcn" not in text.lower() and "shadcn" not in text.lower()


def test_the_parser_is_wired_into_puffpai():
    from puffpai import cli
    args = cli.build_parser().parse_args(["looks", "find", "clean", "and", "dark"])
    assert args.looks_action == "find" and args.words == ["clean", "and", "dark"]


def test_the_looks_page_words_are_plain():
    import re
    from pathlib import Path
    page = (Path(__file__).resolve().parents[1] / "dashboard" / "app" / "dashboard"
            / "looks" / "page.tsx").read_text(encoding="utf-8")
    body = page.split("export default function", 1)[1]
    texts = re.findall(r">\s*([^<>{}\n][^<>{}]*?)\s*<", body)
    texts += re.findall(r'(?:label|lede|title)="([^"]+)"', page)
    for braced in re.findall(r'label=\{([^}\n]*)\}', page):
        texts += re.findall(r'"([^"]+)"', braced)
    flat = list(texts)
    assert any("Keep it" in t for t in flat) and any("Try it on" in t for t in flat)
    bad = [v for t in flat for v in plain_words.check(t)]
    assert bad == []
