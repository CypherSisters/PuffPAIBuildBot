"""Purpose as a card: propose, apply, verify -- and what it must still write.

The load-bearing tests are the two equivalence ones. The design allows Purpose's
owner-visible wording to change in Phase 2; it does not allow what it writes to
change, so today's two step functions (``cli._setup_name_step`` and
``cli._setup_interview_step``) are run as the control -- non-interactively with
flags, and interactively with scripted answers -- and the ``persona`` section
this skill's ``apply`` writes is compared against them field by field.

Each run gets its own data dir: a scaffold writes folders, and a second scaffold
of the same id into the same dir is refused (correctly), which would make the
control and the subject disagree for a reason that is not the port.

Run:
    pytest tests/test_wizard_purpose.py
"""
from __future__ import annotations

import argparse
import builtins
import json
import os

import pytest

from puffpai import cli, persona_scaffold, setup_ops
from puffpai.config import get_config
from puffpai.wizard import cards, loader, runner as wizard_runner

GOAL = "a garden and greenhouse assistant for plantings and harvest"
SPACE_GOAL = ("knows everything about space, space facts, current space "
              "events, astronomy, Earth weather events")


def _args(**kw):
    base = dict(yes=True, name=None, goal=None, scaffold=False, lists=None,
                add_surface=None, drop_surface=None)
    base.update(kw)
    return argparse.Namespace(**base)


def _use(path) -> None:
    """Point this process's data and state dirs at *path*, freshly."""
    os.makedirs(path, exist_ok=True)
    os.environ["PUFFPAI_DATA_DIR"] = str(path)
    os.environ["PUFFPAI_STATE_DIR"] = str(path)
    get_config(_force_reload=True)


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """One temp data dir per test, no Ollama, no leftover stdin state."""
    monkeypatch.setattr(setup_ops, "list_models", lambda *a, **k: [])
    monkeypatch.setattr(cli, "_stdin_exhausted", False)
    prev = {k: os.environ.get(k) for k in ("PUFFPAI_DATA_DIR", "PUFFPAI_STATE_DIR")}
    _use(tmp_path / "run")
    yield tmp_path
    for key, value in prev.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    get_config(_force_reload=True)


def _module():
    packages, refusals = loader.load_builtin_packages()
    assert refusals == []
    return loader.load_module(packages["purpose"]), packages["purpose"]


def _ctx(package, *, args=None, non_interactive=True, sections=None, shared=None,
         part="identity"):
    return wizard_runner.RunContext(
        args=args or _args(), cfg=get_config(), non_interactive=non_interactive,
        shared=shared or {}, sections=sections if sections is not None else {},
        package=package, part=part)


def _typed(monkeypatch, answers: list[str]) -> None:
    """Script the keyboard for the control run."""
    queue = list(answers)
    monkeypatch.setattr(cli, "_stdin_exhausted", False)
    monkeypatch.setattr(builtins, "input",
                        lambda *a, **k: queue.pop(0) if queue else "")


def _settled(value, path):
    """A copy with this run's data dir taken out of every path it wrote."""
    if isinstance(value, dict):
        return {k: _settled(v, path) for k, v in value.items()}
    if isinstance(value, list):
        return [_settled(v, path) for v in value]
    if isinstance(value, str):
        return value.replace(str(path), "<data>").replace("\\", "/")
    return value


# -- propose ------------------------------------------------------------------

