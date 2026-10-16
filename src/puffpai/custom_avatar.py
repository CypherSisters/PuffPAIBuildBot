"""Owner-supplied avatar: one custom face, validated before it ever hits disk.

The build ships no avatar model at all - we had no licence to redistribute the
heads that used to be here. This module is the whole supported path: the owner
brings their own .glb, so "the digital human" is a face they chose rather than
one we chose for them. Requirements for the file are in docs/AVATARS.md.

Everything here treats the upload as hostile, because it is the one place in
the dashboard that accepts a file from a person:

* **Allowlist, never a denylist.** Exactly one extension is accepted (``.glb``)
  and the bytes must actually parse as a glTF binary container. A renamed
  archive, image or executable is refused on content, not on its name.
* **We name the file.** The stored name is a constant in this module. The
  uploaded name is kept only as a display string, never as a path component.
  :func:`resolve_within` is the belt to that braces: any name that escapes the
  avatar directory raises, and the test suite proves it with ``../``.
* **Validation happens entirely in memory, before any write.** A rejected or
  oversized upload never opens the destination, so the previous avatar keeps
  rendering. The write itself is a temp file plus :func:`os.replace`, so even a
  crash mid-write leaves the old face intact rather than a truncated one.
* **Under the data dir.** Paths resolve per call through
  :func:`puffpai.config.get_config`, honouring ``PUFFPAI_DATA_DIR`` the same way
  ``_ai_settings_path()`` does. An import-time repo-root constant would write a
  stranger's uploads into the source tree.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import logging
import os
import struct
import tempfile
from datetime import datetime, timezone

from puffpai.config import get_config

logger = logging.getLogger(__name__)

# ── The contract, stated once and reused by the API and the UI ──────────────

#: Upload ceiling. A Ready Player Me or Avaturn export runs 2-15 MB; 32 MB
#: leaves room for a detailed scan without letting anyone fill the disk through
#: the dashboard.
MAX_UPLOAD_BYTES = 32 * 1024 * 1024
MAX_UPLOAD_MB = MAX_UPLOAD_BYTES // (1024 * 1024)

#: The allowlist. One entry, deliberately. ``.gltf`` is excluded because it is
#: a JSON manifest that references sibling ``.bin``/texture files, which a
#: single-file upload cannot carry - accepting it would mean accepting a model
#: that renders as an untextured wreck.
ALLOWED_EXTENSIONS = frozenset({".glb"})

#: The name we store under. Never derived from the upload.
STORED_NAME = "custom.glb"
_META_NAME = "custom.json"

_GLB_MAGIC = b"glTF"
_GLB_VERSION = 2
_GLB_HEADER_LEN = 12
_GLB_CHUNK_HEADER_LEN = 8
_CHUNK_TYPE_JSON = b"JSON"

#: Characters kept when echoing the uploader's filename back for display.
_DISPLAY_MAX = 80


class AvatarRejected(Exception):
    """An upload failed validation. The message is shown to the owner."""


# ── Paths (resolved per call, never at import) ──────────────────────────────

def avatar_dir() -> str:
    """Directory holding owner-supplied avatars (honours PUFFPAI_DATA_DIR)."""
    return os.path.join(get_config().DATA_DIR, "data", "avatars")


def resolve_within(root: str, name: str) -> str:
    """Resolve *name* inside *root*, or raise :class:`AvatarRejected`.

    Callers only ever pass :data:`STORED_NAME`, so in production this never
    fires. It exists because a filename that reaches a path join is one refactor
    away from being attacker-controlled, and a traversal guard that is only
    added after the bug is a traversal guard that arrived late.
    """
    if not name or name in (".", ".."):
        raise AvatarRejected("Invalid avatar filename.")
    # Reject separators outright rather than relying on normalisation alone:
    # this catches "../x" and "a/../../x" on POSIX and "..\\x" on Windows.
    # "\\" is checked unconditionally (not just via os.sep/os.altsep) so a
    # Windows-authored escape attempt is refused the same way if it is ever
    # replayed on a POSIX host, where a backslash is just another filename
    # character rather than a separator.
    if (os.sep in name or (os.altsep and os.altsep in name)
            or "/" in name or "\\" in name):
        raise AvatarRejected("Invalid avatar filename.")

    root_abs = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root_abs, name))
    if target != root_abs and not target.startswith(root_abs + os.sep):
        raise AvatarRejected("Invalid avatar filename.")
    return target


def custom_avatar_path() -> str:
    """Absolute path of the owner's uploaded avatar, whether or not it exists."""
    return resolve_within(avatar_dir(), STORED_NAME)


def _meta_path() -> str:
    return resolve_within(avatar_dir(), _META_NAME)


# ── Validation ─────────────────────────────────────────────────────────────

def _display_name(filename: str) -> str:
    """A safe-to-render echo of what the owner called their file.

    Display only. It never becomes a path component, but it does reach a
    browser, so anything that is not a plain filename character is dropped.
    """
    base = os.path.basename(filename.replace("\\", "/")).strip()
    kept = "".join(c for c in base if c.isalnum() or c in "._- ")
    kept = kept.strip(". ")
    return kept[:_DISPLAY_MAX] or "avatar.glb"


