"""Tests for the SQLite FTS5 full-text knowledge index.

These exercise the indexing core (``FTSBuilder._index_records``) against a
synthetic corpus, so no real ZIM archive is needed — the ZIM walker feeds that
same function, so the schema, ranking, snippets and query sanitizing are all
covered without a multi-gigabyte fixture.
"""

import os
import sys
import random
import sqlite3

import pytest

from puffpai.knowledge.fts_index import (
    SCHEMA_VERSION,
    FTSBuilder,
    FTSSearcher,
    _relevance_from_bm25,
    library_id_for,
    resolve_fts_dir,
    sanitize_query,
    sanitize_terms,
    shard_path_for,
    shard_status,
)
from puffpai.knowledge.zim_reader import ZIMTruncatedError


# ── Helpers ───────────────────────────────────────────────────────────────

CORPUS = [
    ("A/Water_filtration",
     "Water filtration",
     "Water filtration removes suspended solids from water. A ceramic filter "
     "traps sediment and bacteria. Filtration is the first stage of most "
     "household water treatment systems."),
    ("A/Solar_power",
     "Solar power",
     "Solar power converts sunlight into electricity using photovoltaic "
     "panels. Off-grid systems pair panels with a battery bank."),
    ("A/Rainwater_harvesting",
     "Rainwater harvesting",
     "Rainwater harvesting collects roof runoff into a cistern. The stored "
     "water usually needs filtration before drinking."),
]


def _make_shard(fts_dir, library_id, records, status='complete'):
    """Write a complete (or deliberately half-built) shard by hand."""
    os.makedirs(fts_dir, exist_ok=True)
    db_path = os.path.join(fts_dir, f"{library_id}.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        FTSBuilder._ensure_schema(conn)
        count = FTSBuilder._index_records(conn, records)
        conn.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            [('schema_version', SCHEMA_VERSION),
             ('status', status),
             ('zim_filename', f'{library_id}.zim'),
             ('zim_size', '1234'),
             ('zim_mtime', '5678'),
             ('article_count', str(count))],
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


# ── library_id_for / paths ────────────────────────────────────────────────

@pytest.mark.parametrize("filename,expected", [
    ("wikipedia_en_all_maxi_2024-01.zim", "wikipedia_en_all_maxi_2024_01"),
    ("TED talks (en).zim", "ted_talks_en"),
    ("/data/zim/Wiktionary_EN.ZIM", "wiktionary_en"),
    ("---weird---.zim", "weird"),
])
def test_library_id_for(filename, expected):
    assert library_id_for(filename) == expected


def test_library_id_is_stable_across_directories():
    assert (library_id_for("/mnt/a/foo-bar.zim")
            == library_id_for("D:\\packs\\foo-bar.zim"))


def test_shard_path_for(tmp_path):
    path = shard_path_for("/z/wikipedia_en-2024.zim", str(tmp_path))
    assert path == os.path.join(str(tmp_path), "wikipedia_en_2024.db")


# ── Query sanitizing ──────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", [
    'water "filtration"',
    'water AND filtration',
    'water OR (filtration NOT solar)',
    'water NEAR/3 filtration',
    'water^2 filtration*',
    '"unterminated quote water filtration',
    'water: filtration;',
    'wasserfiltratión — Grünwasser',
    '水 ろ過',
    '((((',
    '****',
    '',
    '   ',
])
def test_sanitize_query_never_raises_fts5_syntax_error(raw, tmp_path):
    """Any user input must produce a runnable MATCH expression or nothing."""
    _make_shard(tmp_path, "corpus", CORPUS)
    searcher = FTSSearcher(str(tmp_path))
    # search() must return a list, never raise
    assert isinstance(searcher.search(raw), list)

    match = sanitize_query(raw)
    if not match:
        return
    conn = sqlite3.connect(os.path.join(str(tmp_path), "corpus.db"))
    try:
        conn.execute("SELECT rowid FROM fts WHERE fts MATCH ?", (match,)).fetchall()
    finally:
        conn.close()


def test_sanitize_query_produces_quoted_and_terms():
    assert sanitize_query('water filtration') == '"water" "filtration"'
    assert sanitize_query('water "AND" filtration') == '"water" "AND" "filtration"'


def test_sanitize_query_keeps_unicode_words():
    assert sanitize_query('Grünwasser') == '"Grünwasser"'


def test_sanitize_query_empty_for_punctuation_only():
    assert sanitize_query('*** (((') == ''


# ── Indexing core ─────────────────────────────────────────────────────────

def test_index_records_inserts_and_keeps_rowid_aligned(tmp_path):
    db_path = _make_shard(tmp_path, "corpus", CORPUS)
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM fts").fetchone()[0] == 3
        # The JOIN in the search SQL is only correct if fts.rowid == docs.id.
        mismatched = conn.execute(
            "SELECT COUNT(*) FROM fts JOIN docs d ON d.id = fts.rowid "
            "WHERE d.title != fts.title").fetchone()[0]
        assert mismatched == 0
    finally:
        conn.close()


def test_index_records_skips_duplicate_paths(tmp_path):
    """Re-running a batch after a crash must not double-index."""
    db_path = _make_shard(tmp_path, "corpus", CORPUS)
    conn = sqlite3.connect(db_path)
    try:
        added = FTSBuilder._index_records(conn, CORPUS)
        conn.commit()
        assert added == 0
        assert conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0] == 3
    finally:
        conn.close()


def test_index_records_truncates_huge_bodies(tmp_path):
    from puffpai.knowledge.fts_index import MAX_BODY_CHARS
    huge = [("A/Huge", "Huge", "lorem " * 200_000)]
    db_path = _make_shard(tmp_path, "huge", huge)
    conn = sqlite3.connect(db_path)
    try:
        body = conn.execute("SELECT body FROM fts").fetchone()[0]
        assert len(body) == MAX_BODY_CHARS
    finally:
        conn.close()


# ── Searching ─────────────────────────────────────────────────────────────

def test_search_finds_expected_document(tmp_path):
    _make_shard(tmp_path, "corpus", CORPUS)
    hits = FTSSearcher(str(tmp_path)).search("photovoltaic")
    assert len(hits) == 1
    assert hits[0]['title'] == "Solar power"
    assert hits[0]['path'] == "A/Solar_power"
    assert hits[0]['library_id'] == "corpus"


def test_search_result_shape(tmp_path):
    _make_shard(tmp_path, "corpus", CORPUS)
    hit = FTSSearcher(str(tmp_path)).search("filtration")[0]
    assert set(hit) == {'library_id', 'path', 'title', 'snippet', 'relevance',
                        'match_mode'}
    assert 0.0 <= hit['relevance'] <= 1.0
    assert hit['match_mode'] == 'and'


def test_search_snippet_is_non_empty_and_highlights(tmp_path):
    _make_shard(tmp_path, "corpus", CORPUS)
    hits = FTSSearcher(str(tmp_path)).search("photovoltaic")
    assert hits[0]['snippet']
    assert '<b>' in hits[0]['snippet']


def test_search_ranking_is_sane(tmp_path):
    """The article that is *about* filtration outranks a passing mention."""
    _make_shard(tmp_path, "corpus", CORPUS)
    hits = FTSSearcher(str(tmp_path)).search("water filtration")
    titles = [h['title'] for h in hits]
    assert titles[0] == "Water filtration"
    assert "Solar power" not in titles  # implicit AND excludes it
    # Relevance is monotonically non-increasing within a shard.
    rels = [h['relevance'] for h in hits]
    assert rels == sorted(rels, reverse=True)
    assert rels[0] == pytest.approx(1.0)


# ── AND-then-OR search semantics ──────────────────────────────────────────

# Mirrors the live failure: a manual whose distinctive terms are rare, next to
# unrelated documents that carry the question's stopwords.
MANUAL_CORPUS = [
    ("A/Kaito_KA700", "Kaito KA700 owner's manual",
     "The Kaito KA700 is a portable emergency radio. It covers AM, FM, "
     "shortwave and the NOAA weather bands."),
    ("A/Library_card", "Using your library",
     "According to the rules, a library card is required to borrow items."),
    ("A/Gardening", "What to plant",
     "According to the almanac, what you plant depends on your zone."),
]


def test_terse_query_matches_under_and(tmp_path):
    """The terse form that already worked live must keep working, unchanged.

    "manual" appears only in the title, which MATCH covers alongside the body.
    """
    _make_shard(tmp_path, "manuals", MANUAL_CORPUS)
    hits = FTSSearcher(str(tmp_path)).search("Kaito KA700 radio manual bands")
    assert [h['title'] for h in hits] == ["Kaito KA700 owner's manual"]
    assert all(h['match_mode'] == 'and' for h in hits)


def test_natural_language_question_falls_back_to_or(tmp_path):
    """The live regression: a full sentence found nothing under strict AND."""
    _make_shard(tmp_path, "manuals", MANUAL_CORPUS)
    question = ("According to the Kaito KA700 manual in my library, what "
                "radio is the manual for and what bands does it cover?")

    # No single document contains every term, so the AND pass is empty.
    searcher = FTSSearcher(str(tmp_path))
    assert searcher._run_pass(sanitize_query(question), None, 20, 'and') == []

    # The OR pass finds the manual, and IDF puts it on top despite the
    # stopwords that the other two documents share.
    hits = searcher.search(question)
    assert hits
    assert hits[0]['title'] == "Kaito KA700 owner's manual"
    assert all(h['match_mode'] == 'or' for h in hits)


def test_or_pass_only_runs_when_and_finds_nothing(tmp_path):
    """An AND-satisfiable query must never relax to OR."""
    _make_shard(tmp_path, "manuals", MANUAL_CORPUS)
    hits = FTSSearcher(str(tmp_path)).search("library card required")
    assert [h['title'] for h in hits] == ["Using your library"]
    assert all(h['match_mode'] == 'and' for h in hits)
    # "According" appears in two documents; under OR both would come back.
    assert len(hits) == 1


def test_single_term_query_never_falls_back(tmp_path):
    """One term cannot be relaxed, so a miss stays a miss."""
    _make_shard(tmp_path, "manuals", MANUAL_CORPUS)
    assert FTSSearcher(str(tmp_path)).search("thermonuclear") == []
    hits = FTSSearcher(str(tmp_path)).search("KA700")
    assert len(hits) == 1
    assert hits[0]['match_mode'] == 'and'


def test_or_pass_respects_library_filter(tmp_path):
    _make_shard(tmp_path, "manuals", MANUAL_CORPUS)
    _make_shard(tmp_path, "other", CORPUS)
    hits = FTSSearcher(str(tmp_path)).search(
        "According to the Kaito KA700 manual what bands",
        libraries=['manuals'])
    assert hits
    assert {h['library_id'] for h in hits} == {'manuals'}
    assert all(h['match_mode'] == 'or' for h in hits)


def test_or_pass_term_cap(tmp_path, monkeypatch):
    """A rambling question is truncated to a bounded disjunction."""
    from puffpai.knowledge import fts_index

    _make_shard(tmp_path, "manuals", MANUAL_CORPUS)
    seen = []
    searcher = FTSSearcher(str(tmp_path))
    original = searcher._run_pass

    def _spy(match, wanted, max_results, mode, words=None, **kwargs):
        seen.append((mode, match))
        return original(match, wanted, max_results, mode, words=words,
                        **kwargs)

    monkeypatch.setattr(searcher, '_run_pass', _spy)
    long_question = "zzqq " + " ".join(f"word{n}" for n in range(60))
    searcher.search(long_question)

    or_matches = [m for mode, m in seen if mode == 'or']
    assert or_matches, "expected an OR pass"
    assert or_matches[0].count(' OR ') == fts_index.MAX_OR_TERMS - 1


