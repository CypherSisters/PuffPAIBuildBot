"""The wizard's brain pick for a chat with tools.

The rule: "The wizard recommends 9B or larger where the computer can run it,
and otherwise the smallest model that still calls tools." Pinned against the
shipped catalog, because that is what a stranger meets.
"""
from __future__ import annotations

import json
import os

import pytest

from puffpai import model_catalog, plain_words

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _catalog() -> dict:
    with open(os.path.join(_REPO_ROOT, "config", "model_catalog.json"),
              encoding="utf-8") as fh:
        return json.load(fh)


def _menu(ram, installed=(), **kw):
    return model_catalog.fit_menu({"memory": {"total_gb": ram}, "hardware_class": "test"},
                                  installed_models=list(installed), catalog=_catalog(), **kw)


def test_the_tool_model_is_in_the_catalog_and_calls_tools():
    rows = {m["id"]: m for m in _catalog()["models"]}
    nine = rows["qwen3.5:9b"]
    assert nine["tools"] is True and nine["params_b"] >= model_catalog.TOOL_MODEL_MIN_B
    assert nine["disk_gb"] == 7 and nine["min_ram_gb"] == 10
    # The 1B and 1.5B rows (the parked rails tier) are not tool models.
    assert rows["llama3.2:1b"]["tools"] is False
    assert rows["qwen2.5:1.5b-instruct"]["tools"] is False
    assert all(isinstance(m.get("tools"), bool) and m.get("params_b") for m in rows.values())


@pytest.mark.parametrize("ram", [10.0, 16.0, 32.0, 63.7, 128.0])
@pytest.mark.parametrize("unattended", [False, True])
def test_where_9b_fits_the_9b_is_recommended_not_the_19gb_row(ram, unattended):
    rec = _menu(ram, prefer_hosted_tier=unattended)["recommended"]
    assert rec["id"] == "qwen3.5:9b" and rec["tools"] is True
    assert "9B or more" in rec["reason"] and "About 7 GB to download" in rec["reason"]


def test_a_bigger_tool_model_already_here_wins():
    rec = _menu(63.7, installed=["qwen3:30b-a3b", "qwen2.5:7b-instruct"])["recommended"]
    assert rec["id"] == "qwen3:30b-a3b"
    assert rec["reason"].startswith("Already on this computer")
    assert "Nothing to download" in rec["reason"]


def test_an_installed_smaller_model_does_not_beat_a_9b_that_fits():
    rec = _menu(16.0, installed=["qwen2.5:7b-instruct"])["recommended"]
    assert rec["id"] == "qwen3.5:9b"


def test_a_tight_9b_still_wins_and_says_so():
    rec = _menu(12.0)["recommended"]
    assert rec["id"] == "qwen3.5:9b" and rec["fit"] == "tight"
    assert "tight fit" in rec["reason"]


@pytest.mark.parametrize("ram", [5.0, 6.0, 8.0, 9.5])
def test_below_9b_the_smallest_tool_model_is_recommended(ram):
    rec = _menu(ram)["recommended"]
    assert rec["id"] == "qwen2.5:3b-instruct"
    assert "smallest one that still uses tools" in rec["reason"]


def test_below_9b_a_tool_model_already_here_wins():
    rec = _menu(8.0, installed=["qwen2.5:7b-instruct"])["recommended"]
    assert rec["id"] == "qwen2.5:7b-instruct"
    assert "still uses tools" in rec["reason"]


@pytest.mark.parametrize("ram", [2.0, 3.0, 4.5])
def test_where_no_tool_model_fits_the_old_pick_says_chat_has_no_tools(ram):
    rec = _menu(ram)["recommended"]
    assert rec["id"] in ("llama3.2:1b", "qwen2.5:1.5b-instruct")
    assert "tools" not in rec
    assert rec["reason"].endswith(model_catalog.NO_TOOLS_NOTE)


def test_below_the_floor_nothing_changes():
    menu = _menu(1.0)
    assert menu["recommended"] is None and menu["message"]


def test_every_option_says_whether_it_calls_tools():
    menu = _menu(64.0)
    assert {o["id"]: o["tools"] for o in menu["options"]}["qwen3.5:9b"] is True
    assert all("params_b" in o for o in menu["options"])


@pytest.mark.parametrize("ram, installed", [(64.0, ()), (64.0, ("qwen3:30b-a3b",)),
                                            (8.0, ()), (8.0, ("qwen2.5:7b-instruct",)),
                                            (12.0, ())])
def test_the_reasons_are_plain_words(ram, installed):
    reason = _menu(ram, installed=installed)["recommended"]["reason"]
    assert not plain_words.check(reason), plain_words.check(reason)
    assert "\u2014" not in reason
    assert not plain_words.check(model_catalog.NO_TOOLS_NOTE)


def test_the_tool_model_profile_has_room_for_tools():
    from puffpai import model_profile
    budgets = model_profile.resolve_budgets("qwen3.5:9b", requested_ctx=16384,
                                            requested_predict=1024)
    assert budgets["num_ctx"] == 16384
