"""The runner: fixed order, one writer per section, an honest log, and resume.

Deterministic. No model anywhere in it. It owns the order, the log, the manifest
write and the refusals; a package decides one thing and hands back data.

**Two orders, and they are not the same thing.** A fixed list sets the *skill*
order (Discover, Purpose, Brain, Sources, Tools, Integrate, Context, Agents,
Secure, Surfaces, Prove, Handoff), which is the order the log and the review
screen are in. What the runner *executes* is a schedule, and a schedule is not
the skill order, because a skill can own more than one question and because a
section has to be written before the skill that reads it runs:

* :data:`SKILL_ORDER` -- the twelve, in skill order. The order of the log, the
  run report and the review screen's rows.
* :data:`SCHEDULE` -- the entries actually executed, in this order: goal, name
  and age first; this computer checked quietly; ONE
  summary with one "Looks good"; the vault; then the building. Each entry is
  attributed to the skill that owns it, and each says whether the owner sees
  it (``ask``) or it answers from the approved summary (``quiet``).

Why the schedule is not simply ``SKILL_ORDER``: Context reads ``surfaces`` and
``knowledge``, and both are written by skills that come *after* Context in the
spec's order, and the summary has to be shown before anything it lists is
applied. ``tests/test_wizard_golden.py`` pins what the schedule writes: a
checked-in manifest per scripted run, so a change to any section's contents is
a test failure and not a surprise in somebody's build.

Each entry names a *part*. ``card`` means "run the propose -> card -> apply ->
verify lifecycle"; ``card:<name>`` means the same for a package that asks more
than one question, and the name reaches the package as ``ctx.part``. Anything
else is a Phase 1 ``part_*`` wrapper, kept only where a package has no
lifecycle (Prove, which asks the owner nothing).

What the runner enforces at run time:

* **One writer per section.** An entry's result may only land in the section its
  package declared in ``writes``. Anything else is refused, logged, and dropped
  -- not merged "just this once".
* **Only Brain and Secure may stop the build.** Any other failure records a gap
  and the wizard carries on. A package raising never takes down the wizard.
* **A deliberate refusal is still a refusal.** ``cli.SetupRefused`` propagates
  from any skill: it is the wizard telling the owner something on purpose, and
  swallowing it into a gap would change what the owner sees.
* **No secret is written to the run state.** The state file is scrubbed of
  every key :mod:`puffpai.build_manifest` calls a secret before it is stored.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Callable

from puffpai.wizard import cards, fixes, loader, stale
from puffpai.wizard.manifest import PackageError

STATE_SCHEMA = "wizard_run/v1"

_log = logging.getLogger(__name__)

#: Ceilings on what a run record may contain when it is read back. A record
#: bigger than this is not one of ours, and reading it would be trusting a file
#: to say how much memory to spend on it.
MAX_STATE_BYTES = 8 * 1024 * 1024
MAX_STATE_ENTRIES = 500
MAX_STATE_LOG = 2000
MAX_STATE_SECTIONS = 40
MAX_STATE_SHARED = 200

#: How old a stopped run may be and still be worth continuing. A week: long
#: enough to cover "I will finish this at the weekend", short enough that the
#: machine the record describes is still that machine.
RESUME_MAX_AGE_S = 7 * 24 * 60 * 60

#: Why a record on disk is not a run to continue. Each is a different sentence
#: to the owner, which is why they are not one boolean.
RESUME_UNREADABLE = "unreadable"
RESUME_NOTHING_DONE = "nothing_done"
RESUME_FINISHED = "finished"
RESUME_TOO_OLD = "too_old"

#: The skills, in their fixed order, with Assemble
#: between Surfaces and Prove. ``crew`` is the Agents skill:
#: ``agents`` is in ``persona_scaffold.RESERVED_IDS``, so it cannot be a package
#: id, and the spec's own cards and review screen already call it ``crew``.
SKILL_ORDER: tuple[str, ...] = (
    "discover", "purpose", "brain", "sources", "tools", "integrate",
    "context", "crew", "secure", "surfaces", "assemble", "prove", "handoff",
)

#: The part name that means "this package is converted": the runner runs the
#: Phase 2 lifecycle (propose -> card -> apply -> verify) instead of calling a
#: ``part_*`` wrapper. A package that still only has ``part_*`` functions keeps
#: working untouched -- mixed mode is what lets the wizard work at every commit
#: while the twelve are converted one at a time.
V2_PART = "card"


def lifecycle_part(part: str) -> str | None:
    """``"card"`` -> ``""``, ``"card:vault"`` -> ``"vault"``, else ``None``.

    One place decides whether an entry is a lifecycle entry, so the runner, the
    service and the tests cannot disagree about what ``card:vault`` means.
    """
    head, _, name = str(part or "").partition(":")
    return name if head == V2_PART else None

#: Outcomes a step can end in. ``not_built`` is the honest one: the package is
#: registered, it ran, and it has nothing to do yet in this phase.
OUTCOMES = ("done", "skipped", "failed", "not_built", "refused")
_TERMINAL = ("done", "skipped", "not_built")


#: How a card reaches the owner. ``ask`` draws it and waits for answers;
#: ``quiet`` never draws it: its answers come from the approved summary
#: (:func:`puffpai.wizard.proposal.answers_for_card`), so a card the owner no
#: longer sees still applies exactly what the summary said and nothing more.
FACE_ASK = "ask"
FACE_QUIET = "quiet"


@dataclass(frozen=True)
class Step:
    """One entry in the schedule: a part of a skill, and how it is shown."""

    entry: str
    skill: str          # "" means the runner itself owns this entry
    part: str
    requires: tuple[str, ...] = ()
    face: str = FACE_ASK
    #: A wording key the console prints before the step runs ("Checking this
    #: computer..."). Empty for none.
    say: str = ""


#: The wizard, entry by entry, in order: what this computer has and the thinking model that fits it, then
#: the goal, then a name, then an age band that only changes the wording;
#: then ONE summary and one "Looks good"; then the
#: vault PIN while the owner is still at the keyboard, and only then the long
#: downloads. The cards between are ``quiet``: they still propose, apply and
#: verify, and their answers come from the approved summary.
#:
#: ``requires`` names keys in the run's shared notebook, and each one is written
#: by an entry above it: ``interview`` by ``purpose``, ``census``/``ollama_url``
#: by ``discover``, ``posture``/``gates`` by ``secure.posture``,
#: ``chosen_model`` by ``brain``. A missing key is a skip with a gap, never a
#: step working from nothing.
SCHEDULE: tuple[Step, ...] = (
    # The census, the Ollama survey and whether the internet answers.
    # Measured, not asked, and shown first so what this computer has and the
    # thinking model that fits it are explicit.
    Step("discover.card", "discover", V2_PART, face=FACE_QUIET, say="checking"),
    Step("runner.first_look", "", "first_look", ("census",)),
    # The goal is the first question, and the name is suggested from it,
    # which is why they are two cards.
    Step("purpose.goal", "purpose", f"{V2_PART}:goal"),
    Step("purpose.identity", "purpose", f"{V2_PART}:identity"),
    # "Finding out what a <kind> bot needs", between the name and the plan. The runner's own entry, like the first screen: it
    # writes no section, only the jobs the owner kept (``shared["needs"]``)
    # and what was found for them. A build without the needs engine finishes
    # it at once, saying nothing, and the plan is drawn as before.
    Step("runner.needs", "", "needs", ("interview",)),
    # The one summary. The runner's own entry: it writes no section, only the
    # owner's decision into the shared notebook.
    Step("runner.summary", "", "summary", ("census", "interview")),
    Step("secure.posture", "secure", f"{V2_PART}:posture", face=FACE_QUIET),
    # The vault straight after "Looks good": the owner is at the keyboard now,
    # and the downloads below can run without them.
    Step("secure.vault", "secure", f"{V2_PART}:vault"),
    Step("sources.card", "sources", V2_PART, ("posture", "gates", "interview"),
         face=FACE_QUIET, say="building"),
    # Quiet unless the hub is on the plan: its address and key are typed here,
    # before the long downloads, for the same reason as the vault.
    Step("tools.card", "tools", V2_PART, ("gates", "posture"), face=FACE_QUIET),
    Step("brain.card", "brain", V2_PART, ("census", "ollama_url", "gates"),
         face=FACE_QUIET),
    Step("crew.card", "crew", V2_PART, ("census",), face=FACE_QUIET),
    Step("purpose.place", "purpose", f"{V2_PART}:place", ("census",),
         face=FACE_QUIET),
    Step("surfaces.card", "surfaces", V2_PART, ("interview",), face=FACE_QUIET),
    Step("runner.persist_config", "", "persist_config", ("chosen_model", "ollama_url")),
    Step("integrate.card", "integrate", V2_PART, ("posture", "gates", "interview"),
         face=FACE_QUIET),
    Step("secure.bridge", "secure", f"{V2_PART}:bridge", ("posture",),
         face=FACE_QUIET),
    Step("runner.wrote_paths", "", "wrote_paths"),
    # No ``brain.ready``: the Brain card's own apply pulls the model and its
    # verify asks the Ollama whether it is there.
    Step("runner.manifest", "", "manifest", ("census",)),
    # What "Looks good" said yes to and no card carries out: books from the
    # libraries, daily updates, the look. No live cards: what the bot reads
    # from outside services is an ingredient of a job in the build plan
    # below, made only after the owner's one Yes. After the record is
    # written, so the build's own answers (online while building, the
    # brain) are known.
    Step("runner.factory", "", "factory"),
    # Context after the manifest, not before it: its verify reads the goal and
    # the name **off disk** (``build_context.collect_goal``).
    Step("context.card", "context", V2_PART, face=FACE_QUIET),
    # The one build plan: ``build.pipeline.plan(goal, ctx)``
    # works out the jobs, finds and checks what fits each, and saves one
    # setup list (``<state>/build/build.json``). Nothing is made here; the
    # handoff hands over to that list and its one Yes on the dashboard. Asks
    # nothing, so no card, like Prove.
    Step("assemble.plan", "assemble", "plan"),
    # Prove asks the owner nothing, so it has no card and keeps its wrapper.
    Step("prove.check", "prove", "check"),
    Step("handoff.card", "handoff", V2_PART),
)


def asked_card_ids(schedule: tuple[Step, ...] = SCHEDULE) -> list[str]:
    """The cards the owner is asked, in order. The browser draws exactly these."""
    return [card_id_of(step) for step in schedule
            if step.face == FACE_ASK and card_id_of(step)]


def _secret_keys() -> frozenset:
    """Key names that never reach the run state. Falls back to a wider set."""
    try:
        from puffpai.build_manifest import _SECRET_KEYS
        return frozenset(_SECRET_KEYS)
    except Exception:  # pragma: no cover - defensive
        return frozenset({"pin", "pin_hash", "vault_pin", "vault_pin_hash",
                          "recovery_code", "vault_recovery_code",
                          "vault_recovery_hash", "ha_token", "token",
                          "password", "secret", "api_key"})


def json_safe(value: Any, *, redact: tuple[str, ...] = (), _depth: int = 0) -> Any:
    """A JSON-storable copy with secrets dropped, by key name and by value.

    Anything that is not plain JSON data is dropped rather than repr'd: the run
    state is a record, not a pickle, and a repr of a live object is how a token
    ends up in a log file.

    Two filters, and the difference matters:

    * **By key name** -- every key :mod:`puffpai.build_manifest` calls a secret
      (``pin``, ``recovery_code``, ``ha_token`` and the rest) is dropped at any
      depth. This is the main one and it is where the vault block's own fields
      are caught.
    * **By value** -- any string holding one of *redact* is dropped whatever it
      is called. The runner passes the few secrets it actually has in hand (the
      PIN typed on the command line, this install's API key), so a value that
      was copied into an innocently-named field does not survive either.

    **The limit, stated plainly:** a secret this function has never been told
    about, stored under a name nobody listed, is written to the record. The
    defence against that is that packages hand back decisions, not credentials
    -- v1 refuses a package that declares ``secrets`` at all -- not that this
    scrub is exhaustive.
    """
    if _depth > 12:
        return None
    if isinstance(value, str):
        if any(token and token in value for token in redact):
            return None
        return value
    if value is None or isinstance(value, (int, float, bool)):
        return value
    secrets = _secret_keys()
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str) or key in secrets:
                continue
            out[key] = json_safe(item, redact=redact, _depth=_depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [json_safe(item, redact=redact, _depth=_depth + 1)
                for item in value]
    return None


class RunContext:
    """What a package part is handed: the run's inputs and a filtered view.

    ``read`` refuses a section the package did not declare in ``reads``. The
    point is not that a built-in could not reach around it -- it runs in-process
    and obviously could -- but that the declaration is the thing reviewed before
    a package ships, so it has to be the thing the code actually uses.
    """

    def __init__(self, *, args, cfg, non_interactive: bool,
                 shared: dict[str, Any], sections: dict[str, dict],
                 package: loader.Package | None, part: str = "",
                 show_once: Any = None, remote: bool = False,
                 console: bool = False):
        #: True when this console is drawing the wizard (no renderer was
        #: handed in). The runner's own summary step only reads the keyboard
        #: then; a test or a browser drives the summary some other way.
        self.console = bool(console)
        #: True when a browser is drawing these cards and answers come back in
        #: a later request. ``non_interactive`` alone could not tell that apart
        #: from ``--yes`` with nobody at the keyboard, so a skill that may ask a
        #: question of a person but not of a script had no way to know which it
        #: was talking to. It is **not** permission to show a secret once: that
        #: is ``face()``, and an HTTP run has none.
        self.remote = bool(remote)
        self.args = args
        self.cfg = cfg
        self.non_interactive = non_interactive
        self.shared = shared
        self._sections = sections
        self.package = package
        #: Which of a multi-question package's parts this call is about
        #: (``card:vault`` -> ``"vault"``). Empty for a package with one card.
        self.part = part
        #: The face that may show the owner a value exactly once -- a recovery
        #: code, a pairing code -- and keep no copy of it. **Per run, never
        #: process-global**: a run whose cards are drawn somewhere this process
        #: cannot see (a browser, over HTTP) carries ``None`` here, so a skill
        #: asking "can anyone read this?" gets a truthful no and declines to
        #: create a secret nobody would ever see. The previous design registered
        #: a console face for the rest of the process, which meant a server that
        #: had once rendered a card on a terminal would answer yes.
        self.show_once = show_once
        #: What ``propose`` worked out, handed to ``apply`` and ``verify`` by
        #: the runner. The lifecycle is four separable calls (spec A.3), so the
        #: thing they share travels on the context rather than in the package.
        self.proposal: dict[str, Any] = {}

    def read(self, section: str) -> dict:
        declared = self.package.reads() if self.package else ()
        if self.package is not None and section not in declared:
            raise PackageError(
                f"Package '{self.package.id}' read the '{section}' section "
                f"without declaring it. A package sees what its manifest says "
                f"it sees.")
        return dict(self._sections.get(section) or {})

    def face(self) -> Any:
        """The show-once face for this run, or ``None``. Fail closed.

        A face is anything with ``show(lines)`` and ``ask(prompt)``. Anything
        else -- including no face at all, which is what every HTTP request
        carries -- is nobody, and a skill that cannot show a value once must
        not create one.
        """
        sink = self.show_once
        if sink is None:
            return None
        if not all(callable(getattr(sink, name, None))
                   for name in ("show", "ask")):
            return None
        return sink

    def own(self) -> dict:
        """This package's own section, live. The one dict it may change."""
        if self.package is None:
            return {}
        return self._sections.setdefault(self.package.writes, {})

    def applied(self, *path: str, default: Any = None) -> Any:
        """What this skill has already written, read-only. The propose rule.

        **One rule, one implementation (re-proposing).**
        A ``propose`` starts from its own applied section when there is one and
        falls back to flags and detection only when there is not, so a card
        re-drawn after an apply shows what the owner actually answered rather
        than the first guess this build made. ``current`` is the applied value;
        ``proposed`` stays the skill's fresh recommendation.

        Unlike :meth:`own` this never creates the section -- proposing writes
        nothing, and a section that exists because somebody looked at it is a
        section the manifest would then carry.
        """
        if self.package is None:
            return default if path else {}
        section = self._sections.get(self.package.writes)
        if not isinstance(section, dict):
            return default if path else {}
        if not path:
            return dict(section)
        value: Any = section
        for key in path:
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]
        return default if value is None else value


