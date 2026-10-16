"""The box's outside proxy: its address and name rules.
Pure functions of ``box/assets/egress_proxy.py``; no
sockets are opened here."""

from __future__ import annotations

import importlib.util
import os
import struct

import pytest

from puffpai.box import driver

_spec = importlib.util.spec_from_file_location(
    "l7_egress_proxy", os.path.join(os.path.dirname(driver.__file__), "assets", "egress_proxy.py"))
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)


@pytest.mark.parametrize("host", ["1.1.1.1", "2130706433", "0x7f.1", "0177.0.0.1", "127.1",
                                  "[::1]", "::1", "10.0.0.1", "0x7f000001", "1.2.3.4.",
                                  "123.456"])
def test_ip_literals_in_every_spelling_are_ip_literals(host):
    assert P.looks_like_ip(host)


@pytest.mark.parametrize("host", ["registry.npmjs.org", "files.pythonhosted.org", "localtest.me",
                                  "a1.example.com", "xn--bcher-kva.example"])
def test_names_are_not_ip_literals(host):
    assert not P.looks_like_ip(host)


@pytest.mark.parametrize("addr", ["8.8.8.8", "1.1.1.1", "104.16.0.1", "2606:4700::1111"])
def test_public_addresses(addr):
    assert P.is_public(addr)


@pytest.mark.parametrize("addr", [
    "127.0.0.1", "10.1.2.3", "172.16.0.1", "172.20.10.1", "192.168.1.1", "169.254.169.254",
    "100.64.0.1", "100.100.100.100", "0.0.0.0", "224.0.0.1", "240.0.0.1", "198.18.0.1",
    "192.0.0.1", "::1", "::", "fc00::1", "fd12::1", "fe80::1", "ff02::1", "64:ff9b::7f00:1",
    "2002:7f00:1::", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "fe80::1%eth0", "not-an-ip",
    "10.255.255.254"])
def test_inside_and_special_addresses_are_not_public(addr):
    assert not P.is_public(addr)


def _client_hello(sni: str | None) -> bytes:
    exts = b""
    if sni is not None:
        name = sni.encode("ascii")
        entry = b"\x00" + struct.pack(">H", len(name)) + name
        lst = struct.pack(">H", len(entry)) + entry
        exts += struct.pack(">HH", 0, len(lst)) + lst
    exts += struct.pack(">HH", 0x000b, 2) + b"\x01\x00"         # another extension after
    body = (b"\x03\x03" + b"\x11" * 32 + b"\x00" + struct.pack(">H", 2) + b"\x13\x01"
            + b"\x01\x00" + struct.pack(">H", len(exts)) + exts)
    hs = b"\x01" + len(body).to_bytes(3, "big") + body
    return b"\x16\x03\x01" + struct.pack(">H", len(hs)) + hs


def test_sni_is_read_from_a_client_hello():
    assert P.sni_of(_client_hello("registry.npmjs.org")) == "registry.npmjs.org"
    assert P.sni_of(_client_hello("Registry.NPMJS.org.")) == "registry.npmjs.org"


@pytest.mark.parametrize("record", [
    b"", b"\x16", b"GET / HTTP/1.1\r\n\r\n", b"\x17\x03\x03\x00\x05hello",
    _client_hello(None), _client_hello("x")[:40], b"\x16\x03\x01\xff\xff\x01" + b"\x00" * 10,
])
def test_no_sni_or_garbage_gives_none(record):
    assert P.sni_of(record) is None


def test_the_log_line_has_the_ledger_shape(tmp_path):
    px = P.Proxy({("registry.npmjs.org", 443)}, str(tmp_path / "o.jsonl"),
                 "box_" + "a" * 24, "box_install", 10)
    px.log("registry.npmjs.org", "done", "", 10, 5)
    px.log("evil host!", "refused", "not_on_list")
    lines = (tmp_path / "o.jsonl").read_text(encoding="utf-8").splitlines()
    import json
    first, second = (json.loads(x) for x in lines)
    assert set(first) == {"t", "service", "host", "path", "method", "outcome", "code", "status",
                          "bytes_in", "bytes_out", "what"}
    assert first["host"] == "registry.npmjs.org" and first["what"] == "box_install"
    assert second["host"] == ""          # a host that is not a clean name is never logged
    # The Windows side re-checks every line before the owner sees it.
    assert driver.ingest_outside([first, second]) == 2


# -- the proxy as a network exit ---------------------------------------------------
# The single-exit scan (tests/test_outside_single_exit.py) sees the
# proxy's one upstream connection. These tests hold the proxy to what a listed
# exit must do: connect only to the address it checked (pinned, never by name),
# refuse before connecting, and write one outside-log line for every attempt.
# Fakes only: no socket is opened.

import asyncio
import json

