"""The Bridge, at the moment a connection is made.

Why this exists
---------------
The Bridge choice (:mod:`puffpai.zone_bridge`) is saved and shown, and it
must also be enforced. Most of the enforcing happens where each kind of traffic already
decides (the owner's switches, :mod:`puffpai.security.lan_posture`). This
module is the backstop underneath all of them: it sits on the same
``socket.connect`` audit hook as :mod:`puffpai.security.lan_guard`, sees the
address really being dialled, and refuses a connection the Bridge does not
allow before a packet is sent, whatever code opened it (a library's own
"phone home", a new call nobody listed, a path that skipped its switch).

What it allows
--------------
Nothing changes at level ``open`` (``fenced`` / ``open_yard``, the default
when no Bridge was chosen). That default is not an open door: every named
outside service in :mod:`puffpai.security.egress_doors` is still shut until
the run posture is ``enumerated`` AND its own switch is on (the wizard plan
or the owner), and a service switched off is refused before a socket opens.
What goes out at this level without a switch being set is BuildBot's own
finding and adding tools, "as built", each with a one-tap off
(:mod:`puffpai.zone_bridge`). This module is the backstop under those
switches, armed when the Bridge is up; it is not what keeps them closed.
``tests/test_bridge_default_keeps_doors_shut.py`` holds that line.

At level ``switched_on`` (the Bridge is up), a connection to anything other
than this computer goes ahead only when:

* a listed exit made it (:data:`EXIT_FILES`: the outside-service code that
  pins, logs and checks the owner's switch for every request; with the Bridge
  up those switches read "not switched on" as off, see
  :func:`puffpai.security.outside_switches.is_off`), or
* a line marked ``# lan-egress:`` made it, and the address and port are
  those of a home service the owner switched on (Home Assistant, n8n, the
  media server, each only while ``enabled``).

At level ``sealed`` nothing but this computer is allowed, listed exit or not.

At both levels a proxy set on this computer (``HTTP_PROXY`` / ``HTTPS_PROXY``
/ ``ALL_PROXY`` or the system proxy, see
:func:`puffpai.security.lan_guard.proxy_endpoints`) is refused too, listed
exit or not: it is on this computer, but it would carry the request on to
wherever it is asked, which this check cannot see. So is a proxy named in the call itself (``proxies=`` in
``requests``, an ``urllib`` ProxyHandler with an address, ``urllib3``'s
ProxyManager, ``http.client``'s ``set_tunnel``, a ``CONNECT`` or a full
address for another computer written through ``http.client``, ``httpx``'s
``proxy=`` and ``mounts``, ``aiohttp``'s ``proxy=``, ``websocket-client``'s
``http_proxy_host``), refused where the library takes it on
(:func:`guard_proxy_entry_points`).
BuildBot's own requests never use a proxy. A proxy reached some other way (a
PAC script, a program that intercepts traffic, another library with its own
proxy code, code that writes its own ``CONNECT`` line or SOCKS greeting down
a plain socket) is not known here.

A refusal raises :class:`BridgeRefused` (an ``OSError``, so every caller that
already handles "could not connect" keeps working), and writes one line to the
app log and one to the outside-traffic log (code ``bridge_up``), which the
owner reads on "What leaves your computer".

What it sees
------------
Every connection Python makes through ``socket.socket.connect`` (``requests``,
``urllib``, ``http.client``, ``socket.create_connection``, asyncio's selector
loop), and asyncio's Windows default loop (the Proactor loop), which dials
without ``socket.connect``: :func:`puffpai.security.lan_guard.install` makes
its connect raise the same audit event first (without that, an asyncio
connection reaches a home address with the Bridge up).

What it does not do
-------------------
* Code that dials without Python's ``socket`` module or asyncio's loops (a
  compiled library with its own network code, ``ctypes`` calls into the
  operating system) raises no audit event and is not seen here. BuildBot has
  no such code today; a new dependency that brings some is not held by this
  backstop, only by the switches in front of it.
* A UDP datagram sent without connecting (``sendto`` / ``sendmsg``, and
  asyncio's Windows loop's ``sendto``) is judged like a connection to the
  same address. A name lookup through the
  operating system (``socket.getaddrinfo``) is not a Python ``sendto`` and is
  not seen: it still asks the resolver the computer is set to use.
* Programs BuildBot starts (the locked box, the hidden browser, Ollama) are
  other processes; the hook does not run inside them. The locked box is held
  by its own switch check (``box.driver._runtime_hosts_allowed``) and its
  allowlisting proxy; the hidden browser has no outside route; Ollama's own
  model downloads are listed as "BuildBot cannot see or stop these".
* Like :mod:`lan_guard`, a listed exit or a marker is found on the CALLING
  THREAD's stack. A connection handed to another thread is judged without
  them, which can only make it stricter (it is refused).

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
import sys
import threading
import time
from typing import Any, Optional
from urllib.parse import urlsplit

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

#: The listed exits (tests/test_outside_single_exit.py EXITS, the modules that
#: run in THIS process): each checks the owner's switch, pins and logs.
EXIT_FILES = ("security/egress_doors.py", "security/pinned_net.py",
              "source_fetch.py", "wizard/netcheck.py",
              # tool servers she added: each checks its own switch and logs
              # every use; its sessions run on a thread started in this file
              "mcp_client.py")

#: The outside-traffic log's reason code and service ids for a refusal.
LEDGER_CODE = "bridge_up"
LEDGER_SERVICE_HOME = "home_network"
LEDGER_SERVICE_OTHER = "the_bridge"

#: How long a switched-on home service's looked-up addresses are remembered.
HOME_NAME_TTL = 60.0

#: Home services with a switch of their own: (config section, url field).
_SWITCHED_HOME = (("ha", "url"), ("n8n", "url"),
                  ("media_server", "jellyfin_url"),
                  ("media_server", "calibre_web_url"))

_SRC = os.path.normcase(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_EXITS = tuple(os.path.normcase(os.path.join(_SRC, *rel.split("/")))
               for rel in EXIT_FILES)

_names: dict[tuple[str, int], tuple[float, frozenset]] = {}
_names_lock = threading.Lock()


# ── the Bridge in force, read back from disk ────────────────────────────────

_MANIFEST_NAME = "build_manifest.json"
_enforced_lock = threading.Lock()
_enforced_cache: dict[str, Any] = {"key": None, "value": None}


def _manifest_file(config_dir: Optional[str]) -> str:
    if config_dir:
        return os.path.join(config_dir, _MANIFEST_NAME)
    from puffpai.config import get_config
    return os.path.join(get_config().CONFIG_DIR, _MANIFEST_NAME)


def enforced(config_dir: Optional[str] = None) -> dict[str, Any]:
    """The Bridge the running bot enforces now. Never raises; fails closed.

    :func:`puffpai.zone_bridge.enforced_from_manifest` over
    ``build_manifest.json``, read straight from disk (never through the config
    loader, which calls this) and cached until that file changes. A manifest
    that exists but cannot be read reads as ``drawbridge_up``; so does a
    config that cannot say where the manifest is.
    """
    from puffpai import zone_bridge
    try:
        path = _manifest_file(config_dir)
    except Exception:
        return zone_bridge.enforced_from_manifest("unreadable")
    try:
        stat = os.stat(path)
        key: Any = (path, stat.st_mtime_ns, stat.st_size)
    except FileNotFoundError:
        key = (path, None, None)
    except OSError:
        return zone_bridge.enforced_from_manifest("unreadable")
    with _enforced_lock:
        if _enforced_cache["key"] == key and _enforced_cache["value"] is not None:
            return dict(_enforced_cache["value"])
    if key[1] is None:
        value = zone_bridge.enforced_from_manifest(None)
    else:
        try:
            import json
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = "unreadable"
        value = zone_bridge.enforced_from_manifest(data)
    with _enforced_lock:
        _enforced_cache.update({"key": key, "value": dict(value)})
    return value


def forget_enforced() -> None:
    """Drop the cached reading (tests, and after the manifest is rewritten)."""
    with _enforced_lock:
        _enforced_cache.update({"key": None, "value": None})


def reach_level(config_dir: Optional[str] = None) -> str:
    """``open``, ``switched_on`` or ``sealed``. Never raises; fails closed."""
    try:
        return str(enforced(config_dir)["level"])
    except Exception:
        return "switched_on"


def only_switched_on(config_dir: Optional[str] = None) -> bool:
    """True when only what the owner switched on may leave this computer."""
    return reach_level(config_dir) in ("switched_on", "sealed")


def is_sealed(config_dir: Optional[str] = None) -> bool:
    """True when nothing may leave this computer at all."""
    return reach_level(config_dir) == "sealed"


def switch_allows(sid: str, get_switch) -> bool:
    """Does the Bridge in force let *sid* be used? See
    :func:`puffpai.zone_bridge.switch_allows_at`."""
    from puffpai import zone_bridge
    return zone_bridge.switch_allows_at(reach_level(), sid, get_switch)


class BridgeRefused(ConnectionRefusedError):
    """The Bridge does not allow this connection. Nothing was sent."""

    def __init__(self, message: str, user_message: str) -> None:
        super().__init__(message)
        self.user_message = user_message
        self.code = LEDGER_CODE


# ── what is on the stack ─────────────────────────────────────────────────────

def exit_on_stack(frame) -> bool:
    """True when one of the listed exits is making this connection."""
    while frame is not None:
        name = os.path.normcase(os.path.abspath(frame.f_code.co_filename))
        if name in _EXITS:
            return True
        frame = frame.f_back
    return False


# ── the home services the owner switched on ─────────────────────────────────

def _ip_text(host: Any) -> Optional[str]:
    try:
        addr = ipaddress.ip_address(str(host).strip().strip("[]").split("%", 1)[0])
    except ValueError:
        return None
    mapped = getattr(addr, "ipv4_mapped", None)
    return str(mapped or addr)


def _addresses_of(host: str, port: int) -> frozenset:
    literal = _ip_text(host)
    if literal:
        return frozenset({literal})
    now = time.monotonic()
    key = (host, port)
    with _names_lock:
        cached = _names.get(key)
        if cached and now - cached[0] < HOME_NAME_TTL:
            return cached[1]
    try:
        found = frozenset(filter(None, (_ip_text(info[4][0]) for info in
                                        socket.getaddrinfo(host, port))))
    except (OSError, UnicodeError):
        found = frozenset()
    with _names_lock:
        _names[key] = (now, found)
    return found


def forget_names() -> None:
    """Drop the remembered addresses (tests, and after the settings change)."""
    with _names_lock:
        _names.clear()


def switched_on_home(cfg: Any = None) -> list[tuple[str, int]]:
    """``(host, port)`` of every home service the owner switched on.

    Read from the settings each time: a service switched off a moment ago is
    not on this list any more.
    """
    if cfg is None:
        try:
            from puffpai.config import get_config
            cfg = get_config()
        except Exception:
            return []
    out: list[tuple[str, int]] = []
    for section, field in _SWITCHED_HOME:
        block = getattr(cfg, section, None)
        if block is None or getattr(block, "enabled", False) is not True:
            continue
        url = str(getattr(block, field, "") or "").strip()
        if not url:
            continue
        try:
            parts = urlsplit(url)
            host = (parts.hostname or "").strip().rstrip(".").lower()
            port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
        except ValueError:
            continue
        if host:
            out.append((host, int(port)))
    return out


def is_switched_on_home(address: Any, cfg: Any = None) -> bool:
    """True when *address* (``(host, port, ...)``) is a switched-on home service."""
    if not (isinstance(address, tuple) and len(address) >= 2):
        return False
    dialled = _ip_text(address[0]) or str(address[0]).strip().lower()
    try:
        port = int(address[1])
    except (TypeError, ValueError):
        return False
    for host, want_port in switched_on_home(cfg):
        if want_port != port:
            continue
        if dialled == host or dialled in _addresses_of(host, want_port):
            return True
    return False


# ── the check ────────────────────────────────────────────────────────────────

def _shown_host(host: Any) -> str:
    try:
        return str(ipaddress.ip_address(str(host).strip("[]").split("%", 1)[0]))
    except ValueError:
        pass
    try:
        from puffpai import web_search
        return web_search.log_name(host, placeholder="", limit=253)
    except Exception:
        return ""


def _refuse(address: Any, kind: str, level: str, where: str) -> None:
    from puffpai import zone_bridge
    from puffpai.security import lan_posture
    raw = address[0] if isinstance(address, tuple) and address else address
    host = _shown_host(raw)
    home = kind in (lan_posture.PRIVATE, lan_posture.LAN_PRESUMED)
    logger.warning(
        "The Bridge stopped a connection from %s to %s: %s", where,
        host or "an address (not shown)",
        "this build keeps everything on this computer" if level ==
        zone_bridge.LEVEL_SEALED else "it was not switched on")
    try:
        from puffpai.security import egress_ledger
        egress_ledger.record(LEDGER_SERVICE_HOME if home else LEDGER_SERVICE_OTHER,
                             host=host, outcome="refused", code=LEDGER_CODE)
    except Exception:  # the log must never turn a refusal into a crash
        pass
    words = (zone_bridge.SEALED_LINE if level == zone_bridge.LEVEL_SEALED
             else zone_bridge.REFUSED_LINE)
    raise BridgeRefused(
        f"{where}: the Bridge stopped a connection to "
        f"{host or 'an address (not shown)'}", words)


#: The outside-traffic log's code for a connection to a proxy on this
#: computer while the Bridge is up.
PROXY_CODE = "bridge_proxy"


def _refuse_proxy(address: Any, level: str, where: str) -> None:
    """Refuse a connection to a proxy on this computer. Always raises."""
    from puffpai import zone_bridge
    logger.warning(
        "The Bridge stopped a connection from %s to a proxy on this computer: "
        "a proxy could carry it past the Bridge", where)
    try:
        from puffpai.security import egress_ledger
        egress_ledger.record(LEDGER_SERVICE_OTHER, host="", outcome="refused",
                             code=PROXY_CODE)
    except Exception:  # the log must never turn a refusal into a crash
        pass
    words = (zone_bridge.SEALED_LINE if level == zone_bridge.LEVEL_SEALED
             else zone_bridge.REFUSED_LINE)
    raise BridgeRefused(
        f"{where}: the Bridge stopped a connection to a proxy on this computer",
        words)


# ── a proxy handed to a request directly ────────────────────────────────────
#
# A proxy named in the call itself (``proxies=`` in a ``requests`` call, an
# ``urllib`` ProxyHandler with an address, ``http.client``'s ``set_tunnel``)
# is not in the settings :func:`lan_guard.proxy_endpoints` reads, so at
# connect time it is just "this computer" and is let through (a proxy on
# this computer receives ``CONNECT 198.51.100.20:443`` with the Bridge up). :func:`guard_proxy_entry_points`
# wraps the places Python's HTTP libraries take a proxy on, so with the Bridge
# up a proxy is refused before anything is dialled, wherever it came from.
# BuildBot's own requests never name a proxy (``lan_guard.no_proxies``,
# ``ProxyHandler({})``), so none of them is touched.

def _refusal_with(extra_base: Optional[type]) -> type:
    """:class:`BridgeRefused` that is also *extra_base*, so a caller that
    catches only its own library's errors still catches the refusal."""
    if extra_base is None or issubclass(BridgeRefused, extra_base):
        return BridgeRefused

    class _Refused(BridgeRefused, extra_base):  # type: ignore[misc, valid-type]
        def __init__(self, message: str, user_message: str) -> None:
            BridgeRefused.__init__(self, message, user_message)
            self.reason = message        # urllib.error.URLError
            self.response = None         # requests.RequestException
            self.request = None

        __str__ = BaseException.__str__

    _Refused.__name__ = f"BridgeRefused_{extra_base.__name__}"
    return _Refused


