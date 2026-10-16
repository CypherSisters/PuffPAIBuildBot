"""Sources: where this bot learns from. The plan, and not one byte.

Phase 2, spec C.4. Today's Zone 2 step asked the sourcing questions and then
downloaded inside the same breath; this half asks only "what should it learn
from", writes that down, and hands it to Integrate. Nothing here opens a
socket, and the card says so in the spec's own words: *this is a plan, nothing
is downloaded yet.*

What the plan carries:

* the deterministic registry search over the goal - today's matcher, which
  cites the word that matched - plus the ``pack:*`` ids the owner chose in the
  interview, mapped onto downloads by ``build_sources.pack_source_ids`` (a data table);
* paid and keyed rows **listed and unselectable** (spec A.5, the owner's answer
  5): they exist, they cost money or need a key, and this build has nowhere
  safe to put a key, so they are recorded as ``declared, no key path yet`` and
  gapped rather than half-offered;
* books from the wider Kiwix library (the dated list that ships with
  BuildBot, searched offline), each citing the catalog words that matched;
  the local model may suggest extra search WORDS for that search, never a
  website, a URL or an id;
* the **tool needs** this kind of bot implies, as plain catalog ids, for the
  Tools skill to resolve later.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

from typing import Any

from puffpai.wizard import sources_plan, sources_suggest

#: The words that go on the card, unchanged, every time (spec C.4).
PLAN_NOTE = "This is a plan, nothing is downloaded yet."

#: When neither BuildBot's list nor the library had anything.
NOTHING_FOUND = ("Nothing in BuildBot's list or the Kiwix library matched that. "
                 "After the build you can look again with `puffpai sources search "
                 "<words>`, or add your own files.")


def _interview(ctx) -> dict:
    """What the owner said this bot is for. Persona first, shared as fallback."""
    try:
        persona = ctx.read("persona")
    except Exception:  # noqa: BLE001 - an undeclared read is the runner's business
        persona = {}
    interview = persona.get("interview") if isinstance(persona, dict) else None
    if not interview:
        interview = ctx.shared.get("interview") or {}
    return interview if isinstance(interview, dict) else {}


def _posture(ctx) -> dict:
    posture = ctx.shared.get("posture")
    if not posture:
        try:
            posture = (ctx.read("posture") or {}).get("posture")
        except Exception:  # noqa: BLE001
            posture = {}
    return posture if isinstance(posture, dict) else {}


def _gates(ctx, posture: dict) -> dict:
    gates = ctx.shared.get("gates")
    if not gates:
        from puffpai import setup_ops
        gates = setup_ops.posture_gates(posture)
    return gates if isinstance(gates, dict) else {}


def _skip_reason(ctx, gates: dict) -> str:
    """Why there is no plan to make, in today's words, or ``""``."""
    if getattr(ctx.args, "skip_knowledge", False):
        return "skipped"
    if getattr(ctx.args, "skip_sourcing", False):
        return "owner"
    if not gates.get("may_offer_online_sourcing"):
        return "offline_build"
    return ""


def _machine(ctx) -> dict:
    """What Discover found: the Ollama address and the models it answered with."""
    machine = {}
    try:
        machine = ctx.read("machine") or {}
    except Exception:  # noqa: BLE001 - an undeclared read is the runner's business
        machine = {}
    out = dict(machine)
    for key in ("ollama_url", "installed_models"):
        if ctx.shared.get(key):
            out[key] = ctx.shared[key]
    return out


def _suggest_client(ctx):
    """The client Sources asks, or ``None`` - and ``None`` opens no socket.

    Order: a client handed in (tests, and anything that wants to inject one),
    then this build's own local model. The model is only asked when Discover
    actually saw an Ollama answer with models installed, so a build that never
    surveyed one never calls out to find out.
    """
    if getattr(ctx.args, "no_suggestions", False):
        return None
    injected = ctx.shared.get("source_suggest_client")
    if injected is not None:
        return injected
    machine = _machine(ctx)
    installed = [str(m) for m in (machine.get("installed_models") or [])]
    url = str(machine.get("ollama_url") or "")
    if not url or not installed:
        return None
    wanted = [str(ctx.shared.get("chosen_model") or ""),
              str(getattr(getattr(ctx.cfg, "ollama", None), "model", "") or "")]
    model = next((m for m in wanted if m and m in installed), installed[0])
    return sources_suggest.default_client(url, model)


