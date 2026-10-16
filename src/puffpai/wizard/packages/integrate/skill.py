"""Integrate: fetch what the plan chose, index it, and say what really landed.

Phase 2, spec C.6. Sources decided what this bot should learn from; this is the
half that moves bytes, and it moves them only through the path that already
exists - ``setup_ops.source_preview`` then ``setup_ops.fetch_source``, the
door-gated fetcher with its guards, its provenance sidecar and its size
acknowledgement. No new network call site is introduced by the split.

The card shows, per source, **what is fetched now and what is left for later**.
A source bigger than the heavy threshold is deferred by default and its fetch
command becomes a gap, because a wizard that silently starts a 55 GB download
is a wizard nobody can interrupt.

What it writes is today's ``knowledge`` block, unchanged: ``sources`` (offered,
fetched, not_fetched, skipped) and ``own_data``, under ``block`` where
``cli._run_setup``'s manifest step reads it, and mirrored at the top of the
section in the shape ``build_sources.source_status`` and Prove already read.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os
from typing import Any

from puffpai.wizard import cards, integrate_fetch, sources_plan

MAX_PATH_OPTION = 120


def _posture(ctx) -> dict:
    posture = ctx.shared.get("posture")
    if not posture:
        try:
            posture = (ctx.read("posture") or {}).get("posture")
        except Exception:  # noqa: BLE001
            posture = {}
    return posture if isinstance(posture, dict) else {}


def _sources(ctx) -> dict:
    plan = ctx.shared.get("sources_plan")
    if not plan:
        try:
            plan = ctx.read("sources")
        except Exception:  # noqa: BLE001
            plan = {}
    return plan if isinstance(plan, dict) else {}


def _packs(ctx) -> list[str]:
    interview = ctx.shared.get("interview") or {}
    return [str(i) for i in (interview.get("selected") or [])
            if str(i).startswith("pack:")]


def _default_zim_dir(ctx) -> str:
    # The propose rule (CONVERTING.md): what this skill already wrote wins over
    # the flag and the config, so a re-drawn card shows the folder the owner
    # typed rather than the one that was suggested to them.
    applied = ctx.applied("zim_dir", default=None)
    if isinstance(applied, str) and applied:
        return applied
    args_dir = getattr(ctx.args, "zim_dir", None)
    if args_dir is not None:
        return str(args_dir)
    knowledge = getattr(ctx.cfg, "knowledge", None)
    configured = str(getattr(knowledge, "zim_dir", "") or "")
    if configured:
        return configured
    # Every build gets a knowledge folder under its own data dir, asked or
    # not: without one, packs downloaded later had nowhere to be found and
    # `puffpai knowledge sync` answered "No knowledge folder configured".
    data_dir = str(getattr(ctx.cfg, "DATA_DIR", "") or "")
    return os.path.join(data_dir, "knowledge_packs") if data_dir else ""


def _auto_index(ctx) -> bool:
    applied = ctx.applied("auto_index", default=None)
    if isinstance(applied, bool):
        return applied
    return not getattr(ctx.args, "no_auto_index", False)


def _own_data_options(ctx) -> tuple[list[dict], list[str]]:
    """(options, what is ticked) for the owner's own folders. Nothing scanned."""
    from puffpai import setup_ops

    named = [str(p) for p in (getattr(ctx.args, "own_data", None) or [])]
    options: list[dict] = []
    seen: set[str] = set()
    for path in named:
        if len(path) <= MAX_PATH_OPTION and path not in seen:
            seen.add(path)
            options.append({"value": path, "label": f"{path} - you named it"})
    if not ctx.non_interactive:
        for loc in setup_ops.data_location_suggestions():
            path = str(loc.get("path") or "")
            if not path or path in seen or len(path) > MAX_PATH_OPTION:
                continue
            seen.add(path)
            options.append({"value": path,
                            "label": f"{loc.get('recorded_path')} - "
                                     f"{loc.get('why')}"})
    return options, [p for p in named if p in seen]


