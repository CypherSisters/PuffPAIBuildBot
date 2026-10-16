"""The owner's own data - asked for, surveyed, and never taken quietly.

Downloadable knowledge packs are somebody else's library. The material that
makes a build actually *yours* is already on your disks, and a wizard that only
offers downloads has quietly decided your files are not a first-class source.
This step asks for them.

Three rules shape everything here, and they are the reason this module exists
rather than a folder picker:

* **Nothing is scanned until you name it.** The suggestions below (Documents and
  friends) are offered as suggestions and nothing more. This module never walks
  a path the owner did not hand it. Removable and network locations are labelled
  as such, because "index my USB stick" and "index my house NAS" are different
  decisions with different consequences.
* **You see the survey before anything moves.** :func:`survey` counts files by
  type and size and says how many are actually ingestible. It reads names, sizes
  and extensions - never contents. Then :func:`apply_choices` needs a yes per
  location; the default is no, for every one.
* **The PII path is not softened.** Ingestion already screens documents and puts
  high/critical personal data in the vault instead of the general knowledge zone
  (:func:`puffpai.ingestion.ingest_file`). This module routes through that, says
  so out loud in the wizard copy, and does not offer a way around it.

**What the manifest records, and what it deliberately does not.** Chosen folder
paths are recorded, because a build that cannot say which folders it learned
from is not auditable - but the home-directory prefix is collapsed to ``~`` so
the account name never lands in the record, and nothing below the folder is
recorded at all: no filenames, no subdirectory names, no contents, no per-file
anything. Counts are per type and per location only. A folder name can itself be
personal ("Divorce", "Oncology"), which is exactly why the owner is shown the
recorded form before it is written, and why declining a location leaves no
trace of it.

Every string here is ASCII: these lines reach a Windows console.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Iterable, Iterator, Optional

from puffpai.ingestion import detect_file_type
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

SCHEMA = "data_sources/v1"

DRIVE_FIXED = "fixed"
DRIVE_REMOVABLE = "removable"
DRIVE_NETWORK = "network"
DRIVE_UNKNOWN = "unknown"

# File types :func:`puffpai.ingestion.detect_file_type` knows how to open. The
# table itself lives in ingestion; this is only the "which of its answers are
# ingestible" question, so a new extension there is picked up here for free.
_NOT_INGESTIBLE = "unknown"

# Directories that are never worth surveying and would drown the counts.
_SKIP_DIRS = frozenset({
    "node_modules", "__pycache__", ".git", ".svn", ".hg", "venv", ".venv",
    "$RECYCLE.BIN", "System Volume Information", "AppData", "Library",
})

# A survey is meant to be a look, not an expedition. Past this many files we
# stop counting and say plainly that the number is a floor, not a total.
DEFAULT_MAX_FILES = 50000

VAULT_NOTE = (
    "Anything I find that looks like personal or sensitive information - "
    "medical notes, financial account numbers, government id numbers - goes "
    "into the vault, not the general knowledge zone. That is not optional and "
    "there is no setting to turn it off. The vault needs your PIN to open; the "
    "knowledge zone does not."
)

CONSENT_NOTE = (
    "Nothing is copied, moved or indexed until you say yes to that location "
    "specifically. Saying yes to one folder is not saying yes to the drive."
)

RECORD_NOTE = (
    "The build record keeps the folder paths you chose and how many files of "
    "each kind were in them. It does not keep any filename, any folder below "
    "the one you chose, or any file contents, and your account name is "
    "replaced with '~' in the recorded path."
)


# ---------------------------------------------------------------------------
# where things live
# ---------------------------------------------------------------------------

def _home() -> str:
    try:
        return os.path.abspath(os.path.expanduser("~"))
    except Exception:  # noqa: BLE001
        return ""


def redact_path(path: str) -> str:
    """The recorded form of a path: home prefix collapsed to '~'.

    Keeps the folder the owner chose (auditable) and drops the account name
    (personal data that has no business in a build record).
    """
    try:
        absolute = os.path.abspath(os.path.expanduser(str(path)))
    except Exception:  # noqa: BLE001
        return str(path)
    home = _home()
    if home and (absolute == home or absolute.startswith(home + os.sep)):
        tail = absolute[len(home):].lstrip(os.sep)
        return "~" + (os.sep + tail if tail else "")
    return absolute


def classify_drive(path: str) -> str:
    """Best-effort: is this a fixed disk, removable media, or a network share?

    Standard library only, and it degrades to ``unknown`` rather than guessing.
    The label exists so the wizard can say "that is a USB stick, it will not be
    there next week" before an owner indexes one.
    """
    try:
        raw = str(path)
        absolute = os.path.abspath(os.path.expanduser(raw))
    except Exception:  # noqa: BLE001
        return DRIVE_UNKNOWN

    if raw.startswith("\\\\") or raw.startswith("//"):
        return DRIVE_NETWORK

    if sys.platform.startswith("win"):
        try:
            import ctypes
            drive = os.path.splitdrive(absolute)[0]
            if not drive:
                return DRIVE_UNKNOWN
            kind = ctypes.windll.kernel32.GetDriveTypeW(drive + os.sep)  # type: ignore[attr-defined]
            return {2: DRIVE_REMOVABLE, 3: DRIVE_FIXED, 4: DRIVE_NETWORK,
                    5: DRIVE_REMOVABLE}.get(int(kind), DRIVE_UNKNOWN)
        except Exception:  # noqa: BLE001 - no ctypes, no windll, no answer
            return DRIVE_UNKNOWN

    for prefix in ("/media/", "/mnt/", "/Volumes/", "/run/media/"):
        if absolute.startswith(prefix):
            return DRIVE_REMOVABLE
    return DRIVE_FIXED


def describe_location(path: str) -> dict[str, Any]:
    """What we can say about a path without opening anything inside it."""
    raw = str(path)
    try:
        absolute = os.path.abspath(os.path.expanduser(raw))
    except Exception:  # noqa: BLE001
        absolute = raw
    exists = False
    readable = False
    try:
        exists = os.path.isdir(absolute)
        readable = exists and os.access(absolute, os.R_OK)
    except OSError:
        exists = False

    drive_kind = classify_drive(absolute)
    note = ""
    if not exists:
        note = "I cannot see that folder right now."
        if drive_kind in (DRIVE_REMOVABLE, DRIVE_NETWORK):
            note = ("I cannot see that folder right now - it looks like it lives "
                    "on something that has to be plugged in or reachable.")
    elif not readable:
        note = "That folder is there but I am not allowed to read it."
    elif drive_kind == DRIVE_REMOVABLE:
        note = ("That is removable media. I can learn from it now, but it will "
                "not be there to re-read once you unplug it.")
    elif drive_kind == DRIVE_NETWORK:
        note = ("That is a network location. Reading it means talking to another "
                "machine on your network, and it is gone when that machine is.")

    return {
        "path": absolute,
        "recorded_path": redact_path(absolute),
        "label": os.path.basename(absolute.rstrip(os.sep)) or absolute,
        "exists": exists,
        "readable": readable,
        "drive_kind": drive_kind,
        "note": note,
    }


def suggested_locations() -> list[dict[str, Any]]:
    """Places worth offering - as suggestions only, never scanned.

    Existence is checked (so the wizard does not offer a folder that is not
    there); nothing inside is looked at. ``scanned`` is False on every entry and
    stays False until the owner picks one.
    """
    home = _home()
    candidates = [
        ("Documents", "the usual home for the things you write and keep"),
        ("Desktop", "whatever you are working on right now"),
        ("Downloads", "often a pile - worth a look before you say yes"),
        ("Pictures", "photos and scans; scans of documents are the useful part"),
    ]
    out: list[dict[str, Any]] = []
    for name, why in candidates:
        if not home:
            continue
        described = describe_location(os.path.join(home, name))
        described.update({
            "suggestion": True,
            "scanned": False,
            "why": why,
        })
        out.append(described)
    return [entry for entry in out if entry["exists"]]


# ---------------------------------------------------------------------------
# the survey - counts, never contents
# ---------------------------------------------------------------------------

def _survey_one(path: str, *, max_files: int,
                follow_symlinks: bool = False) -> dict[str, Any]:
    """Count one location by file type. Never raises; records what it hit."""
    described = describe_location(path)
    counts: dict[str, int] = {}
    total_files = 0
    total_bytes = 0
    ingestible = 0
    ingestible_bytes = 0
    notes: list[str] = []
    truncated = False

    if not described["exists"]:
        notes.append(described["note"] or "That folder is not there.")
        described.update({
            "counts": counts, "files": 0, "bytes": 0, "ingestible": 0,
            "ingestible_bytes": 0, "truncated": False, "surveyed": False,
            "notes": notes,
        })
        return described

    if not described["readable"]:
        notes.append(described["note"] or "I am not allowed to read that folder.")
        described.update({
            "counts": counts, "files": 0, "bytes": 0, "ingestible": 0,
            "ingestible_bytes": 0, "truncated": False, "surveyed": False,
            "notes": notes,
        })
        return described

    denied = 0
    vanished = 0

    def _on_error(exc: OSError) -> None:
        nonlocal denied, vanished
        if isinstance(exc, PermissionError):
            denied += 1
        else:
            vanished += 1

    try:
        walker = os.walk(described["path"], onerror=_on_error,
                         followlinks=follow_symlinks)
        for root, dirs, files in walker:
            dirs[:] = [d for d in dirs
                       if d not in _SKIP_DIRS and not d.startswith(".")]
            for name in files:
                if name.startswith("."):
                    continue
                if total_files >= max_files:
                    truncated = True
                    break
                ftype = detect_file_type(name)
                counts[ftype] = counts.get(ftype, 0) + 1
                total_files += 1
                size = 0
                try:
                    size = os.path.getsize(os.path.join(root, name))
                except OSError:
                    vanished += 1
                total_bytes += size
                if ftype != _NOT_INGESTIBLE:
                    ingestible += 1
                    ingestible_bytes += size
            if truncated:
                break
    except Exception as exc:  # noqa: BLE001 - a survey never takes the wizard down
        logger.warning("Survey of a location stopped early: %s", exc)
        notes.append("I stopped partway through reading that folder, so these "
                     "numbers are a floor, not a total.")

    if denied:
        notes.append(f"I was refused access to {denied} folder(s) inside this "
                     "one and skipped them.")
    if vanished:
        notes.append(f"{vanished} item(s) disappeared or could not be read while "
                     "I was counting. That is normal on a drive in use.")
    if truncated:
        notes.append(f"I stopped counting at {max_files} files. There are at "
                     "least that many here.")
    if described["drive_kind"] in (DRIVE_REMOVABLE, DRIVE_NETWORK) and described["note"]:
        notes.append(described["note"])

    described.update({
        "counts": counts,
        "files": total_files,
        "bytes": total_bytes,
        "ingestible": ingestible,
        "ingestible_bytes": ingestible_bytes,
        "truncated": truncated,
        "surveyed": True,
        "notes": notes,
    })
    return described


def survey(paths: Iterable[str], *,
           max_files: int = DEFAULT_MAX_FILES) -> dict[str, Any]:
    """Look at the locations the owner named and report what is in them.

    Counts files by the type :func:`puffpai.ingestion.detect_file_type` gives
    them, sums sizes, and separates "files" from "files I can actually read".
    **No file is opened.** Unreadable paths, permission errors and drives that
    vanished mid-walk are recorded as notes on the location, not raised.

    Nothing here copies, moves, or indexes anything.
    """
    locations = [_survey_one(str(p), max_files=max_files) for p in (paths or [])]
    totals: dict[str, int] = {}
    for loc in locations:
        for ftype, count in (loc.get("counts") or {}).items():
            totals[ftype] = totals.get(ftype, 0) + count
    return {
        "schema": SCHEMA,
        "locations": locations,
        "counts": totals,
        "files": sum(int(loc.get("files", 0)) for loc in locations),
        "bytes": sum(int(loc.get("bytes", 0)) for loc in locations),
        "ingestible": sum(int(loc.get("ingestible", 0)) for loc in locations),
        "unreadable": [loc["path"] for loc in locations if not loc.get("surveyed")],
        "vault_note": VAULT_NOTE,
        "consent_note": CONSENT_NOTE,
        "record_note": RECORD_NOTE,
    }


# ---------------------------------------------------------------------------
# consent - a yes per location, default no
# ---------------------------------------------------------------------------

def apply_choices(surveyed: dict[str, Any],
                  approve: Optional[Iterable[str]] = None) -> dict[str, Any]:
    """Record which surveyed locations the owner said yes to.

    Everything not in ``approve`` is declined, including anything the owner did
    not answer about. There is no "approve all" argument on purpose: the caller
    has to name each path it is claiming a yes for.
    """
    wanted = {os.path.abspath(os.path.expanduser(str(p)))
              for p in (approve or [])}
    approved: list[dict[str, Any]] = []
    declined: list[dict[str, Any]] = []
    for loc in (surveyed.get("locations") or []):
        if loc.get("path") in wanted and loc.get("surveyed"):
            approved.append(loc)
        else:
            declined.append(loc)

    unknown = sorted(wanted - {loc.get("path") for loc in
                              (surveyed.get("locations") or [])})
    notes: list[str] = []
    if unknown:
        notes.append("I was told yes for a location I never surveyed, so I have "
                     "left it out. Survey it first.")
    return {
        "schema": SCHEMA,
        "approved": approved,
        "declined_count": len(declined),
        "files": sum(int(loc.get("files", 0)) for loc in approved),
        "ingestible": sum(int(loc.get("ingestible", 0)) for loc in approved),
        "vault_note": VAULT_NOTE,
        "notes": notes,
    }


def iter_ingestible_files(location: dict[str, Any], *,
                          max_files: int = DEFAULT_MAX_FILES) -> Iterator[str]:
    """Yield the file paths in an **approved** location worth handing to ingestion.

    Yields paths only; opening and screening them is
    :func:`puffpai.ingestion.ingest_file`'s job, and that is where the PII
    screening and vault routing live. Refuses a location that was not approved,
    so a caller cannot get a file list out of this module by accident.
    """
    if not isinstance(location, dict) or not location.get("surveyed"):
        return
    root = str(location.get("path") or "")
    if not root or not os.path.isdir(root):
        return
    seen = 0
    try:
        for base, dirs, files in os.walk(root, onerror=lambda exc: None):
            dirs[:] = [d for d in dirs
                       if d not in _SKIP_DIRS and not d.startswith(".")]
            for name in files:
                if name.startswith("."):
                    continue
                if detect_file_type(name) == _NOT_INGESTIBLE:
                    continue
                if seen >= max_files:
                    return
                seen += 1
                yield os.path.join(base, name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Stopped listing files in an approved location: %s", exc)


# ---------------------------------------------------------------------------
# learning the approved folders - through the same PII screen as an upload
# ---------------------------------------------------------------------------

#: How many files one run learns. A folder bigger than this is learned in part
#: and the rest is reported, never silently dropped.
LEARN_LIMIT = 200

#: What the file is to the ingestion pipeline: study material. This skips the
#: model classifier (nothing of the owner's is sent to a model to be sorted)
#: and routes the text to the searchable index, after the PII screen.
_LEARN_HINT = "study notes from your own folder"


def _staging_dir() -> str:
    from puffpai.config import get_config
    return os.path.join(get_config().DATA_DIR, "data", "own_folder_staging")


def _staged_name(path: str) -> str:
    """A stable, private name for one file's staging copy.

    Stable, so learning the same file again replaces its earlier chunks rather
    than adding a second copy. Hashed, so the owner's filename never becomes a
    path or an id anywhere.
    """
    import hashlib
    digest = hashlib.sha256(os.path.abspath(path).encode("utf-8", "replace")).hexdigest()
    ext = os.path.splitext(path)[1].lower()[:10]
    return f"own-{digest[:24]}{ext}"


def learn_approved(selection: dict[str, Any], *, vault: Any = None,
                   limit: int = LEARN_LIMIT,
                   ingest: Any = None) -> dict[str, Any]:
    """Learn the files in the folders the owner said yes to. Never raises.

    Every file goes through :func:`puffpai.ingestion.ingest_file`, the same
    path as a dashboard upload: text is extracted, screened for personal
    details, high and critical details go to the vault (or the file is held
    back when the vault is locked - fail closed), and the rest becomes
    searchable. The owner's original is never handed to that function, because
    it deletes its input once it has vaulted it: each file is copied to a
    private staging name first and the copy is removed afterwards.

    Returns counts only - no filename, no path, no text - so the result can go
    in the build record.
    """
    import shutil

    if ingest is None:
        from puffpai import ingestion
        ingest = ingestion.ingest_file
    counts = {"learned": 0, "private_details_to_vault": 0, "held_back": 0,
              "unreadable": 0, "not_reached": 0}
    approved = [loc for loc in (selection.get("approved") or []) if isinstance(loc, dict)]
    if not approved:
        return {**counts, "lines": []}
    staging = _staging_dir()
    try:
        os.makedirs(staging, exist_ok=True)
    except OSError as exc:
        logger.warning("Could not prepare the staging folder: %s", exc)
        return {**counts, "unreadable": sum(int(l.get("ingestible", 0)) for l in approved),
                "lines": ["Could not prepare a place to read your files, so none were learned."]}
    done = 0
    for location in approved:
        for path in iter_ingestible_files(location):
            if done >= limit:
                counts["not_reached"] += 1
                continue
            done += 1
            staged = os.path.join(staging, _staged_name(path))
            try:
                shutil.copyfile(path, staged)
            except OSError:
                counts["unreadable"] += 1
                continue
            try:
                result = ingest(staged, original_filename=os.path.basename(path),
                                user_hint=_LEARN_HINT, auto_apply=False, vault=vault)
            except Exception as exc:  # noqa: BLE001 - one bad file is a count
                logger.warning("Could not learn a file from an approved folder: %s",
                               type(exc).__name__)
                result = None
            finally:
                try:
                    if os.path.exists(staged):
                        os.remove(staged)
                except OSError:
                    pass
            if result is None:
                counts["unreadable"] += 1
            elif getattr(result, "error", None):
                if getattr(result, "pii_detected", False):
                    counts["held_back"] += 1
                else:
                    counts["unreadable"] += 1
            else:
                counts["learned"] += 1
                if getattr(result, "vaulted", False):
                    counts["private_details_to_vault"] += 1
    return {**counts, "lines": learn_summary_lines(counts)}


def learn_summary_lines(counts: dict[str, Any]) -> list[str]:
    """Plain words for what :func:`learn_approved` did."""
    lines: list[str] = []
    learned = int(counts.get("learned", 0))
    if learned:
        lines.append(f"Learned {learned} file(s) from your folders.")
    if counts.get("private_details_to_vault"):
        lines.append(f"{counts['private_details_to_vault']} of them had private "
                     f"details; those details went into your vault and the rest "
                     f"is searchable.")
    if counts.get("held_back"):
        lines.append(f"{counts['held_back']} file(s) have private details and your "
                     f"vault was locked, so they were NOT learned. Unlock the vault "
                     f"and upload them on the Knowledge page.")
    if counts.get("unreadable"):
        lines.append(f"{counts['unreadable']} file(s) could not be read.")
    if counts.get("not_reached"):
        lines.append(f"{counts['not_reached']} more file(s) were not read this time "
                     f"(the first {LEARN_LIMIT} were). Upload the ones you want on "
                     f"the Knowledge page.")
    return lines


# ---------------------------------------------------------------------------
# the manifest block
# ---------------------------------------------------------------------------

def sources_manifest_block(selection: dict[str, Any]) -> dict[str, Any]:
    """The owner's chosen data locations, shaped for the manifest.

    Records, per approved location: the redacted folder path, the drive kind,
    the file count, the ingestible count, and the per-type counts. It records no
    filename, no subdirectory, no file contents and no sizes of individual
    files, because none of those are needed to say what this build learned from
    and every one of them leaks something.
    """
    entries: list[dict[str, Any]] = []
    for loc in (selection.get("approved") or []):
        entries.append({
            "path": str(loc.get("recorded_path") or redact_path(str(loc.get("path", "")))),
            "drive_kind": str(loc.get("drive_kind") or DRIVE_UNKNOWN),
            "files": int(loc.get("files", 0)),
            "ingestible": int(loc.get("ingestible", 0)),
            "bytes": int(loc.get("bytes", 0)),
            "counts": {str(k): int(v) for k, v in (loc.get("counts") or {}).items()},
        })
    return {
        "schema": SCHEMA,
        "locations": entries,
        "location_count": len(entries),
        "files": sum(int(e["files"]) for e in entries),
        "ingestible": sum(int(e["ingestible"]) for e in entries),
        "records": "locations-and-counts-only",
        "pii_routing": "vault",
        "note": RECORD_NOTE,
    }


def source_summary_lines(surveyed: dict[str, Any]) -> list[str]:
    """Plain-language lines describing a survey, for the CLI/API."""
    lines: list[str] = []
    locations = surveyed.get("locations") or []
    if not locations:
        lines.append("You have not pointed me at any of your own folders yet.")
    for loc in locations:
        if not loc.get("surveyed"):
            lines.append(f"  {loc.get('recorded_path')}: {loc.get('note') or 'not readable'}")
            continue
        counts = loc.get("counts") or {}
        kinds = ", ".join(f"{count} {ftype}"
                          for ftype, count in sorted(counts.items())
                          if ftype != _NOT_INGESTIBLE and count)
        lines.append(
            f"  {loc.get('recorded_path')} ({loc.get('drive_kind')}): "
            f"{loc.get('files', 0)} files, {loc.get('ingestible', 0)} I can read"
            + (f" - {kinds}" if kinds else "")
        )
        for note in loc.get("notes") or []:
            lines.append(f"    {note}")
    lines.append(CONSENT_NOTE)
    lines.append(VAULT_NOTE)
    lines.append(RECORD_NOTE)
    return lines


__all__ = [
    "SCHEMA",
    "DRIVE_FIXED",
    "DRIVE_REMOVABLE",
    "DRIVE_NETWORK",
    "DRIVE_UNKNOWN",
    "DEFAULT_MAX_FILES",
    "VAULT_NOTE",
    "CONSENT_NOTE",
    "RECORD_NOTE",
    "classify_drive",
    "describe_location",
    "suggested_locations",
    "redact_path",
    "survey",
    "apply_choices",
    "iter_ingestible_files",
    "sources_manifest_block",
    "source_summary_lines",
]
