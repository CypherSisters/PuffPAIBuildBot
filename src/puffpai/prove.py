"""Prove: the build is asked to show its work, twice.

Two halves, one skill.

**The static half** runs inside the wizard, before the closing screen. No
server, no model, no network: it reads what the build just wrote and checks the
five classes of failure a real owner has hit: a persona id that was
a reserved word (``apps``), a persona nothing in the dashboard could show,
knowledge packs "chosen" that were never downloaded and never said so, and a
closing screen printing commands and ports that did not exist on that machine.
Each check ends in ``pass`` / ``fail`` / ``gap`` / ``not_applicable`` with one
plain line of reason.

**The live half** is ``puffpai prove``, run once the backend is up. It talks to
the build over HTTP the way any client would -- ``GET /persona/list``, ``GET
/knowledge/sources``, and at most three short ``POST /chat/send`` generations --
because a build that agrees with its own manifest is exactly what produced
those failures. It ends with the one step it cannot do: it prints the
dashboard address and asks the owner whether the bot is in the left menu.
**It has no browser, so
it never reports a browser claim it did not observe**; the answer is recorded as
confirmed, refuted, or unproven, and unproven is an acceptable outcome.

A failed Prove does not block Handoff. It is stated on the closing screen and
on the dashboard home, in the same words, until it passes.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from puffpai.wizard import fixes

SCHEMA = "prove/v1"

PASS = "pass"
FAIL = "fail"
GAP = "gap"
NOT_APPLICABLE = "not_applicable"

#: An id a persona may have: a word, lowercase, that can be a folder and a URL
#: segment. Same shape ``persona_scaffold`` writes; stated here because this
#: module's job is to disagree with the build when the build is wrong.
ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")

#: The owner's question, asked verbatim of every build.
IDENTITY_QUESTION = "Who are you and what are you for?"

#: Never more than this many generations. Prove is a check, not a conversation,
#: and every one of these is a local model doing real work.
MAX_CHAT_CALLS = 3

_UNSET = object()


def _check(check_id: str, title: str, result: str, reason: str) -> dict[str, Any]:
    return {"id": check_id, "title": title, "result": result,
            "reason": str(reason)[:300]}


# ---------------------------------------------------------------------------
# The static half
# ---------------------------------------------------------------------------

def _persona_block(manifest: dict[str, Any]) -> dict[str, Any]:
    block = manifest.get("persona")
    return dict(block) if isinstance(block, dict) else {}


def persona_id_of(persona: dict[str, Any]) -> str:
    """The id of the place this build scaffolded, or ``""``.

    **The real shape first.** The wizard and ``puffpai persona new`` both write
    the manifest's persona block with ``app`` (and ``pack``) naming the folder
    and the page -- ``persona_scaffold.scaffold`` returns it that way and both
    paths copy it verbatim. This module used to read ``id``, which no build has
    ever written, so every real build was told "This build made no persona" and
    the three persona checks were skipped on exactly the builds they exist for.

    ``id`` is still accepted so an older record, or a hand-written one, reads
    back. A block with neither -- which is what a build that recorded a goal and
    scaffolded no place looks like -- has no id, and that is a legitimate state,
    not a missing persona.
    """
    if not isinstance(persona, dict):
        return ""
    return str(persona.get("app") or persona.get("id") or "").strip()


def _read_json(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _check_id_sanity(persona_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
    """A known failure: a bot whose id is the word ``apps``."""
    from puffpai import app_registry, persona_scaffold

    title = "The bot's id is its own"
    if persona_scaffold.is_reserved_id(persona_id):
        return _check("id_sanity", title, FAIL,
                      f"'{persona_id}' is a word this build already uses for "
                      f"something else, so the bot has no page of its own.")
    if not ID_RE.match(persona_id):
        return _check("id_sanity", title, FAIL,
                      f"'{persona_id}' cannot be a folder and a web address: "
                      f"lowercase letters, digits and underscore, 2-32 long.")

    app_path = os.path.join(app_registry.user_apps_dir(), persona_id,
                            app_registry.MANIFEST_NAME)
    pack_path = os.path.join(app_registry.user_packs_dir(), persona_id,
                             app_registry.PACK_MANIFEST_NAME)
    missing = [p for p in (app_path, pack_path) if not os.path.isfile(p)]
    if missing:
        return _check("id_sanity", title, FAIL,
                      f"The build says it made '{persona_id}', and "
                      f"{missing[0]} is not there.")
    app = _read_json(app_path)
    pack = _read_json(pack_path)
    for label, data, path in (("app", app, app_path), ("pack", pack, pack_path)):
        if str(data.get("id") or "") != persona_id:
            return _check("id_sanity", title, FAIL,
                          f"The build calls the bot '{persona_id}' and its "
                          f"{label} file calls it "
                          f"'{data.get('id')}'.")
    return _check("id_sanity", title, PASS,
                  f"'{persona_id}' is the bot's own word, and its two files "
                  f"agree.")


def _check_visible(persona_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
    """Can anything show it? The preconditions, on disk, before the server runs."""
    from puffpai import app_registry, persona_scaffold

    title = "The bot can be shown"
    surfaces = manifest.get("surfaces")
    surfaces = surfaces if isinstance(surfaces, dict) else {}
    allowlist = [str(s) for s in (surfaces.get("allowlist") or [])]
    surface = persona_scaffold.persona_surface(persona_id)
    if not allowlist:
        return _check("persona_visible", title, PASS,
                      "This build shows everything, so nothing can hide the "
                      "bot.")
    if surface not in allowlist:
        return _check("persona_visible", title, FAIL,
                      f"The build only loads {len(allowlist)} surfaces and "
                      f"'{surface}' is not among them, so the bot has no way "
                      f"in.")
    app_path = os.path.join(app_registry.user_apps_dir(), persona_id,
                            app_registry.MANIFEST_NAME)
    declared = [str(s) for s in (_read_json(app_path).get("surfaces") or [])]
    if surface not in declared:
        return _check("persona_visible", title, FAIL,
                      f"The bot's own app file does not claim '{surface}', so "
                      f"the menu has nothing to hang it on.")
    return _check("persona_visible", title, PASS,
                  f"'{surface}' is in the build's list and in the bot's own "
                  f"file.")


def _check_sources(status: dict[str, Any],
                   skipped: str = "") -> tuple[dict[str, Any], list[dict]]:
    """Chosen, downloaded, indexed -- reconciled. Gaps, never failures.

    *skipped* is set when this build never offered the owner a source at all.
    That run has nothing "chosen", so it gets one plain line and one gap that
    says what happened, rather than a list of packs the owner never saw.
    """
    from puffpai.knowledge import build_sources

    title = "The knowledge it was promised"
    if skipped:
        return (_check("sources_honest", title, NOT_APPLICABLE,
                       "Knowledge setup was skipped, so nothing was chosen and "
                       "nothing was downloaded."),
                [fixes.gap(
                    skill="prove",
                    what="Knowledge setup was skipped.",
                    why="No source was offered and none was chosen, so this "
                        "build has only its base model and whatever is already "
                        "in its knowledge folder.",
                    impact="Your bot will say it does not know, rather than "
                           "guess, on anything a pack would have covered.",
                    fix={"kind": "command", "id": "setup.rerun", "args": {}})])
    items = [i for i in (status.get("items") or []) if isinstance(i, dict)]
    gaps: list[dict] = []
    missing, unindexed = [], []
    for item in items:
        source_id = str(item.get("id") or "")
        label = str(item.get("label") or source_id)
        if item["status"] == build_sources.STATUS_SELECTED_NOT_DOWNLOADED:
            missing.append(label)
            gaps.append(fixes.gap(
                skill="prove",
                what=f"{label} was chosen during the build and never "
                     f"downloaded.",
                why="It is not in this computer's library folders, and "
                    "your bot has not learned any of it.",
                impact="Your bot will say it does not know, rather than guess, "
                       "on questions this was meant to answer.",
                fix={"kind": "command", "id": "source.fetch",
                     "args": {"source_id": source_id}}))
        elif item["status"] == build_sources.STATUS_FETCHED_NOT_INDEXED:
            unindexed.append(label)
            gaps.append(fixes.gap(
                skill="prove",
                what=f"{label} is downloaded but not searchable yet.",
                why=str(item.get("note") or
                        "The file is on this computer, but your bot has not "
                        "learned any of it yet."),
                impact="Your bot cannot find anything in it until it has "
                       "learned it.",
                fix={"kind": "command", "id": "knowledge.sync", "args": {}}))
    if not gaps:
        return (_check("sources_honest", title, PASS,
                       "Everything this build chose is downloaded and "
                       "searchable."), [])
    parts = []
    if missing:
        parts.append(f"{len(missing)} never downloaded ({', '.join(missing[:3])})")
    if unindexed:
        parts.append(f"{len(unindexed)} downloaded but not searchable")
    return (_check("sources_honest", title, GAP,
                   "; ".join(parts) + "."), gaps)


def _check_handoff(launchers: Any) -> dict[str, Any]:
    """Every command the closing screen is about to print, stat'd on disk."""
    import shutil

    title = "The start-up lines are true here"
    if launchers is None:
        if shutil.which("puffpai"):
            return _check("handoff_correct", title, PASS,
                          "This install has no launcher scripts, and "
                          "`puffpai` runs from this shell.")
        return _check("handoff_correct", title, GAP,
                      "`puffpai` is not on this machine's PATH, so the "
                      "closing screen's commands need the full path.")

    env_file = str(launchers.get("env_file") or "")
    if not env_file or not os.path.isfile(env_file):
        return _check("handoff_correct", title, FAIL,
                      f"The installer record points at {env_file or 'nothing'}, "
                      f"which is not there.")
    # The launchers live in the install folder (beside the pointer); only an
    # older record without it is read as "beside the env file".
    folder = str(launchers.get("root") or "") or os.path.dirname(env_file)
    for key in ("backend", "dashboard"):
        script = str(launchers.get(key) or "")
        if not script:
            continue
        path = os.path.join(folder, os.path.basename(script.replace("\\", "/")))
        if not os.path.isfile(path):
            return _check("handoff_correct", title, FAIL,
                          f"The closing screen would tell you to run {script}, "
                          f"and that file is not in {folder}.")
    url = str(launchers.get("dashboard_url") or "")
    if launchers.get("dashboard") and not url.rsplit(":", 1)[-1].isdigit():
        return _check("handoff_correct", title, FAIL,
                      "The installer wrote a dashboard with no usable port, so "
                      "the address on the closing screen goes nowhere.")
    return _check("handoff_correct", title, PASS,
                  "Every command and address the closing screen prints exists "
                  "on this machine.")


