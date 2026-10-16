"""How long the learning will take - measured, never invented.

The last step of the wizard hands the build a pile of knowledge and starts
indexing it in the background (:mod:`puffpai.knowledge.auto_index` already owns
that work; nothing here indexes anything on its own). What was missing is the
sentence the bot says first:

    "This will take approximately two days to learn all this new stuff. I will
    do it in the background, and after that, automatic indexing takes over."

That sentence is only worth saying if the number in it is real. There are
exactly three ways this module will produce one, in order of preference:

1. **Measured on this machine.** ``PackIndex`` records ``started_at`` and
   ``indexed_at`` next to ``docs``/``chunks`` for every pack already learned.
   Those are real elapsed times on this exact hardware with this exact
   embedding model, so they beat anything else we could reach for.
2. **Calibrated from a sample.** With no history, index a small bounded slice of
   the actual corpus, time it, and extrapolate. Self-calibrating, and it says so
   - the range it produces is deliberately wider than a measured one.
3. **Nothing to measure.** Then say that, and give no number at all. A confident
   wrong duration is worse than an honest unknown, and this is the case the
   tests pin hardest.

There is no fourth path. No table of assumed chunks-per-second, no "typical
machine" constant, no extrapolation from the reference board. If this box has
not done the work or a sample of it, the bot does not know.

Every string here is ASCII: these lines reach a Windows console.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import os
import tempfile
import time
from datetime import datetime
from typing import Any, Iterable, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

SCHEMA = "index_estimate/v1"

BASIS_MEASURED = "measured:this_machine"
BASIS_CALIBRATED = "calibrated:sample"
BASIS_UNKNOWN = "unknown"

# How wide the range is around the central figure. A measured rate comes from
# whole packs already learned on this box, so it is tight; a calibration sample
# is a few hundred articles standing in for millions, so it is deliberately
# loose. Neither is a confidence interval in the statistical sense and neither
# is presented as one - they are honesty margins.
_BAND_MEASURED = 1.3
_BAND_CALIBRATED = 2.0

# A calibration run is a wait the owner is sitting through, so keep the sample
# small. The articles it indexes are not wasted: ZIM doc ids are content
# hashes, so the full pass over the same pack re-stores the same ids rather
# than duplicating them.
DEFAULT_SAMPLE_ARTICLES = 50

_WORDS = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
    7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve",
}

_BASIS_NOTES = {
    BASIS_MEASURED: (
        "That is measured from the packs this machine has already learned, "
        "not a guess from a spec sheet."
    ),
    BASIS_CALIBRATED: (
        "That is measured from a small sample of this material indexed on this "
        "machine just now, then scaled up to the rest. It is a real measurement "
        "of a small thing standing in for a big one, so treat it as a rough "
        "range and I will tighten it as I go."
    ),
    BASIS_UNKNOWN: (
        "I have nothing to measure yet. This machine has not learned anything "
        "and I have not sampled this material, so any number I gave you would "
        "be invented."
    ),
}

_NO_NUMBER_LINE = (
    "I cannot tell you yet how long this will take. I will give you a real "
    "number once I have made a dent in it."
)

_BACKGROUND_LINE = (
    "I will do it in the background, and you can talk to me the whole time. "
    "What I have already learned is searchable right now; anything I have not "
    "reached yet simply comes up empty until I get there, and I will say so "
    "rather than make something up."
)

_AFTERWARD_LINE = (
    "After that, automatic indexing takes over: drop a new pack in the "
    "knowledge folder and I pick it up on my own."
)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _parse_time(value: Any) -> Optional[float]:
    """ISO-8601 timestamp -> epoch seconds, or None. Never raises."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _positive_int(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def _amount(value: float, unit: str) -> str:
    """'2.0, day' -> 'two days'; '1.5, day' -> 'one and a half days'."""
    whole = int(value)
    half = abs(value - whole - 0.5) < 1e-6
    if whole == 0 and half:
        return f"half a {unit}"
    head = _WORDS.get(whole, str(whole))
    text = f"{head} and a half" if half else head
    plural = half or whole != 1
    return f"{text} {unit}{'s' if plural else ''}"


