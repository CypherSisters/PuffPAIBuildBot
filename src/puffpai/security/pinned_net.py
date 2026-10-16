"""Pinned outbound connections: look a name up once, then dial THAT address.

Why this exists
---------------
Every outside exit in BuildBot checked where a name pointed *before*
connecting, and then handed the name to an HTTP library that looked it up
**again** to connect. A name that answers "public" to the first lookup and
"your router" or "the cloud metadata address" to the second (DNS rebinding)
walked straight through. ``egress_doors._verify_peer`` was meant to catch that
after the fact, but with urllib3 2.6 the connected socket is never visible to
it, so it never ran.

This module closes the window by construction instead of after the fact:

1. the name is resolved **once**, here;
2. EVERY answer must be a public address (loopback, private, link-local and
   the metadata range, carrier-grade NAT, multicast, reserved, unspecified,
   and IPv6 forms that smuggle one of those inside are all refused);
3. the socket is opened to **that address** -- the name is never handed to a
   resolver again -- while TLS still uses the NAME for SNI and for checking
   the certificate, and the HTTP ``Host`` header still carries the name;
4. after connecting, the socket's real peer is compared with the address we
   dialled, and a mismatch is refused.

Two transports use it:

* :func:`requests_session` -- a ``requests.Session`` whose HTTPS connections are
  pinned (``egress_doors`` uses it for every door, live card, picture, town
  lookup and sample look). Proxies from the environment are ignored on purpose:
  through a proxy the proxy resolves the name, and every check here would be
  decorative.
* :func:`urllib_opener` -- a ``urllib`` opener with a pinned HTTPS handler, no
  proxy and no redirect following (``source_fetch`` and ``catalogs.live`` use it
  for books, library searches and list refreshes).

A refusal raises :class:`PinRefused`, an ``OSError`` so that code which already
treats "could not connect" as a failure keeps working; the :class:`Pin` also
remembers the refusal so a caller can report it as a refusal rather than as a
network fault.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import functools
import http.client
import ipaddress
import socket
import ssl
import threading
import urllib.request
from typing import Any, Callable, Optional, Sequence

#: A resolver takes ``(host, port)`` and returns address strings.
Resolver = Callable[[str, int], Sequence[str]]

_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


class PinRefused(OSError):
    """A connection was refused before (or instead of) reaching the network.

    ``reason`` is for logs; ``code`` is one word from a closed list
    (``inside_address``, ``network_filter``, ``cannot_look_up``,
    ``peer_moved``, ``not_encrypted``) and never carries an address or a name.
    """

    def __init__(self, reason: str, code: str = "inside_address"):
        super().__init__(reason)
        self.reason = reason
        self.code = code


def address_refusal(text: Any) -> str:
    """Why this address may not be dialled, or '' when it is public.

    One policy for every exit. Forms that carry an IPv4 address inside an IPv6
    one (mapped, 6to4, Teredo, NAT64) are unwrapped and judged on the inner
    address, because ``::ffff:127.0.0.1`` is loopback wearing a hat.
    """
    try:
        addr = ipaddress.ip_address(str(text).split("%", 1)[0].strip())
    except ValueError:
        return "is not an address"
    if isinstance(addr, ipaddress.IPv6Address):
        inner = addr.ipv4_mapped or addr.sixtofour
        if inner is None and addr.teredo is not None:
            inner = addr.teredo[1]
        if inner is None and addr in _NAT64:
            inner = ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
        if inner is not None:
            why = address_refusal(str(inner))
            if why:
                return why
    for attribute, words in (
            ("is_loopback", "is this computer"),
            ("is_link_local", "is link-local (the cloud-metadata range)"),
            ("is_multicast", "is multicast"),
            ("is_unspecified", "is unspecified"),
            ("is_private", "is a private address on this network"),
            ("is_reserved", "is reserved")):
        if getattr(addr, attribute, False):
            return words
    if isinstance(addr, ipaddress.IPv4Address) and addr in _CGNAT:
        return "is a shared carrier address (not public)"
    if not addr.is_global:
        return "is not a public address"
    return ""


#: How long the computer's own DNS server addresses are remembered.
RESOLVER_TTL = 60.0
_resolvers_lock = threading.Lock()
_resolvers_cache: dict[str, Any] = {"at": None, "value": frozenset()}


def _addr(text: Any) -> Optional[ipaddress._BaseAddress]:
    try:
        addr = ipaddress.ip_address(str(text).split("%", 1)[0].strip().strip("[]"))
    except ValueError:
        return None
    mapped = getattr(addr, "ipv4_mapped", None)
    return mapped if mapped is not None else addr


def _resolver_lines_windows() -> list[str]:
    import winreg  # noqa: PLC0415 - Windows only
    found: list[str] = []
    for root in (r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters",
                 r"SYSTEM\CurrentControlSet\Services\Tcpip6\Parameters"):
        keys = [root]
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                root + "\\Interfaces") as ifaces:
                i = 0
                while True:
                    try:
                        keys.append(root + "\\Interfaces\\" + winreg.EnumKey(ifaces, i))
                    except OSError:
                        break
                    i += 1
        except OSError:
            pass
        for path in keys:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
                    for value in ("NameServer", "DhcpNameServer"):
                        try:
                            text, _kind = winreg.QueryValueEx(key, value)
                        except OSError:
                            continue
                        if isinstance(text, str):
                            found.append(text)
            except OSError:
                continue
    return found


def _resolver_lines_posix() -> list[str]:
    found: list[str] = []
    for path in ("/etc/resolv.conf", "/run/systemd/resolve/resolv.conf"):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    parts = line.split()
                    if len(parts) >= 2 and parts[0] == "nameserver":
                        found.append(parts[1])
        except OSError:
            continue
    return found


def resolver_addresses() -> frozenset:
    """The addresses of the DNS servers this computer asks. Never raises.

    Read from the Windows network settings (every adapter's set and
    automatic DNS servers) or ``/etc/resolv.conf`` (and systemd-resolved's
    upstream list), remembered for :data:`RESOLVER_TTL` seconds. Anything
    that cannot be read is left out: this only picks words, never whether a
    connection goes ahead.
    """
    import time  # noqa: PLC0415
    now = time.monotonic()
    with _resolvers_lock:
        at = _resolvers_cache["at"]
        if at is not None and now - at < RESOLVER_TTL:
            return _resolvers_cache["value"]
    try:
        import sys  # noqa: PLC0415
        lines = (_resolver_lines_windows() if sys.platform == "win32"
                 else _resolver_lines_posix())
    except Exception:  # the settings could not be read: know of none
        lines = []
    out = set()
    for line in lines:
        for word in line.replace(",", " ").replace(";", " ").split():
            addr = _addr(word)
            if addr is not None:
                out.add(str(addr))
    value = frozenset(out)
    with _resolvers_lock:
        _resolvers_cache.update({"at": now, "value": value})
    return value


def forget_resolvers() -> None:
    """Drop the remembered DNS server addresses (tests, a network change)."""
    with _resolvers_lock:
        _resolvers_cache.update({"at": None, "value": frozenset()})


def _local_name(host: Any) -> bool:
    """True for a name that is meant to be this computer or a home device."""
    name = str(host or "").strip().rstrip(".").lower()
    if not name:
        return False
    if name == "localhost" or name.endswith(".localhost"):
        return True
    try:
        from puffpai.security import lan_posture  # noqa: PLC0415
        return lan_posture.classify_host(name) != lan_posture.PUBLIC
    except Exception:  # cannot tell: not a filter's answer, keep the old words
        return True


def filter_block(answers: Any, host: Any = None) -> bool:
    """True when a name's answers are what a home DNS filter gives for a block.

    The connection is refused either way; this only picks the words: a
    DNS filter's block reads
    "blocked by your network's filter", never "pointed inside your home
    network". Filters answer a blocked name in one of three ways, and all
    three are read as a filter's block here:

    * ``0.0.0.0`` / ``::`` (Pi-hole's and AdGuard Home's default);
    * this computer's own address, ``127.0.0.1`` / ``::1`` (hosts-file
      blockers, a filter set to answer that way);
    * the address of the DNS server itself (Pi-hole's "IP" mode, a router's
      block page): :func:`resolver_addresses`.

    EVERY answer must be one of those. A name meant to be local (``localhost``,
    ``nas.local``, a one-word house name: *host*) is never a filter's block.
    A name on the internet that really points at this computer (the DNS
    rebinding trick) is also read as a filter's block: it is stopped all the
    same, and the log keeps the name. A filter set to answer some OTHER home
    address (not itself, not this computer) cannot be told apart from a name
    that points at a device at home, so that keeps the inside-address words.
    """
    if host is not None and _local_name(host):
        return False
    try:
        items = [str(a) for a in answers or ()]
    except TypeError:
        return False
    if not items:
        return False
    resolvers: Optional[frozenset] = None
    for text in items:
        addr = _addr(text)
        if addr is None:
            return False
        if addr.is_unspecified or addr.is_loopback:
            continue
        if not address_refusal(str(addr)):
            return False  # a public answer is never a filter's block
        if resolvers is None:
            resolvers = resolver_addresses()
        if str(addr) not in resolvers:
            return False
    return True


def _system_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _norm_host(host: Any) -> str:
    return str(host or "").strip().rstrip(".").lower()


def resolve_public(host: str, port: int = 443,
                   resolver: Optional[Resolver] = None) -> list[str]:
    """Resolve *host* once; return its answers only if EVERY one is public.

    Raises :class:`PinRefused` otherwise. A name with one public and one
    private answer is refused: the stack, not us, would pick which one gets
    the socket.
    """
    name = _norm_host(host)
    if not name:
        raise PinRefused("no host to look up", "cannot_look_up")
    try:
        literal = ipaddress.ip_address(name.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        answers = [str(literal)]
    else:
        try:
            answers = [str(a) for a in (resolver or _system_resolver)(name, port)]
        except OSError as exc:
            raise PinRefused(f"cannot look up {name} ({type(exc).__name__})",
                             "cannot_look_up") from None
    if not answers:
        raise PinRefused(f"{name} looked up to nothing", "cannot_look_up")
    if literal is None and filter_block(answers, name):
        raise PinRefused(f"{name} was blocked by the network's filter "
                         f"(it answered {', '.join(answers[:2])})",
                         "network_filter")
    for answer in answers:
        why = address_refusal(answer)
        if why:
            raise PinRefused(f"{name} points at {answer}, which {why}",
                             "inside_address")
    return answers


class Pin:
    """The addresses one request may dial, looked up at most once per name.

    ``preset`` holds answers a caller already vetted (so a pre-flight check
    and the connection agree on the SAME answer). A name not in ``preset`` is
    resolved on first connect through ``resolver`` and vetted then. Either
    way, the dialled address is fixed for the life of this object.
    """

    def __init__(self, preset: Optional[dict[str, Sequence[str]]] = None,
                 resolver: Optional[Resolver] = None):
        self._lock = threading.Lock()
        self._resolver = resolver
        self._pinned: dict[str, str] = {}
        for host, answers in (preset or {}).items():
            answers = [str(a) for a in answers or ()]
            if not answers:
                continue
            for answer in answers:
                why = address_refusal(answer)
                if why:
                    raise PinRefused(f"{host} points at {answer}, which {why}",
                                     "inside_address")
            self._pinned[_norm_host(host)] = answers[0]
        #: The first refusal this pin raised, or None.
        self.refused: Optional[PinRefused] = None
        #: ``[(host, address)]`` for every socket actually opened.
        self.dialled: list[tuple[str, str]] = []

    def address_for(self, host: str, port: int) -> str:
        """The address to dial for *host*. Resolves and vets on first use."""
        name = _norm_host(host)
        with self._lock:
            known = self._pinned.get(name)
        if known is not None:
            return known
        try:
            answers = resolve_public(name, port, self._resolver)
        except PinRefused as exc:
            self.refused = self.refused or exc
            raise
        with self._lock:
            return self._pinned.setdefault(name, answers[0])

    def check_peer(self, host: str, sock: Any, dialled: str) -> None:
        """Refuse a socket whose real peer is not the address we dialled."""
        try:
            peer = str(sock.getpeername()[0])
        except (OSError, AttributeError, IndexError, TypeError):
            peer = ""
        same = False
        if peer:
            try:
                same = (ipaddress.ip_address(peer.split("%", 1)[0])
                        == ipaddress.ip_address(dialled.split("%", 1)[0]))
            except ValueError:
                same = False
            if not same:
                mapped = getattr(ipaddress.ip_address(peer.split("%", 1)[0]),
                                 "ipv4_mapped", None) if ":" in peer else None
                same = mapped is not None and str(mapped) == dialled
        if not same:
            err = PinRefused(f"connection to {host} landed on "
                             f"{peer or 'an unknown address'}, not {dialled}",
                             "peer_moved")
            self.refused = self.refused or err
            try:
                sock.close()
            except OSError:
                pass
            raise err
        with self._lock:
            self.dialled.append((_norm_host(host), dialled))

    def open_socket(self, host: str, port: int, timeout: Any,
                    source_address: Any = None,
                    create: Optional[Callable[..., Any]] = None) -> Any:
        """Open a TCP socket to the pinned address for *host*."""
        address = self.address_for(host, port)
        opener = create or socket.create_connection
        if source_address is None:
            sock = opener((address, port), timeout)
        else:
            sock = opener((address, port), timeout, source_address)
        self.check_peer(host, sock, address)
        return sock


# ── requests / urllib3 ───────────────────────────────────────────────────────

def _pinned_https_pool(pin: Pin):
    """An ``HTTPSConnectionPool`` subclass whose sockets go to the pinned IP."""
    from urllib3.connection import HTTPSConnection
    from urllib3.connectionpool import HTTPSConnectionPool
    from urllib3.exceptions import ConnectTimeoutError, NewConnectionError
    from urllib3.util import connection as u3conn

    class _PinnedHTTPSConnection(HTTPSConnection):
        def _new_conn(self):  # type: ignore[override]
            # self.host stays the NAME: TLS SNI, the certificate check and the
            # Host header all use it. Only the TCP dial uses the pinned IP.
            try:
                address = pin.address_for(self.host, self.port)
            except PinRefused as exc:
                raise NewConnectionError(self, f"refused: {exc.reason}") from exc
            try:
                sock = u3conn.create_connection(
                    (address, self.port), self.timeout,
                    source_address=self.source_address,
                    socket_options=self.socket_options)
            except socket.timeout as exc:
                raise ConnectTimeoutError(
                    self, f"Connection to {self.host} timed out.") from exc
            except OSError as exc:
                raise NewConnectionError(
                    self, f"Failed to establish a new connection: {exc}") from exc
            try:
                pin.check_peer(self.host, sock, address)
            except PinRefused as exc:
                raise NewConnectionError(self, f"refused: {exc.reason}") from exc
            return sock

    class _PinnedHTTPSPool(HTTPSConnectionPool):
        ConnectionCls = _PinnedHTTPSConnection

    return _PinnedHTTPSPool


def requests_session(pin: Pin):
    """A ``requests.Session`` whose every HTTPS connection is pinned.

    Plain http is refused outright, and proxies from the environment are
    ignored (``trust_env = False``) so no proxy can resolve the name for us.
    """
    import requests
    from requests.adapters import HTTPAdapter

    pool_cls = _pinned_https_pool(pin)

    class _PinnedAdapter(HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):  # type: ignore[override]
            super().init_poolmanager(*args, **kwargs)
            self.poolmanager.pool_classes_by_scheme = {"https": pool_cls}

        def send(self, request, *args, **kwargs):  # type: ignore[override]
            if not str(request.url or "").lower().startswith("https://"):
                err = PinRefused("only encrypted connections are made",
                                 "not_encrypted")
                pin.refused = pin.refused or err
                raise requests.exceptions.ConnectionError(err)
            if kwargs.get("proxies"):
                kwargs["proxies"] = {}
            return super().send(request, *args, **kwargs)

    session = requests.Session()
    session.trust_env = False
    session.proxies = {}
    adapter = _PinnedAdapter(max_retries=0)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# ── urllib ───────────────────────────────────────────────────────────────────

class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """``http.client`` HTTPS connection that dials the pinned address."""

    def __init__(self, host: str, *args: Any, pin: Pin, **kwargs: Any):
        super().__init__(host, *args, **kwargs)
        self._pin = pin
        # HTTPConnection.connect() opens the socket through this attribute
        # with (self.host, self.port); the name is swapped for the pinned
        # address here and nowhere else. TLS then wraps the socket with
        # server_hostname=self.host (the NAME).
        self._create_connection = self._pinned_create

    def _pinned_create(self, address: Any, timeout: Any = None,
                       source_address: Any = None) -> Any:
        host, port = address
        return self._pin.open_socket(host, port, timeout, source_address)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, pin: Pin, context: Optional[ssl.SSLContext] = None):
        super().__init__(context=context or ssl.create_default_context())
        self._pin = pin

    def https_open(self, req):  # type: ignore[override]
        return self.do_open(
            functools.partial(_PinnedHTTPSConnection, pin=self._pin), req,
            context=self._context)


class _RefusePlainHTTP(urllib.request.HTTPHandler):
    def http_open(self, req):  # type: ignore[override]
        raise PinRefused("only encrypted connections are made", "not_encrypted")


def urllib_opener(pin: Pin, *extra_handlers: Any) -> urllib.request.OpenerDirector:
    """A urllib opener: pinned HTTPS, no plain http, no proxy, plus *extra*."""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _RefusePlainHTTP(),
        _PinnedHTTPSHandler(pin), *extra_handlers)


__all__ = ["Pin", "PinRefused", "Resolver", "address_refusal",
           "requests_session", "resolve_public", "urllib_opener"]
