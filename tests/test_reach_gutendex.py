"""Project Gutenberg: the saved list, the live search, the book itself."""
from __future__ import annotations

import json
import urllib.parse

import pytest

from puffpai.catalogs import gutendex as g

BOOK = """The Project Gutenberg eBook of Handbook of Wool Knitting

This eBook is for the use of anyone anywhere in the United States ...

*** START OF THE PROJECT GUTENBERG EBOOK HANDBOOK OF WOOL KNITTING ***

HANDBOOK OF WOOL KNITTING

Cast on 40 stitches.

*** END OF THE PROJECT GUTENBERG EBOOK HANDBOOK OF WOOL KNITTING ***

Section 1. General Terms of Use ... Project Gutenberg-tm License ...
"""


def test_the_saved_list_finds_knitting_books_offline():
    rows = g.search_snapshot(["knitting", "crochet", "pattern"])
    assert 1 <= len(rows) <= g.MAX_PROPOSED
    for row in rows:
        text = (row["title"] + " " + " ".join(row["subjects"])).lower()
        assert "knit" in text or "crochet" in text


def test_one_shared_word_is_not_enough_for_a_goal_of_several_words():
    """'Social Civics' shares 'civic' with a 2014 Honda Civic, and nothing else."""
    ids = [r["id"] for r in g.search_snapshot(["honda", "civic", "maintenance", "recall"])]
    assert all("Civics" not in r["title"] for r in g.search_snapshot(["honda", "civic"]))
    assert ids == []


def test_literature_is_left_out_of_the_saved_list():
    snap = g.load_snapshot()
    assert snap["taken_at"] == "2026-09-24" and len(snap["books"]) > 20000
    csv_text = ("Text#,Type,Issued,Title,Language,Authors,Subjects,LoCC,Bookshelves\n"
                "1,Text,2000,A Novel,en,Someone,Fiction,PR,\n"
                "2,Text,2000,Knitting,en,Someone,Knitting -- Patterns,TT,Category: Crafts\n"
                "3,Text,2000,Tricot,fr,Someone,Knitting,TT,\n")
    built = g.build_snapshot(csv_text, "2026-01-01")
    assert [b[0] for b in built["books"]] == [2]
    assert "Crafts" in built["subjects"]


def test_gutendex_answers_are_checked_and_copyrighted_books_dropped():
    body = json.dumps({"results": [
        {"id": 1, "title": "Free", "languages": ["en"], "copyright": False,
         "authors": [{"name": "A"}], "subjects": ["Knitting"], "bookshelves": []},
        {"id": 2, "title": "Not free", "languages": ["en"], "copyright": True},
        {"id": 3, "title": "Unknown", "languages": ["en"], "copyright": None},
        {"id": "4", "title": "Bad id", "languages": ["en"], "copyright": False},
        {"id": 5, "title": "French", "languages": ["fr"], "copyright": False},
    ]}).encode()
    assert [r["id"] for r in g.parse_results(body)] == [1]


def test_the_saved_catalog_is_used_even_when_live_is_allowed():
    # The catalog saved on this computer is the knowledge path; the
    # live Gutendex search failed often, so it is not asked while the list is there.
    seen = []
    got = g.find(["knitting", "crochet"], live=True,
                 getter=lambda url, **kw: seen.append(url) or (None, "not asked"))
    assert got["searched"] == "snapshot" and got["candidates"] and seen == []
    assert got["note"] == ""


def test_the_live_search_asks_only_gutendex_when_no_list_is_saved(monkeypatch):
    monkeypatch.setattr(g, "load_snapshot", lambda path=None: {"books": [], "taken_at": None})
    seen = []

    def fake(url, *, allowed_hosts, max_bytes, honour_robots):
        seen.append(url)
        assert allowed_hosts == {"gutendex.com"}
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        assert query["copyright"] == "false" and query["languages"] == "en"
        return json.dumps({"results": [
            {"id": 9, "title": "Knitting and crochet", "languages": ["en"],
             "copyright": False, "authors": [], "subjects": ["Knitting"],
             "bookshelves": ["Crafts"]}]}).encode(), "ok"

    got = g.find(["knitting", "crochet"], live=True, getter=fake)
    assert got["searched"] == "live" and [c["id"] for c in got["candidates"]] == ["gutenberg:9"]
    assert len(seen) == g.LIVE_TERM_LIMIT

    down = g.find(["knitting", "crochet"], live=True,
                  getter=lambda url, **kw: (None, "timed out"))
    assert down["searched"] == "snapshot" and "could not be reached" in down["note"]
    assert down["candidates"] == []


def test_the_book_address_is_built_by_code_from_the_number():
    assert g.text_url(26113) == "https://www.gutenberg.org/cache/epub/26113/pg26113.txt"
    assert g.split_id("gutenberg:26113") == 26113
    for bad in ("gutenberg:0", "gutenberg:abc", "gutenberg:1/../x", "gutenberg:12345678"):
        assert g.split_id(bad) is None


def test_boilerplate_is_cut_and_the_book_kept():
    text = g.strip_boilerplate(BOOK)
    assert text.startswith("HANDBOOK OF WOOL KNITTING") and "Cast on 40" in text
    assert "Project Gutenberg" not in text and "License" not in text


def test_a_copyrighted_or_unmarked_file_is_refused():
    with pytest.raises(g.GutenbergError, match="copyright"):
        g.strip_boilerplate("This is a COPYRIGHTED Project Gutenberg eBook\n" + BOOK)
    with pytest.raises(g.GutenbergError, match="starts"):
        g.strip_boilerplate("just some text")


def test_pages_fetch_through_the_download_host_with_robots_honoured():
    calls = []

    def fake(url, *, allowed_hosts, max_bytes, honour_robots):
        calls.append((url, allowed_hosts, max_bytes, honour_robots))
        return BOOK.encode("utf-8"), "ok"

    cand = g.candidate({"id": 26113, "title": "Handbook of Wool Knitting and Crochet",
                        "author": "Anonymous", "subjects": ["Knitting"]})
    pages = g.pages(cand, getter=fake)
    assert calls == [(g.text_url(26113), {"www.gutenberg.org"}, 25 * 1024 * 1024, True)]
    assert pages[0]["url"] == "https://www.gutenberg.org/ebooks/26113"
    meta = g.book_meta(cand)
    assert "ebook #26113" in meta["credit"] and meta["license"] == g.LICENSE


def test_a_candidate_from_the_saved_list_resolves_by_id_offline():
    cand = g.candidate_by_id("gutenberg:26113")
    assert cand is not None and cand["fetchable"] and cand["license_plain"].startswith(
        "public domain in the USA")
    assert g.candidate_by_id("gutenberg:9999999") is None
