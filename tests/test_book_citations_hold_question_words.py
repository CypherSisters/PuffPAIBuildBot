"""A cited book passage has to hold the question's words, and a listing page is
never one (citations were decoration).

The word look-up scores each hit against the best hit of the same look-up, so
the top hit was always 0.85 and cited, whatever it said. The Saturn distance
cited "Highest Voted 'saturn-rings' Questions" (a tag index page), and a
garden question cited "Bird bath".
"""
from __future__ import annotations

import os
import sqlite3

from puffpai.knowledge import book_search, fts_index
from puffpai.knowledge.fts_index import SCHEMA_VERSION, FTSBuilder, _set_meta


def _shard(folder, book_id, records):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{book_id}.db")
    conn = sqlite3.connect(path)
    try:
        FTSBuilder._ensure_schema(conn)
        count = FTSBuilder._index_records(conn, records)
        _set_meta(conn, schema_version=SCHEMA_VERSION, status="complete",
                  zim_filename=f"{book_id}.zim", zim_size=1, zim_mtime=1,
                  article_count=count, book_name="Space Q&A")
        conn.commit()
    finally:
        conn.close()


def test_listing_pages_are_not_answers(tmp_path):
    folder = str(tmp_path / "fts")
    _shard(folder, "spaceqa", [
        ("C/questions/tagged/saturn-rings", "Highest Voted 'saturn-rings' Questions",
         "How far is Saturn? Saturn rings distance. Saturn distance from Earth."),
        ("C/questions_page=3", "questions_page=3",
         "Saturn distance Saturn distance Earth Saturn"),
        ("C/questions/7/how-far-is-saturn", "How far is Saturn from Earth?",
         "Saturn is about 1.4 billion km from the Sun, and its distance from "
         "Earth changes through the year."),
    ])
    hits = book_search.word_hits("how far is saturn from earth", fts_dir=folder)
    titles = [h["title"] for h in hits]
    assert titles == ["How far is Saturn from Earth?"]
    assert hits[0]["relevance"] >= 0.25


def test_the_top_hit_that_does_not_hold_the_words_is_not_cited(tmp_path):
    folder = str(tmp_path / "fts")
    _shard(folder, "birds", [
        ("A/Bird_bath", "Bird bath",
         "A bird bath is a garden ornament: a shallow basin of water for birds."),
    ])
    hits = book_search.word_hits("what does my vegetable garden need this week",
                                 fts_dir=folder)
    assert hits, "the look-up still finds it"
    assert hits[0]["relevance"] < 0.25  # below the chat's citing floor


def test_the_indexer_skips_listing_pages():
    for url in ("questions/tagged/chain", "questions", "questions_page=12",
                "tags/brakes", "users/12/bob"):
        assert fts_index.listing_page_reason(url), url
    for url in ("questions/12/why-does-my-chain-rattle", "Saturn", "about"):
        assert fts_index.listing_page_reason(url) == "", url
