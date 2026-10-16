"""Egress doors — the one place that says what a finished BuildBot may reach.

The wizard already asks the owner two questions about the network
(:mod:`puffpai.build_posture`): whether the *wizard* may reach the internet
while building, and what the *finished bot* may reach when it runs. The second
answer — ``sealed``, ``island`` or ``enumerated`` — was recorded in the build
manifest and then nothing at runtime read it. This module is the part that
reads it.

A **door** is one named outbound destination: a fixed host set, a stated
purpose, a plain-English line saying what leaves the box, and a switch that is
**off until the owner turns it on**. Nothing in ``src/puffpai`` may reach the
public internet except through a door, with one deliberate exception noted
below.

The invariants
--------------
1. **Posture first.** A door can only open under ``run_egress: enumerated``.
   Under ``sealed`` or ``island`` every door is shut no matter what any switch
   says, because those postures are the owner's answer to a bigger question and
   a per-door flag must not be able to overrule them.
2. **Default shut.** Every door declares ``default=False``. A missing switch, a
   junk switch value, an unreadable config and an unreadable manifest all
   resolve to *shut*. There is no door that ships open.
3. **No socket before the decision.** :func:`egress_request` resolves the door,
   the posture and the URL's host *before* importing ``requests``. The decision
   is never made after a connection.
4. **Unknown host refused.** The URL's host must be one the door declares. A
   door is a named destination, not a licence to fetch whatever a caller — or a
   redirect, or an LLM — supplies.
5. **Redirects re-validated per hop.** ``requests`` is called with
   ``allow_redirects=False`` and each ``Location`` is re-checked against the
   same rules. A door to ``api.open-meteo.com`` that follows a 302 elsewhere is
   not a door to ``api.open-meteo.com``.
6. **Honest refusal.** A shut door raises :class:`EgressBlocked`, whose
   ``user_message`` is written to be shown to a person verbatim. Callers must
   show it. Inventing the data, or quietly answering from somewhere else, is
   the failure this module exists to make hard to do by accident.

The one exception
-----------------
The wizard's online-sourcing path (:mod:`puffpai.source_fetch`) does **not**
route through here and is not a door. It has a stronger gate of its own: a
first-party, human-reviewed allowlist (``config/source_registry.json``), a
licence shown before a byte moves, per-hop redirect and private-address
validation, and a size ceiling. It is also build-time, not run-time — the
posture axis that governs it is ``build_network``, not ``run_egress``. Routing
it through a runtime door would weaken it, not strengthen it.

LAN destinations are likewise not doors. Ollama, Home Assistant, n8n and a
media server are the ``island`` posture's whole point; this registry is about
the public internet.

Config
------
Switches live in a top-level ``egress_doors`` object in
``config/puffpai_config.json``::

    "egress_doors": {
      "weather": false,
      "air_quality": false,
      "news_rss": false
    }

Resolution order, most authoritative first:

1. ``PUFFPAI_EGRESS_<DOOR>`` in the environment — so an operator can force a
   door shut from a root-owned unit file without racing the JSON the running
   service also reads;
2. the owner's switch in ``<state>/outside_switches.json``
   (:mod:`puffpai.security.outside_switches`, written by the "What leaves
   your computer" page and ``puffpai outside``): a one-tap OFF there beats a
   yes written anywhere below;
3. ``egress_doors.<flag>`` in ``config/puffpai_config.json``;
4. ``posture.egress_switches.<flag>`` in the build manifest — what the owner
   actually picked in the wizard's enumerated table;
5. ``False``.

Every request, every refusal
----------------------------
:func:`egress_request` and :func:`build_probe_get` write one line per request
(and per refusal) to the outside-traffic log
(:mod:`puffpai.security.egress_ledger`), and every socket they open is PINNED
(:mod:`puffpai.security.pinned_net`): the name is resolved once, every answer
must be public, and the connection goes to that address while TLS still checks
the name. The one network call in this module is :func:`_send`.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import sys
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import urljoin, urlsplit

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

#: Config section holding the per-door switches.
CONFIG_SECTION = "egress_doors"

#: The only ``run_egress`` posture under which any door may open.
OPEN_POSTURE = "enumerated"

#: How many redirect hops a door will follow, each re-validated. Three is more
#: than any door here legitimately needs.
MAX_REDIRECTS = 3

#: Default per-request timeout. A door that hangs is a door that stalls the
#: dashboard poll behind it.
DEFAULT_TIMEOUT = 8.0

_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")


class EgressBlocked(RuntimeError):
    """A call was refused before any socket opened.

    ``user_message`` is written to be shown to a person verbatim: it names the
    door and how to open it. The alternative — a generic failure the caller
    papers over with invented data — is the behaviour this module removes.
    """

    def __init__(self, door_id: str, reason: str, user_message: str,
                 code: str = ""):
        super().__init__(f"egress refused [{door_id}]: {reason}")
        self.door_id = door_id
        self.reason = reason
        self.user_message = user_message
        #: One word from ``egress_ledger.REASON_CODES`` ('' = work it out
        #: from ``reason``, see :func:`reason_code`).
        self.code = code


@dataclass(frozen=True)
class Door:
    """One named outbound destination and the terms it opens on."""

    id: str
    #: Exact hostnames, lower-case, no ports. A host not in this tuple is
    #: refused even when the door is open. Empty only for ``dynamic_hosts``.
    hosts: tuple[str, ...]
    #: What the household gets by opening it.
    purpose: str
    #: What leaves the box when it is open, written for a person to read
    #: before deciding — not a protocol diagram.
    data_sent: str
    #: Key under ``egress_doors`` in config, and under
    #: ``posture.egress_switches`` in the manifest.
    config_flag: str
    #: Environment override. Wins over everything, so a unit file can force
    #: a door shut.
    env_var: str
    #: Sentence shown to a person when the door is shut.
    settings_hint: str
    #: ALWAYS False. Present so the invariant is a value a test asserts on
    #: rather than a promise in a comment.
    default: bool = False
    #: True for a door whose host the owner names per request. Host allowlist
    #: checking is impossible for such a door; every other URL rule still
    #: applies and private/loopback addresses are refused.
    dynamic_hosts: bool = False
    #: Manifest ``posture.egress_switches`` key, when it differs from
    #: ``config_flag``. The wizard's table is smaller than this registry.
    posture_switch: Optional[str] = None
    notes: str = ""
    #: Hosts this door reaches for PICTURES ONLY (an app's picture host): a
    #: plain GET whose answer is an image (never SVG), size-capped
    #: (:func:`_picture_answer`). Never a full host.
    picture_hosts: tuple[str, ...] = ()


#: The doors. This tuple is BuildBot's enumerated-egress contract: it is the
#: complete list of public-internet destinations a running build can reach.
#: Adding an outbound call to this codebase means adding a row here;
#: ``tests/test_dashboard_weather_egress_doors.py`` fails the build if a new
#: ``requests``/``urllib``/``httpx`` call appears outside the registry.
DOORS: tuple[Door, ...] = (
    Door(
        id="weather",
        hosts=("api.open-meteo.com",),
        purpose="Daily high/low for the dashboard weather card.",
        data_sent="Approximate latitude and longitude (the coordinates set in "
                  "config, or the neutral default) and nothing else. No name, "
                  "no address, no household data.",
        config_flag="weather",
        env_var="PUFFPAI_EGRESS_WEATHER",
        posture_switch="weather",
        settings_hint="The weather forecast is switched off, so nothing was "
                      "fetched. You can switch it on under \"What leaves "
                      "your computer\".",
    ),
    Door(
        id="air_quality",
        hosts=("air-quality-api.open-meteo.com",),
        purpose="US AQI, pollutants, and the mold/dust/pollen/asthma "
                "breakdown on the weather card.",
        data_sent="Approximate latitude and longitude, and nothing else.",
        config_flag="air_quality",
        env_var="PUFFPAI_EGRESS_AIR_QUALITY",
        # The wizard's table has one 'weather' switch, not two. Opening
        # weather does NOT open air quality: it is a second host learning the
        # house's coordinates, so it gets its own switch here and stays shut
        # until someone names it.
        posture_switch=None,
        settings_hint="Air quality is switched off, so nothing was fetched. "
                      "You can switch it on under \"What leaves your "
                      "computer\".",
        notes="Deliberately not opened by the wizard's 'weather' switch. Same "
              "vendor, different host, and the owner was told about one.",
    ),
    Door(
        id="news_rss",
        hosts=(),
        dynamic_hosts=True,
        purpose="Headlines for the news dashboard widget.",
        data_sent="The request itself, to whichever feed the owner named. The "
                  "publisher learns this box's IP address and that it asked "
                  "for their feed. No query and no household data are sent.",
        config_flag="news_rss",
        env_var="PUFFPAI_EGRESS_NEWS_RSS",
        posture_switch=None,
        settings_hint="News headlines are switched off, so nothing was "
                      "fetched. You can switch them on under \"What leaves "
                      "your computer\".",
        notes="Dynamic hosts on purpose: a news widget takes the feed URL the "
              "owner supplies, so a fixed host allowlist would break the "
              "documented prop. Every other rule still applies — https only, "
              "no credentials in the URL, no literal IPs, no loopback or "
              "private-range hosts, and redirects may not leave the host the "
              "owner named. That is weaker than a named-host door and it is "
              "why this is a separate switch rather than part of any other.",
    ),
    Door(
        id="web_fetch",
        hosts=(),
        dynamic_hosts=True,
        purpose="The Toolsmith's generic page read: fetch one public web page "
                "and hand back its text, so a plan can use a page nobody wrote "
                "an intent for.",
        data_sent="One HTTPS GET to the address the request names. That site "
                  "learns this box's IP address and which page was asked for. "
                  "No household data, no query this build invents, and no "
                  "credential. The Toolsmith holds none.",
        config_flag="web_fetch",
        env_var="PUFFPAI_EGRESS_WEB_FETCH",
        # Never opened by the wizard's egress table. A general-purpose fetch is
        # not something an owner can be said to have chosen by ticking
        # "weather"; it has to be named.
        posture_switch=None,
        settings_hint="Reading web pages is switched off, so nothing was "
                      "fetched. You can switch it on under \"What leaves "
                      "your computer\".",
        notes="The widest door in this registry, and the one with the most "
              "gates in front of it. Dynamic hosts because the whole point is "
              "a page the caller names, so the host allowlist cannot apply; "
              "everything else does, and more. On top of the registry's own "
              "rules (https only, no credentials in the URL, every resolved "
              "address must be globally routable, the peer re-checked, "
              "redirects re-validated and pinned to the host originally "
              "named) the Toolsmith refuses non-allowlisted ports, "
              "single-label and .local/.lan/.internal names, numeric "
              "host labels that are addresses in disguise, non-text content "
              "types and oversize bodies -- all before this door is asked to "
              "open a socket. It is also double-consented: this door is the "
              "household's standing switch, and the request's own "
              "Budget.allow_egress (default False) must ALSO be set.",
    ),
    Door(
        id="geocode",
        hosts=("geocoding-api.open-meteo.com",),
        purpose="Find the rough map position and time zone of the home town "
                "the owner types, once, so daily updates and local weather "
                "know where home is and what time it is there.",
        data_sent="The town name the owner typed, and nothing else. No "
                  "address, no household data. Only a rough position (about "
                  "11 km) is kept from the answer.",
        config_flag="geocode",
        env_var="PUFFPAI_EGRESS_GEOCODE",
        # Not part of the wizard's table: the owner says yes to it when they
        # type a town (`puffpai home set`), which is where it is proposed.
        posture_switch=None,
        settings_hint="Looking up a town is switched off, so nothing was "
                      "sent. You can switch it on under \"What leaves your "
                      "computer\", or type the rough position instead.",
    ),
    # -- books from the trusted catalogs ------------------------------------
    # These three are reached ONLY by the book fetcher, after the owner says
    # yes to one named book (puffpai sources add / the Find more panel / the
    # wizard's summary), through source_fetch.get_small: the same per-hop
    # https, public-address and host checks as a Kiwix download. The running
    # bot never reaches them on its own, so the switch below stays off and is
    # not read by the fetcher; the rows exist so these services are named,
    # with what they are sent, and so their hosts are listed in one place
    # (the catalog modules read their allowed hosts from here).
    Door(
        id="wikipedia_books",
        hosts=("en.wikipedia.org", "simple.wikipedia.org"),
        purpose="Wikipedia articles about this bot's subject, fetched once "
                "and saved on this computer as a book, when you say yes to "
                "that book.",
        data_sent="The book's subject words (for example: knitting, crochet) "
                  "and the names of the articles it reads. Nothing about you "
                  "or your household.",
        config_flag="wikipedia_books",
        env_var="PUFFPAI_EGRESS_WIKIPEDIA_BOOKS",
        posture_switch=None,
        settings_hint="Wikipedia is asked only when you say yes to a Wikipedia "
                      "book on the Find more knowledge panel, or with: "
                      "puffpai sources add <book>.",
        notes="Polite by rule: a named User-Agent, maxlag on every request, "
              "at least one second between requests, at most 300 articles "
              "and 25 MB of text per book.",
    ),
    Door(
        id="gutenberg_books",
        hosts=("gutendex.com", "www.gutenberg.org"),
        purpose="Free public-domain books from Project Gutenberg: a search "
                "when you look for books, and the book's text when you say "
                "yes to one.",
        data_sent="The words you searched for (to gutendex.com) and the "
                  "number of the book you chose (to www.gutenberg.org). "
                  "Nothing about you or your household.",
        config_flag="gutenberg_books",
        env_var="PUFFPAI_EGRESS_GUTENBERG_BOOKS",
        posture_switch=None,
        settings_hint="Project Gutenberg is asked only when you search for "
                      "books or say yes to one on the Find more knowledge "
                      "panel, or with: puffpai sources add <book>.",
        notes="At most 25 MB per book; Gutenberg's own plain-text file only.",
    ),
    Door(
        id="openstax_books",
        hosts=("openstax.org",),
        purpose="Free OpenStax textbooks: their list when you look for books, "
                "and a textbook's pages when you say yes to one.",
        data_sent="Nothing but the requests themselves: which book list and "
                  "which textbook pages are read. Nothing about you or your "
                  "household.",
        config_flag="openstax_books",
        env_var="PUFFPAI_EGRESS_OPENSTAX_BOOKS",
        posture_switch=None,
        settings_hint="OpenStax is asked only when you search for books or say "
                      "yes to a textbook on the Find more knowledge panel, or "
                      "with: puffpai sources add <book>.",
        notes="At most 600 pages and 25 MB of text per textbook, one page a "
              "second.",
    ),
    Door(
        id="wikibooks_books",
        hosts=("en.wikibooks.org",),
        purpose="Free how-to books from Wikibooks: a search when you look for "
                "books, and a book's pages when you say yes to one.",
        data_sent="The words you searched for and the names of the pages of "
                  "the book you chose. Nothing about you or your household.",
        config_flag="wikibooks_books",
        env_var="PUFFPAI_EGRESS_WIKIBOOKS_BOOKS",
        posture_switch=None,
        settings_hint="Wikibooks is asked only when you search for books or say "
                      "yes to one, or with: puffpai sources add <book>.",
        notes="At most 300 pages and 25 MB of text per book, one page a second.",
    ),
    Door(
        id="archive_books",
        hosts=("archive.org",),
        purpose="Old public-domain books scanned by libraries, from the "
                "Internet Archive: a search when you look for books, and the "
                "book's text when you say yes to one.",
        data_sent="The words you searched for and the id of the book you "
                  "chose. Nothing about you or your household.",
        config_flag="archive_books",
        env_var="PUFFPAI_EGRESS_ARCHIVE_BOOKS",
        posture_switch=None,
        settings_hint="The Internet Archive is asked only when you search for "
                      "books or say yes to one, or with: puffpai sources add <book>.",
        notes="Only books marked not in copyright and published long enough "
              "ago; the Archive's own text of the scan, at most 25 MB.",
    ),
    # -- web search when the owner asks -------------------------------------
    # Reached ONLY by puffpai.web_search, and only when the owner's own chat
    # words ask for a search ("search the web for ..."). Never by the model on
    # its own, never on a schedule. Result links are shown, never fetched.
    Door(
        id="web_search",
        hosts=("en.wikipedia.org", "simple.wikipedia.org"),
        purpose="Searching Wikipedia when you ask me to search. No sign-up "
                "needed.",
        data_sent="The words you asked me to search for, after private "
                  "details are taken out, and your internet address. "
                  "Nothing else about you.",
        config_flag="web_search",
        env_var="PUFFPAI_EGRESS_WEB_SEARCH",
        # The wizard's "Web search" line opens the keyless search: it needs
        # no key, so it is the search a new bot can have on its first day.
        posture_switch="web_search",
        settings_hint="Searching Wikipedia is switched off, so I did not "
                      "search. You can switch it on under \"What leaves "
                      "your computer\".",
        notes="Keyless. A named User-Agent on every request, at most 5 "
              "results, at most 200 characters of search words.",
    ),
    Door(
        id="brave_search",
        hosts=("api.search.brave.com",),
        purpose="Searching the whole web with Brave when you ask me to "
                "search. Needs your own free key from Brave.",
        data_sent="The words you asked me to search for, after private "
                  "details are taken out, your key from Brave, and your "
                  "internet address.",
        config_flag="brave_search",
        env_var="PUFFPAI_EGRESS_BRAVE_SEARCH",
        posture_switch=None,
        settings_hint="Searching with Brave is switched off, so I did not "
                      "search with it. Adding your Brave key on the Sign-up "
                      "keys page switches it on.",
        notes="Keyed (security/keys.py): the key rides in a header, only to "
              "this host, never while the vault is locked (Brave asks for a "
              "payment card). Stops at the local monthly cap.",
    ),
    Door(
        id="marginalia_search",
        hosts=("api2.marginalia-search.com",),
        purpose="Searching the web with Marginalia when you ask me to "
                "search. Needs your own free key; free for personal use only.",
        data_sent="The words you asked me to search for, after private "
                  "details are taken out, your key from Marginalia, and your "
                  "internet address.",
        config_flag="marginalia_search",
        env_var="PUFFPAI_EGRESS_MARGINALIA_SEARCH",
        posture_switch=None,
        settings_hint="Searching with Marginalia is switched off, so I did "
                      "not search with it. Adding your Marginalia key on the "
                      "Sign-up keys page switches it on.",
        notes="Keyed (security/keys.py): header only, only to this host.",
    ),
    # -- her own GitHub account ----------------------------------------------
    # Reached ONLY by puffpai.github_account and the GitHub searcher, and only
    # after she connects her account (that tap is her yes to this switch).
    Door(
        id="github",
        hosts=("api.github.com", "github.com"),
        purpose="Your own GitHub account, when you connect it: better searches, "
                "your own projects and the ones you starred as finds, and a "
                "private project of yours when you say yes to it.",
        data_sent="Your GitHub token (only to api.github.com, never in an "
                  "address), the short search words shown on screen, the names "
                  "of your projects, and your internet address. Connecting "
                  "without a token sends a one-time sign-in code request to "
                  "github.com.",
        config_flag="github",
        env_var="PUFFPAI_EGRESS_GITHUB",
        posture_switch=None,
        settings_hint="Your GitHub account is switched off, so nothing was sent "
                      "to GitHub with it. Connecting it again on the Developer "
                      "page switches it on.",
        notes="Keyed (security/keys.py, row 'github', vault only): the token "
              "rides only in a header to api.github.com, only while the vault "
              "is open, never in an address, never on a redirect. github.com "
              "is reached only for the device sign-in, which carries no token.",
    ),
    # Toolsmith's cloud model (Toolsmith is the ONLY
    # agent that may use a cloud model; everything else stays local). Opened
    # only by the owner's own switch from Settings, Toolsmith's model.
    Door(
        id="toolsmith_cloud",
        hosts=("api.anthropic.com",),
        purpose="Toolsmith's cloud model, when you choose Anthropic for it in "
                "Settings: it works out how to install and start an app.",
        data_sent="When Toolsmith installs an app: that app's README, its file "
                  "list, and the commands Toolsmith runs with their output, after "
                  "keys and private details are taken out, with your Anthropic "
                  "key (only to api.anthropic.com) and your internet address. "
                  "Never your files, chats or vault.",
        config_flag="toolsmith_cloud",
        env_var="PUFFPAI_EGRESS_TOOLSMITH_CLOUD",
        posture_switch=None,
        settings_hint="Toolsmith's cloud model is switched off, so nothing was "
                      "sent to Anthropic. Toolsmith uses the model on this "
                      "computer.",
        notes="Keyed (security/keys.py, row 'anthropic', vault only): the key "
              "rides only in a header to api.anthropic.com, only while the vault "
              "is open, never on a redirect. Called only from Toolsmith's "
              "install loop (security/toolsmith_cloud.py scope guard).",
    ),
    Door(
        id="result_pictures",
        hosts=(),
        dynamic_hosts=True,
        purpose="Pictures a data source's own results point to (a news story's "
                "photo, a library's thumbnail), shown in the app her job made.",
        data_sent="One picture request to the address the source's own answer "
                  "named. That site learns this box's internet address and which "
                  "picture was asked for. Nothing she typed, no cookie, no key.",
        config_flag="result_pictures",
        env_var="PUFFPAI_EGRESS_RESULT_PICTURES",
        posture_switch=None,
        settings_hint="Pictures from a source's results are switched off on this "
                      "computer, so none was fetched.",
        notes="Never opened by a switch of its own: only through "
              ":func:`result_picture_get`, which needs the SOURCE's own door "
              "open (her yes to \"<source> and the picture sites its results "
              "point to\") and a host that appeared in that source's results. "
              "GET only, image content types only (no SVG), size-capped, one "
              "audit line per host. Its env var may switch it off, never on.",
    ),
)

#: The picture door: opened only by :func:`result_picture_get`.
RESULT_PICTURES = "result_pictures"
#: The largest picture a source's result may point to.
RESULT_PICTURE_MAX_BYTES = 8 * 1024 * 1024
#: Picture content types let through (never SVG: it can carry script).
RESULT_PICTURE_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp", "image/avif")

_BY_ID: dict[str, Door] = {d.id: d for d in DOORS}


# ── posture + flag resolution ────────────────────────────────────────────────

_lock = threading.Lock()
#: Each cache remembers the file it was read from and that file's stamp, so a
#: change made by ANOTHER process (``puffpai outside on``, ``puffpai services
#: on``, the wizard) is picked up on the next call, in both directions, with
#: no restart. A switch closed elsewhere takes effect on the very next check.
_config_cache: Optional[dict] = None
_posture_cache: Optional[dict] = None
#: A stamp that never matches: the file could not be looked at, so it is read
#: again (and fails closed) every time instead of being cached.
_NO_STAMP = object()


def _file_stamp(path: str) -> Any:
    """``(path, mtime_ns, size, inode)`` of *path*; ``(path, None, None, None)``
    when it does not exist; :data:`_NO_STAMP` when it cannot be looked at."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return (path, None, None, None)
    except OSError:
        return _NO_STAMP
    return (path, st.st_mtime_ns, st.st_size, getattr(st, "st_ino", 0))


