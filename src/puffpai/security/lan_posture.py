"""LAN posture — the ``island`` half of the egress contract, enforced.

:mod:`puffpai.security.egress_doors` closes the doors to the public internet.
This module closes the hole underneath them: a *configured* service URL that
points somewhere it should not.

The ``island`` posture promises "your home network is allowed, the wider
internet is not". Nothing checked that promise against the addresses in the
config, so ``ollama.url = "https://someone-elses-box.example.com"`` was
unrestricted egress that no door saw — and it carries the worst payload in the
product, because every prompt in the household goes to the brain. Same for
``ha.url`` (a long-lived Home Assistant token in an Authorization header), n8n,
Kiwix and the media servers.

Two postures, two rules
-----------------------
``sealed``
    Only this machine. Loopback (``127.0.0.1``, ``::1``, ``localhost``) and
    nothing else. **Home Assistant on another host requires the ``island``
    posture** — that is not a bug, it is what ``sealed`` means.
``island`` / ``enumerated``
    Loopback plus the home network: RFC 1918 (``10/8``, ``172.16/12``,
    ``192.168/16``), link-local, unique-local IPv6 (``fc00::/7``), and names
    that live inside a house — ``.local``, ``.lan``, ``.home.arpa``,
    ``.internal``, and single-label names — provided they resolve there.
    ``enumerated`` is a superset of ``island``: it adds named WAN doors, it
    does not loosen the LAN.

Two checks, because one of them must not touch the network
----------------------------------------------------------
:func:`classify_host` is a **name-form** check: it reads literal addresses with
:mod:`ipaddress` and judges names by their shape. It performs no DNS and cannot
block, which is why it is the one that runs at config load. Startup must not
hang on a resolver.

:func:`classify_host_resolving` resolves and checks **every** address the name
answers with. It is used where a definitive answer matters and a short wait is
acceptable: the wizard step that writes a URL, and ``/system/status``. It is
what catches a single-label name that a search domain quietly sends off-site.

What "refuse" means, per surface
--------------------------------
* **The wizard write path** refuses outright. A URL that violates the posture
  is never persisted, so the common case never reaches the other two surfaces.
* **Config load** cannot refuse by raising — a bad string in a config file must
  not make the appliance unbootable. It *neutralises* instead, which is the
  fail-closed action that still leaves a working box: an optional integration
  is forced ``enabled = False``, and ``ollama.url`` is reset to the loopback
  default rather than left pointing off-site. Every neutralisation is recorded
  and logged.
* **``/system/status``** publishes what was found, so a neutralisation is
  visible rather than silent.

The Bridge on top
-----------------
With the Bridge up (``drawbridge_up``, :func:`puffpai.security.bridge_guard.reach_level`
is ``switched_on``) the bot reaches nothing at home except the services the
owner switched on. A service with its own switch (Home Assistant, n8n, the
media server: ``enabled``) is allowed on the home network only while it is
on, which :func:`_urls_from_config` already honours. The two with no switch
of their own, the brain (Ollama) and the books (Kiwix), must then be on this
computer: a home-network address for either is a violation, refused on the
write path and neutralised at load like any other. The dashboard hand-off
address is not something the bot reaches and is not judged by the Bridge.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlsplit

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

#: Default resolve timeout. Short on purpose: this runs behind a status route.
RESOLVE_TIMEOUT = 2.0

#: Name suffixes that name something inside a house. A name ending in one of
#: these is presumed LAN by the name-form check and still verified by the
#: resolving check.
_HOUSE_SUFFIXES = (".local", ".lan", ".home.arpa", ".internal", ".localdomain",
                   ".home", ".intranet", ".private")

_LOOPBACK_NAMES = ("localhost", "localhost.localdomain", "ip6-localhost")

#: Ranges :mod:`ipaddress` calls private that are nothing of the kind. RFC 5737
#: TEST-NET-1/2/3 and RFC 3849 2001:db8::/32 are documentation ranges; RFC 2544
#: 198.18/15 is for benchmarking. None of them is a home network, so allowing
#: one under ``island`` would be allowing an address that is neither yours nor
#: reachable — the permissive direction, which is the one that matters here.
_NOT_REALLY_PRIVATE = tuple(__import__("ipaddress").ip_network(c) for c in (
    "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24",
    "198.18.0.0/15", "2001:db8::/32",
))

#: Cloud providers' instance-data services. They sit in link-local space, so
#: they classify as PRIVATE like any link-local device, but they are a cloud
#: machine's own service, never a device in a house. A configured service URL
#: (which carries a password or every prompt) may not point at one under any
#: posture. Only :func:`check_url` applies this; the classifier is unchanged.
_CLOUD_METADATA = frozenset(__import__("ipaddress").ip_address(a) for a in (
    "169.254.169.254", "169.254.170.2", "fd00:ec2::254",
))

#: Classification results.
LOOPBACK = "loopback"
PRIVATE = "private"
LAN_PRESUMED = "lan_presumed"
PUBLIC = "public"
UNRESOLVABLE = "unresolvable"
UNPARSEABLE = "unparseable"

#: What each posture permits a configured service URL to be.
_ALLOWED: dict[str, frozenset[str]] = {
    "sealed": frozenset({LOOPBACK}),
    "island": frozenset({LOOPBACK, PRIVATE, LAN_PRESUMED}),
    "enumerated": frozenset({LOOPBACK, PRIVATE, LAN_PRESUMED}),
}


class PostureViolation(ValueError):
    """A configured URL is not allowed at this build's run posture.

    ``user_message`` is written to be shown to a person verbatim: it names the
    URL, the posture, and what to do instead.
    """

    def __init__(self, service: str, url: str, posture: str, kind: str,
                 user_message: str, detail: str = ""):
        super().__init__(f"{service}: {url} violates posture {posture!r} ({kind})")
        self.service = service
        self.url = url
        self.posture = posture
        self.kind = kind
        self.user_message = user_message
        self.detail = detail

    def as_dict(self) -> dict[str, Any]:
        return {"service": self.service, "url": self.url,
                "posture": self.posture, "classification": self.kind,
                "detail": self.detail, "message": self.user_message}


@dataclass(frozen=True)
class LanService:
    """One configured LAN destination, and how to neutralise it."""

    id: str
    #: Dotted path as it appears in config, for the message.
    config_path: str
    #: What talks to it, and what it would leak.
    stakes: str
    #: Config section carrying an ``enabled`` flag we can force False, if any.
    enable_section: Optional[str] = None
    #: Safe value to fall back to when there is no enable flag.
    safe_default: Optional[str] = None


#: Every LAN destination this build reads out of config. A new one belongs
#: here; ``tests/test_lan_posture_config_urls.py`` fails the build if a URL
#: field appears in ``config.py`` that this table does not cover.
SERVICES: tuple[LanService, ...] = (
    LanService(
        id="ollama", config_path="ollama.url",
        stakes="Every prompt anyone in the household types goes to this "
               "address. A public host here is the worst leak in the product.",
        safe_default="http://localhost:11434"),
    LanService(
        id="home_assistant", config_path="ha.url",
        stakes="A long-lived Home Assistant token is sent to this address in "
               "an Authorization header, along with the state of the house.",
        enable_section="ha"),
    LanService(
        id="n8n", config_path="n8n.url",
        stakes="Workflow payloads, which carry whatever the workflow touches.",
        enable_section="n8n"),
    LanService(
        id="kiwix", config_path="kiwix.url",
        stakes="Every knowledge-pack query.",
        safe_default=""),
    LanService(
        id="jellyfin", config_path="media_server.jellyfin_url",
        stakes="A Jellyfin API key and what the household is watching.",
        enable_section="media_server"),
    LanService(
        id="calibre_web", config_path="media_server.calibre_web_url",
        stakes="What the household is reading.",
        enable_section="media_server"),
    LanService(
        id="dashboard", config_path="dashboard.url",
        stakes="The address the owner is handed off to. A public one sends "
               "them, and their session, somewhere this build does not run.",
        safe_default=""),
)

_BY_ID: dict[str, LanService] = {s.id: s for s in SERVICES}


def service(service_id: str) -> Optional[LanService]:
    """The service row with this id, or None."""
    return _BY_ID.get(str(service_id).strip().lower())


def service_ids() -> tuple[str, ...]:
    return tuple(s.id for s in SERVICES)


# ── address + name classification ────────────────────────────────────────────

#: IPv6 ranges that carry an IPv4 address inside them: NAT64 (RFC 6052 well
#: known prefix, and the local-use /48 of RFC 8215), 6to4 (RFC 3056), Teredo
#: (RFC 4380), IPv4-compatible (::a.b.c.d, RFC 4291 2.5.5.1) and IPv4-
#: translated (::ffff:0:a.b.c.d, RFC 2765). ``ipaddress`` reports most of
#: them as private or reserved, so without this an internet address wrapped
#: in one reads as a home-network address.
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_NAT64_LOCAL = ipaddress.ip_network("64:ff9b:1::/48")
_SIX_TO_FOUR = ipaddress.ip_network("2002::/16")
_TEREDO = ipaddress.ip_network("2001::/32")
_V4_COMPATIBLE = ipaddress.ip_network("::/96")
_V4_TRANSLATED = ipaddress.ip_network("::ffff:0:0:0/96")


def _embedded_ipv4(addr: Any) -> Optional[list]:
    """The IPv4 address(es) an IPv6 address carries, or None if it carries none."""
    if not isinstance(addr, ipaddress.IPv6Address):
        return None
    value = int(addr)
    if addr.ipv4_mapped is not None:
        return [addr.ipv4_mapped]
    if addr in _NAT64 or addr in _V4_TRANSLATED:
        return [ipaddress.IPv4Address(value & 0xFFFFFFFF)]
    if addr in _NAT64_LOCAL:
        # The local-use /48 (RFC 8215) may be used with ANY prefix length
        # RFC 6052 allows from /48 on, and the address does not say which.
        # ``64:ff9b:1::808:808`` is 0.0.0.0 read as a /48 but 8.8.8.8 read
        # as a /96, so reading only one layout can judge it loopback.
        # So every layout is read and the worst verdict wins:
        #   /48: bits 48-63 and 72-87 (bits 64-71 are the zero "u" octet),
        #   /56: bits 56-63 and 72-95,
        #   /64: bits 72-103,
        #   /96: bits 96-127.
        return [
            ipaddress.IPv4Address((((value >> 64) & 0xFFFF) << 16)
                                  | ((value >> 40) & 0xFFFF)),
            ipaddress.IPv4Address((((value >> 64) & 0xFF) << 24)
                                  | ((value >> 32) & 0xFFFFFF)),
            ipaddress.IPv4Address((value >> 24) & 0xFFFFFFFF),
            ipaddress.IPv4Address(value & 0xFFFFFFFF),
        ]
    if addr in _SIX_TO_FOUR:
        return [addr.sixtofour]
    if addr in _TEREDO:
        server, client = addr.teredo
        return [server, client]
    if addr in _V4_COMPATIBLE and value > 1:  # not :: and not ::1
        return [ipaddress.IPv4Address(value & 0xFFFFFFFF)]
    return None


def _numeric_ipv4(text: str) -> Optional[Any]:
    """The IPv4 address a C resolver reads *text* as, else None.

    glibc's inet_aton (which getaddrinfo uses on the box) accepts more than
    the dotted-quad: ``0xa9fea9fe`` and ``2852039166`` are both
    169.254.169.254, and ``0177.1`` is 127.0.0.1. ipaddress refuses those
    spellings, so without this they read as LAN names or domains and the
    address check was decided by spelling. 1 to 4 parts, each decimal, 0x
    hex or 0-led octal; the last part fills the remaining bytes.
    """
    parts = str(text).split(".")
    if not 1 <= len(parts) <= 4:
        return None
    values = []
    for part in parts:
        # Character sets checked by hand: int() would also take "_", "+"
        # and surrounding spaces, which no resolver does.
        if part[:2].lower() == "0x":
            digits, base, allowed = part[2:], 16, "0123456789abcdefABCDEF"
        elif len(part) > 1 and part[0] == "0":
            digits, base, allowed = part[1:], 8, "01234567"
        else:
            digits, base, allowed = part, 10, "0123456789"
        if not digits or any(c not in allowed for c in digits):
            return None
        values.append(int(digits, base))
    *head, last = values
    if any(v > 0xFF for v in head) or last >= 1 << (8 * (4 - len(head))):
        return None
    value = 0
    for v in head:
        value = (value << 8) | v
    value = (value << (8 * (4 - len(head)))) | last
    return ipaddress.IPv4Address(value)


def _classify_address(addr_text: str) -> str:
    """Classify one literal address. Returns '' for a non-address."""
    try:
        addr = ipaddress.ip_address(str(addr_text).split("%", 1)[0])
    except ValueError:
        addr = _numeric_ipv4(addr_text)
        if addr is None:
            return ""
    inner = _embedded_ipv4(addr)
    if inner:
        # ``::ffff:127.0.0.1`` is loopback wearing a costume, and
        # ``64:ff9b::808:808`` is 8.8.8.8 in one. Judge what it carries, not
        # the wrapper; with two (Teredo's server and client) the worse wins.
        verdicts = [_classify_address(str(v)) for v in inner]
        for worst in (PUBLIC, PRIVATE, LOOPBACK):
            if worst in verdicts:
                return worst
        return PUBLIC
    if addr.is_loopback or addr.is_unspecified:
        # 0.0.0.0 means "this machine" to a connecting client on every stack
        # that resolves it at all, so it is judged with loopback rather than
        # being some third thing that slips between the two rules.
        return LOOPBACK
    if any(addr in net for net in _NOT_REALLY_PRIVATE):
        # Python reports the documentation and benchmark ranges as
        # 'private', which is true of their routing and false of their
        # meaning: nothing in a house answers on 203.0.113.5. Treating them as
        # home-network addresses would let a config point at a range that is
        # neither reachable nor yours, under a posture that promises the LAN.
        return PUBLIC
    if addr.is_link_local or addr.is_private or addr.is_reserved:
        return PRIVATE
    return PUBLIC


def host_of(url: str) -> str:
    """The lower-case hostname in *url*, or '' if there is not one."""
    try:
        parts = urlsplit(str(url).strip())
        # Touch .port too: an unbracketed IPv6 literal
        # ("http://2001:db8::1:11434") parses with hostname "2001" and an
        # invalid port, and "2001" is a single label, which this module would
        # otherwise read as a LAN name. A malformed URL must fail closed, not
        # land in the most permissive class.
        parts.port
        host = parts.hostname or ""
    except ValueError:
        return ""
    if ":" in host and not host.startswith("["):
        # urlsplit strips the brackets from a real IPv6 literal, so a colon
        # surviving here means the address was not bracketed.
        try:
            import ipaddress as _ip
            _ip.ip_address(host.split("%", 1)[0])
        except ValueError:
            return ""
    return host.strip().rstrip(".").lower()


def classify_host(host: str) -> str:
    """Classify a host by literal address or by name form. Opens no socket.

    This is the check that runs at config load, so it must never block. A name
    it cannot judge from its shape comes back :data:`LAN_PRESUMED` only when the
    shape says "inside a house"; anything that looks like a registered domain is
    :data:`PUBLIC`, because guessing permissively is how the hole got there.
    """
    host = (host or "").strip().rstrip(".").lower()
    if not host:
        return UNPARSEABLE
    literal = _classify_address(host)
    if literal:
        return literal
    if host in _LOOPBACK_NAMES:
        return LOOPBACK
    if host.endswith(_HOUSE_SUFFIXES):
        return LAN_PRESUMED
    if "." not in host:
        # A single-label name is a LAN name in every normal setup. A search
        # domain can still send it off-site, which is precisely what the
        # resolving check exists to catch.
        return LAN_PRESUMED
    return PUBLIC


def _resolve(host: str, timeout: float) -> list[str]:
    """Every address *host* answers with. Raises OSError if it answers none."""
    previous = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    finally:
        socket.setdefaulttimeout(previous)
    # sockaddr[0] is the address for both AF_INET and AF_INET6; str() is for
    # the type checker, which sees the whole sockaddr union.
    return [str(info[4][0]) for info in infos]


def classify_host_resolving(host: str, *, timeout: float = RESOLVE_TIMEOUT,
                            resolver=None) -> tuple[str, str]:
    """Resolve *host* and classify **every** address it answers with.

    Returns ``(classification, detail)``. The worst answer wins: a name that
    resolves to one private address and one public address is :data:`PUBLIC`,
    because a client connects to whichever the stack hands it.

    A literal address is judged directly and never resolved. A name that
    resolves to nothing is :data:`UNRESOLVABLE`, which callers treat as a
    warning rather than a violation — a Home Assistant that is merely switched
    off must not read as a posture breach.
    """
    host = (host or "").strip().rstrip(".").lower()
    if not host:
        return UNPARSEABLE, "no host in the URL"
    literal = _classify_address(host)
    if literal:
        return literal, host
    if host in _LOOPBACK_NAMES:
        return LOOPBACK, host
    try:
        addresses = (resolver or _resolve)(host, timeout)
    except OSError as exc:
        return UNRESOLVABLE, f"{host} does not resolve ({type(exc).__name__})"
    if not addresses:
        return UNRESOLVABLE, f"{host} resolves to nothing"
    seen = {addr: (_classify_address(addr) or PUBLIC) for addr in addresses}
    if PUBLIC in seen.values():
        offenders = sorted(a for a, k in seen.items() if k == PUBLIC)
        return PUBLIC, f"{host} resolves to {', '.join(offenders)}"
    if PRIVATE in seen.values():
        return PRIVATE, f"{host} resolves to {', '.join(sorted(seen))}"
    return LOOPBACK, f"{host} resolves to {', '.join(sorted(seen))}"


# ── posture ──────────────────────────────────────────────────────────────────

#: Posture the wizard answered in THIS run, set at Step 1 before anything is
#: written. An in-process override, never persisted from here: the manifest is
#: still the record, this is only how a run that has not written one yet knows
#: what it is building.
_active_posture: Optional[str] = None


def set_active_posture(posture: Any) -> str:
    """Declare the posture this wizard run is building. Returns what was set.

    Called from the posture step so every later write in the same run is judged
    against the answer the owner just gave, rather than against a manifest that
    does not exist yet. Normalised through
    :func:`puffpai.build_posture.assess_posture`, so a junk value becomes the
    safe default rather than being honoured.
    """
    global _active_posture
    from puffpai import build_posture

    if posture is None:
        _active_posture = None
        return ""
    raw = (posture.get("run_egress") if isinstance(posture, dict)
           else posture)
    _active_posture = str(
        build_posture.assess_posture(None, raw, None)["run_egress"])
    return _active_posture


def clear_active_posture() -> None:
    """Forget the run posture (end of a wizard run, and tests)."""
    global _active_posture
    _active_posture = None


def active_posture() -> Optional[str]:
    """The posture this run declared, or None."""
    return _active_posture


def posture_block_from_config_dir(config_dir: str) -> Optional[dict[str, Any]]:
    """The raw ``posture`` block on disk, or None when there is no manifest.

    The None matters: "no manifest" and "a manifest that says island" are not
    the same fact, and a first run is the first case. Collapsing them is exactly
    how ``--run-egress sealed --ollama-url <public>`` was accepted on a fresh box.
    """
    try:
        with open(os.path.join(config_dir, "build_manifest.json"),
                  "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and isinstance(data.get("posture"), dict):
        return data["posture"]
    return None


def write_path_posture(config_dir: str = "") -> str:
    """The posture a WRITE must be judged against. Defaults to ``sealed``.

    Order: the posture this run declared, then the manifest if one exists, then
    ``sealed``. The last step is the fix for a first run: with no manifest on
    disk there is nothing that says what is being built, and "nothing known"
    must mean the most restrictive rule, not the default one. Refusing a write
    costs a person one clear message; accepting it bakes a public address into
    a box whose owner asked for ``sealed``.
    """
    from puffpai import build_posture

    if _active_posture:
        return _active_posture
    if not config_dir:
        try:
            from puffpai.config import get_config
            config_dir = get_config().CONFIG_DIR
        except Exception:
            return "sealed"
    block = posture_block_from_config_dir(config_dir)
    if block is None:
        return "sealed"
    return str(build_posture.assess_posture(
        block.get("build_network"), block.get("run_egress"), None)["run_egress"])


def posture_from_config_dir(config_dir: str) -> str:
    """The build's ``run_egress``, read straight off disk and normalised.

    Deliberately does NOT go through :func:`puffpai.build_manifest.read_manifest`
    or :func:`puffpai.config.get_config`: this is called *from inside* the config
    loader and either route would recurse. It reads the JSON and hands the values
    to :func:`puffpai.build_posture.assess_posture`, which is where the
    fail-closed normalisation already lives — an unrecognised value becomes the
    safe default rather than being honoured.
    """
    from puffpai import build_posture

    block: dict[str, Any] = {}
    try:
        with open(os.path.join(config_dir, "build_manifest.json"),
                  "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("posture"), dict):
            block = data["posture"]
    except (OSError, ValueError):
        block = {}
    settled = build_posture.assess_posture(block.get("build_network"),
                                           block.get("run_egress"), None)
    return str(settled["run_egress"])


def current_posture() -> str:
    """The build's ``run_egress`` for callers outside the config loader."""
    try:
        from puffpai.config import get_config
        return posture_from_config_dir(get_config().CONFIG_DIR)
    except Exception as exc:
        logger.debug("posture unavailable for LAN check: %s", exc)
        from puffpai import build_posture
        return build_posture.DEFAULT_RUN_EGRESS


