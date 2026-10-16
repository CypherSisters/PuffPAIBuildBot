"""The needs step's owner-facing strings follow the plain-words rules."""
from __future__ import annotations

import re
from pathlib import Path

from puffpai.catalogs import meaning
from puffpai.wizard import find_parts, needs

FILE = Path(__file__).parent / "fixtures" / "ui_strings" / "needs_and_find_parts.txt"
BANNED = re.compile(r"\b(?:doors?|beta|warts|sandbox|egress|repo|api|spdx|manifest|minilm|"
                    r"embedding|seccomp|bwrap|wsl2|little app)\b", re.I)


def _lines() -> list[str]:
    return [ln for ln in FILE.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_every_line_is_plain():
    for line in _lines():
        assert "—" not in line and "–" not in line, line
        assert not BANNED.search(line), line


def test_the_new_lines_are_listed():
    listed = set(_lines())
    for text in (find_parts.OWN_PICTURES, find_parts.OWN_PICTURES_DETAIL, find_parts.PALETTE,
                 find_parts.PALETTE_DETAIL, find_parts.STOCK_PICTURES_DETAIL,
                 find_parts.NOTE_LINE_NO_WHERE, needs.HELPER_READY, needs.HELPER_NOT_IN_BUILD,
                 needs.HELPER_NO_YES, needs.HELPER_FAILED, needs.HELPER_GETTING,
                 find_parts.NOTHING_FOUND, find_parts.LINE_CHECKING, meaning.NOTE_NOT_READ,
                 find_parts.LINE_SOURCE, find_parts.LINE_CHECKING_START,
                 find_parts.LINE_GITHUB_SPENT, find_parts.NOTES_LINE, meaning.MADE_FOR_ELSE,
                 *find_parts.NOTE_REASONS_MANY.values(), *find_parts.PLACE_NAMES.values()):
        assert text in listed, text