def reset_cache() -> None:
    """Forget the cached config and posture (tests, and a reload after edit).

    Not needed for correctness any more: both caches check their file's stamp
    on every call. Kept as the explicit reload hook."""
    global _config_cache, _posture_cache
    with _lock:
        _config_cache = None
        _posture_cache = None
    _reset_app_consent_cache()


def _config_path() -> str:
    from puffpai.config import get_config
    return os.path.join(get_config().CONFIG_DIR, "puffpai_config.json")


def _raw_config() -> dict:
    """The whole config file, cached until the file changes. Never raises.

    Read directly rather than through the config dataclass because unknown keys
    are dropped there, and a door switch must resolve without the loader having
    been taught about it.
    """
    global _config_cache
    try:
        path = _config_path()
    except Exception as exc:  # egress policy must never gate startup
        logger.debug("egress door config unavailable: %s", exc)
        return {}
    stamp = _file_stamp(path)
    with _lock:
        if (_config_cache is not None and stamp is not _NO_STAMP
                and _config_cache.get("stamp") == stamp):
            return _config_cache["value"]
    raw: dict = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            raw = loaded
    except FileNotFoundError:
        raw = {}
    except (OSError, ValueError) as exc:
        # Fail shut: an unreadable config yields no open doors.
        logger.warning("egress door config unreadable (%s) — every door "
                       "stays shut", exc)
    except Exception as exc:  # egress policy must never gate startup
        logger.debug("egress door config unavailable: %s", exc)
    with _lock:
        _config_cache = {"stamp": stamp, "value": raw}
    return raw


def _manifest_stamp() -> Any:
    try:
        from puffpai import build_manifest
        return _file_stamp(build_manifest.manifest_path())
    except Exception:
        return _NO_STAMP


def run_posture() -> dict[str, Any]:
    """This build's run-time egress posture, normalised and fail-closed.

    Reads ``posture`` from the build manifest and puts it through
    :func:`puffpai.build_posture.assess_posture`, which is where the
    normalisation already lives: an unrecognised ``run_egress`` becomes the
    safe default rather than being honoured, and switches only mean anything
    under ``enumerated``. That matters because :func:`build_manifest.read_manifest`
    returns the file's contents unprojected, so a hand-edited manifest could
    otherwise put an arbitrary string where a posture belongs.

    No manifest at all means no wizard has run, which resolves to the same safe
    default (``island``) and therefore to every door shut.

    Cached until the manifest file changes (its stamp is checked on every
    call), so a posture written by another process is in force at once.
    """
    global _posture_cache
    stamp = _manifest_stamp()
    with _lock:
        if (_posture_cache is not None and stamp is not _NO_STAMP
                and _posture_cache.get("stamp") == stamp):
            return _posture_cache["value"]
    from puffpai import build_posture
    block: dict[str, Any] = {}
    try:
        from puffpai import build_manifest
        data = build_manifest.read_manifest()
        if isinstance(data, dict) and isinstance(data.get("posture"), dict):
            block = data["posture"]
    except Exception as exc:
        logger.debug("build manifest unavailable for egress posture: %s", exc)
    switches = block.get("egress_switches")
    enabled = [k for k, v in switches.items() if v is True]         if isinstance(switches, dict) else None
    settled = build_posture.assess_posture(
        block.get("build_network"), block.get("run_egress"), enabled)
    out = {
        "run_egress": settled["run_egress"],
        "source": settled["run_egress_source"],
        "switches": dict(settled["egress_switches"]),
    }
    with _lock:
        _posture_cache = {"stamp": stamp, "value": out}
    return out


def _as_bool(value: Any) -> Optional[bool]:
    """Interpret a config/env value as a switch, or None if it is not one."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
    return None


def door(door_id: Any) -> Optional[Door]:
    """The door with this id, or None. Built-in ids win over app feed ids."""
    key = str(door_id).strip().lower()
    found = _BY_ID.get(key)
    if found is not None:
        return found
    return _app_door_lookup(key)


def door_ids() -> tuple[str, ...]:
    """Every door id, in registry order: built-in, then app feed doors."""
    return tuple(d.id for d in DOORS) + tuple(d.id for d in app_doors())


def _shown(name: Any) -> str:
    """An id someone else chose (an app's, a door's) as a log line may show
    it: plain and key-safe, else "(not shown)". Fail closed."""
    try:
        from puffpai import web_search
        return web_search.log_name(name, placeholder="(not shown)", limit=64)
    except Exception:
        return "(not shown)"


def _owner_switch(door_id: str) -> Optional[bool]:
    """The owner's switch from ``outside_switches.json``, or None. Fails shut."""
    try:
        from puffpai.security import outside_switches
        return outside_switches.get(door_id)
    except Exception as exc:  # unreadable state: shut, never open
        logger.warning("owner switch for %s unreadable (%s); door stays shut",
                       _shown(door_id), type(exc).__name__)
        return False


def _switch(d: Door) -> tuple[Optional[bool], str]:
    """The owner's explicit setting for this door, and where it came from.

    ``None`` means they gave none. A junk value is *not* a setting and is
    reported as such, which for every door means it stays shut.
    """
    if is_app_door(d):
        # An app feed door has its own tiers: the operator's kill switch, the
        # per-door env var, then the owner's recorded yes/no. See
        # :func:`_app_door_switch`.
        return _app_door_switch(d)

    bridge_up = _bridge_up()
    raw_env = os.environ.get(d.env_var)
    if raw_env is not None and raw_env.strip() != "":
        decided = _as_bool(raw_env)
        if decided is None:
            return None, (f"{d.env_var} is not a switch value; door stays shut")
        if decided is False or not bridge_up:
            return decided, f"{d.env_var}={'on' if decided else 'off'}"
        # The Bridge is up: a setting on this computer may switch a service
        # off, never on. Only the owner's own answers below can.

    owner_switch = _owner_switch(d.id)
    if owner_switch is not None:
        return owner_switch, ("the owner switched it "
                              f"{'on' if owner_switch else 'off'}")

    section = _raw_config().get(CONFIG_SECTION)
    if isinstance(section, dict) and d.config_flag in section:
        decided = _as_bool(section.get(d.config_flag))
        if decided is None:
            return None, (f"{CONFIG_SECTION}.{d.config_flag} is not a switch "
                          f"value; door stays shut")
        if decided is False or not bridge_up:
            return decided, f"{CONFIG_SECTION}.{d.config_flag}={decided}"
        # The Bridge is up: a line in the settings file may switch a service
        # off, never on (the same rule as a live card's, _app_door_switch).
        # The owner's own switch above, or her answer in the wizard below
        # (kept in the build record beside the Bridge choice itself), can.

    if d.posture_switch:
        chosen = run_posture()["switches"].get(d.posture_switch)
        if chosen is True:
            return True, f"the wizard's egress table opened '{d.posture_switch}'"

    if bridge_up and isinstance(section, dict) and d.config_flag in section:
        return None, ("the Bridge is up and the owner has not switched it on "
                      f"({CONFIG_SECTION}.{d.config_flag} in the settings file "
                      "cannot)")
    return None, f"{CONFIG_SECTION}.{d.config_flag} is not set"


def _bridge_up() -> bool:
    """True when only what the owner switched on may leave (fails closed)."""
    try:
        from puffpai.security import bridge_guard
        return bridge_guard.only_switched_on()
    except Exception:
        return True


def egress_allowed(door_id: str) -> tuple[bool, str]:
    """Is this door open? Returns ``(allowed, reason)``.

    Fails shut on every ambiguity: an unknown door, a posture that is not
    ``enumerated``, an unreadable config, a junk switch value and a missing
    switch all return ``False``. *reason* is for logs, status and tests; the
    sentence to show a person is :attr:`Door.settings_hint`.
    """
    d = door(door_id)
    if d is None:
        return False, f"no egress door named {str(door_id)!r}"

    posture = run_posture()["run_egress"]
    if posture != OPEN_POSTURE:
        # Posture beats every switch. This is the whole point: an owner who
        # answered "sealed" does not get a door opened by a stray config key.
        return False, (f"egress door {d.id!r} is shut "
                       f"(posture: {posture})")

    decided, why = _switch(d)
    if decided is not True:
        return False, (f"egress door {d.id!r} is shut "
                       f"(posture: {posture}, {why})")
    return True, f"egress door {d.id!r} is open ({why})"


def shut_reason(door_id: str) -> str:
    """The one-line machine-readable reason a door is shut ('' if it is open)."""
    allowed, reason = egress_allowed(door_id)
    return "" if allowed else reason


def settings_hint(door_id: str) -> str:
    """The sentence to show a person when this door is shut."""
    d = door(door_id)
    return d.settings_hint if d is not None else (
        "This bot has no outside service by that name, so nothing was sent.")


def require_open(door_id: str) -> Door:
    """Return the door if it is open, else raise :class:`EgressBlocked`.

    Call this at the top of any outbound path whose sockets this module cannot
    wrap. It opens nothing.
    """
    allowed, reason = egress_allowed(door_id)
    d = door(door_id)
    if not allowed or d is None:
        if d is None:
            code = "not_on_list"
        elif run_posture()["run_egress"] != OPEN_POSTURE:
            code = "bot_offline"
        elif "=off" in reason or "forces every app door shut" in reason:
            code = "turned_off_here"
        else:
            code = "switched_off"
        raise EgressBlocked(
            str(door_id), reason,
            d.settings_hint if d is not None else
            "This bot has no outside service by that name, so nothing was "
            "sent.", code=code)
    return d


#: Doors listed only for their hosts: their one switch is the matching row on
#: What leaves your computer (outside_services), not this table.
SWITCHED_ON_OUTSIDE_PAGE = frozenset({"wikipedia_books", "gutenberg_books",
                                      "openstax_books", "wikibooks_books",
                                      "archive_books"})


def door_states() -> dict[str, Any]:
    """Every door with its current state, for ``/system/status``.

    Reports the shut ones too. A build should be able to say what it did *not*
    open, not only what it did.
    """
    posture = run_posture()
    out: list[dict[str, Any]] = []
    for d in DOORS + app_doors():
        allowed, reason = egress_allowed(d.id)
        if d.id in SWITCHED_ON_OUTSIDE_PAGE:
            # Not a door switch: the book fetcher asks these only for a book
            # the owner said yes to, and their one switch is the row on What
            # leaves your computer (puffpai outside). No dead config flag.
            out.append({
                "id": d.id, "open": allowed, "reason": "switched_elsewhere",
                "hosts": list(d.hosts), "purpose": d.purpose,
                "data_sent": d.data_sent, "config_flag": None,
                "switched_in": "What leaves your computer (puffpai outside)",
                "default": d.default, "enforced": True,
            })
            continue
        out.append({
            "id": d.id,
            "open": allowed,
            "reason": reason,
            "hosts": list(d.hosts) if not d.dynamic_hosts
            else ["(the address the owner supplies)"],
            "purpose": d.purpose,
            "data_sent": d.data_sent,
            "config_flag": f"{CONFIG_SECTION}.{d.config_flag}",
            "default": d.default,
            "enforced": True,
        })
    return {
        "posture": posture["run_egress"],
        "posture_source": posture["source"],
        "open_posture": OPEN_POSTURE,
        "doors": out,
        "note": ("Every door is shut by default and can only be opened on a "
                 "build whose run posture is 'enumerated'. The wizard's "
                 "online-sourcing step is gated separately by "
                 "config/source_registry.json and is not a door."),
    }


# ── URL rules ────────────────────────────────────────────────────────────────

def _port_of(url: str) -> int:
    """The port a URL will connect on. Every door here is https."""
    try:
        return urlsplit(str(url)).port or 443
    except ValueError:
        return 443


def _clean_host(host: Any) -> str:
    """Normalise a hostname for comparison: lower-case, no trailing dot."""
    if not isinstance(host, str):
        return ""
    return host.strip().rstrip(".").lower()


def _internal_reason(addr_text: str) -> str:
    """Why this address is internal, or '' if it is globally routable.

    Judged with :mod:`ipaddress` on the address itself rather than against a
    list of strings, because a blocklist of ``127.0.0.1``/``10.*`` never covers
    ``0.0.0.0``, ``[::1]``, ``::ffff:127.0.0.1`` or the next range someone
    invents. ``is_global`` is the positive test; the named properties exist so
    the refusal can explain itself.
    """
    try:
        addr = ipaddress.ip_address(str(addr_text).split("%", 1)[0])
    except ValueError:
        return ""
    candidates = [addr]
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        candidates.append(mapped)  # ::ffff:127.0.0.1 is loopback in a costume
    for attribute, description in (
            ("is_loopback", "is loopback"),
            ("is_link_local", "is link-local (the cloud-metadata range)"),
            ("is_multicast", "is multicast"),
            ("is_unspecified", "is unspecified"),
            ("is_private", "is a private address on this network"),
            ("is_reserved", "is reserved")):
        if any(getattr(c, attribute, False) for c in candidates):
            return description
    if not all(c.is_global for c in candidates):
        return "is not globally routable"
    # The pinned-connection policy also unwraps 6to4, Teredo and NAT64 forms;
    # one policy for every exit.
    from puffpai.security.pinned_net import address_refusal
    return address_refusal(str(addr))


def internal_address_reason(addr_text: str) -> str:
    """Public name for :func:`_internal_reason`. '' means globally routable.

    Exported so a caller that does its own pre-flight refusal (the Toolsmith's
    ``web_fetch``, which must reject a literal or a resolved answer *before*
    this module is asked to open anything) judges an address by exactly the
    same policy the doors do, rather than growing a second, drifting copy.
    A non-address string returns '' — it is a hostname, not an address, and
    naming it here is a different check.
    """
    return _internal_reason(addr_text)


def resolve_addresses(host: str, port: int = 443) -> list[str]:
    """Every address *host* answers with. Public seam for pre-flight checks."""
    return _default_resolver(host, port)


def _is_private_host(host: str) -> bool:
    """True for a *literal* address that is not globally routable."""
    return bool(_internal_reason(host))


def _default_resolver(host: str, port: int) -> list[str]:
    """Every address *host* answers with. Injected in tests; never called there."""
    infos = socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
    # sockaddr[0] is the address for both AF_INET and AF_INET6; str() is for
    # the type checker, which sees the whole sockaddr union.
    return [str(info[4][0]) for info in infos]


def _check_resolved_public(d: Door, host: str, port: int,
                           resolver=None) -> list[str]:
    """Resolve *host* and refuse unless EVERY answer is globally routable.

    A dynamic-host door takes an address the owner supplied, so it cannot be
    held to a fixed host allowlist. What it can be held to is where that name
    actually points. Checking the *name* alone is not a control: ``feeds.evil``
    can answer ``169.254.169.254``, and a widget URL is reachable by anything
    that can call ``dashboard_add_widget``.

    Every answer must pass, not merely the first: a name that resolves to one
    public and one private address is refused, because the stack picks which
    one the socket gets. Returns the vetted answers: :func:`egress_request`
    PINS the connection to the first of them (:mod:`pinned_net`), so the name
    is never looked up a second time between this check and the connect --
    that second lookup was the DNS-rebinding window.
    """
    try:
        addresses = (resolver or _default_resolver)(host, port)
    except OSError as exc:
        raise EgressBlocked(
            d.id, f"cannot resolve {host!r} ({type(exc).__name__})",
            f"That address could not be looked up, so nothing was sent.")
    if not addresses:
        raise EgressBlocked(
            d.id, f"{host!r} resolves to nothing",
            f"That address could not be looked up, so nothing was sent.")
    from puffpai.security import pinned_net
    if pinned_net.filter_block(addresses, host):
        raise EgressBlocked(
            d.id, f"{host} was blocked by the network's filter",
            "That website is blocked by your network's filter, so nothing "
            "was sent.", code="network_filter")
    for addr in addresses:
        reason = _internal_reason(addr)
        if reason:
            raise EgressBlocked(
                d.id, f"{host} resolves to {addr}, which {reason}",
                f"That request tried to reach {host}, which points at an "
                f"address on this machine or this network, so nothing was "
                f"sent.", code="inside_address")
    return [str(a) for a in addresses]


