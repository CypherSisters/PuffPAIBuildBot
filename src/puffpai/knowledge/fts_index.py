"""
PuffPAI Full-Text Knowledge Index

Builds and queries a pure-stdlib SQLite FTS5 index over the plain text of
ZIM archive articles. This is PuffPAI's own full-text search: no kiwix-serve,
no libzim, no xapian, no new pip dependencies. sqlite3 ships with Python and
already carries FTS5 in every CPython build we target.

Layout: one shard database per ZIM file, named after a stable library id
derived from the ZIM filename:

    <fts_dir>/wikipedia_en_all_maxi_2024_01.db

Each shard is self-describing (a ``meta`` table records the source ZIM's size
and mtime plus a build status), so a build can be resumed after a crash and a
searcher can tell a finished shard from a half-written one without touching
the ZIM at all.

Usage:
    from puffpai.knowledge.fts_index import FTSBuilder, FTSSearcher

    FTSBuilder().build("/data/zim/wikipedia_en.zim", "/data/fts")

    searcher = FTSSearcher("/data/fts")
    for hit in searcher.search("water filtration", max_results=10):
        print(hit['library_id'], hit['title'], hit['relevance'])

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import html
import logging
import math
import os
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from .zim_reader import ZIMReader, ZIMTruncatedError

logger = logging.getLogger(__name__)

# Bump when the shard schema changes in a way that makes old shards unusable.
# A shard whose schema_version differs is rebuilt from scratch.
SCHEMA_VERSION = "1"

# Namespaces that hold articles. ZIM major version 6+ writes content into 'C';
# older archives use 'A'. Both appear in the wild, often on the same box.
# Namespace 'M' (archive metadata) is deliberately excluded.
ARTICLE_NAMESPACES = ('A', 'C')

# What counts as indexable content.
#
# Indexing text/html alone is wrong for a large slice of the real library: a
# zimgit pack such as zimgit-water_en_2024-08 holds exactly 2 HTML entries (the
# viewer shell) against 17 text/plain files and 7 PDFs that carry all the
# actual content, plus several hundred svg/js/json/wasm assets. Filtering on
# HTML alone indexed the shell and nothing else, so "water filtration" matched
# nothing in a pack about water.
HTML_MIMES = ('text/html',)

# Raw text formats are indexed verbatim. They are NOT run through the HTML
# stripper: plain text is not markup, and stripping would eat any legitimate
# angle-bracket content (code samples, measurements like "<5 mg/L").
RAW_TEXT_MIMES = ('text/plain', 'text/markdown')

# ...but text/plain is also the fallback mime for a pack's web-app machinery.
# The rebuilt zimgit_water shard indexed display_rows.handlebars and
# vendors/bootstrap/js/bootstrap.min.js.map as "content", and a search for
# "filter" returned the bootstrap sourcemap as its top hit. None of that may
# ever reach the RAG pipeline, so raw-text entries are additionally screened
# by path.
JUNK_TEXT_EXTENSIONS = frozenset({
    '.map', '.js', '.mjs', '.css', '.json', '.handlebars', '.mustache',
    '.vtt', '.srt', '.xml', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.log',
})

# A path segment with any of these names marks the whole entry as machinery.
JUNK_PATH_SEGMENTS = frozenset({
    'vendors', 'vendor', 'node_modules', 'assets', 'static', '_static',
    'js', 'css', 'fonts',
})

# Extensions that positively indicate prose.
PROSE_EXTENSIONS = frozenset({'.txt', '.md', '.markdown', '.text', '.rst'})

# What counts as a file extension for the check above: a short run of plain
# alphanumerics. Deliberately strict, because many ZIM article paths are
# extensionless but still contain dots -- "U.S._Constitution" must read as
# "no extension" (and therefore as prose), not as an unknown one.
_FILE_EXT_RE = re.compile(r'\.[a-z0-9]{1,10}$')

INDEXABLE_BODY_MIMES = HTML_MIMES + RAW_TEXT_MIMES

# Indexed by title only, with an empty body. Extracting text from PDF has no
# stdlib-clean answer and this build takes no new dependencies, so the entry is
# made findable by name and openable through the existing article/asset routes.
# Real PDF text extraction is a deliberate future enhancement — when it lands,
# bump SCHEMA_VERSION so existing shards rebuild and pick up the bodies.
TITLE_ONLY_MIMES = ('application/pdf',)

# Everything else — text/css, text/javascript, application/javascript,
# application/json, application/wasm, image/svg+xml, fonts, video — is skipped.
# Those are the viewer's machinery, not the pack's content.


def junk_directory_reason(url: str) -> str:
    """Return a skip reason when any path segment names a machinery directory.

    Empty string means the path is acceptable.
    """
    for segment in url.lower().replace('\\', '/').split('/'):
        if segment in JUNK_PATH_SEGMENTS:
            return f"machinery directory '{segment}'"
    return ''


#: A question-and-answer archive's listing pages: "Highest Voted 'chain'
#: Questions", "questions_page=12", the tag and user indexes. They hold a list
#: of other pages' titles, so they match any word and answer nothing; cited,
#: they made the bot look sourced when it was not.
_LISTING_PAGE_RE = re.compile(
    r"^(?:(?:questions|tags|users|badges|unanswered)/?$"
    r"|(?:questions|tags|users|unanswered)_page="
    r"|questions/tagged/|tags/|users/|badges/)")


def listing_page_reason(url: str) -> str:
    """A skip reason when *url* is an archive's listing page, else ''."""
    u = str(url or "").lower().replace('\\', '/').lstrip('/')
    return "a listing of other pages" if _LISTING_PAGE_RE.match(u) else ''


def classify_text_path(url: str) -> str:
    """Decide whether a raw-text entry is prose or web-app machinery.

    Returns '' to index it, or a short human-readable reason to skip.

    The default for an unrecognized extension is to SKIP. Genuine prose in a
    ZIM is html, .txt/.md, or extensionless; anything else carrying a
    text/plain mime is far more likely to be a template or a sourcemap. Skips
    are logged at debug level so the decision can be audited against a real
    pack.
    """
    reason = junk_directory_reason(url)
    if reason:
        return reason

    basename = url.lower().replace('\\', '/').rsplit('/', 1)[-1]
    match = _FILE_EXT_RE.search(basename)
    ext = match.group(0) if match else ''

    if ext in JUNK_TEXT_EXTENSIONS:
        return f"non-prose extension '{ext}'"
    if not ext or ext in PROSE_EXTENSIONS:
        return ''
    return f"unrecognized extension '{ext}'"


# Defensive cap on a single article's indexed body. A handful of ZIM entries
# are machine-generated list pages measuring tens of megabytes; indexing those
# whole buys nothing and blows out the shard.
MAX_BODY_CHARS = 500_000

# FTS5 treats these as syntax. User queries are stripped of them entirely
# (see sanitize_terms) so a stray quote or paren can never raise
# sqlite3.OperationalError out of a search request.
_FTS_TERM_SPLIT = re.compile(r'[^\w]+', re.UNICODE)

# Ceiling on the OR-fallback pass (see FTSSearcher.search). A rambling
# question must not turn into a disjunction of every word in it.
MAX_OR_TERMS = 24

# Candidates retrieved per shard in stage 1 of the OR pass, before the
# global-IDF re-rank in stage 2. Bounds stage-2 cost at
# shards x this x terms point lookups.
OR_CANDIDATES_PER_SHARD = 10

# Both MATCH passes price out on a sentence's common words, measured live on
# a large install (66 shards, millions of documents) -- but they price out
# DIFFERENTLY, so each pass selects its own affordable term set (the
# selection lives in FTSSearcher._select_terms):
#
#   * OR is walk-bound: bm25-ranking a disjunction walks every posting of
#     every term, so latency is the SUM of the terms' document frequencies
#     -- 12-25s for ordinary sentences before it was budgeted, nearly all
#     of it in "a"/"i"/"on"/"my".
#   * AND is seek-bound: the intersection leapfrogs from the rarest term
#     but must seek into every other term's doclist per candidate,
#     O(rarest-df x terms) -- a 24-term sentence measured 24.6s, warm
#     exactly as slow as cold. A term's OWN frequency barely matters, so
#     AND can afford mid-frequency words ("cut", "hand", "how") that the OR
#     budget must drop. Keeping them is also what keeps the pass precise:
#     "treat" AND "deep" alone answered a wound-care question with
#     Deep-titled film articles.
#
# Keyword queries carry no flooded terms and are untouched by either
# selection.
#
# Frequencies are BUDGETED with flavour-twin shards collapsed (see
# _variant_family): a large install carries English Wikipedia as
# all_maxi + all_nopic + all_mini side by side, which inflated every
# English word's apparent frequency ~1.5-2x and priced terms out of the
# scans without making the scans find any more. The passes themselves
# scan one shard per family for the same reason (_representative_shards).
#
# A term found in at most this many documents is always kept, by both
# passes. Rare terms are where the recall lives (the Kaito KA700 case) and
# their doclists cost microseconds to walk.
MATCH_DF_KEEP_FLOOR = 2_000