def test_propose_returns_three_cards_that_validate_and_write_nothing(fresh):
    """Goal first, then the name and the age, then the place."""
    module, package = _module()
    assert module.PARTS == ("goal", "identity", "place")
    args = _args(name="GardenBot", goal=GOAL, scaffold=True)
    ctx = _ctx(package, args=args, part="goal")
    goal = cards.validate_card(module.propose(ctx)["card"])
    identity = cards.validate_card(module.propose(
        _ctx(package, args=args, part="identity"))["card"])
    place = cards.validate_card(module.propose(
        _ctx(package, args=args, part="place"))["card"])

    for card in (goal, identity, place):
        assert card["skill"] == "purpose" and card["section"] == "persona"
        assert card["estimate"]["bytes"] == 0
    assert goal["part"] == "goal" and identity["part"] == "identity"
    assert place["part"] == "place"
    assert {f["id"] for f in goal["fields"]} == {"goal"}
    assert {f["id"] for f in identity["fields"]} == {"name", "age"}
    assert {f["id"] for f in place["fields"]} >= {"scaffold", "id", "lists"}
    assert ctx._sections == {}, "propose wrote a section"
    assert persona_scaffold.scaffolded_ids() == [], "propose scaffolded"


def test_the_place_card_names_the_id_from_the_name_that_was_applied(fresh):
    """The defect: the id was worked out before anybody had typed.

    Identity is applied first with a real name, and only then is the place card
    proposed -- so the default id is the bot's own word, not the fallback every
    one-card run offered.
    """
    module, package = _module()
    sections: dict = {}
    first = _ctx(package, args=_args(), sections=sections)
    first.proposal = module.propose(first)
    sections["persona"] = module.apply(
        first, {"name": "Infinity", "goal": SPACE_GOAL})["data"]

    card = cards.validate_card(module.propose(
        _ctx(package, args=_args(), sections=sections, part="place"))["card"])
    field = {f["id"]: f for f in card["fields"]}["id"]
    assert field["kind"] == "text" and field["pattern"]
    assert field["current"] == "infinity"
    assert field["current"] == persona_scaffold.suggest_id("Infinity", SPACE_GOAL)
    assert field["current"] != persona_scaffold.FALLBACK_ID


def test_the_identity_card_reproposes_from_what_was_applied(fresh):
    """The propose rule: an applied answer is what the card shows next time."""
    module, package = _module()
    sections: dict = {}
    ctx = _ctx(package, args=_args(), sections=sections, part="goal")
    ctx.proposal = module.propose(ctx)
    assert ctx.proposal["card"]["part"] == "goal"
    assert ctx.proposal["card"]["decided"] == ""
    sections["persona"] = module.apply(ctx, {"goal": SPACE_GOAL})["data"]
    named = _ctx(package, args=_args(), sections=sections, part="identity")
    named.proposal = module.propose(named)
    sections["persona"].update(module.apply(named, {"name": "Infinity"})["data"])

    goal = cards.validate_card(module.propose(
        _ctx(package, args=_args(), sections=sections, part="goal"))["card"])
    again = cards.validate_card(module.propose(
        _ctx(package, args=_args(), sections=sections, part="identity"))["card"])
    assert {f["id"]: f for f in goal["fields"]}["goal"]["current"] == SPACE_GOAL
    assert {f["id"]: f for f in again["fields"]}["name"]["current"] == "Infinity"
    assert again["decided"] == "Infinity"


def test_a_card_with_no_goal_says_it_needs_you(fresh):
    module, package = _module()
    card = cards.validate_card(module.propose(_ctx(package, part="goal"))["card"])
    assert card["part"] == "goal"
    assert card["status"] == "needs_you"
    assert {f["id"] for f in card["fields"]} == {"goal"}


def test_a_goal_that_matches_everything_still_draws_one_screen(fresh):
    """A card is one screen (``cards.MAX_FIELDS``), whatever the goal matched."""
    module, package = _module()
    greedy = ("telescope astronomy space weather smart home lights cameras "
              "fitness medical recipes calendar trivia music movies garden "
              "harvest plantings security news books maps")
    card = cards.validate_card(module.propose(
        _ctx(package, args=_args(name="GardenBot", goal=greedy)))["card"])
    assert len(card["fields"]) <= cards.MAX_FIELDS


