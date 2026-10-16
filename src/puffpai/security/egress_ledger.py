"""The outside-traffic log: one line for every request BuildBot makes.

Ported in spirit from HomeBotBuild ``security/egress_ledger.py`` (``safe_parts``,
the size cap, the never-break-a-request rule), narrowed for BuildBot:

* **Where:** ``<state>/logs/outside.jsonl``, created owner-only.
* **When it is written:** at every exit that reaches outside this computer --
  ``egress_doors.egress_request`` (every door, live card, card picture and town
  lookup), ``egress_doors.build_probe_get`` (the sample look while building),
  ``source_fetch`` (book library searches, book lookups and downloads, per hop),
  ``catalogs.live.fetch_url`` (refreshing the lists of free services) and
  ``wizard.netcheck`` (the internet check). Refusals are written too.
* **What a line holds:** time, service id, host, method, bytes in, bytes out,
  outcome (``done`` / ``refused`` / ``failed``), a one-word reason code from a
  closed list, the HTTP status class, and a ``what`` key from a closed list
  (:data:`WHAT`). Nothing else.

What is NEVER in a line
-----------------------
* **No query string, no fragment, no user:password.** :func:`safe_host` and
  :func:`safe_path` throw them away before a record is built.
* **A path only where the path carries no owner value.** The rule for every
  service BuildBot adds is: owner values (a town, a topic, a date the owner
  picked) go in the QUERY, never the path (``catalogs.api_seeds`` refuses a
  seed row that breaks it). Even so, the exits that can carry an address the
  owner typed or chose -- a news feed, a web page, a book, a live card -- pass
  ``path=""`` and only the host is kept. The path is kept only for fixed,
  code-written addresses (the weather service, the list refreshes).
* **No key in a path, not even part of one.** :func:`clean_path` checks the
  whole path and every segment for a key BEFORE a long path is cut, drops
  the path if any of them looks like one, and cuts only at a ``/`` (the
  segment the limit falls inside is dropped whole).
* **No bodies, headers or keys.** ``bytes_out`` / ``bytes_in`` are lengths.

Keeping it bounded
------------------
Lines older than :data:`RETENTION_DAYS` (30) are dropped, and the file never
grows past :data:`MAX_BYTES`: when it would, the oldest lines go first. The
prune rewrites through a temp file and ``os.replace`` (retried a few times,
because Windows refuses a replace while another process has the file open).

Failure policy
--------------
Writing the log must never break a request: a write that fails is reported
once and then skipped. The decision to allow or refuse was already made and
enforced before this module is reached; losing a line loses evidence, not
enforcement.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import unquote, urlsplit

from puffpai import fsutil
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

LOG_NAME = "outside.jsonl"
SCHEMA = "puffpai_outside_log/v1"

#: How long a line is kept.
RETENTION_DAYS = 30

#: Hard size ceiling for the whole log. A service hammering the network costs
#: the oldest lines, never the disk.
MAX_BYTES = 8 * 1024 * 1024

#: At most this often (seconds) the age-based prune runs on append.
PRUNE_EVERY_S = 3600

#: Reader ceiling, so one request cannot page the whole file into memory.
MAX_READ = 2000

OUTCOMES = ("done", "refused", "failed")

#: The one-word reasons a refusal or failure may carry. Closed on purpose: a
#: free-text reason can hold a host or an address the owner typed.
REASON_CODES = frozenset({
    "", "switched_off", "bot_offline", "not_on_list", "inside_address",
    "cannot_look_up", "peer_moved", "not_encrypted", "redirected_away",
    "too_many_redirects", "sign_in_refused", "not_allowed", "no_answer",
    "bad_answer", "too_big", "robots_rule", "turned_off_here",
    # security.lan_guard: a call marked "home network" dialled the internet,
    # or a proxy that could carry it there.
    "not_home_network", "through_proxy",
    # source_fetch.fetch_small / get_archive: a download that was not
    # the exact version pinned, a host not on the caller's list, too many
    # redirects, a request header outside the short allowlist, a sign-up key
    # asked to go to a host it is not bound to, a key that could not be used.
    "sha_mismatch", "host", "redirects", "header_refused", "key_host",
    "key_unavailable",
    # a home DNS filter blocked the name (pinned_net.filter_block),
    # and the Bridge stopped something the owner had not switched on
    # (security.bridge_guard), or a proxy on this computer it was going
    # through while the Bridge is up.
    "network_filter", "bridge_up", "bridge_proxy",
})

#: What a request was for. Closed vocabulary: the log stores the KEY, the
#: owner's view renders the words. A caller cannot put its own text here.
WHAT: dict[str, str] = {
    "forecast": "asked for the weather forecast",
    "air_quality": "asked for air quality",
    "headlines": "asked for news headlines",
    "web_page": "read a web page",
    "town_lookup": "looked up a town name",
    "card_data": "asked for new data for a live card",
    "card_picture": "fetched a picture for a live card",
    "sample_look": "took one sample look at a service",
    "library_search": "searched the book library",
    "book_lookup": "looked up where to download a book",
    "book_download": "downloaded a book",
    "book_text": "asked for a list of books or the pages of one",
    "robots_rules": "read the site's rules for robots",
    "list_refresh": "updated a list of free services",
    "internet_check": "checked that the internet answers",
    "web_search": "searched the web",  # never the search words
    # finding and adding outside apps, code and pictures
    "code_search": "short search words",
    "picture_search": "short search words",
    "code_download": "a project you picked",
    "picture_download": "pictures you picked",
    "box_install": "code parts while building",
    "box_traffic": "what an added tool sends",
    "app_traffic": "what an added app sends",
    "app_parts": "parts an app loads, saved once",
    "request": "made a request",
    # a tool server she added (puffpai.mcp_client)
    "mcp_tools": "asked a tool server which tools it has",
    "mcp_call": "used a tool from a tool server",
    # the skill and tool shelves BuildBot
    # searches, and the skill files it downloads when she says yes.
    "shelf_search": "short search words, or the name of a skill you picked",
    # her own GitHub account (never the token, never a project's files)
    "github_account": "your GitHub account or the list of your projects",
    # Toolsmith's cloud model: an app's README, file list, commands and output
    "toolsmith_install": "Toolsmith install of an app",
}

#: The service ids ``source_fetch.fetch_small`` / ``get_archive``
#: accept, each with the :data:`WHAT` key its lines carry. Closed, and kept in
#: the security package: the keyed hop (``egress_doors.keyed_first_hop``)
#: checks the owner's switch and writes its line from this table, so a caller
#: cannot log under a name of its own choosing or dodge a switch.
R3_SERVICES: dict[str, str] = {
    "code_search": "code_search",
    "picture_search": "picture_search",
    "code_download": "code_download",
    "picture_download": "picture_download",
    "app_parts": "app_parts",
    "box_install": "box_install",
    "reading_helper": "request",
    # one switch per shelf (outside_services.SHELF_ROWS).
    "shelf_clawhub": "shelf_search",
    "shelf_skills_sh": "shelf_search",
    "shelf_hermes": "shelf_search",
    "shelf_mcp_registry": "shelf_search",
    # her own GitHub account (puffpai.github_account). The token
    # rides only the keyed first hop; the switch is the "github" row.
    "github": "github_account",
}

_SERVICE_RE = re.compile(r"^[a-z0-9][a-z0-9_:\-]{0,63}$")

#: Service ids BuildBot's own code makes in a fixed shape, which the key
#: filter would otherwise judge "random-looking" and drop (a whole line lost,
#: fail-open for the log): a locked-box project or an app's outside site
#: (``outside_switches.switch_id_box`` / ``switch_id_app``: 24 hex), and a
#: browser app's own outside traffic (``app:<app id>:web``, the id shape
#: ``integrate.records`` issues). Only these exact shapes skip the key check;
#: they carry a hash or an issued id, never a value someone typed.
_MADE_SERVICE_RE = re.compile(
    r"^(?:(?:app|box)_[0-9a-f]{24}|app:[a-z0-9][a-z0-9-]{0,29}-[a-z2-7]{8}:web)\Z")
_HOST_RE = re.compile(r"^[a-z0-9.\-:\[\]]{1,253}$")
_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "DELETE", "PATCH",
                      "CONNECT"})
_MAX_PATH = 120

_lock = threading.Lock()
_state: dict[str, Any] = {"last_prune": 0.0, "reported": set()}

#: Tests and an operator may point the log elsewhere.
_PATH_OVERRIDE: Optional[str] = None


def set_path_override(path: Optional[str]) -> None:
    global _PATH_OVERRIDE
    with _lock:
        _PATH_OVERRIDE = path
        _state["last_prune"] = 0.0


def log_path() -> str:
    """``<state>/logs/outside.jsonl``. Resolved on every call, never at import."""
    if _PATH_OVERRIDE:
        return _PATH_OVERRIDE
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), "logs", LOG_NAME)


# ── sanitising ───────────────────────────────────────────────────────────────

def safe_host(url_or_host: Any) -> str:
    """The lower-case host of a URL (or a bare host), or '' if unusable."""
    text = str(url_or_host or "").strip()
    if not text:
        return ""
    if "://" in text:
        try:
            host = urlsplit(text).hostname or ""
        except ValueError:
            return ""
    else:
        host = text.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
        host = host.rsplit("@", 1)[-1]
    host = host.strip().rstrip(".").lower()
    return host if _HOST_RE.match(host) else ""


def safe_path(url: Any) -> str:
    """The path of a URL with query, fragment and credentials dropped, as
    :func:`clean_path` keeps it."""
    text = str(url or "").strip()
    if "://" not in text:
        return ""
    try:
        path = urlsplit(text).path or ""
    except ValueError:
        return ""
    return clean_path(path)


def _path_looks_like_key(path: str) -> bool:
    """True when the whole path, or any one of its segments, looks like a key
    -- as written and with %-escapes undone."""
    texts = [path]
    try:
        plain = unquote(path)
    except Exception:  # fail closed: an undecodable path is not kept
        return True
    if plain != path:
        texts.append(plain)
    for text in texts:
        if _looks_like_key(text):
            return True
        if any(_looks_like_key(seg) for seg in text.split("/") if seg):
            return True
    return False


def clean_path(path: Any) -> str:
    """A path as a log line may keep it, else ''.

    Query, fragment and ``;`` parameters are dropped. Then the key check runs
    on the WHOLE path and on every segment BEFORE anything is cut: a path in
    which any of them looks like a key is not kept at all. Only then is a
    path longer than :data:`_MAX_PATH` cut, and only at a ``/``: the segment
    the limit falls inside is dropped whole, never kept in part (a part of a
    key is too short to look like one, so it would pass any later check).
    """
    text = str(path or "")
    for sep in ("?", "#", ";"):
        text = text.split(sep, 1)[0]
    if not text or not text.startswith("/"):
        return ""
    if _path_looks_like_key(text):
        return ""
    if len(text) > _MAX_PATH:
        # The last '/' inside the limit; the path starts with '/', so >= 0.
        text = text[:text.rfind("/", 0, _MAX_PATH) + 1]
        if _path_looks_like_key(text):
            return ""
    return text


def _int_or_none(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _status_class(status: Any) -> str:
    number = _int_or_none(status)
    if number is None or not 100 <= number <= 599:
        return ""
    return f"{number // 100}xx"


def _looks_like_key(text: str) -> bool:
    """A service id, host or path that looks like a key or a password is not
    kept (a key some service takes in its address, a name someone chose).
    Fail closed: if the check breaks, it is treated as a key."""
    if not text:
        return False
    try:
        from puffpai import web_search
        return web_search.looks_like_pasted_key(text)
    except Exception:
        return True


def build_record(service: str, *, host: str, outcome: str, method: str = "GET",
                 path: str = "", code: str = "", status: Any = None,
                 bytes_in: Any = None, bytes_out: Any = None,
                 what: str = "request",
                 now: Optional[datetime] = None) -> Optional[dict]:
    """One sanitised line, or None when the inputs are not usable.

    Every field is checked against a shape or a closed list here, so a
    caller that passes something odd produces a smaller line, never a leak.
    """
    sid = str(service or "").strip().lower()
    if not _SERVICE_RE.match(sid) or (
            _looks_like_key(sid) and not _MADE_SERVICE_RE.match(sid)):
        return None
    if outcome not in OUTCOMES:
        return None
    clean_host = safe_host(host)
    if _looks_like_key(clean_host):
        clean_host = ""
    verb = str(method or "GET").strip().upper()
    if verb not in _METHODS:
        verb = "GET"
    kept_path = clean_path(path)
    reason = str(code or "")
    if reason not in REASON_CODES:
        reason = "not_allowed" if outcome == "refused" else "no_answer"
    key = what if what in WHAT else "request"
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "t": stamp.isoformat(timespec="seconds"),
        "service": sid,
        "host": clean_host,
        "path": kept_path,
        "method": verb,
        "outcome": outcome,
        "code": reason,
        "status": _status_class(status),
        "bytes_in": _int_or_none(bytes_in),
        "bytes_out": _int_or_none(bytes_out),
        "what": key,
    }


# ── writing ──────────────────────────────────────────────────────────────────

def _report_once(path: str, exc: BaseException) -> None:
    reported = _state["reported"]
    if path in reported:
        return
    reported.add(path)
    logger.warning("outside-traffic log at %s could not be written (%s); "
                   "requests still run, lines are skipped",
                   path, type(exc).__name__)


def _prune_locked(path: str, now: datetime) -> None:
    """Drop lines past retention, then oldest lines past the size cap."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return
    cutoff = now - timedelta(days=RETENTION_DAYS)
    kept: list[str] = []
    for raw in lines:
        try:
            rec = json.loads(raw)
            when = datetime.fromisoformat(str(rec.get("t")))
        except (ValueError, TypeError, AttributeError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when >= cutoff:
            kept.append(raw if raw.endswith("\n") else raw + "\n")
    # Size cap: keep the newest lines that fit in half the cap, so the next
    # prune is not one line away.
    total = sum(len(k.encode("utf-8")) for k in kept)
    if total > MAX_BYTES:
        budget = MAX_BYTES // 2
        trimmed: list[str] = []
        used = 0
        for raw in reversed(kept):
            size = len(raw.encode("utf-8"))
            if used + size > budget:
                break
            trimmed.append(raw)
            used += size
        kept = list(reversed(trimmed))
    if len(kept) == len(lines):
        return
    fsutil.atomic_write(path, "".join(kept))


def append(record: Optional[dict], *, now: Optional[datetime] = None) -> bool:
    """Append one line built by :func:`build_record`. Never raises."""
    if not isinstance(record, dict):
        return False
    try:
        path = log_path()
    except Exception as exc:  # no state dir: skip, never break a request
        logger.debug("outside-traffic log path unavailable: %s", exc)
        return False
    line = json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n"
    with _lock:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            clock = time.time()
            needs_prune = clock - _state["last_prune"] > PRUNE_EVERY_S
            try:
                needs_prune = needs_prune or os.path.getsize(path) + len(line) > MAX_BYTES
            except OSError:
                pass
            if needs_prune:
                _state["last_prune"] = clock
                try:
                    _prune_locked(path, now or datetime.now(timezone.utc))
                except OSError as exc:
                    logger.debug("outside-traffic log prune skipped: %s", exc)
            # One write() of one whole line on an O_APPEND descriptor: a reader
            # never sees half a line from this process.
            fsutil.atomic_append(path, line)
            return True
        except (OSError, TypeError, ValueError) as exc:
            _report_once(path, exc)
            return False


def record(service: str, **fields: Any) -> bool:
    """Build and append one line. The one call every exit makes. Never raises."""
    try:
        return append(build_record(service, **fields))
    except Exception as exc:  # the log must never break a request
        logger.debug("outside-traffic log record skipped: %s", exc)
        return False


# ── reading ──────────────────────────────────────────────────────────────────

def read(hours: float = 24.0, *, limit: int = 500,
         now: Optional[datetime] = None) -> list[dict]:
    """Lines from the last *hours*, newest first, at most *limit*. Never raises.

    Every line is re-checked through :func:`build_record` on the way out, so a
    hand-edited file cannot put a query string or free text in front of the
    owner.
    """
    try:
        hours = max(0.0, min(float(hours), RETENTION_DAYS * 24.0))
    except (TypeError, ValueError):
        hours = 24.0
    try:
        limit = max(1, min(int(limit), MAX_READ))
    except (TypeError, ValueError):
        limit = 500
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = moment - timedelta(hours=hours)
    try:
        path = log_path()
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except Exception:
        return []
    out: list[dict] = []
    for raw in reversed(lines):
        try:
            rec = json.loads(raw)
            when = datetime.fromisoformat(str(rec.get("t")))
        except (ValueError, TypeError, AttributeError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when < cutoff or when > moment + timedelta(minutes=5):
            continue
        clean = build_record(
            str(rec.get("service") or ""), host=str(rec.get("host") or ""),
            outcome=str(rec.get("outcome") or ""),
            method=str(rec.get("method") or "GET"),
            path=str(rec.get("path") or ""), code=str(rec.get("code") or ""),
            status=None, bytes_in=rec.get("bytes_in"),
            bytes_out=rec.get("bytes_out"), what=str(rec.get("what") or ""),
            now=when)
        if clean is None:
            continue
        status = str(rec.get("status") or "")
        clean["status"] = status if re.match(r"^[1-5]xx$", status) else ""
        out.append(clean)
        if len(out) >= limit:
            break
    return out


def last_used(service: str) -> Optional[dict]:
    """The newest line for *service* in the last day, or None."""
    for rec in read(24.0, limit=MAX_READ):
        if rec["service"] == str(service).lower():
            return rec
    return None


__all__ = ["LOG_NAME", "MAX_BYTES", "OUTCOMES", "REASON_CODES",
           "RETENTION_DAYS", "WHAT", "append", "build_record", "clean_path",
           "last_used", "log_path", "read", "record", "safe_host", "safe_path",
           "set_path_override"]