def _verify_peer(d: Door, response, host: str) -> None:
    """Belt and braces: re-check the peer when the transport exposes it.

    The control that closes DNS rebinding is the PIN (:mod:`pinned_net`): the
    socket is opened to the address :func:`_check_resolved_public` vetted, and
    the pinned connection itself compares the socket's real peer with that
    address before a byte is sent. With urllib3 2.6 ``response.raw`` does not
    expose the socket, so this after-the-fact check usually has nothing to
    look at and returns; it stays for transports (and tests) that do.
    """
    peer = None
    try:
        peer = response.raw._connection.sock.getpeername()[0]
    except Exception:
        peer = getattr(response, "peer_address", None)
    if peer is None:
        return
    reason = _internal_reason(str(peer))
    if reason:
        # The bytes are already on the wire; the body is never handed back.
        raise EgressBlocked(
            d.id, f"connection to {host} landed on {peer}, which {reason}",
            f"That request reached an address on this machine or this "
            f"network, so the reply was discarded.")


def _check_url(d: Door, url: Any, hop_host: str = "") -> str:
    """Refuse anything the door does not cover. Opens nothing. Returns the host.

    ``hop_host`` is set when re-validating a redirect target: for a dynamic
    door the hop must stay on the host the owner originally named.
    """
    if not isinstance(url, str) or not url.strip():
        raise EgressBlocked(d.id, "empty url",
                            "That request had no address to fetch.")
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        raise EgressBlocked(d.id, "unparseable url",
                            "That web address could not be read.")

    host = _clean_host(parts.hostname or "")

    if parts.scheme.lower() != "https":
        # Every door here is an https endpoint. Refusing http outright means a
        # downgrade cannot be introduced by a redirect or a hand-edited config.
        raise EgressBlocked(
            d.id, f"scheme {parts.scheme!r} is not https",
            "That request was refused because it was not an encrypted "
            "connection.")
    if parts.username or parts.password:
        # The reason string must not echo the credentials back into a log.
        raise EgressBlocked(d.id, "url carries credentials",
                            "That web address was refused.")
    if not host:
        raise EgressBlocked(d.id, "url has no host",
                            "That web address was refused.")
    if is_app_door(d) and _port_of(url) != 443:
        # An app's door was declared by a manifest, not reviewed by a human,
        # so it gets the default https port and nothing else.
        raise EgressBlocked(
            d.id, f"port {_port_of(url)} is not 443",
            "That request was refused because it used an unusual port.")

    if d.dynamic_hosts:
        if _is_private_host(host):
            raise EgressBlocked(
                d.id, f"host {host!r} is a private or loopback address",
                f"That request tried to reach {host}, which is an address on "
                f"this machine or this network, so nothing was sent.")
        if hop_host and host != hop_host:
            raise EgressBlocked(
                d.id, f"redirect to {host!r} leaves the requested host "
                      f"{hop_host!r}",
                f"That feed redirected to {host}, which is not where you "
                f"pointed it, so nothing was fetched.")
        return host

    if host not in d.hosts and host not in d.picture_hosts:
        raise EgressBlocked(
            d.id,
            f"host {host!r} is not declared by door {d.id!r}; declared: "
            f"{', '.join(d.hosts)}",
            f"That request tried to reach {host}, which is not one of the "
            f"addresses the {d.id!r} service covers, so nothing was sent.")
    return host


# ── credentials ──────────────────────────────────────────────────────────────
#
# A door says *where* a request may go. Until now it said nothing about *what
# authority rides along*: ``egress_request`` forwarded ``**kwargs`` straight to
# ``requests.request``, so any in-process caller could attach an
# ``Authorization`` header and the door layer would carry it through redirect
# hops without ever knowing a credential was on the wire. The rules below make
# that impossible by default and give a credential exactly one way in.

#: Header names that always carry authority, refused from callers outright.
_CREDENTIAL_HEADERS = frozenset({
    "authorization", "proxy-authorization", "cookie", "set-cookie",
    "x-api-key", "x-auth-token",
})

#: Substrings that make a header name credential-shaped. Deliberately broad:
#: a false refusal on ``X-Monkey-Id`` costs one explicit exception here, a
#: false accept on ``X-Session-Key`` costs a leaked credential.
_CREDENTIAL_SUBSTRINGS = ("auth", "token", "secret", "key", "cookie")

_SECURITY_PACKAGE = __name__.rsplit(".", 1)[0]  # "puffpai.security"

#: The mark :meth:`_BoundCredential.__init__` puts on every credential it
#: builds. An object forged with ``object.__new__`` has no mark, so presenting
#: one to :func:`egress_request` is refused even though its type matches.
#: A mark on the object, NOT a module-wide list of issued credentials: such a
#: list would let any code in the process walk it and read a key while its
#: request is in flight.
_ISSUE_MARK = object()


def _is_credential_header(name: Any) -> bool:
    """True when *name* looks like it carries authority. Errs toward True."""
    if not isinstance(name, str):
        return True  # cannot be screened, so it does not go on the wire
    low = name.strip().lower()
    if low in _CREDENTIAL_HEADERS:
        return True
    return any(part in low for part in _CREDENTIAL_SUBSTRINGS)


def _ascii_host(host: Any) -> str:
    """Host normalised for comparison: lower-case, no trailing dot, IDN→ASCII.

    Both sides of the bound-host check go through this, so a credential bound
    to ``example.com`` is not handed to ``exämple.com`` and vice versa.
    """
    cleaned = _clean_host(host)
    if not cleaned:
        return ""
    try:
        return cleaned.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        # Not an IDN-encodable name (literal IP, underscore label, over-long
        # label). Comparison then happens on the cleaned form, which is the
        # same transformation applied to the bound hosts.
        return cleaned


class _BoundCredential:
    """One header's worth of authority, bound to the hosts it may reach.

    **This class is the single sanctioned way a credential reaches the wire,
    and nothing constructs it today.** The credential broker that will mint
    these does not exist yet; the seam exists now so that when it is written
    there is exactly one entrance, already governed by the rules in
    :func:`egress_request`:

    * TLS only, and the destination host must be one of ``hosts`` — exact
      match, IDN-normalised to ASCII, never a wildcard or a suffix match;
    * zero redirects: any 3xx is a refusal, not a hop, so the header is never
      offered to a target the server chose;
    * the header is attached immediately before the call and stripped from
      ``response.request.headers`` before the response is handed back;
    * the value never appears in a log line, a refusal message or a repr.

    It cannot be constructed from outside ``puffpai.security``: ``__init__``
    checks the calling module. That is a guard against accident and against an
    LLM-driven or plugin call site talking itself into attaching a token — not
    a sandbox. Any code already running in this process can monkeypatch this
    module; the boundary this defends is between the security package and the
    rest of the application, not between the application and an attacker with
    code execution.
    """

    __slots__ = ("_header", "_value", "_hosts", "_mark", "__weakref__")

    def __init__(self, header_name: str, value: str,
                 hosts: "tuple[str, ...] | list[str] | set[str]"):
        caller = sys._getframe(1).f_globals.get("__name__", "")
        if not (caller == _SECURITY_PACKAGE
                or caller.startswith(_SECURITY_PACKAGE + ".")):
            raise PermissionError(
                "_BoundCredential may only be constructed inside "
                f"{_SECURITY_PACKAGE!r}; {caller!r} must ask the security "
                "package for a bound credential instead.")

        if not isinstance(header_name, str) or not header_name.strip():
            raise ValueError("credential header name must be a non-empty string")
        header = header_name.strip()
        if not header.isascii() or any(c in header for c in "\r\n\t :"):
            # A header name carrying a separator is header injection.
            raise ValueError("credential header name is not a valid header name")

        if not isinstance(value, str) or not value:
            # No value in the message: this exception may be logged.
            raise ValueError("credential value must be a non-empty string")
        if "\r" in value or "\n" in value:
            raise ValueError("credential value contains a line break")

        if isinstance(hosts, str) or not hosts:
            raise ValueError("credential hosts must be a non-empty collection")
        bound = set()
        for h in hosts:
            if not isinstance(h, str) or not h.strip():
                raise ValueError("credential host entries must be non-empty strings")
            if "*" in h:
                raise ValueError("credential hosts are exact; no wildcards")
            bound.add(_ascii_host(h))
        if "" in bound:
            raise ValueError("credential host entries must be non-empty strings")

        self._header = header
        self._value = value
        self._hosts = frozenset(bound)
        self._mark = _ISSUE_MARK

    @property
    def header_name(self) -> str:
        return self._header

    @property
    def hosts(self) -> frozenset:
        return self._hosts

    def __repr__(self) -> str:  # never the value
        return (f"<_BoundCredential header={self._header!r} "
                f"hosts={sorted(self._hosts)!r}>")

    __str__ = __repr__


#: Where a bound credential may ride. Header only: a
#: key in a query string is copied into every log, proxy and exception text
#: that ever sees the address, so v1 refuses it outright.
CREDENTIAL_PLACEMENTS = ("header",)


def _bind_credential(header_name: str, value: str, hosts, *,
                     placement: str = "header") -> _BoundCredential:
    """Module-private factory for the credential broker
    (:mod:`puffpai.security.keys`).

    Not exported, and -- like the constructor -- refused to any caller outside
    ``puffpai.security``: without this check the factory itself was a way
    round the constructor's guard, because the constructor only saw this
    module as its caller. *placement* must be ``"header"``; anything else
    (``"query"``) raises before a credential exists.
    """
    caller = sys._getframe(1).f_globals.get("__name__", "")
    if not (caller == _SECURITY_PACKAGE
            or caller.startswith(_SECURITY_PACKAGE + ".")):
        raise PermissionError(
            "_bind_credential may only be called inside "
            f"{_SECURITY_PACKAGE!r}; {caller!r} must use the key broker "
            "(puffpai.security.keys) instead.")
    if placement not in CREDENTIAL_PLACEMENTS:
        # No value in the message: this exception may be logged.
        raise ValueError("a key may only travel in a request header")
    return _BoundCredential(header_name, value, hosts)


def _screen_kwargs(door_id: str, kwargs: dict) -> None:
    """Refuse caller-supplied authority before anything touches the network.

    Refusal messages name the *header*, never its value, because they are
    written to a log and shown to a person.
    """
    if "auth" in kwargs:
        raise EgressBlocked(
            door_id, "caller supplied an auth= argument",
            "That request tried to attach a sign-in to an outbound call, "
            "which this connection does not carry.")
    if kwargs.get("cookies"):
        raise EgressBlocked(
            door_id, "caller supplied cookies=",
            "That request tried to attach a sign-in to an outbound call, "
            "which this connection does not carry.")

    headers = kwargs.get("headers")
    if headers is None:
        return
    if not isinstance(headers, Mapping):
        # Unscreenable, so it does not go out. requests accepts a sequence of
        # pairs; a caller that needs that can pass a dict.
        raise EgressBlocked(
            door_id, f"headers must be a mapping, got {type(headers).__name__}",
            "That request was refused because its headers could not be "
            "checked.")
    for name in headers:
        if _is_credential_header(name):
            shown = name if isinstance(name, str) else type(name).__name__
            raise EgressBlocked(
                door_id, f"caller supplied credential-bearing header {shown!r}",
                "That request tried to attach a sign-in to an outbound call, "
                "which this connection does not carry.")


def _check_credential(door_id: str, credential: Any) -> _BoundCredential:
    """Refuse anything that is not a credential this package issued."""
    if type(credential) is not _BoundCredential \
            or getattr(credential, "_mark", None) is not _ISSUE_MARK:
        raise EgressBlocked(
            door_id, "credential was not issued by the security package",
            "That request tried to attach a sign-in that this system did not "
            "issue, so nothing was sent.")
    return credential


def _strip_credential_header(resp: Any, header_name: str) -> None:
    """Remove the credential from the response's record of the request."""
    try:
        headers = getattr(getattr(resp, "request", None), "headers", None)
        if headers is None:
            return
        for key in [k for k in headers
                    if isinstance(k, str) and k.lower() == header_name.lower()]:
            del headers[key]
    except Exception:  # a stub transport with an odd request object
        logger.debug("could not strip credential header from response.request")


# ── a sign-up key on one source_fetch.fetch_small request ────────────────────
#
# ``source_fetch.fetch_small(..., key_id="github")`` may carry a sign-up key.
# The WHOLE keyed request happens here, inside the security package:
# :func:`keyed_first_hop` checks the owner's switch for the service, reads the
# key, binds it with :func:`_bind_credential` to the hosts the curated row,
# the key index AND the stored record all name (exact, IDN-normalised), opens
# its own PINNED connection (never a caller's transport), sends, reads at most
# the size the caller allows, removes any echo of the key from the body and
# the answer headers, writes the one outside-traffic line for the hop (every
# outcome, refusals included) and hands back a plain answer object that holds
# no credential.
#
# Where the key lives while a request is in flight: ONLY in the
# local variables of the one :func:`keyed_first_hop` call and the request
# objects it builds. No module-level table, list or cache in this package
# holds the key, its header value, the words removed from the answer, or a
# bound credential (the issued-credential list became a mark on the object
# for the same reason). The helpers that touch the value
# (:func:`_fetch_key_credential`, :func:`_credential_header_for`) refuse any
# caller outside the package, the same guard :func:`_bind_credential` has.
#
# Other rules, as ``keys.keyed_get``: the curated row must exist, a key that
# was rejected or is over its caps is not used, a card-backed key never works
# with the vault locked, the key rides ONLY the first hop (a redirect never
# gets it), and one use is counted only when a request is about to go out.
# The header value follows the row's ``placement.value_format`` (default
# ``{key}``; GitHub: ``Bearer {key}``).
#
# Residual, stated plainly:
# * The switch check and the log line are enforced HERE, not by trusting the
#   caller, so they hold even for a caller that gets past the caller check
#   below (that check reads the calling frame, which code running ``exec``
#   with made-up globals can imitate).
# * The service name is the caller's choice, as it is for every fetch_small
#   call: a key is bound to hosts, not to one service. A caller that names a
#   service whose switch is on can spend the key's per-minute and monthly
#   allowance against its bound hosts, and the log shows that service.
# * Python gives code in the same process ways to read another call's local
#   variables (``gc``, ``sys._current_frames``, ``ctypes``). Those, and
#   monkeypatching this module, are out of scope, as for every guard here:
#   the boundary kept is that no ordinary name or function outside
#   ``puffpai.security`` hands out the key.

_FETCH_KEY_DOOR = "fetch_small"
_MAX_VALUE_FORMAT = 64
_KEYED_MAX_READ = 64 * 1024 * 1024
#: The functions outside this package that may send a keyed hop:
#: (module, qualified name) of the closure ``fetch_small`` builds for one
#: request, and the one ``get_archive`` builds for one download (a
#: private project of hers, through GitHub's archive address). Not the module
#: as a whole: a module-level helper there would be a way round the switch
#: check.
_KEYED_HOP_CALLER = ("puffpai.source_fetch", "fetch_small.<locals>._send_keyed")
_KEYED_HOP_CALLERS = frozenset({
    _KEYED_HOP_CALLER,
    ("puffpai.source_fetch", "get_archive.<locals>._send_keyed"),
})
#: The ledger id a keyed hop is logged under when its service is not known
#: (the caller's own text never becomes a service id).
_KEYED_UNKNOWN_SERVICE = "fetch_small"
_KEYED_REDIRECTS = (301, 302, 303, 307, 308)
#: Shortest tail of a key that is still removed when a body is cut mid-key.
_MIN_ECHO_TAIL = 6


def _require_security_caller(what: str) -> None:
    """Refuse when the function that called *what* is outside the package."""
    caller = sys._getframe(2).f_globals.get("__name__", "")
    if not (caller == _SECURITY_PACKAGE
            or caller.startswith(_SECURITY_PACKAGE + ".")):
        raise PermissionError(
            f"{what} may only be called inside {_SECURITY_PACKAGE!r}; "
            f"{caller!r} must use source_fetch.fetch_small(key_id=...) instead.")


def _value_format_ok(fmt: Any) -> bool:
    if not isinstance(fmt, str) or len(fmt) > _MAX_VALUE_FORMAT:
        return False
    if fmt.count("{key}") != 1:
        return False
    rest = fmt.replace("{key}", "")
    return rest.isascii() and all(32 <= ord(ch) < 127 for ch in rest) \
        and "{" not in rest and "}" not in rest


def fetch_key_hosts(key_id: Any) -> Optional[frozenset]:
    """The hosts the curated key row *key_id* may go to, or None (no such
    usable row). Reads no vault and sends nothing."""
    from puffpai.security import keys
    try:
        row = keys.spec(key_id)
    except Exception:
        return None
    if not isinstance(row, dict):
        return None
    hosts = frozenset(_ascii_host(h) for h in (row.get("hosts") or [])
                      if isinstance(h, str))
    return frozenset(h for h in hosts if h) or None