def allowed_classifications(posture: str) -> frozenset[str]:
    """What a service URL may be at this posture. Unknown posture fails shut."""
    return _ALLOWED.get(str(posture), _ALLOWED["sealed"])


# ── the check ────────────────────────────────────────────────────────────────

def _message(svc_path: str, url: str, host: str, posture: str, kind: str,
             detail: str, stakes: str) -> str:
    """The sentence a person reads. Names the posture, never jargon-only."""
    where = detail if detail and detail != host else host
    if posture == "sealed":
        rule = ("This build's run posture is 'sealed', which allows nothing "
                "but this machine itself: 127.0.0.1, ::1 or localhost. "
                "Home Assistant, Ollama or a media server on any OTHER host, "
                "even one inside your house, needs the 'island' posture "
                "instead.")
        fix = ("Point it at localhost, or rebuild with the 'island' posture "
               "if this service really does run on another box.")
    else:
        rule = (f"This build's run posture is '{posture}', which allows your "
                f"home network and nothing beyond it.")
        fix = ("Point it at a private address (192.168.x.x, 10.x.x.x, "
               "172.16-31.x.x) or a .local / .lan name on your own network. "
               "There is no posture in this build that lets a core service "
               "live on the public internet.")
    return (f"{svc_path} is set to {url}, and {where} is on the public "
            f"internet. {rule} {stakes} {fix}")


