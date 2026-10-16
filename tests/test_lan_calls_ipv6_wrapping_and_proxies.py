"""A home-network call cannot reach the internet wrapped in IPv6 or via a proxy.

Two ways round the guard that are now closed:

* NAT64 (64:ff9b::/96), 6to4 (2002::/16), Teredo (2001::/32) and
  IPv4-compatible (::a.b.c.d) addresses carrying a PUBLIC IPv4 address were
  classed private, so a marked call could dial 8.8.8.8 as ``64:ff9b::808:808``;
* a marked call went through an HTTP proxy (HTTP_PROXY, or the Windows system
  proxy that requests follows): the guard saw a loopback address and let it
  through, and the proxy carried the request to the internet.

Now the IPv4 address inside is judged, marked calls never use a proxy, and a
marked call that dials a proxy is refused. The only sockets opened here are on
127.0.0.1, ports 8318-8319.
"""
from __future__ import annotations

import ast
import os
import re
import socket
import threading
import types
from pathlib import Path

import pytest
import requests

import conftest
from puffpai import outside_services, plain_words
from puffpai.security import egress_ledger, lan_guard, lan_posture

SRC = Path(__file__).resolve().parents[1] / "src" / "puffpai"
PROXY_PORT, TARGET_PORT = 8318, 8319


def _module_in_src(name: str, code: str) -> types.ModuleType:
    import linecache
    mod = types.ModuleType(name)
    path = os.path.join(conftest._SRC_ROOT, f"{name}.py")
    linecache.cache[path] = (len(code), None, code.splitlines(True), path)
    exec(compile(code, path, "exec"), mod.__dict__)
    return mod


_PROBES = _module_in_src("zz_lan_guard_probe3", (
    "import sys, requests\n"
    "from puffpai.security import lan_guard\n"
    "def marked(addr):\n"
    "    sys.audit('socket.connect', None, addr)  # lan-egress: a home service\n"
    "\n"
    "\n"
    "\n"
    "def unmarked(addr):\n"
    "    sys.audit('socket.connect', None, addr)\n"
    "\n"
    "\n"
    "\n"
    "def marked_get_plain(url):\n"
    "    return requests.get(url, timeout=3)  # lan-egress: a home service, no proxies= (the old way)\n"
    "def marked_get(url):\n"
    "    return requests.get(url, timeout=3, proxies=lan_guard.no_proxies())  # lan-egress: a home service\n"
    "def marked_session_get(url):\n"
    "    return lan_guard.session().get(url, timeout=3)  # lan-egress: a home service\n"))


@pytest.fixture(autouse=True)
def _no_proxy_settings(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                 "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    lan_guard.install()


# ── IPv6 addresses that carry an IPv4 address ───────────────────────────────

@pytest.mark.parametrize("address,want", [
    ("64:ff9b::cb00:7105", "public"),        # NAT64 of 203.0.113.5
    ("64:ff9b::808:808", "public"),          # NAT64 of 8.8.8.8
    ("2002:808:808::1", "public"),           # 6to4 of 8.8.8.8
    ("2001:0:4136:e378::1", "public"),       # Teredo
    ("::808:808", "public"),                 # IPv4-compatible 8.8.8.8
    ("::ffff:0:808:808", "public"),          # IPv4-translated 8.8.8.8
    ("64:ff9b:1:808:8:800::", "public"),     # local-use NAT64 of 8.8.8.8
    ("::ffff:203.0.113.5", "public"),
    ("64:ff9b::c0a8:101", "private"),        # NAT64 of 192.168.1.1
    ("2002:c0a8:101::1", "private"),         # 6to4 of 192.168.1.1
    ("::ffff:127.0.0.1", "loopback"),
    ("::1", "loopback"),
    ("::", "loopback"),
    ("fe80::1%3", "private"),
    ("fd00::1", "private"),
])
def test_an_ipv6_address_is_judged_by_the_ipv4_address_it_carries(address, want):
    assert lan_guard.classify((address, 80)) == want
    assert lan_posture._classify_address(address) == want


@pytest.mark.parametrize("address", ["64:ff9b::808:808", "2002:808:808::1",
                                     "2001:0:4136:e378::1", "::808:808"])
def test_a_marked_call_to_a_wrapped_internet_address_is_refused(address):
    with pytest.raises(lan_guard.LanEgressRefused):
        _PROBES.marked((address, 443, 0, 0))


# ── proxies ─────────────────────────────────────────────────────────────────

def test_a_marked_call_that_dials_a_proxy_is_refused_and_logged(monkeypatch, caplog):
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{PROXY_PORT}")
    before = len(egress_ledger.read(1.0))
    with pytest.raises(lan_guard.LanEgressRefused):
        _PROBES.marked(("127.0.0.1", PROXY_PORT))
    assert "that is a proxy" in caplog.text
    rows = egress_ledger.read(1.0)
    assert len(rows) == before + 1
    assert rows[0]["code"] == lan_guard.PROXY_CODE
    line = outside_services.log_rows(1.0)[0]["line"]
    assert "proxy" in line and not plain_words.check(line)
    # The same address and port without a proxy set is a home service.
    monkeypatch.delenv("HTTP_PROXY")
    _PROBES.marked(("127.0.0.1", PROXY_PORT))
    # A different port on the proxy's computer is not the proxy.
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{PROXY_PORT}")
    _PROBES.marked(("127.0.0.1", TARGET_PORT))
    # An unmarked call is not judged here (the listed exits judge their own).
    _PROBES.unmarked(("127.0.0.1", PROXY_PORT))


@pytest.mark.parametrize("setting", ["HTTPS_PROXY", "ALL_PROXY", "http_proxy"])
def test_every_proxy_setting_counts(monkeypatch, setting):
    monkeypatch.setenv(setting, f"localhost:{PROXY_PORT}")   # a name, no scheme
    assert lan_guard.is_proxy(("127.0.0.1", PROXY_PORT))
    with pytest.raises(lan_guard.LanEgressRefused):
        _PROBES.marked(("127.0.0.1", PROXY_PORT))


def test_the_system_proxy_counts_too(monkeypatch):
    """requests and urllib follow the Windows system proxy when no setting
    names one; the guard reads the same list (urllib.request.getproxies)."""
    import urllib.request
    monkeypatch.setattr(urllib.request, "getproxies",
                        lambda: {"http": f"http://192.168.1.50:{PROXY_PORT}"})
    assert lan_guard.is_proxy(("192.168.1.50", PROXY_PORT))
    with pytest.raises(lan_guard.LanEgressRefused):
        _PROBES.marked(("192.168.1.50", PROXY_PORT))


@pytest.mark.parametrize("url", ["http://192.168.1.20:8123/api/",
                                 "https://homeassistant.local/api/",
                                 "http://127.0.0.1:11435/api/tags"])
def test_no_proxies_keeps_every_proxy_setting_out(monkeypatch, url):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, f"http://127.0.0.1:{PROXY_PORT}")
    with requests.Session() as s:
        merged = s.merge_environment_settings(url, lan_guard.no_proxies(),
                                              None, None, None)["proxies"]
    assert requests.utils.select_proxy(url, merged) is None
    # ... and after a redirect, where requests reads the settings again.
    prepared = requests.Request("GET", url).prepare()
    after = requests.utils.resolve_proxies(prepared, merged, trust_env=True)
    assert requests.utils.select_proxy(url, after) is None
    # A fresh dict every time (requests writes into the one it is given).
    assert lan_guard.no_proxies() is not lan_guard.no_proxies()