def refuse_named_proxy(frame=None, error: type = BridgeRefused) -> None:
    """Refuse a proxy named in a call while the Bridge is up. Else return."""
    from puffpai import zone_bridge
    level = reach_level()
    if level == zone_bridge.LEVEL_OPEN:
        return
    frame = frame if frame is not None else sys._getframe(1)
    try:
        _refuse_proxy(None, level, _where(frame))
    except BridgeRefused as refused:
        if error is BridgeRefused:
            raise
        raise error(str(refused), refused.user_message) from None


def _guard_method(owner: Any, name: str, error: type, when=None,
                  result=None) -> bool:
    """Wrap ``owner.name`` so a proxy is refused while the Bridge is up.

    *owner* is a class or a module. With neither *when* nor *result*, every
    call means a proxy. *when(args, kwargs)* says whether this call names
    one (asked before the call); *result(value)* whether what came back is
    one (asked after a call that only looks the proxy up).
    """
    real = getattr(owner, "__dict__", {}).get(name)
    if real is None:
        return False
    if getattr(real, "_puffpai_bridge_guarded", False):
        return True

    def guarded(*args, **kwargs):
        if when is None and result is None:
            refuse_named_proxy(sys._getframe(1), error)
        elif when is not None:
            try:
                named = bool(when(args, kwargs))
            except Exception:
                named = True  # unreadable arguments: judged as a proxy
            if named:
                refuse_named_proxy(sys._getframe(1), error)
        value = real(*args, **kwargs)
        if result is not None:
            try:
                named = bool(result(value))
            except Exception:
                named = True
            if named:
                refuse_named_proxy(sys._getframe(1), error)
        return value

    guarded.__name__ = getattr(real, "__name__", name)
    guarded.__qualname__ = getattr(real, "__qualname__", name)
    guarded.__doc__ = getattr(real, "__doc__", None)
    guarded.__wrapped__ = real  # type: ignore[attr-defined]
    guarded._puffpai_bridge_guarded = True  # type: ignore[attr-defined]
    setattr(owner, name, guarded)
    return True


