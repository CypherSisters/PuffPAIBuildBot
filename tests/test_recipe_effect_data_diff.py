"""An action's effect is proven by a stored-data diff, never taken on its word (R11).

On the synthetic fixtures (through the stand-in driver): a "Clear all" button
proves ``removes``; a row delete proves ``removes``; a row choice that changes
one field of the named record proves ``changes`` (undoable); a declared
``changes`` whose diff shortens a list becomes ``removes``; an import form that
replaces the data becomes ``removes``. The row actions are built here as the
row compiler emits them.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "webapps"))
import app_host_kit  # noqa: E402

from puffpai.webapps import compile_form, recipe_check as rc, recipes  # noqa: E402

fd = app_host_kit.load_fake_driver()
SHA = "d" * 64
KEY = "items.v1"


def _seeded_app(**kw):
    app = fd.FakeApp(key=KEY, **kw)
    driver = fd.FakeDriver(app)
    new = fd.labels()
    add = compile_form.compile_all(driver, new)["compiled"][0]

    def seed(label):
        for name in ("Alpha", "Beta"):
            args = dict(add["tests"], item_name=name)
            assert driver.run(label, add["recipe"], args)["ok"]
    return app, driver, new, seed, add


def _check(driver, new, recipe, tests, seed):
    return rc.check_recipe(recipes.stamp(recipe, SHA), own_keys={KEY}, current_app_sha=SHA,
                           driver=driver, new_copy=new, test_args=tests, seed=seed)


def test_clear_all_button_proves_removes():
    _, driver, new, seed, _ = _seeded_app(clear_all=True, clear_confirm=False)
    recipe = {"name": "clear_list", "says": "Tidy the list", "effect": "changes", "args": {},
              "steps": [{"do": "click", "sel": "#clear"}],
              "check": {"storage_key": KEY, "list": "items", "match": {}, "present": False}}
    v = _check(driver, new, recipe, {}, seed)
    assert v["effect_proven"] == "removes" and v["effect"] == "removes"
    assert not v["undoable"]
    assert v["verdict"] == "allow", v["rows"]


def test_row_delete_proves_removes():
    _, driver, new, seed, _ = _seeded_app(row_delete=True)
    recipe = {"name": "remove_item", "says": "Remove an item", "effect": "removes",
              "args": {"item_name": {"type": "string", "maxLength": 80, "required": True}},
              "steps": [{"do": "accept_next_dialog"},
                        {"do": "click_in_row", "row": "tr.item-row", "key": "td.name",
                         "match_arg": "item_name", "sel": "button.del"}],
              "check": {"storage_key": KEY, "list": "items", "match": {"name": "item_name"},
                        "present": False}}
    v = _check(driver, new, recipe, {"item_name": "Alpha"}, seed)
    assert v["verdict"] == "allow", v["rows"]
    assert v["effect_proven"] == "removes" and v["effect"] == "removes"
    # declared as a mere change, answering the are-you-sure box is refused before any run (R6)
    sneaky = dict(recipe, effect="changes")
    v = _check(driver, new, sneaky, {"item_name": "Alpha"}, seed)
    assert v["verdict"] == "block"
    assert any(r["rule"] == "R6" and r["result"] == "fail" for r in v["rows"])


def test_row_delete_without_answering_the_dialog_does_not_land():
    _, driver, new, seed, _ = _seeded_app(row_delete=True)
    recipe = {"name": "remove_item", "says": "Remove an item", "effect": "removes",
              "args": {"item_name": {"type": "string", "maxLength": 80, "required": True}},
              "steps": [{"do": "click_in_row", "row": "tr.item-row", "key": "td.name",
                         "match_arg": "item_name", "sel": "button.del"}],
              "check": {"storage_key": KEY, "list": "items", "match": {"name": "item_name"},
                        "present": False}}
    v = _check(driver, new, recipe, {"item_name": "Alpha"}, seed)
    assert v["verdict"] == "block" and any(r["rule"] == "R10" and r["result"] == "fail" for r in v["rows"])


def test_row_choice_proves_changes_and_is_undoable():
    _, driver, new, seed, _ = _seeded_app(row_select=True)
    recipe = {"name": "choose_status", "says": "Change the status of an item", "effect": "changes",
              "args": {"item_name": {"type": "string", "maxLength": 80, "required": True},
                       "status": {"type": "string", "enum": ["To get", "Got it", "Skip"]}},
              "steps": [{"do": "choose_in_row", "row": "tr.item-row", "key": "td.name",
                         "match_arg": "item_name", "sel": "select.status", "arg": "status"}],
              "check": {"storage_key": KEY, "list": "items", "match": {"name": "item_name"},
                        "expect": {"status": "status"}}}
    v = _check(driver, new, recipe, {"item_name": "Beta", "status": "Got it"}, seed)
    assert v["verdict"] == "allow", v["rows"]
    assert v["effect_proven"] == "changes" and v["effect"] == "changes" and v["undoable"]


def test_declared_changes_whose_diff_shortens_a_list_becomes_removes():
    before = {KEY: json.dumps({"items": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]})}
    after = {KEY: json.dumps({"items": [{"id": 1, "name": "A"}]})}
    recipe = {"says": "Tidy up", "steps": [], "check": {"match": {"name": "item_name"}}}
    proven, why = rc.prove_effect(recipe, {"item_name": "A"}, before, after)
    assert proven == "removes" and "data was removed" in why
    assert rc.stricter("changes", proven) == "removes"


def test_import_that_replaces_the_data_becomes_removes():
    _, driver, new, seed, add = _seeded_app(import_form=True)
    # the same fields, saved through a button whose words say it imports
    recipe = json.loads(json.dumps(add["recipe"]))
    recipe["steps"] = [s if s.get("sel") != "#save" else {"do": "click_text", "sel": "button", "text": "Save item"}
                       for s in recipe["steps"]]
    recipe["says"] = "Import items"
    recipe["effect"] = "changes"
    v = _check(driver, new, recipe, add["tests"], seed)
    assert v["effect_proven"] == "removes" and v["effect"] == "removes"


def test_whole_value_replaced_or_key_deleted_is_removes():
    rec = {"says": "x", "steps": [], "check": {"match": {"name": "n"}}}
    assert rc.prove_effect(rec, {}, {"k": "plain"}, {"k": "other"})[0] == "removes"
    assert rc.prove_effect(rec, {}, {"k": "[1]"}, {"k": '{"a":1}'})[0] == "removes"
    assert rc.prove_effect(rec, {}, {"k": "[]", "j": "1"}, {"k": "[]"})[0] == "removes"
    assert rc.prove_effect(rec, {}, {"k": '{"a":1,"b":2}'}, {"k": '{"a":1}'})[0] == "removes"


def test_more_than_one_record_edited_is_removes_and_one_named_record_is_changes():
    b = {"k": json.dumps([{"id": 1, "name": "A", "s": "x"}, {"id": 2, "name": "B", "s": "x"}])}
    both = {"k": json.dumps([{"id": 1, "name": "A", "s": "y"}, {"id": 2, "name": "B", "s": "y"}])}
    one = {"k": json.dumps([{"id": 1, "name": "A", "s": "y"}, {"id": 2, "name": "B", "s": "x"}])}
    rec = {"says": "Change the s of an item", "steps": [], "check": {"match": {"name": "n"}}}
    assert rc.prove_effect(rec, {"n": "A"}, b, both)[0] == "removes"
    assert rc.prove_effect(rec, {"n": "A"}, b, one)[0] == "changes"
    # the one edited record is not the one the details name
    assert rc.prove_effect(rec, {"n": "B"}, b, one)[0] == "removes"
    # an add that also edits another record
    grow = {"k": json.dumps([{"id": 1, "name": "A", "s": "y"}, {"id": 2, "name": "B", "s": "x"},
                             {"id": 3, "name": "C", "s": "x"}])}
    assert rc.prove_effect(rec, {"n": "C"}, b, grow)[0] == "removes"


def test_words_and_dialogs_make_removes():
    same = {"k": "[]"}
    grew = {"k": json.dumps([{"id": 1}])}
    for words in ("Delete", "Remove all", "Reset", "Clear", "Import", "Replace", "Restore",
                  "Erase", "Wipe", "Empty the bin"):
        assert rc.prove_effect({"says": "Add", "steps": []}, {}, same, grew, clicked=[words])[0] == "removes"
    assert rc.prove_effect({"says": "Add", "steps": [{"do": "accept_next_dialog"}]}, {}, same, grew)[0] == "removes"
    assert rc.prove_effect({"says": "Add", "steps": []}, {}, same, grew,
                           dialogs=[{"answered": "yes"}])[0] == "removes"
    assert rc.prove_effect({"says": "Add", "steps": []}, {}, same, grew)[0] == "changes"


def test_a_reading_action_that_changes_data_is_blocked():
    _, driver, new, seed, add = _seeded_app()
    recipe = json.loads(json.dumps(add["recipe"]))
    recipe["effect"] = "reads"
    recipe.pop("check")
    v = _check(driver, new, recipe, add["tests"], seed)
    assert v["verdict"] == "block"


def test_an_app_that_bumps_a_counter_beside_the_list_is_not_a_plain_change():
    app = fd.FakeApp(key=KEY, counter=True)
    driver = fd.FakeDriver(app)
    new = fd.labels()
    add = compile_form.compile_all(driver, new)["compiled"][0]
    v = rc.check_recipe(recipes.stamp(add["recipe"], SHA), own_keys={KEY}, current_app_sha=SHA,
                        driver=driver, new_copy=new, test_args=add["tests"])
    assert v["effect"] == "removes" and not v["undoable"]


# ---------------------------------------------------------------------------
# the effect is proven again on the REAL run of every action,
# reads included, and whatever it did beyond its checked effect is put back
# ---------------------------------------------------------------------------

import copy as _copy  # noqa: E402
import hashlib as _hashlib  # noqa: E402

APP = "pantry-notes-abcdefgh"
SEEDED = {KEY: json.dumps({"items": [{"id": "r1", "name": "Rice"}, {"id": "r2", "name": "Tea"}]})}


class _RealRunDriver:
    """The runner as actions.run sees it: the page loads the bot's copy, and
    the app's own shim writes every change back to the copy. ``doing`` is what
    the app does during the run (a list of storage values, one write each)."""

    def __init__(self, doing, rows=None, later=(), closes=True):
        self.doing = doing
        self.rows = rows or []
        self.ls = {}
        self.later = list(later)       # (seconds, value): the page's own late saves
        self.closes = closes
        self.timers = []
        self.closed = False

    def close(self, label):
        # a closed page runs nothing more: its pending saves never happen
        for t in self.timers:
            t.cancel()
        self.closed = True
        return self.closes

    def open(self, label, fresh=False):
        self.reload(label)

    def reload(self, label):
        from puffpai.webapps import mirror
        self.ls = dict(mirror.get(label)["data"])

    def storage(self, label):
        return {"localStorage": dict(self.ls), "indexedDB": []}

    def run(self, label, recipe, args):
        from puffpai.webapps import mirror
        for value in self.doing:
            self.ls[KEY] = value
            mirror.put(label, mirror.version(label), dict(self.ls), by="runner")
        for delay, value in self.later:
            def save(value=value):
                self.ls[KEY] = value
                mirror.put(label, mirror.version(label), dict(self.ls), by="runner")
            t = threading.Timer(delay, save)
            self.timers.append(t)
            t.start()
        return {"ok": True, "results": {"rows": self.rows}, "clicked": [], "dialogs": []}


def _sealed(recipe, sha, *, effect, proven, undoable=False):
    from puffpai.webapps import recipe_check as _rc
    r = _copy.deepcopy(recipes.stamp(recipe, sha))
    rows = [{"rule": c, "result": "pass", "why": ""} for c in sorted(_rc.REQUIRED_RULES)]
    r["checked"] = {"verdict": "allow", "effect": effect, "effect_proven": proven, "undoable": undoable,
                    "rows": rows, "rows_sha": _hashlib.sha256(_rc.canonical(rows).encode("utf-8")).hexdigest(),
                    "recipe_sha": _rc.recipe_sha(r), "app_sha": sha, "at": "2026-09-27T00:00:00Z",
                    "checker_version": _rc.CHECKER_VERSION}
    return r


@pytest.fixture
def real_run(tmp_path, monkeypatch):
    from puffpai.webapps import actions, host, mirror, runner_host
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", str(tmp_path / "apps"))
    app_host_kit.seal_key(monkeypatch, tmp_path)
    app_host_kit.place_app(APP, "clear_all")
    monkeypatch.setattr(runner_host, "availability", lambda: (True, ""))
    monkeypatch.setattr(host, "runner_secret", lambda: "test-secret")
    monkeypatch.setattr(runner_host, "ensure", lambda labels: object())
    monkeypatch.setattr(actions, "_POLL_S", 0.01, raising=False)
    assert mirror.put(APP, 0, dict(SEEDED))[0] == 200

    def use(driver):
        monkeypatch.setattr(runner_host, "RunnerDriver", lambda runner: driver)
    yield use
    host.refresh()


READ_ROWS = {"name": "read_item_rows", "says": "Read the item list", "effect": "reads", "args": {},
             "steps": [{"do": "read_rows", "sel": "tr.item-row", "cells": "td", "as": "rows"}]}
ADD = {"name": "add_item", "says": "Add an item", "effect": "changes", "start": "reload",
       "args": {"item_name": {"type": "string", "maxLength": 80, "required": True}},
       "steps": [{"do": "fill", "sel": "#f_name", "arg": "item_name"}, {"do": "click", "sel": "#save"}],
       "check": {"storage_key": KEY, "list": "items", "match": {"name": "item_name"}}}


def _action(name):
    from puffpai.webapps import actions
    return next(a for a in actions.list_actions() if a["id"] == f"{APP}__{name}")


def test_a_read_whose_real_run_changes_data_is_put_back(real_run):
    from puffpai.webapps import actions, mirror, wording
    sha = recipes.current_app_sha(APP)
    recipes.save(APP, sha, [_sealed(READ_ROWS, sha, effect="reads", proven="reads")])
    real_run(_RealRunDriver([json.dumps({"items": []})]))            # the app wipes its list
    got = actions.run(_action("read_item_rows"), {}, {})
    assert got["ok"] is False and got["effect"] == "reads" and got["line"] == wording.PUT_BACK
    assert mirror.get(APP)["data"] == SEEDED                          # nothing lost


def test_a_read_that_changes_nothing_answers(real_run):
    from puffpai.webapps import actions, mirror
    sha = recipes.current_app_sha(APP)
    recipes.save(APP, sha, [_sealed(READ_ROWS, sha, effect="reads", proven="reads")])
    v = mirror.version(APP)
    real_run(_RealRunDriver([], rows=[["Rice"], ["Tea"]]))
    got = actions.run(_action("read_item_rows"), {}, {})
    assert got["ok"] is True and got["data"] == [["Rice"], ["Tea"]]
    assert mirror.version(APP) == v and mirror.get(APP)["data"] == SEEDED


def test_a_change_that_removes_on_the_real_run_is_put_back_across_every_write(real_run):
    from puffpai.webapps import actions, mirror, wording
    sha = recipes.current_app_sha(APP)
    recipes.save(APP, sha, [_sealed(ADD, sha, effect="changes", proven="changes", undoable=True)])
    grown = json.dumps({"items": [{"id": "r1", "name": "Rice"}, {"id": "r2", "name": "Tea"},
                                  {"id": "r3", "name": "Oats"}]})
    real_run(_RealRunDriver([grown, json.dumps({"items": [{"id": "r3", "name": "Oats"}]})]))
    v = mirror.version(APP)
    got = actions.run(_action("add_item"), {"item_name": "Oats"}, {})
    assert got["ok"] is False and got["line"] == wording.PUT_BACK and got["undo_token"] is None
    assert mirror.version(APP) > v + 2                                # two writes, then put back
    assert mirror.get(APP)["data"] == SEEDED


def test_a_plain_change_on_the_real_run_is_kept_with_an_undo_token(real_run):
    from puffpai.webapps import actions, mirror
    sha = recipes.current_app_sha(APP)
    recipes.save(APP, sha, [_sealed(ADD, sha, effect="changes", proven="changes", undoable=True)])
    grown = json.dumps({"items": [{"id": "r1", "name": "Rice"}, {"id": "r2", "name": "Tea"},
                                  {"id": "r3", "name": "Oats"}]})
    real_run(_RealRunDriver([grown]))
    got = actions.run(_action("add_item"), {"item_name": "Oats"}, {})
    assert got["ok"] is True and got["effect"] == "changes"
    assert got["undo_token"] == {"kind": "web_recipe", "app_id": APP, "version": mirror.version(APP)}
    assert mirror.get(APP)["data"][KEY] == grown


def test_put_back_undoes_every_write_since_and_refuses_a_version_no_longer_kept(tmp_path, monkeypatch):
    from puffpai.webapps import mirror
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", str(tmp_path / "apps"))
    app = "undo-check-abcdefgh"
    for i in range(1, 4):
        assert mirror.put(app, i - 1, {"k": str(i)})[0] == 200
    assert mirror.put_back(app, 1) == {"ok": True, "why": ""}
    assert mirror.get(app) == {"v": 4, "data": {"k": "1"}}
    assert mirror.put_back(app, 4) == {"ok": True, "why": ""} and mirror.version(app) == 4
    for i in range(5, 5 + mirror.KEEP_VERSIONS):
        mirror.put(app, i - 1, {"k": str(i)})
    assert mirror.put_back(app, 1)["why"] == "not_kept"
    assert mirror.put_back("../x", 1)["why"] == "not_an_app"


def test_the_static_floor_reads_the_steps_and_words():
    assert rc.static_effect_floor(READ_ROWS) == "reads"
    assert rc.static_effect_floor(ADD) == "changes"
    assert rc.static_effect_floor(dict(READ_ROWS, steps=[{"do": "accept_next_dialog"}] + READ_ROWS["steps"])) == "removes"
    assert rc.static_effect_floor(dict(ADD, says="Clear the list")) == "removes"
    assert rc.static_effect_floor(dict(ADD, steps=[{"do": "click_text", "sel": "button", "text": "Reset"}])) == "removes"
    assert rc.static_effect_floor(dict(READ_ROWS, steps=["x"])) == "removes"
    assert rc.static_effect_floor(None) == "removes"


# ---------------------------------------------------------------------------
# the put-back never depends on a kept version file, is checked,
# and the effect is proven on the bot's copy after the page is closed
# ---------------------------------------------------------------------------

_GROWN = json.dumps({"items": [{"id": "r1", "name": "Rice"}, {"id": "r2", "name": "Tea"},
                               {"id": "r3", "name": "Oats"}]})
_TRIMMED = json.dumps({"items": [{"id": "r3", "name": "Oats"}]})


def test_a_read_that_writes_more_than_the_kept_versions_is_still_put_back(real_run):
    from puffpai.webapps import actions, mirror, wording
    sha = recipes.current_app_sha(APP)
    recipes.save(APP, sha, [_sealed(READ_ROWS, sha, effect="reads", proven="reads")])
    v0 = mirror.version(APP)
    harmless = [json.dumps({"items": json.loads(SEEDED[KEY])["items"], "n": i})
                for i in range(mirror.KEEP_VERSIONS + 2)]
    real_run(_RealRunDriver(harmless + [json.dumps({"items": []})]))
    got = actions.run(_action("read_item_rows"), {}, {})
    # the version the run started from is no longer kept as a file...
    assert mirror.put_back(APP, v0)["why"] == "not_kept"
    # ...and still the owner's data is back, and she is told the truth
    assert got["ok"] is False and got["line"] == wording.PUT_BACK
    assert mirror.get(APP)["data"] == SEEDED


def test_when_the_put_back_does_not_hold_the_owner_is_told_so(real_run, monkeypatch):
    from puffpai.webapps import actions, mirror, wording
    sha = recipes.current_app_sha(APP)
    recipes.save(APP, sha, [_sealed(READ_ROWS, sha, effect="reads", proven="reads")])
    real_run(_RealRunDriver([json.dumps({"items": []})]))
    monkeypatch.setattr(mirror, "put_back", lambda *a, **k: {"ok": False, "why": "not_kept"})
    got = actions.run(_action("read_item_rows"), {}, {})
    assert got["ok"] is False and got["line"] == wording.PUT_BACK_FAILED
    assert got["line"] != wording.PUT_BACK


def test_a_late_save_while_the_app_is_still_busy_is_proven_and_put_back(real_run):
    """The app adds the record, then trims the list half a second later: that
    lands inside the quiet window, so it is part of the proof."""
    from puffpai.webapps import actions, mirror, wording
    assert actions.QUIET_S > 0.5
    sha = recipes.current_app_sha(APP)
    recipes.save(APP, sha, [_sealed(ADD, sha, effect="changes", proven="changes", undoable=True)])
    drv = _RealRunDriver([_GROWN], later=[(0.5, _TRIMMED)])
    real_run(drv)
    got = actions.run(_action("add_item"), {"item_name": "Oats"}, {})     # no yes
    assert got["ok"] is False and got["line"] == wording.PUT_BACK and got["undo_token"] is None
    time.sleep(0.2)
    assert mirror.get(APP)["data"] == SEEDED and drv.closed


def test_the_page_is_closed_before_the_proof_so_a_later_save_cannot_happen(real_run, monkeypatch):
    from puffpai.webapps import actions, mirror
    monkeypatch.setattr(actions, "QUIET_S", 0.1)
    sha = recipes.current_app_sha(APP)
    recipes.save(APP, sha, [_sealed(ADD, sha, effect="changes", proven="changes", undoable=True)])
    drv = _RealRunDriver([_GROWN], later=[(1.0, _TRIMMED)])
    real_run(drv)
    got = actions.run(_action("add_item"), {"item_name": "Oats"}, {})
    assert got["ok"] is True and drv.closed
    time.sleep(1.3)
    assert mirror.get(APP)["data"][KEY] == _GROWN            # the late save never ran
    assert mirror.restore(APP, got["undo_token"]["version"]) == {"ok": True, "why": ""}
    assert mirror.get(APP)["data"] == SEEDED


def test_the_undo_of_an_action_with_several_writes_goes_back_to_before_all_of_them(real_run):
    from puffpai.webapps import actions, mirror
    sha = recipes.current_app_sha(APP)
    recipes.save(APP, sha, [_sealed(ADD, sha, effect="changes", proven="changes", undoable=True)])
    first = json.dumps({"items": [{"id": "r1", "name": "Rice"}, {"id": "r2", "name": "Tea"},
                                  {"id": "r3", "name": "Oat"}]})
    v = mirror.version(APP)
    real_run(_RealRunDriver([first, _GROWN]))
    got = actions.run(_action("add_item"), {"item_name": "Oats"}, {})
    assert got["ok"] is True and got["undo_token"]["version"] == v + 2
    assert mirror.restore(APP, v + 2) == {"ok": True, "why": ""}
    assert mirror.get(APP)["data"] == SEEDED                  # not the half-way copy


def test_a_page_that_does_not_close_is_not_trusted(real_run):
    from puffpai.webapps import actions, mirror, wording
    sha = recipes.current_app_sha(APP)
    recipes.save(APP, sha, [_sealed(ADD, sha, effect="changes", proven="changes", undoable=True)])
    real_run(_RealRunDriver([_GROWN], closes=False))
    got = actions.run(_action("add_item"), {"item_name": "Oats"}, {})
    assert got["ok"] is False and got["line"] == wording.BROKE_PUT_BACK and got["undo_token"] is None
    assert mirror.get(APP)["data"] == SEEDED


def test_a_run_that_breaks_partway_after_writing_is_put_back(real_run):
    from puffpai.webapps import actions, mirror, runner_host, wording
    sha = recipes.current_app_sha(APP)
    recipes.save(APP, sha, [_sealed(ADD, sha, effect="changes", proven="changes", undoable=True)])

    class Breaks(_RealRunDriver):
        calls = 0

        def storage(self, label):
            self.calls += 1
            if self.calls > 1:
                raise runner_host.DriverError("gone")
            return super().storage(label)

    real_run(Breaks([_GROWN]))
    got = actions.run(_action("add_item"), {"item_name": "Oats"}, {})
    assert got["ok"] is False and got["line"] == wording.BROKE_PUT_BACK
    assert mirror.get(APP)["data"] == SEEDED


def test_a_run_that_did_not_finish_but_wrote_is_put_back(real_run):
    from puffpai.webapps import actions, mirror, wording
    sha = recipes.current_app_sha(APP)
    recipes.save(APP, sha, [_sealed(ADD, sha, effect="changes", proven="changes", undoable=True)])

    class Half(_RealRunDriver):
        def run(self, label, recipe, args):
            got = super().run(label, recipe, args)
            return dict(got, ok=False)

    real_run(Half([_GROWN]))
    got = actions.run(_action("add_item"), {"item_name": "Oats"}, {})
    assert got["ok"] is False and got["line"] == wording.NOT_FINISHED_PUT_BACK
    assert mirror.get(APP)["data"] == SEEDED


def test_put_back_with_the_held_copy_works_after_pruning_and_writes_nothing_when_equal(tmp_path, monkeypatch):
    from puffpai.webapps import mirror
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", str(tmp_path / "apps"))
    app = "held-copy-abcdefgh"
    assert mirror.put(app, 0, {"k": "0"})[0] == 200
    held = mirror.get(app)
    for i in range(2, 2 + mirror.KEEP_VERSIONS + 3):
        assert mirror.put(app, i - 1, {"k": str(i)})[0] == 200
    assert mirror.put_back(app, held["v"])["why"] == "not_kept"
    assert mirror.put_back(app, held["v"], data=held["data"]) == {"ok": True, "why": ""}
    v = mirror.version(app)
    assert mirror.get(app)["data"] == {"k": "0"}
    assert mirror.put_back(app, held["v"], data=held["data"]) == {"ok": True, "why": ""}
    assert mirror.version(app) == v                            # already equal: no new write
    assert mirror.put_back(app, held["v"], data={"k": 1})["why"] == "bad_data"


# ---------------------------------------------------------------------------
# an edit the owner makes on her
# own screen while the bot runs an action is never counted as the action's
# ---------------------------------------------------------------------------

_OWNERS = json.dumps({"items": [{"id": "r1", "name": "Rice"}, {"id": "r2", "name": "Tea"},
                                {"id": "o1", "name": "Owner's own record"}]})


def _owner_types_during_the_run(delay, results):
    """Her dashboard frame's shim: PUT from the version it loaded; on 423 it
    sends the same change again a moment later (runner/shim.js)."""
    from puffpai.webapps import mirror
    base = mirror.version(APP)

    def go():
        while True:
            code, body = mirror.put(APP, base, {KEY: _OWNERS})       # by="page"
            results.append(code)
            if code != 423:
                return
            time.sleep(0.05)
    t = threading.Timer(delay, go)
    t.start()
    return t


def test_an_owner_edit_during_a_bot_read_waits_and_then_lands(real_run):
    from puffpai.webapps import actions, mirror
    sha = recipes.current_app_sha(APP)
    recipes.save(APP, sha, [_sealed(READ_ROWS, sha, effect="reads", proven="reads")])
    real_run(_RealRunDriver([], rows=[["Rice"], ["Tea"]]))
    codes: list = []
    t = _owner_types_during_the_run(0.3, codes)
    got = actions.run(_action("read_item_rows"), {}, {})
    t.join(5)
    assert got["ok"] is True and got["data"] == [["Rice"], ["Tea"]]   # never "put back"
    assert codes[0] == 423 and codes[-1] == 200                      # held, then saved
    assert "Owner's own record" in mirror.get(APP)["data"][KEY]


def test_an_owner_edit_during_a_bot_change_is_never_undone_with_it(real_run):
    from puffpai.webapps import actions, mirror
    sha = recipes.current_app_sha(APP)
    recipes.save(APP, sha, [_sealed(ADD, sha, effect="changes", proven="changes", undoable=True)])
    real_run(_RealRunDriver([_GROWN]))
    codes: list = []
    t = _owner_types_during_the_run(0.3, codes)
    got = actions.run(_action("add_item"), {"item_name": "Oats"}, {})
    t.join(5)
    assert got["ok"] is True
    # her change waited, then met the bot's newer version: 409, her page reloads
    # and the dashboard says it was not saved (shim "not-saved")
    assert codes[0] == 423 and codes[-1] == 409
    tok = got["undo_token"]["version"]
    assert mirror.get(APP)["data"][KEY] == _GROWN                    # the bot's write, not hers
    assert mirror.restore(APP, tok) == {"ok": True, "why": ""}
    assert mirror.get(APP)["data"] == SEEDED


def test_an_owner_edit_that_lands_after_the_bot_blocks_its_undo(real_run):
    from puffpai.webapps import actions, mirror
    sha = recipes.current_app_sha(APP)
    recipes.save(APP, sha, [_sealed(ADD, sha, effect="changes", proven="changes", undoable=True)])
    real_run(_RealRunDriver([_GROWN]))
    got = actions.run(_action("add_item"), {"item_name": "Oats"}, {})
    cur = mirror.get(APP)
    mine = json.loads(cur["data"][KEY])["items"] + [{"id": "o1", "name": "Owner's own record"}]
    assert mirror.put(APP, cur["v"], {KEY: json.dumps({"items": mine})})[0] == 200
    assert mirror.restore(APP, got["undo_token"]["version"]) == {"ok": False, "why": "newer_write"}
    assert "Owner's own record" in mirror.get(APP)["data"][KEY]


def test_while_the_bot_holds_an_app_only_the_runner_writes_and_undo_waits(tmp_path, monkeypatch):
    from puffpai.webapps import mirror
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", str(tmp_path / "apps"))
    app = "held-app-abcdefgh"
    assert mirror.put(app, 0, {"k": "0"})[0] == 200
    assert mirror.put(app, 1, {"k": "1"})[0] == 200
    mirror.begin_bot_run(app)
    try:
        assert mirror.busy(app)
        assert mirror.put(app, 2, {"k": "page"}) == (423, {"busy": True, "v": 2})
        assert mirror.restore(app, 2) == {"ok": False, "why": "busy"}
        assert mirror.put(app, 2, {"k": "bot"}, by="runner") == (200, {"v": 3})
        assert mirror.put_back(app, 2, data={"k": "1"})["ok"] is True   # the bot's own put-back
    finally:
        mirror.end_bot_run(app)
    assert not mirror.busy(app)
    assert mirror.put(app, 4, {"k": "page"})[0] == 200
    with pytest.raises(ValueError):
        mirror.begin_bot_run("../x")


def test_a_hold_that_was_never_ended_lapses(tmp_path, monkeypatch):
    from puffpai.webapps import mirror
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", str(tmp_path / "apps"))
    app = "lapse-app-abcdefgh"
    monkeypatch.setattr(mirror, "BOT_RUN_MAX_S", 0.05)
    mirror.begin_bot_run(app)
    time.sleep(0.1)
    assert not mirror.busy(app) and mirror.put(app, 0, {"k": "v"})[0] == 200


def test_the_hold_is_released_even_when_the_run_raises(real_run, monkeypatch):
    from puffpai.webapps import actions, mirror
    sha = recipes.current_app_sha(APP)
    recipes.save(APP, sha, [_sealed(ADD, sha, effect="changes", proven="changes", undoable=True)])
    monkeypatch.setattr(actions, "_run_held", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(RuntimeError):
        actions.run(_action("add_item"), {"item_name": "Oats"}, {})
    assert not mirror.busy(APP)


def test_a_runner_that_dies_mid_write_is_a_broken_run_and_the_data_is_put_back(real_run):
    """Validator minor: a BrokenPipeError while writing to the runner used to
    escape actions.run, leaving the page open and the run's writes in place."""
    from puffpai.webapps import actions, mirror, wording
    sha = recipes.current_app_sha(APP)
    recipes.save(APP, sha, [_sealed(ADD, sha, effect="changes", proven="changes", undoable=True)])

    class Dies(_RealRunDriver):
        calls = 0

        def storage(self, label):
            self.calls += 1
            if self.calls > 1:
                raise BrokenPipeError("the runner went away")
            return super().storage(label)

    real_run(Dies([_GROWN]))
    got = actions.run(_action("add_item"), {"item_name": "Oats"}, {})
    assert got["ok"] is False and got["line"] == wording.BROKE_PUT_BACK
    assert mirror.get(APP)["data"] == SEEDED and not mirror.busy(APP)


