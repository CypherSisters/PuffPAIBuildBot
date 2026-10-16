"""Live public data for any app, shown as a card, with no outside code.

An app manifest (``puffpai_app/v1``) may carry ONE ``feed`` block. It names one
outside service, one https address on that service, how often to look, which
fields of the JSON reply to show, and the credit and license lines. This module
reads that block and does everything else itself: it registers the service as
a named outside service that stays shut until the owner says yes, fetches on
the server (the browser never talks to an outside host), keeps what it got as
data in the owner's state folder, fetches and checks pictures, and hands the
dashboard, the chat fastpath and the build's truth source one answer each.

Nothing in a feed block is code. It is parsed as JSON, validated against the
shape below, and used as strings and small path lists. A field this module does
not know is refused, not ignored, so an author learns at install time that a
field did nothing.

The block
---------
::

    "feed": {
      "service": {"name": "NASA Image Library", "host": "images-api.nasa.gov",
                  "image_host": "images-assets.nasa.gov"},
      "url": "https://images-api.nasa.gov/search?q={topic}&media_type=image",
      "params": {"topic": {"label": "Subject", "choices": ["saturn", "mars"],
                           "default": "saturn"}},
      "refresh_minutes": 60,
      "max_age_minutes": 1440,
      "card": {"kind": "image", "title": "Space pictures",
               "image": "collection.items[0].links[0].href",
               "caption": "collection.items[0].data[0].title"},
      "credit": "Pictures: NASA Image and Video Library",
      "license": "NASA media guidelines (most items not copyrighted)"
    }

* ``url`` is GET only, https, on exactly ``service.host``. The only ``{...}``
  placeholders are ``{date}``, ``{lat_round}``, ``{lon_round}`` and names the
  app declares in ``params``, each with a fixed list of choices.
* ``{lat_round}``/``{lon_round}`` come from the town-level ``home`` block in
  config, rounded to one decimal place (about 11 km). An exact location is
  never used.
* ``card.kind`` is ``image`` (one picture and a caption), ``value`` (one number
  or word with a fixed label), ``list`` (up to 20 rows) or ``gallery`` (several
  pictures, each with a short caption or date).
* A gallery's picture is either a path to a picture address in each row
  (``item_image``) or ``item_image_template``: an https address on the feed's
  own hosts built from the row's own values, for services that send a picture
  NAME and a date instead of an address (NASA EPIC). A template placeholder is
  a row path, optionally ``:year``, ``:month`` or ``:day`` of a date value, and
  each filled value must be a plain name (letters, digits, ``_.~-``).
* Paths are a small safe subset: dotted keys and ``[n]`` indexes (``[-1]`` is
  the last one). Nothing else is evaluated.
* ``service.ua_header`` (optional) names one extra header, from the fixed list
  :data:`UA_HEADERS`, that a service documents for callers to name their app
  (the Art Institute of Chicago asks for ``AIC-User-Agent``). Its value is
  always this product's own User-Agent, never text from the manifest.
* ``service.json_label`` (optional) is the one odd label, from the fixed list
  :data:`JSON_SENT_AS`, that a service puts on its JSON reply (7Timer's
  astro.php says ``text/html``). The reply must still read as JSON.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote, urlsplit

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

FEED_KEY = "feed"
CACHE_SCHEMA = "puffpai_feed_cache/v1"
CARD_KINDS = ("image", "value", "list", "gallery")

#: Refresh floor. No feed is fetched more often than this, whatever it says.
MIN_REFRESH_MINUTES = 10
MAX_REFRESH_MINUTES = 7 * 24 * 60
MAX_AGE_CEILING_MINUTES = 30 * 24 * 60

#: A JSON reply bigger than this is refused, not truncated and parsed.
MAX_RESPONSE_BYTES = 512 * 1024
#: One picture. Bigger than this is refused.
MAX_IMAGE_BYTES = 3 * 1024 * 1024
#: Pictures fetched per refresh, across a whole list or gallery card. A picture
#: already stored from the same address is reused and not fetched again.
MAX_IMAGES_PER_REFRESH = 12
MAX_LIST_ITEMS = 20
FETCH_TIMEOUT = 10.0

#: Raster pictures only. SVG can carry script, so it is never accepted.
IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif",
               "image/webp": "webp"}
_IMAGE_NAME_RE = re.compile(r"^[0-9a-f]{64}\.(?:jpg|png|gif|webp)$")

BUILTIN_PARAMS = ("date", "lat_round", "lon_round")
MAX_PARAMS = 4
MAX_CHOICES = 24

_FEED_KEYS = frozenset({"service", "url", "params", "refresh_minutes",
                        "max_age_minutes", "card", "credit", "license",
                        "lookup_url"})
_SERVICE_KEYS = frozenset({"name", "host", "image_host", "ua_header",
                           "json_label"})

#: Odd labels a service may be declared to put on its JSON reply. The body is
#: still only ever parsed as JSON; anything that does not parse is refused.
JSON_SENT_AS = ("text/html",)

#: Extra headers a service may ask for, each carrying this product's own
#: User-Agent and nothing else. A fixed list: a manifest names one, it never
#: supplies a header value.
UA_HEADERS = ("AIC-User-Agent",)
_PARAM_KEYS = frozenset({"label", "choices", "default"})
_CARD_KEYS = {
    "image": frozenset({"kind", "title", "image", "caption", "headline",
                        "image_credit"}),
    "value": frozenset({"kind", "title", "value", "label", "unit", "caption"}),
    "list": frozenset({"kind", "title", "items", "item_title", "item_detail",
                       "item_image", "max_items", "item_link"}),
    "gallery": frozenset({"kind", "title", "items", "item_title", "item_detail",
                          "item_image", "item_image_template", "caption",
                          "max_items", "item_link"}),
}
_PATH_FIELDS = {
    "image": ("image", "caption", "headline", "image_credit"),
    "value": ("value", "caption"),
    "list": ("item_title", "item_detail", "item_image"),
    "gallery": ("item_title", "item_detail", "item_image", "caption"),
}

_PARAM_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
_CHOICE_RE = re.compile(r"^[A-Za-z0-9 ._-]{1,40}$")
_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")
_KEY_TOKEN = r"[A-Za-z_][A-Za-z0-9_-]{0,63}"
_INDEX_TOKEN = r"\[(?:-1|\d{1,3})\]"
_PATH_RE = re.compile(
    rf"^(?:{_KEY_TOKEN}|{_INDEX_TOKEN})(?:\.{_KEY_TOKEN}|{_INDEX_TOKEN})*$")
_TOKEN_RE = re.compile(rf"({_KEY_TOKEN})|\[(-1|\d{{1,3}})\]")
MAX_PATH_LEN = 200
MAX_PATH_TOKENS = 12

_MISSING = object()


class FeedError(Exception):
    """A feed could not be run. ``message`` is plain words for a person."""

    def __init__(self, message: str, status: str = "error"):
        super().__init__(message)
        self.message = message
        self.status = status


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def _plain(value: Any, limit: int) -> bool:
    """A printable ASCII string of at most *limit* characters."""
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        return False
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        return False
    return all(ch.isprintable() for ch in value)


def _forbidden_in(node: Any, depth: int = 0) -> Optional[str]:
    """The first code-shaped key anywhere inside *node*, or None."""
    from puffpai.app_registry import _FORBIDDEN_KEYS
    if depth > 6:
        return "(nested too deep)"
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _FORBIDDEN_KEYS:
                return str(key)
            found = _forbidden_in(value, depth + 1)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _forbidden_in(value, depth + 1)
            if found:
                return found
    return None


def valid_path(path: Any, *, allow_root: bool = False) -> bool:
    """True for a path in the safe subset: ``a.b[0].c``, ``[-1].Kp``."""
    if allow_root and path == "":
        return True
    if not isinstance(path, str) or not path or len(path) > MAX_PATH_LEN:
        return False
    if not _PATH_RE.match(path):
        return False
    return len(parse_path(path)) <= MAX_PATH_TOKENS


def parse_path(path: str) -> list[Any]:
    """``"a.b[0]"`` -> ``["a", "b", 0]``. Call :func:`valid_path` first."""
    out: list[Any] = []
    for key, index in _TOKEN_RE.findall(path or ""):
        out.append(key if key else int(index))
    return out


def resolve(document: Any, path: str) -> Any:
    """The value at *path* in *document*, or the module's missing marker.

    Only plain dict lookups and list indexes. No attribute access, no
    evaluation: a key is a dict key and nothing more.
    """
    node = document
    if path == "":
        return node
    for token in parse_path(path):
        if isinstance(token, int):
            if not isinstance(node, list) or not node:
                return _MISSING
            if token == -1:
                node = node[-1]
            elif token < len(node):
                node = node[token]
            else:
                return _MISSING
        else:
            if not isinstance(node, dict) or token not in node:
                return _MISSING
            node = node[token]
    return node


def _is_registrable(host: Any) -> bool:
    from puffpai.security import egress_doors
    return egress_doors.is_registrable_hostname(host)


def placeholders(url: str) -> list[str]:
    """The ``{name}`` placeholders in a URL template, in order."""
    return _PLACEHOLDER_RE.findall(url or "")


def validate_feed(app_id: Any, feed: Any) -> Optional[str]:
    """Structural check of one feed block. ``None`` means usable.

    Everything not described in this module's docstring is refused by name.
    """
    who = f"App '{app_id}'"
    if not isinstance(feed, dict):
        return f"{who} has a feed block that is not an object."
    bad = _forbidden_in(feed)
    if bad:
        return (f"{who} has a feed declaring '{bad}'. A feed is data, never "
                f"a thing to run.")
    unknown = sorted(set(feed) - _FEED_KEYS)
    if unknown:
        return (f"{who} has a feed with fields this build does not read: "
                f"{', '.join(unknown)}.")

    service = feed.get("service")
    if not isinstance(service, dict):
        return f"{who} has a feed that names no outside service."
    unknown = sorted(set(service) - _SERVICE_KEYS)
    if unknown:
        return f"{who} has a feed service with unknown fields: {', '.join(unknown)}."
    if not _plain(service.get("name"), 64):
        return f"{who} has a feed service with no usable name."
    host = service.get("host")
    if not _is_registrable(host):
        return (f"{who} names feed host {host!r}, which is not a public web "
                f"address name (no addresses, no local names, no ports).")
    image_host = service.get("image_host")
    if image_host is not None and not _is_registrable(image_host):
        return f"{who} names picture host {image_host!r}, which is not usable."
    json_label = service.get("json_label")
    if json_label is not None and json_label not in JSON_SENT_AS:
        return (f"{who} says its service labels data in a way this build does "
                f"not read: {str(json_label)[:40]!r}.")
    ua_header = service.get("ua_header")
    if ua_header is not None and ua_header not in UA_HEADERS:
        return (f"{who} asks for a request header this build does not send: "
                f"{str(ua_header)[:40]!r}.")

    url = feed.get("url")
    if not _plain(url, 400):
        return f"{who} has a feed with no usable address."
    prefix = f"https://{host}/"
    if not url.startswith(prefix):
        return (f"{who} has a feed address that does not start with "
                f"{prefix} - a feed reads from its one named service only.")
    params = feed.get("params")
    if params is not None:
        problem = _validate_params(who, params)
        if problem:
            return problem
    declared = set(params or {})
    for name in placeholders(url):
        if name not in BUILTIN_PARAMS and name not in declared:
            return (f"{who} has feed address placeholder {{{name}}}, which is "
                    f"neither {{date}}, {{lat_round}}, {{lon_round}} nor a "
                    f"declared choice.")
    stripped = _PLACEHOLDER_RE.sub("", url)
    if "{" in stripped or "}" in stripped:
        return f"{who} has a feed address with a stray brace."
    try:
        parts = urlsplit(fill_url(url, {n: "x" for n in placeholders(url)}))
        port = parts.port
    except ValueError:
        return f"{who} has a feed address that cannot be read."
    if (parts.scheme != "https" or (parts.hostname or "") != host
            or port not in (None, 443) or parts.username or parts.password
            or parts.fragment or "@" in parts.netloc):
        return f"{who} has a feed address that is not a plain https address on {host}."

    for field, low, high in (("refresh_minutes", MIN_REFRESH_MINUTES,
                              MAX_REFRESH_MINUTES),
                             ("max_age_minutes", MIN_REFRESH_MINUTES,
                              MAX_AGE_CEILING_MINUTES)):
        value = feed.get(field)
        if not isinstance(value, int) or isinstance(value, bool):
            return f"{who} feed '{field}' must be a whole number of minutes."
        if not low <= value <= high:
            return f"{who} feed '{field}' must be between {low} and {high}."
    if feed["max_age_minutes"] < feed["refresh_minutes"]:
        return f"{who} feed 'max_age_minutes' is shorter than how often it refreshes."

    if not _plain(feed.get("credit"), 200):
        return f"{who} has a feed with no credit line. Every feed says where it came from."
    if not _plain(feed.get("license"), 120):
        return f"{who} has a feed with no license line."

    problem = _validate_card(who, feed.get("card"))
    if problem:
        return problem
    template = feed["card"].get("item_image_template")
    if template is not None and image_template_problem(template, hosts_of(feed)):
        return (f"{who} gallery picture address is not a plain https address "
                f"on its own service built from simple row paths.")
    link = feed["card"].get("item_link")
    if link is not None and link_problem(link, hosts_of(feed)):
        return (f"{who} card link is neither a simple path nor a plain https "
                f"address on its own service built from simple row paths.")
    lookup = feed.get("lookup_url")
    if lookup is not None and lookup_url_problem(lookup, host):
        return (f"{who} lookup address is not a plain https address on {host} "
                f"with one {{topic}} in it.")
    return None


def link_problem(link: Any, hosts: list[str]) -> Optional[str]:
    """Why a card's ``item_link`` cannot be used, or None. Either a path to an
    address in each row, or a web page address on the feed's own service
    (its hosts or its site, ``www.example.org`` for ``api.example.org``)
    built from the row's values (like a picture template)."""
    if isinstance(link, str) and "{" not in link:
        return None if valid_path(link) else "not a simple path"
    problem = image_template_problem(link, page_hosts(hosts))
    if problem is None and api_address(_PLACEHOLDER_RE.sub("x", link)):
        return "is an address for programs, not a web page"
    return problem