def test_the_card_carries_no_secret_and_nothing_a_scrub_would_drop(fresh):
    module, package = _module()
    card = cards.validate_card(module.propose(
        _ctx(package, args=_args(name="GardenBot", goal=GOAL, scaffold=True),
             part="place"))["card"])
    assert [f for f in card["fields"] if f["kind"] == "secret_entry"] == []
    assert wizard_runner.json_safe(card) == card
    assert json.dumps(card)


# -- apply: the two old steps are the control ---------------------------------

def _new_run(path, *, args, answers, non_interactive=True):
    """Both of Purpose's cards, in order, into one section -- as the runner does."""
    _use(path)
    module, package = _module()
    sections: dict = {}
    for part in module.PARTS:
        ctx = _ctx(package, args=args, non_interactive=non_interactive,
                   sections=sections, part=part)
        ctx.proposal = module.propose(ctx)
        asked = {f["id"] for f in ctx.proposal["card"]["fields"]}
        data = module.apply(ctx, {k: v for k, v in (answers or {}).items()
                                  if k in asked})["data"]
        sections["persona"] = dict(sections.get("persona") or {}, **data)
    return sections["persona"]


def _old_run(path, *, args, non_interactive=True, typed=None, monkeypatch=None):
    _use(path)
    if typed is not None:
        _typed(monkeypatch, typed)
    name = cli._setup_name_step(args, non_interactive=non_interactive)
    interview = cli._setup_interview_step(args, {},
                                          non_interactive=non_interactive,
                                          bot_name=name)
    return {"bot_name": name, "interview": interview}


def test_apply_writes_what_the_two_old_steps_wrote_without_a_keyboard(fresh, capsys):
    args = _args(name="GardenBot", goal=GOAL, scaffold=True)
    new = _new_run(fresh / "new", args=args, answers={})
    old = _old_run(fresh / "old", args=args)
    capsys.readouterr()

    assert _settled(new["bot_name"], fresh / "new") == _settled(
        old["bot_name"], fresh / "old")
    assert _settled(new["interview"], fresh / "new") == _settled(
        old["interview"], fresh / "old")
    assert new["persona"]["app"] == old["interview"]["persona"]["app"]


def test_apply_writes_what_the_two_old_steps_wrote_at_a_keyboard(fresh, capsys,
                                                                 monkeypatch):
    lists = "plantings, harvest"
    answers = {"name": "GardenBot", "goal": GOAL, "scaffold": True,
               "id": "greenhouse", "lists": lists}
    new = _new_run(fresh / "new", args=_args(yes=False), answers=answers,
                   non_interactive=False)
    old = _old_run(fresh / "old", args=_args(yes=False), non_interactive=False,
                   monkeypatch=monkeypatch,
                   typed=["GardenBot", GOAL, "", "y", "greenhouse", lists])
    capsys.readouterr()

    assert old["bot_name"] == "GardenBot"
    assert old["interview"]["persona"]["app"] == "greenhouse"
    assert _settled(new["bot_name"], fresh / "new") == _settled(
        old["bot_name"], fresh / "old")
    assert _settled(new["interview"], fresh / "new") == _settled(
        old["interview"], fresh / "old")


def test_an_empty_goal_records_an_empty_interview_exactly_as_today(fresh, capsys):
    new = _new_run(fresh / "new", args=_args(), answers={})
    old = _old_run(fresh / "old", args=_args())
    capsys.readouterr()
    assert new["interview"] == old["interview"]
    assert new["interview"] == {"goal": "", "selected": [], "proposal": None,
                                "persona": None}


def test_the_name_lands_where_both_wizards_read_it(fresh):
    _new_run(fresh / "new", args=_args(goal=GOAL), answers={"name": "GardenBot"})
    from puffpai import bot_identity
    assert bot_identity.bot_name("fallback") == "GardenBot"


