"""The terminal's hooks and multi-number ticks.

* "Change something" on the plan accepts several numbers at once ("2 7"):
  each line flips, and a line that needs the locked box brings the box.
* ``puffpai`` gains the commands of Remove BuildBot, the locked box and
  Toolsmith through ``add_parsers`` hooks: a module that is not in this build
  is skipped, one that is present is called, one that is broken raises.

Nothing here reaches the network.

Run:
    pytest tests/test_cli_parser_hooks_and_ticks.py
"""
from __future__ import annotations

import argparse
import sys
import types

import pytest

from puffpai import cli
from puffpai.wizard import proposal, render_cli


def _summary(items):
    return {"mode": "standard", "jobs": [], "similar": {"heading": "", "rows": []},
            "sections": [{"id": "apps_code", "heading": "Apps and code",
                          "items": items, "empty": ""}],
            "reach": {"current": "only_listed", "label": "Internet: only these",
                      "choices": []}}


def _item(ident, on, *, kind="code_project", requires=(), section="apps_code"):
    return {"id": ident, "section": section, "label": ident.split(":")[-1],
            "detail": "", "on": on, "default_on": on,
            "apply": {"kind": kind, "ref": ident}, "requires": list(requires)}


def _reader(answers):
    answers = list(answers)
    return lambda _prompt: answers.pop(0) if answers else None


@pytest.mark.parametrize("typed", ["2 3", "2,3", "2, 3", "3 2 2"])
def test_several_numbers_flip_each_line(typed):
    items = [_item("apps_code:a", True), _item("apps_code:b", False),
             _item("apps_code:c", True)]
    changes = {"on": {}}
    said = []
    assert render_cli._change_menu(_summary(items), changes, _reader([typed]),
                                   said.append, "standard")
    assert changes["on"] == {"apps_code:b": True, "apps_code:c": False}
    assert any("like 2 7" in line for line in said), said


@pytest.mark.parametrize("words", [
    ["weather helper", "weather"], ["chess board", "chess"]])
def test_ticking_a_boxed_line_brings_the_box(words):
    items = [_item(f"apps_code:{words[1]}", False, requires=["box_setup"]),
             _item("box:locked:setup", False, kind="box_setup", section="box"),
             _item("apps_code:other", True)]
    changes = {"on": {}}
    assert render_cli._change_menu(_summary(items), changes, _reader(["1 3"]),
                                   lambda _l: None, "standard")
    assert changes["on"] == {f"apps_code:{words[1]}": True,
                             "box:locked:setup": True, "apps_code:other": False}


def test_a_number_out_of_range_changes_nothing():
    items = [_item("apps_code:a", True), _item("apps_code:b", False)]
    changes = {"on": {}}
    assert not render_cli._change_menu(_summary(items), changes, _reader(["2 9"]),
                                       lambda _l: None, "standard")
    assert changes == {"on": {}}


def test_one_number_still_asks_yes_or_no():
    items = [_item("apps_code:a", True), _item("apps_code:b", False)]
    changes = {"on": {}}
    assert render_cli._change_menu(_summary(items), changes,
                                   _reader(["2", "yes"]), lambda _l: None,
                                   "standard")
    assert changes["on"] == {"apps_code:b": True}


def test_the_brain_keeps_its_own_menu():
    brain = _item("thinking:brain", True, kind="model_pull", section="thinking")
    items = [brain, _item("apps_code:a", False)]
    changes = {"on": {}}
    render_cli._change_menu(_summary(items), changes, _reader(["1 2"]),
                            lambda _l: None, "standard")
    assert changes["on"] == {"apps_code:a": True}


# -- the add_parsers hooks -----------------------------------------------------------

def _fake_module(name, calls, *, command):
    module = types.ModuleType(name)

    def add_parsers(subparsers):
        calls.append(name)
        sub = subparsers.add_parser(command)
        sub.set_defaults(func=lambda args: 0)
    module.add_parsers = add_parsers
    return module


def test_the_three_hooks_are_the_pinned_modules():
    assert cli.ROUND3_PARSER_MODULES == ("puffpai.remove_cli", "puffpai.box_cli",
                                         "puffpai.integrate.cli")


def test_absent_modules_add_nothing(monkeypatch):
    for name in cli.ROUND3_PARSER_MODULES:
        monkeypatch.setitem(sys.modules, name, None)
    parser = argparse.ArgumentParser()
    assert cli.add_round3_parsers(parser.add_subparsers(dest="command")) == []


def test_present_modules_are_called(monkeypatch):
    calls: list[str] = []
    for name, command in zip(cli.ROUND3_PARSER_MODULES,
                             ("remove", "box", "integrations")):
        monkeypatch.setitem(sys.modules, name,
                            _fake_module(name, calls, command=command))
    parser = argparse.ArgumentParser()
    added = cli.add_round3_parsers(parser.add_subparsers(dest="command"))
    assert added == list(cli.ROUND3_PARSER_MODULES) == calls
    assert parser.parse_args(["box"]).command == "box"


def test_the_real_parser_carries_them(monkeypatch):
    calls: list[str] = []
    monkeypatch.setitem(sys.modules, "puffpai.box_cli",
                        _fake_module("puffpai.box_cli", calls, command="box"))
    monkeypatch.setitem(sys.modules, "puffpai.integrate.cli", None)
    parser = cli.build_parser()
    assert calls == ["puffpai.box_cli"]
    assert parser.parse_args(["box"]).command == "box"


def test_a_module_without_add_parsers_fails_loudly(monkeypatch):
    monkeypatch.setitem(sys.modules, "puffpai.box_cli",
                        types.ModuleType("puffpai.box_cli"))
    with pytest.raises(AttributeError):
        cli.add_round3_parsers(argparse.ArgumentParser().add_subparsers(),
                               modules=("puffpai.box_cli",))


def test_a_broken_module_fails_loudly(monkeypatch):
    """A module that is there but cannot import is never "not in this build"."""
    def broken(name, *args, **kwargs):
        if name == "puffpai.fake_broken_cli":
            raise ModuleNotFoundError("No module named 'yaml_missing'",
                                      name="yaml_missing")
        return real(name, *args, **kwargs)
    import importlib
    real = importlib.import_module
    monkeypatch.setattr(importlib, "import_module", broken)
    with pytest.raises(ModuleNotFoundError):
        cli.add_round3_parsers(argparse.ArgumentParser().add_subparsers(),
                               modules=("puffpai.fake_broken_cli",))


def test_a_missing_module_is_skipped_quietly():
    assert cli.add_round3_parsers(argparse.ArgumentParser().add_subparsers(),
                                  modules=("puffpai.no_such_cli_module",)) == []


def test_a_similar_line_is_offered_in_change_something():
    items = [_item("apps_code:a", True)]
    summary = _summary(items)
    summary["similar"] = {"heading": "Similar projects also have (tick to add):",
                          "rows": [{"name": "Guest list", "seen_in": 3,
                                    "line": "Guest list    seen in 3 similar projects"}]}
    changes = {"on": {}}
    said: list[str] = []
    assert render_cli._change_menu(summary, changes, _reader(["2", "yes"]),
                                   said.append, "standard")
    assert changes["similar"] == ["Guest list"]
    assert any("Guest list (not included)" in line for line in said)
    lines = " ".join(render_cli.summary_lines(dict(
        summary, title="Here is the plan", intro="", lines={}, actions=[])))
    assert "Similar projects also have (tick to add):" in lines
    assert "[ ] Guest list seen in 3 similar projects" in " ".join(lines.split())
    assert proposal.numbered(summary) == items