def page_hosts(hosts: list[str]) -> list[str]:
    """Where a feed's web pages may be: its own hosts and its service's site."""
    out: list[str] = []
    for host in hosts:
        if not host:
            continue
        site = _site_of(str(host).lower())
        for h in (str(host).lower(), site, f"www.{site}"):
            if h not in out:
                out.append(h)
    return out


#: Path parts that mark an address for programs (an API), not a web page.
_API_PARTS = frozenset({"api", "apis", "rest", "graphql", "ws", "json", "xml", "rpc"})
_API_VERSION_RE = re.compile(r"^v\d+(?:\.\d+)*$|^\d+(?:\.\d+)+$")
_API_HOST_RE = re.compile(r"(?:^|[.-])api(?:[.-]|$)")
_API_QUERY_RE = re.compile(r"(?i)(?:^|&)(?:format|fmt|output|f)=(?:json|xml)(?:&|$)")


def api_address(url: Any) -> bool:
    """Does *url* look like an address for programs (an API record) rather
    than a page a person reads? An ``api`` host, a version or ``api`` part in
    the path (``/2.3.0/launches/<id>/``), a data file, or a format=json
    query. A feed's links are never these: opening one shows raw data."""
    if not isinstance(url, str):
        return True
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return True
    host = (parts.hostname or "").lower()
    if _API_HOST_RE.search(host.split(".", 1)[0]) or host.startswith("api."):
        return True
    bits = [b.lower() for b in parts.path.split("/") if b]
    if any(b in _API_PARTS or _API_VERSION_RE.match(b) for b in bits):
        return True
    if bits and bits[-1].endswith((".json", ".xml", ".rss", ".atom", ".csv")):
        return True
    return bool(_API_QUERY_RE.search(parts.query))


def lookup_url_problem(url: Any, host: str) -> Optional[str]:
    """Why a lookup address cannot be used, or None: https on the service's own
    host, and its one placeholder is ``{topic}``."""
    if not _plain(url, 400) or not url.startswith(f"https://{host}/"):
        return "not on its own service"
    if placeholders(url) != ["topic"]:
        return "needs exactly one {topic}"
    try:
        parts = urlsplit(fill_url(url, {"topic": "x"}))
        port = parts.port
    except ValueError:
        return "cannot be read"
    if (parts.scheme != "https" or (parts.hostname or "") != host
            or port not in (None, 443) or parts.username or parts.password
            or parts.fragment or "@" in parts.netloc):
        return "not a plain https address"
    return None


#: ``{path}`` or ``{path:year}`` / ``:month`` / ``:day`` in a picture template.
_TEMPLATE_PART_RE = re.compile(r"^(?P<path>[^:{}]+)(?::(?P<part>year|month|day))?$")
#: A filled template value: a plain name, never a slash, query or dot-only.
_TEMPLATE_VALUE_RE = re.compile(r"^[A-Za-z0-9_.~-]{1,120}$")
_DATE_PARTS_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def image_template_problem(template: Any, hosts: list[str]) -> Optional[str]:
    """Why a picture address template cannot be used, or None.

    It must be https on one of *hosts*, with every placeholder after the host
    (so a row value can never pick the website) and each one a simple path.
    """
    if not _plain(template, 400):
        return "not plain text"
    head = template.split("{", 1)[0]
    if not any(head.startswith(f"https://{h}/") for h in hosts if h):
        return "does not start with https:// and one of its own hosts"
    names = placeholders(template)
    if not names or len(names) > 6:
        return "needs one to six placeholders"
    for name in names:
        match = _TEMPLATE_PART_RE.match(name)
        if not match or not valid_path(match.group("path")):
            return f"placeholder {{{name}}} is not a simple path"
    stripped = _PLACEHOLDER_RE.sub("", template)
    if "{" in stripped or "}" in stripped:
        return "has a stray brace"
    try:
        parts = urlsplit(_PLACEHOLDER_RE.sub("x", template))
        port = parts.port
    except ValueError:
        return "cannot be read"
    if (parts.scheme != "https" or (parts.hostname or "") not in hosts
            or port not in (None, 443) or parts.username or parts.password
            or parts.fragment or "@" in parts.netloc):
        return "is not a plain https address on its own host"
    return None


def fill_image_template(template: str, row: Any) -> Optional[str]:
    """The picture address for one row, or None when a value is missing or
    not a plain name. Values are URL-quoted; nothing else is substituted."""
    values: dict[str, str] = {}
    for name in placeholders(template):
        match = _TEMPLATE_PART_RE.match(name)
        if not match or not valid_path(match.group("path")):
            return None
        value = resolve(row, match.group("path"))
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            return None
        text = str(value).strip()
        part = match.group("part")
        if part:
            date = _DATE_PARTS_RE.match(text)
            if not date:
                return None
            text = date.group({"year": 1, "month": 2, "day": 3}[part])
        if not _TEMPLATE_VALUE_RE.match(text) or not text.strip("."):
            return None
        values[name] = text
    return fill_url(template, values)


def _validate_params(who: str, params: Any) -> Optional[str]:
    if not isinstance(params, dict) or len(params) > MAX_PARAMS:
        return f"{who} has feed choices that are not a small object."
    for name, spec in params.items():
        if not isinstance(name, str) or not _PARAM_NAME_RE.match(name) \
                or name in BUILTIN_PARAMS:
            return f"{who} has an unusable feed choice name {name!r}."
        if not isinstance(spec, dict) or set(spec) - _PARAM_KEYS:
            return f"{who} feed choice '{name}' has fields this build does not read."
        if not _plain(spec.get("label"), 60):
            return f"{who} feed choice '{name}' has no plain label."
        choices = spec.get("choices")
        if not isinstance(choices, list) or not 1 <= len(choices) <= MAX_CHOICES:
            return f"{who} feed choice '{name}' needs 1 to {MAX_CHOICES} options."
        for choice in choices:
            if not isinstance(choice, str) or not _CHOICE_RE.match(choice):
                return (f"{who} feed choice '{name}' has option {choice!r}; "
                        f"options are letters, digits, spaces, dots, dashes.")
        if spec.get("default") not in choices:
            return f"{who} feed choice '{name}' has a default that is not one of its options."
    return None


