"""The provisioning bundle - the sneakernet path.

"They can put it on a thumb drive and take it over to wherever they want it to
live." A connected box assembles a build - manifest, model and pack references,
the rulepacks and phrasebook for that model - writes it to removable media, and
a machine that never touches a network imports it. That makes
``build_network: online`` plus ``run_egress: sealed`` a real, ordinary path
instead of a contradiction, and it is also the answer for a slow or metered
connection.

Three properties make a bundle safe to carry between machines:

* **Everything is checksummed, and import is all-or-nothing.**
  ``CHECKSUMS.json`` covers every file including the manifest.
  :func:`import_bundle` verifies all of them before it copies anything, and one
  bad hash refuses the whole bundle. Hashes stream in chunks, so a multi-gigabyte
  model blob never lands in memory.
* **A bundle is untrusted input.** It arrived on a stranger's thumb drive as far
  as we are concerned. Every path is checked to resolve inside the destination
  (a ``../`` entry is rejected, not sanitised), and the manifest goes through
  :func:`puffpai.build_manifest.sanitize_manifest` on the way in, so a bundle
  can never weaken a contract.
* **No network, anywhere in this module.** Not on export, not on verify, not on
  import. That is the whole point of the path.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from typing import Any, Optional

SCHEMA = "build_bundle/v1"
CHECKSUM_SCHEMA = "build_bundle_checksums/v1"
ALGORITHM = "sha256"

BUNDLE_FILE = "bundle.json"
MANIFEST_FILE = "build_manifest.json"
CHECKSUM_FILE = "CHECKSUMS.json"
PAYLOAD_DIR = "payload"

_CHUNK = 1024 * 1024


# ---------------------------------------------------------------------------
# paths and hashing
# ---------------------------------------------------------------------------

def _safe_join(base: str, relative: str) -> Optional[str]:
    """Resolve ``relative`` under ``base``, or None if it escapes.

    The guard is a real-path containment check, not a string check, so a
    ``../`` entry, an absolute path, and a symlink pointing out of the tree are
    all caught the same way. A bundle that tries any of them is refused whole;
    we never "clean up" a hostile path and carry on.
    """
    rel = str(relative or "").replace("\\", "/").strip()
    if not rel or rel.startswith("/") or ":" in rel.split("/")[0]:
        return None
    root = os.path.realpath(base)
    target = os.path.realpath(os.path.join(root, *rel.split("/")))
    if target == root:
        return None
    if os.path.commonpath([root, target]) != root:
        return None
    return target


def _sha256_file(path: str) -> str:
    """Stream a file through sha256. Never loads the whole file into memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _walk_relative(root: str) -> list[str]:
    """Every file under ``root``, as forward-slash relative paths, sorted."""
    found: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            found.append(rel)
    return sorted(found)


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

