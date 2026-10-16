"""Tests for the honest indexing estimate.

The property that matters most here is the one a regression would quietly
break: **with nothing to measure, the bot gives no number.** A confident wrong
duration is worse than an admitted unknown, so the no-basis case is pinned from
several angles - basis, fields, and the sentence itself carrying no digits.

Run:
    pytest tests/test_index_estimate.py
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from puffpai import index_estimate
from puffpai.knowledge.auto_index import PackIndex


def _iso(when: datetime) -> str:
    return when.isoformat()


@pytest.fixture
def empty_index(tmp_path):
    """A pack index with no history at all."""
    return PackIndex(str(tmp_path / "knowledge_packs.json"))


@pytest.fixture
def learned_index(tmp_path):
    """A pack index carrying one real, completed pack: 100 chunks/second."""
    index = PackIndex(str(tmp_path / "knowledge_packs.json"))
    start = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
    index.mark(
        "pack-already-done",
        filename="wikipedia_small.zim",
        status="complete",
        started_at=_iso(start),
        indexed_at=_iso(start + timedelta(seconds=1000)),
        entry_count=200_000,
        docs=40_000,
        chunks=100_000,
    )
    return index


class _StubIngestion:
    """Stands in for KnowledgeIngestion: returns fake docs after a real pause."""

    def __init__(self, chunks_per_doc: int = 4, pause: float = 0.03) -> None:
        self.chunks_per_doc = chunks_per_doc
        self.pause = pause
        self.calls: list[int] = []

    def ingest_zim(self, path: str, max_articles: int = 0):
        self.calls.append(max_articles)
        time.sleep(self.pause)

        class _Doc:
            chunk_count = self.chunks_per_doc

        return [_Doc() for _ in range(max_articles)]


# ---------------------------------------------------------------------------
# source 1 - measured from this machine
# ---------------------------------------------------------------------------

def test_measured_rate_comes_from_pack_history(learned_index):
    rate = index_estimate.measured_rate(learned_index)
    assert rate is not None
    assert rate["basis"] == index_estimate.BASIS_MEASURED
    assert rate["chunks_per_second"] == pytest.approx(100.0)
    # 100k chunks over 200k header entries.
    assert rate["chunks_per_entry"] == pytest.approx(0.5)
    assert rate["packs_measured"] == 1


def test_incomplete_or_untimed_packs_are_not_measured(tmp_path):
    index = PackIndex(str(tmp_path / "packs.json"))
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    index.mark("running", filename="a.zim", status="indexing",
               started_at=_iso(start), chunks=0)
    index.mark("no_finish", filename="b.zim", status="complete",
               started_at=_iso(start), chunks=500)
    index.mark("no_chunks", filename="c.zim", status="complete",
               started_at=_iso(start), indexed_at=_iso(start + timedelta(seconds=5)),
               chunks=0)
    assert index_estimate.measured_rate(index) is None


def test_estimate_from_real_history_is_measured(learned_index):
    volume = index_estimate.volume_from_packs([
        {"uuid_hex": "big", "filename": "big.zim", "entry_count": 20_000_000},
    ])
    est = index_estimate.estimate(volume, pack_index=learned_index)

    assert est["basis"] == index_estimate.BASIS_MEASURED
    assert est["known"] is True
    # 20M entries * 0.5 chunks/entry / 100 chunks/s = 100,000 s ~ 27.8 hours.
    assert est["central_seconds"] == pytest.approx(100_000, rel=0.01)
    assert est["low_seconds"] < est["central_seconds"] < est["high_seconds"]
    assert "day" in est["phrase"] or "hour" in est["phrase"]
    assert "measured from the packs this machine has already learned" in est["basis_note"]


# ---------------------------------------------------------------------------
# source 2 - calibrated from a sample
# ---------------------------------------------------------------------------

def test_calibration_sample_measures_this_corpus(tmp_path, empty_index):
    ingestion = _StubIngestion()
    meta = {"uuid_hex": "fresh", "filename": "fresh.zim",
            "filepath": str(tmp_path / "fresh.zim"), "entry_count": 1_000_000}
    scratch = str(tmp_path / "scratch" / "calibration_packs.json")

    rate = index_estimate.calibrate(meta, ingestion, sample_articles=40,
                                    scratch_index_path=scratch)

    assert rate is not None
    assert rate["basis"] == index_estimate.BASIS_CALIBRATED
    assert ingestion.calls == [40]
    assert rate["sample_chunks"] == 160
    assert rate["chunks_per_second"] > 0

    # The sample must NOT be recorded in the live pack index, or the real pack
    # would look already-learned and never be indexed properly.
    assert empty_index.get("fresh") is None
    assert PackIndex(scratch).get("fresh") is not None


def test_calibrated_estimate_says_it_is_a_sample(tmp_path, empty_index):
    ingestion = _StubIngestion()
    meta = {"uuid_hex": "fresh", "filename": "fresh.zim",
            "filepath": str(tmp_path / "fresh.zim"), "entry_count": 1_000_000}
    rate = index_estimate.calibrate(meta, ingestion, sample_articles=40,
                                    scratch_index_path=str(tmp_path / "s.json"))
    volume = index_estimate.volume_from_packs([meta])

    est = index_estimate.estimate(volume, pack_index=empty_index, rate=rate)

    assert est["basis"] == index_estimate.BASIS_CALIBRATED
    assert est["known"] is True
    assert est["phrase"]
    assert any("sample" in note for note in est["notes"])
    # The sample band is deliberately wider than the measured one.
    assert est["high_seconds"] / est["central_seconds"] > 1.5


def test_calibration_that_produces_nothing_gives_no_rate(tmp_path):
    class _Empty:
        def ingest_zim(self, path, max_articles=0):
            return []

    meta = {"uuid_hex": "dud", "filename": "dud.zim",
            "filepath": str(tmp_path / "dud.zim"), "entry_count": 10}
    assert index_estimate.calibrate(meta, _Empty(), sample_articles=5,
                                    scratch_index_path=str(tmp_path / "s.json")) is None


# ---------------------------------------------------------------------------
# source 3 - no basis at all
# ---------------------------------------------------------------------------

def test_no_basis_says_so_and_invents_nothing(empty_index):
    volume = index_estimate.volume_from_packs([
        {"uuid_hex": "big", "filename": "big.zim", "entry_count": 20_000_000},
    ])
    est = index_estimate.estimate(volume, pack_index=empty_index)

    assert est["basis"] == index_estimate.BASIS_UNKNOWN
    assert est["known"] is False
    assert est["low_seconds"] is None
    assert est["high_seconds"] is None
    assert est["central_seconds"] is None
    assert est["phrase"] == ""
    assert est["rate"] is None
    # Nowhere in the payload's prose is there a number masquerading as a fact.
    prose = " ".join([est["basis_note"], est["reason"], *est["notes"]])
    assert not any(ch.isdigit() for ch in prose)
    assert json.dumps(est)  # serialisable for the API


def test_no_basis_sentence_carries_no_number(empty_index):
    est = index_estimate.estimate({"entries": 5_000_000}, pack_index=empty_index)
    said = index_estimate.spoken_estimate(est)

    assert "cannot tell you yet how long" in said
    assert not any(ch.isdigit() for ch in said)
    assert said.isascii()
    assert "background" in said
    assert "automatic indexing" in said


def test_known_volume_but_unmeasurable_size_is_still_unknown(learned_index):
    """History exists, but a volume with no size cannot be estimated."""
    est = index_estimate.estimate({"packs": 3}, pack_index=learned_index)
    assert est["basis"] == index_estimate.BASIS_UNKNOWN
    assert est["low_seconds"] is None


# ---------------------------------------------------------------------------
# the words
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("low,high", [
    (30, 60),
    (400, 900),
    (3600 * 3, 3600 * 5),
    (86400 * 1.6, 86400 * 2.4),
    (86400 * 9, 86400 * 14),
])
def test_ranges_round_to_human_units(low, high):
    phrase = index_estimate.human_range(low, high)
    assert phrase.isascii()
    assert "." not in phrase          # never "47.3 hours"
    assert any(unit in phrase for unit in ("minute", "hour", "day"))


def test_equal_endpoints_collapse_to_one_amount():
    assert index_estimate.human_range(86400 * 2.0, 86400 * 2.1) == "two days"


def test_half_units_read_naturally():
    assert index_estimate.human_range(86400 * 1.5, 86400 * 1.5) == "one and a half days"


def test_spoken_estimate_mentions_background_and_what_follows(learned_index):
    volume = index_estimate.volume_from_packs([
        {"uuid_hex": "big", "filename": "big.zim", "entry_count": 20_000_000},
    ])
    est = index_estimate.estimate(volume, pack_index=learned_index)
    said = index_estimate.spoken_estimate(est)

    assert said.isascii()
    assert said.startswith("This will take approximately ")
    assert "background" in said
    assert "automatic indexing takes over" in said
    assert "searchable" in said           # says what IS usable meanwhile
    assert "comes up empty" in said       # and what is not

    with_basis = index_estimate.spoken_estimate(est, with_basis=True)
    assert "measured from the packs this machine has already learned" in with_basis
    assert with_basis.isascii()


def test_summary_lines_are_ascii(learned_index):
    est = index_estimate.estimate({"chunks": 500_000}, pack_index=learned_index)
    for line in index_estimate.estimate_summary_lines(est):
        assert line.isascii()


# ---------------------------------------------------------------------------
# progress
# ---------------------------------------------------------------------------

def test_progress_reports_real_state_and_revises_the_estimate(tmp_path):
    index = PackIndex(str(tmp_path / "packs.json"))
    packs = [
        {"uuid_hex": "one", "filename": "one.zim", "entry_count": 200_000},
        {"uuid_hex": "two", "filename": "two.zim", "entry_count": 200_000},
    ]
    volume = index_estimate.volume_from_packs(packs)

    # Before anything lands there is no basis, and progress says so honestly.
    early = index_estimate.progress(volume, pack_index=index)
    assert early["packs_done"] == 0
    assert early["packs_remaining"] == 2
    assert early["revised_estimate"]["basis"] == index_estimate.BASIS_UNKNOWN
    assert early["message"].isascii()

    # One pack finishes for real: 100k chunks in 1000 seconds on this box.
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    index.mark("one", filename="one.zim", status="complete",
               started_at=_iso(start), indexed_at=_iso(start + timedelta(seconds=1000)),
               entry_count=200_000, docs=40_000, chunks=100_000)

    later = index_estimate.progress(volume, pack_index=index)
    assert later["packs_done"] == 1
    assert later["packs_remaining"] == 1
    assert later["percent_complete"] == 50.0
    assert later["chunks_indexed"] == 100_000
    revised = later["revised_estimate"]
    assert revised["basis"] == index_estimate.BASIS_MEASURED
    assert revised["central_seconds"] == pytest.approx(1000, rel=0.01)
    assert "1 of 2 packs learned" in later["message"]
    assert later["message"].isascii()


def test_progress_when_everything_is_done(tmp_path):
    index = PackIndex(str(tmp_path / "packs.json"))
    packs = [{"uuid_hex": "one", "filename": "one.zim", "entry_count": 10}]
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    index.mark("one", filename="one.zim", status="complete",
               started_at=_iso(start), indexed_at=_iso(start + timedelta(seconds=10)),
               entry_count=10, docs=5, chunks=20)

    done = index_estimate.progress(index_estimate.volume_from_packs(packs),
                                   pack_index=index)
    assert done["packs_remaining"] == 0
    assert done["revised_estimate"] is None
    assert "finished learning everything" in done["message"]
    assert "automatic indexing takes over" in done["message"]


def test_progress_without_a_volume_admits_what_it_cannot_say(tmp_path):
    index = PackIndex(str(tmp_path / "packs.json"))
    report = index_estimate.progress(None, pack_index=index)
    assert report["packs_total"] is None
    assert any("not how much is left" in note for note in report["notes"])
    assert report["message"].isascii()


def test_calibration_defaults_to_a_scratch_index_not_the_live_one(tmp_path, monkeypatch):
    """The DEFAULT path must isolate too, not just an explicit scratch path.

    A real caller does not pass scratch_index_path. If the default ever became
    the live index, a 50-article sample would mark the whole pack "complete",
    find_new_packs would skip it forever, and the owner would end up with a
    knowledge pack that silently contains fifty articles out of a million.
    """
    from puffpai.knowledge.auto_index import PackIndex, default_pack_index_path

    live_path = str(tmp_path / "live_packs.json")
    monkeypatch.setattr("puffpai.knowledge.auto_index.default_pack_index_path",
                        lambda: live_path)
    live = PackIndex(live_path)

    meta = {"uuid_hex": "fresh", "filename": "fresh.zim",
            "filepath": str(tmp_path / "fresh.zim"), "entry_count": 1_000_000}

    rate = index_estimate.calibrate(meta, _StubIngestion(), sample_articles=40)

    assert rate is not None
    # No scratch path was given, and the live index must still know nothing.
    assert PackIndex(live_path).get("fresh") is None
    assert live.get("fresh") is None
    assert default_pack_index_path  # imported for the reader's benefit