#: With the Bridge up these must be on this computer: they have no switch of
#: their own, so nothing the owner switched on can speak for them.
BRIDGE_LOCAL_ONLY = frozenset({"ollama", "kiwix"})


def _bridge_level(bridge_level: Optional[str],
                  config_dir: str = "") -> str:
    """The Bridge level to judge with: the one given, else the one in force."""
    if bridge_level:
        return str(bridge_level)
    try:
        from puffpai.security import bridge_guard
        return bridge_guard.reach_level(config_dir or None)
    except Exception:
        return "switched_on"


def _bridge_violation(service_id: str, url: str, host: str, level: str,
                      resolve: bool, resolver, timeout: float
                      ) -> Optional[PostureViolation]:
    """The Bridge's own verdict on one configured URL, or None."""
    if level != "switched_on" or service_id not in BRIDGE_LOCAL_ONLY:
        return None
    kind = classify_host(host)
    detail = host
    if kind != LOOPBACK and resolve:
        resolved, why = classify_host_resolving(host, timeout=timeout,
                                                resolver=resolver)
        if resolved == LOOPBACK:
            kind, detail = LOOPBACK, why
    if kind == LOOPBACK:
        return None
    svc = _BY_ID.get(service_id)
    path = svc.config_path if svc else service_id
    what = "brain" if service_id == "ollama" else "books"
    return PostureViolation(
        service_id, url, "drawbridge_up", kind,
        f"{path} is set to {url}, which is not on this computer. The Bridge "
        f"is up, so your bot reaches nothing on your home network except the "
        f"services you switched on, and its {what} must be on this computer. "
        f"Point it at localhost, or choose a different Bridge setting in "
        f"Setup.", detail=detail)