def propose(ctx) -> dict:
    """Work out what would be fetched, and what is waiting to be indexed.

    Read-only: the registry for sizes, the knowledge folder for packs already
    on disk, and the suggestion list for folders - which is a list of places
    worth a look and nothing more, because nothing of the owner's is opened
    until they name it.
    """
    from puffpai import cli

    cli.enter_step("Zone 2, the Brain")
    skip = bool(getattr(ctx.args, "skip_knowledge", False))
    plan = _sources(ctx)
    items = list((plan.get("plan") or {}).get("items") or [])
    selected = [str(s) for s in (plan.get("selected") or [])]
    now, later = integrate_fetch.fetch_plan(items, selected)
    recommended_now = list(now)
    applied_now = ctx.applied("fetched", default=None)
    if isinstance(applied_now, list) and applied_now:
        # Already fetched on an earlier pass: the card shows that as what is
        # chosen, and whatever was left is still left.
        kept = [str(s) for s in applied_now if str(s) in set(selected)]
        now = kept + [s for s in now if s not in kept]
        later = [s for s in later if s not in kept]
    zim_dir = _default_zim_dir(ctx)
    auto_index = _auto_index(ctx)

    if skip:
        return {"card": _skipped_card(), "skip": True, "items": items,
                "fetch_now": [], "deferred": [], "zim_dir": "",
                "auto_index": auto_index, "own_data": []}

    waiting = integrate_fetch.index_handoff(zim_dir, auto_index=auto_index,
                                            start=False)
    seconds, basis = integrate_fetch.seconds_from(waiting.get("estimate"))
    bytes_now, all_known = sources_plan.total_bytes(items, now)
    by_id = {item["id"]: item for item in items}

    fields: list[dict[str, Any]] = [
        {"id": "zim_dir", "kind": "text", "label": "Where the books are kept",
         "current": zim_dir, "max_length": 400, "path_like": True,
         "help": "Books put in this folder are learned on their own. Leave it "
                 "blank to keep this folder; type a folder, not yes or no."},
        {"id": "auto_index", "kind": "toggle",
         "label": "Learn new books in the background", "current": auto_index,
         "help": "Off means nothing is learned until you run "
                 "`puffpai knowledge sync`."},
    ]
    # Only rows the plan actually described can be drawn. An id with no row
    # behind it is still fetched - it came from --fetch-source - but there is
    # nothing honest to put on a screen about it.
    shown = [source_id for source_id in (now + later) if source_id in by_id]
    if shown:
        fields.append({
            "id": "fetch_now", "kind": "multi_choice",
            "label": "Download now, or leave for later",
            "options": [{"value": source_id,
                         "label": sources_plan.option_label(by_id[source_id])}
                        for source_id in shown],
            "current": [source_id for source_id in now if source_id in by_id],
            "proposed": [source_id for source_id in recommended_now
                         if source_id in by_id],
            "help": "Anything left unticked stays in the plan, with the "
                    "command that downloads it later.",
        })
    elif now or later:
        fields.append({
            "id": "named_sources", "kind": "info",
            "label": "Named on the command line",
            "current": ", ".join(now + later),
        })
    for number, source_id in enumerate(later, 1):
        item = by_id.get(source_id) or {}
        fields.append({
            "id": f"deferred_{number}", "kind": "info",
            "label": f"{item.get('label', source_id)} is left for later",
            "current": f"{item.get('size_label', 'a large download')} - too big "
                       f"to fetch while you wait. Tick it above if you want it "
                       f"now.",
        })
    options, ticked = _own_data_options(ctx)
    if options:
        fields.append({
            "id": "own_data", "kind": "multi_choice",
            "label": "Your own folders", "options": options[:30],
            "current": ticked,
            "help": "A folder you tick here is read and learned, and "
                    "anything that looks private goes to your vault (or is left "
                    "out while the vault is locked). The build record keeps "
                    "counts only, never filenames.",
        })
    if waiting.get("waiting"):
        fields.append({
            "id": "waiting", "kind": "info", "label": "Already waiting to learn",
            "current": f"{waiting['packs']} book(s) in that folder are not "
                       f"learned yet.",
        })
    # Today's words, kept: a run with nobody reading the card never accepted a
    # size and never approved a folder, and this says so on the card instead of
    # quietly fetching nothing.
    if ctx.non_interactive:
        if not getattr(ctx.args, "fetch_source", None):
            fields.append({
                "id": "no_consent", "kind": "info",
                "label": "Fetching none of them",
                "current": "This is a non-interactive run, and consent nobody "
                           "gave is not consent. Run `puffpai setup` at a "
                           "keyboard, or name what you want with "
                           "--fetch-source <id>."})
        if not ticked:
            fields.append({
                "id": "no_folders", "kind": "info",
                "label": "Reading none of your folders",
                "current": "Nothing of yours was looked at. A folder nobody "
                           "approved is a folder I have no business reading."})

    # A book downloads while building and is read on this computer after
    # that, so fetching one is never "network access after the build"; the
    # card used to say it was. Only the size can make this card weigh more.
    reasons = []
    if bytes_now > sources_plan.HEAVY_BYTES:
        reasons.append("heavy_bytes")
    card = {
        "skill": "integrate", "section": "knowledge",
        "title": "Getting it in",
        "decided": _decided(now, later, bytes_now, all_known),
        "status": "gap" if later else "ready",
        "consequential": bool(reasons),
        "consequential_reasons": reasons,
        "estimate": {"bytes": bytes_now, "seconds": seconds, "basis": basis},
        "fields": fields,
        "gaps": [_defer_gap(by_id.get(source_id) or {"id": source_id,
                                                     "label": source_id,
                                                     "size_label": ""})
                 for source_id in later[:6]],
    }
    return {"card": card, "skip": False, "items": items, "fetch_now": now,
            "deferred": later, "shown": shown, "zim_dir": zim_dir,
            "auto_index": auto_index, "own_data": ticked, "waiting": waiting}


