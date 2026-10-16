"""A local-use NAT64 address is judged by every IPv4 address it may carry.

The local-use NAT64 range
``64:ff9b:1::/48`` (RFC 8215) was read only in the RFC 6052 /48 layout.
``64:ff9b:1::808:808`` -- 8.8.8.8 in the /96 layout -- read as 0.0.0.0 in the
/48 one, was classed as this computer, and a call marked "home network" was
let through to dial it.

The range may be used with any RFC 6052 prefix length from /48 on, and an
address does not say which, so the /48, /56, /64 and /96 layouts are all read
and the worst verdict wins: if any of them is an internet address, a marked
call is refused. No socket is opened here (the probe only raises the audit
event a connect would).
"""
from __future__ import annotations

import ipaddress
import os
import types

import pytest

import conftest
from puffpai.security import lan_guard, lan_posture, pinned_net
from puffpai import source_fetch

BASE = int(ipaddress.IPv6Address("64:ff9b:1::"))


def _v4(text: str) -> int:
    return int(ipaddress.IPv4Address(text))


def at48(v4: str) -> str:
    v = _v4(v4)
    return str(ipaddress.IPv6Address(BASE | ((v >> 16) << 64) | ((v & 0xFFFF) << 40)))


def at56(v4: str) -> str:
    v = _v4(v4)
    return str(ipaddress.IPv6Address(BASE | ((v >> 24) << 64) | ((v & 0xFFFFFF) << 32)))


def at64(v4: str) -> str:
    return str(ipaddress.IPv6Address(BASE | (_v4(v4) << 24)))


def at96(v4: str) -> str:
    return str(ipaddress.IPv6Address(BASE | _v4(v4)))


def _module_in_src(name: str, code: str) -> types.ModuleType:
    import linecache
    mod = types.ModuleType(name)
    path = os.path.join(conftest._SRC_ROOT, f"{name}.py")
    linecache.cache[path] = (len(code), None, code.splitlines(True), path)
    exec(compile(code, path, "exec"), mod.__dict__)
    return mod


_PROBES = _module_in_src("zz_lan_guard_probe_nat64_local", (
    "import sys\n"
    "def marked(addr):\n"
    "    sys.audit('socket.connect', None, addr)  # lan-egress: a home service\n"))


@pytest.fixture(autouse=True)
def _guard():
    lan_guard.install()


def test_the_layout_helpers_agree_with_the_verifier():
    assert at96("8.8.8.8") == "64:ff9b:1::808:808"
    assert at48("8.8.8.8") == "64:ff9b:1:808:8:800::"


@pytest.mark.parametrize("address", [
    "64:ff9b:1::808:808",                     # the address that was misread
    at96("8.8.8.8"), at48("8.8.8.8"), at56("8.8.8.8"), at64("8.8.8.8"),
    at96("1.1.1.1"), at48("1.1.1.1"), at56("1.1.1.1"), at64("1.1.1.1"),
    at96("203.0.113.5"),                      # documentation range: not home
    "64:ff9b:1::cb00:7105",
    # Home in the /48 layout (192.168.1.1) but 168.1.1.0 in the /56 one: the
    # address does not say which layout it is, so it is not trusted as home.
    at48("192.168.1.1"),
])
def test_an_internet_address_in_any_layout_is_public(address):
    assert lan_posture._classify_address(address) == lan_posture.PUBLIC
    assert lan_guard.classify((address, 443)) == lan_posture.PUBLIC
    assert not lan_guard.is_home((address, 443))


@pytest.mark.parametrize("address", [
    "64:ff9b:1::808:808", at48("8.8.8.8"), at56("8.8.8.8"), at64("8.8.8.8"),
    at96("1.1.1.1"),
])
def test_a_marked_call_to_it_is_refused_before_any_packet(address):
    with pytest.raises(lan_guard.LanEgressRefused):
        _PROBES.marked((address, 443, 0, 0))


@pytest.mark.parametrize("address,want", [
    ("64:ff9b:1::", lan_posture.LOOPBACK),                 # all zero
    (at96("192.168.1.1"), lan_posture.PRIVATE),
    (at96("10.0.0.5"), lan_posture.PRIVATE),
])
def test_a_home_address_in_it_is_still_home(address, want):
    assert lan_posture._classify_address(address) == want
    _PROBES.marked((address, 443, 0, 0))   # not refused


@pytest.mark.parametrize("address", ["64:ff9b:1::808:808", at48("8.8.8.8")])
def test_the_internet_exits_never_dial_a_local_use_nat64_address(address):
    """The other direction: the exits that must reach only the internet
    refuse the local-use range whatever it carries (it is not public)."""
    assert pinned_net.address_refusal(address)
    assert not source_fetch.address_is_public(address)


def test_the_well_known_prefix_is_unchanged():
    assert lan_posture._classify_address("64:ff9b::808:808") == lan_posture.PUBLIC
    assert lan_posture._classify_address("64:ff9b::c0a8:101") == lan_posture.PRIVATE