def _validate_card(who: str, card: Any) -> Optional[str]:
    if not isinstance(card, dict):
        return f"{who} has a feed with no card."
    kind = card.get("kind")
    if kind not in CARD_KINDS:
        return f"{who} feed card kind must be one of {', '.join(CARD_KINDS)}."
    unknown = sorted(set(card) - _CARD_KEYS[kind])
    if unknown:
        return (f"{who} feed card has fields a {kind} card does not read: "
                f"{', '.join(unknown)}.")
    if not _plain(card.get("title"), 80):
        return f"{who} feed card has no plain title."
    required = {"image": "image", "value": "value", "list": "item_title",
                "gallery": "items"}[kind]
    if card.get(required) is None:
        return f"{who} {kind} card must say where its {required.replace('_', ' ')} is."
    if kind == "gallery" and card.get("item_image") is None \
            and card.get("item_image_template") is None:
        return f"{who} gallery card must say where its pictures are."
    for field in _PATH_FIELDS[kind]:
        if field in card and not valid_path(card[field]):
            return (f"{who} feed card '{field}' is not a simple path like "
                    f"items[0].title.")
    if kind in ("list", "gallery"):
        if not valid_path(card.get("items"), allow_root=True):
            return f"{who} list card 'items' is not a simple path."
        max_items = card.get("max_items", 10)
        if not isinstance(max_items, int) or isinstance(max_items, bool) \
                or not 1 <= max_items <= MAX_LIST_ITEMS:
            return f"{who} list card shows 1 to {MAX_LIST_ITEMS} rows."
    if kind == "value":
        if not _plain(card.get("label"), 60):
            return f"{who} value card needs a plain label for its number."
        if "unit" in card and not _plain(card.get("unit"), 16):
            return f"{who} value card has an unusable unit."
    return None


def feed_of(manifest: Any) -> Optional[dict[str, Any]]:
    """The manifest's feed block if it is present and valid, else None."""
    if not isinstance(manifest, dict):
        return None
    block = manifest.get(FEED_KEY)
    if block is None or validate_feed(manifest.get("id"), block) is not None:
        return None
    return block


# ---------------------------------------------------------------------------
# what leaves the computer, in plain words (written by code, not the author)
# ---------------------------------------------------------------------------

def hosts_of(feed: dict[str, Any]) -> list[str]:
    service = feed.get("service") or {}
    hosts = [str(service.get("host"))]
    image_host = service.get("image_host")
    if isinstance(image_host, str) and image_host not in hosts:
        hosts.append(image_host)
    return hosts


def what_leaves(feed: dict[str, Any]) -> str:
    """One plain line saying what leaves this computer when the feed runs.

    Generated from the address itself, so it cannot be more reassuring than
    the address is. An author's own description is never used here.
    """
    service = feed.get("service") or {}
    names = placeholders(str(feed.get("url") or ""))
    extra: list[str] = []
    if "date" in names:
        extra.append("today's date")
    if "lat_round" in names or "lon_round" in names:
        extra.append("your town's rough location (to about 11 km, never your "
                     "exact address)")
    for name, spec in (feed.get("params") or {}).items():
        if name in names:
            extra.append(f"the {str(spec.get('label') or name).lower()} you picked")
    try:
        looks_up = bool(feed.get("service")) and bool(lookup_template(feed))
    except Exception:  # noqa: BLE001
        looks_up = False
    if looks_up:
        extra.append("a subject you ask it to look up in chat, once the privacy "
                     "screen lets it out")
    line = (f"A request to {service.get('host')} for this data. That site sees "
            f"your internet address")
    if extra:
        line += " and " + ", ".join(extra)
    line += ". That is all this card sends about you."
    if service.get("image_host") and service.get("image_host") != service.get("host"):
        line += f" Pictures are fetched from {service.get('image_host')}."
    return line


# ---------------------------------------------------------------------------
# paths on disk
# ---------------------------------------------------------------------------

def feeds_root() -> str:
    """``<state>/app_data/feeds`` - beside the app stores, outside the checkout."""
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), "app_data", "feeds")


def _app_root(app_id: str) -> str:
    from puffpai import app_registry
    if not app_registry.valid_id(app_id):
        raise FeedError("That is not a usable app name.", "unknown_app")
    return app_registry.resolve_within(feeds_root(), app_id)


def cache_path(app_id: str) -> str:
    return os.path.join(_app_root(app_id), "latest.json")


def images_dir(app_id: str) -> str:
    return os.path.join(_app_root(app_id), "images")


def image_file(app_id: str, name: str) -> Optional[str]:
    """The cached picture *name* for *app_id*, or None. Name is checked strictly."""
    if not isinstance(name, str) or not _IMAGE_NAME_RE.match(name):
        return None
    try:
        folder = images_dir(app_id)
    except FeedError:
        return None
    path = os.path.join(folder, name)
    return path if os.path.isfile(path) else None


def _write_json(path: str, payload: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def read_cache(app_id: str) -> Optional[dict[str, Any]]:
    """The last stored result for *app_id*, or None. Never raises."""
    try:
        path = cache_path(app_id)
        if os.path.getsize(path) > 2 * 1024 * 1024:
            return None
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, FeedError):
        return None
    if not isinstance(data, dict) or data.get("schema") != CACHE_SCHEMA:
        return None
    return data


def _log(app_id: str, host: str, path: str, ok: bool, detail: str) -> None:
    """One line per fetch in ``feeds/fetch_log.jsonl``: host and path, no query."""
    try:
        os.makedirs(feeds_root(), exist_ok=True)
        with open(os.path.join(feeds_root(), "fetch_log.jsonl"), "a",
                  encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": _iso(time.time()), "app": app_id,
                                 "host": host, "path": path[:200], "ok": ok,
                                 "detail": detail[:200]}) + "\n")
    except OSError:
        pass


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _parse_iso(text: Any) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(text)).timestamp()
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# installed feeds and their outside services
# ---------------------------------------------------------------------------

def _manifests() -> list[dict[str, Any]]:
    from puffpai import app_registry
    return app_registry.current_manifests()