# OR-pass budget: costlier terms are admitted rarest-first until their
# summed variant-deduped document frequency crosses this. The OR walk
# itself only scans one shard per variant family (_representative_shards),
# so this is approximately the number of postings actually walked -- a few
# seconds at the several hundred thousand postings/second the reference
# box sustains for a ranked disjunction. A term frequent enough to be
# dropped here carries near-zero IDF, so a document matching only such
# terms would land at the bottom of the re-rank anyway.
MATCH_DF_BUDGET = 2_500_000

# AND-pass per-term ceiling, as a share of the variant-deduped collection.
# Above it a term sits in so many documents that REQUIRING it only steers
# the intersection toward stopword-salad list pages ("a"/"i"/"my"/"do" are
# all >=16% of the deduped reference collection); below it a term still
# discriminates ("how" 14%, "hand" 11%, "cut" 4%). The ceiling never drops
# under MATCH_DF_KEEP_FLOOR, so small (or library-filtered) collections --
# where every term is cheap and strict AND is exactly right -- keep every
# term, byte-identical to the old behavior.
MATCH_AND_DF_CEILING_SHARE = 0.15

# AND-pass cost budget, in doclist seeks: the intersection performs about
# rarest-term-df x (other terms) of them, at RAW frequencies -- every
# variant shard really is scanned. Terms are admitted rarest-first until
# the product crosses this; ~1M seeks/second measured on the reference
# box, so this is a few seconds cold, and warm runs are far faster.
MATCH_AND_SEEK_BUDGET = 4_000_000

# Document-frequency measurement. fts5vocab has no precomputed counters:
# reading a term's ``doc`` column WALKS the term's entire doclist, so asking
# it about "a" costs the very scan the OR selection exists to avoid
# (measured 1.1s for that one stopword on the 8.3M-document Wikipedia
# shard). Shards larger than this many documents therefore get their
# frequencies ESTIMATED from a mid-shard window of this many rowids -- a
# bounded doclist seek that is microseconds regardless of term frequency
# (measured sub-millisecond for "a" on the same shard). A term absent from
# the window falls back to the exact vocab walk, which is cheap precisely
# because the term is rare; shards at or under the window size are always
# measured exactly, so small collections keep exact numbers.
DF_SAMPLE_ROWS = 10_000

# IDF floor, so a term present in every document still contributes a little
# rather than exactly zero.
_MIN_IDF = 0.01


# ── Identity and paths ──────────────────────────────────────────────────────

def library_id_for(zim_path: str) -> str:
    """Return the stable library id for a ZIM file.

    The id is the filename stem, lowercased, with every run of non-alphanumeric
    characters collapsed to a single underscore and the edges trimmed. It
    depends only on the filename, so it is stable across sessions, machines and
    mount points.

        wikipedia_en_all_maxi_2024-01.zim -> wikipedia_en_all_maxi_2024_01
    """
    # Either separator, whatever this computer uses: a record made on
    # Windows (D:\packs\x.zim) names the same library on Linux.
    name = re.split(r"[\\/]", zim_path)[-1]
    stem = os.path.splitext(name)[0]
    slug = re.sub(r'[^a-z0-9]+', '_', stem.lower())
    return slug.strip('_')


def shard_path_for(zim_path: str, fts_dir: str) -> str:
    """Return the shard database path for a ZIM file."""
    return os.path.join(fts_dir, f"{library_id_for(zim_path)}.db")


# Kiwix flavours: renderings of the SAME article set that differ only in
# how much of each article they carry (full/no-images/intro-only). Only
# these, plus the trailing date, mark a shard as a variant of another --
# selection segments (all/top/history, or ted_mul's per-topic playlists)
# name genuinely different document sets and must keep their own families.
_VARIANT_FLAVOURS = frozenset({'maxi', 'mini', 'nopic'})
_VARIANT_DATE_TAIL = re.compile(r'_\d{4}(_\d{2})?$')


def _variant_family(library_id: str) -> str:
    """The variant family a shard belongs to (see _dedup_doc_freqs).

    Kiwix names packs ``<project>_<lang>_<selection>_<flavour>_<date>``,
    and the same corpus+selection routinely ships in several renderings:
    a large install carries wikipedia_en_all as maxi, nopic and mini
    side by side. Summing document frequencies across those counts the
    same English prose three times over, and scanning all of them walks
    the same postings for the same articles.

    The family is the id with the trailing date and flavour stripped
    ("wikipedia_en_all_maxi_2025_08" -> "wikipedia_en_all"), so flavours
    and dates of one corpus collapse while every distinct selection --
    wikipedia_en_top, wikipedia_en_simple_all, each ted_mul topic pack --
    stays its own family. Within a family the budgeted frequency is the
    MAX across members, which is exact: a flavour twin's documents are a
    subset of the fullest rendering's. Deliberately narrow -- a family
    must never lump disjoint content, because only one member of each
    family gets scanned by the walk-bound passes (_representative_shards).
    """
    base = _VARIANT_DATE_TAIL.sub('', library_id)
    tokens = base.split('_')
    if len(tokens) > 1 and tokens[-1] in _VARIANT_FLAVOURS:
        tokens = tokens[:-1]
    return '_'.join(tokens)


def resolve_fts_dir(cfg: Any = None) -> str:
    """Resolve the directory that holds FTS shard databases.

    Order of preference:
      1. ``cfg.paths.fts_dir`` if set.
      2. A sibling directory named ``fts`` next to the first configured ZIM
         directory (``.../knowledge/zim`` -> ``.../knowledge/fts``).
      3. A ``knowledge_fts`` directory beside the ChromaDB store.

    The directory is not created here; builders create it, searchers tolerate
    its absence.
    """
    if cfg is None:
        from puffpai.config import get_config
        cfg = get_config()

    paths = cfg.paths
    configured = (getattr(paths, 'fts_dir', '') or '').strip()
    if configured:
        return _warn_if_network_path(
            os.path.abspath(os.path.expanduser(configured)))

    zim_dirs = [d for d in (paths.zim_dirs or []) if d]
    if zim_dirs:
        first = os.path.abspath(os.path.expanduser(zim_dirs[0]))
        return _warn_if_network_path(os.path.join(os.path.dirname(first), 'fts'))

    chroma = os.path.abspath(os.path.expanduser(paths.chromadb_path or '.'))
    return _warn_if_network_path(
        os.path.join(os.path.dirname(chroma), 'knowledge_fts'))


def _warn_if_network_path(fts_dir: str) -> str:
    """Warn loudly when the FTS directory lands on a UNC network share.

    SQLite's WAL mode relies on shared-memory locking that network
    filesystems do not implement correctly; a shard built over SMB can
    corrupt under concurrent access. This does not block the caller — a
    share may genuinely be the only writable disk — but the operator needs
    to know to set an explicit local ``paths.fts_dir``.
    """
    if fts_dir.startswith('\\\\') or fts_dir.startswith('//'):
        logger.warning(
            "FTS directory %s is a UNC network path. SQLite WAL is not "
            "supported on network filesystems and shards there may corrupt. "
            "Set paths.fts_dir to a local directory.", fts_dir)
    return fts_dir


# ── Shard schema ────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS docs (
    id    INTEGER PRIMARY KEY,
    path  TEXT UNIQUE,
    title TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
    title,
    body,
    tokenize='porter unicode61'
);
"""

# Snippet highlight markers. snippet() cannot escape the article text it
# wraps, and zim_reader's plain_text decodes &lt;/&gt; back into real angle
# brackets — so asking SQLite for literal <b> tags would let any pack inject
# markup (a literal <script>) straight into an API response the dashboard
# renders. Instead we mark the hit with control characters that cannot occur
# in article text, HTML-escape the whole snippet, and only then substitute the
# real tags (see _render_snippet).
_HL_OPEN = '\x02'
_HL_CLOSE = '\x03'

# A contentless/external-content table would save disk but forbids snippet()
# over stored text and needs the source rows kept in sync by trigger. Disk is
# not the constraint here (a large install has terabytes free), so the plain
# fts5 table stores the body and snippet() works directly. The invariant that
# makes the JOIN below correct: fts.rowid is always the docs.id of the same
# document, assigned by inserting into docs first.
#
# No snippet here, deliberately: snippet() re-tokenizes the whole stored body
# of every row it is asked about, and a machine-generated list page runs to
# half a megabyte. Computed per stage-1 candidate it cost ~11 of the fulltext
# sentence query's 13 measured seconds, mostly on rows that then lost the
# global merge. Winners get snippets afterwards -- see
# FTSSearcher._attach_snippets.
_SEARCH_SQL = """
SELECT d.id AS doc_id,
       d.path,
       d.title,
       bm25(fts) AS rank
