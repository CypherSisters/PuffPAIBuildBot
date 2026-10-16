"""DNS rebinding is closed: a name is looked up once and the socket goes to
that vetted address, while TLS still uses the name (SNI) -- at every exit.

The attack these tests stage: a resolver that answers with a public address
the first time and a private one the next. Before pinning, the pre-flight
check saw the public answer and the HTTP library looked the name up again to
connect, landing inside the house.
"""

from __future__ import annotations

import socket
import threading

import pytest

from puffpai.security import egress_doors, egress_ledger, pinned_net

PUBLIC = "93.184.216.34"
PRIVATE = "192.168.1.1"


class FlipResolver:
    """Public on the first lookup, private on every later one."""

    def __init__(self, first=PUBLIC, then=PRIVATE):
        self.first, self.then, self.calls = first, then, []

    def __call__(self, host, port):
        self.calls.append(host)
        return [self.first] if len(self.calls) == 1 else [self.then]


@pytest.fixture
def ledger(tmp_path):
    egress_ledger.set_path_override(str(tmp_path / "outside.jsonl"))
    yield
    egress_ledger.set_path_override(None)


@pytest.fixture
def open_doors(monkeypatch):
    monkeypatch.setattr(egress_doors, "run_posture",
                        lambda: {"run_egress": "enumerated", "source": "test",
                                 "switches": {}})
    monkeypatch.setenv("PUFFPAI_EGRESS_NEWS_RSS", "on")
    monkeypatch.setenv("PUFFPAI_EGRESS_WEATHER", "on")
    egress_doors.reset_cache()
    yield
    egress_doors.reset_cache()


# ── the address policy ───────────────────────────────────────────────────────

@pytest.mark.parametrize("addr", [
    "127.0.0.1", "10.1.2.3", "172.16.0.9", "192.168.1.1", "169.254.169.254",
    "100.64.0.1", "0.0.0.0", "::1", "fe80::1", "fd00::1", "::ffff:127.0.0.1",
    "2002:c0a8:0101::1",          # 6to4 wrapping 192.168.1.1
    "64:ff9b::a9fe:a9fe",         # NAT64 wrapping 169.254.169.254
    "224.0.0.1",
])
def test_every_inward_address_is_refused(addr):
    assert pinned_net.address_refusal(addr)


@pytest.mark.parametrize("addr", [PUBLIC, "1.1.1.1", "2606:4700:4700::1111"])
def test_public_addresses_pass(addr):
    assert pinned_net.address_refusal(addr) == ""


def test_one_private_answer_among_public_ones_refuses_the_name():
    with pytest.raises(pinned_net.PinRefused) as exc:
        pinned_net.resolve_public("mixed.example", 443,
                                  lambda h, p: [PUBLIC, PRIVATE])
    assert exc.value.code == "inside_address"


# ── requests path (every door, card, picture, town lookup, sample look) ─────

def _capture_dial(monkeypatch):
    """Record where urllib3 dials; stop the request right there."""
    from urllib3.util import connection as u3conn
    dialled = []

    def fake_create(address, *a, **k):
        dialled.append(address)
        raise OSError("test stops here")

    monkeypatch.setattr(u3conn, "create_connection", fake_create)
    return dialled


def test_a_dynamic_door_dials_the_address_it_checked(monkeypatch, open_doors,
                                                     ledger):
    dialled = _capture_dial(monkeypatch)
    flip = FlipResolver()
    with pytest.raises(Exception):
        egress_doors.egress_get("news_rss", "https://feeds.example.test/rss",
                                resolver=flip)
    assert flip.calls == ["feeds.example.test"], "looked up exactly once"
    assert dialled == [(PUBLIC, 443)], "the socket went to the vetted address"
    (line,) = egress_ledger.read(1)
    assert line["outcome"] == "failed" and line["service"] == "news_rss"


def test_a_fixed_door_is_resolved_once_by_the_connection_itself(
        monkeypatch, open_doors, ledger):
    dialled = _capture_dial(monkeypatch)
    flip = FlipResolver()
    with pytest.raises(Exception):
        egress_doors.egress_get("weather",
                                "https://api.open-meteo.com/v1/forecast?x=1",
                                resolver=flip)
    assert flip.calls == ["api.open-meteo.com"]
    assert dialled == [(PUBLIC, 443)]


