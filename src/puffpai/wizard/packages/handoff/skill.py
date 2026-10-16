"""Handoff: how to start it, and everything that is missing.

No owner decision: every field is read-only. The card says
how to start **this** install -- the installer's launchers if there is a
pointer, the manual recipe if there is not -- the dashboard address that
install's own port makes, and then every gap any skill recorded, each with the
one command that fixes it, rendered by ``fixes.safe_gaps`` from a registered id.

**The pairing code is not on the card, and this skill does not print it.** It
cannot be on the card: a card is stored in the run record and returned over
HTTP, and the code is the one thing that gets a stranger's phone into the
dashboard. So ``apply`` hands the block to the run's **show-once face**
(``ctx.show_once``) -- the console, when the console is drawing this wizard,
and nobody at all over HTTP, where the dashboard has its own pairing flow. The
lines come from ``cli.pairing_block_lines()``; nothing here prints and nothing
here records the code.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations


def _start_lines(ctx) -> tuple[list[str], str, bool]:
    """How to open this bot, its web page address, and whether it has one.

    One step, never a recipe. An installed build is opened with the
    installer's Start launcher (it starts the bot and the web page together
    and opens the browser); anything else with ``puffpai start``, which does
    the same. The npm and ``puffpai serve`` steps are what those two do for
    the owner, so the closing screen never shows them. An installed build with
    no web page keeps its bot launcher: there is no page to open.
    """
    from puffpai import cli

    launchers = cli.installed_launchers()
    if launchers:
        if launchers["dashboard"]:
            return ([_start_launcher(ctx, launchers)],
                    str(launchers.get("dashboard_url") or ""), True)
        return [launchers["backend"]], "", False

    from puffpai import start_cli
    return [START_COMMAND], start_cli.page_url(), True


#: How a build with no installer opens: the bot and its web page together.
START_COMMAND = "puffpai start"


def _start_launcher(ctx, launchers: dict) -> str:
    """"Start Rex.cmd - double-click it in <folder>", or ./start-bot.sh."""
    import os
    root = str(launchers.get("root") or "")
    if os.name != "nt":
        return "./start-bot.sh" + (f" (in {root})" if root else "")
    name = ""
    for source in ((getattr(ctx, "shared", None) or {}).get("identity"),
                   _read(ctx, "persona")):
        if isinstance(source, dict) and str(source.get("name") or "").strip():
            name = str(source["name"]).strip()
            break
    import re
    safe = re.sub(r"[^A-Za-z0-9 _.-]", "", name).strip()[:40].strip()
    launcher = "Start BuildBot.cmd"
    if safe and root and os.path.isfile(os.path.join(root, f"Start {safe}.cmd")):
        launcher = f"Start {safe}.cmd"
    return f"{launcher} - double-click it" + (f" in {root}" if root else "")


def _open_word(commands: list[str]) -> str:
    """What the pairing line calls the thing that opens the bot."""
    first = commands[0] if commands else START_COMMAND
    return first.split(" - ", 1)[0].split(" (in ", 1)[0]


def propose(ctx) -> dict:
    """Read the install and the run's gaps. Nothing is asked, nothing written."""
    from puffpai import cli

    cli.enter_step("The closing screen")
    commands, url, has_dashboard = _start_lines(ctx)
    gaps = [gap for gap in (ctx.own().get("gaps") or ()) if isinstance(gap, dict)]
    missing = not_connected(ctx)

    verdict, detail = _verdict(ctx, len(gaps) + len(missing))
    fields = [{"id": "verdict", "kind": "info",
               "label": "Where this build got to",
               "current": f"{verdict} {detail}".strip()}]
    # In the browser she is already in: the lines say how to open it next
    # time, and nothing asks her to pair a device she is using.
    here = bool(getattr(ctx, "remote", False))
    fields += [{"id": f"start_{index}", "kind": "info",
               "label": ("Then run" if index else
                         "Open your bot next time" if here else "Open your bot"),
               "current": command}
              for index, command in enumerate(commands)]
    if has_dashboard:
        fields.append({"id": "dashboard", "kind": "info",
                       "label": "It opens",
                       "current": (f"{url} in your web browser" if url else
                                   "its web page in your web browser")})
        # The code itself goes to the show-once face and is stored nowhere.
        # This line is the part that is still true in an hour, when the code
        # has expired: whatever opens the bot shows a fresh one.
        opener = _open_word(commands)
        if not here:
            fields.append({"id": "pairing", "kind": "info",
                           "label": "Let your first device in",
                           "current": f"the first time, the page asks for a pairing "
                                      f"code; {opener} shows a fresh one "
                                      f"each time it starts."})

    from puffpai.wizard import factory
    for index, line in enumerate(dict.fromkeys(factory.added_lines(ctx.shared))):
        fields.append({"id": f"added_{index}", "kind": "info",
                       "label": "Added for you", "current": line})

    # Each job the bot was built for, and what does it (or that nothing does yet).
    try:
        from puffpai.finding import capabilities
        can_do = capabilities.report_lines()
    except Exception:  # noqa: BLE001 - the closing screen never fails on this
        can_do = []
    for index, line in enumerate(can_do):
        fields.append({"id": f"can_{index}", "kind": "info",
                       "label": "What it can do", "current": line})

    learning = factory.learning_lines(ctx.shared)
    for index, line in enumerate(learning):
        fields.append({"id": f"learning_{index}", "kind": "info",
                       "label": "Learning in the background", "current": line})
    if learning:
        # The wizard's own process ends here, so an ordered book is fetched
        # by the running bot: say when, and with what.
        fields.append({"id": "learning_when", "kind": "info",
                       "label": "Learning in the background",
                       "current": f"Books you said yes to are fetched the first "
                                  f"time you open your bot with "
                                  f"{_open_word(commands)}."})

    for index, line in enumerate(missing):
        fields.append({"id": f"missing_{index}", "kind": "info",
                       "label": "Not connected yet", "current": line})

    hello = first_line(ctx)
    if hello:
        fields.append({"id": "hello", "kind": "info",
                       "label": "Your bot says", "current": hello})

    proof = _proof_line(ctx)
    if proof:
        fields.append({"id": "proof", "kind": "info",
                       "label": "Proof it works", "current": proof})

    card = {
        "skill": "handoff", "section": "handoff",
        "title": TITLE_HERE if here else TITLE,
        "decided": _decided(commands, gaps, missing),
        "status": "gap" if gaps or missing else "ready",
        "consequential": False,
        "estimate": {"bytes": 0, "seconds": 0, "basis": "measured"},
        "fields": fields,
        "gaps": gaps,
    }
    return {"card": card, "commands": commands, "dashboard_url": url,
            "has_dashboard": has_dashboard, "gaps": gaps, "missing": missing}