# ── OR-pass cost bounding ─────────────────────────────────────────────────
#
# bm25-ranking a disjunction walks every posting of every term, so the OR
# fallback's latency is proportional to the summed document frequency of its
# terms. Live, "how do i treat a deep cut on my hand" measured 12-25s across
# 67 shards, nearly all of it scanning the doclists of "a"/"i"/"on"/"my".
# Terms are now selected by scan cost before the disjunction is built; these
# tests shrink MATCH_DF_KEEP_FLOOR/MATCH_DF_BUDGET so a hand-sized corpus can
# exhibit the same economics.

def test_or_selection_drops_terms_that_blow_the_scan_budget(tmp_path,
                                                            monkeypatch):
    from puffpai.knowledge import fts_index
    docs = [(f"A/D{i}", f"Doc {i}", f"ubiquitous filler number {i}")
            for i in range(10)]
    docs.append(("A/Rare", "Rare doc", "the zymurgy article"))
    _make_shard(tmp_path, "corpus", docs)
    monkeypatch.setattr(fts_index, 'MATCH_DF_KEEP_FLOOR', 2)
    monkeypatch.setattr(fts_index, 'MATCH_DF_BUDGET', 5)

    searcher = FTSSearcher(str(tmp_path))
    _, kept, freqs = searcher._select_terms(["ubiquitous", "zymurgy"])
    assert kept == ["zymurgy"]
    assert freqs[searcher._stem("ubiquitous")] == 10
    assert freqs[searcher._stem("zymurgy")] == 1


def test_or_selection_never_selects_nothing(tmp_path, monkeypatch):
    """When every term is over budget, the rarest one is still admitted."""
    from puffpai.knowledge import fts_index
    docs = [(f"A/D{i}", f"Doc {i}", f"alpha beta gamma {i}") for i in range(10)]
    docs.extend((f"A/E{i}", f"Extra {i}", f"alpha filler {i}") for i in range(5))
    _make_shard(tmp_path, "corpus", docs)
    monkeypatch.setattr(fts_index, 'MATCH_DF_KEEP_FLOOR', 1)
    monkeypatch.setattr(fts_index, 'MATCH_DF_BUDGET', 1)

    and_kept, kept, _ = FTSSearcher(str(tmp_path))._select_terms(
        ["alpha", "beta"])
    assert kept == ["beta"]
    assert and_kept == ["beta"]   # AND's never-empty rule matches


def test_or_selection_admits_rarest_first_until_budget(tmp_path, monkeypatch):
    from puffpai.knowledge import fts_index
    docs = [(f"A/A{i}", f"Doc A{i}", f"avocado {i}") for i in range(9)]
    docs += [(f"A/B{i}", f"Doc B{i}", f"broccoli {i}") for i in range(6)]
    docs += [(f"A/C{i}", f"Doc C{i}", f"cabbage {i}") for i in range(3)]
    docs.append(("A/R", "Doc R", "quokka"))
    _make_shard(tmp_path, "corpus", docs)
    monkeypatch.setattr(fts_index, 'MATCH_DF_KEEP_FLOOR', 1)
    monkeypatch.setattr(fts_index, 'MATCH_DF_BUDGET', 10)

    _, kept, _ = FTSSearcher(str(tmp_path))._select_terms(
        ["avocado", "broccoli", "cabbage", "quokka"])
    # quokka (1) is under the floor; cabbage (3) then broccoli (6) fit the
    # 10-document budget; avocado (9) no longer does. Original order kept.
    assert kept == ["broccoli", "cabbage", "quokka"]


def test_or_selection_dedupes_words_sharing_a_stem(tmp_path):
    """"cover"/"covers"/"covering" are one porter clause, not three scans."""
    _make_shard(tmp_path, "corpus", CORPUS)
    and_kept, kept, _ = FTSSearcher(str(tmp_path))._select_terms(
        ["covers", "cover", "covering", "cistern"])
    assert kept == ["covers", "cistern"]
    assert and_kept == ["covers", "cistern"]


def test_or_selection_keeps_distinct_compound_words(tmp_path):
    """Regression: dedup by first token alone collapsed
    "systemd_unit" and "systemd_timer" into one clause, silently dropping a
    query term and the documents only it matched."""
    _make_shard(tmp_path, "admin", [
        ("A/U", "systemd unit reference",
         "The systemd_unit file describes a service."),
        ("A/T", "systemd timer reference",
         "The systemd_timer file schedules a job."),
        ("A/F", "filler", "An unrelated page about nothing relevant."),
    ])
    searcher = FTSSearcher(str(tmp_path))
    _, kept, _ = searcher._select_terms(["systemd_unit", "systemd_timer"])
    assert kept == ["systemd_unit", "systemd_timer"]

    hits = searcher.search("systemd_timer systemd_unit zzzmissing")
    assert {h['title'] for h in hits} == {"systemd unit reference",
                                          "systemd timer reference"}


def test_or_selection_keeps_token_anagram_compounds(tmp_path):
    """Regression: a sorted dedup key collapsed
    "unit_test" and "test_unit" -- the same token multiset but different
    phrases -- into one clause."""
    _make_shard(tmp_path, "code", [
        ("A/UT", "unit_test helper", "The unit_test helper page."),
        ("A/TU", "test_unit fixture", "The test_unit fixture page."),
    ])
    searcher = FTSSearcher(str(tmp_path))
    _, kept, _ = searcher._select_terms(["unit_test", "test_unit"])
    assert kept == ["unit_test", "test_unit"]

    hits = searcher.search("unit_test test_unit zzzmissing")
    assert {h['title'] for h in hits} == {"unit_test helper",
                                          "test_unit fixture"}


def test_compound_cost_is_its_rarest_token(tmp_path, monkeypatch):
    """A phrase scan leapfrogs from its rarest token, so "everywhere_zyx"
    stays affordable even though "everywhere" floods the shard."""
    from puffpai.knowledge import fts_index
    docs = [(f"A/D{i}", f"Doc {i}", f"everywhere padding {i}")
            for i in range(10)]
    docs.append(("A/R", "Rare compound", "everywhere_zyx appears once"))
    _make_shard(tmp_path, "corpus", docs)
    monkeypatch.setattr(fts_index, 'MATCH_DF_KEEP_FLOOR', 2)
    monkeypatch.setattr(fts_index, 'MATCH_DF_BUDGET', 2)

    _, kept, _ = FTSSearcher(str(tmp_path))._select_terms(
        ["everywhere", "everywhere_zyx"])
    assert kept == ["everywhere_zyx"]


def test_or_selection_keeps_everything_under_default_floor(tmp_path):
    """Small collections never trip the default thresholds: every df is tiny,
    so behavior is byte-identical to the unbounded disjunction."""
    _make_shard(tmp_path, "manuals", MANUAL_CORPUS)
    and_kept, kept, _ = FTSSearcher(str(tmp_path))._select_terms(
        ["according", "kaito", "bands"])
    assert kept == ["according", "kaito", "bands"]
    # The AND ceiling never drops under the keep-floor, so a small
    # collection keeps strict-AND precision over every term.
    assert and_kept == ["according", "kaito", "bands"]


def test_sentence_query_scans_only_affordable_terms(tmp_path, monkeypatch):
    """End-to-end: the MATCH FTS5 receives omits the flooded term, and the
    rare terms' recall is intact."""
    from puffpai.knowledge import fts_index
    docs = [(f"A/Filler{i}", f"Filler {i}", f"about generic padding {i}")
            for i in range(12)]
    docs.append(("A/KA700", "Kaito KA700 owner's manual",
                 "Emergency radio covering shortwave bands."))
    _make_shard(tmp_path, "corpus", docs)
    monkeypatch.setattr(fts_index, 'MATCH_DF_KEEP_FLOOR', 3)
    monkeypatch.setattr(fts_index, 'MATCH_DF_BUDGET', 3)

    searcher = FTSSearcher(str(tmp_path))
    seen = []
    original = searcher._run_pass

    def _spy(match, wanted, max_results, mode, words=None, **kwargs):
        seen.append((mode, match))
        return original(match, wanted, max_results, mode, words=words,
                        **kwargs)

    monkeypatch.setattr(searcher, '_run_pass', _spy)
    hits = searcher.search("about the kaito ka700 bands")

    or_matches = [m for mode, m in seen if mode == 'or']
    assert or_matches, "expected an OR pass"
    assert '"about"' not in or_matches[0]
    assert '"kaito"' in or_matches[0]
    assert hits and hits[0]['title'] == "Kaito KA700 owner's manual"
    assert all(h['match_mode'] == 'or' for h in hits)


def test_and_pass_drops_flooded_terms_but_keeps_precision(tmp_path,
                                                          monkeypatch):
    """A sentence's flooded words no longer gate the AND pass: with "the"
    priced out, AND("kaito","radio") still matches in and-mode. Keyword
    queries carry no flooded terms and keep strict-AND behavior."""
    from puffpai.knowledge import fts_index
    docs = [(f"A/F{i}", f"Filler {i}", f"the ordinary filler page {i}")
            for i in range(12)]
    docs.append(("A/K", "Kaito KA700", "Kaito radio covering shortwave bands."))
    _make_shard(tmp_path, "corpus", docs)
    monkeypatch.setattr(fts_index, 'MATCH_DF_KEEP_FLOOR', 3)
    monkeypatch.setattr(fts_index, 'MATCH_DF_BUDGET', 3)

    hits = FTSSearcher(str(tmp_path)).search("the kaito radio")
    assert [h['title'] for h in hits] == ["Kaito KA700"]
    assert hits[0]['match_mode'] == 'and'


# ── Per-pass term selection ───────────────────────────────────────────────
#
# The AND intersection is seek-bound (O(rarest-df x terms)), so it can
# afford to REQUIRE mid-frequency words whose doclists the walk-bound OR
# disjunction cannot afford to scan. Live, "how do i treat a deep cut on my
# hand" kept only [treat, deep] under the single summed budget and returned
# Deep-titled film articles; requiring cut/hand/how as well is what finds
# the wound-care pages.

def test_and_pass_affords_midfrequency_terms_the_or_budget_drops(
        tmp_path, monkeypatch):
    from puffpai.knowledge import fts_index
    docs = [(f"A/M{i}", f"Mid {i}", f"midword page {i}") for i in range(12)]
    docs += [(f"A/F{i}", f"Filler {i}", f"filler page {i}") for i in range(27)]
    docs.append(("A/R", "Anchor", "anchorword appears here once"))
    _make_shard(tmp_path, "corpus", docs)
    monkeypatch.setattr(fts_index, 'MATCH_DF_KEEP_FLOOR', 1)
    monkeypatch.setattr(fts_index, 'MATCH_DF_BUDGET', 5)
    monkeypatch.setattr(fts_index, 'MATCH_AND_DF_CEILING_SHARE', 0.5)

    and_kept, or_kept, _ = FTSSearcher(str(tmp_path))._select_terms(
        ["anchorword", "midword"])
    # midword (12 docs) blows the 5-document OR walk budget but sits under
    # the AND ceiling (half of the 40-document collection).
    assert or_kept == ["anchorword"]
    assert and_kept == ["anchorword", "midword"]