def test_the_id_that_was_shown_is_the_id_that_is_written(fresh):
    """The id is shown and editable, never silently assigned.

    The owner renames the bot on the card and leaves the id alone. The id on
    the card is what gets written -- it is not recomputed from the new name
    behind them, which would make the card a guess rather than a record.
    """
    module, package = _module()
    sections: dict = {}
    identity = _ctx(package, args=_args(goal=GOAL, scaffold=True),
                    sections=sections)
    identity.proposal = module.propose(identity)
    sections["persona"] = module.apply(identity, {"name": "GardenBot"})["data"]

    place = _ctx(package, args=_args(goal=GOAL, scaffold=True),
                 sections=sections, part="place")
    place.proposal = module.propose(place)
    shown = place.proposal["persona_id"]
    data = module.apply(place, {})["data"]
    assert data["persona"]["app"] == shown
    assert data["persona"]["name"] == "GardenBot"


# -- apply: the refusals ------------------------------------------------------

@pytest.mark.parametrize("bad", ["apps", "vault", "settings"])
def test_a_reserved_id_is_refused_with_the_plain_message(fresh, bad):
    module, package = _module()
    args = _args(name="GardenBot", goal=GOAL, scaffold=True)
    sections: dict = {}
    identity = _ctx(package, args=args, sections=sections)
    identity.proposal = module.propose(identity)
    sections["persona"] = module.apply(identity, {})["data"]

    ctx = _ctx(package, args=args, sections=sections, part="place")
    ctx.proposal = module.propose(ctx)
    result = module.apply(ctx, {"id": bad})

    assert result["data"]["interview"]["persona"] is None
    # The goal is still recorded -- nothing was scaffolded, and what the owner
    # said this build is for is not lost with the folder that was refused.
    assert result["data"]["persona"]["scaffolded"] is False
    assert result["data"]["persona"]["goal"] == GOAL
    gap = result["gaps"][0]
    assert gap["skill"] == "purpose"
    assert bad in gap["why"] and "already uses" in gap["why"]
    assert gap["fix"] == {"kind": "card", "id": "purpose"}
    assert persona_scaffold.scaffolded_ids() == []


def test_a_name_this_build_refuses_is_recorded_as_unnamed(fresh):
    module, package = _module()
    ctx = _ctx(package, args=_args(goal=GOAL))
    ctx.proposal = module.propose(ctx)
    result = module.apply(ctx, {"name": "\u200b\u200b"})
    assert result["data"]["bot_name"] == ""
    assert result["gaps"][0]["what"].startswith("The name you typed")


def test_the_scaffold_is_not_made_when_the_owner_says_no(fresh):
    module, package = _module()
    args = _args(name="GardenBot", goal=GOAL, scaffold=True)
    sections: dict = {}
    identity = _ctx(package, args=args, sections=sections)
    identity.proposal = module.propose(identity)
    sections["persona"] = module.apply(identity, {})["data"]

    ctx = _ctx(package, args=args, sections=sections, part="place")
    ctx.proposal = module.propose(ctx)
    result = module.apply(ctx, {"scaffold": False})
    assert result["data"]["interview"]["persona"] is None
    assert persona_scaffold.scaffolded_ids() == []
    # Said, with the command that changes it -- what the legacy path printed.
    gap = result["gaps"][0]
    assert gap["fix"] == {"kind": "command", "id": "setup.scaffold", "args": {}}
    from puffpai.wizard import fixes
    assert fixes.render(gap["fix"]) == "puffpai setup --own-place"


def test_the_goal_is_recorded_where_the_model_reads_it_without_a_scaffold(fresh):
    """The HIGH defect: --yes never scaffolds, so the goal reached nothing.

    ``build_context.collect_goal`` reads ``persona.goal``; a build that records
    the goal only when a folder was made tells its model the owner wrote
    nothing down.
    """
    module, package = _module()
    data = _new_run(fresh / "nogoalplace",
                    args=_args(name="Infinity", goal=SPACE_GOAL, scaffold=False),
                    answers={})
    assert data["interview"]["persona"] is None
    assert data["persona"] == {
        "scaffolded": False, "name": "Infinity", "goal": SPACE_GOAL,
        "note": data["persona"]["note"]}
    assert "--scaffold" in data["persona"]["note"]


