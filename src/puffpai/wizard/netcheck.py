"""Does the internet answer? Measured once, quietly, never asked.

Whether this computer can reach the internet while
building is detected, not asked. :func:`internet_reachable` opens one TCP
connection to the host a model download would come from, sends nothing, and
closes it. The answer is cached for a minute so a browser page that proposes
on every load does not connect on every load.

It is an outside service like any other, so it is listed on "What leaves your
computer" (service ``internet_check``), every connection -- and every refusal
-- is one line in the outside-traffic log, and the owner can switch it off
there. The connection is pinned (:mod:`puffpai.security.pinned_net`): the
name is looked up once, every answer must be a public address, and the socket
goes to that address.

``PUFFPAI_WIZARD_NET_PROBE=off`` or the owner's switch turns the check off,
and then the answer is ``None`` -- *not checked* -- which is never treated as
online: nothing is downloaded by default on a computer nobody checked. The
test suite runs with it off.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os
import socket
import time

#: Where the check connects: the model registry, because a model download is
#: the first thing an online build would fetch. Port 443, nothing sent.
PROBE_HOST = "registry.ollama.ai"
PROBE_PORT = 443
PROBE_TIMEOUT_S = 2.0
PROBE_ENV = "PUFFPAI_WIZARD_NET_PROBE"
CACHE_S = 60.0

#: The outside-services id this check is listed, logged and switched under.
SERVICE = "internet_check"

#: Resolver seam for tests: ``(host, port) -> [address, ...]``; None = system.
_resolve = None

_cache: dict = {}


def _switched_off() -> bool:
    try:
        from puffpai.security import outside_switches
        return outside_switches.is_off(SERVICE)
    except Exception:  # unreadable switch state: do not connect
        return True


def internet_reachable(timeout: float = PROBE_TIMEOUT_S) -> bool | None:
    """True / False when checked, ``None`` when checking is switched off."""
    if str(os.environ.get(PROBE_ENV, "")).strip().lower() in (
            "off", "0", "no", "false"):
        return None
    if _switched_off():
        return None
    now = time.time()
    cached = _cache.get("result")
    if cached is not None and now - _cache.get("at", 0) < CACHE_S:
        return cached
    from puffpai.security import egress_ledger, pinned_net
    pin = pinned_net.Pin(resolver=_resolve)
    try:
        sock = pin.open_socket(PROBE_HOST, PROBE_PORT, timeout,
                               create=socket.create_connection)
        try:
            sock.close()
        except OSError:
            pass
        result = True
        egress_ledger.record(SERVICE, host=PROBE_HOST, outcome="done",
                             what="internet_check", bytes_in=0, bytes_out=0)
    except pinned_net.PinRefused as exc:
        result = False
        egress_ledger.record(SERVICE, host=PROBE_HOST, outcome="refused",
                             code=exc.code, what="internet_check",
                             bytes_in=0, bytes_out=0)
    except OSError:
        result = False
        egress_ledger.record(SERVICE, host=PROBE_HOST, outcome="failed",
                             code="no_answer", what="internet_check",
                             bytes_out=0)
    _cache.update({"result": result, "at": now})
    return result


def forget() -> None:
    """Drop the cached answer (a test, or a cable just plugged in)."""
    _cache.clear()


__all__ = ["PROBE_ENV", "PROBE_HOST", "PROBE_PORT", "SERVICE", "forget",
           "internet_reachable"]
