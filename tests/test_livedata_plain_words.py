"""The live-card screens use plain words.

Scans the whole of each new dashboard file, comments included, because a
banned word in a comment is one copy-paste away from a label.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from puffpai import plain_words

DASH = Path(__file__).resolve().parents[1] / "dashboard" / "app"
FILES = [
    DASH / "_components" / "Feed" / "FeedCardBody.tsx",
    DASH / "_components" / "Widgets" / "AppFeedWidget.tsx",
    DASH / "dashboard" / "apps" / "_components" / "FeedCard.tsx",
    DASH / "dashboard" / "apps" / "_components" / "FeedDraftCard.tsx",
    DASH / "_lib" / "services" / "feedService.ts",
]


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_no_banned_words_on_the_live_card_screens(path):
    text = path.read_text(encoding="utf-8")
    # "index" and "json" are ordinary code in a .tsx file (a loop variable,
    # res.json()); every other owner word is banned even in comments.
    found = plain_words.words_in(text, skip=("index", "json"))
    assert not found, f"{path.name} uses {found}"


def test_the_browser_never_builds_an_outside_address():
    for path in FILES:
        text = path.read_text(encoding="utf-8")
        assert "https://" not in text and "http://" not in text, path.name
