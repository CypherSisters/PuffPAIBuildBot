"""The wizard behind a face that is not a console.

The dashboard is a renderer of the same cards the CLI draws. That
needs three answers from the backend and nothing else -- what the cards are,
what changed when the owner sent answers back, and what the run log says. This
module is those three, and it is deliberately thin: every rule (one writer per
section, reserved sections, gap records, the log) still lives in the runner,
so a card reaching a browser cannot mean a different wizard.

**Proposing is read-only.** :func:`propose_cards` runs each converted skill's
``propose`` and writes nothing -- no run state, no config, no manifest. It is
safe to call on every page load, and a skill whose propose raises is reported
as a refusal rather than taking the page down.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import errno
import importlib
import json
import logging
import os
import re
import threading
import time
import types
from typing import Any, Callable

from puffpai.wizard import cards, loader, runner as wizard_runner, stale

_log = logging.getLogger(__name__)

#: The name of the file that says a build is running. One run at a time, and
#: the reason is not tidiness: two runs applying the same cards would both write
#: the same sections and both write the same run record, so the record would
#: describe neither of them.
LOCK_NAME = "wizard_run.lock"

#: How long a lock left behind by a process that died is honoured. Under it, a
#: second run is refused (fail closed); over it, the lock is taken -- otherwise
#: one crash would wedge the wizard for good with no way out but a file
#: deletion nobody would think to do.
LOCK_TTL_S = 900


class SkillNotConverted(LookupError):
    """Asked for a skill that has no card yet. Phase 2 converts them one by one."""


class RunInProgress(RuntimeError):
    """A build is already running. Two at once would write over each other."""


class ToolsmithNotSaved(RuntimeError):
    """She picked Anthropic but its key is not saved: the step is not done."""

    code = "not_saved"


class AnswerRefused(cards.CardError):
    """An answer this build will not apply, and which field it was about.

    The message is :class:`~puffpai.wizard.cards.CardError`'s, unchanged -- it
    was written to be shown to a person. ``field`` is the id the message names
    when it names a known one, so a browser can put it beside the field instead
    of at the bottom of the card.
    """

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


def _args(extra: dict | None = None) -> types.SimpleNamespace:
    """The flags a step reads, with nothing set: the browser passes answers.

    Everything the steps read is read with ``getattr(args, name, None)``, so an
    empty namespace means "nothing was stated on a command line", which is
    exactly true of a request that arrived over HTTP.
    """
    return types.SimpleNamespace(**(extra or {}))


def _cfg():
    from puffpai.config import get_config
    return get_config()


def _lifecycle_packages() -> tuple[dict[str, loader.Package], list[str]]:
    packages, refusals = loader.load_builtin_packages()
    return packages, refusals


def has_lifecycle(package: loader.Package) -> bool:
    """True when this package is converted: propose, apply and verify."""
    try:
        module = loader.load_module(package)
    except Exception:
        return False
    return all(callable(getattr(module, name, None))
               for name in ("propose", "apply", "verify"))


def parts_of(package: loader.Package) -> tuple[str, ...]:
    """The questions this package asks: ``("",)`` for one card, else its names.

    A package declares them with a module-level ``PARTS`` tuple (Secure:
    posture, vault, bridge). Nothing is inferred -- a package that says nothing
    has exactly one card.
    """
    try:
        module = loader.load_module(package)
    except Exception:
        return ("",)
    declared = getattr(module, "PARTS", None)
    if not isinstance(declared, (list, tuple)) or not declared:
        return ("",)
    names = tuple(str(name) for name in declared
                  if isinstance(name, str) and cards.ID_RE.match(name))
    return names or ("",)


def _context(package: loader.Package, sections: dict, shared: dict,
             args, cfg, part: str = "") -> wizard_runner.RunContext:
    """A context for one propose. **No show-once face, ever.**

    Nothing reached through this module has a screen it can put a value on
    exactly once: the caller is an HTTP request, and a browser response is not
    that screen. So the vault card refuses to create a vault from here and says
    where the PIN can be set instead, and Handoff shows no pairing
    code. This is a fail-closed default, not an oversight.
    """
    return wizard_runner.RunContext(
        args=args, cfg=cfg, non_interactive=True, shared=shared,
        sections=sections, package=package, part=part, show_once=None,
        remote=True)


def _saved(cfg, args) -> wizard_runner.Runner:
    """A runner holding the last run's record, if there is one to hold."""
    run = wizard_runner.Runner(args=args, cfg=cfg, non_interactive=True,
                               remote=True)
    try:
        run.load_state()
    except Exception:  # pragma: no cover - load_state does not raise
        pass
    return run


def _scheduled_ids() -> list[str]:
    """Every card id the schedule runs, in schedule order.

    The schedule is the source of truth for order and for what exists, so the
    dashboard and the CLI cannot end up with two different lists of rows --
    which is the drift (fourteen steps against eight) this whole port is
    against.
    """
    out: list[str] = []
    for step in wizard_runner.SCHEDULE:
        part = wizard_runner.lifecycle_part(step.part)
        if part is None or not step.skill:
            continue
        out.append(f"{step.skill}:{part}" if part else step.skill)
    return out


def _settled(run: wizard_runner.Runner, skill_id: str, part: str) -> bool:
    """Has the owner already applied this card in this build?"""
    step = _step_for(skill_id, part)
    outcome = run.entries.get(step.entry, {}).get("outcome")
    return outcome in ("done", "skipped", "not_built")


def propose_cards(args=None, cfg=None) -> dict:
    """Every card the schedule runs, in schedule order. Writes nothing.

    ``order`` carries the ids explicitly -- a JSON array's order is a promise
    and a JSON object's key order is not -- and ``not_converted`` names the
    skills that have no card at all, so a row nobody can see is not how an
    owner finds out a decision was made for them.

    **Later cards see what earlier cards propose.** A run applies as it goes, so
    the console wizard's Brain step reads the address Discover *wrote*. Nothing
    is applied here, so without help every card proposed against an empty
    notebook: on a fresh build Discover said "Ollama at 127.0.0.1:11435" and the
    Brain row beside it still sized a download against the default address, and
    Crew said "room for up to 0 specialists" because no census had been shared.
    So a *provisional* notebook is threaded through the proposes: each card that
    is not yet applied contributes its ``shared_preview`` -- what its apply would
    put in ``shared`` if this proposal were accepted (CONVERTING.md) -- and the
    later proposes read it. It is a local dict: the run's own notebook, the run
    state and the config are untouched, so this stays safe to call on every page
    load. A card the owner has already applied contributes nothing, because the
    notebook loaded from the run state already holds what it really wrote.
    """
    args = args or _args()
    cfg = cfg or _cfg()
    run = _saved(cfg, args)
    packages, refusals = _lifecycle_packages()

    out: list[dict] = []
    order: list[str] = []
    applied: list[str] = []
    provisional = dict(run.shared)
    for card_id in _scheduled_ids():
        skill_id, part = cards.split_card_id(card_id)
        package = packages.get(skill_id)
        if package is None or not has_lifecycle(package):
            continue
        settled = _settled(run, skill_id, part)
        try:
            card, proposal = _propose(package, run, args, cfg, part=part,
                                      shared=provisional)
        except Exception as exc:
            refusals.append(
                f"The '{card_id}' step could not say what it found: "
                f"{type(exc).__name__}: {exc}")
            continue
        if not settled:
            preview = proposal.get("shared_preview")
            if isinstance(preview, dict):
                provisional.update(
                    {key: value for key, value in preview.items()
                     if isinstance(key, str)})
        out.append(card)
        order.append(cards.card_id(card))
        if settled:
            applied.append(cards.card_id(card))

    converted = {cards.split_card_id(cid)[0] for cid in _scheduled_ids()}
    not_converted = [
        {"skill": skill_id,
         "title": cards.plain(
             getattr(packages.get(skill_id), "label", "") or skill_id,
             cards.MAX_TITLE)}
        for skill_id in wizard_runner.SKILL_ORDER if skill_id not in converted]
    return {"cards": out, "order": order, "not_converted": not_converted,
            "refusals": refusals, "applied": applied,
            "total": cards.total_estimate(out),
            "stale": list(run.stale)}


def propose_one(package: loader.Package, run: wizard_runner.Runner,
                args, cfg, part: str = "") -> dict:
    """One skill's card, validated. Raises if it will not validate."""
    return _propose(package, run, args, cfg, part=part)[0]


def _propose(package: loader.Package, run: wizard_runner.Runner, args, cfg,
             part: str = "", shared: dict | None = None) -> tuple[dict, dict]:
    """The card and the whole proposal behind it.

    *shared* replaces the run's notebook for this one propose, which is how
    :func:`propose_cards` shows a card what the cards above it would share.
    None means the run's own notebook, unchanged.
    """
    module = loader.load_module(package)
    notebook = run.shared if shared is None else shared
    context = _context(package, run.sections, notebook, args, cfg, part)
    proposal = module.propose(context) or {}
    card = cards.validate_card(proposal.get("card"))
    if part and card["part"] != part:
        raise cards.CardError(
            f"'{package.id}' was asked for its '{part}' card and proposed "
            f"'{card['part'] or 'no part'}'.")
    return card, proposal


def _resolve(card_id: Any) -> tuple[loader.Package, str, str]:
    """A card id off the wire into (package, skill, part), or a refusal."""
    skill, part = cards.split_card_id(card_id)
    if not skill:
        raise SkillNotConverted(
            "Name the step to apply, as its id from /wizard/cards.")
    packages, _ = _lifecycle_packages()
    package = packages.get(skill)
    if package is None or not has_lifecycle(package):
        raise SkillNotConverted(
            f"'{skill}' is not a step this build can show a card for yet.")
    known = parts_of(package)
    if part and part not in known:
        raise SkillNotConverted(
            f"'{skill}' has no '{part}' card.")
    if not part and known != ("",):
        # A multi-question package named without a part would be whichever
        # question the run happened to be up to. Asking the caller to say which
        # is the only answer that cannot silently apply the wrong one.
        raise SkillNotConverted(
            f"'{skill}' asks more than one question; name which "
            f"({', '.join(f'{skill}:{name}' for name in known)}).")
    return package, skill, part


def _step_for(skill: str, part: str, *,
              requires: bool = True) -> wizard_runner.Step:
    """The schedule's own entry for this card, so a re-apply is the same entry.

    Falling back to a synthesised entry would give the same card two entry ids
    -- one from the wizard and one from the dashboard -- and the run record
    would then say a step both ran and did not.

    ``requires=False`` drops the entry's dependency list, and only
    :func:`apply_skill` uses it. Re-running **one** card is how an owner
    changes anything, and the run's shared notebook is a property of
    a whole wizard run, not of the build: gating a single re-apply on it would
    mean the dashboard could never change a step until it had walked the whole
    wizard again. A skill's own propose reads its section for what it needs.
    """
    want = f"{wizard_runner.V2_PART}:{part}" if part else wizard_runner.V2_PART
    for step in wizard_runner.SCHEDULE:
        if step.skill == skill and step.part == want:
            return step if requires else wizard_runner.Step(
                step.entry, step.skill, step.part)
    return wizard_runner.Step(f"{skill}.{part or wizard_runner.V2_PART}",
                              skill, want)