def test_and_pass_requires_midfrequency_words_for_precision(tmp_path,
                                                            monkeypatch):
    """End-to-end replica of the wound-care case: with only the two
    OR-affordable terms required, the junk pages match too; requiring the
    mid-frequency words as well singles out the right page, still in
    and-mode."""
    from puffpai.knowledge import fts_index
    docs = [("A/W", "Treating cuts",
             "How to treat a deep cut on my hand: press and bandage."),
            ("A/M1", "Deep film", "treat deep film"),
            ("A/M2", "Deeper film", "deep film sequel")]
    docs += [(f"A/C{i}", f"Cut {i}", f"cut price {i}") for i in range(5)]
    docs += [(f"A/H{i}", f"Hand {i}", f"hand poker {i}") for i in range(6)]
    docs += [(f"A/F{i}", f"Filler {i}", f"misc filler {i}") for i in range(6)]
    _make_shard(tmp_path, "corpus", docs)
    monkeypatch.setattr(fts_index, 'MATCH_DF_KEEP_FLOOR', 1)
    monkeypatch.setattr(fts_index, 'MATCH_DF_BUDGET', 6)
    monkeypatch.setattr(fts_index, 'MATCH_AND_DF_CEILING_SHARE', 0.5)

    searcher = FTSSearcher(str(tmp_path))
    and_kept, or_kept, _ = searcher._select_terms(
        ["treat", "deep", "cut", "hand"])
    assert or_kept == ["treat", "deep"]           # cut (6) and hand (7) priced out
    assert and_kept == ["treat", "deep", "cut", "hand"]

    hits = searcher.search("treat a deep cut on my hand")
    assert [h['title'] for h in hits] == ["Treating cuts"]
    assert hits[0]['match_mode'] == 'and'


def test_and_seek_budget_keeps_the_rarest_terms(tmp_path, monkeypatch):
    """rarest-df x terms is the intersection's cost; when it crosses the
    seek budget the commonest terms are shed first."""
    from puffpai.knowledge import fts_index
    docs = [(f"A/Z{i}", f"Z {i}", f"zeta {i}") for i in range(4)]
    docs += [(f"A/Y{i}", f"Y {i}", f"yak {i}") for i in range(3)]
    docs += [(f"A/X{i}", f"X {i}", f"xray {i}") for i in range(2)]
    _make_shard(tmp_path, "corpus", docs)
    monkeypatch.setattr(fts_index, 'MATCH_DF_KEEP_FLOOR', 1)
    monkeypatch.setattr(fts_index, 'MATCH_DF_BUDGET', 100)
    monkeypatch.setattr(fts_index, 'MATCH_AND_DF_CEILING_SHARE', 1.0)
    monkeypatch.setattr(fts_index, 'MATCH_AND_SEEK_BUDGET', 5)

    and_kept, _, _ = FTSSearcher(str(tmp_path))._select_terms(
        ["zeta", "yak", "xray"])
    # rarest is xray (2 docs); 5 // 2 = 2 terms afford the intersection.
    assert and_kept == ["yak", "xray"]


def test_and_empty_retries_or_even_when_one_term_survives(tmp_path,
                                                          monkeypatch):
    """The wider AND set can require a term combination no document has;
    the retry must still run when the OR set differs, even single-term."""
    from puffpai.knowledge import fts_index
    docs = [(f"A/M{i}", f"Mid {i}", f"midword page {i}") for i in range(8)]
    docs.append(("A/R", "Rare page", "rareword only here"))
    _make_shard(tmp_path, "corpus", docs)
    monkeypatch.setattr(fts_index, 'MATCH_DF_KEEP_FLOOR', 1)
    monkeypatch.setattr(fts_index, 'MATCH_DF_BUDGET', 1)
    monkeypatch.setattr(fts_index, 'MATCH_AND_DF_CEILING_SHARE', 1.0)

    hits = FTSSearcher(str(tmp_path)).search("rareword midword")
    assert [h['title'] for h in hits[:1]] == ["Rare page"]
    assert hits[0]['match_mode'] == 'or'


def test_and_results_prefer_title_matches_over_verbose_bodies(tmp_path):
    """Cross-shard raw bm25 rewarded verbose pages that merely mention every
    term; among AND matches, a document NAMED for a query term wins."""
    _make_shard(tmp_path, "corpus", [
        ("A/T", "Vulcan forge", "The vulcan forge process."),
        ("A/B", "Industrial history",
         "vulcan forge vulcan forge vulcan forge notes, vulcan and forge "
         "techniques through the vulcan forge ages"),
    ])
    hits = FTSSearcher(str(tmp_path)).search("vulcan forge")
    assert [h['title'] for h in hits] == ["Vulcan forge", "Industrial history"]
    assert hits[0]['match_mode'] == 'and'


def test_and_pass_without_any_title_hit_falls_back_to_or(tmp_path):
    """An AND intersection in which no candidate carries a query term in its
    title is stopword salad (live: 'List of Candy Candy episodes' for a
    wound-care question); the OR pass's subset scoring takes over."""
    _make_shard(tmp_path, "corpus", [
        ("A/J", "Weekly digest", "alpha beta gamma delta epsilon"),
        ("A/R", "Alpha handbook", "alpha reference text"),
    ])
    hits = FTSSearcher(str(tmp_path)).search("alpha beta")
    assert hits and all(h['match_mode'] == 'or' for h in hits)
    # Subset informativeness still decides: the page containing both terms
    # outranks the one merely titled with the commoner term.
    assert hits[0]['title'] == "Weekly digest"


def test_single_term_query_is_never_title_gated(tmp_path):
    """With no different OR set to retry, gating would erase the results."""
    _make_shard(tmp_path, "corpus", [
        ("A/J", "Weekly digest", "alpha beta gamma delta epsilon"),
    ])
    hits = FTSSearcher(str(tmp_path)).search("alpha")
    assert [h['title'] for h in hits] == ["Weekly digest"]
    assert hits[0]['match_mode'] == 'and'


# ── Variant-family frequency dedup ────────────────────────────────────────
#
# A large install carries English Wikipedia as all_maxi + all_nopic +
# all_mini side by side; summing frequencies across them made every English
# word look several times as frequent as it is and starved the term
# selections, and scanning all of them walked the same postings for the
# same articles. Shards budget by family (max within, sum across) and only
# one member of each family gets scanned, so a family must never lump
# disjoint content (each ted_mul topic pack is its own corpus).

@pytest.mark.parametrize("library_id,family", [
    ("wikipedia_en_all_maxi_2025_08", "wikipedia_en_all"),
    ("wikipedia_en_all_mini_2025_06", "wikipedia_en_all"),
    ("wikipedia_en_all_nopic_2025_08", "wikipedia_en_all"),
    ("wikipedia_en_simple_all_maxi_2025_09", "wikipedia_en_simple_all"),
    ("wikipedia_en_top_maxi_2025_09", "wikipedia_en_top"),
    ("ted_mul_democracy_2025_07", "ted_mul_democracy"),
    ("ted_mul_water_2025_08", "ted_mul_water"),
    ("incognitocat_me_en_all_2025_09", "incognitocat_me_en_all"),
    ("zimgit_water_en_2024_08", "zimgit_water_en"),
    ("kaito_ka700_en_2026_07", "kaito_ka700_en"),
    ("corpus", "corpus"),
])
def test_variant_family(library_id, family):
    from puffpai.knowledge.fts_index import _variant_family
    assert _variant_family(library_id) == family


def test_variant_dedup_budgets_families_once(tmp_path, monkeypatch):
    from puffpai.knowledge import fts_index
    docs = [(f"A/C{i}", f"Common {i}", f"common text {i}") for i in range(4)]
    docs.append(("A/R", "Rare", "rare text"))
    monkeypatch.setattr(fts_index, 'MATCH_DF_KEEP_FLOOR', 1)
    monkeypatch.setattr(fts_index, 'MATCH_DF_BUDGET', 4)

    # Two flavours of the same corpus: budgeted once, "common" stays.
    variants = tmp_path / "variants"
    _make_shard(variants, "demo_en_all_maxi_2025_01", docs)
    _make_shard(variants, "demo_en_all_nopic_2025_01", docs)
    _, or_kept, freqs = FTSSearcher(str(variants))._select_terms(
        ["common", "rare"])
    assert or_kept == ["common", "rare"]
    assert freqs["common"] == 8          # raw sums stay raw for the re-rank

    # The same corpora as genuinely distinct collections: summed, dropped.
    distinct = tmp_path / "distinct"
    _make_shard(distinct, "alpha_en_docs_2025_01", docs)
    _make_shard(distinct, "beta_en_docs_2025_01", docs)
    _, or_kept, _ = FTSSearcher(str(distinct))._select_terms(
        ["common", "rare"])
    assert or_kept == ["rare"]


def test_or_pass_scans_one_shard_per_variant_family(tmp_path):
    """The walk-bound OR disjunction pays per posting; flavour twins of one
    corpus re-find the same articles, so only the largest member scans."""
    docs_maxi = CORPUS + [("A/X", "Xtra page", "xtra unique page")]
    _make_shard(tmp_path, "demo_en_all_maxi_2025_01", docs_maxi)
    _make_shard(tmp_path, "demo_en_all_nopic_2025_01", CORPUS)
    searcher = FTSSearcher(str(tmp_path))
    assert searcher._representative_shards() == {"demo_en_all_maxi_2025_01"}
    # An explicit library filter keeps its selection.
    assert (searcher._representative_shards({"demo_en_all_nopic_2025_01"})
            == {"demo_en_all_nopic_2025_01"})

    hits = searcher.search("cistern zzznope")
    assert hits and all(h['match_mode'] == 'or' for h in hits)
    assert {h['library_id'] for h in hits} == {"demo_en_all_maxi_2025_01"}


def test_results_never_repeat_a_variant_twin_title(tmp_path):
    """maxi and nopic both hold 'Water filtration'; one result slot each was
    pure noise in live output."""
    _make_shard(tmp_path, "demo_en_all_maxi_2025_01", CORPUS)
    _make_shard(tmp_path, "demo_en_all_nopic_2025_01", CORPUS)
    hits = FTSSearcher(str(tmp_path)).search("filtration")
    titles = [h['title'] for h in hits]
    assert len(titles) == len(set(titles))
    assert "Water filtration" in titles


def test_dedup_doc_freqs_respects_library_filter(tmp_path):
    variants = tmp_path / "v"
    _make_shard(variants, "demo_en_all_maxi_2025_01", CORPUS)
    _make_shard(variants, "demo_en_all_nopic_2025_01", CORPUS[:1])
    searcher = FTSSearcher(str(variants))
    stem = searcher._stem("filtration")
    searcher._global_doc_freqs([stem])   # fills the per-shard cache
    freqs, total = searcher._dedup_doc_freqs([stem])
    assert freqs[stem] == 2              # max(2, 1), not 3
    assert total == len(CORPUS)
    freqs, total = searcher._dedup_doc_freqs(
        [stem], wanted={"demo_en_all_nopic_2025_01"})
    assert freqs[stem] == 1
    assert total == 1


