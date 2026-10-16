"""Apps Toolsmith runs answer on this computer only (a verifier found
MongoDB and Elasticsearch of a compose app, and a vite dev server, answering
from the home network).

* compose: a BuildBot copy of each compose file, every published port on
  127.0.0.1 (short and long syntax, ranges, an address of its own left);
* docker run: ``-p X:Y`` gets 127.0.0.1 in front;
* native servers: ``--host 0.0.0.0`` and friends become 127.0.0.1;
* a page that still listens on every network is said plainly on the job.

No Docker and no real app here: every program's output is made up.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import subprocess

import pytest
import yaml

from puffpai.build import toolsmith_run as T


@pytest.fixture(autouse=True)
def _no_real_listeners(monkeypatch):
    """No test here reads this computer's real listening ports."""
    monkeypatch.setattr(T, "listening_ports", lambda: set())
    monkeypatch.setattr(T, "tree_listen_ports", lambda pid: [])


def _run_for(tmp_path):
    run = T.Run({"id": "j1", "name": "News"}, [{"ref": "r1", "name": "NewsBlur"}],
                lambda _l: None)
    run.app, run.app_id, run.folder = {"ref": "r1", "name": "NewsBlur"}, "nb-abc", str(tmp_path)
    run.compose = "bbtest-nb-abc"
    return run


# ── one published port ───────────────────────────────────────────────────────

@pytest.mark.parametrize("spec, want", [
    ("8080:80", "127.0.0.1:8080:80"),
    ("80", "127.0.0.1::80"),                                   # a random host port
    ("0.0.0.0:27017:27017", "127.0.0.1:27017:27017"),
    ("[::]:9200:9200", "127.0.0.1:9200:9200"),
    ("8000-8010:8000-8010", "127.0.0.1:8000-8010:8000-8010"),  # ranges
    ("9000-9010", "127.0.0.1::9000-9010"),
    ("5434:5432/tcp", "127.0.0.1:5434:5432/tcp"),
    ("${WEB_PORT:-3000}:3000", "127.0.0.1:${WEB_PORT:-3000}:3000"),
    ("${BIND:-0.0.0.0}:6379:6379", "127.0.0.1:6379:6379"),
    ("127.0.0.1:6379:6379", "127.0.0.1:6379:6379"),           # already this computer
    ("192.168.1.9:81:81", "192.168.1.9:81:81"),               # an address of its own: left
])
def test_one_published_port_goes_on_this_computer(spec, want):
    assert T.local_port(spec) == want


COMPOSE = """x-base: &base
  restart: always
services:
  mongo:
    image: mongo:7
    ports:
      - 27017:27017   # the database
      - "29019:29019"
  es:
    image: elasticsearch:8
    ports: ["9200:9200", '9300']
  pg:
    <<: *base
    image: postgres:16
    ports:
      - target: 5432
        published: 5434
      - target: 80
        host_ip: 0.0.0.0
        published: "8080"
      - host_ip: 192.168.1.9
        target: 81
      - "127.0.0.1:6379:6379"
  web:
    image: nginx
    ports:
    - 8000-8010:8000-8010
    environment:
      PORT: "80:80"
"""


def test_compose_short_and_long_syntax_all_go_on_this_computer():
    text = T.local_compose_text(COMPOSE)
    got = yaml.safe_load(text)["services"]
    assert got["mongo"]["ports"] == ["127.0.0.1:27017:27017", "127.0.0.1:29019:29019"]
    assert got["es"]["ports"] == ["127.0.0.1:9200:9200", "127.0.0.1::9300"]
    long = got["pg"]["ports"]
    assert long[0] == {"target": 5432, "published": 5434, "host_ip": "127.0.0.1"}
    assert long[1]["host_ip"] == "127.0.0.1"                    # 0.0.0.0 replaced
    assert long[2]["host_ip"] == "192.168.1.9"                  # its own address left
    assert long[3] == "127.0.0.1:6379:6379"
    assert got["web"]["ports"] == ["127.0.0.1:8000-8010:8000-8010"]   # same-indent list
    assert got["web"]["environment"] == {"PORT": "80:80"}       # not a port list: left
    assert got["pg"]["restart"] == "always" and "# the database" in text   # the rest as it was