def apply_skill(skill: str, answers: Any, args=None, cfg=None) -> dict:
    """Validate, apply, verify, record -- through the runner, not around it.

    The answers are checked against a freshly proposed card before the entry
    runs, so a bad answer is a refusal the caller can act on rather than a gap
    recorded in the owner's build. The runner then proposes again on its own,
    which is cheap by construction (propose is read-only) and is the
    correct behaviour anyway: what is applied is checked against the machine as
    it is now, not as it was when a browser tab was opened.
    """
    args = args or _args()
    cfg = cfg or _cfg()
    package, skill_id, part = _resolve(skill)

    run = _saved(cfg, args)
    card = propose_one(package, run, args, cfg, part=part)
    try:
        clean = cards.validate_answers(card, answers)
    except cards.CardError as exc:
        raise AnswerRefused(
            str(exc),
            cards.field_for_error(str(exc),
                                  [f["id"] for f in card["fields"]])) from None

    outcome = _apply_now(run, clean, skill_id, part, requires=False)
    updated = propose_one(package, run, args, cfg, part=part)
    row = run.log[-1] if run.log else {}
    return {
        "skill": cards.card_id(card),
        "outcome": outcome,
        "card": updated,
        "changed": cards.redact_answers(card, clean),
        "stale": stale.stale_for_answers(card, clean),
        "verified": row.get("verified") or {},
        "gaps": [gap for gap in run.gaps if gap.get("skill") == skill_id],
    }


def asks_for_secret(card_id: Any, answers: Any) -> bool:
    """Do these answers fill a ``secret_entry`` field of that card?

    Used by the API's attempt limiter, so it never raises: anything it cannot
    work out counts as a secret, because the failure this protects is a caller
    grinding at a PIN and the cheap answer to "I do not know" is "count it".
    """
    if not isinstance(answers, dict) or not answers:
        return False
    try:
        package, _skill, part = _resolve(card_id)
        cfg, args = _cfg(), _args()
        card = propose_one(package, _saved(cfg, args), args, cfg, part=part)
    except Exception:  # noqa: BLE001 - a limiter never takes a route down
        return True
    secret_ids = {field["id"] for field in card["fields"]
                  if field["kind"] == "secret_entry"}
    return bool(secret_ids & set(answers))


def _apply_now(run: wizard_runner.Runner, clean: dict, skill_id: str,
               part: str, *, requires: bool) -> str:
    """Run one entry with the answers already validated. No face, by design."""
    run.renderer = lambda shown, **kwargs: clean
    step = _step_for(skill_id, part, requires=requires)
    run.entries.pop(step.entry, None)
    return run.run_step(step)


# -- accept the rest ----------------------------------------------------------

def _lock_path(cfg) -> str:
    config_dir = getattr(cfg, "CONFIG_DIR", None) or os.getcwd()
    return os.path.join(config_dir, LOCK_NAME)