def _fetch_key_credential(key_id: Any, host: Any, *, caller: str,
                          only_target: bool = False
                          ) -> "tuple[Optional[_BoundCredential], str, tuple[bytes, ...]]":
    """``(credential, "ok", echoes)`` for one keyed request to *host*, else
    ``(None, "key_host" | "key_unavailable", ())``. Counts one use when
    issued. *echoes* are the byte strings to remove from the answer (the key
    and its header value); the caller keeps them in a local variable only.

    Package-private: refused (``PermissionError``) to any caller outside
    ``puffpai.security``. With *only_target* the credential is bound to
    *host* alone (a browser app's key: exactly the host it was asked for).
    """
    _require_security_caller("_fetch_key_credential")
    from puffpai.security import keys
    sid = str(key_id or "")
    target = _ascii_host(host)
    try:
        row = keys.spec(sid)
    except Exception:
        row = None
    if not isinstance(row, dict) or not target:
        return None, "key_unavailable", ()
    row_hosts = {_ascii_host(h) for h in (row.get("hosts") or [])
                 if isinstance(h, str)}
    if target not in row_hosts:
        keys._audit("credential_use", caller, "refused", f"{sid} {target} host")  # noqa: SLF001
        return None, "key_host", ()
    placement = row.get("placement") or {}
    fmt = placement.get("value_format", "{key}")
    if not _value_format_ok(fmt) or placement.get("in") != "header":
        return None, "key_unavailable", ()
    try:
        entry = keys._read_index().get(sid)  # noqa: SLF001 - same package
    except Exception:
        entry = None
    if not isinstance(entry, dict) or entry.get("rejected_at"):
        return None, "key_unavailable", ()
    entry_hosts = {_ascii_host(h) for h in (entry.get("hosts") or [])
                   if isinstance(h, str)}
    if target not in entry_hosts:
        keys._audit("credential_use", caller, "refused", f"{sid} {target} host")  # noqa: SLF001
        return None, "key_host", ()
    cap = row.get("monthly_cap")
    if cap is not None:
        used = keys.uses_this_month(sid)
        if used is None or used >= cap:
            return None, "key_unavailable", ()
    vault = keys._vault(None)  # noqa: SLF001
    is_open = keys._is_open(vault)  # noqa: SLF001
    if not is_open and (row.get("card_required")
                        or entry.get("card_required") is not False
                        or not entry.get("background")
                        or row.get("vault_only")):
        keys._audit("credential_use", caller, "refused", f"{sid} {target} locked")  # noqa: SLF001
        return None, "key_unavailable", ()
    record = keys._record_for(vault, sid)  # noqa: SLF001
    raw = None
    try:
        if record is None or (record.get("card_required") is True and not is_open):
            return None, "key_unavailable", ()
        record_hosts = {_ascii_host(h) for h in (record.get("hosts") or [])
                        if isinstance(h, str)}
        if target not in record_hosts:
            keys._audit("credential_use", caller, "refused", f"{sid} {target} host")  # noqa: SLF001
            return None, "key_host", ()
        if not keys._per_minute_ok(sid, row):  # noqa: SLF001
            return None, "key_unavailable", ()
        bound = sorted(row_hosts & entry_hosts & record_hosts)
        if only_target:
            bound = [target] if target in bound else []
        if not bound:
            return None, "key_host", ()
        raw = record.get("value")
        if not isinstance(raw, str) or not raw:
            return None, "key_unavailable", ()
        try:
            cred = _bind_credential(str(placement.get("name") or ""),
                                    fmt.replace("{key}", raw), bound)
        except (ValueError, TypeError):
            return None, "key_unavailable", ()
        keys._count_use(sid)  # noqa: SLF001 - counted before sending
        echoes = tuple({raw.encode("utf-8"),
                        fmt.replace("{key}", raw).encode("utf-8")})
        keys._audit("credential_use", caller, "issued", f"{sid} {target}")  # noqa: SLF001
        return cred, "ok", echoes
    finally:
        record = None
        raw = None


def _credential_header_for(credential: Any, url: Any) -> Optional[tuple[str, str]]:
    """``(header, value)`` to attach for *url*, or None when *url* is not a
    plain https address on one of the credential's own hosts. Raises
    :class:`EgressBlocked` for a credential this package did not issue.

    Package-private: refused (``PermissionError``) to any caller outside
    ``puffpai.security``.
    """
    _require_security_caller("_credential_header_for")
    cred = _check_credential(_FETCH_KEY_DOOR, credential)
    try:
        parts = urlsplit(str(url))
        port = parts.port
    except ValueError:
        return None
    if (parts.scheme or "").lower() != "https" or port not in (None, 443) \
            or parts.username is not None or parts.password is not None \
            or "@" in (parts.netloc or ""):
        return None
    if _ascii_host(parts.hostname or "") not in cred.hosts:
        return None
    return cred.header_name, cred._value  # noqa: SLF001 - the one use


def _credential_scrub(echoes: "tuple[bytes, ...]", data: bytes) -> bytes:
    """*data* with every one of *echoes* replaced, including one cut short at
    the very end of *data*. Returns bytes only."""
    if not isinstance(data, (bytes, bytearray)):
        return data
    out = bytes(data)
    for needle in echoes or ():
        if not needle:
            continue
        if needle in out:
            out = out.replace(needle, b"[removed]")
        # A body read up to a size limit can end part-way through the key.
        for k in range(len(needle) - 1, _MIN_ECHO_TAIL - 1, -1):
            if out.endswith(needle[:k]):
                out = out[:-k] + b"[removed]"
                break
    return out


def _fetch_key_answered(key_id: Any, host: Any, status: Any, *, caller: str) -> None:
    """After a keyed answer: a status the row lists as "rejected" stops the key."""
    from puffpai.security import keys
    sid = str(key_id or "")
    row = keys.spec(sid)
    if not isinstance(row, dict):
        return
    target = _ascii_host(host)
    try:
        code = int(status)
    except (TypeError, ValueError):
        code = 0
    rejected = ((row.get("check") or {}).get("rejected") or [])
    if code in rejected:
        keys._mark_rejected(sid)  # noqa: SLF001
        keys._audit("credential_use", caller, "rejected", f"{sid} {target} {code // 100}xx")  # noqa: SLF001
    else:
        keys._audit("credential_use", caller, "done", f"{sid} {target} {code // 100}xx")  # noqa: SLF001


class KeyedAnswer:
    """What :func:`keyed_first_hop` hands back: a status, the answer headers
    and at most the bytes the caller allowed, every echo of the key removed.
    It holds no credential. ``raw_length`` is how many bytes came off the
    wire before the echo was removed (for the caller's size rule)."""

    __slots__ = ("status", "headers", "raw_length", "_body")

    def __init__(self, status: int, headers: dict, body: bytes, raw_length: int):
        self.status = status
        self.headers = headers
        self.raw_length = raw_length
        self._body = body

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            out, self._body = self._body, b""
        else:
            out, self._body = self._body[:n], self._body[n:]
        return out

    def close(self) -> None:
        self._body = b""


def _keyed_wire(url: str, headers: dict, timeout: float) -> Any:
    """The one network call for a keyed request: pinned (every address the
    name resolves to must be public, and the socket goes to the address that
    was checked while TLS checks the name), no redirects, no proxy, no
    cookies. Never a caller-supplied transport."""
    import urllib.error
    import urllib.request
    from puffpai.security import pinned_net

    class _NoFollow(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            return None

    request = urllib.request.Request(url, headers=headers, method="GET")
    opener = pinned_net.urllib_opener(pinned_net.Pin(), _NoFollow)
    try:
        return opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        return exc


def _keyed_url_ok(url: Any) -> bool:
    """Printable ASCII with no space (as ``source_fetch.url_chars_ok``): the
    HTTP library raises on anything else, and CR/LF could split a request."""
    return isinstance(url, str) and bool(url) and url.isascii() \
        and all(32 < ord(ch) < 127 for ch in url)


def _pin_refused_code(exc: BaseException) -> str:
    """The pinned connection's refusal word inside *exc*, or ''."""
    from puffpai.security.egress_ledger import REASON_CODES
    from puffpai.security.pinned_net import PinRefused
    for candidate in (exc, getattr(exc, "reason", None), exc.__cause__,
                      exc.__context__):
        if isinstance(candidate, PinRefused):
            code = getattr(candidate, "code", "")
            return code if code in REASON_CODES and code else "inside_address"
    return ""


def keyed_first_hop(key_id: Any, url: str, headers: Mapping, *, service: str,
                    max_bytes: int, allow_truncated: bool = False,
                    timeout: float) -> "tuple[Optional[KeyedAnswer], str]":
    """Send ONE GET to *url* carrying the sign-up key *key_id*.

    Returns ``(answer, "ok")`` or ``(None, code)`` with *code* one word from
    ``egress_ledger.REASON_CODES`` (``switched_off``, ``not_allowed``,
    ``sign_in_refused``, ``key_host``, ``key_unavailable``,
    ``inside_address``, ``no_answer``, ``bad_answer``, ``too_big``).

    Checked and written HERE, whoever the caller is: the owner's switch for
    *service* (a known service, else refused), and exactly one
    outside-traffic line for this hop, host only, whatever the outcome. The
    answer never holds the key. The body is at most ``max_bytes + 1`` bytes;
    a longer one is refused (``too_big``) unless *allow_truncated*. A
    redirect answer carries its ``Location`` and no body; the caller follows
    it WITHOUT a key (and logs that next hop itself).

    Only the closure ``source_fetch.fetch_small`` builds for one request may
    call it; any other caller gets ``PermissionError``.
    """
    frame = sys._getframe(1)
    where = (frame.f_globals.get("__name__", ""), frame.f_code.co_qualname)
    frame = None
    if where not in _KEYED_HOP_CALLERS:
        raise PermissionError(
            "keyed_first_hop may only be called by source_fetch.fetch_small "
            "or source_fetch.get_archive; "
            f"{where[0]!r} must use that instead.")
    from puffpai.security import egress_ledger, outside_switches
    svc = service if isinstance(service, str) else ""
    what = egress_ledger.R3_SERVICES.get(svc)
    target = egress_ledger.safe_host(url) if isinstance(url, str) else ""

    def _line(outcome: str, **fields: Any) -> None:
        egress_ledger.record(svc if what else _KEYED_UNKNOWN_SERVICE,
                             host=target, outcome=outcome,
                             what=what or "request", **fields)

    def _refuse(code: str) -> "tuple[None, str]":
        _line("refused", code=code, bytes_in=0, bytes_out=0)
        return None, code

    if what is None:
        return _refuse("not_allowed")
    if outside_switches.is_off(svc):
        return _refuse("switched_off")
    if not _keyed_url_ok(url):
        return _refuse("not_allowed")
    if not isinstance(headers, Mapping) \
            or any(_is_credential_header(n) for n in headers):
        return _refuse("sign_in_refused")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) \
            or max_bytes <= 0:
        return _refuse("not_allowed")
    limit = min(max_bytes + 1, _KEYED_MAX_READ)
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return _refuse("key_host")
    cred, code, echoes = _fetch_key_credential(key_id, host, caller=svc)
    if cred is None:
        return _refuse(code)
    try:
        extra = _credential_header_for(cred, url)
        if extra is None:
            return _refuse("key_host")
        send = {str(k): str(v) for k, v in headers.items()}
        send[extra[0]] = extra[1]
        extra = None
        sent = len(url) + sum(len(k) + len(v) + 4 for k, v in send.items()) + 16
        try:
            resp = _keyed_wire(url, send, timeout)
        except OSError as exc:
            refused = _pin_refused_code(exc)
            if refused:
                return _refuse(refused)
            _line("failed", code="no_answer", bytes_out=sent)
            return None, "no_answer"
        except (http.client.HTTPException, ValueError):
            _line("failed", code="bad_answer", bytes_out=sent)
            return None, "bad_answer"
        finally:
            send = {}
        raw = b""
        failure = ""
        status = 0
        try:
            for attr in ("status", "code"):
                value = getattr(resp, attr, None)
                if isinstance(value, int) and not isinstance(value, bool):
                    status = value
                    break
            answer_headers: dict[str, str] = {}
            declared = ""
            try:
                for name, value in list(getattr(resp, "headers", {}).items()):
                    text = _credential_scrub(echoes, str(value).encode(
                        "utf-8", "replace")).decode("utf-8", "replace")
                    answer_headers.setdefault(str(name), text)
                    if str(name).strip().lower() == "content-length" and not declared:
                        declared = text.strip()
            except Exception:
                answer_headers = {}
            if status in _KEYED_REDIRECTS:
                pass
            elif not allow_truncated and declared.isdigit() \
                    and int(declared) > max_bytes:
                failure = "too_big"
            else:
                try:
                    got = resp.read(limit)
                except (OSError, http.client.HTTPException, ValueError):
                    failure = "no_answer"
                else:
                    raw = bytes(got) if isinstance(got, (bytes, bytearray)) else b""
        finally:
            try:
                resp.close()
            except Exception:
                pass
            resp = None
        _fetch_key_answered(key_id, host, status, caller=svc)
        if failure == "no_answer":
            _line("failed", code="no_answer", status=status, bytes_out=sent)
            return None, "no_answer"
        if failure == "too_big" or (len(raw) > max_bytes and not allow_truncated):
            _line("refused", code="too_big", status=status, bytes_out=sent,
                  bytes_in=len(raw))
            return None, "too_big"
        _line("done", status=status, bytes_out=sent,
              bytes_in=min(len(raw), max_bytes))
        body = _credential_scrub(echoes, raw)
        return KeyedAnswer(status, answer_headers, body, len(raw)), "ok"
    finally:
        cred = None
        echoes = ()


# ── before the bot is set up: one check of a pasted key ─────────────────────
#
# The wizard asks about GitHub right after her goal, before it has recorded
# what the finished bot may reach, so the door cannot open yet (no run
# setting). The build-time channel (fetch_small: the owner's switch and the
# Bridge, no run setting) is what the wizard's own searches use. This is the
# one keyed request that may go out on it with a value not yet stored: the
# curated row's own test address, once, for a row marked
# ``check_before_setup``, under its ``setup_service`` switch, which her yes
# has just switched on. Same rules as the keyed hop: header only, bound to
# that one host, pinned, no redirect, one outside-traffic line, the answer
# discarded (only the status comes back). Only ``puffpai.security.keys`` may
# call it.

def setup_check_hop(key_id: Any, value: str, *, timeout: float = 8.0
                    ) -> "tuple[Optional[int], str, dict]":
    """``(status, "ok", answer)`` or ``(None, code, {})`` for ONE test GET of
    a pasted key before the bot is set up. *answer* is ``{"user": {login,
    name, avatar_url}, "headers": {x-oauth-scopes}}`` from a 200 (the key's
    echo removed), else {}. Refused unless the wizard run says setup is under
    way (``github_account.before_setup``: a positive signal, fail closed)."""
    caller = sys._getframe(1).f_globals.get("__name__", "")
    if caller != _SECURITY_PACKAGE + ".keys":
        raise PermissionError("setup_check_hop may only be called by puffpai.security.keys")
    from puffpai import USER_AGENT
    from puffpai.security import egress_ledger, keys, outside_switches
    row = keys.spec(key_id)
    svc = str((row or {}).get("setup_service") or "")
    what = egress_ledger.R3_SERVICES.get(svc)
    url = str(((row or {}).get("check") or {}).get("url") or "")
    target = egress_ledger.safe_host(url) if url else ""

    def _line(outcome: str, **fields: Any) -> None:
        egress_ledger.record(svc if what else _KEYED_UNKNOWN_SERVICE, host=target,
                             outcome=outcome, what=what or "request", **fields)

    if not isinstance(row, dict) or row.get("check_before_setup") is not True or what is None:
        _line("refused", code="not_allowed", bytes_in=0, bytes_out=0)
        return None, "not_allowed", {}
    try:
        from puffpai import github_account
        under_way = github_account.before_setup()
    except Exception:  # noqa: BLE001 - not knowing is "set up": refused
        under_way = False
    if not under_way:
        _line("refused", code="bot_offline", bytes_in=0, bytes_out=0)
        return None, "bot_offline", {}
    if outside_switches.is_off(svc):
        _line("refused", code="switched_off", bytes_in=0, bytes_out=0)
        return None, "switched_off", {}
    try:
        host = _ascii_host(urlsplit(url).hostname or "")
    except ValueError:
        host = ""
    row_hosts = {_ascii_host(h) for h in row.get("hosts") or [] if isinstance(h, str)}
    fmt = (row.get("placement") or {}).get("value_format", "{key}")
    if not host or host not in row_hosts or not _keyed_url_ok(url) \
            or not _value_format_ok(fmt) or not isinstance(value, str) or not value:
        _line("refused", code="key_host", bytes_in=0, bytes_out=0)
        return None, "key_host", {}
    cred = _bind_credential(str((row.get("placement") or {}).get("name") or ""),
                            fmt.replace("{key}", value), [host])
    try:
        extra = _credential_header_for(cred, url)
        if extra is None:
            _line("refused", code="key_host", bytes_in=0, bytes_out=0)
            return None, "key_host", {}
        echoes = (value.encode("utf-8"), extra[1].encode("utf-8"))
        send = {"User-Agent": USER_AGENT, "Accept": "application/json",
                "Accept-Encoding": "identity", extra[0]: extra[1]}
        extra = None
        sent = len(url) + sum(len(k) + len(v) + 4 for k, v in send.items()) + 16
        try:
            resp = _keyed_wire(url, send, timeout)
        except OSError as exc:
            refused = _pin_refused_code(exc)
            if refused:
                _line("refused", code=refused, bytes_in=0, bytes_out=0)
                return None, refused, {}
            _line("failed", code="no_answer", bytes_out=sent)
            return None, "no_answer", {}
        except (http.client.HTTPException, ValueError):
            _line("failed", code="bad_answer", bytes_out=sent)
            return None, "bad_answer", {}
        finally:
            send = {}
        status = 0
        got = 0
        body = b""
        scopes = ""
        try:
            for attr in ("status", "code"):
                v = getattr(resp, attr, None)
                if isinstance(v, int) and not isinstance(v, bool):
                    status = v
                    break
            try:
                for name, hv in list(getattr(resp, "headers", {}).items()):
                    if str(name).strip().lower() == "x-oauth-scopes":
                        scopes = str(hv)[:400]
            except Exception:
                scopes = ""
            try:
                body = bytes(resp.read(64 * 1024) or b"")
                got = len(body)
            except (OSError, http.client.HTTPException, ValueError):
                got, body = 0, b""
        finally:
            try:
                resp.close()
            except Exception:
                pass
            resp = None
        _line("done", status=status, bytes_out=sent, bytes_in=got)
        answer: dict = {}
        if status == 200:
            try:
                data = json.loads(_credential_scrub(echoes, body).decode("utf-8", "replace"))
            except ValueError:
                data = None
            if isinstance(data, dict):
                answer = {"user": {k: str(data.get(k) or "")[:300]
                                   for k in ("login", "name", "avatar_url")},
                          "headers": ({"x-oauth-scopes": scopes} if scopes else {})}
        body = b""
        echoes = ()
        return status, "ok", answer
    finally:
        cred = None


# ── the wrapper ──────────────────────────────────────────────────────────────

