"""Book folders made of fetched text: written whole, grown without repeats, learned once."""
from __future__ import annotations

import json
import os

import pytest

from puffpai.knowledge import auto_index, text_books as tb

META = {"id": "wikipedia:simple:dinosaur", "title": "Simple English Wikipedia: dinosaur",
        "license": "CC-BY-SA-4.0", "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "credit": "Text from Simple English Wikipedia.", "commercial_use": True,
        "kind": "wikipedia_topic", "catalog": "Simple English Wikipedia", "language": "simple"}


def _pages(*titles):
    return [{"title": t, "url": f"https://simple.wikipedia.org/wiki/{t}", "revision": "7",
             "text": f"It was a dinosaur called {t}.\x00\x07 It lived long ago."}
            for t in titles]


def test_a_book_folder_is_written_with_provenance_per_page(tmp_path):
    rec = tb.write_book(str(tmp_path), META, _pages("Tyrannosaurus", "Triceratops"))
    folder = tmp_path / "wikipedia_simple_dinosaur.book"
    assert rec["_path"] == str(folder) and not (tmp_path / "wikipedia_simple_dinosaur.book.part").exists()
    book = json.loads((folder / "book.json").read_text(encoding="utf-8"))
    assert book["license"] == "CC-BY-SA-4.0" and book["trust_note"].startswith("Downloaded")
    assert [i["title"] for i in book["items"]] == ["Tyrannosaurus", "Triceratops"]
    assert book["items"][0]["url"].endswith("/Tyrannosaurus") and book["items"][0]["revision"] == "7"
    text = (folder / "text" / "0001.txt").read_text(encoding="utf-8")
    assert text.startswith("Tyrannosaurus\n\n") and "\x00" not in text and "\x07" not in text
    assert book["share_with_build"] is True


def test_growing_a_book_adds_only_new_pages(tmp_path):
    tb.write_book(str(tmp_path), META, _pages("Tyrannosaurus"))
    rec = tb.write_book(str(tmp_path), META, _pages("Tyrannosaurus", "Stegosaurus"))
    assert rec["_added"] == 1 and [i["title"] for i in rec["items"]] == [
        "Tyrannosaurus", "Stegosaurus"]
    assert rec["items"][1]["file"] == "text/0002.txt"


def test_a_book_stops_at_its_size_limit_and_says_so(tmp_path):
    rec = tb.write_book(str(tmp_path), META, _pages("A1", "B2", "C3"), max_bytes=70)
    assert len(rec["items"]) == 1 and rec["stopped_at_limit"] is True


def test_nothing_readable_saves_nothing(tmp_path):
    with pytest.raises(tb.BookError):
        tb.write_book(str(tmp_path), META, [{"title": "X", "text": "  "}])
    assert tb.find_books(str(tmp_path)) == []


@pytest.mark.parametrize("bad", ["../x", "a b", "", "x" * 200, "wiki/../../x"])
def test_only_ids_buildbot_made_become_folder_names(bad):
    with pytest.raises(tb.BookError):
        tb.folder_name(bad)


def test_a_personal_use_book_is_marked_never_shared(tmp_path):
    meta = dict(META, id="openstax:astronomy-2e", personal_use_only=True, commercial_use=False)
    rec = tb.write_book(str(tmp_path), meta, _pages("Stars"))
    assert rec["personal_use_only"] is True and rec["share_with_build"] is False


def _shard_titles(fts_dir, book_id):
    import sqlite3
    conn = sqlite3.connect(os.path.join(str(fts_dir), f"{book_id}.db"))
    try:
        return [r[0] for r in conn.execute("SELECT title FROM docs ORDER BY id")]
    finally:
        conn.close()


# A text book is learned in the same two stages as every
# other book (knowledge.learn): the quick stage puts its pages in its own word
# look-up; the meaning stage runs later in the background.

