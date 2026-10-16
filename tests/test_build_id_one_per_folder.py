"""One build, one id, worked out once; and the old pairing file is never split.

Two problems this closes:

* g) Python's normcase and Node's toLowerCase disagree on some folder names
  (``İstanbul``, ``ΣΑΣ``), and long-path prefixes, short (8.3) names and
  junctions gave one folder several ids. Now Python resolves the folder
  canonically, works the id out, and writes both to ``<state>/build_id.json``;
  the dashboard reads that file and never computes an id. Without it (or with
  one written for another folder) the dashboard refuses the one decision it
  needs the id for, in plain words, and creates nothing.
* h) a second process of the SAME build read the owner record the first had
  just created (still empty), decided it was not the owner, and made its own
  pairing state, abandoning the old file. Now the record appears whole or not
  at all, and a reader that finds one being written waits or says "not yet"
  instead of forking.

Everything runs in throwaway folders; nothing touches this checkout's
dashboard/data folder or the real password store.
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from puffpai import pairing_code, plain_words
from puffpai.security import build_identity

REPO = Path(__file__).resolve().parents[1]
DASHBOARD = REPO / "dashboard"
PAIRING_TS = DASHBOARD / "app" / "_lib" / "server" / "pairing.ts"
SHOW_CODE_JS = DASHBOARD / "scripts" / "show-pairing-code.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None or not PAIRING_TS.is_file(),
                                reason="needs Node and the dashboard tree")


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    root = tmp_path / "copy"
    (root / "dashboard" / "data").mkdir(parents=True)
    monkeypatch.delenv("PUFF_PAIRING_STATE", raising=False)
    monkeypatch.delenv("PUFF_PAIRED_DEVICES", raising=False)
    monkeypatch.setattr(pairing_code, "_dashboard_dirs",
                        lambda start=None: [str(root / "dashboard")])
    return root


def _state(tmp_path, name):
    path = tmp_path / name
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _legacy(checkout):
    legacy = checkout / "dashboard" / "data" / "pairing.json"
    assert pairing_code.ensure_code(str(legacy)) is not None
    return legacy, json.loads(legacy.read_text(encoding="utf-8"))["secret"]


# ── g) one id per folder, however it is spelt ───────────────────────────────

def _spellings(real: str, tmp_path) -> dict[str, str]:
    out = {"plain": real, "trailing": real + os.sep, "dot": os.path.join(real, ".")}
    if sys.platform == "win32":
        out["upper"] = real.upper()
        out["long prefix"] = "\\\\?\\" + real
        junction = str(tmp_path / "junction")
        made = subprocess.run(["cmd", "/c", "mklink", "/J", junction, real],
                              capture_output=True, text=True)
        if made.returncode == 0:
            out["junction"] = junction
        buf = ctypes.create_unicode_buffer(1024)
        if (ctypes.windll.kernel32.GetShortPathNameW(real, buf, 1024)
                and buf.value.lower() != real.lower()):
            out["short name"] = buf.value
    else:
        link = str(tmp_path / "link")
        os.symlink(real, link)
        out["symlink"] = link
    return out


def test_one_folder_spelt_many_ways_is_one_build(tmp_path):
    real = _state(tmp_path, "Real State Folder")
    ids = {name: build_identity.build_id(p) for name, p in _spellings(real, tmp_path).items()}
    assert len(set(ids.values())) == 1, ids
    assert len(ids) >= 3


@pytest.mark.parametrize("name", ["İstanbul", "ΣΑΣ", "Straße", "ÄÖÜ"])
def test_the_id_and_the_folder_are_recorded_for_the_dashboard(tmp_path, name):
    folder = _state(tmp_path, name)
    me = build_identity.record(folder)
    assert me == build_identity.build_id(folder)
    rec = json.loads(Path(folder, build_identity.ID_FILE).read_text(encoding="utf-8"))
    assert rec == {"version": 1, "build": me,
                   "state_dir": build_identity.canonical_state_dir(folder)}
    assert os.path.samefile(rec["state_dir"], folder)
    # Asking again leaves the file alone.
    before = os.stat(Path(folder, build_identity.ID_FILE)).st_mtime_ns
    assert build_identity.record(folder) == me
    assert os.stat(Path(folder, build_identity.ID_FILE)).st_mtime_ns == before


def test_recording_into_a_new_state_folder_makes_it_owner_only(tmp_path):
    folder = str(tmp_path / "fresh" / "state")
    assert build_identity.record(folder)
    assert Path(folder, build_identity.ID_FILE).is_file()
    if os.name != "nt":
        assert (os.stat(folder).st_mode & 0o777) == 0o700


def test_a_record_copied_from_another_build_is_corrected(tmp_path):
    a, b = _state(tmp_path, "a"), _state(tmp_path, "b")
    build_identity.record(a)
    shutil.copy(Path(a, build_identity.ID_FILE), Path(b, build_identity.ID_FILE))
    assert build_identity.record(b) == build_identity.build_id(b)
    rec = json.loads(Path(b, build_identity.ID_FILE).read_text(encoding="utf-8"))
    assert rec["build"] == build_identity.build_id(b) != build_identity.build_id(a)


def test_finding_the_pairing_file_records_the_id(checkout, tmp_path):
    a = _state(tmp_path, "bot")
    pairing_code.state_path(state=a)
    assert Path(a, build_identity.ID_FILE).is_file()


def test_the_dashboard_never_works_out_an_id_itself():
    for path in (PAIRING_TS, SHOW_CODE_JS):
        text = path.read_text(encoding="utf-8")
        assert "buildId(" not in text, path
        assert "norm.toLowerCase()" not in text and ".update(norm" not in text, path
        assert "build_id.json" in text, path


def _node_run(args, *, cwd, state, extra_env=None):
    env = {k: v for k, v in os.environ.items()
           if k not in ("PUFF_PAIRING_STATE", "PUFF_PAIRED_DEVICES", "PUFFPAI_DATA_DIR")}
    env["PUFFPAI_STATE_DIR"] = state
    env.update(extra_env or {})
    return subprocess.run([NODE, *args], cwd=str(cwd), env=env,
                          capture_output=True, text=True, timeout=120)


_TS_PATH = (
    "import { pathToFileURL } from 'node:url';\n"
    "const P = await import(pathToFileURL(process.argv[2]).href);\n"
    "try { process.stdout.write('PATH ' + P.pairingStatePath()); }\n"
    "catch (e) { process.stdout.write((e instanceof P.PairingNotReady ? 'NOTREADY ' : 'ERR ') + e.message); }\n"
)


def _ts_path(checkout, state) -> str:
    runner = checkout / "runner_path.mjs"
    runner.write_text(_TS_PATH, encoding="utf-8")
    out = _node_run([str(runner), str(PAIRING_TS)], cwd=checkout / "dashboard", state=state)
    assert out.returncode == 0, out.stderr[-600:]
    return out.stdout.strip()


@needs_node
def test_without_a_recorded_id_the_web_page_refuses_and_creates_nothing(checkout, tmp_path):
    legacy, _ = _legacy(checkout)
    a = _state(tmp_path, "not-started-yet")
    said = _ts_path(checkout, a)
    assert said.startswith("NOTREADY ")
    message = said[len("NOTREADY "):]
    assert "Start the bot once" in message and not plain_words.check(message)
    assert not (legacy.parent / "pairing-owner.json").exists()
    assert not Path(a, "pairing").exists()
    # The code script says the same, in plain words, and stops.
    out = _node_run([str(SHOW_CODE_JS), "--path"], cwd=checkout / "dashboard", state=a)
    assert out.returncode == 2 and "Start the bot once" in out.stderr
    assert not plain_words.check(out.stderr.strip())
    # Once the bot has run, the page finds the old file as its own.
    build_identity.record(a)
    assert _ts_path(checkout, a) == f"PATH {legacy}"


@needs_node
def test_a_record_written_for_another_folder_is_not_trusted(checkout, tmp_path):
    _legacy(checkout)
    a, b = _state(tmp_path, "a"), _state(tmp_path, "b")
    build_identity.record(a)
    shutil.copy(Path(a, build_identity.ID_FILE), Path(b, build_identity.ID_FILE))
    assert _ts_path(checkout, b).startswith("NOTREADY ")   # b does not claim as a


@needs_node
@pytest.mark.skipif(sys.platform != "win32", reason="Windows spellings")
def test_the_web_page_and_python_agree_for_every_spelling(checkout, tmp_path):
    legacy, _ = _legacy(checkout)
    real = _state(tmp_path, "İstanbul ΣΑΣ")
    build_identity.record(real)
    for name, spelt in _spellings(real, tmp_path).items():
        assert _ts_path(checkout, spelt) == f"PATH {legacy}", name
        assert pairing_code.state_path(state=spelt) == str(legacy), name


# ── h) the owner record is never read half-written ──────────────────────────

def test_a_claim_being_written_is_waited_for_not_forked(checkout, tmp_path, monkeypatch):
    """verify3/p_pair.py: the other process of THIS build has created the
    owner record and not written it yet."""
    monkeypatch.setattr(pairing_code, "CLAIM_WAIT_SECONDS", 0.2)
    legacy, secret = _legacy(checkout)
    a = _state(tmp_path, "the-install")
    owner = legacy.parent / "pairing-owner.json"
    owner.write_text("", encoding="utf-8")
    assert pairing_code.state_path(state=a) is None       # "not yet", not "own"
    assert not Path(a, "pairing").exists()                # nothing forked
    owner.write_text(json.dumps({"version": 1, "build": build_identity.build_id(a),
                                 "claimedAt": "x"}), encoding="utf-8")
    assert pairing_code.state_path(state=a) == str(legacy)
    assert json.loads(legacy.read_text(encoding="utf-8"))["secret"] == secret


def test_a_reader_waits_for_a_claim_finished_meanwhile(checkout, tmp_path):
    import threading
    legacy, _ = _legacy(checkout)
    a = _state(tmp_path, "the-install")
    owner = legacy.parent / "pairing-owner.json"
    owner.write_text("", encoding="utf-8")

    def _finish():
        time.sleep(0.3)
        owner.write_text(json.dumps({"version": 1, "build": build_identity.build_id(a),
                                     "claimedAt": "x"}), encoding="utf-8")
    threading.Thread(target=_finish).start()
    assert pairing_code.state_path(state=a) == str(legacy)


def test_an_old_empty_claim_is_damage_and_everybody_gets_their_own(checkout, tmp_path):
    legacy, _ = _legacy(checkout)
    owner = legacy.parent / "pairing-owner.json"
    owner.write_text("", encoding="utf-8")
    old = time.time() - 3600
    os.utime(owner, (old, old))
    a = _state(tmp_path, "a")
    assert pairing_code.state_path(state=a) == os.path.join(a, "pairing", "pairing.json")


def test_the_claim_is_whole_and_leaves_no_temporary_file(checkout, tmp_path):
    legacy, _ = _legacy(checkout)
    a = _state(tmp_path, "a")
    assert pairing_code.state_path(state=a) == str(legacy)
    record = json.loads((legacy.parent / "pairing-owner.json").read_text(encoding="utf-8"))
    assert record["build"] == build_identity.build_id(a)
    assert sorted(os.listdir(legacy.parent)) == ["pairing-owner.json", "pairing.json"]


_CLAIMER = r"""
import os, sys, time
sys.path.insert(0, sys.argv[1])
from puffpai import pairing_code
while time.time() < float(sys.argv[4]):
    pass