def test_search_titles_or_pass_is_cost_bounded(tmp_path, monkeypatch):
    from puffpai.knowledge import fts_index
    docs = [(f"A/T{i}", f"Guide {i} paddling", f"body {i}") for i in range(9)]
    docs.append(("A/Z", "Zephyr class glider", "body"))
    _make_shard(tmp_path, "titles", docs)
    monkeypatch.setattr(fts_index, 'MATCH_DF_KEEP_FLOOR', 2)
    monkeypatch.setattr(fts_index, 'MATCH_DF_BUDGET', 2)

    hits = FTSSearcher(str(tmp_path)).search_titles("paddling zephyr glider")
    # "paddling" (9 title hits) is over budget and dropped; the rare terms
    # still find their document.
    assert [h['title'] for h in hits] == ["Zephyr class glider"]


def test_global_doc_freqs_sums_across_shards_and_respects_filter(tmp_path):
    _make_shard(tmp_path, "lib_a", CORPUS)           # "filtration" in 2 docs
    _make_shard(tmp_path, "lib_b", CORPUS[:1])       # ...and 1 here
    searcher = FTSSearcher(str(tmp_path))
    stem = searcher._stem("filtration")
    assert searcher._global_doc_freqs([stem])[stem] == 3
    assert searcher._global_doc_freqs([stem], wanted={'lib_b'})[stem] == 1
    # An unknown term reads as maximally rare, never as an error.
    assert searcher._global_doc_freqs(["zzznothere"])["zzznothere"] == 0


def test_df_estimation_on_large_shards(tmp_path, monkeypatch):
    """Shards over DF_SAMPLE_ROWS estimate common-term frequencies from a
    mid-shard rowid window (fts5vocab walks a term's whole doclist to count
    it -- 1.1s for "a" on the live Wikipedia shard); terms absent from the
    window still get the exact walk, cheap precisely because they're rare."""
    from puffpai.knowledge import fts_index
    docs = [(f"A/D{i}", f"Doc {i}",
             "common filler" + (" nugget" if i == 3 else ""))
            for i in range(200)]
    _make_shard(tmp_path, "big", docs)
    monkeypatch.setattr(fts_index, 'DF_SAMPLE_ROWS', 50)

    freqs = FTSSearcher(str(tmp_path))._global_doc_freqs(
        ["common", "nugget", "zzznothere"])
    assert freqs["common"] == 200      # 50 of 50 window docs -> whole shard
    assert freqs["nugget"] == 1        # not in the window -> exact vocab walk
    assert freqs["zzznothere"] == 0


def test_df_cache_survives_refresh(tmp_path, monkeypatch):
    """The fulltext endpoint refresh()es every request; measured frequencies
    must not be re-walked each time. Cache is keyed by shard identity, so a
    refresh that rediscovers the same shard serves from cache."""
    from puffpai.knowledge import fts_index
    _make_shard(tmp_path, "corpus", CORPUS)
    searcher = FTSSearcher(str(tmp_path))
    stem = searcher._stem("filtration")
    assert searcher._global_doc_freqs([stem])[stem] == 2

    searcher.refresh()
    assert searcher._global_doc_freqs([stem])[stem] == 2  # rediscovered

    def _boom(*args, **kwargs):
        raise AssertionError("df served from cache must not reopen shards")

    monkeypatch.setattr(fts_index, '_connect', _boom)
    assert searcher._global_doc_freqs([stem])[stem] == 2


@pytest.mark.parametrize("hostile", [
    'according to the "bad (quote AND thing',
    'what about NEAR/3 OR ((( stuff',
    'zzznomatch *** ((( ###',
    'Grünwasser OR "unterminated',
])
def test_hostile_input_is_safe_in_both_passes(tmp_path, hostile):
    """Neither pass may let user punctuation reach FTS5 as syntax."""
    _make_shard(tmp_path, "manuals", MANUAL_CORPUS)
    searcher = FTSSearcher(str(tmp_path))
    assert isinstance(searcher.search(hostile), list)
    # Force the OR pass explicitly too.
    terms = sanitize_terms(hostile)
    if len(terms) > 1:
        assert isinstance(
            searcher._run_pass(' OR '.join(terms), None, 20, 'or'), list)


def test_sanitize_terms_quotes_each_term():
    assert sanitize_terms('water filtration') == ['"water"', '"filtration"']
    assert sanitize_terms('a OR b') == ['"a"', '"OR"', '"b"']
    assert sanitize_terms('*** (((') == []


def test_disjoint_terms_now_fall_back_instead_of_returning_nothing(tmp_path):
    """Semantics change: no document has both terms, so OR answers.

    This previously returned [] by design. Returning the two documents that
    each match one term is what lets a natural-language question reach RAG.
    """
    _make_shard(tmp_path, "corpus", CORPUS)
    hits = FTSSearcher(str(tmp_path)).search("photovoltaic cistern")
    assert {h['title'] for h in hits} == {"Solar power", "Rainwater harvesting"}
    assert all(h['match_mode'] == 'or' for h in hits)


def test_search_respects_max_results(tmp_path):
    _make_shard(tmp_path, "corpus", CORPUS)
    assert len(FTSSearcher(str(tmp_path)).search("water", max_results=1)) == 1
    assert FTSSearcher(str(tmp_path)).search("water", max_results=0) == []


def test_snippet_escapes_markup_from_pack_content(tmp_path):
    """ZIM packs are untrusted markup: only our own <b> tags may survive.

    zim_reader.plain_text decodes &lt;/&gt; back into real angle brackets, so
    without escaping a pack could inject a literal <script> into an API
    response the dashboard renders.
    """
    hostile = [("A/Hostile", "Hostile page",
                'Before <script>alert(1)</script> the payload mentions '
                'photovoltaic panels & "quoted" text after.')]
    _make_shard(tmp_path, "hostile", hostile)

    snippet = FTSSearcher(str(tmp_path)).search("photovoltaic")[0]['snippet']
    assert '&lt;script&gt;' in snippet
    assert '<script>' not in snippet
    assert '&amp;' in snippet
    # ...but the highlight this module inserted is still real markup.
    assert '<b>photovoltaic</b>' in snippet


def test_snippet_escapes_title_and_body_markup(tmp_path):
    _make_shard(tmp_path, "hostile", [
        ("A/X", "<img src=x onerror=alert(1)>",
         "harmless lead <b onclick=evil()>widget</b> trailing text")])
    hit = FTSSearcher(str(tmp_path)).search("widget")[0]
    assert 'onerror' not in hit['snippet']
    assert '<b onclick' not in hit['snippet']
    assert '&lt;b onclick=evil()&gt;' in hit['snippet']


def test_snippet_control_sentinels_never_leak(tmp_path):
    _make_shard(tmp_path, "corpus", CORPUS)
    snippet = FTSSearcher(str(tmp_path)).search("photovoltaic")[0]['snippet']
    assert '\x02' not in snippet and '\x03' not in snippet


def test_search_empty_library_list_returns_nothing(tmp_path):
    """An explicitly empty selection must not silently widen to all shards."""
    _make_shard(tmp_path, "lib_a", CORPUS)
    _make_shard(tmp_path, "lib_b", CORPUS)
    searcher = FTSSearcher(str(tmp_path))
    assert searcher.search("filtration", libraries=[]) == []
    # None still means "every library".
    assert searcher.search("filtration", libraries=None) != []


def test_search_no_match_returns_empty(tmp_path):
    _make_shard(tmp_path, "corpus", CORPUS)
    assert FTSSearcher(str(tmp_path)).search("thermonuclear") == []


def test_search_missing_directory_is_empty(tmp_path):
    searcher = FTSSearcher(str(tmp_path / "does_not_exist"))
    assert searcher.search("water") == []
    assert searcher.is_ready() is False


# ── Shard discovery / status gating ───────────────────────────────────────

def test_searcher_skips_building_shard(tmp_path):
    _make_shard(tmp_path, "done_lib", CORPUS, status='complete')
    _make_shard(tmp_path, "half_lib", CORPUS, status='building')

    searcher = FTSSearcher(str(tmp_path))
    assert searcher.ready_library_ids() == ['done_lib']
    hits = searcher.search("filtration", max_results=50)
    assert {h['library_id'] for h in hits} == {'done_lib'}


def test_searcher_skips_shard_with_stale_schema(tmp_path):
    db_path = _make_shard(tmp_path, "old_lib", CORPUS)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("UPDATE meta SET value = '0' WHERE key = 'schema_version'")
        conn.commit()
    finally:
        conn.close()
    assert FTSSearcher(str(tmp_path)).ready_library_ids() == []


def test_searcher_refresh_picks_up_new_shard(tmp_path):
    searcher = FTSSearcher(str(tmp_path))
    assert searcher.ready_library_ids() == []
    _make_shard(tmp_path, "corpus", CORPUS)
    assert searcher.ready_library_ids() == []  # cached
    searcher.refresh()
    assert searcher.ready_library_ids() == ['corpus']


def test_search_library_filter(tmp_path):
    _make_shard(tmp_path, "lib_a", CORPUS)
    _make_shard(tmp_path, "lib_b", CORPUS)
    searcher = FTSSearcher(str(tmp_path))
    assert set(searcher.ready_library_ids()) == {'lib_a', 'lib_b'}
    hits = searcher.search("filtration", max_results=50, libraries=['lib_b'])
    assert hits and {h['library_id'] for h in hits} == {'lib_b'}


def test_shard_status_reports_missing_and_complete(tmp_path):
    missing = shard_status(str(tmp_path / "nope.db"))
    assert missing['exists'] is False

    db_path = _make_shard(tmp_path, "corpus", CORPUS)
    info = shard_status(db_path)
    assert info['exists'] is True
    assert info['status'] == 'complete'
    assert info['article_count'] == 3
    assert info['schema_version'] == SCHEMA_VERSION


def test_shard_status_tolerates_non_database_file(tmp_path):
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"this is not a sqlite database at all")
    info = shard_status(str(junk))
    assert info['exists'] is True
    assert info.get('status') != 'complete'
    assert FTSSearcher(str(tmp_path)).ready_library_ids() == []


# ── Cross-shard merge (global, by raw bm25) ───────────────────────────────
#
# These replace the previous round-robin interleave tests. Interleaving gave
# every shard a turn regardless of match quality, and combined with per-shard
# normalization it made the top-N a function of shard name rather than
# relevance -- see test_weak_first_shard_does_not_outrank_strong_later_shard.

# One distinctive document, and filler that shares only the query's stopwords.
TRUE_DOC = [
    ("A/Kaito_KA700", "Kaito KA700 owner's manual",
     "The Kaito KA700 portable radio covers what the AM, FM, shortwave and "
     "NOAA weather bands cover."),
]


def _stopword_doc(topic, word):
    return [(f"A/{topic}", topic,
             f"What is {topic}? This article does not cover radios, but it "
             f"does {word} what you might expect about {topic}.")]


