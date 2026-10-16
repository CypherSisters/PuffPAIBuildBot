"""OpenStax: licenses mapped by code, personal-use books labelled, pages read safely."""
from __future__ import annotations

import json

import pytest

from puffpai.catalogs import openstax as o


@pytest.mark.parametrize("name,version,url,expected", [
    ("Creative Commons Attribution License", "4.0", "", "CC-BY-4.0"),
    ("Creative Commons Attribution-NonCommercial-ShareAlike License", "4.0", "",
     "CC-BY-NC-SA-4.0"),
    ("Creative Commons Attribution License", None,
     "https://creativecommons.org/licenses/by/4.0/", "CC-BY-4.0"),
    ("Creative Commons Attribution License", None, "", ""),
    (None, "4.0", "", ""),
    ("All rights reserved", "4.0", "", ""),
])
def test_licenses_map_to_spdx_or_nothing(name, version, url, expected):
    assert o.spdx(name, version, url) == expected


def test_a_non_commercial_book_is_labelled_free_for_personal_use_only():
    book = {"slug": "astronomy-2e", "title": "Astronomy 2e", "state": "live",
            "license": "CC-BY-NC-SA-4.0", "subjects": ["Science"], "pages": 400}
    cand = o.candidate(book)
    assert cand["name"].endswith("(free for personal use only)")
    assert cand["commercial_ok"] is False and cand["personal_use_only"] is True
    assert "free for personal use only" in cand["license_plain"]
    assert cand["size_bytes"] == 400 * o.TEXT_PER_PAGE and cand["fetchable"]


def test_a_book_with_no_license_or_no_pages_is_not_fetchable():
    base = {"slug": "x-book", "title": "X", "state": "live", "subjects": [], "pages": 10}
    assert not o.candidate(dict(base, license=""))["fetchable"]
    assert not o.candidate(dict(base, license="CC-BY-4.0", pages=None))["fetchable"]


def test_the_book_list_is_checked_as_untrusted_data():
    body = json.dumps({"items": [
        {"meta": {"slug": "astronomy", "locale": "en"}, "title": "<b>Astronomy</b>",
         "book_state": "retired", "license_name": "Creative Commons Attribution License",
         "license_version": "4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
         "book_subjects": [{"subject_name": "Science"}], "description": "<p>Stars</p>"},
        {"meta": {"slug": "../../etc", "locale": "en"}, "title": "bad", "book_state": "live"},
        {"meta": {"slug": "fizyka", "locale": "pl"}, "title": "pl", "book_state": "live"},
        {"meta": {"slug": "gone", "locale": "en"}, "title": "gone", "book_state": "deprecated"},
    ]}).encode()
    rows = o.parse_list(body)
    assert [r["slug"] for r in rows] == ["astronomy"]
    assert rows[0]["title"] == "Astronomy" and rows[0]["summary"] == "Stars"
    assert rows[0]["license"] == "CC-BY-4.0"


def test_only_the_books_own_pages_are_read_from_the_sitemap():
    xml = ("<urlset><url><loc>https://openstax.org/books/astronomy/pages/1-introduction</loc></url>"
           "<url><loc>https://evil.example/books/astronomy/pages/x</loc></url>"
           "<url><loc>https://openstax.org/books/other/pages/y</loc></url>"
           "<url><loc>https://openstax.org/books/astronomy/pages/../../admin</loc></url></urlset>")
    assert o.page_urls(xml.encode(), "astronomy") == [
        "https://openstax.org/books/astronomy/pages/1-introduction"]


def test_page_text_takes_the_page_and_nothing_that_runs():
    html = (b"<html><head><title>1.1 The Nature of Astronomy | Astronomy</title></head>"
            b"<body><nav>Menu</nav><div data-type='page'><h1>1.1 The Nature</h1>"
            b"<p>Astronomy is the study of objects beyond Earth.<br>Second line.</p>"
            b"<script>alert(1)</script><img src=x><p>More text.</p></div>"
            b"<footer>Footer</footer></body></html>")
    title, text = o.page_text(html)
    assert title == "1.1 The Nature of Astronomy"
    assert "Astronomy is the study" in text and "More text." in text
    assert "alert" not in text and "Menu" not in text and "Footer" not in text


def test_matching_needs_a_title_or_subject_not_a_blurb_word():
    got = o.find(["competitive"])
    assert got["candidates"] == []
    space = o.find(["space", "telescope", "astronomy"])
    assert [c["id"] for c in space["candidates"]][:1] == ["openstax:astronomy"]


def test_pages_are_fetched_one_at_a_time_from_openstax_only():
    calls = []

    def fake(url, *, allowed_hosts, max_bytes, honour_robots):
        calls.append(url)
        assert allowed_hosts == {"openstax.org"} and honour_robots is True
        if url.endswith(".xml"):
            return (b"<loc>https://openstax.org/books/astronomy/pages/a</loc>"
                    b"<loc>https://openstax.org/books/astronomy/pages/b</loc>"), "ok"
        return b"<div data-type='page'><p>Words about stars.</p></div>", "ok"

    cand = o.candidate({"slug": "astronomy", "title": "Astronomy", "state": "retired",
                        "license": "CC-BY-4.0", "subjects": [], "pages": 2})
    pages = list(o.pages(cand, getter=fake))
    assert [p["url"] for p in pages] == ["https://openstax.org/books/astronomy/pages/a",
                                         "https://openstax.org/books/astronomy/pages/b"]
    assert calls[0] == o.sitemap_url("astronomy")
    meta = o.book_meta(cand)
    assert meta["commercial_use"] is True and meta["personal_use_only"] is False
