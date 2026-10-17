"""The Developer page's command line (puffpai.developer_terminal): a real
shell session, no question before each command.

* Every route needs the bot's own key AND a paired device.
* A session answers only the device that opened it; at most a few at once;
  one left alone too long ends; ending one kills the shell; all end when
  the bot stops.
* Off while commands are switched off (PUFFPAI_TOOL_LOOP=off).
* One audit line per command typed, with secrets hidden, never the output.
* The chat's run_command still asks before every command.

Most tests drive a small Python stand-in for the shell (fast, the same on
every computer); one drives the real shell. No network.
Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest

from puffpai import api_server, brain_commands, brain_tools, developer_terminal as term
from puffpai import plain_words as pw

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}
DEVICE = "deviceaaaa000001"
PAIRED = {**AUTH, "X-Puff-Device-Id": DEVICE}
OTHER = {**AUTH, "X-Puff-Device-Id": "devicebbbb000002"}
THIRD = {**AUTH, "X-Puff-Device-Id": "devicecccc000003"}

FAKE_SHELL = r"""
import os, sys, time
for line in sys.stdin:
    line = line.rstrip("\n")
    if line == "__DONE__":
        print("\x1ePUFFDONE 0 " + os.getcwd(), flush=True)
    elif line == "SLEEP":
        time.sleep(600)
    else:
        print("echo:" + line, flush=True)