def test_a_broken_pipe_to_the_runner_becomes_a_runner_error():
    from puffpai.webapps import runner_host

    class Pipe:
        def write(self, data):
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self):
            pass

    r = runner_host.Runner.__new__(runner_host.Runner)
    r._send_lock = threading.Lock()
    r.proc = type("P", (), {"stdin": Pipe(), "poll": lambda self: None})()
    with pytest.raises(runner_host.RunnerError):
        r._write({"id": 1, "verb": "ping"})


# ---------------------------------------------------------------------------
# two records the owner's words both match: the bot touches neither, and a
# change that did not land is never reported as "nothing was saved" over data
# that moved
# ---------------------------------------------------------------------------

REMOVE = {"name": "remove_item", "says": "Remove an item", "effect": "removes", "start": "reload",
          "args": {"item_name": {"type": "string", "maxLength": 80, "required": True}},
          "steps": [{"do": "accept_next_dialog"},
                    {"do": "click_in_row", "row": "tr.item-row", "key": "td.name",
                     "match_arg": "item_name", "sel": "button.del"}, {"do": "wait", "ms": 400}],
          "check": {"storage_key": KEY, "list": "items", "match": {"name": "item_name"},
                    "present": False}}
FLIP = {"name": "flip_item", "says": "Change the done of an item", "effect": "changes",
        "start": "reload",
        "args": {"item_name": {"type": "string", "maxLength": 80, "required": True}},
        "steps": [{"do": "click_in_row", "row": "tr.item-row", "key": "td.name",
                   "match_arg": "item_name", "sel": "button.flip"}],
        "check": {"storage_key": KEY, "list": "items", "match": {"name": "item_name"},
                  "changed": True}}
