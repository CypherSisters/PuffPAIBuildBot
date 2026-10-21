"""The "Finding out what a <kind> bot needs" step.

Between the name and the plan, both wizards find out what the bot needs. The
needs engine and ``find_parts`` belong elsewhere and are not in this build
yet, so these tests use fakes that follow the pinned signatures exactly
and prove the fallback: with no engine, the wizard
goes from the name straight to the plan, as before.

Nothing here reaches the network. The fakes are goal-agnostic: every test runs
on more than one unrelated goal.

This file also holds the shared fakes the other ``test_r3_*`` files
import (plain helpers, no test functions, so nothing is collected twice).

Run:
    pytest tests/test_wizard_needs_step.py
"""
from __future__ import annotations

import copy
import os
import sys
import time
import types
from dataclasses import dataclass

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _wizard_driver import drive_in_process  # noqa: E402

from puffpai import brain_health, hardware_census, setup_ops  # noqa: E402
from puffpai.config import get_config  # noqa: E402
from puffpai.wizard import netcheck, sources_suggest  # noqa: E402


@pytest.fixture(autouse=True)
def _parked_needs_step(monkeypatch):
    """The needs step is parked behind "Finding things for your bot"
    These tests keep the parked step working,
    so they run the wizard as a build without the find step."""
    from puffpai.wizard import service
    monkeypatch.setattr(service, "finding_engine", lambda: None)



# -- the fakes (pinned signatures, I-4) ---------------------------------------

GOALS = {
    "wedding": ("I want to build a wedding planner bot. My brother just got "
                "engaged. I'll need to be able to track themes from pictures, "
                "plan the budget and find vendors.", "wedding planner"),
    "minecraft": ("A helper for my kids' minecraft server. It should back up "
                  "the world, show who is online and restart it at night.",
                  "minecraft server"),
}


@dataclass(frozen=True)
class FakeSendable:
    """The shape of ``outbound_query.Sendable`` (I-1)."""
    phrase: str
    source_hint: str = ""
    subject: bool = False


def needs_result(kind: str, goal_id: str = "x") -> dict:
    """A needs/v1 answer for any kind: three asked jobs, one usual job,
    one held word, two private words."""
    word = kind.split()[0]
    return {
        "schema": "needs/v1", "prompt": "needs-v5", "model": "fake:1b",
        "goal_digest": f"sha256:{goal_id}", "kind": kind,
        "kind_source": "rules",
        "subject": {"phrase": kind, "notes": [], "sendable": True},
        "jobs": [
            {"id": "j1", "name": f"Keep the {word} plan", "does": "Plans.",
             "source": "asked", "seen_in": [], "ticked": True, "learn": False,
             "parts": [{"kind": "code-app", "search": [
                 {"phrase": f"{word} planner app", "state": "out", "why": []}]}]},
            {"id": "j2", "name": "Track the money", "does": "Budgets.",
             "source": "asked", "seen_in": [], "ticked": True, "learn": False,
             "parts": [{"kind": "data-template", "search": [
                 {"phrase": "budget template", "state": "out", "why": []}]}]},
            {"id": "j3", "name": "Find helpers nearby", "does": "Finds.",
             "source": "asked", "seen_in": [], "ticked": True, "learn": False,
             "parts": [{"kind": "code-app", "search": [
                 {"phrase": "vendor list app", "state": "out", "why": []}]}]},
            {"id": "j4", "name": "Send reminders", "does": "Reminds.",
             "source": "usual", "seen_in": [], "ticked": True, "learn": False,
             "parts": [{"kind": "code-app", "search": [
                 {"phrase": "reminder app", "state": "out", "why": []}]}]},
        ],
        "held": [{"word": "instagram", "why": "brand", "allowed": False}],
        "private_left_out": ["sister", "kids"],
        "helper": "ready",
        "timings": {"rules_ms": 1.0, "first_job_s": 0.1, "model_s": 0.2},
    }