#: The closing card's title: in the terminal she opens it next; in the
#: browser she is already in it.
TITLE = "How to start it"
TITLE_HERE = "Your bot is built"


def _about(topic: str) -> str:
    """"wedding" -> "weddings" after "Ask me anything about": a one-word topic
    ending in -ing that the word list says has a plural (a wedding, a
    painting). "knitting", "gardening", "chess" and "space" stay as they are."""
    word = str(topic or "")
    if " " in word or not word.lower().endswith("ing") or not word.isalpha():
        return word
    return word + "s" if _has_plural(word.lower()) else word


_FLAGS: dict[str, str] = {}


def _has_plural(word: str) -> bool:
    """Does the shipped word list (hunspell en_US) give *word* the plural flag S?"""
    if not _FLAGS:
        try:
            from puffpai.catalogs import needs_rules
            with open(needs_rules._dict_path(), encoding="utf-8", errors="replace") as fh:
                next(fh, None)
                for line in fh:
                    head, _, flags = line.strip().partition("/")
                    if head.endswith("ing") and head.isalpha() and head.islower():
                        _FLAGS[head] = flags
        except OSError:
            _FLAGS[""] = ""
    return "S" in _FLAGS.get(word, "")


def first_line(ctx) -> str:
    """The bot's first words, from its own name and goal. No model, no guess."""
    shared = getattr(ctx, "shared", None) or {}
    name = ""
    for source in (_read(ctx, "persona"), shared.get("identity"), shared):
        if isinstance(source, dict):
            name = str(source.get("name") or source.get("bot_name") or "").strip()
            if name:
                break
    goal = _goal(ctx)
    if not name:
        return ""
    # The owner's own word for it ("dinosaurs", "space"), not a made plural.
    # One reader, shared with the chat greeting (bot_identity.topic_phrase).
    from puffpai import bot_identity
    said = bot_identity.about_phrase(goal)
    about = f"Ask me anything about {said}" if said else "Ask me anything"
    return (f"Hi! I'm {name}. {about}, or ask \"what can you do?\" to see "
            f"everything I have for you.")


