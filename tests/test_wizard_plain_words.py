"""Plain words: the wizard never shows its builder the machinery's names.

Without this, the wizard is "very hard to read and understand".
Every string a builder sees -- the terminal conversation, and the browser's
/wizard answers -- must be plain everyday English. This test runs the real
wizard for three different goals (G1 space, G3 car, G5 knitting), each in both
wordings (a 10-year-old and an adult), collects what it printed and what the
/wizard routes return, and fails on any word from the banned list.

Scope: everything a builder or an
owner reads -- the whole terminal run including the closing screen, every
card the browser is sent (every package: sources, integrate, handoff and
surfaces too), the closing screen over HTTP, and, in
``test_every_owner_facing_sentence_elsewhere_is_plain``, what the bot says
about itself, the build summary lines, the internet-setting and bridge
labels, every fix command, the privacy explainer, the live-card and routine
messages, and the installers' printed lines.

Allowed: product names (PuffPAI, BuildBot, Ollama, Home Assistant), host names
(registry.ollama.ai), and file names nowhere.

Run:
    pytest tests/test_wizard_plain_words.py
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _wizard_driver import drive_in_process  # noqa: E402

from puffpai import brain_health, hardware_census, plain_words, setup_ops  # noqa: E402
from puffpai.config import get_config  # noqa: E402
from puffpai.wizard import netcheck, sources_suggest  # noqa: E402

#: The one banned list lives in puffpai.plain_words; this file adds
#: only the wizard's own layout rules (status lines, words in capitals).
_STATUS = re.compile(r"status:", re.I)
_SHOUTING = re.compile(r"(?<![A-Za-z0-9-])([A-Z]{2,})\s+([A-Z]{2,})(?![A-Za-z0-9-])")
_ACRONYMS = {"NVIDIA", "RTX", "GTX", "GPU", "VRAM", "CPU", "RAM", "NASA",
             "API", "APIS", "AMD", "USB", "SSD", "GB", "MB", "PIN", "AI", "OK",
             "ESA", "USA", "UK", "PC", "TV", "DIY",
             # Licence names printed as their owners write them (CC BY-NC).
             "CC", "BY", "NC", "SA", "ND", "US", "USDA", "NOAA", "NHTSA",
             "ISS", "ZIP"}

GOALS = {
    "G1": "I want a space bot that knows everything about space, shows me cool "
          "space stuff, and works with my smart telescope",
    "G3": "Help me keep my 2014 Honda Civic running - maintenance, recalls, "
          "how-tos",
    "G5": "A knitting and crochet buddy with patterns and tips",
    "G4": "A garden helper for my vegetable beds in Ohio",
}
AGES = {"simple": "10", "standard": "44"}
PIN = "Telescope-Infinity-9042"

#: The packages this file covers; their cards are checked on the API side.
OWNED = {"discover", "purpose", "brain", "tools", "context", "crew", "secure"}

#: The keys of an API answer that carry words for a person. Ids, values,
#: option values and commands are the machine's, and are not checked here.
TEXT_KEYS = {"title", "decided", "label", "help", "current", "detail",
             "intro", "heading", "empty", "what", "why", "impact", "checked",
             "total", "leaves", "reach", "what", "pin", "pin_help", "again",
             "again_help", "building_line", "brain_off_label"}


def offences(text: str) -> list[str]:
    """Every banned thing in *text*, as ``word: the line it was on``."""
    found: list[str] = plain_words.check(text)
    for line in str(text).splitlines():
        if _STATUS.search(line):
            found.append(f"status line: {line.strip()}")
        # Shouting: two capital-letter words in a row ("NOT READY",
        # "FINISHED BOT"). A name in capitals (NASA, NVIDIA RTX) is not.
        for first, second in _SHOUTING.findall(line):
            if first not in _ACRONYMS and second not in _ACRONYMS:
                found.append(f"capitals: {line.strip()}")
                break
    return found


def test_the_checker_catches_the_old_lingo():
    """The list is not vacuous: every phrase the owner quoted is caught."""
    old = [
        "What may the FINISHED BOT reach when it runs? -- sealed, island, enumerated",
        "How far may your bot reach? -- open_yard, fenced, moat, drawbridge_up",
        "Individual doors belong to the enumerated tier",
        "Routing tier: guided (conservative default)",
        "What your bot will be allowed to touch -- comms.calendar, daily.fitness",
        "What this build may reach   [secure:posture]",
        "  status: ready",
        "Step 13 of 14 - The build manifest.",
        "Zone 1, the Vault",
        "Setup finished - NOT READY: the brain is missing.",
        # One list: pack jargon and command text.
        "Put library files (.zim) in a folder",
        "Indexing your packs, shard 3 of 9",
        "run: puffpai setup --scaffold",
    ]
    for line in old:
        assert offences(line), line
    for fine in ("Downloads from registry.ollama.ai.", "Weather outdoors",
                 "Download: 900 MB, from download.kiwix.org.",
                 "Your PIN opens it.", "Left out: NASA Open APIs",
                 "NVIDIA RTX 2000 Ada Generation Laptop GPU with 8.0 GB VRAM",
                 "Your time zone is New York.", "UV index: 5",
                 "It is saved in settings.json.",
                 "      24QVQ-W889R-64T8C-QXHVP-DWJJR"):
        assert not offences(fine), (fine, offences(fine))


@pytest.fixture
def offline_machine(monkeypatch, tmp_path):
    """A pinned machine that reaches nothing, whatever the plan says."""
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("COLUMNS", "80")
    get_config(_force_reload=True)
    census = hardware_census.run_census()
    monkeypatch.setattr(hardware_census, "run_census",
                        lambda *a, **k: copy.deepcopy(census))
    monkeypatch.setattr(setup_ops, "list_models", lambda *a, **k: [])
    monkeypatch.setattr(setup_ops, "ollama_survey", lambda *a, **k: [])
    # The internet "answers", so the plan has downloads on it to word -- and
    # nothing is ever fetched or pulled.
    monkeypatch.setattr(netcheck, "internet_reachable", lambda *a, **k: True)
    monkeypatch.setattr(setup_ops, "fetch_source", lambda *a, **k: {
        "ok": False, "reason": "tests never download"})
    monkeypatch.setattr(brain_health, "pull_model", lambda *a, **k: {
        "ok": False, "message": "tests never download"})
    monkeypatch.setattr(sources_suggest, "suggest", lambda *a, **k: [])
    yield tmp_path
    get_config(_force_reload=True)


def _script(goal: str, age: str) -> list[tuple[str, object]]:
    return [("what kind of bot", goal), ("how old are you", age),
            ("choose a vault pin", PIN), ("pick a pin", PIN),
            ("type it again", PIN), ("type the same pin again", PIN),
            ("type the recovery code back", "skip")]


@pytest.mark.parametrize("goal_id", sorted(GOALS))
@pytest.mark.parametrize("mode", sorted(AGES))
def test_the_terminal_conversation_is_plain(offline_machine, monkeypatch,
                                            capsys, goal_id, mode):
    code, transcript, _asked = drive_in_process(
        ["setup", "--skip-ha", "--no-suggestions"],
        _script(GOALS[goal_id], AGES[mode]),
        monkeypatch=monkeypatch, capsys=capsys)
    assert code == 0
    # The whole run, the closing screen included.
    shown = transcript
    assert "Here is the plan for" in shown and "How to start it" in shown
    bad = offences(shown)
    assert not bad, f"{goal_id} {mode}:\n  " + "\n  ".join(bad)
    assert shown.isascii()


def _texts(value, key: str = "") -> list[str]:
    """Every person-facing string in an API answer, by key name.

    A choice field's ``current`` and ``proposed`` are the VALUE picked, which
    is the machine's id and never drawn -- its option's label is what a person
    reads, and labels are collected separately.
    """
    out: list[str] = []
    if isinstance(value, dict):
        machine_value = value.get("kind") in ("choice", "multi_choice", "toggle",
                                              "number", "secret_entry")
        for k, v in value.items():
            if machine_value and k in ("current", "proposed"):
                continue
            out.extend(_texts(v, k))
    elif isinstance(value, list):
        for item in value:
            out.extend(_texts(item, key))
    elif isinstance(value, str) and key in TEXT_KEYS:
        out.append(value)
    return out


def _option_labels(cards_: list[dict]) -> list[str]:
    return [opt["label"] for card in cards_ for field in card.get("fields", ())
            for opt in field.get("options") or ()]


@pytest.mark.parametrize("goal_id", sorted(GOALS))
@pytest.mark.parametrize("mode", sorted(AGES))
def test_the_browser_answers_are_plain(offline_machine, goal_id, mode):
    os.environ.setdefault("PUFFPAI_API_KEY", "d" * 64)
    from puffpai import api_server

    client = api_server.app.test_client()
    auth = {"Authorization": f"Bearer {api_server._API_KEY}"}
    texts: list[str] = []

    flow = client.get("/wizard/flow", headers=auth).json
    texts += _texts(flow)
    assert client.post("/wizard/apply", headers=auth, json={
        "skill": "purpose:goal",
        "answers": {"goal": GOALS[goal_id]}}).status_code == 200
    assert client.post("/wizard/apply", headers=auth, json={
        "skill": "purpose:identity",
        "answers": {"age": AGES[mode]}}).status_code == 200
    flow = client.get("/wizard/flow", headers=auth).json
    texts += _texts(flow)
    summary = client.post("/wizard/summary", headers=auth, json={}).json
    texts += _texts(summary)
    texts += [choice["label"] for choice in summary["reach"]["choices"]]
    texts += [choice["label"] for choice in summary["model_choices"]]
    texts += [action["label"] for action in summary["actions"]]

    body = client.get("/wizard/cards", headers=auth).json
    mine = body["cards"]
    assert {card["skill"] for card in mine} >= {"discover", "purpose", "brain",
                                                "secure", "crew", "context",
                                                "sources", "surfaces"}
    texts += _texts(mine) + _option_labels(mine)
    # Looks good, then the closing screen as the browser gets it.
    assert client.post("/wizard/approve", headers=auth,
                       json={"changes": {}}).status_code == 200
    client.post("/wizard/vault/skip", headers=auth, json={})
    client.post("/wizard/run", headers=auth, json={})
    flow = client.get("/wizard/flow", headers=auth).json
    assert flow.get("handoff"), "the closing screen reached the browser"
    texts += _texts(flow["handoff"])
    assert "on this terminal" not in json.dumps(flow["handoff"])

    bad = [hit for text in texts for hit in offences(text)]
    assert not bad, f"{goal_id} {mode}:\n  " + "\n  ".join(sorted(set(bad)))
    # The summary really is in the wording the age asked for.
    if mode == "simple":
        assert summary["mode"] == "simple"
        assert summary["intro"].startswith("Nothing has happened yet.")
    else:
        assert summary["mode"] == "standard"


def test_every_wording_entry_is_plain_in_both_versions():
    from puffpai.wizard import wording

    bad = []
    for key, entry in wording.TEXT.items():
        for mode, text in entry.items():
            filled = text.replace("{", "").replace("}", "")
            bad += [f"{key}/{mode}: {hit}" for hit in offences(filled)]
    assert not bad, "\n".join(bad)


def _fix_lines() -> list[str]:
    """Every fix command a closing screen can print, rendered for real."""
    from puffpai.wizard import fixes
    samples = {"source.fetch": {"source_id": "kiwix_wikipedia_en_nopic"},
               "persona.rename_id": {"persona_id": "sprout", "new_id": "sprout2"},
               "model.pull": {"model": "qwen2.5:7b-instruct"},
               "model.pull_at": {"model": "qwen2.5:7b-instruct",
                                 "url": "http://127.0.0.1:11435"},
               "outside.on": {"service": "weather"}}
    return [fixes.render({"kind": "command", "id": cid,
                          "args": samples.get(cid, {})})
            for cid in fixes.command_ids()]


def _installer_lines() -> list[str]:
    """What the installers print: the quoted text of every output line."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out: list[str] = []
    for name, verbs in (("install.ps1", ("Write-Host", "Say", "Step", "Info",
                                         "Warn", "Fail")),
                        ("install.sh", ("say", "step", "fail", "printf",
                                        "echo"))):
        with open(os.path.join(root, name), encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if text.startswith("#") or not text.startswith(verbs):
                    continue
                out += re.findall(r'"([^"$]{3,})"', text)
    return out


def test_every_owner_facing_sentence_elsewhere_is_plain(offline_machine,
                                                        monkeypatch):
    """The rest of what an owner reads, outside the wizard's own screens."""
    from livedata_support import feed_manifest, install, set_posture
    from puffpai import (build_manifest, build_posture, feeds, offers,
                         routines, self_knowledge, zone_bridge)
    from puffpai.security import privacy_explainer

    texts: list[str] = []
    for net in ("online", "offline"):
        for egress in ("sealed", "island", "enumerated"):
            manifest = {
                "build": {"name": "GardenBot"},
                "hardware": {"memory": {"total_gb": 32.0}},
                "brain": {"model": "qwen2.5:7b-instruct"},
                "crew": {"max_specialists": 2, "master_core": 7},
                "posture": build_posture.posture_manifest_block(
                    build_posture.assess_posture(net, egress, ["weather"])),
                "surfaces": {"allowlist": ["instruments.telescope"]},
            }
            texts += build_manifest.manifest_summary_lines(manifest)
            texts += self_knowledge.limits_summary(manifest)
            texts.append(build_posture.describe_combination(net, egress))
    for option in (build_posture.build_network_options()
                   + build_posture.run_egress_options()):
        texts += [option["label"], option["description"],
                  option.get("note") or ""]
    for row in build_posture.egress_switches():
        texts += [row["label"], row["description"]]
    for posture in zone_bridge.posture_menu():
        texts += [posture["label"], posture["summary"], posture["costs"],
                  *posture["allows"].values()]
    for flags in ((True, False, False), (False, True, False),
                  (False, False, False)):
        texts.append(zone_bridge.posture_for_environment(*flags)["reason"])
    texts += [self_knowledge.workflows_answer(rich=True),
              self_knowledge.workflows_answer(rich=False)]
    texts += _fix_lines()
    local = "http://127.0.0.1:11435"
    texts += privacy_explainer.plain_summary(vault=None, services=[],
                                             model_url=local)
    texts += [privacy_explainer.privacy_answer(rich=True, vault=None,
                                               model_url=local, services=[]),
              privacy_explainer.where_am_i_answer(rich=True, vault=None,
                                                  model_url=local)]
    texts += [f"{row['label']} {row.get('how_to_add') or ''}"
              for row in routines.ability_rows()]
    for item in offers.proposal_items(GOALS["G4"], []):
        texts += [item["label"], item["detail"]]
    # A live card in each state its owner can meet.
    for run_egress, consent in (("island", True), ("enumerated", None),
                                ("enumerated", False), ("enumerated", True)):
        set_posture(run_egress)
        install(feed_manifest())
        if consent is not None:
            feeds.set_allowed("skyshots", consent)
        state = feeds.service_state("skyshots")
        texts += [state["message"], state["how_to_fix"] or "",
                  state["what_leaves"]]
        texts += [f"{row['label']} {row.get('how_to_add') or ''}"
                  for row in feeds.ability_rows()]
    texts += _installer_lines()

    bad = sorted({hit for text in texts for hit in offences(text)})
    assert not bad, "; ".join(bad)