def _check_goal(goal: str, bot_name: str) -> dict[str, Any]:
    title = "It knows what it is for"
    if not str(goal).strip():
        return _check("goal_reaches_model", title, FAIL,
                      "Nothing in this build records what the owner asked for, "
                      "so the bot is told nothing about itself.")
    if not str(bot_name).strip():
        return _check("goal_reaches_model", title, FAIL,
                      "The build has a goal and no name to answer to.")
    return _check("goal_reaches_model", title, PASS,
                  f"{bot_name} is told: {str(goal).strip()[:120]}")


def static_prove(*, manifest: Any = _UNSET, sources: Any = _UNSET,
                 launchers: Any = _UNSET, goal: Any = _UNSET,
                 bot_name: Any = _UNSET) -> dict[str, Any]:
    """Run every static check. Never raises: a check that breaks is a gap.

    Every input can be handed in, which is how the tests replay a known-broken
    build; left alone, each is read from this install.
    """
    if manifest is _UNSET:
        try:
            from puffpai import build_manifest
            manifest = build_manifest.read_manifest() or {}
        except Exception:
            manifest = {}
    manifest = manifest if isinstance(manifest, dict) else {}

    persona = _persona_block(manifest)
    persona_id = persona_id_of(persona)
    checks: list[dict[str, Any]] = []
    gaps: list[dict] = []

    if not persona_id:
        for check_id, title in (("id_sanity", "The bot's id is its own"),
                                ("persona_visible", "The bot can be shown")):
            checks.append(_check(check_id, title, NOT_APPLICABLE,
                                 "This build scaffolded no place of its own, "
                                 "so there is no id or page to look for."))
    else:
        checks.append(_safely(_check_id_sanity, "id_sanity",
                              "The bot's id is its own", persona_id, manifest))
        checks.append(_safely(_check_visible, "persona_visible",
                              "The bot can be shown", persona_id, manifest))

    try:
        from puffpai.knowledge import build_sources
        skipped = build_sources.sourcing_skipped(manifest)
    except Exception:
        skipped = ""
    if sources is _UNSET:
        try:
            from puffpai.knowledge import build_sources
            sources = build_sources.source_status(manifest or None)
        except Exception:
            sources = {}
    try:
        source_check, source_gaps = _check_sources(sources or {}, skipped)
    except Exception as exc:  # pragma: no cover - defensive
        source_check, source_gaps = _broken("sources_honest",
                                            "The knowledge it was promised",
                                            exc), []
    checks.append(source_check)
    gaps.extend(source_gaps)

    if launchers is _UNSET:
        try:
            from puffpai import cli
            launchers = cli.installed_launchers()
        except Exception:
            launchers = None
    checks.append(_safely(_check_handoff, "handoff_correct",
                          "The start-up lines are true here", launchers))

    # The goal is checked whether or not a place was scaffolded. A goal reaches
    # the model through ``build_context.collect_goal``, which reads
    # ``persona.goal`` off the manifest -- a build that recorded a goal and no
    # folder is a build whose bot knows what it is for, and skipping the check
    # there was how "--yes --goal ..." ended up telling the model the owner had
    # written nothing down.
    if goal is _UNSET:
        # The block being checked first, then this install. They are the same
        # source on a live run (``collect_goal`` reads ``persona.goal`` off the
        # manifest); handing a manifest in is how a build is replayed, and a
        # replay must not be graded against the machine it is replayed on.
        goal = str(persona.get("goal") or "")
        if not goal:
            try:
                from puffpai import build_context
                goal = build_context.collect_goal()
            except Exception:
                goal = ""
    if bot_name is _UNSET:
        bot_name = str(persona.get("name") or "")
        if not bot_name:
            try:
                from puffpai import bot_identity
                bot_name = bot_identity.bot_name("")
            except Exception:
                bot_name = ""
    if not persona_id and not str(goal or "").strip() and not persona:
        checks.append(_check("goal_reaches_model", "It knows what it is for",
                             NOT_APPLICABLE,
                             "This build recorded nothing about what it is "
                             "for, and made no persona to carry one."))
    else:
        checks.append(_safely(_check_goal, "goal_reaches_model",
                              "It knows what it is for", goal, bot_name))

    for check in checks:
        if check["result"] == FAIL:
            gaps.append(fixes.gap(
                skill="prove",
                what=f"{check['title']}: no.",
                why=check["reason"],
                impact="The build reports success over something an owner "
                       "would find broken on their first look.",
                fix=_fix_for(check, persona_id)))

    return {
        "schema": SCHEMA,
        "static_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "persona_id": persona_id,
        "bot_name": str(persona.get("name") or ""),
        "checks": checks,
        "gaps": gaps,
        "verdict": verdict(checks),
        "live": None,
    }


