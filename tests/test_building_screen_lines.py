"""The Building screen streams its lines.

Each applier's ``ctx.progress`` appends to ``shared["factory"]["lines"]``
while it works (invariant I-6). The terminal prints every new line as it
arrives; the browser reads them from ``GET /wizard/build`` while the build
is still running. An older factory that streams nothing prints what its
finished record holds, as before.

Run:
    pytest tests/test_building_screen_lines.py
"""
from __future__ import annotations

import os
import sys
import threading
import time
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_wizard_needs_step import (  # noqa: E402
    GOALS, answer_questions, no_engine, pinned_machine)

from puffpai import cli  # noqa: E402
from puffpai.wizard import service  # noqa: E402


def _wait(test, timeout=10.0):
    end = time.time() + timeout
    while not test():
        if time.time() > end:
            return False
        time.sleep(0.02)
    return True


def test_build_lines_are_plain_capped_text():
    shared = {"factory": {"lines": ["Adding the plan app", "", 7,
                                    "  Downloaded the version from June 2026 "
                                    "... done"]}}
    assert service.build_lines(shared) == [
        "Adding the plan app",
        "Downloaded the version from June 2026 ... done"]
    assert service.build_lines({}) == [] and service.build_lines(None) == []
    many = {"factory": {"lines": [f"line {n}" for n in range(1000)]}}
    assert len(service.build_lines(many)) == service.MAX_BUILD_LINES


def test_the_terminal_prints_each_line_as_it_arrives():
    printed: list[str] = []
    seen_first = threading.Event()
    ctx = types.SimpleNamespace(shared={}, console=True)

    def apply(ctx):
        record = ctx.shared.setdefault("factory", {"lines": []})
        record["lines"].append("Adding the first thing")
        # The line is on the screen before the applier goes on.
        if _wait(lambda: "  Adding the first thing" in printed):
            seen_first.set()
        record["lines"].append("Adding the second thing ... done")
        return {"shared": {"factory": dict(record)}}

    cli._streamed_factory(ctx, apply, out=printed.append, every_s=0.01)
    assert seen_first.is_set(), "the first line waited for the end"
    assert printed == ["  Adding the first thing",
                       "  Adding the second thing ... done"]


def test_a_factory_that_streams_nothing_prints_its_record_at_the_end():
    printed: list[str] = []
    ctx = types.SimpleNamespace(shared={}, console=True)
    result = cli._streamed_factory(
        ctx, lambda ctx: {"shared": {"factory": {"lines": ["Added one"]}}},
        out=printed.append, every_s=0.01)
    assert printed == ["  Added one"]
    assert result["shared"]["factory"]["lines"] == ["Added one"]
    assert cli._streamed_factory(ctx, lambda ctx: {"note": "x"},
                                 out=printed.append) == {"note": "x"}


@pytest.mark.parametrize("goal_id", sorted(GOALS))
def test_the_browser_reads_the_lines_while_it_builds(monkeypatch, tmp_path,
                                                     goal_id):
    from puffpai.wizard import factory as wizard_factory

    pinned_machine(monkeypatch, tmp_path)
    no_engine(monkeypatch)
    answer_questions(GOALS[goal_id][0])
    service.approve({"on": {}})
    service.skip_vault()
    release = threading.Event()

    def apply(ctx):
        record = ctx.shared.setdefault("factory", {"lines": []})
        record["lines"].append("Adding a thing ... done")
        release.wait(10)
        record["lines"].append("Put it on your home page ... done")
        return {"shared": {"factory": dict(record)}}

    monkeypatch.setattr(wizard_factory, "apply_approved", apply)
    service.start_rest()
    try:
        assert _wait(lambda: service.rest_status()["lines"] == [
            "Adding a thing ... done"]), service.rest_status()
        assert service.rest_status()["running"] is True
    finally:
        release.set()
    assert _wait(lambda: not service.rest_status()["running"], 60)
    assert service.rest_status()["lines"] == [
        "Adding a thing ... done", "Put it on your home page ... done"]


# -- What the build left waiting --------------------------------------------------
#
# The real integration records in a scratch state folder; the box
# driver and Toolsmith's decide are fakes on their pinned names.

KINDS = ("wedding planner", "chess coach")


def _record(status, *, name, target="", reasons=("license_mismatch",),
            by="wizard"):
    from puffpai.integrate import records
    rec = records.blank(records.new_id(name), name=name, by=by)
    rec["status"] = status
    rec["held"] = {"reasons": list(reasons), "ticket_target": target}
    records.save(rec)
    return rec["id"]