def egress_request(door_id: str, method: str, url: str, *,
                   timeout: float = DEFAULT_TIMEOUT, resolver=None,
                   credential: Optional["_BoundCredential"] = None, **kwargs):
    """Make one outbound request through *door_id*, or refuse without a socket.

    Order matters and is the security property:

    1. screen the caller's kwargs — no caller may attach authority;
    2. resolve the door, the posture and the switch — nothing has touched the
       network yet;
    3. validate the URL's scheme, credentials and host;
    4. only then open a PINNED connection (:func:`_send`) and log it;
    5. follow at most :data:`MAX_REDIRECTS` hops, re-validating each
       ``Location``, because a redirect target is chosen by the server and the
       approval that covered hop one says nothing about hop two.

    **Authority is not a kwarg.** ``headers`` carrying a credential-shaped name
    (``Authorization``, ``Proxy-Authorization``, ``Cookie``, ``X-Api-Key``,
    ``X-Auth-Token``, or anything containing *auth*, *token*, *secret*, *key*
    or *cookie*), an ``auth=`` argument and a ``cookies=`` argument are all
    refused. The refusal names the header, never its value.

    The one way in is ``credential=``, which takes a
    :class:`_BoundCredential` that only code inside ``puffpai.security`` can
    construct — see that class for why nothing constructs one today. When a
    credential is present the rules tighten: https only, the destination host
    must be in the credential's bound hosts (exact, IDN-normalised), **zero
    redirects** (a 3xx is a refusal, so the header is never re-offered to a
    target the server chose), and the header is stripped from
    ``response.request.headers`` before the response is returned.

    ``params`` is deliberately **not** screened. A door's query string is
    composed by the call site, not by a remote party, and a name-based filter
    there would refuse legitimate parameters (``api_key`` style keys belong to
    the future broker's policy, not to a substring match) while a determined
    caller just renames the parameter. Anything secret in a query string is
    already logged by every intermediary; the broker must not put one there.

    Raises :class:`EgressBlocked` for a shut door, a refused URL or refused
    authority, and lets transport errors from ``requests`` propagate unchanged.
    """
    scope = kwargs.pop("picture_scope", None)
    _screen_kwargs(door_id, kwargs)
    cred = _check_credential(door_id, credential) if credential is not None else None

    ledger = _LedgerTag.for_door(door(door_id), str(door_id), url)
    try:
        if str(door_id) == RESULT_PICTURES or scope is not None:
            d, origin_host = _picture_scope_open(door_id, method, url, scope, kwargs,
                                                 credential)
        else:
            d = require_open(door_id)
            origin_host = _check_url(d, url)
    except EgressBlocked as blocked:
        ledger.refused(blocked, url, method)
        raise
    # An app door's hosts come from a manifest, not from a human review of this
    # file, so its name is resolved and held to the same public-address rule a
    # dynamic door is. A declared name that answers with a LAN address is
    # refused rather than dialled.
    check_addresses = d.dynamic_hosts or is_app_door(d)
    picture_only = d.id == RESULT_PICTURES or (
        origin_host in d.picture_hosts and origin_host not in d.hosts)
    if picture_only:
        try:
            _picture_request_ok(d, method, kwargs, credential)
        except EgressBlocked as blocked:
            ledger.refused(blocked, url, method)
            raise
        kwargs["stream"] = True
    pin = _new_pin(resolver)
    if check_addresses:
        # A door with a fixed host set was reviewed by a human; a dynamic one
        # was not, so the name it was handed is resolved and every answer
        # checked before a socket opens -- and the socket is then PINNED to
        # the answer that was checked. A fixed-host door is resolved, vetted
        # and pinned by the connection itself (pinned_net), once.
        try:
            pin_add(pin, origin_host,
                    _check_resolved_public(d, origin_host, _port_of(url),
                                           resolver))
        except EgressBlocked as blocked:
            ledger.refused(blocked, url, method)
            raise

    kwargs.pop("allow_redirects", None)  # this function owns hop policy

    if cred is not None:
        # _check_url already refused anything but https; restated here so the
        # credential rule does not depend on a door rule staying as it is.
        if urlsplit(url.strip()).scheme.lower() != "https":
            raise EgressBlocked(
                door_id, "credential requested on a non-https url",
                "That request was refused because it was not an encrypted "
                "connection.", code="not_encrypted")
        if _ascii_host(origin_host) not in cred.hosts:
            raise EgressBlocked(
                door_id,
                f"credential is not bound to host {origin_host!r}",
                "That request tried to send a sign-in to an address it is not "
                "registered for, so nothing was sent.", code="sign_in_refused")
        # Attached last, on a copy, so the value lives in no caller's dict.
        kwargs["headers"] = {**(kwargs.get("headers") or {}),
                             cred.header_name: cred._value}

    current = url
    current_host = origin_host
    for _hop in range(MAX_REDIRECTS + 1):
        resp = ledger.send(pin, d, method, current, timeout=timeout,
                           allow_redirects=False, **kwargs)
        if cred is not None:
            _strip_credential_header(resp, cred.header_name)
            if resp.status_code in (301, 302, 303, 307, 308):
                # Not a hop. The next target is the server's choice, and a
                # credential follows only where it was bound.
                raise EgressBlocked(
                    door_id, "authenticated request was redirected",
                    "That address redirected while carrying a sign-in, so "
                    "nothing further was sent.", code="sign_in_refused")
            return resp
        if check_addresses:
            _verify_peer(d, resp, current_host)
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location") or ""
            nxt = urljoin(current, location)
            # Re-validate before following, not after.
            try:
                nxt_host = _check_url(
                    d, nxt, hop_host=origin_host if d.dynamic_hosts else "")
                if check_addresses:
                    pin_add(pin, nxt_host,
                            _check_resolved_public(d, nxt_host, _port_of(nxt),
                                                   resolver))
            except EgressBlocked as blocked:
                ledger.refused(blocked, nxt, method)
                raise
            current, current_host = nxt, nxt_host
            if not picture_only and current_host in d.picture_hosts \
                    and current_host not in d.hosts:
                # A hop onto a pictures-only host: from here, a picture or nothing.
                picture_only = True
                _picture_request_ok(d, method, kwargs, credential)
            continue
        if picture_only:
            return _picture_answer(d, resp)
        return resp

    blocked = EgressBlocked(
        d.id, f"more than {MAX_REDIRECTS} redirects",
        "That site redirected too many times, so nothing was fetched.",
        code="too_many_redirects")
    ledger.refused(blocked, current, method)
    raise blocked


# ── the one network call, and its log line ───────────────────────────────────

def _new_pin(resolver=None):
    from puffpai.security import pinned_net
    return pinned_net.Pin(resolver=resolver)


def pin_add(pin, host: str, answers) -> None:
    """Pin *host* to answers :func:`_check_resolved_public` already vetted."""
    from puffpai.security import pinned_net
    fresh = pinned_net.Pin(preset={host: list(answers)})
    with pin._lock:  # noqa: SLF001 - one pin per request, same package
        pin._pinned.update(fresh._pinned)  # noqa: SLF001


def _send(method: str, url: str, *, pin, **kwargs):
    """THE network call for every door, card, picture, town lookup and sample.

    A pinned ``requests`` session (:func:`pinned_net.requests_session`):
    https only, no proxy from the environment, and the socket goes to the
    address *pin* holds for the URL's host. Tests replace this one function.
    """
    from puffpai.security import pinned_net
    session = pinned_net.requests_session(pin)
    try:
        return session.request(method, url, **kwargs)
    finally:
        session.close()


#: Door id -> what the request was for (``egress_ledger.WHAT`` key).
_WHAT_BY_DOOR = {"weather": "forecast", "air_quality": "air_quality",
                 "news_rss": "headlines", "web_fetch": "web_page",
                 "geocode": "town_lookup",
                 # searches (and a key's one test search)
                 "web_search": "web_search",
                 "brave_search": "web_search",
                 "marginalia_search": "web_search",
                 # her own GitHub account (the sign-in check, the
                 # device sign-in)
                 "github": "github_account",
                 # Toolsmith's cloud model (never the key, never the words)
                 "toolsmith_cloud": "toolsmith_install"}

#: Doors whose address carries no owner value in its path: the path is kept
#: in the log. Every other exit logs the host only.
_PATH_KEPT = frozenset({"weather", "air_quality", "geocode"})


def reason_code(exc: BaseException) -> str:
    """The closed-list reason word for a refusal (``egress_ledger``)."""
    code = str(getattr(exc, "code", "") or "")
    if code:
        return code
    text = str(getattr(exc, "reason", "") or exc).lower()
    if "no egress door named" in text or "not declared" in text:
        return "not_on_list"
    if "forces every app door shut" in text or "=off" in text:
        return "turned_off_here"
    if "posture:" in text and "is shut" in text:
        return ("switched_off" if f"posture: {OPEN_POSTURE}" in text
                else "bot_offline")
    if "is shut" in text or "switched" in text:
        return "switched_off"
    if "https" in text or "scheme" in text:
        return "not_encrypted"
    if "credential" in text or "auth" in text or "cookie" in text:
        return "sign_in_refused"
    if "landed on" in text:
        return "peer_moved"
    if "cannot resolve" in text or "resolves to nothing" in text \
            or "look up" in text:
        return "cannot_look_up"
    if "private" in text or "loopback" in text or "resolves to" in text \
            or "points at" in text:
        return "inside_address"
    if "more than" in text and "redirect" in text:
        return "too_many_redirects"
    if "redirect" in text:
        return "redirected_away"
    return "not_allowed"


def _ledger_service(door_id: str) -> str:
    """The outside-services id a door's traffic is logged under."""
    app = app_of(door_id)
    if app:
        return f"card-{app}"
    return str(door_id or "").strip().lower() or "unknown"


def _bytes_out(method: str, url: str, headers: Any, body: Any) -> Optional[int]:
    """About how many bytes the request put on the wire (line + headers + body).

    A length only: nothing of the request's content is kept.
    """
    try:
        parts = urlsplit(str(url))
        target = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
        total = len(f"{method} {target} HTTP/1.1\r\n".encode("utf-8"))
        total += len(f"Host: {parts.hostname or ''}\r\n".encode("utf-8"))
        if isinstance(headers, Mapping):
            for key, value in headers.items():
                total += len(f"{key}: {value}\r\n".encode("utf-8", "replace"))
        if isinstance(body, (bytes, bytearray)):
            total += len(body)
        elif isinstance(body, str):
            total += len(body.encode("utf-8", "replace"))
        return total + 2
    except Exception:
        return None


def _bytes_in(resp: Any, streamed: bool) -> Optional[int]:
    """The reply's size when it is known without consuming a stream."""
    if not streamed:
        if hasattr(resp, "_content"):
            content = getattr(resp, "_content", None)
        else:
            content = getattr(resp, "content", None)
        if isinstance(content, (bytes, bytearray)):
            return len(content)
    try:
        declared = (getattr(resp, "headers", None) or {}).get("Content-Length")
        return int(declared) if declared is not None else None
    except (TypeError, ValueError, AttributeError):
        return None


class _LedgerTag:
    """Writes the outside-traffic log line for one :func:`egress_request`."""

    def __init__(self, service: str, what: str, keep_path: bool,
                 picture_host: str = ""):
        self.service = service
        self.what = what
        self.keep_path = keep_path
        self.picture_host = picture_host

    @classmethod
    def for_door(cls, d: Optional[Door], door_id: str, url: str) -> "_LedgerTag":
        key = str(door_id or "").strip().lower()
        if d is not None and is_app_door(d):
            picture = d.picture_hosts[0] if d.picture_hosts else (
                d.hosts[1] if len(d.hosts) > 1 else "")
            return cls(_ledger_service(key), "card_data", False, picture)
        return cls(_ledger_service(key), _WHAT_BY_DOOR.get(key, "request"),
                   key in _PATH_KEPT)

    def _what_for(self, url: str) -> str:
        try:
            host = _clean_host(urlsplit(str(url)).hostname or "")
        except ValueError:
            host = ""
        if self.picture_host and host == self.picture_host:
            return "card_picture"
        return self.what

    def _path(self, url: str) -> str:
        if not self.keep_path:
            return ""
        from puffpai.security import egress_ledger
        return egress_ledger.safe_path(url)

    def refused(self, blocked: BaseException, url: str, method: str) -> None:
        from puffpai.security import egress_ledger
        egress_ledger.record(
            self.service, host=egress_ledger.safe_host(url),
            outcome="refused", method=method, path=self._path(url),
            code=reason_code(blocked), what=self._what_for(url),
            bytes_out=0, bytes_in=0)

    def send(self, pin, d: Door, method: str, url: str, **kwargs):
        """Send one hop through :func:`_send`, log it, and return the reply."""
        from puffpai.security import egress_ledger
        host = egress_ledger.safe_host(url)
        out = _bytes_out(method, url, kwargs.get("headers"),
                         kwargs.get("data") or kwargs.get("json"))
        try:
            resp = _send(method, url, pin=pin, **kwargs)
        except Exception as exc:
            refused = getattr(pin, "refused", None)
            if refused is not None:
                blocked = EgressBlocked(
                    d.id, f"pinned connection refused: {refused.reason}",
                    "That request tried to reach an address on this machine "
                    "or this network, so nothing was sent.",
                    code=getattr(refused, "code", "inside_address"))
                self.refused(blocked, url, method)
                raise blocked from None
            egress_ledger.record(
                self.service, host=host, outcome="failed", method=method,
                path=self._path(url), code="no_answer",
                what=self._what_for(url), bytes_out=out)
            raise exc
        req = getattr(resp, "request", None)
        if req is not None and isinstance(getattr(req, "headers", None), Mapping):
            out = _bytes_out(method, url, req.headers,
                             getattr(req, "body", None)) or out
        egress_ledger.record(
            self.service, host=host, outcome="done", method=method,
            path=self._path(url), status=getattr(resp, "status_code", None),
            bytes_in=_bytes_in(resp, bool(kwargs.get("stream"))),
            bytes_out=out, what=self._what_for(url))
        return resp


# ── pictures only ───────────────────────────────────────────────────────────

def _picture_request_ok(d: "Door", method: str, kwargs: dict, credential: Any) -> None:
    """A pictures-only host gets a plain GET: no query of ours, no body, no
    sign-in (headers were already screened for anything credential-shaped)."""
    if str(method).upper() != "GET" or credential is not None \
            or any(k in kwargs for k in ("params", "data", "json", "files")):
        raise EgressBlocked(d.id, "a pictures-only site gets a plain GET only",
                            "That site is reached for pictures only, so nothing was sent.",
                            code="not_on_list")


class _PictureAnswer:
    """A picture answer read in full (capped), standing in for the response."""

    def __init__(self, status: int, headers: dict, body: bytes):
        self.status_code, self.headers, self.content = status, headers, body
        self.ok = 200 <= status < 300

    def iter_content(self, chunk_size: int = 65536, *_a, **_k):
        size = max(1, int(chunk_size or 65536))
        for i in range(0, len(self.content), size):
            yield self.content[i:i + size]

    def close(self) -> None:
        pass


def _picture_answer(d: "Door", resp: Any,
                    max_bytes: Optional[int] = None) -> "_PictureAnswer":
    """Only a picture comes back from a pictures-only site: an image type
    (never SVG), at most :data:`RESULT_PICTURE_MAX_BYTES`. Anything else is
    refused (:class:`EgressBlocked`, code ``not_a_picture`` / ``too_big``)."""
    cap = int(max_bytes or RESULT_PICTURE_MAX_BYTES)
    try:
        status = int(getattr(resp, "status_code", 0) or 0)
        headers = {str(k).lower(): str(v) for k, v in
                   dict(getattr(resp, "headers", {}) or {}).items()}
        ctype = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if status == 200 and ctype not in RESULT_PICTURE_TYPES:
            raise EgressBlocked(d.id, f"answer is {ctype or 'untyped'}",
                                "That address did not answer with a picture.",
                                code="not_a_picture")
        declared = headers.get("content-length", "")
        if declared.isdigit() and int(declared) > cap:
            raise EgressBlocked(d.id, "picture too big", "That picture was too big.",
                                code="too_big")
        chunks, total = [], 0
        it = getattr(resp, "iter_content", None)
        for chunk in (it(65536) if callable(it) else [getattr(resp, "content", b"") or b""]):
            total += len(chunk or b"")
            if total > cap:
                raise EgressBlocked(d.id, "picture too big", "That picture was too big.",
                                    code="too_big")
            chunks.append(bytes(chunk or b""))
    finally:
        close = getattr(resp, "close", None)
        if callable(close):
            close()
    keep = {k: v for k, v in headers.items() if k in ("content-type", "cache-control",
                                                      "last-modified", "etag")}
    out = {"Content-Type": keep.pop("content-type", "")}
    out.update(keep)
    return _PictureAnswer(status, out, b"".join(chunks))


# ── pictures a source's own results point to ─────────────────────────────────
#
# A data source's switch row says "<source> and the picture sites its results
# point to" (build.setup); her yes to it covers both. The picture sites are
# not known before her yes (no look may run before it), so they are not a
# host list: a picture is let through ONLY when the source's own door is open
# (that yes, and the run posture) AND its host appeared in that source's own
# results (the caller keeps those, build: assemble.server.host). GET only, no
# query of hers, no cookie or key, image types only (never SVG), size-capped,
# same public-address pinning and same-host redirects as any dynamic door.

_SCOPE_KEY = object()


class _PictureScope:
    """Made only by :func:`result_picture_get`; anything else is refused."""
    __slots__ = ("source_door", "hosts", "_key")

    def __init__(self, source_door: str, hosts: frozenset, key: object):
        self.source_door, self.hosts, self._key = source_door, hosts, key


def _picture_scope_open(door_id: Any, method: str, url: str, scope: Any, kwargs: dict,
                        credential: Any) -> tuple[Door, str]:
    if str(door_id) != RESULT_PICTURES or not isinstance(scope, _PictureScope) \
            or scope._key is not _SCOPE_KEY:  # noqa: SLF001 - same module
        raise EgressBlocked(str(door_id), "picture door without its scope",
                            "That picture was not asked for the usual way, so nothing "
                            "was fetched.", code="not_on_list")
    if str(method).upper() != "GET" or credential is not None or set(kwargs) - {"stream"}:
        raise EgressBlocked(RESULT_PICTURES, "picture request carries more than a GET",
                            "Only a plain picture request is allowed there.",
                            code="not_on_list")
    off = _as_bool(os.environ.get(door(RESULT_PICTURES).env_var) or "")
    if off is False:
        raise EgressBlocked(RESULT_PICTURES, "PUFFPAI_EGRESS_RESULT_PICTURES=off",
                            door(RESULT_PICTURES).settings_hint, code="turned_off_here")
    require_open(scope.source_door)          # her yes to the source, and the posture
    d = door(RESULT_PICTURES)
    host = _check_url(d, url)
    if host not in scope.hosts:
        raise EgressBlocked(RESULT_PICTURES, f"{host!r} is not in the source's results",
                            "That picture's site was not in the source's own results, so "
                            "nothing was fetched.", code="not_on_list")
    return d, host


_PICTURE_AUDITED: set[tuple[str, str]] = set()


def result_picture_get(source_door: str, url: str, result_hosts: Any, *,
                       timeout: float = DEFAULT_TIMEOUT,
                       max_bytes: int = RESULT_PICTURE_MAX_BYTES) -> tuple[int, dict, bytes]:
    """One picture from a site *source_door*'s own results pointed to
    (*result_hosts*: hosts seen in them). ``(status, headers, body)``.
    Raises :class:`EgressBlocked` when the source is shut, the host was not
    in its results, or the answer is not a picture or too big."""
    hosts = frozenset(str(h).strip().rstrip(".").lower() for h in result_hosts or ()
                      if isinstance(h, str) and h)
    scope = _PictureScope(str(source_door), hosts, _SCOPE_KEY)
    answer = egress_request(RESULT_PICTURES, "GET", url, timeout=timeout,
                            picture_scope=scope)
    if len(answer.content) > max_bytes:
        raise EgressBlocked(RESULT_PICTURES, "picture too big", "That picture was too big.",
                            code="too_big")
    status = int(answer.status_code)
    headers = {str(k).lower(): str(v) for k, v in answer.headers.items()}
    host = _clean_host(urlsplit(url.strip()).hostname or "")
    if (str(source_door), host) not in _PICTURE_AUDITED:
        _PICTURE_AUDITED.add((str(source_door), host))
        try:
            from puffpai.security.audit_log import AuditLog
            AuditLog().log(action="result_picture", user="owner", result="fetched",
                           details=f"{str(source_door)[:80]} -> {host}"[:200])
        except Exception:  # noqa: BLE001 - the ledger line is kept anyway
            pass
    return status, {k: v for k, v in headers.items() if k in ("content-type",)}, \
        answer.content