def _decided(now: list[str], later: list[str], size: int, known: bool) -> str:
    if not now and not later:
        return "Nothing to download. Books you put in the folder are learned on their own."
    where = sources_plan.size_label(size / sources_plan.MB) if known else "size not known"
    parts = [f"{len(now)} book(s) downloaded now ({where})"]
    if later:
        parts.append(f"{len(later)} left for later")
    return ", ".join(parts) + "."


def _defer_gap(item: dict) -> dict:
    source_id = str(item.get("id") or "")
    return {
        "skill": "integrate",
        "what": f"{item.get('label') or source_id} was chosen and not "
                f"downloaded.",
        "why": f"It is {item.get('size_label') or 'a large download'}, so the "
               f"build left it rather than downloading it while you waited.",
        "impact": "Your bot will say it does not know, rather than guess, on "
                  "what that source covers.",
        "fix": {"kind": "command", "id": "source.fetch",
                "args": {"source_id": source_id}},
    }


def _skipped_card() -> dict:
    return {
        "skill": "integrate", "section": "knowledge",
        "title": "Getting it in",
        "decided": "Knowledge setup was skipped. Point the config at a pack "
                   "folder when you have one.",
        "status": "ready",
        "consequential": False,
        "estimate": {"bytes": 0, "seconds": 0, "basis": "calibrated"},
        "fields": [{"id": "skipped", "kind": "info", "label": "Skipped",
                    "current": "Nothing was fetched and no folder was read."}],
    }


