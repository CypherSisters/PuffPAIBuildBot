"""Resume, on its own screen: what a continued run may skip, and what it may not.

Resuming was deliberately not offered, because a resumed run
would skip the vault ceremony on the strength of a file saying it happened.
That is still the rule this file is about -- what changed is that it is now
enforced rather than avoided:

* a **non-blocking** card the record calls done is skipped, and that is the
  whole point of continuing;
* a **blocking** card (Brain, Secure -- the two packages whose manifests say
  ``blocking``) is verified against the machine again before it is skipped, and
  asked again when verify does not pass;
* the **vault** is checked on disk on top of that, because Secure's verify only
  looks at the vault when the recorded block claims one was configured -- so a
  record that lies about the vault would otherwise verify clean;
* ``--yes`` never resumes. Nobody is at the keyboard to be offered the choice.

Run:
    pytest tests/test_wizard_resume.py
"""
from __future__ import annotations

import argparse
import json
import os
import types

import pytest

from puffpai.wizard import loader, render_cli, service
from puffpai.wizard import runner as wizard_runner
from puffpai.wizard.runner import Runner, Step


@pytest.fixture
def cfg(tmp_path):
    return types.SimpleNamespace(CONFIG_DIR=str(tmp_path / "config"))


def _args(**kw):
    return argparse.Namespace(yes=False, **kw)


def _card(skill: str, section: str, part: str = "") -> dict:
    return {"skill": skill, "section": section, "part": part,
            "title": f"{skill} card", "decided": "as proposed",
            "status": "ready",
            "estimate": {"bytes": 0, "seconds": 0, "basis": "measured"}}


def _skill(skill: str, section: str, ran: list, *, part: str = "",
           verdict: str = "pass"):
    """A converted package whose every call is recorded in *ran*."""

    def propose(ctx):
        ran.append(f"propose:{skill}{':' + part if part else ''}")
        return {"card": _card(skill, section, part)}

    def apply(ctx, answers):
        ran.append(f"apply:{skill}{':' + part if part else ''}")
        return {"data": {"ran": True}}

    def verify(ctx):
        ran.append(f"verify:{skill}{':' + part if part else ''}")
        return {"status": verdict, "evidence": "stubbed",
                "gap": {"skill": skill, "what": "w", "why": "y", "impact": "i",
                        "fix": {"kind": "command", "id": "setup.rerun",
                                "args": {}}}}

    return {"propose": propose, "apply": apply, "verify": verify}


#: discover and surfaces do not block; brain and secure do (their manifests).
SCHEDULE = (
    Step("discover.card", "discover", "card"),
    Step("brain.card", "brain", "card"),
    Step("secure.vault", "secure", "card:vault"),
    Step("surfaces.card", "surfaces", "card"),
)


def _stub(monkeypatch, modules: dict[str, dict]):
    def fake_load_module(package):
        return types.SimpleNamespace(**(modules.get(package.id) or {}))

    monkeypatch.setattr(loader, "load_module", fake_load_module)


def _runner(cfg, monkeypatch, modules, *, args=None) -> Runner:
    _stub(monkeypatch, modules)
    return Runner(args=args or _args(), cfg=cfg, non_interactive=True,
                  schedule=SCHEDULE, renderer=lambda card, **kw: {})


def _first_run(cfg, monkeypatch, ran, *, stop_after: str = "brain.card"):
    """Walk the schedule far enough to leave a stopped record on disk."""
    modules = {"discover": _skill("discover", "machine", ran),
               "brain": _skill("brain", "brain", ran),
               "secure": _skill("secure", "posture", ran, part="vault"),
               "surfaces": _skill("surfaces", "surfaces", ran)}
    run = _runner(cfg, monkeypatch, modules)
    for step in SCHEDULE:
        run.run_step(step)
        if step.entry == stop_after:
            break
    return run


# -- what continuing skips, and what it does not ------------------------------

def test_continuing_skips_a_done_non_blocking_card(cfg, monkeypatch):
    first: list[str] = []
    _first_run(cfg, monkeypatch, first, stop_after="discover.card")
    assert "apply:discover" in first

    second: list[str] = []
    modules = {"discover": _skill("discover", "machine", second),
               "brain": _skill("brain", "brain", second),
               "secure": _skill("secure", "posture", second, part="vault"),
               "surfaces": _skill("surfaces", "surfaces", second)}
    stopped, why = wizard_runner.find_stopped_run(args=_args(), cfg=cfg,
                                                   schedule=SCHEDULE)
    assert why == "" and stopped is not None
    fresh = _runner(cfg, monkeypatch, modules)
    fresh.adopt(stopped)
    fresh.reverify_blocking()
    fresh.run()

    # Discover does not block, it was done, and it is not asked again at all.
    assert "apply:discover" not in second
    assert "propose:discover" not in second
    assert "apply:brain" in second and "apply:surfaces" in second


