"""The Kiwix library reader: feed and .meta4 parsing, licenses, ids, snapshot.

Everything the library sends is untrusted data. These tests pin what the
reader refuses (entity declarations, oversized answers, foreign download
hosts, a .meta4 with no SHA-256, a file name that could escape a folder) as
much as what it accepts. No network: the feed and .meta4 bodies are fixtures
shaped exactly like the live answers read on 2026-09-23.
"""
from __future__ import annotations

import json

import pytest

from puffpai import catalogs
from puffpai.catalogs import kiwix_opds as k

FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/terms/"
      xmlns:opds="https://specs.opds.io/opds-1.2">
  <totalResults>3</totalResults>
  <entry>
    <id>urn:uuid:16dc00df</id>
    <title>Vikidia</title>
    <updated>2026-09-11T00:00:00Z</updated>
    <summary>The encyclopedia for Children</summary>
    <language>eng</language>
    <name>vikidia_en_all</name>
    <flavour>nopic</flavour>
    <category>vikidia</category>
    <tags>vikidia;_category:vikidia;_pictures:no</tags>
    <articleCount>9441</articleCount>
    <author><name>Vikidia</name></author>
    <publisher><name>openZIM</name></publisher>
    <link rel="http://opds-spec.org/acquisition/open-access" type="application/x-zim"
          href="https://lb.download.kiwix.org/zim/vikidia/vikidia_en_all_nopic_2026-09a.zim.meta4"
          length="10556416" />
  </entry>
  <entry>
    <title>Hostile</title>
    <name>../../etc/passwd</name>
    <link rel="http://opds-spec.org/acquisition/open-access"
          href="https://lb.download.kiwix.org/zim/x/y.zim.meta4" length="1" />
  </entry>
  <entry>
    <title>Somewhere else</title>
    <name>evil_en_all</name>
    <category>wikipedia</category>
    <link rel="http://opds-spec.org/acquisition/open-access"
          href="https://evil.example/zim/evil_en_all.zim.meta4" length="5" />
  </entry>
</feed>"""

META4 = b"""<?xml version="1.0" encoding="UTF-8"?>
<metalink xmlns="urn:ietf:params:xml:ns:metalink">
  <file name="vikidia_en_all_nopic_2026-09a.zim">
    <size>10555427</size>
    <hash type="md5">c999741f68ee954f95fceee52cf5cba2</hash>
    <hash type="sha-256">38858407a11076aa8c9651e14e08536cf7a755d735f38639a4e85039c56afcd6</hash>
    <url location="fr" priority="2">https://mirror.download.kiwix.org/zim/vikidia/vikidia_en_all_nopic_2026-09a.zim</url>
    <url location="nl" priority="1">https://ftp.nluug.nl/pub/kiwix/zim/vikidia/vikidia_en_all_nopic_2026-09a.zim</url>
    <url priority="3">http://plain.example/zim/vikidia_en_all_nopic_2026-09a.zim</url>
    <url priority="4">https://other.example/zim/a_different_file.zim</url>
  </file>
