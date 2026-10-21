"""Secure: the vault, what may leave this box, and the bridge.

Phase 2 (spec C.9). One package, one section (``posture``), and **three cards**,
because Secure is asked at three points and one of them has to come first: the
posture gates every later menu, and a card that asked for the vault PIN at the
same moment would be asking questions whose options are not settled yet
(spec B.1, and ``PUFFPAI_BUILDBOT_WIZARD_V2_SPEC.md`` on posture-first).

So this package exposes its parts one card at a time:

* ``posture`` -- the two axes and, once the enumerated tier is settled, the
  door table. Asked first, before anything it gates.
* ``vault`` -- Zone 1: the PIN, and the recovery code shown once.
* ``bridge`` -- Zone 3: what the finished bot may reach, reconciled against the
  egress tier the posture step settled.

Which part a call is about is taken from ``ctx.part``, which the runner sets
from the schedule entry (``card:vault``) and the API from the card id the
browser named (``secure:vault``). When nothing says, it is inferred from how far
the run has got: no posture yet means the posture card, a posture and no vault
block means the vault card, and after that the bridge.

Two things this file will not do, both load-bearing:

* **No secret is returned, logged or put on a card.** The PIN arrives as a
  ``secret_entry`` answer, is used, and is dropped; the recovery code exists
  only inside :mod:`puffpai.wizard.secure_vault` for as long as it takes to
  show it. Neither reaches ``data``, ``shared``, a gap, an exception or a note.
* **No vault is created that nobody could get back into.** If no face has
  registered to show the recovery code once, the vault is not sealed and the
  card says why. Fail closed: the alternative is an encrypted store whose only
  key is a PIN and whose recovery code was written to nowhere.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

#: The three questions this package asks, in the order they are asked.
PARTS = ("posture", "vault", "bridge")

#: A Bridge posture id or a build-network id typed rather than picked.
_ID_PATTERN = r"^[a-z][a-z0-9_]{0,39}$"

#: Plain words for every option this package offers. The ids stay the ids the
#: runtime reads; nothing a person sees is an id, so a person can read and
#: understand what they are choosing.
BUILD_NETWORK_WORDS = {
    "online": "Online while building: it may download what the plan lists",
    "offline": "Offline while building: only what is already on this computer",
}
RUN_EGRESS_WORDS = {
    "sealed": "Nothing at all, not even your home network",
    "island": ("Your home network, and on the internet only BuildBot's own "
               "tools for finding things, each with an off switch"),
    "enumerated": "Only the outside services on the plan",
}
BRIDGE_WORDS = {
    "open_yard": ("Keep what I already said yes to (today this works the "
                  "same as asking once)"),
    "fenced": ("Ask once for each outside service when it is added, and let "
               "me switch any of them off"),
    "drawbridge_up": ("Reach only the services I switch on, nothing else at "
                      "home or on the internet"),
}
# The old per-visit asking choice (moat) was never built and is
# folded into "fenced" (zone_bridge.RETIRED_POSTURES).


# -- which part -----------------------------------------------------------

def part_of(ctx) -> str:
    """The part this call is about. Explicit when the runner says; inferred else.

    The inference reads the run's shared notebook rather than the section,
    because reading the section through ``ctx.own()`` would create it, and
    ``propose`` writes nothing at all (spec A.3).
    """
    hint = str(getattr(ctx, "part", "") or "")
    if hint in PARTS:
        return hint
    shared = getattr(ctx, "shared", None) or {}
    if not shared.get("posture"):
        return "posture"
    if shared.get("vault_block") is None:
        return "vault"
    return "bridge"


# -- the lifecycle ------------------------------------------------------------

def propose(ctx) -> dict:
    part = part_of(ctx)
    proposal = {"posture": _propose_posture,
                "vault": _propose_vault,
                "bridge": _propose_bridge}[part](ctx)
    proposal["part"] = part
    # The card names which question it is, so a renderer keys its row on
    # ``secure:vault`` rather than on a position in a list.
    card = proposal.get("card")
    if isinstance(card, dict):
        card["part"] = part
    return proposal


def apply(ctx, answers: dict) -> dict:
    part = (ctx.proposal or {}).get("part") or part_of(ctx)
    return {"posture": _apply_posture,
            "vault": _apply_vault,
            "bridge": _apply_bridge}[part](ctx, answers or {})


# -- posture ------------------------------------------------------------------

def _stated(args, name, allowed):
    value = getattr(args, name, None)
    value = str(value) if value is not None else ""
    return value if value in allowed else ""


def _propose_posture(ctx) -> dict:
    """The two axes. Read-only: menus and what the command line already said."""
    from puffpai import build_posture, cli, setup_ops

    cli.enter_step("Posture")
    options = setup_ops.posture_options()
    net_ids = [str(opt["id"]) for opt in options["build_network"]]
    egress_ids = [str(opt["id"]) for opt in options["run_egress"]]

    # The propose rule (CONVERTING.md): a posture already applied is what the
    # card shows; the flags and then the shipped defaults are the fallback.
    applied = ctx.applied("posture", default={}) or {}
    net = (str(applied.get("build_network") or "") if str(
               applied.get("build_network") or "") in net_ids else "")
    egress = (str(applied.get("run_egress") or "") if str(
                  applied.get("run_egress") or "") in egress_ids else "")
    # What the summary settled comes next: this card is not shown any more,
    # and what it proposes has to be what the owner said yes to.
    planned_net, planned_egress = _from_summary(ctx)
    net = net or _stated(ctx.args, "build_network", net_ids) \
        or planned_net or build_posture.DEFAULT_BUILD_NETWORK
    egress = egress or _stated(ctx.args, "run_egress", egress_ids) \
        or planned_egress or build_posture.DEFAULT_RUN_EGRESS

    fields = [
        {"id": "build_network", "kind": "choice",
         "label": "Can this computer use the internet while we build?",
         "options": [_option(opt, BUILD_NETWORK_WORDS)
                     for opt in options["build_network"]],
         "current": net,
         "help": "Measured on this computer. Offline, nothing is downloaded."},
        {"id": "run_egress", "kind": "choice",
         "label": "What may the finished bot reach when it runs?",
         "options": [_option(opt, RUN_EGRESS_WORDS)
                     for opt in options["run_egress"]],
         "current": egress,
         "help": "Everything not on the plan stays shut."},
    ]

    # The door table is offered only where doors mean anything: the enumerated
    # tier. An offline or sealed build is not shown a list of ways out, which
    # is the same rule today's step follows by not asking the question. Picking
    # the enumerated tier here brings this card back with the doors on it --
    # every door still starts shut, and opening one is always a deliberate
    # second pass.
    if egress == "enumerated":
        fields.append({
            "id": "doors", "kind": "multi_choice",
            "label": "Which outside services may it use?",
            "options": [_switch_option(opt)
                        for opt in options["egress_switches"]],
            "current": _applied_doors(applied, options)
                       or _stated_doors(ctx.args, options)
                       or _summary_doors(ctx, options),
            "help": "Every one starts off. Only the ones you switch on are "
                    "used."})
    else:
        fields.append({
            "id": "doors_note", "kind": "info",
            "label": "Outside services",
            "current": "None. Outside services can only be switched on when "
                       "the finished bot may use the internet."})

    consequential = egress == "enumerated"
    card = {
        "skill": "secure", "section": "posture",
        "title": "What it may reach",
        "decided": _reach_sentence(net, egress),
        "status": "ready",
        "consequential": consequential,
        "consequential_reasons": ["runtime_egress"] if consequential else [],
        "estimate": {"bytes": 0, "seconds": 0, "basis": "measured"},
        "fields": fields,
    }
    # What this card's apply would share (CONVERTING.md): the posture and the
    # gates every later menu is filtered by. ``assess_posture`` decides, it does
    # not persist -- the write is ``persist_posture`` in the apply, and it is
    # not called here.
    doors = (_applied_doors(applied, options)
             or _stated_doors(ctx.args, options)
             or _summary_doors(ctx, options))
    preview_posture = setup_ops.assess_posture(
        net, egress, doors if egress == "enumerated" else [])
    return {"card": card,
            "shared_preview": {
                "posture": preview_posture,
                "gates": setup_ops.posture_gates(preview_posture),
                "build_network": str(
                    preview_posture.get("build_network") or "")}}


def _option(opt, words: dict) -> dict:
    value = str(opt.get("id"))
    return {"value": value, "label": words.get(value) or value}


def _switch_option(opt) -> dict:
    label = str(opt.get("label") or opt.get("id"))
    description = str(opt.get("description") or "")
    return {"value": str(opt.get("id")),
            "label": f"{label}: {description}" if description else label}


def _reach_sentence(net: str, egress: str) -> str:
    building = ("It may download while we build" if net == "online"
                else "Nothing is downloaded while we build")
    running = {"sealed": "and the finished bot reaches nothing at all.",
               "island": "and the finished bot stays on your home network.",
               "enumerated": "and the finished bot uses only the outside "
                             "services on the plan."}.get(egress, "")
    return f"{building}, {running}".rstrip(", ")


def _from_summary(ctx) -> tuple[str, str]:
    """(build network, runtime reach) the measured internet and the approved
    summary imply, or empty strings where they say nothing."""
    from puffpai.wizard import proposal

    shared = getattr(ctx, "shared", None) or {}
    internet = shared.get("internet")
    net = "online" if internet is True else "offline" if internet is False \
        else ""
    state = shared.get("summary")
    egress = ""
    if isinstance(state, dict) and state.get("approved") \
            and not state.get("non_interactive"):
        egress = proposal.run_egress_for(state, ctx.args) or ""
    elif isinstance(state, dict) and proposal.runtime_reach(state):
        egress = "enumerated"
    return net, egress


def _summary_doors(ctx, options) -> list[str]:
    from puffpai.wizard import proposal

    known = {str(opt["id"]) for opt in options["egress_switches"]}
    state = (getattr(ctx, "shared", None) or {}).get("summary")
    return [ref for ref in proposal.approved_refs(state, "door")
            if ref in known]


def _stated_doors(args, options) -> list[str]:
    known = {str(opt["id"]) for opt in options["egress_switches"]}
    return [str(door) for door in (getattr(args, "egress_door", None) or ())
            if str(door) in known]


def _applied_doors(applied: dict, options) -> list[str]:
    """Doors already open in the recorded posture. Every door still starts shut:
    this shows what was opened by name, it does not open anything."""
    known = {str(opt["id"]) for opt in options["egress_switches"]}
    return [str(door) for door in (applied.get("enabled_switches") or ())
            if str(door) in known]


def _apply_posture(ctx, answers: dict) -> dict:
    """Settle the posture, write it where the runtime reads it, share the gates.

    An axis nobody answered is passed as ``None``, not as the value the card
    displayed: ``assess_posture`` records the difference between a choice and a
    default, and a card that turned every default into a choice would erase it.
    """
    from puffpai import build_posture, setup_ops

    args = ctx.args
    net = answers.get("build_network") or getattr(args, "build_network", None)
    egress = answers.get("run_egress") or getattr(args, "run_egress", None)
    if "doors" in answers:
        doors = [str(door) for door in answers["doors"]]
    else:
        doors = [str(door) for door in (getattr(args, "egress_door", None) or ())]

    applied = ctx.applied("posture", default={}) or {}
    if applied and not answers and net is None and egress is None and not doors:
        # Re-applying this card with nothing new to say keeps what was settled,
        # including whether each axis was a choice or a default. Re-assessing
        # from the shipped defaults would quietly turn a chosen posture back
        # into the default one.
        posture = applied
    else:
        posture = setup_ops.assess_posture(net, egress, doors)
    setup_ops.persist_posture(posture)
    gates = setup_ops.posture_gates(posture)

    result = {
        "section": "posture",
        "data": {"posture": posture},
        "shared": {"posture": posture, "gates": gates,
                   "build_network": str(posture.get("build_network") or "")},
        "note": build_posture.describe_combination(
            posture.get("build_network"), posture.get("run_egress")),
    }
    from puffpai.wizard import proposal
    if posture.get("run_egress") == "enumerated" and not posture.get(
            "enabled_switches") and not proposal.runtime_reach(
                (getattr(ctx, "shared", None) or {}).get("summary")):
        result["gap"] = {
            "skill": "secure",
            "what": "Outside services are allowed, but none is switched on.",
            "why": "Every outside service starts off, and none was switched on.",
            "impact": "Your bot reaches nothing outside your home network "
                      "until you switch one on.",
            "fix": {"kind": "command", "id": "setup.rerun", "args": {}},
        }
    return result


# -- vault --------------------------------------------------------------------

def _vault_state(ctx) -> tuple[str, bool]:
    from puffpai import cli
    from puffpai.security.vault import Vault

    vdir = cli._vault_dir(ctx.cfg)
    try:
        return vdir, bool(Vault(vault_dir=vdir).is_initialized)
    except Exception:  # pragma: no cover - a vault dir we cannot read is "no"
        return vdir, False


def _propose_vault(ctx) -> dict:
    """Zone 1. The card asks for a PIN twice and carries neither."""
    from puffpai import cli
    from puffpai.wizard import secure_vault, wording

    cli.enter_step("Zone 1, the Vault")
    vdir, sealed = _vault_state(ctx)
    skipping = bool(getattr(ctx.args, "skip_vault", False))
    showable = secure_vault.can_show(ctx)
    mode = wording.mode_of(getattr(ctx, "shared", None) or {})
    name = str((getattr(ctx, "shared", None) or {}).get("bot_name")
               or cli.DEFAULT_BOT_NAME)

    fields = [{"id": "intro", "kind": "info", "label": "What this is",
               "current": _one_line(wording.say("vault_what", mode,
                                                name=name))}]
    if sealed:
        fields.append({
            "id": "existing", "kind": "info", "label": "Already set up",
            "current": wording.say("vault_exists", mode)})
        decided = wording.say("vault_exists", mode)
        status = "ready"
    elif skipping:
        fields.append({"id": "skipping", "kind": "info", "label": "Skipped",
                       "current": wording.say("vault_skipped", mode)})
        decided = wording.say("vault_skipped", mode)
        status = "needs_you"
    elif not showable:
        # Fail closed, and say so on the card rather than at the failure. This
        # is the branch a browser gets: the run that drew this card has no face
        # to put a recovery code in front of, so no vault is created here. The
        # dashboard's own Vault step (POST /setup/vault) does have one, which is
        # what the owner is pointed at rather than a terminal they may not have.
        fields.append({
            "id": "no_screen", "kind": "info", "label": "On the next screen",
            "current": wording.say("vault_no_screen", mode)})
        decided = wording.say("vault_no_screen", mode)
        status = "needs_you"
    else:
        # Where it lives, before the PIN: this computer,
        # a drive or folder she chooses, or a BuildBot vault she already has.
        from puffpai.security import vault_place
        stated = _stated_folder(ctx, vdir)
        fields.append({
            "id": "where", "kind": "choice",
            "label": wording.say("vault_where", mode),
            "help": wording.say("vault_where_help", mode),
            "options": [{"value": value, "label": label}
                        for value, label, _help in vault_place.CHOICES],
            "current": "folder" if stated else "here"})
        fields.append({
            "id": "folder", "kind": "text", "path_like": True,
            "max_length": 400,
            "label": wording.say("vault_folder", mode),
            "help": _one_line(wording.say("vault_folder_help", mode,
                                          **wording.vault_folder_examples())),
            "ask_if": {"field": "where", "values": ["folder", "existing"]},
            "current": str(stated or "")})
        fields.append({
            "id": "pin", "kind": "secret_entry",
            "label": wording.say("vault_pin", mode),
            "help": _one_line(wording.say("vault_pin_help", mode) + " "
                              + wording.say("vault_pin_existing_help", mode))})
        fields.append({
            "id": "pin_again", "kind": "secret_entry",
            "label": wording.say("vault_pin_again", mode),
            "help": wording.say("vault_pin_again_help", mode)})
        decided = ("Choose a PIN. Then you get one recovery code, shown "
                   "once, in case you ever forget the PIN.")
        status = "needs_you"
        if not getattr(ctx.args, "vault_password", None) and ctx.non_interactive:
            # Today's wording, kept. The questions above stay on the card --
            # they are what this step asks, and a renderer that can ask them
            # still should -- but a run with nobody at the keyboard will answer
            # none of them, and the card says what that means before it
            # happens rather than after.
            fields.append({
                "id": "no_keyboard", "kind": "info",
                "label": "On this run, no vault is made",
                "current": wording.say("vault_no_keyboard", mode)})

    card = {
        "skill": "secure", "section": "posture",
        "title": wording.say("vault_title", mode),
        "decided": decided, "status": status,
        "consequential": not sealed,
        "consequential_reasons": [] if sealed else ["vault_pin_unset"],
        "estimate": {"bytes": 0, "seconds": 0, "basis": "measured"},
        "fields": fields,
    }
    return {"card": card, "vault_dir": vdir, "sealed": sealed}


def _apply_vault(ctx, answers: dict) -> dict:
    """The ceremony, in the order today runs it. Returns a status-only block.

    Every return below carries a block that says what happened and nothing
    about what was typed. ``skipped`` is a decision and ``rejected`` is a failed
    build -- the same distinction ``cli._setup_vault_step`` draws, kept because
    the closing screen and the manifest both key off it.
    """
    from puffpai import cli, setup_ops
    from puffpai.security.vault import Vault, VaultError
    from puffpai.wizard import secure_vault

    if getattr(ctx.args, "skip_vault", False):
        return _vault_result(cli._vault_skipped(setup_ops))

    vdir, sealed = _vault_state(ctx)
    if sealed:
        return _vault_result(cli._existing_vault_block())

    pin = answers.get("pin") or getattr(ctx.args, "vault_password", None) or ""
    again = answers.get("pin_again")
    if answers.get("pin") and again is not None and again != answers["pin"]:
        # Said without quoting either: "those two did not match" is the whole
        # of what a log may know about a PIN that was mistyped.
        return _vault_result(cli._vault_skipped(setup_ops), gap={
            "skill": "secure",
            "what": "No vault was created.",
            "why": "The two PIN entries did not match.",
            "impact": "Nothing on this box is encrypted until you set one.",
            "fix": {"kind": "command", "id": "setup.rerun", "args": {}}})

    if not pin:
        return _vault_result(cli._vault_skipped(setup_ops))

    # Where it lives. Checked before the PIN rules: a vault the
    # owner already has opens with its own PIN, whatever rules it was made by.
    from puffpai.security import vault_place
    stated = _stated_folder(ctx, vdir)
    where = answers.get("where") or ("folder" if stated else "here")
    folder_text = str(answers.get("folder") or stated or "").strip()
    target = None
    if where == "here" and str(answers.get("folder") or "").strip():
        # Her choice wins; the folder is not silently dropped.
        _say(ctx, "You chose On this computer, so the folder you typed was "
                  "not used. Your vault stays on this computer.")
    if where in ("folder", "existing"):
        if not folder_text:
            return _place_refused(setup_ops, "You picked your own folder but "
                                  "typed none, so no vault was made.")
        try:
            purpose, info = _resolve_place(ctx, where, folder_text)
        except vault_place.PlaceRefused as exc:
            return _place_refused(setup_ops, exc.message)
        if purpose == "existing":
            try:
                done = vault_place.use_existing(info["folder"], pin)
            except vault_place.PlaceRefused as exc:
                return _place_refused(setup_ops, exc.message)
            finally:
                del pin
            _follow_setting(ctx)
            for line in done["lines"]:
                _say(ctx, line)
            return _vault_result(cli._existing_vault_block())
        target = info["folder"]
    elif not vault_place.same_folder(vdir, vault_place.default_folder()):
        target = "here"         # an earlier setting pointed elsewhere

    ok, reason = setup_ops.validate_vault_pin(pin)
    if not ok:
        # Today: a bad --vault-pin at a keyboard sends the owner back to the
        # prompt, and in a script it rejects the build. A card has no "back to
        # the prompt": the card comes again, so both paths record the refusal
        # and neither pretends a vault exists.
        if not ctx.non_interactive and not cli._stdin_is_tty():
            raise cli.SetupRefused(
                "The vault PIN you passed cannot be used, and there is no "
                "terminal to type another one into.",
                "This run's stdin is a pipe or a file, so nothing can prompt "
                "you, and a build that reports 'setup complete' with no vault "
                "would be lying about the one thing you asked for.",
                ["Re-run with a --vault-pin that passes policy "
                 "(digits-only PINs need at least 10 digits, and common "
                 "sequences are refused),",
                 "or pass --skip-vault to set it up later,",
                 "or run `puffpai setup` at a real keyboard."])
        return _vault_result(cli._vault_rejected(setup_ops, reason), gap={
            "skill": "secure",
            "what": f"That PIN cannot be used, so {cli.VAULT_REJECTED_LINE}.",
            "why": reason,
            "impact": "Nothing on this box is encrypted, and this build is not "
                      "finished.",
            "fix": {"kind": "command", "id": "setup.rerun", "args": {}}})

    sink = secure_vault.face_of(ctx)
    if sink is None:
        # The one refusal that is new, and it is the fail-closed one: a sealed
        # vault whose recovery code was shown to nobody is a vault with exactly
        # one key and no spare. This is what an HTTP apply gets, by
        # construction -- a request carries no face.
        return _vault_result(cli._vault_rejected(
            setup_ops, "there was nowhere to show the recovery code"), gap={
            "skill": "secure",
            "what": "No vault was created.",
            "why": "A vault issues one recovery code, shown once, and this "
                   "screen has no way to show it to you.",
            "impact": "Nothing on this box is encrypted. Set the vault PIN in "
                      "the step-by-step setup (Vault step).",
            "fix": {"kind": "command", "id": "setup.rerun", "args": {}}})

    if target is not None:
        try:
            placed = vault_place.set_new_place(
                None if target == "here" else target)
        except vault_place.PlaceRefused as exc:
            return _place_refused(setup_ops, exc.message)
        _follow_setting(ctx)
        vdir = placed["folder"]
        for line in placed["lines"]:
            _say(ctx, line)

    vault = Vault(vault_dir=vdir)
    try:
        block, code = setup_ops.seal_vault_with_recovery(pin, vault=vault)
    except (ValueError, VaultError) as exc:
        return _vault_result(cli._vault_rejected(setup_ops, str(exc)), gap={
            "skill": "secure",
            "what": f"The vault was not created, so {cli.VAULT_REJECTED_LINE}.",
            "why": str(exc),
            "impact": "Nothing on this box is encrypted, and this build is not "
                      "finished.",
            "fix": {"kind": "command", "id": "setup.rerun", "args": {}}})

    from puffpai.wizard import wording
    young = wording.mode_of(getattr(ctx, "shared", None) or {}) == wording.SIMPLE
    confirmed = secure_vault.show_and_confirm(
        code, pin, vault, sink=sink, non_interactive=ctx.non_interactive,
        young=young)
    del code, pin
    block["recovery_confirmed"] = bool(confirmed)
    block["ready"] = True
    block["rejected"] = False
    return _vault_result(block)


def _stated_folder(ctx, vdir: str) -> str:
    """The folder already asked for: ``--vault-folder``, or a vault folder an
    earlier ``puffpai vault location`` saved. "" means this computer."""
    from puffpai.security import vault_place
    stated = str(getattr(ctx.args, "vault_folder", None) or "")
    if stated:
        return stated
    if vault_place.same_folder(vdir, vault_place.default_folder()):
        return ""
    return vdir


def _say(ctx, line: str) -> None:
    """One plain line on the console drawing this run; nothing elsewhere."""
    if getattr(ctx, "console", False):
        from puffpai.wizard import render_cli
        render_cli.say_line(line)


def _resolve_place(ctx, where: str, text: str) -> tuple[str, dict]:
    """The folder the owner typed, checked: ``(purpose, check)``.

    A bare drive gets the ``PuffPAI Vault`` folder offered (a run with nobody
    at the keyboard takes it, and says so). A folder that already holds a
    BuildBot vault is that vault: the PIN typed is its PIN, and no second
    vault is made beside it. Raises :class:`vault_place.PlaceRefused`.
    """
    from puffpai import cli
    from puffpai.security import vault_place
    purpose = "existing" if where == "existing" else "new"
    make = False
    for _ in range(3):
        try:
            return purpose, vault_place.check_folder(
                text, purpose=purpose, make_subfolder=make, create=False)
        except vault_place.PlaceRefused as exc:
            if exc.code == "bare_drive" and not make:
                if ctx.non_interactive:
                    _say(ctx, f"{exc.message} Yes: nobody is at the keyboard, "
                              f"and a folder is the safe choice.")
                elif not cli._ask_yes_no(f"  {exc.message}", default=True):
                    raise
                make = True
                continue
            if exc.code == "has_vault" and purpose == "new":
                _say(ctx, "There is already a BuildBot vault in that folder, "
                          "so BuildBot uses it. The PIN you typed has to be "
                          "that vault's PIN.")
                purpose = "existing"
                continue
            raise
    raise vault_place.PlaceRefused("not_folder", "That folder could not be used.")


def _follow_setting(ctx) -> None:
    """Keep this run's settings object on the vault folder just saved, so the
    vault card's own check (and the runner's) look in the new place."""
    try:
        from puffpai.config import get_config
        ctx.cfg.paths.vault_data_dir = get_config().paths.vault_data_dir
    except Exception:  # pragma: no cover - a settings object without paths
        pass


def _place_refused(setup_ops, reason: str) -> dict:
    from puffpai import cli
    return _vault_result(cli._vault_rejected(setup_ops, reason), gap={
        "skill": "secure",
        "what": "No vault was made, and the vault's folder was not changed.",
        "why": reason,
        "impact": "Nothing on this box is encrypted, and this build is not "
                  "finished.",
        "fix": {"kind": "command", "id": "setup.rerun", "args": {}}})


def _vault_result(block: dict, gap: dict | None = None) -> dict:
    result = {"section": "posture", "data": {"vault": block},
              "shared": {"vault_block": block}}
    if gap:
        result["gap"] = gap
    return result


# -- bridge -------------------------------------------------------------------

def _propose_bridge(ctx) -> dict:
    """Zone 3. A policy choice, never a scan -- nothing here looks at the LAN."""
    from puffpai import cli, setup_ops, zone_bridge

    cli.enter_step("Zone 3, the Bridge")
    args = ctx.args
    posture = (ctx.shared or {}).get("posture") or {}
    # The propose rule (CONVERTING.md): the three facts and the posture the
    # owner already answered are what the card shows.
    applied = ctx.applied("bridge", default={}) or {}
    environment = applied.get("environment") or {}
    has_ha = bool(environment.get("home_assistant")) if environment else (
        bool(getattr(args, "ha_url", None)) or setup_ops.ha_configured())
    has_firewall = bool(environment.get("external_firewall")) if environment \
        else bool(getattr(args, "has_firewall", False))
    has_managed_lan = bool(environment.get("managed_lan")) if environment \
        else bool(getattr(args, "has_managed_lan", False))
    recommendation = setup_ops.bridge_options(has_ha, has_firewall,
                                              has_managed_lan)
    postures = recommendation.get("postures") or []
    chosen = str(applied.get("posture") or applied.get("bridge_posture") or
                 getattr(args, "bridge_posture", None) or "")
    known = {str(item.get("id")) for item in postures}
    if chosen not in known:
        chosen = str(recommendation.get("recommended")
                     or zone_bridge.DEFAULT_POSTURE)
    final, note = setup_ops.reconcile_bridge(
        chosen, str(posture.get("run_egress") or ""))

    fields = [
        {"id": "home_assistant", "kind": "toggle",
         "label": "Do you use Home Assistant?", "current": has_ha},
        {"id": "external_firewall", "kind": "toggle",
         "label": "Do you have a special router or firewall that blocks "
                  "things?",
         "current": has_firewall},
        {"id": "managed_lan", "kind": "toggle",
         "label": "Is your home network split into separate parts for "
                  "different devices?", "current": has_managed_lan},
        {"id": "bridge_posture", "kind": "choice",
         "label": "When should it ask you before it reaches out?",
         "options": [_bridge_option(item) for item in postures],
         "current": chosen if chosen in known else "",
         "free_text": True, "pattern": _ID_PATTERN,
         "help": "Nothing else on your network checks what it does, so the "
                 "safest choice is to be asked before anything new reaches "
                 "outside."},
    ]
    if note:
        fields.append({"id": "reconciled", "kind": "info",
                       "label": "Matched to your other choice",
                       "current": "Set to the stricter of your two choices "
                                  "about the internet."})

    consequential = str(posture.get("run_egress") or "") == "enumerated"
    card = {
        "skill": "secure", "section": "posture",
        "title": "When it asks first",
        "decided": BRIDGE_WORDS.get(final, BRIDGE_WORDS["fenced"]) + ".",
        "status": "ready",
        "consequential": consequential,
        "consequential_reasons": ["runtime_egress"] if consequential else [],
        "estimate": {"bytes": 0, "seconds": 0, "basis": "measured"},
        "fields": fields,
    }
    return {"card": card, "recommendation": recommendation}


def _bridge_option(item) -> dict:
    value = str(item.get("id"))
    return {"value": value, "label": BRIDGE_WORDS.get(value) or value}


def _one_line(text: str) -> str:
    return " ".join(str(text or "").split())


def _apply_bridge(ctx, answers: dict) -> dict:
    from puffpai import cli, setup_ops, zone_bridge

    args = ctx.args
    posture = (ctx.shared or {}).get("posture") or {}
    if getattr(args, "skip_bridge", False):
        block = setup_ops.bridge_block(
            zone_bridge.DEFAULT_POSTURE,
            run_egress=posture.get("run_egress"), chosen_by="default")
        return {"section": "posture", "data": {"bridge": block},
                "shared": {"bridge_block": block}}

    proposed = ((ctx.proposal or {}).get("recommendation") or {}).get(
        "environment") or {}
    environment = {
        key: bool(answers[key]) if key in answers else bool(proposed.get(key))
        for key in ("home_assistant", "external_firewall", "managed_lan")}
    # Always re-asked for the environment as it now stands: the suggestion is a
    # function of those three answers, so changing one has to change the
    # suggestion and not only the record.
    recommendation = setup_ops.bridge_options(
        environment["home_assistant"], environment["external_firewall"],
        environment["managed_lan"])

    chosen = answers.get("bridge_posture") or getattr(args, "bridge_posture", None)
    chosen_by = "owner"
    if not chosen:
        chosen = str(recommendation.get("recommended")
                     or zone_bridge.DEFAULT_POSTURE)
        chosen_by = "default"

    block = setup_ops.bridge_block(
        str(chosen), run_egress=posture.get("run_egress"),
        chosen_by=chosen_by, environment=environment)
    result = {"section": "posture", "data": {"bridge": block},
              "shared": {"bridge_block": block}}
    if block.get("note"):
        result["note"] = str(block["note"])
    return result


# -- verify -------------------------------------------------------------------

def verify(ctx) -> dict:
    """Observe: the vault on disk, the doors as stored, the packages installed.

    Nothing here re-reads the proposal. The three things checked are three
    places the record could disagree with the machine -- a vault block saying
    sealed over a directory with no vault in it, a door table in the manifest
    that the runtime config does not match, and a package declaring more than
    the posture allows (spec C.9's audit).
    """
    from puffpai import setup_ops
    from puffpai.security import egress_doors
    from puffpai.wizard import secure_audit

    section = ctx.own()
    posture = section.get("posture") or {}
    vault_block = section.get("vault")
    evidence: list[str] = []
    gaps: list[dict] = []
    # Asked about one card, answer for that card. A missing vault is the
    # vault card's failure; making the posture card fail for it too sends the
    # owner back through questions they already settled.
    part = str(getattr(ctx, "part", "") or "")
    check_vault = part in ("", "vault")
    check_posture = part in ("", "posture", "bridge")

    if check_vault and isinstance(vault_block, dict) and vault_block.get("configured"):
        _vdir, sealed = _vault_state(ctx)
        if not sealed:
            gaps.append({
                "skill": "secure",
                "what": "The vault this build recorded is not on this machine.",
                "why": f"{_vdir} holds no initialised vault, though the record "
                       f"says one was created.",
                "impact": "Nothing is encrypted, and anything stored through "
                          "the vault will fail.",
                "fix": {"kind": "command", "id": "setup.rerun", "args": {}}})
        else:
            evidence.append("the vault is initialised on disk")
        for banned in ("pin", "pin_hash", "recovery_code", "recovery_hash"):
            if vault_block.get(banned):
                gaps.append({
                    "skill": "secure",
                    "what": "The vault record carries something it must not.",
                    "why": f"the '{banned}' field is set in the recorded vault "
                           f"block",
                    "impact": "A build record is not a place a key may live.",
                    "fix": {"kind": "command", "id": "setup.rerun", "args": {}}})

    if check_posture and posture:
        recorded = set(posture.get("enabled_switches") or [])
        stored = set()
        try:
            egress_doors.reset_cache()
            for row in egress_doors.door_states().get("doors") or ():
                if not (isinstance(row, dict) and row.get("open")):
                    continue
                door = egress_doors.door(row.get("id"))
                stored.add(str(getattr(door, "posture_switch", None)
                               or getattr(door, "config_flag", None)
                               or row.get("id")))
        except Exception as exc:  # pragma: no cover - defensive
            gaps.append({
                "skill": "secure",
                "what": "This build could not read its own list of outside "
                        "services.",
                "why": f"{type(exc).__name__}: {exc}",
                "impact": "What your bot may reach at run time is not proven "
                          "either way.",
                "fix": {"kind": "command", "id": "setup.rerun", "args": {}}})
            stored = recorded
        extra = sorted(stored - recorded)
        if extra:
            gaps.append({
                "skill": "secure",
                "what": "An outside service is switched on that this build "
                        "did not record.",
                "why": f"the settings have {', '.join(extra)} switched on and "
                       f"the plan does not",
                "impact": "Your bot can reach something you were not shown.",
                "fix": {"kind": "command", "id": "setup.rerun", "args": {}}})
        else:
            evidence.append(f"{len(stored)} outside service(s) on, all of "
                            f"them recorded")

        gates = setup_ops.posture_gates(posture)
        audit = secure_audit.baseline(posture, gates)
        evidence.append(audit["evidence"])
        gaps.extend(secure_audit.gaps_for(audit))

    if gaps:
        # One gap goes back, because that is what a verdict carries; the count
        # is in the evidence so the log does not imply the first was the only
        # one. Recording all of them needs the runner to accept a list from
        # verify, which is a runner change and not this module's.
        evidence.append(f"{len(gaps)} disagreement(s) between the record and "
                        f"this machine")
        return {"status": "gap", "evidence": "; ".join(evidence),
                "gap": gaps[0]}
    return {"status": "pass", "evidence": "; ".join(evidence) or
            "nothing recorded yet to check"}