def test_continuing_re_verifies_a_blocking_card_and_keeps_it_when_it_holds(
        cfg, monkeypatch):
    first: list[str] = []
    _first_run(cfg, monkeypatch, first)  # through brain.card

    second: list[str] = []
    modules = {"discover": _skill("discover", "machine", second),
               "brain": _skill("brain", "brain", second, verdict="pass"),
               "secure": _skill("secure", "posture", second, part="vault"),
               "surfaces": _skill("surfaces", "surfaces", second)}
    stopped, _why = wizard_runner.find_stopped_run(args=_args(), cfg=cfg,
                                                   schedule=SCHEDULE)
    fresh = _runner(cfg, monkeypatch, modules)
    fresh.adopt(stopped)
    asked_again = fresh.reverify_blocking()
    fresh.run()

    assert asked_again == [], "a blocking card whose verify passes is kept"
    assert "verify:brain" in second, "it was re-verified, not simply believed"
    assert "apply:brain" not in second, "and then not asked again"


def test_continuing_asks_a_blocking_card_again_when_verify_does_not_pass(
        cfg, monkeypatch):
    first: list[str] = []
    _first_run(cfg, monkeypatch, first)

    second: list[str] = []
    modules = {"discover": _skill("discover", "machine", second),
               "brain": _skill("brain", "brain", second, verdict="gap"),
               "secure": _skill("secure", "posture", second, part="vault"),
               "surfaces": _skill("surfaces", "surfaces", second)}
    stopped, _why = wizard_runner.find_stopped_run(args=_args(), cfg=cfg,
                                                   schedule=SCHEDULE)
    fresh = _runner(cfg, monkeypatch, modules)
    fresh.adopt(stopped)
    asked_again = fresh.reverify_blocking()
    fresh.run()

    assert asked_again == ["brain"]
    assert "apply:brain" in second, "the model is not there, so it is asked again"


def test_a_verify_that_raises_is_a_re_ask_not_a_crash(cfg, monkeypatch):
    first: list[str] = []
    _first_run(cfg, monkeypatch, first)

    second: list[str] = []
    modules = {"discover": _skill("discover", "machine", second),
               "brain": _skill("brain", "brain", second),
               "secure": _skill("secure", "posture", second, part="vault"),
               "surfaces": _skill("surfaces", "surfaces", second)}

    def explodes(ctx):
        raise RuntimeError("the Ollama went away")

    modules["brain"]["verify"] = explodes
    stopped, _why = wizard_runner.find_stopped_run(args=_args(), cfg=cfg,
                                                   schedule=SCHEDULE)
    fresh = _runner(cfg, monkeypatch, modules)
    fresh.adopt(stopped)
    assert fresh.reverify_blocking() == ["brain"]


def test_the_vault_is_asked_again_when_there_is_no_vault_on_disk(cfg, monkeypatch):
    """The rule the whole screen exists for.

    Secure's verify is told to pass. The vault is still asked again, because
    there is no initialised vault on this machine and a file saying there is
    one is not a vault.
    """
    first: list[str] = []
    _first_run(cfg, monkeypatch, first, stop_after="secure.vault")

    second: list[str] = []
    modules = {"discover": _skill("discover", "machine", second),
               "brain": _skill("brain", "brain", second, verdict="pass"),
               "secure": _skill("secure", "posture", second, part="vault",
                                verdict="pass"),
               "surfaces": _skill("surfaces", "surfaces", second)}
    monkeypatch.setattr(wizard_runner, "vault_on_disk", lambda cfg_: False)
    stopped, _why = wizard_runner.find_stopped_run(args=_args(), cfg=cfg,
                                                   schedule=SCHEDULE)
    fresh = _runner(cfg, monkeypatch, modules)
    fresh.adopt(stopped)
    asked_again = fresh.reverify_blocking()
    after_reverify = list(second)
    fresh.run()

    assert asked_again == ["secure:vault"]
    assert "apply:secure:vault" in second
    # And the vault's verify was never even consulted during the re-verify:
    # the disk answered first.
    assert "verify:secure:vault" not in after_reverify