def fake_needs_module(kind: str, calls: dict, *, prompt: str = "needs-v5",
                      fail: bool = False) -> types.ModuleType:
    mod = types.ModuleType("puffpai.wizard.needs")

    def prewarm(cfg) -> None:
        calls.setdefault("prewarm", []).append(True)

    def run(goal, *, cfg, on_line=None, on_job=None, deadline_s=60.0):
        calls.setdefault("run", []).append({"deadline_s": deadline_s})
        if fail:
            raise RuntimeError("the brain fell over")
        result = needs_result(kind)
        result["prompt"] = prompt
        for job in result["jobs"]:
            if on_job:
                on_job(copy.deepcopy(job))
        return result

    def from_words(words, *, cfg):
        return needs_result(kind)

    def choose(needs, *, ticked, added, allowed_words):
        calls.setdefault("choose", []).append(
            {"ticked": list(ticked), "added": list(added),
             "allowed_words": list(allowed_words)})
        out = copy.deepcopy(needs)
        for job in out["jobs"]:
            job["ticked"] = job["id"] in ticked
        for text in added:
            out["jobs"].append({
                "id": f"j{len(out['jobs']) + 1}", "name": text, "does": "",
                "source": "owner", "seen_in": [], "ticked": True,
                "learn": False, "parts": []})
        for row in out["held"]:
            if row["word"] in allowed_words:
                row["allowed"] = True
        return out

    def sends(needs, *, goal):
        calls.setdefault("sends", []).append(True)
        out = [FakeSendable(needs["subject"]["phrase"], "subject", True)]
        for job in needs["jobs"]:
            if not job.get("ticked"):
                continue
            for part in job.get("parts") or ():
                for row in part.get("search") or ():
                    out.append(FakeSendable(row["phrase"], job["id"]))
        return out

    def get_helper(*, ticket, device_id, progress):
        return {"state": "failed", "line": "not in this build yet"}

    for fn in (prewarm, run, from_words, choose, sends, get_helper):
        setattr(mod, fn.__name__, fn)
    return mod


def plan_result(kind: str) -> dict:
    """What a fake ``find_parts.plan_items`` finds: a pre-ticked app that
    needs the locked box, the box line itself, a pictures line, a statement
    line, notes for two jobs and one job it did not reach."""
    word = kind.split()[0]
    return {
        "items": [
            {"id": f"apps_code:github:someone/{word}-planner",
             "section": "apps_code",
             "label": f"Planner (github.com/someone/{word}-planner)",
             "detail": "Opens inside your bot. Needs no internet.",
             "size_bytes": 320000, "outside_host": "codeload.github.com",
             "default_on": True, "needs_yes": True,
             "apply": {"kind": "code_project",
                       "ref": f"github:someone/{word}-planner"},
             "need": "j1", "why": "helps with: the plan", "might_help": False,
             "requires": ["box_setup"],
             "facts": {"stars": 566, "changed": "2026-03", "license": "MIT",
                       "mode": "web_build", "trust_path": "stars",
                       "hosts_all": ["codeload.github.com"],
                       "runtime_hosts": [], "size_bytes": 320000,
                       "box": True, "junk": "dropped"}},
            {"id": "apps_code:github:other/money-sheet", "section": "apps_code",
             "label": "Money sheet (github.com/other/money-sheet)",
             "detail": "A list your bot can read.", "size_bytes": 4000,
             "outside_host": "codeload.github.com", "default_on": False,
             "needs_yes": True,
             "apply": {"kind": "data_import", "ref": "github:other/money-sheet"},
             "need": "j2", "might_help": True},
            {"id": "pictures:commons:examples", "section": "pictures",
             "label": "12 example pictures from Wikimedia Commons",
             "detail": "Free to use with credit.", "size_bytes": 9000000,
             "outside_host": "upload.wikimedia.org", "default_on": False,
             "needs_yes": True,
             "apply": {"kind": "picture_set", "ref": "commons:examples"}},
            {"id": "apps_code:note:server-apps", "section": "apps_code",
             "label": "Some apps need their own server",
             "detail": "BuildBot cannot run those yet.", "size_bytes": None,
             "outside_host": None, "default_on": True, "needs_yes": False,
             "apply": {"kind": "none", "ref": ""}, "need": "j1"},
        ],
        "notes": {"j1": ["Pinry (github.com/pinry/pinry, needs its own server)"],
                  "j2": ["BuildBot found nothing ready-made it can add for "
                         "this. Your bot's brain can still help with it in "
                         "chat."]},
        "not_reached": ["j3"],
        "similar": [],
        "sends": [{"phrase": kind, "source": "subject"}],
        "left_out": 7,
    }


BOX_ITEM = {"id": "box:locked:setup", "section": "box",
            "label": "Set up the locked box for outside code (one time)",
            "detail": "A separate, locked Linux space on this computer.",
            "size_bytes": 140000000, "outside_host": None,
            "default_on": False, "needs_yes": True,
            "apply": {"kind": "box_setup", "ref": "box"}}


def install_box_line(monkeypatch) -> None:
    """The locked box line, the way the box section provider adds it."""
    from puffpai.wizard import proposal
    monkeypatch.setattr(proposal, "_PROVIDERS", list(proposal._PROVIDERS))
    proposal.register_provider("test:box", lambda sctx: [dict(BOX_ITEM)])