def egress_get(door_id: str, url: str, **kwargs):
    """``GET`` through a door. See :func:`egress_request`.

    Every kwarg, including ``credential=``, is screened by
    :func:`egress_request`; this wrapper adds no path around those checks.
    """
    return egress_request(door_id, "GET", url, **kwargs)


# ── app feed doors: an installed app's declared feed host, as a real door ────
#
# An app's ``feed`` block (:mod:`puffpai.feeds`) names ONE host its data comes
# from, and optionally one host its pictures come from. These functions turn
# that declaration into an ordinary :class:`Door` in this registry, so the
# feed runner goes through :func:`egress_request` like every other outbound
# call -- same posture check, same exact-host allowlist, same https-only rule,
# same per-hop redirect re-validation, same refusal of caller-attached
# credentials. There is no second path.
#
# Ported in spirit from HomeBotBuild ``security/egress_doors.py`` 700-1000
# (``register_app_doors`` / ``app_egress_request``), narrowed to feeds:
#
# 1. **Forced shut.** ``default`` is the literal False below and is never read
#    from a manifest. No manifest key opens a door. The only ways one opens are
#    the owner's recorded yes (:func:`set_app_door_consent`, reached from
#    ``puffpai apps allow`` and ``POST /apps/<id>/feed/allow``) or an explicit
#    ``egress_doors.<flag>`` in config -- and then only on a build whose run
#    posture is ``enumerated``, because :func:`egress_allowed` checks posture
#    before it ever asks for a switch.
# 2. **Namespaced.** Every id is ``app:<app_id>:feed``. Built-in ids never hold
#    a colon, so a manifest can neither collide with nor shadow ``weather``.
# 3. **Consent is bound to the hosts it was given for.** The flag carries an
#    8-hex digest of the sorted host set. An app update that repoints its feed
#    produces a different flag, which nobody has said yes to, so the door falls
#    back to shut. Consent does not survive a change to what it was consent to.
# 4. **The owner's yes lives in the state dir**, not in the checkout and not in
#    the shared config file: ``<state>/egress_app_doors.json``. It is re-read
#    when its mtime changes, so ``puffpai apps allow`` in a terminal is seen by
#    a running server without a restart. An unreadable or malformed file is no
#    setting at all -- every app door shut.
# 5. **Operator kill switch.** ``PUFFPAI_EGRESS_APPS=off`` forces every app door
#    shut regardless of anything the owner recorded. Only "off" is honoured
#    there; "on" opens nothing.

#: Every app door id starts with this. A built-in id can never contain a colon.
APP_DOOR_PREFIX = "app:"

#: Operator override that forces every app door shut. "on" is ignored.
APPS_KILL_ENV = "PUFFPAI_EGRESS_APPS"

#: File name of the owner's recorded yes/no, under the state dir.
APP_CONSENT_FILE = "egress_app_doors.json"
APP_CONSENT_SCHEMA = "puffpai_app_door_consent/v1"

#: Mirrors ``app_registry._ID_RE`` plus its length bound. Re-stated, not
#: imported: this module must not depend on the registry it protects.
_APP_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")

#: A hostname a manifest may declare (mirrors HBB ``_APP_HOST_RE``).
_APP_HOST_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)"
                          r"(?:\.(?!-)[a-z0-9-]{1,63}(?<!-))*$")

#: The last label of a registrable NAME: alphabetic, or ``xn--`` ACE. Either
#: way it starts with a letter, which no IP literal in any notation can do.
_APP_TLD_RE = re.compile(r"^(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})$")

#: Suffixes that only ever mean "this network", never a public service.
_LOCAL_TLDS = frozenset({"local", "lan", "internal", "localhost", "home",
                         "arpa", "localdomain", "intranet", "corp"})

#: At most this many hosts per app door: the data host and the picture host.
MAX_APP_DOOR_HOSTS = 2

_APP_LOCK = threading.RLock()
_APP_DOORS: dict[str, Door] = {}

_consent_lock = threading.Lock()
_consent_cache: dict[str, Any] = {"path": None, "mtime": None, "doors": {}}


def is_ip_literal(host: Any) -> bool:
    """True if *host* is an address rather than a name, in ANY notation.

    Ported from HomeBotBuild: dotted quad, octal, hex, a bare integer,
    shorthand with fewer than four parts, and IPv6 in every form. Done by
    parsing, because the set of things that resolve as an address is defined
    by the parsers, not by a regex somebody hoped was complete.
    """
    if not isinstance(host, str):
        return False
    text = host.strip()
    if not text:
        return False
    if text.startswith("[") or text.endswith("]") or ":" in text:
        return True
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        pass
    parts = text.split(".")
    if len(parts) > 4:
        return False
    for part in parts:
        if not part:
            return False
        low = part.lower()
        try:
            if low.startswith("0x"):
                int(low, 16)
            elif low.startswith("0") and len(low) > 1:
                int(low, 8)
            else:
                int(low, 10)
        except ValueError:
            return False
    return True


def is_registrable_hostname(host: Any) -> bool:
    """True only for a public NAME a door may declare. Refuses every literal.

    Lower-case hostname shape, at least two labels, a TLD-shaped last label
    that is not a local-network suffix, and not an address in any notation.
    ``localhost``, ``nas.local``, ``8.8.8.8``, ``127.1``, ``0x7f000001`` and
    ``[::1]`` all fail.
    """
    if not isinstance(host, str) or host != host.strip().lower():
        return False
    if len(host) > 253 or not _APP_HOST_RE.match(host):
        return False
    if is_ip_literal(host):
        return False
    labels = host.split(".")
    if len(labels) < 2:
        return False
    if labels[-1] in _LOCAL_TLDS:
        return False
    return bool(_APP_TLD_RE.match(labels[-1]))


def app_door_id(app_id: str) -> str:
    """The registry id for an app's feed door: ``app:<app_id>:feed``."""
    return f"{APP_DOOR_PREFIX}{app_id}:feed"


def is_app_door(d: "Door | str | None") -> bool:
    """True for a door registered from an app's feed block."""
    key = d.id if isinstance(d, Door) else str(d or "")
    return key.startswith(APP_DOOR_PREFIX)


def app_of(d: "Door | str") -> Optional[str]:
    """The app id that declared this door, or None for a built-in one."""
    key = d.id if isinstance(d, Door) else str(d)
    if not key.startswith(APP_DOOR_PREFIX):
        return None
    return key[len(APP_DOOR_PREFIX):].split(":", 1)[0] or None


def _host_digest(hosts) -> str:
    """8 hex of sha256 over the sorted host set -- the consent binding.

    Not a secret and not an authenticator. Its job is that a host set the
    owner never saw cannot inherit a yes the owner gave for a different one.
    """
    payload = "\n".join(sorted(hosts))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def app_door_flag(app_id: str, hosts) -> str:
    """The consent/config key for an app's feed door, bound to its hosts."""
    clean = sorted({_clean_host(h) for h in (hosts or ()) if _clean_host(h)})
    return f"app_{app_id}_feed_{_host_digest(clean)}"


def _app_door_from_spec(app_id: str, spec: Any) -> Optional[Door]:
    """One ``Door`` from one feed spec, or None -- never a half-trusted door.

    *spec* is ``{"hosts": [...], "name": str, "purpose": str,
    "data_sent": str}``. Every host is re-checked here even though the loader
    already did, because "the loader checked it" is not a control this module
    inherits for the one field it enforces on.
    """
    if not isinstance(spec, Mapping):
        return None
    raw_hosts = spec.get("hosts")
    if not isinstance(raw_hosts, (list, tuple)) or not raw_hosts:
        return None
    hosts: list[str] = []
    for raw in raw_hosts:
        if not isinstance(raw, str) or not is_registrable_hostname(raw):
            return None
        if raw not in hosts:
            hosts.append(raw)
    pictures: list[str] = []
    raw_pics = spec.get("picture_hosts") or []
    if not isinstance(raw_pics, (list, tuple)):
        return None
    for raw in raw_pics:
        if not isinstance(raw, str) or not is_registrable_hostname(raw):
            return None
        if raw not in hosts and raw not in pictures:
            pictures.append(raw)
    if len(hosts) + len(pictures) > MAX_APP_DOOR_HOSTS:
        return None
    name = spec.get("name")
    name = str(name)[:64] if isinstance(name, str) and name.strip() else app_id
    purpose = spec.get("purpose")
    data_sent = spec.get("data_sent")
    # Bound to every site it reaches, pictures-only ones too (so a yes given
    # when the picture host was a full host still counts for the same set).
    flag = app_door_flag(app_id, hosts + pictures)
    return Door(
        id=app_door_id(app_id),
        hosts=tuple(hosts),
        picture_hosts=tuple(pictures),
        purpose=(str(purpose)[:200] if isinstance(purpose, str) else ""),
        data_sent=(str(data_sent)[:200] if isinstance(data_sent, str) else ""),
        config_flag=flag,
        env_var="PUFFPAI_EGRESS_" + flag.upper(),
        settings_hint=(
            f"{name} has not been allowed to use {', '.join(hosts)} yet, so "
            f"nothing was fetched. To allow it, run: puffpai apps allow "
            f"{app_id}"),
        # NEVER from the manifest. A literal, so a test can assert on it.
        default=False,
        notes=(f"Declared by the '{app_id}' app's feed block and registered on "
               f"scan. Shut until the owner allows it; a host this door does "
               f"not name is refused even when it is open."),
    )


def register_app_doors(entries) -> tuple[str, ...]:
    """Replace the whole app-door set from *entries*: ``[(app_id, spec), ...]``.

    A wholesale replace, not a merge: a rescan IS install and uninstall, so an
    app whose folder is gone loses its door in the same breath. Never raises; a
    refused entry simply has no door, which is the fail-shut outcome.
    """
    built: dict[str, Door] = {}
    for entry in (entries or ()):
        try:
            app_id, spec = entry
        except (TypeError, ValueError):
            continue
        app_id = str(app_id or "").strip()
        if not _APP_ID_RE.match(app_id):
            logger.warning("egress: refusing an app door for unusable app id %s",
                           _shown(app_id))
            continue
        d = _app_door_from_spec(app_id, spec)
        if d is None:
            logger.warning("egress: app %s feed door is not registrable; the "
                           "app reaches nothing", _shown(app_id))
            continue
        if d.id in _BY_ID or d.id in built:
            logger.warning("egress: refusing duplicate app door id %s",
                           _shown(d.id))
            continue
        built[d.id] = d
    with _APP_LOCK:
        changed = set(built) != set(_APP_DOORS) or any(
            _APP_DOORS.get(k) != v for k, v in built.items())
        _APP_DOORS.clear()
        _APP_DOORS.update(built)
    if changed:
        logger.info("egress: %d app feed door(s) registered, all shut until "
                    "the owner allows them: %s", len(built),
                    ", ".join(_shown(k) for k in built) or "-")
    return tuple(built)


def clear_app_doors() -> None:
    """Forget every registered app door (a build with no feed apps; tests)."""
    with _APP_LOCK:
        _APP_DOORS.clear()


def app_doors() -> tuple[Door, ...]:
    """The registered app feed doors, in registration order."""
    with _APP_LOCK:
        return tuple(_APP_DOORS.values())


def app_door(app_id: str) -> Optional[Door]:
    """The registered feed door for *app_id*, or None."""
    with _APP_LOCK:
        return _APP_DOORS.get(app_door_id(str(app_id or "").strip()))


def _app_door_lookup(key: str) -> Optional[Door]:
    with _APP_LOCK:
        return _APP_DOORS.get(key)


# -- the owner's recorded yes/no ---------------------------------------------

def app_consent_path() -> str:
    """``<state dir>/egress_app_doors.json``. Never inside the checkout."""
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), APP_CONSENT_FILE)


def _reset_app_consent_cache() -> None:
    with _consent_lock:
        _consent_cache.update({"path": None, "mtime": None, "doors": {}})


def _read_app_consent() -> dict[str, Any]:
    """``{flag: {"open": bool, ...}}`` from the state file. Fails to {}.

    Re-read when the path or mtime changes. A missing file is no setting; an
    unreadable, oversize or malformed one is ALSO no setting, and is logged --
    either way every app door stays shut.
    """
    try:
        path = app_consent_path()
    except Exception as exc:
        logger.debug("app door consent path unavailable: %s", exc)
        return {}
    try:
        stat = os.stat(path)
        mtime = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        with _consent_lock:
            _consent_cache.update({"path": path, "mtime": None, "doors": {}})
        return {}
    with _consent_lock:
        if _consent_cache["path"] == path and _consent_cache["mtime"] == mtime:
            return dict(_consent_cache["doors"])
    doors: dict[str, Any] = {}
    try:
        if stat.st_size > 256 * 1024:
            raise ValueError("consent file is larger than 256 KB")
        with open(path, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        if (isinstance(loaded, dict)
                and loaded.get("schema") == APP_CONSENT_SCHEMA
                and isinstance(loaded.get("doors"), dict)):
            for flag, entry in loaded["doors"].items():
                if (isinstance(flag, str) and isinstance(entry, dict)
                        and isinstance(entry.get("open"), bool)):
                    doors[flag] = dict(entry)
        else:
            logger.warning("app door consent file %s is not in the expected "
                           "shape; every app door stays shut", path)
    except (OSError, ValueError) as exc:
        logger.warning("app door consent file unreadable (%s); every app door "
                       "stays shut", exc)
        doors = {}
    with _consent_lock:
        _consent_cache.update({"path": path, "mtime": mtime, "doors": doors})
    return dict(doors)


def _app_door_switch(d: Door) -> tuple[Optional[bool], str]:
    """The owner's setting for an app door, and where it came from.

    Order: the operator kill switch (off only), the per-door env var, the
    owner's recorded yes/no, ``egress_doors.<flag>`` in config, else none.
    """
    kill = os.environ.get(APPS_KILL_ENV)
    if kill is not None and _as_bool(kill) is False:
        return False, f"{APPS_KILL_ENV}=off forces every app door shut"

    bridge_up = _bridge_up()
    raw_env = os.environ.get(d.env_var)
    if raw_env is not None and raw_env.strip() != "":
        decided = _as_bool(raw_env)
        if decided is None:
            return None, f"{d.env_var} is not a switch value; door stays shut"
        if decided is False or not bridge_up:
            return decided, f"{d.env_var}={'on' if decided else 'off'}"

    entry = _read_app_consent().get(d.config_flag)
    if isinstance(entry, dict) and isinstance(entry.get("open"), bool):
        return entry["open"], ("the owner allowed it" if entry["open"]
                               else "the owner said no")

    if bridge_up:
        # With the Bridge up a live card opens only on the owner's own
        # recorded yes, never on a line in a settings file.
        return None, "the Bridge is up and the owner has not allowed it"

    section = _raw_config().get(CONFIG_SECTION)
    if isinstance(section, dict) and d.config_flag in section:
        decided = _as_bool(section.get(d.config_flag))
        if decided is None:
            return None, (f"{CONFIG_SECTION}.{d.config_flag} is not a switch "
                          f"value; door stays shut")
        return decided, f"{CONFIG_SECTION}.{d.config_flag}={decided}"

    return None, "the owner has not allowed it"


def app_door_consent(app_id: str) -> Optional[bool]:
    """What the owner recorded for this app's feed door: True, False or None.

    Only the recorded yes/no (and config), not posture: "did the owner say
    yes" and "is the door open" are different questions and a screen needs
    both answers.
    """
    d = app_door(app_id)
    if d is None:
        return None
    decided, _why = _app_door_switch(d)
    return decided


def set_app_door_consent(app_id: str, allowed: bool, *,
                         when: Optional[str] = None) -> dict[str, Any]:
    """Record the owner's yes or no for an app's feed door. Opens no socket.

    Refuses an app with no registered door (not installed, no feed, or a feed
    this module would not register). The write is atomic (temp file + replace)
    and keyed by the host-bound flag, so a yes never carries over to a
    different host set. Returns ``{"door", "flag", "hosts", "open"}``.
    """
    if not isinstance(allowed, bool):
        raise ValueError("allowed must be True or False")
    d = app_door(app_id)
    if d is None:
        raise KeyError(f"no feed door is registered for app {app_id!r}")
    from datetime import datetime, timezone
    path = app_consent_path()
    stamp = when or datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _consent_lock:
        existing: dict[str, Any] = {}
        try:
            with open(path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if (isinstance(loaded, dict)
                    and loaded.get("schema") == APP_CONSENT_SCHEMA
                    and isinstance(loaded.get("doors"), dict)):
                existing = {k: v for k, v in loaded["doors"].items()
                            if isinstance(k, str) and isinstance(v, dict)}
        except (OSError, ValueError):
            existing = {}
        existing[d.config_flag] = {
            "open": allowed, "app": app_of(d),
            "hosts": list(d.hosts) + list(d.picture_hosts),
            "at": stamp,
        }
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"schema": APP_CONSENT_SCHEMA, "doors": existing}, fh,
                      indent=2, sort_keys=True)
        try:
            os.chmod(tmp, 0o600)
        except OSError:  # pragma: no cover - Windows ignores the mode
            pass
        os.replace(tmp, path)
        _consent_cache.update({"path": None, "mtime": None, "doors": {}})
    logger.info("egress: owner %s app door %s (%s)",
                "allowed" if allowed else "shut", _shown(d.id),
                ", ".join(_shown(h) for h in d.hosts))
    return {"door": d.id, "flag": d.config_flag, "hosts": list(d.hosts),
            "open": allowed}


def app_egress_get(app_id: str, url: str, **kwargs):
    """``GET`` through an app's feed door. The ONE outbound path for a feed.

    This is :func:`egress_request` against ``app:<app_id>:feed`` and nothing
    more: shut-door refusal before any socket, exact-host allowlist, https,
    redirects re-validated, no caller-attached credential.
    """
    return egress_request(app_door_id(str(app_id or "").strip()), "GET", url,
                          **kwargs)


