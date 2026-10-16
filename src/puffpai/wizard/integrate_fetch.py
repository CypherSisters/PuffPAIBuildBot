"""The bytes half of today's Zone 2 step, lifted out of the prompting.

Integrate's ``apply`` is the only place in the wizard that downloads, and it
does it through the door that already exists: ``setup_ops.source_preview``
(the consent screen: license, size, address, all from the registry, no socket)
and then ``setup_ops.fetch_source`` with the acknowledgement a person gave.
**No new network call site is added anywhere in this split** - the egress-door
tests are the contract, and they test that path.

What lives here rather than in the package: the loops, so the skill file stays
a readable statement of what Integrate decides.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

from typing import Any, Optional

from puffpai.wizard import sources_plan


def fetch_plan(items: list[dict[str, Any]], selected: list[str]
               ) -> tuple[list[str], list[str]]:
    """(fetch now, leave for later) for a selection, by size.

    A source bigger than :data:`sources_plan.HEAVY_BYTES` is **deferred by
    default**. Nobody should discover that their wizard is 55 GB into a
    download because they ticked "Wikipedia" two screens ago; the fetch command
    for a deferred source is recorded as a gap, which is a thing they can do
    later on purpose.
    """
    by_id = {item["id"]: item for item in items}
    now, later = [], []
    for source_id in selected:
        item = by_id.get(source_id) or {}
        size = item.get("bytes")
        if size is not None and int(size) > sources_plan.HEAVY_BYTES:
            later.append(source_id)
        else:
            now.append(source_id)
    return now, later


def _record_library_choice(source_id: str) -> None:
    """Write a Kiwix book the owner chose into this build's overlay.

    A book found in the Kiwix library (id ``kiwix:...``) is not on BuildBot's
    own list, so the fetcher would not know it. Called only after the owner's
    yes; the row names the book, never an address. A shipped id, an unknown id
    or a book with no known license writes nothing, and the fetch below then
    refuses it in plain words.
    """
    from puffpai import source_registry
    from puffpai.catalogs import kiwix_opds, knowledge_resolver

    if kiwix_opds.split_book_id(source_id) is None:
        return
    if source_registry.get_source(source_id) is not None:
        return
    cand = knowledge_resolver.candidate_by_id(source_id)
    if cand is None or not cand.get("fetchable"):
        return
    try:
        source_registry.add_owner_row(
            source_registry.row_from_candidate(cand, added_via="wizard"))
    except (ValueError, OSError):
        return


def run_fetches(source_ids: list[str], posture: dict,
                *, acknowledged: bool) -> dict[str, Any]:
    """Fetch each id through today's path. Returns today's sourcing halves.

    Nothing is downloaded without ``acknowledged``: the size and the license
    were on the card, and a run where nobody read the card is a run where
    nobody accepted them. A refusal is recorded, never raised - an undownloaded
    source is a gap, not a stop (spec C.6).
    """
    from puffpai import setup_ops

    fetched: list[dict[str, Any]] = []
    not_fetched: list[str] = []
    for source_id in source_ids:
        if acknowledged:
            _record_library_choice(source_id)
        preview = setup_ops.source_preview(source_id, posture)
        if not preview.get("ok"):
            not_fetched.append(source_id)
            continue
        if not acknowledged:
            not_fetched.append(source_id)
            continue
        result = setup_ops.fetch_source(source_id, posture,
                                        size_acknowledged=True)
        if result.get("ok"):
            fetched.append(setup_ops.source_manifest_entry(result))
        else:
            not_fetched.append(source_id)
    return {"fetched": fetched, "not_fetched": not_fetched}


def own_data_block(named: list[str], *, skip: bool) -> dict[str, Any]:
    """Survey and approve exactly the folders that were named. Nothing else.

    The rule today's step is built around survives the move: a folder nobody
    named is never opened, and a declined folder leaves no trace - not a path,
    not a count.
    """
    from puffpai import setup_ops

    empty = setup_ops.data_sources_block({"approved": []})
    if skip or not named:
        return empty
    surveyed = setup_ops.survey_data_locations([str(p) for p in named])
    approve = [str(loc.get("path")) for loc in (surveyed.get("locations") or [])
               if loc.get("surveyed")]
    return setup_ops.data_sources_block(
        setup_ops.approve_data_locations(surveyed, approve))


def own_data_and_learn(named: list[str], *, skip: bool,
                       ingest: Any = None) -> dict[str, Any]:
    """Survey and approve exactly the named folders, then learn their files.

    ``{"block": <the manifest block, unchanged>, "learned": <counts + lines>}``.
    The block is exactly :func:`own_data_block`'s (paths redacted, counts
    only). Learning goes through the upload path's PII screen; the vault is a
    fresh, locked instance here, so a file with high or critical personal
    details is held back rather than learned (fail closed), and said so.
    """
    from puffpai import data_sources, setup_ops

    empty = setup_ops.data_sources_block({"approved": []})
    none = {"learned": 0, "lines": []}
    if skip or not named:
        return {"block": empty, "learned": none}
    surveyed = setup_ops.survey_data_locations([str(p) for p in named])
    approve = [str(loc.get("path")) for loc in (surveyed.get("locations") or [])
               if loc.get("surveyed")]
    selection = setup_ops.approve_data_locations(surveyed, approve)
    try:
        from puffpai.security.vault import Vault
        vault = Vault()
    except Exception:  # noqa: BLE001 - no vault object: read nothing (fail closed)
        vault = None
    if vault is None:
        learned = {"learned": 0, "lines": [
            "Your folders were recorded but not read: the vault that protects "
            "private details could not be opened."]}
    else:
        learned = data_sources.learn_approved(selection, vault=vault, ingest=ingest)
    return {"block": setup_ops.data_sources_block(selection), "learned": learned}


def index_handoff(zim_dir: str, *, auto_index: bool,
                  start: bool = True) -> dict[str, Any]:
    """What is waiting, how long it will take, and whether it was started.

    The estimate is measured from this machine's own history or it is not given
    at all; there is no calibration run, for the same reason there never was
    one - calibrating means indexing in the foreground.
    """
    from puffpai import setup_ops

    if not zim_dir:
        return {"packs": 0, "estimate": None, "started": 0, "waiting": False}
    volume = setup_ops.pending_index_volume(zim_dir)
    packs = int(volume.get("packs") or 0)
    if not packs:
        return {"packs": 0, "estimate": None, "started": 0, "waiting": False}
    estimate = setup_ops.index_estimate_for(volume)
    started = 0
    if auto_index and start:
        started = setup_ops.start_indexing(zim_dir)
    return {"packs": packs, "estimate": estimate, "started": int(started),
            "waiting": True}


def seconds_from(estimate: Optional[dict[str, Any]]) -> tuple[Optional[float], str]:
    """(seconds, basis) for a card, or (None, "unknown"). Never invented."""
    if not isinstance(estimate, dict):
        return None, "unknown"
    basis = str(estimate.get("basis") or "unknown")
    if basis not in ("measured", "calibrated"):
        return None, "unknown"
    seconds = estimate.get("central_seconds")
    if not isinstance(seconds, (int, float)):
        return None, "unknown"
    return float(seconds), basis


__all__ = ["fetch_plan", "run_fetches", "own_data_block", "index_handoff",
           "seconds_from"]