_TWO_TEAS = json.dumps({"items": [{"id": "r1", "name": "Tea"}, {"id": "r2", "name": "Tea"},
                                  {"id": "r3", "name": "Rice"}]})


class _Counting(_RealRunDriver):
    runs = 0

    def run(self, label, recipe, args):
        type(self).runs += 1
        return super().run(label, recipe, args)


@pytest.mark.parametrize("recipe,effect,args,ctx", [
    (REMOVE, "removes", {"item_name": "Tea"}, {"yes": True}),
    (REMOVE, "removes", {"item_name": " tea "}, {"yes": True}),      # same record, other spelling
    (FLIP, "changes", {"item_name": "Tea"}, {}),
])
def test_two_records_the_words_both_match_are_both_left_alone(real_run, recipe, effect, args, ctx):
    from puffpai.webapps import actions, mirror, wording
    assert mirror.put(APP, mirror.version(APP), {KEY: _TWO_TEAS})[0] == 200
    sha = recipes.current_app_sha(APP)
    recipes.save(APP, sha, [_sealed(recipe, sha, effect=effect, proven=effect)])
    one = json.dumps({"items": [{"id": "r2", "name": "Tea"}, {"id": "r3", "name": "Rice"}]})
    _Counting.runs = 0
    real_run(_Counting([one]))           # would remove the first Tea, as a page click would
    v = mirror.version(APP)
    got = actions.run(_action(recipe["name"]), args, ctx)
    assert got["ok"] is False and got["line"] == wording.MORE_THAN_ONE and got["undo_token"] is None
    assert _Counting.runs == 0                                         # nothing was clicked
    assert mirror.version(APP) == v and mirror.get(APP)["data"] == {KEY: _TWO_TEAS}