def check_url(service_id: str, url: str, posture: str, *, resolve: bool = False,
              resolver=None, timeout: float = RESOLVE_TIMEOUT,
              bridge_level: Optional[str] = None,
              ) -> Optional[PostureViolation]:
    """Judge one configured URL against *posture*. Returns a violation or None.

    An empty URL is "not configured" and is never a violation. An
    :data:`UNRESOLVABLE` name is a warning, not a violation: a service that is
    switched off must not read as a posture breach.

    The Bridge is judged too (*bridge_level*, else the one in force): with it
    up, the brain and the books must be on this computer.
    """
    url = (url or "").strip()
    if not url:
        return None
    svc = _BY_ID.get(service_id)
    path = svc.config_path if svc else service_id
    stakes = svc.stakes if svc else ""

    host = host_of(url)
    if not host:
        return PostureViolation(
            service_id, url, posture, UNPARSEABLE,
            f"{path} is set to {url!r}, which has no host in it. Nothing can "
            f"check where that points, so it is refused.")

    found = _check_url_posture(service_id, url, posture, host, path, stakes,
                               resolve=resolve, resolver=resolver,
                               timeout=timeout)
    if found is not None:
        return found
    metadata = _cloud_metadata_address(host, resolve, resolver, timeout)
    if metadata:
        return PostureViolation(
            service_id, url, posture, PUBLIC,
            f"{path} is set to {url}, and {metadata} is a cloud provider's "
            f"machine-data service, not a device in your home. Nothing is "
            f"sent there. Point it at the address of the device on your own "
            f"network.", detail=metadata)
    return _bridge_violation(service_id, url, host,
                             _bridge_level(bridge_level), resolve, resolver,
                             timeout)


