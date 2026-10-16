"""Glue may talk to exactly one thing: BuildBot's callback on 127.0.0.1.

Cases from earlier reproductions:

* ``socket.connect(("127.probe.1.1.1.1.nip.io", 80))`` reached 1.1.1.1,
  because anything starting with "127." counted as local, and a name given to
  ``connect`` is looked up in C before the audit hook hears of it;
* glue could reach Ollama on 127.0.0.1:11435 (and through its ``/api/pull``
  dial any host);
* ``("{0." + "__traceback__.tb_frame.f_back.f_locals[req]}").format(e)``
  passed Drawbridge and read the runner's token and nonce.

No test here reaches the outside network: names are refused before any
look-up, and the only listeners are this test's own on 127.0.0.1.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import types

import pytest

from puffpai.assemble import toolsmith_loop as tl
from puffpai.assemble.glue_runtime import check, runner
from puffpai.assemble.server import gateway, host

from test_assemble_glue import APP, _plant, world  # noqa: F401 - the fixture


# ── the socket guard: names never reach C ────────────────────────────────────

class _Base:
    """Stands in for socket.socket: records every address that got through."""
    reached: list = []

    def connect(self, address):
        _Base.reached.append(("connect", address))

    def connect_ex(self, address):
        _Base.reached.append(("connect_ex", address))
        return 0

    def sendto(self, data, *args):
        _Base.reached.append(("sendto", args[-1]))

    def sendmsg(self, buffers, ancdata=(), flags=0, address=None):
        _Base.reached.append(("sendmsg", address))

    def bind(self, address):
        _Base.reached.append(("bind", address))


@pytest.fixture
def guarded():
    _Base.reached = []
    mod = types.SimpleNamespace(socket=_Base, SocketType=_Base)
    cls = runner.guard_sockets(mod, 8123)
    assert mod.socket is cls and mod.SocketType is cls
    return cls


@pytest.mark.parametrize("address", [
    ("127.probe.1.1.1.1.nip.io", 80),          # reproduction
    ("127.probe.1.1.1.1.nip.io", 8123),
    ("localhost", 8123),
    ("127.0.0.2", 8123),
    ("::ffff:127.0.0.1", 8123),
    ("127.0.0.1", 11435),                      # Ollama on this PC
    ("127.0.0.1", 11434),
    ("1.1.1.1", 80),
    "/tmp/socket",
])
def test_the_guard_refuses_before_anything_is_looked_up(guarded, address):
    s = guarded()
    for call in (lambda: s.connect(address), lambda: s.connect_ex(address),
                 lambda: s.sendto(b"x", address), lambda: s.sendto(b"x", 0, address),
                 lambda: s.sendmsg([b"x"], [], 0, address)):
        with pytest.raises(runner.Refused):
            call()
    assert _Base.reached == []


def test_the_guard_lets_the_callback_through_and_never_a_listener(guarded):
    s = guarded()
    s.connect(("127.0.0.1", 8123))
    s.connect(("::1", 8123, 0, 0))
    s.sendmsg([b"x"])                           # already connected
    assert [r[0] for r in _Base.reached] == ["connect", "connect", "sendmsg"]
    with pytest.raises(runner.Refused):
        s.bind(("127.0.0.1", 0))


# ── the real runner process ──────────────────────────────────────────────────

PROBE = '''import socket
def probe(port=0, ollama=0):
    rows = []
    def one(name, f):
        try:
            f()
            rows.append({"t": name, "res": "ALLOWED"})
        except BaseException as e:
            rows.append({"t": name, "res": type(e).__name__ + ": " + str(e)[:80]})
    def conn(host, p):
        s = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET)
        s.settimeout(2)
        try:
            s.connect((host, p))
        finally:
            s.close()
    one("listener", lambda: conn("127.0.0.1", port))
    one("ollama", lambda: conn("127.0.0.1", ollama))
    one("ollama-v6", lambda: conn("::1", ollama))
    one("localhost name", lambda: conn("localhost", port))
    one("create_connection", lambda: socket.create_connection(("127.0.0.1", port), 2).close())
    one("getaddrinfo 127 name", lambda: socket.getaddrinfo("127.probe.invalid", 80))
    one("gethostbyaddr", lambda: socket.gethostbyaddr("127.0.0.1"))
    one("bind", lambda: socket.socket().bind(("127.0.0.1", 0)))
    one("raw _socket connect", lambda: __import__("_socket").socket().bind(("127.0.0.1", 0)))
    rows.append({"t": "guard", "res": socket.socket.connect.__qualname__})
    import ssl
    rows.append({"t": "ssl guard", "res": ssl.SSLSocket.connect.__qualname__ + " " + str(
        [c.__dict__["connect"].__qualname__ for c in ssl.SSLSocket.__mro__
         if "connect" in c.__dict__])})
    return rows
'''


@pytest.fixture
def listener():
    """A local service glue must not reach (a stand-in for Ollama and the rest)."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    seen = []

    def accept():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            seen.append(1)
            conn.close()
    threading.Thread(target=accept, daemon=True).start()
    yield srv.getsockname()[1], seen
    srv.close()