# ── a browser app's outside sites, on the app door ───────────────────────────
#
# A browser app Toolsmith added (``puffpai.webapps``) runs in its own locked
# frame with no internet. When the install-time scan finds it needs an outside
# site (its data service, its maps), the app's forwarding address sends the
# request here, and ONLY here: :func:`app_web_request`.
#
# Built on the app feed door above, with the same rules made narrower:
#
# 1. **Registered, bounded.** :func:`register_web_app_door` gives the app one
#    door, ``app:<app id>:web``, naming at most :data:`MAX_APP_DOOR_HOSTS`
#    public names (each through :func:`is_registrable_hostname`). Web doors
#    live in their own table: :func:`door` and :func:`egress_request` never
#    see them, so no other path (and no config key or per-door environment
#    value) can open one.
# 2. **Each site is its own yes.** A site is off until the owner switches it
#    on with an owner-only consent ticket (``app_host_on``, bound to this app
#    and this site). The recorded yes lives in ``egress_app_doors.json`` under
#    a flag bound to the door's WHOLE site set, so an app that now names a new
#    site has every site off again until the owner says yes anew. Switching a
#    site off never needs a ticket. ``PUFFPAI_EGRESS_APPS=off`` forces every
#    site off. The bot's run setting must let it use the outside services the
#    owner said yes to (``enumerated``), as for every door.
# 3. **Narrow requests.** https on 443, no sign-in in the address, every
#    address the name resolves to public, and the socket pinned to the
#    address that was checked. Request headers: only ``content-type``,
#    ``accept``, ``accept-language`` (others are dropped, and only their
#    names are logged). A sign-up key rides only when the caller names its
#    handle, only on the first hop, bound to exactly that site, and every echo
#    of it is removed from the answer. At most two redirects, each only to a
#    site of this app that is switched on; a redirect never carries the key.
# 4. **Narrow answers.** Only ``content-type``, ``content-length``,
#    ``cache-control``, ``etag`` and ``last-modified`` come back (never
#    ``Set-Cookie``, a CSP, ``Clear-Site-Data``, ``Service-Worker-Allowed`` or
#    ``Location``), and sizes are capped both ways.
# 5. **Logged.** One outside-traffic line per request on the wire (and one per
#    refusal), host only, ``what="app_traffic"``, service ``app:<id>:web``.

#: The web door id's last part.
WEB_DOOR_SUFFIX = ":web"

#: A browser app's id as ``integrate.records`` issues it (``webapps.ID_RE``):
#: re-stated, not imported, like :data:`_APP_ID_RE`. At most 39 characters.
_WEB_APP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,29}-[a-z2-7]{8}\Z")

#: Methods a browser app may forward. Anything else is refused.
WEB_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"})

#: Request headers an app may send (lower-case). Every other one is dropped.
WEB_REQUEST_HEADERS = ("content-type", "accept", "accept-language")

#: Answer headers an app gets back (lower-case). Every other one is dropped.
WEB_ANSWER_HEADERS = ("content-type", "content-length", "cache-control", "etag",
                      "last-modified")

#: The refusal words :func:`app_web_request` answers with (``{"refused": code}``).
WEB_REFUSALS = frozenset({
    "switched_off", "not_https", "not_443", "userinfo", "inside_address",
    "too_big", "redirect_off_list", "unknown_app",
    # also honest, beyond the pinned list: the bot is set to stay off the
    # internet, a method or size the app may not use, no DNS answer, a third
    # redirect, a key that cannot be used or not for this site, no answer.
    "bot_offline", "not_allowed", "cannot_look_up", "redirects",
    "key_unavailable", "key_host", "no_answer", "bad_answer", "peer_moved",
    # a DNS filter at home (Pi-hole, AdGuard Home) blocked the name.
    "network_filter"})

WEB_MAX_REDIRECTS = 2
WEB_TIMEOUT = 15.0
_WEB_MAX_OUT_CAP = 10 * 1024 * 1024
_WEB_MAX_IN_CAP = 50 * 1024 * 1024
_WEB_MAX_HEADER_VALUE = 512
_WEB_WHAT = "app_traffic"
#: The log's service id for a request naming no web door this build has.
_WEB_UNKNOWN_SERVICE = "app_web"
#: Tests replace this; the running app looks names up with the system.
_WEB_RESOLVER = None

#: The pinned refusal word -> the outside-traffic log's own word (so the
#: "What leaves your computer" page has words for every line).
_WEB_LEDGER_CODE = {
    "unknown_app": "not_on_list", "not_https": "not_encrypted",
    "not_443": "not_allowed", "userinfo": "sign_in_refused",
    "redirect_off_list": "redirected_away",
}

_WEB_DOORS: dict[str, Door] = {}


def web_door_id(app_id: str) -> str:
    """``app:<app id>:web``."""
    return f"{APP_DOOR_PREFIX}{app_id}{WEB_DOOR_SUFFIX}"


def _web_flag(app_id: str, hosts) -> str:
    """The recorded-yes key: this app and its WHOLE site set."""
    return f"app_{app_id}_web_{_host_digest(sorted(set(hosts)))}"


def _web_host(host: Any) -> str:
    return str(host).strip().rstrip(".").lower() if isinstance(host, str) else ""


def register_web_app_door(app_id: str, hosts: list[str]) -> str:
    """Give browser app *app_id* its door for *hosts*; returns its id.

    Raises ``ValueError`` for an id that is not a browser app's, a name that
    is not a public registrable one, or more than :data:`MAX_APP_DOOR_HOSTS`
    sites. Registering again replaces the door; a different site set means a
    different recorded-yes key, so every site starts off again.
    """
    if not isinstance(app_id, str) or not _WEB_APP_ID_RE.match(app_id):
        raise ValueError("not a browser app's id")
    if isinstance(hosts, (str, bytes)) or not isinstance(hosts, (list, tuple)):
        raise ValueError("hosts must be a list of site names")
    clean: list[str] = []
    for raw in hosts:
        host = _web_host(raw)
        if not is_registrable_hostname(host):
            raise ValueError("a site is not a public name")
        if host not in clean:
            clean.append(host)
    if len(clean) > MAX_APP_DOOR_HOSTS:
        raise ValueError(f"at most {MAX_APP_DOOR_HOSTS} outside sites per app")
    flag = _web_flag(app_id, clean)
    d = Door(
        id=web_door_id(app_id), hosts=tuple(clean),
        purpose="An added app's outside sites.",
        data_sent="What the app sends there, and your internet address.",
        config_flag=flag,
        # Never read for a web door: a site opens only with the owner's
        # recorded yes. Only the operator's "off" (APPS_KILL_ENV) is honoured.
        env_var=APPS_KILL_ENV,
        settings_hint=("That outside site is switched off for this app, so "
                       "nothing was sent."),
        default=False,
        notes=("A browser app's outside sites. Each is off until the owner "
               "says yes to it; only app_web_request uses this door."))
    with _APP_LOCK:
        _WEB_DOORS[app_id] = d
    logger.info("egress: browser app %s may ask for %d outside site(s), all "
                "off until the owner says yes", _shown(app_id), len(clean))
    return d.id


def unregister_web_app_door(app_id: str) -> bool:
    """Forget *app_id*'s door and every yes recorded for it (app removed)."""
    key = app_id if isinstance(app_id, str) else ""
    with _APP_LOCK:
        gone = _WEB_DOORS.pop(key, None)
    if key:
        with _web_set_lock:
            _store_web_consent(key, None)
    return gone is not None


def web_app_door(app_id: str) -> Optional[Door]:
    """The web door for *app_id*, or None."""
    with _APP_LOCK:
        return _WEB_DOORS.get(app_id if isinstance(app_id, str) else "")


def web_app_ids() -> tuple[str, ...]:
    """Every browser app with a web door, in registration order."""
    with _APP_LOCK:
        return tuple(_WEB_DOORS)


def clear_web_app_doors() -> None:
    """Forget every web door (tests). Recorded yeses stay on disk."""
    with _APP_LOCK:
        _WEB_DOORS.clear()


def _web_on_in(entry: Any, d: Door) -> set[str]:
    """The sites of *d* a recorded entry has on (fails to none)."""
    if not isinstance(entry, dict) or entry.get("kind") != "web" \
            or entry.get("open") is not True:
        return set()
    listed = entry.get("hosts_on")
    if not isinstance(listed, list):
        return set()
    return {h for h in listed if isinstance(h, str) and h in d.hosts}


def _recorded_web_on(d: Door) -> set[str]:
    """The sites of *d* the owner switched on (the file only; fails to none)."""
    return _web_on_in(_read_app_consent().get(d.config_flag), d)


def _web_killed() -> bool:
    kill = os.environ.get(APPS_KILL_ENV)
    return kill is not None and _as_bool(kill) is False


def app_web_hosts(app_id: str) -> dict[str, bool]:
    """``{site: on}`` for every site of *app_id*'s door; ``{}`` when it has none.

    On means the owner said yes to that site and this computer's settings do
    not force app sites off.
    """
    d = web_app_door(app_id)
    if d is None:
        return {}
    on = set() if _web_killed() else _recorded_web_on(d)
    return {h: (h in on) for h in d.hosts}


def _by_kind(by: Any) -> str:
    text = str(by or "")
    if text.startswith("device:"):
        return "device"
    if text.startswith("terminal:"):
        return "terminal"
    return text if text in ("owner", "wizard", "installer") else "owner"


#: Held across every change to a browser app's sites: the read of what is
#: on, the spending of the owner's yes, and the write. Without it, a change
#: that read "site A is on" before the owner switched A off could write A back
#: on with no new yes. Re-entrant so a change made from inside another (a
#: nested owner action on this thread) cannot deadlock. Taken BEFORE
#: ``_consent_lock``, never while holding it.
_web_set_lock = threading.RLock()


def _store_web_consent(app_id: str,
                       entry: "Optional[dict] | Callable[[dict], dict]") -> None:
    """Write (or with None, drop every) web yes for *app_id*. Atomic.

    *entry* may be a function of the freshly read ``{flag: entry}`` map that
    returns the entry to write: it runs under the file lock, so a change is
    applied to what is on the disk now, not to an older read.

    Raises ``OSError``/``ValueError`` rather than write a yes over a file that
    could not be read.
    """
    from datetime import datetime, timezone
    path = app_consent_path()
    with _consent_lock:
        existing: dict[str, Any] = {}
        try:
            with open(path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if (isinstance(loaded, dict)
                    and loaded.get("schema") == APP_CONSENT_SCHEMA
                    and isinstance(loaded.get("doors"), dict)):
                existing = {k: v for k, v in loaded["doors"].items()
                            if isinstance(k, str) and isinstance(v, dict)}
        except FileNotFoundError:
            existing = {}
        except (OSError, ValueError):
            # Never write over a file that could not be read: that would
            # lose every other recorded yes (and the evidence). While it
            # cannot be read, every app door and site reads as shut anyway,
            # so a removal has nothing more to do and a change is refused.
            if entry is None:
                return
            raise
        if callable(entry):
            entry = entry(dict(existing))
        # One app has one web door: yeses for its older site sets go too.
        existing = {k: v for k, v in existing.items()
                    if not (v.get("kind") == "web" and v.get("app") == app_id)}
        if entry is not None:
            entry = dict(entry)
            entry.setdefault("at", datetime.now(timezone.utc)
                             .isoformat(timespec="seconds"))
            existing[entry.pop("flag")] = entry
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"schema": APP_CONSENT_SCHEMA, "doors": existing}, fh,
                      indent=2, sort_keys=True)
        try:
            os.chmod(tmp, 0o600)
        except OSError:  # pragma: no cover - Windows ignores the mode
            pass
        os.replace(tmp, path)
        _consent_cache.update({"path": None, "mtime": None, "doors": {}})


_WEB_SET_WORDS = {
    "unknown_app": "That app has no outside sites here, so nothing was changed.",
    "not_on_list": "That app did not ask for that site, so nothing was changed.",
    "one_at_a_time": "Say yes to one outside site at a time.",
    "cannot": "Your choice could not be saved on this computer, so nothing "
              "was changed.",
}


def set_app_web_hosts(app_id: str, hosts_on: list[str], *, by: str,
                      ticket: Optional[str] = None) -> dict:
    """Record which of *app_id*'s sites are on: *hosts_on*, never more.

    A site ends up on only if it is on when the change is written or its yes
    is spent in this call, so a site switched off while this call was in
    flight stays off. Switching a site OFF needs nothing. Switching one ON needs *ticket*: an
    owner-only consent ticket (``app_host_on``, target
    ``consent_tickets.target_app_host(app_id, site)``) minted for the device
    *by* names (``device:<id>`` or ``terminal:<id>``), spent here. One site
    can be switched on per call. Opens no socket.

    Returns ``{"ok": True, "door", "hosts": {site: on}, "switched_on",
    "switched_off", "bot_offline"}`` or ``{"ok": False, "refused": code,
    "message": plain words}``.
    """
    from puffpai.security import consent_tickets

    def _no(code: str, words: str = "") -> dict:
        return {"ok": False, "refused": code,
                "message": words or _WEB_SET_WORDS.get(code, "")}

    d = web_app_door(app_id)
    if d is None:
        return _no("unknown_app")
    if isinstance(hosts_on, (str, bytes)) \
            or not isinstance(hosts_on, (list, tuple, set, frozenset)):
        return _no("not_on_list")
    want: set[str] = set()
    for raw in hosts_on:
        host = _web_host(raw)
        if host not in d.hosts:
            return _no("not_on_list")
        want.add(host)
    # One lock across read, yes and write, and the write applied to what is
    # on the disk at that moment: a site can end up on only if it is on
    # there now or its yes was spent in this call. So a site switched off
    # while this call was in flight stays off.
    with _web_set_lock:
        current = _recorded_web_on(d)
        new_on = sorted(want - current)
        if len(new_on) > 1:
            return _no("one_at_a_time")
        if new_on:
            target = consent_tickets.target_app_host(app_id, new_on[0])
            ok, verdict = consent_tickets.redeem(ticket, by, "app_host_on", target)
            if not ok:
                return _no(verdict, consent_tickets.refusal_words(verdict))
        done = _write_web_hosts(d, app_id, keep=want, add=set(new_on), by=by)
    if done is None:
        return _no("cannot")
    before, after = done
    on, off = sorted(after - before), sorted(before - after)
    logger.info("egress: browser app %s: %d outside site(s) switched on, %d "
                "off", _shown(app_id), len(on), len(off))
    return {"ok": True, "door": d.id, "hosts": app_web_hosts(app_id),
            "switched_on": on, "switched_off": off,
            "bot_offline": run_posture()["run_egress"] != OPEN_POSTURE}


def _write_web_hosts(d: Door, app_id: str, *, keep: set[str], add: set[str],
                     by: str) -> "Optional[tuple[set[str], set[str]]]":
    """Write *d*'s sites as ``(on now & keep) | add``, where "on now" is read
    under the file lock. *add* must only hold sites whose yes was just spent.
    Returns ``(before, after)``, or None when it could not be saved (nothing
    changed). Call with :data:`_web_set_lock` held."""
    seen: dict[str, set[str]] = {}

    def build(existing: dict) -> dict:
        before = _web_on_in(existing.get(d.config_flag), d)
        after = (before & keep) | (add & set(d.hosts))
        seen["before"], seen["after"] = before, after
        return {"flag": d.config_flag, "kind": "web", "app": app_id,
                "open": bool(after), "hosts": list(d.hosts),
                "hosts_on": sorted(after), "by": _by_kind(by)}

    try:
        _store_web_consent(app_id, build)
    except (OSError, ValueError):
        return None
    return seen["before"], seen["after"]


def switch_off_web_hosts(app_id: str, hosts: list[str], *, by: str) -> dict:
    """Switch *hosts* of *app_id* off, and nothing else. Needs no yes.

    Unlike :func:`set_app_web_hosts` this names only the sites to switch
    off, so a site switched on (with its own yes) since the caller last
    looked is left as it is. Sites the app did not ask for are ignored.
    Returns the same shape as :func:`set_app_web_hosts`. Opens no socket.
    """
    d = web_app_door(app_id)
    if d is None:
        return {"ok": False, "refused": "unknown_app",
                "message": _WEB_SET_WORDS["unknown_app"]}
    if isinstance(hosts, (str, bytes)) \
            or not isinstance(hosts, (list, tuple, set, frozenset)):
        return {"ok": False, "refused": "not_on_list",
                "message": _WEB_SET_WORDS["not_on_list"]}
    gone = {_web_host(h) for h in hosts}
    with _web_set_lock:
        done = _write_web_hosts(d, app_id, keep=set(d.hosts) - gone, add=set(),
                                by=by)
    if done is None:
        return {"ok": False, "refused": "cannot",
                "message": _WEB_SET_WORDS["cannot"]}
    before, after = done
    off = sorted(before - after)
    logger.info("egress: browser app %s: %d outside site(s) switched off",
                _shown(app_id), len(off))
    return {"ok": True, "door": d.id, "hosts": app_web_hosts(app_id),
            "switched_on": [], "switched_off": off,
            "bot_offline": run_posture()["run_egress"] != OPEN_POSTURE}


def _web_url_code(url: Any) -> "tuple[str, str]":
    """``('', host)`` when *url* has the shape a web request may use, else
    ``(code, host_or_'')``. Opens nothing."""
    if not _keyed_url_ok(url):
        return "not_allowed", ""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return "not_allowed", ""
    host = _web_host(parts.hostname or "")
    if (parts.scheme or "").lower() != "https":
        return "not_https", host
    if parts.username is not None or parts.password is not None \
            or "@" in (parts.netloc or ""):
        return "userinfo", host
    if port is not None and port != 443:
        return "not_443", host
    if not host:
        return "not_allowed", ""
    return "", host


def _web_request_headers(headers: Any) -> "tuple[dict[str, str], list[str]]":
    """``(kept, dropped names)``: only :data:`WEB_REQUEST_HEADERS`, values
    printable, short and single-line."""
    kept: dict[str, str] = {}
    dropped: list[str] = []
    if headers is None:
        return kept, dropped
    if not isinstance(headers, Mapping):
        return kept, ["(not a list of headers)"]
    for name, value in headers.items():
        low = name.strip().lower() if isinstance(name, str) else ""
        ok_value = (isinstance(value, str) and len(value) <= _WEB_MAX_HEADER_VALUE
                    and value.isascii()
                    and all(32 <= ord(c) < 127 or c == "\t" for c in value))
        if low in WEB_REQUEST_HEADERS and ok_value and low not in kept:
            kept[low] = value
        else:
            dropped.append(name if isinstance(name, str) else type(name).__name__)
    return ({"-".join(p.capitalize() for p in k.split("-")): v
             for k, v in kept.items()}, dropped)


def _web_header_items(resp: Any) -> list:
    try:
        headers = getattr(resp, "headers", None)
        return list(headers.items()) if headers is not None else []
    except Exception:
        return []


def _web_header(resp: Any, name: str) -> str:
    """One answer header, whatever case the server wrote it in."""
    wanted = name.lower()
    for key, value in _web_header_items(resp):
        if isinstance(key, str) and key.strip().lower() == wanted:
            return str(value)
    return ""


def _web_answer_headers(resp: Any, echoes: "tuple[bytes, ...]") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in _web_header_items(resp):
        low = key.strip().lower() if isinstance(key, str) else ""
        if low not in WEB_ANSWER_HEADERS or low in out:
            continue
        text = str(value)
        if echoes:
            text = _credential_scrub(echoes, text.encode("utf-8", "replace")
                                     ).decode("utf-8", "replace")
        if len(text) > _WEB_MAX_HEADER_VALUE \
                or any(ord(c) < 32 and c != "\t" for c in text):
            continue
        if low == "content-length" and not text.strip().isdigit():
            continue
        out[low] = text
    return out