def export_bundle(
    manifest: dict[str, Any],
    dest_dir: str,
    payloads: Optional[dict[str, str]] = None,
    references: Optional[dict[str, Any]] = None,
    label: str = "",
) -> dict[str, Any]:
    """Write a provisioning bundle to ``dest_dir``. Returns a result record.

    ``payloads`` maps a bundle-relative path (under ``payload/``) to the source
    file to copy: the chosen model blob, the selected packs, the rulepacks and
    the phrasebook for that model. ``references`` records what the bundle points
    at rather than carries - names, sizes, sources - as data, for a target that
    already has the blobs.

    The manifest is sanitised on the way out as well as on the way in, so a
    bundle never ships a contract-disabling field even by accident. Everything
    written, including the manifest and the bundle record itself, is covered by
    ``CHECKSUMS.json``.
    """
    from puffpai import build_manifest as bm

    problems: list[str] = []
    os.makedirs(dest_dir, exist_ok=True)
    payload_root = os.path.join(dest_dir, PAYLOAD_DIR)

    clean = bm.sanitize_manifest(manifest)

    copied: list[str] = []
    for rel, source in (payloads or {}).items():
        rel_in_bundle = f"{PAYLOAD_DIR}/{str(rel).replace(os.sep, '/').lstrip('/')}"
        target = _safe_join(dest_dir, rel_in_bundle)
        if target is None:
            problems.append(
                f"Refused to write '{rel}': that path points outside the bundle."
            )
            continue
        if not os.path.isfile(source):
            problems.append(f"Skipped '{rel}': there is no file at {source}.")
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(source, target)
        copied.append(rel_in_bundle)

    record = {
        "schema": SCHEMA,
        "bundle_id": uuid.uuid4().hex,
        "created_at": time.time(),
        "label": str(label or ""),
        "algorithm": ALGORITHM,
        "manifest_file": MANIFEST_FILE,
        "payload_files": sorted(copied),
        "references": references or {},
        "note": (
            "A provisioning bundle. Import verifies every checksum before it "
            "applies anything, and needs no network at any point."
        ),
    }

    with open(os.path.join(dest_dir, MANIFEST_FILE), "w", encoding="utf-8") as fh:
        json.dump(clean, fh, indent=2)
        fh.write("\n")
    with open(os.path.join(dest_dir, BUNDLE_FILE), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
        fh.write("\n")

    checksums: dict[str, str] = {}
    for rel in _walk_relative(dest_dir):
        if rel == CHECKSUM_FILE:
            continue
        checksums[rel] = _sha256_file(os.path.join(dest_dir, rel))

    with open(os.path.join(dest_dir, CHECKSUM_FILE), "w", encoding="utf-8") as fh:
        json.dump(
            {"schema": CHECKSUM_SCHEMA, "algorithm": ALGORITHM, "files": checksums},
            fh,
            indent=2,
        )
        fh.write("\n")

    return {
        "ok": not problems,
        "path": dest_dir,
        "bundle_id": record["bundle_id"],
        "files": sorted(checksums.keys()),
        "problems": problems,
    }


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

def verify_bundle(src_dir: str) -> tuple[bool, list[str]]:
    """Dry-run check of a bundle. Returns (ok, plain-English problems).

    Checks the shape (a checksum list, a bundle record, a manifest), that every
    listed path stays inside the bundle, that every listed file is present and
    hashes to what the list says, and that nothing is present that the list does
    not cover. An uncovered file is a problem, not a curiosity: an unchecksummed
    file is exactly what someone would add.
    """
    problems: list[str] = []

    if not os.path.isdir(src_dir):
        return False, [f"There is no bundle at {src_dir}."]

    checksum_path = os.path.join(src_dir, CHECKSUM_FILE)
    try:
        with open(checksum_path, "r", encoding="utf-8") as fh:
            checksum_doc = json.load(fh)
    except (FileNotFoundError, OSError):
        return False, [f"This bundle has no {CHECKSUM_FILE}, so nothing can be trusted."]
    except json.JSONDecodeError:
        return False, [f"The {CHECKSUM_FILE} in this bundle is not readable JSON."]

    if not isinstance(checksum_doc, dict) or not isinstance(checksum_doc.get("files"), dict):
        return False, [f"The {CHECKSUM_FILE} in this bundle does not list any files."]
    if str(checksum_doc.get("algorithm", ALGORITHM)) != ALGORITHM:
        return False, [
            f"This bundle was checksummed with "
            f"'{checksum_doc.get('algorithm')}', and this build only verifies {ALGORITHM}."
        ]

    listed: dict[str, str] = {str(k): str(v) for k, v in checksum_doc["files"].items()}

    for rel in sorted(listed):
        target = _safe_join(src_dir, rel)
        if target is None:
            problems.append(
                f"Refusing this bundle: '{rel}' points outside the bundle folder."
            )
            continue
        if not os.path.isfile(target):
            problems.append(f"Missing file: '{rel}' is listed but is not in the bundle.")
            continue
        if _sha256_file(target) != listed[rel]:
            problems.append(
                f"Changed file: '{rel}' does not match its checksum, so this bundle "
                f"is not the one that was exported."
            )

    on_disk = set(_walk_relative(src_dir)) - {CHECKSUM_FILE}
    for rel in sorted(on_disk - set(listed)):
        problems.append(f"Extra file: '{rel}' is in the bundle but is not checksummed.")

    for required in (BUNDLE_FILE, MANIFEST_FILE):
        if required not in listed:
            problems.append(f"This bundle has no {required}, so there is nothing to apply.")

    return (not problems), problems


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

def import_bundle(
    src_dir: str,
    dest_dir: str,
    manifest_target: Optional[str] = None,
) -> dict[str, Any]:
    """Verify a bundle whole, then apply it. No network at any point.

    Nothing is copied and nothing is written until :func:`verify_bundle` passes
    for every file; a single bad hash refuses the whole bundle. The manifest is
    then sanitised (a bundle is untrusted input) and stamped with bundle
    provenance so the build can say where it came from.

    ``manifest_target`` defaults to ``dest_dir/build_manifest.json``; the caller
    doing the wizard's apply step passes the real manifest path.
    """
    from puffpai import build_manifest as bm

    ok, problems = verify_bundle(src_dir)
    if not ok:
        return {"ok": False, "applied": False, "problems": problems, "manifest": None, "files": []}

    with open(os.path.join(src_dir, CHECKSUM_FILE), "r", encoding="utf-8") as fh:
        listed = {str(k): str(v) for k, v in json.load(fh)["files"].items()}
    with open(os.path.join(src_dir, BUNDLE_FILE), "r", encoding="utf-8") as fh:
        record = json.load(fh)
    with open(os.path.join(src_dir, MANIFEST_FILE), "r", encoding="utf-8") as fh:
        raw_manifest = json.load(fh)

    payload_prefix = f"{PAYLOAD_DIR}/"
    planned: list[tuple[str, str, str]] = []
    for rel in sorted(listed):
        if not rel.startswith(payload_prefix):
            continue
        source = _safe_join(src_dir, rel)
        target = _safe_join(dest_dir, rel)
        if source is None or target is None:
            # verify_bundle already refuses these; belt and braces at write time.
            return {
                "ok": False,
                "applied": False,
                "problems": [f"Refusing this bundle: '{rel}' points outside the folder."],
                "manifest": None,
                "files": [],
            }
        planned.append((rel, source, target))

    os.makedirs(dest_dir, exist_ok=True)
    written: list[str] = []
    for rel, source, target in planned:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(source, target)
        written.append(rel)

    clean = bm.sanitize_manifest(raw_manifest if isinstance(raw_manifest, dict) else {})
    clean["provenance"] = bm.bundle_provenance_block(record, verified=True)

    target_manifest = manifest_target or os.path.join(dest_dir, MANIFEST_FILE)
    os.makedirs(os.path.dirname(os.path.abspath(target_manifest)), exist_ok=True)
    with open(target_manifest, "w", encoding="utf-8") as fh:
        json.dump(clean, fh, indent=2)
        fh.write("\n")
    written.append(os.path.basename(target_manifest))

    return {
        "ok": True,
        "applied": True,
        "problems": [],
        "manifest": clean,
        "manifest_path": target_manifest,
        "files": written,
        "bundle_id": record.get("bundle_id"),
    }


def bundle_summary_lines(result: dict[str, Any]) -> list[str]:
    """Plain-language lines describing an export, verify, or import result."""
    lines: list[str] = []
    if result.get("ok"):
        lines.append("Bundle checked out: every file matched its checksum.")
        if result.get("applied"):
            lines.append(f"Applied {len(result.get('files') or [])} files. Nothing touched a network.")
        elif result.get("path"):
            lines.append(f"Wrote {len(result.get('files') or [])} files to {result['path']}.")
    else:
        lines.append("This bundle was refused. Nothing was applied.")
    for problem in result.get("problems") or []:
        lines.append(f"  - {problem}")
    return lines


__all__ = [
    "SCHEMA",
    "CHECKSUM_SCHEMA",
    "ALGORITHM",
    "BUNDLE_FILE",
    "MANIFEST_FILE",
    "CHECKSUM_FILE",
    "PAYLOAD_DIR",
    "export_bundle",
    "verify_bundle",
    "import_bundle",
    "bundle_summary_lines",
]