def _search_words(ctx, goal: str) -> tuple[list[str], str]:
    """Extra search words for *goal* from the local model, asked at most once.

    Propose stays side-effect free and quiet: no call without a goal, and none
    when this same goal was already answered - the words are cached in the
    applied ``sources`` section, the only place they are written. The model
    runs on this computer, so nothing leaves it.
    """
    goal = str(goal or "").strip()
    cached = ctx.applied("search_terms", default=None)
    if (goal and isinstance(cached, list)
            and str(ctx.applied("search_terms_for", default="") or "") == goal):
        return [str(t) for t in cached], ""
    detail = sources_suggest.search_terms(goal, client=_suggest_client(ctx))
    return detail["terms"], detail["note"]


def _builder_age(interview: dict):
    """The builder's age or age band, if the interview recorded one.

    Only changes the ORDER books are listed in (a young builder sees books
    written for children first). Nothing is hidden because of it.
    """
    for key in ("builder_age", "age", "age_band", "builder_age_band"):
        value = interview.get(key)
        if value not in (None, ""):
            return value
    return None


def propose(ctx) -> dict:
    """Search the registry and draw the plan. Read-only (spec A.3).

    Every figure comes from the shipped registry, so this works with the WAN
    unplugged - which matters, because the build that most needs an honest plan
    is the one that cannot go and look.
    """
    from puffpai import cli

    cli.enter_step("Zone 2, the Brain")
    interview = _interview(ctx)
    posture = _posture(ctx)
    gates = _gates(ctx, posture)
    goal = str(interview.get("goal") or "")
    packs = [str(i) for i in (interview.get("selected") or [])
             if str(i).startswith("pack:")]

    skipped = _skip_reason(ctx, gates)
    if skipped:
        return {"card": _skipped_card(skipped), "plan": _empty_plan(goal, skipped),
                "items": [], "selected": [], "suggested": [], "tool_needs": []}

    terms, terms_note = _search_words(ctx, goal)
    plan = sources_plan.plan_rows(goal, packs, terms=terms,
                                  age=_builder_age(interview))
    plan["search_terms"] = terms
    items = plan["items"]
    fetchable = [item for item in items if item["selectable"]]
    blocked = [item for item in items if not item["selectable"]]
    recommended = sources_plan.default_selection(items, goal)
    # The propose rule (CONVERTING.md): what was applied is what the card shows
    # as chosen; the registry's default selection stays the recommendation. A
    # re-drawn card used to show the first proposal again, so an owner who had
    # unticked a 55 GB pack was shown it ticked.
    applied = ctx.applied("selected", default=None)
    known = {item["id"] for item in fetchable}
    selected = ([str(s) for s in applied if str(s) in known]
                if isinstance(applied, list) else recommended)
    jobs_decide = not isinstance(applied, list) and _jobs_want_no_books(ctx)
    if jobs_decide:
        # The plan is drawn from her jobs and none is about learning, so the
        # plan offers no book: this record says the same (not "3 book(s)
        # found, 1 chosen (about 257 MB)" here while the plan says "What it
        # will know: Nothing here yet").
        selected = []
    needs = sources_plan.tool_needs(goal, items, selected)

    total, all_known = sources_plan.total_bytes(items, selected)
    fields: list[dict[str, Any]] = [
        {"id": "plan_note", "kind": "info", "label": "What this step does",
         "current": PLAN_NOTE + " Ticking a book only notes that you want it; "
                    "nothing downloads until the next step."},
    ]
    if fetchable:
        fields.append({
            "id": "selected", "kind": "multi_choice",
            "label": "What it learns from",
            "options": [{"value": item["id"],
                         "label": sources_plan.option_label(item)}
                        for item in fetchable[:40]],
            "current": [i for i in selected
                        if i in {item["id"] for item in fetchable[:40]}],
            "proposed": [i for i in recommended
                         if i in {item["id"] for item in fetchable[:40]}],
            "help": "Sizes and licenses come from the library's own list. "
                    + PLAN_NOTE,
        })
    else:
        fields.append({
            "id": "nothing_matched", "kind": "info", "label": "Nothing matched",
            "current": str(plan.get("message") or NOTHING_FOUND),
        })

    for number, item in enumerate(blocked[:6], 1):
        fields.append({
            "id": f"needs_a_key_{number}", "kind": "info",
            "label": f"{item['label']} - {sources_plan.NO_KEY_PATH}",
            "current": f"{item['why_not']}. It is listed so you know it "
                       f"exists: {item['what_it_is'] or item['source_url']}",
        })
    from puffpai.catalogs import goal_words
    looked_for = goal_words.goal_terms(goal, extra=terms)
    if looked_for:
        fields.append({
            "id": "search_words", "kind": "info", "label": "Words I looked for",
            "current": ", ".join(looked_for)
                       + (" (some suggested by your bot's own model)" if terms else ""),
        })
    elif terms_note:
        fields.append({"id": "search_words", "kind": "info",
                       "label": "Words I looked for", "current": terms_note})
    if needs:
        fields.append({
            "id": "tool_needs", "kind": "info",
            "label": "What a bot like this will need to reach for",
            "current": ", ".join(need["label"] for need in needs[:12]),
            "help": "The next-but-one step says which of these this build "
                    "actually has.",
        })
    for pack in plan.get("unmapped_packs") or ():
        fields.append({
            "id": f"unmapped_{len(fields)}", "kind": "info",
            "label": f"{_pack_words(pack)} has no download on BuildBot's list",
            "current": "Put the file in the knowledge folder yourself and it "
                       "is learned from there.",
        })

    card = {
        "skill": "sources", "section": "sources",
        "title": "What it learns from",
        "decided": (NO_LEARN_JOB if jobs_decide
                    else _decided(items, selected, total, all_known)),
        "status": "gap" if blocked else "ready",
        "consequential": total > sources_plan.HEAVY_BYTES,
        "consequential_reasons": (["heavy_bytes"]
                                  if total > sources_plan.HEAVY_BYTES else []),
        "estimate": {"bytes": total, "seconds": 0,
                     "basis": "calibrated" if all_known else "unknown"},
        "fields": fields,
        "gaps": [_keyed_gap(item) for item in blocked[:6]],
    }
    return {"card": card, "plan": plan, "items": items, "selected": selected,
            "search_terms": terms, "tool_needs": needs, "blocked": blocked}


