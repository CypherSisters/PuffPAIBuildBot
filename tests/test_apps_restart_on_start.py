"""Apps come back after a backend restart: the apps Toolsmith got working
are started again in the background, one at a time, with plain lines; one
whose folder is gone is skipped; ``apps.restart_on_start: false`` turns it
off. Processes are mocked.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import types

from puffpai import api_server
from puffpai.build import toolsmith_run as T


def test_restart_all_starts_each_stopped_app_once_and_skips_a_missing_folder(tmp_path, monkeypatch):
    here = tmp_path / "bb-a"
    here.mkdir()
    reg = {"a-1": {"name": "Alpha", "port": 5101, "folder": str(here)},
           "b-2": {"name": "Beta", "port": 5102, "folder": str(tmp_path / "gone")},
           "c-3": {"name": "Gamma", "port": 5103, "folder": str(here)},
           "d-4": {"name": "Delta", "port": 5104, "folder": str(here)}}
    monkeypatch.setattr(T, "registry", lambda: reg)
    monkeypatch.setattr(T, "port_open", lambda port: port == 5103)       # already running
    calls = []

    def restart(app_id, wait=60.0):
        calls.append(app_id)
        if app_id == "d-4":
            return {"ok": False, "said": "Delta was started but does not answer yet."}
        return {"ok": True, "url": "http://127.0.0.1:5101/"}
    monkeypatch.setattr(T, "restart", restart)
    lines = []
    assert T.restart_all(lines.append) == ["a-1"]
    assert calls == ["a-1", "d-4"]
    assert "Beta: its folder is gone, so it was not started again." in lines
    assert "Alpha is running again at http://127.0.0.1:5101/." in lines
    assert not any("—" in line for line in lines)


def _threads(monkeypatch):
    started = []
    monkeypatch.setattr(api_server.threading, "Thread", lambda *a, **k: types.SimpleNamespace(
        start=lambda: started.append(k.get("target"))))
    return started


def test_the_backend_starts_them_in_the_background_by_default(monkeypatch):
    started = _threads(monkeypatch)
    monkeypatch.setattr(api_server._cfg, "_raw", {})
    ran = []
    monkeypatch.setattr(T, "restart_all", lambda say_line=None, **k: ran.append(1) or [])
    api_server._start_app_restarts(delay=0)  # noqa: SLF001
    assert len(started) == 1 and not ran          # in the background, not inline
    started[0]()
    assert ran == [1]


def test_the_owner_can_turn_it_off(monkeypatch, capsys):
    started = _threads(monkeypatch)
    monkeypatch.setattr(api_server._cfg, "_raw", {"apps": {"restart_on_start": False}})
    api_server._start_app_restarts(delay=0)  # noqa: SLF001
    assert started == []
    assert "not started again" in capsys.readouterr().out