def test_learning_makes_a_word_look_up_and_records_the_book(tmp_path):
    rec = tb.write_book(str(tmp_path), META, _pages("Tyrannosaurus", "Triceratops"))
    index = auto_index.PackIndex(str(tmp_path / "packs.json"))
    fts = tmp_path / "fts"
    got = tb.learn(rec["_path"], pack_index=index, fts_dir=str(fts))
    lid = tb.learn_id(META["id"])
    assert got["status"] == "complete" and got["docs"] == 2 and got["book_id"] == lid
    assert _shard_titles(fts, lid) == ["Tyrannosaurus", "Triceratops"]
    from puffpai.knowledge import fts_index
    labels = fts_index.shard_labels(str(fts / f"{lid}.db"))
    assert labels["source_id"] == META["id"] and labels["book_name"] == META["title"]
    assert labels["source_license"] == "CC-BY-SA-4.0"
    record = index.get("book:" + META["id"])
    assert record["status"] == "complete" and record["quick"] == "complete"
    assert record["meaning"] == "pending" and record["book_id"] == lid
    # learned once: a second run learns nothing new
    again = tb.learn(rec["_path"], pack_index=index, fts_dir=str(fts))
    assert again["learned_now"] == 0
    # a grown book learns only its new page
    tb.write_book(str(tmp_path), META, _pages("Stegosaurus"))
    more = tb.learn(rec["_path"], pack_index=index, fts_dir=str(fts))
    assert more["learned_now"] == 1 and more["docs"] == 3
    assert _shard_titles(fts, lid)[-1] == "Stegosaurus"


def test_a_failure_while_learning_is_recorded_not_raised(tmp_path, monkeypatch):
    from puffpai.knowledge import fts_index
    rec = tb.write_book(str(tmp_path), META, _pages("Tyrannosaurus"))
    index = auto_index.PackIndex(str(tmp_path / "packs.json"))

    def broken(*a, **kw):
        raise RuntimeError("store is gone")

    monkeypatch.setattr(fts_index.FTSBuilder, "_index_records", staticmethod(broken))
    got = tb.learn(rec["_path"], pack_index=index, fts_dir=str(tmp_path / "fts"))
    assert got["status"] == "error" and "store is gone" in got["error"]
    assert index.get("book:" + META["id"])["status"] == "error"
    assert tb.pending_files(rec["_path"]) == ["text/0001.txt"]


def test_a_learned_book_counts_as_a_library_for_what_can_you_do(tmp_path):
    from puffpai import build_abilities
    rec = tb.write_book(str(tmp_path), META, _pages("Tyrannosaurus"))
    index = auto_index.PackIndex(str(tmp_path / "packs.json"))
    tb.learn(rec["_path"], pack_index=index, fts_dir=str(tmp_path / "fts"))
    records = [dict(r, chunks=max(int(r.get("chunks") or 0), int(r.get("docs") or 0)))
               for r in index.records()]
    row = build_abilities.library_row(records, None)
    assert row["status"] == build_abilities.AVAILABLE


def test_the_meaning_stage_learns_a_text_book_like_any_other(tmp_path):
    """Quick stage here, then knowledge.learn.deep_learn: passages by book id."""
    from learning_support import FakeRag, fake_embedder
    from puffpai.knowledge import learn, reading_helper

    rec = tb.write_book(str(tmp_path), META, [
        {"title": "Tyrannosaurus", "url": "u1",
         "text": "Tyrannosaurus was a large meat-eating dinosaur. " * 20}])
    index = auto_index.PackIndex(str(tmp_path / "packs.json"))
    fts = str(tmp_path / "fts")
    lid = tb.learn(rec["_path"], pack_index=index, fts_dir=fts)["book_id"]
    assert learn.meaning_work(index) == [lid]
    rag = FakeRag()
    orig = reading_helper.get_embedder
    reading_helper.get_embedder = lambda threads=0: fake_embedder
    try:
        out = learn.deep_learn(lid, pack_index=index, rag=rag, fts_dir=fts,
                               should_pause=lambda: False)
    finally:
        reading_helper.get_embedder = orig
    assert out["status"] == "done" and out["chunks"] >= 1
    metas = [r["meta"] for r in rag.knowledge_collection.rows.values()]
    assert metas and all(m["book_id"] == lid for m in metas)
    assert index.get("book:" + META["id"])["meaning"] == "done"