#: The record when her jobs decide and none of them is about learning.
NO_LEARN_JOB = "No book is added: none of your jobs is about learning something."


def _jobs_want_no_books(ctx) -> bool:
    """Is the plan drawn from her jobs, with none of the ticked ones a
    "learn about" job (the plan then offers no book, proposal.skipped_with_needs)?"""
    try:
        from puffpai.wizard import proposal
        if not proposal.needs_active(ctx.shared):
            return False
        jobs = [j for j in (proposal.needs_of(ctx.shared) or {}).get("jobs") or ()
                if isinstance(j, dict) and j.get("ticked")]
    except Exception:  # noqa: BLE001 - unknown: the record stays as it was
        return False
    return bool(jobs) and not any(j.get("learn") is True for j in jobs)


def _pack_words(pack: str) -> str:
    """``pack:stackexchange_science`` -> ``Stackexchange Science``. No raw ids on a card."""
    return str(pack).split(":", 1)[-1].replace("_", " ").title()


def _decided(items: list[dict], selected: list[str], total: int,
             all_known: bool) -> str:
    if not items:
        return "Nothing matched this goal. " + PLAN_NOTE
    size = (sources_plan.size_label(total / sources_plan.MB) if all_known
            else "size not known for all of them")
    return (f"{len(items)} book(s) found, {len(selected)} chosen "
            f"({size}). " + PLAN_NOTE)


def _keyed_gap(item: dict) -> dict:
    return {
        "skill": "sources",
        "what": f"{item['label']} needs a key or a subscription, so it is "
                f"listed and not selected.",
        "why": f"{sources_plan.NO_KEY_PATH}: {item['why_not']}.",
        "impact": "Your bot will not have anything from that source, and will "
                  "say so rather than guess.",
        "fix": {"kind": "command", "id": "setup.rerun", "args": {}},
    }


def _empty_plan(goal: str, skipped: str) -> dict:
    return {"goal": goal, "message": None, "note": None, "items": [],
            "offered": [], "unmapped_packs": [], "skipped": skipped}


def _skipped_card(skipped: str) -> dict:
    why = {
        "owner": "You skipped this. Nothing was searched and nothing is "
                 "planned.",
        "skipped": "Knowledge setup was skipped, so there is no plan to make.",
        "offline_build": "You chose an offline build, so nothing is fetched "
                         "while building. Assemble the packs on a connected "
                         "machine and bring them over on removable media.",
    }[skipped]
    return {
        "skill": "sources", "section": "sources",
        "title": "What it learns from",
        "decided": why,
        "status": "ready",
        "consequential": False,
        "estimate": {"bytes": 0, "seconds": 0, "basis": "calibrated"},
        "fields": [{"id": "skipped", "kind": "info", "label": "No plan",
                    "current": why + " " + PLAN_NOTE}],
    }