def test_the_session_and_the_opener_use_no_proxy(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{PROXY_PORT}")
    s = lan_guard.session()
    assert s.trust_env is False
    merged = s.merge_environment_settings("http://192.168.1.20/", {}, None, None, None)
    assert requests.utils.select_proxy("http://192.168.1.20/", merged["proxies"]) is None
    import urllib.request

    def _proxied(built):
        return [h for h in built.handlers
                if isinstance(h, urllib.request.ProxyHandler) and h.proxies]
    assert _proxied(urllib.request.build_opener())      # the default follows it
    assert not _proxied(lan_guard.opener())             # ours does not


class _Listener:
    """A one-shot HTTP answerer on 127.0.0.1; remembers what it was sent."""

    def __init__(self, port: int):
        self.seen: list[bytes] = []
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen(4)
        self.sock.settimeout(5)
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        try:
            while True:
                conn, _ = self.sock.accept()
                with conn:
                    self.seen.append(conn.recv(400))
                    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n"
                                 b"Connection: close\r\n\r\nok")
        except OSError:
            return

    def close(self):
        self.sock.close()


def test_a_real_marked_call_never_goes_through_a_real_proxy(monkeypatch):
    """verify3's probe 2, end to end on loopback: a proxy that would forward
    anything, and a home service. The proxy never hears from a marked call."""
    proxy, target = _Listener(PROXY_PORT), _Listener(TARGET_PORT)
    try:
        monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{PROXY_PORT}")
        url = f"http://127.0.0.1:{TARGET_PORT}/leak?secret=1"
        assert _PROBES.marked_get(url).text == "ok"            # direct, no proxy
        assert _PROBES.marked_session_get(url).text == "ok"    # direct, no proxy
        # The old way (no proxies=) dials the proxy: refused before it is sent.
        with pytest.raises(requests.exceptions.RequestException):
            _PROBES.marked_get_plain(url)
        assert proxy.seen == []
        assert len(target.seen) == 2
    finally:
        proxy.close()
        target.close()


# ── every marked call in BuildBot skips proxies ─────────────────────────────

_MARK = "# lan-" "egress:"
_REQ_CALL = re.compile(r"(?:^|\.)(?:requests|_requests|_req)\.(get|post|put|patch|delete|head|request)$")


def _dotted(node) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def test_every_marked_call_in_the_code_skips_proxies():
    problems = []
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if _MARK not in text or path.name == "lan_guard.py":
            continue
        lines = text.splitlines()
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, ast.Call) or _MARK not in lines[node.lineno - 1]:
                continue
            name = _dotted(node.func)
            where = f"{path.relative_to(SRC)}:{node.lineno}"
            if _REQ_CALL.search(name) and not any(
                    k.arg == "proxies" for k in node.keywords):
                problems.append(f"{where}: {name} without proxies=")
            if name.endswith("urlopen"):
                problems.append(f"{where}: urlopen follows the system proxy")
            if name.endswith("build_opener") and not (
                    node.args and "ProxyHandler" in ast.unparse(node.args[0])):
                problems.append(f"{where}: build_opener without ProxyHandler({{}})")
            if name.endswith("Session") and "trust_env = False" not in "\n".join(
                    lines[node.lineno:node.lineno + 4]):
                problems.append(f"{where}: a Session without trust_env = False")
    assert not problems, problems


def test_the_module_says_what_it_does_not_cover():
    doc = lan_guard.__doc__
    assert "another thread" in doc and "not judged" in doc
    assert "proxy" in doc.lower()
