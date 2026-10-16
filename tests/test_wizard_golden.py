"""The golden runs: what two scripted wizards write, and what one of them says.

This replaced ``tests/test_wizard_equivalence.py``. That test proved the package
runner wrote the same manifest as the straight line of ``_setup_*_step`` calls
it was ported from. The straight line is gone, so the control is gone with it,
and a comparison against nothing is not a proof.

What is checked instead is a **golden**: two scripted runs, their manifests
normalised and checked in under ``tests/golden/``, compared section by section.
The property is the same one the equivalence test had -- *what this wizard
writes does not change by accident* -- and it now holds without a second
implementation to hold it up.

Two runs, because they exercise different halves:

* ``offline_yes`` -- ``puffpai setup --yes``, offline, nothing typed. Every card
  accepts its own proposal, which is the path an installer script takes.
* ``stargazerbot_scripted`` -- the same wizard with somebody answering: the goal, a
  name and an age (10, so the simple wording) typed at the three questions, and
  Enter everywhere else -- which accepts the one summary with "Looks good". Its **console output** is a golden too, because spec F's promise is
  that the owner's screen does not change underneath them.

The machine is pinned (:data:`CENSUS`) so neither golden describes the box the
suite happens to run on, and the normalisation drops what genuinely moves --
timestamps, the temp data directory, this build's version.

**When a golden fails.** It is a diff, not a verdict. Read it: if the change is
one you meant, re-generate with ``PUFFPAI_WRITE_GOLDEN=1 pytest
tests/test_wizard_golden.py`` and commit the new golden *with* the change that
caused it, so the review sees both. If the change is one you did not mean, it
is the bug the golden exists to catch.

Run:
    pytest tests/test_wizard_golden.py
"""
from __future__ import annotations

import copy
import json
import os
import re
import socket

import pytest

from puffpai import build_manifest, cli, hardware_census, setup_ops
from puffpai.config import get_config
from puffpai.wizard import render_cli

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")

#: Set to re-write the goldens from this run rather than compare against them.
WRITE_ENV = "PUFFPAI_WRITE_GOLDEN"

#: The machine both goldens describe. A literal, not a probe: a golden taken
#: from whatever box ran the suite last would fail on the next one, and the
#: thing under test is the wizard's decisions, not this laptop's disk.
CENSUS: dict = {
    "schema": "hardware_census/v1",
    "os": {"system": "Linux", "release": "6.8.0", "machine": "x86_64",
           "python": "3.11.9", "source": "detected"},
    "cpu": {"model": "Golden Reference CPU", "arch": "x86_64",
            "logical_cores": 8, "source": "detected"},
    "memory": {"total_gb": 32.0, "available_gb": 24.0, "source": "detected"},
    "accelerator": {"kind": "none", "name": "", "vram_gb": 0.0,
                    "unified_memory": False, "source": "detected"},
    "soc": {"model": None, "is_jetson": False, "source": "detected"},
    "disk": {"path": "/golden", "total_gb": 500.0, "free_gb": 200.0,
             "source": "detected"},
    "hardware_class": "workstation",
    "model_budget_gb": 28.0,
    "notes": [],
}

#: Every run below is offline and skips the two steps that reach outward, so
#: nothing here downloads and nothing here talks to a real Ollama.
BASE_ARGV = ["setup", "--skip-ha", "--skip-knowledge",
             "--build-network", "offline"]

#: What the scripted owner types, keyed by a phrase in the prompt. Every key has
#: to be used at least once (:func:`_feeder`), so a card whose wording drifts
#: away from these is a failure here rather than a golden quietly recorded with
#: the defaults instead of the answers.
SCRIPT: dict[str, str] = {
    "what kind of bot do you want": "a stargazer bot for my telescopes and space",
    "what should we call it": "NebulaBot",
    "how old are you": "10",
}


@pytest.fixture
def pinned(monkeypatch, tmp_path):
    """One fixed machine, no models installed, an isolated data directory,
    and the default Ollama address (the goldens print it; a machine that
    points PUFFPAI_OLLAMA_URL elsewhere is not the wizard's decision). Nothing
    may connect to that address: every Ollama question is answered above."""
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("PUFFPAI_OLLAMA_URL", raising=False)
    real_connect = socket.socket.connect

    def _no_ollama(sock, address):
        if isinstance(address, tuple) and len(address) >= 2 and address[1] == 11434:
            raise ConnectionRefusedError("the golden runs never talk to Ollama")
        return real_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", _no_ollama)
    get_config(_force_reload=True)
    monkeypatch.setattr(hardware_census, "run_census",
                        lambda *a, **k: copy.deepcopy(CENSUS))
    monkeypatch.setattr(setup_ops, "list_models", lambda *a, **k: [])
    monkeypatch.setattr(setup_ops, "ollama_survey", lambda *a, **k: [])
    # The proof's start-up check asks whether `puffpai` runs from this shell.
    # The goldens were recorded from a checkout (it does not); CI installs the
    # package (it does). Pin the answer so the golden is the same everywhere.
    import shutil
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k:
                        None if name == "puffpai" else real_which(name, *a, **k))
    yield tmp_path
    get_config(_force_reload=True)


