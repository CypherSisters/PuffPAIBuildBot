"""A call marked "home network" really stays in the home network, at run time.

The single-exit rule lets exactly two kinds of connection leave BuildBot's
code: one through a listed exit (egress_doors, pinned_net, source_fetch,
netcheck), which checks, pins and logs it, or one on a line marked
``# lan-egress: <why>`` -- Ollama, Home Assistant, n8n, Jellyfin and the other
services the owner runs on this computer or at home. Taken on trust, the
marker would let a marked call dial whatever address the setting holds, the
open internet included.

This module checks the promise the marker makes. :func:`install` adds a Python
audit hook on ``socket.connect``. The hook sees the address actually being
dialled -- after the name was looked up, for ``requests``, ``urllib`` and
``socket.create_connection`` -- so there is no gap between a check and the
connection. When the connection comes from a marked line in BuildBot's code
and the address is on the internet (not this computer, not a private or
link-local home address), the connection is refused before a packet is sent,
logged in the app log and in the outside-traffic log, and the caller gets an
ordinary connection error (:class:`LanEgressRefused`, an ``OSError``), which
every marked call site already handles as "that service did not answer".

An IPv6 address that carries an IPv4 address (NAT64 ``64:ff9b::/96``, 6to4
``2002::/16``, Teredo ``2001::/32``, IPv4-compatible ``::a.b.c.d``) is judged
by the IPv4 address inside it: ``64:ff9b::808:808`` is
8.8.8.8 and is refused. The local-use NAT64 range ``64:ff9b:1::/48`` is read
in every layout it may use (/48, /56, /64 and /96) and refused if any of them
is an internet address: ``64:ff9b:1::808:808`` is 8.8.8.8 in the /96 one.

Proxies
-------
A proxy (the ``HTTP_PROXY``/``HTTPS_PROXY``/``ALL_PROXY`` settings, or the
Windows system proxy, which ``requests`` and ``urllib`` follow by default) is
usually on this computer or the home network, so a marked call sent through
one would pass the address check -- and the proxy would carry it on to the
internet. So:

* marked calls never use a proxy: sessions are made with
  ``trust_env = False`` (:func:`session`), single ``requests`` calls pass
  ``proxies=lan_guard.no_proxies()``, and ``urllib`` openers are built with an
  empty ``ProxyHandler`` (:func:`opener`);
* as a backstop, a marked call that dials a configured proxy's address and
  port is refused (and logged, code ``through_proxy``) whatever it was going
  to reach behind it: the check cannot see past the proxy, so it does not
  guess.

asyncio
-------
Python raises the ``socket.connect`` event from ``socket.socket.connect``
only. asyncio's selector loop calls that; its Windows default, the Proactor
loop, does not (it dials with the operating system's overlapped connect).
:func:`install` wraps the Proactor loop's connect so it raises the same event
first; see :func:`_guard_proactor`.

What it does not do
-------------------
* Code that dials without Python's ``socket`` module or asyncio's loops (a
  compiled library with its own network code, ``ctypes`` calls into the
  operating system) raises no ``socket.connect`` event and is not seen.
* Unmarked connections are not judged here. The listed exits police their own
  traffic, and ``tests/conftest.py`` fails the test run on any unmarked
  connection that leaves this computer.
* The marker is found on the CALLING THREAD's own call stack. A marked line
  that hands the connection to another thread (a thread pool, a background
  worker) is not seen from that thread, so that connection is not judged.
  BuildBot's marked calls all connect on the thread that makes them; a new
  marked call must too.
* A raw ``socket.connect`` given a NAME (not an address) is judged by the
  name's shape (:func:`puffpai.security.lan_posture.classify_host`), because
  the lookup happens inside the C call. BuildBot's marked calls all dial
  through ``requests``/``urllib``/``create_connection``, which pass addresses.
* A marker is found by reading the source line (and the three above it, for
  a call spread over several lines). Without the ``.py`` source (a
  bytecode-only install) no marker is seen and nothing is judged.
* A proxy named by a host name is matched by what that name looks up to
  (remembered for a minute); a proxy set up some other way (a PAC script, a
  program that intercepts traffic) is not known here.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import ipaddress
import linecache
import os
import sys
import threading
import time
from typing import Any, Optional
from urllib.parse import urlsplit

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

#: Split so this line is not itself read as a marker by the single-exit scan.
MARKER = "# lan-" "egress:"
#: How many lines above the calling line may carry the marker.
MARKER_WINDOW = 3
#: The outside-traffic log's service id for a refused home-network call.
LEDGER_SERVICE = "home_network"
LEDGER_CODE = "not_home_network"
#: The outside-traffic log's code for a marked call that dialled a proxy.
PROXY_CODE = "through_proxy"
#: How long a proxy host name's addresses are remembered.
PROXY_NAME_TTL = 60.0

_SRC = os.path.normcase(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SELF = os.path.normcase(os.path.abspath(__file__))

_installed = False
_install_lock = threading.Lock()
_busy = threading.local()


class LanEgressRefused(ConnectionRefusedError):
    """A call marked "home network" tried to reach the internet. Refused."""


def classify(address: Any) -> str:
    """``loopback`` / ``private`` / ``lan_presumed`` / ``public`` / ``''``.

    ``''`` means "not a network address" (a Unix socket path and the like).
    """
    from puffpai.security import lan_posture
    host = address[0] if isinstance(address, tuple) and address else address
    if isinstance(host, (bytes, bytearray)):
        try:
            host = bytes(host).decode("ascii")
        except UnicodeDecodeError:
            return lan_posture.PUBLIC
    if not isinstance(host, str):
        return ""
    host = host.strip().strip("[]")
    if not host:
        return lan_posture.LOOPBACK  # INADDR_ANY: this computer
    return (lan_posture._classify_address(host)  # noqa: SLF001 - same family
            or lan_posture.classify_host(host))


def is_home(address: Any) -> bool:
    """True for this computer, a private/link-local address, or a house name."""
    from puffpai.security import lan_posture
    return classify(address) != lan_posture.PUBLIC


# ── proxies: marked calls never use one ─────────────────────────────────────

def no_proxies() -> dict:
    """A FRESH ``proxies=`` value for one ``requests`` call: no proxy at all.

    ``None`` for every scheme stops the environment's and the system's proxy
    being filled in; ``no_proxy: "*"`` keeps them out after a redirect too
    (requests re-reads them there). Fresh each time because requests adds to
    the dict it is given.
    """
    return {"http": None, "https": None, "all": None, "no_proxy": "*"}


def session():
    """A requests session that never uses a proxy (``trust_env = False``).

    ``trust_env = False`` also stops the session reading ``.netrc`` sign-ins
    and the ``REQUESTS_CA_BUNDLE`` setting; a home service with its own
    certificate needs ``verify=`` set on the session instead.
    """
    import requests  # noqa: PLC0415 - only the marked callers need it
    made = requests.Session()  # lan-egress: builds (does not dial) the proxy-free session marked callers use; their own line is judged
    made.trust_env = False
    made.proxies = {}
    return made


def opener(*handlers):
    """A ``urllib`` opener that never uses a proxy (an empty ProxyHandler)."""
    import urllib.request  # noqa: PLC0415
    return urllib.request.build_opener(  # lan-egress: builds (does not dial) the proxy-free opener marked callers use; their own line is judged
        urllib.request.ProxyHandler({}), *handlers)


_proxy_names: dict[str, tuple[float, frozenset]] = {}
_proxy_names_lock = threading.Lock()


def _ip_text(host: Any) -> Optional[str]:
    try:
        addr = ipaddress.ip_address(str(host).strip().strip("[]").split("%", 1)[0])
    except ValueError:
        return None
    mapped = getattr(addr, "ipv4_mapped", None)
    return str(mapped or addr)


def _addresses_of(host: str) -> frozenset:
    literal = _ip_text(host)
    if literal:
        return frozenset({literal})
    now = time.monotonic()
    with _proxy_names_lock:
        cached = _proxy_names.get(host)
        if cached and now - cached[0] < PROXY_NAME_TTL:
            return cached[1]
    import socket  # noqa: PLC0415
    try:
        found = frozenset(filter(None, (_ip_text(info[4][0]) for info in
                                        socket.getaddrinfo(host, None))))
    except (OSError, UnicodeError):
        found = frozenset()
    with _proxy_names_lock:
        _proxy_names[host] = (now, found)
    return found


_DEFAULT_PROXY_PORTS = {"http": 80, "https": 443, "socks": 1080,
                        "socks4": 1080, "socks5": 1080, "socks5h": 1080}


def proxy_endpoints() -> frozenset:
    """``(address, port)`` of every proxy ``requests``/``urllib`` would use.

    Read fresh each time: the environment's proxy settings and, on Windows,
    the system proxy (``urllib.request.getproxies``).
    """
    try:
        import urllib.request  # noqa: PLC0415
        found = urllib.request.getproxies()
    except Exception:  # a broken registry entry is "no proxy we can see"
        return frozenset()
    out = set()
    for scheme, value in found.items():
        if scheme == "no" or not isinstance(value, str) or not value.strip():
            continue
        text = value.strip()
        if "://" not in text:
            text = "http://" + text
        try:
            parts = urlsplit(text)
            host, port = parts.hostname, parts.port
        except ValueError:
            continue
        if not host:
            continue
        if port is None:
            port = _DEFAULT_PROXY_PORTS.get(parts.scheme.lower(), 80)
        for addr in _addresses_of(host):
            out.add((addr, port))
    return frozenset(out)


def is_proxy(address: Any) -> bool:
    """True when *address* (``(host, port, ...)``) is a configured proxy."""
    if not (isinstance(address, tuple) and len(address) >= 2):
        return False
    host = _ip_text(address[0]) or str(address[0]).strip().lower()
    try:
        port = int(address[1])
    except (TypeError, ValueError):
        return False
    endpoints = proxy_endpoints()
    return bool(endpoints) and (host, port) in endpoints


def marked_site(frame) -> Optional[tuple[str, int]]:
    """``(file, line)`` of the first BuildBot frame, when that line is marked.

    Walks outward from *frame*. The first frame inside ``src/puffpai`` (other
    than this module) decides: marked -> its site, unmarked -> None.
    """
    while frame is not None:
        name = os.path.normcase(os.path.abspath(frame.f_code.co_filename))
        if name.startswith(_SRC) and name != _SELF:
            number = frame.f_lineno
            for n in range(max(1, number - MARKER_WINDOW), number + 1):
                if MARKER in linecache.getline(frame.f_code.co_filename, n):
                    return os.path.relpath(name, _SRC), number
            return None
        frame = frame.f_back
    return None


def _shown_host(host: Any) -> str:
    """*host* as a log line may show it: an IP address as it is, a host name
    only when it is plain and does not look like a key (a host name can be
    anything the caller put in an address). Fail closed."""
    try:
        return str(ipaddress.ip_address(str(host).strip("[]").split("%", 1)[0]))
    except ValueError:
        pass
    try:
        from puffpai import web_search
        return web_search.log_name(host, placeholder="an address (not shown)",
                                   limit=253)
    except Exception:  # never the raw name instead
        return "an address (not shown)"


def _record_refusal(address: Any, where: str, line: int, *,
                    code: str = LEDGER_CODE) -> None:
    raw = address[0] if isinstance(address, tuple) and address else address
    host = _shown_host(raw)
    if code == PROXY_CODE:
        logger.warning(
            "Refused a home-network call from %s:%d to %s: that is a proxy, "
            "which could pass it on to the internet. Home-network calls do "
            "not use a proxy.", where, line, host)
    else:
        logger.warning(
            "Refused a home-network call from %s:%d to %s: that address is on "
            "the internet, not this computer or the home network. Check the "
            "address set for that service.", where, line, host)
    try:
        from puffpai.security import egress_ledger
        egress_ledger.record(LEDGER_SERVICE,
                             host=host if host != "an address (not shown)" else "",
                             outcome="refused",
                             code=code)
    except Exception:  # the log must never turn a refusal into a crash
        pass


def check(address: Any, frame=None) -> None:
    """Refuse *address* when a marked BuildBot line is dialling the internet,
    or dialling a proxy (which could carry it to the internet)."""
    if is_home(address):
        if not is_proxy(address):
            return
        site = marked_site(frame if frame is not None else sys._getframe(1))
        if site is None:
            return
        _record_refusal(address, *site, code=PROXY_CODE)
        raise LanEgressRefused(
            f"{site[0]}:{site[1]} is marked as a home-network call but was "
            f"going through a proxy; it was stopped")
    site = marked_site(frame if frame is not None else sys._getframe(1))
    if site is None:
        return
    _record_refusal(address, *site)
    raise LanEgressRefused(
        f"{site[0]}:{site[1]} is marked as a home-network call but was going "
        f"to an internet address; it was stopped")


#: The audit events judged: a connection, and a datagram sent to an address
#: without connecting first (UDP ``sendto`` / ``sendmsg``; without them those
#: pass the Bridge unseen).
_EVENTS = frozenset({"socket.connect", "socket.sendto", "socket.sendmsg"})


def _hook(event: str, args: tuple) -> None:
    if event not in _EVENTS:
        return
    if event != "socket.connect" and not (len(args) > 1 and isinstance(args[1], tuple)):
        return  # sendmsg on a connected socket: judged when it connected
    if getattr(_busy, "on", False):
        return  # the refusal's own logging never re-enters the check
    _busy.on = True
    try:
        frame = sys._getframe(1)
        check(args[1] if len(args) > 1 else None, frame)
        # The Bridge: with it up, only what the owner switched on
        # may leave this computer, whoever opens the connection.
        from puffpai.security import bridge_guard
        bridge_guard.check(args[0] if args else None,
                           args[1] if len(args) > 1 else None, frame)
    finally:
        _busy.on = False


# ── asyncio on Windows: the Proactor loop dials without socket.connect ──────
#
# The default asyncio event loop on Windows (ProactorEventLoop) opens a TCP
# connection with the overlapped ``ConnectEx`` call and a UDP one with
# ``WSAConnect`` (``asyncio.windows_events.IocpProactor.connect``). Neither
# goes through ``socket.socket.connect``, so Python never raises the
# ``socket.connect`` audit event for them and the hook above never sees them
# (so an asyncio connection would reach a home address with the Bridge
# up). :func:`install` therefore wraps that one method
# so it raises the same audit event, with the same socket and address, BEFORE
# the connection is started. Every hook then judges it exactly as it judges a
# plain ``socket.connect``, and a refusal stops it before a packet is sent.
# The selector loop (and every other platform) calls ``sock.connect`` itself,
# which already raises the event.

#: The real ``IocpProactor.connect``, as found at install time.
_proactor_real_connect: Any = None


def _proactor_connect(self, conn, address):
    sys.audit("socket.connect", conn, address)
    return _proactor_real_connect(self, conn, address)


_proactor_connect._puffpai_guarded = True  # type: ignore[attr-defined]

#: The real ``IocpProactor.sendto``: a UDP datagram to an address, which
#: (like connect) never goes through ``socket.socket.sendto``.
_proactor_real_sendto: Any = None


def _proactor_sendto(self, conn, buf, flags=0, addr=None):
    if addr is not None:
        sys.audit("socket.sendto", conn, addr)
    return _proactor_real_sendto(self, conn, buf, flags, addr)


_proactor_sendto._puffpai_guarded = True  # type: ignore[attr-defined]


def _guard_proactor() -> bool:
    """Make the Windows Proactor loop's connect raise ``socket.connect``.

    True when the wrapper is in place (now or already). False where there is
    no Proactor loop (not Windows) or its connect cannot be found, which is
    said in the log: then only the selector loop's connections are seen.
    """
    global _proactor_real_connect, _proactor_real_sendto
    if sys.platform != "win32":
        return False
    try:
        from asyncio import windows_events  # noqa: PLC0415 - Windows only
    except Exception:  # pragma: no cover - a Python without it
        logger.warning("asyncio's Windows loop could not be loaded; its "
                       "connections are not checked here")
        return False
    proactor = getattr(windows_events, "IocpProactor", None)
    current = getattr(proactor, "connect", None)
    if current is None:  # pragma: no cover - a changed standard library
        logger.warning("asyncio's Windows loop has no connect to check; its "
                       "connections are not checked here")
        return False
    sendto = getattr(proactor, "sendto", None)
    if sendto is not None and not getattr(sendto, "_puffpai_guarded", False):
        _proactor_real_sendto = sendto
        proactor.sendto = _proactor_sendto
    if getattr(current, "_puffpai_guarded", False):
        return True
    _proactor_real_connect = current
    proactor.connect = _proactor_connect
    return True


def proactor_guarded() -> bool:
    """True when the Windows Proactor loop's connections reach the hook."""
    if sys.platform != "win32":
        return False
    try:
        from asyncio import windows_events  # noqa: PLC0415
    except Exception:  # pragma: no cover
        return False
    current = getattr(getattr(windows_events, "IocpProactor", None), "connect", None)
    return bool(getattr(current, "_puffpai_guarded", False))


def install() -> bool:
    """Add the audit hook once per process. True when this call added it.

    Audit hooks cannot be removed; that is the point: nothing later in the
    process can switch this check off. On Windows it also makes asyncio's
    Proactor loop raise the ``socket.connect`` event (:func:`_guard_proactor`),
    which it otherwise never does.
    """
    global _installed
    with _install_lock:
        _guard_proactor()
        # A proxy named in a call (not in the settings) is refused with the
        # Bridge up where the HTTP libraries take it on (bridge_guard).
        from puffpai.security import bridge_guard
        bridge_guard.guard_proxy_entry_points()
        if _installed:
            return False
        sys.addaudithook(_hook)
        _installed = True
    return True


def installed() -> bool:
    return _installed


__all__ = ["LEDGER_CODE", "LEDGER_SERVICE", "LanEgressRefused", "MARKER",
           "PROXY_CODE", "check", "classify", "install", "installed",
           "is_home", "is_proxy", "marked_site", "no_proxies", "opener",
           "proactor_guarded", "proxy_endpoints", "session"]
