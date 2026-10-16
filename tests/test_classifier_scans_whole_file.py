"""A big file is scanned to its last line: a detail past the first chunk is
still found, kept out of the searchable copy, and held in the vault."""

from __future__ import annotations

import pytest

from puffpai.ingestion import screen_text_for_pii
from puffpai.security import data_classifier as dc
from puffpai.security.data_classifier import DataClassifier
from puffpai.security.vault import Vault

LINE = "The quick brown fox jumps over the lazy dog. Notes about tomatoes.\n"
EMAIL = "jane.doe@example.com"
SSN = "123-45-6789"


def _filler(chars: int) -> str:
    return (LINE * (chars // len(LINE) + 1))[:chars]


def test_detail_far_past_first_chunk_is_found():
    text = _filler(dc._SCAN_CHUNK * 2 + 1234) + (
        f"\nMy email is {EMAIL} and my SSN is {SSN}\n")
    found = DataClassifier().scan_for_pii(text)
    assert EMAIL in found.emails
    assert SSN in found.ssns


def test_redacted_copy_has_nothing_from_the_tail():
    text = _filler(700_000) + f"\nMy email is {EMAIL} and my SSN is {SSN}\n"
    red, found = DataClassifier().redact_pii(text)
    assert found.has_pii
    assert EMAIL not in red and SSN not in red


@pytest.mark.parametrize("sep", ["\n", " "])
def test_value_straddling_a_cut_is_found_whole(sep):
    # No line breaks near the cut forces a space cut; then the value sits
    # right across the 500,000 mark either way.
    words = "word " if sep == " " else LINE
    head = (words * (dc._SCAN_CHUNK // len(words) + 1))[:dc._SCAN_CHUNK - 10]
    text = head + sep + f"{EMAIL}{sep}" + _filler(5000)
    assert text.index(EMAIL) < dc._SCAN_CHUNK < text.index(EMAIL) + len(EMAIL)
    found = DataClassifier().scan_for_pii(text)
    assert found.emails == [EMAIL]


def test_chunks_cover_every_character_and_stay_bounded():
    # Every line is unique, so each piece's place in the text is exact.
    text = "".join(f"line {i:08d}\n" for i in range(130_000))
    assert len(text) > dc._SCAN_CHUNK * 3
    spans = dc._chunks(text)
    assert len(spans) >= 4
    assert all(hi - lo <= dc._SCAN_CHUNK for lo, hi in spans)
    covered_to = 0
    for lo, hi in spans:
        assert lo <= covered_to, "gap between chunks"
        if covered_to:
            assert covered_to - lo >= dc._SCAN_OVERLAP // 2, "overlap"
        covered_to = hi
    assert covered_to == len(text)


def test_chunks_never_split_a_marker():
    marker = dc.REDACTION_MARKERS["password"]
    head = "x" * (dc._SCAN_CHUNK - 5)
    text = head + marker + "y" * 1000
    for lo, hi in dc._chunks(text):
        p = text[lo:hi]
        # Either the whole marker or none of it.
        assert (marker in p) or ("kept private]" not in p
                                 and "[password" not in p)


FULL = "alice.smith@example.com"


@pytest.mark.parametrize("at", [
    # No space or line break anywhere: the cut is hard at 500,000 and the
    # next chunk starts at 495,904, ten characters into the address.
    dc._SCAN_CHUNK - dc._SCAN_OVERLAP - 10,
    # The hard cut itself falls one character before the address ends.
    dc._SCAN_CHUNK - len(FULL) + 1,
])
def test_no_half_value_at_a_hard_cut(at):
    text = "x" * (at - 1) + "," + FULL + "," + "y" * 20_000
    assert text.index(FULL) == at
    assert not any(c.isspace() for c in text)
    found = DataClassifier().scan_for_pii(text)
    assert found.emails == [FULL]


def test_a_value_inside_an_overlap_is_found_once():
    text = _filler(dc._SCAN_CHUNK - 1500) + f"My email is {EMAIL}\n" + _filler(9000)
    found = DataClassifier().scan_for_pii(text)
    assert found.emails == [EMAIL]


def test_small_text_unchanged():
    text = f"Call me on 555-867-5309, email {EMAIL}."
    found = DataClassifier().scan_for_pii(text)
    assert found.emails == [EMAIL]
    assert found.phones == ["555-867-5309"]


def test_upload_screen_vaults_a_tail_detail(tmp_path):
    v = Vault(vault_dir=str(tmp_path / "vault"))
    v.create("correct horse battery")
    text = _filler(650_000) + f"\nMy SSN is {SSN}\n"
    res = screen_text_for_pii(text, "big.txt", vault=v)
    assert res.pii_vaulted
    assert SSN not in res.text_for_index
