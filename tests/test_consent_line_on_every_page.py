"""The consent line everywhere.

Never "nothing leaves your computer" on its own; the line
is "Nothing leaves your computer without your consent." In the wedding run
the sidebar and home said "12 outside services on" while the plan said
nothing leaves. The sidebar private-stuff card,
the home page and the Developer overview and Bridge pages now show the
consent line, and the same on-list and count as "What leaves your computer",
all read from the backend (privacy_explainer.consent_summary through
/system/privacy and /front). Nothing in the dashboard fixes a count.

No network, no model.
"""
from __future__ import annotations

import glob
import os
import re

import pytest

from test_plain_words_in_ui_strings import tsx_strings
from puffpai import api_server, bot_identity, front_page, outside_services
from puffpai.security import privacy_explainer as pe

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "dashboard", "app")
CONSENT = "Nothing leaves your computer without your consent."


def _read(*parts: str) -> str:
    with open(os.path.join(APP, *parts), encoding="utf-8") as fh:
        return fh.read()


ROWS = [
    {"id": "library", "name": "Book library", "state": "on",
     "websites": ["openlibrary.org"]},
    {"id": "weather", "name": "Weather and forecasts", "state": "off",
     "websites": ["api.open-meteo.com"]},
    {"id": "app_search", "name": "Looking for apps and code", "state": "on",
     "websites": ["api.github.com", "registry.npmjs.org"]},
    {"id": "pictures", "name": "Looking for pictures", "state": "on",
     "websites": ["commons.wikimedia.org"]},
]


@pytest.fixture
def three_on(monkeypatch):
    monkeypatch.setattr(outside_services, "rows", lambda: [dict(r) for r in ROWS])


@pytest.fixture
def none_on(monkeypatch):
    rows = [dict(r, state="off") for r in ROWS]
    monkeypatch.setattr(outside_services, "rows", lambda: rows)


@pytest.fixture
def unreadable(monkeypatch):
    def boom():
        raise RuntimeError("switches cannot be read")
    monkeypatch.setattr(outside_services, "rows", boom)


# -- the one backend summary --------------------------------------------------

def test_the_summary_names_what_is_on_in_the_list_order_and_words(three_on):
    got = pe.consent_summary()
    assert got["consent_line"] == CONSENT
    assert got["count"] == 3
    assert got["on"] == ["Book library", "Looking for apps and code",
                         "Looking for pictures"]
    assert got["line"] == ("What leaves your computer: only what you say yes "
                           "to. 3 outside services are on: Book library, "
                           "Looking for apps and code, Looking for pictures.")


def test_none_on_still_says_consent_not_nothing_leaves(none_on):
    got = pe.consent_summary()
    assert got["count"] == 0 and got["on"] == []
    assert "only what you say yes to" in got["line"]
    summary = " ".join(pe.plain_summary(None, vault=None, model_url=""))
    assert CONSENT in summary


def test_unreadable_switches_are_not_reported_as_zero(unreadable):
    got = pe.consent_summary()
    assert got["count"] is None and got["on"] == []
    assert got["consent_line"] == CONSENT


def test_the_privacy_route_carries_the_same_summary(three_on):
    api_server.app.config.update(TESTING=True)
    client = api_server.app.test_client()
    got = client.get("/system/privacy", headers={
        "Authorization": f"Bearer {api_server._API_KEY}"}).get_json()
    assert got["outside_services_on"] == 3
    assert got["consent"] == pe.consent_summary()


def test_home_reads_the_same_summary_and_count(three_on):
    front = front_page.front()
    summary = pe.consent_summary()
    assert front["outside"]["line"] == summary["line"]
    assert front["outside"]["consent_line"] == CONSENT
    assert front["outside"]["count"] == 3
    assert front["outside"]["on"] == summary["on"]
    assert "3 outside services on" in front["dateline"]


def test_home_with_none_on_makes_no_bare_claim(none_on):
    front = front_page.front()
    words = " ".join([front["dateline"], front["outside"]["line"]])
    assert "nothing goes online" not in words
    assert "stays on this computer" not in words
    assert front["outside"]["count"] == 0


def test_the_chat_greeting_carries_the_consent_line():
    for goal in ("I want a wedding planner bot.", None):
        words = bot_identity.greeting("WeddingBot", goal)
        assert CONSENT in words
        assert "stays on this computer" not in words


# -- the dashboard reads it, never fixes it ----------------------------------

PAGES = {
    "sidebar": ("dashboard", "_components", "AsideMenu", "index.tsx"),
    "developer": ("dashboard", "developer", "page.tsx"),
    "bridge": ("dashboard", "developer", "bridge", "page.tsx"),
}


@pytest.mark.parametrize("page", sorted(PAGES))
def test_each_surface_shows_the_backend_consent_line_and_list(page):
    src = _read(*PAGES[page])
    assert "consentLine(" in src and "data-consent-line" in src
    assert "onCount(" in src
    assert "Nothing leaves your computer" not in src  # the words come from the backend


def test_the_sidebar_and_bridge_name_what_is_on():
    for page in ("sidebar", "bridge"):
        assert "onNames(" in _read(*PAGES[page])


def test_the_developer_overview_counts_from_the_privacy_route_not_the_build():
    src = _read(*PAGES["developer"])
    assert "getPrivacy()" in src
    assert "open_doors" not in src


def test_the_home_page_shows_the_backend_consent_line():
    src = _read("_components", "Front", "FrontPage.tsx")
    assert "front.outside.consent_line" in src
    assert "data-outside-count" in src


def test_the_shared_words_hold_no_count_or_promise():
    src = _read("dashboard", "_lib", "consentWords.ts")
    assert not re.search(r"\b\d+ outside", src)
    assert "Nothing leaves" not in src
    assert "p?.consent?.consent_line" in src and "p?.consent?.count" in src


#: Owner text that claims nothing leaves, with no consent qualifier.
_BARE = re.compile(
    r"nothing (leaves|goes (out|online))|never leaves|stays off the internet|"
    r"everything we talk about stays|nothing was sent anywhere|"
    r"private: stays on this computer",
    re.IGNORECASE)


def _qualified(text: str) -> bool:
    return "without your consent" in text or "say yes to" in text


def test_no_dashboard_page_says_nothing_leaves_without_consent():
    """The sweep: every on-screen string in the dashboard."""
    found = []
    for path in glob.glob(os.path.join(APP, "**", "*.tsx"), recursive=True):
        for where, text in tsx_strings(path):
            if _BARE.search(text) and not _qualified(text):
                found.append((os.path.relpath(path, APP), where, text))
    # The vault's own pages say its contents stay on this computer, locked:
    # true of the vault and not a claim about what leaves.
    found = [f for f in found if not f[0].startswith(os.path.join("dashboard", "vault"))]
    assert found == []