def _web_wire(method: str, url: str, headers: dict, body: Optional[bytes],
              timeout: float, pin: Any) -> Any:
    """THE network call for a browser app's outside request: pinned to the
    address that was checked, no redirects followed, no proxy, no cookies.
    Tests replace this one function."""
    import urllib.error
    import urllib.request
    from puffpai.security import pinned_net

    class _NoFollow(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            return None

    request = urllib.request.Request(url, data=body, headers=headers,
                                     method=method)
    opener = pinned_net.urllib_opener(pin, _NoFollow)
    try:
        return opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        return exc


def _web_status(resp: Any) -> int:
    for attr in ("status", "code"):
        value = getattr(resp, attr, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return 0


def _web_close(resp: Any) -> None:
    try:
        resp.close()
    except Exception:
        pass


def _web_size(value: Any, cap: int) -> Optional[int]:
    """A caller's size limit, clamped to *cap*; None when it is not one."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return min(value, cap)


# -- a refusal the owner should hear about -------------------------------------
#
# An app that reaches a site it has no place for (no door at all, or a site
# its door does not name) is refused, which on its own leaves its panels
# empty with no word why. The refusal stays exactly as it is; on top of it,
# the site is handed to the build's runtime capture
# (``puffpai.build.make.note_runtime_refusal``), which records ONE
# "Allow <app> to reach <host>" setup row per app, asked once. Nothing here
# opens anything, and a capture that fails never changes the refusal.

#: Tests (and the build layer, if it wants) set this; None means the build
#: layer's own capture, imported when first needed.
WEB_REFUSAL_HOOK: Optional[Callable[[str, str, str], Any]] = None


def _note_web_refusal(app_id: str, url: Any, code: str) -> None:
    """Hand one refusal (app, host, code) to the capture. Never raises."""
    try:
        bad, host = _web_url_code(url)
        if bad or not host or not is_registrable_hostname(host):
            return
        fn = WEB_REFUSAL_HOOK
        if fn is None:
            import importlib
            fn = getattr(importlib.import_module("puffpai.build.make"),
                         "note_runtime_refusal", None)
        if callable(fn):
            fn(app_id, host, code)
    except Exception as exc:  # noqa: BLE001 - the refusal itself already stands
        logger.debug("egress: refusal not handed on (%s)", type(exc).__name__)


def app_web_request(app_id: str, method: str, url: str, *,
                    body: Optional[bytes] = None,
                    headers: Optional[dict] = None,
                    key_handle: Optional[str] = None,
                    max_out: int = 1_000_000, max_in: int = 5_000_000) -> dict:
    """One outside request for browser app *app_id*. Never raises.

    Returns ``{"status", "headers", "body"}`` or ``{"refused": code}`` with
    *code* from :data:`WEB_REFUSALS`. Nothing is sent unless every check
    passes; see the section notes above for the rules.
    """
    from puffpai.security import egress_ledger, keys, pinned_net

    app = app_id if isinstance(app_id, str) else ""
    d = web_app_door(app)
    service = web_door_id(app) if d is not None else _WEB_UNKNOWN_SERVICE
    verb = method.strip().upper() if isinstance(method, str) else ""
    logged_verb = verb if verb in WEB_METHODS else "GET"

    def _line(target: Any, outcome: str, **fields: Any) -> None:
        code = fields.pop("code", "")
        if code:
            fields["code"] = _WEB_LEDGER_CODE.get(code, code)
        egress_ledger.record(service, host=egress_ledger.safe_host(target),
                             outcome=outcome, method=logged_verb,
                             what=_WEB_WHAT, **fields)

    def _refuse(code: str, target: Any = url, *, sent: int = 0) -> dict:
        _line(target, "refused", code=code, bytes_in=0, bytes_out=sent)
        return {"refused": code}

    if d is None:
        _note_web_refusal(app, url, "unknown_app")
        return _refuse("unknown_app")
    if verb not in WEB_METHODS:
        return _refuse("not_allowed")
    cap_out = _web_size(max_out, _WEB_MAX_OUT_CAP)
    cap_in = _web_size(max_in, _WEB_MAX_IN_CAP)
    if cap_out is None or cap_in is None:
        return _refuse("not_allowed")
    code, host = _web_url_code(url)
    if code:
        return _refuse(code)
    if _web_killed():
        _line(url, "refused", code="turned_off_here", bytes_in=0, bytes_out=0)
        return {"refused": "switched_off"}
    if host not in _recorded_web_on(d):
        if host not in d.hosts:
            # A site its door does not name: no switch could ever open it.
            _note_web_refusal(app, url, "not_on_list")
        return _refuse("switched_off")
    if run_posture()["run_egress"] != OPEN_POSTURE:
        return _refuse("bot_offline")
    if body is not None and not isinstance(body, (bytes, bytearray)):
        return _refuse("not_allowed")
    if body is not None and len(body) == 0:
        body = None
    if body is not None:
        if verb in ("GET", "HEAD"):
            return _refuse("not_allowed")
        if len(body) > cap_out:
            return _refuse("too_big")
        body = bytes(body)
    send, dropped = _web_request_headers(headers)
    if dropped:
        logger.info("browser app %s: request headers not sent: %s",
                    _shown(app), ", ".join(_shown(n) for n in dropped[:10]))
    sid = ""
    cred = None
    echoes: "tuple[bytes, ...]" = ()
    try:
        if key_handle is not None:
            sid = keys.service_of(key_handle)
            if not sid:
                return _refuse("key_unavailable")
            cred, key_code, echoes = _fetch_key_credential(
                sid, host, caller="app_web", only_target=True)
            if cred is None:
                return _refuse(key_code if key_code in WEB_REFUSALS
                               else "key_unavailable")
        current, current_host = url, host
        send_method, send_body = verb, body
        for hop in range(WEB_MAX_REDIRECTS + 1):
            try:
                answers = pinned_net.resolve_public(current_host, 443,
                                                    _WEB_RESOLVER)
            except pinned_net.PinRefused as exc:
                word = exc.code if exc.code in WEB_REFUSALS else "inside_address"
                return _refuse(word, current)
            pin = pinned_net.Pin(preset={current_host: answers})
            wire_headers = {"User-Agent": FEED_USER_AGENT,
                            "Accept-Encoding": "identity", **send}
            keyed = hop == 0 and cred is not None
            if keyed:
                extra = _credential_header_for(cred, current)
                if extra is None:
                    return _refuse("key_host", current)
                wire_headers[extra[0]] = extra[1]
                extra = None
            sent = _bytes_out(send_method, current, wire_headers, send_body) or 0
            try:
                resp = _web_wire(send_method, current, wire_headers, send_body,
                                 WEB_TIMEOUT, pin)
            except OSError as exc:
                refused = _pin_refused_code(exc)
                if refused:
                    return _refuse(refused if refused in WEB_REFUSALS
                                   else "inside_address", current)
                _line(current, "failed", code="no_answer", bytes_out=sent)
                return {"refused": "no_answer"}
            except (http.client.HTTPException, ValueError):
                _line(current, "failed", code="bad_answer", bytes_out=sent)
                return {"refused": "bad_answer"}
            finally:
                wire_headers = {}
            status = _web_status(resp)
            if keyed:
                cred = None  # a key rides the first hop only
                _fetch_key_answered(sid, current_host, status, caller="app_web")
            if status in _KEYED_REDIRECTS:
                location = _web_header(resp, "location")
                _web_close(resp)
                _line(current, "done", status=status, bytes_out=sent, bytes_in=0)
                if hop >= WEB_MAX_REDIRECTS:
                    return _refuse("redirects", current)
                nxt = urljoin(current, location) if location else ""
                code, nxt_host = _web_url_code(nxt)
                if not location or code == "not_allowed":
                    _line(current, "failed", code="bad_answer", bytes_out=0)
                    return {"refused": "bad_answer"}
                if code:
                    return _refuse(code, nxt)
                if _web_killed() or nxt_host not in _recorded_web_on(d):
                    return _refuse("redirect_off_list", nxt)
                if status == 303 or (status in (301, 302) and send_method == "POST"):
                    send_method = "HEAD" if send_method == "HEAD" else "GET"
                    send_body = None
                    send = {k: v for k, v in send.items() if k != "Content-Type"}
                current, current_host = nxt, nxt_host
                continue
            try:
                declared = _web_header(resp, "content-length").strip()
                if declared.isdigit() and int(declared) > cap_in:
                    _line(current, "refused", code="too_big", status=status,
                          bytes_out=sent, bytes_in=0)
                    return {"refused": "too_big"}
                data = b"" if send_method == "HEAD" else resp.read(cap_in + 1)
            except (OSError, http.client.HTTPException, ValueError):
                _line(current, "failed", code="no_answer", status=status,
                      bytes_out=sent)
                return {"refused": "no_answer"}
            finally:
                _web_close(resp)
            data = bytes(data) if isinstance(data, (bytes, bytearray)) else b""
            if len(data) > cap_in:
                _line(current, "refused", code="too_big", status=status,
                      bytes_out=sent, bytes_in=len(data))
                return {"refused": "too_big"}
            answer_headers = _web_answer_headers(resp, echoes)
            if echoes:
                data = _credential_scrub(echoes, data)
            if send_method != "HEAD" and "content-length" in answer_headers:
                # The length of what is handed back, not what the server said.
                answer_headers["content-length"] = str(len(data))
            _line(current, "done", status=status, bytes_out=sent,
                  bytes_in=len(data))
            return {"status": status, "headers": answer_headers, "body": data}
        return _refuse("redirects", current)  # pragma: no cover - loop returns
    except Exception as exc:  # noqa: BLE001 - never raise to the app's page
        logger.warning("browser app %s: outside request failed (%s)",
                       _shown(app), type(exc).__name__)
        _line(url, "failed", code="no_answer", bytes_out=0)
        return {"refused": "no_answer"}
    finally:
        cred = None
        echoes = ()


# ── build-time probe: one look at a catalog-listed keyless endpoint ──────────
#
# When BuildBot drafts a feed app from a live-data service it found in a
# catalog, it needs to see ONE sample reply to know which fields hold a picture,
# a number or a list. That look happens while building, so it is governed by
# the build-network answer (like :mod:`puffpai.source_fetch`), not by the run
# posture, and it is not a door: nothing the finished bot does can reach it.
#
# It is deliberately narrower than any door:
#   * the caller must say the build is online (the wizard's own answer), and
#     ``PUFFPAI_BUILD_PROBES=off`` refuses every probe regardless;
#   * https on 443 only, exactly the declared host, a registrable public name,
#     every resolved address public, and the socket pinned to the address
#     that was checked (the name is never looked up twice);
#   * ZERO redirects -- a 3xx is recorded and refused, never followed;
#   * GET only, fixed headers, no caller kwargs at all (so no credential, no
#     cookie, no body), and the reply is read up to ``max_bytes`` and no more.

#: Operator switch that refuses every build-time probe. Only "off" is honoured.
BUILD_PROBE_ENV = "PUFFPAI_BUILD_PROBES"

#: Hard ceiling on what one probe reads, whatever the caller asks for.
BUILD_PROBE_MAX_BYTES = 512 * 1024

#: The one User-Agent every probe and feed sends. Several public APIs (for
#: example MusicBrainz and Wikidata) refuse anonymous clients; this names the
#: product and nothing about the household.
from puffpai import USER_AGENT as FEED_USER_AGENT  # noqa: E402

_PROBE_DOOR = Door(
    id="build_probe", hosts=(), purpose="One look at a sample reply while "
    "building.", data_sent="A request for the sample address only.",
    config_flag="build_probe", env_var=BUILD_PROBE_ENV,
    settings_hint="The build could not take a sample look at that service.",
    default=False, dynamic_hosts=False,
)


#: What the owner is told when the Bridge holds a sample look back.
SAMPLE_LOOK_HELD_LINE = (
    "The Bridge is up and \"Looking at a service before adding it\" is not "
    "switched on, so nothing was fetched. You can switch it on under What "
    "leaves your computer.")


def sample_look_held() -> bool:
    """True when the Bridge (or her switch) keeps sample looks in. Fails closed."""
    try:
        from puffpai.security import outside_switches
        return outside_switches.is_off(SAMPLE_LOOK_SERVICE)
    except Exception:  # unreadable: held, never let out
        return True


def build_probe_get(url: str, *, host: str, build_online: bool,
                    max_bytes: int = BUILD_PROBE_MAX_BYTES,
                    timeout: float = DEFAULT_TIMEOUT,
                    resolver=None) -> dict[str, Any]:
    """Fetch one sample reply from *url* on *host*, or raise :class:`EgressBlocked`.

    Returns ``{"status", "content_type", "bytes", "truncated", "body"}`` where
    ``body`` is at most *max_bytes* (capped at :data:`BUILD_PROBE_MAX_BYTES`)
    raw bytes. The body is data; this function never parses or follows it.
    Every look -- and every refusal -- is one line in the outside-traffic log
    under the ``sample_look`` service, host only.
    """
    from puffpai.security import egress_ledger
    pd = _PROBE_DOOR

    def _refuse(reason: str, message: str, code: str) -> EgressBlocked:
        blocked = EgressBlocked(pd.id, reason, message, code=code)
        egress_ledger.record(SAMPLE_LOOK_SERVICE,
                             host=egress_ledger.safe_host(url),
                             outcome="refused", code=code, what="sample_look",
                             bytes_in=0, bytes_out=0)
        return blocked

    kill = os.environ.get(BUILD_PROBE_ENV)
    if kill is not None and _as_bool(kill) is False:
        raise _refuse(f"{BUILD_PROBE_ENV}=off",
                      "Sample looks at outside services are switched off on "
                      "this computer, so nothing was fetched.",
                      "turned_off_here")
    if _owner_switch(SAMPLE_LOOK_SERVICE) is False:
        raise _refuse("the owner switched sample looks off",
                      "Sample looks at outside services are switched off, so "
                      "nothing was fetched.", "switched_off")
    if sample_look_held():
        # With the Bridge up only an explicit ON lets a look
        # out (this file is a listed exit, so the connect-time backstop
        # cannot tell a look from any other request it makes).
        raise _refuse("the Bridge is up and sample looks are not switched on",
                      SAMPLE_LOOK_HELD_LINE, "bridge_up")
    if build_online is not True:
        raise _refuse("the build is not online",
                      "This build was set up to stay offline while building, "
                      "so nothing was fetched.", "bot_offline")
    want = _clean_host(host)
    if not is_registrable_hostname(want):
        raise _refuse(f"host {_shown(host)} is not a public name",
                      "That service's address was not a normal public web "
                      "address, so nothing was fetched.", "not_on_list")
    try:
        parts = urlsplit(str(url or "").strip())
        port = parts.port or 443
    except ValueError:
        raise _refuse("unparseable url", "That web address could not be read.",
                      "not_allowed")
    if parts.scheme.lower() != "https" or port != 443:
        raise _refuse("not https on 443",
                      "That request was refused because it was not an "
                      "ordinary encrypted connection.", "not_encrypted")
    if parts.username or parts.password:
        raise _refuse("url carries credentials", "That web address was refused.",
                      "not_allowed")
    got = _clean_host(parts.hostname or "")
    if got != want:
        raise _refuse(f"url host {got!r} is not {want!r}",
                      "That address was not on the service it claimed to be, "
                      "so nothing was fetched.", "not_on_list")
    try:
        answers = _check_resolved_public(pd, got, 443, resolver)
    except EgressBlocked as blocked:
        egress_ledger.record(SAMPLE_LOOK_SERVICE, host=got, outcome="refused",
                             code=reason_code(blocked), what="sample_look",
                             bytes_in=0, bytes_out=0)
        raise
    pin = _new_pin(resolver)
    pin_add(pin, got, answers)

    limit = max(1, min(int(max_bytes), BUILD_PROBE_MAX_BYTES))
    headers = {"User-Agent": FEED_USER_AGENT, "Accept": "application/json"}
    out = _bytes_out("GET", parts.geturl(), headers, None)
    try:
        resp = _send("GET", parts.geturl(), pin=pin, timeout=timeout,
                     allow_redirects=False, stream=True, headers=headers)
    except Exception:
        refused = getattr(pin, "refused", None)
        if refused is not None:
            raise _refuse(f"pinned connection refused: {refused.reason}",
                          "That request tried to reach an address on this "
                          "machine or this network, so nothing was sent.",
                          getattr(refused, "code", "inside_address")) from None
        egress_ledger.record(SAMPLE_LOOK_SERVICE, host=got, outcome="failed",
                             code="no_answer", what="sample_look",
                             bytes_out=out)
        raise
    total = 0
    try:
        _verify_peer(pd, resp, got)
        if resp.status_code in (301, 302, 303, 307, 308):
            egress_ledger.record(SAMPLE_LOOK_SERVICE, host=got, outcome="done",
                                 status=resp.status_code, what="sample_look",
                                 bytes_in=0, bytes_out=out)
            raise EgressBlocked(pd.id, f"redirect ({resp.status_code})",
                                "That service sent the request somewhere else, "
                                "so nothing was fetched.", code="redirected_away")
        chunks: list[bytes] = []
        truncated = False
        for chunk in resp.iter_content(chunk_size=16384):
            if not chunk:
                continue
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                truncated = True
                break
        body = b"".join(chunks)[:limit]
        egress_ledger.record(SAMPLE_LOOK_SERVICE, host=got, outcome="done",
                             status=resp.status_code, what="sample_look",
                             bytes_in=total, bytes_out=out)
        return {"status": int(resp.status_code),
                "content_type": str(resp.headers.get("Content-Type") or "")[:120],
                "bytes": len(body), "truncated": truncated, "body": body}
    finally:
        try:
            resp.close()
        except Exception:  # pragma: no cover - a stub transport
            pass


#: The outside-services id sample looks are logged and switched under.
SAMPLE_LOOK_SERVICE = "sample_look"


__all__ = [
    "APP_DOOR_PREFIX",
    "APPS_KILL_ENV",
    "CONFIG_SECTION",
    "OPEN_POSTURE",
    "MAX_REDIRECTS",
    "DEFAULT_TIMEOUT",
    "Door",
    "DOORS",
    "EgressBlocked",
    "BUILD_PROBE_ENV",
    "SAMPLE_LOOK_SERVICE",
    "BUILD_PROBE_MAX_BYTES",
    "FEED_USER_AGENT",
    "WEB_ANSWER_HEADERS",
    "WEB_DOOR_SUFFIX",
    "WEB_MAX_REDIRECTS",
    "WEB_METHODS",
    "WEB_REFUSALS",
    "WEB_REQUEST_HEADERS",
    "app_door",
    "app_door_consent",
    "app_door_flag",
    "app_door_id",
    "app_doors",
    "app_egress_get",
    "app_of",
    "app_web_hosts",
    "app_web_request",
    "clear_web_app_doors",
    "register_web_app_door",
    "set_app_web_hosts",
    "switch_off_web_hosts",
    "unregister_web_app_door",
    "web_app_door",
    "web_app_ids",
    "web_door_id",
    "build_probe_get",
    "clear_app_doors",
    "door",
    "door_ids",
    "door_states",
    "egress_allowed",
    "egress_get",
    "egress_request",
    "internal_address_reason",
    "is_app_door",
    "is_ip_literal",
    "is_registrable_hostname",
    "register_app_doors",
    "set_app_door_consent",
    "require_open",
    "reason_code",
    "resolve_addresses",
    "reset_cache",
    "run_posture",
    "settings_hint",
    "shut_reason",
]