def fake_parts_module(kind: str, calls: dict) -> types.ModuleType:
    mod = types.ModuleType("puffpai.wizard.find_parts")

    def plan_items(needs, *, goal, online, allow_send, cfg, on_line=None):
        calls.setdefault("plan_items", []).append(
            {"online": online, "allow_send": allow_send,
             "ticked": [j["id"] for j in needs["jobs"] if j.get("ticked")]})
        if on_line:
            on_line(f"GitHub: {kind} ... 2 fit")
        return plan_result(kind)

    def found_for(item_id):
        return None

    mod.plan_items = plan_items
    mod.found_for = found_for
    return mod


def fake_where_module() -> types.ModuleType:
    """The where-to-look registry's ``route`` (I-3), with two live places."""
    mod = types.ModuleType("puffpai.catalogs.where_to_look")

    def route(part_kind):
        rows = [{"id": "local_snapshot", "label": "Snapshot", "role": "snapshot",
                 "enabled": True},
                {"id": "github_repo_search", "label": "GitHub", "role": "live",
                 "enabled": True},
                {"id": "gitlab", "label": "GitLab", "role": "live",
                 "enabled": False}]
        if part_kind == "data-template":
            rows.append({"id": "commons", "label": "Wikimedia Commons",
                         "role": "live", "enabled": True})
        return rows

    mod.route = route
    return mod


def install_fakes(monkeypatch, kind: str, *, parts: bool = True,
                  where: bool = True, **options) -> dict:
    """Put the fake engine (and find_parts, and the where-to-look registry)
    where the wizard looks for them."""
    calls: dict = {}
    monkeypatch.setitem(sys.modules, "puffpai.catalogs.where_to_look",
                        fake_where_module() if where else None)
    monkeypatch.setitem(sys.modules, "puffpai.wizard.needs",
                        fake_needs_module(kind, calls, **options))
    if parts:
        monkeypatch.setitem(sys.modules, "puffpai.wizard.find_parts",
                            fake_parts_module(kind, calls))
    else:
        monkeypatch.setitem(sys.modules, "puffpai.wizard.find_parts", None)
    return calls


def no_engine(monkeypatch) -> None:
    """Make sure no engine is importable (``None`` in sys.modules raises
    ModuleNotFoundError with the module's own name, the "absent" case)."""
    monkeypatch.setitem(sys.modules, "puffpai.wizard.needs", None)
    monkeypatch.setitem(sys.modules, "puffpai.wizard.find_parts", None)


def pinned_machine(monkeypatch, tmp_path, *, internet: bool = True) -> dict:
    """A pinned machine: nothing downloaded, the internet check answered."""
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("COLUMNS", "100")
    get_config(_force_reload=True)
    census = hardware_census.run_census()
    census.setdefault("memory", {})["total_gb"] = 32.0
    monkeypatch.setattr(hardware_census, "run_census",
                        lambda *a, **k: copy.deepcopy(census))
    monkeypatch.setattr(setup_ops, "list_models", lambda *a, **k: [])
    monkeypatch.setattr(setup_ops, "ollama_survey", lambda *a, **k: [])
    monkeypatch.setattr(netcheck, "internet_reachable", lambda *a, **k: internet)
    monkeypatch.setattr(sources_suggest, "suggest", lambda *a, **k: [])
    calls = {"pull": [], "fetch": []}
    monkeypatch.setattr(brain_health, "pull_model", lambda model, *a, **k: (
        calls["pull"].append(model) or {"ok": False, "message": "no"}))
    monkeypatch.setattr(setup_ops, "fetch_source", lambda sid, *a, **k: (
        calls["fetch"].append(sid) or {"ok": False, "reason": "no"}))
    return calls


def answer_questions(goal: str) -> None:
    """Answer the goal and name questions the way the browser does."""
    from puffpai.wizard import service
    service.apply_skill("purpose:goal", {"goal": goal})
    service.apply_skill("purpose:identity", {"age": ""})


def wait_for(fn, what: str = "done", timeout: float = 20.0) -> dict:
    """Ask ``fn()`` until its answer says done."""
    end = time.time() + timeout
    while True:
        view = fn()
        if view.get(what):
            return view
        if time.time() > end:
            raise AssertionError(f"never {what}: {view}")
        time.sleep(0.05)


def looked(service, body: dict) -> dict:
    """POST the owner's choice and wait until looking has finished."""
    service.needs_choose(body)
    return _wait_parts(service)