_SERVICE = "box_" + "a" * 24
_HOST = "registry.npmjs.org"


class _Writer:
    def __init__(self):
        self.sent = b""
        self.closed = False

    def write(self, data):
        self.sent += data

    async def drain(self):
        return None

    def close(self):
        self.closed = True


def _run_handle(tmp_path, monkeypatch, *, request: bytes, answers, allow=((_HOST, 443),),
                client=None, reader=None):
    """Drive one client through Proxy.handle. Returns (log lines, dials, client writer)."""
    dials = []
    lookups = []
    upstream = _Writer()

    async def fake_open_connection(host, port, *a, **k):
        dials.append((host, port))
        r = asyncio.StreamReader()
        r.feed_eof()
        return r, upstream

    async def fake_getaddrinfo(host, port, *a, **k):
        lookups.append(host)
        return [(2, 1, 6, "", (addr, port)) for addr in answers]

    monkeypatch.setattr(P.asyncio, "open_connection", fake_open_connection)
    log = tmp_path / "outside.jsonl"
    px = P.Proxy(set(allow), str(log), _SERVICE, "box_traffic", 10 ** 6)
    client = client if client is not None else _Writer()

    async def go():
        asyncio.get_running_loop().getaddrinfo = fake_getaddrinfo
        rd = reader() if reader is not None else asyncio.StreamReader()
        rd.feed_data(request)
        if reader is None:
            rd.feed_eof()
        await px.handle(rd, client)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(go())
    finally:
        loop.close()
    px.fh.close()
    lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    return lines, dials, lookups, client, upstream


def test_the_proxy_connects_only_to_the_address_it_checked(tmp_path, monkeypatch):
    hello = _client_hello(_HOST)
    lines, dials, lookups, client, upstream = _run_handle(
        tmp_path, monkeypatch, request=f"CONNECT {_HOST}:443 HTTP/1.1\r\n\r\n".encode() + hello,
        answers=["104.16.0.1"])
    assert lookups == [_HOST]                      # one lookup, by the proxy itself
    assert dials == [("104.16.0.1", 443)]          # pinned: the vetted address, never the name
    assert upstream.sent.startswith(hello)
    assert len(lines) == 1 and lines[0]["outcome"] == "done"
    assert lines[0]["host"] == _HOST and lines[0]["bytes_out"] == len(hello)
    assert driver.ingest_outside(lines) == 1       # and the owner's log accepts it


def test_a_name_with_any_inside_answer_is_refused_before_connecting(tmp_path, monkeypatch):
    lines, dials, _, client, _ = _run_handle(
        tmp_path, monkeypatch, request=f"CONNECT {_HOST}:443 HTTP/1.1\r\n\r\n".encode(),
        answers=["104.16.0.1", "10.0.0.5"])
    assert dials == []
    assert b"403" in client.sent
    assert [(x["outcome"], x["code"]) for x in lines] == [("refused", "inside_address")]


@pytest.mark.parametrize("request_line,code", [
    (b"CONNECT evil.example:443 HTTP/1.1\r\n\r\n", "not_on_list"),
    (f"CONNECT {_HOST}:8443 HTTP/1.1\r\n\r\n".encode(), "not_on_list"),
    (b"CONNECT 104.16.0.1:443 HTTP/1.1\r\n\r\n", "not_on_list"),
    (b"CONNECT 127.1:443 HTTP/1.1\r\n\r\n", "inside_address"),
    (f"GET http://{_HOST}/ HTTP/1.1\r\n\r\n".encode(), "not_encrypted"),
])
def test_a_refusal_never_looks_up_or_connects_and_is_logged(tmp_path, monkeypatch,
                                                           request_line, code):
    lines, dials, lookups, client, _ = _run_handle(
        tmp_path, monkeypatch, request=request_line, answers=["104.16.0.1"])
    assert dials == [] and lookups == []
    assert [(x["outcome"], x["code"]) for x in lines] == [("refused", code)]


def test_a_wrong_sni_is_cut_after_connecting_and_logged(tmp_path, monkeypatch):
    lines, dials, _, _, upstream = _run_handle(
        tmp_path, monkeypatch,
        request=f"CONNECT {_HOST}:443 HTTP/1.1\r\n\r\n".encode() + _client_hello("evil.example"),
        answers=["104.16.0.1"])
    assert dials == [("104.16.0.1", 443)]
    assert upstream.sent == b"" and upstream.closed   # nothing of the client's reaches it
    assert [(x["outcome"], x["code"]) for x in lines] == [("refused", "not_allowed")]


class _GoneWriter(_Writer):
    """A client that went away right after asking to connect."""

    async def drain(self):
        raise ConnectionResetError("client went away")


