"""The word look-up for the owner's uploaded documents.

Every upload gets this quick stage, always: its passages go into one SQLite
file beside the upload list (``<data>/data/uploads_quick.db``) the moment it
is uploaded. So an upload answers questions that use its own words even when
the reading helper is not on this computer; the meaning look-up in the store
is added on top when the helper is there. An upload is never refused for want
of the helper.

It holds exactly the text the upload list already keeps (the PII screen has
run before either sees it), and it is kept apart from the books' look-ups so
the routines' daily passage, which reads only books, can never read out an
owner's own document.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from typing import Any, Optional

from .fts_index import _relevance_from_bm25, sanitize_terms

FILENAME = "uploads_quick.db"
_lock = threading.Lock()

_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS parts USING fts5(
    file_id UNINDEXED, filename UNINDEXED, chunk_index UNINDEXED, body,
    tokenize='porter unicode61'
);
"""

#: Words too common to find anything with (the books' look-up uses them too).
STOP_WORDS = _STOP = frozenset("""a an and are as at be been but by can could did do does for
from had has have how i in is it its me my of on or our should so than that the
their them then there these they this those to up us was we were what when where
which who why will with would you your about tell show give find please""".split())


def db_path() -> str:
    from puffpai.ingestion import _data_root
    return os.path.join(str(_data_root()), FILENAME)


def _connect(path: Optional[str] = None) -> sqlite3.Connection:
    path = path or db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def add(file_id: str, filename: str, chunks: list[str], *,
        path: Optional[str] = None) -> int:
    """Replace one upload's passages. Returns how many were written. Raises on failure."""
    with _lock:
        conn = _connect(path)
        try:
            conn.execute("DELETE FROM parts WHERE file_id = ?", (file_id,))
            conn.executemany(
                "INSERT INTO parts (file_id, filename, chunk_index, body) "
                "VALUES (?, ?, ?, ?)",
                [(file_id, filename, i, text) for i, text in enumerate(chunks)])
            conn.commit()
            got = conn.execute("SELECT count(*) FROM parts WHERE file_id = ?",
                               (file_id,)).fetchone()[0]
        finally:
            conn.close()
    if got != len(chunks):
        raise RuntimeError(f"stored {got} of {len(chunks)} passages")
    return int(got)


def remove(file_id: str, *, path: Optional[str] = None) -> int:
    """Delete one upload's passages. Returns how many went. Never raises."""
    path = path or db_path()
    if not os.path.isfile(path):
        return 0
    try:
        with _lock:
            conn = _connect(path)
            try:
                cur = conn.execute("DELETE FROM parts WHERE file_id = ?", (file_id,))
                conn.commit()
                return int(cur.rowcount or 0)
            finally:
                conn.close()
    except sqlite3.Error:
        return 0


def search(query: str, n: int = 5, *, path: Optional[str] = None) -> list[dict[str, Any]]:
    """Best passages for *query*: ``{file_id, filename, chunk_index, snippet, relevance}``.

    ``relevance`` is 0.35..0.85, the scale the books' word look-up uses.
    """
    path = path or db_path()
    if not os.path.isfile(path):
        return []
    words = [t for t in sanitize_terms(query) if t.strip('"').lower() not in _STOP]
    if not words:
        return []
    match = " OR ".join(words[:24])
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        try:
            rows = conn.execute(
                "SELECT file_id, filename, chunk_index, body, bm25(parts) AS rank "
                "FROM parts WHERE parts MATCH ? ORDER BY rank LIMIT ?",
                (match, max(1, min(int(n), 20)))).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    if not rows:
        return []
    best = rows[0][4]
    out = []
    for file_id, filename, chunk_index, body, rank in rows:
        rel = _relevance_from_bm25(rank, best)  # 0.5..1
        out.append({"file_id": file_id, "filename": filename,
                    "chunk_index": int(chunk_index or 0), "snippet": body,
                    "relevance": max(0.35, min(0.85, 0.35 + (rel - 0.5)))})
    return out


def count(file_id: str, *, path: Optional[str] = None) -> int:
    path = path or db_path()
    if not os.path.isfile(path):
        return 0
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return int(conn.execute("SELECT count(*) FROM parts WHERE file_id = ?",
                                (file_id,)).fetchone()[0])
    finally:
        conn.close()


__all__ = ["FILENAME", "STOP_WORDS", "add", "count", "db_path", "remove", "search"]
