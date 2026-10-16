"""A line marked ``# lan-egress:`` may only reach this computer or the home network.

The marker made the single-exit scanner skip a line, but
nothing checked where that line connected. caretaker's health probe dialled
whatever host was configured, public addresses included. Now:

* in the running app, :mod:`puffpai.security.lan_guard` (an audit hook on
  ``socket.connect``) refuses a marked call to an internet address before a
  packet is sent, and logs it in the app log and the outside-traffic log;
* in the test run, tests/conftest.py's guard fails a marked call to an
  internet address, the same as an unmarked one.

No test here sends a packet to the internet: the production check is driven
through ``sys.audit`` with the exact event the socket module raises, and the
test guard raises before the real connect.
"""
from __future__ import annotations

import linecache
import os
import socket
import sys
import types

import pytest

import conftest
from puffpai import outside_services, plain_words
from puffpai.security import egress_ledger, lan_guard


def _module_in_src(name: str, code: str) -> types.ModuleType:
    """A throwaway module whose code reads as if it lived in src/puffpai."""
    mod = types.ModuleType(name)
    path = os.path.join(conftest._SRC_ROOT, f"{name}.py")
    linecache.cache[path] = (len(code), None, code.splitlines(True), path)
    exec(compile(code, path, "exec"), mod.__dict__)
    return mod


_PROBES = _module_in_src("zz_lan_guard_probe", (
    "import sys\n"
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
    "def marked_above(addr):\n"
    "    # lan-egress: a call spread over lines\n"
    "    sys.audit(\n"
    "        'socket.connect', None, addr)\n"))


def test_the_running_app_installs_the_check():
    from puffpai import api_server  # noqa: F401 - importing it installs the hook
    assert lan_guard.installed()


@pytest.mark.parametrize("addr", [("8.8.8.8", 53), ("2606:4700:4700::1111", 443, 0, 0),
                                  ("203.0.113.9", 80), ("example.com", 80)])
def test_a_marked_call_to_the_internet_is_refused_and_logged(addr, caplog):
    lan_guard.install()
    before = len(egress_ledger.read(1.0))
    with pytest.raises(lan_guard.LanEgressRefused):
        _PROBES.marked(addr)
    with pytest.raises(lan_guard.LanEgressRefused):
        _PROBES.marked_above(addr)
    assert "Refused a home-network call" in caplog.text
    rows = egress_ledger.read(1.0)
    assert len(rows) == before + 2
    assert rows[0]["service"] == lan_guard.LEDGER_SERVICE
    assert rows[0]["outcome"] == "refused"
    assert rows[0]["code"] == lan_guard.LEDGER_CODE
    # And the owner reads it in plain words on "What leaves your computer".
    line = outside_services.log_rows(1.0)[0]["line"]
    assert "meant for your home network but was going to the internet" in line
    assert not plain_words.check(line)


@pytest.mark.parametrize("addr", [("127.0.0.1", 11435), ("::1", 8000, 0, 0),
                                  ("192.168.1.20", 8123), ("10.0.0.5", 11434),
                                  ("172.16.4.2", 5678), ("fe80::1", 80, 0, 3),
                                  ("fd12:3456::7", 8096, 0, 0),
                                  ("homeassistant.local", 8123), ("nas", 80)])
def test_a_marked_call_inside_the_house_goes_ahead(addr):
    lan_guard.install()
    _PROBES.marked(addr)          # no exception


def test_unmarked_calls_are_left_to_their_own_exit():
    lan_guard.install()
    _PROBES.unmarked(("8.8.8.8", 53))   # judged by the exits / the test guard


def test_a_real_socket_from_a_marked_line_is_refused_before_a_packet():
    """The hook acts on the socket.connect event, which comes before the
    syscall. The test-run guard's wrapper is bypassed here (``connect`` is a
    stand-in that raises the same event) so the production hook is what acts."""
    lan_guard.install()
    mod =_module_in_src("zz_lan_guard_real", (
        "import socket\n"
        "def go(connect):\n"
        "    s = socket.socket()\n"
        "    try:\n"
        "        return connect(s, ('198.51.100.7', 9))  # lan-egress: a home device\n"
        "    finally:\n"
        "        s.close()\n"))
    seen = []

    def _raw_connect(sock, address):
        # What socket.socket.connect does first: raise the audit event.
        sys.audit("socket.connect", sock, address)
        seen.append(address)       # would be the syscall: never reached
        return 0

    with pytest.raises(lan_guard.LanEgressRefused):
        mod.go(_raw_connect)
    assert seen == []


def test_the_test_run_fails_a_marked_call_to_the_internet():
    mod = _module_in_src("zz_lan_guard_testguard", (
        "import socket\n"
        "def go():\n"
        "    s = socket.socket()\n"
        "    try:\n"
        "        return s.connect_ex(('192.0.2.1', 9))  # lan-egress: says home, is not\n"
        "    finally:\n"
        "        s.close()\n"))
    before = len(conftest.OUTSIDE_EXIT_VIOLATIONS)
    with pytest.raises(conftest.OutsideExitViolation):
        mod.go()
    del conftest.OUTSIDE_EXIT_VIOLATIONS[before:]   # planted on purpose


def test_caretakers_probe_to_an_internet_host_is_stopped_not_sent():
    from puffpai.agents import caretaker
    before = len(conftest.OUTSIDE_EXIT_VIOLATIONS)
    with pytest.raises(conftest.OutsideExitViolation):
        caretaker._tcp_reachable("8.8.8.8", 53, timeout=0.01)
    del conftest.OUTSIDE_EXIT_VIOLATIONS[before:]
    # The production check, for the same line: refused, and the probe's own
    # error handling turns that into "not reachable".
    lan_guard.install()
    frame_mod = _module_in_src("zz_lan_guard_caretaker", (
        "import sys\n"
        "def go():\n"
        "    sys.audit('socket.connect', None, ('8.8.8.8', 53))  # lan-egress: probe\n"))
    with pytest.raises(OSError):
        frame_mod.go()


def test_every_marked_session_call_in_the_bridges_is_marked():
    """The Home Assistant and Jellyfin calls made through a session are marked,
    so the check covers them too (the marker used to sit only on the line
    that made the session)."""
    root = conftest._SRC_ROOT
    for rel, needles in (("ha_bridge.py", ("self._session.get(", "self._session.post(",
                                           "self._session.delete(")),
                         ("media_bridge.py", ("self._session.request(",))):
        text = open(os.path.join(root, rel), encoding="utf-8").read().splitlines()
        for needle in needles:
            lines = [ln for ln in text if needle in ln]
            assert lines and all(lan_guard.MARKER in ln for ln in lines), (rel, needle)