def _fake_driver(monkeypatch, state, calls=None):
    mod = types.ModuleType("puffpai.box.driver")
    mod.status = lambda: types.SimpleNamespace(state=state)
    mod.distro_name = lambda: "PuffPAI-sandbox-dev"
    if calls is not None:
        def install_wsl(*, ticket, device_id):
            calls.append((ticket, device_id))
            return {"started": True, "message": "Windows is turning it on."}
        mod.install_wsl = install_wsl
    monkeypatch.setitem(sys.modules, "puffpai.box.driver", mod)


@pytest.mark.parametrize("kind", KINDS)
def test_a_held_item_waits_with_two_answers(monkeypatch, tmp_path, kind):
    pinned_machine(monkeypatch, tmp_path)
    held = _record("held", name=f"{kind} app", target="held:x:0123456789abcdef")
    _record("held", name="not waiting", target="")          # no yes possible
    _record("held", name="by chat", target="held:y:0123456789abcdef", by="chat")
    _record("ready", name="added fine")
    waiting = service.build_waiting()
    assert [row["id"] for row in waiting] == [held]
    row = waiting[0]
    assert row["status"] == "held" and row["name"] == f"{kind} app"
    assert row["lines"][0].startswith(
        "Drawbridge found something the plan did not show: ")
    assert row["lines"][0].endswith(".")
    assert row["lines"][1] == "It is waiting for your yes."
    assert (row["yes"], row["no"]) == ("Add it anyway", "Leave it out")


@pytest.mark.parametrize("state,shown", [("no_wsl", True), ("wsl_off", True),
                                         ("ready", False), ("no_box", False)])
def test_the_linux_feature_line_only_when_windows_needs_it(monkeypatch, tmp_path,
                                                          state, shown):
    pinned_machine(monkeypatch, tmp_path)
    _record("waiting_box", name="a tool")
    _record("waiting_box", name="another tool")
    _fake_driver(monkeypatch, state)
    rows = [r for r in service.build_waiting() if r["status"] == "waiting_box"]
    if not shown:
        assert rows == []
        return
    assert rows == [{
        "id": "box:wsl", "status": "waiting_box", "count": 2,
        "lines": ["Windows needs its Linux feature turned on for 2 things you "
                  "picked.",
                  "(Windows will ask for permission.) After that, restart the "
                  "computer and open BuildBot. It will finish them then."],
        "button": "Turn it on"}]


def test_one_waiting_thing_is_said_in_the_singular(monkeypatch, tmp_path):
    pinned_machine(monkeypatch, tmp_path)
    _record("waiting_box", name="a tool")
    _fake_driver(monkeypatch, "no_wsl")
    assert service.build_waiting()[0]["lines"][0].startswith(
        "Windows needs its Linux feature turned on for 1 thing you picked.")


def test_without_toolsmith_nothing_waits(monkeypatch):
    monkeypatch.setitem(sys.modules, "puffpai.integrate.records", None)
    assert service.build_waiting() == []


def test_the_browser_gets_the_waiting_list_after_the_build(monkeypatch, tmp_path):
    from puffpai.wizard import factory as wizard_factory
    pinned_machine(monkeypatch, tmp_path)
    no_engine(monkeypatch)
    answer_questions(GOALS["wedding"][0])
    service.approve({"on": {}})
    service.skip_vault()
    held = _record("held", name="Planner", target="held:p:0123456789abcdef")
    monkeypatch.setattr(wizard_factory, "apply_approved",
                        lambda ctx: {"shared": {"factory": {"lines": []}}})
    service.start_rest()
    assert _wait(lambda: not service.rest_status()["running"], 60)
    status = service.rest_status()
    assert [row["id"] for row in status["waiting"]] == [held]
    assert status["go_on"] == "Go on without them for now"


@pytest.mark.parametrize("answer,yes", [("yes", True), ("y", True), ("no", False)])
def test_the_terminal_asks_yes_or_no_for_a_held_item(answer, yes):
    from puffpai.wizard import render_cli
    decided, printed = [], []
    render_cli.waiting_screen(
        [{"id": "planner-abcdefgh", "status": "held", "name": "Planner",
          "lines": ["Drawbridge found something the plan did not show: x.",
                    "It is waiting for your yes."]}],
        decide=lambda item_id, ok: decided.append((item_id, ok)) or ["Added Planner."],
        turn_on=lambda: "unused",
        ask=lambda prompt: (printed.append(prompt), answer)[1],
        out=printed.append)
    assert decided == [("planner-abcdefgh", yes)]
    assert "  Add Planner anyway? Type yes or no." in printed
    assert "  Added Planner." in printed