def looks_like_glb(data: bytes) -> bool:
    """True when *data* really is a glTF binary container.

    Checks the 12-byte header (magic, version, declared length) and that the
    first chunk is the mandatory JSON chunk and fits inside the payload. This
    is what stops a ``.zip`` renamed to ``.glb``: the extension is the claim,
    these bytes are the evidence.
    """
    if len(data) < _GLB_HEADER_LEN + _GLB_CHUNK_HEADER_LEN:
        return False
    magic, version, declared = struct.unpack_from("<4sII", data, 0)
    if magic != _GLB_MAGIC or version != _GLB_VERSION:
        return False
    # The header's own length field must describe this file, not a larger one.
    if declared < _GLB_HEADER_LEN or declared > len(data):
        return False
    chunk_len, chunk_type = struct.unpack_from("<I4s", data, _GLB_HEADER_LEN)
    if chunk_type != _CHUNK_TYPE_JSON or chunk_len == 0:
        return False
    end = _GLB_HEADER_LEN + _GLB_CHUNK_HEADER_LEN + chunk_len
    return end <= declared


def validate_upload(filename: str, data: bytes) -> str:
    """Check an upload end to end; return the display name or raise.

    Raises :class:`AvatarRejected` with owner-facing wording. Nothing is written
    to disk here, which is what guarantees a rejected upload leaves the previous
    avatar untouched.
    """
    if not filename or not filename.strip():
        raise AvatarRejected("No filename on the upload.")

    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise AvatarRejected(
            f"Only {allowed} avatars are accepted. That file is {ext or 'unnamed'}."
        )

    if not data:
        raise AvatarRejected("That file is empty.")

    if len(data) > MAX_UPLOAD_BYTES:
        raise AvatarRejected(
            f"That file is {len(data) / (1024 * 1024):.1f} MB. "
            f"The limit is {MAX_UPLOAD_MB} MB."
        )

    if not looks_like_glb(data):
        raise AvatarRejected(
            "That file is named .glb but is not a glTF binary model."
        )

    return _display_name(filename)


# ── Store / read / remove ──────────────────────────────────────────────────

def save_custom_avatar(filename: str, data: bytes) -> dict:
    """Validate and store an owner-supplied avatar. Returns its metadata.

    Raises :class:`AvatarRejected` if the upload does not pass, having touched
    nothing. On success the replacement is atomic: the new bytes land in a temp
    file in the same directory and are moved over the old one, so a reader mid
    swap sees either the old avatar or the new one, never a half-written file.
    """
    display = validate_upload(filename, data)

    directory = avatar_dir()
    os.makedirs(directory, exist_ok=True)
    target = resolve_within(directory, STORED_NAME)

    tmp_fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".partial")
    try:
        with os.fdopen(tmp_fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, target)
    except Exception:
        # Leave the previous avatar exactly as it was.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    meta = {
        "stored_name": STORED_NAME,
        "original_name": display,
        "size_bytes": len(data),
        "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        with open(_meta_path(), "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2)
    except OSError as exc:
        # The model is what matters; a missing sidecar only costs the label.
        logger.warning("Could not write avatar metadata: %s", exc)
    return meta


def custom_avatar_meta() -> dict | None:
    """Metadata for the stored avatar, or None when there is not one.

    The model file is the source of truth. A sidecar without a model is stale
    and reported as absent; a model without a sidecar still reports, with the
    size read off disk, because the owner's face existing matters more than our
    bookkeeping being complete.
    """
    path = custom_avatar_path()
    if not os.path.isfile(path):
        return None

    meta = {
        "stored_name": STORED_NAME,
        "original_name": STORED_NAME,
        "size_bytes": os.path.getsize(path),
        "uploaded_at": datetime.fromtimestamp(
            os.path.getmtime(path), timezone.utc
        ).isoformat(timespec="seconds"),
    }
    try:
        with open(_meta_path(), "r", encoding="utf-8") as fh:
            stored = json.load(fh)
        if isinstance(stored, dict):
            for key in ("original_name", "uploaded_at"):
                if isinstance(stored.get(key), str):
                    meta[key] = stored[key]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return meta


def delete_custom_avatar() -> bool:
    """Remove the owner's avatar. True if there was one to remove."""
    removed = False
    for path in (custom_avatar_path(), _meta_path()):
        try:
            os.unlink(path)
            removed = removed or path.endswith(STORED_NAME)
        except (FileNotFoundError, OSError):
            continue
    return removed


def limits() -> dict:
    """The upload contract, for a UI that must state the cap before the pick."""
    return {
        "max_bytes": MAX_UPLOAD_BYTES,
        "max_mb": MAX_UPLOAD_MB,
        "allowed_extensions": sorted(ALLOWED_EXTENSIONS),
    }