def _fix_for(check: dict[str, Any], persona_id: str) -> dict[str, Any]:
    """The one registered command that answers this failure."""
    if check["id"] == "id_sanity" and persona_id:
        from puffpai import persona_scaffold
        if persona_scaffold.is_reserved_id(persona_id):
            return {"kind": "command", "id": "persona.rename_id",
                    "args": {"persona_id": persona_id,
                             "new_id": persona_scaffold.FALLBACK_ID}}
    return {"kind": "command", "id": "setup.rerun", "args": {}}


def _safely(func, check_id: str, title: str, *args) -> dict[str, Any]:
    try:
        return func(*args)
    except Exception as exc:  # pragma: no cover - defensive
        return _broken(check_id, title, exc)


def _broken(check_id: str, title: str, exc: BaseException) -> dict[str, Any]:
    return _check(check_id, title, GAP,
                  f"This could not be checked on this machine: "
                  f"{type(exc).__name__}.")


def verdict(checks: list[dict[str, Any]]) -> str:
    results = {c.get("result") for c in checks}
    if FAIL in results:
        return FAIL
    if GAP in results:
        return GAP
    return PASS


# ---------------------------------------------------------------------------
# What the owner reads
# ---------------------------------------------------------------------------

#: The closing screen's Prove lines sit between these two markers. They are the
#: whole of what Prove adds to a wizard whose printed output Phase 1 froze, and
#: ``tests/test_wizard_equivalence.py`` compares the two paths with the block
#: between them removed -- which keeps that comparison exact everywhere else.
BEGIN_MARK = "--- proof of this build ---"
END_MARK = "--- end proof ---"