def test_the_runner_saying_two_rows_match_is_told_plainly(real_run):
    from puffpai.webapps import actions, mirror, wording
    sha = recipes.current_app_sha(APP)
    recipes.save(APP, sha, [_sealed(REMOVE, sha, effect="removes", proven="removes")])

    class PageHasTwo(_RealRunDriver):   # the page shows two rows the stored data does not
        def run(self, label, recipe, args):
            return {"ok": False, "error": "more than one item matches that", "results": {},
                    "clicked": [], "dialogs": []}

    real_run(PageHasTwo([]))
    got = actions.run(_action("remove_item"), {"item_name": "Tea"}, {"yes": True})
    assert got["ok"] is False and got["line"] == wording.MORE_THAN_ONE
    assert mirror.get(APP)["data"] == SEEDED


def test_a_change_that_did_not_land_but_moved_data_is_put_back_and_said(real_run):
    """The asked-for record is still there, but another one went: the owner is
    never told "nothing was saved" over data that changed."""
    from puffpai.webapps import actions, mirror, wording
    sha = recipes.current_app_sha(APP)
    recipes.save(APP, sha, [_sealed(REMOVE, sha, effect="removes", proven="removes")])
    wrong = json.dumps({"items": [{"id": "r2", "name": "Tea"}]})       # Rice went, Tea stayed
    real_run(_RealRunDriver([wrong]))
    got = actions.run(_action("remove_item"), {"item_name": "Tea"}, {"yes": True})
    assert got["ok"] is False and got["line"] == wording.NOT_AS_ASKED_PUT_BACK
    assert got["line"] != wording.APP_DID_NOT_TAKE
    assert mirror.get(APP)["data"] == SEEDED