@dataclass
class StepRecord:
    entry: str
    skill: str
    part: str
    outcome: str
    started_at: float
    finished_at: float
    note: str = ""
    refusal: str = ""
    #: What the owner was shown, what the owner changed, and what
    #: verify observed. ``changed`` is the redacted copy -- a secret_entry
    #: answer is recorded as having been entered, never as its value.
    shown: dict | None = None
    changed: dict | None = None
    verified: dict | None = None

    def as_dict(self) -> dict:
        row = {"entry": self.entry, "skill": self.skill, "part": self.part,
               "outcome": self.outcome, "started_at": self.started_at,
               "finished_at": self.finished_at, "note": self.note,
               "refusal": self.refusal}
        if self.shown is not None:
            row["shown"] = self.shown
        if self.changed is not None:
            row["changed"] = self.changed
        if self.verified is not None:
            row["verified"] = self.verified
        return row


class Runner:
    """Walks the schedule, writes the state after every entry, never crashes.

    *runner_parts* is how the CLI hands in the two inline pieces of today's
    ``_run_setup`` and the manifest write; tests substitute their own.
    """

    def __init__(self, *, args, cfg, non_interactive: bool,
                 packages: dict[str, loader.Package] | None = None,
                 refusals: list[str] | None = None,
                 schedule: tuple[Step, ...] = SCHEDULE,
                 runner_parts: dict[str, Callable[[RunContext], Any]] | None = None,
                 state_path: str | None = None,
                 renderer: Callable[..., dict] | None = None,
                 show_once: Any = None, remote: bool = False):
        loaded, load_refusals = ((packages, list(refusals or []))
                                 if packages is not None
                                 else loader.load_builtin_packages())
        self.packages = loaded
        self.schedule = schedule
        self.runner_parts = dict(runner_parts or {})
        self.args = args
        self.cfg = cfg
        self.non_interactive = non_interactive
        self.shared: dict[str, Any] = {}
        self.sections: dict[str, dict] = {}
        self.entries: dict[str, dict] = {}
        self.log: list[dict] = []
        self.gaps: list[dict] = []
        self.refusals: list[str] = list(load_refusals)
        self.started_at = time.time()
        self._state_path = state_path
        #: How a card reaches the owner. The console renderer by default; the
        #: API hands in one that accepts the proposal and returns the answers
        #: the browser sent. The runner itself draws nothing.
        self.renderer = renderer
        self._show_once = show_once
        #: See :class:`RunContext`: a browser is a face that can be asked a
        #: question, and is still not a face a value may be shown once on.
        self.remote = bool(remote)
        #: Skills a change made stale, newest last (spec B.4).
        self.stale: list[str] = []

    def show_once(self) -> Any:
        """The face this run may show a value once through, or ``None``.

        Fail closed, and the rule is one line: **a run that draws its cards
        somewhere other than this console has no face unless one was handed
        in.** The console renderer is the default renderer, so ``puffpai setup``
        gets one; :mod:`puffpai.wizard.service` always passes a renderer of its
        own, so an HTTP request never does. Nothing here is global, so nothing
        can be inherited by a later run or a later request.
        """
        if self._show_once is not None:
            return self._show_once
        if self.renderer is None:
            from puffpai.wizard import render_cli
            self._show_once = render_cli.ConsoleShowOnce()
            return self._show_once
        return None

    # -- state ------------------------------------------------------------

    def state_path(self) -> str:
        if self._state_path:
            return self._state_path
        config_dir = getattr(self.cfg, "CONFIG_DIR", None) or os.getcwd()
        return os.path.join(config_dir, "wizard_run.json")

    def redactions(self) -> tuple[str, ...]:
        """The secret values this run actually holds, for the value-level scrub.

        Only what is genuinely in hand: a PIN passed on the command line, and
        this install's API key if it can be read without asking for it. Short
        values are left out -- a six-character floor keeps a common word from
        blanking half the record.
        """
        candidates: list[str] = []
        for name in ("vault_pin", "recovery_code", "api_key"):
            value = getattr(self.args, name, None)
            if isinstance(value, str):
                candidates.append(value)
        try:
            from puffpai import config as _config
            key = getattr(_config, "get_api_key", None)
            if callable(key):
                candidates.append(str(key() or ""))
        except Exception:
            pass
        return tuple(dict.fromkeys(v for v in candidates if len(v) >= 6))

    def state(self) -> dict:
        redact = self.redactions()
        return {
            "schema": STATE_SCHEMA,
            "started_at": self.started_at,
            "updated_at": time.time(),
            "build_network": self.shared.get("build_network", ""),
            "skills": self.skill_status(),
            "entries": self.entries,
            "log": self.log,
            "gaps": json_safe(self.gaps, redact=redact),
            "stale": list(self.stale),
            "refusals": self.refusals,
            "sections": json_safe(self.sections, redact=redact),
            "shared": json_safe(self.shared, redact=redact),
        }

    def _save(self) -> None:
        """Write the state atomically. A failure here never stops the wizard."""
        path = self.state_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            handle = tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", delete=False,
                dir=os.path.dirname(path), prefix=".wizard_run-", suffix=".tmp")
            try:
                # Not sort_keys: the skills block is written in the spec's
                # fixed order and that order is part of what it says.
                json.dump(self.state(), handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                handle.close()
            try:
                os.chmod(handle.name, 0o600)
            except OSError:
                pass
            os.replace(handle.name, path)
        except Exception:
            return

    def load_state(self) -> bool:
        """Read a stopped run's record back in. False when it is not usable.

        Wired to the resume screen (:func:`find_stopped_run`). Loading a record
        is **not** the same as trusting it: what a resumed run may skip is
        decided afterwards by :meth:`reverify_blocking`, which observes the
        machine rather than reading this file again.

        Everything in the file is treated as data from somewhere else. The file
        is the wizard's own, but a file on disk is a file anything with the
        owner's account could have written, so every field is type-checked, the
        containers are capped, and one wrong shape returns False rather than
        half a run. Stored gaps are re-rendered through the fix registry and
        dropped if they do not validate: a gap is a line this build prints, and
        a line this build prints is never read off disk.
        """
        path = self.state_path()
        try:
            if os.path.getsize(path) > MAX_STATE_BYTES:
                return self._unusable_state("it is larger than a run record")
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return False
        if not isinstance(data, dict) or data.get("schema") != STATE_SCHEMA:
            return False

        started_at = data.get("started_at")
        if isinstance(started_at, bool) or not isinstance(started_at, (int, float)):
            return self._unusable_state("it does not say when it started")

        entries = data.get("entries") or {}
        log = data.get("log") or []
        gaps = data.get("gaps") or []
        sections = data.get("sections") or {}
        shared = data.get("shared") or {}
        if not isinstance(entries, dict):
            return self._unusable_state("its step record is not a record")
        if not isinstance(log, list):
            return self._unusable_state("its log is not a log")
        if not isinstance(gaps, list):
            return self._unusable_state("its gap list is not a list")
        if not isinstance(sections, dict) or not isinstance(shared, dict):
            return self._unusable_state("its build record is the wrong shape")
        if (len(entries) > MAX_STATE_ENTRIES or len(log) > MAX_STATE_LOG
                or len(sections) > MAX_STATE_SECTIONS
                or len(shared) > MAX_STATE_SHARED):
            return self._unusable_state("it holds more than a run could")

        clean_entries: dict[str, dict] = {}
        for key, value in entries.items():
            if (isinstance(key, str) and isinstance(value, dict)
                    and isinstance(value.get("outcome"), str)):
                clean_entries[key] = {"outcome": value["outcome"],
                                      "at": value.get("at")}
        clean_sections = {}
        for key, value in sections.items():
            if isinstance(key, str) and isinstance(value, dict):
                clean_sections[key] = dict(value)
        handoff = clean_sections.get("handoff")
        if isinstance(handoff, dict) and "gaps" in handoff:
            handoff["gaps"] = fixes.safe_gaps(handoff.get("gaps"),
                                              drop_invalid=True)

        self.started_at = float(started_at)
        self.entries = clean_entries
        self.log = [row for row in log if isinstance(row, dict)]
        self.gaps = fixes.safe_gaps(gaps, drop_invalid=True)
        self.stale = [name for name in (data.get("stale") or [])
                      if isinstance(name, str) and name in SKILL_ORDER]
        self.sections = clean_sections
        self.shared = {k: v for k, v in shared.items() if isinstance(k, str)}
        return True

    def _unusable_state(self, why: str) -> bool:
        """Say once, in the log, that a run record was not usable. Never why it
        might have been tampered with -- that is a guess, and the honest
        statement is that this build will not read it."""
        _log.warning("The run record at %s was not read back: %s.",
                     self.state_path(), why)
        return False

    # -- reporting --------------------------------------------------------

    def skill_status(self) -> dict[str, dict]:
        """One row per skill, in the spec's order, whatever the schedule did."""
        out: dict[str, dict] = {}
        for skill_id in SKILL_ORDER:
            package = self.packages.get(skill_id)
            rows = [r for r in self.log if r.get("skill") == skill_id]
            if not rows:
                outcome = "not_run"
            elif any(r["outcome"] == "failed" for r in rows):
                outcome = "failed"
            elif any(r["outcome"] == "refused" for r in rows):
                outcome = "refused"
            elif all(r["outcome"] == "not_built" for r in rows):
                outcome = "not_built"
            elif any(r["outcome"] == "skipped" for r in rows):
                outcome = "skipped"
            else:
                outcome = "done"
            out[skill_id] = {
                "outcome": outcome,
                "label": package.label if package else "",
                "version": package.manifest["version"] if package else "",
                "writes": package.writes if package else "",
                "started_at": rows[0]["started_at"] if rows else None,
                "finished_at": rows[-1]["finished_at"] if rows else None,
                "notes": [r["note"] for r in rows if r.get("note")],
            }
        return out

    def record_gap(self, gap: dict) -> None:
        """Store a gap, and mirror it where the closing screen looks (A.8).

        The mirror into ``handoff.gaps`` is the runner writing a section it does
        not own, and it is the one place that happens. It is deliberate and
        named in the spec: Handoff has to enumerate gaps recorded by skills that
        ran long before it, including gaps from a run that never reached it.
        """
        # The same gap said twice is one gap: a step's apply and its verify
        # often notice the same missing thing, and the closing screen listed
        # "no brain" three times over.
        # Two gaps from one step with the same real fix (``ollama pull x``)
        # are one missing thing said twice; "run setup again" is too general
        # a fix to merge on, so those also have to say the same words.
        def key(g: dict) -> tuple:
            fix_id = (g.get("fix") or {}).get("id")
            if fix_id and fix_id != "setup.rerun":
                return (g.get("skill"), g.get("fix_command"))
            return (g.get("skill"), g.get("what"), g.get("fix_command"))
        # Two steps that say the same words with the same fix (a book that
        # is downloaded but not searchable, seen by Integrate and by Prove)
        # are one missing thing too.
        def said(g: dict) -> tuple:
            return (g.get("what"), g.get("fix_command"))
        if any(key(g) == key(gap) or said(g) == said(gap) for g in self.gaps):
            return
        self.gaps.append(gap)
        handoff = self.sections.setdefault("handoff", {})
        handoff.setdefault("gaps", []).append(gap)

    # -- running ----------------------------------------------------------

    def _run_entry(self, step: Step) -> str:
        started = time.time()
        journal: dict[str, Any] = {}

        def finish(outcome: str, note: str = "", refusal: str = "") -> str:
            record = StepRecord(step.entry, step.skill, step.part, outcome,
                                started, time.time(), note, refusal,
                                shown=journal.get("shown"),
                                changed=journal.get("changed"),
                                verified=journal.get("verified"))
            self.log.append(record.as_dict())
            self.entries[step.entry] = {"outcome": outcome, "at": record.finished_at}
            self._save()
            return outcome

        missing = [key for key in step.requires if self.shared.get(key) is None]
        if missing:
            note = ("an earlier step did not finish, so this one had nothing to "
                    "work from")
            if step.skill:
                self.record_gap(fixes.gap(
                    skill=step.skill,
                    what=f"The {step.skill} step did not run.",
                    why=f"It needs {', '.join(missing)} from an earlier step "
                        f"that did not finish.",
                    impact="That part of your build is not recorded, and the "
                           "manifest says so rather than guessing.",
                    fix={"kind": "command", "id": "setup.rerun", "args": {}}))
            return finish("skipped", note)

        package = self.packages.get(step.skill) if step.skill else None
        if step.skill and package is None:
            return finish("failed", "", f"no package named '{step.skill}' loaded")

        lifecycle = lifecycle_part(step.part)
        context = RunContext(args=self.args, cfg=self.cfg,
                             non_interactive=self.non_interactive,
                             shared=self.shared, sections=self.sections,
                             package=package, part=lifecycle or "",
                             show_once=self.show_once(), remote=self.remote,
                             console=self.renderer is None)
        if step.say == "building" and self.renderer is None                 and self.runner_parts.get("before_building"):
            # The console's last question before the build (Toolsmith's model).
            try:
                self.runner_parts["before_building"](context)
            except Exception as exc:  # noqa: BLE001 - a question, never a stop
                if _is_deliberate_refusal(exc):
                    raise
                _log.warning("The question before the build was not asked: %s",
                             type(exc).__name__)
        if step.say and self.renderer is None:
            from puffpai.wizard import render_cli, wording
            render_cli.say_line(" ".join(wording.say(
                step.say, wording.mode_of(self.shared),
                name=self.shared.get("bot_name") or "your bot").split()))
        try:
            if package is None:
                part = self.runner_parts.get(step.part)
                if part is None:
                    return finish("skipped", "nothing wired for this entry")
                result = part(context)
            elif lifecycle is not None:
                result = self._run_lifecycle(package, context, journal,
                                             face=step.face)
            else:
                module = loader.load_module(package)
                func = getattr(module, f"part_{step.part}", None)
                if func is None:
                    return finish(
                        "failed", "",
                        f"package '{package.id}' has no part '{step.part}'")
                result = func(context)
        except Exception as exc:
            if _is_deliberate_refusal(exc):
                raise
            if package is not None and package.blocking:
                raise
            self.record_gap(fixes.gap(
                skill=step.skill or "runner",
                what=f"The {step.skill or 'wizard'} step stopped early.",
                why=f"{type(exc).__name__}: {exc}",
                impact="Your build carried on without it; that part of the "
                       "record is empty rather than wrong.",
                fix={"kind": "command", "id": "setup.rerun", "args": {}}))
            return finish("failed", "", f"{type(exc).__name__}: {exc}")

        return finish(*self._apply_result(step, package, result))

    # -- the Phase 2 lifecycle --------------------------------------------

    def _run_lifecycle(self, package: loader.Package,
                       context: RunContext, journal: dict,
                       face: str = FACE_ASK) -> Any:
        """propose -> card -> renderer -> apply -> verify, in that order.

        Every refusal here is the runner's, not the package's: a card that does
        not validate is not shown, an answer the card did not ask for is not
        applied, and a verify that claims something is recorded as what it
        observed. A package raising anywhere in here is handled exactly as a
        Phase 1 part raising -- gap and carry on, unless it is Brain or Secure.
        """
        module = loader.load_module(package)
        for name in ("propose", "apply", "verify"):
            if not callable(getattr(module, name, None)):
                raise PackageError(
                    f"Package '{package.id}' is scheduled for the card "
                    f"lifecycle and has no {name}().")

        proposal = module.propose(context) or {}
        if not isinstance(proposal, dict):
            raise PackageError(
                f"Package '{package.id}': propose() returns an object holding "
                f"its card, not {type(proposal).__name__}.")
        context.proposal = proposal
        card = cards.validate_card(proposal.get("card"))
        if card["skill"] != package.id or card["section"] != package.writes:
            raise PackageError(
                f"Package '{package.id}' proposed a card for "
                f"'{card['skill']}'/'{card['section']}'. A package speaks for "
                f"the one section it owns.")
        if context.part and card["part"] != context.part:
            raise PackageError(
                f"Package '{package.id}' was asked for its '{context.part}' "
                f"card and proposed '{card['part'] or 'no part'}'. A card "
                f"answers the question it was asked.")
        journal["shown"] = {"title": card["title"], "status": card["status"],
                            "decided": card["decided"],
                            "fields": [f["id"] for f in card["fields"]]}

        answers = self._render(card, face=face)
        answers = cards.validate_answers(card, answers)
        journal["changed"] = cards.redact_answers(card, answers)
        if answers:
            for skill_id in stale.stale_for_answers(card, answers):
                if skill_id not in self.stale:
                    self.stale.append(skill_id)

        result = module.apply(context, answers)
        if result is None:
            result = {}
        if not isinstance(result, dict):
            raise PackageError(
                f"Package '{package.id}': apply() returns a result object, not "
                f"{type(result).__name__}.")

        # Verify observes the world, and the section it is about is part of the
        # world by then: the data lands before verify is asked, not after the
        # entry finishes. Anything claiming a section the package does not own
        # is left for ``_apply_result`` to refuse -- it is not written here and
        # then unwritten.
        data = result.get("data")
        claimed = result.get("section")
        if isinstance(data, dict) and data and claimed in (None, package.writes):
            self.sections.setdefault(package.writes, {}).update(data)

        verdict = module.verify(context) or {}
        if not isinstance(verdict, dict):
            verdict = {}
        status = verdict.get("status") if verdict.get("status") in (
            "pass", "gap") else "gap"
        journal["verified"] = {
            "status": status,
            "evidence": cards.plain(verdict.get("evidence"), 300)}
        gap = verdict.get("gap")
        if status == "gap" and isinstance(gap, dict):
            result = dict(result)
            result.setdefault("gaps", [])
            result["gaps"] = list(result["gaps"]) + [gap]
        return result

    def quiet_answers(self, card: dict) -> dict:
        """What a card the owner does not see answers: the approved summary."""
        from puffpai.wizard import proposal
        return proposal.answers_for_card(
            card, self.shared.get("summary"), self.args,
            internet=self.shared.get("internet"))

    def _render(self, card: dict, face: str = FACE_ASK) -> dict:
        """Hand the card to whoever is showing it. A renderer that breaks is
        the same as a renderer that changed nothing: the proposal stands.

        On the console (no renderer handed in) a ``quiet`` card is not drawn:
        it answers from the approved summary -- unless the summary put
        something on it only the owner can type (the hub's key), in which case
        it is asked like any other card. A face that was handed a renderer
        (the browser service, tests) decides for itself.
        """
        renderer = self.renderer
        if renderer is None:
            from puffpai.wizard import proposal, render_cli, wording
            if face == FACE_QUIET and not (
                    not self.non_interactive
                    and proposal.must_ask(card, self.shared.get("summary"))):
                return self.quiet_answers(card)
            mode = wording.mode_of(self.shared)
            try:
                return render_cli.render(card,
                                         non_interactive=self.non_interactive,
                                         mode=mode) or {}
            except Exception as exc:
                if _is_deliberate_refusal(exc):
                    raise
                _log.warning("The card for '%s' could not be shown: %s",
                             card.get("skill"), exc)
                return {}
        try:
            return renderer(card, non_interactive=self.non_interactive) or {}
        except Exception as exc:
            if _is_deliberate_refusal(exc):
                raise
            _log.warning("The card for '%s' could not be shown: %s",
                         card.get("skill"), exc)
            return {}

    def _apply_result(self, step: Step, package: loader.Package | None,
                      result: Any) -> tuple[str, str, str]:
        """Merge a part's result under the one-writer rule."""
        if result is None:
            return ("done", "", "")
        if not isinstance(result, dict):
            return ("refused", "",
                    f"'{step.entry}' returned {type(result).__name__}, not a result")

        shared = result.get("shared")
        if isinstance(shared, dict):
            self.shared.update(shared)

        data = result.get("data")
        if isinstance(data, dict) and data:
            claimed = result.get("section")
            owned = package.writes if package else ""
            if package is None:
                return ("refused", "", "the runner writes no section")
            if claimed is not None and claimed != owned:
                refusal = (f"package '{package.id}' owns the '{owned}' section "
                           f"and tried to write '{claimed}'; nothing was "
                           f"written")
                self.record_gap(fixes.gap(
                    skill=package.id,
                    what=f"The {package.id} step tried to write a section it "
                         f"does not own.",
                    why=refusal,
                    impact="Nothing was recorded from that step, which is the "
                           "safe half of the mistake.",
                    fix={"kind": "command", "id": "setup.rerun", "args": {}}))
                return ("refused", "", refusal)
            self.sections.setdefault(owned, {}).update(data)

        # ``gaps`` (plural) is for a step that finds several at once: Prove
        # checks five classes and an owner with two undownloaded packs has two
        # gaps, not one summarised into a sentence they cannot act on.
        for many in result.get("gaps") or ():
            if isinstance(many, dict):
                self.record_gap(fixes.gap(
                    skill=many.get("skill") or step.skill or "runner",
                    what=many.get("what", ""), why=many.get("why", ""),
                    impact=many.get("impact", ""), fix=many.get("fix"),
                    blocking=bool(many.get("blocking"))))

        gap = result.get("gap")
        if isinstance(gap, dict):
            self.record_gap(fixes.gap(
                skill=gap.get("skill") or step.skill or "runner",
                what=gap.get("what", ""), why=gap.get("why", ""),
                impact=gap.get("impact", ""), fix=gap.get("fix"),
                blocking=bool(gap.get("blocking"))))

        outcome = result.get("status", "done")
        if outcome not in OUTCOMES:
            outcome = "done"
        return (outcome, str(result.get("note", "")), "")

    def run_step(self, step: Step) -> str:
        """Run one entry now, whatever the schedule says.

        How a single card is re-applied from a face that is not walking the
        whole wizard (spec B.3: re-running one skill is how you change
        anything). It goes through the same path as a scheduled entry, so the
        one-writer rule, the gap records and the log all still apply.
        """
        return self._run_entry(step)

    def run(self) -> None:
        for step in self.schedule:
            if self.entries.get(step.entry, {}).get("outcome") in _TERMINAL:
                continue
            self._run_entry(step)
        self._save()

    def next_entry(self) -> str:
        for step in self.schedule:
            if self.entries.get(step.entry, {}).get("outcome") not in _TERMINAL:
                return step.entry
        return ""

    # -- resume -----------------------------------------------------------

    def save(self) -> None:
        """Write the run record now, outside a step. The resume screen's
        "continue" uses it: what :meth:`reverify_blocking` un-did has to be on
        disk before the next request reads the record back."""
        self._save()

    def adopt(self, other: "Runner") -> None:
        """Take a stopped run's record as this run's starting point.

        Copies, never aliases: the stopped run's dicts are the ones that came
        off disk, and a live run mutating them in place would make the record
        it was loaded from a moving target.

        What is **not** copied is anything the loaded record could not carry --
        a secret was scrubbed on the way out and is not restored on the way back
        in, so a step that needs one asks for it again.
        """
        self.started_at = other.started_at
        self.entries = {k: dict(v) for k, v in other.entries.items()}
        self.log = [dict(row) for row in other.log]
        self.gaps = [dict(gap) for gap in other.gaps]
        self.stale = list(other.stale)
        self.sections = {k: dict(v) for k, v in other.sections.items()}
        self.shared = dict(other.shared)

    def entry_titles(self) -> dict[str, str]:
        """Entry id -> the title the owner was shown, from the stored log.

        Off disk, so it is clamped through :func:`cards.plain` before it is put
        on a screen. A record can say anything; what it says is not a licence to
        print it raw.
        """
        out: dict[str, str] = {}
        for row in self.log:
            entry = row.get("entry") if isinstance(row, dict) else None
            shown = row.get("shown") if isinstance(row, dict) else None
            if not isinstance(entry, str) or not isinstance(shown, dict):
                continue
            title = cards.plain(shown.get("title"), cards.MAX_TITLE)
            if title:
                out[entry] = title
        return out

    def _entry_label(self, step: Step) -> str:
        """What to call an entry the log has no title for."""
        if not step.skill and step.part in RUNNER_ENTRY_TITLES:
            return RUNNER_ENTRY_TITLES[step.part]
        package = self.packages.get(step.skill) if step.skill else None
        label = getattr(package, "label", "") or step.skill or "the wizard"
        part = lifecycle_part(step.part)
        return cards.plain(f"{label} ({part})" if part else label,
                           cards.MAX_TITLE)

    def resume_summary(self) -> dict:
        """What the resume screen says: when, where it stopped, what was decided.

        Card entries only. The runner's own entries (the Ollama config, the
        manifest) are not decisions an owner made, and listing them under "what
        you already decided" would be the record claiming credit for itself.
        """
        titles = self.entry_titles()
        decided: list[dict] = []
        stopped: Step | None = None
        done = [i for i, step in enumerate(self.schedule)
                if self.entries.get(step.entry, {}).get("outcome") in _TERMINAL]
        for index, step in enumerate(self.schedule):
            terminal = self.entries.get(step.entry, {}).get(
                "outcome") in _TERMINAL
            # A runner entry a later entry has already gone past (a record
            # from before the first screen existed) is not where it stopped.
            passed = (not step.skill and bool(done) and index < done[-1])
            if not terminal and stopped is None and not passed:
                stopped = step
            if not terminal or not step.skill:
                continue
            if lifecycle_part(step.part) is None:
                continue
            decided.append({
                "entry": step.entry,
                "card": card_id_of(step),
                "title": titles.get(step.entry) or self._entry_label(step)})
        return {
            "started_at": self.started_at,
            "age_s": max(0.0, time.time() - self.started_at),
            "decided": decided,
            "stopped_entry": stopped.entry if stopped else "",
            "stopped_card": card_id_of(stopped) if stopped else "",
            "stopped_title": (titles.get(stopped.entry)
                              or self._entry_label(stopped)) if stopped else "",
        }

    def reverify_blocking(self) -> list[str]:
        """Un-do every blocking card this record calls done that is not.

        The rule the resume screen rests on. A card from a package that may stop
        the build -- Brain and Secure, and only those, from their manifests --
        is not skipped because a file says it finished. Its ``verify`` is run
        again, against the machine as it is now, and anything that does not come
        back ``pass`` is dropped from the record so the schedule asks it again.

        The vault is stricter still, and deliberately so: before verify is even
        asked, the disk is checked for an initialised vault. Secure's verify
        only looks at the vault when the *recorded* block says one was
        configured, so a record that says the vault step finished with no vault
        in it would otherwise verify clean and the one ceremony nobody should
        take a file's word for would be skipped.

        Returns the card ids that will be asked again, so a screen can say so.
        """
        asked_again: list[str] = []
        for step in self.schedule:
            part = lifecycle_part(step.part)
            if part is None or not step.skill:
                continue
            if self.entries.get(step.entry, {}).get("outcome") not in _TERMINAL:
                continue
            package = self.packages.get(step.skill)
            if package is None or not package.blocking:
                continue
            if self._still_true(package, step, part):
                continue
            self.entries.pop(step.entry, None)
            asked_again.append(card_id_of(step))
        return asked_again

    def _still_true(self, package: loader.Package, step: Step,
                    part: str) -> bool:
        """Does this blocking card's verify still pass on this machine?

        Fail closed everywhere: a verify that raises, a package that will not
        load, a verdict that is not a dict -- all of them mean "ask again".
        """
        if step.skill == "secure" and part == "vault" and not vault_on_disk(
                self.cfg):
            return False
        context = RunContext(args=self.args, cfg=self.cfg,
                             non_interactive=True, shared=self.shared,
                             sections=self.sections, package=package,
                             part=part, show_once=None, remote=True)
        try:
            module = loader.load_module(package)
            verdict = module.verify(context)
        except Exception as exc:  # noqa: BLE001 - a broken verify is a re-ask
            _log.warning("The '%s' card could not be re-verified on resume: "
                         "%s: %s", card_id_of(step), type(exc).__name__, exc)
            return False
        return isinstance(verdict, dict) and verdict.get("status") == "pass"


#: What the resume screen calls the runner's own entries.
RUNNER_ENTRY_TITLES = {"first_look": "What this computer has",
                       "needs": "What your bot needs",
                       "summary": "The plan"}

#: Entries that measure and ask nothing. A record where only these finished
#: holds no answer of the owner's, so there is nothing to offer to continue:
#: reloading the first screen is not "a setup that stopped".
MEASURED_ONLY = ("discover.card",)


def nothing_answered(run: "Runner") -> bool:
    """Did this record finish nothing but the quiet check of this computer?"""
    done = [entry for entry, row in run.entries.items()
            if isinstance(row, dict) and row.get("outcome") in _TERMINAL]
    return bool(done) and all(entry in MEASURED_ONLY for entry in done)


def card_id_of(step: Step) -> str:
    """``secure.vault`` the entry -> ``secure:vault`` the card id, or ""."""
    part = lifecycle_part(step.part)
    if part is None or not step.skill:
        return ""
    return f"{step.skill}:{part}" if part else step.skill


def vault_on_disk(cfg) -> bool:
    """Is there an initialised vault on this machine? Nothing else counts.

    Not "does the record say so". The vault is the one step a resumed run may
    not take a file's word for, and the file and the disk are two different
    claims.
    """
    try:
        from puffpai import cli
        from puffpai.security.vault import Vault
        return bool(Vault(vault_dir=cli._vault_dir(cfg)).is_initialized)
    except Exception:  # pragma: no cover - a vault dir we cannot read is "no"
        return False


def archive_state(path: str) -> str:
    """Move a run record aside. Returns where it went, or "" if it did not.

    Starting over does not delete the old record: it is the only account of
    what the owner decided last time, and an owner who picked "start over" by
    mistake has lost nothing they cannot find.
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = f"{path}.{stamp}.stopped"
    try:
        os.replace(path, target)
    except OSError:
        return ""
    return target


def find_stopped_run(*, args, cfg, state_path: str | None = None,
                     schedule: tuple[Step, ...] = SCHEDULE
                     ) -> tuple["Runner | None", str]:
    """A stopped run worth offering to continue, and why not when there is none.

    The second half of the pair is the point: a record that is there and is not
    offerable is not the same as no record at all, and the owner is told when
    this build set one aside. The reasons, all of them plain:

    * ``""`` -- nothing on disk. Nothing to say.
    * :data:`RESUME_UNREADABLE` -- a file is there and this build will not read
      it. Said out loud, because something was set aside.
    * :data:`RESUME_NOTHING_DONE` -- a record with no finished step. There is
      nothing to continue *from*, so starting over is starting over.
    * :data:`RESUME_FINISHED` -- the run reached the end of the schedule.
    * :data:`RESUME_TOO_OLD` -- older than :data:`RESUME_MAX_AGE_S`. A month-old
      half-build is a description of a machine that has since changed.
    """
    run = Runner(args=args, cfg=cfg, non_interactive=True,
                 schedule=schedule, state_path=state_path)
    if not os.path.exists(run.state_path()):
        return None, ""
    if not run.load_state():
        return None, RESUME_UNREADABLE
    if not any(row.get("outcome") in _TERMINAL for row in run.entries.values()):
        return None, RESUME_NOTHING_DONE
    if not run.next_entry():
        return None, RESUME_FINISHED
    if time.time() - run.started_at > RESUME_MAX_AGE_S:
        return None, RESUME_TOO_OLD
    return run, ""


def _is_deliberate_refusal(exc: BaseException) -> bool:
    """A refusal the wizard means, as opposed to a step that broke.

    ``cli.SetupRefused`` is the wizard telling the owner exactly what is wrong
    and what to do. It has its own screen and its own exit code, and turning it
    into a gap would replace a clear message with a quiet one.
    """
    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
        return True
    try:
        from puffpai.cli import SetupRefused
    except Exception:  # pragma: no cover - defensive
        return False
    return isinstance(exc, SetupRefused)


__all__ = ["FACE_ASK", "FACE_QUIET", "OUTCOMES", "RESUME_FINISHED", "RESUME_MAX_AGE_S",
           "RESUME_NOTHING_DONE", "RESUME_TOO_OLD", "RESUME_UNREADABLE",
           "RunContext", "Runner", "SCHEDULE", "SKILL_ORDER", "STATE_SCHEMA",
           "Step", "StepRecord", "V2_PART", "archive_state", "asked_card_ids",
           "card_id_of",
           "find_stopped_run", "json_safe", "lifecycle_part", "vault_on_disk"]