def test_global_merge_puts_the_true_match_first(tmp_path):
    """THE LIVE REPRO: rare terms must beat stopword matches in other shards.

    With ~40 shards built, "What bands does the Kaito KA700 radio cover?"
    returned five topically irrelevant articles, all at relevance 1.0, and the
    actual manual not at all. Per-shard normalization tied every shard's best
    hit at 1.0 and round-robin then took the first shards alphabetically.
    """
    # Names chosen so the true match sorts LAST alphabetically.
    _make_shard(tmp_path, "aaa_biomedical", _stopword_doc("biomedical science", "cover"))
    _make_shard(tmp_path, "bbb_tents", _stopword_doc("the best tent", "cover"))
    _make_shard(tmp_path, "ccc_microwave", _stopword_doc("microwave safety", "cover"))
    _make_shard(tmp_path, "ddd_gardening", _stopword_doc("gardening", "cover"))
    _make_shard(tmp_path, "zzz_radios", TRUE_DOC)

    hits = FTSSearcher(str(tmp_path)).search(
        "What bands does the Kaito KA700 radio cover?", max_results=5)

    assert hits, "expected the OR fallback to find something"
    assert hits[0]['title'] == "Kaito KA700 owner's manual"
    assert hits[0]['library_id'] == 'zzz_radios'
    assert hits[0]['match_mode'] == 'or'
    assert hits[0]['relevance'] == pytest.approx(1.0)

    # The stopword documents must rank strictly below, and visibly so -- not
    # all tied at the top as they were under per-shard normalization.
    tail = hits[1:]
    assert tail, "expected the noise documents to still be present, but lower"
    assert all(h['relevance'] < hits[0]['relevance'] for h in tail)
    assert max(h['relevance'] for h in tail) < 0.9


def test_weak_first_shard_does_not_outrank_strong_later_shard(tmp_path):
    """Kills the round-robin regression specifically."""
    _make_shard(tmp_path, "aaa_weak", _stopword_doc("aardvarks", "cover"))
    _make_shard(tmp_path, "zzz_strong", TRUE_DOC)

    hits = FTSSearcher(str(tmp_path)).search("Kaito KA700 bands cover")
    assert hits[0]['library_id'] == 'zzz_strong'


def test_and_mode_multi_shard_orders_by_global_score(tmp_path):
    """AND results are ordered by raw bm25 across shards, not by shard order."""
    _make_shard(tmp_path, "aaa_passing", [
        ("A/Mention", "Passing mention",
         "A long article about many unrelated things. " * 40 +
         "It mentions sand filtration once.")])
    _make_shard(tmp_path, "zzz_focused", [
        ("A/Filtration", "Sand filtration",
         "Sand filtration. Sand filtration is the topic of this short note.")])

    hits = FTSSearcher(str(tmp_path)).search("sand filtration")
    assert [h['library_id'] for h in hits] == ['zzz_focused', 'aaa_passing']
    assert all(h['match_mode'] == 'and' for h in hits)
    assert hits[0]['relevance'] > hits[1]['relevance']


def test_global_merge_trims_to_max_results(tmp_path):
    for name in ('lib_a', 'lib_b', 'lib_c'):
        _make_shard(tmp_path, name, CORPUS)
    hits = FTSSearcher(str(tmp_path)).search("filtration", max_results=4)
    assert len(hits) == 4


def test_only_the_global_best_scores_one(tmp_path):
    """Per-shard normalization used to give every shard a 1.0. Not any more."""
    _make_shard(tmp_path, "aaa_weak", _stopword_doc("aardvarks", "cover"))
    _make_shard(tmp_path, "zzz_strong", TRUE_DOC)
    hits = FTSSearcher(str(tmp_path)).search("Kaito KA700 bands cover")
    assert len([h for h in hits if h['relevance'] == pytest.approx(1.0)]) == 1


def test_internal_rank_key_never_escapes(tmp_path):
    _make_shard(tmp_path, "corpus", CORPUS)
    for hit in FTSSearcher(str(tmp_path)).search("filtration"):
        assert '_rank' not in hit
        assert '_rowid' not in hit


# ── Stage 2: global IDF re-rank ───────────────────────────────────────────
#
# Local bm25 cannot see across shards. A 2-document pack in which both
# documents contain "kaito" and "ka700" assigns those terms almost no weight,
# so its raw scores lose a global sort to incidental matches in huge shards.
# Judged against the whole collection the same terms are extremely rare.

_FILLER_WORDS = ("what does the cover a of and to in is are it you your this "
                 "that with for network wireless signal frequency").split()


def _filler_docs(prefix, count, seed=0):
    """Bulk documents so a shard's local IDF is realistic."""
    rng = random.Random(seed)
    return [(f"A/{prefix}_{i}", f"{prefix.title()} article {i}",
             " ".join(rng.choice(_FILLER_WORDS) for _ in range(80)))
            for i in range(count)]


@pytest.fixture
def kaito_library(tmp_path):
    """The live pathology, in miniature.

    A tiny 2-document radio pack (both documents mention kaito/ka700, so local
    IDF for those terms is ~0) against large shards holding a single-rare-term
    match and stopword-only noise.
    """
    _make_shard(tmp_path, "aa_wireless",
                _filler_docs("wireless", 400, seed=1) + [
                    ("A/Freq", "Frequency separation in wireless networks",
                     "What does frequency separation cover in wireless "
                     "networks? It does not cover radios.")])
    _make_shard(tmp_path, "bb_osm",
                _filler_docs("osm", 400, seed=2) + [
                    ("A/Antenna", "Antenna:use",
                     "What does this tag cover? It describes antenna use.")])
    _make_shard(tmp_path, "cc_wiki",
                _filler_docs("wiki", 400, seed=3) + [
                    ("A/KaitoKid", "User Kaito Kid",
                     "What does this user cover? Kaito Kid is a character "
                     "page and does not cover radios.")])
    # The real pack: small, and both documents carry the rare terms.
    _make_shard(tmp_path, "zz_radios", [
        ("A/KA700", "Kaito KA700 owner's manual",
         "The Kaito KA700 portable emergency radio covers the AM, FM, "
         "shortwave and NOAA weather bands."),
        ("A/KA700_specs", "Kaito KA700 specifications",
         "Kaito KA700 specifications: dimensions, battery, and the bands "
         "the radio covers."),
    ])
    return str(tmp_path)


def test_live_pathology_true_manual_ranks_first(kaito_library):
    """On the box this returned frequency separation, Antenna:use and Kaito
    Kid, with the manual absent entirely."""
    hits = FTSSearcher(kaito_library).search(
        "What bands does the Kaito KA700 radio cover?", max_results=5)

    assert hits
    assert hits[0]['match_mode'] == 'or'
    assert hits[0]['relevance'] == pytest.approx(1.0)

    # Both documents of the tiny radio pack match every distinctive term, so
    # they tie at the top -- that tie is correct, they are the right pack.
    assert [h['library_id'] for h in hits[:2]] == ['zz_radios', 'zz_radios']
    assert {h['title'] for h in hits[:2]} == {"Kaito KA700 owner's manual",
                                              "Kaito KA700 specifications"}

    ranks = {h['title']: i for i, h in enumerate(hits)}
    # A document matching only "kaito" must rank below the full-phrase matches.
    assert ranks["User Kaito Kid"] > 1
    # Stopword-only matches must rank below the single-rare-term match.
    for noise in ("Frequency separation in wireless networks", "Antenna:use"):
        if noise in ranks:
            assert ranks[noise] > ranks["User Kaito Kid"]


def test_live_pathology_noise_is_separated_by_relevance(kaito_library):
    """The tail must be visibly weaker, so _trim_or_tail can cut it."""
    hits = FTSSearcher(kaito_library).search(
        "What bands does the Kaito KA700 radio cover?", max_results=5)

    radio_hits = [h for h in hits if h['library_id'] == 'zz_radios']
    noise_hits = [h for h in hits if h['library_id'] != 'zz_radios']
    assert all(h['relevance'] == pytest.approx(1.0) for h in radio_hits)
    assert all(h['relevance'] < 0.8 for h in noise_hits)

    # What api_server._trim_or_tail would keep: the right pack, nothing else.
    cutoff = max(h['relevance'] for h in hits) - 0.25
    kept = [h for h in hits if h['relevance'] >= cutoff]
    assert {h['library_id'] for h in kept} == {'zz_radios'}
    assert len(kept) == 2


def test_single_rare_term_beats_stopwords_only(kaito_library):
    """"User Kaito Kid" matches one rare term; noise matches none."""
    hits = FTSSearcher(kaito_library).search(
        "What does the Kaito cover?", max_results=5)
    assert hits[0]['title'] in ("User Kaito Kid", "Kaito KA700 owner's manual",
                                "Kaito KA700 specifications")
    assert hits[0]['relevance'] == pytest.approx(1.0)


# ── Stemming correctness (the silent-zero-IDF trap) ───────────────────────

@pytest.mark.parametrize("word,stem", [
    ("bands", "band"),
    ("filtration", "filtrat"),
    ("covers", "cover"),
    ("radios", "radio"),
    ("running", "run"),
    ("kaito", "kaito"),
    ("ka700", "ka700"),
])
def test_stemmer_matches_the_indexed_form(tmp_path, word, stem):
    _make_shard(tmp_path, "corpus", CORPUS)
    assert FTSSearcher(str(tmp_path))._stem(word) == stem


def test_vocab_lookup_uses_stems_not_surface_forms(tmp_path):
    """Asserts the DF path directly: the trap is a silent zero, not an error.

    fts5vocab holds post-tokenization terms, so looking up "bands" finds
    nothing while the index actually contains "band".
    """
    _make_shard(tmp_path, "radios", [
        ("A/KA700", "Kaito KA700", "The radio covers many bands and filtration.")])
    searcher = FTSSearcher(str(tmp_path))
    conn = sqlite3.connect(os.path.join(str(tmp_path), "radios.db"))
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.vocab "
                     "USING fts5vocab(main, fts, row)")

        def df(term):
            row = conn.execute("SELECT doc FROM temp.vocab WHERE term = ?",
                               (term,)).fetchone()
            return row[0] if row else 0

        # The surface forms are absent from the vocab...
        assert df("bands") == 0
        assert df("filtration") == 0
        # ...but the stems the searcher looks up are present.
        assert df(searcher._stem("bands")) == 1
        assert df(searcher._stem("filtration")) == 1
    finally:
        conn.close()


def test_stemmed_terms_get_nonzero_idf_end_to_end(tmp_path):
    """A query using surface forms must still find its rare terms."""
    _make_shard(tmp_path, "aa_noise", _filler_docs("noise", 300, seed=5))
    _make_shard(tmp_path, "zz_radios", [
        ("A/KA700", "Kaito KA700 manual",
         "This radio covers the shortwave bands."),
        ("A/Extra", "Kaito KA700 addendum", "More about the bands it covers.")])
    hits = FTSSearcher(str(tmp_path)).search(
        "What bands does this cover for wireless", max_results=5)
    assert hits[0]['library_id'] == 'zz_radios'


def test_stem_cache_is_reused(tmp_path):
    _make_shard(tmp_path, "corpus", CORPUS)
    searcher = FTSSearcher(str(tmp_path))
    assert searcher._stem("bands") == "band"
    assert searcher._stem_cache["bands"] == ("band",)
    assert searcher._stem("bands") == "band"


def test_stem_terms_splits_compounds(tmp_path):
    """"\\w" keeps underscores; unicode61 splits them into several terms."""
    _make_shard(tmp_path, "corpus", CORPUS)
    searcher = FTSSearcher(str(tmp_path))
    assert searcher._stem_terms("bands") == ("band",)
    # Token order and multiplicity are preserved: an anagram compound or a
    # repeated-token compound is a different phrase, not a duplicate.
    assert searcher._stem_terms("water_storage") == ("water", "storag")
    assert searcher._stem_terms("storage_water") == ("storag", "water")
    assert searcher._stem_terms("water_water") == ("water", "water")
    # The single-term view is the compound's leading token.
    assert searcher._stem("water_storage") == "water"


# ── Vocab table mechanics ─────────────────────────────────────────────────