def test_a_change_that_did_not_land_and_moved_nothing_says_nothing_was_saved(real_run):
    from puffpai.webapps import actions, mirror, wording
    sha = recipes.current_app_sha(APP)
    recipes.save(APP, sha, [_sealed(REMOVE, sha, effect="removes", proven="removes")])
    real_run(_RealRunDriver([]))
    got = actions.run(_action("remove_item"), {"item_name": "Tea"}, {"yes": True})
    assert got["ok"] is False and got["line"] == wording.APP_DID_NOT_TAKE
    assert mirror.get(APP)["data"] == SEEDED


def test_one_record_of_that_name_is_removed_as_before(real_run):
    from puffpai.webapps import actions, mirror
    sha = recipes.current_app_sha(APP)
    recipes.save(APP, sha, [_sealed(REMOVE, sha, effect="removes", proven="removes")])
    left = json.dumps({"items": [{"id": "r1", "name": "Rice"}]})
    real_run(_RealRunDriver([left]))
    got = actions.run(_action("remove_item"), {"item_name": "Tea"}, {"yes": True})
    assert got["ok"] is True and got["effect"] == "removes"
    assert mirror.get(APP)["data"] == {KEY: left}


def test_matching_records_counts_only_what_the_check_matches():
    data = {KEY: _TWO_TEAS}
    assert rc.targets_a_row(REMOVE) and rc.targets_a_row(FLIP) and not rc.targets_a_row(ADD)
    assert rc.matching_records(REMOVE, {"item_name": "Tea"}, data) == 2
    assert rc.matching_records(REMOVE, {"item_name": "Rice"}, data) == 1
    assert rc.matching_records(REMOVE, {"item_name": "Oats"}, data) == 0
    assert rc.matching_records(REMOVE, {"item_name": "Tea"}, {KEY: "not json"}) == 0