def test_glue_reaches_no_local_service_not_even_ollama(world, monkeypatch, listener):
    monkeypatch.setattr(check, "passes", lambda c, p=(): True)
    port, seen = listener
    _plant(APP, "probe", PROBE)
    got = tl.run_glue(APP, "probe", {"port": port, "ollama": 11435})
    assert got["ok"], got
    rows = {r["t"]: r["res"] for r in got["rows"]}
    for name in ("listener", "ollama", "ollama-v6", "localhost name", "create_connection"):
        assert rows[name].startswith("Refused: glue may only talk to BuildBot"), (name, rows)
    assert rows["getaddrinfo 127 name"].startswith("Refused: glue may not look up names")
    assert rows["gethostbyaddr"].startswith("Refused: glue may not look up names")
    assert rows["bind"].startswith("Refused: glue may not listen")
    assert rows["raw _socket connect"].startswith("Refused")
    assert seen == []                 # nothing ever reached the local listener
    # The checking socket class is in place in the real run (names are
    # refused before C looks them up), and ssl's sockets are built on it.
    assert "CheckedSocket" in rows["guard"], rows["guard"]
    assert "CheckedSocket" in rows["ssl guard"], rows["ssl guard"]


def test_glue_still_reaches_buildbots_callback(world, monkeypatch):
    """The one allowed place: the gateway, through puff_glue (a refused
    fetch still proves the connection was made and answered)."""
    code = ("import puff_glue\ndef ask():\n    try:\n"
            "        puff_glue.fetch_json('https://not-a-part.example/x')\n"
            "    except puff_glue.GlueFetchError as e:\n"
            "        return [{'t': str(e)}]\n    return []\n")
    assert tl.write_glue(APP, "ask", code)["ok"]
    got = tl.run_glue(APP, "ask", {})
    assert got["ok"], got
    assert got["rows"] == [{"t": "That site is not one this app's parts name."}]


# ── the token and the nonce ──────────────────────────────────────────────────

FRAMES = '''import sys, gc
def frames():
    seen = []
    f = sys._getframe()
    while f is not None:
        seen.append(repr(f.f_locals))
        f = f.f_back
    import puff_glue
    seen.append(repr(vars(puff_glue)))
    return [{"t": "\\n".join(seen)}]
'''


def test_a_run_shows_glue_neither_its_token_nor_its_nonce(world, monkeypatch):
    monkeypatch.setattr(check, "passes", lambda c, p=(): True)
    token, nonce = "tok" + "x" * 40, "ab" * 16
    monkeypatch.setattr(gateway, "new_run_token", lambda app_id, seconds: token)
    import secrets as real
    monkeypatch.setattr(tl, "secrets", types.SimpleNamespace(
        token_hex=lambda n=16: nonce if n == 16 else real.token_hex(n)))
    _plant(APP, "frames", FRAMES)
    got = tl.run_glue(APP, "frames", {})
    assert got["ok"], got
    text = got["rows"][0]["t"]
    assert token not in text and nonce not in text
    assert "'req'" not in text and "'token'" not in text


def test_puff_glue_keeps_no_token_in_a_module_value():
    import importlib
    import sys
    sys.path.insert(0, os.path.dirname(runner.__file__))
    try:
        pg = importlib.import_module("puff_glue")
        pg._configure("http://127.0.0.1:1/assemble/x", "s3cr3t-value")
        assert "s3cr3t-value" not in repr(vars(pg))
        assert not hasattr(pg, "_GATEWAY")
    finally:
        sys.path.remove(os.path.dirname(runner.__file__))