def test_vocab_table_is_idempotent_and_works_read_only(tmp_path):
    """The vocab table is reader-side: no shard format change, no rebuild."""
    _make_shard(tmp_path, "corpus", CORPUS)
    db = os.path.join(str(tmp_path), "corpus.db")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.vocab "
                     "USING fts5vocab(main, fts, row)")
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.vocab "
                     "USING fts5vocab(main, fts, row)")
        terms = conn.execute("SELECT term, doc FROM temp.vocab "
                             "WHERE term = 'filtrat'").fetchone()
        assert terms is not None and terms[1] >= 1
    finally:
        conn.close()


def test_total_docs_sums_shard_meta(tmp_path):
    _make_shard(tmp_path, "lib_a", CORPUS)           # 3 docs
    _make_shard(tmp_path, "lib_b", CORPUS[:1])       # 1 doc
    searcher = FTSSearcher(str(tmp_path))
    assert searcher.total_docs == 4


def test_total_docs_ignores_incomplete_shards(tmp_path):
    _make_shard(tmp_path, "done", CORPUS)
    _make_shard(tmp_path, "half", CORPUS, status='building')
    assert FTSSearcher(str(tmp_path)).total_docs == 3


def test_refresh_reloads_doc_counts(tmp_path):
    searcher = FTSSearcher(str(tmp_path))
    assert searcher.total_docs == 0
    _make_shard(tmp_path, "corpus", CORPUS)
    searcher.refresh()
    assert searcher.total_docs == 3


def test_idf_is_higher_for_rarer_terms():
    from puffpai.knowledge.fts_index import _idf
    assert _idf(1, 10_000) > _idf(5_000, 10_000)
    # A term in every document is floored, never zero or negative.
    assert _idf(10_000, 10_000) > 0.0


def test_relevance_from_bm25_normalization():
    # bm25 is negative-is-better; the global best hit normalizes to 1.0.
    assert _relevance_from_bm25(-10.0, -10.0) == pytest.approx(1.0)
    assert _relevance_from_bm25(-5.0, -10.0) == pytest.approx(0.75)
    assert _relevance_from_bm25(0.0, -10.0) == pytest.approx(0.5)
    # Degenerate shard (no discriminating score) stays mid-band, not 0.
    assert _relevance_from_bm25(0.0, 0.0) == pytest.approx(0.5)


# ── FTS directory resolution ──────────────────────────────────────────────

class _Paths:
    def __init__(self, fts_dir="", zim_dirs=None, chromadb_path="./kiwix_rag_db"):
        self.fts_dir = fts_dir
        self.zim_dirs = zim_dirs or []
        self.chromadb_path = chromadb_path


class _Cfg:
    def __init__(self, paths):
        self.paths = paths


def test_resolve_fts_dir_prefers_explicit_setting(tmp_path):
    cfg = _Cfg(_Paths(fts_dir=str(tmp_path / "custom")))
    assert resolve_fts_dir(cfg) == os.path.abspath(str(tmp_path / "custom"))


def test_resolve_fts_dir_derives_sibling_of_zim_dir():
    cfg = _Cfg(_Paths(zim_dirs=["/data/puffpai/knowledge/zim"]))
    resolved = resolve_fts_dir(cfg)
    assert os.path.basename(resolved) == "fts"
    assert os.path.basename(os.path.dirname(resolved)) == "knowledge"


def test_resolve_fts_dir_falls_back_to_chromadb_sibling(tmp_path):
    cfg = _Cfg(_Paths(chromadb_path=str(tmp_path / "chroma")))
    resolved = resolve_fts_dir(cfg)
    assert os.path.basename(resolved) == "knowledge_fts"
    assert os.path.dirname(resolved) == os.path.abspath(str(tmp_path))


@pytest.mark.skipif(sys.platform != "win32", reason="a UNC path is a Windows path")
def test_resolve_fts_dir_warns_on_unc_path(caplog):
    """SQLite WAL over SMB is unsupported — warn loudly, but don't block."""
    cfg = _Cfg(_Paths(fts_dir=r"\\nas\media\fts"))
    with caplog.at_level("WARNING", logger="puffpai.knowledge.fts_index"):
        resolved = resolve_fts_dir(cfg)
    assert resolved  # not blocked
    assert "UNC network path" in caplog.text
    assert "paths.fts_dir" in caplog.text


@pytest.mark.skipif(sys.platform != "win32", reason="a UNC path is a Windows path")
def test_resolve_fts_dir_warns_for_derived_unc_path(caplog):
    cfg = _Cfg(_Paths(zim_dirs=[r"\\nas\media\library"]))
    with caplog.at_level("WARNING", logger="puffpai.knowledge.fts_index"):
        resolve_fts_dir(cfg)
    assert "UNC network path" in caplog.text


def test_resolve_fts_dir_quiet_for_local_path(tmp_path, caplog):
    cfg = _Cfg(_Paths(fts_dir=str(tmp_path / "fts")))
    with caplog.at_level("WARNING", logger="puffpai.knowledge.fts_index"):
        resolve_fts_dir(cfg)
    assert "UNC" not in caplog.text


# ── CLI output encoding ───────────────────────────────────────────────────

def test_cli_output_is_pure_ascii():
    """The builder is normally run with stdout piped to a log file.

    On a non-UTF-8 locale a single non-ASCII character in any print() raises
    UnicodeEncodeError and takes down a multi-hour build, so the CLI module's
    source is held to ASCII-only.
    """
    from puffpai.knowledge import fts_build_cli
    source = open(fts_build_cli.__file__, encoding='utf-8').read()
    offenders = sorted({c for c in source if ord(c) > 127})
    assert offenders == [], f"non-ASCII in CLI source: {offenders}"


def test_cli_summary_survives_cp1252_stdout(tmp_path, capsys):
    """Reproduces the redirected-stdout crash: every line must encode."""
    from puffpai.knowledge import fts_build_cli

    exit_code = fts_build_cli.main([
        '--zim-dir', str(tmp_path / 'no_zims_here'),
        '--fts-dir', str(tmp_path / 'fts'),
    ])
    assert exit_code == 1  # no ZIMs found
    captured = capsys.readouterr()
    for stream in (captured.out, captured.err):
        stream.encode('cp1252')  # must not raise UnicodeEncodeError


def test_cli_reports_errors_but_exits_zero(tmp_path, monkeypatch, capsys):
    """Contract: a truncated pack is reported, the run still exits 0."""
    from puffpai.knowledge import fts_build_cli

    zim_dir = tmp_path / "zim"
    zim_dir.mkdir()
    zim_path = zim_dir / "broken_pack.zim"
    zim_path.write_bytes(b"\x00" * 8192)

    monkeypatch.setattr(fts_build_cli, 'discover_zim_files',
                        lambda **kw: [str(zim_path)])
    monkeypatch.setattr(fts_build_cli.FTSBuilder, 'build',
                        lambda self, p, d, **kw: _errored_summary(p, d))

    exit_code = fts_build_cli.main([
        '--zim-dir', str(zim_dir), '--fts-dir', str(tmp_path / 'fts')])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert 'ERROR' in captured.out
    assert 'Failed libraries' in captured.out
    captured.out.encode('cp1252')


def _errored_summary(zim_path, fts_dir):
    from puffpai.knowledge.fts_index import BuildSummary
    return BuildSummary(
        library_id=library_id_for(zim_path), zim_path=zim_path,
        shard_path=shard_path_for(zim_path, fts_dir),
        status='error', error='truncated ZIM: 4,096 bytes missing')


# ── Builder guard rails ───────────────────────────────────────────────────

def test_build_reports_error_for_unreadable_zim(tmp_path):
    summary = FTSBuilder().build(str(tmp_path / "missing.zim"), str(tmp_path / "fts"))
    assert summary.status == 'error'
    assert summary.ok is False
    assert 'cannot stat ZIM' in summary.error


class _FakeEntry:
    def __init__(self, index, mime_type='text/html', url=None, title=None,
                 namespace='C', is_redirect=False):
        self.index = index
        self.namespace = namespace
        self.url = url if url is not None else f"Article_{index}"
        self.title = title if title is not None else f"Article {index}"
        self.mime_type = mime_type
        self.is_redirect = is_redirect
        self.redirect_index = 0


class _FakeArticle:
    """Stand-in exposing both accessors the walker uses.

    ``plain_text`` is the HTML-stripped form, ``text`` the raw decoded blob —
    the same split as the real Article, so the test can tell which one the
    walker picked for a given mime type.
    """

    def __init__(self, index=None, plain_text=None, text=None):
        if plain_text is None:
            plain_text = (f"Body of article {index}. "
                          f"It mentions widget{index} exactly once.")
        self.plain_text = plain_text
        self.text = text if text is not None else plain_text


class _FakeZIM:
    """Minimal ZIMReader stand-in so the real walk/resume path is exercised.

    ``truncate_at`` makes the archive raise ZIMTruncatedError partway through
    the scan, reproducing a pack whose header validates but whose tail is
    missing. ``entries`` supplies an explicit mixed-mime corpus instead of the
    default run of HTML articles.
    """

    def __init__(self, path, entry_count=1234, truncate_at=None, entries=None):
        self.filepath = path
        self.entries = entries
        self.entry_count = len(entries) if entries is not None else entry_count
        self.truncate_at = truncate_at
        self.closed = False
        self.articles_fetched = []

    def get_entry(self, index):
        if self.truncate_at is not None and index >= self.truncate_at:
            raise ZIMTruncatedError(self.filepath, 1024, 8192)
        if self.entries is None:
            return _FakeEntry(index)
        spec = self.entries[index]
        return _FakeEntry(
            index,
            mime_type=spec['mime'],
            url=spec['url'],
            title=spec['title'],
            namespace=spec.get('namespace', 'C'),
            is_redirect=spec.get('is_redirect', False),
        )

    def get_article(self, index, follow_redirects=True):
        self.articles_fetched.append(index)
        if self.entries is None:
            return _FakeArticle(index)
        spec = self.entries[index]
        return _FakeArticle(plain_text=spec.get('plain_text', spec.get('text', '')),
                            text=spec.get('text', ''))

    def close(self):
        self.closed = True