def _wait_parts(service) -> dict:
    end = time.time() + 20
    while True:
        view = service.needs()
        if view["phase"] == "parts" and view["done"]:
            return view
        if time.time() > end:
            raise AssertionError(f"looking never finished: {view}")
        time.sleep(0.05)


# -- the schedule --------------------------------------------------------------

def test_the_needs_step_sits_between_the_name_and_the_plan():
    from puffpai.wizard import runner as wizard_runner

    entries = [step.entry for step in wizard_runner.SCHEDULE]
    at = entries.index("runner.needs")
    assert entries[at - 1] == "purpose.identity"
    assert entries[at + 1] == "runner.summary"
    step = wizard_runner.SCHEDULE[at]
    assert step.skill == "" and step.part == "needs"
    assert wizard_runner.RUNNER_ENTRY_TITLES["needs"] == "What your bot needs"


def test_the_titles_name_the_kind_once_it_is_known():
    from puffpai.wizard import service

    progress = service.NeedsProgress()
    assert progress.title("standard") == "Finding out what your bot needs"
    assert progress.lines("standard") == ["Reading what you wrote ..."]
    for goal_id, (_goal, kind) in GOALS.items():
        done = service.NeedsProgress.from_result(needs_result(kind, goal_id))
        assert done.title("standard") == f"Finding out what a {kind} bot needs"
        assert done.lines("standard") == [
            "Reading what you wrote ... done",
            "From your words: 3 things",
            f"Asking your bot's brain what {kind} bots usually need ... 1 more",
            "Kept private, never searched: sister, kids",
        ]


@pytest.mark.parametrize("prompt,missing,line", [
    ("stage-A", False, "Your bot's brain is slow on this computer, so BuildBot used "
                       "your words and a short list."),
    ("rules", False, "Your bot's brain did not answer in time, so this list comes "
                     "from your own words. You can add more below."),
    ("rules", True, "Your bot's brain is not downloaded yet (that happens when you "
                    "approve the plan), so this list comes from your own words."),
])
def test_a_slow_or_silent_brain_is_said_plainly(prompt, missing, line):
    from puffpai.wizard import service

    result = needs_result("recipe box")
    result["prompt"] = prompt
    if missing:
        result["model_missing"] = True
    result["private_left_out"] = []
    lines = service.NeedsProgress.from_result(result).lines("standard")
    assert lines[-1] == line
    assert not any(l.startswith("Kept private") for l in lines)


def test_lines_appear_as_the_jobs_arrive():
    from puffpai.wizard import service

    progress = service.NeedsProgress(kind="garden helper")
    job = needs_result("garden helper")["jobs"][0]
    progress.on_job(job)
    assert progress.lines("standard") == [
        "Reading what you wrote ... done", "From your words: 1 things",
        "Asking your bot's brain what garden helper bots usually need ..."]
    # The terminal prints only what is finished, so nothing is printed twice.
    assert progress.lines("standard", final_only=True) == [
        "Reading what you wrote ... done"]


# -- the flow ------------------------------------------------------------------

@pytest.mark.parametrize("goal_id", sorted(GOALS))
def test_flow_goes_goal_identity_needs_summary_with_the_engine(
        monkeypatch, tmp_path, goal_id):
    from puffpai.wizard import service

    goal, kind = GOALS[goal_id]
    pinned_machine(monkeypatch, tmp_path)
    calls = install_fakes(monkeypatch, kind)
    assert service.flow()["next"] == "first_look"
    service.first_look_choose()
    assert service.flow()["next"] == "goal"
    service.apply_skill("purpose:goal", {"goal": goal})
    assert service.flow()["next"] == "identity"
    service.apply_skill("purpose:identity", {"age": ""})
    assert service.flow()["next"] == "needs"

    view = wait_for(service.needs)
    assert view["phase"] == "needs"
    assert view["title"] == f"Finding out what a {kind} bot needs"
    assert service.flow()["next"] == "needs", "nothing moves on by itself"
    view = _wait_parts_after(service, {"look": "online"})
    assert view["title"] == "Looking for things your bot can use"
    assert service.flow()["next"] == "summary"
    assert len(calls["plan_items"]) == 1
    assert len(calls["run"]) == 1


def _wait_parts_after(service, body):
    service.needs_choose(body)
    return _wait_parts(service)


def test_without_the_engine_the_name_goes_straight_to_the_plan(
        monkeypatch, tmp_path):
    from puffpai.wizard import service

    pinned_machine(monkeypatch, tmp_path)
    no_engine(monkeypatch)
    answer_questions(GOALS["minecraft"][0])
    assert service.flow()["next"] == "summary"
    with pytest.raises(service.SkillNotConverted):
        service.needs()
    # "Looks good" settles the needs entry, so a finished browser build is
    # not offered back as a stopped one.
    service.approve({"on": {}})
    from puffpai.wizard import runner as wizard_runner
    run = wizard_runner.Runner(args=service._args(), cfg=get_config(),
                               non_interactive=True)
    assert run.load_state()
    assert run.entries["runner.needs"]["outcome"] == "skipped"


