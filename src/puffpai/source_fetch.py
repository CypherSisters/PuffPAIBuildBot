"""Fetching a vetted source - the only door in the wizard that opens outward.

The other half of "go find what this bot needs to know". :mod:`puffpai.source_registry`
decides what may be offered; this module is the only thing that moves bytes, and
it is deliberately hard to talk into moving the wrong ones.

Eight rules, each of which is a test in ``tests/test_sourcing.py``:

* **Registry only, no open crawl.** :func:`fetch` takes a source id, or an
  address that matches a registry row *exactly*. There is no parameter that
  accepts an arbitrary URL, and no prefix or host matching that would let a
  caller smuggle a path onto a listed host. If it is not in the shipped file,
  this build cannot reach it.
* **License or nothing.** A row without a license is not fetchable, because it
  is not offerable, because :func:`puffpai.source_registry.offerable_sources`
  drops it below the layer any caller here can reach.
* **Size before bytes.** :func:`fetch` refuses unless the caller passes
  ``size_acknowledged=True``, which the wizard may only do after showing
  :func:`preview` - license, size, and address, together, in one screen. The
  server's own Content-Length is then checked against a ceiling derived from
  the registry, so a row that lies about being small does not become a silent
  50 GB download.
* **Private address space is not reachable.** Every hop is resolved and every
  resolved address must be globally routable: loopback, private, link-local,
  carrier-grade NAT, multicast, and reserved ranges are refused, in IPv4 and in
  IPv6, including the addresses that smuggle IPv4 inside IPv6. The check runs
  again on every hop, so a name that answers publicly once and privately on the
  redirect is refused on the redirect. Be precise about what that does and does
  not buy: we resolve, judge, and then hand the name to the transport, which
  resolves it a second time. A name that flips inside that window still wins.
  Closing it for real needs connect-time address pinning, which urllib does not
  give us; the registry allowlist is what carries the weight in the meantime,
  and the guard is the second lock, not the first.
* **robots.txt is honoured, and honoured fail-closed.** A missing robots file
  means allowed; a robots file we could not read means refused.
* **One host, one request at a time, not too fast.** Per-host rate limiting,
  and a redirect cap.
* **Provenance is recorded.** Source, license, fetch time, byte count, and a
  SHA-256 of exactly what landed, written beside the artifact. A pack can be
  audited later and revoked with :func:`revoke`.
* **Fetched content is DATA, forever.** Nothing in this module returns the body
  of a download. Not as a string, not inside a record, not in a summary line.
  The only thing that goes near a prompt is :func:`prompt_reference`, which
  emits the label, the license, the path, and the digest - never the text. A
  downloaded file that says "ignore previous instructions" is a file that says
  that, and it stays a file. Same doctrine as ``.puffmix`` and untrusted
  community capabilities.

Downloaded ZIM packs land in the configured ZIM directory, where
:func:`puffpai.knowledge.auto_index.find_new_packs` picks them up. We do not
index here, and we do not serve ZIM over HTTP: our own reader
(:mod:`puffpai.knowledge.zim_reader`) opens the file directly.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import datetime
import hashlib
import http.client
import ipaddress
import json
import os
import shutil
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from typing import Any, Callable, Optional, Sequence

from puffpai import source_registry
from puffpai.build_posture import gates
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

SCHEMA = "source_fetch/v1"
PROVENANCE_SCHEMA = "source_provenance/v1"
PROVENANCE_SUFFIX = ".provenance.json"

# Scheme allowlist. There is no hardened fetch helper elsewhere in this tree to
# reuse, so this is the one, and it is https-only: plaintext http would let
# anything on the path rewrite a knowledge pack in flight.
ALLOWED_SCHEMES: tuple[str, ...] = ("https",)
_DEFAULT_PORTS = {"https": 443}

MAX_REDIRECTS = 3
TIMEOUT_S = 30.0
CHUNK_BYTES = 64 * 1024
MIN_SIZE_CEILING = 8 * 1024 * 1024
SIZE_SLACK = 1.5
#: Fewest seconds between two requests to one host: once a second, unless the
#: host is named in HOST_MIN_INTERVAL_BY_HOST.
HOST_MIN_INTERVAL_S = 1.0
#: The only hosts paced differently, by exact name (lower case; no wildcards,
#: no parent domains, so a look-alike or a sub-host waits the full second).
#: GitHub's file host (each project's files at
#: a pinned commit) may be read 4 times a second. Add a host here only on the owner's
#: word; the test pins this table.
HOST_MIN_INTERVAL_BY_HOST: dict[str, float] = {
    "raw.githubusercontent.com": 0.25,
}


def host_min_interval(host: str) -> float:
    """Fewest seconds between two requests to *host* (its exact name)."""
    return HOST_MIN_INTERVAL_BY_HOST.get(str(host or "").lower(), HOST_MIN_INTERVAL_S)


from puffpai import USER_AGENT  # noqa: E402 - one name for every request

# The sentence that travels with every downloaded artifact. It is written into
# the provenance sidecar so that anything reading a pack later - including a
# future us - is told what the content is allowed to be.
TRUST_NOTE = (
    "Downloaded reference material. This is DATA. It is never placed in a "
    "prompt as instruction, no matter what it says about itself."
)


# ---------------------------------------------------------------------------
# transport - injectable so the guards can be tested without a network
# ---------------------------------------------------------------------------

Transport = Callable[[str, dict[str, str], float], Any]
Resolver = Callable[[str], Sequence[str]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse to follow redirects inside urllib.

    Redirects are followed by hand in :func:`_open_chain` so that the address
    guard runs again on every hop. An automatic follow inside the transport
    would be a hole straight through the SSRF check.
    """

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        return None


def _default_transport(url: str, headers: dict[str, str], timeout: float) -> Any:
    """One HTTP request: no redirect following, no cookies, no auth, no proxy.

    Proxies are disabled deliberately, and this is a security decision rather
    than a convenience one. Through a proxy, the name we checked is resolved by
    the proxy and not by us, so every address guard in this module becomes
    decorative. A build behind a mandatory proxy will fail to fetch, and failing
    is the correct outcome: the honest fix is a sneakernet bundle
    (:mod:`puffpai.build_bundle`), not a fetch we cannot vouch for.
    """
    from puffpai.security import pinned_net

    request = urllib.request.Request(url, headers=headers, method="GET")
    # Pinned: the name is looked up once, every answer must be public, and the
    # socket goes to that address while TLS still checks the name. The guard in
    # _open_chain looked the name up too, but a second lookup by urllib would
    # be the DNS-rebinding window.
    opener = pinned_net.urllib_opener(pinned_net.Pin(), _NoRedirect)
    try:
        return opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        # HTTPError is itself a readable response; a 30x or a 404 is an answer,
        # not a crash.
        return exc


def _default_resolver(host: str) -> list[str]:
    """Every address this host resolves to, as text. Empty on failure."""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return []
    return [str(info[4][0]) for info in infos]


def _status_of(response: Any) -> int:
    for attr in ("status", "code"):
        value = getattr(response, attr, None)
        if isinstance(value, int):
            return value
    return 0


def _header_of(response: Any, name: str) -> Optional[str]:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    try:
        value = getter(name)
    except Exception:  # pragma: no cover - defensive
        return None
    if value is None and isinstance(headers, dict):
        # Header names are case-insensitive. A real urllib answer uses a
        # case-insensitive message; a plain dict (the keyed hop's answer)
        # keeps the server's own spelling, so look again ignoring case.
        wanted = str(name).lower()
        for key, found in headers.items():
            if isinstance(key, str) and key.lower() == wanted:
                value = found
                break
    return None if value is None else str(value)


# ---------------------------------------------------------------------------
# the address guard
# ---------------------------------------------------------------------------

def address_is_public(text: str) -> bool:
    """True only for an address that is globally routable.

    Everything that could point back inside the house is refused: loopback,
    RFC1918, carrier-grade NAT, link-local (including the cloud metadata
    address), multicast, broadcast, reserved, and unspecified - and the same
    list again for IPv6, plus unique-local. IPv6 forms that carry an IPv4
    address inside them (v4-mapped, 6to4, Teredo, NAT64) are unwrapped and the
    embedded address is judged on its own merits, because
    ``::ffff:127.0.0.1`` is a loopback address wearing a hat.
    """
    try:
        addr = ipaddress.ip_address(str(text).strip())
    except ValueError:
        return False

    if isinstance(addr, ipaddress.IPv6Address):
        embedded = addr.ipv4_mapped or addr.sixtofour
        if embedded is None and addr.teredo is not None:
            embedded = addr.teredo[1]
        if embedded is None and addr in ipaddress.ip_network("64:ff9b::/96"):
            embedded = ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
        if embedded is not None:
            return address_is_public(str(embedded))

    if (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    ):
        return False
    # is_global is the affirmative test; the flags above are the explicit
    # statement of what we mean, so a future change to either is visible.
    return bool(addr.is_global)


def guard_url(
    url: str,
    *,
    allowed_hosts: Optional[set[str]] = None,
    resolver: Optional[Resolver] = None,
) -> tuple[bool, str]:
    """Is this address safe to open? Returns ``(ok, reason)``; never raises.

    Called before the first request and again before every redirect hop, so a
    name that resolves publicly once and privately a moment later is caught on
    the hop rather than trusted from the first lookup.
    """
    resolve = resolver or _default_resolver
    raw = str(url or "").strip()
    if not raw:
        return False, "No address was given."
    try:
        parts = urllib.parse.urlsplit(raw)
    except ValueError:
        return False, "That address could not be parsed."

    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        return False, f"Only {', '.join(ALLOWED_SCHEMES)} addresses are fetched; got '{scheme or 'none'}'."
    if parts.username or parts.password or "@" in (parts.netloc or ""):
        return False, "That address carries credentials, which we never send."

    host = (parts.hostname or "").strip().rstrip(".")
    if not host:
        return False, "That address has no host."
    try:
        port = parts.port
    except ValueError:
        return False, "That address has an invalid port."
    if port is not None and port != _DEFAULT_PORTS.get(scheme):
        return False, f"Only the default port is fetched; got port {port}."

    if allowed_hosts is not None and host.lower() not in {h.lower() for h in allowed_hosts}:
        return False, (
            f"'{host}' is not a host this source is allowed to redirect to."
        )

    addresses = list(resolve(host))
    if not addresses:
        return False, f"'{host}' did not resolve to any address."
    from puffpai.security.pinned_net import filter_block
    if filter_block(addresses, host):
        return False, FILTER_BLOCKED_LINE
    for address in addresses:
        if not address_is_public(address):
            return False, (
                f"'{host}' resolves to {address}, which is inside a private, "
                f"loopback, or reserved range. Refusing."
            )
    return True, "ok"


# ---------------------------------------------------------------------------
# per-host rate limiting and the robots cache
# ---------------------------------------------------------------------------

_state_lock = threading.Lock()
_last_request_at: dict[str, float] = {}
#: A shorter wait for one request, set per thread by :func:`quick_timeout`.
_quick = threading.local()


class quick_timeout:
    """Within this block, a request on THIS thread waits at most *seconds*.

    An owner's search asks the libraries at once, each on its own thread, and
    gives up on one that does not answer (``knowledge_resolver``); a library
    that hangs must not hold its socket for the full :data:`TIMEOUT_S`.
    Downloads never use it.
    """

    def __init__(self, seconds: float):
        self.seconds = max(0.5, float(seconds))

    def __enter__(self) -> "quick_timeout":
        self._before = getattr(_quick, "seconds", None)
        _quick.seconds = self.seconds
        return self

    def __exit__(self, *exc: Any) -> None:
        _quick.seconds = self._before