def feed_apps(manifests: Optional[list[dict[str, Any]]] = None
              ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """``[(manifest, feed)]`` for every installed app with a usable feed."""
    out = []
    for manifest in (manifests if manifests is not None else _manifests()):
        feed = feed_of(manifest)
        if feed is not None:
            out.append((manifest, feed))
    return out


def sync_doors(manifests: Optional[list[dict[str, Any]]] = None) -> tuple[str, ...]:
    """Register one outside service per installed feed app. All start shut.

    A wholesale replace: an app that is gone loses its service in the same
    call. Consent is bound to the host set, so a repointed feed needs a new yes.
    """
    from puffpai.security import egress_doors
    entries = []
    for manifest, feed in feed_apps(manifests):
        service = feed["service"]
        # Its data host is a full host; its picture host is reached for
        # PICTURES ONLY (egress_doors: a plain GET answered by a capped
        # image), never as a full host.
        data_host = str(service.get("host"))
        pics = [h for h in hosts_of(feed) if h != data_host]
        entries.append((str(manifest["id"]), {
            "hosts": [data_host],
            "picture_hosts": pics,
            "name": str(service["name"]),
            "purpose": f"Live data for {manifest.get('name') or manifest['id']}.",
            "data_sent": what_leaves(feed),
        }))
    # The build's other data sources (puffpai.build.sources): one shut door
    # per host, beside the live cards' (this is a wholesale replace).
    try:
        from puffpai.build import sources
        taken = {e[0] for e in entries}
        entries += [e for e in sources.door_entries() if e[0] not in taken]
    except Exception as exc:  # noqa: BLE001 - no build record: live cards only
        logger.debug("feeds: build sources not added (%s)", type(exc).__name__)
    return egress_doors.register_app_doors(entries)


def _find(app_id: str, manifests: Optional[list[dict[str, Any]]] = None
          ) -> tuple[dict[str, Any], dict[str, Any]]:
    for manifest, feed in feed_apps(manifests):
        if manifest.get("id") == app_id:
            return manifest, feed
    raise FeedError("No installed app by that name shows live data.", "unknown_app")


def service_state(app_id: str) -> dict[str, Any]:
    """Whether this app may reach its service, and the plain sentence why.

    ``state`` is one of ``open``, ``not_asked``, ``said_no``, ``bot_offline``,
    ``switched_off``, ``unknown``. Only ``open`` fetches.
    """
    from puffpai.security import egress_doors
    manifest, feed = _find(app_id)
    sync_doors()
    name = str(manifest.get("name") or app_id)
    service = str(feed["service"]["name"])
    host = str(feed["service"]["host"])
    door_id = egress_doors.app_door_id(app_id)
    allowed, _reason = egress_doors.egress_allowed(door_id)
    consent = egress_doors.app_door_consent(app_id)
    kill = os.environ.get(egress_doors.APPS_KILL_ENV, "").strip().lower()
    posture = egress_doors.run_posture().get("run_egress")
    # "MusicBrainz may use MusicBrainz" reads as a mistake; say it once.
    for_app = "" if service.lower() == name.lower() else f" for {name}"
    uses = (f"{name} may fetch from {host}." if not for_app
            else f"{name} may use {service} ({host}).")
    if allowed:
        state, message, how = "open", uses, None
    elif kill in ("0", "false", "no", "off"):
        state = "switched_off"
        message = ("Outside services are switched off on this computer by "
                   "whoever runs it, so nothing is fetched.")
        how = None
    elif consent is True and posture != egress_doors.OPEN_POSTURE:
        state = "bot_offline"
        # One plain command changes exactly this: the bot may use the outside
        # services the owner said yes to, and this one is switched on.
        message = (f"You said yes to {service}, but this bot is set to stay off "
                   f"the internet, so {name} cannot fetch anything. The "
                   f"command below lets it use the outside services you said "
                   f"yes to, and nothing else.")
        how = f"puffpai services on {app_id}"
    elif consent is False:
        state, message = "said_no", (f"You said no to {service}{for_app}, so "
                                     f"nothing new is fetched.")
        how = f"puffpai apps allow {app_id}"
    elif consent is None:
        state = "not_asked"
        message = (f"{name} has not been allowed to use {service} ({host}) yet, "
                   f"so nothing has been fetched.")
        how = f"puffpai apps allow {app_id}"
    else:  # pragma: no cover - every branch above is exhaustive today
        state, message, how = "unknown", "This could not be checked.", None
    return {"state": state, "message": message, "how_to_fix": how,
            "service": service, "host": host, "hosts": hosts_of(feed),
            "what_leaves": what_leaves(feed)}


def services_on(app_id: str) -> dict[str, Any]:
    """The owner's plain command: let this live card use its outside service.

    Records the yes for this one service and, when the bot was set to stay off
    the internet, changes only that setting to "only the outside services you
    said yes to". Every other service stays as it was (shut unless the owner
    said yes to it). Returns :func:`service_state` plus ``changed_setting``.
    """
    from puffpai import build_manifest, build_posture
    from puffpai.security import egress_doors
    state = set_allowed(app_id, True)
    changed = False
    if state["state"] == "bot_offline":
        manifest = build_manifest.read_manifest() or {}
        posture = manifest.get("posture") if isinstance(manifest.get("posture"),
                                                         dict) else {}
        if not posture:
            raise FeedError("This bot has not been set up yet. Run puffpai "
                            "setup first.", "not_set_up")
        manifest["posture"] = build_posture.posture_manifest_block(
            build_posture.assess_posture(posture.get("build_network"),
                                         egress_doors.OPEN_POSTURE,
                                         posture.get("enabled_switches") or []))
        build_manifest.write_manifest(manifest)
        egress_doors.reset_cache()
        changed = True
        state = service_state(app_id)
        state["fetching"] = start_first_fetch(app_id, state=state)
    state["changed_setting"] = changed
    return state


def set_allowed(app_id: str, allowed: bool) -> dict[str, Any]:
    """Record the owner's yes or no for this app's service. Opens nothing else.

    A yes that leaves the service open starts the card's first fetch in the
    background (:func:`start_first_fetch`), so its tile has data in seconds.
    """
    from puffpai.security import egress_doors
    _find(app_id)
    sync_doors()
    try:
        egress_doors.set_app_door_consent(app_id, bool(allowed))
    except KeyError:
        raise FeedError("That app has no outside service to allow.", "unknown_app")
    state = service_state(app_id)
    state["recorded"] = "yes" if allowed else "no"
    if allowed and state["state"] == "open":
        state["fetching"] = start_first_fetch(app_id, state=state)
    return state


#: A first fetch the service turned away as "too many requests" (429) waits
#: this long and tries once more. The usual cause is BuildBot's own sample look
#: a moment earlier, at a service that allows one request every few seconds.
FIRST_FETCH_RETRY_SECONDS = 6.0


def _run_first_fetch(app_id: str, *, sleep=time.sleep) -> None:
    try:
        state = refresh(app_id, force=True)
        if state.get("card") is not None or not (read_cache(app_id) or {}).get("busy"):
            return
        sleep(FIRST_FETCH_RETRY_SECONDS)
        manifest, feed = _find(app_id)
        if service_state(app_id)["state"] != "open":
            return
        with _lock_for(app_id):
            cache = read_cache(app_id)
            if (cache or {}).get("status") == "ok" or not (cache or {}).get("busy"):
                return
            _fetch_into_cache(app_id, manifest, feed, cache, time.time())
    except Exception as exc:  # noqa: BLE001 - the scheduler tries again later
        logger.info("live card %s: first fetch did not run: %s", app_id,
                    type(exc).__name__)


def _start_thread(app_id: str) -> bool:
    threading.Thread(target=_run_first_fetch, args=(app_id,), daemon=True,
                     name=f"live-card-first-fetch-{app_id}"[:60]).start()
    return True


#: How a first fetch is started: a background thread in the running bot. The
#: test suite swaps it for a recorder, so no test fetches behind its back.
_first_fetch_starter = _start_thread


def start_first_fetch(app_id: str, *,
                      state: Optional[dict[str, Any]] = None) -> bool:
    """Fetch a card that was just switched on, now, in the background.

    Called wherever the owner switches a card on (the Add page, the "What
    leaves your computer" page, chat, ``puffpai services on``), and only when
    its service is open. It goes through :func:`refresh`, so the ten-minute
    floor and the one-fetch-at-a-time lock still hold: a card fetched a moment
    ago is not fetched again, and two switch-ons make one fetch. True when a
    fetch was started.
    """
    try:
        state = state if state is not None else service_state(app_id)
    except FeedError:
        return False
    if state.get("state") != "open":
        return False
    try:
        return bool(_first_fetch_starter(app_id))
    except Exception as exc:  # noqa: BLE001 - never block a switch-on
        logger.info("live card %s: first fetch not started: %s", app_id,
                    type(exc).__name__)
        return False


# ---------------------------------------------------------------------------
# building the address
# ---------------------------------------------------------------------------

def fill_url(template: str, values: dict[str, str]) -> str:
    """Substitute placeholders, every value URL-quoted (nothing unescaped)."""
    def repl(match: re.Match) -> str:
        return quote(str(values.get(match.group(1), "")), safe="")
    return _PLACEHOLDER_RE.sub(repl, template)


def home_block() -> dict[str, Any]:
    """The town-level home, or ``{}``: ``{"lat", "lon", "timezone"}``.

    One reader for the one ``home`` block (``label``, ``latitude``,
    ``longitude``, ``timezone``) that ``puffpai home set`` writes:
    :func:`puffpai.home_location.get_home`, which reads the config file itself
    (so an unattended feed runs while the vault is locked) and rounds the
    position to one decimal place whatever the file holds.
    """
    try:
        from puffpai import home_location
        home = home_location.get_home()
    except Exception:  # noqa: BLE001 - no home is "not set"
        return {}
    if not home:
        return {}
    out: dict[str, Any] = {"lat": round(float(home["latitude"]), 1),
                           "lon": round(float(home["longitude"]), 1)}
    if home.get("timezone"):
        out["timezone"] = str(home["timezone"])
    return out


def _choices_path(app_id: str) -> str:
    return os.path.join(_app_root(app_id), "choices.json")


def current_choices(app_id: str, feed: dict[str, Any]) -> dict[str, str]:
    """The owner's picked option per declared choice, else the default."""
    picked: dict[str, Any] = {}
    try:
        with open(_choices_path(app_id), "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            picked = loaded
    except (OSError, ValueError, FeedError):
        picked = {}
    out = {}
    for name, spec in (feed.get("params") or {}).items():
        value = picked.get(name)
        out[name] = value if value in spec["choices"] else spec["default"]
    return out


def set_choice(app_id: str, name: str, value: str) -> dict[str, str]:
    """Pick one of an app's declared options. Anything else is refused."""
    _manifest, feed = _find(app_id)
    spec = (feed.get("params") or {}).get(name)
    if not isinstance(spec, dict) or value not in spec["choices"]:
        raise FeedError("That is not one of the options this app offers.",
                        "bad_choice")
    picked = current_choices(app_id, feed)
    picked[name] = value
    _write_json(_choices_path(app_id), picked)
    return picked


def build_url(app_id: str, feed: dict[str, Any], *, now: Optional[float] = None
              ) -> str:
    """The address to fetch, or :class:`FeedError` when a value is missing."""
    names = placeholders(feed["url"])
    values: dict[str, str] = dict(current_choices(app_id, feed))
    home = home_block() if ({"lat_round", "lon_round", "date"} & set(names)) else {}
    if "lat_round" in names or "lon_round" in names:
        if "lat" not in home or "lon" not in home:
            raise FeedError("This needs your town, and none is set yet. Set "
                            "your home town in the bot's settings first.",
                            "needs_home")
        values["lat_round"] = f"{home['lat']:.1f}"
        values["lon_round"] = f"{home['lon']:.1f}"
    if "date" in names:
        stamp = datetime.fromtimestamp(now if now is not None else time.time(),
                                       timezone.utc)
        tz = home.get("timezone")
        if tz:
            try:
                from zoneinfo import ZoneInfo
                stamp = stamp.astimezone(ZoneInfo(tz))
            except Exception:
                pass
        values["date"] = stamp.strftime("%Y-%m-%d")
    url = fill_url(feed["url"], values)
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != feed["service"]["host"]:
        raise FeedError("The address this app would fetch is not on its "
                        "service, so nothing was fetched.", "bad_address")
    return url


# ---------------------------------------------------------------------------
# reading the reply
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]{0,200}>")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def as_text(value: Any, limit: int = 200) -> str:
    """A reply value as plain display text. Data, never markup or instructions."""
    if value is _MISSING or value is None:
        return ""
    if isinstance(value, bool):
        text = "yes" if value else "no"
    elif isinstance(value, int):
        text = str(value)
    elif isinstance(value, float):
        text = f"{value:.2f}".rstrip("0").rstrip(".")
    elif isinstance(value, str):
        text = value
    elif isinstance(value, list):
        text = ", ".join(as_text(v, 60) for v in value[:3]
                         if not isinstance(v, (dict, list)))
    else:
        return ""
    text = _TAG_RE.sub(" ", text)
    text = _CONTROL_RE.sub(" ", text)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit - 1].rstrip() + "…"
    return text


def image_url_ok(url: Any, feed: dict[str, Any]) -> Optional[str]:
    """A picture address the backend may fetch, as https, or None.

    Only on the feed's own service or its declared picture host. A plain http
    address on one of those hosts is upgraded to https; nothing is ever fetched
    over plain http.
    """
    if not isinstance(url, str) or len(url) > 1000:
        return None
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("https", "http") or parts.username or parts.password:
        return None
    if port not in (None, 443) or "@" in parts.netloc:
        return None
    host = (parts.hostname or "").lower()
    if host not in hosts_of(feed):
        return None
    return parts._replace(scheme="https", netloc=host).geturl()


