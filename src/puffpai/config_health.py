"""Config health — tell the truth when a shipped config is broken.

A loader that falls back on *any* failure is a silent-degradation trap. A stray
quote in ``config/capability_catalog.json`` used to leave the wizard building
someone a bot from a two-item baked-in list, with plausible-looking wrong
results and nothing said. It was only caught by counting items in a debug probe.

The rule this module exists to enforce is the distinction the old code did not
make:

* **Absent** — legitimate and common (a fresh clone, an optional config). Use
  the baked-in fallback, silently. That behaviour is correct and is kept.
* **Unparseable, or parseable but structurally invalid** — a corrupt
  *first-party* config. The system still continues on the fallback, because
  crashing the wizard mid-build is worse, but the degradation is recorded here,
  logged at ERROR, surfaced in plain words, and written into any manifest
  committed while it is in force. A build should carry the truth about how it
  was made.

The record is process-wide and deliberately dependency-free: a loader deep in
the call graph can report without threading a context object through every
caller, so loader signatures stay drop-in. A lock keeps it safe for the
threaded API server.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

# Kinds of failure. ABSENT is not a degradation; the other two are.
ABSENT = "absent"
UNPARSEABLE = "unparseable"
INVALID = "invalid"

DEGRADED_KINDS = (UNPARSEABLE, INVALID)

_KIND_PHRASE = {
    UNPARSEABLE: "could not be read (the file is not valid JSON)",
    INVALID: "was read but does not have the shape this build expects",
    ABSENT: "is not installed",
}

_lock = threading.Lock()
_records: list[dict[str, Any]] = []


def record(
    config: str,
    path: str,
    kind: str,
    detail: str,
    *,
    reduced: bool = False,
) -> dict[str, Any]:
    """Record one config-health event and return the stored record.

    ``config`` is a stable short name (``capability_catalog``), ``path`` the file
    or directory it was loaded from, ``kind`` one of :data:`ABSENT`,
    :data:`UNPARSEABLE`, :data:`INVALID`, and ``detail`` a plain-English
    one-liner. ``reduced`` says whether the fallback now in use is materially
    smaller than the real file should be — the difference between "degraded" and
    "degraded and you are missing options".

    Duplicate events (same config, path and kind) collapse onto the first
    record; loaders are called many times per run and the owner needs the fact
    once, not once per call. Never raises.
    """
    kind = kind if kind in _KIND_PHRASE else INVALID
    entry = {
        "config": str(config),
        "path": str(path),
        "kind": kind,
        "detail": str(detail),
        "reduced": bool(reduced),
        "recorded_at": time.time(),
    }
    with _lock:
        for existing in _records:
            if (
                existing["config"] == entry["config"]
                and existing["path"] == entry["path"]
                and existing["kind"] == entry["kind"]
            ):
                return dict(existing)
        _records.append(entry)

    # Absent is normal; corrupt is not. Only one of them gets to be loud.
    if kind == ABSENT:
        logger.debug("Config %s absent at %s; using the built-in fallback.", config, path)
    else:
        # The consequence is not the same in every case, so do not assert one.
        # A corrupt catalog really does drop the whole build back to a stub
        # list. One bad app manifest among five does not - the other four
        # loaded, and saying otherwise sends someone hunting a fault that is
        # not there. `reduced` is what distinguishes them; use it.
        consequence = (
            "Falling back to the built-in list; results will be incomplete."
            if reduced else
            "The rest of this config loaded; only this part was skipped."
        )
        logger.error(
            "Corrupt first-party config %s at %s (%s): %s %s",
            config, path, kind, detail, consequence,
        )
    return dict(entry)


def report(*, degraded_only: bool = False) -> list[dict[str, Any]]:
    """Every event recorded so far, oldest first, as plain dicts (copies)."""
    with _lock:
        items = [dict(r) for r in _records]
    if degraded_only:
        items = [r for r in items if r["kind"] in DEGRADED_KINDS]
    return items


def is_degraded() -> bool:
    """True when at least one first-party config is corrupt.

    An absent file is not degradation — that is the fallback working as designed.
    """
    with _lock:
        return any(r["kind"] in DEGRADED_KINDS for r in _records)


def clear() -> None:
    """Forget every recorded event. For tests and for a clean re-run."""
    with _lock:
        _records.clear()


# ---------------------------------------------------------------------------
# owner-facing words
# ---------------------------------------------------------------------------

def summary_lines() -> list[str]:
    """Plain-words degradation notice for the CLI and the dashboard.

    ASCII only: these strings reach the Windows console. Empty list means every
    first-party config read cleanly, which is the thing worth being able to say.
    """
    out: list[str] = []
    for rec in report(degraded_only=True):
        phrase = _KIND_PHRASE.get(rec["kind"], _KIND_PHRASE[INVALID])
        line = f"Heads up: the {_spoken(rec['config'])} {phrase}, so the wizard is "
        line += (
            "working from a much smaller built-in list. Your build will be missing options. "
            if rec["reduced"]
            else "working from its built-in defaults instead. "
        )
        line += f"Fix or reinstall {_short_path(rec['path'])} and re-run setup."
        out.append(_ascii(line))
    return out


def _spoken(config: str) -> str:
    """A config's short name as words, e.g. 'capability catalog'."""
    return config.replace("_", " ").strip() or "config"