RUN_LINE = ("Once it is running, ask it to prove itself: "
            "`puffpai prove`")


def closing_lines(record: dict[str, Any]) -> list[str]:
    """What the closing screen prints. One line when everything passed."""
    checks = record.get("checks") or []
    gaps = record.get("gaps") or []
    lines = [BEGIN_MARK]
    failed = [c for c in checks if c.get("result") == FAIL]
    if failed:
        lines.append("Before you start - this build did not check out:")
        for check in failed:
            lines.append(f"  - {check['reason']}")
    elif gaps:
        lines.append(f"Before you start - {len(gaps)} thing"
                     f"{'s' if len(gaps) != 1 else ''} the build chose and did "
                     f"not finish:")
    for gap in fixes.safe_gaps(gaps):
        # Re-rendered, never echoed: the gap may have come off disk, and the
        # closing screen is the last place a line should be printed on trust.
        lines.append(f"  - {gap['what']}")
        if gap["fix_command"]:
            lines.append(f"      {gap['fix_command']}")
    lines.append(RUN_LINE)
    lines.append(END_MARK)
    return lines


def count_line(checks: list) -> str:
    """"Of 5 checks: 1 passed, 2 found something missing, 2 did not apply."

    Never "5 checks ran and none failed" when one passed.
    """
    checks = [c for c in checks or [] if isinstance(c, dict)]
    passed = sum(1 for c in checks if c.get("result") == PASS)
    gaps = sum(1 for c in checks if c.get("result") == GAP)
    skipped = sum(1 for c in checks if c.get("result") == NOT_APPLICABLE)
    parts = [f"{passed} passed"]
    if gaps:
        parts.append(f"{gaps} found something missing")
    if skipped:
        parts.append(f"{skipped} did not apply to this build")
    other = len(checks) - passed - gaps - skipped
    if other:
        parts.append(f"{other} could not tell")
    noun = "check" if len(checks) == 1 else "checks"
    return f"Of {len(checks)} {noun}: " + ", ".join(parts) + "."