# Mirrors the shape of a real zimgit pack: a couple of HTML viewer-shell
# entries, the actual content as text/plain and PDFs, and a pile of assets.
MIXED_PACK = [
    {'mime': 'text/html', 'url': 'index.html', 'title': 'Viewer shell',
     'plain_text': 'Loading the offline viewer application shell.',
     'text': '<html><body>Loading the offline viewer application shell.</body></html>'},
    {'mime': 'text/plain', 'url': 'guides/filtration.txt',
     'title': 'Slow sand filtration',
     'text': 'Slow sand filtration removes pathogens. Keep turbidity <5 NTU '
             'and the schmutzdecke intact.'},
    {'mime': 'text/plain; charset=utf-8', 'url': 'guides/chlorine.txt',
     'title': 'Chlorination',
     'text': 'Chlorination dosing uses sodium hypochlorite at 2 mg/L.'},
    {'mime': 'text/markdown', 'url': 'notes/wells.md', 'title': 'Hand-dug wells',
     'text': '# Hand-dug wells\n\nLine the shaft with concrete rings.'},
    {'mime': 'text/plain', 'url': 'guides/handpump_maintenance',
     'title': 'Handpump maintenance',
     'text': 'Extensionless prose. Replace the leathers when discharge drops. '
             'Covers filtration of the rising main.'},
    {'mime': 'application/pdf', 'url': 'manuals/borehole_manual.pdf',
     'title': 'Borehole drilling manual', 'text': '%PDF-1.4 binary junk'},
    # Web-app machinery served as text/plain -- the on-box failure mode.
    {'mime': 'text/plain', 'url': 'vendors/bootstrap/js/bootstrap.min.js.map',
     'title': 'bootstrap.min.js.map',
     'text': 'sourcemap mappings filtration filter filter filter'},
    {'mime': 'text/plain', 'url': 'templates/display_rows.handlebars',
     'title': 'display_rows.handlebars',
     'text': '{{#each rows}} filtration {{/each}}'},
    {'mime': 'text/plain', 'url': 'static/config.yaml', 'title': 'config.yaml',
     'text': 'filtration: enabled'},
    {'mime': 'text/plain', 'url': 'build/tsconfig.json', 'title': 'tsconfig.json',
     'text': '{"filtration": true}'},
    {'mime': 'text/plain', 'url': 'logs/build.log', 'title': 'build.log',
     'text': 'INFO filtration pipeline started'},
    {'mime': 'text/plain', 'url': 'data/records.sqlite3',
     'title': 'records.sqlite3', 'text': 'binary filtration blob'},
    {'mime': 'text/css', 'url': 'assets/style.css', 'title': 'style.css',
     'text': 'body { filtration: none; }'},
    {'mime': 'application/javascript', 'url': 'assets/app.js', 'title': 'app.js',
     'text': 'function filtration() { return 1; }'},
    {'mime': 'application/json', 'url': 'assets/data.json', 'title': 'data.json',
     'text': '{"filtration": true}'},
    {'mime': 'image/svg+xml', 'url': 'assets/icon.svg', 'title': 'icon.svg',
     'text': '<svg><title>filtration</title></svg>'},
    {'mime': 'application/wasm', 'url': 'assets/mod.wasm', 'title': 'mod.wasm',
     'text': 'wasm filtration'},
    {'mime': 'text/plain', 'url': 'meta/about.txt', 'title': 'About',
     'namespace': 'M', 'text': 'Metadata namespace filtration blurb.'},
    {'mime': 'text/plain', 'url': 'guides/old.txt', 'title': 'Redirected',
     'is_redirect': True, 'text': 'filtration redirect'},
]


def _pack_entry(url):
    """Look a MIXED_PACK entry up by url (never by position)."""
    for spec in MIXED_PACK:
        if spec['url'] == url:
            return spec
    raise KeyError(url)


@pytest.fixture
def fake_zim(tmp_path, monkeypatch):
    """Install a synthetic ZIM archive and return (zim_path, fts_dir, install)."""
    from puffpai.knowledge import fts_index

    zim_path = tmp_path / "synthetic_pack.zim"
    zim_path.write_bytes(b"\x00" * 8192)
    fts_dir = tmp_path / "fts"
    holder = {}

    def install(**kwargs):
        def _factory(path):
            holder['reader'] = _FakeZIM(path, **kwargs)
            return holder['reader']
        monkeypatch.setattr(fts_index, 'ZIMReader', _factory)
        return holder

    install()
    return str(zim_path), str(fts_dir), install


# ── Mime policy (what actually counts as content) ─────────────────────────

@pytest.fixture
def mixed_shard(fake_zim):
    """Build a shard from the mixed-mime pack and return (summary, fts_dir)."""
    zim_path, fts_dir, install = fake_zim
    install(entries=MIXED_PACK)
    summary = FTSBuilder().build(zim_path, fts_dir, batch_size=5)
    assert summary.status == 'complete'
    return summary, fts_dir


def test_mixed_pack_indexes_content_not_assets(mixed_shard):
    """Regression: a zimgit pack's content is text/plain and PDFs, not HTML.

    Filtering on text/html alone indexed only the viewer shell, so a water
    pack returned nothing for "water filtration".
    """
    summary, fts_dir = mixed_shard
    conn = sqlite3.connect(summary.shard_path)
    try:
        paths = {r[0] for r in conn.execute("SELECT path FROM docs")}
    finally:
        conn.close()

    assert paths == {
        'C/index.html',                    # html
        'C/guides/filtration.txt',         # plain
        'C/guides/chlorine.txt',           # plain, with charset parameter
        'C/notes/wells.md',                # markdown
        'C/guides/handpump_maintenance',   # plain, extensionless prose
        'C/manuals/borehole_manual.pdf',   # pdf, title only
    }
    assert summary.articles_indexed == 6


@pytest.mark.parametrize("excluded", [
    'C/assets/style.css',
    'C/assets/app.js',
    'C/assets/data.json',
    'C/assets/icon.svg',
    'C/assets/mod.wasm',
    'M/meta/about.txt',      # metadata namespace
    'C/guides/old.txt',      # redirect
    # Machinery served as text/plain.
    'C/vendors/bootstrap/js/bootstrap.min.js.map',
    'C/templates/display_rows.handlebars',
    'C/static/config.yaml',
    'C/build/tsconfig.json',
    'C/logs/build.log',
    'C/data/records.sqlite3',   # unknown extension defaults to skip
])
def test_mixed_pack_excludes_assets_metadata_and_redirects(mixed_shard, excluded):
    summary, _ = mixed_shard
    conn = sqlite3.connect(summary.shard_path)
    try:
        found = conn.execute("SELECT 1 FROM docs WHERE path = ?",
                             (excluded,)).fetchone()
    finally:
        conn.close()
    assert found is None


def test_asset_bodies_are_not_searchable(mixed_shard):
    """Every excluded entry contains the word 'filtration' as a tripwire.

    The two paths that do come back are the positive control: the filter
    excludes machinery without blanket-excluding raw text.
    """
    _, fts_dir = mixed_shard
    hits = FTSSearcher(fts_dir).search("filtration", max_results=50)
    paths = {h['path'] for h in hits}
    assert paths == {'C/guides/filtration.txt', 'C/guides/handpump_maintenance'}


def test_sourcemap_never_outranks_real_content(mixed_shard):
    """On-box symptom: searching 'filter' returned the bootstrap sourcemap.

    The sourcemap repeats 'filter' four times, so under plain bm25 it would
    win outright. It must not appear at all.
    """
    _, fts_dir = mixed_shard
    hits = FTSSearcher(fts_dir).search("filter", max_results=50)
    titles = [h['title'] for h in hits]
    assert 'bootstrap.min.js.map' not in titles
    assert 'display_rows.handlebars' not in titles


def test_extensionless_prose_is_indexed(mixed_shard):
    """Many ZIM article paths have no extension at all; those are content."""
    _, fts_dir = mixed_shard
    hits = FTSSearcher(fts_dir).search("leathers discharge")
    assert [h['path'] for h in hits] == ['C/guides/handpump_maintenance']


@pytest.mark.parametrize("url", [
    'guides/filtration.txt',
    'notes/wells.md',
    'notes/wells.markdown',
    'notes/wells.text',
    'notes/wells.rst',
    'guides/handpump_maintenance',       # no extension
    'A/U.S._Constitution',               # dots, but not an extension
    'articles/Nitrogen_(element)',
    'wiki/Rev._Martin_Luther_King',
])
def test_classify_text_path_indexes_prose(url):
    from puffpai.knowledge.fts_index import classify_text_path
    assert classify_text_path(url) == '', url


@pytest.mark.parametrize("url", [
    'vendors/bootstrap/js/bootstrap.min.js.map',
    'templates/display_rows.handlebars',
    'templates/row.mustache',
    'app/main.js',
    'app/main.mjs',
    'style/theme.css',
    'data/config.json',
    'data/config.yaml',
    'data/config.yml',
    'data/config.toml',
    'data/settings.ini',
    'data/settings.cfg',
    'logs/build.log',
    'subs/talk.vtt',
    'subs/talk.srt',
    'feed/sitemap.xml',
    'vendor/lib/thing.txt',        # junk directory beats a prose extension
    'node_modules/pkg/readme.md',
    'assets/notes.txt',
    'static/notes.txt',
    '_static/notes.txt',
    'fonts/notes.txt',
    'js/notes.txt',
    'css/notes.txt',
    'data/records.sqlite3',        # unknown extension -> conservative skip
    'bin/tool.exe',
])
def test_classify_text_path_rejects_machinery(url):
    from puffpai.knowledge.fts_index import classify_text_path
    assert classify_text_path(url) != '', url


def test_pdf_in_machinery_directory_is_skipped(fake_zim):
    zim_path, fts_dir, install = fake_zim
    install(entries=[
        {'mime': 'application/pdf', 'url': 'assets/print_stylesheet_widget.pdf',
         'title': 'Widget print stylesheet', 'text': '%PDF'},
        {'mime': 'application/pdf', 'url': 'manuals/widget_manual.pdf',
         'title': 'Widget manual', 'text': '%PDF'},
    ])
    FTSBuilder().build(zim_path, fts_dir, batch_size=5)
    hits = FTSSearcher(fts_dir).search("widget", max_results=50)
    assert [h['path'] for h in hits] == ['C/manuals/widget_manual.pdf']


def test_plain_text_body_is_searchable_verbatim(mixed_shard):
    """Raw text must not go through the HTML stripper."""
    _, fts_dir = mixed_shard
    searcher = FTSSearcher(fts_dir)
    assert [h['title'] for h in searcher.search("schmutzdecke")] == \
        ["Slow sand filtration"]
    # "<5 NTU" would be eaten as a tag by the HTML stripper. It survives the
    # raw-text path, and the angle bracket is escaped (not dropped) on output.
    hit = searcher.search("NTU")[0]
    assert '&lt;5 <b>NTU</b>' in hit['snippet']


def test_markdown_body_is_searchable(mixed_shard):
    _, fts_dir = mixed_shard
    hits = FTSSearcher(fts_dir).search("concrete rings")
    assert [h['title'] for h in hits] == ["Hand-dug wells"]


def test_html_body_still_uses_the_stripper(fake_zim):
    """HTML keeps the plain_text path: markup must not become search terms."""
    zim_path, fts_dir, install = fake_zim
    install(entries=[_pack_entry('index.html')])
    FTSBuilder().build(zim_path, fts_dir, batch_size=5)
    searcher = FTSSearcher(fts_dir)
    assert searcher.search("viewer application shell")     # stripped text
    assert searcher.search("body") == []                   # tag name, not content


def test_pdf_is_findable_by_title_with_empty_body(mixed_shard):
    summary, fts_dir = mixed_shard
    conn = sqlite3.connect(summary.shard_path)
    try:
        body = conn.execute(
            "SELECT body FROM fts JOIN docs d ON d.id = fts.rowid "
            "WHERE d.path = 'C/manuals/borehole_manual.pdf'").fetchone()[0]
    finally:
        conn.close()
    assert body == ''

    hits = FTSSearcher(fts_dir).search("borehole drilling")
    assert len(hits) == 1
    assert hits[0]['path'] == 'C/manuals/borehole_manual.pdf'
    # Empty body must not yield a blank row: fall back to the escaped title.
    assert hits[0]['snippet'] == 'Borehole drilling manual'
    assert 0.0 <= hits[0]['relevance'] <= 1.0


def test_pdf_body_is_not_fetched_from_the_archive(fake_zim):
    """Title-only records skip blob decompression entirely."""
    zim_path, fts_dir, install = fake_zim
    holder = install(entries=[_pack_entry('manuals/borehole_manual.pdf')])
    FTSBuilder().build(zim_path, fts_dir, batch_size=5)
    assert holder['reader'].articles_fetched == []


