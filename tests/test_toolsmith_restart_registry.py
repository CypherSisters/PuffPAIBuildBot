"""Restart and retry of a Toolsmith app (live run 2026-10-14).

* A restarted app came up on localhost:3000 (::1) instead of its port: the
  registry now records the address that really answers (its port on
  127.0.0.1 or ::1, else a local address its own start log printed).
* A retry registered the same repo a second time: one entry per app.
* /build/apps and /installed said "stopped" for an app on ::1: port_open
  checks 127.0.0.1 and ::1.
* The job said "found no content" right after Toolsmith saw 582 words: the
  proof reads the page with the same reader.

Processes are mocked; the only sockets are on this computer's loopback.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import os
import socket

import pytest

from puffpai.build import toolsmith_run as T


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "_state_dir", lambda: str(tmp_path))
    return tmp_path


def _write(state, reg):
    (state / T.REGISTRY).write_text(json.dumps(reg), encoding="utf-8")


def test_port_open_sees_an_app_listening_on_ipv6_loopback_only():
    if not socket.has_ipv6:
        pytest.skip("no IPv6 here")
    s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    try:
        s.bind(("::1", 0))
    except OSError:
        pytest.skip("::1 cannot be bound here")
    s.listen(8)
    try:
        port = s.getsockname()[1]
        assert T.answering_host(port) == "localhost"
        assert T.port_open(port)
    finally:
        s.close()
    assert not T.port_open("nope")


def test_running_apps_say_running_for_an_app_on_ipv6(state, monkeypatch):
    _write(state, {"wm-1": {"name": "worldmonitor", "url": "http://localhost:3000/",
                            "port": 3000, "folder": "x"}})
    monkeypatch.setattr(T, "answering_host", lambda port: "localhost" if port == 3000 else "")
    assert T.running_apps()[0]["running"] is True


def test_restart_passes_the_same_port_and_records_the_one_that_answers(state, monkeypatch):
    folder = state / "host_apps" / "bb-wm-1"
    folder.mkdir(parents=True)
    _write(state, {"wm-1": {"name": "worldmonitor", "url": "http://127.0.0.1:5199/",
                            "port": 5199, "folder": str(folder), "pids": [1],
                            "starts": [{"command": "npm run dev", "port": 5199}]}})
    spawned = []

    class P:
        pid = 4321

    def spawn(cmd, f, port, project, log):
        spawned.append((cmd, port, project))
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with open(log, "w", encoding="utf-8") as fh:
            fh.write("  VITE ready\n  \x1b[32m->\x1b[0m  Local:   http://localhost:3000/\n")
        return P()
    monkeypatch.setattr(T, "spawn_server", spawn)
    monkeypatch.setattr(T, "_program", lambda name: None)
    monkeypatch.setattr(T, "say", lambda line: None)
    up = {"on": False}
    monkeypatch.setattr(T, "answering_host",
                        lambda port: "localhost" if up["on"] and int(port) == 3000 else "")
    monkeypatch.setattr(T.time, "sleep", lambda s: up.update(on=True))
    got = T.restart("wm-1", wait=5)
    assert spawned == [("npm run dev", 5199, "bb-wm-1")]
    assert got["ok"] and got["url"] == "http://localhost:3000/"
    entry = T.registry()["wm-1"]
    assert entry["port"] == 3000 and entry["url"] == "http://localhost:3000/"
    assert entry["pids"] == [4321]


def test_a_retry_of_the_same_repo_updates_its_entry(state, monkeypatch):
    _write(state, {"worldmonitor-aaa111": {"name": "worldmonitor", "ref": "github:k/worldmonitor",
                                           "repo": "k/worldmonitor", "port": 3000,
                                           "url": "http://127.0.0.1:3000/", "folder": "old"},
                   "other-bbb222": {"name": "other", "repo": "o/other", "port": 4000,
                                    "url": "http://127.0.0.1:4000/", "folder": "o"}})
    monkeypatch.setattr(T, "answering_host", lambda port: "")
    entry = {"name": "worldmonitor", "repo": "k/worldmonitor", "port": 3001,
             "url": "http://127.0.0.1:3001/", "folder": "new"}
    got = T._same_app_id("worldmonitor-b292c0", entry)  # noqa: SLF001
    assert got == "worldmonitor-aaa111"
    T._keep(got, entry)  # noqa: SLF001
    reg = T.registry()
    assert sorted(reg) == ["other-bbb222", "worldmonitor-aaa111"]
    assert reg["worldmonitor-aaa111"]["port"] == 3001
    # A new repo keeps its own id.
    assert T._same_app_id("new-ccc333", {"repo": "n/new", "folder": "n"}) == "new-ccc333"  # noqa: SLF001


def test_a_second_copy_that_still_answers_on_its_own_port_is_left_alone(state, monkeypatch):
    _write(state, {"wm-aaa111": {"repo": "k/worldmonitor", "port": 3000, "folder": "old"}})
    monkeypatch.setattr(T, "answering_host", lambda port: "127.0.0.1" if port == 3000 else "")
    assert T._same_app_id("wm-b292c0", {"repo": "k/worldmonitor", "port": 3001,  # noqa: SLF001
                                         "folder": "new"}) == "wm-b292c0"
    assert "wm-aaa111" in T.registry()


def test_the_proof_uses_the_same_reader_toolsmith_saw_working(monkeypatch):
    from puffpai.build import prove
    from puffpai.integrate import host_tools
    job = {"id": "j1", "route": "A", "ask": "What is on the dashboard?"}
    made = {"ok": True, "screen": {"kind": "own", "url": "http://localhost:3000/",
                                   "toolsmith_app": "wm-1"},
            "made": {"toolsmith_app": "wm-1", "app_name": "worldmonitor"}}
    read = []
    monkeypatch.setattr(host_tools, "read_page", lambda url: read.append(url) or {
        "ok": True, "text": "World news live map earthquakes elections markets"})
    monkeypatch.setattr(prove, "page_answers", lambda url, **kw: "")
    monkeypatch.setattr(prove, "_read_answer", lambda *a, **k: "the brain looked at its page "
                                                               "but found no content there yet")
    got = prove.prove(job, {}, made=made)
    assert got["state"] == "working", got
    assert read == ["http://localhost:3000/"]
    monkeypatch.setattr(host_tools, "read_page", lambda url: {
        "ok": False, "said": "Its page showed no text yet; it may still be loading."})
    got = prove.prove(job, {}, made=made)
    assert got["state"] == "not_yet" and "no content" in got["reason"]


def test_stale_copies_of_one_repo_collapse_to_the_running_one(state, monkeypatch):
    """Live 2026-10-14: a stopped worldmonitor-b292c0 (port 3000, from before
    the one-entry fix) stayed in /build/apps beside the running copy."""
    old, new, gone = state / "bb-wm-old", state / "bb-wm-new", state / "bb-x-gone"
    old.mkdir()
    new.mkdir()
    _write(state, {
        "wm-new": {"name": "worldmonitor", "port": 56673, "folder": str(new),
                   "ref": "github:koala73/worldmonitor", "repo": "koala73/worldmonitor",
                   "job": "world-news-map", "started_at": 100},
        "wm-old": {"name": "worldmonitor", "port": 3000, "folder": str(old),
                   "ref": "github:koala73/worldmonitor", "job": "news-report-maker",
                   "started_at": 200},
        "x-1": {"name": "x", "port": 4100, "folder": str(gone), "ref": "github:o/x"},
        "fp-1": {"name": "feedparser", "port": 64254, "folder": str(old),
                 "ref": "code:kurtmckee/feedparser", "repo": "kurtmckee/feedparser"}})
    monkeypatch.setattr(T, "port_open", lambda port: port in (56673, 64254))
    # x-1 (folder gone, the only copy of its repo) stays listed as stopped.
    assert [a["id"] for a in T.running_apps()] == ["wm-new", "x-1", "fp-1"]
    on_disk = json.loads((state / T.REGISTRY).read_text(encoding="utf-8"))
    assert sorted(on_disk) == ["fp-1", "wm-new", "x-1"]


def test_entries_without_a_repo_never_collapse(state, monkeypatch):
    here = state / "bb-a"
    here.mkdir()
    _write(state, {"a-1": {"port": 1, "folder": str(here)},
                   "b-2": {"port": 2, "folder": str(here)},
                   "c-3": {"port": 3}})
    monkeypatch.setattr(T, "port_open", lambda port: False)
    assert sorted(T.tidy_registry()) == ["a-1", "b-2", "c-3"]


def test_of_two_stopped_copies_the_newest_stays(state, monkeypatch):
    a, b = state / "bb-a", state / "bb-b"
    a.mkdir()
    b.mkdir()
    _write(state, {"a-1": {"port": 1, "folder": str(a), "ref": "github:o/r", "started_at": 5},
                   "b-2": {"port": 2, "folder": str(b), "ref": "github:o/r", "started_at": 9}})
    monkeypatch.setattr(T, "port_open", lambda port: False)
    assert sorted(T.tidy_registry()) == ["b-2"]


def test_restart_all_tidies_before_starting(state, monkeypatch):
    a, b = state / "bb-a", state / "bb-b"
    a.mkdir()
    b.mkdir()
    _write(state, {"a-1": {"port": 1, "folder": str(a), "ref": "github:o/r", "started_at": 5},
                   "b-2": {"port": 2, "folder": str(b), "ref": "github:o/r", "started_at": 9}})
    monkeypatch.setattr(T, "port_open", lambda port: False)
    calls = []
    monkeypatch.setattr(T, "restart", lambda app_id, wait=60.0: calls.append(app_id) or
                        {"ok": True, "url": "http://127.0.0.1:2/"})
    assert T.restart_all(lambda _l: None) == ["b-2"] and calls == ["b-2"]


def test_kept_for_finds_a_repo_kept_with_its_folder(state):
    here = state / "bb-wm"
    here.mkdir()
    _write(state, {"wm-1": {"port": 1, "folder": str(here), "ref": "awesome-selfhosted:wm",
                            "repo": "koala73/worldmonitor"},
                   "z-1": {"port": 2, "folder": str(state / "nope"), "ref": "github:o/z"}})
    assert T.kept_for({"ref": "github:koala73/worldmonitor",
                       "url": "https://github.com/koala73/worldmonitor"}) == "wm-1"
    assert T.kept_for({"ref": "github:o/z"}) == ""
