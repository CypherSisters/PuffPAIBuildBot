"""The bot's copy of an app's stored data: versions, 409, undo, caps."""
from __future__ import annotations

import json
import os

import pytest

from puffpai.webapps import mirror

APP = "shelf-keeper-abcdefgh"


def _data(n):
    return {"items": json.dumps([{"id": i} for i in range(n)])}


def test_versions_count_up_and_a_stale_write_gets_409():
    assert mirror.get(APP) == {"v": 0, "data": {}}
    assert mirror.put(APP, 0, _data(1)) == (200, {"v": 1})
    assert mirror.put(APP, 1, _data(2)) == (200, {"v": 2})
    # a page that loaded version 1 writes: refused, the newer data stays
    assert mirror.put(APP, 1, _data(9)) == (409, {"v": 2})
    assert mirror.get(APP)["data"] == _data(2)


def test_only_strings_and_whole_numbers_are_accepted():
    assert mirror.put(APP, 0, {"a": 1})[0] == 400
    assert mirror.put(APP, 0, ["a"])[0] == 400
    assert mirror.put(APP, "0", {"a": "b"})[0] == 400
    assert mirror.put(APP, True, {"a": "b"})[0] == 400
    assert mirror.version(APP) == 0


def test_at_most_5_mb():
    big = {"x": "y" * (mirror.MAX_BYTES + 10)}
    assert mirror.put(APP, 0, big)[0] == 413
    assert mirror.version(APP) == 0


def test_the_last_10_versions_are_kept():
    for v in range(15):
        assert mirror.put(APP, v, _data(v + 1))[0] == 200
    vdir = os.path.join(os.environ["PUFFPAI_STATE_DIR"], "webapps", APP, "versions")
    kept = sorted(int(n.split(".")[0]) for n in os.listdir(vdir))
    assert kept == list(range(6, 16))


def test_restore_undoes_the_last_change_only_while_nothing_newer_exists():
    mirror.put(APP, 0, _data(1))
    mirror.put(APP, 1, _data(2))              # the bot's change made version 2
    assert mirror.restore(APP, 2) == {"ok": True, "why": ""}
    assert mirror.get(APP) == {"v": 3, "data": _data(1)}
    mirror.put(APP, 3, _data(5))              # version 4
    mirror.put(APP, 4, _data(6))              # a newer write, version 5
    assert mirror.restore(APP, 4) == {"ok": False, "why": "newer_write"}
    assert mirror.get(APP)["data"] == _data(6)
    assert mirror.restore(APP, 99)["ok"] is False
    assert mirror.restore(APP, 0)["ok"] is False
    assert mirror.restore("not-an-app", 1) == {"ok": False, "why": "not_an_app"}


def test_restore_refused_when_the_older_copy_is_gone():
    for v in range(12):
        mirror.put(APP, v, _data(v + 1))
    # version 1 was dropped long ago, so version 2 cannot be undone to it
    assert mirror.restore(APP, 12)["ok"] is True
    for v in range(13, 25):
        mirror.put(APP, v, _data(v))
    cur = mirror.version(APP)
    os.remove(os.path.join(os.environ["PUFFPAI_STATE_DIR"], "webapps", APP, "versions", f"{cur - 1}.json"))
    assert mirror.restore(APP, cur) == {"ok": False, "why": "not_kept"}


def test_the_watch_log_is_capped_and_rotated_and_keeps_closed_fields(monkeypatch):
    monkeypatch.setattr(mirror, "WATCH_MAX_BYTES", 4000)
    for i in range(200):
        mirror.watch(APP, kind="blocked", host=f"h{i}.example.org", directive="connect-src",
                     cookie="puff_device=SECRET", reason="x" * 500)
    base = os.path.join(os.environ["PUFFPAI_STATE_DIR"], "webapps", APP)
    assert os.path.getsize(os.path.join(base, "watch.jsonl")) <= 4000
    assert os.path.exists(os.path.join(base, "watch.1.jsonl"))
    text = open(os.path.join(base, "watch.jsonl"), encoding="utf-8").read()
    assert "SECRET" not in text and "cookie" not in text
    row = json.loads(text.splitlines()[-1])
    assert set(row) <= {"time", "kind", "host", "directive", "method", "reason"}
    assert len(row["reason"]) <= 120


def test_check_copies_are_cleared_and_apps_are_not():
    label = "chk-abcdefghijklmnop"
    mirror.put(label, 0, _data(1))
    mirror.clear(label)
    assert mirror.get(label)["v"] == 0
    with pytest.raises(ValueError):
        mirror.clear(APP)


def test_an_undo_mark_takes_the_undo_back_to_before_the_whole_action():
    mirror.put(APP, 0, _data(1))              # before the action: version 1
    mirror.put(APP, 1, _data(2))              # the action's first write
    mirror.put(APP, 2, _data(3))              # and its second, version 3
    assert mirror.mark_undo(APP, 2, _data(1)) is False      # not the current version
    assert mirror.mark_undo(APP, 3, {"k": 1}) is False      # not stored data
    assert mirror.mark_undo("../x", 3, _data(1)) is False
    assert mirror.mark_undo(APP, 3, _data(1)) is True
    assert mirror.restore(APP, 3) == {"ok": True, "why": ""}
    assert mirror.get(APP) == {"v": 4, "data": _data(1)}


def test_undo_marks_are_pruned_with_their_version():
    mirror.put(APP, 0, _data(1))
    assert mirror.mark_undo(APP, 1, {})
    for v in range(1, 1 + mirror.KEEP_VERSIONS):
        mirror.put(APP, v, _data(v + 1))
    vdir = os.path.join(os.environ["PUFFPAI_STATE_DIR"], "webapps", APP, "versions")
    assert "1.undo.json" not in os.listdir(vdir)