def _take_lock(cfg) -> str:
    """Claim the one-run-at-a-time lock, or raise :class:`RunInProgress`."""
    path = _lock_path(cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    for attempt in (1, 2):
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise
            try:
                age = time.time() - os.path.getmtime(path)
            except OSError:
                age = LOCK_TTL_S + 1
            if attempt == 1 and age > LOCK_TTL_S:
                # A run that died leaves this behind. Honoured while it could
                # still be a live run, taken once it cannot be -- the
                # alternative is a wizard nobody can start again.
                try:
                    os.unlink(path)
                except OSError:
                    pass
                continue
            raise RunInProgress(
                "A build is already running. Wait for it to finish, or "
                "re-open this page in a few minutes.") from None
        else:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                json.dump({"pid": os.getpid(), "started_at": time.time()}, out)
            return path
    raise RunInProgress("A build is already running.")


def _drop_lock(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def run_rest(acknowledged: Any = (), args=None, cfg=None) -> dict:
    """Apply, in schedule order, every card the owner has not answered.

    Three things are never accepted on the owner's behalf, and each is a
    refusal rather than a silence on the row:

    * a **consequential** card -- a heavy download, runtime egress, a vault PIN
      still unset, a destructive re-run -- unless its id is in *acknowledged*,
      which is the browser saying the owner opened that row and read it;
    * a card carrying a **secret_entry**, because nobody typed the secret and
      applying it would record a decision the owner did not make.

    What is **not** refused, and was: the wizard's own entries -- the Ollama
    config, the build manifest, Prove's static checks. They ask the owner
    nothing, so there is no consent to be missing, and skipping them was how a
    build made entirely in a browser ended with no manifest and an unprovable
    bot. They run at their scheduled positions, only when their inputs exist,
    with their output captured and no show-once face
    (:func:`_run_support_step`). First-run setup is **not** marked complete
    while a card that blocks is still waiting: the manifest records what was
    decided, and ``complete``/``outstanding`` in the reply say what is not.

    ``acknowledged`` is checked against the real card set: an id this build
    does not have is a refusal, not an ignored string, because an unchecked id
    list is a list a caller can pad until something matches.
    """
    args = args or _args()
    cfg = cfg or _cfg()
    known = set(_scheduled_ids())
    seen: list[str] = []
    for value in (acknowledged or ()):
        card_id = value if isinstance(value, str) else ""
        if card_id not in known:
            raise SkillNotConverted(
                f"{card_id or value!r} is not a step in this build, so it is "
                f"not something to acknowledge.")
        seen.append(card_id)
    accepted = set(seen)

    lock = _take_lock(cfg)
    try:
        return _run_rest_locked(accepted, args, cfg)
    finally:
        _drop_lock(lock)


def _run_rest_locked(accepted: set[str], args, cfg) -> dict:
    from puffpai.wizard import proposal

    packages, refusals = _lifecycle_packages()
    run = _saved(cfg, args)
    state = _summary_state(run)
    if state:
        # The approved summary spelled out every size and every
        # outside service, and "Looks good" was the owner's yes to all of it.
        # That is the acknowledgement -- not a row-by-row opening.
        accepted = set(_scheduled_ids())
        extra = dict(vars(args)) if hasattr(args, "__dict__") else {}
        extra["accept_source_size"] = True
        if run.shared.get("vault_skipped"):
            extra["skip_vault"] = True
        args = types.SimpleNamespace(**extra)
        run.args = args
    # A renderer, so ``Runner.show_once()`` answers None: nothing reached from
    # here has a screen a value could be shown on exactly once, and a skill that
    # needs one declines rather than creating a secret nobody will ever see.
    run.renderer = lambda card, **kwargs: {}
    # The Building screen reads the factory's lines off this live run while
    # it works (rest_status), rather than waiting for the record on disk.
    _REST["run"] = run
    results: list[dict] = []
    #: Cards that block and were not settled. While this is non-empty the build
    #: is not finished, whatever else ran.
    outstanding: list[str] = []
    run.runner_parts = _support_parts(lambda: not outstanding)

    for step in wizard_runner.SCHEDULE:
        part = wizard_runner.lifecycle_part(step.part)
        if step.entry == NEEDS_ENTRY:
            # Asked before the plan (/wizard/needs); nothing to carry out.
            continue
        if step.entry == SUMMARY_ENTRY:
            # Never drawn from here: the console's summary screen reads a
            # keyboard. The browser approves through /wizard/approve.
            if state:
                results.append({"card": step.entry, "outcome": "done",
                                "applied": False,
                                "why": "the owner said Looks good"})
            else:
                results.append({"card": step.entry, "outcome": "needs_you",
                                "applied": False,
                                "why": "say Looks good to the plan first"})
                outstanding.append("summary")
            continue
        if part is None or not step.skill:
            results.append(_run_support_step(run, step))
            continue
        card_id = f"{step.skill}:{part}" if part else step.skill
        skill_id = step.skill
        package = packages.get(skill_id)
        if package is None or not has_lifecycle(package):
            continue
        done = run.entries.get(step.entry, {}).get("outcome")
        if done in ("done", "skipped", "not_built"):
            results.append({"card": card_id, "outcome": done,
                            "applied": False,
                            "why": "the owner already settled this one"})
            continue
        try:
            card = propose_one(package, run, args, cfg, part=part)
        except Exception as exc:
            results.append({"card": card_id, "outcome": "failed",
                            "applied": False,
                            "why": f"{type(exc).__name__}: {exc}"})
            refusals.append(f"The '{card_id}' step could not propose: "
                            f"{type(exc).__name__}: {exc}")
            if getattr(package, "blocking", False):
                outstanding.append(card_id)
            continue

        if cards.has_secret_field(card):
            results.append({"card": card_id, "outcome": "needs_you",
                            "applied": False,
                            "why": "this one asks for something only you can "
                                   "type"})
            if getattr(package, "blocking", False):
                outstanding.append(card_id)
            continue
        if card["consequential"] and card_id not in accepted:
            results.append({"card": card_id, "outcome": "needs_you",
                            "applied": False,
                            "why": "open this row and read it first: "
                                   + ", ".join(card["consequential_reasons"])})
            if getattr(package, "blocking", False):
                outstanding.append(card_id)
            continue

        answers = {}
        if step.face == wizard_runner.FACE_QUIET:
            answers = cards.validate_answers(card, proposal.answers_for_card(
                card, state, args, internet=run.shared.get("internet")))
        outcome = _apply_now(run, answers, skill_id, part, requires=True)
        row = run.log[-1] if run.log else {}
        results.append({"card": card_id, "outcome": outcome, "applied": True,
                        "why": "", "verified": row.get("verified") or {}})
        if outcome not in ("done", "not_built") and getattr(
                package, "blocking", False):
            outstanding.append(card_id)

    return {"results": results, "refusals": refusals,
            "gaps": run.gaps, "stale": list(run.stale),
            "complete": not outstanding, "outstanding": outstanding}


def _support_parts(may_complete):
    """The wizard's own entries -- the config, the manifest -- for a browser.

    The same three the console wizard runs (``cli._wizard_runner_parts``), with
    one difference stated where it happens: this run may end with a blocking
    card still waiting for the owner, so *may_complete* decides whether first-run
    setup is marked done. The manifest itself is written either way -- a record
    of what was decided is the honest artefact, and the run state says what is
    still outstanding.
    """
    from puffpai import cli
    try:
        return cli._wizard_runner_parts(may_complete)
    except Exception:  # pragma: no cover - defensive
        return {}


def _run_support_step(run: wizard_runner.Runner,
                      step: wizard_runner.Step) -> dict:
    """Run one non-card entry server-side, quietly, at its scheduled position.

    These are the entries that make a build a build -- the Ollama config, the
    build manifest, Prove's static checks -- and a dashboard-only run used to
    skip every one of them, so a browser could walk every card and end with no
    ``config/build_manifest.json`` at all and ``GET /build/prove`` answering
    ``ran: false``.

    Three things hold:

    * **Requirements are honoured.** An entry whose inputs an earlier step never
      produced is reported as waiting, and is not run against nothing.
    * **Nothing is printed.** These entries print for a console wizard; here the
      caller is an HTTP request and stdout is a server log, so it is captured
      and dropped.
    * **No face.** The runner was given a renderer, so ``show_once()`` is None.
    """
    import contextlib
    import io

    missing = [key for key in step.requires if run.shared.get(key) is None]
    if missing:
        return {"card": step.entry, "outcome": "needs_you", "applied": False,
                "why": f"waiting on {', '.join(missing)} from an earlier step"}
    # Re-run every time: the manifest is a record of the sections as they stand,
    # and a second /wizard/run after another card was applied has to rewrite it
    # rather than report the first one as still true.
    run.entries.pop(step.entry, None)
    sink = io.StringIO()
    try:
        with contextlib.redirect_stdout(sink):
            outcome = run.run_step(step)
    except Exception as exc:  # noqa: BLE001 - one entry never takes the route down
        return {"card": step.entry, "outcome": "failed", "applied": False,
                "why": f"{type(exc).__name__}: {exc}"}
    return {"card": step.entry, "outcome": outcome, "applied": True, "why": ""}


# -- resume -------------------------------------------------------------------

def resume_offer(args=None, cfg=None) -> dict:
    """Is there a stopped setup to continue, and what would continuing keep?

    Read-only, like :func:`propose_cards`: it reads the record and observes
    nothing. The re-verify that decides what a continued run may actually skip
    happens in :func:`resume_choice`, when the owner has said to continue --
    verifying costs a disk read and an Ollama call, and a page load is not a
    reason to make them.
    """
    args = args or _args()
    cfg = cfg or _cfg()
    run, why = wizard_runner.find_stopped_run(args=args, cfg=cfg)
    if run is not None and wizard_runner.nothing_answered(run):
        # Only this computer was checked (the first screen was open): the
        # page just carries on from there.
        return {"offer": False, "why": "Nothing was answered yet."}
    if run is None:
        return {"offer": False,
                "why": why or "There is no stopped setup to continue."}
    summary = run.resume_summary()
    summary["offer"] = True
    summary["why"] = ""
    return summary


def resume_choice(choice: Any, args=None, cfg=None) -> dict:
    """``continue`` or ``restart``, applied to the record. Same rules as the CLI.

    **Continue is not "trust the file".** Every blocking card the record calls
    done is verified against this machine again (:meth:`Runner.reverify_blocking`)
    and anything that does not hold is dropped from the record, so the next
    ``GET /wizard/cards`` shows it unanswered and ``POST /wizard/run`` asks it
    again. The vault is checked on disk whatever the record says.

    **Restart archives.** The old record is moved aside, not deleted.

    The same one-run-at-a-time lock ``/wizard/run`` takes is taken here: both
    write the run record, and two writers of one record describe neither run.
    """
    args = args or _args()
    cfg = cfg or _cfg()
    if choice not in ("continue", "restart"):
        raise AnswerRefused(
            "Say whether to continue the stopped setup or start over: "
            "'continue' or 'restart'.", "choice")
    run, why = wizard_runner.find_stopped_run(args=args, cfg=cfg)
    if run is None:
        return {"choice": choice, "resumed": False, "archived": False,
                "reasked": [], "why": why or "there is no stopped setup here"}
    lock = _take_lock(cfg)
    try:
        if choice == "restart":
            moved = wizard_runner.archive_state(run.state_path())
            return {"choice": choice, "resumed": False,
                    "archived": bool(moved), "reasked": [],
                    "why": "the stopped setup was set aside"}
        reasked = run.reverify_blocking()
        run.save()
        return {"choice": choice, "resumed": True, "archived": False,
                "reasked": reasked, "why": ""}
    finally:
        _drop_lock(lock)


def run_state(args=None, cfg=None) -> dict:
    """What the log says: one row per skill, the gaps, and what is stale.

    Read back through :meth:`Runner.load_state`, which re-renders every stored
    gap through the fix registry -- a command line this build prints is never
    read off disk.
    """
    args = args or _args()
    cfg = cfg or _cfg()
    run = wizard_runner.Runner(args=args, cfg=cfg, non_interactive=True)
    found = run.load_state()
    return {
        "schema": wizard_runner.STATE_SCHEMA,
        "found": bool(found),
        "started_at": run.started_at if found else None,
        # The order is carried explicitly: a JSON object's key order is not a
        # promise, and the spec's fixed order is part of what the log says.
        "order": list(wizard_runner.SKILL_ORDER),
        "skills": run.skill_status(),
        "entries": run.entries,
        "log": run.log,
        "gaps": run.gaps,
        "stale": list(run.stale),
        "next_entry": run.next_entry(),
        # The resume banner's whole input. It rides on the state route rather
        # than having one of its own because it is the same question the log
        # answers -- what happened last time -- and a second request for one
        # boolean is a second thing that can be out of date with the first.
        "resume": resume_offer(args, cfg),
    }


# -- the conversation: the same questions the console asks ---------------------
#
# The terminal wizard and the browser wizard
# are equal -- same questions, same order, same summary, one "Looks good". The
# browser draws exactly what these return; it keeps no list of questions of its
# own.

SUMMARY_ENTRY = "runner.summary"
FLOW_SCHEMA = "wizard_flow/v1"


def setup_in_progress(args=None, cfg=None) -> bool:
    """True ONLY when this computer's wizard run is under way: its goal is
    saved and its plan is not approved yet. A positive signal from the run's
    own record: no record, an unreadable record, or a record with an approved
    plan all answer False (fail closed), so deleting or damaging a file can
    never make a finished bot look "not set up yet"."""
    try:
        run = _saved(cfg or _cfg(), args or _args())
        return _settled(run, "purpose", "goal") and _summary_state(run) is None
    except Exception:  # noqa: BLE001 - not knowing is "set up"
        return False


def _summary_state(run: wizard_runner.Runner) -> dict | None:
    state = run.shared.get("summary")
    return state if isinstance(state, dict) and state.get("approved") else None


def _ensure_checked(run: wizard_runner.Runner) -> None:
    """Check this computer (Discover) if this run has not yet. Quiet, like the
    console: its card is never drawn, and it answers nothing."""
    if _settled(run, "discover", ""):
        return
    _apply_now(run, {}, "discover", "", requires=False)


def flow(args=None, cfg=None) -> dict:
    """What the browser shows next, and everything it needs to show it.

    ``questions`` are the cards the console asks before the summary, in the
    console's order, each marked applied or not. ``next`` is one of
    ``goal``, ``identity``, ``find``, ``summary``, ``hub``, ``vault``, ``build``,
    ``building`` or ``done``. Read-only.
    """
    from puffpai.wizard import proposal, wording

    args = args or _args()
    cfg = cfg or _cfg()
    run = _saved(cfg, args)
    packages, refusals = _lifecycle_packages()
    provisional = dict(run.shared)
    questions: list[dict] = []
    for card_id in wizard_runner.asked_card_ids():
        skill_id, part = cards.split_card_id(card_id)
        if skill_id != "purpose":
            continue
        package = packages.get(skill_id)
        if package is None:
            continue
        settled = _settled(run, skill_id, part)
        try:
            card, found = _propose(package, run, args, cfg, part=part,
                                   shared=provisional)
        except Exception as exc:  # noqa: BLE001 - one card never sinks the page
            refusals.append(f"The '{card_id}' question could not be drawn: "
                            f"{type(exc).__name__}")
            continue
        if not settled and isinstance(found.get("shared_preview"), dict):
            provisional.update(found["shared_preview"])
        questions.append({"id": card_id, "card": card, "applied": settled})

    state = _summary_state(run)
    hub_card = None
    if state and proposal.approved_refs(state, "home_assistant") and not \
            _settled(run, "tools", ""):
        package = packages.get("tools")
        if package is not None:
            try:
                hub_card = propose_one(package, run, args, cfg)
            except Exception:  # noqa: BLE001
                hub_card = None
    vault_ready = wizard_runner.vault_on_disk(cfg)
    status = rest_status()
    mode = wording.mode_of(run.shared)
    name = str(run.shared.get("bot_name") or "your bot")

    def said(key: str) -> str:
        return " ".join(wording.say(key, mode, name=name).split())

    handoff = None
    if run.entries.get("handoff.card", {}).get("outcome") in ("done",):
        package = packages.get("handoff")
        if package is not None:
            try:
                handoff = propose_one(package, run, args, cfg)
            except Exception:  # noqa: BLE001
                handoff = None

    looked = run.entries.get(FIRST_LOOK_ENTRY, {}).get("outcome") == "done"
    if not looked and not any(q["applied"] for q in questions):
        # What this computer has and the thinking model that fits it, before
        # the goal question -- the same first screen the console shows.
        nxt = "first_look"
    elif not all(q["applied"] for q in questions):
        nxt = next(q["id"] for q in questions if not q["applied"])
        nxt = nxt.split(":", 1)[1]
    # No "Finding things for your bot" pick step before the plan: the build
    # plan finds, checks what fits each
    # job and shows ONE setup list with ONE Yes; nothing is installed before
    # it. /wizard/find stays for older pages only.
    elif (not state and needs_engine() is not None and not _needs_settled(run)
          and finding_engine() is None):
        # "Finding out what a <kind> bot needs", only when this build
        # has the needs engine and not the find step. Without either the plan
        # comes straight after the name, as before.
        nxt = "needs"
    elif not state:
        nxt = "summary"
    elif hub_card is not None:
        nxt = "hub"
    elif not vault_ready and not run.shared.get("vault_skipped"):
        nxt = "vault"
    elif status["running"]:
        nxt = "building"
    elif handoff is not None:
        nxt = "done"
    else:
        nxt = "build"
    return {
        "schema": FLOW_SCHEMA,
        "next": nxt,
        "wording": mode,
        "name": str(run.shared.get("bot_name") or ""),
        "questions": questions,
        "approved": bool(state),
        "hub": hub_card,
        "vault": {"ready": vault_ready,
                  "title": said("vault_title"),
                  "what": said("vault_what"),
                  "pin": said("vault_pin"),
                  "pin_help": said("vault_pin_help"),
                  "again": said("vault_pin_again"),
                  "again_help": said("vault_pin_again_help"),
                  # Where it lives, asked before the PIN. The
                  # folder itself, and the chooser, come from /vault/location.
                  "where": _vault_where_words()},
        "building_line": said("building"),
        "building": status,
        "handoff": handoff,
        "refusals": refusals,
        # "Do you have a GitHub account?": the browser asks it
        # once, after the goal and before the plan, and never blocks on it.
        # It rides beside ``next`` rather than being a step of its own, so the
        # terminal's order is unchanged.
        "github": {"asked": run.entries.get(GITHUB_ENTRY, {}).get("outcome") == "done"},
        # "Who should do Toolsmith's installs?": asked once, after the GitHub
        # question and before the build, beside ``next`` like GitHub. The
        # setting itself lives in /toolsmith/model (security/toolsmith_cloud.py).
        "toolsmith": {"asked": run.entries.get(TOOLSMITH_ENTRY, {}).get("outcome") == "done"},
    }


def _vault_where_words() -> dict:
    """The vault's place question and its three answers, the same words the
    terminal card uses (security/vault_place.py)."""
    from puffpai.security import vault_place
    return {"question": vault_place.QUESTION,
            "choices": [{"value": value, "label": label, "help": help_}
                        for value, label, help_ in vault_place.CHOICES],
            "encrypted_drive_line": vault_place.ENCRYPTED_DRIVE_LINE,
            "network_line": vault_place.NETWORK_LINE,
            "not_a_vault_line": vault_place.NOT_A_VAULT_LINE}


FIRST_LOOK_ENTRY = "runner.first_look"
GITHUB_ENTRY = "runner.github"
GITHUB_CHOICES = ("connected", "skipped")


def github_choose(choice: Any, args=None, cfg=None) -> dict:
    """The browser's GitHub question answered: ``connected`` or ``skipped``.

    Records only that it was asked, so it is not asked again. Connecting
    itself goes through /github/*; nothing about the account is kept here.
    """
    if choice not in GITHUB_CHOICES:
        raise AnswerRefused("'choice' is 'connected' or 'skipped'.", "choice")
    args = args or _args()
    cfg = cfg or _cfg()
    lock = _take_lock(cfg)
    try:
        run = _saved(cfg, args)
        now = time.time()
        run.entries[GITHUB_ENTRY] = {"outcome": "done", "at": now}
        run.log.append({"entry": GITHUB_ENTRY, "skill": "", "part": "github",
                        "outcome": "done", "started_at": now,
                        "finished_at": now, "note": f"GitHub question: {choice}",
                        "refusal": ""})
        run.save()
    finally:
        _drop_lock(lock)
    return {"choice": choice}


TOOLSMITH_ENTRY = "runner.toolsmith_model"
TOOLSMITH_CHOICES = ("local", "cloud", "skipped")


def toolsmith_choose(choice: Any, args=None, cfg=None) -> dict:
    """The browser's "Who should do Toolsmith's installs?" answered: ``local``,
    ``cloud`` or ``skipped``.

    Records only that it was asked, so it is not asked again. The choice
    itself, the model and the key go through /toolsmith/model
    (``toolsmith_cloud``); no key ever reaches this record.

    ``cloud`` is recorded only once Toolsmith's setting really says cloud
    (the key saved). Otherwise her wish is kept (the build holds before
    Toolsmith's first run, ``toolsmith_cloud.held``), the step stays not
    done, and :class:`ToolsmithNotSaved` says so.
    """
    if choice not in TOOLSMITH_CHOICES:
        raise AnswerRefused("'choice' is 'local', 'cloud' or 'skipped'.", "choice")
    from puffpai.security import toolsmith_cloud
    if choice == "cloud" and toolsmith_cloud.status().get("where") != "cloud":
        toolsmith_cloud.record_wish()
        if toolsmith_cloud.consent_outdated():
            # Her key is saved; only her yes is to an older sentence.
            raise ToolsmithNotSaved(toolsmith_cloud.RECONSENT_HOLD_LINE)
        raise ToolsmithNotSaved("Your Anthropic key is not saved yet, so Toolsmith still "
                                "uses this computer. BuildBot asks for the key before "
                                "Toolsmith's first install.")
    toolsmith_cloud.clear_wish()
    if choice == "local" and toolsmith_cloud.consent_outdated():
        # Only her own pick of This computer ends a yes to the old sentence;
        # "skipped" leaves the hold in place until she picks.
        toolsmith_cloud.use_local()
    args = args or _args()
    cfg = cfg or _cfg()
    lock = _take_lock(cfg)
    try:
        run = _saved(cfg, args)
        now = time.time()
        run.entries[TOOLSMITH_ENTRY] = {"outcome": "done", "at": now}
        run.log.append({"entry": TOOLSMITH_ENTRY, "skill": "", "part": "toolsmith_model",
                        "outcome": "done", "started_at": now,
                        "finished_at": now, "note": f"Toolsmith question: {choice}",
                        "refusal": ""})
        run.save()
    finally:
        _drop_lock(lock)
    return {"choice": choice}


def first_look(args=None, cfg=None) -> dict:
    """The first screen: what this computer has and the thinking model that
    fits it. Checks this computer first (quietly, as the console does), which
    is the one thing this writes."""
    from puffpai.wizard import first_look as wizard_first_look

    args = args or _args()
    cfg = cfg or _cfg()
    run = _saved(cfg, args)
    _ensure_checked(run)
    # A person is choosing here, so the most capable model that fits is shown
    # (an unattended --yes run is not committed to the biggest download).
    return wizard_first_look.screen(args=args, cfg=cfg, shared=run.shared,
                                    sections=run.sections,
                                    non_interactive=False)


def first_look_choose(model: Any = None, args=None, cfg=None) -> dict:
    """"Use this one" or a pick under "Change": kept for the Brain card, and the
    first screen marked done. Only a model the screen offered is taken."""
    from puffpai.wizard import first_look as wizard_first_look

    if model is not None and (not isinstance(model, str) or len(model) > 200):
        raise AnswerRefused("'model' is a short piece of text.", None)
    args = args or _args()
    cfg = cfg or _cfg()
    lock = _take_lock(cfg)
    try:
        run = _saved(cfg, args)
        _ensure_checked(run)
        shown = wizard_first_look.screen(args=args, cfg=cfg, shared=run.shared,
                                         sections=run.sections,
                                         non_interactive=False)
        try:
            entry = wizard_first_look.choose(run.shared, shown, model)
        except ValueError as exc:
            raise AnswerRefused(str(exc), "model") from None
        now = time.time()
        run.entries[FIRST_LOOK_ENTRY] = {"outcome": "done", "at": now}
        run.log.append({"entry": FIRST_LOOK_ENTRY, "skill": "",
                        "part": "first_look", "outcome": "done",
                        "started_at": now, "finished_at": now,
                        "note": "first screen seen in the browser",
                        "refusal": ""})
        run.save()
    finally:
        _drop_lock(lock)
    return {"model": entry.get("model")}


def summary(changes: Any = None, args=None, cfg=None) -> dict:
    """The one summary, with *changes* drawn in. Nothing is approved here.

    The first call checks this computer (quietly, the way the console does
    between the questions and the plan); that is the one thing this route
    writes, and it is the same entry the console would write.
    """
    from puffpai.wizard import proposal

    args = args or _args()
    cfg = cfg or _cfg()
    run = _saved(cfg, args)
    _ensure_checked(run)
    changes = _clean_changes(changes)
    shown = proposal.summary_for(args=args, cfg=cfg, shared=run.shared,
                                 sections=run.sections, changes=changes)
    # Validate the changes against what is shown: an id the plan does not
    # offer is refused now, not at "Looks good".
    try:
        proposal.decide(shown, changes=changes, args=args)
    except proposal.ProposalError as exc:
        raise AnswerRefused(str(exc), None) from None
    return shown


def _clean_changes(changes: Any) -> dict:
    if changes is None:
        return {"on": {}}
    if not isinstance(changes, dict):
        raise AnswerRefused("Changes are an object: on, model, reach.", None)
    out: dict = {"on": {}}
    on = changes.get("on") or {}
    if not isinstance(on, dict) or len(on) > 80:
        raise AnswerRefused("'on' is a short list of item ids and true or "
                            "false.", None)
    out["on"] = {str(k): v for k, v in on.items()}
    for key in ("model", "reach"):
        value = changes.get(key)
        if value is not None:
            if not isinstance(value, str) or len(value) > 200:
                raise AnswerRefused(f"'{key}' is a short piece of text.", None)
            out[key] = value
    town = changes.get("town")
    if town is not None:
        # The home town typed on the plan's weather line (proposal.town_question).
        if not isinstance(town, str) or len(town) > 200:
            raise AnswerRefused("'town' is a short piece of text.", "town")
        out["town"] = town
    return out


def approve(changes: Any = None, args=None, cfg=None) -> dict:
    """"Looks good": record the decision the way the console's summary does.

    The decision goes into the run's shared notebook under ``summary`` and the
    summary entry is marked done, so ``/wizard/run`` (or the console, resumed)
    applies exactly this plan. One at a time, behind the run lock.
    """
    from puffpai.wizard import proposal

    args = args or _args()
    cfg = cfg or _cfg()
    lock = _take_lock(cfg)
    try:
        run = _saved(cfg, args)
        _ensure_checked(run)
        changes = _clean_changes(changes)
        shown = proposal.summary_for(args=args, cfg=cfg, shared=run.shared,
                                     sections=run.sections, changes=changes)
        try:
            state = proposal.decide(shown, changes=changes, args=args)
        except proposal.ProposalError as exc:
            raise AnswerRefused(str(exc), None) from None
        run.shared["summary"] = state
        run.shared["interview"] = proposal.settle_interview(
            run.shared.get("interview"), state)
        now = time.time()
        if not _needs_settled(run):
            # No needs step ran (a build without the engine): the entry is
            # settled here, so a finished browser build is not a stopped one.
            run.entries[NEEDS_ENTRY] = {"outcome": "skipped", "at": now}
            run.log.append({"entry": NEEDS_ENTRY, "skill": "", "part": "needs",
                            "outcome": "skipped", "started_at": now,
                            "finished_at": now,
                            "note": "no needs step in this build",
                            "refusal": ""})
        _switch_on_named_shelves(state)
        run.entries[SUMMARY_ENTRY] = {"outcome": "done", "at": now}
        run.log.append({"entry": SUMMARY_ENTRY, "skill": "", "part": "summary",
                        "outcome": "done", "started_at": now,
                        "finished_at": now,
                        "note": "summary approved in the browser",
                        "refusal": ""})
        run.save()
    finally:
        _drop_lock(lock)
    _toolsmith_key_ready()
    return {"approved": True,
            "on": [item["id"] for item in state["items"] if item["on"]]}


def _switch_on_named_shelves(state: Any) -> None:
    """The plan named the skill shelves and code sites its search asks for
    each job (``plan_outside.stay_on``): her Looks good switches them on, the
    way the old "Finding things for your bot" step did. Not for a bot that
    stays offline, and never one she switched off herself."""
    from puffpai import plan_outside
    try:
        if not plan_outside.build_finds():
            return
        reach = (state or {}).get("reach") if isinstance(state, dict) else None
        if reach == plan_outside.REACH_OFFLINE or plan_outside._sealed():  # noqa: SLF001
            return
        from puffpai.finding import find as finding_find
        finding_find.switch_on_shelves(by="wizard")
    except Exception as exc:  # noqa: BLE001 - the plan then finds less
        _log.warning("wizard: shelves not switched on (%s)", type(exc).__name__)


def skip_vault(args=None, cfg=None) -> dict:
    """The owner chose to set the vault PIN later. Recorded, not hidden: the
    closing screen says nothing is locked away yet."""
    args = args or _args()
    cfg = cfg or _cfg()
    run = _saved(cfg, args)
    run.shared["vault_skipped"] = True
    run.save()
    return {"skipped": True}


_REST: dict[str, Any] = {"thread": None, "result": None, "error": "",
                         "run": None}

#: At most this many Building lines are sent to the browser at once.
MAX_BUILD_LINES = 300


def build_lines(shared: Any) -> list[str]:
    """The factory's progress lines, as they arrive (I-6: each applier's
    ``ctx.progress`` appends to ``shared["factory"]["lines"]``). Plain text,
    capped; an older build that has no lines gives none."""
    record = shared.get("factory") if isinstance(shared, dict) else None
    lines = record.get("lines") if isinstance(record, dict) else None
    if not isinstance(lines, list):
        return []
    return [cards.plain(line, 300) for line in list(lines)[-MAX_BUILD_LINES:]
            if isinstance(line, str) and line.strip()]


#: What a waiting item may be, on the Building screen.
WAITING_HELD = "held"
WAITING_BOX = "waiting_box"
#: Box states in which Windows' Linux feature must be turned on first.
WSL_MISSING_STATES = ("no_wsl", "wsl_off")


def _held_line(words, code: str, said: list[str], used: set[int]) -> str:
    """The held line for one reason code: the exact line the build said
    (its facts filled in, like the host or the size) when one matches the
    code's words, else the code's words without facts."""
    template = words.REASONS.get(code) if code else None
    if template:
        pattern = re.escape(words.HELD).replace(re.escape("{reason}"), "{reason}")
        reason = re.sub(r"\\\{[a-z_]+\\\}", ".+?", re.escape(template))
        rule = re.compile("^" + pattern.format(reason=reason) + "$")
        for number, line in enumerate(said):
            if number not in used and rule.match(line):
                used.add(number)
                return line
    return words.HELD.format(reason=words.reason_words(code) if code
                             else words.REASON_OTHER)


def build_waiting(mode: str = "standard", said: Any = ()) -> list[dict]:
    """What the build left waiting for the owner, both faces.

    * Each thing BuildBot added from a plan that Drawbridge held and that can
      take a yes: ``{"id", "name", "status": "held", "lines", "yes", "no"}``.
    * One line when things wait for the locked box and Windows' Linux
      feature is not on: ``{"id": "box:wsl", "status": "waiting_box",
      "count", "lines", "button"}``.

    Read from the integration records and the box status when they are in
    this build; nothing otherwise. Never raises."""
    from puffpai.wizard import wording
    out: list[dict] = []
    records = _optional("puffpai.integrate.records")
    if records is None:
        return out
    try:
        rows = [r for r in records.all() if isinstance(r, dict)]
    except Exception as exc:  # noqa: BLE001 - a page never breaks on this
        _log.warning("The waiting list could not be read: %s", type(exc).__name__)
        return out
    words = _optional("puffpai.integrate.wording")
    said = [str(line) for line in said or () if isinstance(line, str)]
    used: set[int] = set()
    boxed = 0
    for rec in rows:
        if rec.get("by") != "wizard":
            continue
        held = rec.get("held") if isinstance(rec.get("held"), dict) else {}
        if rec.get("status") == WAITING_BOX:
            boxed += 1
            continue
        if rec.get("status") != WAITING_HELD or not held.get("ticket_target") \
                or words is None:
            continue
        codes = [str(c) for c in held.get("reasons") or ()]
        out.append({"id": str(rec.get("id") or ""),
                    "name": cards.plain(rec.get("name"), 80),
                    "status": WAITING_HELD,
                    "lines": [_held_line(words, codes[0] if codes else "",
                                         said, used),
                              words.HELD_WAITING],
                    "yes": wording.say("building_held_yes", mode),
                    "no": wording.say("building_held_no", mode)})
    if boxed:
        driver = _optional("puffpai.box.driver")
        try:
            state = driver.status().state if driver is not None else ""
        except Exception:  # noqa: BLE001 - status never breaks a page
            state = ""
        if state in WSL_MISSING_STATES:
            out.append({"id": "box:wsl", "status": WAITING_BOX, "count": boxed,
                        "lines": [wording.say(
                            "building_wsl", mode, n=boxed,
                            things="thing" if boxed == 1 else "things"),
                            wording.say("building_wsl_after", mode)],
                        "button": wording.say("building_wsl_button", mode)})
    return out


def decide_waiting(item_id: str, yes: bool, device_id: str) -> list[str]:
    """The owner's yes or no to a held item (``integrate.apply.decide``), as the
    lines to show. Never raises."""
    apply = _optional("puffpai.integrate.apply")
    words = _optional("puffpai.integrate.wording")
    if apply is None or not callable(getattr(apply, "decide", None)):
        return [getattr(words, "NOT_IN_BUILD", "") or
                "This is not in this build yet. Nothing was changed."]
    try:
        got = apply.decide(item_id, yes, device_id=device_id)
    except Exception as exc:  # noqa: BLE001 - DecideError carries its line
        line = getattr(exc, "line", "")
        if not line:
            _log.warning("A held item could not be decided: %s",
                         type(exc).__name__)
        return [cards.plain(line, 300) or "That did not work. Nothing was changed."]
    got = got if isinstance(got, dict) else {}
    out = [cards.plain(x, 300) for x in got.get("lines") or ()]
    out.append(cards.plain(got.get("line"), 300))
    return [x for x in out if x]


def turn_on_wsl(device_id: str, mode: str = "standard") -> str:
    """Ask Windows to turn on its Linux feature (``box.driver.install_wsl``),
    under a consent ticket for *device_id*. One plain line; never raises."""
    from puffpai.wizard import wording
    driver = _optional("puffpai.box.driver")
    install = getattr(driver, "install_wsl", None) if driver is not None else None
    if install is None:
        return wording.say("building_wsl_later", mode)
    try:
        from puffpai.security import consent_tickets
        ticket = consent_tickets.mint(
            "box_setup", consent_tickets.target_box(driver.distro_name()),
            wording.say("building_wsl_sentence", mode), device_id)
        got = install(ticket=ticket, device_id=device_id)
    except Exception as exc:  # noqa: BLE001 - an honest line, never a crash
        _log.warning("Turning on the Linux feature did not start: %s",
                     type(exc).__name__)
        return wording.say("building_wsl_later", mode)
    return cards.plain((got or {}).get("message"), 300) or \
        wording.say("building_wsl_later", mode)


def waiting_console(ctx, *, ask=None, out=print) -> None:
    """The terminal's Building screen tail: yes or no to each held item, and
    the Windows' Linux feature line. Only for a person at this console."""
    from puffpai.wizard import render_cli, wording
    if not getattr(ctx, "console", False) or ctx.non_interactive:
        return
    from puffpai.security import consent_tickets
    mode = wording.mode_of(ctx.shared)
    device = consent_tickets.terminal_device_id()
    render_cli.waiting_screen(
        build_waiting(mode, build_lines(ctx.shared)),
        decide=lambda item_id, yes: decide_waiting(item_id, yes, device),
        turn_on=lambda: turn_on_wsl(device, mode), ask=ask, out=out, mode=mode)


def rest_status() -> dict:
    from puffpai.wizard import wording
    thread = _REST.get("thread")
    run = _REST.get("run")
    running = bool(thread is not None and thread.is_alive())
    mode = wording.mode_of(getattr(run, "shared", None) or {})
    return {"running": running,
            "result": _REST.get("result"), "error": _REST.get("error") or "",
            "lines": build_lines(getattr(run, "shared", None)),
            "waiting": [] if running else build_waiting(
                mode, build_lines(getattr(run, "shared", None))),
            "go_on": wording.say("building_go_on", mode),
            # Toolsmith waits for the vault (or its key is not ready while
            # this build runs): the Building screen asks for her PIN here.
            "vault_wait": _toolsmith_vault_wait(running)}


def start_rest(args=None, cfg=None) -> dict:
    """Build in the background: downloads take minutes, a request does not.

    The same :func:`run_rest` as ``POST /wizard/run``, on a thread, with its
    result kept for ``GET /wizard/build``. Refused while one is running.
    """
    import threading

    if rest_status()["running"]:
        raise RunInProgress("The build is already running.")
    args = args or _args()
    cfg = cfg or _cfg()
    run = _saved(cfg, args)
    if not _summary_state(run):
        raise AnswerRefused("Say \"Looks good\" to the plan first.", None)
    _REST.update({"result": None, "error": "", "run": None})

    def work() -> None:
        try:
            _REST["result"] = run_rest((), args=args, cfg=cfg)
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            _REST["error"] = f"{type(exc).__name__}: {exc}"

    thread = threading.Thread(target=work, name="wizard-build", daemon=True)
    _REST["thread"] = thread
    thread.start()
    _toolsmith_key_ready()
    return {"started": True}


# -- Toolsmith's key for the session (security/toolsmith_cloud.py) -------------
#
# Owner, 2026-10-15: she saved the Anthropic key early in the wizard, the vault
# idle-locked while she went on, and the first install paused for a vault she
# could not open from the wizard. So the plan's approval and the build's start
# make the key ready (vault open, cloud Toolsmith on). For her keep time
# (1 hour by default), Toolsmith work is the wizard's build running, and each
# wizard step she takes (note_step: any wizard POST answered OK, or a poll of
# the build while it runs), counted at the time she took it. A wizard left
# alone with the plan never approved lets go of the key after her keep time,
# like any other quiet (coordinator, 2026-10-15: no 24 hours for an abandoned
# wizard).

def _toolsmith_key_ready() -> None:
    try:
        from puffpai.security import toolsmith_cloud
        toolsmith_cloud.take_for_plan()
    except Exception:  # noqa: BLE001 - a run or an unlock takes it later
        _log.info("wizard: Toolsmith's key not made ready")


def _toolsmith_vault_wait(running: bool) -> str:
    try:
        from puffpai.security import toolsmith_cloud
        return toolsmith_cloud.vault_wait_line(running)
    except Exception:  # noqa: BLE001 - a status line never breaks the screen
        return ""


def _wizard_going() -> bool:
    """The wizard's build is running (its thread is alive). A wizard that is
    only under way does not count here: her steps count (:func:`note_step`)."""
    thread = _REST.get("thread")
    return bool(thread is not None and thread.is_alive())


def note_step(method: str, path: str) -> None:
    """api_server, after a wizard request answered OK: a POST (a step she
    took) or a GET /wizard/build while the build runs is Toolsmith activity
    now. Other GETs (the page loading, flow reads) are not. Never raises."""
    try:
        if method == "POST" or (method == "GET" and path == "/wizard/build"
                                and _wizard_going()):
            from puffpai.security import toolsmith_cloud
            toolsmith_cloud.wizard_touched()
    except Exception:  # noqa: BLE001 - never breaks a reply
        _log.debug("wizard: step not noted for the Toolsmith key")


def _hook_toolsmith_lease() -> None:
    try:
        from puffpai.security import toolsmith_cloud
        toolsmith_cloud.set_wizard_probe(_wizard_going)
    except Exception:  # noqa: BLE001 - without it her keep time runs as before
        _log.info("wizard: Toolsmith key lease probe not set")


_hook_toolsmith_lease()



# -- "Finding out what a <kind> bot needs" ------------------------------------
#
# Between the name and the plan, both wizards turn the
# goal into the jobs the bot needs (the needs engine), let the owner
# tick, untick and add, say exactly which words would leave this computer, and
# then look for things that fit those jobs (``find_parts``). The plan is then
# drawn by /wizard/summary as before, grouped by job (proposal.py).
#
# Privacy rules held here: the goal text is never logged, never put in a run
# note and never sent anywhere by this module; the only words that may leave
# are the ``Sendable`` phrases the engine hands back, and only after the owner
# pressed "Start looking".

NEEDS_ENTRY = "runner.needs"
#: How long the brain may take over the job list before the rules' list is used.
NEEDS_DEADLINE_S = 60.0
MAX_TICKED = 24
MAX_ADDED = 10
MAX_ADDED_CHARS = 120
MAX_ALLOWED = 20
MAX_JOBS_SHOWN = 16
LOOK_CHOICES = ("online", "here")


def _optional(modname: str):
    """Another part's module, or ``None`` when it is not in this build.

    A module that is present but fails to import fails loudly: that is a bug,
    not "not in this build yet"."""
    try:
        return importlib.import_module(modname)
    except ModuleNotFoundError as e:
        if e.name is not None and (e.name == modname or modname.startswith(e.name + ".")):
            return None          # not in this build yet
        raise                    # exists but broken: fail loudly


def needs_engine():
    """``puffpai.wizard.needs``, or ``None``."""
    return _optional("puffpai.wizard.needs")


def parts_engine():
    """``puffpai.wizard.find_parts``, or ``None``."""
    return _optional("puffpai.wizard.find_parts")


def finding_engine():
    """``puffpai.finding.api`` (the find step), or ``None``."""
    return _optional("puffpai.finding.api")


FIND_ENTRY = "finding.things"


def _needs_settled(run: wizard_runner.Runner) -> bool:
    return run.entries.get(NEEDS_ENTRY, {}).get("outcome") in (
        "done", "skipped", "not_built")


def _goal_of(shared: Any, sections: Any = None) -> str:
    interview = (shared or {}).get("interview") if isinstance(shared, dict) else None
    if not isinstance(interview, dict):
        interview = ((sections or {}).get("persona") or {}).get("interview") \
            if isinstance(sections, dict) else None
    goal = (interview or {}).get("goal") if isinstance(interview, dict) else ""
    return goal if isinstance(goal, str) else ""


def early_kind(goal: str) -> str:
    """The bot's subject before the engine has answered, or ``""``."""
    words = _optional("puffpai.catalogs.personal_words")
    if words is None or not goal:
        return ""
    try:
        return cards.plain(words.subject_of(goal), 60)
    except Exception:  # noqa: BLE001 - only the title waits for the answer then
        return ""


def _is_needs(value: Any) -> bool:
    return isinstance(value, dict) and value.get("schema") == "needs/v1" \
        and isinstance(value.get("jobs"), list)


def _jobs(needs: Any) -> list[dict]:
    if not _is_needs(needs):
        return []
    return [job for job in needs["jobs"] if isinstance(job, dict)
            and isinstance(job.get("id"), str)]


def _ticked_ids(needs: Any) -> list[str]:
    return [job["id"] for job in _jobs(needs) if job.get("ticked")]


class NeedsProgress:
    """The needs screen's live lines, from the engine's jobs as they arrive.

    The same object draws the browser's lines (all of them, the one in
    progress ending in "...") and the terminal's (only lines that are
    finished, so nothing is printed twice)."""

    def __init__(self, kind: str = "") -> None:
        self.kind = kind
        self.asked = 0
        self.usual = 0
        self.seen = 0
        self.result: dict | None = None
        self.done = False

    @classmethod
    def from_result(cls, result: Any) -> "NeedsProgress":
        progress = cls()
        if _is_needs(result):
            progress.finish(result)
        return progress

    def on_job(self, job: Any) -> None:
        if not isinstance(job, dict):
            return
        self.seen += 1
        if job.get("source") == "asked":
            self.asked += 1
        elif job.get("source") == "usual":
            self.usual += 1

    def finish(self, result: dict) -> None:
        self.result = result
        self.done = True
        kind = cards.plain(result.get("kind"), 60)
        if kind:
            self.kind = kind

    def title(self, mode: str) -> str:
        from puffpai.wizard import wording
        if self.kind:
            return wording.say("needs_title", mode, kind=self.kind)
        return wording.say("needs_title_unknown", mode)

    def lines(self, mode: str, *, final_only: bool = False) -> list[str]:
        from puffpai.wizard import wording

        def say(key: str, **values: Any) -> str:
            return " ".join(wording.say(key, mode, **values).split())

        out: list[str] = []
        if not self.done and not self.seen:
            return [] if final_only else [say("needs_reading")]
        out.append(say("needs_reading_done"))
        jobs = _jobs(self.result) if self.done else []
        asked = (sum(1 for j in jobs if j.get("source") == "asked")
                 if self.done else self.asked)
        usual = (sum(1 for j in jobs if j.get("source") == "usual")
                 if self.done else self.usual)
        if self.done or self.usual or not final_only:
            out.append(say("needs_from_words", count=asked))
        if self.done:
            prompt = (self.result or {}).get("prompt")
            if prompt == "stage-A":
                out.append(say("needs_slow"))
            elif prompt == "rules":
                out.append(say("needs_failed"))
            elif self.kind:
                out.append(say("needs_asking_done", kind=self.kind,
                               count=usual))
            private = [cards.plain(w, 40) for w in
                       ((self.result or {}).get("private_left_out") or ())
                       if isinstance(w, str) and w.strip()]
            if private:
                out.append(say("needs_private", words=", ".join(private)))
        elif self.kind and not final_only:
            out.append(say("needs_asking", kind=self.kind))
        return out


def _and_list(words: list[str]) -> str:
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def _source_labels(needs: Any) -> list[str]:
    """Which outside places "Start looking" asks, from the where-to-look
    registry: its live rows for the part kinds of the ticked jobs.
    None when the registry is not in this build: then nothing is asked."""
    where = _optional("puffpai.catalogs.where_to_look")
    if where is None:
        return []
    kinds: list[str] = []
    for job in _jobs(needs):
        if not job.get("ticked"):
            continue
        for part in job.get("parts") or ():
            kind = part.get("kind") if isinstance(part, dict) else None
            if isinstance(kind, str) and kind not in kinds:
                kinds.append(kind)
    labels: list[str] = []
    for kind in kinds:
        try:
            rows = where.route(kind) or []
        except Exception:  # noqa: BLE001 - a registry that cannot say names nothing
            rows = []
        for row in rows:
            if not isinstance(row, dict) or row.get("role") != "live" \
                    or row.get("enabled") is False:
                continue
            label = cards.plain(row.get("label"), 40)
            if label and label not in labels:
                labels.append(label)
    return labels


def _sensitive_note_key(goal: str) -> str:
    """The wording key for a sensitive subject: the health note, or the
    private-subject note when ``subject_note_kind`` says the subject
    is sensitive but not about health (belief, legal, money, grief, sex,
    violence). Without that function, or when it fails, the health note (it
    warns either way)."""
    words = _optional("puffpai.catalogs.personal_words")
    kind_of = getattr(words, "subject_note_kind", None) if words is not None else None
    if callable(kind_of):
        try:
            if kind_of(goal) == "private":
                return "needs_private_note"
        except Exception as exc:  # noqa: BLE001 - the health note still warns
            _log.warning("Could not tell which subject note fits: %s", type(exc).__name__)
    return "needs_health_note"


def _asked_labels(needs: Any, sent: list, labels: list[str]) -> list[str]:
    """*labels* cut to the places the search really sends one of *sent* to
    (``where_to_look.asked_labels``, over the part kinds the plan
    search asks). Without that function in this build, *labels* as they are;
    when it fails, no place (and then nothing is offered to send)."""
    where = _optional("puffpai.catalogs.where_to_look")
    asked = getattr(where, "asked_labels", None) if where is not None else None
    if not callable(asked):
        return labels
    # find_parts searches every part kind a ticked job has (apps and code,
    # books, live info, pictures), so no kind is left out here unless the
    # engine names the ones it searches (merge, milestone F).
    finder = _optional("puffpai.wizard.find_parts")
    kinds = getattr(finder, "SEARCHED_KINDS", None) if finder is not None else None
    try:
        got = asked(needs, sent, kinds=kinds) or []
    except Exception as exc:  # noqa: BLE001 - a check that fails names nothing
        _log.warning("Could not say which places are asked: %s", type(exc).__name__)
        return []
    out: list[str] = []
    for label in got:
        plain = cards.plain(label, 40)
        if plain and plain not in out:
            out.append(plain)
    return out


def _sendables(engine, needs: dict, goal: str) -> list:
    """What would leave this computer, as the engine's ``Sendable`` phrases.
    Anything that goes wrong is "nothing leaves"."""
    try:
        sent = engine.sends(needs, goal=goal) or []
    except Exception as exc:  # noqa: BLE001
        _log.warning("The needs step could not say what it sends: %s",
                     type(exc).__name__)
        return []
    return [s for s in sent if isinstance(getattr(s, "phrase", None), str)
            and s.phrase]


def needs_words(needs: dict, *, goal: str, mode: str, internet: bool,
                engine=None, helper_answer: str = "") -> dict:
    """Everything the job list shows, in both faces' words. Pure but for
    asking the engine which phrases would be sent."""
    from puffpai.wizard import wording

    def say(key: str, **values: Any) -> str:
        return " ".join(wording.say(key, mode, **values).split())

    engine = engine if engine is not None else needs_engine()
    kind = cards.plain(needs.get("kind"), 60)
    helper = needs.get("helper") if needs.get("helper") in (
        "ready", "missing", "declined") else "ready"
    if helper == "missing" and helper_answer in ("declined", "asked"):
        helper = "declined" if helper_answer == "declined" else "ready"
    # Offered once, and only when it could be downloaded.
    offer = helper == "missing" and internet and helper_answer != "asked"
    chips = {"asked": say("needs_chip_asked"),
             "usual": say("needs_chip_usual", kind=kind) if kind else "",
             "owner": say("needs_chip_added")}
    jobs = [{"id": job["id"],
             "name": cards.plain(job.get("name"), 80),
             "does": cards.plain(job.get("does"), 200),
             "source": str(job.get("source") or ""),
             "ticked": bool(job.get("ticked")),
             "chip": chips.get(str(job.get("source") or ""), "")}
            for job in _jobs(needs)[:MAX_JOBS_SHOWN]]
    held = [{"word": cards.plain(row.get("word"), 40),
             "why": str(row.get("why") or ""),
             "allowed": bool(row.get("allowed"))}
            for row in needs.get("held") or () if isinstance(row, dict)
            and isinstance(row.get("word"), str) and row["word"].strip()]
    subject = needs.get("subject") if isinstance(needs.get("subject"), dict) \
        else {}
    notes = [n for n in (subject.get("notes") or ()) if n in ("brand", "health")]
    # Words are shown as "sent" only when there is somewhere outside to ask:
    # with no online place in this build, nothing leaves, and the screen
    # says BuildBot looks only on this computer.
    labels = _source_labels(needs) if internet else []
    sent = _sendables(engine, needs, goal) if labels and engine else []
    if sent:
        # Only the places that will really be sent one of these phrases.
        labels = _asked_labels(needs, sent, labels)
        sent = sent if labels else []
    subject_phrase = next((s.phrase for s in sent if getattr(s, "subject", False)),
                          "")
    phrases = [s.phrase for s in sent if not getattr(s, "subject", False)]
    before: list[str] = []
    if sent:
        if subject_phrase:
            note = (wording.say(_sensitive_note_key(goal), mode) if "health" in notes
                    else wording.say("needs_brand_note", mode)
                    if "brand" in notes else "")
            before.append(say("needs_searching_with", subject=subject_phrase)
                          + note)
        if phrases:
            before.append(say("needs_job_words" if subject_phrase
                              else "needs_only_words",
                              phrases=", ".join(phrases)))
        finder = _optional("puffpai.wizard.find_parts")
        plain = getattr(finder, "plain_places", None) if finder is not None else None
        before.append(say("needs_sources", sources=plain(labels) if callable(plain)
                          else _and_list(labels)))
        before.append(say("needs_nothing_else"))
        buttons = [{"value": "online", "label": say("needs_start")},
                   {"value": "here", "label": say("needs_here")}]
        prompt = say("needs_terminal_prompt")
    elif internet:
        before.append(say("needs_sources_none"))
        buttons = [{"value": "here", "label": say("needs_start")}]
        prompt = say("needs_terminal_prompt_offline")
    else:
        before.append(say("needs_offline"))
        buttons = [{"value": "here", "label": say("needs_start")}]
        prompt = say("needs_terminal_prompt_offline")
    return {
        "kind": kind,
        "title": (say("needs_title", kind=kind) if kind
                  else say("needs_title_unknown")),
        "jobs": jobs,
        "held": held,
        "private_left_out": [cards.plain(w, 40) for w in
                             (needs.get("private_left_out") or ())
                             if isinstance(w, str) and w.strip()],
        "subject": {"phrase": cards.plain(subject.get("phrase"), 60),
                    "notes": notes},
        "sends": ([subject_phrase] if subject_phrase else []) + phrases,
        "helper": helper,
        "helper_offer": say("needs_helper_offer") if offer else "",
        "helper_buttons": ([{"value": "get", "label": say("needs_helper_get")},
                            {"value": "not_now",
                             "label": say("needs_helper_not_now")}]
                           if offer else []),
        "intro": (say("needs_intro", kind=kind) if kind
                  else say("needs_intro_unknown")),
        "held_line": say("needs_held") if held else "",
        "add": say("needs_add"),
        "add_prompt": say("needs_add_prompt"),
        "words_prompt": say("needs_words_prompt"),
        "before_button": before,
        "buttons": buttons,
        "online": bool(sent),
        "prompt": prompt,
    }


#: The needs step's live work, per wizard run (keyed by when the run started).
_NEEDS_LOCK = threading.Lock()
_NEEDS_LIVE: dict[str, dict] = {}


def _run_key(run: wizard_runner.Runner) -> str:
    return f"{float(run.started_at):.6f}"


def _update_run(cfg, args, change: Callable[[wizard_runner.Runner], None],
                wait_s: float = 30.0) -> bool:
    """Load the run record, change it, save it, behind the one-run lock."""
    deadline = time.time() + wait_s
    while True:
        try:
            lock = _take_lock(cfg)
            break
        except RunInProgress:
            if time.time() > deadline:
                return False
            time.sleep(0.2)
    try:
        run = _saved(cfg, args)
        change(run)
        run.save()
        return True
    finally:
        _drop_lock(lock)


def _settle_needs(run: wizard_runner.Runner, outcome: str, note: str) -> None:
    now = time.time()
    run.entries[NEEDS_ENTRY] = {"outcome": outcome, "at": now}
    run.log.append({"entry": NEEDS_ENTRY, "skill": "", "part": "needs",
                    "outcome": outcome, "started_at": now, "finished_at": now,
                    "note": note, "refusal": ""})


def _new_live(progress: NeedsProgress) -> dict:
    return {"phase": "needs", "progress": progress, "error": "",
            "parts_lines": [], "parts_done": False, "thread": None}


def _start_needs(key: str, goal: str, args, cfg, *, reader: Any = None) -> dict:
    """Run the needs engine on a thread; its jobs are drawn as they arrive."""
    engine = needs_engine()
    live = _new_live(NeedsProgress(early_kind(goal)))

    def work() -> None:
        progress = live["progress"]
        try:
            result = engine.run(goal, cfg=cfg, on_job=progress.on_job,
                                deadline_s=NEEDS_DEADLINE_S)
            if not _is_needs(result):
                raise ValueError("the needs engine did not answer needs/v1")
            if reader:
                # The reader's age band: books are offered at her level.
                result["reader"] = str(reader)

            def keep(run: wizard_runner.Runner) -> None:
                run.shared["needs_draft"] = result
            _update_run(cfg, args, keep)
            progress.finish(result)
        except Exception as exc:  # noqa: BLE001 - reported plainly, never raised
            # Only the kind of failure: the goal is never written to a log.
            _log.warning("The needs step stopped: %s", type(exc).__name__)
            # Recorded first, then said: a screen that sees "stopped" can
            # move straight on to the plan.
            _update_run(cfg, args, lambda run: _settle_needs(
                run, "skipped", "the needs step stopped; the plan is drawn "
                                "as before"))
            live["error"] = "stopped"
            progress.done = True

    thread = threading.Thread(target=work, name="wizard-needs", daemon=True)
    live["thread"] = thread
    _NEEDS_LIVE[key] = live
    thread.start()
    return live


def _parts_tail(record: dict, mode: str, *, ticked: Any = None,
                said: Any = ()) -> list[str]:
    """The closing lines of "Looking for things your bot can use", each only
    when it is true and was not already said by the search itself.

    "Stopped looking" is said only for a job the owner kept (a job she
    unticked was never going to be looked for), and "Checking what fits"
    only once."""
    from puffpai.wizard import wording
    said = [" ".join(str(line).split()) for line in said or ()]
    out: list[str] = []
    not_reached = [j for j in record.get("not_reached") or ()
                   if ticked is None or j in ticked]
    stopped = " ".join(wording.say("looking_stopped", mode).split())
    if not_reached and stopped not in said:
        out.append(stopped)
    head = " ".join(wording.say("looking_checked", mode, kept=0,
                                left_out=0).split()).split(" ...")[0]
    # With no job kept there is nothing to fit: what BuildBot's own lists
    # offered is never counted as fitting "your jobs".
    if ticked is not None and not ticked:
        return out
    if not any(line.startswith(head) for line in said):
        kept = sum(1 for item in record.get("items") or ()
                   if (item.get("apply") or {}).get("kind") != "none")
        out.append(" ".join(wording.say(
            "looking_checked", mode, kept=kept,
            left_out=int(record.get("left_out") or 0)).split()))
    return out


_COUNT_LINE = re.compile(r"^(.{1,60}): \d[\d,]* found$")


def _latest_lines(lines: list[str]) -> list[str]:
    """The looking lines as the page shows them: each place's count is one
    line that goes up ("GitHub: 12 found", then "GitHub: 38 found" in its
    place), and "Checking what fits your jobs ..." is followed by its count in
    the same line. Nothing ever reads as going back."""
    out: list[str] = []
    at: dict[str, int] = {}
    for line in lines:
        m = _COUNT_LINE.match(line)
        key = m.group(1) if m else ""
        if key and key in at:
            out[at[key]] = line
            continue
        if out and out[-1].endswith(" ...") and line.startswith(out[-1]):
            out[-1] = line
            continue
        if any(x.endswith(" ...") and line.startswith(x) for x in out):
            k = next(i for i, x in enumerate(out) if x.endswith(" ...") and line.startswith(x))
            out[k] = line
            continue
        if key:
            at[key] = len(out)
        out.append(line)
    return out


def _once(on_line: Callable[[str], None]) -> Callable[[str], None]:
    """*on_line*, saying each line at most once (a searcher that is not in
    this build says so once per build, not once per job)."""
    seen: set[str] = set()

    def say(line: str) -> None:
        key = " ".join(str(line).split())
        if not key or key in seen:
            return
        seen.add(key)
        on_line(line)
    return say


def find_for_needs(needs: dict, *, goal: str, online: bool, allow_send: bool,
                   cfg, mode: str, on_line: Callable[[str], None]) -> dict | None:
    """Run ``find_parts.plan_items`` once and keep what it found for the plan.

    Returns the record the plan is drawn from (also remembered by
    :mod:`puffpai.wizard.proposal`, so drawing and redrawing the plan never
    searches again), or ``None`` when ``find_parts`` is not in this build."""
    from puffpai.wizard import proposal, wording

    engine = parts_engine()
    if engine is None:
        return None
    said: list[str] = []
    once = _once(lambda line: (said.append(line), on_line(line)))
    try:
        result = engine.plan_items(
            needs, goal=goal, online=online, allow_send=allow_send, cfg=cfg,
            on_line=lambda line: once(cards.plain(line, 200)))
    except Exception as exc:  # noqa: BLE001 - an honest line, never a crash
        _log.warning("Looking for parts stopped: %s", type(exc).__name__)
        once(" ".join(wording.say("looking_failed", mode).split()))
        result = {"items": [], "notes": {}, "not_reached": _ticked_ids(needs),
                  "similar": [], "sends": [], "left_out": 0}
    record = proposal.remember_parts(needs, result, online=online,
                                     allow_send=allow_send)
    for line in _parts_tail(record, mode, ticked=set(_ticked_ids(needs)),
                            said=said):
        once(line)
    return record


def _start_parts(live: dict, needs: dict, goal: str, *, online: bool,
                 allow_send: bool, args, cfg, mode: str) -> None:
    live["phase"] = "parts"
    lines: list[str] = live["parts_lines"]

    def work() -> None:
        record = None
        try:
            record = find_for_needs(needs, goal=goal, online=online,
                                    allow_send=allow_send, cfg=cfg, mode=mode,
                                    on_line=lines.append)
        finally:
            def keep(run: wizard_runner.Runner) -> None:
                if record is not None:
                    run.shared["needs_parts"] = dict(record, lines=list(lines))
                count = len((record or {}).get("items") or ())
                _settle_needs(run, "done", f"looked for parts: {count} found")
            _update_run(cfg, args, keep)
            live["parts_done"] = True

    thread = threading.Thread(target=work, name="wizard-parts", daemon=True)
    live["thread"] = thread
    thread.start()


def _needs_view(run: wizard_runner.Runner, live: dict | None, *,
                goal: str) -> dict:
    """The GET /wizard/needs shape, from the live work or the record."""
    from puffpai.wizard import wording

    mode = wording.mode_of(run.shared)
    draft = run.shared.get("needs_draft")
    progress = live["progress"] if live else NeedsProgress.from_result(draft)
    if not _is_needs(draft) and live and _is_needs(progress.result):
        draft = progress.result
    stored_parts = run.shared.get("needs_parts") if isinstance(
        run.shared.get("needs_parts"), dict) else {}
    parts = bool(run.shared.get("needs_look")) or bool(
        live and live.get("phase") == "parts")
    stopped = bool(live and live.get("error")) or (
        _needs_settled(run) and not _is_needs(draft))
    view: dict[str, Any] = {
        "title": progress.title(mode), "lines": progress.lines(mode),
        "jobs": [], "held": [], "private_left_out": [],
        "subject": {"phrase": "", "notes": []}, "sends": [],
        "helper": "ready", "done": bool(progress.done or _is_needs(draft)
                                        or stopped),
        "phase": "parts" if parts else "needs",
        "not_reached": [str(j) for j in stored_parts.get("not_reached") or ()],
    }
    if stopped:
        view["lines"] = view["lines"] + [
            " ".join(wording.say("needs_stopped", mode).split())]
        view["phase"] = "parts"
        view["done"] = True
        return view
    if _is_needs(draft):
        internet = run.shared.get("internet") is True
        view.update(needs_words(draft, goal=goal, mode=mode,
                                internet=internet,
                                helper_answer=str(run.shared.get(
                                    "needs_helper") or "")))
        view["title"] = progress.title(mode)
    if parts:
        view["title"] = wording.say("looking_title", mode)
        if live and live.get("phase") == "parts":
            view["lines"] = _latest_lines(list(live["parts_lines"]))
            view["done"] = bool(live.get("parts_done")) or _needs_settled(run)
        else:
            view["lines"] = _latest_lines([cards.plain(line, 200)
                                           for line in stored_parts.get("lines") or ()
                                           if isinstance(line, str)])
            view["done"] = _needs_settled(run)
    return view


def _needs_run(args, cfg) -> tuple[wizard_runner.Runner, str]:
    from puffpai.wizard import wording
    run = _saved(cfg, args)
    goal = _goal_of(run.shared, run.sections)
    if not goal or not os.path.exists(run.state_path()):
        raise AnswerRefused(wording.say("needs_no_goal"), None)
    return run, goal


def needs(args=None, cfg=None) -> dict:
    """GET /wizard/needs: start finding out what the bot needs (once per
    run), and say how far it has got."""
    from puffpai.wizard import wording
    if needs_engine() is None:
        raise SkillNotConverted(wording.say("needs_not_built"))
    args = args or _args()
    cfg = cfg or _cfg()
    run, goal = _needs_run(args, cfg)
    key = _run_key(run)
    restart = None
    with _NEEDS_LOCK:
        live = _NEEDS_LIVE.get(key)
        if live is None and not _is_needs(run.shared.get("needs_draft")) \
                and not _needs_settled(run):
            live = _start_needs(key, goal, args, cfg,
                                reader=run.shared.get("age_band"))
        elif live is None and run.shared.get("needs_look") \
                and not _needs_settled(run) \
                and _is_needs(run.shared.get("needs")):
            # Looking had started when this backend stopped: start it again
            # with the owner's own answer, rather than wait for nothing.
            live = _new_live(NeedsProgress.from_result(run.shared["needs"]))
            live["phase"] = "parts"
            _NEEDS_LIVE[key] = live
            restart = live
    if restart is not None:
        from puffpai.wizard import wording
        online = run.shared.get("internet") is True
        _start_parts(restart, run.shared["needs"], goal, online=online,
                     allow_send=run.shared.get("needs_look") == "online"
                     and online, args=args, cfg=cfg,
                     mode=wording.mode_of(run.shared))
    return _needs_view(run, live, goal=goal)


def _str_list(value: Any, name: str, most: int, longest: int) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > most or not all(
            isinstance(v, str) and 0 < len(v.strip()) <= longest for v in value):
        raise AnswerRefused(f"'{name}' is a short list of short pieces of "
                            f"text.", name)
    return [" ".join(v.split()) for v in value]


def _clean_needs_body(body: Any) -> dict:
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise AnswerRefused("Send ticked, added, allowed_words and look.", None)
    look = body.get("look")
    if look is not None and look not in LOOK_CHOICES:
        raise AnswerRefused("'look' is online or here.", "look")
    ticked = body.get("ticked")
    return {
        "ticked": None if ticked is None else _str_list(ticked, "ticked",
                                                        MAX_TICKED, 24),
        "added": _str_list(body.get("added"), "added", MAX_ADDED,
                           MAX_ADDED_CHARS),
        "allowed_words": _str_list(body.get("allowed_words"), "allowed_words",
                                   MAX_ALLOWED, 40),
        "look": look,
        "get_helper": body.get("get_helper") is True,
        # "Not now" is an explicit false; leaving it out says nothing.
        "helper_no": body.get("get_helper") is False,
    }


def choose_needs(engine, draft: dict, *, ticked: list[str] | None,
                 added: list[str], allowed_words: list[str]) -> dict:
    """The owner's ticks, additions and allowed words, through the engine.
    Only this run's own job ids and held words are accepted."""
    known = {job["id"] for job in _jobs(draft)}
    if ticked is not None and any(t not in known for t in ticked):
        raise AnswerRefused("That is not one of the jobs on the list.",
                            "ticked")
    held = {str(row.get("word") or "").lower() for row in draft.get("held") or ()
            if isinstance(row, dict)}
    if any(word.lower() not in held for word in allowed_words):
        raise AnswerRefused("Only a word on the list can be allowed.",
                            "allowed_words")
    wanted = _ticked_ids(draft) if ticked is None else list(ticked)
    if set(wanted) == set(_ticked_ids(draft)) and not added \
            and not allowed_words:
        return draft
    chosen = engine.choose(draft, ticked=wanted, added=list(added),
                           allowed_words=list(allowed_words))
    if not _is_needs(chosen):
        raise AnswerRefused("That change could not be used. Nothing was "
                            "changed.", None)
    return chosen


def get_helper(device_id: str, mode: str,
               progress: Callable[[str], None] | None = None) -> dict:
    """Get BuildBot's reading helper, after the owner's "Get it": a consent
    ticket for this device, then the engine's download.
    ``{"state": "ready"|"failed", "line": str}``; never raises."""
    from puffpai.wizard import wording
    engine = needs_engine()
    fetch = getattr(engine, "get_helper", None) if engine is not None else None
    if fetch is None:
        return {"state": "failed",
                "line": " ".join(wording.say("needs_helper_later", mode).split())}
    try:
        from puffpai.security import consent_tickets
        ticket = consent_tickets.mint(
            "add_on", "reading_helper",
            wording.say("needs_helper_sentence", mode), device_id)
        got = fetch(ticket=ticket, device_id=device_id,
                    progress=progress or (lambda _line: None))
    except Exception as exc:  # noqa: BLE001 - an honest line, never a crash
        _log.warning("The reading helper was not got: %s", type(exc).__name__)
        got = None
    if not isinstance(got, dict) or got.get("state") not in ("ready", "failed"):
        return {"state": "failed",
                "line": " ".join(wording.say("needs_helper_failed", mode).split())}
    return {"state": got["state"], "line": cards.plain(got.get("line"), 200)}


def _add_similar_after_looking(run: wizard_runner.Runner, added: list[str],
                               engine, mode: str) -> None:
    """The plan's "Similar projects also have": each ticked line becomes a
    job, its finds move under it; nothing is searched again."""
    from puffpai.wizard import proposal
    for name in added:
        try:
            proposal.add_similar(run.shared, name, engine=engine)
        except proposal.SimilarRefused as exc:
            raise AnswerRefused(str(exc), "added") from None


def needs_choose(body: Any, args=None, cfg=None, *,
                 device_id: str | None = None) -> dict:
    """POST /wizard/needs: ticks, additions, allowed words; ``look`` starts
    looking (``online`` sends only the phrases shown, ``here`` sends
    nothing). ``get_helper`` true gets the reading helper for *device_id*
    (the paired device asking), false is "Not now". After looking, ``added``
    names a "Similar projects also have" line of the plan."""
    from puffpai.wizard import wording
    engine = needs_engine()
    if engine is None:
        raise SkillNotConverted(wording.say("needs_not_built"))
    clean = _clean_needs_body(body)
    args = args or _args()
    cfg = cfg or _cfg()
    helper_line = ""
    if clean["get_helper"]:
        run = _saved(cfg, args)
        mode = wording.mode_of(run.shared)
        if not device_id:
            raise AnswerRefused(wording.say("needs_helper_failed", mode),
                                "get_helper")
        got = get_helper(device_id, mode)
        helper_line = got["line"]
        _update_run(cfg, args, lambda r: r.shared.update(
            needs_helper="asked" if got["state"] == "ready" else "failed"))
    elif clean["helper_no"]:
        _update_run(cfg, args, lambda r: r.shared.update(
            needs_helper="declined"))
    lock = _take_lock(cfg)
    try:
        run, goal = _needs_run(args, cfg)
        mode = wording.mode_of(run.shared)
        key = _run_key(run)
        live = _NEEDS_LIVE.get(key)
        looking = bool(run.shared.get("needs_look") or _needs_settled(run) or (
            live and live.get("phase") == "parts"))
        only = (clean["ticked"] is None and not clean["allowed_words"]
                and not clean["look"])
        if looking and only and clean["added"]:
            _add_similar_after_looking(run, clean["added"], engine, mode)
            run.save()
            view = _needs_view(run, live, goal=goal)
            return view
        if looking and only and not clean["added"] and (
                clean["get_helper"] or clean["helper_no"]):
            view = _needs_view(run, live, goal=goal)
            if helper_line:
                view["helper_line"] = helper_line
            return view
        if looking:
            raise AnswerRefused(wording.say("needs_already_looking", mode),
                                None)
        draft = run.shared.get("needs_draft")
        if not _is_needs(draft):
            raise RunInProgress(" ".join(wording.say("needs_wait",
                                                     mode).split()))
        draft = choose_needs(engine, draft, ticked=clean["ticked"],
                             added=clean["added"],
                             allowed_words=clean["allowed_words"])
        run.shared["needs_draft"] = draft
        start = None
        if clean["look"]:
            online = run.shared.get("internet") is True
            allow_send = clean["look"] == "online" and online
            run.shared["needs"] = draft
            run.shared["needs_look"] = "online" if allow_send else "here"
            run.shared["needs_look_at"] = time.time()  # plan_outside
            if live is None:
                live = _new_live(NeedsProgress.from_result(draft))
                _NEEDS_LIVE[key] = live
            live["phase"] = "parts"
            start = (online, allow_send)
        run.save()
    finally:
        _drop_lock(lock)
    if start is not None:
        _start_parts(live, draft, goal, online=start[0], allow_send=start[1],
                     args=args, cfg=cfg, mode=mode)
    view = _needs_view(_saved(cfg, args), live, goal=goal)
    if helper_line:
        view["helper_line"] = helper_line
    return view


# -- the terminal: the same screen, drawn by render_cli ------------------------

def run_needs_console(ctx) -> dict | None:
    """The runner's ``needs`` entry on the terminal (``puffpai setup``).

    ``None`` (nothing said, nothing changed) when this build has no needs
    engine or no goal; the plan is then drawn as before. A run nobody is
    watching (``--yes``, or a face that is not this console) keeps the
    engine's list and looks only on this computer: nothing leaves without a
    person pressing Enter under the "Searching with" lines."""
    from puffpai.wizard import render_cli, wording

    engine = needs_engine()
    goal = _goal_of(ctx.shared, getattr(ctx, "_sections", None))
    if engine is None or not goal:
        return None
    mode = wording.mode_of(ctx.shared)
    console = bool(getattr(ctx, "console", False))
    out = print if console else (lambda _line: None)
    watched = console and not ctx.non_interactive

    progress = NeedsProgress(early_kind(goal))
    shown: list[str] = []

    def show() -> None:
        for line in progress.lines(mode, final_only=True)[len(shown):]:
            shown.append(line)
            for part in render_cli.wrap(line):
                out(part)

    out("")
    out(render_cli.ascii_only(progress.title(mode)))
    try:
        result = engine.run(goal, cfg=ctx.cfg,
                            on_job=lambda job: (progress.on_job(job), show()),
                            deadline_s=NEEDS_DEADLINE_S)
        if not _is_needs(result):
            raise ValueError("the needs engine did not answer needs/v1")
        if ctx.shared.get("age_band"):
            # The reader's age band: books are offered at her level.
            result["reader"] = str(ctx.shared["age_band"])
    except Exception as exc:  # noqa: BLE001 - the plan is drawn as before
        _log.warning("The needs step stopped: %s", type(exc).__name__)
        for part in render_cli.wrap(wording.say("needs_stopped", mode)):
            out(part)
        return {"status": "skipped",
                "note": "the needs step stopped; the plan is drawn as before"}
    progress.finish(result)
    show()

    draft = result
    online = ctx.shared.get("internet") is True
    look = "here"
    helper_answer = ""
    if watched:
        words = needs_words(draft, goal=goal, mode=mode, internet=online,
                            engine=engine)
        wanted = render_cli.helper_screen(words, out=out, mode=mode)
        if wanted:
            from puffpai.security import consent_tickets
            got = get_helper(consent_tickets.terminal_device_id(), mode,
                             progress=lambda line: [out(p) for p in
                                                    render_cli.wrap(line, 2)])
            for part in render_cli.wrap(got["line"]):
                out(part)
            helper_answer = "asked" if got["state"] == "ready" else "failed"
        elif wanted is False:
            helper_answer = "declined"
    while watched:
        words = needs_words(draft, goal=goal, mode=mode, internet=online,
                            engine=engine, helper_answer=helper_answer)
        command = render_cli.needs_screen(words, out=out)
        if command[0] == "look":
            look = command[1] if online else "here"
            break
        try:
            if command[0] == "toggle":
                flipped = set(_ticked_ids(draft)) ^ set(command[1])
                draft = choose_needs(engine, draft,
                                     ticked=[j["id"] for j in _jobs(draft)
                                             if j["id"] in flipped],
                                     added=[], allowed_words=[])
            elif command[0] == "add":
                draft = choose_needs(engine, draft, ticked=None,
                                     added=[command[1]], allowed_words=[])
            elif command[0] == "allow":
                draft = choose_needs(engine, draft, ticked=None, added=[],
                                     allowed_words=[command[1]])
        except AnswerRefused as exc:
            render_cli.say_line(str(exc), out=out)

    allow_send = look == "online" and online
    out("")
    out(render_cli.ascii_only(wording.say("looking_title", mode)))
    lines: list[str] = []

    def said(line: str) -> None:
        lines.append(line)
        for part in render_cli.wrap(line):
            out(part)

    look_at = time.time()  # what was sent is counted from here
    record = find_for_needs(draft, goal=goal, online=online,
                            allow_send=allow_send, cfg=ctx.cfg, mode=mode,
                            on_line=said)
    shared = {"needs": draft, "needs_draft": draft,
              "needs_look": "online" if allow_send else "here",
              "needs_look_at": look_at}
    if record is not None:
        shared["needs_parts"] = dict(record, lines=lines)
    return {"shared": shared,
            "note": f"{len(_ticked_ids(draft))} jobs kept; "
                    f"{len((record or {}).get('items') or ())} things found"}


__all__ = ["AnswerRefused", "FLOW_SCHEMA", "LOCK_NAME", "RunInProgress",
           "SUMMARY_ENTRY", "SkillNotConverted", "apply_skill", "approve",
           "asks_for_secret", "first_look", "first_look_choose", "flow", "github_choose", "toolsmith_choose", "has_lifecycle", "parts_of",
           "propose_cards", "propose_one", "rest_status", "resume_choice",
           "resume_offer", "run_rest", "run_state", "skip_vault",
           "start_rest", "summary", "NEEDS_ENTRY", "build_lines", "needs",
           "needs_choose", "needs_engine", "needs_words", "parts_engine",
           "run_needs_console"]