def apply(ctx, answers: dict) -> dict:
    """Do it: the folder, the fetches, the owner's folders, the handoff.

    In exactly today's order, because the order is load-bearing - the knowledge
    folder is configured *before* the fetches, which is how a ZIM pack lands in
    the watched folder and gets indexed with no further wiring.
    """
    from puffpai import setup_ops

    proposal = ctx.proposal or {}
    if proposal.get("skip"):
        # The same record today's step writes when it is skipped: nothing was
        # offered, so nothing downstream may report a source as chosen.
        block = {"sources": {"offered": [], "fetched": [], "not_fetched": [],
                             "skipped": "skipped"}}
        return {"section": "knowledge", "data": {"block": block},
                "shared": {"knowledge_block": block}}

    plan = _sources(ctx)
    items = list(proposal.get("items") or [])
    posture = _posture(ctx)

    zim_dir = str(answers.get("zim_dir", proposal.get("zim_dir", "")) or "")
    # The card refuses a non-path answer before it gets here; this is the same
    # rule applied again for a caller that reached apply() without the card,
    # because "no" was once accepted and written to the config as a folder.
    if zim_dir and cards.path_refusal(zim_dir):
        zim_dir = str(proposal.get("zim_dir", "") or "")
    auto_index = bool(answers.get("auto_index",
                                  proposal.get("auto_index", True)))
    if zim_dir and not os.path.isdir(zim_dir):
        # The owner accepted the folder on the card (or it is the default under
        # this build's own data dir); an empty folder is created so later
        # downloads and `puffpai knowledge sync` have somewhere to look.
        try:
            os.makedirs(zim_dir, exist_ok=True)
        except OSError:
            zim_dir = ""
    setup_ops.set_knowledge_config(zim_dir, auto_index=auto_index)

    selected = [str(s) for s in (plan.get("selected") or [])]
    if "fetch_now" in answers:
        shown = set(proposal.get("shown") or ())
        ticked = [str(s) for s in (answers.get("fetch_now") or [])]
        # An id the card could not draw was named on the command line, so it
        # was never on the screen to untick.
        now = ticked + [s for s in (proposal.get("fetch_now") or ())
                        if s not in shown and s not in ticked]
        deferred = [s for s in selected if s not in now]
    else:
        now = list(proposal.get("fetch_now") or [])
        deferred = list(proposal.get("deferred") or [])

    # Non-interactive is the one case where the card was never read, so the
    # sizes on it were never accepted. ``--accept-source-size`` is the separate,
    # deliberate yes today's step demands, and it stays the yes here.
    acknowledged = (not ctx.non_interactive
                    or bool(getattr(ctx.args, "accept_source_size", False)))
    outcome = integrate_fetch.run_fetches(now, posture,
                                          acknowledged=acknowledged)
    not_fetched = list(outcome["not_fetched"]) + [s for s in deferred
                                                  if s not in outcome["not_fetched"]]

    record: dict[str, Any] = {
        "offered": [str(s) for s in (plan.get("offered") or [])],
        "fetched": outcome["fetched"],
        "not_fetched": not_fetched,
        "skipped": _skipped_reason(ctx, plan),
    }
    named = ([str(p) for p in (answers.get("own_data") or [])]
             if "own_data" in answers
             else list(proposal.get("own_data") or []))
    folders = integrate_fetch.own_data_and_learn(
        named, skip=bool(getattr(ctx.args, "skip_own_data", False)))
    own_data = folders["block"]

    block = {"sources": record, "own_data": own_data}
    handoff = integrate_fetch.index_handoff(zim_dir, auto_index=auto_index)

    fetched_ids = [str(entry.get("id")) for entry in outcome["fetched"]]
    data = {
        # ``block`` is what the manifest step reads, in today's shape exactly.
        "block": block,
        "sources": record,
        "own_data": own_data,
        # Counts only: what was learned from the owner's folders, through the
        # same PII screen as an upload. Never a filename.
        "own_data_learned": {k: v for k, v in folders["learned"].items()},
        "packs": _packs(ctx),
        "zim_dir": zim_dir,
        "auto_index": auto_index,
        "fetched": fetched_ids,
        "deferred": deferred,
        "status": ("fetched" if fetched_ids
                   else "deferred" if deferred else "none"),
        "index": {"packs": handoff["packs"], "started": handoff["started"]},
    }
    result: dict[str, Any] = {
        "section": "knowledge", "data": data,
        "shared": {"knowledge_block": block, "knowledge_status": data["status"]},
    }
    by_id = {item["id"]: item for item in items}
    gaps = [_defer_gap(by_id.get(source_id) or {"id": source_id,
                                                "label": source_id,
                                                "size_label": ""})
            for source_id in not_fetched]
    if gaps:
        result["gaps"] = gaps
    return result