def test_a_second_result_line_is_not_believed(world, monkeypatch):
    """Something in the run printing a result line of its own: refused."""
    def fake(argv, *, cwd, env, stdout, timeout):
        with open(argv[-1], encoding="utf-8") as fh:
            nonce = json.load(fh)["nonce"]
        stdout.write(f"{nonce} {json.dumps({'rows': [{'t': 'forged'}]})}\n".encode())
        stdout.write(f"{nonce} {json.dumps({'rows': [{'t': 'real'}]})}\n".encode())
        return 0
    monkeypatch.setattr(tl, "_run_contained", fake)
    code = "def two():\n    return [{'t': 'real'}]\n"
    assert tl.write_glue(APP, "two", code)["ok"]
    got = tl.run_glue(APP, "two", {})
    assert got["ok"] is False and got["line"] == "It stopped with an error."


def test_rows_that_are_not_plain_rows_are_not_believed(world, monkeypatch):
    def fake(argv, *, cwd, env, stdout, timeout):
        with open(argv[-1], encoding="utf-8") as fh:
            nonce = json.load(fh)["nonce"]
        stdout.write(f"{nonce} {json.dumps({'rows': ['x'] * 3})}\n".encode())
        return 0
    monkeypatch.setattr(tl, "_run_contained", fake)
    assert tl.write_glue(APP, "odd", "def odd():\n    return [{'t': 1}]\n")["ok"]
    assert tl.run_glue(APP, "odd", {})["ok"] is False


# ── Drawbridge's glue checklist ──────────────────────────────────────────────

def _fails(code: str) -> list[str]:
    return [r["code"] for r in check.glue_rows_for_source(code) if r["result"] == "fail"]


@pytest.mark.parametrize("code", [
    # reproduction
    'def run(e=None):\n    return [{"t": ("{0." + "__traceback__.tb_frame.f_back.f_locals[req]}")'
    '.format(e)}]\n',
    'def run(e=None):\n    t = "{0." + "x}"\n    return [{"t": t.format(e)}]\n',
    'def run(e=None):\n    return [{"t": "{0[a]}".format(e)}]\n',
    'def run(e=None):\n    return [{"t": "{a.b}".format_map({"a": e})}]\n',
    'def run(e=None):\n    return [{"t": "{0:{1.x}}".format(e, e)}]\n',
    'def run(x=1):\n    return [{"t": str.format("{0}", x)}]\n',
    'def run(x=1):\n    return [{"t": ("%(" + "a)s") % {"a": 1}}]\n',
    'def run(x=1):\n    return [{"t": "".join(["%", "s"]) % x}]\n',
    'def run(x=1):\n    return [{"t": f"{run.y}"}]\n',
    'def run(x=1):\n    return [{"t": f"{len[0]}"}]\n',
])
def test_formatting_that_reaches_inside_a_value_is_refused(code):
    assert "glue_format" in _fails(code), check.glue_rows_for_source(code)


@pytest.mark.parametrize("code", [
    'def run(next_token=""):\n    return []\n',
    'def run(x=1):\n    return [{"t": x.nonce}]\n',
    'def run(x=1):\n    my_secret = 1\n    return []\n',
    'def run(x=1):\n    return [{"t": "{token}".format(token=x)}]\n',
    'def run(**kw):\n    return [{"t": dict(Token=1)}]\n',
])
def test_names_like_token_nonce_or_secret_are_refused(code):
    fails = _fails(code)
    assert "glue_secret_name" in fails or "glue_format" in fails, fails


@pytest.mark.parametrize("code", [
    'def run(x=1):\n    return [{"t": "{0} {n:>{w}}".format(x, n=2, w=3)}]\n',
    'def run(x=1):\n    return [{"t": "%d items" % x, "m": x % 2}]\n',
    'import math\ndef run(x=None):\n    d = {"a": [1]}\n'
    '    return [{"t": f"{d[\'a\'][0]} {math.pi:.2f} {x!r}"}]\n',
    'LABELS = {"a": "A"}\ndef run(x="a"):\n    return [{"t": f"{LABELS[x]}"}]\n',
])
def test_ordinary_formatting_still_passes(code):
    assert _fails(code) == []