def _cloud_metadata_address(host: str, resolve: bool, resolver,
                            timeout: float) -> str:
    """The cloud instance-data address *host* is or resolves to, else ''."""
    def _hit(text: str) -> bool:
        try:
            addr = ipaddress.ip_address(str(text).split("%", 1)[0])
        except ValueError:
            addr = _numeric_ipv4(text)
            if addr is None:
                return False
        return addr in _CLOUD_METADATA or any(
            inner in _CLOUD_METADATA for inner in (_embedded_ipv4(addr) or []))
    if _hit(host):
        return host
    if (not resolve or _classify_address(host)
            or classify_host(host) in (LOOPBACK, UNPARSEABLE)):
        return ""
    try:
        addresses = (resolver or _resolve)(host, timeout)
    except OSError:
        return ""
    for addr in addresses or []:
        if _hit(addr):
            return str(addr)
    return ""


def _check_url_posture(service_id: str, url: str, posture: str, host: str,
                       path: str, stakes: str, *, resolve: bool, resolver,
                       timeout: float) -> Optional[PostureViolation]:
    """The run setting's verdict on one URL (the part before the Bridge)."""
    by_name = classify_host(host)
    if resolve:
        kind, detail = classify_host_resolving(host, timeout=timeout,
                                               resolver=resolver)
        if kind == UNRESOLVABLE:
            # Resolving may only ever make the verdict WORSE, never better. A
            # name that looks like a registered domain and answers nothing is
            # still a public name: an NXDOMAIN today says nothing about what it
            # answers tomorrow, and treating "cannot check" as "fine" is how a
            # resolver outage would become a way around this whole module.
            if by_name == PUBLIC:
                kind = PUBLIC
            else:
                # A .local name that is merely switched off is not a breach.
                return None
    else:
        kind, detail = by_name, host
    if kind in allowed_classifications(posture):
        return None
    if kind == UNPARSEABLE:
        return PostureViolation(
            service_id, url, posture, kind,
            f"{path} is set to {url!r}, which has no host in it.")
    if posture == "sealed" and kind in (PRIVATE, LAN_PRESUMED):
        # Not public — just not this machine. Say the right thing.
        return PostureViolation(
            service_id, url, posture, kind,
            f"{path} is set to {url}, which is on your home network but not "
            f"on this machine. This build's run posture is 'sealed', which "
            f"allows nothing outbound at all except this machine itself "
            f"(127.0.0.1 / localhost). Home Assistant, Ollama or a media "
            f"server on another host requires the 'island' posture. "
            f"Point it at localhost, or rebuild with 'island'.",
            detail=detail)
    return PostureViolation(
        service_id, url, posture, kind,
        _message(path, url, host, posture, kind, detail, stakes),
        detail=detail)