def _quantize(seconds: float) -> tuple[float, str]:
    """Round an elapsed-seconds figure to a unit a person would actually say."""
    if seconds < 90:
        return (1.0, "minute")
    if seconds < 3600:
        minutes = seconds / 60.0
        rounded = round(minutes / 5.0) * 5 if minutes >= 10 else round(minutes)
        return (float(max(1, rounded)), "minute")
    if seconds < 36 * 3600:
        hours = seconds / 3600.0
        rounded_h = round(hours * 2) / 2 if hours < 4 else float(round(hours))
        return (max(1.0, rounded_h), "hour")
    days = seconds / 86400.0
    rounded_d = round(days * 2) / 2 if days < 4 else float(round(days))
    return (max(1.0, rounded_d), "day")


def _as_days(seconds: float) -> tuple[float, str]:
    """Express seconds in days, floored at half a day."""
    days = seconds / 86400.0
    rounded = round(days * 2) / 2 if days < 4 else float(round(days))
    return (max(0.5, rounded), "day")


def human_range(low_seconds: float, high_seconds: float) -> str:
    """A spoken range: 'two days', 'two to three days', '40 minutes to two hours'.

    When the top of the range is in days the bottom is said in days too. Nobody
    compares "21 hours" against "a day and a half" in their head, and a range
    that is hard to hold is a range that does not inform anyone.
    """
    low_value, low_unit = _quantize(low_seconds)
    high_value, high_unit = _quantize(high_seconds)
    if high_unit == "day" and low_unit != "day":
        low_value, low_unit = _as_days(low_seconds)
    if (low_value, low_unit) == (high_value, high_unit):
        return _amount(high_value, high_unit)
    low_text = _amount(low_value, low_unit)
    if low_unit == high_unit and int(low_value) > 0:
        # "two to three days" rather than "two days to three days".
        low_text = low_text.rsplit(" ", 1)[0]
    return f"{low_text} to {_amount(high_value, high_unit)}"


# ---------------------------------------------------------------------------
# volume - how much work is sitting there
# ---------------------------------------------------------------------------

def volume_from_packs(packs: Iterable[dict]) -> dict[str, Any]:
    """Describe a pile of not-yet-indexed packs as a work volume.

    Takes the metadata dicts :func:`puffpai.knowledge.auto_index.find_new_packs`
    returns. ``entries`` is the ZIM header's entry count - the only size signal
    available before a pack is opened - and the per-pack counts are kept so
    :func:`progress` can subtract the finished ones exactly.
    """
    uuids: list[str] = []
    entry_counts: dict[str, int] = {}
    names: list[str] = []
    for meta in packs or []:
        if not isinstance(meta, dict):
            continue
        uuid_hex = str(meta.get("uuid_hex") or "")
        entries = _positive_int(meta.get("entry_count"))
        if uuid_hex:
            uuids.append(uuid_hex)
            entry_counts[uuid_hex] = entries
        name = meta.get("filename")
        if name:
            names.append(str(name))
    return {
        "packs": len(uuids) or len(names),
        "pack_uuids": uuids,
        "entry_counts": entry_counts,
        "entries": sum(entry_counts.values()),
        "filenames": names,
    }


# ---------------------------------------------------------------------------
# source 1 - this machine's own history
# ---------------------------------------------------------------------------

