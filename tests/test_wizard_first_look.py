"""The first screen: "Let's see what we're working with here".

The hardware scan is shown explicitly, with the recommended LLM size for this
hardware. Proven here:

* what was found is said plainly, and what was not measured says so;
* the recommendation is the Brain card's own, with a reason tied to the
  numbers, and a model that does not fit is never offered (brain-never-too-big);
* the pick reaches the plan's "How it thinks" line;
* the console asks "Use this one / Change" and nothing else.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import copy
import types

import pytest

from puffpai import plain_words
from puffpai.config import get_config
from puffpai.wizard import first_look, proposal


def _census(ram, *, accel=None, cpu="Test CPU", free=120.0, stated=False):
    return {
        "schema": "hardware_census/v1",
        "os": {"system": "Linux"},
        "cpu": {"model": cpu, "logical_cores": 8, "source": "detected"},
        "memory": {"total_gb": ram,
                   "source": "stated" if stated else "detected"},
        "accelerator": accel or {"kind": "none", "vram_gb": None},
        "soc": {"is_jetson": False},
        "disk": {"path": "/x", "total_gb": 500.0, "free_gb": free},
        "hardware_class": "workstation",
        "model_budget_gb": None,
        "notes": [],
    }


def _shared(ram, installed=(), internet=True, **kw):
    return {"census": _census(ram, **kw), "ollama_url": "http://127.0.0.1:11434",
            "installed_models": list(installed) if installed is not None else None,
            "internet": internet}


ARGS = types.SimpleNamespace(no_suggestions=True, skip_ha=True)


def _screen(shared, *, non_interactive=False):
    return first_look.screen(args=ARGS, cfg=get_config(), shared=shared,
                             sections={}, non_interactive=non_interactive)


def _all_text(shown) -> str:
    parts = [shown["title"], shown["intro"], shown["none_line"],
             shown["too_big"], shown["others_heading"]]
    parts += [f"{r['label']}: {r['text']}" for r in shown["found"]]
    parts += [shown["recommend"][k] for k in ("line", "why", "good_for")]
    parts += [c["label"] + " " + c["good_for"] for c in shown["choices"]]
    return "\n".join(p for p in parts if p)


@pytest.fixture(autouse=True)
def _offline_catalogs(monkeypatch):
    from puffpai import setup_ops
    monkeypatch.setattr(setup_ops, "list_models", lambda *a, **k: [])
    yield


def test_what_was_found_is_plain_and_unknown_says_so():
    shared = _shared(None, installed=None, internet=None, free=None,
                     accel={"kind": "unknown"})
    shared["census"]["cpu"] = {}
    rows = {r["label"]: r["text"] for r in first_look.found_rows(shared)}
    assert rows["Memory"] == "not measured"
    assert rows["Processor"] == "not measured"
    assert rows["Free disk space"] == "not measured"
    assert rows["The internet"] == "not checked"
    assert rows["Graphics card"].startswith("not detected")
    assert rows["Ollama"].startswith("not answering at http://127.0.0.1:11434")


def test_measured_facts_and_the_models_ollama_already_has():
    shared = _shared(63.7, installed=["qwen2.5:7b-instruct", "llama3.2:3b"],
                     accel={"kind": "nvidia_dgpu", "name": "RTX 2000",
                            "vram_gb": 8.0})
    rows = {r["label"]: r["text"] for r in first_look.found_rows(shared)}
    assert rows["Memory"] == "63.7 GB"
    assert rows["Graphics card"] == "RTX 2000, with 8 GB of its own memory"
    assert rows["Ollama"] == ("is running, with 2 models "
                              "already: qwen2.5:7b-instruct, llama3.2:3b")
    stated = _shared(16, stated=True)
    assert first_look.found_rows(stated)[0]["text"] == "16 GB (you said this)"


def test_an_installed_model_that_fits_is_recommended_and_bigger_ones_listed():
    # The installed model is the 9B tool model (9B or
    # larger where the computer can run it).
    shown = _screen(_shared(63.7, installed=["qwen3.5:9b"],
                            accel={"kind": "nvidia_dgpu", "name": "RTX",
                                   "vram_gb": 8.0}))
    rec = shown["recommend"]
    assert rec["value"] == "qwen3.5:9b"
    assert rec["line"].startswith("The thinking model I recommend for this "
                                  "computer: Qwen3.5 9B (qwen3.5:9b)")
    assert "use tools well" in rec["why"]
    assert "already on this computer" in rec["line"]
    assert "63.7 GB of memory" in rec["why"]
    big = next(c for c in shown["choices"] if c["value"] == "qwen3:30b-a3b")
    assert "bigger and slower" in big["label"] and "19 GB download" in big["label"]
    assert big["good_for"].startswith("handles requests with several steps")
    assert not plain_words.check(_all_text(shown)), plain_words.check(_all_text(shown))


def test_a_person_and_a_script_both_get_the_9b_tool_model():
    # Not "most capable for a person, a smaller
    # tier for a script": both get the 9B tool model, and neither is
    # committed to the 19 GB download.
    person = _screen(_shared(64))
    script = _screen(_shared(64), non_interactive=True)
    assert person["recommend"]["value"] == "qwen3.5:9b"
    assert "64 GB of memory" in person["recommend"]["why"]
    assert script["recommend"]["value"] == "qwen3.5:9b"


def test_a_small_graphics_card_is_said_out_loud():
    # 6 GB: smaller than the 7 GB 9B tool model.
    shown = _screen(_shared(64, accel={"kind": "nvidia_dgpu", "name": "Card",
                                       "vram_gb": 6.0}))
    assert shown["recommend"]["value"] == "qwen3.5:9b"
    assert "6 GB of its own memory" in shown["recommend"]["why"]
    assert "slower" in shown["recommend"]["why"]


@pytest.mark.parametrize("ram", [2.5, 4, 6, 12, 20, 28, 40])
def test_nothing_that_does_not_fit_is_ever_offered(ram):
    from puffpai import model_catalog
    shown = _screen(_shared(ram))
    floors = {str(m["id"]): float(m["min_ram_gb"])
              for m in model_catalog.load_catalog()["models"]}
    offered = [c["value"] for c in shown["choices"]]
    if shown["recommend"]["value"] in floors:
        offered.append(shown["recommend"]["value"])
    assert all(floors[m] <= ram for m in offered), (ram, offered)
    too_big = [m for m, floor in floors.items() if floor > ram]
    if too_big:
        assert shown["too_big"].startswith("Too big for this computer, so never "
                                           "offered:")


def test_below_the_floor_nothing_is_recommended_and_the_menu_says_why():
    shown = _screen(_shared(1.5))
    assert shown["recommend"]["value"] == ""
    assert shown["choices"] == []
    assert "below the floor of our smallest local model" in shown["none_line"]


def test_offline_says_it_cannot_download_and_never_claims_a_tight_fit_wrongly():
    shown = _screen(_shared(32, internet=False))
    why = shown["recommend"]["why"]
    assert why.startswith("The internet is not reachable, so it cannot be "
                          "downloaded now.")
    assert "room to spare" in why


def test_the_pick_is_kept_and_is_the_brain_on_the_plan(monkeypatch):
    shared = _shared(64)
    shown = _screen(shared)
    small = next(c for c in shown["choices"] if c["value"] == "llama3.2:3b")
    with pytest.raises(ValueError):
        first_look.choose(shared, shown, "made-up:model")
    entry = first_look.choose(shared, shown, small["value"])
    assert entry == {"model": "llama3.2:3b", "recommended": "qwen3.5:9b",
                     "seen": True}
    found, _notes = proposal.previews(args=ARGS, cfg=get_config(),
                                      shared=shared, sections={},
                                      only=("secure:posture", "brain"))
    assert found["brain"]["model"] == "llama3.2:3b"
    # And the screen, drawn again, shows the pick as the current one.
    assert _screen(shared)["current"] == "llama3.2:3b"


def test_accepting_keeps_the_recommendation():
    shared = _shared(64)
    shown = _screen(shared)
    assert first_look.choose(shared, shown, None)["model"] == "qwen3.5:9b"


def test_the_console_asks_use_or_change_and_nothing_else():
    shown = _screen(_shared(64))
    out: list[str] = []
    asked: list[str] = []

    def reader(answers):
        def read(prompt):
            asked.append(prompt)
            return answers.pop(0)
        return read

    assert first_look.console(shown, non_interactive=False,
                              ask=reader([""]), out=out.append) == "qwen3.5:9b"
    text = "\n".join(out)
    assert "Let's see what we're working with here" in text
    assert "1) Use this one" in text and "2) Change" in text
    assert asked[-1].strip().startswith("Press Enter to use Qwen3.5 9B "
                                        "(qwen3.5:9b), or type 2")
    number = next(i for i, c in enumerate(shown["choices"], 1)
                  if c["value"] == "llama3.2:3b")
    out.clear()
    assert first_look.console(shown, non_interactive=False,
                              ask=reader(["2", str(number)]),
                              out=out.append) == "llama3.2:3b"
    assert "The plan will use Llama 3.2 3B (llama3.2:3b)." in " ".join(
        " ".join(out).split())


def test_a_yes_run_shows_the_screen_and_picks_nothing():
    shown = _screen(_shared(64), non_interactive=True)
    out: list[str] = []
    assert first_look.console(shown, non_interactive=True, out=out.append) is None
    assert "Let's see what we're working with here" in "\n".join(out)


def test_with_nothing_else_to_pick_it_only_says_go_on():
    shown = _screen(_shared(32, internet=False))  # offline: nothing to download
    asked: list[str] = []
    picked = first_look.console(shown, non_interactive=False,
                                ask=lambda p: asked.append(p) or "",
                                out=lambda line: None)
    assert picked == shown["recommend"]["value"]
    assert asked == ["  Press Enter to go on."]


def test_the_wizard_shows_it_before_the_goal_question(monkeypatch, capsys,
                                                       tmp_path):
    """The real console wizard, driven like a person would: the first screen
    comes before "What kind of bot do you want?", and a pick made there is the
    brain on the plan."""
    import sys
    import os
    sys.path.insert(0, os.path.dirname(__file__))
    from _wizard_driver import drive_in_process
    from puffpai import hardware_census, setup_ops
    from puffpai.wizard import netcheck

    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("COLUMNS", "80")
    get_config(_force_reload=True)
    census = _census(64.0)
    monkeypatch.setattr(hardware_census, "run_census",
                        lambda *a, **k: copy.deepcopy(census))
    monkeypatch.setattr(setup_ops, "ollama_survey", lambda *a, **k: [])
    monkeypatch.setattr(netcheck, "internet_reachable", lambda *a, **k: True)
    shown = _screen(_shared(64))
    number = next(i for i, c in enumerate(shown["choices"], 1)
                  if c["value"] == "llama3.2:3b")
    code, transcript, _asked = drive_in_process(
        ["setup", "--skip-ha", "--no-suggestions", "--skip-knowledge",
         "--skip-vault"],
        [("or type 2 to pick another", "2"),
         ("which thinking model", str(number)),
         ("what kind of bot", "a knitting buddy with patterns")],
        monkeypatch=monkeypatch, capsys=capsys)
    assert code == 0
    head, rest = transcript.split("What kind of bot do you want?", 1)
    assert "Let's see what we're working with here" in head
    assert "The thinking model I recommend for this computer: Qwen3.5 9B" in head
    plan = rest.split("Here is the plan for", 1)[1]
    assert "The brain: llama3.2:3b" in plan
    get_config(_force_reload=True)


def test_a_data_folder_not_made_yet_is_still_on_this_disk(tmp_path, monkeypatch):
    """The check now runs first, before anything is written: a data folder that
    does not exist yet is measured and verified on the folder above it."""
    from puffpai import hardware_census
    from puffpai.wizard.packages.discover import skill as discover

    missing = tmp_path / "not" / "made" / "yet"
    disk = hardware_census._detect_disk(str(missing))
    assert disk["free_gb"] and disk["path"] == str(missing)
    assert discover._same_disk_there(str(missing)) is True
    monkeypatch.setattr(discover.os.path, "exists", lambda p: False)
    assert discover._same_disk_there(str(missing)) is False


def test_a_record_with_only_the_check_is_not_a_setup_to_continue(tmp_path):
    """Reloading the first screen is not "a setup that stopped": only the quiet
    check of this computer had run (found in the headless Edge proof)."""
    from puffpai.wizard import runner as wizard_runner
    run = wizard_runner.Runner(args=types.SimpleNamespace(), cfg=get_config(),
                               non_interactive=True)
    run.entries = {"discover.card": {"outcome": "done", "at": 1.0}}
    assert wizard_runner.nothing_answered(run) is True
    run.entries["runner.first_look"] = {"outcome": "done", "at": 2.0}
    assert wizard_runner.nothing_answered(run) is False
    run.entries = {}
    assert wizard_runner.nothing_answered(run) is False
    step = next(s for s in wizard_runner.SCHEDULE
                if s.entry == "runner.first_look")
    assert run._entry_label(step) == "What this computer has"