def _urls_from_config(cfg: Any) -> list[tuple[str, str]]:
    """``(service_id, url)`` for every LAN service that is actually in play.

    A service with an ``enabled`` flag that is off is skipped. Its URL is
    usually just the shipped default — ``ha.url`` ships as
    ``http://homeassistant.local:8123``, which is a violation under ``sealed``
    on a box where Home Assistant was never set up — and reporting a breach for
    something nothing calls buries the ones that matter. Anything without an
    enable flag (Ollama, Kiwix, the dashboard hand-off) is always checked,
    because "configured" is the only signal those have.
    """
    out: list[tuple[str, str]] = []
    for svc in SERVICES:
        if svc.enable_section:
            block = getattr(cfg, svc.enable_section, None)
            if block is not None and not getattr(block, "enabled", False):
                continue
        section, _, field = svc.config_path.partition(".")
        block = getattr(cfg, section, None)
        url = getattr(block, field, "") if block is not None else ""
        out.append((svc.id, str(url or "")))
    return out


def check_config(cfg: Any, posture: str, *, resolve: bool = False,
                 resolver=None, timeout: float = RESOLVE_TIMEOUT,
                 bridge_level: Optional[str] = None,
                 ) -> list[PostureViolation]:
    """Every LAN-service violation on this config object, in table order."""
    level = _bridge_level(bridge_level, getattr(cfg, "CONFIG_DIR", "") or "")
    found = []
    for service_id, url in _urls_from_config(cfg):
        violation = check_url(service_id, url, posture, resolve=resolve,
                              resolver=resolver, timeout=timeout,
                              bridge_level=level)
        if violation is not None:
            found.append(violation)
    return found