def _short_path(path: str) -> str:
    """The tail of a path - enough to find the file, no home directory leak."""
    norm = str(path).replace("\\", "/").rstrip("/")
    parts = [p for p in norm.split("/") if p]
    return "/".join(parts[-2:]) if len(parts) >= 2 else (parts[-1] if parts else "the config file")


def _ascii(text: str) -> str:
    """Drop anything a Windows console cannot print."""
    return text.encode("ascii", "replace").decode("ascii")


# ---------------------------------------------------------------------------
# manifest block
# ---------------------------------------------------------------------------

def manifest_block() -> Optional[dict[str, Any]]:
    """The ``config_health`` block for a manifest, or None when healthy.

    A build assembled on top of a corrupt catalog is a build with limitations its
    owner cannot see, so the manifest carries the fact. Only the tail of each
    path is recorded — a manifest can be published, and an absolute path is a
    home-directory leak. Nothing here is ever read back as a switch;
    :func:`puffpai.build_manifest.sanitize_manifest` regenerates this block from
    the live record on every write, so an imported bundle can neither forge
    health it does not have nor invent a degradation.
    """
    events = report(degraded_only=True)
    if not events:
        return None
    return {
        "degraded": True,
        "configs": sorted({e["config"] for e in events}),
        "reduced": any(e["reduced"] for e in events),
        "events": [
            {
                "config": e["config"],
                "file": _short_path(e["path"]),
                "kind": e["kind"],
                "detail": _ascii(e["detail"]),
                "reduced": e["reduced"],
            }
            for e in events
        ],
        "note": (
            "Recorded by the wizard: at least one first-party config could not "
            "be read, so this build was assembled from built-in fallbacks and "
            "may be missing options. This is a statement, not a setting."
        ),
    }


# ---------------------------------------------------------------------------
# loader helper
# ---------------------------------------------------------------------------

def note_load_failure(
    config: str,
    path: str,
    exc: BaseException,
    *,
    reduced: bool = False,
) -> None:
    """Classify a loader exception and record it. Absent stays silent."""
    if isinstance(exc, FileNotFoundError) or isinstance(exc, NotADirectoryError):
        record(config, path, ABSENT, "The file is not installed.")
        return
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 2:
        record(config, path, ABSENT, "The file is not installed.")
        return
    if isinstance(exc, ValueError):  # json.JSONDecodeError subclasses ValueError
        record(config, path, UNPARSEABLE, f"JSON parse error: {exc}", reduced=reduced)
        return
    record(config, path, UNPARSEABLE, f"Could not be opened: {exc}", reduced=reduced)