FROM fts
JOIN docs d ON d.id = fts.rowid
WHERE fts MATCH ?
ORDER BY rank
LIMIT ?
"""

# Snippet for one known row: the rowid equality makes FTS5 seek its doclists
# instead of iterating them, so the cost is one document's tokenization.
_SNIPPET_SQL = f"""
SELECT snippet(fts, 1, '{_HL_OPEN}', '{_HL_CLOSE}', '...', 24)
FROM fts
WHERE fts MATCH ? AND rowid = ?
"""

# bm25 weights: title=1, body=0. The MATCH expression is already restricted to
# the title column, and zeroing the body keeps rank purely "how well is this
# article NAMED for the query" — an article that merely mentions the terms a
# thousand times must not outrank one actually titled with them.
_TITLE_SEARCH_SQL = """
SELECT d.path,
       d.title,
       bm25(fts, 1.0, 0.0) AS rank
FROM fts
JOIN docs d ON d.id = fts.rowid
WHERE fts MATCH ?
ORDER BY rank
LIMIT ?
"""


def _render_snippet(raw: str, title: str = '') -> str:
    """Escape a snippet for safe HTML rendering, keeping the <b> highlights.

    Article bodies come from third-party ZIM packs and are not trusted markup.
    Everything is escaped first, so only the sentinels this module inserted can
    ever become tags.

    Title-only records (PDFs) have an empty body, so snippet() over the body
    column yields nothing; those fall back to the escaped title rather than
    handing the UI a blank result row.
    """
    if not raw or not raw.strip():
        return html.escape(title or '')
    return (html.escape(raw)
            .replace(_HL_OPEN, '<b>')
            .replace(_HL_CLOSE, '</b>'))


def _connect(db_path: str, *, read_only: bool = False) -> sqlite3.Connection:
    """Open a shard database with the pragmas we want."""
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    if not read_only:
        # Another writer of the same shard (the deeper stage's checkpoint) is
        # waited for, never a "database is locked" stop.
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _get_meta(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    except sqlite3.DatabaseError:
        # No meta table yet, or the file on disk is not a database at all.
        return default
    return row['value'] if row is not None else default


def _set_meta(conn: sqlite3.Connection, **pairs: Any) -> None:
    conn.executemany(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        [(k, '' if v is None else str(v)) for k, v in pairs.items()],
    )


def shard_status(db_path: str) -> dict[str, Any]:
    """Read a shard's meta table without holding the file open.

    Returns a dict with at least ``exists``; when the shard is readable it also
    carries ``status``, ``schema_version``, ``article_count``, ``zim_filename``,
    ``zim_size``, ``zim_mtime``, ``last_entry_index`` and ``error``.
    Never raises.
    """
    info: dict[str, Any] = {'exists': os.path.isfile(db_path), 'path': db_path}
    if not info['exists']:
        return info
    try:
        conn = _connect(db_path, read_only=True)
    except sqlite3.Error as exc:
        info['status'] = 'error'
        info['error'] = str(exc)
        return info
    try:
        # last_entry_index MUST be in this list: FTSBuilder.build() reads the
        # resume checkpoint back through shard_status(), so omitting it makes
        # every restart look like a fresh build and silently drops the shard.
        for key in ('schema_version', 'status', 'zim_filename', 'zim_size',
                    'zim_mtime', 'article_count', 'last_entry_index', 'error'):
            info[key] = _get_meta(conn, key)
        try:
            info['article_count'] = int(info.get('article_count') or 0)
        except ValueError:
            info['article_count'] = 0
    except sqlite3.Error as exc:
        info['status'] = 'error'
        info['error'] = str(exc)
    finally:
        conn.close()
    return info


# ── Book labels (BuildBot) ──────────────────────────────────────────────────

#: Meta keys a builder may stamp on a shard from its caller's labels.
LABEL_KEYS = ('source_id', 'source_label', 'source_license', 'book_name')

_LABEL_JUNK = re.compile(r'[\x00-\x1f\x7f]+')


def clean_labels(labels: Any) -> dict[str, str]:
    """Only the known label keys, each one short line of text. Never raises."""
    if not isinstance(labels, dict):
        return {}
    out: dict[str, str] = {}
    for key in LABEL_KEYS:
        value = labels.get(key)
        if isinstance(value, str):
            text = _LABEL_JUNK.sub(' ', value).strip()[:200]
            if text:
                out[key] = text
    return out


def shard_labels(db_path: str) -> dict[str, str]:
    """The label keys stored in a shard's meta table ({} when unreadable)."""
    try:
        conn = _connect(db_path, read_only=True)
    except sqlite3.Error:
        return {}
    try:
        return {k: v for k in LABEL_KEYS if (v := _get_meta(conn, k))}
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


# ── Building ────────────────────────────────────────────────────────────────

@dataclass
class BuildSummary:
    """Outcome of building (or skipping) one shard."""
    library_id: str
    zim_path: str
    shard_path: str
    status: str = "complete"       # complete | error | skipped
    entries_scanned: int = 0
    articles_indexed: int = 0
    total_entries: int = 0
    resumed_from: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ('complete', 'skipped')