def _read(ctx, section: str) -> dict:
    try:
        data = ctx.read(section)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _goal(ctx) -> str:
    """The owner's goal sentence, from the persona section or the notebook."""
    persona = _read(ctx, "persona")
    for source in (persona, persona.get("interview"),
                   (getattr(ctx, "shared", None) or {}).get("interview")):
        if isinstance(source, dict) and isinstance(source.get("goal"), str):
            if source["goal"].strip():
                return source["goal"].strip()
    return ""


def _pages(ctx) -> list[str]:
    derivation = _read(ctx, "surfaces").get("surfaces")
    if not isinstance(derivation, dict):
        return []
    return [str(s) for s in (derivation.get("surfaces") or [])]


def not_connected(ctx) -> list[str]:
    """What the owner asked for that nothing in this build does yet.

    Worked out the same way the running bot works it out
    (:mod:`puffpai.build_abilities`): the things the goal says the bot should
    work with that no ability covers, and pages on the list that nothing
    stands behind. Plain lines, one per thing. Never raises.
    """
    try:
        from puffpai import build_abilities as ba
        pages = [r for r in (ba.page_row(sid) for sid in _pages(ctx))
                 if r is not None]
        for_goal = ba.goal_rows(_goal(ctx), ba.core_rows())
        heads = {ba._stem(r["label"].split()[-1]) for r in for_goal}
        lines: list[str] = []
        for r in for_goal + pages:
            if r.get("status") != ba.NOT_IN_THIS_BUILD:
                continue
            if r["id"].startswith("page:") and ba.label_terms(r) & heads:
                continue
            line = (f"{r['label']} - nothing in this build does that yet. "
                    f"It can be added later.")
            if line not in lines:
                lines.append(line)
        return lines
    except Exception:
        return []


def _verdict(ctx, missing_count: int = 0) -> tuple[str, str]:
    """Did this build finish, and if not, what has to happen first.

    NOT READY has two independent reasons: a vault the owner asked for and did not get
    is as unfinished as a missing brain, and "Setup complete" over either of
    them is the wizard lying on its last screen. "Your bot is ready" is also
    only said when nothing is missing, never above "6 things are still
    missing".
    """
    brain = ctx.shared.get("brain_ready") or {}
    vault = ctx.shared.get("vault_block") or {}
    brain_ready = bool(brain.get("ready"))
    vault_rejected = bool(vault.get("rejected"))
    if brain_ready and not vault_rejected:
        if missing_count:
            what = ("1 thing is" if missing_count == 1
                    else f"{missing_count} things are")
            return (f"Setup finished. Your bot can start, but {what} still "
                    f"missing - see below.", "")
        return ("Setup complete. Your bot is ready.", "")
    if not brain_ready and vault_rejected:
        head = ("Setup finished, but it is not ready yet: the brain is missing and the "
                "vault was not created.")
    elif vault_rejected:
        head = "Setup finished, but it is not ready yet: the vault was not created."
    else:
        head = "Setup finished, but it is not ready yet: the brain is missing."

    detail = "" if brain_ready else str(brain.get("reason") or "")
    first = str(brain.get("remedy") or "") or str(brain.get("pull_command") or "")
    if first and not brain_ready:
        detail = (f"{detail} Do this first, because nothing else can answer "
                  f"until it is done: {first}").strip()
    return (head, detail)