def test_a_broken_engine_fails_loudly_not_as_absent(monkeypatch):
    """I-10: a module that is there but broken is a bug, not "not in this
    build yet"."""
    from puffpai.wizard import service

    def broken(name, *args, **kwargs):
        if name == "puffpai.wizard.needs":
            raise ModuleNotFoundError("No module named 'numpy'", name="numpy")
        return real(name, *args, **kwargs)

    real = service.importlib.import_module
    monkeypatch.setattr(service.importlib, "import_module", broken)
    with pytest.raises(ModuleNotFoundError):
        service.needs_engine()


def test_an_engine_that_stops_leaves_the_plan_as_before(monkeypatch, tmp_path):
    from puffpai.wizard import service

    pinned_machine(monkeypatch, tmp_path)
    install_fakes(monkeypatch, "bird watching", fail=True)
    answer_questions("A bird watching log for my garden feeder")
    view = wait_for(service.needs)
    assert view["done"] and view["lines"][-1].startswith(
        "Something went wrong while finding out what your bot needs")
    assert service.flow()["next"] == "summary"


# -- the terminal ----------------------------------------------------------------

def _setup(monkeypatch, capsys, goal, script):
    return drive_in_process(
        ["setup", "--skip-ha", "--no-suggestions"],
        [("what kind of bot", goal), ("how old are you", ""), *script,
         ("choose a vault pin", "Telescope-Infinity-9042"),
         ("pick a pin", "Telescope-Infinity-9042"),
         ("type it again", "Telescope-Infinity-9042"),
         ("type the same pin again", "Telescope-Infinity-9042"),
         ("type the recovery code back", "skip")],
        monkeypatch=monkeypatch, capsys=capsys)


@pytest.mark.parametrize("goal_id", sorted(GOALS))
def test_the_terminal_asks_the_same_step_before_the_plan(
        monkeypatch, capsys, tmp_path, goal_id):
    goal, kind = GOALS[goal_id]
    pinned_machine(monkeypatch, tmp_path)
    calls = install_fakes(monkeypatch, kind)
    code, transcript, _asked = _setup(monkeypatch, capsys, goal, [
        ("press enter to start looking", ["2 4", ""])])
    assert code == 0
    needs_at = transcript.index(f"Finding out what a {kind} bot needs")
    plan_at = transcript.index("Here is the plan for")
    assert needs_at < plan_at
    for line in ("Reading what you wrote ... done", "From your words: 3 things",
                 f"Here is what a {kind} bot needs.", "[x] 1. ",
                 "you asked", f"most {kind} bots have this",
                 f"Searching with: {kind}", "Only these words go to those sites",
                 "Looking for things your bot can use",
                 f"GitHub: {kind} ... 2 fit",
                 "Checking what fits your jobs ... 3 kept, 7 left out",
                 "Stopped looking to keep this quick."):
        assert line in transcript, line
    # "2 4" unticked two jobs at once, and Enter started looking online.
    assert calls["choose"][0]["ticked"] == ["j1", "j3"]
    assert calls["plan_items"] == [{"online": True, "allow_send": True,
                                    "ticked": ["j1", "j3"]}]
    # The plan shows what a person would see: grouped by job.
    assert "What it will do" in transcript[plan_at:]


def test_the_terminal_here_answer_sends_nothing(monkeypatch, capsys, tmp_path):
    goal, kind = GOALS["wedding"]
    pinned_machine(monkeypatch, tmp_path)
    calls = install_fakes(monkeypatch, kind)
    code, _transcript, _ = _setup(monkeypatch, capsys, goal, [
        ("press enter to start looking", "here")])
    assert code == 0
    assert calls["plan_items"][0]["allow_send"] is False


def test_a_yes_run_never_looks_online(monkeypatch, capsys, tmp_path):
    goal, kind = GOALS["minecraft"]
    pinned_machine(monkeypatch, tmp_path)
    calls = install_fakes(monkeypatch, kind)
    code, _transcript, asked = drive_in_process(
        ["setup", "--yes", "--skip-ha", "--no-suggestions", "--goal", goal],
        [], monkeypatch=monkeypatch, capsys=capsys)
    assert code == 0
    assert calls["plan_items"][0]["allow_send"] is False
    assert not any("start looking" in q.lower() for q in asked)