def test_zimgit_water_pack_shape(fake_zim):
    """Reproduces the on-box gap with zimgit-water_en_2024-08's real mime mix.

    818 entries: 366 svg, 212 js, 167 json, 17 text/plain, 15 wasm, 7 pdf,
    2 text/html. HTML-only filtering indexed 2 of 818 and "water filtration"
    matched nothing; the content lives in the text/plain files and PDFs.
    """
    zim_path, fts_dir, install = fake_zim
    entries = []
    for n in range(366):
        entries.append({'mime': 'image/svg+xml', 'url': f'a/i{n}.svg',
                        'title': f'i{n}', 'text': '<svg/>'})
    for n in range(212):
        entries.append({'mime': 'application/javascript', 'url': f'a/s{n}.js',
                        'title': f's{n}', 'text': 'var x=1;'})
    for n in range(167):
        entries.append({'mime': 'application/json', 'url': f'a/d{n}.json',
                        'title': f'd{n}', 'text': '{}'})
    # The 17 text/plain entries are NOT all prose. On the real pack most are
    # sourcemaps and handlebars templates served under text/plain; only a few
    # are genuine documents.
    for n in range(8):
        entries.append({'mime': 'text/plain',
                        'url': f'vendors/bootstrap/js/b{n}.min.js.map',
                        'title': f'b{n}.min.js.map',
                        'text': 'sourcemap water filtration filter filter'})
    for n in range(4):
        entries.append({'mime': 'text/plain',
                        'url': f'templates/display_rows{n}.handlebars',
                        'title': f'display_rows{n}.handlebars',
                        'text': '{{#each}} water filtration {{/each}}'})
    for n in range(5):
        entries.append({'mime': 'text/plain', 'url': f'doc/water_guide_{n}',
                        'title': f'Water document {n}',
                        'text': f'Water filtration technique number {n}.'})
    for n in range(15):
        entries.append({'mime': 'application/wasm', 'url': f'a/m{n}.wasm',
                        'title': f'm{n}', 'text': 'wasm'})
    for n in range(7):
        entries.append({'mime': 'application/pdf', 'url': f'doc/manual{n}.pdf',
                        'title': f'Water supply manual {n}', 'text': '%PDF'})
    for n in range(2):
        entries.append({'mime': 'text/html', 'url': f'index{n}.html',
                        'title': f'Viewer {n}', 'plain_text': 'viewer shell',
                        'text': '<html>viewer shell</html>'})
    # The counted mimes total 786; the remaining 32 of the pack's 818 entries
    # are other assets (fonts, images, css) that must also stay out.
    for n in range(32):
        entries.append({'mime': 'font/woff2' if n % 2 else 'image/png',
                        'url': f'a/f{n}', 'title': f'f{n}',
                        'text': 'water filtration'})
    assert len(entries) == 818

    install(entries=entries)
    summary = FTSBuilder().build(zim_path, fts_dir, batch_size=100)

    assert summary.total_entries == 818
    # 5 prose docs + 7 pdf + 2 html. The other 12 text/plain entries are
    # sourcemaps and templates and must not be indexed at all.
    assert summary.articles_indexed == 14

    searcher = FTSSearcher(fts_dir)
    hits = searcher.search("water filtration", max_results=50)
    assert len(hits) == 5                   # was 0 under HTML-only filtering
    assert all(h['path'].startswith('C/doc/water_guide_') for h in hits)

    hits = searcher.search("water supply manual", max_results=50)
    assert len(hits) == 7                   # PDFs, by title

    # The machinery is invisible however you search for it.
    assert searcher.search("sourcemap", max_results=50) == []
    assert [h for h in searcher.search("filter", max_results=50)
            if '.map' in h['path'] or '.handlebars' in h['path']] == []


def test_title_only_snippet_escapes_markup(fake_zim):
    zim_path, fts_dir, install = fake_zim
    install(entries=[{'mime': 'application/pdf', 'url': 'x.pdf',
                      'title': '<script>alert(1)</script> widget manual',
                      'text': ''}])
    FTSBuilder().build(zim_path, fts_dir, batch_size=5)
    snippet = FTSSearcher(fts_dir).search("widget")[0]['snippet']
    assert '&lt;script&gt;' in snippet
    assert '<script>' not in snippet


# ── Resume (the checkpoint must actually survive a restart) ───────────────

def test_shard_status_exposes_resume_checkpoint(tmp_path, fake_zim):
    """Regression: build() reads last_entry_index back via shard_status().

    When that key was missing from shard_status's fixed key list, every
    restart silently looked like a fresh build and dropped the shard.
    """
    zim_path, fts_dir, _ = fake_zim
    summary = FTSBuilder().build(zim_path, fts_dir, batch_size=100)
    info = shard_status(summary.shard_path)
    assert 'last_entry_index' in info
    assert info['last_entry_index'] == str(summary.total_entries - 1)


def test_build_resumes_after_interruption(tmp_path, fake_zim):
    """Crash mid-build, rebuild, and land on an exact, duplicate-free index."""
    zim_path, fts_dir, _ = fake_zim

    class _Boom(Exception):
        pass

    def _crash_after_1000(done, total, indexed):
        if done >= 1000:
            raise _Boom()

    builder = FTSBuilder()
    with pytest.raises(_Boom):
        builder.build(zim_path, fts_dir, batch_size=500,
                      progress_cb=_crash_after_1000)

    partial = shard_status(os.path.join(fts_dir, "synthetic_pack.db"))
    assert partial['status'] == 'building'
    assert partial['last_entry_index'] == '999'
    # A half-built shard is invisible to search.
    assert FTSSearcher(fts_dir).ready_library_ids() == []

    summary = builder.build(zim_path, fts_dir, batch_size=500)
    assert summary.status == 'complete'
    assert summary.resumed_from == 1000

    conn = sqlite3.connect(summary.shard_path)
    try:
        docs = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        fts = conn.execute("SELECT COUNT(*) FROM fts").fetchone()[0]
        distinct = conn.execute("SELECT COUNT(DISTINCT path) FROM docs").fetchone()[0]
        mismatched = conn.execute(
            "SELECT COUNT(*) FROM fts JOIN docs d ON d.id = fts.rowid "
            "WHERE d.title != fts.title").fetchone()[0]
    finally:
        conn.close()

    assert docs == 1234          # every entry indexed exactly once
    assert fts == 1234           # fts and docs stay coupled across the resume
    assert distinct == 1234      # no duplicates from the replayed batch
    assert mismatched == 0       # rowid alignment survived the restart
    assert summary.articles_indexed == 1234

    # And the resumed shard is genuinely searchable end to end.
    hits = FTSSearcher(fts_dir).search("widget1200")
    assert [h['title'] for h in hits] == ["Article 1200"]


def test_build_skips_completed_shard_on_rerun(tmp_path, fake_zim):
    zim_path, fts_dir, _ = fake_zim
    builder = FTSBuilder()
    builder.build(zim_path, fts_dir, batch_size=500)
    again = builder.build(zim_path, fts_dir, batch_size=500)
    assert again.status == 'skipped'
    assert again.articles_indexed == 1234


def test_force_rebuilds_from_scratch(tmp_path, fake_zim):
    zim_path, fts_dir, _ = fake_zim
    builder = FTSBuilder()
    builder.build(zim_path, fts_dir, batch_size=500)
    forced = builder.build(zim_path, fts_dir, batch_size=500, force=True)
    assert forced.status == 'complete'
    assert forced.resumed_from == 0
    conn = sqlite3.connect(forced.shard_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0] == 1234
    finally:
        conn.close()


def test_mid_scan_truncation_marks_shard_error(tmp_path, fake_zim):
    """A pack that dies partway through the scan must never say 'complete'."""
    zim_path, fts_dir, install = fake_zim
    install(truncate_at=700)

    summary = FTSBuilder().build(zim_path, fts_dir, batch_size=500)
    assert summary.status == 'error'
    assert 'truncated ZIM at entry 700' in summary.error

    info = shard_status(summary.shard_path)
    assert info['status'] == 'error'
    # A partial index must not be published as searchable.
    assert FTSSearcher(fts_dir).ready_library_ids() == []


def test_build_marks_shard_error_for_truncated_zim(tmp_path, monkeypatch):
    """A partially copied pack is recorded, not raised — the loop continues."""
    from puffpai.knowledge import fts_index
    from puffpai.knowledge.zim_reader import ZIMTruncatedError

    zim_path = tmp_path / "broken_pack.zim"
    zim_path.write_bytes(b"\x00" * 128)
    fts_dir = tmp_path / "fts"

    def _boom(path):
        raise ZIMTruncatedError(str(path), 128, 4096)

    monkeypatch.setattr(fts_index, 'ZIMReader', _boom)

    summary = fts_index.FTSBuilder().build(str(zim_path), str(fts_dir))
    assert summary.status == 'error'
    assert 'truncated ZIM' in summary.error

    info = shard_status(summary.shard_path)
    assert info['status'] == 'error'
    # A shard in error state is never searched.
    assert FTSSearcher(str(fts_dir)).ready_library_ids() == []


# ── Title search ──────────────────────────────────────────────────────────

def test_search_titles_matches_on_title_only(tmp_path):
    _make_shard(tmp_path, "corpus", CORPUS)
    hits = FTSSearcher(str(tmp_path)).search_titles("filtration")
    assert [h['title'] for h in hits] == ["Water filtration"]
    # "cistern" appears only in a body; a title search must not surface it.
    assert FTSSearcher(str(tmp_path)).search_titles("cistern") == []


def test_search_titles_result_shape(tmp_path):
    _make_shard(tmp_path, "corpus", CORPUS)
    hit = FTSSearcher(str(tmp_path)).search_titles("solar power")[0]
    assert set(hit) == {'library_id', 'path', 'title'}
    assert hit['path'] == "A/Solar_power"
    assert hit['library_id'] == "corpus"


def test_search_titles_or_fallback(tmp_path):
    """No title carries both words, so the OR pass surfaces each candidate."""
    _make_shard(tmp_path, "corpus", CORPUS)
    hits = FTSSearcher(str(tmp_path)).search_titles("solar rainwater")
    assert {h['title'] for h in hits} == {"Solar power", "Rainwater harvesting"}


def test_search_titles_respects_limits(tmp_path):
    _make_shard(tmp_path, "one", CORPUS)
    _make_shard(tmp_path, "two", CORPUS)
    assert len(FTSSearcher(str(tmp_path)).search_titles(
        "water", max_results=1)) == 1
    assert FTSSearcher(str(tmp_path)).search_titles(
        "water", max_results=0) == []


def test_search_titles_per_library_cap(tmp_path):
    records = [(f"A/W{i}", f"Water article {i}", "body text")
               for i in range(10)]
    _make_shard(tmp_path, "many", records)
    hits = FTSSearcher(str(tmp_path)).search_titles("water", per_library=2)
    assert len(hits) == 2


@pytest.mark.parametrize("hostile", [
    '"unbalanced', 'title : (x)', 'a NEAR/3 b', '(((', '*', 'aid OR',
])
def test_search_titles_hostile_input_is_safe(tmp_path, hostile):
    """Operator characters must arrive as quoted terms, never as syntax."""
    _make_shard(tmp_path, "corpus", CORPUS)
    FTSSearcher(str(tmp_path)).search_titles(hostile)  # must not raise