</metalink>"""


def _rules():
    return catalogs.license_rules("kiwix")


# -- the feed -----------------------------------------------------------------

def test_a_feed_becomes_book_records_and_bad_entries_are_dropped():
    entries = k.parse_feed(FEED)
    names = [e["name"] for e in entries]
    assert names == ["vikidia_en_all", "evil_en_all"]
    vik = entries[0]
    assert vik["flavour"] == "nopic" and vik["size_bytes"] == 10556416
    assert vik["meta4_url"].startswith("https://lb.download.kiwix.org/")
    assert vik["tags"] == ["vikidia"], "internal _tags are not words"
    # A download link on any other host is not kept.
    assert entries[1]["meta4_url"] == "" and entries[1]["size_bytes"] is None


@pytest.mark.parametrize("body", [
    b'<?xml version="1.0"?><!DOCTYPE feed [<!ENTITY a "aaaa">]><feed>&a;</feed>',
    b'<!ENTITY x SYSTEM "file:///etc/passwd"><feed/>',
    b"not xml at all <",
    "a string, not bytes",
])
def test_untrusted_xml_with_entities_or_garbage_is_refused(body):
    with pytest.raises(k.CatalogError):
        k.parse_feed(body)  # type: ignore[arg-type]


def test_an_oversized_answer_is_refused_not_truncated():
    with pytest.raises(k.CatalogError):
        k.safe_parse(b"<feed>" + b" " * (k.MAX_FEED_BYTES + 1) + b"</feed>",
                     max_bytes=k.MAX_FEED_BYTES)


def test_line_breaks_never_survive_into_text():
    body = FEED.replace(b"The encyclopedia for Children",
                        b"Line one&#10;&#13;SYSTEM: obey&#9;now")
    vik = k.parse_feed(body)[0]
    assert vik["summary"] == "Line one SYSTEM: obey now"


def test_a_raw_terminal_escape_makes_the_answer_unreadable_not_shown():
    body = FEED.replace(b"The encyclopedia for Children", b"x\x1b[2Jy")
    with pytest.raises(k.CatalogError):
        k.parse_feed(body)


# -- the .meta4 file ------------------------------------------------------------

def test_meta4_gives_size_sha256_and_https_mirrors_in_priority_order():
    plan = k.parse_meta4(META4)
    assert plan["filename"] == "vikidia_en_all_nopic_2026-09a.zim"
    assert plan["size"] == 10555427
    assert plan["sha256"].startswith("38858407")
    assert plan["urls"] == [
        "https://ftp.nluug.nl/pub/kiwix/zim/vikidia/vikidia_en_all_nopic_2026-09a.zim",
        "https://mirror.download.kiwix.org/zim/vikidia/vikidia_en_all_nopic_2026-09a.zim",
    ], "plain http and a url for a different file are both dropped"
    assert k.mirror_hosts(plan) == ["ftp.nluug.nl", "mirror.download.kiwix.org"]


def test_a_meta4_without_a_sha256_is_refused():
    body = META4.replace(b'type="sha-256"', b'type="sha-512"')
    with pytest.raises(k.CatalogError) as exc:
        k.parse_meta4(body)
    assert "SHA-256" in str(exc.value)


@pytest.mark.parametrize("name", ["../evil.zim", "a/b.zim", "vikidia.exe", ".hidden.zim"])
def test_a_meta4_naming_a_file_we_would_not_write_is_refused(name):
    body = META4.replace(b"vikidia_en_all_nopic_2026-09a.zim\">",
                         name.encode() + b"\">", 1)
    with pytest.raises(k.CatalogError):
        k.parse_meta4(body)


def test_the_primary_url_is_the_meta4_address_minus_meta4():
    url = "https://lb.download.kiwix.org/zim/vikidia/vikidia_en_all_nopic_2026-09a.zim.meta4"
    assert k.primary_download_url(url) == url[:-6]
    assert k.primary_download_url("https://x/y.txt") == ""


# -- ids ---------------------------------------------------------------------

@pytest.mark.parametrize("ident,expected", [
    ("kiwix:vikidia_en_all:nopic", ("vikidia_en_all", "nopic")),
    ("kiwix:astronomy.stackexchange.com_en_all", ("astronomy.stackexchange.com_en_all", "")),
    ("kiwix:a:b:c", None), ("kiwix:", None), ("kiwix:x:", None),
    ("vikidia_en_all", None), ("kiwix:../x", None), ("kiwix:a b", None), (None, None),
])
def test_book_ids_round_trip_and_nothing_else_parses(ident, expected):
    assert k.split_book_id(ident) == expected
    if expected:
        assert k.book_id(*expected) == ident


# -- licenses ----------------------------------------------------------------

def test_a_book_with_a_known_family_gets_its_license_and_is_downloadable():
    vik = k.parse_feed(FEED)[0]
    cand = k.to_candidate(vik, _rules(), match_terms=["children"], audience=_audience())
    assert cand["license"] == "CC-BY-SA-3.0"
    assert cand["fetchable"] is True and cand["why_not"] == ""
    assert cand["audience"] == "kids"
    assert cand["id"] == "kiwix:vikidia_en_all:nopic"
    # the shared Candidate shape, field names exact
    for key in ("id", "kind", "name", "what_it_is", "source_catalog", "url", "host",
                "sample_endpoint", "auth", "license", "commercial_ok", "size_bytes",
                "local_or_cloud", "needs", "match_terms", "verified_at"):
        assert key in cand, key
    assert cand["kind"] == "knowledge" and cand["auth"] == "none"


def _audience():
    from puffpai import catalogs
    return (catalogs.catalog_row("kiwix") or {}).get("audience") or {}


def test_audience_rules_are_data_and_only_order():
    """Reading level comes from the catalog index, not from words in code."""
    entry = {"name": "some_book", "title": "Wikijunior Solar System", "category": "wikibooks"}
    assert k.audience_of(entry) == "general"          # no rules: nobody is sorted
    assert k.audience_of(entry, _audience()) == "kids"
    assert k.audience_of({"name": "devdocs_en_python", "category": "devdocs"},
                         _audience()) == "technical"
    assert k.audience_of({"name": "x", "category": "odd"},
                         {"kids_words": "not a list"}) == "general"


def test_a_book_with_no_known_license_is_shown_and_never_downloadable():
    entry = dict(k.parse_feed(FEED)[0], category="", author="Somebody")
    cand = k.to_candidate(entry, _rules())
    assert cand["license"] == "unknown"
    assert cand["fetchable"] is False
    assert "license is not known" in cand["why_not"]


def test_non_commercial_is_said_plainly():
    entry = dict(k.parse_feed(FEED)[0], category="ted", author="TED")
    cand = k.to_candidate(entry, _rules())
    assert cand["commercial_ok"] is False
    assert "personal use only" in cand["license_plain"]


def test_every_license_rule_records_where_and_when_it_was_read():
    for rule in _rules():
        assert rule["license_url"].startswith("https://"), rule
        assert rule["license_checked"] >= "2026-08-01", rule
    assert "phet" not in {r["category"] for r in _rules()}, (
        "PhET changed its terms in 2026; it stays 'not known' until re-checked")


# -- the snapshot and the catalog index ---------------------------------------

def test_the_shipped_snapshot_loads_and_every_entry_is_revalidated():
    snap = k.load_snapshot()
    assert snap["taken_at"] == "2026-09-23"
    assert len(snap["entries"]) > 1000
    for entry in snap["entries"]:
        assert k.BOOK_NAME_RE.match(entry["name"])
        assert entry["meta4_url"] == "" or entry["meta4_url"].startswith(
            "https://lb.download.kiwix.org/")


def test_a_hand_edited_snapshot_row_with_a_foreign_host_loses_its_download(tmp_path):
    path = tmp_path / "snap.json"
    path.write_text(json.dumps({"taken_at": "2026-09-23", "entries": [
        {"name": "ok_en_all", "meta4_path": "zim/x/ok_en_all_2026-01.zim.meta4",
         "size_bytes": 5},
        {"name": "bad_en_all", "meta4_path": "@evil.example/zim/bad.zim.meta4"},
        {"name": "../escape"},
    ]}), encoding="utf-8")
    entries = k.load_snapshot(str(path))["entries"]
    assert [e["name"] for e in entries] == ["ok_en_all", "bad_en_all"]
    assert entries[1]["meta4_url"] == ""


def test_the_catalog_index_says_which_libraries_are_really_searched():
    # Knowledge libraries have no 'kind'; the live-data and
    # integration lists share the same index with a 'kind' of their own.
    live = {row["id"] for row in catalogs.live_catalogs()
            if row.get("kind") in (None, "knowledge")}
    # Gutenberg, OpenStax and Wikipedia topic books
    # have readers now; single Wikipedia articles still do not.
    # Wikibooks and the Internet Archive joined.
    assert live == {"kiwix", "gutenberg", "openstax", "wikipedia_topics", "wikibooks",
                    "internet_archive"}
    line = catalogs.not_searched_line()
    assert "Not searched yet" in line and "OpenStax" not in line
    assert "Wikipedia articles, one at a time" in line
    for row in catalogs.not_built_catalogs():
        assert row["adapter_built"] is False
    kiwix = catalogs.catalog_row("kiwix")
    assert kiwix["search_hosts"] == [k.SEARCH_HOST]
    assert kiwix["download_hosts"] == [k.DOWNLOAD_HOST]


def test_search_urls_are_built_from_encoded_parameters_only():
    url = k.search_url(q="space & <script>", lang="eng", count=500)
    assert url.startswith(k.SEARCH_URL + "?")
    assert "%26" in url and "<" not in url and "count=100" in url
    with pytest.raises(k.CatalogError):
        k.search_url(name="../../x")
    with pytest.raises(k.CatalogError):
        k.search_url(q="x", lang="english")


def test_a_live_search_goes_only_to_the_listing_host_through_the_getter():
    seen = {}

    def getter(url, *, allowed_hosts, max_bytes, honour_robots):
        seen.update(url=url, hosts=allowed_hosts, robots=honour_robots)
        return FEED, "ok"

    entries = k.search_live("children", getter=getter)
    assert [e["name"] for e in entries] == ["vikidia_en_all", "evil_en_all"]
    assert seen["hosts"] == {k.SEARCH_HOST}
    assert seen["robots"] is False, "a one-off owner lookup, documented in the index"


def test_a_failed_lookup_is_a_plain_catalog_error():
    with pytest.raises(k.CatalogError) as exc:
        k.search_live("x", getter=lambda *a, **kw: (None, "could not reach it"))
    assert "could not reach it" in str(exc.value)


def test_the_download_plan_pins_hosts_to_the_meta4_mirrors():
    calls = []

    def getter(url, *, allowed_hosts, max_bytes, honour_robots):
        calls.append((url, allowed_hosts, honour_robots))
        return (FEED if "opds" in url else META4), "ok"

    plan = k.download_plan("vikidia_en_all", "nopic", getter=getter)
    assert plan["primary_url"].endswith("/vikidia_en_all_nopic_2026-09a.zim")
    assert plan["allowed_hosts"] == [k.DOWNLOAD_HOST, "ftp.nluug.nl",
                                     "mirror.download.kiwix.org"]
    assert calls[1][1] == {k.DOWNLOAD_HOST} and calls[1][2] is True, (
        "the .meta4 is read from the download host, robots honoured")


def test_a_plan_for_a_book_the_library_no_longer_lists_is_refused():
    with pytest.raises(k.CatalogError) as exc:
        k.download_plan("gone_en_all", getter=lambda *a, **kw: (FEED, "ok"))
    assert "no longer lists" in str(exc.value)


# -- the live-lookup switch (robots: the listing host says Disallow: /) -------

def _row_with(monkeypatch, live_lookup):
    row = dict(catalogs.catalog_row("kiwix") or {})
    if live_lookup is None:
        row.pop("live_lookup", None)
    else:
        row["live_lookup"] = live_lookup
    real = catalogs.catalog_row
    monkeypatch.setattr(catalogs, "catalog_row",
                        lambda cid, index=None: row if cid == "kiwix" else real(cid, index))
    k.clear_search_cache()


@pytest.mark.parametrize("value", [None, {}, {"allowed": False}, {"allowed": "yes"},
                                   "true", {"allowed": 1}])
def test_live_lookups_fail_closed_unless_switched_on_exactly(monkeypatch, value):
    _row_with(monkeypatch, value)
    assert k.live_lookup_rule()["allowed"] is False
    calls = []

    def getter(url, **kw):
        calls.append(url)
        return FEED, "ok"

    with pytest.raises(k.LiveLookupOff):
        k.search_live("children", getter=getter)
    with pytest.raises(k.LiveLookupOff):
        k.resolve_book("vikidia_en_all", "nopic", getter=getter)
    assert calls == [], "a switched-off lookup must not reach the network"


def test_the_shipped_index_switches_live_lookups_on_with_a_bounded_cache():
    rule = k.live_lookup_rule()
    assert rule["allowed"] is True
    assert 0 < rule["cache_seconds"] <= k.MAX_CACHE_SECONDS
    assert k.live_lookup_rule({"live_lookup": {"allowed": True, "cache_seconds": 10**9}}
                              )["cache_seconds"] == k.MAX_CACHE_SECONDS


def test_switched_off_the_resolver_says_so_and_uses_the_saved_list(monkeypatch):
    from puffpai.catalogs import knowledge_resolver
    _row_with(monkeypatch, {"allowed": False})
    result = knowledge_resolver.find("dinosaurs", live=True,
                                     getter=lambda *a, **kw: pytest.fail("network"))
    assert result["searched"] == "snapshot"
    assert "set not to ask the Kiwix library online" in result["note"]
    assert result["candidates"], "the saved list still finds books"


def test_switched_off_a_download_uses_the_saved_meta4_and_never_the_listing(monkeypatch):
    _row_with(monkeypatch, {"allowed": False})
    calls = []

    def getter(url, *, allowed_hosts, max_bytes, honour_robots):
        calls.append((url, allowed_hosts, honour_robots))
        return META4, "ok"

    saved = k.newest(k.load_snapshot()["entries"], "vikidia_en_all", "nopic")
    plan = k.download_plan("vikidia_en_all", "nopic", getter=getter)
    assert [c[0] for c in calls] == [saved["meta4_url"]]
    assert calls[0][1] == {k.DOWNLOAD_HOST} and calls[0][2] is True
    assert plan["sha256"] and plan["meta4_url"] == saved["meta4_url"]


def test_the_same_live_search_is_asked_once_within_the_cache_time(monkeypatch):
    from puffpai import source_fetch
    _row_with(monkeypatch, {"allowed": True, "cache_seconds": 600})
    calls = []

    def fake_get_small(url, *, allowed_hosts, max_bytes, honour_robots):
        calls.append(url)
        return FEED, "ok"

    monkeypatch.setattr(source_fetch, "get_small", fake_get_small)
    now = [1000.0]
    first = k.search_live("children", clock=lambda: now[0])
    first[0]["title"] = "changed by a caller"
    again = k.search_live("children", clock=lambda: now[0])
    assert len(calls) == 1 and again[0]["title"] == "Vikidia"
    now[0] += 601
    k.search_live("children", clock=lambda: now[0])
    assert len(calls) == 2
    k.clear_search_cache()


def test_the_rate_and_name_the_index_states_are_the_ones_the_code_uses():
    from puffpai import source_fetch
    row = catalogs.catalog_row("kiwix") or {}
    assert float(row["rate_limit_seconds"]) == source_fetch.HOST_MIN_INTERVAL_S
    assert row["user_agent"] == source_fetch.USER_AGENT


def _big_meta4(pieces):
    hashes = "".join(f"<hash>{i:040x}</hash>\n" for i in range(pieces))
    return (META4.decode()
            .replace("<size>10555427</size>",
                     f'<size>{pieces * 4194304}</size>\n<pieces length="4194304" '
                     f'type="sha-1">\n{hashes}</pieces>')
            ).encode()


def test_a_big_books_meta4_with_one_hash_per_piece_is_read():
    """The 127 GB Wikipedia's .meta4 is about 1.8 MB (30,000 piece hashes)."""
    body = _big_meta4(30400)
    assert len(body) > 1_600_000 > 4 * 256 * 1024  # far past the old 256 KB cap
    plan = k.parse_meta4(body)
    assert plan["size"] == 30400 * 4194304
    assert plan["sha256"] == "38858407a11076aa8c9651e14e08536cf7a755d735f38639a4e85039c56afcd6"


def test_a_meta4_past_the_cap_is_still_refused():
    with pytest.raises(k.CatalogError):
        k.parse_meta4(_big_meta4(k.MAX_META4_BYTES // 40))


def test_a_live_off_note_survives_the_general_encyclopedia_note(monkeypatch):
    from puffpai.catalogs import knowledge_resolver
    _row_with(monkeypatch, {"allowed": False})
    # a word no saved list has (volcanoes now finds Project Gutenberg books)
    result = knowledge_resolver.find("zorbing", live=True,
                                     getter=lambda *a, **kw: pytest.fail("network"))
    assert result["note"].startswith("BuildBot is set not to ask the Kiwix library online")
    assert "only about zorbing." in result["note"]