def request_timeout() -> float:
    """How long one request on this thread may wait for an answer."""
    seconds = getattr(_quick, "seconds", None)
    return min(TIMEOUT_S, seconds) if seconds else TIMEOUT_S
_robots_cache: dict[str, Optional[urllib.robotparser.RobotFileParser]] = {}


def clear_caches() -> None:
    """Forget rate-limit timers and cached robots files. For tests and re-runs."""
    with _state_lock:
        _last_request_at.clear()
        _robots_cache.clear()


def _rate_limit(
    host: str,
    sleeper: Callable[[float], None],
    clock: Callable[[], float],
) -> None:
    """Hold the host's minimum gap (:func:`host_min_interval`) between requests to it."""
    with _state_lock:
        last = _last_request_at.get(host)
        now = clock()
        wait = 0.0 if last is None else host_min_interval(host) - (now - last)
        _last_request_at[host] = now + max(wait, 0.0)
    if wait > 0:
        sleeper(wait)


# ---------------------------------------------------------------------------
# opening a chain of hops, re-guarding each one
# ---------------------------------------------------------------------------

#: The outside-services id every library request is logged and switched under.
LIBRARY_SERVICE = "library"

#: The book sites with their own row on "What leaves your computer": their
#: requests are logged and switched under their own id instead of the library's.
#: The value is how a refusal names them.
BOOK_SITE_SERVICES: dict[str, str] = {
    "wikipedia_books": "Wikipedia books",
    "gutenberg_books": "Project Gutenberg books",
    "openstax_books": "OpenStax textbooks",
    "wikibooks_books": "Wikibooks",
    "archive_books": "Internet Archive books",
}


def _service_words(service: str) -> str:
    return BOOK_SITE_SERVICES.get(service) or "The book library"


#: What the owner reads when a home DNS filter (Pi-hole, AdGuard Home) blocked
#: the name (security.pinned_net.filter_block).
FILTER_BLOCKED_LINE = ("That website is blocked by your network's filter, so "
                       "nothing was fetched.")


def _pin_refusal_code(exc: BaseException) -> str:
    """The pinned transport's refusal code inside *exc* (urllib wraps it), or ''."""
    from puffpai.security.pinned_net import PinRefused
    for candidate in (exc, getattr(exc, "reason", None), exc.__cause__,
                      exc.__context__):
        if isinstance(candidate, PinRefused):
            return candidate.code
    return ""


def _open_chain(
    url: str,
    allowed_hosts: set[str],
    headers: dict[str, str],
    transport: Transport,
    resolver: Optional[Resolver],
    sleeper: Callable[[float], None],
    clock: Callable[[], float],
    what: str = "book_download",
    service: str = LIBRARY_SERVICE,
) -> tuple[Optional[Any], str, Optional[str]]:
    """Open ``url``, following redirects by hand. Returns (response, final_url, refusal).

    The guard runs at the top of every iteration, not once before the loop, so
    a redirect to a name that now points at 127.0.0.1 is refused on the hop.
    The default transport is PINNED (:mod:`puffpai.security.pinned_net`), so
    the connection itself cannot be rebound between the guard and the dial.

    Every hop -- and every refusal -- is one line in the outside-traffic log
    under ``library`` (host only) -- or under the book site's own *service*
    id (:data:`BOOK_SITE_SERVICES`) -- and the owner's OFF switch for that
    service refuses before any socket opens.
    """
    from puffpai.security import egress_ledger, outside_switches

    if service not in BOOK_SITE_SERVICES:
        service = LIBRARY_SERVICE

    def _log(outcome: str, target: str, **fields: Any) -> None:
        # Host only: a book's address names the book the owner chose.
        egress_ledger.record(service, host=egress_ledger.safe_host(target),
                             outcome=outcome, what=what, **fields)

    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        if outside_switches.is_off(service):
            _log("refused", current, code="switched_off", bytes_in=0, bytes_out=0)
            return None, current, (
                f"{_service_words(service)} is switched off under \"What "
                "leaves your computer\", so nothing was fetched.")
        ok, reason = guard_url(current, allowed_hosts=allowed_hosts, resolver=resolver)
        if not ok:
            _log("refused", current,
                 code="network_filter" if reason == FILTER_BLOCKED_LINE
                 else "not_allowed", bytes_in=0, bytes_out=0)
            return None, current, reason

        host = (urllib.parse.urlsplit(current).hostname or "").lower()
        _rate_limit(host, sleeper, clock)
        sent = len(current) + sum(len(k) + len(v) + 4 for k, v in headers.items()) + 16
        try:
            response = transport(current, dict(headers), request_timeout())
        except OSError as exc:
            refused = _pin_refusal_code(exc)
            if refused:
                _log("refused", current, code=refused, bytes_in=0, bytes_out=0)
            else:
                _log("failed", current, code="no_answer", bytes_out=sent)
            return None, current, f"Could not reach that source: {exc}"

        status = _status_of(response)
        declared = _header_of(response, "Content-Length")
        _log("done", current, status=status, bytes_out=sent,
             bytes_in=int(declared) if (declared or "").isdigit() else None)
        if status in (301, 302, 303, 307, 308):
            location = _header_of(response, "Location")
            try:
                response.close()
            except Exception:  # pragma: no cover - defensive
                pass
            if not location:
                return None, current, "That source redirected without saying where."
            current = urllib.parse.urljoin(current, location)
            continue
        return response, current, None

    return None, current, f"That source redirected more than {MAX_REDIRECTS} times."


# ---------------------------------------------------------------------------
# small bounded GETs - catalog listings and .meta4 files, never packs
# ---------------------------------------------------------------------------

def get_small(
    url: str,
    *,
    allowed_hosts: set[str],
    max_bytes: int,
    honour_robots: bool = True,
    transport: Optional[Transport] = None,
    resolver: Optional[Resolver] = None,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    service: str = LIBRARY_SERVICE,
) -> tuple[Optional[bytes], str]:
    """One small GET through the same guards as a download. ``(body, reason)``.

    For catalog listings and ``.meta4`` files: metadata a few hundred KB long,
    read into memory, parsed by the caller as untrusted data. Every hop is
    re-guarded (https only, default port, no credentials, public addresses,
    ``allowed_hosts`` only), the per-host rate limit applies, and a body longer
    than ``max_bytes`` is refused rather than truncated, because half a feed
    parsed as a whole one is a quiet lie.

    ``honour_robots=False`` exists for exactly one caller: a library's own
    search API asked once because the owner typed a search (see
    ``config/catalog_index.json``, the ``robots`` note on each row). robots.txt
    is a rule for crawlers; a one-off lookup on a person's behalf is not a crawl.
    Downloads never pass it.
    """
    allowed = {str(h).strip().lower() for h in allowed_hosts if str(h).strip()}
    if not allowed:
        return None, "No host is allowed for this lookup."
    move = transport or _default_transport
    if honour_robots:
        ok, why = robots_allows(url, allowed_hosts=allowed, transport=move,
                                resolver=resolver, sleeper=sleeper, clock=clock,
                                service=service)
        if not ok:
            return None, why
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    what = ("book_lookup" if urllib.parse.urlsplit(str(url)).path.endswith(".meta4")
            else "library_search")
    if service in BOOK_SITE_SERVICES and what == "library_search":
        what = "book_text"
    response, _final, refusal = _open_chain(
        url, allowed, headers, move, resolver, sleeper, clock, what=what,
        service=service)
    if refusal is not None:
        return None, refusal
    assert response is not None
    try:
        status = _status_of(response)
        if status != 200:
            return None, f"The library answered {status}, so there is nothing to read."
        declared = _header_of(response, "Content-Length")
        try:
            if declared is not None and int(declared) > max_bytes:
                return None, "The library sent more than this lookup allows. Refused."
        except ValueError:
            pass
        body = response.read(max_bytes + 1)
    except OSError as exc:
        return None, f"Could not read the library's answer: {exc}"
    finally:
        try:
            response.close()
        except Exception:  # pragma: no cover - defensive
            pass
    if not isinstance(body, (bytes, bytearray)):
        return None, "The library's answer was not bytes."
    if len(body) > max_bytes:
        return None, "The library sent more than this lookup allows. Refused."
    return bytes(body), "ok"


# ---------------------------------------------------------------------------
# fetch_small (searches, READMEs, pictures, app parts) and
# get_archive (a project at its pinned version)
# ---------------------------------------------------------------------------
#
# Same guards as get_small, on every hop: https only, the default port, no
# user:password in the address, only ``allowed_hosts``, every address the
# name resolves to must be public, the connection is PINNED to the address
# that was checked (``pinned_net``), at most 2 redirects, each re-checked,
# and one line in the outside-traffic log per hop, host only (the path can
# name a project or a search the owner chose). The owner's one-tap OFF for
# the service refuses before any socket opens. Reasons are one word from
# ``egress_ledger.REASON_CODES`` (``REASON_CODES`` here is the same set).

from puffpai.security.egress_ledger import REASON_CODES  # noqa: E402
from puffpai.security.egress_ledger import R3_SERVICES as _LEDGER_R3_SERVICES  # noqa: E402


class FetchError(RuntimeError):
    """A refused or failed :func:`get_archive`. ``code`` is one word from
    :data:`REASON_CODES`; ``message`` is plain words."""

    def __init__(self, code: str, message: str = ""):
        self.code = code if code in REASON_CODES and code else "not_allowed"
        self.message = message or _FETCH_WORDS.get(self.code, _FETCH_WORDS["not_allowed"])
        super().__init__(self.message)


_FETCH_WORDS = {
    "not_allowed": "That address is not one this may fetch, so nothing was fetched.",
    "switched_off": ("That is switched off under \"What leaves your computer\", "
                     "so nothing was fetched."),
    "not_encrypted": ("That address does not use a secure connection, so "
                      "nothing was fetched."),
    "sign_in_refused": "That address carries a sign-in, which is never sent.",
    "host": "That website is not on the list for this, so nothing was fetched.",
    "cannot_look_up": "That website's name could not be found.",
    "inside_address": ("That address points inside your home network or this "
                       "computer, so nothing was fetched."),
    "network_filter": FILTER_BLOCKED_LINE,
    "peer_moved": ("That address points inside your home network or this "
                   "computer, so nothing was fetched."),
    "redirects": "The website sent it somewhere else too many times.",
    "too_big": "It was bigger than allowed, so it was not kept.",
    "sha_mismatch": ("The download was not the exact version that was picked, so "
                     "it was not kept."),
    "no_answer": "The website did not answer.",
    "bad_answer": "The website gave an answer this could not use.",
    "robots_rule": "That website's rules for robots do not allow this.",
}

#: Service id -> the ``egress_ledger.WHAT`` key its lines carry. Closed: a
#: caller cannot log under a name of its own choosing, and so cannot dodge the
#: owner's OFF switch for the real service.
#: (The table lives in ``egress_ledger`` so the keyed hop in the security
#: package reads the same one; this is a copy for readers of this module.)
R3_SERVICES: dict[str, str] = dict(_LEDGER_R3_SERVICES)