def test_compose_runs_a_copy_and_never_changes_the_apps_own_files(tmp_path):
    run = _run_for(tmp_path)
    (tmp_path / "docker-compose.yml").write_text(COMPOSE, encoding="utf-8")
    (tmp_path / "docker-compose.override.yml").write_text(
        "services:\n  mongo:\n    ports:\n      - 27018:27018\n", encoding="utf-8")
    cmd, why = run.rewrite("docker compose up --build")
    assert not why
    assert "-f .bb-local.docker-compose.yml -f .bb-local.docker-compose.override.yml" in cmd
    assert (tmp_path / "docker-compose.yml").read_text(encoding="utf-8") == COMPOSE
    copy = yaml.safe_load((tmp_path / ".bb-local.docker-compose.yml").read_text("utf-8"))
    assert copy["services"]["mongo"]["ports"][0] == "127.0.0.1:27017:27017"
    over = (tmp_path / ".bb-local.docker-compose.override.yml").read_text("utf-8")
    assert '"127.0.0.1:27018:27018"' in over
    # A file it names, in a folder of its own: the copy sits beside it.
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "compose.prod.yml").write_text(COMPOSE, encoding="utf-8")
    cmd, _ = run.rewrite("docker compose --file=deploy/compose.prod.yml up -d")
    assert "--file=deploy/.bb-local.compose.prod.yml" in cmd
    assert (tmp_path / "deploy" / ".bb-local.compose.prod.yml").is_file()


def test_a_compose_file_with_nothing_to_change_is_used_as_it_is(tmp_path):
    run = _run_for(tmp_path)
    (tmp_path / "compose.yaml").write_text(
        "services:\n  web:\n    ports:\n      - 127.0.0.1:8080:80\n", encoding="utf-8")
    cmd, _ = run.rewrite("docker compose up -d")
    assert "-f compose.yaml" in cmd and ".bb-local" not in cmd