def test_a_vault_that_is_on_disk_is_left_alone(cfg, monkeypatch):
    first: list[str] = []
    _first_run(cfg, monkeypatch, first, stop_after="secure.vault")

    second: list[str] = []
    modules = {"discover": _skill("discover", "machine", second),
               "brain": _skill("brain", "brain", second),
               "secure": _skill("secure", "posture", second, part="vault"),
               "surfaces": _skill("surfaces", "surfaces", second)}
    monkeypatch.setattr(wizard_runner, "vault_on_disk", lambda cfg_: True)
    stopped, _why = wizard_runner.find_stopped_run(args=_args(), cfg=cfg,
                                                   schedule=SCHEDULE)
    fresh = _runner(cfg, monkeypatch, modules)
    fresh.adopt(stopped)
    assert fresh.reverify_blocking() == []
    fresh.run()
    assert "apply:secure:vault" not in second


# -- what is offered at all ---------------------------------------------------

def test_a_finished_run_and_an_old_one_are_not_offered(cfg, monkeypatch):
    ran: list[str] = []
    modules = {skill: _skill(skill, section, ran, part=part)
               for skill, section, part in (("discover", "machine", ""),
                                            ("brain", "brain", ""),
                                            ("secure", "posture", "vault"),
                                            ("surfaces", "surfaces", ""))}
    run = _runner(cfg, monkeypatch, modules)
    run.run()
    stopped, why = wizard_runner.find_stopped_run(args=_args(), cfg=cfg,
                                                   schedule=SCHEDULE)
    assert stopped is None and why == wizard_runner.RESUME_FINISHED

    # Now age it past the ceiling.
    path = run.state_path()
    with open(path, encoding="utf-8") as handle:
        state = json.load(handle)
    state["entries"].pop("surfaces.card")
    state["started_at"] = state["started_at"] - wizard_runner.RESUME_MAX_AGE_S - 60
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(state, handle)
    stopped, why = wizard_runner.find_stopped_run(args=_args(), cfg=cfg,
                                                   schedule=SCHEDULE)
    assert stopped is None and why == wizard_runner.RESUME_TOO_OLD


def test_a_record_with_nothing_finished_is_not_something_to_continue(cfg,
                                                                     monkeypatch):
    _stub(monkeypatch, {})
    os.makedirs(cfg.CONFIG_DIR, exist_ok=True)
    with open(os.path.join(cfg.CONFIG_DIR, "wizard_run.json"), "w",
              encoding="utf-8") as handle:
        json.dump({"schema": wizard_runner.STATE_SCHEMA, "started_at": 1e9,
                   "entries": {"discover.card": {"outcome": "failed"}},
                   "log": [], "gaps": [], "sections": {}, "shared": {}}, handle)
    stopped, why = wizard_runner.find_stopped_run(args=_args(), cfg=cfg,
                                                   schedule=SCHEDULE)
    assert stopped is None and why == wizard_runner.RESUME_NOTHING_DONE


