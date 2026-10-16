"""``puffpai start`` / ``puffpai stop``: one command opens the bot.

The real start (both processes, a real browser address, Ctrl+C) is proven on
a PC separately; these tests hold the rules around it with no ports
opened: loopback only, usage reports off, the web page made ready first, one
plain pairing line in local time, and stop kills only what start recorded.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from puffpai import cli, plain_words, start_cli
from puffpai.config import get_config


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("PUFFPAI_PORT", raising=False)
    monkeypatch.delenv("PUFFPAI_DASHBOARD_PORT", raising=False)
    get_config(_force_reload=True)
    yield tmp_path
    get_config(_force_reload=True)


def test_the_commands_exist():
    parser = cli.build_parser()
    args = parser.parse_args(["start", "--port", "8264", "--dashboard-port", "3264",
                              "--no-browser"])
    assert args.func is start_cli.cmd_start
    assert (args.port, args.dashboard_port, args.no_browser) == (8264, 3264, True)
    assert parser.parse_args(["stop"]).func is start_cli.cmd_stop


def test_ports_flag_then_setting_then_default(state, monkeypatch):
    ns = argparse.Namespace(port=None, dashboard_port=None)
    backend, dash = start_cli.ports(ns)
    assert backend == get_config().api_server.port and dash == 3003
    monkeypatch.setenv("PUFFPAI_PORT", "8111")
    monkeypatch.setenv("PUFFPAI_DASHBOARD_PORT", "3111")
    assert start_cli.ports(ns) == (8111, 3111)
    assert start_cli.page_url() == "http://127.0.0.1:3111/"
    assert start_cli.ports(argparse.Namespace(port=8264, dashboard_port=3264)) == (8264, 3264)


def test_the_up_check_only_ever_asks_this_computer():
    assert start_cli.answers("http://example.com:80/health") is False
    assert start_cli.answers("http://192.168.1.2:8000/health") is False
    assert start_cli.answers("https://127.0.0.1:1/health") is False


def test_the_children_run_with_usage_reports_off_and_on_this_computer():
    env = start_cli.child_env(8264, 3264, "k" * 43)
    assert env["NEXT_TELEMETRY_DISABLED"] == "1"
    assert env["PUFFPAI_BACKEND_URL"] == "http://127.0.0.1:8264"
    assert env["PUFFPAI_API_KEY"] == "k" * 43


FAKE_NODE = str(Path("/opt/trusted-node/bin/node").resolve())
FAKE_NPM_CLI = str(Path("/opt/trusted-node/lib/node_modules/npm/bin/npm-cli.js").resolve())


def _trusted_node(monkeypatch, node=FAKE_NODE, npm_cli=FAKE_NPM_CLI):
    monkeypatch.setattr(start_cli, "_node", lambda: node)
    monkeypatch.setattr(start_cli, "_npm_cli", lambda n: npm_cli if n == node else None)


def test_no_node_is_a_plain_refusal(tmp_path, monkeypatch):
    said = []
    _trusted_node(monkeypatch, node=None)
    assert start_cli.web_page_ready(tmp_path, {}, say=said.append) is False
    assert "Node.js" in " ".join(said)
    assert plain_words.check("\n".join(said)) == []


def test_the_web_page_gets_its_parts_then_a_fresh_build(state, tmp_path, monkeypatch):
    dash = tmp_path / "dashboard"
    (dash / "scripts").mkdir(parents=True)
    calls = []
    _trusted_node(monkeypatch)

    def run(argv, cwd, env, **kw):
        calls.append((argv, env.get("NEXT_TELEMETRY_DISABLED")))
        if "ensure-fresh-build.js" in " ".join(argv):
            (dash / ".next").mkdir(exist_ok=True)
            (dash / ".next" / "BUILD_ID").write_text("x")
        return subprocess.CompletedProcess(argv, 0)

    said = []
    env = start_cli.child_env(8264, 3264, "k")
    assert start_cli.web_page_ready(dash, env, say=said.append, run=run) is True
    assert "ci" in calls[0][0] and "ensure-fresh-build.js" in " ".join(calls[1][0])
    # npm is npm's own npm-cli.js, run on the trusted node, both by full path
    assert calls[0][0][:3] == [FAKE_NODE, FAKE_NPM_CLI, "ci"]
    assert calls[1][0][0] == FAKE_NODE
    assert all(flag == "1" for _argv, flag in calls), "usage reports were on"
    assert plain_words.check("\n".join(said)) == []


def test_a_build_that_fails_says_where_the_details_are(state, tmp_path, monkeypatch):
    dash = tmp_path / "dashboard"
    (dash / "node_modules" / "next").mkdir(parents=True)
    _trusted_node(monkeypatch)
    said = []
    ok = start_cli.web_page_ready(
        dash, {}, say=said.append,
        run=lambda argv, **kw: subprocess.CompletedProcess(argv, 1))
    assert ok is False
    assert "could not be built" in said[-1] and "web-page-check.log" in said[-1]


def test_waiting_gives_up_with_the_log_tail(tmp_path):
    log = tmp_path / "bot.log"
    log.write_text("line one\nThe port is taken\n")
    ticks = iter(range(0, 1000, 5))
    said = []
    ok = start_cli.wait_up("your bot", "http://127.0.0.1:1/health", None, 20, log,
                           say=said.append, clock=lambda: next(ticks),
                           sleep=lambda s: None, check=lambda url: False)
    assert ok is False
    assert said[-1].strip() == "The port is taken"
    ok = start_cli.wait_up("your bot", "u", None, 20, log, say=said.append,
                           check=lambda url: True)
    assert ok is True and said[-1] == "  Your bot is ready."


def test_the_pairing_step_is_one_plain_line_in_local_time(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFF_PAIRING_STATE", str(tmp_path / "pairing.json"))
    line = start_cli.pairing_line(None)
    assert "\n" not in line
    assert re.search(r"Type [2-9A-Z]{4}-[2-9A-Z]{4} \(it works until "
                     r"\d{1,2}:\d{2} (?:am|pm)\)\.$", line), line
    assert plain_words.check(line) == []


def test_stop_stops_exactly_what_start_recorded(state, capsys):
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        record = {"backend_pid": sleeper.pid, "dashboard_pid": None}
        path = start_cli.run_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record))
        assert start_cli.cmd_stop(argparse.Namespace()) == 0
        out = capsys.readouterr().out
        assert "Stopped your bot." in out
        deadline = time.time() + 10
        while sleeper.poll() is None and time.time() < deadline:
            time.sleep(0.1)
        assert sleeper.poll() is not None, "the recorded process is still running"
        assert not path.exists()
    finally:
        if sleeper.poll() is None:
            sleeper.kill()


def test_stop_with_nothing_running_says_so(state, capsys):
    assert start_cli.cmd_stop(argparse.Namespace()) == 0
    assert "was not running" in capsys.readouterr().out
    path = start_cli.run_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"backend_pid": 999999, "dashboard_pid": None}))
    assert start_cli.cmd_stop(argparse.Namespace()) == 0
    assert "was not running" in capsys.readouterr().out
    assert not path.exists()


def test_the_owner_words_in_the_module_are_plain():
    source = Path(start_cli.__file__).read_text(encoding="utf-8")
    said = re.findall(r'say\((f?"[^"]*"(?:\s*f?"[^"]*")*)', source)
    assert said, "no owner lines found to check"
    # The f-string holes are filled with the bot's own values; the words
    # around them are what is checked.
    text = "\n".join(re.sub(r"\{[^}]*\}", "X", re.sub(r'^f?"|"$', "", s))
                     for s in said)
    assert plain_words.check(text) == []


# ---------------------------------------------------------------------------
# every program by full path, from host_programs
# ---------------------------------------------------------------------------

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows programs")


@pytest.fixture
def no_which(monkeypatch):
    import shutil

    def boom(*_a, **_k):
        raise AssertionError("shutil.which was asked")
    monkeypatch.setattr(shutil, "which", boom)


def test_start_cli_never_asks_shutil_which(no_which):
    source = Path(start_cli.__file__).read_text(encoding="utf-8")
    assert "import shutil" not in source
    assert "shutil.which(" not in source


def test_npm_missing_is_a_plain_refusal(state, tmp_path, monkeypatch, no_which):
    dash = tmp_path / "dashboard"
    dash.mkdir()
    _trusted_node(monkeypatch, npm_cli=None)
    said, ran = [], []
    ok = start_cli.web_page_ready(dash, {}, say=said.append,
                                  run=lambda argv, **kw: ran.append(argv))
    assert ok is False and ran == []
    assert "npm part is missing" in said[-1]
    assert plain_words.check("\n".join(said)) == []


def test_the_node_children_find_the_trusted_node_first(state, tmp_path, monkeypatch):
    dash = tmp_path / "dashboard"
    (dash / "node_modules" / "next").mkdir(parents=True)
    _trusted_node(monkeypatch)
    envs = []

    def run(argv, cwd, env, **kw):
        envs.append(env)
        (dash / ".next").mkdir(exist_ok=True)
        (dash / ".next" / "BUILD_ID").write_text("x")
        return subprocess.CompletedProcess(argv, 0)

    planted = str(tmp_path / "planted")
    base = {"PATH": planted, "NEXT_TELEMETRY_DISABLED": "1"}
    assert start_cli.web_page_ready(dash, base, say=lambda _l: None, run=run) is True
    path_parts = envs[0]["PATH"].split(os.pathsep)
    assert path_parts[0] == os.path.dirname(FAKE_NODE)
    assert path_parts.index(planted) > 0
    if os.name == "nt":
        assert envs[0]["NoDefaultCurrentDirectoryInExePath"] == "1"
    assert base == {"PATH": planted, "NEXT_TELEMETRY_DISABLED": "1"}  # not changed


@pytest.mark.skipif(os.name != "nt", reason="Windows paths: elsewhere C:\\node splits on ':'")
def test_node_env_merges_every_spelling_of_path_once():
    env = {"Path": "C:\\a;C:\\b".replace(";", os.pathsep),
           "PATH": "C:\\b".replace(";", os.pathsep), "X": "1"}
    out = start_cli.node_env(env, os.path.join("C:\\node", "node.exe"))
    assert [k for k in out if k.upper() == "PATH"] == ["PATH"]
    parts = out["PATH"].split(os.pathsep)
    assert parts[0] == "C:\\node" and parts.count("C:\\b") == 1
    assert out["X"] == "1"


def test_the_web_page_starts_on_the_trusted_node(state, tmp_path, monkeypatch):
    dash = tmp_path / "dashboard"
    dash.mkdir()
    (dash / "package.json").write_text("{}")
    _trusted_node(monkeypatch)
    monkeypatch.setattr(start_cli, "dashboard_dir", lambda: dash)
    monkeypatch.setattr(start_cli, "_api_key", lambda: "k" * 43)
    monkeypatch.setattr(start_cli, "answers", lambda url: url.endswith(":8264/health"))
    monkeypatch.setattr(start_cli, "_read_record",
                        lambda: {"backend_pid": 4242, "backend_port": 8264})
    monkeypatch.setattr(start_cli, "alive", lambda pid: pid == 4242)
    monkeypatch.setattr(start_cli, "web_page_ready", lambda *a, **k: True)
    monkeypatch.setattr(start_cli, "wait_up", lambda *a, **k: False)
    stopped, spawned = [], []
    monkeypatch.setattr(start_cli, "stop_pid", stopped.append)

    class Proc:
        pid = 5151

        def poll(self):
            return None

    def spawn(argv, cwd, env, log):
        spawned.append((argv, env))
        return Proc()

    monkeypatch.setattr(start_cli, "_spawn", spawn)
    args = argparse.Namespace(port=8264, dashboard_port=3264, no_browser=True)
    assert start_cli.cmd_start(args) == 1  # wait_up said no
    (argv, env), = spawned
    assert argv[0] == FAKE_NODE and argv[2] == "start"
    assert env["PATH"].split(os.pathsep)[0] == os.path.dirname(FAKE_NODE)
    assert stopped == [5151]


def test_the_web_page_is_not_started_by_name_when_node_goes_missing(
        state, tmp_path, monkeypatch):
    dash = tmp_path / "dashboard"
    dash.mkdir()
    (dash / "package.json").write_text("{}")
    _trusted_node(monkeypatch, node=None)
    monkeypatch.setattr(start_cli, "dashboard_dir", lambda: dash)
    monkeypatch.setattr(start_cli, "_api_key", lambda: "k" * 43)
    monkeypatch.setattr(start_cli, "answers", lambda url: url.endswith(":8264/health"))
    monkeypatch.setattr(start_cli, "_read_record",
                        lambda: {"backend_pid": 4242, "backend_port": 8264})
    monkeypatch.setattr(start_cli, "alive", lambda pid: pid == 4242)
    monkeypatch.setattr(start_cli, "web_page_ready", lambda *a, **k: True)
    spawned = []
    monkeypatch.setattr(start_cli, "_spawn", lambda *a, **k: spawned.append(a))
    args = argparse.Namespace(port=8264, dashboard_port=3264, no_browser=True)
    assert start_cli.cmd_start(args) == 1
    assert spawned == []


@windows_only
def test_stop_uses_taskkill_from_the_system_folder(monkeypatch, tmp_path, no_which):
    from puffpai.security import host_programs
    planted = tmp_path / "taskkill.exe"
    planted.write_bytes(b"")  # zero bytes: never run
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    calls = []
    monkeypatch.setattr(start_cli, "alive", lambda pid: True)
    monkeypatch.setattr(start_cli.subprocess, "run",
                        lambda argv, **kw: calls.append((argv, kw)))
    assert start_cli.stop_pid(4242) is True
    (argv, kw), = calls
    expected = os.path.join(host_programs.SYSTEM32, "taskkill.exe")
    assert os.path.normcase(argv[0]) == os.path.normcase(expected)
    assert argv[1:] == ["/PID", "4242", "/T", "/F"]
    assert os.path.normcase(kw["cwd"]) == os.path.normcase(host_programs.SAFE_CWD)
    assert str(tmp_path) not in kw["env"]["PATH"]
    assert planted.read_bytes() == b""


@windows_only
def test_alive_uses_tasklist_from_the_system_folder(monkeypatch, tmp_path, no_which):
    from puffpai.security import host_programs
    monkeypatch.chdir(tmp_path)
    (tmp_path / "tasklist.exe").write_bytes(b"")
    calls = []

    def run(argv, **kw):
        calls.append((argv, kw))
        return subprocess.CompletedProcess(argv, 0, stdout='"x.exe","4242"')

    monkeypatch.setattr(start_cli.subprocess, "run", run)
    assert start_cli.alive(4242) is True
    (argv, kw), = calls
    expected = os.path.join(host_programs.SYSTEM32, "tasklist.exe")
    assert os.path.normcase(argv[0]) == os.path.normcase(expected)
    assert os.path.normcase(kw["cwd"]) == os.path.normcase(host_programs.SAFE_CWD)


@windows_only
def test_no_system_taskkill_stops_the_process_itself(monkeypatch):
    from puffpai.security import host_programs
    monkeypatch.setattr(start_cli, "alive", lambda pid: True)
    monkeypatch.setattr(host_programs, "path",
                        lambda name: None if name == "taskkill" else "x")
    ran, killed = [], []
    monkeypatch.setattr(start_cli.subprocess, "run", lambda *a, **k: ran.append(a))
    monkeypatch.setattr(start_cli.os, "kill", lambda pid, sig: killed.append(pid))
    assert start_cli.stop_pid(4242) is True
    assert ran == [] and killed == [4242]


@windows_only
def test_no_system_tasklist_means_not_running(monkeypatch):
    from puffpai.security import host_programs
    monkeypatch.setattr(host_programs, "path", lambda name: None)
    ran = []
    monkeypatch.setattr(start_cli.subprocess, "run", lambda *a, **k: ran.append(a))
    assert start_cli.alive(4242) is False and ran == []


@windows_only
def test_a_planted_node_and_npm_are_never_used(state, tmp_path, monkeypatch, no_which):
    """End to end through the real host_programs: node from the installer's
    record, npm as npm-cli.js beside it; a node.exe and npm.cmd planted in
    the working folder and first on PATH are never named."""
    from puffpai.security import host_programs
    install = tmp_path / "install"
    install.mkdir()
    trusted = tmp_path / "nodejs"
    cli_js = trusted / "node_modules" / "npm" / "bin" / "npm-cli.js"
    cli_js.parent.mkdir(parents=True)
    cli_js.write_text("")
    (trusted / "node.exe").write_bytes(b"")  # zero bytes: never run
    (install / "node_path.txt").write_text(str(trusted / "node.exe"), encoding="utf-8")
    monkeypatch.setattr(host_programs, "_install_root", lambda: str(install))
    planted = tmp_path / "planted"
    planted.mkdir()
    for name in ("node.exe", "npm.cmd", "npm", "node"):
        (planted / name).write_bytes(b"")
    monkeypatch.chdir(planted)
    monkeypatch.setenv("PATH", str(planted) + os.pathsep + os.environ.get("PATH", ""))
    dash = tmp_path / "dashboard"
    (dash / "scripts").mkdir(parents=True)
    calls = []

    def run(argv, cwd, env, **kw):
        calls.append((list(argv), env))
        (dash / ".next").mkdir(exist_ok=True)
        (dash / ".next" / "BUILD_ID").write_text("x")
        return subprocess.CompletedProcess(argv, 0)

    env = start_cli.child_env(8264, 3264, "k")
    assert start_cli.web_page_ready(dash, env, say=lambda _l: None, run=run) is True
    (parts_argv, parts_env), (check_argv, _env) = calls
    assert parts_argv[:3] == [str(trusted / "node.exe"), str(cli_js), "ci"]
    assert check_argv[0] == str(trusted / "node.exe")
    assert all(str(planted) not in arg for argv, _e in calls for arg in argv)
    assert parts_env["PATH"].split(os.pathsep)[0] == str(trusted)
    for name in ("node.exe", "npm.cmd"):
        assert (planted / name).read_bytes() == b""