# ── enforcement at config load ───────────────────────────────────────────────

def enforce(cfg: Any, posture: str) -> list[PostureViolation]:
    """Neutralise every violating service on *cfg* in place. Never raises.

    Called from the config loader, where raising would make a bad string in a
    config file enough to stop the appliance booting. Neutralising is the
    fail-closed action that still leaves a working box:

    * a service with an ``enabled`` flag is forced ``enabled = False``, so
      nothing calls it;
    * a service without one is reset to its safe default (loopback for Ollama,
      empty for Kiwix and the dashboard hand-off), so the address is gone
      rather than merely unused.

    Returns the violations, which the loader stores for ``/system/status``. The
    name-form check is used here and never the resolving one: config load must
    not block on a resolver.
    """
    violations = check_config(cfg, posture, resolve=False)
    for violation in violations:
        svc = _BY_ID.get(violation.service)
        if svc is None:
            continue
        if svc.enable_section:
            block = getattr(cfg, svc.enable_section, None)
            if block is not None and getattr(block, "enabled", False):
                block.enabled = False
                violation.detail = (violation.detail + "; "
                                    if violation.detail else "")
                violation.detail += f"{svc.enable_section}.enabled forced False"
        if svc.safe_default is not None:
            section, _, field = svc.config_path.partition(".")
            block = getattr(cfg, section, None)
            if block is not None:
                setattr(block, field, svc.safe_default)
                violation.detail = (violation.detail + "; "
                                    if violation.detail else "")
                violation.detail += (f"{svc.config_path} reset to "
                                     f"{svc.safe_default!r}")
        logger.warning("LAN posture (%s): %s", posture, violation.user_message)
    return violations


# ── the wizard write path ────────────────────────────────────────────────────