def test_a_corrupted_record_is_named_as_unreadable(cfg, monkeypatch):
    _stub(monkeypatch, {})
    os.makedirs(cfg.CONFIG_DIR, exist_ok=True)
    path = os.path.join(cfg.CONFIG_DIR, "wizard_run.json")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{not json at all")
    stopped, why = wizard_runner.find_stopped_run(args=_args(), cfg=cfg,
                                                   schedule=SCHEDULE)
    assert stopped is None and why == wizard_runner.RESUME_UNREADABLE


def test_starting_over_archives_the_record_rather_than_deleting_it(cfg,
                                                                   monkeypatch):
    first: list[str] = []
    run = _first_run(cfg, monkeypatch, first)
    path = run.state_path()
    moved = wizard_runner.archive_state(path)
    assert moved and os.path.exists(moved) and not os.path.exists(path)
    with open(moved, encoding="utf-8") as handle:
        assert json.load(handle)["schema"] == wizard_runner.STATE_SCHEMA


# -- the screen ---------------------------------------------------------------

def test_the_screen_says_when_it_stopped_where_and_what_was_decided(cfg,
                                                                    monkeypatch):
    first: list[str] = []
    _first_run(cfg, monkeypatch, first)
    stopped, _why = wizard_runner.find_stopped_run(args=_args(), cfg=cfg,
                                                   schedule=SCHEDULE)
    summary = stopped.resume_summary()

    assert summary["stopped_card"] == "secure:vault"
    assert [row["card"] for row in summary["decided"]] == ["discover", "brain"]
    lines = render_cli.resume_lines(summary)
    text = "\n".join(lines)
    assert text.strip().startswith("A setup from ")
    assert "Pick up where it stopped, or start over?" in text
    assert "Already answered:" in text
    assert "discover card" in text and "brain card" in text


@pytest.mark.parametrize("typed,expected", [
    ("continue", "continue"), ("c", "continue"), ("CONTINUE", "continue"),
    ("over", "restart"), ("", "restart"), ("nonsense", "restart"),
    (None, "restart"),
])
def test_the_screen_falls_back_to_starting_over(typed, expected):
    summary = {"age_s": 60, "stopped_title": "the vault", "decided": []}
    choice = render_cli.resume_screen(summary, ask=lambda prompt: typed,
                                      out=lambda line: None)
    assert choice == expected


def test_how_long_ago_says_it_in_words():
    assert render_cli.how_long_ago(30) == "a moment ago"
    assert render_cli.how_long_ago(60) == "1 minute ago"
    assert render_cli.how_long_ago(3 * 3600) == "3 hours ago"
    assert render_cli.how_long_ago(2 * 86400) == "2 days ago"


# -- the CLI ------------------------------------------------------------------

def test_yes_never_resumes_and_says_a_stopped_run_was_set_aside(cfg, monkeypatch,
                                                               capsys):
    from puffpai import cli

    first: list[str] = []
    run = _first_run(cfg, monkeypatch, first)
    path = run.state_path()

    fresh = _runner(cfg, monkeypatch, {}, args=argparse.Namespace(yes=True))
    cli._offer_resume(argparse.Namespace(yes=True), fresh)
    out = capsys.readouterr().out

    assert cli.RESUME_SET_ASIDE_LINE in out
    assert fresh.entries == {}, "a --yes run starts over"
    assert not os.path.exists(path), "the stopped record was set aside"


def test_a_corrupted_record_starts_over_with_a_plain_line(cfg, monkeypatch,
                                                          capsys):
    from puffpai import cli

    _stub(monkeypatch, {})
    os.makedirs(cfg.CONFIG_DIR, exist_ok=True)
    path = os.path.join(cfg.CONFIG_DIR, "wizard_run.json")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{not json at all")

    fresh = _runner(cfg, monkeypatch, {})
    cli._offer_resume(_args(), fresh)
    out = capsys.readouterr().out

    assert cli.RESUME_UNREADABLE_LINE in out
    assert "Traceback" not in out
    assert fresh.entries == {}
    assert not os.path.exists(path)


def test_choosing_continue_on_the_console_adopts_the_record(cfg, monkeypatch,
                                                            capsys):
    from puffpai import cli

    first: list[str] = []
    _first_run(cfg, monkeypatch, first)
    modules = {"discover": _skill("discover", "machine", []),
               "brain": _skill("brain", "brain", []),
               "secure": _skill("secure", "posture", [], part="vault"),
               "surfaces": _skill("surfaces", "surfaces", [])}
    monkeypatch.setattr(wizard_runner, "vault_on_disk", lambda cfg_: True)
    monkeypatch.setattr(render_cli, "resume_screen",
                        lambda summary, **kw: "continue")
    fresh = _runner(cfg, monkeypatch, modules)
    cli._offer_resume(_args(), fresh)

    assert "discover.card" in fresh.entries and "brain.card" in fresh.entries
    assert "Picking up where it stopped" in capsys.readouterr().out


def test_choosing_start_over_on_the_console_archives_and_forgets(cfg, monkeypatch,
                                                                 capsys):
    from puffpai import cli

    first: list[str] = []
    run = _first_run(cfg, monkeypatch, first)
    monkeypatch.setattr(render_cli, "resume_screen",
                        lambda summary, **kw: "restart")
    fresh = _runner(cfg, monkeypatch, {})
    cli._offer_resume(_args(), fresh)

    assert fresh.entries == {}
    assert not os.path.exists(run.state_path())
    assert "Starting over" in capsys.readouterr().out


# -- the service and the route ------------------------------------------------

def test_the_state_route_carries_the_resume_offer(cfg, monkeypatch):
    first: list[str] = []
    _first_run(cfg, monkeypatch, first)
    monkeypatch.setattr(service, "_cfg", lambda: cfg)

    # The service walks the real schedule, not this file's four-step one, so
    # what it says stopped is the real wizard's first unfinished entry. What it
    # must get right either way is that there is an offer and that it names the
    # cards this record really settled.
    offer = service.resume_offer()
    assert offer["offer"] is True
    assert offer["stopped_card"], "the offer has to say where it stopped"
    assert {row["card"] for row in offer["decided"]} == {"discover", "brain"}

    state = service.run_state(cfg=cfg)
    assert state["resume"]["offer"] is True


def test_no_stopped_run_is_an_offer_of_nothing(cfg, monkeypatch):
    _stub(monkeypatch, {})
    monkeypatch.setattr(service, "_cfg", lambda: cfg)
    assert service.resume_offer() == {"offer": False, "why": "There is no stopped setup to continue."}


def test_the_service_continue_re_verifies_and_saves(cfg, monkeypatch):
    first: list[str] = []
    _first_run(cfg, monkeypatch, first, stop_after="secure.vault")
    monkeypatch.setattr(service, "_cfg", lambda: cfg)
    monkeypatch.setattr(wizard_runner, "vault_on_disk", lambda cfg_: False)

    result = service.resume_choice("continue")
    assert result["resumed"] is True
    assert result["reasked"] == ["secure:vault"]

    # It was written down: the next request sees the vault unanswered.
    with open(os.path.join(cfg.CONFIG_DIR, "wizard_run.json"),
              encoding="utf-8") as handle:
        entries = json.load(handle)["entries"]
    assert "secure.vault" not in entries
    assert "discover.card" in entries


def test_the_service_restart_archives_and_resumes_nothing(cfg, monkeypatch):
    first: list[str] = []
    run = _first_run(cfg, monkeypatch, first)
    monkeypatch.setattr(service, "_cfg", lambda: cfg)

    result = service.resume_choice("restart")
    assert result["resumed"] is False and result["archived"] is True
    assert not os.path.exists(run.state_path())


def test_the_service_refuses_a_choice_it_does_not_understand(cfg, monkeypatch):
    first: list[str] = []
    _first_run(cfg, monkeypatch, first)
    monkeypatch.setattr(service, "_cfg", lambda: cfg)
    with pytest.raises(service.AnswerRefused) as caught:
        service.resume_choice("maybe")
    assert caught.value.field == "choice"


# -- the banner ---------------------------------------------------------------

def test_the_browser_draws_the_same_choice_as_the_console():
    """One question, two renderers -- the console screen and the browser's.

    Checked at the source, because the alternative is a browser harness for one
    conditional. What it asserts is the wiring that cannot be faked: the banner
    reads ``resume`` off ``GET /wizard/state``, offers both answers, and sends
    them to ``POST /wizard/resume`` rather than deciding anything itself.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    screen = os.path.join(root, "dashboard", "app", "onboarding",
                          "Conversation.tsx")
    with open(screen, encoding="utf-8") as handle:
        source = handle.read()

    assert "getWizardState" in source, "the banner has nothing to read"
    assert "state.data?.resume" in source
    assert 'chooseResume("continue")' in source
    assert 'chooseResume("restart")' in source
    assert "resumeWizard(choice)" in source, (
        "the browser decides nothing: the server owns the re-verify")
    assert "reverify" not in source.lower(), (
        "the re-verify rule is the backend's; a copy in the browser would be a "
        "second rule")
    assert "A setup from " in source and "stopped at" in source


def test_a_missing_vault_reasks_the_vault_card_only(tmp_path, monkeypatch):
    """The owner re-answers the vault, not the posture questions they settled."""
    from puffpai.wizard import runner as wizard_runner
    from puffpai.wizard.packages.secure import skill as secure_skill

    class Ctx:
        part = "posture"

        def own(self):
            return {"posture": {"enabled_switches": [], "run_egress": "island"},
                    "vault": {"configured": True}}

    from puffpai.wizard import secure_audit

    monkeypatch.setattr(secure_skill, "_vault_state",
                        lambda ctx: (str(tmp_path), False))
    monkeypatch.setattr(secure_audit, "baseline",
                        lambda p, g: {"evidence": "ok", "violations": []})
    monkeypatch.setattr(secure_audit, "gaps_for", lambda a: [])
    verdict = secure_skill.verify(Ctx())
    assert verdict["status"] == "pass", verdict
    Ctx.part = "vault"
    assert secure_skill.verify(Ctx())["status"] != "pass"