class FTSBuilder:
    """Build per-ZIM FTS5 shards.

    The build is resumable: every batch commits the indexed rows and the index
    of the last scanned ZIM entry in one transaction, so an interrupted run
    picks up where it stopped as long as the source ZIM is byte-identical.
    """

    def build(
        self,
        zim_path: str,
        fts_dir: str,
        *,
        batch_size: int = 500,
        progress_cb: Optional[Callable[[int, int, int], None]] = None,
        force: bool = False,
        labels: Optional[dict] = None,
    ) -> BuildSummary:
        """Build (or resume, or skip) the shard for one ZIM file.

        ``labels`` (BuildBot) are short facts about the book written into the
        shard's meta table -- ``source_id``, ``source_label``,
        ``source_license`` and ``book_name`` -- so an answer can name its book
        and a book can be removed by id. Only :data:`LABEL_KEYS` are kept, each
        reduced to one short line of plain text.
        """
        lib_id = library_id_for(zim_path)
        db_path = shard_path_for(zim_path, fts_dir)
        summary = BuildSummary(library_id=lib_id, zim_path=zim_path,
                               shard_path=db_path)

        try:
            zim_size = os.path.getsize(zim_path)
            zim_mtime = int(os.path.getmtime(zim_path))
        except OSError as exc:
            summary.status = 'error'
            summary.error = f"cannot stat ZIM: {exc}"
            logger.warning("[%s] %s", lib_id, summary.error)
            return summary

        os.makedirs(fts_dir, exist_ok=True)

        existing = shard_status(db_path)
        if (not force
                and existing.get('status') == 'complete'
                and existing.get('schema_version') == SCHEMA_VERSION
                and existing.get('zim_size') == str(zim_size)
                and existing.get('zim_mtime') == str(zim_mtime)):
            summary.status = 'skipped'
            summary.articles_indexed = existing.get('article_count', 0)
            logger.info("[%s] shard already complete (%d articles), skipping",
                        lib_id, summary.articles_indexed)
            return summary

        # A 'building' shard over the same bytes can be resumed; anything else
        # (forced rebuild, different ZIM, stale schema) starts clean.
        resume_from = -1
        if (not force
                and existing.get('status') == 'building'
                and existing.get('schema_version') == SCHEMA_VERSION
                and existing.get('zim_size') == str(zim_size)
                and existing.get('zim_mtime') == str(zim_mtime)):
            try:
                resume_from = int(existing.get('last_entry_index') or -1)
            except (TypeError, ValueError):
                resume_from = -1

        conn = _connect(db_path)
        try:
            if resume_from < 0:
                self._reset(conn)
            self._ensure_schema(conn)
            _set_meta(conn,
                      schema_version=SCHEMA_VERSION,
                      zim_filename=os.path.basename(zim_path),
                      zim_size=zim_size,
                      zim_mtime=zim_mtime,
                      status='building',
                      error='')
            if resume_from < 0:
                _set_meta(conn, last_entry_index=-1, article_count=0)
            clean = clean_labels(labels)
            if clean:
                _set_meta(conn, **clean)
            conn.commit()

            summary.resumed_from = max(resume_from + 1, 0)
            try:
                indexed_before = int(_get_meta(conn, 'article_count', '0') or 0)
            except ValueError:
                indexed_before = 0

            self._walk_zim(conn, zim_path, summary,
                           start_index=summary.resumed_from,
                           indexed_before=indexed_before,
                           batch_size=batch_size,
                           progress_cb=progress_cb)

            if summary.status == 'error':
                _set_meta(conn, status='error', error=summary.error)
                conn.commit()
                return summary

            _set_meta(conn, status='complete', error='',
                      article_count=summary.articles_indexed,
                      last_entry_index=summary.total_entries - 1)
            conn.commit()
            conn.execute("PRAGMA optimize")
            logger.info("[%s] complete: %d articles from %d entries",
                        lib_id, summary.articles_indexed, summary.total_entries)
        finally:
            conn.close()

        return summary

    # ── internals ──────────────────────────────────────────────────────────

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(_SCHEMA_SQL)

    @staticmethod
    def _reset(conn: sqlite3.Connection) -> None:
        """Drop and recreate the content tables (meta is rewritten by caller)."""
        conn.executescript(
            "DROP TABLE IF EXISTS fts;"
            "DROP TABLE IF EXISTS docs;"
            "DROP TABLE IF EXISTS meta;"
        )
        conn.commit()

    @staticmethod
    def _index_records(conn: sqlite3.Connection,
                       records: Iterable[tuple[str, str, str]]) -> int:
        """Index an iterable of ``(path, title, text)`` triples.

        This is the whole indexing core: the ZIM walker feeds it articles and
        the tests feed it a synthetic corpus, so both exercise identical code.
        Returns the number of documents actually inserted; a path already in
        the shard is skipped (makes resumed batches idempotent).
        """
        inserted = 0
        for path, title, text in records:
            body = (text or '')[:MAX_BODY_CHARS]
            cur = conn.execute(
                "INSERT OR IGNORE INTO docs (path, title) VALUES (?, ?)",
                (path, title or ''),
            )
            if cur.rowcount == 0:
                continue  # already indexed
            conn.execute(
                "INSERT INTO fts (rowid, title, body) VALUES (?, ?, ?)",
                (cur.lastrowid, title or '', body),
            )
            inserted += 1
        return inserted

    def _walk_zim(self, conn: sqlite3.Connection, zim_path: str,
                  summary: BuildSummary, *, start_index: int,
                  indexed_before: int, batch_size: int,
                  progress_cb: Optional[Callable[[int, int, int], None]]) -> None:
        """Scan the ZIM's directory entries and index every article found."""
        lib_id = summary.library_id
        summary.articles_indexed = indexed_before

        try:
            reader = ZIMReader(zim_path)
        except ZIMTruncatedError as exc:
            # A partially copied pack must never kill the build loop: record it
            # on the shard and let the caller move on to the next ZIM.
            summary.status = 'error'
            summary.error = f"truncated ZIM: {exc.missing_bytes:,} bytes missing"
            logger.warning("[%s] %s", lib_id, summary.error)
            return
        except Exception as exc:
            summary.status = 'error'
            summary.error = f"cannot open ZIM: {exc}"
            logger.warning("[%s] %s", lib_id, summary.error)
            return

        try:
            total = reader.entry_count
            summary.total_entries = total
            batch: list[tuple[str, str, str]] = []
            index = start_index - 1

            for index in range(start_index, total):
                try:
                    record = self._read_record(reader, index, lib_id)
                except ZIMTruncatedError as exc:
                    # A pack truncated *after* its header still opens cleanly
                    # and only fails once the scan reaches the missing bytes.
                    # Mirror the open-time handling: flag this shard and stop,
                    # but leave the surrounding build run to carry on. Marking
                    # it complete here would publish a silently partial index.
                    self._flush(conn, batch, summary, index - 1)
                    summary.status = 'error'
                    summary.error = (f"truncated ZIM at entry {index}: "
                                     f"{exc.missing_bytes:,} bytes missing")
                    logger.warning("[%s] %s", lib_id, summary.error)
                    return
                if record is not None:
                    batch.append(record)

                if (index + 1) % batch_size == 0:
                    self._flush(conn, batch, summary, index)
                    batch = []
                    if progress_cb is not None:
                        progress_cb(index + 1, total, summary.articles_indexed)

            self._flush(conn, batch, summary, max(index, start_index - 1))
            summary.entries_scanned = max(total - start_index, 0)
            if progress_cb is not None:
                progress_cb(total, total, summary.articles_indexed)
        finally:
            try:
                reader.close()
            except Exception:
                pass

    @staticmethod
    def _read_record(reader: ZIMReader, index: int,
                     lib_id: str) -> Optional[tuple[str, str, str]]:
        """Extract one indexable ``(path, title, text)`` triple, or None.

        Any per-article failure is logged and swallowed: one unreadable entry
        in a six-million-entry archive must not abort the build. The single
        exception is ZIMTruncatedError, which is not a bad article but a bad
        *file* — it propagates so the caller can fail the whole shard.
        """
        try:
            entry = reader.get_entry(index)
            if entry.is_redirect:
                return None
            if entry.namespace not in ARTICLE_NAMESPACES:
                return None

            # Drop any "; charset=utf-8" parameter before matching.
            mime = (entry.mime_type or '').split(';')[0].strip().lower()
            path = f"{entry.namespace}/{entry.url}"
            title = entry.title or entry.url

            if mime.startswith(TITLE_ONLY_MIMES):
                # No body, and no need to decompress the blob to find that out.
                reason = junk_directory_reason(entry.url)
                if reason:
                    logger.debug("[%s] skipping %s: %s", lib_id, path, reason)
                    return None
                return (path, title, '')

            if not mime.startswith(INDEXABLE_BODY_MIMES):
                return None

            reason = listing_page_reason(entry.url)
            if reason:
                logger.debug("[%s] skipping %s: %s", lib_id, path, reason)
                return None

            if mime.startswith(RAW_TEXT_MIMES):
                # text/plain is also the mime a pack's build tooling gets
                # served under, so screen by path before decompressing.
                reason = classify_text_path(entry.url)
                if reason:
                    logger.debug("[%s] skipping %s: %s", lib_id, path, reason)
                    return None

            article = reader.get_article(index, follow_redirects=False)
            if article is None:
                return None

            # plain_text strips markup and decodes entities; text is the raw
            # decoded blob. Only HTML wants the stripper (see RAW_TEXT_MIMES).
            body = (article.plain_text if mime.startswith(HTML_MIMES)
                    else article.text)
            if not body or not body.strip():
                return None
            return (path, title, body)
        except ZIMTruncatedError:
            raise
        except Exception as exc:
            logger.debug("[%s] entry %d skipped: %s", lib_id, index, exc)
            return None

    def _flush(self, conn: sqlite3.Connection,
               batch: list[tuple[str, str, str]],
               summary: BuildSummary, last_index: int) -> None:
        """Commit a batch of records and the resume checkpoint together."""
        if batch:
            summary.articles_indexed += self._index_records(conn, batch)
        _set_meta(conn,
                  last_entry_index=last_index,
                  article_count=summary.articles_indexed)
        conn.commit()


# ── Searching ───────────────────────────────────────────────────────────────

def sanitize_terms(query: str) -> list[str]:
    """Split arbitrary user input into safe, individually quoted FTS5 terms.

    Every FTS5 operator character is a potential syntax error coming from a
    search box, so nothing is trusted: the query is split on non-word
    characters and each surviving term is re-quoted. Quotes, parens,
    ``AND``/``OR``/``NEAR`` and unicode punctuation all become ordinary
    separators or ordinary quoted terms, never operators.

    Returns [] when the query has no indexable characters at all.
    """
    return [f'"{t}"' for t in _FTS_TERM_SPLIT.split(query or '') if t]


def sanitize_query(query: str) -> str:
    """Build a safe implicit-AND FTS5 MATCH expression from user input."""
    return ' '.join(sanitize_terms(query))


def _relevance_from_bm25(rank: float, best_rank: float) -> float:
    """Map a raw bm25 score into a 0-1 relevance against the GLOBAL best.

    SQLite's ``bm25()`` returns negative numbers where more negative is a
    better match. ``best_rank`` is the best raw score across every shard
    searched, so the single strongest document anywhere scores 1.0 and the
    rest decay toward 0.5.

    This used to normalize per shard, which was actively harmful: it made
    EVERY shard's best hit 1.0, so a document matching only "what" and "cover"
    tied the actual Kaito KA700 manual and the merge could not tell them
    apart. Raw bm25 can: it weights by inverse document frequency, so a hit on
    "kaito"/"ka700" scores far stronger than any stopword-only match.

    Known caveat, accepted for this build: raw scores are only roughly
    comparable across shards. A small shard has degenerate IDF -- a term
    present in most of a 2-document pack earns almost no weight -- so its
    scores sit on a slightly different scale from a Wikipedia shard's. That is
    imperfect, but strictly better than per-shard-max=1.0, which carried no
    cross-shard signal at all.

    Every result here is a literal term match, so the floor stays well above
    the 0.25 relevance cut the RAG pipeline applies to semantic hits.
    """
    return _relevance_from_score(-float(rank), -float(best_rank))


def _relevance_from_score(score: float, best: float) -> float:
    """Normalize a positive, higher-is-better score into the 0.5-1.0 band.

    Shared by both ranking paths so AND (raw bm25) and OR (summed global IDF)
    hand the RAG pipeline numbers on the same scale.
    """
    score = max(0.0, score)
    best = max(0.0, best)
    if best <= 0.0:
        return 0.5
    return max(0.0, min(1.0, 0.5 + 0.5 * (score / best)))