def _arg(args: tuple, kwargs: dict, index: int, name: str) -> Any:
    return args[index] if len(args) > index else kwargs.get(name)


#: A request target that starts with a scheme (``http://x/``), the form a
#: request takes when it is sent to a proxy.
_ABSOLUTE_FORM = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")


#: Characters that end the address part of a full request target, as a
#: browser-style (WHATWG) reader sees it: it treats ``\\`` like ``/``.
_AUTHORITY_END = re.compile(r"[/\\?#]")


def _http_request_names_a_proxy(args: tuple, kwargs: dict) -> bool:
    """``HTTPConnection.putrequest(self, method, url, ...)``: a ``CONNECT``,
    a tunnel host set on the connection, or a full address for some other
    computer (``GET http://x/``, the shape a request takes when the computer
    it is sent to is a proxy).

    ``http://203.0.113.77\\@127.0.0.1/`` reads as 127.0.0.1 to Python's
    ``urlsplit`` but as 203.0.113.77 to a browser-style reader (a Node
    forwarding proxy). A full address that two readers could read differently
    (a backslash, a name and password before ``@``, a control or space
    character, a ``%`` in the address part) counts as naming a proxy, and so
    does a tunnel host set by hand.
    """
    conn = args[0] if args else None
    if getattr(conn, "_tunnel_host", None):
        return True
    method = str(_arg(args, kwargs, 1, "method") or "")
    if method.strip().upper() == "CONNECT":
        return True
    raw = str(_arg(args, kwargs, 2, "url") or "")
    url = raw.strip()
    if not _ABSOLUTE_FORM.match(url):
        return False  # a path, even one with an address in its query
    if "\\" in raw or any(ord(ch) <= 32 or ord(ch) == 127 for ch in url):
        return True
    after = url.split("://", 1)[1]
    authority = _AUTHORITY_END.split(after, 1)[0]
    if "@" in authority or "%" in authority:
        return True
    try:
        host = (urlsplit(url).hostname or "").strip("[]").lower()
    except ValueError:
        return True
    # The same address part, read the browser way: it must name the same host.
    whatwg = authority.rsplit(":", 1)[0] if not authority.endswith("]") else authority
    if whatwg.strip("[]").lower() != host:
        return True
    own = str(getattr(conn, "host", "") or "").strip("[]").lower()
    return host != own