def test_the_terminal_leaves_it_waiting_on_enter():
    from puffpai.wizard import render_cli
    decided = []
    render_cli.waiting_screen(
        [{"id": "p-abcdefgh", "status": "held", "name": "P", "lines": ["x"]},
         {"id": "box:wsl", "status": "waiting_box", "lines": ["y"]}],
        decide=lambda *a: decided.append(a) or [],
        turn_on=lambda: decided.append("wsl") or "", ask=lambda _p: "",
        out=lambda _l: None)
    assert decided == []


def test_the_terminal_turns_it_on_after_a_yes():
    from puffpai.wizard import render_cli
    printed = []
    render_cli.waiting_screen(
        [{"id": "box:wsl", "status": "waiting_box", "lines": ["y"]}],
        decide=lambda *a: [], turn_on=lambda: "Windows is turning it on.",
        ask=lambda _p: "yes", out=printed.append)
    assert "  Windows is turning it on." in printed


def test_decide_goes_through_toolsmith_with_the_device(monkeypatch):
    calls = []
    fake = types.ModuleType("puffpai.integrate.apply")

    class DecideError(Exception):
        def __init__(self, line):
            super().__init__(line)
            self.line = line

    def decide(item_id, yes, *, device_id):
        calls.append((item_id, yes, device_id))
        if item_id == "gone-abcdefgh":
            raise DecideError("That is not waiting for a yes.")
        return {"status": "added", "line": "Added Planner.",
                "lines": ["Set it up to open inside your bot ... done"]}
    fake.decide = decide
    fake.DecideError = DecideError
    monkeypatch.setitem(sys.modules, "puffpai.integrate.apply", fake)
    assert service.decide_waiting("planner-abcdefgh", True,
                                  "terminal:1-abcdefgh") == [
        "Set it up to open inside your bot ... done", "Added Planner."]
    assert service.decide_waiting("gone-abcdefgh", False,
                                  "terminal:1-abcdefgh") == [
        "That is not waiting for a yes."]
    assert calls[0] == ("planner-abcdefgh", True, "terminal:1-abcdefgh")


def test_turning_on_the_linux_feature_uses_a_box_ticket(monkeypatch):
    from puffpai.security import consent_tickets
    calls = []
    _fake_driver(monkeypatch, "no_wsl", calls)
    device = consent_tickets.terminal_device_id()
    assert service.turn_on_wsl(device) == "Windows is turning it on."
    ticket, used = calls[0]
    assert used == device
    ok, _verdict = consent_tickets.redeem(
        ticket, device, "box_setup",
        consent_tickets.target_box("PuffPAI-sandbox-dev"))
    assert ok


def test_turning_it_on_is_honest_when_the_box_cannot_yet(monkeypatch):
    _fake_driver(monkeypatch, "no_wsl")          # no install_wsl in this build
    assert service.turn_on_wsl("terminal:1-abcdefgh") == (
        "Turning it on from here is not in this build yet. Nothing was changed.")


@pytest.mark.parametrize("host", ["cdn.example.org", "fonts.example.net"])
def test_the_held_line_uses_the_words_the_build_said(monkeypatch, tmp_path, host):
    """The record keeps the reason code; the build said the words with their
    facts. Each held thing shows the build's own line once."""
    pinned_machine(monkeypatch, tmp_path)
    first = _record("held", name="One", target="held:a:0123456789abcdef",
                    reasons=("runtime_hosts_new",))
    second = _record("held", name="Two", target="held:b:0123456789abcdef",
                     reasons=("runtime_hosts_new",))
    said = [f"Drawbridge found something the plan did not show: it wants to "
            f"reach {host}, which the plan did not show.",
            "It is waiting for your yes."]
    rows = {row["id"]: row for row in service.build_waiting("standard", said)}
    lines = sorted(rows[first]["lines"][:1] + rows[second]["lines"][:1])
    # One of them gets the build's exact words, the other the plain reason.
    assert lines == sorted([
        said[0],
        "Drawbridge found something the plan did not show: one of its checks "
        "did not pass."])