print("OWNER" if pairing_code.keeps_legacy(sys.argv[2], sys.argv[3]) else "NOT")
"""


def test_many_builds_claiming_at_once_exactly_one_wins(checkout, tmp_path):
    legacy, _ = _legacy(checkout)
    start_at = time.time() + 2.0
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    procs = [subprocess.Popen([sys.executable, "-c", _CLAIMER, str(REPO / "src"),
                               str(legacy), _state(tmp_path, f"bot{i}"), str(start_at)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, env=env) for i in range(6)]
    answers = []
    for proc in procs:
        out, err = proc.communicate(timeout=120)
        assert proc.returncode == 0, err[-400:]
        answers.append(out.strip())
    assert answers.count("OWNER") == 1, answers
    record = json.loads((legacy.parent / "pairing-owner.json").read_text(encoding="utf-8"))
    assert len(record["build"]) == 16
    assert not [n for n in os.listdir(legacy.parent) if n.endswith(".tmp")]


@needs_node
def test_the_web_page_waits_out_a_claim_being_written_too(checkout, tmp_path):
    legacy, _ = _legacy(checkout)
    a = _state(tmp_path, "a")
    build_identity.record(a)
    (legacy.parent / "pairing-owner.json").write_text("", encoding="utf-8")
    said = _ts_path(checkout, a)
    assert said.startswith("NOTREADY ") and "Try again" in said
    assert not Path(a, "pairing").exists()


@needs_node
def test_the_web_page_claims_whole_and_python_agrees(checkout, tmp_path):
    legacy, _ = _legacy(checkout)
    a = _state(tmp_path, "a")
    build_identity.record(a)
    assert _ts_path(checkout, a) == f"PATH {legacy}"
    assert sorted(os.listdir(legacy.parent)) == ["pairing-owner.json", "pairing.json"]
    assert pairing_code.state_path(state=a) == str(legacy)
