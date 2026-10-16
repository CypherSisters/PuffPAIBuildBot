"""Every shipped catalog snapshot and word list has a row in THIRD_PARTY_NOTICES.md."""
from __future__ import annotations

import os
import re

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SECTION = "## Catalog snapshots and word lists"


def _section() -> str:
    with open(os.path.join(ROOT, "THIRD_PARTY_NOTICES.md"), encoding="utf-8") as fh:
        text = fh.read()
    assert SECTION in text
    body = text.split(SECTION, 1)[1]
    return body.split("\n## ", 1)[0]


def _rows() -> list[list[str]]:
    rows = []
    for line in _section().splitlines():
        if line.startswith("| `"):
            rows.append([c.strip() for c in line.strip("|").split("|")])
    return rows


def test_every_snapshot_and_word_list_has_a_row():
    named = {re.sub(r"^`|`$", "", r[0]) for r in _rows()}
    snaps = [n for n in os.listdir(os.path.join(ROOT, "config", "catalog_snapshots"))
             if not n.endswith(".vec.npz")]
    words = os.listdir(os.path.join(ROOT, "config", "wordlists"))
    missing = [n for n in snaps if n not in named] + \
        [n for n in words if f"config/wordlists/{n}" not in named]
    assert missing == []


def test_every_row_names_a_source_licence_and_what_was_derived():
    for row in _rows():
        assert len(row) == 5, row
        name, source, licence, attribution, derived = row
        assert source and attribution and derived, name
        assert licence, name
        assert "http" in source or "written for this project" in source, name


def test_the_share_alike_snapshot_says_so():
    row = next(r for r in _rows() if r[0].startswith("`awesome_selfhosted_"))
    assert "CC BY-SA 3.0" in row[2] and "same licence" in row[4]


def test_no_em_dash_in_the_section():
    assert "—" not in _section()
