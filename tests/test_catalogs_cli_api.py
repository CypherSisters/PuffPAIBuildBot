"""The owner-facing ends of discovery: ``puffpai catalogs`` and GET /catalogs/discover.

* ``status`` and ``search`` never go online and speak plain words;
* ``refresh`` names each outside service before going online, asks first, and
  sends nothing on a "no" or when the build was set to stay offline;
* the HTTP route is behind the app key, offline, and refuses oversize input.

Run:
    pytest tests/test_catalogs_cli_api.py
"""
from __future__ import annotations

import os
import re
import tempfile

_TMP_DATA_DIR = tempfile.mkdtemp(prefix="puffpai_catalogs_api_test_")
os.environ["PUFFPAI_DATA_DIR"] = _TMP_DATA_DIR

import pytest  # noqa: E402

from puffpai import api_server, build_manifest, cli  # noqa: E402
from puffpai.catalogs import live  # noqa: E402
from puffpai import plain_words  # noqa: E402  (the one banned list)

API_KEY = api_server._API_KEY


@pytest.fixture(autouse=True)
def _no_network(monkeypatch, tmp_path):
    def boom(*_a, **_k):
        raise AssertionError("this test must not go online")
    monkeypatch.setattr(live, "fetch_url", boom)
    monkeypatch.setattr(live, "state_snapshot_dir", lambda: str(tmp_path / "state_snaps"))
    live.clear_caches()
    yield
    live.clear_caches()


def test_status_is_plain_and_offline(capsys):
    assert cli.main(["catalogs", "status"]) == 0
    out = capsys.readouterr().out
    assert "Home Assistant connections list" in out
    assert "only part of the list" in out  # the MCP snapshot stopped at its size limit
    assert plain_words.is_plain(out), out


def test_search_is_offline_and_plain(capsys):
    assert cli.main(["catalogs", "search", "dinosaurs", "fossils"]) == 0
    out = capsys.readouterr().out
    assert "(in the lists on this computer)" in out
    assert "Paleobiology Database" in out
    assert plain_words.is_plain(out), out


def test_search_with_a_goal_lists_owned_things(capsys):
    assert cli.main(["catalogs", "search", "--goal",
                     "A music bot that knows bands and albums and plays my records"]) == 0
    out = capsys.readouterr().out
    assert "MusicBrainz" in out and "Your records" in out


def test_search_with_nothing_asks_for_words(capsys):
    assert cli.main(["catalogs", "search"]) == 2


def test_refresh_says_where_it_goes_and_a_no_sends_nothing(capsys, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    monkeypatch.setattr(live, "refresh", lambda *a, **k: pytest.fail("must not refresh"))
    assert cli.main(["catalogs", "refresh", "apis_guru", "ha_integrations"]) == 1
    out = capsys.readouterr().out
    assert "asks api.apis.guru" in out and "asks raw.githubusercontent.com" in out
    assert "Nothing was sent." in out
    assert plain_words.is_plain(out), out


def test_refresh_of_a_list_that_does_not_exist(capsys):
    assert cli.main(["catalogs", "refresh", "nope", "--yes"]) == 2


def test_refresh_refused_when_the_bot_stays_offline(capsys, monkeypatch):
    monkeypatch.setattr(build_manifest, "read_manifest",
                        lambda path=None: {"posture": {"build_network": "offline"}})
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("must not ask"))
    assert cli.main(["catalogs", "refresh", "apis_guru", "--yes"]) == 1
    assert "stay offline" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client()


def _auth():
    return {"Authorization": f"Bearer {API_KEY}"}


def test_route_needs_the_app_key(client):
    assert client.get("/catalogs/discover?terms=dinosaur").status_code == 401
    assert "catalogs_discover" not in api_server._PUBLIC_ENDPOINTS


def test_route_returns_candidates_and_summary_lines(client):
    resp = client.get("/catalogs/discover", query_string={
        "goal": "Help me keep my 2014 Honda Civic running - maintenance, recalls"}, headers=_auth())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["live"][0]["id"] == "seed:nhtsa_recalls"
    sections = [i["section"] for i in body["proposal_items"]]
    assert "live_info" in sections and "devices" in sections


def test_route_refuses_oversize_input(client):
    resp = client.get("/catalogs/discover", query_string={"goal": "x" * 3000}, headers=_auth())
    assert resp.status_code == 400


def test_status_shows_the_shipped_checked_list_and_search_cuts_at_words(capsys):
    assert cli.main(["catalogs", "status"]) == 0
    out = capsys.readouterr().out
    assert "not saved on this computer yet" not in out.split("\n")[1], out
    assert re.search(r"free live-info websites: \d+ entries, checked \d{4}-\d\d-\d\d", out), out
    assert cli.main(["catalogs", "search", "--goal",
                     "I want a space bot that knows everything about space and works with my smart telescope"]) == 0
    out = capsys.readouterr().out
    assert "sandbox" not in out and plain_words.is_plain(out), out
    assert "(?)" not in out
    # The "runs on your computer" note is said once per line pair, not twice.
    assert out.count("It is a program that would run on your computer") == 0, out
    for line in out.splitlines():
        if line.startswith("  - ") and ": " in line:
            text = line.split(": ", 1)[1]
            # Either the whole text, or cut at a word with "..." - never mid-word.
            assert len(text) <= 110, line
