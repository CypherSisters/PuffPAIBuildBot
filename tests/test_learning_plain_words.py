"""The terminal, the browser and the chat say the same things about learning,
each in its own words, and none of them uses jargon (commands included)."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from learning_support import (ARTICLES, FakeRag, fake_embedder,  # noqa: E402
                              jargon_in, make_book)

from puffpai import cli  # noqa: E402
from puffpai.knowledge import learn, learning_api, progress, reading_helper  # noqa: E402
from puffpai.knowledge.auto_index import STATUS_COMPLETE, PackIndex  # noqa: E402

BOOK = "vikidia_en_all_nopic_2026_09a"
NAME = "Vikidia (text only)"


@pytest.fixture
def shelf(tmp_path, monkeypatch):
    fts = learn.books_dir()
    make_book(fts, BOOK, ARTICLES, labels={"book_name": NAME, "source_id": "kiwix:v",
                                           "source_label": NAME})
    zim = tmp_path / "vikidia.zim"
    zim.write_bytes(b"x")
    PackIndex().mark("u1", filename=zim.name, path=str(zim), book_id=BOOK, name=NAME,
                     status=STATUS_COMPLETE, quick="complete", meaning=learn.M_PENDING,
                     source_id="kiwix:v", docs=33)
    w = progress.Writer(BOOK, NAME)
    w.update(force=True, stage="not_started", quick_ready=True,
             meaning={"chars_total": 11_000_000})
    return {"zim": str(zim)}


def _run(capsys, *argv):
    code = cli.main(["knowledge", *argv])
    out = capsys.readouterr().out
    for line in out.splitlines():
        assert not jargon_in(line), line
    return code, out


def test_status_names_each_book_in_plain_words(shelf, capsys):
    code, out = _run(capsys, "status")
    assert code == 0
    assert f"{NAME}: ready to answer questions." in out
    assert f'To start it, run: puffpai knowledge deeper "{NAME}"' in out
    assert "Reading helper:" in out


def test_deeper_stop_and_remove_speak_plainly(shelf, capsys, monkeypatch):
    code, out = _run(capsys, "deeper", "vikidia", "--stop")
    assert code == 0 and "It still answers questions." in out
    assert PackIndex().find_by_book(BOOK)[1]["meaning"] == learn.M_OFF
    code, out = _run(capsys, "deeper", "no such book")
    assert code == 1 and f"These are the books: {NAME}" in out
    monkeypatch.setattr(learn, "remove_book",
                        lambda book_id, **kw: {"ok": True, "rows": 0})
    code, out = _run(capsys, "remove", "vikidia", "--yes")
    assert code == 0 and f"Removed {NAME}." in out


def test_deeper_runs_in_the_terminal_and_reports(shelf, capsys, monkeypatch):
    monkeypatch.setattr(reading_helper, "get_embedder", lambda threads=0: fake_embedder)
    rag = FakeRag()
    real = learn.deep_learn
    monkeypatch.setattr(learn, "deep_learn",
                        lambda book_id, **kw: real(book_id, **dict(kw, rag=rag)))
    code, out = _run(capsys, "deeper", NAME)
    assert code == 0 and f"{NAME}: fully learned." in out
    assert rag.knowledge_collection.count() > 0


def test_the_helper_command_says_where_it_is(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(reading_helper, "chroma_cache_dir", lambda: str(tmp_path / "c"))
    code, out = _run(capsys, "helper")
    assert code == 1 and out.startswith("Reading helper: Not on this computer")
    code, out = _run(capsys, "helper", "from-file", str(tmp_path / "missing.tar.gz"))
    assert code == 1 and "not there" in out


def test_sync_on_an_empty_folder(capsys, tmp_path):
    code, out = _run(capsys, "sync", "--zim-dir", str(tmp_path))
    assert code == 0 and "No new books" in out


# -- the routes --------------------------------------------------------------------------

@pytest.fixture
def web():
    from puffpai import api_server
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client(), {"Authorization": f"Bearer {api_server._API_KEY}"}


def test_the_learning_route_is_behind_the_key_and_speaks_browser_words(shelf, web):
    client, auth = web
    assert client.get("/knowledge/learning").status_code == 401
    books = client.get("/knowledge/learning", headers=auth).json["books"]
    assert books[0]["id"] == BOOK and books[0]["can_start"]
    assert "Press Learn it fully to start." in books[0]["lines"]
    for line in books[0]["lines"]:
        assert not jargon_in(line)


@pytest.mark.parametrize("body,status", [
    (None, 400), ({"id": BOOK}, 400), ({"id": BOOK, "on": "yes"}, 400),
    ({"id": "../../etc", "on": True}, 404), ({"id": "nobook", "on": True}, 404)])
def test_the_deeper_route_takes_only_a_known_book_and_a_yes_or_no(shelf, web, body, status):
    client, auth = web
    got = client.post("/knowledge/learning/deeper", json=body, headers=auth)
    assert got.status_code == status


def test_the_deeper_route_starts_and_stops(shelf, web, monkeypatch):
    client, auth = web
    started = []
    monkeypatch.setattr(learn, "deep_learn",
                        lambda book_id, **kw: started.append((book_id, kw["owner_asked"])))
    got = client.post("/knowledge/learning/deeper", json={"id": BOOK, "on": True},
                      headers=auth)
    assert got.status_code == 200
    learning_api._threads[BOOK].join(timeout=5)
    assert started == [(BOOK, True)]
    got = client.post("/knowledge/learning/deeper", json={"id": BOOK, "on": False},
                      headers=auth)
    assert got.status_code == 200 and learn.stop_requested(BOOK)


def test_the_reading_helper_route_gives_the_outside_row(web):
    client, auth = web
    got = client.get("/knowledge/reading-helper", headers=auth).json
    assert got["row"]["id"] == "reading_helper" and got["state"] in ("ready", "missing", "bad")


def test_the_bot_answers_are_you_done_learning_from_the_same_records(shelf):
    from puffpai import api_server
    answer = api_server._maybe_learning_answer("are you done learning?")
    assert answer.startswith(f"{NAME}: ready to answer questions.")
    assert "Knowledge page" in answer
    assert api_server._maybe_learning_answer("what is a volcano?") is None
    for bad in jargon_in(answer):
        pytest.fail(bad)


def test_the_ability_row_counts_a_quick_ready_book_as_learned(shelf):
    from puffpai.knowledge import find_more
    rows = find_more.ability_rows()
    assert rows[0]["status"] == "available"