def _tunnel_names_a_proxy(args: tuple, kwargs: dict) -> bool:
    """``HTTPConnection._tunnel(self)``: sends ``CONNECT`` to whatever
    ``_tunnel_host`` says, however it was set (``set_tunnel`` or by hand)."""
    conn = args[0] if args else None
    return bool(getattr(conn, "_tunnel_host", None))


def _aiohttp_names_a_proxy(args: tuple, kwargs: dict) -> bool:
    """``ClientRequest.update_proxy(self, proxy, ...)``: runs for every
    request, with ``proxy`` set only when one is named (or found in the
    settings, with ``trust_env``)."""
    return _arg(args, kwargs, 1, "proxy") is not None


def _websocket_names_a_proxy(args: tuple, kwargs: dict) -> bool:
    """``websocket._http.proxy_info(**options)``: made for every connection,
    with ``http_proxy_host`` set only when a proxy is named."""
    return bool(kwargs.get("http_proxy_host"))


def _websocket_found_a_proxy(value: Any) -> bool:
    """``websocket._http.get_proxy_info`` returns ``(host, port, auth)``,
    with a host when the settings name a proxy."""
    return bool(value and value[0])


#: Where each library takes a proxy on, by module: ``(owner, name, when,
#: result, error)``. *owner* is a class name in the module, or ``""`` for the
#: module itself. Modules not imported yet are wrapped when they are
#: (:class:`_GuardOnImport`), so BuildBot does not load libraries it does not
#: use just to watch them.
_LATE: dict[str, tuple] = {
    "httpcore._sync.http_proxy": (
        ("ForwardHTTPConnection", "__init__", None, None, None),
        ("TunnelHTTPConnection", "__init__", None, None, None)),
    "httpcore._async.http_proxy": (
        ("AsyncForwardHTTPConnection", "__init__", None, None, None),
        ("AsyncTunnelHTTPConnection", "__init__", None, None, None)),
    "httpcore._sync.socks_proxy": (
        ("Socks5Connection", "__init__", None, None, None),),
    "httpcore._async.socks_proxy": (
        ("AsyncSocks5Connection", "__init__", None, None, None),),
    "aiohttp.client_reqrep": (
        ("ClientRequest", "update_proxy", _aiohttp_names_a_proxy, None,
         ("aiohttp.client_exceptions", "ClientConnectionError")),),
    "websocket._http": (
        ("proxy_info", "__init__", _websocket_names_a_proxy, None,
         ("websocket._exceptions", "WebSocketProxyException")),
        ("", "get_proxy_info", None, _websocket_found_a_proxy,
         ("websocket._exceptions", "WebSocketProxyException"))),
}

