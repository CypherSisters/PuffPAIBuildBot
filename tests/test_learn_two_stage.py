"""A book answers after its quick stage; the deeper stage resumes, pauses and removes.

Stand-ins only: a word look-up built from a synthetic book, a fake store and a
fake reading helper. The real 10 MB book is proved by a real run.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from learning_support import (ARTICLES, FakeCollection, FakeRag,  # noqa: E402
                              fake_embedder, make_book)

from puffpai.knowledge import book_search, learn, progress, reading_helper  # noqa: E402
from puffpai.knowledge.auto_index import STATUS_COMPLETE, STATUS_REVOKED, PackIndex  # noqa: E402

BOOK = "dino_book_2026_09"
LABELS = {"source_id": "kiwix:dino", "source_label": "Dino Book",
          "source_license": "CC-BY-SA-3.0", "book_name": "Dino Book"}


@pytest.fixture
def book(tmp_path, monkeypatch):
    fts = str(tmp_path / "fts")
    make_book(fts, BOOK, ARTICLES, labels=LABELS)
    monkeypatch.setattr(learn, "books_dir", lambda cfg=None: fts)
    zim = tmp_path / f"{BOOK}.zim"
    zim.write_bytes(b"x")
    pi = PackIndex(str(tmp_path / "packs.json"))
    pi.mark("u1", filename=zim.name, path=str(zim), book_id=BOOK, name="Dino Book",
            status=STATUS_COMPLETE, quick="complete", meaning=learn.M_PENDING,
            source_id="kiwix:dino")
    monkeypatch.setattr(reading_helper, "get_embedder", lambda threads=0: fake_embedder)
    return {"fts": fts, "pi": pi, "zim": str(zim)}


def test_a_book_answers_from_its_quick_stage_before_any_meaning(book):
    hits = book_search.book_hits("how big was tyrannosaurus rex", fts_dir=book["fts"])
    assert hits and hits[0]["title"] == "Tyrannosaurus"
    assert hits[0]["library"] == "Dino Book"
    assert hits[0]["relevance"] >= 0.35
    assert len(hits[0]["text"]) <= book_search.PASSAGE_CHARS + 6


def test_the_deeper_stage_writes_every_passage_with_the_book_in_its_id(book):
    rag = FakeRag()
    out = learn.deep_learn(BOOK, pack_index=book["pi"], rag=rag,
                           should_pause=lambda: False)
    assert out["status"] == learn.M_DONE
    rows = rag.knowledge_collection.rows
    assert rows and all(i.startswith(f"{BOOK}:") for i in rows)
    assert all(r["meta"]["book_id"] == BOOK for r in rows.values())
    assert all(len(r["doc"]) <= 800 for r in rows.values())
    assert not any(r["meta"]["title"] == "Short" for r in rows.values())
    assert book["pi"].find_by_book(BOOK)[1]["meaning"] == learn.M_DONE
    assert progress.plain_lines(progress.read(BOOK)) == ["Dino Book: fully learned."]


def test_a_stopped_run_resumes_where_it_left_off_and_writes_nothing_twice(book, monkeypatch):
    coll = FakeCollection()
    monkeypatch.setattr(learn, "WRITE_BATCH", 16)
    calls = {"n": 0}

    def stop_after_three():
        calls["n"] += 1
        return calls["n"] > 3
    monkeypatch.setattr(learn, "stop_requested", lambda book_id: stop_after_three())
    shard = os.path.join(book["fts"], f"{BOOK}.db")
    writer = progress.Writer(BOOK, "Dino Book")
    first = learn.meaning_stage(BOOK, shard, coll, fake_embedder, labels=LABELS,
                                writer=writer, write_batch=16, embed_batch=8,
                                should_pause=lambda: False)
    assert first["status"] == "stopped"
    partial = dict(coll.rows)
    assert partial
    monkeypatch.setattr(learn, "stop_requested", lambda book_id: False)
    writer = progress.Writer(BOOK, "Dino Book")
    second = learn.meaning_stage(BOOK, shard, coll, fake_embedder, labels=LABELS,
                                 writer=writer, write_batch=16, embed_batch=8,
                                 should_pause=lambda: False)
    assert second["status"] == "done"
    assert progress.read(BOOK)["resumed_from_pct"] > 0
    assert any(line.startswith("Picked up where I left off")
               for line in progress.plain_lines(progress.read(BOOK)))
    full = FakeCollection()
    learn.reset_checkpoint(shard)
    learn.meaning_stage(BOOK, shard, full, fake_embedder, labels=LABELS,
                        writer=progress.Writer(BOOK, "Dino Book"),
                        should_pause=lambda: False)
    assert set(coll.rows) == set(full.rows), "resumed run == one clean run"


def test_learning_pauses_while_someone_talks(book):
    shard = os.path.join(book["fts"], f"{BOOK}.db")
    talking = iter([True, True, True, False] + [False] * 1000)
    slept = []
    out = learn.meaning_stage(BOOK, shard, FakeCollection(), fake_embedder,
                              labels=LABELS, writer=progress.Writer(BOOK, "Dino Book"),
                              should_pause=lambda: next(talking), sleep=slept.append)
    assert out["status"] == "done"
    assert slept == [1.0, 1.0]


def test_the_heartbeat_is_a_file_any_process_can_read(tmp_path):
    assert progress.someone_talking() is False
    progress.note_talking()
    assert progress.someone_talking() is True
    assert progress.someone_talking(window=0) is False


def test_a_second_learner_cannot_take_the_same_book(book):
    lock = learn.BookLock(book["fts"], BOOK)
    assert lock.acquire()
    try:
        out = learn.deep_learn(BOOK, pack_index=book["pi"], rag=FakeRag(),
                               should_pause=lambda: False)
        assert out["status"] == "busy"
    finally:
        lock.release()


def test_the_same_article_in_two_books_never_collides(tmp_path, book):
    other = "dino_book_copy"
    make_book(book["fts"], other, ARTICLES, labels=dict(LABELS, source_id="kiwix:copy"))
    coll = FakeCollection()
    for bid in (BOOK, other):
        learn.meaning_stage(bid, os.path.join(book["fts"], f"{bid}.db"), coll,
                            fake_embedder, labels=LABELS,
                            writer=progress.Writer(bid, bid), should_pause=lambda: False)
    per_book = {b: [i for i in coll.rows if i.startswith(f"{b}:")] for b in (BOOK, other)}
    assert len(per_book[BOOK]) == len(per_book[other]) > 0


def test_without_the_reading_helper_the_book_waits_and_still_answers(book, monkeypatch):
    monkeypatch.setattr(reading_helper, "get_embedder", lambda threads=0: None)
    out = learn.deep_learn(BOOK, pack_index=book["pi"], rag=FakeRag())
    assert out["status"] == learn.M_WAITING_HELPER
    lines = progress.plain_lines(progress.read(BOOK), "terminal")
    assert lines[0] == "Dino Book: ready to answer questions."
    assert "reading helper" in lines[1]
    assert book_search.book_hits("volcano magma", fts_dir=book["fts"])


def test_a_big_untimed_book_waits_for_the_owner(book, monkeypatch):
    monkeypatch.setattr(learn, "AUTO_MAX_CHARS_UNTIMED", 10)
    out = learn.deep_learn(BOOK, pack_index=book["pi"], rag=FakeRag())
    assert out["status"] == learn.M_NOT_STARTED
    lines = progress.plain_lines(progress.read(BOOK), "terminal")
    assert any("has not started" in x for x in lines)
    assert not any("about" in x and "hour" in x for x in lines), "nothing timed yet"
    assert lines[-1] == 'To start it, run: puffpai knowledge deeper "Dino Book"'
    asked = learn.deep_learn(BOOK, pack_index=book["pi"], rag=FakeRag(),
                             owner_asked=True, should_pause=lambda: False)
    assert asked["status"] == learn.M_DONE


def test_removing_a_book_deletes_its_look_up_its_passages_and_its_file(book):
    coll = FakeCollection()
    coll.upsert(["other:1:0"], ["kept"], [{"book_id": "other"}], [[0.0]])
    coll.upsert(["legacy_0"], ["old"], [{"source_id": "kiwix:dino"}], [[0.0]])
    rag = FakeRag(coll)
    learn.deep_learn(BOOK, pack_index=book["pi"], rag=rag, should_pause=lambda: False)
    assert any(i.startswith(BOOK) for i in coll.rows)
    out = learn.remove_book(BOOK, pack_index=book["pi"], rag=rag)
    assert out["ok"] and out["file_removed"]
    assert set(coll.rows) == {"other:1:0"}
    assert not os.path.exists(os.path.join(book["fts"], f"{BOOK}.db"))
    assert progress.read(BOOK) is None
    assert not os.path.exists(book["zim"])
    assert book["pi"].find_by_book(BOOK)[1]["status"] == STATUS_REVOKED
    assert book_search.book_hits("tyrannosaurus", fts_dir=book["fts"]) == []


def test_find_book_takes_a_name_or_a_word_of_it(book):
    assert learn.find_book("Dino Book", book["pi"])[0] == BOOK
    assert learn.find_book("dino", book["pi"])[0] == BOOK
    assert learn.find_book("cats", book["pi"]) is None


def test_meaning_work_lists_only_books_allowed_to_run(book):
    assert learn.meaning_work(book["pi"]) == [BOOK]
    uuid = book["pi"].find_by_book(BOOK)[0]
    book["pi"].mark(uuid, meaning=learn.M_OFF)
    assert learn.meaning_work(book["pi"]) == []


def test_a_book_without_a_download_record_is_named_by_its_own_title(monkeypatch):
    meta = {"filename": "vikidia_en_all_nopic_2026-09a.zim", "filepath": "x.zim"}
    assert learn.book_name(meta, {"source_label": "Vikidia (text only)"}) == \
        "Vikidia (text only)"
    monkeypatch.setattr(learn, "_zim_title", lambda path: "Vikidia")
    assert learn.book_name(meta, None) == "Vikidia"
    monkeypatch.setattr(learn, "_zim_title", lambda path: "")
    assert learn.book_name(meta, None) == "Vikidia en all nopic 09a"


def test_the_estimator_reads_the_whole_learning_time_not_the_quick_stage(book, monkeypatch):
    from datetime import datetime

    from puffpai import index_estimate
    uuid = book["pi"].find_by_book(BOOK)[0]
    book["pi"].mark(uuid, quick_seconds=2.0, meaning_seconds=100.0,
                    started_at="2026-09-24T00:00:00+00:00",
                    indexed_at="2026-09-24T00:00:02+00:00")
    assert index_estimate.measured_rate(book["pi"]) is None, "no passages yet: no rate"
    monkeypatch.setattr(learn, "meaning_stage",
                        lambda *a, **k: {"status": "done", "chunks": 5000,
                                         "chars_per_s": 1.0, "active_seconds": 50.0})
    learn.deep_learn(BOOK, pack_index=book["pi"], rag=FakeRag())
    rec = book["pi"].find_by_book(BOOK)[1]
    took = (datetime.fromisoformat(rec["indexed_at"])
            - datetime.fromisoformat(rec["started_at"])).total_seconds()
    assert rec["meaning_seconds"] == 150.0 and abs(took - 152.0) < 0.01
    assert rec["chunks"] == 5000


def _revoke(book, coll):
    """Set the book aside the way the download code does (source_fetch.revoke)."""
    from puffpai import source_fetch
    return source_fetch.revoke(book["zim"], "bad copy", knowledge_collection=coll,
                               pack_index=book["pi"])


def test_a_revoked_book_stops_answering_from_both_look_ups(book, monkeypatch):
    from puffpai.knowledge import auto_index
    monkeypatch.setattr(auto_index, "default_pack_index_path", lambda: book["pi"]._path)
    coll = FakeCollection()
    rag = FakeRag(coll)
    learn.deep_learn(BOOK, pack_index=book["pi"], rag=rag, should_pause=lambda: False)
    assert book_search.book_hits("tyrannosaurus", fts_dir=book["fts"], rag=rag)
    # An unlabelled leftover row the revocation cannot pick out by source id.
    coll.upsert([f"{BOOK}:9:0"], ["Tyrannosaurus leftover"],
                [{"book_id": BOOK, "title": "Tyrannosaurus"}], [[0.0]])
    _revoke(book, coll)
    assert book["pi"].find_by_book(BOOK)[1]["status"] == STATUS_REVOKED
    # The revocation left the look-up behind; the books still must not answer.
    assert book_search.book_hits("tyrannosaurus", fts_dir=book["fts"], rag=rag) == []
    assert book_search.pick_passage(["tyrannosaurus"], rag=rag, fts_dir=book["fts"]) is None
    # ... and the first look cleaned up what the revocation missed.
    assert not os.path.exists(os.path.join(book["fts"], f"{BOOK}.db"))
    assert not any(r["meta"].get("book_id") == BOOK for r in coll.rows.values())
    assert progress.read(BOOK) is None
    # The set-aside file stays for a person to review.
    assert os.path.exists(book["zim"] + ".revoked")


def test_a_revoked_book_is_not_reported_as_ready(book, monkeypatch):
    from puffpai.knowledge import auto_index
    monkeypatch.setattr(auto_index, "default_pack_index_path", lambda: book["pi"]._path)
    progress.Writer(BOOK, "Dino Book").quick(10, 10)
    assert any("Dino Book" in line for line in progress.all_lines())
    _revoke(book, FakeCollection())
    assert not any("Dino Book" in line for line in progress.all_lines())
    assert progress.chat_answer() is None


def test_a_busy_revoked_book_is_hidden_even_before_it_is_cleaned(book, monkeypatch):
    from puffpai.knowledge import auto_index
    monkeypatch.setattr(auto_index, "default_pack_index_path", lambda: book["pi"]._path)
    # Busy while it is revoked: revoke cannot clean it (revoke cleans
    # a book it can lock at once), and readers still hide it.
    lock = learn.BookLock(book["fts"], BOOK)
    assert lock.acquire()
    try:
        _revoke(book, FakeCollection())
        assert book_search.book_hits("tyrannosaurus", fts_dir=book["fts"]) == []
        assert os.path.exists(os.path.join(book["fts"], f"{BOOK}.db"))
    finally:
        lock.release()


# ── another writer never stops learning (2026-10-14) ─────────────────────────

class _LockedTwice(FakeCollection):
    """A store that finds its database locked the first two writes."""

    def __init__(self):
        super().__init__()
        self.fails = 2

    def upsert(self, **kw):
        if self.fails:
            self.fails -= 1
            import sqlite3
            raise sqlite3.OperationalError("database is locked")
        return super().upsert(**kw)


def test_a_locked_store_is_waited_for_not_a_stop(book):
    shard = os.path.join(book["fts"], f"{BOOK}.db")
    coll = _LockedTwice()
    out = learn.meaning_stage(BOOK, shard, coll, fake_embedder, labels=LABELS,
                              writer=progress.Writer(BOOK, "Dino Book"),
                              should_pause=lambda: False, sleep=lambda s: None)
    assert out["status"] == "done" and coll.rows and coll.fails == 0


def test_a_shard_another_connection_is_writing_is_waited_for(book, monkeypatch):
    import sqlite3
    import threading
    import time
    shard = os.path.join(book["fts"], f"{BOOK}.db")
    monkeypatch.setattr(learn, "BUSY_SECONDS", 1)
    other = sqlite3.connect(shard, check_same_thread=False)
    other.execute("BEGIN IMMEDIATE")                  # another writer holds the shard
    other.execute("INSERT INTO meta (key, value) VALUES ('other', '1') "
                  "ON CONFLICT(key) DO UPDATE SET value = '1'")
    threading.Timer(1.5, other.commit).start()
    out = learn.meaning_stage(BOOK, shard, FakeCollection(), fake_embedder, labels=LABELS,
                              writer=progress.Writer(BOOK, "Dino Book"),
                              should_pause=lambda: False, sleep=lambda s: time.sleep(0.5))
    other.close()
    assert out["status"] == "done"


def test_a_book_stopped_by_a_lock_resumes_on_the_next_scan(book, monkeypatch):
    import sqlite3
    monkeypatch.setattr(learn, "WRITE_TRIES", 1)

    class Locked(FakeCollection):
        def upsert(self, **kw):
            raise sqlite3.OperationalError("database is locked")
    rag = FakeRag()
    rag.knowledge_collection = Locked()
    out = learn.deep_learn(BOOK, pack_index=book["pi"], rag=rag, should_pause=lambda: False)
    assert out["status"] == learn.M_STOPPED and "locked" in out["error"]
    assert learn.meaning_work(book["pi"]) == [BOOK]          # the next scan takes it
    out = learn.deep_learn(BOOK, pack_index=book["pi"], rag=FakeRag(),
                           should_pause=lambda: False)
    assert out["status"] == learn.M_DONE
