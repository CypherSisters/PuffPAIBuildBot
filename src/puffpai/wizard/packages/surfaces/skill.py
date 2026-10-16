"""Surfaces: where the owner meets the bot.

The allowlist is not a cosmetic filter: a surface that is
not on it is **not loaded**, so this card is the shape of the build rather than
a view preference.

* :func:`propose` derives the allowlist the goal implies and the flags amend.
  Read-only: :func:`build_interview.derive_surfaces` and
  :func:`build_interview.apply_surface_choices` are both pure, nothing is asked
  and nothing is written.
* the card offers every surface this build knows how to load, with the owner's
  words as labels and the reason each proposed one is proposed.
* :func:`apply` writes the ``surfaces`` section -- the same derivation object
  today's step returns, through the same two functions.
* :func:`verify` observes the taxonomy: every id on the list is a surface this
  build actually knows how to load.

**The persona's own surface is not one of the choices.** The persona
always gets a rail entry, a home card and search, and no gate may remove it. It
is shown on the card as an included fact, and it survives whatever is unchecked
below it. Taking it off is removing the persona (``puffpai persona remove``),
which takes the surface with it -- not unticking a box here.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

#: How many reason lines the card carries. A card is one screen
#: (``cards.MAX_FIELDS``), and the allowlist itself is the answer.
MAX_SUMMARY_LINES = 20


def _interview(ctx) -> dict:
    """What Purpose recorded. The section first, the run's notebook second."""
    interview = ctx.read("persona").get("interview")
    if not isinstance(interview, dict):
        interview = ctx.shared.get("interview")
    return interview if isinstance(interview, dict) else {}


def _persona_surfaces(interview: dict) -> list[str]:
    """The scaffolded persona's own surface, if this build made one."""
    persona = interview.get("persona")
    if not isinstance(persona, dict):
        return []
    return [str(s) for s in (persona.get("surfaces") or [])]


def _options(derivation: dict, chosen: list[str], always: list[str]) -> list[dict]:
    """Every surface this build can load, in the owner's words.

    The persona's surface is left out: it is not a choice, and an
    option the answer cannot change is a box that lies about what unticking it
    does.
    """
    from puffpai import build_interview as bi

    taxonomy = bi.surface_taxonomy()
    available = [str(s) for s in (derivation.get("available") or [])]
    for sid in chosen:
        if sid not in available:
            available.append(sid)
    out: list[dict] = []
    for sid in available:
        if sid in always:
            continue
        label = str((taxonomy.get(sid) or {}).get("label") or sid)
        if taxonomy.get(sid, {}).get("sensitive"):
            label = f"{label} (sensitive)"
        out.append({"value": sid, "label": label})
    return out[:60]


def propose(ctx) -> dict:
    """Derive the allowlist the goal implies. Read-only."""
    from puffpai import cli, setup_ops

    cli.enter_step("Surfaces")
    interview = _interview(ctx)
    proposal = interview.get("proposal")

    if not proposal:
        derivation = setup_ops.derive_surfaces({"goal": "", "flat": []})
    else:
        derivation = cli._with_persona_surfaces(
            setup_ops.derive_surfaces(proposal), interview.get("persona"))

    add = [str(s) for s in (getattr(ctx.args, "add_surface", None) or [])]
    drop = [str(s) for s in (getattr(ctx.args, "drop_surface", None) or [])]
    if add or drop:
        derivation = setup_ops.apply_surface_choices(derivation, add=add,
                                                     remove=drop)
    # The propose rule (CONVERTING.md): an allowlist the owner already settled
    # is what the card shows; the goal's derivation stays the recommendation.
    proposed = [str(s) for s in (derivation.get("surfaces") or [])]
    applied = ctx.applied("surfaces", default=None)
    if isinstance(applied, dict) and applied.get("surfaces") is not None:
        derivation = applied

    always = [s for s in _persona_surfaces(interview)
              if s in (derivation.get("surfaces") or [])]
    chosen = [s for s in (derivation.get("surfaces") or []) if s not in always]

    fields: list[dict] = []
    options = _options(derivation, chosen, always)
    if options:
        fields.append({
            "id": "allowlist", "kind": "multi_choice",
            "label": "Pages your bot will have",
            "options": options, "current": chosen,
            "proposed": [s for s in proposed
                         if s in {opt["value"] for opt in options}],
            "help": "A page you leave unticked is left out of your bot "
                    "completely. It is fine to leave them all out."})
    for _sid in always:
        fields.append({
            "id": f"persona_{len(fields)}", "kind": "info",
            "label": "Always included",
            "current": "Your bot's own page. It stays for as long as your "
                       "bot does."})
    # One reason line per page: every page on the list has to cite the words
    # in the goal that put it there. Plain words, never the page's id.
    for line in _why_lines(derivation)[:MAX_SUMMARY_LINES]:
        fields.append({"id": f"why_{len(fields)}", "kind": "info",
                       "label": "Why", "current": line})
    # A page is a label and an icon. One with nothing behind it is said out
    # loud here, not shown later as if it worked.
    for line in _not_connected_lines(chosen):
        fields.append({"id": f"missing_{len(fields)}", "kind": "info",
                       "label": "Not connected yet", "current": line})

    card = {
        "skill": "surfaces", "section": "surfaces",
        "title": "Pages your bot will have",
        "decided": _decided(derivation),
        "status": "ready",
        "consequential": False,
        "estimate": {"bytes": 0, "seconds": 0, "basis": "measured"},
        "fields": fields,
    }
    return {"card": card, "derivation": derivation, "always": always}