def require_allowed(service_id: str, url: str, *, posture: Optional[str] = None,
                    resolve: bool = True, resolver=None,
                    timeout: float = RESOLVE_TIMEOUT) -> None:
    """Raise :class:`PostureViolation` if *url* may not be written. Else return.

    This is the surface that refuses outright, because refusing a write is
    free: nothing is running against the value yet, and the person is right
    there to read the message. Resolves by default — the wizard can afford two
    seconds, and it is the only place a single-label name gets checked before
    it lands in a file.

    The posture comes from :func:`write_path_posture`, which prefers the answer
    THIS run gave over anything on disk and falls back to ``sealed`` rather than
    to the ordinary default. On a first run there is no manifest yet, and
    reading "no manifest" as "island" is what let
    ``--run-egress sealed --ollama-url http://192.168.1.50:11434`` be accepted
    on a box whose owner had just asked for sealed.
    """
    violation = check_url(service_id, url,
                          posture if posture is not None else write_path_posture(),
                          resolve=resolve, resolver=resolver, timeout=timeout)
    if violation is not None:
        raise violation


# ── /system/status ───────────────────────────────────────────────────────────

def posture_check(cfg: Any = None, *, resolve: bool = True, resolver=None,
                  timeout: float = RESOLVE_TIMEOUT) -> dict[str, Any]:
    """The ``posture_check`` block for ``/system/status``.

    ``lan_services`` is the string ``"ok"`` when every configured URL is where
    the posture says it may be, and otherwise the list of violations. Resolving
    here rather than trusting the name-form check is the point: this is where a
    single-label name that a search domain sends off-site becomes visible.
    """
    if cfg is None:
        try:
            from puffpai.config import get_config
            cfg = get_config()
        except Exception as exc:
            logger.debug("config unavailable for posture check: %s", exc)
            return {"posture": "unknown", "lan_services": "unknown"}
    # Derive the posture from the config object we were handed, not from the
    # global singleton: the two can be different objects, and reporting one
    # config's URLs against another config's posture is worse than useless.
    config_dir = getattr(cfg, "CONFIG_DIR", "")
    posture = (posture_from_config_dir(config_dir) if config_dir
               else current_posture())
    live = check_config(cfg, posture, resolve=resolve, resolver=resolver,
                        timeout=timeout)
    neutralised = load_time_violations()

    # ``lan_services`` reports the SITUATION, not the tidied-up object in
    # memory. Neutralising at load fixes the running process; it does not touch
    # the config file, so the bad URL is still there and will be read again at
    # every restart. Reporting "ok" because the in-memory copy is clean is the
    # same category of lie as the egress log that said enforced:false — the
    # value is technically about something, and nobody reads it that way.
    findings = [dict(v.as_dict(), state="live") for v in live]
    seen = {(v["service"], v["url"]) for v in findings}
    for v in neutralised:
        entry = v.as_dict()
        if (entry["service"], entry["url"]) not in seen:
            findings.append(dict(entry, state="neutralised_at_load"))

    block: dict[str, Any] = {
        "posture": posture,
        "lan_services": "ok" if not findings else findings,
        "live_violations": [v.as_dict() for v in live],
        "neutralised": [v.as_dict() for v in neutralised],
        "checked": [s.config_path for s in SERVICES],
        "note": ("Under 'sealed' only this machine is allowed; under 'island' "
                 "and 'enumerated', your home network and nothing beyond it. "
                 "'lan_services' is \"ok\" only when nothing is violating AND "
                 "nothing had to be neutralised at startup: a neutralised entry "
                 "means the running process is safe but the address is still in "
                 "your config file and will be read again on the next restart. "
                 "'live_violations' is what is wrong right now; 'neutralised' is "
                 "what startup switched off or reset."),
    }
    if neutralised:
        # Kept for anything already reading the older key.
        block["neutralised_at_load"] = [v.as_dict() for v in neutralised]
    return block


# ── what the loader found, for anything that wants to report it ──────────────

_load_violations: list[PostureViolation] = []


def record_load_violations(violations: list[PostureViolation]) -> None:
    """Store what the config loader neutralised, for ``/system/status``."""
    global _load_violations
    _load_violations = list(violations)


def load_time_violations() -> list[PostureViolation]:
    """What the last config load neutralised."""
    return list(_load_violations)


__all__ = [
    "LOOPBACK", "PRIVATE", "LAN_PRESUMED", "PUBLIC", "UNRESOLVABLE",
    "UNPARSEABLE", "RESOLVE_TIMEOUT",
    "LanService", "PostureViolation", "SERVICES",
    "allowed_classifications", "check_config", "check_url", "classify_host",
    "classify_host_resolving", "current_posture", "enforce", "host_of",
    "load_time_violations", "posture_check", "posture_from_config_dir",
    "record_load_violations", "require_allowed", "service", "service_ids",
]