#: The ledger id a refusal is logged under when the caller named a service
#: this does not know (the caller's own text never becomes a service id).
UNKNOWN_SERVICE_ID = "fetch_small"

#: The services get_archive may download for.
ARCHIVE_SERVICES = frozenset({"code_download", "box_install", "reading_helper"})

SMALL_MAX_REDIRECTS = 2
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)

#: The only answer headers fetch_small hands back (lower-case).
SMALL_RESPONSE_HEADERS = ("etag", "last-modified", "content-type",
                          "x-ratelimit-limit", "x-ratelimit-remaining",
                          "x-ratelimit-reset", "x-ratelimit-resource",
                          "retry-after", "ratelimit", "ratelimit-policy", "link",
                          # GitHub: what a signed-in token may do (names only)
                          "x-oauth-scopes", "github-authentication-token-expiration")
#: The only request headers a caller may add.
SMALL_REQUEST_HEADERS = frozenset({"accept", "if-none-match", "if-modified-since"})
_MAX_HEADER_VALUE = 1024
_MAX_ANSWER_HEADER = 2048
_SHA256_RE_CHARS = frozenset("0123456789abcdef")


def _what_for(service: str) -> Optional[str]:
    if service in R3_SERVICES:
        return R3_SERVICES[service]
    if service == LIBRARY_SERVICE:
        return "library_search"
    if service in BOOK_SITE_SERVICES:
        return "book_text"
    return None


def _clean_hosts(allowed_hosts: Any) -> set[str]:
    if isinstance(allowed_hosts, str) or not allowed_hosts:
        return set()
    out = set()
    for h in allowed_hosts:
        text = str(h or "").strip().rstrip(".").lower()
        if text:
            out.add(text)
    return out


def url_chars_ok(url: Any) -> bool:
    """True when *url* is a string of printable ASCII with no space.

    Checked on the exact text that will be sent: a space, a control
    character (CR/LF included) or a non-ASCII character would make the HTTP
    library raise instead of refusing, and CR/LF could split the request.
    A caller percent-encodes such characters first (``%20`` for a space).
    """
    return (isinstance(url, str) and bool(url) and url.isascii()
            and all(32 < ord(ch) < 127 for ch in url))


def _hop_code(url: str, allowed: set[str], resolver: Optional[Resolver]) -> str:
    """'' when *url* may be opened, else the one-word reason."""
    if not url_chars_ok(url):
        return "not_allowed"
    try:
        parts = urllib.parse.urlsplit(str(url))
        port = parts.port
    except ValueError:
        return "not_allowed"
    if (parts.scheme or "").lower() not in ALLOWED_SCHEMES:
        return "not_encrypted"
    if parts.username is not None or parts.password is not None \
            or "@" in (parts.netloc or ""):
        return "sign_in_refused"
    if port is not None and port != _DEFAULT_PORTS["https"]:
        return "not_allowed"
    host = (parts.hostname or "").strip().rstrip(".").lower()
    if not host:
        return "not_allowed"
    if host not in allowed:
        return "host"
    resolve = resolver or _default_resolver
    addresses = list(resolve(host))
    if not addresses:
        return "cannot_look_up"
    from puffpai.security.pinned_net import filter_block
    if filter_block(addresses, host):
        return "network_filter"
    if any(not address_is_public(a) for a in addresses):
        return "inside_address"
    return ""


def _r3_chain(
    url: str,
    *,
    allowed: set[str],
    headers: dict[str, str],
    transport: Transport,
    resolver: Optional[Resolver],
    service: str,
    what: str,
    max_redirects: int,
    keyed_hop: Optional[Callable[[str, dict[str, str], float],
                                 tuple[Optional[Any], str]]] = None,
) -> tuple[Optional[Any], str, str, int]:
    """Open *url*, following at most *max_redirects* redirects by hand.

    Returns ``(response, final_url, code, bytes_out)``; ``code`` is '' when a
    final (non-redirect) response came back, which the caller logs after
    reading it. Every other hop is logged here, EXCEPT the keyed hop:
    *keyed_hop* sends the FIRST request instead of *transport* (the sign-up
    key is added, sent and removed again inside ``puffpai.security``; this
    module never holds it). The security package checks the owner's switch
    and writes that hop's outside-traffic line itself, whatever the outcome,
    so this function writes none for it. It answers ``(response, "ok")`` or
    ``(None, reason)``. Redirects never carry the key.
    """
    from puffpai.security import egress_ledger, outside_switches

    def _log(outcome: str, target: str, **fields: Any) -> None:
        egress_ledger.record(service, host=egress_ledger.safe_host(target),
                             outcome=outcome, what=what, **fields)

    current = url
    for hop in range(max_redirects + 1):
        if outside_switches.is_off(service):
            _log("refused", current, code="switched_off", bytes_in=0, bytes_out=0)
            return None, current, "switched_off", 0
        code = _hop_code(current, allowed, resolver)
        if code:
            _log("refused", current, code=code, bytes_in=0, bytes_out=0)
            return None, current, code, 0
        send = dict(headers)
        host = (urllib.parse.urlsplit(current).hostname or "").lower()
        _rate_limit(host, time.sleep, time.monotonic)
        sent = len(current) + sum(len(k) + len(v) + 4 for k, v in send.items()) + 16
        keyed = hop == 0 and keyed_hop is not None
        try:
            if keyed:
                # Logged (every outcome) inside the security package.
                response, key_code = keyed_hop(current, send, request_timeout())
                if response is None:
                    if key_code not in REASON_CODES or not key_code:
                        key_code = "key_unavailable"
                    return None, current, key_code, 0
            else:
                response = transport(current, send, request_timeout())
        except OSError as exc:
            refused = _pin_refusal_code(exc)
            if refused:
                code = refused if refused in REASON_CODES else "inside_address"
                _log("refused", current, code=code, bytes_in=0, bytes_out=0)
                return None, current, code, 0
            _log("failed", current, code="no_answer", bytes_out=sent)
            return None, current, "no_answer", 0
        except (http.client.HTTPException, ValueError):
            # A garbled status line or header block, or an address the HTTP
            # library will not send (url_chars_ok already refuses those).
            _log("failed", current, code="bad_answer", bytes_out=sent)
            return None, current, "bad_answer", 0
        finally:
            send = {}
        status = _status_of(response)
        if status in _REDIRECT_STATUSES:
            location = _header_of(response, "Location")
            try:
                response.close()
            except Exception:  # pragma: no cover - defensive
                pass
            if not keyed:
                _log("done", current, status=status, bytes_out=sent, bytes_in=0)
            if not location:
                return None, current, "bad_answer", 0
            current = urllib.parse.urljoin(current, location)
            continue
        return response, current, "", sent
    _log("refused", current, code="redirects", bytes_in=0, bytes_out=0)
    return None, current, "redirects", 0


def _r3_robots(url: str, allowed: set[str], move: Transport,
               resolver: Optional[Resolver], service: str) -> str:
    """robots.txt for *url*'s host, fail closed, logged under *service*.

    Returns '' when the robots rules allow *url*. Otherwise the one honest
    word: the chain's own reason when the robots file could not be asked for
    (the switch is off, an inside address, no DNS answer, offline, ...),
    ``no_answer`` when its answer broke off part way, and ``robots_rule``
    only when the website's rules say no (an error answer for the robots
    file is read the robots-standard way: do not fetch).
    """
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if not host:
        return "not_allowed"
    with _state_lock:
        known = host in _robots_cache
        cached = _robots_cache.get(host)
    if known:
        if cached is None or cached.can_fetch(USER_AGENT, url):
            return ""
        return "robots_rule"
    robots_url = urllib.parse.urlunsplit(("https", parts.netloc, "/robots.txt", "", ""))
    response, _final, code, sent = _r3_chain(
        robots_url, allowed=set(allowed),
        headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
        transport=move, resolver=resolver, service=service, what="robots_rules",
        max_redirects=SMALL_MAX_REDIRECTS)
    if code or response is None:
        # The chain already wrote this hop's line, with the same word.
        return code if (code and code in REASON_CODES) else "no_answer"
    from puffpai.security import egress_ledger
    status = _status_of(response)
    raw = b""
    broke = False
    try:
        if status == 200:
            raw = response.read(256 * 1024)
    except (OSError, http.client.HTTPException, ValueError):
        broke = True
    finally:
        try:
            response.close()
        except Exception:  # pragma: no cover - defensive
            pass
    if broke:
        egress_ledger.record(service, host=host, outcome="failed", code="no_answer",
                             what="robots_rules", status=status, bytes_out=sent)
        return "no_answer"
    egress_ledger.record(service, host=host, outcome="done", what="robots_rules",
                         status=status, bytes_out=sent, bytes_in=len(raw or b""))
    if status in (404, 410):
        with _state_lock:
            _robots_cache[host] = None
        return ""
    if status != 200:
        return "robots_rule"
    parser = urllib.robotparser.RobotFileParser()
    try:
        parser.parse(raw.decode("utf-8", errors="replace").splitlines())
    except Exception:
        return "robots_rule"
    with _state_lock:
        _robots_cache[host] = parser
    return "" if parser.can_fetch(USER_AGENT, url) else "robots_rule"


def _request_headers(headers: Any) -> Optional[dict[str, str]]:
    """The caller's extra headers when every one is allowed, else None."""
    out: dict[str, str] = {}
    if headers is None:
        return out
    if not isinstance(headers, dict):
        return None
    for name, value in headers.items():
        if not isinstance(name, str) or name.strip().lower() not in SMALL_REQUEST_HEADERS:
            return None
        if not isinstance(value, str) or len(value) > _MAX_HEADER_VALUE \
                or not value.isascii() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            return None
        out["-".join(p.capitalize() for p in name.strip().lower().split("-"))] = value
    return out


def _answer_headers(response: Any) -> dict[str, str]:
    """Only the :data:`SMALL_RESPONSE_HEADERS`, lower-case names, one value each."""
    found: dict[str, str] = {}
    headers = getattr(response, "headers", None)
    items: list = []
    try:
        if headers is not None and hasattr(headers, "items"):
            items = list(headers.items())
    except Exception:  # pragma: no cover - defensive
        items = []
    for name, value in items:
        key = str(name).strip().lower()
        if key not in SMALL_RESPONSE_HEADERS or key in found:
            continue
        text = str(value)
        if any(ord(c) < 32 and c != "\t" for c in text):
            continue
        found[key] = text[:_MAX_ANSWER_HEADER]
    return found