def measured_rate(pack_index: Any = None) -> Optional[dict[str, Any]]:
    """This machine's real indexing rate, from packs it has already learned.

    Returns None when the pack index holds nothing usable - no completed pack
    with both timestamps and a chunk count. Never raises: a rate we cannot read
    is a rate we do not have, which the caller turns into an honest unknown.
    """
    try:
        if pack_index is None:
            from puffpai.knowledge.auto_index import PackIndex
            pack_index = PackIndex()
        records = list(pack_index.records())
    except Exception as exc:  # noqa: BLE001 - no history is not a failure
        logger.debug("Could not read pack history: %s", exc)
        return None

    total_chunks = 0
    total_seconds = 0.0
    total_docs = 0
    entry_chunks = 0
    entries = 0
    packs = 0

    for rec in records:
        if not isinstance(rec, dict):
            continue
        if rec.get("status") != "complete":
            continue
        chunks = _positive_int(rec.get("chunks"))
        started = _parse_time(rec.get("started_at"))
        finished = _parse_time(rec.get("indexed_at"))
        if not chunks or started is None or finished is None:
            continue
        elapsed = finished - started
        if elapsed <= 0:
            continue
        packs += 1
        total_chunks += chunks
        total_docs += _positive_int(rec.get("docs"))
        total_seconds += elapsed
        pack_entries = _positive_int(rec.get("entry_count"))
        if pack_entries:
            entries += pack_entries
            entry_chunks += chunks

    if not packs or total_seconds <= 0 or total_chunks <= 0:
        return None

    rate: dict[str, Any] = {
        "basis": BASIS_MEASURED,
        "chunks_per_second": total_chunks / total_seconds,
        "packs_measured": packs,
        "chunks_measured": total_chunks,
        "docs_measured": total_docs,
        "seconds_measured": round(total_seconds, 1),
    }
    if entries and entry_chunks:
        rate["chunks_per_entry"] = entry_chunks / entries
    return rate


# ---------------------------------------------------------------------------
# source 2 - calibrate against the real corpus
# ---------------------------------------------------------------------------

