"""No path makes a service the owner switched OFF read as ON or "as built".

Four ways an OFF used to come back:

1. the damaged file was kept aside, then the fresh write failed: the next read
   found no file and answered "as built", with no note for the owner;
2. two processes: B's write landed between A's rename and A's fresh write
   (the lock was per thread), so B's file had no reset record;
3. one damaged entry (``{"on": "false"}``, ``null``) was silently dropped to
   "as built" instead of being treated as damage;
4. a deleted file read as "as built".

Now the reset record is written to a guard file FIRST, every change holds a
cross-process lock, a damaged entry is damage, and a missing file after
switches were ever written here is damage.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from puffpai.security import outside_switches as sw

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean():
    sw.forget()
    yield
    sw.forget()


def _write_raw(obj) -> None:
    with open(sw.path(), "w", encoding="utf-8") as fh:
        fh.write(obj if isinstance(obj, str) else json.dumps(obj))
    sw.forget()


def _owner_off_library() -> None:
    sw.set_switch("library", False)
    sw.forget()
    assert sw.get("library") is False


def _kept_aside() -> list[str]:
    folder = os.path.dirname(sw.path())
    return sorted(n for n in os.listdir(folder) if n.startswith(sw.DAMAGED_PREFIX))


# ── 1. the fresh write fails after the damaged file was kept aside ──────────

def test_a_failed_fresh_write_still_reads_as_off_now_and_later(monkeypatch):
    _owner_off_library()
    _write_raw("{garbage")
    real = sw._write_atomic

    def _fail_the_switch_file(target, payload):
        if os.path.normcase(target) == os.path.normcase(sw.path()):
            raise OSError("disk full")
        return real(target, payload)
    monkeypatch.setattr(sw, "_write_atomic", _fail_the_switch_file)
    assert sw.get("library") is False
    assert not os.path.exists(sw.path())        # kept aside, fresh write failed
    monkeypatch.setattr(sw, "_write_atomic", real)
    sw.forget()                                  # a later read, or another process
    assert sw.get("library") is False
    assert sw.get("weather") is False            # never set: off, not "as built"
    notice = sw.reset_notice()
    assert notice and notice["kind"] == "reset" and notice["kept_as"] in _kept_aside()
    # The owner's next write does not bring anything else back.
    sw.set_switch("weather", True)
    sw.forget()
    assert sw.get("library") is False and sw.get("weather") is True


def test_when_the_reset_cannot_be_recorded_nothing_is_moved_and_all_is_off(monkeypatch):
    _owner_off_library()
    _write_raw("{garbage")
    monkeypatch.setattr(sw, "_write_atomic",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    assert sw.get("library") is False
    assert sw.get("weather") is False
    assert os.path.exists(sw.path()) and _kept_aside() == []
    with pytest.raises(sw.SwitchesUnreadable):
        sw.set_switch("weather", True)


def test_the_verifiers_probe_whole_write_fails(monkeypatch):
    """A write that fails entirely."""
    _owner_off_library()
    _write_raw("{garbage")
    with monkeypatch.context() as m:
        m.setattr(sw, "_write_atomic",
                  lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
        assert sw.get("library") is False
    sw.forget()
    assert sw.get("library") is False
    assert sw.reset_notice()["kind"] == "reset"


# ── 2. two processes ────────────────────────────────────────────────────────

_PROCESS_A = r"""
import os, sys, time
os.environ["PUFFPAI_STATE_DIR"] = sys.argv[1]
from puffpai.security import outside_switches as sw
real = os.rename
def slow_rename(a, b):
    real(a, b)
    with open(sys.argv[2], "w") as fh:
        fh.write("renamed")
    time.sleep(float(sys.argv[3]))   # still holding the lock