def fetch_small(url: str, *, allowed_hosts: set[str], max_bytes: int,
                service: str, honour_robots: bool = True,
                allow_truncated: bool = False, headers: dict | None = None,
                key_id: str | None = None, transport=None,
                resolver=None) -> dict:
    """One small GET. Never raises for a refusal.

    Returns ``{"body": bytes | None, "reason": str, "status": int | None,
    "truncated": bool, "headers": {lower-case name: value}}``. ``reason`` is
    ``"ok"`` when an answer came back (whatever its status: a 304 or a 403
    rate limit is an answer), else one word from :data:`REASON_CODES`.

    * ``headers``: only ``Accept``, ``If-None-Match``, ``If-Modified-Since``;
      anything else refuses the request (``header_refused``) before sending.
    * ``allow_truncated``: a longer body gives its first ``max_bytes`` bytes
      and ``truncated=True`` instead of ``too_big``.
    * ``key_id``: a sign-up key from the vault, bound to the hosts its curated
      row names (``key_host`` for any other), on the first request only.
    """
    from puffpai.security import egress_doors, egress_ledger

    result: dict[str, Any] = {"body": None, "reason": "not_allowed", "status": None,
                              "truncated": False, "headers": {}}
    what = _what_for(str(service or ""))

    def _refuse(code: str) -> dict:
        # Every refusal is a line, even one for a service name this does not
        # know (logged under a fixed id: the caller's text never becomes one).
        logged_as = service if what is not None else UNKNOWN_SERVICE_ID
        egress_ledger.record(logged_as, host=egress_ledger.safe_host(url),
                             outcome="refused", code=code,
                             what=what or "request", bytes_in=0, bytes_out=0)
        result["reason"] = code
        return result

    allowed = _clean_hosts(allowed_hosts)
    if what is None or not allowed or isinstance(max_bytes, bool) \
            or not isinstance(max_bytes, int) or max_bytes <= 0:
        return _refuse("not_allowed")

    extra = _request_headers(headers)
    if extra is None:
        return _refuse("header_refused")
    try:
        parts = urllib.parse.urlsplit(str(url or ""))
        host = (parts.hostname or "").rstrip(".").lower()
        _ = parts.port
    except ValueError:
        return _refuse("not_allowed")
    if key_id is not None:
        if transport is not None:
            # A sign-up key only ever travels on the security package's own
            # pinned connection, never through a caller's transport.
            return _refuse("key_unavailable")
        key_hosts = egress_doors.fetch_key_hosts(key_id)
        if key_hosts is None:
            return _refuse("key_unavailable")
        if egress_doors._ascii_host(host) not in key_hosts:  # noqa: SLF001
            return _refuse("key_host")
    move = transport or _default_transport
    # robots.txt only for a host on the list: asking an unlisted host for its
    # robots file would already be a request to it. The chain refuses those.
    if honour_robots and host in allowed and (parts.scheme or "").lower() == "https":
        robots_code = _r3_robots(str(url), allowed, move, resolver, service)
        if robots_code:
            # The honest word: robots_rule only when the website's rules say
            # no; otherwise why its robots file could not be read (already one
            # log line). Nothing more is sent either way.
            result["reason"] = robots_code
            return result
    send = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity", **extra}
    keyed_hop = None
    keyed_answers: list = []
    if key_id is not None:
        def _send_keyed(hop_url: str, hop_headers: dict[str, str],
                        timeout: float) -> tuple[Optional[Any], str]:
            # The ONLY caller egress_doors.keyed_first_hop accepts. The
            # security package checks the owner's switch for *service* and
            # writes this hop's outside-traffic line itself.
            answer, reason = egress_doors.keyed_first_hop(
                key_id, hop_url, hop_headers, service=service,
                max_bytes=max_bytes, allow_truncated=bool(allow_truncated),
                timeout=timeout)
            if answer is not None:
                keyed_answers.append(answer)
            return answer, reason
        keyed_hop = _send_keyed
    try:
        response, final, code, sent = _r3_chain(
            str(url), allowed=allowed, headers=send, transport=move,
            resolver=resolver, service=service, what=what,
            max_redirects=SMALL_MAX_REDIRECTS, keyed_hop=keyed_hop)
        if code:
            result["reason"] = code
            return result
        assert response is not None
        final_host = egress_ledger.safe_host(final)
        status = _status_of(response)
        result["status"] = status
        if keyed_answers and response is keyed_answers[0]:
            # Sent, sized and logged inside the security package: the body is
            # at most max_bytes (+1 when cut) with the key's echo removed.
            body = response.read(max_bytes + 1)
            response.close()
            body = bytes(body if isinstance(body, (bytes, bytearray)) else b"")
            if len(body) > max_bytes or response.raw_length > max_bytes:
                body = body[:max_bytes]
                result["truncated"] = True
            result["body"] = body
            result["headers"] = _answer_headers(response)
            result["reason"] = "ok"
            return result
        try:
            declared = _header_of(response, "Content-Length")
            if not allow_truncated and (declared or "").isdigit() \
                    and int(declared) > max_bytes:
                body = None
            else:
                body = response.read(max_bytes + 1)
        except (OSError, http.client.HTTPException, ValueError):
            egress_ledger.record(service, host=final_host, outcome="failed",
                                 code="no_answer", what=what, status=status,
                                 bytes_out=sent)
            result["reason"] = "no_answer"
            return result
        finally:
            try:
                response.close()
            except Exception:  # pragma: no cover - defensive
                pass
        if body is not None and not isinstance(body, (bytes, bytearray)):
            egress_ledger.record(service, host=final_host, outcome="failed",
                                 code="bad_answer", what=what, status=status,
                                 bytes_out=sent)
            result["reason"] = "bad_answer"
            return result
        size = len(body or b"")
        if body is None or (size > max_bytes and not allow_truncated):
            egress_ledger.record(service, host=final_host, outcome="refused",
                                 code="too_big", what=what, status=status,
                                 bytes_out=sent, bytes_in=size if body else 0)
            result["reason"] = "too_big"
            return result
        body = bytes(body)
        if size > max_bytes or len(body) > max_bytes:
            body = body[:max_bytes]
            result["truncated"] = True
        egress_ledger.record(service, host=final_host, outcome="done", what=what,
                             status=status, bytes_out=sent,
                             bytes_in=min(size, max_bytes))
        result["body"] = body
        result["headers"] = _answer_headers(response)
        result["reason"] = "ok"
        return result
    finally:
        keyed_hop = None
        send = {}