_guarded_names: set[str] = set()
_guarded_lock = threading.Lock()


def _error_for(spec: Optional[tuple]) -> type:
    if not spec:
        return BridgeRefused
    try:
        module = sys.modules.get(spec[0]) or __import__(spec[0], fromlist=["_"])
        return _refusal_with(getattr(module, spec[1]))
    except Exception:
        return BridgeRefused


def _guard_late_module(module_name: str, module: Any) -> None:
    for owner_name, name, when, result, error in _LATE.get(module_name, ()):
        owner = getattr(module, owner_name, None) if owner_name else module
        if owner is None:
            continue
        try:
            if _guard_method(owner, name, _error_for(error), when, result):
                with _guarded_lock:
                    _guarded_names.add(
                        f"{module_name}.{owner_name + '.' if owner_name else ''}{name}")
        except Exception:  # pragma: no cover - a changed library
            logger.warning("The Bridge could not watch %s.%s for a proxy",
                           module_name, name)


class _GuardOnImport:
    """Wraps a module in :data:`_LATE` right after it is first imported."""

    _puffpai_bridge_guard = True

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in _LATE:
            return None
        for finder in sys.meta_path:
            if finder is self or getattr(finder, "_puffpai_bridge_guard", False):
                continue
            find = getattr(finder, "find_spec", None)
            if find is None:
                continue
            spec = find(fullname, path, target)
            if spec is not None:
                break
        else:
            return None
        loader = spec.loader
        real_exec = getattr(loader, "exec_module", None)
        if real_exec is None:
            return spec

        def exec_module(module, _real=real_exec, _name=fullname):
            _real(module)
            _guard_late_module(_name, module)

        try:
            loader.exec_module = exec_module
        except Exception:  # pragma: no cover - a loader that will not take it
            pass
        return spec

    def invalidate_caches(self) -> None:
        return None


