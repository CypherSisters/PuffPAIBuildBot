"""Searchers behind the "where to look" registry, and the helpers they share.

Each module here is named by a registry row's ``adapter``
(``puffpai.catalogs.searchers.<module>:<function>``). Snapshot adapters read a
file shipped with the build and send nothing. Live adapters take only
``outbound_query.Sendable`` phrases and put them into addresses only through
``outbound_query.url_quote``.

Shared here:

* :func:`new_found` - a ``found/v1`` record with every pinned key present;
* :class:`WordIndex` and :func:`job_terms` - the word match used on snapshots
  (a find needs a name or tag hit, or two description hits; one common word in
  a description is not a match);
* :func:`fetch` - the one way a searcher asks a site: through
  ``source_fetch.fetch_small`` (or the older ``get_small`` when that is not in
  this build), against the search budget, with a 24-hour cache under
  ``<state>/cache/where_to_look/`` and ``If-None-Match`` on repeats.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import html as html_lib
import importlib
import json
import os
import re
import threading
import time
import unicodedata
from typing import Any, Callable, Iterable, Optional

CACHE_TTL_S = 24 * 3600
FOUND_SCHEMA = "found/v1"
WHAT_IT_IS_MAX = 120
#: The name shown for a find whose own name has nothing readable in it.
NO_NAME = "No readable name"

_REPO_KEYS = ("host", "owner", "name", "default_branch", "pinned_commit", "pinned_at", "stars",
              "pushed_at", "archived", "fork", "license_reported", "license_read",
              "license_check", "license_check_why", "size_kb",
              "language", "topics", "latest_release", "in_awesome_selfhosted", "renamed_from")
_DETAIL_KEYS = ("readme_text", "feature_lines", "entry_html", "keeps_data", "inputs",
                "has_build_script", "code_bytes", "runtime_hosts_guess", "root_markers",
                "package_json", "page_hits")

#: Words that name what a job does, not what it is about (goal-agnostic).
JOB_VERBS = frozenset("""
plan plans planning track tracks tracking find finds finding keep keeps keeping make makes
making manage manages managing add adds list lists show shows help helps get gets use uses
create creates build builds organize organise organizes organises record records log logs
remember remembers check checks set sets store stores save saves compare compares suggest
suggests idea ideas come whole need needs want wants thing things stuff
""".split())


def _optional(modname: str):
    try:
        return importlib.import_module(modname)
    except ModuleNotFoundError as e:
        if e.name is not None and (e.name == modname or modname.startswith(e.name + ".")):
            return None          # not in this build yet
        raise                    # exists but broken: fail loudly


# ---------------------------------------------------------------------------
# found/v1
# ---------------------------------------------------------------------------

#: Signs that UTF-8 text was read as Windows-1252 somewhere upstream ("â€”" for an em dash).
_MOJIBAKE_RE = re.compile("â€|[ÂÃ][\u0080-¿]")
#: Dashes a person would read as an em dash (owner words never have one).
_DASH_CHARS = "‒–—―⸺⸻﹘﹣－"
_DASH_RE = re.compile(r"\s*[" + _DASH_CHARS + r"]+\s*")
_SPACE_RE = re.compile(r"\s+")
#: What a cut may not end on: a word that needs the next one.
_DANGLING = frozenset("a an the and or of for to with in on at by from as is are your our "
                      "its their this that".split())


#: A run of characters that Windows-1252 can write (the upper half, and the
#: few it moved elsewhere): only such a run can be UTF-8 read the wrong way.
_CP1252_RUN_RE = re.compile("[\u0080-ÿŒœŠšŸŽžƒ"
                            "ˆ˜–—‘-„†-•…‰"
                            "‹›€™]+")


def _cp1252_byte(ch: str) -> Optional[int]:
    try:
        return ch.encode("cp1252")[0]
    except UnicodeEncodeError:
        return ord(ch) if 0x80 <= ord(ch) <= 0x9f else None   # the five bytes cp1252 leaves out


#: The letters a two-byte UTF-8 sequence read the wrong way usually stands for
#: when its first character is a capital letter from "Ä" to "ß": the rest of
#: the Latin letters, Greek and Cyrillic. Anything else there (phonetic signs,
#: Hebrew, Syriac) is far more likely a real capital followed by a mark.
_TWO_BYTE_LETTERS = ((0x0100, 0x024F), (0x0370, 0x052F))


def _plausible(lead: int, ch: str, after_capital: bool) -> bool:
    """Is *ch*, read again from a sequence starting with byte *lead*, what the
    text really said? "Ã©" is always "é". A sequence that starts with a real
    capital ("Ä", "É", "Ü") is read again only when it gives a letter of the
    kinds above and the letter before it is not a real capital: "«CAFÉ»" and
    "ÄÖÜ»" are real capitals and a closing mark, never "CAFɻ"."""
    if lead in (0xC2, 0xC3) or lead >= 0xE0:
        return True
    cp = ord(ch)
    return not after_capital and any(lo <= cp <= hi for lo, hi in _TWO_BYTE_LETTERS)


def _repair_run(run: str, before: str = "") -> str:
    """One run, with every UTF-8 sequence in it that was read the wrong way
    read again, and every other character kept as it is ("CafÃ©" -> "Café";
    a real "é" stays). *before* is the character just before the run."""
    out: list[str] = []
    after_capital = before.isupper()
    i = 0
    while i < len(run):
        lead = _cp1252_byte(run[i])
        size = (2 if lead is not None and 0xc2 <= lead <= 0xdf else
                3 if lead is not None and 0xe0 <= lead <= 0xef else
                4 if lead is not None and 0xf0 <= lead <= 0xf4 else 0)
        if size and i + size <= len(run):
            raw = [_cp1252_byte(c) for c in run[i:i + size]]
            if all(b is not None for b in raw):
                try:
                    ch = bytes(raw).decode("utf-8")
                except UnicodeDecodeError:
                    ch = ""
                if ch and _plausible(lead, ch, after_capital):
                    out.append(ch)
                    after_capital = False
                    i += size
                    continue
        out.append(run[i])
        after_capital = run[i].isupper()
        i += 1
    return "".join(out)


def _repair_mojibake(text: str) -> str:
    """Text that was UTF-8 read as Windows-1252 somewhere upstream, read again.

    Run by run (one real accented letter anywhere, "CafÃ© été", makes an
    all-or-nothing repair give up, and then the "©" of "Ã©" is dropped as a
    symbol, leaving "CafÃ été").
    """
    if not _MOJIBAKE_RE.search(text):
        return text
    return _CP1252_RUN_RE.sub(
        lambda m: _repair_run(m.group(0), m.string[m.start() - 1:m.start()]), text)


def _pictograph(ch: str) -> bool:
    """Emoji and other picture characters (and what builds them), never the
    ordinary signs a description uses ("°", "©", "®", "™", "№", arrows)."""
    cp = ord(ch)
    return (0x1f000 <= cp <= 0x1faff          # emoji, flags, playing cards, symbols and pictographs
            or 0x2600 <= cp <= 0x27bf         # miscellaneous symbols, dingbats
            or 0x2b00 <= cp <= 0x2bff         # arrows and stars drawn as emoji
            or 0x1f3fb <= cp <= 0x1f3ff       # skin tones
            or cp in (0xfe0e, 0xfe0f, 0x20e3)  # picture/text selectors, keycap
            or 0xe0020 <= cp <= 0xe007f)      # tag letters (flag sequences)


def _drop_char(ch: str) -> bool:
    """Characters that never reach the owner: broken bytes, invisible marks, pictographs."""
    if ch == "�":
        return True
    cat = unicodedata.category(ch)
    # Cs: lone surrogates (broken text); Cf: zero-width and direction marks, which
    # can make text read differently from what it says; Co/Cn: private or unassigned.
    return cat in ("Cs", "Cf", "Co", "Cn") or _pictograph(ch)


def whole_words(value: Any, cut_at: int) -> str:
    """Text that its source stores cut at *cut_at* characters, ending on a whole word.

    A text of *cut_at* characters or more was (most likely) cut there, maybe
    mid-word ("Italian, Spanish or French, for", "Wesleyan Me"): its last
    word is dropped, then any word that needs the next one ("for", "the").
    A text that ends a sentence is kept. Shorter text is kept as it is.
    """
    raw = str(value or "")
    text = raw.strip()
    if len(raw) < int(cut_at) or text.endswith((".", "!", "?", ")")):
        return text
    # Cut right after a space: the last word is whole; otherwise it may not be.
    words = text.split() if raw[-1:].isspace() else text.split()[:-1]
    while len(words) > 1 and words[-1].lower().strip(",;:") in _DANGLING:
        words.pop()
    return " ".join(words).rstrip(" ,;:-(/|&+")


def _close_brackets(text: str) -> str:
    """*text* with no bracket left open: an open "(" or "[" with no close is
    cut off with what follows it, or closed when too little would be left."""
    for opening, closing in (("(", ")"), ("[", "]")):
        depth, last_open = 0, -1
        for i, ch in enumerate(text):
            if ch == opening:
                if depth == 0:
                    last_open = i
                depth += 1
            elif ch == closing and depth:
                depth -= 1
        if depth:
            before = text[:last_open].rstrip(" ,;:-/|&+")
            if len(before.split()) >= 3:
                text = before
            else:
                text = text.rstrip(" ,;:-/|&+.") + closing * depth
    return text


def plain_text(value: Any, limit: int = WHAT_IT_IS_MAX, *, full_stop: bool = True) -> str:
    """Outside text (a description, a README line) as one plain sentence for the owner.

    Decodes entities, repairs text that was read in the wrong encoding, drops
    broken bytes (U+FFFD), invisible direction marks, control characters and
    pictographs, turns dashes into commas, never cuts a word in half, never
    leaves a bracket open or a doubled full stop, and ends with a full stop
    (with *full_stop* False, for a name, none is added and none is taken
    away). At most *limit* characters, full stop included. Empty in, empty
    out. Nothing here makes the text trusted: callers still neutralise it.
    """
    text = str(value or "")[: max(0, int(limit)) * 8 + 64]
    if not text.strip():
        return ""
    text = html_lib.unescape(_repair_mojibake(text))
    text = unicodedata.normalize("NFKC", text)
    text = "".join(" " if unicodedata.category(c) == "Cc" else c for c in text
                   if not _drop_char(c))
    # "2019–2020" keeps a hyphen; a dash between words becomes a comma.
    text = re.sub(r"(?<=\d)\s*[" + _DASH_CHARS + r"]\s*(?=\d)", "-", text)
    text = _DASH_RE.sub(", ", text)
    text = _SPACE_RE.sub(" ", text).strip(" ,;:-|/")
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r",(?:\s*,)+", ",", text)
    # "W. M.." (an initial, then a full stop added upstream) is one full stop;
    # "..." (three or more) is left as it is.
    text = re.sub(r"(?<!\.)\.\.(?!\.)", ".", text)
    if not text:
        return ""
    room = max(1, int(limit) - (1 if full_stop else 0))   # a place kept for the full stop
    cut_short = len(text) > room
    if cut_short:
        cut = text[:room + 1]
        space = cut.rfind(" ")
        if space <= 0:
            return ""                             # one word longer than the room: nothing honest to show
        text = cut[:space]
    closed = _close_brackets(text)
    shortened = cut_short or len(closed) < len(text)
    text = closed.rstrip(" ,;:-(/|&+")
    if shortened:
        # A cut text never ends on a word that needs the next one ("for", "the").
        words = text.split(" ")
        while len(words) > 1 and words[-1].lower().strip(",;:") in _DANGLING:
            words.pop()
            words[-1] = words[-1].rstrip(",;:-/|&+")
        text = " ".join(words)
    if full_stop and text and text[-1] not in ".!?":
        text = text.rstrip(".") + "."
    return text[:max(1, int(limit))]


def new_found(*, source: str, part: str, id: str, name: str, friendly: str = "",
              what_it_is: str = "", url: str = "", repo: Optional[dict] = None,
              package: Optional[dict] = None, picture: Optional[dict] = None,
              details: Optional[dict] = None, mode_guess: str = "unknown",
              integrable: bool = False, trust: Optional[dict] = None) -> dict[str, Any]:
    """A ``found/v1`` record with exactly the pinned keys (``meaning`` set later)."""
    from puffpai import artifact_review

    def _clean(text: Any, limit: int) -> str:
        return artifact_review.neutralise_label(str(text or ""), limit=limit)

    def _describe(text: Any) -> str:
        # Clean first (so a dash or broken byte never survives), neutralise the
        # whole cleaned text (addresses, orders), then cut on a word.
        pre = plain_text(text, limit=WHAT_IT_IS_MAX * 8)
        return plain_text(artifact_review.neutralise_label(pre, limit=WHAT_IT_IS_MAX * 8),
                          limit=WHAT_IT_IS_MAX)

    def _label(text: Any, limit: int) -> str:
        # A name is cleaned like a description (broken bytes, dashes, marks: the
        # "Wedyesday �" name came from a stored snapshot), with no full stop
        # added, neutralised whole and only then cut on a word (not at 100
        # characters, mid-word or on "for").
        pre = plain_text(text, 400, full_stop=False)
        return plain_text(_clean(pre, 400), limit, full_stop=False)

    # A name with nothing readable left (only broken bytes or pictographs) is
    # never shown raw: the last part of the find's id stands in for it, and
    # failing that a plain placeholder (a raw fallback would keep U+FFFD and
    # emoji).
    tail = re.sub(r"[^A-Za-z0-9]+", " ", str(id or "").rsplit(":", 1)[-1].rsplit("/", 1)[-1])
    shown = _label(name, 100) or _label(tail, 100) or NO_NAME
    rec: dict[str, Any] = {
        "schema": FOUND_SCHEMA, "id": str(id)[:200], "source": source, "part": part,
        "name": shown,
        "friendly": _label(friendly or name, 80) or _label(tail, 80) or NO_NAME,
        "what_it_is": _describe(what_it_is), "url": str(url or "")[:300],
        "repo": None, "package": None, "picture": None, "details": None,
        "mode_guess": mode_guess, "integrable": bool(integrable),
        "trust": trust or {"floor_ok": False, "path": "", "pretick_ok": False, "reasons": []},
        "meaning": None,
    }
    if repo is not None:
        rec["repo"] = {k: repo.get(k) for k in _REPO_KEYS}
        rec["repo"]["topics"] = list(repo.get("topics") or [])
    if package is not None:
        rec["package"] = {k: package.get(k) for k in
                          ("registry", "name", "version", "downloads_monthly", "bin",
                           "repo_name_matches")}
    if picture is not None:
        rec["picture"] = {k: picture.get(k) for k in ("license", "credit", "thumb_local",
                                                      "full_host")}
    if details is not None:
        rec["details"] = details
    return rec


def empty_details() -> dict[str, Any]:
    return {"readme_text": "", "feature_lines": [], "entry_html": None, "keeps_data": False,
            "inputs": 0, "has_build_script": False, "code_bytes": 0, "runtime_hosts_guess": [],
            "root_markers": [], "package_json": None, "page_hits": []}


# ---------------------------------------------------------------------------
# word matching on snapshots (nothing leaves the computer)
# ---------------------------------------------------------------------------

def job_terms(job: dict[str, Any], kind: str) -> list[str]:
    """Search words for *job* on this computer: its name plus its search phrases for *kind*."""
    from puffpai.catalogs import goal_words
    texts = [str(job.get("name") or "")]
    for part in job.get("parts") or []:
        if isinstance(part, dict) and part.get("kind") == kind:
            for s in part.get("search") or []:
                phrase = s.get("phrase") if isinstance(s, dict) else getattr(s, "phrase", s)
                if isinstance(phrase, str):
                    texts.append(phrase)
    for extra in job.get("words") or []:
        if isinstance(extra, str):
            texts.append(extra)
    words = goal_words.goal_terms(" ".join(texts)[:600], limit=12)
    return [w for w in words if w not in JOB_VERBS]


_WEIGHTS = {"name": 3, "tag": 2, "text": 1}


class WordIndex:
    """Vocabulary -> row positions for one snapshot; built once per process."""

    def __init__(self, rows: list[dict[str, Any]], fields: Callable[[dict], dict[str, str]]):
        from puffpai.catalogs import goal_words
        self.rows = rows
        self.words: list[dict[str, set[str]]] = []
        self.vocab: dict[str, set[int]] = {}
        for pos, row in enumerate(rows):
            f = fields(row)
            per = {k: set(goal_words.raw_words(str(f.get(k) or ""))[:400]) for k in _WEIGHTS}
            self.words.append(per)
            for ws in per.values():
                for w in ws:
                    self.vocab.setdefault(w, set()).add(pos)

    def match(self, terms: Iterable[str], limit: int) -> list[tuple[int, int, list[str]]]:
        """``[(score, position, matched terms)]`` best first; see the module rule."""
        from puffpai.catalogs import goal_words
        terms = [t for t in dict.fromkeys(terms) if t]
        if not terms:
            return []
        hits: dict[int, dict[str, int]] = {}
        for term in terms:
            for word, where in self.vocab.items():
                if not goal_words.term_hits_word(term, word):
                    continue
                for pos in where:
                    per = self.words[pos]
                    best = max((_WEIGHTS[f] for f, ws in per.items() if word in ws), default=0)
                    got = hits.setdefault(pos, {})
                    if best > got.get(term, 0):
                        got[term] = best
        out = []
        for pos, got in hits.items():
            score = sum(got.values())
            if score >= 2 and (max(got.values()) >= 2 or len(got) >= 2):
                out.append((score, pos, sorted(got)))
        out.sort(key=lambda t: (-t[0], t[1]))
        return out[:max(0, int(limit))]


# ---------------------------------------------------------------------------
# meaning matches on snapshots (shipped vectors; nothing leaves the computer)
# ---------------------------------------------------------------------------

#: Every shipped snapshot stem and the function giving its rows as
#: ``[(find id, {"name", "description", "tags"})]``, the same ids its adapter
#: gives its finds. ``tools/build_snapshot_vectors.py`` embeds exactly these.
SNAPSHOT_ROWS: dict[str, str] = {
    "api_seeds": "seeds:rows_api_seeds",
    "public_apis_2026-09-23": "seeds:rows_public_apis",
    "apis_guru_2026-09-23": "seeds:rows_apis_guru",
    "ha_integrations_2026-09-23": "seeds:rows_ha_integrations",
    "kiwix_opds_eng_2026-09-23": "knowledge:rows_kiwix",
    "gutenberg_catalog_en_2026-09-24": "knowledge:rows_gutenberg",
    "openstax_books_2026-09-24": "knowledge:rows_openstax",
    "looks_tweakcn_2026-09-24": "looks:rows_tweakcn",
    "looks_shadcn_2026-09-24": "looks:rows_shadcn",
    "mcp_registry_2026-09-23": "mcp:rows",
    "awesome_selfhosted_2026-09-27": "awesome_selfhosted:rows",
    "hacs_2026-09-27": "hacs:rows",
}
#: A snapshot row is a meaning match for a job at this cosine or more
#: (all-MiniLM-L6-v2; the meaning check scores every find again).
#: Measured: nonsense job text reached
#: 0.43 at most over four snapshots; rows that fit their job scored 0.47-0.69.
VECTOR_MIN_SIM = 0.46


def snapshot_rows(stem: str) -> list[tuple[str, dict[str, Any]]]:
    """``[(id, candidate row)]`` for a shipped snapshot, deduplicated by id (first wins)."""
    spec = SNAPSHOT_ROWS.get(stem)
    if not spec:
        return []
    modname, fn_name = spec.split(":")
    mod = importlib.import_module(f"puffpai.catalogs.searchers.{modname}")
    out, seen = [], set()
    for rid, row in getattr(mod, fn_name)():
        rid = str(rid)
        if rid and rid not in seen:
            seen.add(rid)
            out.append((rid, row))
    return out


def job_text(job: dict[str, Any], kind: str) -> str:
    """What a job is about, in words, for a meaning match on this computer only."""
    parts = [str(job.get("name") or ""), str(job.get("does") or "")]
    for part in job.get("parts") or []:
        if isinstance(part, dict) and part.get("kind") == kind:
            for s in part.get("search") or []:
                phrase = s.get("phrase") if isinstance(s, dict) else getattr(s, "phrase", s)
                if isinstance(phrase, str):
                    parts.append(phrase)
    return ". ".join(p for p in parts if p.strip())[:600]


_JOB_VEC_LOCK = threading.Lock()
_JOB_VECS: dict[str, Any] = {}


def _embed_one(text: str):
    """The unit vector for *text* from the local reading helper, or None when it is absent."""
    with _JOB_VEC_LOCK:
        if text in _JOB_VECS:
            return _JOB_VECS[text]
    try:
        from puffpai.knowledge import reading_helper
        embedder = reading_helper.get_embedder()
    except Exception:  # noqa: BLE001 - no helper: word matches only
        embedder = None
    if embedder is None:
        return None
    import numpy as np
    try:
        vec = np.asarray(embedder([text])[0], dtype=np.float32)
    except Exception:  # noqa: BLE001
        return None
    norm = float(np.linalg.norm(vec))
    vec = vec / norm if norm else vec
    with _JOB_VEC_LOCK:
        if len(_JOB_VECS) > 512:
            _JOB_VECS.clear()
        _JOB_VECS[text] = vec
    return vec


def vector_match(stem: str, text: str, *, limit: int,
                 min_sim: float = VECTOR_MIN_SIM) -> list[tuple[float, str]]:
    """``[(cosine, id)]`` best first: snapshot rows whose shipped vector means what *text* means."""
    if not text.strip() or limit <= 0:
        return []
    from puffpai.catalogs import where_to_look
    got = where_to_look.snapshot_vectors(stem)
    if not got:
        return []
    vec = _embed_one(text)
    if vec is None:
        return []
    import numpy as np
    ids, vecs = got
    sims = vecs.astype(np.float32) @ vec
    order = np.argsort(-sims)[:max(0, int(limit))]
    return [(float(sims[i]), ids[i]) for i in order if sims[i] >= min_sim]


def local_search(job: dict[str, Any], kind: str, limit: int, *, key: str, loader: Callable,
                 fields: Callable, stem: str, row_id: Callable[[dict], str]) -> list[dict]:
    """Snapshot rows for *job*: word matches first, then meaning matches, at most *limit*."""
    idx = index_for(key, loader, fields)
    picked = [idx.rows[pos] for _s, pos, _m in idx.match(job_terms(job, kind), limit)]
    if len(picked) >= limit:
        return picked[:limit]
    by_id = getattr(idx, "by_id", None)
    if by_id is None:
        by_id = {}
        for r in idx.rows:
            by_id.setdefault(str(row_id(r)), r)
        idx.by_id = by_id
    seen = {str(row_id(r)) for r in picked}
    for _sim, rid in vector_match(stem, job_text(job, kind), limit=limit):
        if rid in by_id and rid not in seen:
            picked.append(by_id[rid])
            seen.add(rid)
        if len(picked) >= limit:
            break
    return picked


_INDEXES: dict[str, WordIndex] = {}
_INDEX_LOCK = threading.Lock()


def index_for(key: str, loader: Callable[[], list[dict]],
              fields: Callable[[dict], dict[str, str]]) -> WordIndex:
    with _INDEX_LOCK:
        idx = _INDEXES.get(key)
    if idx is None:
        idx = WordIndex(loader(), fields)
        with _INDEX_LOCK:
            _INDEXES[key] = idx
    return idx


def forget_indexes() -> None:
    with _INDEX_LOCK:
        _INDEXES.clear()


# ---------------------------------------------------------------------------
# the one fetch path: budget, cache, conditional requests
# ---------------------------------------------------------------------------

_STATUS_RE = re.compile(r"answered (\d{3})")
#: Accept values whose answer is the same shape without the header (so the
#: older fetch path, which sends no headers, still gets a usable answer).
PLAIN_ACCEPTS = frozenset({"application/vnd.github+json", "application/json"})
#: On the older fetch path, the most a "first part is enough" answer may read
#: before it is cut (that path refuses, rather than cuts, anything longer).
OLDER_PATH_TRUNCATED_MAX = 2 * 1024 * 1024


_DEADLINE = threading.local()


class deadline:
    """Within this block, on THIS thread, no new request is sent after *at*.

    *at* is a ``time.monotonic()`` value (None: no limit). A request not sent
    answers ``reason="budget"``, which every reader treats as "stop here": a
    find cut short keeps fewer facts, never gains any.
    """

    def __init__(self, at: Optional[float]):
        self.at = at

    def __enter__(self) -> "deadline":
        self._before = getattr(_DEADLINE, "at", None)
        _DEADLINE.at = self.at
        return self

    def __exit__(self, *exc: Any) -> None:
        _DEADLINE.at = self._before


def _past_deadline() -> bool:
    at = getattr(_DEADLINE, "at", None)
    return at is not None and time.monotonic() > at


def can_send_headers() -> bool:
    """True when ``source_fetch.fetch_small`` (which sends request headers) is in this build."""
    from puffpai import source_fetch
    return callable(getattr(source_fetch, "fetch_small", None))


def cache_dir() -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), "cache", "where_to_look")


def _cache_path(key: tuple) -> str:
    digest = hashlib.sha256(json.dumps(list(key), sort_keys=True).encode("utf-8")).hexdigest()
    return os.path.join(cache_dir(), digest + ".json")


def cache_read(key: tuple) -> Optional[dict[str, Any]]:
    try:
        with open(_cache_path(key), encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or not isinstance(rec.get("at"), (int, float)):
        return None
    body = rec.get("body_b64")
    try:
        rec["body"] = base64.b64decode(body) if isinstance(body, str) else None
    except ValueError:
        return None
    return rec


#: The cache keeps at most this many answers; the oldest go first.
CACHE_MAX_FILES = 3000
CACHE_KEEP_FILES = 2000


def prune_cache(directory: Optional[str] = None) -> int:
    """Remove the oldest cached answers once there are too many. Returns how many went."""
    directory = directory or cache_dir()
    try:
        names = [n for n in os.listdir(directory) if n.endswith(".json")]
    except OSError:
        return 0
    if len(names) <= CACHE_MAX_FILES:
        return 0
    aged = []
    for n in names:
        try:
            aged.append((os.path.getmtime(os.path.join(directory, n)), n))
        except OSError:
            continue
    aged.sort()
    gone = 0
    for _t, n in aged[:max(0, len(aged) - CACHE_KEEP_FILES)]:
        try:
            os.remove(os.path.join(directory, n))
            gone += 1
        except OSError:
            continue
    return gone


def cache_write(key: tuple, *, body: Optional[bytes], status: Optional[int], etag: Optional[str],
                truncated: bool, at: float) -> None:
    path = _cache_path(key)
    try:
        if not os.path.exists(path):
            prune_cache(os.path.dirname(path))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"   # finds are read in parallel
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"at": at, "status": status, "etag": (etag or None) and str(etag)[:200],
                       "truncated": bool(truncated),
                       "body_b64": base64.b64encode(body).decode("ascii") if body is not None
                       else None}, fh)
        os.replace(tmp, path)
    except OSError:
        pass


def _result(body, status, headers=None, truncated=False, reason="ok", cached=False):
    return {"body": body, "status": status, "headers": dict(headers or {}),
            "truncated": bool(truncated), "reason": reason, "cached": cached}


def fetch(url: str, *, hosts: Iterable[str], max_bytes: int, service: str, pool: str, budget,
          extra_pools: tuple = (), progress: Optional[Callable[[str], None]] = None,
          robots: bool = True, allow_truncated: bool = False, accept: Optional[str] = None,
          cache_key: Optional[tuple] = None, clock: Optional[Callable[[], float]] = None,
          cache: bool = True) -> dict:
    """One small GET for a searcher. ``{"body","status","headers","truncated","reason","cached"}``.

    A fresh cache entry answers without spending budget. A stale one is asked
    again with ``If-None-Match``; 304 keeps the cached body. 403/429 go through
    ``budget.observe`` (wait and try once more, or mark the source later).
    Never sends a key in this build.
    """
    from puffpai import source_fetch

    key = cache_key or ("url", url)
    # cache=False: the answer's body is never kept (a whole archive); a "not
    # there" answer still is, so it is not asked again for a day.
    cached = cache_read(key)
    clock = clock or getattr(budget, "clock", None) or time.time
    now = clock()
    if cached is not None and now - float(cached["at"]) < CACHE_TTL_S:
        return _result(cached.get("body"), cached.get("status"), truncated=cached.get("truncated"),
                       cached=True)
    headers: dict[str, str] = {}
    if accept:
        headers["accept"] = accept
    if cached is not None and cached.get("etag"):
        headers["if-none-match"] = str(cached["etag"])
    fetch_small = getattr(source_fetch, "fetch_small", None)
    if not callable(fetch_small) and accept and accept not in PLAIN_ACCEPTS:
        # The older path cannot send an Accept header, and this answer's shape
        # depends on one: asking would spend a call on an answer we cannot use.
        return _result(None, None, reason="needs headers")
    res = _result(None, None, reason="not asked")
    for attempt in (0, 1):
        if _past_deadline():
            return _result(None, None, reason="budget")
        if not budget.take(pool, *extra_pools, progress=progress):
            return _result(None, None, reason="later" if budget.later(pool) else "budget")
        if callable(fetch_small):
            got = fetch_small(url, allowed_hosts=set(hosts), max_bytes=max_bytes, service=service,
                              honour_robots=robots, allow_truncated=allow_truncated,
                              headers=headers or None)
            res = _result(got.get("body"), got.get("status"),
                          {str(k).lower(): v for k, v in (got.get("headers") or {}).items()},
                          got.get("truncated"), str(got.get("reason") or ""))
        else:
            # The older path refuses a body longer than its limit instead of
            # cutting it. When a cut answer is fine (git info/refs), let it read
            # up to OLDER_PATH_TRUNCATED_MAX and keep only the first max_bytes.
            limit = max(max_bytes, OLDER_PATH_TRUNCATED_MAX) if allow_truncated else max_bytes
            body, reason = source_fetch.get_small(url, allowed_hosts=set(hosts),
                                                  max_bytes=limit, honour_robots=robots,
                                                  service=service)
            m = _STATUS_RE.search(reason or "")
            status = 200 if body is not None else (int(m.group(1)) if m else None)
            cut = body is not None and len(body) > max_bytes
            res = _result(body[:max_bytes] if cut else body, status, truncated=cut, reason=reason)
        verdict = budget.observe(pool, status=res["status"], headers=res["headers"],
                                 progress=progress, retried=attempt == 1,
                                 body=res["body"] if res["status"] in (403, 429) else None)
        if verdict == "retry":
            continue
        if verdict == "later":
            res["reason"] = "later"
            return res
        if verdict == "refused":
            res["reason"] = "refused"
        break
    status = res["status"]
    if status == 304 and cached is not None:
        cache_write(key, body=cached.get("body"), status=cached.get("status"),
                    etag=cached.get("etag"), truncated=bool(cached.get("truncated")), at=now)
        return _result(cached.get("body"), cached.get("status"), res["headers"],
                       cached.get("truncated"), cached=True)
    if status == 200 and res["body"] is not None and cache:
        cache_write(key, body=res["body"], status=200, etag=res["headers"].get("etag"),
                    truncated=res["truncated"], at=now)
    elif status == 404:
        cache_write(key, body=None, status=404, etag=None, truncated=False, at=now)
    return res


def deep(obj: Any) -> Any:
    return copy.deepcopy(obj)


# ---------------------------------------------------------------------------
# pictures: thumbnails kept on this computer (the dashboard never loads an outside picture)
# ---------------------------------------------------------------------------

THUMB_MAX_BYTES = 2 * 1024 * 1024
THUMB_MAX_PIXELS = 40_000_000
THUMB_SIDE = 480
_PICTURE_KINDS = ("JPEG", "PNG", "GIF", "WEBP")


def picture_kind(body: bytes) -> Optional[str]:
    """JPEG, PNG, GIF or WEBP from the first bytes, else None (SVG and the rest refused)."""
    if not isinstance(body, (bytes, bytearray)) or len(body) < 12:
        return None
    head = bytes(body[:12])
    if head.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "GIF"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "WEBP"
    return None


def thumbs_dir() -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), "cache", "thumbs")


def save_thumbnail(body: Optional[bytes]) -> Optional[str]:
    """A picture from outside, made safe: re-drawn as a small plain JPEG on this computer.

    Refused (None): empty or over 2 MB, not a JPEG/PNG/GIF/WebP by its own
    first bytes (no SVG, which can carry script), a declared size over 40
    million pixels (checked from the header, before any decoding), a format
    that differs from its first bytes, or anything Pillow cannot read. Only
    the first frame is used; the copy keeps no metadata. The file name is the
    sha256 of the new JPEG, under ``<state>/cache/thumbs/``.
    """
    import io
    import warnings

    kind = picture_kind(body or b"")
    if kind is None or len(body) > THUMB_MAX_BYTES:
        return None
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(body)) as im:
                if im.format != kind or im.format not in _PICTURE_KINDS:
                    return None
                w, h = im.size
                if w <= 0 or h <= 0 or w * h > THUMB_MAX_PIXELS:
                    return None
                if kind == "JPEG":
                    im.draft("RGB", (THUMB_SIDE * 2, THUMB_SIDE * 2))
                im.seek(0)
                frame = im.convert("RGB")
        frame.thumbnail((THUMB_SIDE, THUMB_SIDE))
        out = io.BytesIO()
        frame.save(out, "JPEG", quality=82, optimize=True)
    except Exception:  # noqa: BLE001 - a picture Pillow cannot read is refused
        return None
    data = out.getvalue()
    name = hashlib.sha256(data).hexdigest() + ".jpg"
    folder = thumbs_dir()
    path = os.path.join(folder, name)
    try:
        os.makedirs(folder, exist_ok=True)
        if not os.path.exists(path):
            tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
    except OSError:
        return None
    return path


__all__ = ["CACHE_TTL_S", "FOUND_SCHEMA", "JOB_VERBS", "WordIndex", "cache_dir", "cache_read",
           "cache_write", "empty_details", "fetch", "forget_indexes", "index_for", "job_terms",
           "new_found"]