# -- verify -------------------------------------------------------------------

def _applied_sections(module, package, path, **kw):
    """Both cards applied into one section dict, for the verify tests."""
    section = _new_run(path, args=_args(**kw), answers={})
    return {"persona": section}


def test_verify_observes_the_folders_on_disk(fresh):
    module, package = _module()
    sections = _applied_sections(module, package, fresh / "verify",
                                 name="GardenBot", goal=GOAL, scaffold=True)
    ctx = _ctx(package, args=_args(name="GardenBot", goal=GOAL, scaffold=True),
               sections=sections, part="place")
    verdict = module.verify(ctx)
    assert verdict["status"] == "pass"
    assert "on disk" in verdict["evidence"]


def test_a_persona_whose_folders_are_gone_is_a_gap_and_not_an_exception(fresh):
    module, package = _module()
    sections = {"persona": {"persona": {"scaffolded": True, "app": "greenhouse",
                                        "app_manifest": "Q:\\nowhere\\app.json",
                                        "pack_manifest": "Q:\\nowhere\\pack.json"}}}
    verdict = module.verify(_ctx(package, sections=sections))
    assert verdict["status"] == "gap"
    assert verdict["gap"]["fix"]["id"] == "setup.rerun"


def test_a_build_with_no_persona_verifies_and_says_so(fresh):
    module, package = _module()
    verdict = module.verify(_ctx(package, sections={"persona": {}}))
    assert verdict["status"] == "pass"
    assert "none to find" in verdict["evidence"]


def test_a_trimmed_build_missing_the_persona_surface_is_a_gap(fresh, monkeypatch):
    module, package = _module()
    sections = _applied_sections(module, package, fresh / "trimmed",
                                 name="GardenBot", goal=GOAL, scaffold=True)
    ctx = _ctx(package, args=_args(name="GardenBot", goal=GOAL, scaffold=True),
               sections=sections, part="place")

    from puffpai import build_manifest
    monkeypatch.setattr(build_manifest, "read_manifest",
                        lambda *a, **k: {"surfaces": {"allowlist": ["house.lights"],
                                                      "owner_edited": True}})
    verdict = module.verify(ctx)
    assert verdict["status"] == "gap"
    assert "allowlist" in verdict["evidence"]


# -- through the runner -------------------------------------------------------

def test_the_runner_walks_both_cards_and_records_what_it_showed(fresh):
    def answers(card, **kw):
        return {"id": "greenhouse"} if card["part"] == "place" else {}

    run = wizard_runner.Runner(
        args=_args(name="GardenBot", goal=GOAL, scaffold=True),
        cfg=get_config(), non_interactive=True,
        schedule=(wizard_runner.Step("purpose.goal", "purpose",
                                     f"{wizard_runner.V2_PART}:goal"),
                  wizard_runner.Step("purpose.identity", "purpose",
                                     f"{wizard_runner.V2_PART}:identity"),
                  wizard_runner.Step("purpose.place", "purpose",
                                     f"{wizard_runner.V2_PART}:place")),
        renderer=answers)
    run.run()

    assert run.sections["persona"]["bot_name"] == "GardenBot"
    assert run.sections["persona"]["persona"]["app"] == "greenhouse"
    goal, identity, place = run.log[0], run.log[1], run.log[-1]
    assert [row["outcome"] for row in (goal, identity, place)] == [
        "done", "done", "done"]
    assert goal["shown"]["title"] == "Your bot"
    assert identity["shown"]["title"] == "Its name"
    assert place["shown"]["title"] == "A place of its own"
    assert place["changed"] == {"id": "greenhouse"}
    assert place["verified"]["status"] == "pass"
    assert run.stale[-2:] == ["prove", "handoff"]
    assert "surfaces" in run.stale