def _idf(doc_freq: int, total_docs: int) -> float:
    """Standard BM25+ inverse document frequency, floored."""
    doc_freq = max(0, doc_freq)
    total_docs = max(1, total_docs)
    return max(_MIN_IDF,
               math.log((total_docs - doc_freq + 0.5) / (doc_freq + 0.5) + 1.0))


class FTSSearcher:
    """Query every complete FTS shard in a directory."""

    def __init__(self, fts_dir: str):
        self.fts_dir = fts_dir
        self._shards: Optional[dict[str, str]] = None
        # Document count per shard, read from each shard's meta at discovery
        # time (no extra query) and summed for the global IDF denominator.
        self._doc_counts: dict[str, int] = {}
        # Measured document frequencies, keyed by (library_id, shard
        # identity) then stem. Shard identity is (zim_mtime, article_count)
        # from the shard's own meta, so the cache survives refresh() -- which
        # the fulltext endpoint calls on every request -- and only a rebuilt
        # shard invalidates its entries (pruned at discovery).
        self._shard_identity: dict[str, tuple] = {}
        self._df_cache: dict[tuple, dict[str, int]] = {}
        # Porter stems are a property of the tokenizer, not of any shard, so
        # they are cached for the life of the searcher.
        self._stem_conn: Optional[sqlite3.Connection] = None
        self._stem_cache: dict[str, tuple[str, ...]] = {}

    def refresh(self) -> None:
        """Forget the cached shard list; the next search rediscovers it."""
        self._shards = None
        self._doc_counts = {}

    @property
    def shards(self) -> dict[str, str]:
        """Mapping of ``library_id -> shard path`` for complete shards only."""
        if self._shards is None:
            self._shards = self._discover()
        return self._shards

    def ready_library_ids(self) -> list[str]:
        return sorted(self.shards)

    def is_ready(self) -> bool:
        return bool(self.shards)

    @property
    def total_docs(self) -> int:
        """Total indexed documents across every ready shard (global IDF's N)."""
        if self._shards is None:
            self._shards = self._discover()
        return sum(self._doc_counts.values())

    def _discover(self) -> dict[str, str]:
        found: dict[str, str] = {}
        self._doc_counts = {}
        self._shard_identity = {}
        if not os.path.isdir(self.fts_dir):
            self._df_cache = {}
            return found
        try:
            entries = sorted(os.scandir(self.fts_dir), key=lambda e: e.name)
        except OSError as exc:
            logger.warning("Cannot scan FTS directory %s: %s", self.fts_dir, exc)
            self._df_cache = {}
            return found
        for entry in entries:
            if not entry.is_file() or not entry.name.endswith('.db'):
                continue
            info = shard_status(entry.path)
            if (info.get('status') == 'complete'
                    and info.get('schema_version') == SCHEMA_VERSION):
                lib_id = entry.name[:-3]
                found[lib_id] = entry.path
                self._doc_counts[lib_id] = info.get('article_count', 0)
                self._shard_identity[lib_id] = (info.get('zim_mtime'),
                                                info.get('article_count'))
            else:
                logger.debug("Skipping shard %s (status=%s)",
                             entry.name, info.get('status'))
        # Frequencies measured for shard versions no longer present are dead.
        live = {(lib, ident) for lib, ident in self._shard_identity.items()}
        self._df_cache = {key: dfs for key, dfs in self._df_cache.items()
                          if key in live}
        return found

    def search(self, query: str, max_results: int = 20,
               libraries: Optional[Iterable[str]] = None) -> list[dict[str, Any]]:
        """Full-text search across shards, in up to two passes.

        Pass 1 requires every AFFORDABLE term (implicit AND) -- precise, and
        the right default for a deliberate keyword search, which carries no
        collection-flooded words and therefore keeps them all. A sentence's
        "a"/"to"/"my" are dropped before either pass runs (see
        _select_terms): requiring them made the AND intersection cost tens
        of seconds AND steered it toward machine-generated list pages that
        happen to contain every stopword. Each pass runs on its own
        affordable set -- AND is seek-bound and can require mid-frequency
        words the walk-bound OR must drop. If the AND pass finds nothing
        at all, or finds only documents not one of which carries a query
        term in its TITLE (the signature of that same list-page salad),
        pass 2 retries with the OR-affordable terms OR-ed together.

        The retry exists because natural language does not survive a strict
        AND. "According to the Kaito KA700 manual in my library, what bands
        does it cover?" demands one document containing "according", "library"
        and "bands" all together, which no manual does -- so the RAG door
        contributed nothing to chat while the terse "Kaito KA700 radio manual
        bands" worked fine. bm25 weights by inverse document frequency, so on
        the OR pass the rare terms (Kaito, KA700) still dominate the ranking
        and the stopwords are effectively noise.

        The retry is deliberately at the search() level, not per shard: a term
        set that matches in ANY shard keeps full AND precision everywhere, and
        only a globally empty result relaxes to OR.

        Args:
            query: Raw user query; sanitized before it reaches FTS5.
            max_results: Maximum results returned in total.
            libraries: Optional iterable of library ids to restrict the search.

        Returns:
            List of ``{library_id, path, title, snippet, relevance, match_mode}``
            dicts, interleaved round-robin across shards so one large library
            cannot crowd out the rest. ``match_mode`` is 'and' or 'or'.
        """
        terms = sanitize_terms(query)
        if not terms or max_results <= 0:
            return []

        # Only None means "every library". An explicitly empty selection means
        # the caller asked for no libraries, and must not silently widen to all.
        wanted = None if libraries is None else set(libraries)
        if wanted is not None and not wanted:
            return []

        # Cap and cost-select the terms once, per pass: a long question
        # must not become a 60-clause MATCH scanned across a
        # six-million-document shard, and a sentence's flooded words price
        # out the AND intersection just as surely as the OR disjunction --
        # but the passes afford different sets (see _select_terms and the
        # MATCH_* constants), so each runs on its own. Terms stay in the
        # order the user typed them, which is where the specific ones
        # usually are.
        words = [t.strip('"') for t in terms[:MAX_OR_TERMS]]
        and_words, or_words, doc_freq = self._select_terms(words, wanted)

        # A single-term OR identical to the AND that just came up empty (or
        # gated) would only repeat it; any OTHER OR set is a genuinely
        # different (usually wider) query and is worth the retry.
        can_retry = and_words != or_words or len(or_words) >= 2

        results = self._run_pass(' '.join(f'"{w}"' for w in and_words),
                                 wanted, max_results, 'and',
                                 words=and_words, doc_freq=doc_freq,
                                 title_gate=can_retry)
        if results or not can_retry:
            return results

        return self._run_pass(' OR '.join(f'"{w}"' for w in or_words),
                              wanted, max_results, 'or', words=or_words,
                              doc_freq=doc_freq)

    # ── Global IDF (stage 2) ───────────────────────────────────────────────

    def _stem_terms(self, word: str) -> tuple[str, ...]:
        """Return every indexed term a word produces under 'porter unicode61'.

        fts5vocab stores POST-tokenization terms, so a document frequency
        lookup for "bands" finds nothing while the index actually holds
        "band" — silently zeroing that term's IDF. Rather than reimplement
        Porter (and drift from whatever SQLite actually does), the word is
        pushed through a scratch in-memory fts5 table with the same tokenizer
        and read back out of its vocab table. That is correct by construction.

        Most words tokenize to exactly one term, but ``\\w`` keeps
        underscores that unicode61 then splits: "systemd_unit" indexes as
        ("systemd", "unit"). Returning the full tuple IN TOKEN ORDER (the
        instance vocab carries offsets) lets callers tell such compounds
        apart; collapsing to the first term made "systemd_unit" and
        "systemd_timer" look like the same word, and a sorted tuple would
        still collide token-anagrams like "unit_test"/"test_unit", which
        are different phrases. Falls back to ``(word.lower(),)`` when
        tokenization fails or eats the word entirely.
        """
        cached = self._stem_cache.get(word)
        if cached is not None:
            return cached

        terms: tuple[str, ...] = ()
        try:
            if self._stem_conn is None:
                conn = sqlite3.connect(':memory:')
                conn.execute("CREATE VIRTUAL TABLE stem_src USING "
                             "fts5(w, tokenize='porter unicode61')")
                # Two-argument fts5vocab: the three-argument (schema, table,
                # type) form is only legal for a table created in temp. The
                # 'instance' type (one row per token occurrence, with its
                # offset) is what preserves token order and multiplicity.
                conn.execute("CREATE VIRTUAL TABLE stem_vocab USING "
                             "fts5vocab(stem_src, instance)")
                self._stem_conn = conn
            self._stem_conn.execute("DELETE FROM stem_src")
            self._stem_conn.execute("INSERT INTO stem_src(w) VALUES (?)", (word,))
            terms = tuple(row[0] for row in self._stem_conn.execute(
                "SELECT term FROM stem_vocab ORDER BY offset") if row[0])
        except sqlite3.Error as exc:
            logger.debug("Stemming failed for %r, using raw form: %s", word, exc)
        if not terms:
            terms = (word.lower(),)

        self._stem_cache[word] = terms
        return terms

    def _stem(self, word: str) -> str:
        """The single indexed term for a word (a compound's leading token)."""
        return self._stem_terms(word)[0]

    def _global_doc_freqs(self, stems: Iterable[str],
                          wanted: Optional[set] = None) -> dict[str, int]:
        """Sum each stem's document frequency over the searched shards.

        Frequencies come from :meth:`_measure_shard_dfs` and are cached per
        (shard, identity), so a stem is measured at most once per shard
        version per process -- the fulltext endpoint refresh()es on every
        request, and the cache is what keeps that free. A shard that cannot
        be measured contributes nothing, which errs toward treating a term
        as rarer than it is: the safe direction, since rare terms are kept.
        """
        freqs: dict[str, int] = {stem: 0 for stem in stems}
        if not freqs:
            return freqs
        for lib_id, db_path in self.shards.items():
            if wanted is not None and lib_id not in wanted:
                continue
            cache = self._df_cache.setdefault(
                (lib_id, self._shard_identity.get(lib_id)), {})
            missing = [stem for stem in freqs if stem not in cache]
            if missing:
                self._measure_shard_dfs(lib_id, db_path, missing, cache)
            for stem in freqs:
                freqs[stem] += cache.get(stem, 0)
        return freqs

    def _dedup_doc_freqs(self, stems: Iterable[str],
                         wanted: Optional[set] = None,
                         ) -> tuple[dict[str, int], int]:
        """Sum stems' frequencies with near-duplicate shard variants collapsed.

        Shards are grouped into families (see :func:`_variant_family`); a
        family contributes the MAX of its members' frequencies rather than
        their sum, so the three-plus renderings of English Wikipedia stop
        quadruple-billing every English word to the scan budgets. Returns
        ``(freqs, total_docs)`` where ``total_docs`` is the collection size
        under the same collapse, for the AND ceiling's share computation.

        Reads the per-shard cache that :meth:`_global_doc_freqs` fills, so
        it must run after it (``_select_terms`` does); a shard missing from
        the cache contributes nothing, same as there.
        """
        freqs: dict[str, int] = {stem: 0 for stem in stems}
        family_dfs: dict[str, dict[str, int]] = {}
        family_docs: dict[str, int] = {}
        for lib_id in self.shards:
            if wanted is not None and lib_id not in wanted:
                continue
            family = _variant_family(lib_id)
            cache = self._df_cache.get(
                (lib_id, self._shard_identity.get(lib_id)), {})
            best = family_dfs.setdefault(family, {})
            for stem in freqs:
                df = cache.get(stem, 0)
                if df > best.get(stem, 0):
                    best[stem] = df
            docs = self._doc_counts.get(lib_id, 0)
            if docs > family_docs.get(family, 0):
                family_docs[family] = docs
        for best in family_dfs.values():
            for stem, df in best.items():
                freqs[stem] += df
        return freqs, sum(family_docs.values())

    def _representative_shards(self, wanted: Optional[set] = None) -> set:
        """One shard per variant family: the largest member of each.

        Both MATCH passes pay per shard they visit, and the flavour twins
        of one corpus (maxi/nopic/mini) hold the same articles -- scanning
        all of them costs multiples of the budget for candidates that
        arrive as duplicate titles and crowd the result list. Skipping the
        twins loses nothing: a family only ever contains renderings of one
        corpus+selection (see _variant_family), and the fullest rendering
        carries every document the others do. A library-filtered search
        picks the largest member WITHIN the filter, so an explicit request
        for a nopic pack still searches it.
        """
        best: dict[str, tuple[int, str]] = {}
        for lib_id in self.shards:
            if wanted is not None and lib_id not in wanted:
                continue
            key = (self._doc_counts.get(lib_id, 0), lib_id)
            family = _variant_family(lib_id)
            if family not in best or key > best[family]:
                best[family] = key
        return {lib_id for _, lib_id in best.values()}

    def _measure_shard_dfs(self, lib_id: str, db_path: str,
                           stems: list[str], cache: dict[str, int]) -> None:
        """Measure document frequencies for some stems on one shard.

        Two instruments, chosen by what they cost on THIS shard:

        * Estimation, for shards larger than DF_SAMPLE_ROWS: count the
          stem's hits in a mid-shard window of DF_SAMPLE_ROWS rowids and
          scale up. The rowid range makes FTS5 seek a bounded slice of the
          doclist, so a stopword costs the same microseconds as a rare
          word. Only stems that re-tokenize to themselves can be counted
          this way (MATCH re-runs the tokenizer, and Porter is not
          idempotent for every string).
        * The exact fts5vocab walk, for everything else: small shards
          (exact numbers for small collections, and for the tests), stems
          absent from the sample window (the walk is cheap precisely
          because the term is rare), and non-self-tokenizing stems.

        Reader-side only -- fts5vocab is a virtual table over the index the
        shard already has, so no shard format change and no rebuild. A
        failed measurement stays uncached and simply contributes nothing
        this time.
        """
        n_docs = self._doc_counts.get(lib_id, 0)
        try:
            conn = _connect(db_path, read_only=True)
        except sqlite3.Error as exc:
            logger.warning("Cannot open shard %s for term stats: %s",
                           lib_id, exc)
            return
        try:
            vocab_ready = False
            window_lo = max(0, (n_docs - DF_SAMPLE_ROWS) // 2)
            for stem in stems:
                df = None
                if (n_docs > DF_SAMPLE_ROWS
                        and self._stem_terms(stem) == (stem,)):
                    cnt = conn.execute(
                        "SELECT count(*) FROM fts WHERE fts MATCH ? "
                        "AND rowid > ? AND rowid <= ?",
                        (f'"{stem}"', window_lo,
                         window_lo + DF_SAMPLE_ROWS)).fetchone()[0]
                    if cnt:
                        df = cnt * n_docs // DF_SAMPLE_ROWS
                if df is None:
                    if not vocab_ready:
                        conn.execute(
                            "CREATE VIRTUAL TABLE IF NOT EXISTS temp.vocab "
                            "USING fts5vocab(main, fts, row)")
                        vocab_ready = True
                    row = conn.execute(
                        "SELECT doc FROM temp.vocab WHERE term = ?",
                        (stem,)).fetchone()
                    df = int(row[0]) if row is not None and row[0] else 0
                cache[stem] = df
        except sqlite3.Error as exc:
            logger.debug("Term stats unavailable on %s: %s", lib_id, exc)
        finally:
            conn.close()

    def _select_terms(self, words: list[str],
                      wanted: Optional[set] = None,
                      ) -> tuple[list[str], list[str], dict[str, int]]:
        """Choose which terms each MATCH pass can afford to scan.

        The two passes have different cost models, so each gets its own
        set (see the MATCH_* constants for the measurements):

        * The OR disjunction is walk-bound -- it costs the SUM of its
          terms' document frequencies. Terms under MATCH_DF_KEEP_FLOOR are
          always kept (rare terms are the recall the OR fallback exists
          for, and they cost nothing); costlier ones are admitted
          rarest-first while their summed variant-deduped frequency fits
          MATCH_DF_BUDGET. Dropping a budget-buster barely moves ranking:
          its IDF is near zero, so documents matching only it sit at the
          bottom of the re-rank regardless.
        * The AND intersection is seek-bound -- O(rarest-df x terms), so a
          term's own frequency is nearly free and the pass can REQUIRE
          mid-frequency words the OR budget cannot afford to walk. Its
          ceiling is per-term: anything under MATCH_AND_DF_CEILING_SHARE
          of the deduped collection (or under the keep-floor) qualifies;
          admission is rarest-first until rarest-df x terms crosses
          MATCH_AND_SEEK_BUDGET. For "how do i treat a deep cut on my
          hand" that requires how+treat+deep+cut+hand together, which
          finds wound-care pages, where requiring just the two
          OR-affordable terms surfaced Deep-titled film articles.

        When nothing qualifies for a pass, its rarest term is admitted
        regardless, so neither selection can come out empty. A keyword
        query has no flooded terms and keeps every term in BOTH sets, so
        deliberate searches keep strict-AND precision.

        Budgeting frequencies are variant-deduped (see _dedup_doc_freqs);
        the AND seek budget uses raw frequencies, because the intersection
        really does run in every variant shard.

        Words are first deduplicated by their FULL tokenization: "cover" and
        "covers" both index as ("cover",) and are one FTS5 clause, so
        keeping both would only double the scan -- but a compound like
        "systemd_unit" tokenizes to two terms and must not collide with
        "systemd_timer" just because they share a first token (that
        collision silently dropped a query term and its documents).

        A multi-token word is a phrase query, and FTS5 drives a phrase scan
        from its rarest constituent, so a compound's cost is the MINIMUM of
        its tokens' frequencies: "the_kaito" is a handful of seeks into
        "the"'s doclist, not a walk of it.

        Returns ``(and_words, or_words, doc_freq)``: each pass's kept words
        in their original order, and the measured raw frequencies keyed by
        indexed term, ready to hand to :meth:`_rerank_by_global_idf` so it
        need not repeat the lookups.
        """
        by_form: dict[tuple[str, ...], str] = {}
        for word in words:
            by_form.setdefault(self._stem_terms(word), word)
        if not by_form:
            return [], [], {}

        tokens = {tok for form in by_form for tok in form}
        doc_freq = self._global_doc_freqs(tokens, wanted)
        dedup_freq, dedup_docs = self._dedup_doc_freqs(tokens, wanted)

        cost = {form: min(dedup_freq[tok] for tok in form)
                for form in by_form}
        raw_cost = {form: min(doc_freq[tok] for tok in form)
                    for form in by_form}

        # OR: walk-bound, summed budget.
        kept_or = {form for form in by_form
                   if cost[form] <= MATCH_DF_KEEP_FLOOR}
        costly = sorted((cost[form], form) for form in by_form
                        if cost[form] > MATCH_DF_KEEP_FLOOR)
        budget = MATCH_DF_BUDGET
        for df, form in costly:
            if df <= budget or not kept_or:
                kept_or.add(form)
                budget -= df

        # AND: seek-bound, per-term ceiling plus a rarest-df x terms cap.
        ceiling = max(MATCH_DF_KEEP_FLOOR,
                      dedup_docs * MATCH_AND_DF_CEILING_SHARE)
        afford = sorted((cost[form], form) for form in by_form
                        if cost[form] <= ceiling)
        if not afford:
            afford = [min((cost[form], form) for form in by_form)]
        rarest_raw = min(raw_cost[form] for _, form in afford)
        max_terms = max(1, MATCH_AND_SEEK_BUDGET // max(rarest_raw, 1))
        kept_and = {form for _, form in afford[:max_terms]}

        return ([word for form, word in by_form.items() if form in kept_and],
                [word for form, word in by_form.items() if form in kept_or],
                doc_freq)

    def _rerank_by_global_idf(self, candidates: list[dict[str, Any]],
                              words: list[str],
                              max_results: int,
                              doc_freq: dict[str, int],
                              require_all: bool = False) -> list[dict[str, Any]]:
        """Re-rank pass candidates by the global IDF of the terms they hit.

        Stage 1 gives recall (each shard's own best guesses); this gives
        precision. Local bm25 cannot: the Kaito pack holds 2 documents and
        both contain "kaito" and "ka700", so within that shard those terms
        carry almost no information and its raw scores lose a global sort to
        incidental matches in 6-million-document shards. Judged against the
        WHOLE collection those same terms are extremely rare, which is exactly
        the signal that identifies the manual.

        A term found in a candidate's TITLE counts its IDF a second time.
        Among documents that contain the same query terms, being NAMED for
        one is what separates the wound-care article from the film plot
        that happens to mention every word: measured live, the raw-bm25
        sort of an AND intersection put "Invasive test" (a 5,400-document
        Wikipedia intersection member) above the outdoors-stackexchange
        page literally titled "How should you treat an open wound in the
        field?" -- cross-shard bm25 magnitudes reward big shards' verbose
        pages. With ``require_all`` (the AND pass) every candidate contains
        every term, so the body sum is constant, the membership probes are
        skipped, and title hits plus local bm25 decide the order.

        ``doc_freq`` is the per-stem collection-wide document frequency the
        pass already measured while choosing its terms (see
        :meth:`_select_terms` and :meth:`_global_doc_freqs`), so this
        method does no frequency lookups of its own. A stem missing from the
        mapping scores as frequency 0 -- maximally rare -- which degrades
        toward keeping a term's influence rather than discarding it.

        Cost is bounded: at most len(shards) x OR_CANDIDATES_PER_SHARD
        candidates and MAX_OR_TERMS terms, and each (term, candidate)
        membership test is a point probe -- ``MATCH ? AND rowid = ?`` seeks
        the doclist (the title probe is the same seek with a ``title :``
        column filter, and only runs for terms the document is known to
        contain). It must NOT be written as one ``rowid IN (...)`` query
        per term: FTS5 cannot take an IN over rowid as seeks until SQLite
        3.38's vtab-IN interface, and on older builds (some installs
        run 3.37) it decodes the term's ENTIRE doclist instead -- measured
        as the membership pass alone holding fulltext at 13s after
        everything else was bounded.

        Each returned hit carries ``_tscore`` (its title-IDF component) so
        :meth:`_run_pass` can tell a title-anchored AND result set from
        stopword salad; it never leaves the module.
        """
        stems = {word: self._stem(word) for word in words}
        total_docs = max(self.total_docs, 1)

        by_shard: dict[str, list[dict[str, Any]]] = {}
        for hit in candidates:
            by_shard.setdefault(hit['library_id'], []).append(hit)

        matched: dict[tuple, set] = {}
        titled: dict[tuple, set] = {}

        for lib_id, group in by_shard.items():
            db_path = self.shards.get(lib_id)
            if db_path is None:
                continue
            try:
                conn = _connect(db_path, read_only=True)
            except sqlite3.Error as exc:
                logger.warning("Cannot reopen shard %s for re-rank: %s",
                               lib_id, exc)
                continue
            try:
                rowids = [hit['_rowid'] for hit in group]
                for word, stem in stems.items():
                    for rowid in rowids:
                        try:
                            contains = require_all
                            if not contains:
                                row = conn.execute(
                                    "SELECT 1 FROM fts WHERE fts MATCH ? "
                                    "AND rowid = ?",
                                    (f'"{word}"', rowid)).fetchone()
                                contains = row is not None
                            if not contains:
                                continue
                            matched.setdefault((lib_id, rowid),
                                               set()).add(stem)
                            row = conn.execute(
                                "SELECT 1 FROM fts WHERE fts MATCH ? "
                                "AND rowid = ?",
                                (f'title : "{word}"', rowid)).fetchone()
                            if row is not None:
                                titled.setdefault((lib_id, rowid),
                                                  set()).add(stem)
                        except sqlite3.Error as exc:
                            logger.debug(
                                "term membership probe failed on %s: %s",
                                lib_id, exc)
                            break
            finally:
                conn.close()

        idf = {stem: _idf(doc_freq.get(stem, 0), total_docs)
               for stem in stems.values()}
        all_idf = sum(idf[stem] for stem in set(stems.values()))

        scored = []
        for hit in candidates:
            key = (hit['library_id'], hit['_rowid'])
            body = (all_idf if require_all else
                    sum(idf[stem] for stem in matched.get(key, ())))
            tscore = sum(idf[stem] for stem in titled.get(key, ()))
            hit['_tscore'] = tscore
            scored.append((body + tscore, hit))

        # Best IDF sum first; ties broken by the shard's own bm25 (negative is
        # better), which keeps ordering stable among equally-informative hits.
        # A title already taken by another shard of the same variant family
        # is the same article again (maxi vs nopic); it must not burn a
        # second result slot.
        scored.sort(key=lambda pair: (-pair[0], pair[1]['_rank']))

        best_score = None
        seen_titles: set = set()
        results = []
        for score, hit in scored:
            twin_key = (_variant_family(hit['library_id']), hit['title'])
            if twin_key in seen_titles:
                continue
            seen_titles.add(twin_key)
            if best_score is None:
                best_score = score
            hit.pop('_rank', None)
            # '_rowid' stays: _run_pass still needs it to fetch the winners'
            # snippets, and strips it before anything leaves the module.
            hit['relevance'] = round(_relevance_from_score(score, best_score), 4)
            results.append(hit)
            if len(results) >= max_results:
                break
        return results

    def _run_pass(self, match: str, wanted: Optional[set], max_results: int,
                  mode: str,
                  words: Optional[list[str]] = None,
                  doc_freq: Optional[dict[str, int]] = None,
                  title_gate: bool = False) -> list[dict[str, Any]]:
        """Fan a MATCH expression across the shards and merge globally.

        Every shard is still asked for a bounded number of rows, and the
        winners are then chosen ACROSS the whole collection rather than by
        taking turns. Round-robin interleaving used to decide this, and
        combined with per-shard normalization it made ranking meaningless:
        with ~40 shards built, "What bands does the Kaito KA700 radio
        cover?" returned the first five shards alphabetically -- biomedical
        science, tents, microwave safety -- every one at relevance 1.0,
        with the actual manual nowhere in the list.

        When ``words`` are supplied (the bare, unquoted query terms), both
        modes rank through :meth:`_rerank_by_global_idf`; a raw cross-shard
        bm25 sort is only the fallback for callers without them. Raw bm25
        is not comparable between shards, and measured live it ranked big
        shards' verbose pages above a small shard's page literally titled
        with the query's words. ``doc_freq`` is the per-stem document
        frequency measured during term selection; when a caller supplies
        words without it, the frequencies are measured here.

        ``title_gate`` (AND pass only): an AND result set in which not one
        candidate carries a query term in its title is stopword salad --
        the intersection of a sentence's mid-frequency words selects FOR
        rambling list pages. Returning [] lets search() fall through to
        the OR pass, whose subset-scoring handles exactly that shape; the
        OR pass cannot come up empty in that case, because any document
        matching the whole AND also matches the disjunction.
        """
        # Stage 1, recall: ask each shard for its own best guesses -- one
        # per variant family, since flavour twins would only re-find the
        # same articles (see _representative_shards). With a re-rank
        # coming, per-shard depth only needs to feed the candidate pool,
        # and it bounds the membership-probe cost.
        per_shard_limit = (min(max_results, OR_CANDIDATES_PER_SHARD)
                           if words else max_results)
        scan = self._representative_shards(wanted)

        collected: list[dict[str, Any]] = []
        for lib_id, db_path in self.shards.items():
            if lib_id not in scan:
                continue
            collected.extend(
                self._search_shard(lib_id, db_path, match,
                                   per_shard_limit, mode))

        if not collected:
            return []

        if words:
            # Stage 2, precision: rank candidates by how informative their
            # matched terms are -- body membership for the OR pass (its
            # candidates matched different subsets), title membership for
            # both (being NAMED for a term beats mentioning it).
            if doc_freq is None:
                doc_freq = self._global_doc_freqs(
                    {self._stem(w) for w in words}, wanted)
            top = self._rerank_by_global_idf(collected, words, max_results,
                                             doc_freq,
                                             require_all=(mode == 'and'))
            if (mode == 'and' and title_gate
                    and all(hit['_tscore'] <= 0 for hit in top)):
                return []
        else:
            collected.sort(key=lambda hit: hit['_rank'])
            top = collected[:max_results]

            best_rank = top[0]['_rank']
            for hit in top:
                hit['relevance'] = round(
                    _relevance_from_bm25(hit.pop('_rank'), best_rank), 4)

        self._attach_snippets(top, match)
        for hit in top:
            hit.pop('_rowid', None)
            hit.pop('_tscore', None)
        return top

    def _attach_snippets(self, hits: list[dict[str, Any]],
                         match: str) -> None:
        """Fetch snippets for the winning rows only.

        snippet() re-tokenizes the whole stored body of every row it is
        asked about, and a machine-generated list page runs to half a
        megabyte -- computing it for every stage-1 candidate held the
        fulltext sentence query at 13s after everything else was bounded,
        mostly on rows that lost the global merge anyway. Winners are at
        most max_results rows, each fetched by a rowid point probe against
        the same MATCH expression, so the highlights are identical to what
        stage 1 would have produced. A row whose snippet cannot be fetched
        falls back to its escaped title rather than being dropped.
        """
        by_shard: dict[str, list[dict[str, Any]]] = {}
        for hit in hits:
            by_shard.setdefault(hit['library_id'], []).append(hit)
        for lib_id, group in by_shard.items():
            db_path = self.shards.get(lib_id)
            conn = None
            if db_path is not None:
                try:
                    conn = _connect(db_path, read_only=True)
                except sqlite3.Error as exc:
                    logger.warning("Cannot reopen shard %s for snippets: %s",
                                   lib_id, exc)
            try:
                for hit in group:
                    snip = ''
                    if conn is not None:
                        try:
                            row = conn.execute(
                                _SNIPPET_SQL,
                                (match, hit['_rowid'])).fetchone()
                            if row is not None:
                                snip = row[0] or ''
                        except sqlite3.Error as exc:
                            logger.debug("snippet fetch failed on %s: %s",
                                         lib_id, exc)
                    hit['snippet'] = _render_snippet(snip, hit['title'])
            finally:
                if conn is not None:
                    conn.close()

    @staticmethod
    def _search_shard(lib_id: str, db_path: str, match: str,
                      limit: int, mode: str = 'and') -> list[dict[str, Any]]:
        try:
            conn = _connect(db_path, read_only=True)
        except sqlite3.Error as exc:
            logger.warning("Cannot open shard %s: %s", db_path, exc)
            return []
        try:
            rows = conn.execute(_SEARCH_SQL, (match, limit)).fetchall()
        except sqlite3.OperationalError as exc:
            # Should be unreachable after sanitize_query, but a malformed shard
            # or an exotic tokenizer error must degrade to "no results", never
            # to a 500.
            logger.warning("FTS query failed on %s: %s", lib_id, exc)
            return []
        except sqlite3.Error as exc:
            logger.warning("FTS query error on %s: %s", lib_id, exc)
            return []
        finally:
            conn.close()

        # Raw bm25 travels with each hit as '_rank' so _run_pass can rank the
        # whole collection together; it is converted to 'relevance' there and
        # never leaves this module. Snippets are attached later, winners only.
        return [{
            'library_id': lib_id,
            'path': row['path'],
            'title': row['title'],
            'match_mode': mode,
            '_rank': row['rank'],
            '_rowid': row['doc_id'],
        } for row in rows]

    # ── Title search ───────────────────────────────────────────────────────

    def search_titles(self, query: str, max_results: int = 50,
                      per_library: int = 5) -> list[dict[str, Any]]:
        """Search article *titles* across every ready shard.

        Runs the MATCH against the existing ``fts`` table with a ``title :``
        column filter, so it needs no extra index and no shard rebuild, and
        it covers every pack the fulltext search covers — including the
        multi-gigabyte ones the old per-ZIM directory scan had to skip.

        Like :meth:`search`, pass 1 requires every affordable term; only a
        globally empty result relaxes to OR, and each pass runs on its own
        cost-selected term set (see :meth:`_select_terms`). Terms are whole
        tokens, never prefixes: a low-selectivity prefix ("f*") degenerates
        to a scan of a multi-million-entry term index and takes tens of
        seconds on the largest shards, which is exactly the pathology this
        method replaces.

        Returns ``{library_id, path, title}`` dicts, best global bm25 first,
        with rank weighted entirely on the title column (see
        :data:`_TITLE_SEARCH_SQL`).
        """
        terms = sanitize_terms(query)
        if not terms or max_results <= 0:
            return []

        per_library = max(1, min(per_library, max_results))

        # Same cost selection as search(): a title: filter still walks each
        # term's full body-inclusive doclist, so flooded words cost the same
        # seconds here as in a body search.
        and_words, or_words, _ = self._select_terms(
            [t.strip('"') for t in terms[:MAX_OR_TERMS]])

        # One shard per variant family; flavour twins only repeat titles
        # (see _representative_shards).
        scan = self._representative_shards()
        anded = ' '.join(f'"{w}"' for w in and_words)
        hits = self._title_pass(f'title : ({anded})', per_library, libs=scan)
        if not hits and (and_words != or_words or len(or_words) >= 2):
            ored = ' OR '.join(f'"{w}"' for w in or_words)
            hits = self._title_pass(f'title : ({ored})', per_library,
                                    libs=scan)

        # A title from another flavour of the same corpus is the same
        # article again; it must not burn a second result slot.
        hits.sort(key=lambda hit: hit['_rank'])
        seen: set = set()
        top = []
        for hit in hits:
            twin_key = (_variant_family(hit['library_id']), hit['title'])
            if twin_key in seen:
                continue
            seen.add(twin_key)
            del hit['_rank']
            top.append(hit)
            if len(top) >= max_results:
                break
        return top

    def _title_pass(self, match: str, per_library: int,
                    libs: Optional[set] = None) -> list[dict[str, Any]]:
        """Fan one title-filtered MATCH across the shards."""
        collected: list[dict[str, Any]] = []
        for lib_id, db_path in self.shards.items():
            if libs is not None and lib_id not in libs:
                continue
            try:
                conn = _connect(db_path, read_only=True)
            except sqlite3.Error as exc:
                logger.warning("Cannot open shard %s: %s", db_path, exc)
                continue
            try:
                rows = conn.execute(_TITLE_SEARCH_SQL,
                                    (match, per_library)).fetchall()
            except sqlite3.Error as exc:
                # A malformed shard must degrade to "no results from this
                # library", never take down the whole search.
                logger.warning("Title query failed on %s: %s", lib_id, exc)
                continue
            finally:
                conn.close()
            collected.extend({
                'library_id': lib_id,
                'path': row['path'],
                'title': row['title'],
                '_rank': row['rank'],
            } for row in rows)
        return collected
