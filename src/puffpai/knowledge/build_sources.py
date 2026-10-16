"""What this build's knowledge was promised, and what it actually has.

The wizard records two different kinds of wish in the build manifest and neither
of them was ever reconciled against the disk:

* ``knowledge.sources`` - the vetted registry rows that were *offered*, and the
  ones that were *fetched*. A fetched row lands as a file under the data dir
  (or in the ZIM folder), with a provenance sidecar beside it. Nothing read that
  back, so a source could be downloaded and still be invisible everywhere.
* ``knowledge.packs`` - catalog ids the owner picked during the build interview
  (``pack:wikipedia``). These are wishes, not downloads: choosing one moves no
  bytes at all. The manifest recorded the wish, the dashboard counted indexed
  libraries, and the gap between the two was never stated anywhere.

This module is the reconciliation, and it is read-only: it opens no socket,
downloads nothing, and indexes nothing. It reads the manifest, the shipped
registry, the download folders, and the pack index, and gives every source and
pack exactly one of four statuses:

``indexed``
    Bytes are on this disk and the knowledge store has them.
``fetched_not_indexed``
    Bytes are on this disk and the knowledge store does not. For a ``docs`` row
    that is the permanent, honest answer rather than a backlog: those rows are
    landing pages and API catalogs, so they are reported as ``live_source`` -
    something the bot can cite and reach, not a shelf of indexed documents.
``selected_not_downloaded``
    The owner chose it and nothing was downloaded. Carries the size and the
    exact command that would download it.
``offered``
    The wizard proposed it and the owner did not take it. A suggestion.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

SCHEMA = "knowledge_build_sources/v1"

STATUS_INDEXED = "indexed"
STATUS_FETCHED_NOT_INDEXED = "fetched_not_indexed"
STATUS_SELECTED_NOT_DOWNLOADED = "selected_not_downloaded"
STATUS_OFFERED = "offered"

#: Bytes that can be indexed, versus a reference the bot reaches live. A ``docs``
#: registry row is a landing page - NASA's API catalog, the Astropy docs index -
#: and calling one "indexed knowledge" would be a lie told by a progress bar.
FORM_DOCUMENTS = "documents"
FORM_LIVE_SOURCE = "live_source"

#: The command that downloads one source after the build. It shows the size and
#: license and asks for a yes before a byte moves (a script adds
#: ``--yes --accept-size``, the separate deliberate flag).
FETCH_COMMAND = "puffpai sources add {source_id}"

#: What ``selected_by`` says for a book the owner added after the build.
ADDED_BY_OWNER = "added by you"

SYNC_COMMAND = "puffpai knowledge sync"


def pack_source_ids(pack: str) -> tuple[str, ...]:
    """The downloads that would satisfy one interview pack, or ``()``.

    The mapping is data, not code: the Kiwix row of ``config/catalog_index.json``
    lists, per pack, the BuildBot list ids and ``kiwix:`` book ids that would
    satisfy it. An id that is neither a ``kiwix:`` book nor a row on BuildBot's
    list is dropped, and a pack with nothing listed is reported as unmapped
    rather than guessed at.
    """
    try:
        from puffpai import catalogs
        row = catalogs.catalog_row("kiwix") or {}
        listed = (row.get("interview_packs") or {}).get(str(pack)) or []
    except Exception:  # noqa: BLE001 - no index means no mapping
        return ()
    if not isinstance(listed, list):
        return ()
    from puffpai import source_registry
    out: list[str] = []
    for ident in listed:
        text = str(ident)
        if text.startswith("kiwix:") or source_registry.get_source(text) is not None:
            out.append(text)
    return tuple(out)


def _size_label(size_mb: Any) -> str:
    """Plain words for a download size. Unknown stays unknown."""
    try:
        value = float(size_mb)
    except (TypeError, ValueError):
        return "large download"
    if value <= 0:
        return "large download"
    if value >= 1024:
        return f"about {value / 1024:.1f} GB"
    return f"about {value:.0f} MB"


def _load_registry(registry: Optional[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if registry is None:
        try:
            from puffpai import source_registry
            registry = source_registry.load_registry()
        except Exception as exc:  # noqa: BLE001 - a missing registry is not fatal here
            logger.warning("Could not load the source registry: %s", exc)
            return {}
    rows = registry.get("sources") if isinstance(registry, dict) else None
    out: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        if isinstance(row, dict) and row.get("id"):
            out[str(row["id"])] = row
    return out


def _library_entry(source_id: str) -> Optional[dict[str, Any]]:
    """Label, license and size for a Kiwix book the wizard offered, from the saved list.

    A book the wizard offered but nobody chose is not in any registry file, so
    without this it would show up as a bare id. Read-only and offline.
    """
    if not source_id.startswith("kiwix:"):
        return None
    try:
        from puffpai.catalogs import knowledge_resolver
        cand = knowledge_resolver.candidate_by_id(source_id)
    except Exception:  # noqa: BLE001 - a label is a nicety, never a stop
        return None
    if not cand:
        return None
    size = cand.get("size_bytes")
    return {"id": source_id, "label": cand.get("name"), "kind": "zim",
            "license": cand.get("license") if cand.get("fetchable") else "",
            "license_url": cand.get("license_url") or "",
            "commercial_use": cand.get("commercial_ok") is True,
            "what_it_is": cand.get("what_it_is") or "",
            "size_mb": (size / (1024 * 1024)) if isinstance(size, int) else None}


def _sources_dir(data_dir: Optional[str]) -> str:
    if data_dir:
        return os.path.join(data_dir, "sources")
    try:
        from puffpai.config import get_config
        return os.path.join(get_config().DATA_DIR, "sources")
    except Exception:  # noqa: BLE001
        return ""


def _zim_dir(zim_dir: Optional[str]) -> str:
    if zim_dir is not None:
        return zim_dir
    try:
        from puffpai.config import get_config
        return str(get_config().knowledge.zim_dir or "")
    except Exception:  # noqa: BLE001
        return ""


def on_disk_sources(*directories: str) -> dict[str, dict[str, Any]]:
    """Source ids whose artifact is present here, from the provenance sidecars.

    The sidecar is the join between "the registry says this exists" and "this
    machine has it": :mod:`puffpai.source_fetch` writes one beside every file it
    downloads. A sidecar whose artifact has since been deleted does not count -
    that is a record of a download, not a download.

    Windows note: paths are rebuilt from the directory being scanned and the
    sidecar's own filename, never from the ``path`` recorded inside it, so a
    record written on another machine (or before a data dir moved) still
    resolves here.
    """
    from puffpai import source_fetch

    found: dict[str, dict[str, Any]] = {}
    for directory in directories:
        if not directory or not os.path.isdir(directory):
            continue
        for record in source_fetch.provenance_records(directory):
            source_id = str(record.get("source_id") or "")
            filename = os.path.basename(str(record.get("filename") or ""))
            if not source_id or not filename:
                continue
            path = os.path.join(directory, filename)
            if not os.path.exists(path):
                continue
            found[source_id] = {
                "filename": filename,
                "directory": directory,
                "bytes": int(record.get("bytes") or 0),
                "fetched_at": str(record.get("fetched_at") or ""),
                "revoked_at": record.get("revoked_at"),
            }
    return found


def _pack_records(pack_records: Optional[list[dict]]) -> list[dict]:
    if pack_records is not None:
        return list(pack_records)
    try:
        from puffpai.knowledge.auto_index import PackIndex
        return PackIndex().records()
    except Exception as exc:  # noqa: BLE001 - no pack index means nothing indexed
        logger.debug("No pack index available: %s", exc)
        return []


def _indexed_ids_and_files(records: list[dict]) -> tuple[set[str], set[str]]:
    """(source ids, filenames) the pack index reports as fully indexed."""
    from puffpai.knowledge.auto_index import STATUS_COMPLETE

    ids: set[str] = set()
    names: set[str] = set()
    for record in records:
        if str(record.get("status")) != STATUS_COMPLETE:
            continue
        source_id = str(record.get("source_id") or "")
        if source_id:
            ids.add(source_id)
        filename = os.path.basename(str(record.get("filename") or ""))
        if filename:
            names.add(filename.lower())
    return ids, names


#: ``knowledge.sources.skipped`` values that mean **nothing was ever offered**:
#: the owner skipped knowledge setup (``--skip-knowledge``) or skipped the
#: sourcing step itself. Neither run showed the owner a catalog, so neither run
#: has anything the owner "chose". The other reasons - ``non_interactive``,
#: ``offline_build`` - are runs where a plan exists and was not carried out, and
#: those stay reported exactly as they are.
SOURCING_NEVER_OFFERED = ("skipped", "owner")


def sourcing_skipped(manifest: Optional[dict[str, Any]]) -> str:
    """The skip reason when this build never offered a source, else ``""``.

    One reader for the whole product. It is what keeps ``prove``, this module's
    ``source_status`` and the closing screen from telling an owner who skipped
    knowledge setup that packs "were chosen during the build and never
    downloaded" - nothing was chosen, because nothing was offered.
    """
    knowledge = (manifest or {}).get("knowledge") or {}
    block = knowledge.get("sources") or {}
    reason = str(block.get("skipped") or "") if isinstance(block, dict) else ""
    return reason if reason in SOURCING_NEVER_OFFERED else ""


def _manifest_sources(manifest: Optional[dict[str, Any]]) -> tuple[list[str], list[str], list[str]]:
    """(offered ids, fetched ids, pack ids) as the manifest recorded them.

    A build whose sourcing was skipped reports no offered ids and no packs:
    what is on disk is still read and still reported, because that is an
    observation, but nothing is carried forward as "chosen".
    """
    knowledge = (manifest or {}).get("knowledge") or {}
    block = knowledge.get("sources") or {}
    skipped = bool(sourcing_skipped(manifest))
    offered = ([] if skipped
               else [str(i) for i in (block.get("offered") or []) if i])
    fetched: list[str] = []
    for entry in block.get("fetched") or []:
        if isinstance(entry, dict):
            if entry.get("id"):
                fetched.append(str(entry["id"]))
        elif entry:
            fetched.append(str(entry))
    packs = ([] if skipped
             else [str(p) for p in (knowledge.get("packs") or []) if p])
    return offered, fetched, packs


def _source_item(source_id: str, entry: dict[str, Any], status: str,
                 *, selected_by: list[str], disk: Optional[dict[str, Any]],
                 pack_index_note: str = "") -> dict[str, Any]:
    kind = str(entry.get("kind") or "")
    form = FORM_LIVE_SOURCE if kind == "docs" else FORM_DOCUMENTS
    item: dict[str, Any] = {
        "id": source_id,
        "label": str(entry.get("label") or source_id),
        "kind": kind or "unknown",
        "form": form,
        "status": status,
        "domain": str(entry.get("domain") or ""),
        "license": str(entry.get("license") or ""),
        "license_url": str(entry.get("license_url") or ""),
        "commercial_use": bool(entry.get("commercial_use", False)),
        "what_it_is": str(entry.get("what_it_is") or ""),
        "source_url": str(entry.get("source_url") or ""),
        "size_mb": entry.get("size_mb"),
        "size_label": _size_label(entry.get("size_mb")),
        "selected_by": list(selected_by),
        "command": "",
        "note": "",
    }
    if status == STATUS_SELECTED_NOT_DOWNLOADED:
        item["command"] = FETCH_COMMAND.format(source_id=source_id)
        chosen = ("You added this" if ADDED_BY_OWNER in selected_by
                  else "Chosen during the build")
        item["note"] = (
            f"{chosen}. Nothing was downloaded yet: this is "
            f"{item['size_label']}."
        )
    elif status == STATUS_OFFERED:
        item["command"] = FETCH_COMMAND.format(source_id=source_id)
        item["note"] = "Suggested during the build. Not chosen, not downloaded."
    elif status == STATUS_FETCHED_NOT_INDEXED and form == FORM_LIVE_SOURCE:
        item["note"] = (
            "A live source, not a shelf of documents: the address and what it "
            "covers are on file, and nothing was indexed."
        )
    elif status == STATUS_FETCHED_NOT_INDEXED:
        item["command"] = SYNC_COMMAND
        item["note"] = pack_index_note or (
            "Downloaded and waiting. It is indexed on the next start, or now "
            "with the command."
        )
    if pack_index_note:
        # "The record says it was downloaded and it is not here" beats every
        # other reading of fetched_not_indexed, so it is the last word.
        item["note"] = pack_index_note
        item["command"] = ""
    if disk:
        item["filename"] = disk.get("filename", "")
        item["bytes"] = disk.get("bytes", 0)
        item["fetched_at"] = disk.get("fetched_at", "")
    return item


def source_status(
    manifest: Optional[dict[str, Any]] = None,
    *,
    registry: Optional[dict[str, Any]] = None,
    data_dir: Optional[str] = None,
    zim_dir: Optional[str] = None,
    pack_records: Optional[list[dict]] = None,
) -> dict[str, Any]:
    """Every source and pack this build knows about, with one honest status each.

    Reads only: the manifest, the shipped registry, the two download folders,
    and the pack index. Never raises - a missing manifest is a build that chose
    nothing, which is a real answer rather than an error.
    """
    if manifest is None:
        try:
            from puffpai import build_manifest
            manifest = build_manifest.read_manifest()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read the build manifest: %s", exc)
            manifest = None

    rows = _load_registry(registry)
    offered, fetched, packs = _manifest_sources(manifest)

    sources_dir = _sources_dir(data_dir)
    zim_folder = _zim_dir(zim_dir)
    disk = on_disk_sources(sources_dir, zim_folder)

    records = _pack_records(pack_records)
    indexed_ids, indexed_files = _indexed_ids_and_files(records)

    # Which registry rows each chosen pack would need.
    selected_by: dict[str, list[str]] = {}
    unmapped_packs: list[str] = []
    for pack in packs:
        mapped = pack_source_ids(pack)
        if not mapped:
            unmapped_packs.append(pack)
            continue
        for source_id in mapped:
            selected_by.setdefault(source_id, []).append(pack)
    # Books the owner added after the build are chosen too, even though the
    # build record never saw them: the overlay row is the record of that yes.
    for source_id, row in rows.items():
        if str(row.get("provenance") or "") == "owner_added":
            selected_by.setdefault(source_id, []).append(ADDED_BY_OWNER)

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    ordered = list(dict.fromkeys(
        list(disk.keys()) + fetched + list(selected_by.keys()) + offered))
    for source_id in ordered:
        if source_id in seen:
            continue
        seen.add(source_id)
        entry = rows.get(source_id) or _library_entry(source_id) or {"id": source_id}
        present = disk.get(source_id)
        on_this_disk = present is not None
        kind = str(entry.get("kind") or "")
        is_live = kind == "docs"

        if on_this_disk or source_id in fetched:
            if not is_live and (
                    source_id in indexed_ids
                    or (on_this_disk
                        and str(present.get("filename", "")).lower() in indexed_files)):
                # Chunks are in the knowledge store. That stays true even if the
                # pack file has since been deleted from the folder.
                status = STATUS_INDEXED
            elif not on_this_disk:
                # The manifest says it was fetched and the file is not here:
                # deleted, or the data dir moved. Not indexed either way.
                status = STATUS_FETCHED_NOT_INDEXED
            elif is_live:
                status = STATUS_FETCHED_NOT_INDEXED
            elif source_id in indexed_ids or \
                    str(present.get("filename", "")).lower() in indexed_files:
                status = STATUS_INDEXED
            else:
                status = STATUS_FETCHED_NOT_INDEXED
        elif source_id in selected_by:
            status = STATUS_SELECTED_NOT_DOWNLOADED
        else:
            status = STATUS_OFFERED

        note = ""
        if status == STATUS_FETCHED_NOT_INDEXED and not on_this_disk:
            note = ("The build record says this was downloaded, and the file is "
                    "not in the knowledge folders on this machine.")
        items.append(_source_item(source_id, entry, status,
                                  selected_by=selected_by.get(source_id, []),
                                  disk=present, pack_index_note=note))

    for pack in unmapped_packs:
        items.append({
            "id": pack,
            "label": pack.split(":", 1)[-1].replace("_", " ").title(),
            "kind": "pack",
            "form": FORM_DOCUMENTS,
            "status": STATUS_SELECTED_NOT_DOWNLOADED,
            "domain": "",
            "license": "",
            "license_url": "",
            "commercial_use": False,
            "what_it_is": "",
            "source_url": "",
            "size_mb": None,
            "size_label": "large download",
            "selected_by": [pack],
            "command": SYNC_COMMAND,
            "note": ("Chosen during the build. BuildBot cannot download this "
                     "one for you (its license is not confirmed): put the .zim "
                     "file in the knowledge folder, then run the command."),
        })

    counts = {
        STATUS_INDEXED: 0,
        STATUS_FETCHED_NOT_INDEXED: 0,
        STATUS_SELECTED_NOT_DOWNLOADED: 0,
        STATUS_OFFERED: 0,
    }
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1

    return {
        "schema": SCHEMA,
        "items": items,
        "counts": counts,
        "packs_chosen": list(packs),
        "zim_dir": zim_folder,
        "sources_dir": sources_dir,
    }


def shelf_rows(pack_records: Optional[list[dict]] = None, *,
               registry: Optional[dict[str, Any]] = None,
               skip_names: Any = ()) -> list[dict[str, Any]]:
    """The downloaded books, shaped like the Knowledge shelf's other rows.

    ``GET /knowledge/libraries`` listed only uploads and hand-indexed
    libraries, so a book fetched by the wizard or by Find more answered in
    chat and never showed on the shelf. Read from the pack index: every book
    whose learning finished. Never raises.
    """
    from puffpai.knowledge.auto_index import STATUS_COMPLETE

    rows = _load_registry(registry)
    skip = {str(n).strip().lower() for n in skip_names or ()}
    out: list[dict[str, Any]] = []
    for record in _pack_records(pack_records):
        if str(record.get("status")) != STATUS_COMPLETE:
            continue
        source_id = str(record.get("source_id") or "")
        filename = os.path.basename(str(record.get("filename") or ""))
        entry = rows.get(source_id) or (_library_entry(source_id)
                                        if source_id else None) or {}
        name = str(entry.get("label") or record.get("name")
                   or os.path.splitext(filename)[0] or source_id or "Downloaded book")
        if name.strip().lower() in skip:
            continue
        skip.add(name.strip().lower())
        try:
            size = os.path.getsize(str(record.get("path") or ""))
        except OSError:
            size = 0
        indexed_at = str(record.get("indexed_at") or "")
        out.append({
            "name": name, "collection": "", "zone": "knowledge",
            "library_id": f"book:{source_id or filename}",
            # A book whose quick stage is done answers from every article
            # already, before (or without) its deeper passages.
            "chunk_count": (max(int(record.get("chunks") or 0), int(record.get("docs") or 0))
                            if record.get("quick") == "complete"
                            else int(record.get("chunks") or 0)), "size": size,
            "added": indexed_at, "uploaded_at": "", "is_upload": False,
            "source_available": False, "source_content_type": "",
            "downloaded": True,
        })
    return out


__all__ = [
    "shelf_rows",
    "SCHEMA",
    "STATUS_INDEXED",
    "STATUS_FETCHED_NOT_INDEXED",
    "STATUS_SELECTED_NOT_DOWNLOADED",
    "STATUS_OFFERED",
    "FORM_DOCUMENTS",
    "FORM_LIVE_SOURCE",
    "FETCH_COMMAND",
    "ADDED_BY_OWNER",
    "pack_source_ids",
    "on_disk_sources",
    "source_status",
]