# ---------------------------------------------------------------------------
# one row asked for, one row touched: a remove that also takes another record
# (or a change that also adds one) is put back, counted on the real data
# ---------------------------------------------------------------------------

_THREE = json.dumps({"items": [{"id": "r1", "name": "Tea"}, {"id": "r3", "name": "Rice"},
                               {"id": "r4", "name": "Milk"}]})


def test_a_remove_that_also_takes_another_record_is_put_back(real_run):
    from puffpai.webapps import actions, mirror, wording
    assert mirror.put(APP, mirror.version(APP), {KEY: _THREE})[0] == 200
    sha = recipes.current_app_sha(APP)
    recipes.save(APP, sha, [_sealed(REMOVE, sha, effect="removes", proven="removes")])
    real_run(_RealRunDriver([json.dumps({"items": [{"id": "r4", "name": "Milk"}]})]))  # Tea AND Rice
    got = actions.run(_action("remove_item"), {"item_name": "Tea"}, {"yes": True})
    assert got["ok"] is False and got["line"] == wording.PUT_BACK and got["undo_token"] is None
    assert mirror.get(APP)["data"] == {KEY: _THREE}


def test_a_remove_that_also_writes_another_stored_key_is_put_back(real_run):
    from puffpai.webapps import actions, mirror, wording
    sha = recipes.current_app_sha(APP)
    recipes.save(APP, sha, [_sealed(REMOVE, sha, effect="removes", proven="removes")])

    class AlsoWipesNotes(_RealRunDriver):
        def run(self, label, recipe, args):
            self.ls["notes.v1"] = "{}"
            return super().run(label, recipe, args)

    assert mirror.put(APP, mirror.version(APP), {**SEEDED, "notes.v1": '{"a": "keep me"}'})[0] == 200
    real_run(AlsoWipesNotes([json.dumps({"items": [{"id": "r1", "name": "Rice"}]})]))
    got = actions.run(_action("remove_item"), {"item_name": "Tea"}, {"yes": True})
    assert got["ok"] is False and got["line"] == wording.PUT_BACK
    assert mirror.get(APP)["data"] == {**SEEDED, "notes.v1": '{"a": "keep me"}'}


