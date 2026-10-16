"""The outside-traffic log never keeps part of a key when it cuts a long path.

The key check runs on the whole path and on every segment BEFORE a long path
is cut; a cut happens only at a '/', and the segment the limit falls inside is
dropped whole. (Before: the path was cut to 120 characters first, and the
part of a key that was left was too short to look like one, so it was
written to outside.jsonl.)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from puffpai.security import egress_ledger

# A made-up key of a realistic shape. Not a real credential.
KEY = "q8ZrT2vLx9KmW4pNc7Hs1YdQ3"
LIMIT = egress_ledger._MAX_PATH  # noqa: SLF001 - the limit under test


@pytest.fixture
def log(tmp_path):
    path = tmp_path / "logs" / "outside.jsonl"
    egress_ledger.set_path_override(str(path))
    yield path
    egress_ledger.set_path_override(None)


def _slash_path(kept: int) -> str:
    """A key in its own segment, placed so a plain 120-character cut keeps
    *kept* characters of it."""
    path = "/files/" + "x" * (LIMIT - kept - 8) + "/" + KEY + "/raw"
    assert path[:LIMIT].endswith("/" + KEY[:kept])
    return path


def _glued_path(kept: int) -> str:
    """No '/' before the key: it follows letters in the same segment."""
    path = "/files/" + "x" * (LIMIT - kept - 7) + KEY + "/raw"
    assert path[:LIMIT].endswith("x" + KEY[:kept])
    return path


def _missed_path(kept: int) -> str:
    """A key glued between runs of "abc1" in one segment: the key check
    still misses this whole, so only the cut at a
    slash keeps the key out. Keeps that fallback tested."""
    path = ("/files/" + ("abc1" * 60)[:LIMIT - kept - 7] + KEY + "abc1" * 30
            + "/raw")
    assert path[:LIMIT].endswith(KEY[:kept])
    from puffpai import web_search
    assert not web_search.looks_like_pasted_key(path)  # else not a fallback test
    return path


def _record_path(path: str) -> str:
    rec = egress_ledger.build_record("weather", host="api.example.com",
                                     outcome="done", path=path)
    return rec["path"]


def _no_piece_of_key(text: str) -> None:
    # Six characters of the key are already more than a coincidence.
    for start in range(len(KEY) - 5):
        assert KEY[start:start + 6] not in text, KEY[start:start + 6]


@pytest.mark.parametrize("kept", [9, 12, 14, 15])
def test_a_key_in_its_own_segment_drops_the_whole_path(kept):
    path = _record_path(_slash_path(kept))
    assert path == ""


@pytest.mark.parametrize("kept", [9, 12, 14, 15])
def test_a_key_glued_to_letters_is_cut_away_whole(kept):
    path = _record_path(_glued_path(kept))
    # The key check now sees a key glued onto a run of letters,
    # so the whole path is dropped, as for a key in its own segment. Before,
    # only the cut kept it out: the segment the limit fell inside was dropped.
    assert path == ""
    _no_piece_of_key(path)


@pytest.mark.parametrize("kept", [9, 12, 14, 15])
def test_a_key_the_check_misses_is_still_cut_away_at_a_slash(kept):
    path = _record_path(_missed_path(kept))
    # The segment the limit falls inside is dropped; nothing of it is kept.
    assert path == "/files/"
    _no_piece_of_key(path)


@pytest.mark.parametrize("make", [_slash_path, _glued_path, _missed_path])
@pytest.mark.parametrize("kept", [9, 12, 14, 15])
def test_no_piece_of_the_key_reaches_the_file(log, make, kept):
    assert egress_ledger.record("weather", host="api.example.com",
                                outcome="done", path=make(kept),
                                what="forecast")
    _no_piece_of_key(log.read_text(encoding="utf-8"))


@pytest.mark.parametrize("kept", [9, 12, 14, 15])
def test_safe_path_applies_the_same_rule(kept):
    url = "https://api.example.com" + _slash_path(kept) + "?town=Dayton"
    assert egress_ledger.safe_path(url) == ""
    url = "https://api.example.com" + _glued_path(kept)
    assert egress_ledger.safe_path(url) == ""  # the glued key is seen whole
    url = "https://api.example.com" + _missed_path(kept)
    assert egress_ledger.safe_path(url) == "/files/"  # the cut keeps it out


def test_a_percent_escaped_key_segment_drops_the_path():
    escaped = "".join(f"%{ord(c):02X}" for c in KEY)
    assert _record_path("/v1/" + escaped + "/raw") == ""


def test_a_long_plain_path_is_still_kept_cut_at_a_slash():
    words = ["forecast", "daily", "temperature", "precipitation", "windspeed",
             "humidity", "sunrise", "sunset", "pressure", "cloudcover",
             "visibility", "uvindex", "dewpoint", "snowfall", "gusts"]
    long_path = "/v1/" + "/".join(words)
    assert len(long_path) > LIMIT
    path = _record_path(long_path)
    assert path and path.endswith("/") and len(path) <= LIMIT
    assert long_path.startswith(path)
    # Only whole segments are kept.
    kept = [seg for seg in path.split("/") if seg]
    assert kept == (["v1"] + words)[:len(kept)]
    assert len(kept) >= 10


def test_a_short_plain_path_is_kept_as_it_was():
    assert _record_path("/v1/forecast") == "/v1/forecast"
    assert _record_path("/v1/forecast;jsessionid=abc") == "/v1/forecast"


def test_a_hand_edited_long_line_is_cut_the_same_way_on_read(log):
    log.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    lines = [
        {"t": now.isoformat(), "service": "weather", "host": "x.org",
         "path": _slash_path(12), "outcome": "done", "what": "forecast"},
        {"t": now.isoformat(), "service": "weather", "host": "x.org",
         "path": _glued_path(14), "outcome": "done", "what": "forecast"},
    ]
    log.write_text("".join(json.dumps(x) + "\n" for x in lines),
                   encoding="utf-8")
    recs = egress_ledger.read(24)
    # Both dropped whole: the glued key is seen whole too.
    assert sorted(r["path"] for r in recs) == ["", ""]
    _no_piece_of_key(json.dumps(recs))