def test_a_name_that_answers_private_at_connect_is_refused_and_logged(
        monkeypatch, open_doors, ledger):
    dialled = _capture_dial(monkeypatch)
    with pytest.raises(egress_doors.EgressBlocked) as exc:
        egress_doors.egress_get("weather", "https://api.open-meteo.com/v1/f",
                                resolver=lambda h, p: [PRIVATE])
    assert dialled == [], "no socket to an inward address"
    assert exc.value.code == "inside_address"
    (line,) = egress_ledger.read(1)
    assert line["outcome"] == "refused" and line["code"] == "inside_address"


def test_the_pinned_session_ignores_proxies_from_the_environment(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://10.0.0.1:3128")
    session = pinned_net.requests_session(pinned_net.Pin())
    assert session.trust_env is False and session.proxies == {}


def test_tls_still_names_the_host_while_dialling_the_ip(monkeypatch, open_doors,
                                                        ledger):
    """SNI carries the name: read it off the wire of a socketpair."""
    from urllib3.util import connection as u3conn
    ours, theirs = socket.socketpair()
    seen = {}

    def fake_create(address, *a, **k):
        seen["address"] = address
        return ours

    def read_hello():
        theirs.settimeout(5)
        try:
            seen["hello"] = theirs.recv(4096)
        finally:
            theirs.close()

    monkeypatch.setattr(u3conn, "create_connection", fake_create)
    # A socketpair's peer is loopback; the peer check is covered elsewhere.
    monkeypatch.setattr(pinned_net.Pin, "check_peer",
                        lambda self, host, sock, dialled: None)
    reader = threading.Thread(target=read_hello)
    reader.start()
    with pytest.raises(Exception):
        egress_doors.egress_get("weather", "https://api.open-meteo.com/v1/f",
                                resolver=lambda h, p: [PUBLIC], timeout=5)
    reader.join(10)
    assert seen["address"] == (PUBLIC, 443)
    assert b"api.open-meteo.com" in seen.get("hello", b""), "SNI is the name"


def test_a_socket_that_lands_somewhere_else_is_refused():
    pin = pinned_net.Pin(preset={"x.example": [PUBLIC]})

    class Sock:
        closed = False

        def getpeername(self):
            return (PRIVATE, 443)

        def close(self):
            Sock.closed = True

    with pytest.raises(pinned_net.PinRefused) as exc:
        pin.check_peer("x.example", Sock(), PUBLIC)
    assert exc.value.code == "peer_moved" and Sock.closed


# ── urllib path (books, library search, list refreshes) ──────────────────────

def test_the_urllib_opener_dials_the_vetted_address(monkeypatch):
    flip = FlipResolver()
    dialled = []

    def fake_create(address, timeout=None, source=None):
        dialled.append(address)
        raise OSError("test stops here")

    monkeypatch.setattr(pinned_net, "_system_resolver", flip)
    monkeypatch.setattr(pinned_net.socket, "create_connection", fake_create)
    opener = pinned_net.urllib_opener(pinned_net.Pin())
    with pytest.raises(OSError):
        opener.open("https://opds.library.kiwix.org/catalog", timeout=5)
    assert flip.calls == ["opds.library.kiwix.org"]
    assert dialled == [(PUBLIC, 443)]


def test_the_urllib_opener_refuses_a_private_answer(monkeypatch):
    dialled = []
    monkeypatch.setattr(pinned_net, "_system_resolver", lambda h, p: [PRIVATE])
    monkeypatch.setattr(pinned_net.socket, "create_connection",
                        lambda *a, **k: dialled.append(a) or None)
    opener = pinned_net.urllib_opener(pinned_net.Pin())
    with pytest.raises(OSError):
        opener.open("https://opds.library.kiwix.org/catalog", timeout=5)
    assert dialled == []


def test_plain_http_is_refused_by_the_opener():
    opener = pinned_net.urllib_opener(pinned_net.Pin())
    with pytest.raises(OSError):
        opener.open("http://example.org/", timeout=5)


def test_source_fetch_and_live_lists_use_the_pinned_opener():
    import inspect

    from puffpai import source_fetch
    from puffpai.catalogs import live
    # conftest replaces the transports in every test; read the real source.
    text = inspect.getsource(source_fetch)
    start = text.index("def _default_transport(")
    body = text[start:text.index("\ndef ", start + 10)]
    assert "pinned_net.urllib_opener" in body
    assert "source_fetch._default_transport" in inspect.getsource(live)
