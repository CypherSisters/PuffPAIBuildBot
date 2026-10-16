"""The single-exit rule is also watched at run time (tests/conftest.py).

A spelling scan cannot see every way to open a connection, so the suite also
watches what BuildBot's code actually does: a connection past this computer,
or a fetching program, from src/puffpai that did not come through a listed
exit or a line marked ``# lan-egress:`` raises and fails the run.
"""
from __future__ import annotations

import socket
import sys
import types

import pytest

import conftest


def _module_in_src(name: str, code: str) -> types.ModuleType:
    """A throwaway module whose code reads as if it lived in src/puffpai."""
    import os
    mod = types.ModuleType(name)
    import linecache
    path = os.path.join(conftest._SRC_ROOT, f"{name}.py")
    lines = code.splitlines(True)
    linecache.cache[path] = (len(code), None, lines, path)
    exec(compile(code, path, "exec"), mod.__dict__)
    return mod


def test_a_direct_connect_from_src_is_stopped():
    mod = _module_in_src("zz_rt_probe", (
        "import socket\n"
        "def go():\n"
        "    s = socket.socket()\n"
        "    try:\n"
        "        s.connect(('192.0.2.1', 9))\n"
        "    finally:\n"
        "        s.close()\n"))
    before = len(conftest.OUTSIDE_EXIT_VIOLATIONS)
    with pytest.raises(conftest.OutsideExitViolation):
        mod.go()
    # This one was planted on purpose: take it back out of the summary.
    del conftest.OUTSIDE_EXIT_VIOLATIONS[before:]


def test_a_fetching_program_from_src_is_stopped():
    mod = _module_in_src("zz_rt_probe2", (
        "import subprocess\n"
        "def go():\n"
        "    subprocess.run(['curl', 'https://example.org'])\n"))
    before = len(conftest.OUTSIDE_EXIT_VIOLATIONS)
    with pytest.raises(conftest.OutsideExitViolation):
        mod.go()
    del conftest.OUTSIDE_EXIT_VIOLATIONS[before:]


def test_this_computer_and_a_marked_lan_call_are_let_through():
    mod = _module_in_src("zz_rt_probe3", (
        "import socket\n"
        "def local():\n"
        "    s = socket.socket()\n"
        "    try:\n"
        "        return s.connect_ex(('127.0.0.1', 9))\n"
        "    finally:\n"
        "        s.close()\n"
        "def lan():\n"
        "    s = socket.socket()\n"
        "    s.settimeout(0.01)\n"
        "    try:\n"
        # A private (home-network) address. 192.0.2.1 used to stand in here,
        # but that is a documentation range, not a home address, and a marked
        # call to it is now refused (security.lan_guard).
        "        return s.connect_ex(('10.255.255.1', 9))  # lan-egress: a home device\n"
        "    finally:\n"
        "        s.close()\n"))
    mod.local()
    mod.lan()