def calibrate(meta: dict, ingestion: Any, *,
              sample_articles: int = DEFAULT_SAMPLE_ARTICLES,
              scratch_index_path: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Index a small bounded sample of one real pack and time it.

    The sample is written through the ordinary indexing path, so the numbers are
    this machine doing this build's actual work - not a synthetic benchmark. The
    bookkeeping goes to a **scratch** pack index, never the live one: recording a
    50-article sample as a completed pack would tell ``find_new_packs`` the whole
    pack was learned and it would never be indexed properly. The sampled
    articles are not wasted work either - ZIM doc ids are content hashes, so the
    full pass re-stores the same ids instead of duplicating them.

    Returns a rate dict, or None if the sample produced nothing measurable.
    Never raises.
    """
    from puffpai.knowledge.auto_index import PackIndex, index_pack

    sample = max(1, int(sample_articles))
    scratch = scratch_index_path or os.path.join(
        tempfile.mkdtemp(prefix="puffpai-calib-"), "calibration_packs.json")
    try:
        scratch_index = PackIndex(scratch)
        started = time.monotonic()
        result = index_pack(meta, ingestion, scratch_index, max_articles=sample)
        elapsed = time.monotonic() - started
    except Exception as exc:  # noqa: BLE001 - a failed sample is just no basis
        logger.warning("Calibration sample failed: %s", exc)
        return None

    chunks = _positive_int(result.get("chunks"))
    docs = _positive_int(result.get("docs"))
    if not chunks or elapsed <= 0:
        return None

    rate: dict[str, Any] = {
        "basis": BASIS_CALIBRATED,
        "chunks_per_second": chunks / elapsed,
        "sample_articles": sample,
        "sample_docs": docs,
        "sample_chunks": chunks,
        "sample_seconds": round(elapsed, 2),
    }
    # Articles asked for, not documents stored: the entry counts we extrapolate
    # from are header entry counts, and plenty of entries never become docs.
    rate["chunks_per_entry"] = chunks / sample
    return rate


# ---------------------------------------------------------------------------
# the estimate
# ---------------------------------------------------------------------------

def _resolve_chunks(volume: dict[str, Any],
                    rate: dict[str, Any]) -> tuple[Optional[float], Optional[str]]:
    """How many chunks this volume is worth, and why we cannot say if we cannot."""
    chunks = volume.get("chunks")
    if isinstance(chunks, (int, float)) and not isinstance(chunks, bool) and chunks > 0:
        return (float(chunks), None)
    entries = _positive_int(volume.get("entries"))
    per_entry = rate.get("chunks_per_entry")
    if entries and isinstance(per_entry, (int, float)) and per_entry > 0:
        return (float(entries) * float(per_entry), None)
    if entries:
        return (None, "I know how many articles are waiting but not yet how much "
                      "work each one is on this machine.")
    return (None, "I cannot see how much material is waiting.")


def estimate(volume: dict[str, Any], *,
             pack_index: Any = None,
             rate: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """How long it will take to learn ``volume``, or an honest 'I do not know'.

    ``volume`` is a dict from :func:`volume_from_packs` (or any dict carrying
    ``chunks`` or ``entries``). ``rate`` may be supplied by the caller - that is
    how a :func:`calibrate` result gets in - otherwise this machine's measured
    history is used, and if there is none the answer is ``basis: unknown`` with
    **no** low/high figures at all.

    Never raises.
    """
    volume = volume if isinstance(volume, dict) else {}
    notes: list[str] = []
    resolved = rate if isinstance(rate, dict) and rate.get("chunks_per_second") else None
    if resolved is None:
        resolved = measured_rate(pack_index)

    if resolved is None:
        return _unknown(volume, "This machine has not indexed anything yet, so "
                                "there is no speed to measure.")

    chunks, why_not = _resolve_chunks(volume, resolved)
    if chunks is None:
        return _unknown(volume, why_not or "I cannot size the work yet.")

    per_second = float(resolved["chunks_per_second"])
    if per_second <= 0:
        return _unknown(volume, "The speed I measured came out at zero, which "
                                "cannot be right, so I am not going to use it.")

    basis = str(resolved.get("basis") or BASIS_MEASURED)
    band = _BAND_CALIBRATED if basis == BASIS_CALIBRATED else _BAND_MEASURED
    seconds = chunks / per_second
    low = seconds / band
    high = seconds * band
    phrase = human_range(low, high)

    if basis == BASIS_CALIBRATED:
        notes.append(
            "This is scaled up from a sample, so it is a rough range. Ask me "
            "again in a while and the answer will be measured instead."
        )

    return {
        "schema": SCHEMA,
        "basis": basis,
        "basis_note": _BASIS_NOTES.get(basis, _BASIS_NOTES[BASIS_UNKNOWN]),
        "known": True,
        "phrase": phrase,
        "low_seconds": round(low, 1),
        "high_seconds": round(high, 1),
        "central_seconds": round(seconds, 1),
        "chunks_estimated": int(chunks),
        "packs": _positive_int(volume.get("packs")),
        "entries": _positive_int(volume.get("entries")),
        "rate": resolved,
        "notes": notes,
    }


def _unknown(volume: dict[str, Any], reason: str) -> dict[str, Any]:
    """The no-basis answer. Carries no duration of any kind, on purpose."""
    return {
        "schema": SCHEMA,
        "basis": BASIS_UNKNOWN,
        "basis_note": _BASIS_NOTES[BASIS_UNKNOWN],
        "known": False,
        "phrase": "",
        "low_seconds": None,
        "high_seconds": None,
        "central_seconds": None,
        "chunks_estimated": None,
        "packs": _positive_int(volume.get("packs")),
        "entries": _positive_int(volume.get("entries")),
        "rate": None,
        "reason": reason,
        "notes": [reason],
    }


def spoken_estimate(est: dict[str, Any], *, with_basis: bool = False) -> str:
    """The sentence the bot says before it starts. ASCII, one paragraph.

    Always states three things: how long (or that it does not know), that the
    work happens in the background and what is usable meanwhile, and that
    automatic indexing takes over afterward.
    """
    est = est if isinstance(est, dict) else {}
    if est.get("known") and est.get("phrase"):
        opening = (f"This will take approximately {est['phrase']} to learn all "
                   "this new stuff.")
    else:
        opening = _NO_NUMBER_LINE
    parts = [opening, _BACKGROUND_LINE, _AFTERWARD_LINE]
    if with_basis:
        parts.insert(1, str(est.get("basis_note") or _BASIS_NOTES[BASIS_UNKNOWN]))
    return " ".join(parts)


def estimate_summary_lines(est: dict[str, Any]) -> list[str]:
    """Plain-language lines for the CLI/API."""
    lines = [spoken_estimate(est)]
    lines.append(str(est.get("basis_note") or ""))
    for note in est.get("notes") or []:
        text = str(note)
        if text and text != est.get("basis_note"):
            lines.append(text)
    return [line for line in lines if line]


# ---------------------------------------------------------------------------
# "how far along are you?"
# ---------------------------------------------------------------------------

def progress(volume: Optional[dict[str, Any]] = None, *,
             pack_index: Any = None) -> dict[str, Any]:
    """Where the background indexing actually is, plus a revised estimate.

    Reads the live pack index rather than any in-memory counter, so the answer
    survives a restart and is true even if the caller was not the one who
    started the work. When ``volume`` carries ``pack_uuids`` the remainder is
    exact; without it we can still say what is finished and what is running, and
    we say that that is all we can say.

    The revised estimate is recomputed from whatever history now exists, so a
    calibrated guess is replaced by a measured answer as soon as one real pack
    has landed. Never raises.
    """
    volume = volume if isinstance(volume, dict) else {}
    try:
        if pack_index is None:
            from puffpai.knowledge.auto_index import PackIndex
            pack_index = PackIndex()
        records = list(pack_index.records())
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not read pack index for progress: %s", exc)
        records = []

    uuids = [str(u) for u in (volume.get("pack_uuids") or [])]
    entry_counts = volume.get("entry_counts") or {}
    notes: list[str] = []

    if uuids:
        done: list[str] = []
        remaining: list[str] = []
        for uuid_hex in uuids:
            try:
                record = pack_index.get(uuid_hex)
            except Exception:  # noqa: BLE001 - unreadable index means not done
                record = None
            if record and record.get("status") == "complete":
                done.append(uuid_hex)
            else:
                remaining.append(uuid_hex)
        remaining_entries = sum(_positive_int(entry_counts.get(u)) for u in remaining)
        total = len(uuids)
        percent: Optional[float] = round(100.0 * len(done) / total, 1) if total else None
        remaining_volume = {
            "packs": len(remaining),
            "pack_uuids": remaining,
            "entry_counts": {u: _positive_int(entry_counts.get(u)) for u in remaining},
            "entries": remaining_entries,
        }
    else:
        done = [str(r.get("filename") or "") for r in records
                if isinstance(r, dict) and r.get("status") == "complete"]
        remaining = []
        percent = None
        remaining_volume = {"packs": 0, "pack_uuids": [], "entry_counts": {}, "entries": 0}
        notes.append(
            "I was not told which packs this run started with, so I can tell you "
            "what is finished but not how much is left."
        )

    in_progress: list[str] = []
    try:
        in_progress = [str(r.get("filename") or "") for r in pack_index.in_progress]
    except Exception:  # noqa: BLE001
        in_progress = [str(r.get("filename") or "") for r in records
                       if isinstance(r, dict) and r.get("status") == "indexing"]

    revised = estimate(remaining_volume, pack_index=pack_index) if remaining else None

    if not remaining and uuids:
        message = ("I have finished learning everything from that batch. "
                   "From here, automatic indexing takes over: drop a new pack "
                   "in the knowledge folder and I pick it up on my own.")
    elif revised is not None and revised.get("known"):
        message = (f"{len(done)} of {len(uuids)} packs learned. "
                   f"About {revised['phrase']} left, and that is now measured "
                   "from the packs I have already done here.")
    elif uuids:
        message = (f"{len(done)} of {len(uuids)} packs learned. I cannot put a "
                   "number on the rest yet.")
    else:
        message = f"{len(done)} packs learned so far."

    return {
        "schema": SCHEMA,
        "packs_total": len(uuids) or None,
        "packs_done": len(done),
        "packs_remaining": len(remaining),
        "percent_complete": percent,
        "in_progress": in_progress,
        "chunks_indexed": getattr(pack_index, "total_chunks", 0),
        "docs_indexed": getattr(pack_index, "total_docs", 0),
        "revised_estimate": revised,
        "message": message,
        "notes": notes,
    }


__all__ = [
    "SCHEMA",
    "BASIS_MEASURED",
    "BASIS_CALIBRATED",
    "BASIS_UNKNOWN",
    "DEFAULT_SAMPLE_ARTICLES",
    "volume_from_packs",
    "measured_rate",
    "calibrate",
    "estimate",
    "spoken_estimate",
    "estimate_summary_lines",
    "human_range",
    "progress",
]