def test_nothing_of_that_name_means_nothing_is_clicked(real_run):
    from puffpai.webapps import actions, mirror, wording
    sha = recipes.current_app_sha(APP)
    recipes.save(APP, sha, [_sealed(REMOVE, sha, effect="removes", proven="removes")])
    _Counting.runs = 0
    real_run(_Counting([json.dumps({"items": [{"id": "r2", "name": "Tea"}]})]))  # would drop Rice
    got = actions.run(_action("remove_item"), {"item_name": "Coffee"}, {"yes": True})
    assert got["ok"] is False and got["line"] == wording.NO_MATCH and _Counting.runs == 0
    assert mirror.get(APP)["data"] == SEEDED


def test_a_row_change_that_also_adds_a_record_is_put_back(real_run):
    from puffpai.webapps import actions, mirror, wording
    sha = recipes.current_app_sha(APP)
    recipes.save(APP, sha, [_sealed(FLIP, sha, effect="changes", proven="changes", undoable=True)])
    flipped = json.dumps({"items": [{"id": "r1", "name": "Rice"}, {"id": "r2", "name": "Tea",
                                                                   "done": True},
                                    {"id": "r9", "name": "Junk"}]})
    real_run(_RealRunDriver([flipped]))
    got = actions.run(_action("flip_item"), {"item_name": "Tea"}, {})
    assert got["ok"] is False and got["line"] == wording.PUT_BACK and got["undo_token"] is None
    assert mirror.get(APP)["data"] == SEEDED