def _label(sid: str) -> str:
    """A page in the owner's words. A page outside the taxonomy is the bot's
    own page, and is called that."""
    from puffpai import build_interview as bi

    label = str((bi.surface_taxonomy().get(sid) or {}).get("label") or "")
    return label or "your bot's own page"


def _why_lines(derivation: dict) -> list[str]:
    """Why each page is on the list, with the page's name in place of its id."""
    reasons = derivation.get("reasons") or {}
    out: list[str] = []
    for sid in derivation.get("surfaces") or []:
        sid = str(sid)
        label = _label(sid)
        reason = str(reasons.get(sid) or "").strip()
        if not reason:
            reason = f"{label}, because you chose it."
        reason = reason.replace(sid, label)
        out.append(reason)
    return out


def _not_connected_lines(chosen: list[str]) -> list[str]:
    """Pages on the list that nothing in this build stands behind yet."""
    try:
        from puffpai import build_abilities as ba
    except Exception:
        return []
    out: list[str] = []
    for sid in chosen:
        try:
            backing = ba.page_row(sid)
        except Exception:
            backing = None
        if backing is not None and backing.get("status") == ba.NOT_IN_THIS_BUILD:
            out.append(f"{_label(sid)}: this page is only a name for now. "
                       f"Nothing in this build works with it yet; it can be "
                       f"added later.")
    return out


def _decided(derivation: dict) -> str:
    surfaces = [str(s) for s in (derivation.get("surfaces") or [])]
    if not surfaces:
        return "no pages yet, so your bot has only what you add"
    sensitive = list(derivation.get("sensitive") or [])
    noun = "page" if len(surfaces) == 1 else "pages"
    line = f"{len(surfaces)} {noun}: {', '.join(_label(s) for s in surfaces)}"
    if sensitive:
        line += f" ({len(sensitive)} private)"
    return line


def apply(ctx, answers: dict) -> dict:
    """Write the ``surfaces`` section: the derivation today's step returns.

    The owner's ticks become the same ``add``/``remove`` lists the
    ``--add-surface`` / ``--drop-surface`` flags produce, and go through the
    same :func:`setup_ops.apply_surface_choices`, so a build made by clicking
    and a build made by flags record the same object.
    """
    from puffpai import setup_ops

    proposal = ctx.proposal or {}
    derivation = proposal.get("derivation")
    if not isinstance(derivation, dict):
        derivation = setup_ops.derive_surfaces({"goal": "", "flat": []})
    always = [str(s) for s in (proposal.get("always") or [])]

    chosen = answers.get("allowlist")
    if chosen is not None:
        keep = set(str(s) for s in chosen) | set(always)
        proposed = [str(s) for s in (derivation.get("surfaces") or [])]
        add = [s for s in keep if s not in proposed]
        remove = [s for s in proposed if s not in keep]
        if add or remove:
            derivation = setup_ops.apply_surface_choices(
                derivation, add=sorted(add), remove=sorted(remove))

    return {"section": "surfaces", "data": {"surfaces": derivation},
            "shared": {"surfaces": derivation}}


def verify(ctx) -> dict:
    """Observe the taxonomy: is every id on the list one this build loads?"""
    from puffpai import build_interview as bi

    derivation = ctx.own().get("surfaces") or {}
    chosen = [str(s) for s in (derivation.get("surfaces") or [])]
    taxonomy = bi.surface_taxonomy()
    unknown = [sid for sid in chosen if sid not in taxonomy]
    if unknown:
        return {"status": "gap",
                "evidence": f"{', '.join(unknown)} is not a surface this build "
                            f"knows how to load",
                "gap": {"skill": "surfaces",
                        "what": "Your bot's page list names a page this build "
                                "does not have.",
                        "why": f"These names on the list are not pages "
                               f"this build knows: {', '.join(unknown)}.",
                        "impact": "Nothing shows for them, so the list "
                                  "promises something the build cannot show.",
                        "fix": {"kind": "command", "id": "setup.rerun",
                                "args": {}}}}
    return {"status": "pass",
            "evidence": f"all {len(chosen)} allowed surfaces are in this "
                        f"build's taxonomy"}