def _proof_line(ctx) -> str:
    """What Prove found, in one line, and the command that proves the rest.

    Prove's gaps are already on this card -- the runner mirrors every gap into
    ``handoff.gaps``. What is not is the verdict itself and the fact that the
    static half is not the whole proof, which is exactly the honesty the
    closing screen used to carry and must not lose by becoming a card.
    """
    from puffpai import prove

    record = ctx.shared.get("prove_record")
    if not isinstance(record, dict) or not record.get("checks"):
        return ""
    # prove.summary_line is also what GET /build/prove gives the home page,
    # so the end screen and the home page say the same thing.
    return prove.summary_line(record)


def _decided(commands: list[str], gaps: list, missing: list = ()) -> str:
    first = _open_word(commands)
    count = len(gaps) + len(missing)
    if not count:
        return f"Start it with {first}; nothing is missing."
    what = "thing is" if count == 1 else "things are"
    return f"Start it with {first}; {count} {what} still missing."


def apply(ctx, answers: dict) -> dict:
    """Record the closing screen, and show the pairing code once.

    The code goes to the run's show-once face and is stored nowhere -- not in
    the card, not in the section, not in the run log. A run with no face (every
    HTTP request) shows no code: the dashboard has its own pairing flow, and a
    pairing code in an API response is a pairing code in somebody's proxy log.
    """
    from puffpai import cli

    proposal = ctx.proposal or {}
    commands = proposal.get("commands")
    if commands is None:
        commands, url, has_dashboard = _start_lines(ctx)
    else:
        url = proposal.get("dashboard_url") or ""
        has_dashboard = bool(proposal.get("has_dashboard"))

    sink = ctx.face()
    shown_code = False
    if has_dashboard and sink is not None:
        line = pairing_line(commands)
        if line:
            sink.show(["", line])
            shown_code = True

    # The new bot wakes up: its personality, made once from the goal, its name
    # and what it installed, with this build's brain (fallback without one).
    try:
        from puffpai import persona
        shared = ctx.shared or {}
        url, model = str(shared.get("ollama_url") or ""), str(shared.get("chosen_model") or "")
        persona.make(ask=persona.brain_ask(url, model) if url and model else None,
                     keep_woke=False)
    except Exception:  # noqa: BLE001 - a personality never sinks the build
        pass

    return {"section": "handoff",
            "data": {"closing_shown": True,
                     "pairing_shown": shown_code,
                     "commands": list(commands),
                     "dashboard_url": url,
                     "prove_shown": bool(
                         (ctx.shared.get("prove_record") or {}).get("checks"))},
            "note": "closing screen rendered as a card"}


def pairing_line(commands: list[str]) -> str:
    """One line for the show-once face: the code, and until when, locally.

    "" when no code can be made here (no web page folder): the card's pairing
    line still says where a fresh one comes from.
    """
    from puffpai import cli, pairing_code
    try:
        info = pairing_code.ensure_code()
    except Exception:  # noqa: BLE001 - a display convenience never breaks the finish
        info = None
    if not info:
        return ""
    return (f"Your pairing code for the first device: "
            f"{pairing_code.format_code(info['code'])}. It works until "
            f"{cli._local_clock(info['expires_at'])}; after that, "
            f"{_open_word(commands)} shows a fresh one.")


def verify(ctx) -> dict:
    """Does every command this card names resolve on this install?

    The check is Prove's own (``prove._check_handoff``), reused rather than
    re-implemented: the failure it was written for (a closing screen printing
    commands that do not exist on that machine) is exactly the
    one this verify is for, and two copies of it would be two answers.
    """
    from puffpai import cli, prove

    try:
        check = prove._check_handoff(cli.installed_launchers())
    except Exception as exc:  # noqa: BLE001 - unreachable is a gap
        return {"status": "gap",
                "evidence": f"the start-up lines could not be checked ({exc})",
                "gap": _gap("The start-up lines on this screen were not "
                            "checked.", str(exc))}
    if check.get("result") == prove.PASS:
        return {"status": "pass", "evidence": str(check.get("reason") or "")}
    return {"status": "gap", "evidence": str(check.get("reason") or ""),
            "gap": _gap("Something the closing screen names is not on this "
                        "machine.", str(check.get("reason") or ""))}


def _gap(what: str, why: str) -> dict:
    return {"skill": "handoff", "what": what, "why": why,
            "impact": "Following the closing screen will not start your bot.",
            "fix": {"kind": "command", "id": "setup.rerun", "args": {}}}