def summary_head(record: dict[str, Any]) -> str:
    """What the build's own checks found, in one sentence, or "" when none
    ran. The one wording the end screen and the home page both show, so the
    two cannot disagree."""
    checks = [c for c in (record or {}).get("checks") or [] if isinstance(c, dict)]
    if not checks:
        return ""
    failed = [c for c in checks if c.get("result") == FAIL]
    if failed:
        return ("This build did not check out: "
                + "; ".join(str(c.get("reason") or "") for c in failed[:3]))
    return count_line(checks)


def summary_line(record: dict[str, Any]) -> str:
    """:func:`summary_head`, then how to prove the rest while the running bot
    has not been asked yet."""
    head = summary_head(record)
    if not head:
        return ""
    live = (record or {}).get("live")
    if isinstance(live, dict) and live.get("ran"):
        return head
    # Words on a screen, not code marks around the command.
    return f"{head} {RUN_LINE.replace('`', '')}"


def strip_block(lines: list[str]) -> list[str]:
    """*lines* with the Prove block removed. Used by the equivalence test."""
    out, inside = [], False
    for line in lines:
        if line.strip() == BEGIN_MARK:
            inside = True
            continue
        if line.strip() == END_MARK:
            inside = False
            continue
        if not inside:
            out.append(line)
    return out


# ---------------------------------------------------------------------------
# The record, in the store the runner already owns
# ---------------------------------------------------------------------------

def state_path(cfg: Any = None) -> str:
    """``<CONFIG_DIR>/wizard_run.json`` -- the runner's own state file.

    Not the build manifest: its schema is frozen for Phase 1 and a proof is not
    part of what the build IS.
    """
    if cfg is None:
        from puffpai.config import get_config
        cfg = get_config()
    return os.path.join(getattr(cfg, "CONFIG_DIR", "") or os.getcwd(),
                        "wizard_run.json")


def read_record(cfg: Any = None) -> dict[str, Any]:
    """The last prove record, or ``{}`` when prove has never run.

    The record comes off disk, so its gaps are rebuilt through the fix registry
    on the way out and any whose fix does not validate is dropped. Every
    consumer -- the CLI, ``GET /build/prove``, the dashboard strip behind it --
    therefore sees commands this build rendered, never a string a file
    supplied.
    """
    data = _read_json(state_path(cfg))
    section = (data.get("sections") or {}).get("prove")
    if isinstance(section, dict) and section.get("schema") == SCHEMA:
        record = dict(section)
        record["gaps"] = fixes.safe_gaps(record.get("gaps"), drop_invalid=True)
        return record
    return {}


def write_record(record: dict[str, Any], cfg: Any = None) -> str:
    """Store *record* under the run state's ``prove`` section, atomically."""
    import tempfile

    path = state_path(cfg)
    data = _read_json(path)
    data.setdefault("sections", {})
    if not isinstance(data.get("sections"), dict):
        data["sections"] = {}
    data["sections"]["prove"] = record
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", delete=False, dir=os.path.dirname(path) or ".",
        prefix=".wizard_run-", suffix=".tmp")
    try:
        json.dump(data, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    try:
        os.chmod(handle.name, 0o600)
    except OSError:
        pass
    os.replace(handle.name, path)
    return path


__all__ = ["BEGIN_MARK", "END_MARK", "FAIL", "GAP", "IDENTITY_QUESTION",
           "count_line", "summary_head", "summary_line",
           "MAX_CHAT_CALLS", "NOT_APPLICABLE", "PASS", "RUN_LINE", "SCHEMA",
           "closing_lines", "read_record", "state_path", "static_prove",
           "strip_block", "verdict", "write_record"]