def guard_proxy_entry_points() -> list[str]:
    """Wrap the places a proxy is taken on. Returns what is guarded so far.

    Safe to call again (already-wrapped methods are left alone). Standard
    library: ``http.client`` (``set_tunnel``, and ``putrequest`` for a
    ``CONNECT`` or a full address for another computer, so code that writes
    a proxy request by hand through it is held too) and ``urllib``. Then
    ``urllib3`` and ``requests``, and ``httpx`` (through ``httpcore``'s proxy
    connections), ``aiohttp`` and ``websocket-client`` when they are loaded,
    now or later. A library that is not installed is skipped.
    """
    done: list[str] = []
    try:
        import http.client
        if _guard_method(http.client.HTTPConnection, "set_tunnel", BridgeRefused):  # lan-egress: wraps (does not dial) the tunnel setter so the Bridge can refuse a proxy
            done.append("http.client.HTTPConnection.set_tunnel")  # lan-egress: a name in a list, nothing is dialled
        if _guard_method(http.client.HTTPConnection, "putrequest", BridgeRefused,  # lan-egress: wraps (does not dial) the request line writer
                         when=_http_request_names_a_proxy):
            done.append("http.client.HTTPConnection.putrequest")  # lan-egress: a name in a list, nothing is dialled
        # A tunnel host set by hand (no set_tunnel) is read when the CONNECT
        # is sent, so it is checked there, at send time.
        if _guard_method(http.client.HTTPConnection, "_tunnel", BridgeRefused,  # lan-egress: wraps (does not dial) the CONNECT writer
                         when=_tunnel_names_a_proxy):
            done.append("http.client.HTTPConnection._tunnel")  # lan-egress: a name in a list, nothing is dialled
    except Exception:  # pragma: no cover - the standard library is there
        pass
    try:
        import urllib.error
        import urllib.request
        if _guard_method(urllib.request.Request, "set_proxy",
                         _refusal_with(urllib.error.URLError)):
            done.append("urllib.request.Request.set_proxy")
    except Exception:  # pragma: no cover
        pass
    try:
        import urllib3.poolmanager  # lan-egress: imported only to wrap (not dial) its ProxyManager
        if _guard_method(urllib3.poolmanager.ProxyManager, "__init__", BridgeRefused):
            done.append("urllib3.poolmanager.ProxyManager.__init__")
    except Exception:
        pass
    try:
        import requests.adapters
        import requests.exceptions
        if _guard_method(requests.adapters.HTTPAdapter, "proxy_manager_for",
                         _refusal_with(requests.exceptions.ProxyError)):
            done.append("requests.adapters.HTTPAdapter.proxy_manager_for")
    except Exception:
        pass
    if not any(getattr(f, "_puffpai_bridge_guard", False) for f in sys.meta_path):
        sys.meta_path.insert(0, _GuardOnImport())
    for module_name in _LATE:
        module = sys.modules.get(module_name)
        if module is not None:
            _guard_late_module(module_name, module)
    with _guarded_lock:
        done.extend(sorted(_guarded_names))
    return done