def extract(document: Any, feed: dict[str, Any]) -> dict[str, Any]:
    """The card's values from one parsed reply. Plain data out, nothing else."""
    card = feed["card"]
    kind = card["kind"]
    out: dict[str, Any] = {"kind": kind, "title": card["title"]}
    if kind == "image":
        out["image_url"] = image_url_ok(resolve(document, card["image"]), feed)
        for field in ("caption", "headline", "image_credit"):
            if field in card:
                out[field] = as_text(resolve(document, card[field]), 300)
    elif kind == "gallery":
        out.update(_extract_gallery(document, feed))
    elif kind == "value":
        out["label"] = card["label"]
        out["unit"] = card.get("unit", "")
        out["value"] = as_text(resolve(document, card["value"]), 80)
        if "caption" in card:
            out["caption"] = as_text(resolve(document, card["caption"]), 200)
    else:
        rows = resolve(document, card["items"])
        items: list[dict[str, Any]] = []
        if isinstance(rows, list):
            for row in rows[:int(card.get("max_items", 10))]:
                item = {"title": as_text(resolve(row, card["item_title"]), 160)}
                if "item_detail" in card:
                    item["detail"] = as_text(resolve(row, card["item_detail"]), 300)
                if "item_image" in card:
                    item["image_url"] = image_url_ok(resolve(row, card["item_image"]), feed)
                link = item_link(row, feed)
                if link:
                    item["link"] = link
                if item["title"]:
                    items.append(item)
        out["items"] = items
    return out


#: Where a row usually keeps the address of its own page, read when a card
#: names no ``item_link`` (so an older card's items still open their page).
ROW_LINK_FIELDS = ("link", "url", "permalink", "html_url", "web_url")


def _site_of(host: str) -> str:
    """The service's own site: ``api.example.org`` -> ``example.org``. A short
    second part (``x.co.uk``) keeps the whole host."""
    labels = host.split(".")
    if len(labels) >= 3 and len(labels[-2]) >= 4:
        return ".".join(labels[1:])
    return host


def link_ok(url: Any, feed: dict[str, Any]) -> Optional[str]:
    """A row's web page address as https, or None: on the feed's own hosts or
    its service's own site (``www.example.org`` for ``api.example.org``), and
    never an address for programs (:func:`api_address`). Only shown as a
    link; never fetched."""
    if not isinstance(url, str) or len(url) > 1000 or api_address(url):
        return None
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    if (parts.scheme not in ("https", "http") or parts.username or parts.password
            or port not in (None, 443) or "@" in parts.netloc):
        return None
    host = (parts.hostname or "").lower()
    for own in hosts_of(feed):
        site = _site_of(str(own).lower())
        if host == site or host.endswith("." + site):
            return parts._replace(scheme="https", netloc=host).geturl()
    return None


def item_link(row: Any, feed: dict[str, Any]) -> Optional[str]:
    """The web page one row links to, or None. Shown to the owner as a link;
    this bot never fetches it. The card's own ``item_link`` (a page template
    the seed declares, or a path to the row's page address) first; a card
    that names none uses the row's own page address when it has one on the
    service's site. An address for programs (the API record the row came
    from) is never a link (otherwise "Read" on a launch shows raw data
    from ll.thespacedevs.com); then the row has no link."""
    link = feed["card"].get("item_link")
    if not isinstance(link, str):
        if not isinstance(row, dict):
            return None
        for field in ROW_LINK_FIELDS:
            got = link_ok(row.get(field), feed) if isinstance(row.get(field), str) else None
            if got:
                return got
        return None
    url = fill_image_template(link, row) if "{" in link else resolve(row, link)
    return link_ok(url, feed)


def _extract_gallery(document: Any, feed: dict[str, Any]) -> dict[str, Any]:
    """A gallery's rows: only rows with a usable picture address are kept.

    Each row's ``title`` is always set (its title, else its detail, else a
    fixed "Picture n"), so everything that reads a list reads a gallery too.
    """
    card = feed["card"]
    out: dict[str, Any] = {}
    if "caption" in card:
        out["caption"] = as_text(resolve(document, card["caption"]), 300)
    rows = resolve(document, card["items"])
    items: list[dict[str, Any]] = []
    if isinstance(rows, list):
        for row in rows[:int(card.get("max_items", 10))]:
            if "item_image_template" in card:
                url = fill_image_template(card["item_image_template"], row)
            else:
                url = resolve(row, card["item_image"])
            url = image_url_ok(url, feed)
            if not url:
                continue
            title = as_text(resolve(row, card["item_title"]), 160) \
                if "item_title" in card else ""
            detail = as_text(resolve(row, card["item_detail"]), 300) \
                if "item_detail" in card else ""
            if not title:
                title, detail = detail, ""
            item = {"title": title or f"Picture {len(items) + 1}",
                    "image_url": url}
            if detail:
                item["detail"] = detail
            link = item_link(row, feed)
            if link:
                item["link"] = link
            items.append(item)
    out["items"] = items
    return out


def varies(card: dict[str, Any]) -> bool:
    """False for a list or gallery whose rows all say the same thing.

    Ten rows of one sentence is worth nothing (one caption ten times and no
    pictures). A gallery
    varies when its pictures do.
    """
    items = card.get("items") if card.get("kind") in ("list", "gallery") else None
    if not items or len(items) < 2:
        return True
    if card.get("kind") == "gallery":
        pictures = {i.get("image_url") or i.get("image") for i in items}
        if len(pictures - {None}) > 1:
            return True
    return len({(i.get("title"), i.get("detail")) for i in items}) > 1


def has_content(card: dict[str, Any]) -> bool:
    kind = card.get("kind")
    if kind == "image":
        return bool(card.get("image_url") or card.get("caption"))
    if kind == "value":
        return bool(card.get("value"))
    return bool(card.get("items"))


#: Labels some services put on a JSON reply (iTunes says text/javascript,
#: 7Timer text/plain). The body is only ever parsed as JSON, never run, and
#: anything that does not parse is refused like any other bad reply.
_JSON_LABELS = ("application/json", "text/json", "text/javascript",
                "application/javascript", "text/plain")


def _json_type(content_type: str, declared: Any = None) -> bool:
    """A JSON label, or the one odd label this service is declared to use
    (``declared``, only from :data:`JSON_SENT_AS`)."""
    base = (content_type or "").split(";", 1)[0].strip().lower()
    if declared in JSON_SENT_AS and base == declared:
        return True
    return base in _JSON_LABELS or (
        base.startswith("application/") and base.endswith("+json"))


