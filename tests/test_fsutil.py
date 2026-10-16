"""atomic_write survives a reader holding the file open (Windows), and appends are whole."""
from __future__ import annotations

import os

import pytest

from puffpai import fsutil


def test_atomic_write_retries_while_the_file_is_held(tmp_path, monkeypatch):
    target = tmp_path / "p.json"
    target.write_text("old")
    real = os.replace
    fails = {"n": 2}

    def flaky(src, dst):
        if fails["n"]:
            fails["n"] -= 1
            raise PermissionError("held open")
        return real(src, dst)
    monkeypatch.setattr(fsutil.os, "replace", flaky)
    monkeypatch.setattr(fsutil.time, "sleep", lambda s: None)
    fsutil.atomic_write(target, "new")
    assert target.read_text() == "new"
    assert os.listdir(tmp_path) == ["p.json"], "no temporary file left"


def test_atomic_write_gives_up_cleanly_after_five_tries(tmp_path, monkeypatch):
    target = tmp_path / "p.json"
    target.write_text("old")
    tries = []

    def held(src, dst):
        tries.append(1)
        raise PermissionError("held open")
    monkeypatch.setattr(fsutil.os, "replace", held)
    monkeypatch.setattr(fsutil.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        fsutil.atomic_write(target, "new")
    assert len(tries) == 5
    assert target.read_text() == "old"
    assert os.listdir(tmp_path) == ["p.json"]


def test_a_real_open_reader_does_not_break_the_writer(tmp_path):
    target = tmp_path / "p.json"
    fsutil.atomic_write_json(target, {"a": 1})
    with open(target, encoding="utf-8") as held:
        try:
            fsutil.atomic_write_json(target, {"a": 2})
        except PermissionError:
            pass  # Windows may still refuse after the retries; never a crash elsewhere
        held.read()
    assert fsutil.read_json(target)["a"] in (1, 2)


def test_append_and_read(tmp_path):
    log = tmp_path / "log.txt"
    fsutil.atomic_append(log, "one")
    fsutil.atomic_append(log, "two\n")
    assert fsutil.read_text(log) == "one\ntwo\n"
    assert fsutil.read_json(tmp_path / "missing.json", {"x": 0}) == {"x": 0}
