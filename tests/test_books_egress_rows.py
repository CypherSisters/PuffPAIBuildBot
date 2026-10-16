"""Security pass on the two book rows, wikibooks_books and archive_books.

Each request goes through the real ``source_fetch.get_small`` guards with a
made-up transport and address book (no network): only the row's own host,
a redirect to anywhere else refused, too big refused, and the outside
traffic log keeps the host only.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json

import pytest


class _Answer:
    def __init__(self, status=200, body=b"", headers=None):
        self.status = status
        self.headers = dict(headers or {})
        self._body = body

    def read(self, amt=-1):
        return self._body if amt is None or amt < 0 else self._body[:amt]

    def close(self):
        pass


@pytest.fixture
def wire(monkeypatch):
    """``(getter_for, asked, logged)``: a getter through the real guards."""
    from puffpai import source_fetch
    from puffpai.security import egress_ledger, outside_switches

    source_fetch.clear_caches()
    monkeypatch.setattr(outside_switches, "is_off", lambda _sid: False)
    logged: list[dict] = []
    monkeypatch.setattr(egress_ledger, "record",
                        lambda service, **f: logged.append(dict(f, service=service)) or True)
    asked: list[str] = []
    answers: dict[str, _Answer] = {}

    def transport(url, headers, timeout):
        asked.append(url)
        for key, answer in answers.items():
            if key in url:
                return answer
        return _Answer(404)

    def getter_for(service):
        def getter(url, **kwargs):
            return source_fetch.get_small(url, service=service, transport=transport,
                                          resolver=lambda _h: ["93.184.215.14"],
                                          sleeper=lambda _s: None, **kwargs)
        return getter
    return getter_for, asked, logged, answers


def test_each_row_is_pinned_to_its_own_host():
    from puffpai.catalogs import internet_archive, wikibooks
    from puffpai.security import egress_doors
    assert egress_doors.door("wikibooks_books").hosts == ("en.wikibooks.org",)
    assert egress_doors.door("archive_books").hosts == ("archive.org",)
    assert wikibooks._hosts() == {"en.wikibooks.org"}
    assert internet_archive._hosts() == {"archive.org"}


def test_a_redirect_to_another_host_is_refused(wire):
    from puffpai.catalogs import internet_archive, wikibooks
    getter_for, asked, logged, answers = wire
    answers["api.php"] = _Answer(302, headers={"Location": "https://evil.example/x"})
    answers["advancedsearch"] = _Answer(302, headers={"Location": "https://ia.example/y"})
    with pytest.raises(wikibooks.WikibooksError):
        wikibooks.ask({"list": "search"}, getter=getter_for(wikibooks.SERVICE))
    with pytest.raises(internet_archive.ArchiveError):
        internet_archive.search_live(["wedding"], getter=getter_for(internet_archive.SERVICE))
    assert not any("evil.example" in u or "ia.example" in u for u in asked)
    assert [r["code"] for r in logged if r["outcome"] == "refused"] == [
        "not_allowed", "not_allowed"]


def test_too_big_is_refused_not_cut(wire, monkeypatch):
    from puffpai.catalogs import internet_archive, wikibooks
    from puffpai.knowledge import text_books
    getter_for, _asked, _logged, answers = wire
    answers["api.php"] = _Answer(200, json.dumps({"query": {"search": []},
                                                  "pad": "x" * 100}).encode())
    with pytest.raises(wikibooks.WikibooksError):
        wikibooks.ask({"list": "search"}, getter=getter_for(wikibooks.SERVICE),
                      max_bytes=50)
    monkeypatch.setattr(text_books, "MAX_BOOK_BYTES", 40)
    answers["_djvu.txt"] = _Answer(200, b"<pre>" + b"a" * 200 + b"</pre>")
    with pytest.raises(internet_archive.ArchiveError):
        internet_archive.pages({"id": "archive:oldbook1900"},
                               getter=getter_for(internet_archive.SERVICE))


def test_the_log_keeps_the_host_never_the_search_words(wire):
    from puffpai.catalogs import internet_archive, wikibooks
    getter_for, asked, logged, answers = wire
    answers["api.php"] = _Answer(200, b'{"query": {"search": []}}')
    answers["advancedsearch"] = _Answer(200, b'{"response": {"docs": []}}')
    wikibooks.search_live(["knitting"], getter=getter_for(wikibooks.SERVICE))
    internet_archive.search_live(["knitting"], getter=getter_for(internet_archive.SERVICE))
    assert any("knitting" in u for u in asked)
    assert {r["service"] for r in logged} == {"wikibooks_books", "archive_books"}
    assert all("knitting" not in json.dumps(r) for r in logged)
    assert {r["host"] for r in logged} == {"en.wikibooks.org", "archive.org"}


def test_the_archive_text_is_read_as_plain_text():
    from puffpai.catalogs import internet_archive
    body = b"<html><pre>The Best Man &lt;script&gt;x&lt;/script&gt; &amp; ushers</pre></html>"
    assert internet_archive.stream_text(body) == "The Best Man <script>x</script> & ushers"