"""
ROUTES = ("/developer/terminal/open", "/developer/terminal/input",
          "/developer/terminal/output", "/developer/terminal/interrupt",
          "/developer/terminal/close")


@pytest.fixture
def state(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("PUFFPAI_TOOL_LOOP", raising=False)
    monkeypatch.setattr(brain_commands, "_vault_dir",
                        lambda: str(tmp_path / "data" / "vault_data"))
    yield tmp_path / "state"
    term.close_all()


@pytest.fixture
def fake(monkeypatch, state):
    monkeypatch.setattr(term, "shell_argv", lambda: [sys.executable, "-u", "-c", FAKE_SHELL])
    monkeypatch.setattr(term, "_preamble", lambda: "")
    monkeypatch.setattr(term, "done_line", lambda: "__DONE__\n")
    return state


@pytest.fixture
def client():
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client()


def _audit_lines(state) -> list[dict]:
    path = state / "security_logs" / "audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _open(client, headers=PAIRED) -> str:
    got = client.post("/developer/terminal/open", headers=headers, json={})
    assert got.status_code == 200, got.get_json()
    return got.get_json()["id"]


def _run(client, sid, line, headers=PAIRED, since=0, seconds=20) -> dict:
    assert client.post("/developer/terminal/input", headers=headers,
                       json={"id": sid, "line": line}).status_code == 200
    end = time.monotonic() + seconds
    text, at = "", since
    while time.monotonic() < end:
        got = client.post("/developer/terminal/output", headers=headers,
                          json={"id": sid, "since": at}).get_json()
        text += got["text"]
        at = got["next"]
        if not got["busy"]:
            return {**got, "text": text}
    raise AssertionError(f"still running: {text!r}")


# -- the gates ---------------------------------------------------------------------

def test_every_route_needs_the_key(client, fake):
    assert client.get("/developer/terminal").status_code == 401
    for route in ROUTES:
        assert client.post(route, json={"id": "x"}).status_code == 401, route
    assert term._sessions == {}


def test_every_route_needs_a_paired_device(client, fake):
    assert client.get("/developer/terminal", headers=AUTH).status_code == 403
    for route in ROUTES:
        got = client.post(route, headers=AUTH, json={"id": "x"})
        assert got.status_code == 403 and got.get_json()["code"] == "not_paired", route
    assert term._sessions == {}


def test_a_session_runs_commands_and_keeps_going(client, fake):
    page = client.get("/developer/terminal", headers=PAIRED).get_json()
    assert page["line"] == term.TILE_LINE and page["off"] is False
    sid = _open(client)
    first = _run(client, sid, "hello")
    assert "echo:hello" in first["text"] and first["alive"]
    assert first["cwd"] == term.install_folder()
    second = _run(client, sid, "again", since=first["next"])
    assert "echo:again" in second["text"] and "echo:hello" not in second["text"]


def test_another_device_cannot_drive_or_read_the_session(client, fake):
    sid = _open(client)
    _run(client, sid, "mine")
    for route in ROUTES[1:]:
        got = client.post(route, headers=OTHER, json={"id": sid, "line": "theirs", "since": 0})
        assert got.status_code == 404, route
    # Still hers, still running, and "theirs" never reached it.
    got = _run(client, sid, "still")
    assert "echo:still" in got["text"] and "theirs" not in got["text"]


def test_closing_kills_the_shell(client, fake):
    sid = _open(client)
    proc = term._sessions[sid].proc
    assert client.post("/developer/terminal/close", headers=PAIRED,
                       json={"id": sid}).status_code == 200
    assert proc.poll() is not None
    assert client.post("/developer/terminal/input", headers=PAIRED,
                       json={"id": sid, "line": "x"}).status_code == 404


def test_a_session_left_alone_ends(client, fake, monkeypatch):
    sid = _open(client)
    s = term._sessions[sid]
    monkeypatch.setattr(term, "IDLE_SECONDS", 5)
    s.last = time.monotonic() - 10
    assert term.reap() == 1
    assert s.proc.poll() is not None
    assert client.post("/developer/terminal/output", headers=PAIRED,
                       json={"id": sid}).status_code == 404


def test_only_a_few_at_once(client, fake, monkeypatch):
    monkeypatch.setattr(term, "MAX_SESSIONS", 2)
    monkeypatch.setattr(term, "MAX_PER_DEVICE", 1)
    first = _open(client)
    old = term._sessions[first].proc
    second = _open(client)                       # her oldest one makes room
    assert first not in term._sessions and old.poll() is not None
    _open(client, OTHER)
    got = client.post("/developer/terminal/open", headers=THIRD, json={})
    assert got.status_code == 429 and got.get_json()["code"] == "too_many"
    assert second in term._sessions


def test_every_shell_ends_when_the_bot_stops(client, fake):
    procs = [term._sessions[_open(client, h)].proc for h in (PAIRED, OTHER)]
    term.close_all()
    assert all(p.poll() is not None for p in procs) and term._sessions == {}


def test_off_when_commands_are_switched_off(client, fake, monkeypatch):
    sid = _open(client)
    monkeypatch.setenv("PUFFPAI_TOOL_LOOP", "off")
    got = client.post("/developer/terminal/open", headers=PAIRED, json={})
    assert got.status_code == 403 and got.get_json()["message"] == term.COMMANDS_OFF
    assert client.post("/developer/terminal/input", headers=PAIRED,
                       json={"id": sid, "line": "x"}).status_code == 403
    assert client.get("/developer/terminal", headers=PAIRED).get_json()["off"] is True


# -- the audit log ---------------------------------------------------------------------

def test_one_audit_line_per_command_never_the_output(client, fake):
    sid = _open(client)
    _run(client, sid, "puffpai vault store x --vault-pin 4815")
    _run(client, sid, "$env:HA_TOKEN = 'abcdefgh12345678'")
    # A line that looks like a pasted key or password is refused before the
    # route runs (security.pasted_key_guard): never sent, never logged.
    got = client.post("/developer/terminal/input", headers=PAIRED,
                      json={"id": sid, "line": "export MY_PASSWORD=hunter2hunter2"})
    assert got.status_code == 400 and got.get_json()["key_paste"] is True
    lines = [l for l in _audit_lines(fake) if l["action"] == "developer_terminal_command"]
    assert len(lines) == 2
    text = json.dumps(_audit_lines(fake))
    for secret in ("4815", "abcdefgh12345678", "hunter2hunter2"):
        assert secret not in text
    assert "puffpai vault store x --vault-pin [hidden]" in text
    assert "echo:" not in text                   # never what it printed


def test_text_for_a_running_command_is_logged_without_the_text(client, fake):
    sid = _open(client)
    client.post("/developer/terminal/input", headers=PAIRED, json={"id": sid, "line": "SLEEP"})
    client.post("/developer/terminal/input", headers=PAIRED,
                json={"id": sid, "line": "my-answer-xyz"})
    events = [l["action"] for l in _audit_lines(fake)]
    assert events[-2:] == ["developer_terminal_command", "developer_terminal_typed"]
    assert "my-answer-xyz" not in json.dumps(_audit_lines(fake))


def test_known_secrets_are_hidden_in_the_audit_text(monkeypatch):
    monkeypatch.setattr(brain_commands, "_known_secrets", lambda: ["s3cr3t-value-123"])
    assert "s3cr3t-value-123" not in term.safe_command("curl -H 'x: s3cr3t-value-123' a")


# -- the chat is unchanged --------------------------------------------------------------

def test_the_chat_still_asks_before_every_command(state, monkeypatch):
    seen: list = []
    monkeypatch.setattr(brain_commands.subprocess, "run",
                        lambda *a, **k: seen.append(a) or subprocess.CompletedProcess(a, 0, "", ""))
    brain_commands.register()
    ctx = {"caller": "owner"}
    got = brain_tools.call("run_command", {"command": "puffpai where"}, ctx)
    assert not got["ok"] and seen == []          # no way to ask: nothing runs
    got = brain_tools.call("run_command", {"command": "puffpai where"},
                           dict(ctx, ask=lambda q: False))
    assert not got["ok"] and seen == []
    got = brain_tools.call("run_command", {"command": "puffpai where"},
                           dict(ctx, ask=lambda q: True))
    assert got["ok"] and len(seen) == 1


def test_the_command_bin_has_this_bots_own_puffpai(state):
    import os
    folder = brain_commands.shim_dir()
    text = open(os.path.join(folder, "puffpai"), encoding="utf-8").read()
    assert "-m puffpai" in text
    assert str(state).replace("\\", "/") in text
    assert folder in term.shell_env()[next(k for k in term.shell_env() if k.upper() == "PATH")]


def test_the_shell_gets_no_secret_variables(state, monkeypatch):
    monkeypatch.setenv("PUFFPAI_API_KEY", "should-not-pass")
    monkeypatch.setenv("SOME_TOKEN", "nor-this")
    env = term.shell_env()
    assert "PUFFPAI_API_KEY" not in env and "SOME_TOKEN" not in env


# -- the words ------------------------------------------------------------------------

def test_every_word_she_reads_is_plain():
    lines = [term.TILE_LINE, term.NOT_PAIRED, term.COMMANDS_OFF,
             term.TOO_MANY.format(n=4), term.GONE, term.NO_SHELL,
             term.STOPPED_RESTARTED.format(cwd="C:/x"), term.ENDED]
    for line in lines:
        assert pw.check(line) == [], line
        assert "\u2014" not in line and "door" not in line.lower()


# -- the real shell ---------------------------------------------------------------------

@pytest.mark.skipif(term.shell_argv() is None, reason="no shell on this computer")
def test_the_real_shell_keeps_its_state_stops_and_ends(client, state):
    nt = sys.platform == "win32"
    sid = _open(client)
    got = _run(client, sid, "$x = 'kept-123'" if nt else "x=kept-123")
    got = _run(client, sid, "echo $x", since=got["next"])
    assert "kept-123" in got["text"]
    got = _run(client, sid, "cmd /c exit 3" if nt else "sh -c 'exit 3'", since=got["next"])
    assert "(exit code 3)" in got["text"]
    # Ctrl+C stops a long command; the command line goes on.
    client.post("/developer/terminal/input", headers=PAIRED,
                json={"id": sid, "line": "Start-Sleep 120" if nt else "sleep 120"})
    time.sleep(1)
    assert client.post("/developer/terminal/interrupt", headers=PAIRED,
                       json={"id": sid}).status_code == 200
    # The stopped command's end comes back from the shell a moment later
    # (Linux and macOS: "(exit code 130)"); the next command follows it, as
    # it does for a person. Sent sooner, it would go to the stopping command.
    end = time.monotonic() + 20
    while True:
        got = client.post("/developer/terminal/output", headers=PAIRED,
                          json={"id": sid, "since": got["next"]}).get_json()
        if not got["busy"]:
            break
        assert time.monotonic() < end, "the stopped command did not end"
        time.sleep(0.1)
    got = _run(client, sid, "echo after-stop", since=got["next"])
    assert "after-stop" in got["text"]
    proc = term._sessions[sid].proc
    client.post("/developer/terminal/close", headers=PAIRED, json={"id": sid})
    assert proc.poll() is not None