def _feeder(seen: set[str]):
    """A stand-in for the console's line reader that answers from :data:`SCRIPT`.

    Not stdin, on purpose. The show-once recovery ceremony reads from the same
    stream the card answers come from (CONVERTING.md), so a positional file of
    answers is one line out of step the moment a code is re-issued -- and end of
    file is sticky, so the symptom is a run that silently takes defaults. Keying
    on the prompt cannot go out of step.
    """

    def read(prompt: str) -> str:
        # Printed the way the console prints it, so the golden reads like the
        # screen did: each question, then what was typed after the mark.
        print(prompt)
        lowered = str(prompt or "").lower()
        for key, answer in SCRIPT.items():
            if key in lowered:
                seen.add(key)
                print(render_cli.ANSWER_MARK + answer)
                return answer
        print(render_cli.ANSWER_MARK)
        return ""

    return read


def _run(argv: list[str], *, script: bool, monkeypatch, capsys) -> tuple[dict, str]:
    """Run one wizard and return (manifest, console output)."""
    seen: set[str] = set()
    if script:
        monkeypatch.setattr(render_cli, "_read_line", _feeder(seen))
        monkeypatch.setattr(render_cli, "_read_secret",
                            lambda prompt: print(prompt) or "")
    capsys.readouterr()
    assert cli.main(list(argv)) == 0
    out = capsys.readouterr().out
    if script:
        assert seen == set(SCRIPT), (
            f"the scripted answers no longer match the prompts: "
            f"{sorted(set(SCRIPT) - seen)} was never asked")
    manifest = build_manifest.read_manifest()
    assert manifest, "the wizard wrote no manifest"
    return manifest, out


# -- normalisation ------------------------------------------------------------
#
# What is dropped is what moves for reasons that are not this wizard's
# decisions. Everything else is compared exactly: a golden that normalised away
# the thing it was watching would be a green test over a changed build.

#: Keys whose value is a clock reading, at any depth.
_MOVING_KEYS = ("generated_at", "expires", "uptime_s", "started_at",
                "finished_at", "duration_s", "elapsed_s")


def _settle(value, replacements: list[tuple[str, str]]):
    if isinstance(value, dict):
        return {key: _settle(item, replacements) for key, item in value.items()
                if not (key.endswith("_at") or key in _MOVING_KEYS)}
    if isinstance(value, list):
        return [_settle(item, replacements) for item in value]
    if isinstance(value, str):
        return _replace(value, replacements)
    return value


def _replace(text: str, replacements: list[tuple[str, str]]) -> str:
    for needle, token in replacements:
        if needle:
            text = text.replace(needle, token)
    return text


def _replacements(tmp_path) -> list[tuple[str, str]]:
    """Paths and versions, longest first so a parent does not eat a child."""
    from puffpai import __version__

    roots: list[tuple[str, str]] = []
    for path, token in ((str(tmp_path / "data"), "<data>"),
                        (str(tmp_path / "state"), "<state>"),
                        (str(tmp_path), "<tmp>")):
        roots.append((path, token))
        roots.append((path.replace("\\", "/"), token))
        roots.append((path.replace("\\", "\\\\"), token))
    roots.append((str(__version__), "<version>"))
    return sorted(roots, key=lambda row: -len(row[0]))


def _normalised(manifest: dict, tmp_path) -> dict:
    return _settle(manifest, _replacements(tmp_path))


#: Lines whose content is a clock or a path this build cannot fix. Dropped from
#: the console golden by pattern, so what is dropped is stated and small.
_DROP_LINE = re.compile(
    r"^\s*(about \d+s |Wrote |  crash log:|  log:)", re.IGNORECASE)

#: A pairing code, issued fresh on every run. Four and four, which is the shape
#: a recovery code has too -- so this scrub is also what keeps a checked-in
#: transcript from ever being a place a code could land
#: (:func:`test_the_goldens_carry_no_secret` checks the scrub held).
_CODE = re.compile(r"\b[A-Z0-9]{4}-[A-Z0-9]{4}\b")
#: "It stops working at 2:12 am" is the pairing code's clock, printed in a
#: sentence, in this computer's own time.
_CLOCK = re.compile(r"\b\d{1,2}:\d{2} (?:am|pm)\b")