os.rename = slow_rename
print(sw.get("library"))
"""


def _start_process_a(tmp_path, pause: float):
    flag = tmp_path / "a-renamed.flag"
    env = {**os.environ, "PYTHONPATH": str(REPO / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.Popen(
        [sys.executable, "-c", _PROCESS_A, os.path.dirname(sw.path()), str(flag),
         str(pause)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True)
    deadline = time.monotonic() + 60
    while not flag.exists():
        if proc.poll() is not None or time.monotonic() > deadline:
            out, err = proc.communicate(timeout=5)
            pytest.fail(f"process A did not get to its rename: {err[-400:]}")
        time.sleep(0.02)
    return proc


def test_another_processes_write_waits_for_the_reset_and_keeps_it(tmp_path):
    _owner_off_library()
    _write_raw("{garbage")
    proc = _start_process_a(tmp_path, pause=1.5)
    try:
        # A has renamed the damaged file and not yet written the fresh one.
        assert not os.path.exists(sw.path())
        started = time.monotonic()
        sw.set_switch("weather", True)           # B: the owner switches one on
        waited = time.monotonic() - started
    finally:
        out, err = proc.communicate(timeout=60)
    assert proc.returncode == 0, err[-400:]
    assert out.strip() == "False"
    assert waited >= 0.5                          # B waited for A's lock
    sw.forget()
    assert sw.get("library") is False             # the owner's OFF held
    assert sw.get("weather") is True
    assert sw.get("wikipedia_books") is False     # never set: off after a reset
    stored = json.loads(Path(sw.path()).read_text(encoding="utf-8"))
    assert stored["reset"]["kept_as"] in _kept_aside()


def test_even_without_the_lock_a_write_in_the_window_keeps_the_reset(monkeypatch):
    """verify3/p_sw2.py, with process A doing what it does now: the reset is
    recorded BEFORE the rename, so B's write in the window carries it."""
    _owner_off_library()
    _write_raw("{garbage")
    # Process A, step by step: reset record first, then the rename ...
    sw._write_guard({"at": "2026-09-24T00:00:00+00:00",
                     "kept_as": "outside_switches.damaged-20260924T000000000000Z.json",
                     "seen_at": None})
    os.rename(sw.path(), os.path.join(os.path.dirname(sw.path()),
                                      "outside_switches.damaged-20260924T000000000000Z.json"))
    # ... and process B writes before A's fresh write, lock or no lock.
    import contextlib
    monkeypatch.setattr(sw, "_changing", contextlib.nullcontext)
    sw.set_switch("weather", True)
    sw.forget()
    assert sw.get("library") is False
    assert sw.get("weather") is True
    assert sw.reset_notice()["kind"] == "reset"


def test_a_lock_held_too_long_refuses_the_write_and_changes_nothing(tmp_path, monkeypatch):
    sw.set_switch("library", False)
    _write_raw("{garbage")
    proc = _start_process_a(tmp_path, pause=3.0)
    try:
        monkeypatch.setattr(sw, "LOCK_TIMEOUT", 0.3)
        with pytest.raises(sw.SwitchesBusy) as caught:
            sw.set_switch("library", True)
        assert "Try again" in caught.value.message
    finally:
        proc.communicate(timeout=60)
    monkeypatch.setattr(sw, "LOCK_TIMEOUT", 10.0)
    sw.forget()
    assert sw.get("library") is False


# ── 3. one damaged entry is damage ──────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    {"library": {"on": "false", "by": "owner", "at": "x"}},
    {"library": None},
    {"library": {"by": "owner"}},
    {"Library!": {"on": False}},
    {"library": {"on": False, "by": 5}},
])
def test_a_damaged_entry_is_damage_not_as_built(bad):
    sw.set_switch("weather", False)
    _write_raw({"schema": sw.SCHEMA, "services": {"weather": {"on": False}, **bad}})
    assert sw.get("library") is False
    assert sw.get("weather") is False
    assert sw.reset_notice()["kind"] == "reset"
    assert len(_kept_aside()) == 1


# ── 4. a missing file ───────────────────────────────────────────────────────

def test_a_deleted_file_reads_as_off_after_switches_were_written():
    _owner_off_library()
    os.remove(sw.path())
    sw.forget()
    assert sw.get("library") is False
    assert sw.get("weather") is False
    notice = sw.reset_notice()
    assert notice["kind"] == "reset" and notice["kept_as"] == ""


def test_an_older_file_from_before_the_guard_is_remembered_on_first_read():
    """A file written before this change has no guard; reading it once
    records that switches were written, so deleting it later is seen."""
    _write_raw({"schema": sw.SCHEMA,
                "services": {"library": {"on": False, "by": "owner", "at": "x"}}})
    assert not os.path.exists(sw.guard_path())
    assert sw.get("library") is False
    assert os.path.exists(sw.guard_path())
    os.remove(sw.path())
    sw.forget()
    assert sw.get("library") is False


def test_a_new_build_with_nothing_written_is_as_built():
    assert not os.path.exists(sw.path()) and not os.path.exists(sw.guard_path())
    assert sw.get("library") is None
    assert sw.reset_notice() is None


def test_a_damaged_guard_counts_as_a_reset_and_is_repaired():
    _owner_off_library()
    Path(sw.guard_path()).write_text("{nope", encoding="utf-8")
    sw.forget()
    assert sw.get("weather") is False               # fail closed
    guard = json.loads(Path(sw.guard_path()).read_text(encoding="utf-8"))
    assert guard["written"] is True and guard["reset"]["at"]
    first = guard["reset"]["at"]
    sw.forget()
    sw.get("weather")
    again = json.loads(Path(sw.guard_path()).read_text(encoding="utf-8"))
    assert again["reset"]["at"] == first            # repaired once, then stable


def test_seeing_the_note_switches_nothing_on_and_survives_a_lost_file():
    _owner_off_library()
    _write_raw("{garbage")
    assert sw.get("library") is False
    assert sw.acknowledge_reset() is True
    sw.forget()
    assert sw.reset_notice()["seen"] is True
    os.remove(sw.path())
    sw.forget()
    assert sw.get("library") is False and sw.get("weather") is False
    assert sw.reset_notice()["seen"] is True