def _unlink_quiet(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _archive_dest(dest_path: Any) -> str:
    if not isinstance(dest_path, str) or not dest_path or "\0" in dest_path \
            or not os.path.isabs(dest_path):
        raise FetchError("not_allowed", "The download needs a full path to "
                                        "save to, so nothing was fetched.")
    dest = os.path.abspath(dest_path)
    from puffpai.security import safe_archive
    try:
        forbidden = safe_archive.vault_and_key_roots()
    except Exception:
        raise FetchError("not_allowed", "Could not tell where your vault is, "
                                        "so nothing was fetched.") from None
    real = os.path.normcase(os.path.realpath(dest))
    for root in forbidden:
        r = os.path.normcase(os.path.realpath(root)).rstrip("\\/")
        if real == r or real.startswith(r + os.sep):
            raise FetchError("not_allowed", "A download is never saved next to "
                                            "your vault or keys.")
    for candidate in (dest, dest + ".part"):
        if os.path.lexists(candidate) and (os.path.islink(candidate)
                                           or os.path.isdir(candidate)
                                           or _is_link_like(candidate)):
            raise FetchError("not_allowed", "The place to save the download is "
                                            "not a plain file, so nothing was "
                                            "fetched.")
    return dest


def _is_link_like(path: str) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return bool((getattr(st, "st_file_attributes", 0) or 0) & 0x400)


def get_archive(url: str, *, allowed_hosts: set[str], max_bytes: int,
                dest_path: str, service: str,
                expect_sha256: str | None = None,
                max_redirects: int = 2, key_id: str | None = None) -> dict:
    """Download one archive to *dest_path*, streamed and counted.

    Returns ``{"path", "sha256", "bytes", "final_host"}``. Raises
    :class:`FetchError` (``.code`` in :data:`REASON_CODES`) when refused or
    failed; nothing is left at *dest_path* then. ``expect_sha256`` refuses
    any other bytes (``sha_mismatch``). ``max_redirects`` is capped at 2.

    ``key_id``: a sign-up key from the vault on the FIRST request only (as
    :func:`fetch_small`): sent, logged and switch-checked inside the security
    package, bound to its curated hosts, never on a redirect. GitHub's archive
    address for a private project answers with a redirect to a short-lived
    download address; that next hop is an ordinary request with no key, and
    its address is never written anywhere (the log keeps the host only).
    """
    from puffpai.security import egress_doors, egress_ledger

    svc = str(service or "")
    if svc not in ARCHIVE_SERVICES:
        raise FetchError("not_allowed")
    what = R3_SERVICES[svc]
    allowed = _clean_hosts(allowed_hosts)
    if not allowed or isinstance(max_bytes, bool) or not isinstance(max_bytes, int) \
            or max_bytes <= 0:
        raise FetchError("not_allowed")
    expect = None
    if expect_sha256 is not None:
        expect = str(expect_sha256).strip().lower()
        if len(expect) != 64 or not set(expect) <= _SHA256_RE_CHARS:
            raise FetchError("not_allowed", "The expected version check is not "
                                            "a proper fingerprint, so nothing "
                                            "was fetched.")
    try:
        redirects = max(0, min(int(max_redirects), SMALL_MAX_REDIRECTS))
    except (TypeError, ValueError):
        redirects = 0
    dest = _archive_dest(dest_path)
    part = dest + ".part"

    keyed_hop = None
    if key_id is not None:
        key_hosts = egress_doors.fetch_key_hosts(key_id)
        if key_hosts is None:
            raise FetchError("key_unavailable", "Your sign-in could not be used, so "
                                                "nothing was fetched.")
        try:
            first_host = (urllib.parse.urlsplit(str(url)).hostname or "").rstrip(".").lower()
        except ValueError:
            raise FetchError("not_allowed") from None
        if egress_doors._ascii_host(first_host) not in key_hosts:  # noqa: SLF001
            raise FetchError("key_host", "Your sign-in may not be sent there, so "
                                         "nothing was fetched.")
        # The keyed hop reads at most this much itself (GitHub answers with a
        # redirect, so normally nothing); a bigger first answer is refused.
        first_max = min(max_bytes, 64 * 1024 * 1024 - 1)

        def _send_keyed(hop_url: str, hop_headers: dict[str, str],
                        timeout: float) -> tuple[Optional[Any], str]:
            # Checked, sent and logged inside the security package.
            return egress_doors.keyed_first_hop(
                key_id, hop_url, hop_headers, service=svc, max_bytes=first_max,
                allow_truncated=False, timeout=timeout)
        keyed_hop = _send_keyed

    try:
        response, final, code, sent = _r3_chain(
            str(url), allowed=allowed,
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
            transport=_default_transport, resolver=None, service=svc, what=what,
            max_redirects=redirects, keyed_hop=keyed_hop)
    finally:
        keyed_hop = None
    if code:
        raise FetchError(code)
    assert response is not None
    final_host = egress_ledger.safe_host(final)

    def _line(outcome: str, **fields: Any) -> None:
        egress_ledger.record(svc, host=final_host, outcome=outcome, what=what,
                             bytes_out=sent, **fields)

    status = _status_of(response)
    if status != 200:
        try:
            response.close()
        except Exception:  # pragma: no cover - defensive
            pass
        _line("failed", code="bad_answer", status=status, bytes_in=0)
        raise FetchError("bad_answer", f"The website answered {status}, so "
                                       f"nothing was downloaded.")
    declared = _header_of(response, "Content-Length")
    if (declared or "").isdigit() and int(declared) > max_bytes:
        try:
            response.close()
        except Exception:  # pragma: no cover - defensive
            pass
        _line("refused", code="too_big", status=status, bytes_in=0)
        raise FetchError("too_big")

    digest = hashlib.sha256()
    count = 0
    failure = ""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    # A leftover .part is removed and a NEW file made: opening an existing one
    # for writing would write through a hard link into some other file.
    _unlink_quiet(part)
    try:
        with open(part, "xb") as fh:
            while True:
                try:
                    chunk = response.read(CHUNK_BYTES)
                except (OSError, http.client.HTTPException, ValueError):
                    failure = "no_answer"
                    break
                if not chunk:
                    break
                if not isinstance(chunk, (bytes, bytearray)):
                    failure = "bad_answer"
                    break
                count += len(chunk)
                if count > max_bytes:
                    failure = "too_big"
                    break
                digest.update(chunk)
                fh.write(chunk)
    except OSError:
        failure = failure or "no_answer"
    finally:
        try:
            response.close()
        except Exception:  # pragma: no cover - defensive
            pass
    got = digest.hexdigest()
    if not failure and expect is not None and got != expect:
        failure = "sha_mismatch"
    if failure:
        _unlink_quiet(part)
        _line("refused" if failure in ("too_big", "sha_mismatch") else "failed",
              code=failure, status=status, bytes_in=count)
        raise FetchError(failure)
    try:
        os.replace(part, dest)
    except OSError:
        _unlink_quiet(part)
        _line("failed", code="no_answer", status=status, bytes_in=count)
        raise FetchError("no_answer", "The download could not be saved.") from None
    _line("done", status=status, bytes_in=count)
    return {"path": dest, "sha256": got, "bytes": count, "final_host": final_host}


# ---------------------------------------------------------------------------
# robots.txt - fail closed
# ---------------------------------------------------------------------------

def robots_allows(
    url: str,
    *,
    allowed_hosts: Optional[set[str]] = None,
    transport: Optional[Transport] = None,
    resolver: Optional[Resolver] = None,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    service: str = LIBRARY_SERVICE,
) -> tuple[bool, str]:
    """Does this host's robots.txt permit us to fetch ``url``?

    A 404 means there are no rules and we may proceed. Anything else that is
    not a readable 200 - a 500, a timeout, a body we cannot decode - means we
    do not know the rules, and not knowing is refusal. Cached per host.
    """
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if not host:
        return False, "That address has no host."

    with _state_lock:
        known = host in _robots_cache
        cached = _robots_cache.get(host)
    if known:
        if cached is None:
            return True, "That host publishes no robots.txt, so nothing is disallowed."
        allowed = bool(cached.can_fetch(USER_AGENT, url))
        return allowed, "ok" if allowed else "That host's robots.txt disallows this path."

    robots_url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
    hosts = set(allowed_hosts or {host})
    hosts.add(host)
    response, _final, refusal = _open_chain(
        robots_url, hosts, {"User-Agent": USER_AGENT},
        transport or _default_transport, resolver, sleeper, clock,
        what="robots_rules", service=service,
    )
    if refusal is not None:
        return False, f"Could not read robots.txt for '{host}', so we are not fetching: {refusal}"

    assert response is not None
    status = _status_of(response)
    try:
        if status in (401, 403):
            return False, f"'{host}' refused to show robots.txt, so we are not fetching."
        if status in (404, 410):
            with _state_lock:
                _robots_cache[host] = None
            return True, "That host publishes no robots.txt, so nothing is disallowed."
        if status != 200:
            return False, f"'{host}' answered {status} for robots.txt, so we are not fetching."
        raw = response.read(256 * 1024)
    except OSError as exc:
        return False, f"Could not read robots.txt for '{host}', so we are not fetching: {exc}"
    finally:
        try:
            response.close()
        except Exception:  # pragma: no cover - defensive
            pass

    parser = urllib.robotparser.RobotFileParser()
    try:
        text = raw.decode("utf-8", errors="replace")
        parser.parse(text.splitlines())
    except Exception:
        return False, f"Could not understand robots.txt for '{host}', so we are not fetching."
    with _state_lock:
        _robots_cache[host] = parser
    allowed = bool(parser.can_fetch(USER_AGENT, url))
    return allowed, "ok" if allowed else "That host's robots.txt disallows this path."


# ---------------------------------------------------------------------------
# posture, destinations, and names
# ---------------------------------------------------------------------------

def _posture_refusal(posture: Any, *, downloading: bool) -> Optional[str]:
    """Why this posture forbids online sourcing, or None if it permits it.

    Fail-closed on an absent posture. :func:`puffpai.build_posture.gates` treats
    an empty dict as the friendly default (online), which is right for a menu
    and wrong for a socket: "nobody has answered step 0 yet" must never mean
    "go ahead and download". A posture assembled by ``assess_posture`` always
    carries ``build_network``, defaulted or stated, so the honest path is
    unaffected.
    """
    if not isinstance(posture, dict) or not posture.get("build_network"):
        return (
            "No build posture has been recorded yet, so the wizard will not "
            "reach the internet. Choose a posture first."
        )
    permitted = gates(posture)
    if not permitted.get("may_offer_online_sourcing"):
        return (
            "This build is set to offline while building, so the wizard does "
            "not offer or fetch online sources."
        )
    if downloading and not permitted.get("may_download_packs"):
        return "This build's posture does not permit downloading packs."
    return None


def _safe_filename(entry: dict[str, Any]) -> Optional[str]:
    """The on-disk name for a source, taken from the registry and nothing else.

    Never from Content-Disposition and never from the final URL: a redirect
    must not get to choose a filename. A name with a separator, a drive letter,
    or a parent reference is refused rather than sanitised.
    """
    name = str(entry.get("filename") or "").strip()
    if not name:
        return None
    if name in (".", "..") or name != os.path.basename(name):
        return None
    if any(ch in name for ch in ("/", "\\", ":", "\0")) or name.startswith("."):
        return None
    return name


def destination_dir(entry: dict[str, Any], dest_dir: Optional[str] = None) -> str:
    """Where a source of this kind lands.

    ZIM packs go to the watched ZIM directory so
    :func:`puffpai.knowledge.auto_index.find_new_packs` sees them on the next
    scan. Everything else goes to a ``sources`` folder under the data dir.
    """
    if dest_dir:
        return os.path.abspath(dest_dir)
    from puffpai.config import get_config
    cfg = get_config()
    if str(entry.get("kind")) == "zim":
        configured = str(getattr(cfg.knowledge, "zim_dir", "") or "").strip()
        if configured:
            return os.path.abspath(configured)
        return os.path.join(cfg.DATA_DIR, "zim")
    return os.path.join(cfg.DATA_DIR, "sources")


def _within(root: str, candidate: str) -> bool:
    root_real = os.path.realpath(root)
    cand_real = os.path.realpath(candidate)
    return cand_real == root_real or cand_real.startswith(root_real + os.sep)


def _size_ceiling(entry: dict[str, Any], override: Optional[int]) -> int:
    if override is not None and override > 0:
        return int(override)
    try:
        declared = float(entry.get("size_mb") or 0)
    except (TypeError, ValueError):
        declared = 0.0
    return max(int(declared * SIZE_SLACK * 1024 * 1024), MIN_SIZE_CEILING)


#: Room left over after a download, so a big book never fills the disk the
#: bot, its index and the owner's own files live on.
DISK_HEADROOM_BYTES = 512 * 1024 * 1024


def _disk_free(path: str) -> Optional[int]:
    """Free bytes on the disk holding ``path``, or ``None`` if it cannot be read."""
    try:
        return int(shutil.disk_usage(path).free)
    except (OSError, ValueError):
        return None


def _room_refusal(source_id: str, folder: str, needed: int,
                  part_path: str) -> Optional[dict[str, Any]]:
    """A refusal when the disk cannot hold the rest of the download. Fails closed."""
    try:
        already = os.path.getsize(part_path) if os.path.exists(part_path) else 0
    except OSError:
        already = 0
    remaining = max(int(needed) - already, 0)
    free = _disk_free(folder)
    if free is None:
        return _refusal(source_id, (
            "Could not tell how much free space the download folder has, so "
            "nothing was downloaded."), status="no_room")
    if free < remaining + DISK_HEADROOM_BYTES:
        return _refusal(source_id, (
            f"Not enough free space: this needs about {_mb(remaining + DISK_HEADROOM_BYTES)} "
            f"free (the download plus room to spare) and the disk has about "
            f"{_mb(free)}. Nothing was downloaded."), status="no_room")
    return None


def _mb(num_bytes: int) -> str:
    value = num_bytes / (1024 * 1024)
    if value >= 1024:
        return f"{value / 1024:.1f} GB"
    return f"{value:.0f} MB"


def _size_phrase(size_mb: Any) -> str:
    try:
        value = float(size_mb)
    except (TypeError, ValueError):
        return "an unknown amount"
    if value >= 1024:
        return f"about {value / 1024:.1f} GB"
    return f"about {value:.0f} MB"


# ---------------------------------------------------------------------------
# preview - license, size, address, before a byte moves
# ---------------------------------------------------------------------------

def preview(
    source_id: str,
    posture: Optional[dict[str, Any]],
    registry: Optional[dict[str, Any]] = None,
    dest_dir: Optional[str] = None,
) -> dict[str, Any]:
    """What the owner is shown before anything is downloaded. No network at all.

    Returns the licence, the approximate size, the exact address, and where the
    file would land. :func:`fetch` will not run until a caller has been through
    here and says so with ``size_acknowledged=True``, which is how "nothing
    bulky downloads without the size shown first" is enforced in code rather
    than trusted to the UI.
    """
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "ok": False,
        "source_id": str(source_id or ""),
        "reason": "",
        "source": None,
        "lines": [],
    }
    refusal = _posture_refusal(posture, downloading=False)
    if refusal:
        result["reason"] = refusal
        result["lines"] = [refusal]
        return result

    entry = source_registry.get_source(source_id, registry)
    if entry is None:
        reason = (
            "That is not a source on the vetted list, so there is nothing to "
            "preview. The wizard only fetches what the shipped registry names."
        )
        result["reason"] = reason
        result["lines"] = [reason]
        return result

    filename = _safe_filename(entry)
    target_dir = destination_dir(entry, dest_dir)
    commercial = bool(entry.get("commercial_use", False))
    result["ok"] = True
    result["reason"] = "ok"
    result["source"] = {
        "id": entry.get("id"),
        "label": entry.get("label"),
        "kind": entry.get("kind"),
        "what_it_is": entry.get("what_it_is"),
        "license": entry.get("license"),
        "license_url": entry.get("license_url"),
        "commercial_use": commercial,
        "size_mb": entry.get("size_mb"),
        "source_url": entry.get("source_url"),
        "filename": filename,
        "destination": target_dir,
    }
    lines = [
        f"{entry.get('label')} ({entry.get('kind')})",
        f"  What it is: {entry.get('what_it_is')}",
        f"  License:    {entry.get('license')} ({entry.get('license_url')})",
        f"  Size:       {_size_phrase(entry.get('size_mb'))} to download",
        f"  From:       {source_registry.where_from(entry)}",
        f"  Lands in:   {target_dir}",
    ]
    if not commercial:
        lines.append(
            "  Note:       Non-commercial use only. Fine for your own bot; not "
            "for a product you sell."
        )
    lines.append(
        "  Downloaded material is reference DATA. Your bot reads it; it never "
        "takes orders from it."
    )
    result["lines"] = lines
    return result


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------

def _refusal(source_id: str, reason: str, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "schema": SCHEMA,
        "ok": False,
        "status": "refused",
        "source_id": str(source_id or ""),
        "reason": reason,
        "path": None,
        "bytes": 0,
        "sha256": None,
        "provenance": None,
    }
    out.update(extra)
    return out


