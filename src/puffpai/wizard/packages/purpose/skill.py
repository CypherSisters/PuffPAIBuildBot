"""Purpose: what this bot is for, and what it is called.

The **goal is the first question**. One skill, one section, **three cards**, because each later
question is derived from the answer before it:

* ``purpose:goal`` -- "What kind of bot do you want? Say it in your own words."
  The goal is matched against the starter list here, quietly: the match feeds
  the summary, it is not shown as a question.
* ``purpose:identity`` -- the name, *suggested from the goal*
  (:func:`suggest_name`), and the builder's age, which is kept only as a band
  (under 13 / 13-17 / adult) and only changes how the wizard words its
  questions. The finished bot never reads it.
* ``purpose:place`` -- whether the bot gets a place of its own, the id it lives
  under, and the lists it keeps. The id is *proposed from the name and the goal
  that were just applied* (:func:`persona_scaffold.suggest_id`), which is only
  possible on a second card: the one-card version worked the suggestion out at
  propose time, before anybody had typed anything, so a bot named Infinity was
  offered the fallback id and accepting the default gave it.

Both cards write the one ``persona`` section, and both take their starting
values from it when it already holds them (``ctx.applied()``), so a card
re-drawn after an apply shows what the owner answered rather than the first
guess this build made.

**The goal is recorded whether or not a place is scaffolded.**
:func:`puffpai.build_context.collect_goal` reads ``persona.goal`` off the build
manifest, and that block used to exist only when a persona was scaffolded --
which ``--yes`` never does. So a build made with ``--goal "..." --yes`` told its
model that the owner had not written down what the build was for, which was
false. :func:`recorded_persona` is the one shape both wizards write: the
scaffolded block when there is one, and a name-and-goal block when there is not.

**No model runs here.** Today's interview is token matching against the starter
catalog (:func:`build_interview.propose`) and nothing else, so the Phase 2
conversion adds no model call: spec C.2's LLM half is not built, and a card that
quietly started talking to Ollama would be a new egress this step never had.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os

from puffpai import app_registry

#: The two questions this package asks, in the order they are asked.
PARTS = ("goal", "identity", "place")

#: What the owner may type as an id. The same shape
#: :func:`app_registry.valid_id` enforces, stated on the card so a bad id is
#: refused by the renderer rather than six lines later by the scaffolder.
ID_PATTERN = (r"^[a-z][a-z0-9_]{%d,%d}$"
              % (app_registry.MIN_ID_LEN - 1, app_registry.MAX_ID_LEN - 1))

#: How much of a goal the card carries. The interview's own cap, so the card
#: cannot accept a goal the matcher would silently trim.
GOAL_MAX = 1000

#: How many lines of the catalog summary the card carries. A card is one screen
#: (``cards.MAX_FIELDS``), so a goal that matched everything is shown in part
#: rather than making the card unrenderable.
MAX_SUMMARY_LINES = 16


def _flag(ctx, name, default=None):
    return getattr(ctx.args, name, default)


def part_of(ctx) -> str:
    """Which question this call is about. Explicit when the runner says so.

    The fallback reads the run's shared notebook rather than the section:
    ``propose`` writes nothing, and ``ctx.own()`` would create the section.
    """
    hint = str(getattr(ctx, "part", "") or "")
    if hint in PARTS:
        return hint
    shared = getattr(ctx, "shared", None) or {}
    if shared.get("interview") is None:
        return "goal"
    if shared.get("bot_name") is None:
        return "identity"
    return "place"


# -- what is already recorded -------------------------------------------------

def _applied_interview(ctx) -> dict:
    interview = ctx.applied("interview")
    if not isinstance(interview, dict):
        interview = (getattr(ctx, "shared", None) or {}).get("interview")
    return interview if isinstance(interview, dict) else {}


def _applied_persona(ctx) -> dict:
    persona = _applied_interview(ctx).get("persona")
    if not isinstance(persona, dict):
        persona = ctx.applied("persona")
    return persona if isinstance(persona, dict) else {}


def _proposed_name(ctx) -> tuple[str, str]:
    """The name the card offers, and why it is that one.

    What the owner already applied, when they have; the flag when there is one;
    nothing when there is nobody to ask (today's non-interactive run records an
    unnamed build rather than pretending the owner chose the default); the
    shipped default otherwise, because that is what pressing Enter has always
    meant here.
    """
    from puffpai import cli, first_run

    applied = ctx.applied()
    if "bot_name" in applied:
        name = str(applied.get("bot_name") or "")
        return name, ("what you answered on this card" if name
                      else "you have not named it, so it answers to "
                           f"{cli.DEFAULT_BOT_NAME}")

    flag = _flag(ctx, "name")
    if flag is not None:
        ok, reason = first_run.name_is_acceptable(flag)
        if ok:
            return first_run.sanitize_name(flag), "what you passed on the command line"
        return "", f"the name you passed cannot be used: {reason}"
    if getattr(ctx, "remote", False):
        # A browser is asking right now: the same suggestion the console
        # offers, so both faces ask the same question.
        suggestion = suggest_name(_goal(ctx))
        if suggestion:
            return suggestion, "suggested from what you said you want"
        return "", f"until you type one, it answers to {cli.DEFAULT_BOT_NAME}"
    if ctx.non_interactive:
        return "", ("nobody was asked, so this build is recorded as unnamed and "
                    f"answers to {cli.DEFAULT_BOT_NAME}")
    suggestion = suggest_name(_goal(ctx))
    if suggestion:
        return suggestion, "suggested from what you said you want"
    return cli.DEFAULT_BOT_NAME, "the shipped default until you change it"


#: Words that say how somebody feels about a bot rather than what it is about.
#: A name is made from the first word that is left.
def suggest_name(goal: str) -> str:
    """"I love dinosaurs" -> "DinosaurBot". Deterministic; "" when nothing fits.

    The goal's topic word (:func:`puffpai.persona_scaffold.topic_word`, which
    the daily update's name uses too), capitalised, with "Bot" on the end. No
    model: a name is a suggestion the owner can overwrite with one keypress,
    and it has to be the same suggestion every time.
    """
    from puffpai import persona_scaffold

    word = persona_scaffold.topic_word(str(goal or ""))
    return word[:1].upper() + word[1:] + "Bot" if word else ""


def _goal(ctx) -> str:
    """The goal as this card starts with it: what was applied, or the flag.

    Nothing is asked here. An empty goal is a real answer -- today's step
    returns an empty interview for it -- and the card carries it as the text
    field the owner fills in.
    """
    applied = _applied_interview(ctx).get("goal")
    if isinstance(applied, str) and applied:
        return applied
    return str(_flag(ctx, "goal") or "")


def _census(ctx) -> dict:
    return (ctx.read("machine").get("census") or ctx.shared.get("census") or {})


# -- propose ------------------------------------------------------------------

def propose(ctx) -> dict:
    part = part_of(ctx)
    proposal = {"goal": _propose_goal,
                "identity": _propose_identity,
                "place": _propose_place}[part](ctx)
    proposal["part"] = part
    card = proposal.get("card")
    if isinstance(card, dict):
        card["part"] = part
    return proposal


def _mode(ctx) -> str:
    from puffpai.wizard import wording
    return wording.mode_of(getattr(ctx, "shared", None) or {})


def _match(ctx, goal: str):
    from puffpai import build_interview as bi, crew_sizing
    if not goal:
        return None
    return bi.propose(goal, crew_budget=crew_sizing.crew_budget(_census(ctx)))


def _interview_for(goal: str, match) -> dict:
    return {"goal": (match or {}).get("goal", goal) if goal else "",
            "selected": [entry["id"] for entry in (match or {}).get("flat", [])],
            "proposal": match, "persona": None}


def _flat(text: str) -> str:
    return " ".join(str(text or "").split())


def _propose_goal(ctx) -> dict:
    """The first question: what kind of bot. Read-only (spec A.3)."""
    from puffpai import cli
    from puffpai.wizard import wording

    cli.enter_step("Build interview")
    mode = _mode(ctx)
    goal = _goal(ctx)
    match = _match(ctx, goal)
    card = {
        "skill": "purpose", "section": "persona",
        "title": wording.say("goal_title", mode),
        "decided": goal,
        "status": "ready" if goal else "needs_you",
        "consequential": False,
        "estimate": {"bytes": 0, "seconds": 0, "basis": "measured"},
        "fields": [
            {"id": "goal", "kind": "text",
             "label": wording.say("goal_question", mode),
             "max_length": GOAL_MAX, "current": goal,
             "help": _flat(wording.say("goal_help", mode)),
             "examples": wording.goal_examples(mode)},
        ],
    }
    return {"card": card, "goal": goal, "match": match,
            "shared_preview": {"interview": _interview_for(goal, match)}}


def _propose_identity(ctx) -> dict:
    """The name, suggested from the goal, and the age band. Read-only."""
    from puffpai import cli, persona_scaffold
    from puffpai.wizard import wording

    cli.enter_step("Build interview")
    mode = _mode(ctx)
    name, _name_why = _proposed_name(ctx)
    shown = name or cli.DEFAULT_BOT_NAME
    fields = [
        {"id": "name", "kind": "text",
         "label": wording.say("name_question", mode),
         "max_length": persona_scaffold.MAX_NAME_LEN, "current": name,
         "help": _flat(wording.say("name_help_page" if getattr(ctx, "remote", False)
                                   else "name_help", mode, suggestion=shown))},
        {"id": "age", "kind": "text",
         "label": wording.say("age_question", mode),
         "max_length": 20, "current": "",
         "help": wording.say("age_help", mode)},
    ]
    card = {
        "skill": "purpose", "section": "persona",
        "title": wording.say("name_title", mode),
        "decided": shown,
        "status": "ready",
        "consequential": False,
        "estimate": {"bytes": 0, "seconds": 0, "basis": "measured"},
        "fields": fields,
    }
    return {"card": card, "name": name,
            "shared_preview": {"bot_name": name}}


def _propose_place(ctx) -> dict:
    """The place, its id and its lists -- named from the applied name and goal."""
    from puffpai import build_interview as bi, cli, crew_sizing, persona_scaffold

    cli.enter_step("Build interview")
    name, _why = _proposed_name(ctx)
    goal = _goal(ctx)
    census = _census(ctx)
    match = (bi.propose(goal, crew_budget=crew_sizing.crew_budget(census))
             if goal else None)
    offer = bi.scaffold_offer(match) if match else None
    made = _applied_persona(ctx)

    scaffold = bool(made.get("scaffolded")) or bool(_flag(ctx, "scaffold"))
    # Somebody is answering at a keyboard or in a browser: the offer's own
    # default stands. Only a run nobody is watching (--yes) declines for them.
    answering = not ctx.non_interactive or bool(getattr(ctx, "remote", False))
    if not scaffold and offer is not None and answering:
        scaffold = bool(offer["default_yes"])

    # The suggestion now comes from the name and the goal as they stand -- which
    # is the whole reason this is a second card.
    suggested_id = (str(made.get("app") or "")
                    or persona_scaffold.suggest_id(name, goal)
                    or persona_scaffold.FALLBACK_ID)

    lists_flag = _flag(ctx, "lists")
    if made.get("lists"):
        lists = ", ".join(str(key) for key in made["lists"])
    elif lists_flag is not None:
        lists = str(lists_flag)
    elif offer is not None and not ctx.non_interactive:
        lists = ", ".join(offer["suggested_lists"])
    else:
        lists = ""

    fields: list[dict] = []
    if offer is not None:
        fields.append({"id": "offer", "kind": "info",
                       "label": offer["question"], "current": offer["reason"]})
    if not goal:
        fields.append({"id": "no_goal", "kind": "info",
                       "label": "Nothing to name it after yet",
                       "current": "No goal was recorded on the card before "
                                  "this one, so the id below is a fallback "
                                  "rather than your own words."})
    fields.extend([
        {"id": "scaffold", "kind": "toggle",
         "label": "A place of its own to keep lists", "current": scaffold,
         "help": "A folder, a page and the lists below. `puffpai persona new` "
                 "does the same thing any time."},
        {"id": "id", "kind": "text", "label": "The id it lives under",
         "max_length": app_registry.MAX_ID_LEN, "pattern": ID_PATTERN,
         "current": suggested_id,
         "help": "Lowercase, no spaces; it names a folder and a page. Taken "
                 "from the name you gave it, then from your own words."},
        {"id": "lists", "kind": "text", "label": "Lists it should keep",
         "max_length": 200, "current": lists,
         "help": "Comma separated, blank for none. Nothing is invented: these "
                 "are suggested from the nouns in your goal."},
    ])

    card = {
        "skill": "purpose", "section": "persona",
        "title": "A place of its own",
        "decided": _decided(name, goal, scaffold, suggested_id),
        "status": "ready" if goal else "needs_you",
        "consequential": False,
        "estimate": {"bytes": 0, "seconds": 0, "basis": "measured"},
        "fields": fields,
    }
    return {"card": card, "name": name, "goal": goal, "scaffold": scaffold,
            "persona_id": suggested_id, "lists": lists, "census": census,
            "match": match}


def _decided(name: str, goal: str, scaffold: bool, persona_id: str) -> str:
    parts = [name or "unnamed"]
    parts.append(goal or "no goal given yet")
    parts.append(f"its own place, as '{persona_id}'" if scaffold
                 else "no place of its own")
    return " - ".join(parts)


# -- apply --------------------------------------------------------------------

def apply(ctx, answers: dict) -> dict:
    part = (ctx.proposal or {}).get("part") or part_of(ctx)
    return {"goal": _apply_goal,
            "identity": _apply_identity,
            "place": _apply_place}[part](ctx, answers or {})


def _apply_goal(ctx, answers: dict) -> dict:
    """Write the goal and the interview into ``persona``.

    Scaffolded or not, a persona block carrying the goal is recorded, which is
    where ``build_context`` looks. The name is not touched here: it is the
    next card's.
    """
    proposal = ctx.proposal or {}
    goal = str(answers.get("goal", proposal.get("goal") or "") or "")
    made = _applied_persona(ctx)
    made = made if made.get("scaffolded") else {}
    name = str(ctx.applied("bot_name", default="") or "")
    match = proposal.get("match")
    if goal and (not isinstance(match, dict) or match.get("goal") != goal):
        match = _match(ctx, goal)
    interview = _interview_for(goal, match if goal else None)
    interview["persona"] = made or None
    result = _result(name, interview, [], made)
    # The name is the next card's to share; this card never claims one.
    result["shared"].pop("bot_name", None)
    if not name:
        result["data"].pop("bot_name", None)
    return result


def _apply_identity(ctx, answers: dict) -> dict:
    """Write the name where both wizards read it, and the age as a band only."""
    from puffpai.wizard import wording

    proposal = ctx.proposal or {}
    gaps: list[dict] = []
    name = _chosen_name(answers, proposal, gaps)
    interview = dict(_applied_interview(ctx))
    interview.setdefault("goal", _goal(ctx))
    interview.setdefault("selected", [])
    interview.setdefault("proposal", None)
    made = _applied_persona(ctx)
    made = made if made.get("scaffolded") else {}
    interview["persona"] = made or None
    result = _result(name, interview, gaps, made)
    # The number typed is never kept: only which of three bands it falls in.
    band = wording.band_for_age(answers.get("age"))
    if band is None:
        band = wording.band_for_age(getattr(ctx.args, "age", None))
    if band is None:
        band = ctx.applied("age_band", default=None)
    result["data"]["age_band"] = band
    result["shared"]["age_band"] = band
    result["shared"]["wording"] = wording.mode_for_band(band)
    return result


def _apply_place(ctx, answers: dict) -> dict:
    """Scaffold the place, or record that there is none and how to make one."""
    proposal = ctx.proposal or {}
    gaps: list[dict] = []

    name = str(proposal.get("name") or ctx.applied("bot_name", default="") or "")
    from puffpai.wizard import proposal as plan

    # The approved plan is the choice: packs it left out are not "chosen".
    interview = plan.settle_interview(_applied_interview(ctx),
                                      (getattr(ctx, "shared", None) or {})
                                      .get("summary"))
    goal = str(interview.get("goal") or proposal.get("goal") or "")

    persona = None
    wants = bool(answers.get("scaffold", proposal.get("scaffold")))
    if wants and goal:
        persona = _scaffold(answers, proposal, name=name, goal=goal, gaps=gaps)
    elif wants and not goal:
        gaps.append({
            "skill": "purpose",
            "what": "Your bot was not given a place of its own.",
            "why": "Nothing was recorded about what it is for, and a place is "
                   "made from the goal.",
            "impact": "There is nowhere yet to keep the lists you asked for.",
            "fix": {"kind": "command", "id": "setup.scaffold", "args": {}}})
    elif not wants:
        # The legacy path printed this at the owner; a card records it where the
        # closing screen can read it, with the command that changes it.
        gaps.append({
            "skill": "purpose",
            "what": "Your bot has no place of its own.",
            "why": ("This setup ran without asking questions, so no folder, "
                    "page or lists were made for it." if ctx.non_interactive
                    else "You said no to a folder, a page and its lists."),
            "impact": "It answers and it knows its goal; it just has nowhere "
                      "to keep a list.",
            "fix": {"kind": "command", "id": "setup.scaffold", "args": {}}})

    interview["goal"] = goal
    interview.setdefault("selected", [])
    interview.setdefault("proposal", None)
    interview["persona"] = persona
    return _result(name, interview, gaps, persona or {})


def _chosen_name(answers: dict, proposal: dict, gaps: list) -> str:
    """The name, written where both wizards read it. "" means unnamed.

    An answer is checked by the same guard the flag goes through, and a name
    this build will not use is recorded as unnamed with a gap saying so -- the
    alternative is a manifest claiming the owner named it something they were
    told was refused.
    """
    from puffpai import first_run, setup_ops

    chosen = proposal.get("name") or ""
    typed = answers.get("name")
    # A blank answer is Enter: keep the suggestion, in the browser as in the
    # terminal. Only a name actually typed is checked (and can be refused).
    if typed is not None and str(typed).strip():
        ok, reason = first_run.name_is_acceptable(typed)
        if ok:
            chosen = first_run.sanitize_name(typed)
        else:
            chosen = ""
            gaps.append({
                "skill": "purpose",
                "what": "The name you typed was not used.",
                "why": f"That name cannot be used: {reason}",
                "impact": "This build is recorded as unnamed and answers to "
                          "the shipped default.",
                "fix": {"kind": "card", "id": "purpose"}})
    if chosen:
        setup_ops.write_main_config({"identity": {"name": chosen}})
    return chosen


def _scaffold(answers: dict, proposal: dict, *, name: str, goal: str,
              gaps: list) -> dict | None:
    """Make the persona, or record why nothing was written.

    :func:`persona_scaffold.scaffold` owns every refusal here, including the
    reserved ids (``apps``, ``vault``, ``settings`` and the rest), and it is
    also what puts ``persona.<id>`` on a trimmed build's allowlist. Neither is
    re-implemented: a second copy of either rule is a second chance to disagree
    with it.
    """
    from puffpai import persona_scaffold

    app_id = str(answers.get("id", proposal.get("persona_id") or ""))
    lists = answers.get("lists", proposal.get("lists") or "")
    try:
        made = persona_scaffold.scaffold(app_id, name=name or app_id,
                                         goal=goal, lists=lists)
    except persona_scaffold.ScaffoldError as exc:
        gaps.append({
            "skill": "purpose",
            "what": "Your bot was not given a place of its own.",
            "why": str(exc),
            "impact": "Nothing was written, so there is nowhere yet to keep "
                      "the lists you asked for.",
            "fix": {"kind": "card", "id": "purpose"}})
        return None
    return scaffolded_block(made)


def scaffolded_block(made: dict) -> dict:
    """The manifest's ``persona`` block for a place that was just scaffolded.

    One builder, used by the wizard and by ``cli._maybe_scaffold_persona``, so
    the two paths cannot drift on the shape everything downstream reads
    (``app`` names the folder and the page; ``prove.persona_id_of`` reads it).
    """
    return {
        "scaffolded": True,
        "app": made["id"],
        "pack": made["id"],
        "name": made["name"],
        "goal": made["goal"],
        "lists": made["lists"],
        "surfaces": made["surfaces"],
        "app_manifest": made["app_path"],
        "pack_manifest": made["pack_path"],
        "note": ("Written by the wizard from the owner's goal; declarative "
                 "only, and editable by hand."),
    }


def recorded_persona(interview: dict, bot_name: str) -> dict | None:
    """The ``persona`` block this build records, scaffolded or not.

    ``None`` only when there is genuinely nothing to say: no place, no goal.
    Otherwise the scaffolded block, or the name and the goal on their own --
    which is what :func:`puffpai.build_context.collect_goal` reads, and the
    reason a ``--yes --goal`` build used to tell its model nothing.

    Both wizards call this, so the record is the same object either way.
    """
    made = (interview or {}).get("persona")
    if isinstance(made, dict) and made:
        return dict(made)
    goal = str((interview or {}).get("goal") or "")
    name = str(bot_name or "")
    if not goal and not name:
        return None
    return {"scaffolded": False, "name": name, "goal": goal,
            "note": ("No place of its own was made; this records what the "
                     "owner said it is for. `puffpai setup --scaffold` makes "
                     "one.")}


def _result(name: str, interview: dict, gaps: list, made: dict) -> dict:
    data = {"bot_name": name, "interview": interview}
    persona = recorded_persona(interview, name)
    if persona is not None:
        data["persona"] = persona
    result = {"section": "persona", "data": data,
              "shared": {"bot_name": name, "interview": interview}}
    if gaps:
        result["gaps"] = gaps
    return result


# -- verify -------------------------------------------------------------------

def verify(ctx) -> dict:
    """Observe the world (spec A.3): the folders, then the allowlist."""
    from puffpai import persona_scaffold

    section = ctx.own()
    persona = section.get("persona")
    if not isinstance(persona, dict) or not persona.get("scaffolded"):
        goal = str((persona or {}).get("goal") or "")
        if goal:
            return {"status": "pass",
                    "evidence": "no place was asked for; the goal is recorded "
                                "where the model reads it"}
        return {"status": "pass",
                "evidence": "no persona was asked for, so there is none to find"}

    app_id = str(persona.get("app") or "")
    missing = [path for path in (persona.get("app_manifest"),
                                 persona.get("pack_manifest"))
               if not (isinstance(path, str) and path and os.path.exists(path))]
    if missing or app_id not in persona_scaffold.scaffolded_ids():
        return {"status": "gap",
                "evidence": f"'{app_id}' is not on disk as a persona",
                "gap": {"skill": "purpose",
                        "what": "The place your bot keeps its lists is not "
                                "there.",
                        "why": f"The wizard recorded a persona called "
                               f"'{app_id}' and its folders are not on this "
                               f"machine now.",
                        "impact": "Its page is empty and the lists you named "
                                  "have nowhere to go.",
                        "fix": {"kind": "command", "id": "setup.rerun",
                                "args": {}}}}

    surface = persona_scaffold.persona_surface(app_id)
    allowlist, trimmed = _build_allowlist()
    if trimmed and surface not in allowlist:
        return {"status": "gap",
                "evidence": f"{surface} is not on this trimmed build's allowlist",
                "gap": {"skill": "purpose",
                        "what": "Your bot's own page is not on the list of "
                                "pages this build loads.",
                        "why": f"This build loads only what it allows, and "
                               f"{surface} is not on that list.",
                        "impact": "Your bot is invisible in its own navigation.",
                        "fix": {"kind": "command", "id": "setup.rerun",
                                "args": {}}}}
    where = "on the allowlist" if trimmed else "on an untrimmed build"
    return {"status": "pass",
            "evidence": f"'{app_id}' is on disk and its surface is {where}"}


def _build_allowlist() -> tuple[list[str], bool]:
    """This build's surface allowlist, and whether it is trimmed at all.

    An empty allowlist on a build that never chose one means "load everything",
    which is why the trimmed question is asked separately and answered by
    :mod:`puffpai.persona_scaffold`'s own rule rather than a second copy of it.
    """
    from puffpai import build_manifest, persona_scaffold

    try:
        record = build_manifest.read_manifest() or {}
        surfaces = record.get("surfaces")
        surfaces = dict(surfaces) if isinstance(surfaces, dict) else {}
    except Exception:
        return [], False
    allowlist = [str(s) for s in (surfaces.get("allowlist") or [])]
    return allowlist, persona_scaffold._trimming_is_active(surfaces)