def _console(out: str, tmp_path) -> str:
    replacements = _replacements(tmp_path)
    lines = []
    for line in out.replace("\r\n", "\n").split("\n"):
        line = _CODE.sub("<code>", _replace(line.rstrip(), replacements))
        line = _CLOCK.sub("<clock>", line)
        if _DROP_LINE.match(line):
            continue
        lines.append(line)
    return "\n".join(lines).strip() + "\n"


def _golden(name: str, produced):
    """Compare against the checked-in golden, or write it when asked to."""
    path = os.path.join(GOLDEN_DIR, name)
    text = (json.dumps(produced, indent=2, sort_keys=True) + "\n"
            if not isinstance(produced, str) else produced)
    if os.environ.get(WRITE_ENV):
        os.makedirs(GOLDEN_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        pytest.skip(f"wrote {name}")
    assert os.path.exists(path), (
        f"{name} is missing. Re-generate it with {WRITE_ENV}=1 and commit it "
        f"with the change that needed it.")
    with open(path, encoding="utf-8") as handle:
        expected = handle.read()
    if isinstance(produced, str):
        assert text == expected, (
            f"the wizard's console output changed. {name} is what the owner "
            f"saw; if you meant this, re-generate and commit it.")
        return
    # Written on Windows: a path's separator is not part of what is pinned.
    want = json.loads(expected.replace("\\\\", "/"))
    got = json.loads(text.replace("\\\\", "/"))
    assert set(want) == set(got), (
        f"the manifest's sections changed: "
        f"{sorted(set(got) ^ set(want))}")
    for section in sorted(want):
        assert got[section] == want[section], (
            f"the '{section}' section of the manifest changed")


# -- the goldens --------------------------------------------------------------

def test_the_offline_yes_run_writes_the_manifest_it_always_wrote(
        pinned, monkeypatch, capsys):
    manifest, _out = _run(
        BASE_ARGV + ["--yes", "--goal", "a space bot for my telescopes"],
        script=False, monkeypatch=monkeypatch, capsys=capsys)
    _golden("wizard_offline_yes.manifest.json",
            _normalised(manifest, pinned))


def test_the_scripted_stargazer_bot_writes_the_manifest_it_always_wrote(
        pinned, monkeypatch, capsys):
    manifest, _out = _run(BASE_ARGV, script=True, monkeypatch=monkeypatch,
                          capsys=capsys)
    assert manifest["build"]["name"] == "NebulaBot", (
        "the scripted name never reached the manifest, so this golden would "
        "pin a run nobody answered")
    _golden("wizard_stargazerbot_scripted.manifest.json",
            _normalised(manifest, pinned))


def test_the_scripted_stargazer_bot_prints_the_wizard_it_always_printed(
        pinned, monkeypatch, capsys):
    """The owner's screen does not change underneath them.

    The whole console transcript, normalised for paths and this build's version
    and nothing else. This is the test that says retiring the legacy path
    changed nothing an owner sees.
    """
    _manifest, out = _run(BASE_ARGV, script=True, monkeypatch=monkeypatch,
                          capsys=capsys)
    _golden("wizard_stargazerbot_scripted.console.txt", _console(out, pinned))


def test_the_goldens_carry_no_secret(pinned, monkeypatch, capsys):
    """A golden is checked in, so it is read by anybody. Nothing in it is a key.

    The scripted run sets no PIN, so there is no recovery code to leak; this
    asserts the shape anyway, because a golden is exactly the kind of file a
    secret gets committed in.
    """
    from puffpai.build_manifest import _SECRET_KEYS

    def keys_of(value, found: set):
        if isinstance(value, dict):
            for key, item in value.items():
                found.add(key)
                keys_of(item, found)
        elif isinstance(value, list):
            for item in value:
                keys_of(item, found)
        return found

    for name in sorted(os.listdir(GOLDEN_DIR)):
        path = os.path.join(GOLDEN_DIR, name)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        if not name.endswith(".json"):
            # The console golden is prose and it says the word PIN out loud --
            # that is the vault card explaining itself. What may not be in it is
            # a value: a recovery code is issued in groups of four, and this run
            # sets no PIN, so there is none to find.
            assert not re.search(r"\b[A-Z0-9]{4}-[A-Z0-9]{4}\b", text), (
                f"{name} holds something shaped like a recovery code")
            continue
        # By key, not by substring: "method": "pin" is how the vault block says
        # what kind of lock it is, and a text search cannot tell that from a
        # field called pin.
        carried = keys_of(json.loads(text), set()) & set(_SECRET_KEYS)
        assert not carried, f"{name} carries {sorted(carried)}"