def test_a_row_change_of_that_one_record_is_kept():
    before = {KEY: _THREE}
    after = {KEY: json.dumps({"items": [{"id": "r1", "name": "Tea", "done": True},
                                        {"id": "r3", "name": "Rice"}, {"id": "r4", "name": "Milk"}]})}
    assert rc.one_row_only(FLIP, {"item_name": "Tea"}, before, after)
    # the same flip that also edits Rice is not one row
    both = {KEY: json.dumps({"items": [{"id": "r1", "name": "Tea", "done": True},
                                       {"id": "r3", "name": "Rice", "done": True},
                                       {"id": "r4", "name": "Milk"}]})}
    assert not rc.one_row_only(FLIP, {"item_name": "Tea"}, before, both)


@pytest.mark.parametrize("after,ok", [
    ({"items": [{"id": "r3", "name": "Rice"}, {"id": "r4", "name": "Milk"}]}, True),
    ({"items": [{"id": "r4", "name": "Milk"}, {"id": "r3", "name": "Rice"}]}, True),   # order only
    ({"items": [{"id": "r4", "name": "Milk"}]}, False),                                # one more gone
    ({"items": [{"id": "r3", "name": "Rice!"}, {"id": "r4", "name": "Milk"}]}, False),  # one edited
    ({"items": [{"id": "r3", "name": "Rice"}, {"id": "r4", "name": "Milk"},
                {"id": "r5", "name": "New"}]}, False),                                  # one added
    ({"items": [{"id": "r1", "name": "Tea"}, {"id": "r3", "name": "Rice"},
                {"id": "r4", "name": "Milk"}]}, False),                                 # nothing gone
    ({"items": [{"id": "r3", "name": "Rice"}, {"id": "r4", "name": "Milk"}], "title": "x"}, False),
])
def test_one_row_only_counts_what_a_remove_took(after, ok):
    args = {"item_name": "Tea"}
    assert rc.one_row_only(REMOVE, args, {KEY: _THREE}, {KEY: json.dumps(after)}) is ok


def test_one_row_only_with_two_records_of_another_name():
    before = {KEY: json.dumps({"items": [{"name": "Tea"}, {"name": "Rice"}, {"name": "Rice"}]})}
    fine = {KEY: json.dumps({"items": [{"name": "Rice"}, {"name": "Rice"}]})}
    lost = {KEY: json.dumps({"items": [{"name": "Rice"}]})}
    assert rc.one_row_only(REMOVE, {"item_name": "Tea"}, before, fine)
    assert not rc.one_row_only(REMOVE, {"item_name": "Tea"}, before, lost)
    # two of the asked-for name are never one row
    assert not rc.one_row_only(REMOVE, {"item_name": "Rice"}, before,
                               {KEY: json.dumps({"items": [{"name": "Tea"}, {"name": "Rice"}]})})


def test_removing_the_last_record_may_clear_the_stored_value_only_when_it_held_nothing_else():
    root = dict(REMOVE, check=dict(REMOVE["check"], list=""))
    one = {KEY: json.dumps([{"name": "Tea"}])}
    assert rc.one_row_only(root, {"item_name": "Tea"}, one, {})
    assert rc.one_row_only(root, {"item_name": "Tea"}, one, {KEY: "[]"})
    kept = {KEY: json.dumps({"items": [{"name": "Tea"}], "title": "Pantry"})}
    assert not rc.one_row_only(REMOVE, {"item_name": "Tea"}, kept, {})
    assert rc.one_row_only(REMOVE, {"item_name": "Tea"}, kept,
                           {KEY: json.dumps({"items": [], "title": "Pantry"})})