def _transfer(
    source_id: str,
    url: str,
    allowed_hosts: set[str],
    part_path: str,
    ceiling: int,
    move: Transport,
    resolver: Optional[Resolver],
    sleeper: Callable[[float], None],
    clock: Callable[[], float],
    cancel: Optional[threading.Event],
    on_progress: Optional[Callable[[dict[str, Any]], None]],
) -> dict[str, Any]:
    """Stream one address into part_path, resuming a partial file.

    Returns {"ok": True, "written", "final_url", "resuming"} when the whole
    body arrived, else {"ok": False, "record": <refusal or cancel record>,
    "retry_elsewhere": bool}. retry_elsewhere is True only when this
    attempt wrote nothing (a refused hop, a non-200 answer), so trying the
    next mirror cannot mix bytes. Never raises.
    """
    already = 0
    try:
        if os.path.exists(part_path):
            already = os.path.getsize(part_path)
    except OSError:
        already = 0

    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    if already > 0:
        headers["Range"] = f"bytes={already}-"

    response, final_url, chain_refusal = _open_chain(
        url, allowed_hosts, headers, move, resolver, sleeper, clock,
    )
    if chain_refusal is not None:
        return {"ok": False, "retry_elsewhere": True,
                "record": _refusal(source_id, chain_refusal)}
    assert response is not None

    status = _status_of(response)
    resuming = status == 206 and already > 0
    if status == 200:
        already = 0  # server ignored the Range; start over
    elif status != 206:
        try:
            response.close()
        except Exception:  # pragma: no cover - defensive
            pass
        return {"ok": False, "retry_elsewhere": True, "record": _refusal(
            source_id, f"That source answered {status}; nothing was downloaded.")}

    declared_length = _header_of(response, "Content-Length")
    try:
        remaining_declared = int(declared_length) if declared_length else None
    except ValueError:
        remaining_declared = None
    if remaining_declared is not None and already + remaining_declared > ceiling:
        try:
            response.close()
        except Exception:  # pragma: no cover - defensive
            pass
        return {"ok": False, "retry_elsewhere": False, "record": _refusal(source_id, (
            f"That source says it is {(already + remaining_declared) // (1024 * 1024)} MB, "
            f"which is far larger than the {ceiling // (1024 * 1024)} MB the registry "
            f"led us to expect. Refusing rather than filling the disk."
        ))}

    total_expected = None if remaining_declared is None else already + remaining_declared
    written = already
    cancelled = False
    try:
        with open(part_path, "ab" if resuming else "wb") as handle:
            if not resuming:
                handle.truncate(0)
                written = 0
            while True:
                if cancel is not None and cancel.is_set():
                    cancelled = True
                    break
                chunk = response.read(CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > ceiling:
                    handle.flush()
                    return {"ok": False, "retry_elsewhere": False, "record": _refusal(source_id, (
                        f"That source sent more than the {ceiling // (1024 * 1024)} MB "
                        f"ceiling for this entry. Stopped; the partial file was kept."
                    ), path=part_path, bytes=written)}
                handle.write(chunk)
                if on_progress is not None:
                    try:
                        on_progress({
                            "source_id": source_id,
                            "downloaded": written,
                            "total": total_expected,
                        })
                    except Exception:  # pragma: no cover - a bad callback is not our failure
                        pass
    except OSError as exc:
        return {"ok": False, "retry_elsewhere": False, "record": _refusal(
            source_id, f"The download stopped: {exc}", path=part_path, bytes=written)}
    finally:
        try:
            response.close()
        except Exception:  # pragma: no cover - defensive
            pass

    if cancelled:
        return {"ok": False, "retry_elsewhere": False, "record": {
            "schema": SCHEMA,
            "ok": False,
            "status": "cancelled",
            "source_id": source_id,
            "reason": (
                "Download cancelled. The part that arrived is kept, so asking "
                "again picks up where this left off."
            ),
            "path": part_path,
            "bytes": written,
            "sha256": None,
            "provenance": None,
            "resumable": True,
        }}

    # A short read is not a finished download. Keeping the .part file means the
    # next call resumes; renaming it into place would mean a truncated pack got
    # indexed and a provenance record vouched for it.
    if total_expected is not None and written != total_expected:
        return {"ok": False, "retry_elsewhere": False, "record": _refusal(source_id, (
            f"That download stopped early: {written} bytes of {total_expected}. "
            f"The part that arrived is kept, so asking again resumes it."
        ), status="incomplete", path=part_path, bytes=written, resumable=True)}
    return {"ok": True, "written": written, "final_url": final_url,
            "resuming": resuming}


def _fetch_kiwix_book(
    entry: dict[str, Any],
    *,
    dest_dir: Optional[str],
    move: Transport,
    resolver: Optional[Resolver],
    sleeper: Callable[[float], None],
    clock: Callable[[], float],
    cancel: Optional[threading.Event],
    on_progress: Optional[Callable[[dict[str, Any]], None]],
    max_bytes: Optional[int],
) -> dict[str, Any]:
    """Download the current issue of one Kiwix book, checked against the library.

    1. Ask the library for the newest listing of the book (by name), then read
       that issue's ``.meta4``: exact size, SHA-256, mirror list.
    2. Refuse if the issue grew past the ceiling of the size the owner saw.
    3. Start at the library's own address; follow a redirect only to a host
       the ``.meta4`` names. If the redirect goes elsewhere, try the named
       mirrors directly, in the library's order.
    4. The bytes must be exactly the size and SHA-256 the ``.meta4`` gave, or
       nothing is installed.
    """
    from puffpai.catalogs import kiwix_opds

    source_id = str(entry.get("id"))
    name = str(entry.get("kiwix_book") or "")
    flavour = str(entry.get("kiwix_flavour") or "")

    def getter(url: str, *, allowed_hosts: set[str], max_bytes: int,
               honour_robots: bool) -> tuple[Optional[bytes], str]:
        return get_small(url, allowed_hosts=allowed_hosts, max_bytes=max_bytes,
                         honour_robots=honour_robots, transport=move,
                         resolver=resolver, sleeper=sleeper, clock=clock)

    try:
        plan = kiwix_opds.download_plan(
            name, flavour, getter=getter,
            fallback_meta4_url=str(entry.get("meta4_url") or ""))
    except kiwix_opds.CatalogError as exc:
        return _refusal(source_id, (
            f"Could not find the current copy of this book in the Kiwix library, "
            f"so nothing was downloaded. {exc}"))

    filename = str(plan["filename"])
    if _safe_filename({"filename": filename}) != filename \
            or not kiwix_opds.ZIM_FILENAME_RE.match(filename):
        return _refusal(source_id, "The library named a file this build will not write.")
    size = int(plan["size"])
    ceiling = _size_ceiling(entry, max_bytes)
    if size > ceiling:
        return _refusal(source_id, (
            f"The current copy of this book is {size // (1024 * 1024)} MB, more than "
            f"the {_size_phrase(entry.get('size_mb'))} you were shown. Nothing was "
            f"downloaded; look at it again and say yes to the new size."))

    target_dir = destination_dir(entry, dest_dir)
    final_path = os.path.join(target_dir, filename)
    part_path = final_path + ".part"
    if not _within(target_dir, final_path):  # pragma: no cover - _safe_filename covers it
        return _refusal(source_id, "That file name would escape the download folder.")
    try:
        os.makedirs(target_dir, exist_ok=True)
    except OSError as exc:
        return _refusal(source_id, f"Could not prepare the download folder: {exc}")

    already = read_provenance(final_path)
    if os.path.isfile(final_path) and already \
            and str(already.get("sha256") or "") == plan["sha256"] \
            and not already.get("revoked_at"):
        return {"schema": SCHEMA, "ok": True, "status": "already_here",
                "source_id": source_id, "reason": "ok", "path": final_path,
                "bytes": int(already.get("bytes") or 0), "sha256": plan["sha256"],
                "resumed": False, "provenance": already}

    no_room = _room_refusal(source_id, target_dir, size, part_path)
    if no_room is not None:
        return no_room

    allowed = {str(h).lower() for h in plan["allowed_hosts"]}
    primary = str(plan["primary_url"])
    urls = [primary] + [u for u in plan["urls"] if u != primary]
    last: Optional[dict[str, Any]] = None
    moved: dict[str, Any] = {}
    for url in urls:
        ok, why = robots_allows(url, allowed_hosts=allowed, transport=move,
                                resolver=resolver, sleeper=sleeper, clock=clock)
        if not ok:
            last = _refusal(source_id, why)
            continue
        moved = _transfer(source_id, url, allowed, part_path, size, move,
                          resolver, sleeper, clock, cancel, on_progress)
        if moved.get("ok"):
            break
        last = moved["record"]
        if not moved.get("retry_elsewhere"):
            return last
    if not moved.get("ok"):
        return last or _refusal(source_id, "No mirror of this book answered.")

    written, final_url = int(moved["written"]), str(moved["final_url"])
    if written != size:
        return _refusal(source_id, (
            f"That download stopped early: {written} bytes of {size}. The part "
            f"that arrived is kept, so asking again resumes it."),
            status="incomplete", path=part_path, bytes=written, resumable=True)

    digest = hashlib.sha256()
    try:
        with open(part_path, "rb") as handle:
            for block in iter(lambda: handle.read(CHUNK_BYTES), b""):
                digest.update(block)
    except OSError as exc:
        return _refusal(source_id, f"Could not finish the download: {exc}", path=part_path)
    digest_hex = digest.hexdigest()
    if digest_hex != plan["sha256"]:
        rejected = final_path + ".rejected"
        try:
            os.replace(part_path, rejected)
        except OSError:  # pragma: no cover - defensive
            rejected = part_path
        return _refusal(source_id, (
            "The downloaded file does not match the fingerprint the Kiwix library "
            "published for it. Nothing was installed; the file was set aside. "
            "Treat this as a possibly tampered or broken copy."),
            status="checksum_mismatch", path=rejected, bytes=written, sha256=digest_hex)

    try:
        os.replace(part_path, final_path)
    except OSError as exc:
        return _refusal(source_id, f"Could not finish the download: {exc}", path=part_path)

    record = _provenance(entry, final_path, final_url, written, digest_hex)
    record.update({
        "source_url": primary,
        "sha256_pinned": True,
        "pin_source": "kiwix_meta4",
        "kiwix_book": name,
        "kiwix_flavour": flavour,
        "kiwix_issue": plan.get("updated") or "",
        "added": str(entry.get("provenance") or "shipped"),
        "added_via": str(entry.get("added_via") or ""),
    })
    _write_provenance(final_path, record)
    logger.info("Fetched Kiwix book %s (%s bytes, SHA-256 checked) to %s",
                source_id, written, final_path)
    return {"schema": SCHEMA, "ok": True, "status": "fetched", "source_id": source_id,
            "reason": "ok", "path": final_path, "bytes": written,
            "sha256": digest_hex, "resumed": bool(moved.get("resuming")),
            "provenance": record}


def fetch(
    source: str,
    posture: Optional[dict[str, Any]],
    *,
    size_acknowledged: bool = False,
    dest_dir: Optional[str] = None,
    registry: Optional[dict[str, Any]] = None,
    transport: Optional[Transport] = None,
    resolver: Optional[Resolver] = None,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    cancel: Optional[threading.Event] = None,
    on_progress: Optional[Callable[[dict[str, Any]], None]] = None,
    max_bytes: Optional[int] = None,
) -> dict[str, Any]:
    """Download one vetted source. Never raises; refusals come back as records.

    ``source`` is a registry id, or an address that matches a registry row
    exactly. Anything else is refused - there is no way to hand this function a
    URL of your own choosing and have it fetched.

    **Wiring rule for whoever calls this.** ``registry`` exists so tests can
    supply a fixture, and it is the one parameter that can widen what this
    module may reach. It must never be filled from a request body, a query
    string, an uploaded file, or anything else a user can shape. The API and
    CLI pass nothing and get the shipped file; that is the intended and only
    production path.

    The returned record carries the path, the byte count, the SHA-256, and the
    provenance block. It never carries the content. Resuming an interrupted
    download is automatic: the partial file is kept as ``<name>.part`` and the
    next call asks for the rest with a Range request. Setting ``cancel`` stops
    the transfer at the next chunk and leaves the partial file in place, so a
    cancel is a pause.
    """
    ident = str(source or "").strip()

    refusal = _posture_refusal(posture, downloading=True)
    if refusal:
        return _refusal(ident, refusal)

    # Registry only. An id, or an exact address from the shipped list. Nothing
    # else resolves, so an off-registry URL cannot be fetched by any caller.
    entry = source_registry.get_source(ident, registry)
    if entry is None and "://" in ident:
        entry = source_registry.source_for_url(ident, registry)
    if entry is None:
        return _refusal(ident, (
            "That is not a source on the vetted list. The wizard only fetches "
            "addresses that are spelled out in the shipped source registry."
        ))

    source_id = str(entry.get("id"))
    problem = source_registry.entry_problem(entry)
    if problem is not None:  # pragma: no cover - offerable_sources already filtered
        return _refusal(source_id, f"That source cannot be offered: {problem}")

    if not size_acknowledged:
        return _refusal(source_id, (
            f"This download is {_size_phrase(entry.get('size_mb'))}. Show the "
            f"size, license, and address first, then confirm."
        ), status="needs_confirmation", preview=preview(source_id, posture, registry, dest_dir))

    if entry.get("kiwix_book"):
        return _fetch_kiwix_book(
            entry, dest_dir=dest_dir, move=transport or _default_transport,
            resolver=resolver, sleeper=sleeper, clock=clock, cancel=cancel,
            on_progress=on_progress, max_bytes=max_bytes)

    filename = _safe_filename(entry)
    if not filename:
        return _refusal(source_id, "That source has no usable filename in the registry.")

    target_dir = destination_dir(entry, dest_dir)
    final_path = os.path.join(target_dir, filename)
    part_path = final_path + ".part"
    if not _within(target_dir, final_path):  # pragma: no cover - _safe_filename covers it
        return _refusal(source_id, "That source's filename would escape the download folder.")
    try:
        os.makedirs(target_dir, exist_ok=True)
    except OSError as exc:
        return _refusal(source_id, f"Could not prepare the download folder: {exc}")

    url = str(entry.get("source_url"))
    origin_host = (urllib.parse.urlsplit(url).hostname or "").lower()
    allowed_hosts = {origin_host}
    for extra_host in entry.get("redirect_hosts") or []:
        allowed_hosts.add(str(extra_host).strip().lower())
    allowed_hosts.discard("")

    move = transport or _default_transport

    allowed, why = robots_allows(
        url, allowed_hosts=allowed_hosts, transport=move,
        resolver=resolver, sleeper=sleeper, clock=clock,
    )
    if not allowed:
        return _refusal(source_id, why)

    ceiling = _size_ceiling(entry, max_bytes)
    no_room = _room_refusal(source_id, target_dir, ceiling, part_path)
    if no_room is not None:
        return no_room
    moved = _transfer(source_id, url, allowed_hosts, part_path, ceiling, move,
                      resolver, sleeper, clock, cancel, on_progress)
    if not moved.get("ok"):
        return moved["record"]
    written, final_url, resuming = moved["written"], moved["final_url"], moved["resuming"]

    digest = hashlib.sha256()
    try:
        with open(part_path, "rb") as handle:
            for block in iter(lambda: handle.read(CHUNK_BYTES), b""):
                digest.update(block)
    except OSError as exc:
        return _refusal(source_id, f"Could not finish the download: {exc}", path=part_path)
    digest_hex = digest.hexdigest()

    # Optional pinning. Where a maintainer could verify a digest, the registry
    # carries it and a mismatch is a hard stop: this is the only defence in the
    # module against a listed host that has been compromised or has silently
    # replaced a file. Most rows cannot carry one - the Kiwix library reissues
    # packs on its own schedule - and a pin we invented would be worse than no
    # pin, so the field is optional and its absence is stated in the record.
    expected = str(entry.get("sha256") or "").strip().lower()
    if expected and digest_hex != expected:
        rejected = final_path + ".rejected"
        try:
            os.replace(part_path, rejected)
        except OSError:  # pragma: no cover - defensive
            rejected = part_path
        return _refusal(source_id, (
            "That download does not match the checksum the registry pins for "
            "it. Nothing was installed. The file was set aside for inspection; "
            "treat this as a possible tampered or replaced source."
        ), status="checksum_mismatch", path=rejected, bytes=written, sha256=digest_hex)

    try:
        os.replace(part_path, final_path)
    except OSError as exc:
        return _refusal(source_id, f"Could not finish the download: {exc}", path=part_path)

    record = _provenance(entry, final_path, final_url, written, digest_hex)
    _write_provenance(final_path, record)
    logger.info(
        "Fetched vetted source %s (%s bytes) to %s", source_id, written, final_path,
    )
    return {
        "schema": SCHEMA,
        "ok": True,
        "status": "fetched",
        "source_id": source_id,
        "reason": "ok",
        "path": final_path,
        "bytes": written,
        "sha256": record["sha256"],
        "resumed": resuming,
        "provenance": record,
    }


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------

def _provenance(
    entry: dict[str, Any],
    path: str,
    final_url: str,
    size: int,
    sha256: str,
) -> dict[str, Any]:
    """The audit record for one downloaded artifact. Metadata only, by design."""
    return {
        "schema": PROVENANCE_SCHEMA,
        "source_id": entry.get("id"),
        "label": entry.get("label"),
        "kind": entry.get("kind"),
        "domain": entry.get("domain"),
        "source_url": entry.get("source_url"),
        "final_url": final_url,
        "license": entry.get("license"),
        "license_url": entry.get("license_url"),
        "commercial_use": bool(entry.get("commercial_use", False)),
        "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "bytes": int(size),
        "sha256": sha256,
        # Whether that digest was checked against a pin, or merely measured.
        # The difference matters to anyone auditing this pack later.
        "sha256_pinned": bool(str(entry.get("sha256") or "").strip()),
        "filename": os.path.basename(path),
        "path": path,
        "registry_schema": source_registry.SCHEMA,
        "content_trust": "untrusted-data",
        "trust_note": TRUST_NOTE,
        "revoked_at": None,
    }


def _write_provenance(path: str, record: dict[str, Any]) -> None:
    try:
        with open(path + PROVENANCE_SUFFIX, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2, sort_keys=True)
    except OSError as exc:  # pragma: no cover - disk trouble is reported, not fatal
        logger.error("Could not write provenance for %s: %s", path, exc)


def read_provenance(path: str) -> Optional[dict[str, Any]]:
    """The audit record beside a downloaded artifact, or None."""
    try:
        with open(str(path) + PROVENANCE_SUFFIX, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def provenance_records(directory: str) -> list[dict[str, Any]]:
    """Every audit record in a folder, oldest first. The auditable half of a build."""
    records: list[dict[str, Any]] = []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return records
    for name in names:
        if not name.endswith(PROVENANCE_SUFFIX):
            continue
        artifact = os.path.join(directory, name[: -len(PROVENANCE_SUFFIX)])
        record = read_provenance(artifact)
        if record is not None:
            records.append(record)
    records.sort(key=lambda r: str(r.get("fetched_at") or ""))
    return records


def chunk_metadata(provenance: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """The provenance subset that is stamped onto every chunk of a pack.

    Deliberately small, and deliberately made only of things *we* control: the
    registry id, our own label and license strings, the fetch time, and the
    digest we measured. ``final_url`` is excluded even though the record has it,
    because a redirect target is a string the far end chose, and nothing the far
    end chose belongs in metadata that will later be rendered next to an answer.

    Returns ``None`` for a pack with no provenance, so an unlabelled pack stays
    unlabelled rather than acquiring an invented default.
    """
    if not isinstance(provenance, dict) or not provenance.get("source_id"):
        return None
    fields = {
        "source_id": provenance.get("source_id"),
        "source_label": provenance.get("label"),
        "source_license": provenance.get("license"),
        "source_license_url": provenance.get("license_url"),
        "source_url": provenance.get("source_url"),
        "source_fetched_at": provenance.get("fetched_at"),
        "source_sha256": provenance.get("sha256"),
        "source_sha256_pinned": bool(provenance.get("sha256_pinned", False)),
        "source_commercial_use": bool(provenance.get("commercial_use", False)),
    }
    return {k: v for k, v in fields.items() if v is not None and v != ""}


def _matching_chunk_ids(collection: Any, source_id: str) -> Optional[list[str]]:
    """Ids of every chunk labelled with this source, or None if we cannot ask.

    None and an empty list mean very different things - "the store would not
    answer" versus "the store answered, and there are none" - and revocation
    reports them differently, because only one of them is a completed job.
    """
    if collection is None:
        return None
    where = {"source_id": str(source_id)}
    # include=[] asks for ids only, which is far cheaper on a large collection,
    # but not every ChromaDB version accepts an empty include. Fall through to
    # the plain call on ANY failure of the first form, and only give up when
    # both have failed - a store that will not answer must return None, never
    # an empty list, because "none found" and "could not ask" lead to opposite
    # conclusions about whether a revocation finished.
    last_error: Optional[BaseException] = None
    for kwargs in ({"where": where, "include": []}, {"where": where}):
        try:
            got = collection.get(**kwargs)
        except Exception as exc:  # noqa: BLE001 - try the next call shape
            last_error = exc
            continue
        ids = (got or {}).get("ids") or []
        # Chroma returns a flat id list from get(); a nested one would be query().
        if ids and isinstance(ids[0], list):
            ids = [i for group in ids for i in group]
        return [str(i) for i in ids]
    logger.warning("Knowledge store would not answer for %s: %s", source_id, last_error)
    return None


def evict_source(source_id: str, collection: Any) -> dict[str, Any]:
    """Delete every chunk labelled with ``source_id`` from a collection.

    Reports what it found, what it deleted, and what is still there. ``ok`` is
    True only when the store was reachable, chunks were found, and a re-count
    afterwards shows none left. A partial eviction reports ``ok`` False with the
    remaining count, because "mostly revoked" is the failure mode this whole
    exercise exists to eliminate.
    """
    sid = str(source_id or "").strip()
    if not sid:
        return {"ok": False, "found": None, "evicted": 0, "remaining": None,
                "reason": "No source id was given, so nothing could be evicted."}

    before = _matching_chunk_ids(collection, sid)
    if before is None:
        return {"ok": False, "found": None, "evicted": 0, "remaining": None,
                "reason": (
                    "The knowledge store could not be reached, so nothing was "
                    "evicted. The content is still there and still answering."
                )}
    if not before:
        return {"ok": False, "found": 0, "evicted": 0, "remaining": 0,
                "reason": "No chunks in the knowledge store carry that source id."}

    try:
        collection.delete(ids=before)
    except Exception as exc:
        return {"ok": False, "found": len(before), "evicted": 0,
                "remaining": len(before),
                "reason": f"The knowledge store refused the delete: {exc}"}

    after = _matching_chunk_ids(collection, sid)
    if after is None:
        return {"ok": False, "found": len(before), "evicted": None,
                "remaining": None,
                "reason": (
                    "The delete was sent but the store would not confirm what "
                    "is left, so this revocation cannot be called complete."
                )}
    evicted = len(before) - len(after)
    if after:
        return {"ok": False, "found": len(before), "evicted": evicted,
                "remaining": len(after),
                "reason": (
                    f"Evicted {evicted} of {len(before)} chunks; {len(after)} "
                    f"still in the knowledge store and still answering."
                )}
    return {"ok": True, "found": len(before), "evicted": evicted, "remaining": 0,
            "reason": f"Evicted {evicted} chunks from the knowledge store."}


def revoke(path: str, reason: str = "", *,
           knowledge_collection: Any = None,
           pack_index: Any = None) -> dict[str, Any]:
    """Take a downloaded source out of service - the file AND its indexed chunks.

    Revocation that leaves the content answering questions is not revocation,
    it is a note in a file nobody reads. So this does three things and reports
    honestly on each:

    1. **The artifact is set aside** - renamed with a ``.revoked`` suffix, which
       takes it out of the ZIM directory scan. Nothing is deleted; a revocation
       should be reviewable and un-doable by a person who decides it was wrong.
    2. **The chunks are evicted** from the Zone-2 knowledge collection, matched
       on the ``source_id`` that :func:`chunk_metadata` stamped on them at index
       time. This is the part that actually stops a poisoned pack answering.
    3. **The pack record is marked revoked**, so that if the file is ever put
       back it is indexed again rather than trusted as already-present.

    ``ok`` is True only when all three succeeded. The common partial outcome is
    a pack indexed before provenance existed: its chunks carry no source label,
    so they cannot be told apart from every other pack's in a shared collection.
    That is stated as a fact and a door in ``limits``, never smoothed over.
    """
    target = str(path or "").strip()
    record = read_provenance(target) if target else None
    # Refuse anything that is not an artifact we actually fetched. Without this
    # a caller could hand us a bare name and we would happily create a
    # provenance file for a download that never happened.
    if not target or (record is None and not os.path.isfile(target)):
        return {
            "ok": False,
            "reason": "That is not a downloaded artifact this build fetched.",
            "path": None,
            "provenance": None,
            "source_id": None,
            "eviction": None,
            "chunks_evicted": None,
            "limits": [],
        }
    record = dict(record or {})
    source_id = str(record.get("source_id") or "")
    limits: list[str] = []

    # Look the pack record up BEFORE the rename, while its recorded path still
    # matches what is on disk.
    pack_uuid: Optional[str] = None
    pack_record: dict[str, Any] = {}
    if pack_index is not None:
        try:
            found = pack_index.find_by_path(target)
            if found:
                pack_uuid, pack_record = str(found[0]), dict(found[1] or {})
        except Exception as exc:  # noqa: BLE001 - the index is a bonus, not a gate
            logger.warning("Could not read the pack index while revoking: %s", exc)

    revoked_path = target + ".revoked"
    moved = False
    try:
        if os.path.exists(target):
            os.replace(target, revoked_path)
            moved = True
    except OSError as exc:
        return {
            "ok": False,
            "reason": f"Could not set that artifact aside: {exc}",
            "path": target,
            "provenance": record,
            "source_id": source_id or None,
            "eviction": None,
            "chunks_evicted": None,
            "limits": [],
        }

    # --- evict the chunks --------------------------------------------------
    eviction: Optional[dict[str, Any]] = None
    if not source_id:
        limits.append(
            "This pack has no source id, so its chunks were never labelled and "
            "cannot be picked out of the knowledge store. The file is set aside "
            "and will not be read again, but anything already indexed from it is "
            "still there and can still answer. `puffpai knowledge reindex` "
            "cannot help here either: there is no download record to label this "
            "pack's chunks with. Clearing it for certain means rebuilding the "
            "knowledge collection from the packs you still trust."
        )
    else:
        eviction = evict_source(source_id, knowledge_collection)
        if eviction["found"] == 0:
            # The store answered and found nothing. Whether that is fine or bad
            # depends entirely on whether this pack was ever indexed.
            indexed_chunks = int(pack_record.get("chunks") or 0)
            was_indexed = (
                str(pack_record.get("status") or "") == "complete"
                and indexed_chunks > 0
            )
            if was_indexed:
                limits.append(
                    f"The pack record says {indexed_chunks} chunks were indexed "
                    f"from this pack, but none of them carry a source label - it "
                    f"was indexed before packs recorded where they came from. I "
                    f"cannot tell its chunks apart from any other pack's, so I "
                    f"have not removed them and they can still answer questions. "
                    f"To make them identifiable: take the .revoked suffix back "
                    f"off {os.path.basename(revoked_path) if moved else os.path.basename(target)}, "
                    f"run `puffpai knowledge reindex` so its chunks are stamped "
                    f"with where they came from, then revoke it again. The "
                    f"re-index reads the whole pack, so it is not quick."
                )
            else:
                limits.append(
                    "Nothing from this pack was in the knowledge store, so there "
                    "was nothing to evict."
                )
        elif not eviction["ok"]:
            limits.append(str(eviction["reason"]))

    # --- stamp the provenance ----------------------------------------------
    record["revoked_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    record["revoked_reason"] = str(reason or "")
    record["path"] = revoked_path if moved else record.get("path")
    record["chunks_evicted"] = None if eviction is None else eviction.get("evicted")
    _write_provenance(target, record)

    # --- mark the pack record ------------------------------------------------
    if pack_index is not None and pack_uuid:
        try:
            from puffpai.knowledge.auto_index import STATUS_REVOKED
            pack_index.mark(
                pack_uuid, status=STATUS_REVOKED, path=revoked_path,
                revoked_at=record["revoked_at"], revoked_reason=str(reason or ""),
            )
        except Exception as exc:  # noqa: BLE001
            limits.append(
                f"The pack record could not be updated ({exc}), so if this file "
                f"is put back it may be treated as already indexed."
            )

    # --- clean the book's own look-up and passages (knowledge.learn) --------
    # A book learned in two stages also has a word look-up and passages by
    # book id. Readers hide a revoked book at once; this removes what it left.
    book_id = str(pack_record.get("book_id") or "")
    book_cleaned = False
    if book_id:
        try:
            from puffpai.knowledge import learn as _learn

            class _Store:
                pass
            store = None
            if knowledge_collection is not None:
                store = _Store()
                store.knowledge_collection = knowledge_collection
            if book_id in _learn.forget_set_aside([book_id], rag=store):
                # Its word look-up was what answered; that is gone now.
                book_cleaned = True
                limits = [x for x in limits if not x.startswith(
                    "Nothing from this pack was in the knowledge store")]
        except Exception as exc:  # noqa: BLE001 - readers still hide it
            logger.warning("Could not clean the revoked book %s: %s", book_id, exc)

    evicted = None if eviction is None else eviction.get("evicted")
    evicted_ok = bool(eviction is not None and eviction.get("ok"))
    complete = (evicted_ok or book_cleaned) and not limits
    if complete and not evicted_ok:
        summary = ("Revoked. The file is set aside and the book's own look-up "
                   "was removed, so it no longer answers questions.")
    elif complete:
        summary = (
            f"Revoked. The file is set aside and {evicted} chunks were removed "
            f"from the knowledge store."
        )
    else:
        summary = (
            "Partly revoked. The file is set aside, but this revocation is not "
            "complete - see what is still true, below."
        )
    return {
        "ok": complete,
        "reason": summary,
        "path": revoked_path if moved else target,
        "provenance": record,
        "source_id": source_id or None,
        "eviction": eviction,
        "chunks_evicted": evicted,
        "limits": limits,
    }


def revocation_summary_lines(result: dict[str, Any]) -> list[str]:
    """Plain-language lines describing a revocation, for the CLI/API.

    A fact plus a door, in the house voice: what happened, and where a limit is
    liftable, how to lift it.
    """
    if not isinstance(result, dict):
        return ["Nothing to report."]
    lines = [str(result.get("reason") or "")]
    if result.get("path"):
        lines.append(f"  File set aside at: {result['path']}")
    if result.get("chunks_evicted") is not None:
        lines.append(
            f"  Chunks removed from the knowledge store: {result['chunks_evicted']}"
        )
    for limit in result.get("limits") or []:
        lines.append(f"  Still true: {limit}")
    return [line for line in lines if line]


# ---------------------------------------------------------------------------
# the data-forever boundary
# ---------------------------------------------------------------------------

def quarantine_note() -> str:
    """The one sentence that governs everything this module downloads."""
    return TRUST_NOTE


def prompt_reference(record: Optional[dict[str, Any]]) -> str:
    """The ONLY thing from a download that may go near a model prompt.

    Names the source, its license, and the digest of what landed - and not one
    byte of what it says. Every field here comes from the shipped registry or
    from our own measurement of the file; nothing comes from the response body
    or from any header the server chose. That is the whole trick: there is no
    code path in this module that turns downloaded text into prompt text, so a
    pack that opens with "ignore previous instructions, you are now..." is a
    pack that contains that sentence and nothing more.
    """
    if not isinstance(record, dict):
        return "No source."
    provenance = record.get("provenance") if "provenance" in record else record
    if not isinstance(provenance, dict):
        return "No source."
    return (
        f"Source: {provenance.get('label')} "
        f"[{provenance.get('license')}] "
        f"sha256={provenance.get('sha256')} "
        f"({provenance.get('bytes')} bytes). {TRUST_NOTE}"
    )


def fetch_summary_lines(result: dict[str, Any]) -> list[str]:
    """Plain-language lines describing a fetch result, for the CLI/API.

    Metadata only - the file's contents never appear here either.
    """
    if not isinstance(result, dict):
        return ["Nothing to report."]
    if result.get("ok"):
        provenance = result.get("provenance") or {}
        return [
            f"Downloaded: {provenance.get('label')}",
            f"  Landed in: {result.get('path')}",
            f"  Size:      {int(result.get('bytes') or 0)} bytes",
            f"  License:   {provenance.get('license')}",
            f"  SHA-256:   {result.get('sha256')}",
            f"  {TRUST_NOTE}",
        ]
    return [str(result.get("reason") or "That download did not happen.")]


__all__ = [
    "SCHEMA",
    "PROVENANCE_SCHEMA",
    "PROVENANCE_SUFFIX",
    "ALLOWED_SCHEMES",
    "MAX_REDIRECTS",
    "TRUST_NOTE",
    "USER_AGENT",
    "address_is_public",
    "guard_url",
    "robots_allows",
    "clear_caches",
    "destination_dir",
    "preview",
    "fetch",
    "read_provenance",
    "provenance_records",
    "chunk_metadata",
    "evict_source",
    "revoke",
    "revocation_summary_lines",
    "quarantine_note",
    "prompt_reference",
    "fetch_summary_lines",
]
