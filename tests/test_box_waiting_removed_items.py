"""A removed thing is no longer waiting for the box (Upbeat was removed and still listed in box/waiting.json; it was
written there in the same minute it was removed)."""
from __future__ import annotations

import json
import os

from puffpai.box import driver
from puffpai.integrate import records


def _record(name):
    iid = records.new_id(name)
    records.save(records.blank(iid, name=name))
    return iid


def _wait(iid, name):
    driver.add_waiting({"item": {"id": iid, "label": name,
                                 "apply": {"kind": "code_project"}, "integration": iid}})


def _ids():
    return [w["item"]["id"] for w in driver.waiting()]


def test_removing_it_takes_it_off_the_waiting_list():
    up = _record("Upbeat")
    other = _record("Other")
    _wait(up, "Upbeat")
    _wait(other, "Other")
    assert _ids() == [up, other]
    got = records.remove(up, by="owner")
    assert got["ok"], got
    assert _ids() == [other]
    with open(driver._waiting_path(), encoding="utf-8") as fh:  # noqa: SLF001
        assert [i["item"]["id"] for i in json.load(fh)["items"]] == [other]


def test_an_item_whose_record_is_gone_is_not_listed():
    """Written after the removal (the race in her log), or left from before."""
    gone = records.new_id("Gone")
    _wait(gone, "Gone")
    assert _ids() == []
    assert driver.waiting_lines() == []


def test_a_plain_box_item_without_an_integration_still_waits():
    driver.add_waiting({"item": {"id": "tool-1", "label": "A tool",
                                 "apply": {"kind": "cli_tool"}}})
    assert _ids() == ["tool-1"]
    assert driver.remove_waiting("tool-1") is True
    assert driver.remove_waiting("tool-1") is False
    assert _ids() == []
    assert os.path.isfile(driver._waiting_path())  # noqa: SLF001
