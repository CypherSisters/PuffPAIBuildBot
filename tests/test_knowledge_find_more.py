"""Adding knowledge after the build: `puffpai sources`, the API, printed fixes.

What an owner can do once the bot exists: search for books about anything,
see what one is, how big it is and its license, say yes, and have it land in
the bot's knowledge folder checked against the library's own fingerprint -
from the terminal or from the dashboard. And the commands the wizard prints
as fixes must actually run.

No network: the Kiwix library and its mirrors are a scripted transport.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time

_TMP_DATA_DIR = tempfile.mkdtemp(prefix="puffpai_find_more_test_")
os.environ.setdefault("PUFFPAI_DATA_DIR", _TMP_DATA_DIR)

import pytest  # noqa: E402

from puffpai import build_manifest, cli, source_fetch, source_registry  # noqa: E402
from puffpai.build_posture import assess_posture, posture_manifest_block  # noqa: E402
from puffpai.catalogs import kiwix_opds  # noqa: E402
from puffpai.config import get_config  # noqa: E402
from puffpai.knowledge import build_sources, find_more  # noqa: E402
from puffpai.wizard import fixes  # noqa: E402

BOOK = "kiwix:vikidia_en_all:nopic"
BODY = b"ZIM-" + b"\x07" * 5000
SHA = hashlib.sha256(BODY).hexdigest()
FILE = "vikidia_en_all_nopic_2026-09a.zim"
META4_URL = f"https://lb.download.kiwix.org/zim/vikidia/{FILE}.meta4"
PRIMARY = META4_URL[:-6]
MIRROR = f"https://ftp.nluug.nl/pub/kiwix/zim/vikidia/{FILE}"


class Resp:
    def __init__(self, status=200, headers=None, body=b""):
        self.status, self.headers, self._b, self._p = status, dict(headers or {}), body, 0

    def read(self, amt=-1):
        amt = len(self._b) - self._p if amt is None or amt < 0 else amt
        chunk = self._b[self._p:self._p + amt]
        self._p += len(chunk)
        return chunk

    def close(self):
        pass


def _feed():
    return f"""<feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <title>Vikidia</title><updated>2026-09-11T00:00:00Z</updated>
      <name>vikidia_en_all</name><flavour>nopic</flavour><category>vikidia</category>
      <link rel="http://opds-spec.org/acquisition/open-access" href="{META4_URL}"
            length="{len(BODY)}"/></entry></feed>""".encode()


def _meta4():
    return f"""<metalink xmlns="urn:ietf:params:xml:ns:metalink">
      <file name="{FILE}"><size>{len(BODY)}</size>
      <hash type="sha-256">{SHA}</hash>
      <url priority="1">{MIRROR}</url></file></metalink>""".encode()


class FakeLibrary:
    def __init__(self):
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append(url)
        if url.startswith(kiwix_opds.SEARCH_URL):
            return Resp(200, {}, _feed())
        if url == META4_URL:
            return Resp(200, {}, _meta4())
        if url == PRIMARY:
            return Resp(302, {"Location": MIRROR})
        if url == MIRROR:
            return Resp(200, {"Content-Length": str(len(BODY))}, BODY)
        return Resp(404)


@pytest.fixture
def scratch(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    get_config(_force_reload=True)
    source_fetch.clear_caches()
    kiwix_opds.clear_search_cache()
    library = FakeLibrary()
    monkeypatch.setattr(source_fetch, "_default_transport", library)
    monkeypatch.setattr(source_fetch, "_default_resolver", lambda host: ["93.184.216.34"])
    yield library
    source_fetch.clear_caches()
    kiwix_opds.clear_search_cache()
    get_config(_force_reload=True)


def _built(network="online"):
    path = build_manifest.manifest_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"posture": posture_manifest_block(assess_posture(network, "island"))}, fh)


# -- search --------------------------------------------------------------------

def test_search_before_any_build_uses_the_saved_list_and_never_the_network(scratch):
    result = find_more.search("dinosaurs")
    assert result["searched"] == "snapshot"
    assert scratch.calls == []
    assert any(c["id"] == "kiwix:ted_mul_dinosaurs" for c in result["candidates"])
    text = "\n".join(result["lines"])
    assert "Download:" in text and "License:" in text
    assert "puffpai sources add" in text


def test_search_after_an_online_build_asks_the_live_library(scratch):
    _built("online")
    result = find_more.search("vikidia")
    assert result["searched"] == "live"
    assert any(c.startswith(kiwix_opds.SEARCH_URL) for c in scratch.calls)


def test_an_offline_build_is_told_why_the_saved_list_was_used(scratch):
    _built("offline")
    result = find_more.search("dinosaurs", live=True)
    assert result["searched"] == "snapshot" and scratch.calls == []
    assert "stay offline" in result["note"]


# -- add -----------------------------------------------------------------------

@pytest.mark.parametrize("ident,words", [
    ("https://evil.example/pack.zim", "not an id"),
    ("kiwix:../../etc", "not an id"),
    ("kiwix:not_a_real_book_en_all", "has anything with that id"),
    ("kiwix:gardenology_en_all", "license is not known"),
])
def test_add_refuses_anything_that_is_not_a_listed_downloadable_book(scratch, ident, words):
    _built("online")
    result = find_more.add(ident, size_acknowledged=True, via="test")
    assert not result["ok"] and words in result["reason"]
    # a well-formed book name the saved list lacks is asked for by name once
    # (it may be newer than the list); nothing else is ever requested
    assert [c for c in scratch.calls if not c.startswith(kiwix_opds.SEARCH_URL)] == []
    assert len(scratch.calls) <= 1
    assert source_registry.load_overlay() == []


def test_add_before_a_build_or_on_an_offline_build_downloads_nothing(scratch):
    assert "not been set up" in find_more.add(BOOK, size_acknowledged=True, via="t")["reason"]
    _built("offline")
    assert "stay offline" in find_more.add(BOOK, size_acknowledged=True, via="t")["reason"]
    assert scratch.calls == []


def test_without_a_yes_add_only_shows_what_it_would_do(scratch):
    _built("online")
    result = find_more.add(BOOK, size_acknowledged=False, via="test")
    assert result["status"] == "needs_confirmation"
    text = "\n".join(result["lines"])
    for words in ("Vikidia", "about 10 MB", "CC-BY-SA-3.0", "Kiwix library",
                  "needs no internet"):
        assert words in text, words
    assert scratch.calls == [] and source_registry.load_overlay() == []


def test_a_yes_records_the_choice_downloads_checks_and_sets_the_folder(scratch):
    _built("online")
    result = find_more.add(BOOK, size_acknowledged=True, via="cli")
    assert result["ok"], result["reason"]
    folder = get_config(_force_reload=True).knowledge.zim_dir
    assert folder and os.path.isfile(os.path.join(folder, FILE))
    assert result["result"]["provenance"]["sha256_pinned"] is True
    rows = source_registry.load_overlay()
    assert [r["id"] for r in rows] == [BOOK]
    assert rows[0]["provenance"] == "owner_added" and rows[0]["added_via"] == "cli"
    # The Knowledge page now shows it as the owner's own addition.
    status = build_sources.source_status({"knowledge": {}}, pack_records=[])
    item = {i["id"]: i for i in status["items"]}[BOOK]
    assert item["selected_by"] == [build_sources.ADDED_BY_OWNER]
    assert item["status"] == build_sources.STATUS_FETCHED_NOT_INDEXED
    assert item["command"] == "puffpai knowledge sync"


def test_a_chosen_book_that_did_not_download_shows_the_add_command(scratch):
    _built("online")
    row = source_registry.row_from_candidate(find_more.look(BOOK), added_via="cli")
    source_registry.add_owner_row(row)
    status = build_sources.source_status({"knowledge": {}}, pack_records=[])
    item = {i["id"]: i for i in status["items"]}[BOOK]
    assert item["status"] == build_sources.STATUS_SELECTED_NOT_DOWNLOADED
    assert item["command"] == f"puffpai sources add {BOOK}"
    assert item["note"].startswith("You added this")


# -- the CLI face ----------------------------------------------------------------

def test_cli_search_prints_plain_results(scratch, capsys):
    assert cli.main(["sources", "search", "--offline", "music", "bands"]) == 0
    out = capsys.readouterr().out
    assert "Looked for: music, band" in out and "puffpai sources add kiwix:" in out


def test_cli_add_with_yes_but_no_accept_size_refuses(scratch, capsys):
    _built("online")
    assert cli.main(["sources", "add", BOOK, "--yes"]) == 1
    out = capsys.readouterr().out
    assert "--accept-size" in out and scratch.calls == []


def test_cli_add_asks_and_a_no_downloads_nothing(scratch, capsys, monkeypatch):
    _built("online")
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert cli.main(["sources", "add", BOOK]) == 1
    assert "Nothing was downloaded" in capsys.readouterr().out
    assert scratch.calls == []


# -- the commands the wizard prints as fixes actually run ----------------------

def test_every_registered_fix_command_parses():
    parser = cli.build_parser()
    samples = {"source.fetch": {"source_id": BOOK},
               "persona.rename_id": {"persona_id": "nova", "new_id": "nova2"},
               "model.pull": {"model": "qwen2.5:7b-instruct"},
               "model.pull_at": {"model": "qwen2.5:7b-instruct",
                                 "url": "http://127.0.0.1:11435"},
               "outside.on": {"service": "weather"}}
    for command_id in fixes.command_ids():
        line = fixes.render({"kind": "command", "id": command_id,
                             "args": samples.get(command_id, {})})
        if line.startswith("ollama "):
            continue  # the wizard's model fixes are Ollama's own command
        argv = line.split()[1:]
        assert line.startswith("puffpai ")
        parser.parse_args(argv)  # raises SystemExit on an unknown command


def test_the_printed_fetch_then_sync_commands_work_after_a_build(scratch, capsys,
                                                                 monkeypatch):
    """A real run: both printed knowledge fixes failed. Now they run."""
    _built("online")
    fetch_line = fixes.render({"kind": "command", "id": "source.fetch",
                               "args": {"source_id": BOOK}})
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert cli.main(fetch_line.split()[1:]) == 0, capsys.readouterr().out
    assert "Added Vikidia" in capsys.readouterr().out

    synced = {}

    def fake_sync(zim_dir, **kw):
        synced["dir"] = zim_dir
        return [{"filename": FILE, "status": "complete", "docs": 3, "chunks": 3}]

    monkeypatch.setattr("puffpai.knowledge.auto_index.sync_new_packs", fake_sync)
    sync_line = fixes.render({"kind": "command", "id": "knowledge.sync", "args": {}})
    assert cli.main(sync_line.split()[1:]) == 0
    out = capsys.readouterr().out
    assert "No knowledge folder configured" not in out
    assert os.path.isfile(os.path.join(synced["dir"], FILE))


# -- the API ----------------------------------------------------------------------

@pytest.fixture
def client(scratch):
    from puffpai import api_server
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client(), {"Authorization": f"Bearer {api_server._API_KEY}"}


def test_the_find_route_is_behind_the_api_key(client):
    http, auth = client
    assert http.get("/knowledge/find?q=dinosaurs").status_code == 401
    got = http.get("/knowledge/find?q=dinosaurs&offline=1", headers=auth)
    body = got.get_json()
    assert got.status_code == 200 and body["success"]
    ids = [c["id"] for c in body["candidates"]]
    assert "kiwix:ted_mul_dinosaurs" in ids
    one = body["candidates"][0]
    assert {"size_words", "license_plain", "fetchable"} <= set(one)
    assert "meta4_url" not in one, "download addresses are not sent to the browser"


@pytest.mark.parametrize("payload,status", [
    ({"id": "https://evil.example/x.zim", "size_acknowledged": True}, 400),
    ({"id": BOOK}, 400),
    ({"id": BOOK, "size_acknowledged": "yes"}, 400),
    ([1, 2], 400),
])
def test_the_add_route_takes_only_a_catalog_id_and_a_real_yes(client, payload, status):
    http, auth = client
    _built("online")
    got = http.post("/knowledge/add", json=payload, headers=auth)
    assert got.status_code == status
    assert source_registry.load_overlay() == []


def test_the_add_route_downloads_in_the_background_and_reports(client, monkeypatch):
    http, auth = client
    _built("online")
    monkeypatch.setattr("puffpai.knowledge.auto_index.start_background_index",
                        lambda folder, **kw: (1, None))
    got = http.post("/knowledge/add", json={"id": BOOK, "size_acknowledged": True},
                    headers=auth)
    assert got.status_code == 200 and got.get_json()["status"] == "started"
    # The per-host pause (one second between requests to one host) is real
    # here, so the job takes a few seconds.
    for _ in range(300):
        state = http.get("/knowledge/add/status", headers=auth).get_json()
        if state.get("state") in ("done", "failed"):
            break
        time.sleep(0.05)
    assert state["state"] == "done", state
    assert "learning it now" in state["message"]
    assert [r["id"] for r in source_registry.load_overlay()] == [BOOK]


# -- ability rows for the one source of truth ---------------------------------

def test_ability_rows_say_available_only_for_a_really_learned_book():
    shape = {"id", "label", "status", "how_to_add"}
    none = find_more.ability_rows([])
    # "action" is optional: the way to add it, worded per place.
    assert all(set(r) - {"action"} == shape for r in none)
    assert none[0]["action"] == {"id": "knowledge.search", "args": {}}
    assert none[0]["status"] == "not_set_up"
    assert none[0]["how_to_add"] == "puffpai sources search <words>"
    waiting = find_more.ability_rows([{"status": "indexing"}])
    assert waiting[0]["status"] == "not_set_up"
    assert waiting[0]["how_to_add"] == "puffpai knowledge sync"
    done = find_more.ability_rows([{"status": "complete", "chunks": 7040}])
    assert done[0]["status"] == "available"
    empty_done = find_more.ability_rows([{"status": "complete", "chunks": 0}])
    assert empty_done[0]["status"] != "available"


def test_an_unreadable_pack_index_is_unknown_never_available():
    rows = find_more.ability_rows([object()])  # not a dict: reading it fails
    assert rows[0]["status"] == "unknown"


# -- a book newer than the saved list ------------------------------------------

def test_a_book_only_the_live_library_lists_can_still_be_added(scratch, monkeypatch):
    _built("online")
    monkeypatch.setattr(find_more, "look", lambda ident: None)  # not in the saved list
    cand = find_more.look_for_add(BOOK)
    assert cand is not None and cand["id"] == BOOK and cand["fetchable"]
    assert any(c.startswith(kiwix_opds.SEARCH_URL) for c in scratch.calls)
    out = find_more.add(BOOK, size_acknowledged=True, via="cli")
    assert out["ok"], out.get("reason")


def test_an_offline_build_never_asks_the_live_library_for_an_unknown_book(scratch, monkeypatch):
    _built("offline")
    monkeypatch.setattr(find_more, "look", lambda ident: None)
    assert find_more.look_for_add(BOOK) is None
    assert scratch.calls == []


def test_only_kiwix_ids_are_ever_looked_up_live(scratch, monkeypatch):
    _built("online")
    for ident in ("not_a_registry_row", "https://evil.example/x.zim", "../../etc/passwd"):
        assert find_more.look_for_add(ident) is None
    assert scratch.calls == []
