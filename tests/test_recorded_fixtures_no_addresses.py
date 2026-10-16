"""Recorded fixtures hold no network addresses.

No file under ``tests/fixtures/recorded_web/`` may contain an IPv4 or IPv6 address
or an ``x-ratelimit-ip`` header. Candidates are confirmed with :mod:`ipaddress`
so clock times ("10:30:45") and CSS (``::before``) are not mistaken for one.
"""
from __future__ import annotations

import ipaddress
import os
import re

import pytest

PROBE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "recorded_web")
_V4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
_V6 = re.compile(r"(?<![\w:])[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7}(?![\w:])")


def addresses(text: str) -> list[str]:
    out = []
    for m in _V4.finditer(text):
        try:
            ipaddress.IPv4Address(m.group())
            out.append(m.group())
        except ValueError:
            pass
    for m in _V6.finditer(text):
        tok = m.group()
        if len(re.findall(r"[0-9A-Fa-f]{1,4}", tok)) < 2:
            continue
        try:
            ipaddress.IPv6Address(tok)
            out.append(tok)
        except ValueError:
            pass
    return out


def _files() -> list[str]:
    return sorted(os.path.join(PROBE, n) for n in os.listdir(PROBE))


def test_there_are_recorded_fixtures():
    assert len(_files()) >= 10


@pytest.mark.parametrize("path", _files(), ids=os.path.basename)
def test_no_address_and_no_ratelimit_ip_header(path):
    with open(path, "rb") as fh:
        text = fh.read().decode("utf-8", "replace")
    assert addresses(text) == []
    assert "x-ratelimit-ip" not in text.lower()


@pytest.mark.parametrize("text, found", [
    ("from 140.82.112.6 today", ["140.82.112.6"]), ("at 2001:db8::1 now", ["2001:db8::1"]),
    ("at 10:30:45", []), ("a::before{}", []), ("version 1.2.3", []), ("999.1.1.1", []),
])
def test_the_detector(text, found):
    assert addresses(text) == found


# Email addresses: the same rule as the tree-wide hygiene scan
# (tests/test_repo_hygiene_docs.py), so a recorded answer never carries a real
# person's or a real site's address. Reserved documentation domains are fine.
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RESERVED = re.compile(r"@(?:[A-Za-z0-9.-]+\.)?example\.(?:com|org|net)$|"
                       r"@[A-Za-z0-9.-]*\.(?:example|invalid|test|localhost|local)$", re.I)
CORPUS = os.path.join(os.path.dirname(PROBE), "classified_finds.json")


def emails(text: str) -> list[str]:
    return [h for h in _EMAIL.findall(text) if not _RESERVED.search(h)]


@pytest.mark.parametrize("path", _files() + [CORPUS], ids=os.path.basename)
def test_no_routable_email_address(path):
    with open(path, "rb") as fh:
        assert emails(fh.read().decode("utf-8", "replace")) == []


@pytest.mark.parametrize("text, found", [
    ("git" "@github.com:o/r.git", ["git" "@github.com"]), ("mail a" "@b.co now", ["a" "@b.co"]),
    ("git@example.invalid:o/r.git", []), ("you@example.com", []), ("x@site.test", []),
])
def test_the_email_detector(text, found):
    assert emails(text) == found