def apply(ctx, answers: dict) -> dict:
    """Write the plan. Moves nothing, downloads nothing, asks for no key.

    The selection is the owner's, in this order: what they changed on the card,
    then ``--fetch-source`` from the command line, then the proposal. A
    ``--yes`` run with neither selects **nothing** - which is what today's step
    does, and for the same reason: consent nobody gave is not consent.
    """
    proposal = ctx.proposal or {}
    items = list(proposal.get("items") or [])
    plan = dict(proposal.get("plan") or _empty_plan("", ""))
    skipped = str(plan.get("skipped") or "")
    fetchable = {item["id"] for item in items if item["selectable"]}

    named = [str(s) for s in (getattr(ctx.args, "fetch_source", None) or [])]
    applied = ctx.applied("selected", default=None)
    if "selected" in answers:
        selected = [str(s) for s in (answers.get("selected") or [])]
        chosen_by = "owner"
    elif named:
        selected = named
        chosen_by = "command_line"
    elif isinstance(applied, list) and applied:
        # Already settled on an earlier pass. Re-applying this card without an
        # answer keeps the owner's plan rather than re-deciding it.
        selected = [str(s) for s in applied]
        chosen_by = str(ctx.applied("selected_by", default="owner") or "owner")
    elif ctx.non_interactive:
        # Nobody read the card, so nobody accepted the sizes on it.
        selected, chosen_by = [], "non_interactive"
    else:
        selected = list(proposal.get("selected") or [])
        chosen_by = "proposal"

    refused = [source_id for source_id in selected
               if source_id in {item["id"] for item in items}
               and source_id not in fetchable]
    selected = [source_id for source_id in selected if source_id not in refused]

    total, all_known = sources_plan.total_bytes(items, selected)
    blocked = [item for item in items if not item["selectable"]]
    data = {
        "plan": {
            "goal": plan.get("goal", ""),
            "skipped": skipped or None,
            "items": items,
            "message": plan.get("message"),
            "unmapped_packs": plan.get("unmapped_packs") or [],
        },
        "offered": list(plan.get("offered") or []),
        "selected": selected,
        "selected_by": chosen_by,
        "unavailable": [{"id": item["id"], "label": item["label"],
                         "status": sources_plan.NO_KEY_PATH,
                         "why": item["why_not"]} for item in blocked],
        "search_terms": list(proposal.get("search_terms") or []),
        # The cache key, and the whole cache: the goal these words were for. A
        # re-drawn card for the same goal asks the model nothing.
        "search_terms_for": str(plan.get("goal") or "").strip(),
        "tool_needs": list(proposal.get("tool_needs") or []),
        "bytes": total,
        "bytes_known": all_known,
    }
    result: dict[str, Any] = {
        "section": "sources", "data": data,
        "shared": {"sources_plan": data},
    }
    gaps = [_keyed_gap(item) for item in blocked]
    if gaps:
        result["gaps"] = gaps
    return result


def verify(ctx) -> dict:
    """Observe the registry on disk: does every planned id still resolve?

    It reads the shipped file rather than the proposal:
    a plan naming a row that is not in this build's registry cannot be carried
    out, and saying so here is cheaper than a failed download later.
    """
    from puffpai import source_registry

    section = ctx.own()
    selected = [str(s) for s in (section.get("selected") or [])]
    if not selected:
        return {"status": "pass",
                "evidence": "nothing is planned, so there is nothing to resolve"}

    try:
        registry = source_registry.load_registry()
    except Exception as exc:  # noqa: BLE001 - unreachable is a gap, not a crash
        registry = None
        missing, unlicensed = list(selected), []
        note = f"the source registry could not be read ({exc})"
    else:
        missing, unlicensed = [], []
        note = ""
        for source_id in selected:
            entry = source_registry.get_source(source_id, registry)
            if entry is None:
                if _library_book_ok(source_id):
                    continue
                missing.append(source_id)
            elif not entry.get("license"):
                unlicensed.append(source_id)

    if missing or unlicensed:
        bad = (missing + unlicensed)[0]
        return {
            "status": "gap",
            "evidence": note or (f"{len(missing)} planned source(s) are not in "
                                 f"this build's registry"),
            "gap": {
                "skill": "sources",
                "what": "A book in the plan is not on BuildBot's list or in the "
                        "Kiwix library.",
                "why": note or (f"{bad} is not a downloadable book with a known "
                                f"license."),
                "impact": "Nothing will be downloaded for it, and your bot will "
                          "not know that subject.",
                "fix": {"kind": "command", "id": "setup.rerun", "args": {}},
            },
        }
    return {"status": "pass",
            "evidence": f"{len(selected)} planned book(s) are listed, each with "
                        f"a license"}


def _library_book_ok(source_id: str) -> bool:
    """A Kiwix book id the saved library list has, with a known license."""
    from puffpai.catalogs import kiwix_opds, knowledge_resolver

    if kiwix_opds.split_book_id(source_id) is None:
        return False
    cand = knowledge_resolver.candidate_by_id(source_id)
    return bool(cand and cand.get("fetchable"))
