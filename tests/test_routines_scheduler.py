"""The routines clock (puffpai.scheduler): the engine's rules, with a fake source.

No thread and no wall clock: the clock is injected and ``tick()`` is called by
hand. What is asserted is the policy ported from HomeBotBuild's scheduler:
fire on time, at most once (also across a restart), missed while switched off,
never an occurrence that came due before its item existed, and a broken source
does not stop the others.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import pytest

from puffpai import scheduler as sched_mod
from puffpai.scheduler import Scheduler

T0 = 1_790_000_000.0  # a fixed "now", in epoch seconds


class Clock:
    def __init__(self, t: float = T0):
        self.t = t

    def __call__(self) -> float:
        return self.t


class FakeSource:
    name = "fake"

    def __init__(self, rows):
        self.rows = rows            # [{"id", "due_ms", "created_ms"?, ...}]
        self.marks = []
        self.runs = []
        self.flushes = 0

    def refresh(self):
        pass

    def occurrences(self):
        return [{"id": r["id"], "due_ms": r["due_ms"], "kind": "test",
                 "item": {"id": r["id"], "created_ms": r.get("created_ms")},
                 "label": r.get("label", r["id"]),
                 "late_ok_seconds": r.get("late_ok", 0),
                 "announce_missed": r.get("announce_missed", True)}
                for r in self.rows if not r.get("done")]

    def mark(self, occ, status, iso, ms):
        self.marks.append((occ["id"], status))
        for r in self.rows:
            if r["id"] == occ["id"]:
                r["done"] = True

    def sweep(self, now_ms, timeout_ms):
        return []

    def flush(self):
        self.flushes += 1

    def run(self, occ):
        self.runs.append(occ["id"])
        return {"ok": True}


def _sched(tmp_path, source, clock, said=None):
    return Scheduler([source], clock=clock, state_path=str(tmp_path / "sched.json"),
                     announce=(said.append if said is not None else None))


def test_due_now_runs_once_and_is_marked(tmp_path):
    clock = Clock()
    src = FakeSource([{"id": "a", "due_ms": int(T0 * 1000) - 5_000,
                       "created_ms": int(T0 * 1000) - 60_000}])
    s = _sched(tmp_path, src, clock)
    s.tick()                       # first tick: last_checked is None
    clock.t += 30
    out = s.tick()
    assert src.runs == ["a"]
    assert src.marks == [("a", "fired")]
    assert out["fired"] == []      # nothing new the second time


def test_not_due_yet_does_nothing(tmp_path):
    src = FakeSource([{"id": "a", "due_ms": int(T0 * 1000) + 60_000}])
    s = _sched(tmp_path, src, Clock())
    s.tick()
    assert src.runs == [] and src.marks == []


def test_no_double_run_across_a_restart(tmp_path):
    """The run token is persisted; a new Scheduler on the same state skips it."""
    clock = Clock()
    rows = [{"id": "a", "due_ms": int(T0 * 1000) - 1_000,
             "created_ms": int(T0 * 1000) - 60_000}]
    src = FakeSource(rows)
    _sched(tmp_path, src, clock).tick()
    assert src.runs == ["a"]
    rows[0].pop("done")            # the source forgot its own write
    again = FakeSource(rows)
    clock.t += 30
    _sched(tmp_path, again, clock).tick()
    assert again.runs == []


def test_missed_while_switched_off_is_said_once_not_run(tmp_path):
    clock = Clock()
    said: list[str] = []
    src = FakeSource([])
    s = _sched(tmp_path, src, clock, said)
    s.tick()                                        # bot was on at T0
    src.rows.append({"id": "r", "label": "Reminder: water the plants",
                     "due_ms": int((T0 + 3600) * 1000),
                     "created_ms": int(T0 * 1000)})
    clock.t = T0 + 5 * 3600                         # off for five hours
    out = s.tick()
    assert src.runs == []
    assert [m[1] for m in src.marks] == ["missed"]
    assert len(out["missed"]) == 1
    assert said == ["I was switched off, so I missed this: Reminder: water the plants."]


def test_a_quiet_miss_is_not_announced(tmp_path):
    clock = Clock()
    said: list[str] = []
    src = FakeSource([])
    s = _sched(tmp_path, src, clock, said)
    s.tick()
    src.rows.append({"id": "d", "due_ms": int((T0 + 60) * 1000),
                     "created_ms": int(T0 * 1000), "announce_missed": False})
    clock.t = T0 + 3 * 3600
    s.tick()
    assert said == [] and src.runs == []


def test_late_but_within_its_own_window_still_runs(tmp_path):
    clock = Clock()
    src = FakeSource([])
    s = _sched(tmp_path, src, clock)
    s.tick()
    src.rows.append({"id": "d", "due_ms": int((T0 + 60) * 1000),
                     "created_ms": int(T0 * 1000), "late_ok": 6 * 3600})
    clock.t = T0 + 2 * 3600
    s.tick()
    assert src.runs == ["d"]


def test_nothing_from_before_the_item_existed(tmp_path):
    """Due before it was created (or approved): recorded, never run or said."""
    clock = Clock()
    said: list[str] = []
    src = FakeSource([{"id": "old", "due_ms": int((T0 - 7200) * 1000),
                       "created_ms": int((T0 - 60) * 1000)}])
    s = _sched(tmp_path, src, clock, said)
    out = s.tick()
    clock.t += 30
    s.tick()
    assert src.runs == [] and said == []
    assert [o["id"] for o in out["silent"]] == ["old"]
    assert src.marks == [("old", "missed")]


def test_a_broken_source_does_not_stop_the_others(tmp_path):
    class Broken(FakeSource):
        name = "broken"

        def occurrences(self):
            raise RuntimeError("boom")

    good = FakeSource([{"id": "g", "due_ms": int(T0 * 1000) - 1_000,
                        "created_ms": int(T0 * 1000) - 60_000}])
    s = Scheduler([Broken([]), good], clock=Clock(),
                  state_path=str(tmp_path / "s.json"), announce=lambda t: None)
    s.tick()
    assert good.runs == ["g"]


def test_thread_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("PUFFPAI_SCHEDULER", "0")
    assert sched_mod.start_scheduler() is None


def test_default_source_is_the_routine_store():
    names = [s.name for s in sched_mod.build_default_sources()]
    # The clock also keeps the allowed live cards fresh.
    assert names == ["routines", "live_cards"]