#: The guard's own files: never named as the place a connection came from.
_GUARD_FILES = tuple(os.path.normcase(os.path.join(_SRC, "security", f))
                     for f in ("lan_guard.py", "bridge_guard.py"))


def _where(frame) -> str:
    while frame is not None:
        name = os.path.normcase(os.path.abspath(frame.f_code.co_filename))
        if name.startswith(_SRC) and name not in _GUARD_FILES:
            return f"{os.path.relpath(name, _SRC)}:{frame.f_lineno}"
        frame = frame.f_back
    return "code outside BuildBot"


def check(sock: Any, address: Any, frame=None) -> None:
    """Refuse *address* when the Bridge does not allow it. Else return.

    Cheap on the common path: this computer, a socket that is not an internet
    one, and level ``open`` all return before anything is read from disk
    beyond one ``stat`` of the build record.
    """
    if not (isinstance(address, tuple) and len(address) >= 2):
        return  # a Unix socket path and the like: not the network
    family = getattr(sock, "family", None)
    if family is not None and family not in (socket.AF_INET, socket.AF_INET6):
        return
    from puffpai import zone_bridge
    from puffpai.security import lan_guard, lan_posture
    kind = lan_guard.classify(address)
    if kind == "":
        return
    if kind == lan_posture.LOOPBACK:
        # This computer is always allowed, except a proxy set on it (the
        # HTTP(S)_PROXY / ALL_PROXY settings or the system proxy), which
        # would carry the request on past the Bridge: the check cannot see
        # what it is asked to reach behind it.
        # With the Bridge up, nothing dials one. BuildBot's own requests
        # never use a proxy (pinned_net, lan_guard.session); the locked box's
        # allowlisting proxy runs inside the box, not on this list.
        if reach_level() == zone_bridge.LEVEL_OPEN or not lan_guard.is_proxy(address):
            return
        frame = frame if frame is not None else sys._getframe(1)
        _refuse_proxy(address, reach_level(), _where(frame))
        return  # pragma: no cover - _refuse_proxy always raises
    level = reach_level()
    if level == zone_bridge.LEVEL_OPEN:
        return
    frame = frame if frame is not None else sys._getframe(1)
    where = _where(frame)
    if level == zone_bridge.LEVEL_SEALED:
        _refuse(address, kind, level, where)
    if exit_on_stack(frame):
        return  # the exit checked the owner's switch (Bridge-aware) itself
    if (kind in (lan_posture.PRIVATE, lan_posture.LAN_PRESUMED)
            and lan_guard.marked_site(frame) is not None
            and is_switched_on_home(address)):
        return
    _refuse(address, kind, level, where)


__all__ = ["BridgeRefused", "EXIT_FILES", "HOME_NAME_TTL", "LEDGER_CODE",
           "LEDGER_SERVICE_HOME", "LEDGER_SERVICE_OTHER", "PROXY_CODE", "check",
           "enforced", "exit_on_stack", "forget_enforced", "forget_names",
           "guard_proxy_entry_points", "is_sealed", "is_switched_on_home",
           "only_switched_on", "reach_level", "refuse_named_proxy",
           "switch_allows", "switched_on_home"]
