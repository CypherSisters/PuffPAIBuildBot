"""Where a build looks for live information, outside services and devices.

This is the "knows where to look" half for everything that is not a download:
free websites that answer questions with fresh data (a launch schedule, a
recall list, a fossil record), and ways to connect the owner's own things
(through Home Assistant) or outside tools (listed in the MCP Registry).

It works for ANY goal. Nothing here knows about space, cars or dinosaurs: the
goal's words are matched against lists that span every topic -

* our own hand-checked list of free websites (``config/catalog_seeds/apis.json``,
  every row checked live with one small request before it shipped),
* the APIs.guru directory (:mod:`puffpai.catalogs.apis_guru`),
* the public-apis list (:mod:`puffpai.catalogs.public_apis`),
* Home Assistant's integration list (:mod:`puffpai.catalogs.ha_integrations`),
* the MCP Registry (:mod:`puffpai.catalogs.mcp_registry`).

Rules this module keeps
-----------------------
* **Offline by default.** :func:`discover_live`, :func:`discover_integrations`
  and :func:`proposal_items` read dated snapshots on disk and never open a
  socket. The only thing that goes online is :func:`refresh` (and the lazy
  spec read in :mod:`puffpai.catalogs.apis_guru`), and only when the owner runs
  ``puffpai catalogs refresh`` and says yes.
* **One named outside service per request.** Every fetch names the list it is
  reading and may only reach that list's own hosts (``config/catalog_index.json``).
  Each hop is re-checked with :func:`puffpai.source_fetch.guard_url` - https on
  the default port, public addresses only, no credentials, no proxy.
* **Fetched text is data.** Names and descriptions from outside lists are
  cleaned to short plain text and shown as data. They never become
  instructions; the optional model re-rank may only reorder ids this code
  returned (:func:`rerank`).
* **Evidence or silence.** No match means no line. A website we could not
  check is never pre-ticked. "Unknown" is never treated as "free" or "fine".
* **Nothing is installed or run.** An MCP server is shown as something that
  would need a code sandbox, which this build does not have yet.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import datetime
import glob
import gzip
import hashlib
import json
import os
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional, Sequence

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

SNAPSHOT_SCHEMA = "catalog_snapshot/v1"
CANDIDATE_KINDS = ("knowledge", "live_data", "integration", "theme")
AUTH_VALUES = ("none", "free_key", "paid", "unknown")
PLACE_VALUES = ("local", "cloud", "unknown")

#: The catalogs this module reads. Ids match rows in ``config/catalog_index.json``.
CATALOG_IDS = ("api_seeds", "apis_guru", "public_apis", "ha_integrations", "mcp_registry")

from puffpai import USER_AGENT  # noqa: E402 - one name for every request
MAX_REDIRECTS = 3
TIMEOUT_S = 30.0
DEFAULT_MAX_BYTES = 25 * 1024 * 1024
HOST_MIN_INTERVAL_S = 1.0

_SNAPSHOT_NAME = re.compile(r"^(?P<cat>[a-z0-9_]+)_(?P<date>\d{4}-\d{2}-\d{2})\.json(?:\.gz)?$")


class CatalogFetchError(RuntimeError):
    """A catalog request was refused or failed. ``str(exc)`` is plain words."""


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------

def repo_root() -> str:
    try:
        from puffpai.config import get_config
        root = get_config().REPO_ROOT
        if root:
            return root
    except Exception:  # pragma: no cover - config unreadable
        pass
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))


def shipped_snapshot_dir() -> str:
    return os.path.join(repo_root(), "config", "catalog_snapshots")


def state_snapshot_dir() -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), "catalog_snapshots")


def index_rows() -> dict[str, dict[str, Any]]:
    """This module's rows from ``config/catalog_index.json``, by id. Never raises."""
    path = os.path.join(repo_root(), "config", "catalog_index.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    rows = data.get("catalogs") if isinstance(data, dict) else None
    out: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        if isinstance(row, dict) and row.get("id") in CATALOG_IDS:
            out[str(row["id"])] = row
    return out


def catalog_hosts(catalog_id: str) -> set[str]:
    row = index_rows().get(catalog_id) or {}
    return {str(h).lower() for h in (row.get("search_hosts") or []) if h}


def catalog_label(catalog_id: str) -> str:
    row = index_rows().get(catalog_id) or {}
    return str(row.get("label") or catalog_id)


# ---------------------------------------------------------------------------
# snapshots
# ---------------------------------------------------------------------------

_snap_lock = threading.Lock()
_snap_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def snapshot_files(catalog_id: str, dirs: Optional[Sequence[str]] = None) -> list[tuple[str, str]]:
    """``(date, path)`` for every snapshot of this catalog, oldest first.

    A state-folder snapshot sorts after a shipped one of the same date, so a
    refresh on the owner's machine wins over what shipped.
    """
    if dirs is None:
        dirs = [shipped_snapshot_dir()]
        try:
            dirs.append(state_snapshot_dir())
        except Exception:  # pragma: no cover - no state dir
            pass
    found: list[tuple[str, int, str]] = []
    for rank, folder in enumerate(dirs):
        for path in glob.glob(os.path.join(folder, f"{catalog_id}_*.json*")):
            match = _SNAPSHOT_NAME.match(os.path.basename(path))
            if match and match.group("cat") == catalog_id:
                found.append((match.group("date"), rank, path))
    found.sort()
    return [(date, path) for date, _rank, path in found]


def read_snapshot_file(path: str) -> Optional[dict[str, Any]]:
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return None
    with _snap_lock:
        cached = _snap_cache.get(path)
        if cached and cached[0] == stamp:
            return cached[1]
    try:
        if path.endswith(".gz"):
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                data = json.load(handle)
        else:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
    except (OSError, ValueError, EOFError) as exc:
        logger.warning("catalog snapshot unreadable: %s (%s)", path, exc)
        return None
    if not isinstance(data, dict) or data.get("schema") != SNAPSHOT_SCHEMA \
            or not isinstance(data.get("items"), list):
        logger.warning("catalog snapshot has the wrong shape: %s", path)
        return None
    with _snap_lock:
        _snap_cache[path] = (stamp, data)
    return data


def latest_snapshot(catalog_id: str, dirs: Optional[Sequence[str]] = None) -> Optional[dict[str, Any]]:
    """The newest readable snapshot of a catalog, or None."""
    for _date, path in reversed(snapshot_files(catalog_id, dirs)):
        data = read_snapshot_file(path)
        if data is not None and data.get("catalog") == catalog_id:
            data = dict(data)
            data["_path"] = path
            return data
    return None


def clear_caches() -> None:
    with _snap_lock:
        _snap_cache.clear()
    with _rate_lock:
        _last_request.clear()
    _corpus_cache.clear()


def write_snapshot(catalog_id: str, items: list[dict[str, Any]], *,
                   provenance: dict[str, Any], out_dir: str,
                   compress: bool = False, date: Optional[str] = None,
                   extra: Optional[dict[str, Any]] = None) -> str:
    """Write a dated snapshot atomically. Returns its path."""
    day = date or datetime.date.today().isoformat()
    os.makedirs(out_dir, exist_ok=True)
    record: dict[str, Any] = {
        "schema": SNAPSHOT_SCHEMA,
        "catalog": catalog_id,
        "date": day,
        "provenance": provenance,
        "count": len(items),
    }
    if extra:
        record.update(extra)
    record["items"] = items
    name = f"{catalog_id}_{day}.json" + (".gz" if compress else "")
    path = os.path.join(out_dir, name)
    tmp = path + ".tmp"
    text = json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    if compress:
        with gzip.open(tmp, "wt", encoding="utf-8") as handle:
            handle.write(text)
    else:
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.write("\n")
    os.replace(tmp, path)
    clear_caches()
    return path


# ---------------------------------------------------------------------------
# the one guarded fetch
# ---------------------------------------------------------------------------

@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    body: bytes
    sha256: str
    fetched_at: str
    content_type: str


_rate_lock = threading.Lock()
_last_request: dict[str, float] = {}


def _rate_limit(host: str, interval: float,
                sleeper: Callable[[float], None] = time.sleep) -> None:
    with _rate_lock:
        now = time.monotonic()
        last = _last_request.get(host)
        wait = 0.0 if last is None else interval - (now - last)
        _last_request[host] = now + max(wait, 0.0)
    if wait > 0:
        sleeper(wait)


#: The outside-services id list refreshes are logged and switched under.
REFRESH_SERVICE = "catalog_refresh"


def _default_transport(url: str, headers: dict[str, str], timeout: float) -> Any:
    """One GET through the sourcing gate's own transport (no redirect
    following, no proxy, no cookies); :func:`fetch_url` guards every hop."""
    from puffpai import source_fetch
    return source_fetch._default_transport(url, headers, timeout)


def _status(response: Any) -> int:
    for attr in ("status", "code"):
        value = getattr(response, attr, None)
        if isinstance(value, int):
            return value
    return 0


def _header(response: Any, name: str) -> str:
    headers = getattr(response, "headers", None)
    try:
        value = headers.get(name) if headers is not None else None
    except Exception:  # pragma: no cover
        value = None
    return "" if value is None else str(value)


def fetch_url(url: str, *, hosts: Iterable[str], max_bytes: int = DEFAULT_MAX_BYTES,
              accept: str = "application/json",
              transport: Optional[Callable[[str, dict[str, str], float], Any]] = None,
              resolver: Optional[Callable[[str], list[str]]] = None,
              interval: float = HOST_MIN_INTERVAL_S,
              sleeper: Callable[[float], None] = time.sleep) -> FetchResult:
    """GET one address from a named list's own hosts. Raises CatalogFetchError.

    Every hop - the first and each redirect - must be https on the default
    port, on one of ``hosts``, and resolve only to public addresses. The body
    is read up to ``max_bytes`` and refused beyond it. Nothing is sent but the
    request line and a plain User-Agent: no cookies, no key, no proxy.
    """
    from puffpai.source_fetch import guard_url

    allowed = {str(h).lower() for h in hosts if h}
    if not allowed:
        raise CatalogFetchError("No outside service is named for this request, so nothing was sent.")
    from puffpai.security import egress_ledger, outside_switches
    from puffpai.source_fetch import _pin_refusal_code

    def _log(outcome: str, target: str, **fields: Any) -> None:
        # Host only, per the outside-traffic log's path rule.
        egress_ledger.record(REFRESH_SERVICE, host=egress_ledger.safe_host(target),
                             outcome=outcome, what="list_refresh", **fields)

    send = transport or _default_transport
    headers = {"User-Agent": USER_AGENT, "Accept": accept, "Accept-Encoding": "identity"}
    current = str(url)
    for _hop in range(MAX_REDIRECTS + 1):
        if outside_switches.is_off(REFRESH_SERVICE):
            _log("refused", current, code="switched_off", bytes_in=0, bytes_out=0)
            raise CatalogFetchError(
                "Updating the lists of free services is switched off under "
                "\"What leaves your computer\", so nothing was sent.")
        ok, reason = guard_url(current, allowed_hosts=allowed, resolver=resolver)
        if not ok:
            _log("refused", current, code="not_allowed", bytes_in=0, bytes_out=0)
            raise CatalogFetchError(f"Refused before sending anything: {reason}")
        host = (urllib.parse.urlsplit(current).hostname or "").lower()
        _rate_limit(host, interval, sleeper)
        sent = len(current) + sum(len(k) + len(v) + 4 for k, v in headers.items()) + 16
        try:
            response = send(current, dict(headers), TIMEOUT_S)
        except OSError as exc:
            refused = _pin_refusal_code(exc)
            if refused:
                _log("refused", current, code=refused, bytes_in=0, bytes_out=0)
            else:
                _log("failed", current, code="no_answer", bytes_out=sent)
            raise CatalogFetchError(f"Could not reach {host}: {exc}") from None
        status = _status(response)
        _declared = _header(response, "Content-Length")
        _log("done", current, status=status, bytes_out=sent,
             bytes_in=int(_declared) if _declared.isdigit() else None)
        if status in (301, 302, 303, 307, 308):
            location = _header(response, "Location")
            try:
                response.close()
            except Exception:  # pragma: no cover
                pass
            if not location:
                raise CatalogFetchError(f"{host} sent us somewhere else without saying where.")
            current = urllib.parse.urljoin(current, location)
            continue
        if status != 200:
            try:
                response.close()
            except Exception:  # pragma: no cover
                pass
            raise CatalogFetchError(f"{host} answered with code {status}, so nothing was saved.")
        declared = _header(response, "Content-Length")
        if declared.isdigit() and int(declared) > max_bytes:
            response.close()
            raise CatalogFetchError(
                f"{host} offered {int(declared):,} bytes, more than the {max_bytes:,} we allow.")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                response.close()
                raise CatalogFetchError(
                    f"{host} sent more than the {max_bytes:,} bytes we allow, so we stopped.")
            chunks.append(chunk)
        try:
            response.close()
        except Exception:  # pragma: no cover
            pass
        body = b"".join(chunks)
        return FetchResult(
            url=str(url), final_url=current, status=status, body=body,
            sha256=hashlib.sha256(body).hexdigest(),
            fetched_at=datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
            content_type=_header(response, "Content-Type"),
        )
    raise CatalogFetchError(f"The address redirected more than {MAX_REDIRECTS} times.")


def fetch_json(url: str, **kwargs: Any) -> tuple[Any, FetchResult]:
    result = fetch_url(url, **kwargs)
    try:
        return json.loads(result.body.decode("utf-8")), result
    except (UnicodeDecodeError, ValueError):
        raise CatalogFetchError("The answer was not readable data, so nothing was saved.") from None


def provenance_of(result: FetchResult, catalog_id: str) -> dict[str, Any]:
    return {
        "catalog": catalog_id,
        "source_url": result.url,
        "final_url": result.final_url,
        "fetched_at": result.fetched_at,
        "bytes": len(result.body),
        "sha256": result.sha256,
        "fetched_by": USER_AGENT,
    }


def refresh_refusal() -> Optional[str]:
    """Why this build may not go online to refresh lists, or None if it may.

    A build whose recorded posture says "offline while building" is not asked
    to go online by a later command either. With no build recorded yet, the
    owner's explicit command plus their yes is the permission.
    """
    try:
        from puffpai.build_manifest import read_manifest
        from puffpai.build_posture import gates
        manifest = read_manifest()
    except Exception:  # pragma: no cover - unreadable manifest
        manifest = None
    posture = (manifest or {}).get("posture") if isinstance(manifest, dict) else None
    if isinstance(posture, dict) and posture.get("build_network"):
        if not gates(posture).get("may_offer_online_sourcing"):
            return ("This bot was set up to stay offline, so it will not go online "
                    "to update its lists. Re-run setup to change that.")
    return None


# ---------------------------------------------------------------------------
# plain text and words
# ---------------------------------------------------------------------------

_TAG = re.compile(r"<[^>]{0,400}>")
_MD_LINK = re.compile(r"!?\[([^\]]{0,200})\]\([^)]{0,500}\)")
_URL = re.compile(r"https?://\S+")
_WS = re.compile(r"\s+")


def clean_text(value: Any, limit: int = 240) -> str:
    """Outside text, made safe to show: no markup, no links, no control chars."""
    text = "" if value is None else str(value)
    text = _MD_LINK.sub(r"\1", text)
    text = _TAG.sub(" ", text)
    text = _URL.sub("", text)
    text = "".join(ch if (unicodedata.category(ch)[0] != "C") else " " for ch in text)
    text = text.replace("`", "").replace("*", "").replace("#", "")
    text = _WS.sub(" ", text).strip()
    if len(text) > limit:
        # Cut at a word and leave room for the "...", so the result is never
        # longer than ``limit`` (labels promise <= 80 characters).
        cut = text[: max(1, limit - 3)].rsplit(" ", 1)[0]
        text = cut.rstrip(",;:.- ") + "..."
    return text


#: Ordinary English filler. General language, not topic knowledge: a goal's
#: topic words are what is left after these are gone.
STOPWORDS = frozenset("""
a an the and or but if so of to in on at by for from with without about into over under
i me my mine we our us you your he she it its they them their this that these those
is am are was were be been being do does did have has had can could will would should may might must
want wants wanted like likes love loves need needs wish make makes build help helps helper helping
bot bots robot buddy friend assistant app thing things stuff cool fun awesome great nice really very
know knows knowing knowledge everything anything something all every each any some more most lots lot
show shows showing tell tells teach teaches learn learning give gives get gets find finds keep keeps
work works working use uses using play how what when where why who which tip tips info information
please just also too plus one two three new old big small good best better
smart tos howto howtos
""".split())

#: Words that introduce a place ("in Ohio", "near Leeds"). A capitalised word
#: after one of these is where the owner lives, not what the bot is about.
_PLACE_PREPS = frozenset(("in", "near", "around", "from"))


def place_words(goal: str) -> set[str]:
    """Capitalised words right after "in"/"near"/"around"/"from". Wording only."""
    tokens = re.findall(r"[A-Za-z][A-Za-z'.-]*", str(goal or ""))
    out: set[str] = set()
    for i, tok in enumerate(tokens[:-1]):
        if tok.lower() in _PLACE_PREPS:
            j = i + 1
            while j < len(tokens) and tokens[j][:1].isupper() and j - i <= 3:
                out.add(stem(tokens[j].lower()))
                j += 1
    return out

_WORD = re.compile(r"[a-z0-9]+")


def stem(word: str) -> str:
    """Plural to singular, and nothing cleverer. ``dinosaurs`` -> ``dinosaur``."""
    w = word.lower()
    if len(w) <= 3 or w.isdigit():
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith(("ches", "shes", "sses", "xes", "zes")):
        return w[:-2]
    if w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def words(text: Any) -> list[str]:
    return [stem(w) for w in _WORD.findall(str(text or "").lower())]


def content_words(text: Any) -> list[str]:
    out: list[str] = []
    for raw in _WORD.findall(str(text or "").lower()):
        if raw in STOPWORDS or len(raw) < 3 or raw.isdigit():
            continue
        w = stem(raw)
        if w in STOPWORDS:
            continue
        if w not in out:
            out.append(w)
    return out


def terms_from_goal(goal: str) -> list[str]:
    """The topic words of a goal, in order. Deterministic; no model.

    The words come from :func:`goal_words.search_terms`, so the
    owner's relatives, names, ages, dates, places and sensitive words, and the
    words people use to tell their news ("getting", "married"), are never a
    topic. Place names after "in"/"near" are left out as before: they say
    where the owner is, and they go to the home-location setting.
    """
    from puffpai.catalogs import goal_words
    places = place_words(goal)
    kept = normalize_terms(goal_words.search_terms(goal, limit=24))
    return [w for w in kept if w not in places]


#: Words that say what shape a thing has, not what it is about (else NASA
#: pictures land on a wedding plan): these shared, alone or together, are no
#: evidence that a live-info website fits the bot ("pictures and photos"
#: would bring NASA to a wedding, a scrapbook and a recipe bot).
GENERIC_TAGS = frozenset("""picture photo photograph image pic calendar date day week
time schedule list data info information news map search album gallery video file record
log note daily random free open online""".split())


#: Words that say what a bot or a website DOES, not what it is about (else
#: "Issue Tracking API" for a bakery on "tracking", "BC Route Planner" for a
#: wedding planner on "planner"). Like
#: :data:`GENERIC_TAGS`, they are never the evidence a website fits.
ACTION_WORDS = frozenset("""track tracking tracker remind reminder reminded find finder see
look show get make plan planner manage manager organize organizer keep save pick picker
custom export import pay helper tool app monthly weekly yearly""".split())

#: How many of a list's own words for a website count as what it is mainly
#: about. Our checked list writes each website's main subjects first
#: ("earthquake" for USGS, "aurora" for the space weather centre).
_MAIN_TAGS = 2
#: A word of the bot's own subject may sit a little further down that list
#: ("bird" for iNaturalist and GBIF).
_SUBJECT_TAGS = 6


def _tag_index(word: str, tags: Sequence[str]) -> int:
    from puffpai.catalogs import goal_words
    return next((i for i, t in enumerate(tags) if goal_words._same_word(word, t)), len(tags) + 99)


#: A verb that starts a task: at the start of a line, a list item or a
#: clause, or after "to"/"and"/"then", with "the"/"a"/"my" after it ("clean
#: the ring", "to water the plants").
_TASK_VERB_RE = re.compile(
    r"(?:^|[\n\r,;:.!?]|\bto\b|\band\b|\bthen\b)[ \t]*(?:[-*•·+][ \t]*|\d{1,2}[.)][ \t]*)?"
    r"([A-Za-z]+)[ \t]+(?:the|a|an|my|our|his|her|their|your|some)\b", re.I)


def _task_verbs(goal: str) -> set[str]:
    """The stems of verbs that start a task in *goal* ("clean the ring",
    "water the nest", "feed the echo"): what she wants done, never what the
    bot is about (else a chore list matches an EPA "Clean Water Act" card
    on "clean" and "water")."""
    from puffpai.catalogs import personal_words
    out: set[str] = set()
    for m in _TASK_VERB_RE.finditer(str(goal or "")[:2000]):
        w = m.group(1).lower()
        if personal_words.is_common_word(w) and personal_words._has_verb_forms(w):
            out.add(stem(w))
    return out


def fits_goal(goal: str, cand: dict[str, Any], matched: Sequence[str]) -> bool:
    """Does a live-info website really fit *goal*?

    The whole goal sentence is not passed to
    :func:`goal_words.match_strength` as one request: that wants every word
    of it, so an earthquake bot would lose USGS and a book club bot its
    libraries. Each of the goal's own search words (personal words already
    out) is checked on its own against the website's name, description and
    the words its list files it under. A word counts only when it says what
    the bot is ABOUT: never a shape word (:data:`GENERIC_TAGS`: "picture",
    "calendar") or a doing word (:data:`ACTION_WORDS`: "track", "planner"),
    and never a word she typed only as the last word of a longer phrase
    (the "light" of a warning light is not about lights) unless it is a
    word of the bot's subject (the last word of a two-word subject counts).
    Then it fits when

    * two such words hold (a car bot's car and registration), or
    * one such word is what the website is mainly about (among the first
      :data:`_MAIN_TAGS` words of its list: earthquake for USGS), or
    * one such word is a word of the bot's subject and sits among the first
      :data:`_SUBJECT_TAGS` words of its list (bird for a bird watching
      log bot).

    Pictures and photos never bring space pictures to a wedding plan, a
    monster never brings Pokemon to a D&D bot, and a night never brings a
    stargazing forecast to a movie night bot. *matched* (what the ranker
    matched) is kept for callers; the check reads the goal itself.
    """
    from puffpai.catalogs import goal_words, personal_words
    text = f"{cand.get('name') or ''} {cand.get('what_it_is') or ''}"
    tags = [str(t) for t in cand.get("match_terms") or ()]
    try:
        terms = goal_words.search_terms(goal, limit=24)
        runs = goal_words.request_runs(personal_words.without_hard_tokens(goal))
        subject = {goal_words.singular(w) for w in personal_words.subject_of(goal).split()}
    except Exception:  # noqa: BLE001 - a check that fails keeps the line off
        return False
    whole = {w for r in runs for w in (r if len(r) == 1 else r[:-1])}
    tail_only = {r[-1] for r in runs if len(r) > 1} - whole
    doing = _task_verbs(goal)
    hits: list[str] = []
    for term in terms:
        key = stem(term)
        if not key or key in GENERIC_TAGS or key in ACTION_WORDS or term in ACTION_WORDS:
            continue
        if key in doing and goal_words.singular(term) not in subject:
            continue
        if goal_words.singular(term) not in subject and any(
                goal_words._same_word(term, t) for t in tail_only) and not any(
                goal_words._same_word(term, w) for w in whole):
            continue
        try:
            level = goal_words.match_strength(term, text, subjects=tags)[0]
        except Exception:  # noqa: BLE001
            continue
        if level >= 1 and key not in {stem(h) for h in hits}:
            hits.append(term)
    if len(hits) >= 2:
        return True
    for term in hits:
        where = _tag_index(term, tags)
        if where < _MAIN_TAGS:
            return True
        if goal_words.singular(term) in subject and where < _SUBJECT_TAGS:
            return True
    return False


def normalize_terms(terms: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for term in terms or []:
        for w in content_words(term):
            if w not in out:
                out.append(w)
    return out[:24]


# ---------------------------------------------------------------------------
# candidates
# ---------------------------------------------------------------------------

def make_candidate(**fields: Any) -> dict[str, Any]:
    """A Candidate with every field present and every enum checked."""
    kind = fields.get("kind")
    if kind not in CANDIDATE_KINDS:
        raise ValueError(f"unknown candidate kind: {kind!r}")
    auth = fields.get("auth") or "unknown"
    if auth not in AUTH_VALUES:
        auth = "unknown"
    place = fields.get("local_or_cloud")
    if place is not None and place not in PLACE_VALUES:
        place = "unknown"
    size = fields.get("size_bytes")
    commercial = fields.get("commercial_ok")
    return {
        "id": str(fields["id"]),
        "kind": kind,
        "name": clean_text(fields.get("name"), 80) or str(fields["id"]),
        "what_it_is": clean_text(fields.get("what_it_is"), 240),
        "source_catalog": str(fields.get("source_catalog") or ""),
        "url": fields.get("url") or None,
        "host": (str(fields["host"]).lower() if fields.get("host") else None),
        "sample_endpoint": fields.get("sample_endpoint") or None,
        "auth": auth,
        "license": str(fields.get("license") or "not stated"),
        "commercial_ok": commercial if isinstance(commercial, bool) else None,
        "size_bytes": size if isinstance(size, int) and size >= 0 else None,
        "local_or_cloud": place,
        "needs": [str(n) for n in (fields.get("needs") or [])],
        "match_terms": [str(t) for t in (fields.get("match_terms") or [])][:40],
        "verified_at": fields.get("verified_at") or None,
        # Optional, from our own hand-checked list only: the address a card
        # reads (placeholders {topic}, {lat_round}, {lon_round}), a neutral
        # subject for it, and the card's own fields. None everywhere else.
        "card_url": fields.get("card_url") or None,
        "card_topic": fields.get("card_topic") or None,
        "card": fields.get("card") if isinstance(fields.get("card"), dict) else None,
        # Our own list only: how a picture NAME in a reply row becomes a
        # picture address (api_seeds.image_template_problem checks it).
        "image_template": fields.get("image_template") or None,
        # Our own list only: the app-naming header a service documents
        # (feeds.UA_HEADERS); it carries this product's User-Agent.
        "ua_header": fields.get("ua_header") or None,
        # Our own list only: the odd label a service puts on its JSON reply
        # (feeds.JSON_SENT_AS); the reply must still read as JSON.
        "json_label": fields.get("json_label") or None,
        # Our own list only: a search-style address with one {topic}, for
        # looking a subject up from chat (feeds.lookup_url_problem checks it).
        "lookup_url": fields.get("lookup_url") or None,
    }


def safe_https_url(value: Any) -> Optional[str]:
    """An outside-supplied address, kept only if it is a plain https URL."""
    text = str(value or "").strip()
    if not text or len(text) > 500 or any(ch.isspace() for ch in text):
        return None
    try:
        parts = urllib.parse.urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        return None
    if port not in (None, 443):
        return None
    host = parts.hostname.lower()
    if host in ("localhost",) or host.endswith((".local", ".localhost", ".internal", ".lan")):
        return None
    if re.fullmatch(r"[0-9.]+|\[?[0-9a-f:]+\]?", host):
        return None
    return text


def host_of(url: Any) -> Optional[str]:
    try:
        host = urllib.parse.urlsplit(str(url or "")).hostname
    except ValueError:
        return None
    return host.lower() if host else None


# ---------------------------------------------------------------------------
# ranking
# ---------------------------------------------------------------------------

_corpus_cache: dict[str, Any] = {}

#: Where a match counts for more. A hit on a word the list itself tagged the
#: item with beats a hit buried in a description.
FIELD_WEIGHT = {"terms": 3.0, "name": 2.5, "desc": 1.0}
#: How far each list is trusted to be right about an item. Hand-checked rows
#: first, then lists with machine-readable detail, then a plain README list.
SOURCE_WEIGHT = {"api_seeds": 1.6, "apis_guru": 1.0, "public_apis": 0.85,
                 "ha_integrations": 1.0, "mcp_registry": 0.7}


#: Lists whose names are package or repository names (for example
#: "django-telescope"). One goal word in such a name is too often a
#: coincidence, so it only counts when the list's own description uses the
#: word too, or when two goal words match.
_NAME_NEEDS_DESC = frozenset(("mcp_registry",))


def _fields_of(cand: dict[str, Any]) -> dict[str, set[str]]:
    desc = cand.get("what_it_is")
    if cand.get("source_catalog") == "mcp_registry":
        from puffpai.catalogs import mcp_registry
        desc = mcp_registry.own_description(desc)
    return {
        "terms": set(w for t in cand.get("match_terms") or [] for w in words(t)),
        "name": set(words(cand.get("name"))),
        "desc": set(words(desc)),
    }


def _idf(corpus: list[dict[str, Any]]) -> dict[str, float]:
    import math
    counts: dict[str, int] = {}
    for cand in corpus:
        seen: set[str] = set()
        for field in _fields_of(cand).values():
            seen |= field
        for w in seen:
            counts[w] = counts.get(w, 0) + 1
    n = max(len(corpus), 1)
    return {w: math.log(1 + n / c) for w, c in counts.items()}


def rank(candidates: list[dict[str, Any]], terms: Sequence[str],
         idf: Optional[dict[str, float]] = None) -> list[tuple[float, list[str], dict[str, Any]]]:
    """``(score, matched_terms, candidate)``, best first; only real matches.

    A candidate matched only inside its description needs two different goal
    words there, so one common word in a long blurb is not a match. For lists
    named by package (see ``_NAME_NEEDS_DESC``) a single name match also needs
    the description to agree.
    """
    wanted = [t for t in terms if t]
    if not wanted:
        return []
    weights = idf if idf is not None else _idf(candidates)
    default_w = max(weights.values(), default=1.0)
    out: list[tuple[float, list[str], dict[str, Any]]] = []
    for cand in candidates:
        fields = _fields_of(cand)
        needs_desc = cand.get("source_catalog") in _NAME_NEEDS_DESC
        score = 0.0
        matched: list[str] = []
        strong = False
        for term in wanted:
            best = 0.0
            for name, bag in fields.items():
                if term in bag:
                    best = max(best, FIELD_WEIGHT[name])
                    if name != "desc" and (not needs_desc or term in fields["desc"]):
                        strong = True
            if best:
                score += best * weights.get(term, default_w)
                matched.append(term)
        if not matched or (not strong and len(matched) < 2):
            continue
        score *= SOURCE_WEIGHT.get(cand.get("source_catalog", ""), 1.0)
        if cand.get("kind") == "live_data":
            if cand.get("auth") == "none":
                score *= 1.15
            elif cand.get("auth") in ("paid",):
                score *= 0.5
            if cand.get("sample_endpoint") and cand.get("verified_at"):
                score *= 1.1
        out.append((round(score, 4), matched, cand))
    out.sort(key=lambda item: (-item[0], item[2]["name"].lower(), item[2]["id"]))
    return out


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _live_corpus() -> tuple[list[dict[str, Any]], dict[str, float]]:
    from puffpai.catalogs import api_seeds, apis_guru, public_apis
    key = "live"
    stamp = (_mtime(api_seeds.seed_path()),) + tuple(
        (cid, tuple((p, _mtime(p)) for _d, p in snapshot_files(cid)))
        for cid in ("apis_guru", "public_apis", "api_seeds_check"))
    cached = _corpus_cache.get(key)
    if cached and cached[0] == stamp:
        return cached[1]
    corpus: list[dict[str, Any]] = []
    corpus.extend(api_seeds.candidates())
    corpus.extend(apis_guru.candidates())
    corpus.extend(public_apis.candidates())
    value = (corpus, _idf(corpus))
    _corpus_cache[key] = (stamp, value)
    return value


def _dedupe_by_host(ranked: list[tuple[float, list[str], dict[str, Any]]]) -> list[tuple[float, list[str], dict[str, Any]]]:
    """The same service listed twice (our list and a directory) shows once.

    It keeps the best position, and the entry from the most trusted list: our
    checked row beats APIs.guru (which has a readable description), which
    beats a plain README row.
    """
    slot_by_key: dict[str, int] = {}
    out: list[tuple[float, list[str], dict[str, Any]]] = []
    for item in ranked:
        cand = item[2]
        keys = [k for k in (cand.get("host") or host_of(cand.get("url")) or "",
                            " ".join(words(cand.get("name")))) if k]
        hit = next((slot_by_key[k] for k in keys if k in slot_by_key), None)
        if hit is None:
            for k in keys:
                slot_by_key[k] = len(out)
            out.append(item)
            continue
        score, matched, kept = out[hit]
        if SOURCE_WEIGHT.get(cand["source_catalog"], 0) > SOURCE_WEIGHT.get(kept["source_catalog"], 0):
            out[hit] = (score, matched, cand)
        for k in keys:
            slot_by_key.setdefault(k, hit)
    return out


def discover_live_ranked(terms: Sequence[Any], audience: Any = None, *,
                         limit: int = 10) -> list[tuple[float, list[str], dict[str, Any]]]:
    wanted = normalize_terms(terms)
    corpus, idf = _live_corpus()
    return _dedupe_by_host(rank(corpus, wanted, idf))[: max(0, int(limit))]


def discover_live(terms: Sequence[Any], audience: Any = None, *, limit: int = 10) -> list[dict[str, Any]]:
    """Live-data Candidates for a goal's words, best first. Offline.

    Order of trust: our hand-checked list, then APIs.guru, then public-apis.
    ``audience`` is accepted for the shared call shape and deliberately does
    not filter anything: the finished bot has no age mode; age only changes
    how the wizard words its questions.
    """
    return [cand for _s, _m, cand in discover_live_ranked(terms, audience, limit=limit)]


def _integration_corpus() -> tuple[list[dict[str, Any]], dict[str, float]]:
    key = "integrations"
    stamp = tuple((cid, tuple((p, _mtime(p)) for _d, p in snapshot_files(cid)))
                  for cid in ("ha_integrations", "mcp_registry"))
    cached = _corpus_cache.get(key)
    if cached and cached[0] == stamp:
        return cached[1]
    from puffpai.catalogs import ha_integrations, mcp_registry
    ha = ha_integrations.candidates()
    mcp = mcp_registry.candidates()
    value = (ha + mcp, _idf(ha + mcp))
    _corpus_cache[key] = (stamp, value)
    return value


def discover_integrations_ranked(terms: Sequence[Any], *,
                                 limit: int = 10) -> list[tuple[float, list[str], dict[str, Any]]]:
    wanted = normalize_terms(terms)
    corpus, idf = _integration_corpus()
    ranked = rank(corpus, wanted, idf)
    # Home Assistant first: it runs on the owner's own network and nothing
    # extra is installed. MCP entries would need a code sandbox.
    ranked.sort(key=lambda item: (item[2]["source_catalog"] != "ha_integrations", -item[0]))
    return ranked[: max(0, int(limit))]


def discover_integrations(terms: Sequence[Any], *, limit: int = 10) -> list[dict[str, Any]]:
    """Integration Candidates (Home Assistant, then MCP Registry). Offline."""
    return [cand for _s, _m, cand in discover_integrations_ranked(terms, limit=limit)]


# ---------------------------------------------------------------------------
# optional model re-rank: ids in, ids out
# ---------------------------------------------------------------------------

def rerank(candidates: list[dict[str, Any]], goal: str,
           ask: Callable[[str], str], *, keep: int = 5) -> list[dict[str, Any]]:
    """Let a model reorder candidates this code already found. Never add one.

    The prompt carries ids with short cleaned names only. The reply is read for
    ids, and any id not in ``candidates`` is ignored, so the model can neither
    invent a website nor smuggle one in. Candidates the model skipped keep
    their code order after the ones it chose. A failed call returns the code
    order unchanged.
    """
    if not candidates:
        return []
    by_id = {c["id"]: c for c in candidates}
    lines = [f"- {c['id']}: {clean_text(c['name'], 60)}" for c in candidates]
    prompt = (
        "Pick the entries that best fit this goal, best first. Reply with ids "
        "only, one per line, copied exactly from the list. Do not add anything "
        "that is not in the list.\n"
        f"Goal: {clean_text(goal, 200)}\nList:\n" + "\n".join(lines)
    )
    try:
        reply = str(ask(prompt) or "")
    except Exception as exc:  # pragma: no cover - model down
        logger.info("rerank skipped: %s", exc)
        return list(candidates)
    chosen: list[dict[str, Any]] = []
    for token in re.findall(r"[A-Za-z0-9_.:/@-]+", reply):
        token = token.strip(".-:/")
        if token in by_id and by_id[token] not in chosen:
            chosen.append(by_id[token])
        if len(chosen) >= keep:
            break
    rest = [c for c in candidates if c not in chosen]
    return chosen + rest


# ---------------------------------------------------------------------------
# devices named in a goal
# ---------------------------------------------------------------------------

_DEVICE_VERBS = ("works with", "work with", "connect to", "connect with", "connects to",
                 "hook up to", "hooks up to", "talk to", "talks to", "control", "controls",
                 "plays", "play", "run", "runs", "use with", "linked to", "link to",
                 # So "... and turns on the Hue lights" gives a Hue line.
                 "turns on", "turn on", "turns off", "turn off", "switches on",
                 "switch on", "switches off", "switch off",
                 # So "a bot that dims the Hue lights" gives a line.
                 "dims", "dim", "brightens", "brighten", "turns up", "turn up",
                 "turns down", "turn down")
_POSSESSIVE = ("my", "our")
_PHRASE_STOP = frozenset("""and or but in on at for to with that which who so because while
when where if then than as by from of into running is are was be the a an""".split())
_DEVICE_CUES = frozenset(("smart", "device", "app", "gadget", "account", "wifi", "wireless"))
_SPLIT = re.compile(r"[,.;:!?()\[\]\"/]|\s[-–—]\s|\s[-–—]|[-–—]\s|[ \t]*[\r\n]+[ \t]*")


#: Words that end a device phrase without being part of it: "turn the Sonos
#: down", "switch the Hue lights off".
_PHRASE_END = frozenset(("down", "up", "off", "out", "again", "tonight", "later"))


def _maker_domains() -> frozenset[str]:
    """Home Assistant setup names that are a maker's own name ("hue" of
    Philips Hue, "sonos"): a word she types in lower case that IS one of
    these names her maker as surely as a capital does ("dims the hue
    lights", "my hue lamps"). Only connections
    whose own name carries a word the spelling list does not know as an
    ordinary word ("Philips"), so "ring" or "august" typed in lower case
    stay ordinary words."""
    corpus, _idf_map = _integration_corpus()
    key = ("maker_domains", id(corpus))
    got = _corpus_cache.get("maker_domains")
    if got and got[0] == key:
        return got[1]
    from puffpai.catalogs import personal_words
    out: set[str] = set()
    for cand in corpus:
        if cand.get("source_catalog") != "ha_integrations":
            continue
        domain = ha_domain(cand)
        if not domain or not domain.isalpha():
            continue
        if any(personal_words.dict_kind(w) != "common" for w in _whole_name(cand)):
            out.add(domain)
    value = frozenset(out)
    _corpus_cache["maker_domains"] = (key, value)
    return value


def _is_person_word(token: str) -> bool:
    """A relative or other person word ("friend", "sister", "grandson"), or
    the people a bot is for when they are hers ("our family", "my kids")."""
    from puffpai.catalogs import personal_words
    w = re.sub(r"[^\w'-]", "", token.replace("’", "'")).lower()
    return bool(w) and (personal_words._kin(w) or w in personal_words.AUDIENCE)


def _personal_owner(token: str) -> bool:
    """A person's possessive in front of their thing: "daughter's", "Jake's"."""
    t = re.sub(r"[^\w'-]", "", token.replace("’", "'"))
    if not (t.lower().endswith("'s") or (t.endswith("'") and len(t) > 2)):
        return False
    return _is_person_word(t[:-2] if t.lower().endswith("'s") else t[:-1]) or t[:1].isupper()


def device_phrases(goal: str) -> list[dict[str, Any]]:
    """Things the owner says they HAVE, with how sure we are they are devices.

    Pure wording, no topic lists. A phrase is kept when it follows "my"/"our"
    or a verb like "works with" / "plays". It counts as device-like (``cue``)
    when that verb introduced it, or it carries a word such as "smart" or
    "app", a model year, or a capitalised brand-style name. Phrases without a
    cue ("my vegetable beds") are kept only so a real integration match can
    still surface; they never produce a "not found" line.
    """
    text = str(goal or "")[:500]
    # A list she types one thing a line, under a heading or with bullets
    # ("lights:" then "- Philips Hue", "- LIFX"): read on its own below.
    text, line_items = _line_list_items(text)
    places = place_words(text)
    out: list[dict[str, Any]] = []
    from puffpai.catalogs import personal_words
    # Each clause with the mark that ends it, so a list of her things can go
    # on after a comma ("my Hue lights, Sonos speakers and Ring doorbell").
    clauses: list[tuple[str, str]] = []
    last = 0
    for m in _SPLIT.finditer(text):
        # A line break lists like a comma ("my Hue lights" + line break +
        # "Sonos speakers").
        mark_ = m.group(0).strip()[:1] or ("," if re.search(r"[\r\n]", m.group(0)) else "")
        clauses.append((text[last:m.start()], mark_))
        last = m.end()
    clauses.append((text[last:], ""))
    carry: Optional[bool] = None     # ownership carried over a comma, if any
    #: A list of makers' names after "for"/"with" ("a bot for Hue, Sonos and
    #: Ring"): each name in it is read as strictly as "the <Maker>".
    carry_strict = False
    #: After a full stop the list goes on only with "Also"/"Plus"/"And"
    #: ("a bot for our lights. Also Ring doorbell").
    carry_needs_word = False
    for clause, mark in clauses:
        tokens = clause.split()
        lower = [t.lower() for t in tokens]
        i = 0
        #: Where a list goes on after "and"/"or"/"&" ("my Philips Hue lights
        #: and Ring doorbell"), and whose it is.
        go_on: Optional[int] = None
        go_on_owned = False
        go_on_strict = False
        if carry is not None and tokens:
            first = 0
            while first < min(2, len(tokens)) and lower[first] in _LIST_GOES_ON:
                first += 1
            said_more = first > 0
            # "And the Ecobee thermostat": past "the"/"a" too; the phrase
            # loop below skips them itself.
            while said_more and first < min(4, len(tokens)) \
                    and lower[first] in ("the", "a", "an"):
                first += 1
            if carry_needs_word and not said_more:
                first = len(tokens)          # a new sentence, not her list
            if (first < len(tokens) and lower[first] not in _PHRASE_STOP
                    and lower[first] not in _POSSESSIVE
                    and lower[first] not in personal_words.PRONOUNS
                    and not any(lower[first] == v.split()[0] for v in _DEVICE_VERBS)):
                go_on, go_on_owned, go_on_strict = first, carry, carry_strict
                i = first
        carry = None
        ended_at = -1
        while i < len(tokens):
            start = None
            cue = False
            if go_on is not None and i == go_on:
                start = i
            for verb in (() if start is not None else _DEVICE_VERBS):
                parts = verb.split()
                if lower[i:i + len(parts)] == parts:
                    start = i + len(parts)
                    cue = True
                    break
            if start is None and lower[i] in _POSSESSIVE:
                start = i + 1
            # "mutes the Sonos", "locks the August lock", "turn the Sonos
            # down": a maker's name after "the", whatever the verb. Such a
            # phrase gives a line only when a connection matches it; a name
            # that matches nothing ("the Smiths") gives none.
            article = False
            if start is None and lower[i] == "the" and i + 1 < len(tokens) and (
                    (tokens[i + 1][:1].isupper() and lower[i + 1] not in _PHRASE_STOP)
                    or re.sub(r"[^a-z0-9]", "", lower[i + 1]) in _maker_domains()):
                start = i + 1
                article = True
            strict = go_on is not None and i == go_on and go_on_strict
            if start is None and lower[i] in ("for", "with") and i + 1 < len(tokens) \
                    and tokens[i + 1][:1].isupper() and lower[i + 1] not in _PHRASE_STOP \
                    and lower[i + 1] not in _POSSESSIVE:
                start = i + 1
                strict = True
            if start is None:
                i += 1
                continue
            j = start
            owned = lower[i] in _POSSESSIVE or (go_on is not None and i == go_on and go_on_owned)
            go_on = None
            while j < len(tokens) and lower[j] in ("my", "our", "the", "a", "an", "your"):
                owned = owned or lower[j] in _POSSESSIVE
                j += 1
            # A person is never a device. "my daughter's 2019 Toyota
            # Corolla" is the Corolla ("daughter's" is skipped), and "my friend
            # Dana" is not a thing at all (no line, so no name on the plan).
            while j < len(tokens) and _personal_owner(tokens[j]):
                j += 1
            if j < len(tokens) and _is_person_word(tokens[j]):
                i = max(j, i + 1)
                continue
            phrase: list[str] = []
            while j < len(tokens) and len(phrase) < 4:
                word = tokens[j]
                low = lower[j]
                if low in _PHRASE_STOP or (low.endswith("ing") and len(low) > 5) or (
                        phrase and low in _PHRASE_END):
                    break
                phrase.append(word)
                j += 1
            if (phrase and j + 1 < len(tokens) and lower[j] in ("and", "or", "&")
                    and lower[j + 1] not in _PHRASE_STOP and lower[j + 1] not in _POSSESSIVE
                    and lower[j + 1] not in personal_words.PRONOUNS
                    and not any(lower[j + 1] == v.split()[0] for v in _DEVICE_VERBS)):
                go_on, go_on_owned, go_on_strict = j + 1, owned, strict
            if phrase:
                has_year = any(re.fullmatch(r"(19|20)\d\d", w) for w in phrase)
                # The phrase always follows "my"/"our"/a verb, so a capital
                # letter here is a name (a brand or model), not a sentence start.
                capital = any(w[:1].isupper() for w in phrase)
                proper = has_year or capital
                cue = cue or proper or any(w.lower() in _DEVICE_CUES for w in phrase)
                label = " ".join(phrase)
                core = [stem(w) for w in (w.lower() for w in phrase)
                        if not re.fullmatch(r"\d+", w) and w not in _DEVICE_CUES and w not in STOPWORDS
                        and stem(w) not in places]
                # Her maker or model words: written with a capital here, or
                # not an everyday word ("roomba"). A connection must carry
                # one of them to be hers (else "our Ecobee thermostat" gets
                # the eQ-3 on the shared "thermostat").
                brand: list[str] = []
                for w in phrase:
                    low = w.lower()
                    st = stem(low)
                    if st not in core or low in _DEVICE_CUES or re.fullmatch(r"\d+", low):
                        continue
                    plain = re.sub(r"[^a-z0-9]", "", low)
                    if low in _NAME_FILLER or st in _NAME_FILLER:
                        continue     # "Google Home": "home" names no maker
                    if (w[:1].isupper() or personal_words.dict_kind(plain) != "common"
                            or plain in _maker_domains()):
                        if st not in brand:
                            brand.append(st)
                # A maker's name typed with a hyphen ("TP-Link") is also the
                # name joined ("tplink"), as connections write it (else
                # "TP-Link Kasa plugs" takes Emulated Kasa).
                for w in phrase:
                    st = stem(w.lower())
                    joined = re.sub(r"[^a-z0-9]", "", w.lower())
                    if "-" in w and st in brand and joined and joined not in brand:
                        brand.append(joined)
                        if joined not in core:
                            core.append(joined)
                # A maker's name she typed in lower case ("my roomba") says
                # it is a device as much as a capital does.
                cue = cue or bool(brand)
                if (article or strict) and not brand:
                    phrase = []
                if phrase and core and all(p["label"].lower() != label.lower() for p in out):
                    out.append({"label": clean_text(label, 40), "words": core, "cue": cue,
                                "proper": proper, "owned": owned, "brand": brand,
                                "article": article, "strict": strict})
                ended_at = j
            i = go_on if go_on is not None else max(j, i + 1)
        if mark in (",", ".", ";", "/") and tokens and ended_at == len(tokens) and out:
            carry = bool(out[-1].get("owned"))
            carry_strict = bool(out[-1].get("strict"))
            # A semicolon or a slash lists like a comma ("Hue lights; Sonos
            # speakers", "Hue lights / Sonos speakers"); a full stop needs
            # "Also"/"Plus"/"And".
            carry_needs_word = mark not in (",", ";", "/")
    for phrase in line_items:
        if all(p["label"].lower() != phrase["label"].lower() for p in out):
            out.append(phrase)
    return out


#: A bullet or a number at the start of a line of a list ("- ", "* ", "1. ").
_BULLET_RE = re.compile(r"^[ \t]*(?:[-*•·‣◦▪+]|\d{1,2}[.)])[ \t]+")
#: Words in a list's heading that say its lines are her gadgets.
_LIST_HEAD_CUES = frozenset(("device", "devices", "gadget", "gadgets", "smart", "my", "our"))


def _line_list_items(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Her things listed one a line ("lights:" then "- Philips Hue",
    "- LIFX", "- Wyze cam", or "my devices:" then "Hue", "Sonos", "Ring").

    A list is the lines under a heading that ends with a colon, or two or
    more lines that start with a bullet. It is a list of her devices only on
    evidence: at least two of its lines name a maker a connection carries
    by a word that is not an everyday word ("Sonos", "LIFX", "Philips"), or
    one when the heading says "my"/"our"/"devices". Then every line is read
    as "my <line>", so each thing on it gets its line (a connection, or the
    plain "no ready way" line). A shopping list ("Apples", "Bread") is left
    to the usual reading. Returns the text without a device list's lines,
    and the phrases of those lines."""
    lines = re.split(r"\r\n|\r|\n", text)
    blocks: list[tuple[int, int, str]] = []      # (first line, end, heading)
    # A heading and its list on one line ("my devices: Hue, Sonos, Ecobee",
    # "my devices: Hue / Sonos / Ecobee"): its things, split at commas,
    # slashes, semicolons and "and".
    inline: list[tuple[int, list[str], str]] = []
    for n_line, line in enumerate(lines):
        head, colon, rest = line.partition(":")
        if colon and head.strip() and rest.strip():
            things = [x.strip() for x in re.split(r"\s*(?:[,;/&]|\band\b)\s*", rest)
                      if x.strip()]
            if len(things) >= 2:
                inline.append((n_line, things, head))
    k = 0
    while k < len(lines):
        head = lines[k].rstrip()
        if head.endswith(":") and k + 1 < len(lines) and lines[k + 1].strip():
            m = k + 1
            while m < len(lines) and lines[m].strip() and not lines[m].rstrip().endswith(":"):
                m += 1
            blocks.append((k + 1, m, head[:-1]))
            k = m
            continue
        if _BULLET_RE.match(lines[k]):
            m = k
            while m < len(lines) and _BULLET_RE.match(lines[m]):
                m += 1
            if m - k >= 2:
                blocks.append((k, m, ""))
                k = m
                continue
        k += 1
    if not blocks and not inline:
        return text, []
    from puffpai.catalogs import personal_words
    makers = _maker_domains()
    drop: set[int] = set()
    found: list[dict[str, Any]] = []
    groups = [(list(range(first, end)), lines[first:end], head) for first, end, head in blocks]
    groups += [([n_line], things, head) for n_line, things, head in inline]
    for rows, items, head in groups:
        head_words = {w.lower() for w in re.findall(r"[A-Za-z]+", head)}
        phrases: list[dict[str, Any]] = []
        strong = 0
        for line in items:
            item = _BULLET_RE.sub("", line).strip().rstrip(",;.")
            if not item or len(item.split()) > 4:
                continue
            got = device_phrases("my " + item)
            if not got:
                continue
            phrase = got[0]
            phrases.append(phrase)
            plain = {re.sub(r"[^a-z0-9]", "", w.lower()) for w in item.split()}
            maker_said = {w for w in plain if w and (
                w in makers or (w.isalpha() and personal_words.dict_kind(w) != "common"))}
            if maker_said and _device_lookup(phrase)[0] == "match":
                strong += 1
        need = 1 if head_words & _LIST_HEAD_CUES else 2
        if strong >= need and phrases:
            drop |= set(rows)
            found.extend(phrases)
    if not found:
        return text, []
    rest = "\n".join(line for n, line in enumerate(lines) if n not in drop)
    return rest, found


#: Words that carry her list on into the next part ("and", "Also", "Plus").
_LIST_GOES_ON = frozenset(("and", "or", "&", "also", "plus"))


#: Words in an integration's name that say nothing about which thing it is
#: for ("Generic Thermostat", "Smart Plug").
_NAME_FILLER = frozenset(("generic", "home", "assistant", "integration", "the", "and",
                          "for", "of", "by", "but", "with")) | _DEVICE_CUES


def _whole_name(cand: dict[str, Any]) -> set[str]:
    """The words of an integration's own name that say what it is. Only
    "smart" of the device words is dropped: "Google Wifi" is not just
    "google" (else "my Google Home" takes Google Wifi)."""
    drop = (_NAME_FILLER - _DEVICE_CUES) | {"smart"}
    return {w for w in words(cand.get("name")) if len(w) > 1 and not w.isdigit()
            and w not in STOPWORDS and w not in drop}


def _device_lookup(phrase: dict[str, Any], *, whole_name: bool = False
                   ) -> tuple[str, Optional[tuple[dict[str, Any], list[str]]]]:
    """Find the Home Assistant connection her words name.

    Returns ``("match", (integration, hit words))``, ``("none", None)`` when
    no connection is hers by the rules below, or ``("unsure", None)`` when
    several could be hers and her words do not say which (a guess is never
    shown).

    So that "our Ecobee thermostat" and "our Nest thermostat" are not given
    the eQ-3 Bluetooth thermostat on the shared word "thermostat", and "our
    thermostat" does not always get the eQ-3 guess:

    - When she names a maker or model (``phrase["brand"]``), a connection
      counts only when its own name carries one of those words.
    - When she names none, a connection counts only when her words hold its
      whole name; an ordinary word shared with a maker's connection
      ("thermostat") is never enough.
    - The best has the most of her maker words, then her words hold its
      whole name, then the most shared words; a tie between different
      connections is "unsure".

    With *whole_name* (nothing says the thing is a device: "our office"),
    only a whole-name match counts."""
    corpus, _idf_map = _integration_corpus()
    brand = set(phrase.get("brand") or [])
    said = set(phrase["words"]) | set(words(phrase.get("label")))
    scored: list[tuple[tuple[int, int, int], dict[str, Any], list[str]]] = []
    for cand in corpus:
        if cand.get("source_catalog") != "ha_integrations":
            continue
        name_words = set(words(cand.get("name"))) | set(cand.get("match_terms") or [])
        hit = [w for w in phrase["words"] if w in name_words]
        if not hit:
            continue
        core = _whole_name(cand)
        whole = bool(core) and core <= said
        brand_hits = len(brand & set(hit))
        if whole_name and not whole:
            continue
        if brand and not brand_hits:
            continue
        if not brand and not whole:
            continue
        scored.append(((brand_hits, 1 if whole else 0, len(hit)), cand, hit))
    if not scored:
        return "none", None
    scored.sort(key=lambda row: row[0], reverse=True)
    top = scored[0][0]
    tied = [(c, h) for k, c, h in scored if k == top]
    if len({ha_domain(c) for c, _h in tied}) > 1:
        # "my Hue lights" ties Philips Hue, Hue BLE and Emulated Hue: the
        # one connection whose own setup
        # name IS the maker word she typed ("hue"), and whose name holds
        # nothing else but maker names ("Philips"), is hers. A name with
        # an everyday word she did not say ("Google Calendar" for "my
        # Google Home") is not. Otherwise no guess.
        from puffpai.catalogs import personal_words
        own = [(c, h) for c, h in tied if ha_domain(c) in brand
               and all(personal_words.dict_kind(w) != "common"
                       for w in _whole_name(c) - said)]
        if len({ha_domain(c) for c, _h in own}) == 1:
            return "match", own[0]
        return "unsure", None
    return "match", (scored[0][1], scored[0][2])


def ha_domain(cand: dict[str, Any]) -> str:
    """The connection's setup name (its id when that cannot be read)."""
    from puffpai.catalogs import ha_integrations
    try:
        return ha_integrations.setup_domain(cand)
    except Exception:  # noqa: BLE001 - only used to tell two rows apart
        return str(cand.get("id") or "")


def _shares_a_word(phrase: dict[str, Any]) -> bool:
    """Does any connection's own name carry one of her words?"""
    corpus, _idf_map = _integration_corpus()
    said = set(phrase.get("words") or [])
    return any(cand.get("source_catalog") == "ha_integrations"
               and said & (set(words(cand.get("name"))) | set(cand.get("match_terms") or []))
               for cand in corpus)


def _names_the_thing(phrase: dict[str, Any], hit: list[str]) -> bool:
    """Did the match hit the phrase's last word, the thing itself?"""
    words_ = phrase.get("words") or []
    return bool(words_) and words_[-1] in hit


# ---------------------------------------------------------------------------
# proposal items for the wizard's one summary
# ---------------------------------------------------------------------------

MAX_LIVE_ITEMS = 5
MAX_LIVE_DEFAULT_ON = 3


def _terms_note(cand: dict[str, Any]) -> str:
    lic = clean_text(cand.get("license"), 90)
    stated = bool(lic) and not lic.lower().startswith(("not stated", "terms not stated"))
    if cand.get("commercial_ok") is False:
        return "For home use only, not for business use."
    if stated:
        return f"Terms: {lic}."
    return "Its terms of use are not stated; check before relying on it."


def _live_item(cand: dict[str, Any], default_on: bool) -> dict[str, Any]:
    # Only a service's own host is "where questions go". A documentation
    # page's host is not, so an unread directory row says it does not know.
    host = cand.get("host")
    if cand.get("source_catalog") == "api_seeds":
        gives = clean_text(cand.get("what_it_is"), 140) or clean_text(cand.get("name"), 80)
        parts = [gives if gives.endswith((".", "!", "?", "...")) else gives + "."]
    else:
        said = clean_text(cand.get("what_it_is"), 100)
        listed = catalog_label(cand.get("source_catalog", ""))
        parts = [f"Found in the {listed}." + (f' It describes itself as: "{said}"' if said else "")]
    if host:
        if "home_location" in (cand.get("needs") or []):
            parts.append(f"What leaves your computer: each question goes to {host}, "
                         "with your town's rough location (never your address).")
        elif "zip_code" in (cand.get("needs") or []):
            parts.append(f"What leaves your computer: each question goes to {host}, "
                         "with your ZIP code.")
        else:
            parts.append(f"What leaves your computer: each question goes to {host}.")
    else:
        parts.append("Where its questions would go is not known until we look it up.")
    if cand.get("auth") == "none":
        parts.append("No sign-up needed.")
    elif "key" in (cand.get("needs") or []) or cand.get("auth") in ("free_key", "paid", "unknown"):
        parts.append("It may need a sign-up key, which this bot cannot store yet.")
    parts.append(_terms_note(cand))
    if not cand.get("verified_at"):
        parts.append("We have not checked that it still works.")
    label = f"Live info from {clean_text(cand.get('name'), 60)}"
    return {
        "id": f"live:{cand['source_catalog']}:{cand['id'].split(':')[-1]}",
        "section": "live_info",
        "label": label[:80],
        "detail": " ".join(parts),
        "size_bytes": None,
        "outside_host": host,
        "default_on": bool(default_on),
        "needs_yes": True,
        "apply": {"kind": "live_source", "ref": cand["id"]},
    }


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "thing"


def _device_item(phrase: dict[str, Any], match: Optional[tuple[dict[str, Any], list[str]]]) -> dict[str, Any]:
    thing = phrase["label"]
    if match is None:
        return {
            "id": f"device:none:{_slug(thing)}",
            "section": "devices",
            "label": f"Your {thing}: no ready way to connect found yet"[:80],
            "detail": ("We looked and did not find a ready way to connect it. "
                       "You can add it later."),
            "size_bytes": None,
            "outside_host": None,
            "default_on": False,
            "needs_yes": False,
            "apply": {"kind": "none", "ref": ""},
        }
    cand, _hit = match
    name = clean_text(cand.get("name"), 40)
    if cand.get("local_or_cloud") == "local":
        how = "works on your home network"
        leaves = "It talks only to your device, on your home network."
        host = None
    elif cand.get("local_or_cloud") == "cloud":
        how = "goes through the maker's website"
        leaves = f"Home Assistant talks to {name}'s online service, so your device's information goes to them."
        host = None
    else:
        how = "we could not tell if it stays at home"
        leaves = "We could not tell whether it stays on your home network."
        host = None
    # An "if": most people have no hub. The connection's own name is never
    # cut off (else the line could show only her brand while ticking another
    # maker's connection); her words are shortened instead.
    room = 80 - len(f"Your , if you have Home Assistant ({name})")
    if len(thing) > room:
        cut = thing[:max(room, 0)].rsplit(" ", 1)[0].strip() if room > 0 else ""
        thing = cut or "device"
    label = f"Your {thing}, if you have Home Assistant ({name})"
    from puffpai.catalogs import ha_integrations
    domain = ha_integrations.setup_domain(cand)
    return {
        "id": f"device:ha:{domain}",
        "section": "devices",
        "label": label[:80],
        "detail": (f"Home Assistant's {name} connection {how}. {leaves} "
                   "This way only works if you have Home Assistant, a smart "
                   "home hub; skip it if you don't."),
        "size_bytes": None,
        "outside_host": host,
        "default_on": False,
        "needs_yes": cand.get("local_or_cloud") != "local",
        "apply": {"kind": "ha_integration", "ref": domain},
    }


def proposal_items(goal: str, terms: Optional[Sequence[Any]] = None) -> list[dict[str, Any]]:
    """ProposalItems for "live_info" and "devices". Offline; evidence or silence.

    Live info: the best matching websites, at most five. Up to three are
    pre-ticked, and only ones from our hand-checked list whose last check
    passed; everything from a directory we have not checked stays unticked.
    Devices: one line per thing the goal says the owner has. A line appears
    when Home Assistant has a matching connection, or when the wording clearly
    names a device and we found nothing (then it says so). No match, no line.
    """
    wanted = normalize_terms(terms or []) or terms_from_goal(goal)
    phrases = device_phrases(goal)
    # A brand or model name ("2014 Honda Civic") names the owner's thing, so
    # it is matched against connections below, not searched as a topic.
    names = {w for p in phrases if p.get("proper") for w in p["words"]}
    topic = [w for w in wanted if w not in names]
    items: list[dict[str, Any]] = []
    on = 0
    ranked = discover_live_ranked(topic, limit=MAX_LIVE_ITEMS * 4)
    for score, matched, cand in ranked:
        if len(items) >= MAX_LIVE_ITEMS:
            break
        if cand["source_catalog"] != "api_seeds" and len(matched) < 2:
            continue  # one shared word is not evidence for an unchecked directory row
        # Our own checked list gets no pass (else NASA pictures show up for a
        # wedding). Every line must fit the goal's words, and a seed is
        # pre-ticked only when it does.
        if not fits_goal(goal, cand, matched):
            continue
        tick = (cand["source_catalog"] == "api_seeds" and bool(cand.get("verified_at"))
                and cand.get("auth") == "none" and on < MAX_LIVE_DEFAULT_ON)
        on += 1 if tick else 0
        items.append(_live_item(cand, tick))
    seen: set[str] = set()
    for phrase in phrases:
        # Nothing says it is a device ("our office", "my thermostat"): a
        # connection counts only when her words name it whole, never one
        # ordinary word of a maker's name.
        status, match = _device_lookup(phrase, whole_name=not phrase["cue"])
        if status == "unsure" or (match is None and not phrase.get("brand")
                                  and _shares_a_word(phrase)):
            # Several makers' connections could be hers ("our thermostat"),
            # and her words do not say which: no guess, and no "no ready way"
            # line either, since there may well be one.
            continue
        # Her words hold a maker's whole name of two words or more, typed in
        # lower case ("our google nest thermostat"): that names her device
        # as surely as capitals do.
        whole = _whole_name(match[0]) if match is not None else set()
        if len(whole) >= 2 and whole <= (set(phrase["words"])
                                         | set(words(phrase.get("label")))):
            phrase = dict(phrase, cue=True)
        if match is not None and not phrase["cue"] and not _names_the_thing(
                phrase, match[1]):
            # "our weather patterns" is not a gadget: a word in front of the
            # thing ("weather") matching a connection's name ("Ambient Radio
            # Weather Network") says nothing about what the owner has.
            match = None
        # Not "Epic Games Store" for "play practice games": a phrase
        # only a verb made device-like ("play", "run") is a device only when
        # an integration names ALL of it; alone it never makes a line.
        # A maker's name she typed ("the hue lights") says it is her device
        # as much as a capital does.
        verb_only = (not phrase.get("proper") and not phrase.get("owned")
                     and not phrase.get("brand") and not any(
                         str(w).lower() in _DEVICE_CUES
                         for w in str(phrase.get("label") or "").split()))
        if match is not None and verb_only and set(phrase["words"]) - set(match[1]):
            match = None
        if match is not None and phrase.get("strict"):
            # A maker's name in a list after "for"/"with": the connection's
            # setup name is a word she typed, and its own name holds nothing
            # else but makers' names ("Philips Hue" for "Hue"), never an
            # everyday word she did not say ("Google Calendar" for "Google").
            typed = set(phrase.get("brand") or []) | {
                re.sub(r"[^a-z0-9]", "", w.lower()) for w in str(phrase.get("label") or "").split()}
            said_words = set(phrase["words"]) | set(words(phrase.get("label")))
            from puffpai.catalogs import personal_words
            if ha_domain(match[0]) not in typed or any(
                    personal_words.dict_kind(w) == "common"
                    for w in _whole_name(match[0]) - said_words):
                match = None
        if match is not None and (phrase.get("strict") or phrase.get("article")):
            # A capital word of hers that the connection's name does not
            # carry names some other thing ("Ring Pop" candy, not the Ring
            # doorbell).
            name_has = (set(words(match[0].get("name"))) | set(match[0].get("match_terms") or [])
                        | {ha_domain(match[0])})
            if any(w[:1].isupper() and stem(w.lower()) not in name_has
                   and re.sub(r"[^a-z0-9]", "", w.lower()) not in name_has
                   for w in str(phrase.get("label") or "").split()):
                match = None
        if match is not None and phrase.get("article"):
            # Only after "the" ("mutes the Sonos"): the connection's own
            # setup name must be a word she typed, never a near name ("the
            # Smiths" is not A. O. Smith).
            typed = set(phrase.get("brand") or []) | {
                re.sub(r"[^a-z0-9]", "", w.lower()) for w in str(phrase.get("label") or "").split()}
            if ha_domain(match[0]) not in typed:
                match = None
        if match is None and (not phrase["cue"] or verb_only or phrase.get("article")
                              or phrase.get("strict")):
            continue
        item = _device_item(phrase, match)
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        items.append(item)
    return items


# ---------------------------------------------------------------------------
# refresh: the only path that goes online
# ---------------------------------------------------------------------------

def refresh(catalog_id: str, *, out_dir: Optional[str] = None,
            fetch: Optional[Callable[..., FetchResult]] = None,
            progress: Callable[[str], None] = lambda _m: None) -> dict[str, Any]:
    """Re-read one outside list and write a dated snapshot. Online.

    The caller (the CLI) has already shown the owner which service is asked
    and got a yes. Returns ``{"ok", "catalog", "path", "count", "message"}``.
    """
    import importlib
    if catalog_id not in CATALOG_IDS:
        return {"ok": False, "catalog": catalog_id, "path": None, "count": 0,
                "message": f"There is no list called '{catalog_id}'."}
    adapter = importlib.import_module(f"puffpai.catalogs.{catalog_id}")
    refusal = refresh_refusal()
    if refusal:
        return {"ok": False, "catalog": catalog_id, "path": None, "count": 0, "message": refusal}
    target = out_dir or state_snapshot_dir()
    try:
        return adapter.refresh(out_dir=target, fetch=fetch or fetch_url, progress=progress)
    except CatalogFetchError as exc:
        return {"ok": False, "catalog": catalog_id, "path": None, "count": 0, "message": str(exc)}


def _seed_file_status() -> Optional[dict[str, Any]]:
    """The shipped checked list, when no newer check was saved on this computer.

    Its date is the newest day a row was checked; a list with no checked rows
    has no date, so it is never shown as checked.
    """
    from puffpai.catalogs import api_seeds
    rows = api_seeds.load_rows()
    if not rows:
        return None
    days = sorted(str(r.get("verified_at") or "")[:10] for r in rows if r.get("verified_at"))
    if not days:
        return None
    return {"date": days[-1], "count": len(rows), "complete": True,
            "_path": os.path.abspath(api_seeds.seed_path())}


def snapshot_status() -> list[dict[str, Any]]:
    """One line per list: which snapshot is in use, how old, how many entries."""
    rows = index_rows()
    out = []
    for cid in CATALOG_IDS:
        snap = None
        if cid == "api_seeds":
            snap = latest_snapshot("api_seeds_check") or _seed_file_status()
        else:
            snap = latest_snapshot(cid)
        out.append({
            "catalog": cid,
            "label": str((rows.get(cid) or {}).get("label") or cid),
            "date": (snap or {}).get("date"),
            "count": (snap or {}).get("count"),
            "complete": (snap or {}).get("complete", True) if snap else None,
            "where": ("your computer" if snap and snap.get("_path", "").startswith(
                os.path.abspath(state_snapshot_dir())) else ("shipped" if snap else None)),
        })
    return out


# ---------------------------------------------------------------------------
# CLI: puffpai catalogs refresh|search|status
# ---------------------------------------------------------------------------

def _yes(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _cli_details(cands: list[dict[str, Any]], *, assume_yes: bool = False) -> None:
    """Read the APIs.guru description of each shortlisted service. Online, asked first."""
    if not cands:
        print("\nNothing found here has a description to read.")
        return
    refusal = refresh_refusal()
    if refusal:
        print("\n" + refusal)
        return
    print(f"\nTo tell whether these need a sign-up key, this asks api.apis.guru for "
          f"{len(cands)} short description(s). Only the requests leave your computer.")
    if not assume_yes and not _yes("Go ahead? [y/N] "):
        print("Nothing was sent.")
        return
    from puffpai.catalogs import apis_guru
    for cand in cands:
        try:
            got = apis_guru.enrich(cand)
        except CatalogFetchError as exc:
            print(f"  - {cand['name']}: could not read it. {exc}")
            continue
        key = "no sign-up key" if got["auth"] == "none" else "may need a sign-up key"
        if got.get("host"):
            where = f"questions go to {got['host']}"
        else:
            where = "it does not give a secure (https) address, so this bot will not use it"
        sample = got.get("sample_endpoint")
        test = f"a test address: {sample} (not tried yet)" if sample else \
            "no test address could be made without your own details"
        print(f"  - {got['name']}: {key}; {where}; {test}")


def cli_main(args: Any) -> int:
    import sys
    try:  # outside names can carry any character; a console must not crash on one
        sys.stdout.reconfigure(errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    action = getattr(args, "catalogs_action", "status")
    if action == "status":
        print("Lists this bot looks through for live information and connections:")
        for row in snapshot_status():
            if row["date"]:
                part = "" if row["complete"] is not False else " (only part of the list)"
                verb = "checked" if row["catalog"] == "api_seeds" else "saved"
                print(f"  {row['label']}: {row['count']:,} entries, {verb} {row['date']}"
                      f" ({row['where']}){part}")
            else:
                print(f"  {row['label']}: not saved on this computer yet")
        return 0

    if action == "search":
        words_in = list(getattr(args, "words", None) or [])
        goal = getattr(args, "goal", None) or " ".join(words_in)
        wanted = normalize_terms(words_in) or terms_from_goal(goal)
        if not wanted:
            print("Tell me what the bot is for, for example: puffpai catalogs search dinosaurs fossils")
            return 2
        print(f"Looking for: {', '.join(wanted)}  (in the lists on this computer)")
        live = discover_live_ranked(wanted, limit=getattr(args, "limit", 5) or 5)
        print("\nWebsites with live information:")
        if not live:
            print("  none found")
        for score, matched, cand in live:
            host = cand.get("host") or host_of(cand.get("url")) or "no web address listed"
            key = "no sign-up" if cand["auth"] == "none" else "may need a sign-up key"
            checked = f"checked {cand['verified_at'][:10]}" if cand.get("verified_at") else "not checked by us"
            print(f"  - {cand['name']} ({host}): {clean_text(cand['what_it_is'], 110)}")
            print(f"      {key}; {checked}; matched {', '.join(matched)}; from {catalog_label(cand['source_catalog'])}")
        conns = discover_integrations_ranked(wanted, limit=getattr(args, "limit", 5) or 5)
        print("\nWays to connect things:")
        if not conns:
            print("  none found")
        for score, matched, cand in conns:
            if "code_sandbox" in (cand.get("needs") or []):
                where = "a program that would run on your computer; this bot cannot run outside programs safely yet"
            elif cand.get("source_catalog") == "mcp_registry" and cand.get("local_or_cloud") == "cloud":
                where = f"sends your data to {cand.get('host') or 'its maker'}"
            else:
                where = {"local": "stays on your home network",
                         "cloud": "goes through an online service"}.get(
                             cand.get("local_or_cloud") or "", "not sure where it runs")
            said = cand["what_it_is"]
            if cand.get("source_catalog") == "mcp_registry":
                # The line below already says where it runs; show the list's own words.
                from puffpai.catalogs import mcp_registry
                said = mcp_registry.own_description(said) or said
            print(f"  - {cand['name']}: {clean_text(said, 110)}")
            print(f"      {where}; matched {', '.join(matched)}; from {catalog_label(cand['source_catalog'])}")
        if goal:
            devices = [i for i in proposal_items(goal, wanted) if i["section"] == "devices"]
            if devices:
                print("\nThings you said you have:")
                for item in devices:
                    print(f"  - {item['label']}")
        if getattr(args, "details", False):
            _cli_details([c for _s, _m, c in live if c["source_catalog"] == "apis_guru"],
                         assume_yes=bool(getattr(args, "yes", False)))
        return 0

    if action == "refresh":
        wanted = list(getattr(args, "which", None) or []) or list(CATALOG_IDS)
        rows = index_rows()
        unknown = [w for w in wanted if w not in CATALOG_IDS]
        if unknown:
            print(f"There is no list called: {', '.join(unknown)}. "
                  f"The lists are: {', '.join(CATALOG_IDS)}")
            return 2
        refusal = refresh_refusal()
        if refusal:
            print(refusal)
            return 1
        print("This goes online to update these lists:")
        for cid in wanted:
            row = rows.get(cid) or {}
            if cid == "api_seeds":
                from puffpai.catalogs import api_seeds
                count = len(api_seeds.load_rows())
                print(f"  - {row.get('label') or cid}: asks each of its {count} websites "
                      "one small question, to check it still works")
                continue
            hosts = ", ".join(row.get("search_hosts") or [])
            if not hosts:
                print(f"  - {row.get('label') or cid}: no address on file, so it will be skipped")
                continue
            print(f"  - {row.get('label') or cid}: asks {hosts}")
        print("Only the requests themselves leave your computer. Nothing about you is sent.")
        if not getattr(args, "yes", False) and not _yes("Go ahead? [y/N] "):
            print("Nothing was sent.")
            return 1
        out_dir = getattr(args, "out", None)
        status = 0
        for cid in wanted:
            result = refresh(cid, out_dir=out_dir, progress=lambda m: print("   ", m))
            if result.get("ok"):
                print(f"  {catalog_label(cid)}: saved {result['count']:,} entries -> {result['path']}")
                if result.get("message"):
                    print(f"    {result['message']}")
            else:
                status = 1
                print(f"  {catalog_label(cid)}: not updated. {result.get('message')}")
        return status

    print("Use: puffpai catalogs status | search <words> | refresh [list ...]")
    return 2


# ---------------------------------------------------------------------------
# HTTP: the discovery route's body (offline)
# ---------------------------------------------------------------------------

MAX_GOAL_CHARS = 500
MAX_TERMS = 20
MAX_TERM_CHARS = 40


def discover_payload(goal: Any, terms: Any) -> dict[str, Any]:
    """What ``GET /catalogs/discover`` returns. Validates its inputs; offline."""
    goal_text = clean_text(goal, MAX_GOAL_CHARS) if goal else ""
    raw_terms: list[str] = []
    if isinstance(terms, str):
        raw_terms = [t for t in terms.split(",")]
    elif isinstance(terms, (list, tuple)):
        raw_terms = [str(t) for t in terms]
    raw_terms = [clean_text(t, MAX_TERM_CHARS) for t in raw_terms[:MAX_TERMS]]
    wanted = normalize_terms(raw_terms) or terms_from_goal(goal_text)
    return {
        "terms": wanted,
        "live": discover_live(wanted, limit=10),
        "integrations": discover_integrations(wanted, limit=10),
        "proposal_items": proposal_items(goal_text, wanted) if goal_text else [],
        "lists": snapshot_status(),
    }