def _read_capped(resp: Any, limit: int) -> Optional[bytes]:
    """Up to *limit* bytes of a streamed body, or None if it is bigger."""
    declared = resp.headers.get("Content-Length")
    try:
        if declared is not None and int(declared) > limit:
            return None
    except ValueError:
        pass
    chunks: list[bytes] = []
    total = 0
    for chunk in resp.iter_content(chunk_size=16384):
        if not chunk:
            continue
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _sniff_image(body: bytes) -> Optional[str]:
    """The extension the bytes really are, from their first bytes."""
    if body.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if body[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "webp"
    return None


def request_headers(feed: dict[str, Any], accept: str) -> dict[str, str]:
    """The fixed headers one feed request carries: this product's User-Agent,
    what it accepts, and the service's own app-naming header when its feed
    declares one from :data:`UA_HEADERS` (same User-Agent value)."""
    from puffpai.security import egress_doors
    headers = {"User-Agent": egress_doors.FEED_USER_AGENT, "Accept": accept}
    name = (feed.get("service") or {}).get("ua_header")
    if name in UA_HEADERS:
        headers[name] = egress_doors.FEED_USER_AGENT
    return headers


_IMAGE_ACCEPT = "image/jpeg, image/png, image/gif, image/webp"


def _fetch_image(app_id: str, url: str,
                 feed: Optional[dict[str, Any]] = None) -> Optional[str]:
    """Fetch and store one picture; return its local name, or None."""
    from puffpai.security import egress_doors
    try:
        resp = egress_doors.app_egress_get(
            app_id, url, timeout=FETCH_TIMEOUT, stream=True,
            headers=request_headers(feed or {}, _IMAGE_ACCEPT))
    except Exception as exc:
        _log(app_id, urlsplit(url).hostname or "", urlsplit(url).path, False,
             f"picture: {type(exc).__name__}")
        return None
    try:
        declared = (resp.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if resp.status_code != 200 or declared not in IMAGE_TYPES:
            _log(app_id, urlsplit(url).hostname or "", urlsplit(url).path, False,
                 f"picture refused: status {resp.status_code}, type {declared[:40]}")
            return None
        body = _read_capped(resp, MAX_IMAGE_BYTES)
        if body is None or not body:
            _log(app_id, urlsplit(url).hostname or "", urlsplit(url).path, False,
                 "picture refused: too big or empty")
            return None
        real = _sniff_image(body)
        if real is None or real != IMAGE_TYPES[declared]:
            _log(app_id, urlsplit(url).hostname or "", urlsplit(url).path, False,
                 "picture refused: bytes are not the type it claimed")
            return None
        name = f"{hashlib.sha256(body).hexdigest()}.{real}"
        folder = images_dir(app_id)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        if not os.path.exists(path):
            tmp = f"{path}.{os.getpid()}.tmp"
            with open(tmp, "wb") as fh:
                fh.write(body)
            os.replace(tmp, path)
        _log(app_id, urlsplit(url).hostname or "", urlsplit(url).path, True,
             f"picture {len(body)} bytes")
        return name
    finally:
        try:
            resp.close()
        except Exception:
            pass


def _prune_images(app_id: str, keep: set[str]) -> None:
    try:
        folder = images_dir(app_id)
        for name in os.listdir(folder):
            if _IMAGE_NAME_RE.match(name) and name not in keep:
                os.remove(os.path.join(folder, name))
    except (OSError, FeedError):
        pass


# ---------------------------------------------------------------------------
# the runner
# ---------------------------------------------------------------------------

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(app_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(app_id, threading.Lock())


def _is_due(cache: Optional[dict[str, Any]], feed: dict[str, Any], now: float,
            *, force: bool) -> bool:
    if not cache or cache.get("status") != "ok":
        # Never fetched, or the last try failed: a failed try still waits the
        # floor, so a dead service is not hammered by every page view.
        last = _parse_iso((cache or {}).get("tried_at"))
        return last is None or now - last >= MIN_REFRESH_MINUTES * 60
    fetched = _parse_iso(cache.get("fetched_at"))
    if fetched is None:
        return True
    tried = _parse_iso(cache.get("tried_at"))
    if cache.get("last_error") and tried is not None \
            and now - tried < MIN_REFRESH_MINUTES * 60:
        return False
    age = now - fetched
    if force:
        return age >= MIN_REFRESH_MINUTES * 60
    return age >= int(feed["refresh_minutes"]) * 60


def refresh(app_id: str, *, force: bool = False, now: Optional[float] = None
            ) -> dict[str, Any]:
    """Fetch this app's feed if it is due and allowed; return :func:`card_state`.

    Never raises for a service problem: the stored result says what went wrong
    in plain words and the last good values are kept (and aged honestly).
    """
    now = time.time() if now is None else now
    manifest, feed = _find(app_id)
    state = service_state(app_id)
    if state["state"] != "open":
        return card_state(app_id, now=now)
    with _lock_for(app_id):
        cache = read_cache(app_id)
        if not _is_due(cache, feed, now, force=force):
            return card_state(app_id, now=now)
        _fetch_into_cache(app_id, manifest, feed, cache, now)
    _log_refresh(app_id, feed)
    return card_state(app_id, now=now)


def _log_refresh(app_id: str, feed: dict[str, Any]) -> None:
    """The refresh on the team page, under Majordomo (the work she is not
    watching)."""
    from puffpai import agent_activity
    name = str(((feed or {}).get("service") or {}).get("name") or app_id)
    new = read_cache(app_id) or {}
    if new.get("last_error"):
        agent_activity.record("majordomo", f"Tried to refresh {name}: "
                              f"{new['last_error']}", status="error")
    else:
        agent_activity.record("majordomo", f"Refreshed {name}")


def _fetch_into_cache(app_id: str, manifest: dict[str, Any], feed: dict[str, Any],
                      cache: Optional[dict[str, Any]], now: float) -> None:
    from puffpai.security import egress_doors
    host = feed["service"]["host"]
    base = dict(cache or {}) if (cache or {}).get("status") == "ok" else {}

    def failed(message: str, detail: str, *, busy: bool = False) -> None:
        record = dict(base) if base else {"schema": CACHE_SCHEMA, "app_id": app_id}
        record.update({"schema": CACHE_SCHEMA, "app_id": app_id,
                       "tried_at": _iso(now), "last_error": message})
        if busy:
            # "Too many requests": the first fetch waits and tries once more.
            record["busy"] = True
        if not base:
            record["status"] = "error"
        _write_json(cache_path(app_id), record)
        _log(app_id, host, "", False, detail)

    try:
        url = build_url(app_id, feed, now=now)
    except FeedError as exc:
        failed(exc.message, exc.status)
        return
    path = urlsplit(url).path
    try:
        resp = egress_doors.app_egress_get(
            app_id, url, timeout=FETCH_TIMEOUT, stream=True,
            headers=request_headers(feed, "application/json"))
    except egress_doors.EgressBlocked as blocked:
        failed("The request was stopped before it left this computer.",
               blocked.reason)
        return
    except Exception as exc:
        failed(f"{feed['service']['name']} did not answer.", type(exc).__name__)
        return
    try:
        if resp.status_code != 200:
            failed(f"{feed['service']['name']} answered with an error "
                   f"({resp.status_code}).", f"status {resp.status_code}",
                   busy=resp.status_code == 429)
            return
        if not _json_type(resp.headers.get("Content-Type") or "",
                          feed["service"].get("json_label")):
            failed(f"{feed['service']['name']} did not send data in the "
                   f"expected form.", "content type not json")
            return
        body = _read_capped(resp, MAX_RESPONSE_BYTES)
    finally:
        try:
            resp.close()
        except Exception:
            pass
    if body is None:
        failed(f"{feed['service']['name']} sent more than this bot will read "
               f"(512 KB).", "reply too big")
        return
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        failed(f"{feed['service']['name']} sent data that could not be read.",
               "not json")
        return

    card = extract(document, feed)
    if not has_content(card):
        failed(f"{feed['service']['name']} answered, but the parts this card "
               f"shows were not in the reply.", "picks empty")
        return

    keep: set[str] = set()
    fetched = 0
    targets = []
    if card["kind"] == "image" and card.get("image_url"):
        targets.append(card)
    elif card["kind"] in ("list", "gallery"):
        targets.extend(i for i in card["items"] if i.get("image_url"))
    # A picture already stored from the same address is not fetched again.
    known = (cache or {}).get("pictures")
    known = known if isinstance(known, dict) else {}
    pictures: dict[str, str] = {}
    for target in targets:
        url = target.pop("image_url")
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
        name = known.get(key)
        if not (isinstance(name, str) and image_file(app_id, name)):
            name = None
            if fetched < MAX_IMAGES_PER_REFRESH:
                name = _fetch_image(app_id, url, feed)
                fetched += 1
        if name:
            target["image"] = name
            keep.add(name)
            pictures[key] = name
    _prune_images(app_id, keep)
    if card["kind"] == "gallery":
        with_picture = [i for i in card["items"] if i.get("image")]
        if with_picture:
            card["items"] = with_picture

    _write_json(cache_path(app_id), {
        "schema": CACHE_SCHEMA, "app_id": app_id, "status": "ok",
        "fetched_at": _iso(now), "tried_at": _iso(now), "last_error": "",
        "source_host": host, "bytes": len(body), "card": card,
        "pictures": pictures,
    })
    _log(app_id, host, path, True, f"{len(body)} bytes")


def card_state(app_id: str, *, now: Optional[float] = None) -> dict[str, Any]:
    """What the dashboard, the widget and the chat answer all read. No fetch.

    ``status`` is ``ok``, ``too_old``, ``never_fetched``, ``error`` or the
    service ``state`` when the owner has not allowed it. Card values older than
    ``max_age_minutes`` are withheld, not shown as current.
    """
    now = time.time() if now is None else now
    manifest, feed = _find(app_id)
    service = service_state(app_id)
    cache = read_cache(app_id) or {}
    card = cache.get("card") if cache.get("status") == "ok" else None
    fetched = _parse_iso(cache.get("fetched_at"))
    age_minutes = int((now - fetched) // 60) if fetched is not None else None
    status = "ok"
    message = ""
    if card is None:
        status = "error" if cache.get("last_error") else "never_fetched"
        message = cache.get("last_error") or "Nothing has been fetched yet."
    elif age_minutes is not None and age_minutes > int(feed["max_age_minutes"]):
        status, card = "too_old", None
        message = ("The last copy is too old to show as current. It will "
                   "update when the service answers again.")
    if service["state"] != "open" and not cache.get("card"):
        # Nothing was ever stored: the reason is the service, not the data.
        status, message = service["state"], service["message"]
    out = {
        "app_id": app_id,
        "name": str(manifest.get("name") or app_id),
        "status": status,
        "message": message,
        "card": card,
        "fetched_at": cache.get("fetched_at") if card is not None else None,
        "age_minutes": age_minutes if card is not None else None,
        "last_error": cache.get("last_error") or "",
        "refresh_minutes": int(feed["refresh_minutes"]),
        "credit": feed["credit"],
        "license": feed["license"],
        "service": {"name": feed["service"]["name"],
                    "host": feed["service"]["host"],
                    "image_host": feed["service"].get("image_host")},
        "allowed": service["state"] == "open",
        "service_state": service["state"],
        "service_message": service["message"],
        "how_to_fix": service["how_to_fix"],
        "what_leaves": service["what_leaves"],
        "choices": [
            {"name": name, "label": spec["label"], "options": spec["choices"],
             "picked": current_choices(app_id, feed).get(name)}
            for name, spec in (feed.get("params") or {}).items()
        ],
    }
    return out


def latest_readings(*, now: Optional[float] = None
                    ) -> Optional[list[dict[str, Any]]]:
    """Every installed live card's last stored values, for the routines.

    The shape :func:`puffpai.routines.set_feed_reader` reads. ``None`` when
    this build has no live card at all. Reads the stored copies only and
    never goes online; a card with nothing fresh carries a plain ``problem``.
    """
    apps = feed_apps()
    if not apps:
        return None
    out: list[dict[str, Any]] = []
    for manifest, _feed in apps:
        app_id = str(manifest.get("id") or "")
        try:
            state = card_state(app_id, now=now)
        except Exception:  # noqa: BLE001 - one broken card never hides the rest
            continue
        reading: dict[str, Any] = {"id": app_id, "name": state["name"],
                                   "credit": str(state.get("credit") or "")}
        card = state.get("card") or {}
        if card:
            kind = card.get("kind")
            if kind == "value":
                reading["value"] = card.get("value")
                reading["unit"] = card.get("unit") or ""
            elif kind == "image":
                words = card.get("headline") or card.get("caption")
                reading["items"] = [{"title": words}] if words else []
            else:
                reading["items"] = [{"title": i.get("title")}
                                    for i in card.get("items") or ()
                                    if isinstance(i, dict) and i.get("title")]
            reading["updated_at"] = state.get("fetched_at")
        else:
            reading["problem"] = str(state.get("message")
                                     or "There is nothing fresh to show yet.")
        out.append(reading)
    return out


#: Words that never tie a question to one card.
_CHAT_STOP = frozenset("""
what whats when where which who whom whose why how show tell give read find
about from with this that these those there their them they have does doing
your mine ours please today tonight right now latest newest current some any
more most just like want know card cards live data info information bot
""".split())

# ---------------------------------------------------------------------------
# looking a subject up (the chat's <app>_lookup tool)
# ---------------------------------------------------------------------------

#: Rows a lookup hands back.
LOOKUP_ROWS = 8


def _seed_lookup(host: str) -> Optional[str]:
    """A search-style address with one {topic} for this host from BuildBot's own
    hand-checked list, for a card made before the list carried one."""
    try:
        from puffpai.catalogs import api_seeds
        rows = api_seeds.load_rows()
    except Exception:  # noqa: BLE001 - no list, no lookup
        return None
    for row in rows:
        if str(row.get("host") or "").lower() != host:
            continue
        for key in ("lookup_url", "card_url"):
            url = row.get(key)
            if isinstance(url, str) and not lookup_url_problem(url, host):
                return url
    return None


def lookup_template(feed: dict[str, Any]) -> Optional[str]:
    """The address a lookup uses, with one ``{topic}``, or None when this feed
    cannot look a subject up. Its own lookup address first, then its card
    address when that takes a subject and nothing else, then the list's."""
    host = feed["service"]["host"]
    own = feed.get("lookup_url")
    if isinstance(own, str) and not lookup_url_problem(own, host):
        return own
    if not lookup_url_problem(feed.get("url"), host):
        return feed["url"]
    return _seed_lookup(host)


def lookup_apps(manifests: Optional[list[dict[str, Any]]] = None
                ) -> list[tuple[dict[str, Any], dict[str, Any], str]]:
    """``[(manifest, feed, template)]`` for every feed app that can look up."""
    out = []
    for manifest, feed in feed_apps(manifests):
        template = lookup_template(feed)
        if template:
            out.append((manifest, feed, template))
    return out


def lookup(app_id: str, topic: Any, *, said: str = "") -> dict[str, Any]:
    """Look *topic* up on this app's outside service. Read-only.

    The subject goes through the privacy screen first (``said`` is her own
    words, read there and never sent) and leaves only as a screened phrase; the
    request goes through the app's own outside service, so it runs only while
    she has it switched on and the Bridge lets it out. Returns ``{"ok",
    "service", "topic", "items": [{title, detail, link}], "credit"}`` or
    ``{"ok": False, "error"}`` in plain words.
    """
    from puffpai.security import egress_doors, outbound_query
    try:
        manifest, feed = _find(app_id)
    except FeedError as exc:
        return {"ok": False, "error": exc.message}
    name = str(feed["service"]["name"])
    template = lookup_template(feed)
    if not template:
        return {"ok": False, "error": f"{name} cannot look a subject up."}
    state = service_state(app_id)
    if state["state"] != "open":
        return {"ok": False, "error": state["message"],
                "how_to_fix": state.get("how_to_fix")}
    words = " ".join(str(topic or "").split())
    screened = outbound_query.screen([words], goal=str(said or words))
    if not screened.sendable:
        return {"ok": False, "error": (
            f"That subject stays on this computer (it is private or not a plain "
            f"search), so nothing was sent to {name}. Try a plainer subject.")}
    phrase = screened.sendable[0]
    url = template.replace("{topic}", outbound_query.url_quote(phrase))
    host = feed["service"]["host"]
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != host:
        return {"ok": False, "error": f"The address for {name} is not on its own "
                                      f"service, so nothing was sent."}
    try:
        resp = egress_doors.app_egress_get(
            app_id, url, timeout=FETCH_TIMEOUT, stream=True,
            headers=request_headers(feed, "application/json"))
    except egress_doors.EgressBlocked as blocked:
        _log(app_id, host, parts.path, False, blocked.reason)
        return {"ok": False, "error": "The request was stopped before it left "
                                      "this computer."}
    except Exception as exc:  # noqa: BLE001
        _log(app_id, host, parts.path, False, type(exc).__name__)
        return {"ok": False, "error": f"{name} did not answer."}
    try:
        if resp.status_code != 200:
            _log(app_id, host, parts.path, False, f"status {resp.status_code}")
            return {"ok": False, "error": f"{name} answered with an error "
                                          f"({resp.status_code})."}
        if not _json_type(resp.headers.get("Content-Type") or "",
                          feed["service"].get("json_label")):
            return {"ok": False, "error": f"{name} did not send data in the "
                                          f"expected form."}
        body = _read_capped(resp, MAX_RESPONSE_BYTES)
    finally:
        try:
            resp.close()
        except Exception:
            pass
    if body is None:
        return {"ok": False, "error": f"{name} sent more than this bot will read."}
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {"ok": False, "error": f"{name} sent data that could not be read."}
    _log(app_id, host, parts.path, True, f"lookup, {len(body)} bytes")
    card = extract(document, feed)
    items = []
    if card.get("kind") in ("list", "gallery"):
        rows = card.get("items") or []
    else:
        rows = [{"title": card.get("headline") or card.get("label") or "",
                 "detail": card.get("caption") or card.get("value") or ""}]
    for row in rows[:LOOKUP_ROWS]:
        item = {k: row[k] for k in ("title", "detail", "link") if row.get(k)}
        if item.get("title") or item.get("detail"):
            items.append(item)
    return {"ok": True, "service": name, "topic": phrase.phrase, "items": items,
            "credit": feed["credit"],
            **({} if items else {"note": f"{name} found nothing for that."})}


LOOKUP_SCHEMA = {"type": "object", "properties": {
    "topic": {"type": "string",
              "description": "The subject to look up, in one to four plain words."}},
    "required": ["topic"]}


def lookup_tool_name(app_id: str) -> str:
    return f"{app_id}_lookup"[:120]


def lookup_tools(ctx: Any = None) -> list:
    """One read-only chat tool per live card that can look a subject up
    (``<app>_lookup(topic)``). A brain tools provider: only for the owner."""
    from puffpai import brain_tools
    ctx = ctx if isinstance(ctx, dict) else {}
    if ctx.get("caller") not in ("owner", "terminal"):
        return []
    try:
        apps = lookup_apps()
    except Exception:  # noqa: BLE001 - no cards, no tools
        return []
    out = []
    for manifest, feed, _template in apps[:12]:
        app_id = str(manifest["id"])
        name = str(feed["service"]["name"])

        def _run(args: dict, tctx: dict, _app: str = app_id) -> dict:
            return lookup(_app, args.get("topic"), said=str((tctx or {}).get("said") or ""))
        try:
            out.append(brain_tools.make(
                lookup_tool_name(app_id),
                f"Look a subject up on {name}, an outside service this bot uses "
                f"for its live card. Read-only. Use it when the owner asks about "
                f"something {name} knows. Answer from what it finds and give its "
                f"links.",
                LOOKUP_SCHEMA, _run,
                progress=lambda a, _n=name: f"Looking it up on {_n} ...",
                source="feed"))
        except ValueError:
            continue
    return out


#: A question that asks about the cards in general.
_ALL_CARDS_RE = re.compile(r"\b(?:live\s+cards?|your\s+cards?|the\s+cards?|"
                           r"dashboard)\b")

#: How many cards go into one chat turn, and how much of each.
CHAT_MAX_CARDS = 3
CHAT_MAX_CHARS = 700


def _chat_words(text: Any) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9]+", str(text or "").lower()):
        if len(w) < 3 or w in _CHAT_STOP:
            continue
        out.add(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss")
                else w)
    return out


def _card_words(manifest: dict[str, Any], feed: dict[str, Any]) -> set[str]:
    card = feed.get("card") or {}
    parts = [manifest.get("name"), str(manifest.get("id") or "").replace("_", " "),
             card.get("title"), card.get("label"), feed["service"].get("name"),
             manifest.get("description")]
    about = manifest.get("about_words")
    if isinstance(about, list):
        parts += [str(w) for w in about[:40] if isinstance(w, str)]
    return _chat_words(" ".join(str(p) for p in parts if p))


def card_words_for_chat(state: dict[str, Any]) -> str:
    """One card's latest data as a plain line, for the model to answer from."""
    name = state.get("name") or state.get("app_id")
    card = state.get("card")
    if not card:
        why = state.get("message") or "Nothing has been fetched yet."
        return f"- {name}: has no data right now ({why})"
    kind = card.get("kind")
    if kind == "value":
        unit = f" {card['unit']}" if card.get("unit") else ""
        body = f"{card.get('label')}: {card.get('value')}{unit}"
        if card.get("caption"):
            body += f" ({card['caption']})"
    elif kind == "image":
        body = "picture: " + " - ".join(
            str(card[f]) for f in ("headline", "caption") if card.get(f))
    elif kind == "gallery":
        rows = [str(i.get("title") or "") for i in card.get("items") or []]
        body = f"{len(rows)} pictures"
        if card.get("caption"):
            body += f" ({card['caption']})"
        body += ": " + "; ".join(r for r in rows if r)
    else:
        rows = []
        for item in card.get("items") or []:
            line = str(item.get("title") or "")
            if item.get("detail"):
                line += f" ({str(item['detail'])[:160]})"
            rows.append(line)
        body = "; ".join(rows) if rows else "an empty list"
    when = state.get("fetched_at") or "an unknown time"
    text = f"- {name} (fetched {when}): {body}"
    return text[:CHAT_MAX_CHARS]


def _cards_of_working_jobs() -> set[str]:
    """Live cards that are ingredients of a job that is working (one build
    foundation): that job's own tools answer about them, so their stored
    copy (which may be older than the job) is not put in the chat."""
    try:
        from puffpai.build import schema as build_schema
        if not build_schema.exists():
            return set()
        build = build_schema.load_build()
    except Exception:  # noqa: BLE001 - no build record: every card as before
        return set()
    parts = build.get("parts") or {}
    out: set[str] = set()
    for job in build.get("jobs") or []:
        if job.get("status") != build_schema.WORKING:
            continue
        for ref in job.get("ingredients") or []:
            part = parts.get(ref) if isinstance(parts.get(ref), dict) else {}
            for key in ("live_card", "app_id"):
                if part.get(key):
                    out.add(str(part[key]))
    return out


def chat_context(message: Any, manifests: Optional[list[dict[str, Any]]] = None
                 ) -> str:
    """This bot's own live cards that a chat question is about, as plain lines.

    Matched on the card's own words (its name, title, label, service and
    description), never a topic table. Reads the stored copy, fetching first
    only when the card is due and the owner allowed its service. Empty string
    when no card fits. Never raises.
    """
    try:
        apps = feed_apps(manifests)
    except Exception:
        return ""
    if not apps:
        return ""
    asked = _chat_words(message)
    every = bool(_ALL_CARDS_RE.search(str(message or "").lower()))
    done = _cards_of_working_jobs()
    picked = [(m, f) for m, f in apps if str(m.get("id") or "") not in done
              and (every or (asked & _card_words(m, f)))]
    lines = []
    for manifest, _feed in picked[:CHAT_MAX_CARDS]:
        app_id = str(manifest.get("id") or "")
        try:
            state = refresh(app_id)
        except Exception:
            try:
                state = card_state(app_id)
            except Exception:
                continue
        lines.append(card_words_for_chat(state))
    return "\n".join(lines)


class FeedRefreshSource:
    """A scheduler source that keeps the allowed live cards fresh.

    Each tick it refreshes every installed card whose service the owner
    allowed, and :func:`refresh` itself only fetches when the card's own
    ``refresh_minutes`` have passed. It schedules nothing to announce.
    """

    name = "live_cards"

    def refresh(self) -> None:
        for manifest, _feed in feed_apps():
            app_id = str(manifest.get("id") or "")
            try:
                if service_state(app_id)["state"] == "open":
                    refresh(app_id)
            except Exception as exc:  # noqa: BLE001 - next tick tries again
                logger.info("live card %s not refreshed: %s", app_id,
                            type(exc).__name__)

    def occurrences(self) -> list:
        return []

    def mark(self, occ, status, iso, ms) -> None:  # noqa: ARG002
        return None

    def sweep(self, now_ms, ring_timeout_ms) -> list:  # noqa: ARG002
        return []

    def flush(self) -> None:
        return None


def ui_summary(manifest: dict[str, Any]) -> Optional[dict[str, Any]]:
    """A small, bounded description of an app's feed for ``ui_records``."""
    feed = feed_of(manifest)
    if feed is None:
        return None
    return {"kind": feed["card"]["kind"], "title": feed["card"]["title"],
            "service": feed["service"]["name"], "host": feed["service"]["host"],
            "image_host": feed["service"].get("image_host"),
            "what_leaves": what_leaves(feed), "credit": feed["credit"]}


# ---------------------------------------------------------------------------
# what this build can do (rows for the abilities registry)
# ---------------------------------------------------------------------------

def _action_name(text: Any) -> str:
    """A name fit to sit inside an action's sentence (see plain_words)."""
    clean = re.sub(r"[^A-Za-z0-9 ,.&()'+/:-]", " ", str(text or ""))
    clean = " ".join(clean.split())[:80].strip(" ,.:-/")
    return clean if clean[:1].isalnum() else "this card"


def _fix_action(state: dict[str, Any], app_id: str, name: str,
                service: str) -> Optional[dict[str, Any]]:
    """The action behind a service state's command, or None."""
    if state.get("state") in ("not_asked", "said_no"):
        return {"id": "apps.allow",
                "args": {"app": app_id, "name": _action_name(name),
                         "service": _action_name(service)}}
    if state.get("state") == "bot_offline":
        return {"id": "services.on",
                "args": {"app": app_id, "name": _action_name(name)}}
    return None


def ability_rows() -> list[dict[str, Any]]:
    """One ability row per installed live-data app, and per waiting draft.

    Shape: ``{"id", "label", "status", "how_to_add"}``. ``status`` is
    ``available`` only when the service is actually open right now.
    """
    from puffpai import plain_words
    rows: list[dict[str, Any]] = []
    try:
        apps = feed_apps()
        sync_doors()
    except Exception as exc:
        logger.debug("feed ability rows unavailable: %s", exc)
        return [{"id": "live:apps", "label": "Live data from outside services",
                 "status": "unknown", "how_to_add": None}]
    for manifest, feed in apps:
        app_id = str(manifest["id"])
        name = str(manifest.get("name") or app_id)
        service = str(feed["service"]["name"])
        label = (f"Show live {name} on a card" if service.lower() == name.lower()
                 else f"Show live {name} from {service} on a card")[:120]
        try:
            state = service_state(app_id)
        except Exception:
            rows.append({"id": f"live:{app_id}", "label": label,
                         "status": "unknown", "how_to_add": None})
            continue
        row = {
            "id": f"live:{app_id}", "label": label,
            "status": "available" if state["state"] == "open" else "not_set_up",
            "how_to_add": None if state["state"] == "open" else state["how_to_fix"],
        }
        act = _fix_action(state, app_id, name, service)
        if act is not None:
            row = plain_words.with_action(row, act)
        rows.append(row)
    try:
        from puffpai import feed_generator
        installed = {str(m.get("id")) for m, _f in apps}
        from puffpai import drawbridge
        for draft in feed_generator.list_drafts():
            if draft["id"] in installed:
                continue
            last = drawbridge.latest_for(draft["id"]) or {}
            if last.get("verdict") == drawbridge.BLOCK:
                # Stopped by the checks: not something "ready to add".
                rows.append({"id": f"live:{draft['id']}",
                             "label": f"Live {draft['name']}"[:120],
                             "status": "not_in_this_build",
                             "note": "stopped by BuildBot's safety checks",
                             "how_to_add": None})
                continue
            rows.append(plain_words.with_action({
                "id": f"live:{draft['id']}",
                "label": f"Live {draft['name']} (ready to add)"[:120],
                "status": "not_set_up",
                "how_to_add": f"puffpai apps install {draft['id']}",
            }, {"id": "apps.install",
                "args": {"app": draft["id"],
                         "name": _action_name(draft["name"])}}))
    except Exception as exc:
        logger.debug("feed drafts unavailable for ability rows: %s", exc)
    return rows


# ---------------------------------------------------------------------------
# the wizard's single summary: the "live_info" section
# ---------------------------------------------------------------------------

def _discover_live(goal: str, terms: list[str]) -> list[dict[str, Any]]:
    """Live-data Candidates from the discovery module, when it is in this build."""
    for module_name in ("puffpai.live_discovery", "puffpai.api_discovery",
                        "puffpai.discovery"):
        try:
            module = __import__(module_name, fromlist=["discover_live"])
        except ImportError:
            continue
        fn = getattr(module, "discover_live", None)
        if callable(fn):
            try:
                found = fn(goal, terms)
            except TypeError:
                found = fn(goal)
            return [c for c in (found or []) if isinstance(c, dict)]
    return []


def _clean_catalog_text(value: Any, limit: int) -> str:
    """Catalog text is data: plain, single-line, bounded."""
    return as_text(value if isinstance(value, str) else "", limit)


def proposal_items(goal: str, terms: list[str],
                   candidates: Optional[list[dict[str, Any]]] = None
                   ) -> list[dict[str, Any]]:
    """ProposalItems for the ``live_info`` section of the wizard's summary.

    Built only from live-data Candidates code returned (discovery, or
    *candidates* when a caller passes them). Keyless ones are proposed ticked;
    ones that need a key are listed unticked with a plain note, because this
    build has no safe place for a key yet.
    """
    found = candidates if candidates is not None else _discover_live(goal, terms)
    wanted = {str(t).strip().lower() for t in (terms or []) if str(t).strip()}
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for cand in found:
        if not isinstance(cand, dict) or cand.get("kind") != "live_data":
            continue
        cid = str(cand.get("id") or "").strip()
        host = str(cand.get("host") or "").strip().lower()
        if not cid or not _is_registrable(host):
            continue
        match = {str(t).lower() for t in (cand.get("match_terms") or [])}
        if wanted and match and not (wanted & match):
            continue
        catalog = re.sub(r"[^a-z0-9_.-]", "", str(cand.get("source_catalog") or
                                                  "catalog").lower())[:40] or "catalog"
        item_id = f"live_info:{catalog}:{re.sub(r'[^A-Za-z0-9_.-]', '', cid)[:60]}"
        if item_id in seen:
            continue
        seen.add(item_id)
        # Catalog text that reads like an order or holds a web address is
        # never shown; the line falls back to the website's name, unticked.
        from puffpai.artifact_review import reads_like_orders
        odd = [f for f in ("name", "what_it_is", "license")
               if reads_like_orders(_clean_catalog_text(cand.get(f), 400))]
        name = ("" if "name" in odd else _clean_catalog_text(cand.get("name"), 60)) or host
        what = "" if "what_it_is" in odd else _clean_catalog_text(cand.get("what_it_is"), 160)
        license_text = ("" if "license" in odd else
                        _clean_catalog_text(cand.get("license"), 60)) or "not stated"
        auth = cand.get("auth")
        detail = (f"{what + ' ' if what else ''}Comes from {host}. What leaves "
                  f"your computer: a request for the data and your internet "
                  f"address. License: {license_text}.")
        if cand.get("commercial_ok") is False:
            detail += " Not for business use."
        if odd:
            detail = ("Its description had odd words in it, so they are not "
                      "shown. " + detail)
        docs = str(cand.get("url") or "")
        if not (docs.startswith("https://") and _is_registrable(
                (urlsplit(docs).hostname or "") if docs else "")):
            docs = f"https://{host}/"
        if auth == "none":
            items.append({
                "id": item_id, "section": "live_info",
                "label": f"Live: {name}"[:80], "detail": detail[:400],
                "size_bytes": None, "outside_host": host,
                "default_on": license_text != "not stated" and not odd,
                "needs_yes": True,
                "apply": {"kind": "live_feed", "ref": cid},
            })
        else:
            items.append({
                "id": item_id, "section": "live_info",
                "label": f"Live: {name} (needs a sign-up key)"[:80],
                "detail": (detail + " It needs a sign-up key, and this bot has "
                           "no safe place for keys yet, so it is listed for "
                           "you to look at yourself.")[:400],
                "size_bytes": None, "outside_host": host,
                "default_on": False, "needs_yes": True,
                "apply": {"kind": "look_yourself", "ref": docs[:300]},
            })
    return items


__all__ = [
    "CARD_KINDS", "FeedError", "MAX_RESPONSE_BYTES", "MIN_REFRESH_MINUTES",
    "ability_rows", "as_text", "build_url", "card_state", "extract",
    "feed_apps", "feed_of", "fill_url", "home_block", "image_file",
    "image_url_ok", "latest_readings", "FeedRefreshSource", "parse_path",
    "fill_image_template", "image_template_problem", "varies",
    "services_on", "start_first_fetch", "request_headers", "UA_HEADERS",
    "JSON_SENT_AS",
    "proposal_items", "read_cache", "refresh",
    "resolve", "service_state", "set_allowed", "set_choice", "sync_doors",
    "ui_summary", "valid_path", "validate_feed", "what_leaves",
]
