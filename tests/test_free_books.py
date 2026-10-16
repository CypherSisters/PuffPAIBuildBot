"""More free books: Wikibooks, the Internet Archive, and "Books and guides"
cards in the find step, the Add page and chat. Recorded library answers only
(tests/fixtures/books, recorded from en.wikibooks.org and
archive.org, trimmed): no test goes online.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import datetime
import json
import os
import urllib.parse
import zipfile
from pathlib import Path

import pytest

FIX = Path(__file__).resolve().parent / "fixtures" / "books"


class Libraries:
    """A library getter (``source_fetch.get_small``'s shape) answering from
    the recorded files. Anything not recorded does not answer."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def _file(self, name: str):
        p = FIX / name
        return (p.read_bytes(), "ok") if p.exists() else (None, "not recorded")

    def __call__(self, url, *, allowed_hosts, max_bytes, honour_robots=True):
        self.urls.append(url)
        u = urllib.parse.urlsplit(url)
        assert u.hostname in allowed_hosts, (u.hostname, allowed_hosts)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if u.hostname == "en.wikibooks.org":
            if q.get("list") == "search":
                return self._file(f"wb_search_{q['srsearch']}.json")
            if q.get("generator") == "allpages":
                return self._file("wb_sub_" + q["gapprefix"].strip("/").replace(" ", "_")
                                  + ".json")
            if q.get("list") == "allpages":
                return self._file("wb_titles_" + q["apprefix"].strip("/").replace(" ", "_")
                                  + ".json")
            if "extracts" in q.get("prop", ""):
                return self._file("wb_page_" + q["titles"].replace(" ", "_")
                                  .replace("/", "~") + ".json")
            if q.get("prop") == "info":
                return self._file("wb_info_Machine_Knitting.json")
        if u.hostname == "archive.org":
            if u.path == "/advancedsearch.php" and "knitting" in q["q"]:
                return self._file("ia_search_knitting.json")
            if u.path == "/advancedsearch.php" and "identifier:" in q["q"]:
                return self._file("ia_search_knitting.json")
            if u.path.startswith("/stream/"):
                return self._file(f"ia_stream_{u.path.split('/')[2]}.html")
        return None, "not recorded"


@pytest.fixture
def libs(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PUFFPAI_FIND_BOOKS", "on")
    from puffpai.finding import books
    fake = Libraries()
    monkeypatch.setattr(books, "GETTER", fake)
    monkeypatch.setattr(books, "EMBED", False)    # no reading helper: the word rule
    return fake


# -- the two new libraries -------------------------------------------------------------

def test_wikibooks_lists_a_book_only_when_its_own_title_matches(libs):
    from puffpai.catalogs import wikibooks
    got = wikibooks.find(["knitting"], live=True, getter=libs)
    names = [c["name"] for c in got["candidates"]]
    # The search answered chapters of 28 books (Harry Potter, Statistics ...);
    # only the book whose own title says knitting is listed.
    assert names == ["Machine Knitting"]
    cand = got["candidates"][0]
    assert cand["id"] == "wikibooks:192334" and cand["pages"] == 14
    assert cand["license"] == "CC-BY-SA-4.0" and cand["commercial_ok"] is True
    assert 0 < cand["size_bytes"] < 1024 * 1024
    assert wikibooks.find(["knitting"], live=False)["candidates"] == []


def test_internet_archive_offers_only_old_public_domain_scans(libs):
    from puffpai.catalogs import internet_archive as ia
    url = ia.search_url(["knitting"])
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["q"][0]
    assert "possible-copyright-status:NOT_IN_COPYRIGHT" in q and "mediatype:texts" in q
    assert f"TO {datetime.date.today().year - 96}]" in q
    got = ia.find(["knitting"], live=True, getter=libs)
    assert 0 < len(got["candidates"]) <= ia.MAX_PROPOSED
    for cand in got["candidates"]:
        assert cand["year"] <= ia.public_domain_year()
        assert cand["license_plain"].startswith("public domain in the USA")
        assert cand["downloads"] > 0
    # best match first, then the most downloaded
    downloads = [c["downloads"] for c in got["candidates"]]
    assert downloads == sorted(downloads, reverse=True)
    # an uploader's own licence or a recent year is never trusted
    body = json.dumps({"response": {"docs": [
        {"identifier": "new-book", "title": "Knitting now", "year": 1990, "downloads": 9},
        {"identifier": "../etc", "title": "Knitting", "year": 1900, "downloads": 9},
        {"identifier": "ok-book", "title": "Knitting", "year": "1901", "downloads": 9}]}})
    assert [r["identifier"] for r in ia.parse_results(body.encode())] == ["ok-book"]


def test_offline_search_never_asks_the_online_only_libraries():
    from puffpai.catalogs import knowledge_resolver as kr
    got = kr.find("knitting", live=False)
    assert "wikibooks" not in got["tiers"] and "archive" not in got["tiers"]


# -- "Books and guides" cards ------------------------------------------------------------

def test_books_for_a_job_name_what_where_licence_size_and_format(libs):
    from puffpai.finding import books
    finds = books.search("knitting")
    assert 0 < len(finds) <= books.PER_JOB
    by_library: dict[str, int] = {}
    for f in finds:
        by_library[f["site_name"]] = by_library.get(f["site_name"], 0) + 1
        assert f["type"] == "book" and f["can"] == "add_book"
        assert f["type_words"] in ("book", "guide", "Q&A collection", "textbook")
        assert f["licence_words"] and f["size_words"] and f["format_words"]
        assert f["what"] and f["site_name"] in f["line"] and f["size_words"] in f["line"]
        assert not f.get("can_trade")
    assert max(by_library.values()) <= books.PER_LIBRARY
    assert {"Wikibooks", "the Internet Archive"} <= set(by_library)
    guide = next(f for f in finds if f["site_name"] == "Wikibooks")
    assert guide["type_words"] == "guide"


def test_at_most_two_small_open_books_start_ticked():
    from puffpai.finding import books
    def find(n, *, tickable=True, score=5, downloads=0):
        return {"id": f"book:x{n}", "type": "book", "tickable": tickable, "score": score,
                "popularity": {"downloads": downloads}, "rank": n}
    finds = [find(1, score=3), find(2, downloads=50), find(3), find(4, tickable=False, score=9)]
    picked = books.pick_ticked(finds)
    assert [f["id"] for f in picked] == ["book:x2", "book:x3"]
    assert sum(1 for f in finds if f.get("ticked")) == 2
    big = {"id": "kiwix:x", "tier": "kiwix", "fetchable": True, "commercial_ok": True,
           "size_bytes": 900 * 1024 * 1024, "score": 9, "name": "Knitting Q&A",
           "what_it_is": "Knitting questions"}
    personal = dict(big, size_bytes=10, commercial_ok=False)
    assert not books.tickable(big, "knitting")
    assert not books.tickable(personal, "knitting")
    assert books.tickable(dict(big, size_bytes=10), "knitting")
    # filed under the subject is not enough: its own name has to say it
    assert not books.tickable(dict(big, size_bytes=10, name="Type specimens",
                                   subjects=["Printing"]), "3d printing")


def test_find_things_shows_books_and_guides_with_at_most_two_ticked(libs):
    from puffpai.finding import find
    from puffpai.finding import wording as W
    assert find.clean_kinds(["book"]) == ["book"] and find.sites_for(["book"]) == ("libraries",)
    result = find.find_things("knitting", ["books"])
    types = [t for job in result["jobs"] for t in job["types"]]
    assert [t["type"] for t in types] == ["book"]
    assert types[0]["type_words"] == "books and guides"
    finds = types[0]["finds"]
    assert len(finds) <= 5 and "raw" not in finds[0]
    ticked = [f for f in finds if f.get("ticked")]
    assert 0 < len(ticked) <= 2
    assert all(f["tickable"] for f in ticked)
    assert "free libraries" in W.FINDING_INTRO
    text = find.as_text(result)
    assert "[book:" in text and "books and guides" in text


# -- her yes: fetched, learned, a thing ---------------------------------------------------

def test_a_yes_to_a_book_orders_it_and_it_becomes_a_thing(libs, monkeypatch, tmp_path):
    from puffpai import things
    from puffpai.finding import find, install
    from puffpai.knowledge import find_more
    monkeypatch.setattr(find_more, "knowledge_folder", lambda create=False: str(tmp_path))
    monkeypatch.setattr(find_more, "_learn_rest_in_background", lambda folder: None)
    result = find.find_things("knitting", ["book"])
    f = next(x for t in result["jobs"][0]["types"] for x in t["finds"]
             if x["id"].startswith("book:archive:"))
    from puffpai.finding import tools
    assert tools.install_question({"id": f["id"]})["title"].startswith("Add ")
    card = install.preview(f["id"])
    assert card["question"] and card["can"] == "add_book"
    joined = " ".join(card["lines"])
    assert "public domain in the USA" in joined and "up to 25 MB" in joined
    assert "puffpai sources add" not in joined
    out = install.install_thing(f["id"], via="add_page")
    assert out["status"] == "added", out
    ident = f["id"].split(":", 1)[1]
    assert ident in find_more.pending_orders()
    entry = install.entries()[-1]
    assert entry["type"] == "book" and entry["undo"]["kind"] == "book"
    tid = things.id_for_install(entry["undo"])
    assert things.get(tid) is not None and things.get(tid)["kind"] == "book"
    assert out.get("page")
    # the same book again is not ordered twice; a book stays until the Knowledge page
    assert install.install_thing(f["id"])["line"].endswith("so it was not added again.")
    undo = install.undo(entry["entry"])
    assert not undo["ok"] and "Knowledge page" in undo["line"]


def test_fetched_books_are_saved_and_learned_with_the_existing_pipeline(libs, tmp_path):
    from puffpai.catalogs import internet_archive as ia
    from puffpai.catalogs import wikibooks
    from puffpai.knowledge import book_search, text_books
    books_dir = tmp_path / "books"
    archive = ia.find(["knitting"], live=True, getter=libs)["candidates"]
    spool = next(c for c in archive if c["identifier"] == "spoolknitting00mccorich")
    guide = wikibooks.find(["knitting"], live=True, getter=libs)["candidates"][0]
    for cand, module in ((spool, ia), (guide, wikibooks)):
        record = text_books.write_book(str(tmp_path), module.book_meta(cand),
                                       module.pages(cand, getter=libs))
        assert record["license"] and record["credit"] and record["items"]
        got = text_books.learn(record["_path"], fts_dir=str(books_dir))
        assert got["status"] == "complete" and got["docs"] >= 1
    hits = book_search.word_hits("toboggan cap", fts_dir=str(books_dir))
    assert hits and hits[0]["library"] == "Spool knitting"
    hits = book_search.word_hits("machine knitting edges", fts_dir=str(books_dir))
    assert any("Wikibooks" in h["library"] for h in hits)


def test_add_page_search_includes_the_new_libraries(libs, monkeypatch):
    from puffpai.catalogs import knowledge_resolver as kr
    from puffpai.knowledge import find_more
    got = kr.find("knitting", live=True, getter=libs, reach_getter=libs)
    tiers = {kr.tier_of(c) for c in got["candidates"]}
    assert {"wikibooks", "archive"} <= tiers
    assert "Wikibooks" in find_more.looked_in_words(got)
    assert "the Internet Archive" in find_more.looked_in_words(got)


# -- EPUB --------------------------------------------------------------------------------

def test_epub_text_in_reading_order(tmp_path):
    from puffpai.file_processor import extract_text
    path = tmp_path / "book.epub"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml",
                   '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/>'
                   "</rootfiles></container>")
        z.writestr("OEBPS/content.opf",
                   '<package><metadata><dc:title>Knots</dc:title></metadata><manifest>'
                   '<item id="b" href="text/two.xhtml"/><item id="a" href="text/one.xhtml"/>'
                   '</manifest><spine><itemref idref="a"/><itemref idref="b"/></spine></package>')
        z.writestr("OEBPS/text/one.xhtml", "<html><body><h1>One</h1><p>Tie a reef knot."
                   "</p><script>alert(1)</script></body></html>")
        z.writestr("OEBPS/text/two.xhtml", "<html><body><p>Then a bowline.</p></body></html>")
    text, meta = extract_text(str(path))
    assert text.index("reef knot") < text.index("bowline")
    assert "alert" not in text and meta["title"] == "Knots" and meta["chapters"] == 2