def test_a_compose_file_outside_the_app_is_never_copied(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    (tmp_path / "compose.yml").write_text(COMPOSE, encoding="utf-8")
    (app / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    run = _run_for(app)
    cmd, _ = run.rewrite("docker compose -f ../compose.yml up -d")
    assert "-f ../compose.yml" in cmd and not (tmp_path / ".bb-local.compose.yml").exists()


# ── docker run and native servers ────────────────────────────────────────────

def test_docker_run_publishes_on_this_computer_only(tmp_path):
    run = _run_for(tmp_path)
    cmd, _ = run.rewrite('docker run -p 27017:27017 --publish=9200:9200 -p "80" '
                         "-p 127.0.0.1:6379:6379 -e PORT=1 mongo:7 --bind_ip_all -p 5")
    assert "-p 127.0.0.1:27017:27017" in cmd and "--publish=127.0.0.1:9200:9200" in cmd
    assert '-p "127.0.0.1::80"' in cmd and "-p 127.0.0.1:6379:6379" in cmd
    assert cmd.endswith("mongo:7 --bind_ip_all -p 5")          # the app's own words after it


@pytest.mark.parametrize("cmd, want", [
    ("vite --host 0.0.0.0 --port 3000", "vite --host 127.0.0.1 --port 3000"),
    ("npm run dev -- --host", "npm run dev -- --host 127.0.0.1"),
    ("npm run dev -- --host --port 3000", "npm run dev -- --host 127.0.0.1 --port 3000"),
    ("uvicorn main:app --host=0.0.0.0", "uvicorn main:app --host=127.0.0.1"),
    ("next dev -H 0.0.0.0", "next dev -H 127.0.0.1"),
    ("gunicorn --bind 0.0.0.0:8000 app:app", "gunicorn --bind 127.0.0.1:8000 app:app"),
    ("gunicorn -b 0.0.0.0:8000 app:app", "gunicorn -b 127.0.0.1:8000 app:app"),
    ("HOST=0.0.0.0 npm start", "HOST=127.0.0.1 npm start"),
    ("python manage.py runserver 0.0.0.0:8000", "python manage.py runserver 127.0.0.1:8000"),
    ("flask run --host=::", "flask run --host=127.0.0.1"),
    ("npm run dev", "npm run dev"),
    # Inside a container 0.0.0.0 is what publishing needs: left.
    ("docker run img serve --host 0.0.0.0", None),
])
def test_server_host_flags_become_this_computer(cmd, want):
    assert T.local_host_flags(cmd) == (cmd if want is None else want)


def test_start_server_runs_the_rewritten_command(tmp_path, monkeypatch):
    run = _run_for(tmp_path)
    seen = {}

    def spawn(cmd, folder, port, project, log):
        seen["cmd"] = cmd
        return None
    monkeypatch.setattr(T, "spawn_server", spawn)
    run.start_server({"command": "npx vite --host 0.0.0.0", "port": 3000})
    assert seen["cmd"] == "npx vite --host 127.0.0.1"


# ── what really listens ──────────────────────────────────────────────────────

WINDOWS = "0.0.0.0\n::\n"
MAC = ("COMMAND  PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n"
       "node    4242 me   21u IPv4 0x1 0t0 TCP *:3000 (LISTEN)\n")
LINUX = "LISTEN 0 511 127.0.0.1:3000 0.0.0.0:*\nLISTEN 0 511 [::1]:3000 [::]:*\n"


def test_listen_output_is_read_on_each_computer():
    assert T.listen_hosts(WINDOWS, 3000) == ["0.0.0.0", "::"]
    assert T.listen_hosts(MAC, 3000) == ["*"]
    assert T.listen_hosts(LINUX, 3000) == ["127.0.0.1", "::1"]
    assert T.network_reachable(["0.0.0.0"]) and T.network_reachable(["*"])
    assert T.network_reachable(["192.168.1.20"])
    assert not T.network_reachable(["127.0.0.1", "::1", "::ffff:127.0.0.1"])
    assert not T.network_reachable([])                    # nothing read: nothing said


@pytest.mark.skipif(T.os.name != "nt", reason="the Windows way of asking")
def test_listen_addresses_asks_the_system_with_a_full_path(monkeypatch):
    calls = []

    def fake(argv, **kw):
        calls.append((argv, kw))
        return subprocess.CompletedProcess(argv, 0, stdout=WINDOWS, stderr="")
    monkeypatch.setattr(T, "_program", lambda name: r"C:\Windows\System32\powershell.exe")
    monkeypatch.setattr(T.subprocess, "run", fake)
    assert T.listen_addresses(3000) == ["0.0.0.0", "::"]
    argv, kw = calls[0]
    assert argv[0].endswith("powershell.exe") and "-LocalPort 3000" in argv[-1]
    assert kw["creationflags"] & T.procflags.CREATE_NO_WINDOW         # no window


def _page(monkeypatch, tmp_path):
    from puffpai.integrate import host_tools
    kept = {}
    monkeypatch.setattr(host_tools, "read_page", lambda url: {
        "ok": True, "title": "NewsBlur", "text": "Your feeds Sign in Popular stories"})
    monkeypatch.setattr(T, "_keep", lambda app_id, entry: kept.update(entry))
    run = _run_for(tmp_path)
    said = []
    run.progress = said.append
    return run, kept, said


def test_a_page_that_listens_on_every_network_is_said_plainly_and_keeps_running(
        monkeypatch, tmp_path):
    run, kept, said = _page(monkeypatch, tmp_path)
    monkeypatch.setattr(T, "listen_addresses", lambda port: ["0.0.0.0"])
    monkeypatch.setattr(T, "_kill", lambda pid: pytest.fail("the app was stopped"))
    assert run.done({"url": "http://127.0.0.1:3000/"})["ok"]
    assert kept["reachable_from_network"] is True
    assert any(i["what"] == T.NETWORK_NOTE for i in run.beyond_done)
    assert any(T.NETWORK_NOTE in s for s in said)
    assert "\u2014" not in T.NETWORK_NOTE and "door" not in T.NETWORK_NOTE.lower()


def test_a_page_on_this_computer_only_says_nothing_more(monkeypatch, tmp_path):
    run, kept, _said = _page(monkeypatch, tmp_path)
    monkeypatch.setattr(T, "listen_addresses", lambda port: ["127.0.0.1"])
    assert run.done({"url": "http://127.0.0.1:3000/"})["ok"]
    assert "reachable_from_network" not in kept and not run.beyond_done


def _ps(rows):
    return subprocess.CompletedProcess([], 0, stdout="\n".join(json.dumps(r) for r in rows),
                                       stderr="")


def test_compose_publish_line_and_check_use_what_docker_really_bound(monkeypatch, tmp_path):
    run, kept, said = _page(monkeypatch, tmp_path)
    run.used_compose = True
    rows = [{"Publishers": [{"URL": "127.0.0.1", "PublishedPort": 8080, "Protocol": "tcp"}]},
            {"Publishers": [{"URL": "0.0.0.0", "PublishedPort": 27017, "Protocol": "tcp"}]}]
    monkeypatch.setattr(T, "_program", lambda name: "docker")
    monkeypatch.setattr(T.subprocess, "run", lambda *a, **k: _ps(rows))
    monkeypatch.setattr(T, "listen_addresses", lambda port: ["127.0.0.1"])
    assert run._compose_published() == [("127.0.0.1", 8080), ("0.0.0.0", 27017)]
    assert run.done({"url": "http://127.0.0.1:8080/"})["ok"]
    assert kept["reachable_from_network"] is True                 # the database port
    rows[1]["Publishers"][0]["URL"] = "127.0.0.1"
    kept.clear()
    run.result = None
    assert run.done({"url": "http://127.0.0.1:8080/"})["ok"]
    assert "reachable_from_network" not in kept


def test_the_compose_up_line_names_the_real_addresses(monkeypatch, tmp_path):
    run = _run_for(tmp_path)
    said = []
    run.progress = said.append
    (tmp_path / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    run.shell = type("S", (), {"cwd": str(tmp_path)})()
    monkeypatch.setattr(run, "_ensure_shell", lambda: None)
    monkeypatch.setattr(run, "_run_line", lambda cmd, wait, **kw: ("0", "ok"))
    monkeypatch.setattr(run, "_compose_published",
                        lambda **kw: [("127.0.0.1", 8080), ("192.168.1.9", 81)])
    monkeypatch.setattr(T, "_program", lambda name: "/full/docker")
    monkeypatch.setattr(T.subprocess, "run", lambda argv, *a, **k: (
        subprocess.CompletedProcess(argv, 0, stdout="", stderr="") if argv[1:2] == ["ps"]
        else pytest.fail(f"ran {argv}")))                 # only the after-check's docker ps
    got = run.terminal({"command": "docker compose up -d"})
    assert got["web_addresses_to_try"] == ["http://127.0.0.1:8080/", "http://127.0.0.1:81/"]
    # An address the app's own file names is left (only 0.0.0.0 / :: is stopped).
    assert any("publish 127.0.0.1:8080, 192.168.1.9:81" in s for s in said)


# ── every compose call and docker run in a chained line (a verifier saw
#    "... down && docker compose up -d" publish MongoDB, Redis, Elasticsearch
#    on 0.0.0.0: only the first call had been rewritten) ─────────────────────

ONE = "services:\n  mongo:\n    image: mongo:7\n    ports:\n      - 27017:27017\n"


def _chain_app(tmp_path):
    run = _run_for(tmp_path)
    (tmp_path / "docker-compose.yml").write_text(ONE, encoding="utf-8")
    return run


def _compose_calls(cmd):
    """Each compose call's own words, up to the next separator or closing quote."""
    return [cmd[m.end():T._cmd_end(cmd, m.end())] for m in T._COMPOSE_CMD.finditer(cmd)]


def test_chained_down_and_up_both_use_the_local_copy(tmp_path):
    run = _chain_app(tmp_path)
    cmd, why = run.rewrite("docker compose -f .bb-local.docker-compose.yml down && "
                           "docker compose up -d")
    assert not why
    calls = _compose_calls(cmd)
    assert len(calls) == 2
    for call in calls:
        assert " -f .bb-local.docker-compose.yml" in call and f"-p {run.compose}" in call
    assert calls[1].endswith(" up -d")


def test_a_semicolon_chain_and_docker_compose_with_a_dash(tmp_path):
    run = _chain_app(tmp_path)
    cmd, _ = run.rewrite("docker compose down; docker-compose up || docker compose start\n"
                         "docker compose restart")
    calls = _compose_calls(cmd)
    assert len(calls) == 4
    assert all("-f .bb-local.docker-compose.yml" in c for c in calls)
    assert " up -d" in calls[1]                          # in the background too


def test_compose_inside_a_bash_c_string(tmp_path):
    run = _chain_app(tmp_path)
    cmd, _ = run.rewrite('bash -c "docker compose pull && docker compose up -d"; '
                         "sh -c 'docker compose up -d'")
    calls = _compose_calls(cmd)
    assert len(calls) == 3
    assert all("-f .bb-local.docker-compose.yml" in c for c in calls)
    assert cmd.count('"') == 2 and cmd.count("'") == 2  # the quoting kept


def test_cd_into_a_sub_folder_uses_the_copy_there(tmp_path):
    run = _chain_app(tmp_path)
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "compose.yml").write_text(ONE, encoding="utf-8")
    cmd, _ = run.rewrite("cd deploy && docker compose up -d")
    assert cmd == f"cd deploy && docker compose -p {run.compose} -f .bb-local.compose.yml up -d"
    assert (tmp_path / "deploy" / ".bb-local.compose.yml").is_file()
    assert (tmp_path / "deploy" / "compose.yml").read_text(encoding="utf-8") == ONE
    # A cd inside a subshell is gone after it: the next call is back in the app's folder.
    cmd, _ = run.rewrite("(cd deploy && docker compose up -d) && docker compose up -d")
    calls = _compose_calls(cmd)
    assert "-f .bb-local.compose.yml" in calls[0]
    assert "-f .bb-local.docker-compose.yml" in calls[1]
    # Outside the app's folder: no copy is written there.
    cmd, _ = run.rewrite("cd .. && docker compose up -d")
    assert ".bb-local" not in cmd
    assert not (tmp_path.parent / ".bb-local.docker-compose.yml").exists()


def test_two_compose_calls_and_a_docker_run_in_one_line(tmp_path):
    run = _chain_app(tmp_path)
    c = run.compose
    cmd, _ = run.rewrite("docker compose up -d && docker compose -p other -f docker-compose.yml "
                         "up && docker run -p 9200:9200 elasticsearch:8; "
                         "docker run --publish=6379:6379 redis:7")
    calls = _compose_calls(cmd)
    assert len(calls) == 2 and all("-f .bb-local.docker-compose.yml" in x for x in calls)
    assert "other" not in cmd and calls[1].count(f"-p {c}") == 1
    assert f"docker run --name {c} -d -p 127.0.0.1:9200:9200 elasticsearch:8" in cmd
    assert f"docker run --name {c}-2 -d --publish=127.0.0.1:6379:6379 redis:7" in cmd
    # compose run -p publishes on 127.0.0.1 too.
    cmd, _ = run.rewrite("docker compose run -p 8080:80 mongo")
    assert "run -p 127.0.0.1:8080:80 mongo" in cmd and f"compose -p {c} -f" in cmd


def test_a_compose_file_name_is_still_not_a_compose_command():
    assert not T._COMPOSE_CMD.search("cat docker/docker-compose.yml")
    assert not T._COMPOSE_CMD.search("Get-Content docker/docker-compose.yml")


# ── the after-check: what Docker really published, after EVERY command ──────

def _docker_mock(monkeypatch, ps_lines, compose_rows=()):
    """A made-up Docker: ``docker ps`` lines (name, compose project, compose
    folder, ports; tab-separated), compose ps rows, every call kept."""
    calls = []

    def fake(argv, *a, **k):
        calls.append(list(argv))
        if argv[1:2] == ["ps"]:
            return subprocess.CompletedProcess(argv, 0, stdout="\n".join(ps_lines), stderr="")
        if argv[1:2] == ["compose"] and "ps" in argv:
            return _ps(list(compose_rows))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
    monkeypatch.setattr(T, "_program", lambda name: "/full/docker" if name == "docker" else None)
    monkeypatch.setattr(T.subprocess, "run", fake)
    return calls


def _terminal_app(monkeypatch, tmp_path, code="0"):
    run = _chain_app(tmp_path)
    said = []
    run.progress = said.append
    run.shell = type("S", (), {"cwd": str(tmp_path)})()
    monkeypatch.setattr(run, "_ensure_shell", lambda: None)
    monkeypatch.setattr(run, "_run_line", lambda cmd, wait, **kw: (code, "ok"))
    return run, said


def _downs(calls):
    return [x for x in calls if x[1:2] == ["compose"] and "down" in x]


def test_a_compose_publish_on_every_network_is_stopped_at_once(monkeypatch, tmp_path):
    run, said = _terminal_app(monkeypatch, tmp_path)
    c = run.compose
    calls = _docker_mock(monkeypatch, [
        f"{c}-es-1\t{c}\t{tmp_path}\t0.0.0.0:9200->9200/tcp, :::27017->27017/tcp",
        f"{c}-web-1\t{c}\t{tmp_path}\t127.0.0.1:8080->80/tcp",
        "someone-else\tother\t/elsewhere\t0.0.0.0:5432->5432/tcp"])     # not this app's
    got = run.terminal({"command": "docker compose down && docker compose up -d"})
    assert not got["ok"]
    assert got["said"] == ("It published 0.0.0.0:9200, :::27017 to every network; BuildBot "
                           "stopped it. Use the BuildBot copy: docker compose -f "
                           ".bb-local.docker-compose.yml up -d.")
    assert _downs(calls) == [["/full/docker", "compose", "-p", c, "down"]]
    assert sum(1 for x in calls if x[1:2] == ["ps"]) == 1        # one docker ps
    assert "web_addresses_to_try" not in got and any("stopped it" in s for s in said)
    assert "—" not in got["said"]


def test_a_docker_run_on_every_network_is_removed(monkeypatch, tmp_path):
    run, _said = _terminal_app(monkeypatch, tmp_path)
    run.allowed_images = {"elasticsearch:8"}
    c = run.compose
    calls = _docker_mock(monkeypatch, [f"{c}\t\t\t0.0.0.0:9200->9200/tcp, [::]:9200->9200/tcp"])
    got = run.terminal({"command": "docker run -p 9200:9200 elasticsearch:8"})
    assert not got["ok"] and "0.0.0.0:9200, [::]:9200 to every network" in got["said"]
    assert ["/full/docker", "rm", "-f", c] in calls
    assert c not in run.containers and not _downs(calls)


def test_an_indirect_start_script_is_caught_too(monkeypatch, tmp_path):
    """./start.sh runs compose with a project name of its own, from inside the
    app's folder: no compose words in the command, still caught and stopped."""
    run, said = _terminal_app(monkeypatch, tmp_path, code="1")       # even when it "failed"
    calls = _docker_mock(monkeypatch, [
        f"mongo\tstackname\t{tmp_path}\t0.0.0.0:27017->27017/tcp",
        f"{run.compose}-redis\t\t\t0.0.0.0:6379->6379/tcp"])
    got = run.terminal({"command": "./start.sh"})
    assert not got["ok"] and "0.0.0.0:27017, 0.0.0.0:6379 to every network" in got["said"]
    assert ["/full/docker", "compose", "-p", "stackname", "down"] in calls
    assert ["/full/docker", "rm", "-f", f"{run.compose}-redis"] in calls
    assert run.ran["./start.sh"]["ok"] is False and "stopped it" in run.ran["./start.sh"]["error"]


def test_start_server_that_starts_containers_on_every_network_is_stopped(monkeypatch,
                                                                         tmp_path):
    run = _chain_app(tmp_path)
    run.progress = lambda _l: None
    c = run.compose
    proc = type("P", (), {"pid": 4242, "poll": lambda self: None})()
    monkeypatch.setattr(T, "spawn_server", lambda *a: proc)
    monkeypatch.setattr(T, "port_open", lambda port: True)
    killed = []
    monkeypatch.setattr(T, "_kill", killed.append)
    calls = _docker_mock(monkeypatch, [f"{c}-db-1\t{c}\t{tmp_path}\t0.0.0.0:5433->5432/tcp"])
    got = run.start_server({"command": "make up", "port": 3000})
    assert not got["ok"] and got["said"].startswith("It published 0.0.0.0:5433 to every network")
    assert _downs(calls) == [["/full/docker", "compose", "-p", c, "down"]]
    assert killed == [4242] and 4242 not in run.pids and not run.starts
    # All on this computer: it starts as before.
    calls = _docker_mock(monkeypatch, [f"{c}-db-1\t{c}\t{tmp_path}\t127.0.0.1:5433->5432/tcp"])
    got = run.start_server({"command": "make up", "port": 3000})
    assert got["ok"] and run.starts and not _downs(calls)


def test_all_on_this_computer_runs_on_as_before(monkeypatch, tmp_path):
    run, _said = _terminal_app(monkeypatch, tmp_path)
    c = run.compose
    rows = [{"Publishers": [{"URL": "127.0.0.1", "PublishedPort": 27017, "Protocol": "tcp"}]}]
    calls = _docker_mock(monkeypatch, [f"{c}-mongo-1\t{c}\t{tmp_path}\t127.0.0.1:27017->27017/tcp",
                                       f"{c}-worker-1\t{c}\t{tmp_path}\t6379/tcp"], rows)
    got = run.terminal({"command": "docker compose up -d"})
    assert got["ok"] and got["web_addresses_to_try"] == ["http://127.0.0.1:27017/"]
    assert not any("down" in x or "rm" in x for x in calls)
    # Every command is checked (a plain npm step too), with one docker ps.
    calls.clear()
    assert run.terminal({"command": "npm install"})["ok"]
    assert calls == [calls[0]] and calls[0][1:2] == ["ps"]


def test_a_native_server_on_the_ipv6_any_address_is_reachable_from_the_network():
    # keeptrack.space listened on [::]:5544 (2026-10-14): each system's own words.
    windows = "::\n"                                              # Get-NetTCPConnection
    mac = "node    123 me   22u  IPv6 0x1      0t0  TCP *:5544 (LISTEN)\n"   # lsof
    linux = "LISTEN 0      511                 [::]:5544             [::]:*\n"  # ss
    for text in (windows, mac, linux):
        hosts = T.listen_hosts(text, 5544)
        assert T.network_reachable(hosts), (text, hosts)
    assert not T.network_reachable(T.listen_hosts("::1\n127.0.0.1\n", 5544))