def _skipped_reason(ctx, plan: dict):
    """Today's ``skipped`` value, decided from the same three facts."""
    planned = (plan.get("plan") or {}).get("skipped")
    if planned:
        return planned
    if ctx.non_interactive and not (getattr(ctx.args, "fetch_source", None) or []):
        return "non_interactive"
    return None


def verify(ctx) -> dict:
    """Observe the disk and the index, through ``build_sources.source_status``.

    This is the function that makes it impossible for the Knowledge
    page to read zero while the record claims five. It reads provenance
    sidecars beside real files and the pack index - never the proposal.
    """
    from puffpai.knowledge import build_sources

    section = ctx.own()
    record = (section.get("block") or {}).get("sources") or {}
    fetched = [str(entry.get("id")) for entry in (record.get("fetched") or [])
               if isinstance(entry, dict)]
    manifest = {"knowledge": {"sources": record,
                              "packs": section.get("packs") or []}}
    try:
        status = build_sources.source_status(
            manifest,
            data_dir=str(getattr(ctx.cfg, "DATA_DIR", "") or ""),
            zim_dir=str(section.get("zim_dir") or ""))
    except Exception as exc:  # noqa: BLE001 - unreachable is a gap, never a stop
        return {"status": "gap", "evidence": f"the disk could not be read ({exc})",
                "gap": {"skill": "integrate",
                        "what": "This build could not check what is really on "
                                "disk.",
                        "why": f"Reading the knowledge folders failed: {exc}.",
                        "impact": "What your bot knows is unverified until this "
                                  "is re-run.",
                        "fix": {"kind": "command", "id": "setup.rerun",
                                "args": {}}}}

    items = {str(item.get("id")): item for item in status.get("items") or []}
    for source_id in fetched:
        item = items.get(source_id) or {}
        if not item.get("filename"):
            return {
                "status": "gap",
                "evidence": f"{source_id} is recorded as fetched and has no "
                            f"file with a provenance record on this machine",
                "gap": {
                    "skill": "integrate",
                    "what": f"{item.get('label') or source_id} is recorded as "
                            f"downloaded and is not on this machine.",
                    "why": "No file with a provenance record for it is in the "
                           "knowledge folders.",
                    "impact": "Your bot will not be able to answer from it.",
                    "fix": {"kind": "command", "id": "source.fetch",
                            "args": {"source_id": source_id}},
                },
            }

    counts = status.get("counts") or {}
    unindexed = [item for item in status.get("items") or ()
                 if item.get("status") == build_sources.STATUS_FETCHED_NOT_INDEXED
                 and item.get("form") == build_sources.FORM_DOCUMENTS]
    if unindexed:
        item = unindexed[0]
        return {
            "status": "gap",
            "evidence": f"{len(unindexed)} source(s) are on disk and not in the "
                        f"knowledge store yet",
            "gap": {
                "skill": "integrate",
                # Prove's own words for the same book, so the closing
                # screen lists it once (runner.record_gap).
                "what": f"{item.get('label')} is downloaded but not searchable "
                        f"yet.",
                "why": str(item.get("note") or
                           "The file is on this computer, but your bot has not "
                           "learned any of it yet."),
                "impact": "Your bot will not find anything from it until it is "
                          "indexed.",
                "fix": {"kind": "command", "id": "knowledge.sync", "args": {}},
            },
        }

    waiting = [item for item in status.get("items") or ()
               if item.get("status") == build_sources.STATUS_SELECTED_NOT_DOWNLOADED]
    if waiting:
        item = waiting[0]
        return {
            "status": "gap",
            "evidence": f"{len(waiting)} chosen source(s) were never downloaded",
            "gap": _defer_gap({"id": str(item.get("id")),
                               "label": item.get("label"),
                               "size_label": item.get("size_label")}),
        }

    return {"status": "pass",
            "evidence": f"{counts.get(build_sources.STATUS_INDEXED, 0)} indexed, "
                        f"{len(fetched)} fetched this run, nothing chosen is "
                        f"missing"}