class _BrokenReader(asyncio.StreamReader):
    """A client whose first read after the tunnel opens fails."""

    async def read(self, n=-1):
        raise ConnectionResetError("client went away")


def test_a_client_gone_right_after_connect_is_still_logged_and_upstream_closed(
        tmp_path, monkeypatch):
    lines, dials, _, client, upstream = _run_handle(
        tmp_path, monkeypatch, request=f"CONNECT {_HOST}:443 HTTP/1.1\r\n\r\n".encode(),
        answers=["104.16.0.1"], client=_GoneWriter())
    assert dials == [("104.16.0.1", 443)]           # a real dial happened ...
    assert upstream.sent == b"" and upstream.closed  # ... nothing reached it, and it is closed
    assert client.closed
    assert [(x["outcome"], x["code"]) for x in lines] == [("failed", "no_answer")]
    assert driver.ingest_outside(lines) == 1        # and the owner's log accepts it


def test_a_client_read_error_after_connect_is_logged_once(tmp_path, monkeypatch):
    lines, dials, _, _, upstream = _run_handle(
        tmp_path, monkeypatch, request=f"CONNECT {_HOST}:443 HTTP/1.1\r\n\r\n".encode(),
        answers=["104.16.0.1"], reader=_BrokenReader)
    assert dials == [("104.16.0.1", 443)]
    assert upstream.sent == b"" and upstream.closed
    assert [(x["outcome"], x["code"]) for x in lines] == [("refused", "not_encrypted")]


def test_a_proxy_stopped_mid_tunnel_still_logs_the_visit(tmp_path, monkeypatch):
    """Stopping the proxy cancels a live tunnel; the visit is still one line."""
    hello = _client_hello(_HOST)
    upstream = _Writer()

    async def fake_open_connection(host, port, *a, **k):
        return asyncio.StreamReader(), upstream       # never answers, never ends

    async def fake_getaddrinfo(host, port, *a, **k):
        return [(2, 1, 6, "", ("104.16.0.1", port))]

    monkeypatch.setattr(P.asyncio, "open_connection", fake_open_connection)
    log = tmp_path / "outside.jsonl"
    px = P.Proxy({(_HOST, 443)}, str(log), _SERVICE, "box_traffic", 10 ** 6)

    async def go():
        asyncio.get_running_loop().getaddrinfo = fake_getaddrinfo
        rd = asyncio.StreamReader()
        rd.feed_data(f"CONNECT {_HOST}:443 HTTP/1.1\r\n\r\n".encode() + hello)
        task = asyncio.ensure_future(px.handle(rd, _Writer()))
        for _ in range(20):
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(go())
    finally:
        loop.close()
    px.fh.close()
    lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    assert upstream.sent.startswith(hello) and upstream.closed
    assert len(lines) == 1 and lines[0]["outcome"] == "failed"
    assert lines[0]["bytes_out"] == len(hello)


def test_the_proxy_does_not_start_when_its_log_cannot_be_written(tmp_path):
    with pytest.raises(OSError):
        P.Proxy({(_HOST, 443)}, str(tmp_path / "no-such-dir" / "o.jsonl"), _SERVICE,
                "box_traffic", 10)


def test_the_proxy_is_the_only_connection_in_the_box_code():
    """The box's code stays under the single-exit scan: its only connection is
    the proxy's one dial of a vetted address (the line the scan owner lists)."""
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location(
        "l7_single_exit_scan", os.path.join(here, "test_outside_single_exit.py"))
    scan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scan)
    found = [o for o in scan._offenders() + scan._ast_offenders()
             if o.startswith(("box/", "box_cli.py", "remove_cli.py",
                              "security/owner_folders.py"))]
    proxy_path = os.path.join(os.path.dirname(driver.__file__), "assets", "egress_proxy.py")
    proxy_lines = open(proxy_path, encoding="utf-8").read().splitlines()
    # Read the proxy with the scan's own patterns whether or not it is listed:
    # its one dial is the only connection it opens.
    dials = [n for n, line in enumerate(proxy_lines, 1)
             if scan._CONNECT_RE.search(line.split("#", 1)[0])]
    assert dials and all("open_connection(a, port)" in proxy_lines[n - 1] for n in dials), dials
    if "box/assets/egress_proxy.py" in scan.EXITS:
        # The scan owner listed the proxy as an exit: then
        # nothing else in the box code may open a connection at all.
        assert found == [], found
        return
    assert found, "the scan no longer sees the proxy's dial"
    assert all(o.startswith("box/assets/egress_proxy.py:") for o in found), found
    assert {o.split(":")[1] for o in found} == {
        str(n) for n, line in enumerate(open(os.path.join(
            os.path.dirname(driver.__file__), "assets", "egress_proxy.py"),
            encoding="utf-8").read().splitlines(), 1)
        if "open_connection(a, port)" in line}
