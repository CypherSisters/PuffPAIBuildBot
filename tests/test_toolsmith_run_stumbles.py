"""Toolsmith's install loop: the stumbles of a live compose-app run (2026-10-15).

1. every command starts in the app's folder (a "cd docker" left behind);
2. no page wait after a start command that failed;
3. compose up / pull and docker pull are long commands (no | tail, no 2-minute cut);
4. an example .env: never over one already written, its template secrets
   filled with fresh values that are never shown or sent;
5. one extension of the try's minutes when it runs and is close;
6. a page answering while a sibling compose service restarts is not working;
7. no subprocess output decoded with the system code page.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import ast
import os
import pathlib
import time

import pytest

from puffpai.build import runbook
from puffpai.build import toolsmith_run as T


@pytest.fixture(autouse=True)
def _no_real_docker(monkeypatch):
    real = T._program
    monkeypatch.setattr(T, "_program", lambda name: None if name == "docker" else real(name))
    monkeypatch.setattr(T, "listening_ports", lambda: set())
    monkeypatch.setattr(T, "tree_listen_ports", lambda pid: [])


class _Shell:
    def __init__(self, cwd):
        self.cwd, self.ended = cwd, False

    def interrupt(self):
        pass


def _run(tmp_path, monkeypatch, outputs=lambda cmd: ("0", "ok")):
    run = T.Run({"id": "j1", "name": "Fit"}, [{"ref": "r1", "name": "Fit"},
                                              {"ref": "r2", "name": "Other"}], lambda _l: None)
    run.app, run.app_id, run.folder = {"ref": "r1", "name": "Fit"}, "fit-abc123", str(tmp_path)
    run.compose = "bbtest-fit-abc123"
    run.shell = _Shell(str(tmp_path))
    monkeypatch.setattr(run, "_ensure_shell", lambda: None)
    sent: list[tuple[str, dict]] = []

    def fake(cmd, wait, **kw):
        sent.append((cmd, kw))
        m = __import__("re").match(r"^cd '([^']+)'$", cmd) or \
            __import__("re").match(r"^cd (\S+)$", cmd)
        if m:                                   # a cd moves the fake shell
            where = m.group(1)
            run.shell.cwd = where if os.path.isabs(where) else os.path.join(run.shell.cwd, where)
            return "0", ""
        return outputs(cmd)
    monkeypatch.setattr(run, "_run_line", fake)
    run.sent = sent
    return run


# 1 ── every command starts in the app's folder ──────────────────────────────

def test_each_command_starts_in_the_apps_folder(tmp_path, monkeypatch):
    (tmp_path / "docker").mkdir()
    run = _run(tmp_path, monkeypatch)
    got = run.terminal({"command": "cd docker"})
    assert got["ok"] and "next command starts in the app's folder" in got["output"]
    assert run.shell.cwd.endswith("docker")
    run.terminal({"command": "cd docker && ls"})
    cmds = [c for c, _kw in run.sent]
    # Back home first, so the model's own "cd docker" lands in docker/ (not docker/docker).
    assert cmds[-2] == f"cd '{tmp_path}'" and cmds[-1] == "cd docker && ls"


def test_no_extra_cd_when_the_shell_is_home(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch)
    run.terminal({"command": "ls"})
    assert [c for c, _kw in run.sent] == ["ls"]


# 2 ── no page wait after a failed start ─────────────────────────────────────

def test_a_failed_start_is_reported_not_waited_for(tmp_path, monkeypatch):
    from puffpai.integrate import host_tools
    run = _run(tmp_path, monkeypatch, lambda cmd: (
        "1", "bash: cd: docker: No such file or directory") if "compose" in cmd else ("0", ""))
    got = run.terminal({"command": "cd docker && docker compose -f docker-compose.prod.yml "
                                   "up -d"})
    assert not got["ok"] and run.last_start and not run.last_start["ok"]
    monkeypatch.setattr(host_tools, "_answers", lambda url: False)
    t = time.monotonic()
    waited = run.wait_for_page({"url": "http://127.0.0.1:65240", "minutes": 3})
    assert time.monotonic() - t < 2
    assert not waited["ok"] and "start command failed" in waited["said"]
    assert "No such file" in waited["what_went_wrong"]


def test_after_a_start_that_worked_the_wait_goes_on(tmp_path, monkeypatch):
    from puffpai.integrate import host_tools
    (tmp_path / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    run = _run(tmp_path, monkeypatch)
    assert run.terminal({"command": "docker compose up -d"})["ok"]
    assert run.last_start["ok"]
    monkeypatch.setattr(host_tools, "_answers", lambda url: True)
    monkeypatch.setattr(run, "read_page", lambda args: {"ok": True, "said": "looked"})
    assert run.wait_for_page({"url": "http://127.0.0.1:65240"})["said"] == "looked"


# 3 ── compose up / pull are long commands ───────────────────────────────────

@pytest.mark.parametrize("cmd", [
    "docker compose -p x -f docker-compose.prod.yml up -d", "docker compose up",
    "docker-compose up -d", "docker compose pull", "docker pull postgres:16",
    "cd docker && docker compose up -d --wait",
])
def test_compose_up_and_pulls_are_long_commands(cmd):
    assert T.is_build(cmd)
    line, why = T.build_line(cmd + " 2>&1 | tail -15")
    assert not why and "tail" not in line


@pytest.mark.parametrize("cmd", ["docker compose ps", "docker compose logs --tail 50",
                                 "cat docker-compose.yml", "docker compose down"])
def test_other_compose_commands_are_not(cmd):
    assert not T.is_build(cmd)


def test_compose_up_waits_while_its_containers_change(tmp_path, monkeypatch):
    (tmp_path / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    run = _run(tmp_path, monkeypatch)
    run.terminal({"command": "docker compose up -d 2>&1 | tail -8"})
    cmd, kw = run.sent[-1]
    assert "tail" not in cmd
    assert kw["long_s"] == T.BUILD_COMMAND_S and kw["working"] == run._compose_work  # noqa: SLF001


def test_a_quiet_command_whose_work_grows_is_not_cut_at_its_wait(tmp_path):
    class _Quiet:                                   # runs on, prints nothing, ever
        def send(self, line):
            pass

        def read(self, since, wait):
            time.sleep(min(wait, 0.05))
            return {"text": "", "next": 0, "busy": True, "alive": True}
    run = T.Run({"id": "j"}, [], lambda _l: None)
    run.folder = str(tmp_path)
    run.shell = _Quiet()
    ticks = iter(range(10_000))
    monkey = T.PROBE_S
    try:
        T.PROBE_S = 0.05
        t = time.monotonic()
        code, _ = run._run_line("docker compose up -d", 0.3, long_s=1.0,  # noqa: SLF001
                                working=lambda: str(next(ticks)))
    finally:
        T.PROBE_S = monkey
    assert code is None and time.monotonic() - t >= 0.9 and run.stopped_why == "long"


# 4 ── example .env: never over one already there, template secrets filled ───

def test_template_secrets_get_fresh_values_and_the_rest_stays():
    text = ("BETTER_AUTH_SECRET=changeme\n"
            "SPARKY_FITNESS_API_ENCRYPTION_KEY=\"your_encryption_key_here\"\n"
            "DB_PASSWORD=\n"
            "JWT_SECRET=a1b2c3d4e5f6a7b8c9d0real\n"           # a real value stays
            "OPENAI_API_KEY=\n"                               # an empty outside key: not used
            "SSL_KEY_FILE=/run/secrets/key\n"                 # a path, not a secret
            "DB_USER=postgres\nDB_HOST=db\nPORT=3000\n"
            "API_URL=http://localhost:3000\n"
            "export SESSION_TOKEN=xxxxxxxx # set me\n")
    n = iter(range(100))
    out, filled = runbook.fill_env_secrets(text, lambda: f"NEW{next(n)}")
    assert filled == {"BETTER_AUTH_SECRET": "NEW0", "SPARKY_FITNESS_API_ENCRYPTION_KEY": "NEW1"}
    lines = out.splitlines()
    assert lines[0] == "BETTER_AUTH_SECRET=NEW0"
    assert lines[1] == 'SPARKY_FITNESS_API_ENCRYPTION_KEY="NEW1"'
    # A *_TOKEN is taken for an outside service's: left as it is.
    assert lines[-1] == "export SESSION_TOKEN=xxxxxxxx # set me"
    for same in ("DB_PASSWORD=",                         # empty: never filled
                 "JWT_SECRET=a1b2c3d4e5f6a7b8c9d0real", "OPENAI_API_KEY=",
                 "SSL_KEY_FILE=/run/secrets/key", "DB_USER=postgres", "PORT=3000",
                 "API_URL=http://localhost:3000"):
        assert same in lines


def test_the_example_is_copied_only_when_its_own_folder_has_no_env(tmp_path):
    (tmp_path / ".env.example").write_text("A=1\n", encoding="utf-8")
    (tmp_path / "docker").mkdir()
    (tmp_path / "docker" / ".env").write_text("A=2\n", encoding="utf-8")   # another folder's
    assert runbook.example_copies(str(tmp_path)) == [(".env.example", ".env")]
    (tmp_path / ".env").write_text("A=3\n", encoding="utf-8")
    assert runbook.example_copies(str(tmp_path)) == []


def test_one_placeholder_gets_one_value_everywhere_in_the_file():
    text = ("MYSQL_PASSWORD=example\nWORDPRESS_DB_PASSWORD=example\nMYSQL_USER=example\n"
            "DB_PASSWORD=changeme\nDATABASE_URL=postgres://app:changeme@db:5432/app\n"
            "SITE_URL=https://example.com\nKEY=\nSTRIPE_SECRET_KEY=your_key_here\n"
            "GOOGLE_CLIENT_SECRET=changeme\nSMTP_PASSWORD=changeme\n")
    n = iter(range(100))
    out, filled = runbook.fill_env_secrets(text, lambda: f"N{next(n)}")
    v = runbook.env_values(out)
    assert v["MYSQL_PASSWORD"] == v["WORDPRESS_DB_PASSWORD"] == "N0"      # still equal
    assert v["DB_PASSWORD"] == "N1" and v["DATABASE_URL"] == "postgres://app:N1@db:5432/app"
    assert v["MYSQL_USER"] == "example"                   # not a secret: as it was
    assert v["SITE_URL"] == "https://example.com"         # a word inside a host: as it was
    assert "\nKEY=\n" in out                             # empty: never filled
    # Outside services' and optional credentials: left as they are.
    assert "\nSTRIPE_SECRET_KEY=your_key_here\n" in out
    assert v["GOOGLE_CLIENT_SECRET"] == "changeme"
    assert v["SMTP_PASSWORD"] == "changeme"
    assert filled == {"MYSQL_PASSWORD": "N0", "WORDPRESS_DB_PASSWORD": "N0",
                      "DB_PASSWORD": "N1", "DATABASE_URL": "N1"}


def _compose_app(tmp_path):
    d = tmp_path / "docker"
    d.mkdir()
    (d / "docker-compose.prod.yml").write_text(
        "services:\n  server:\n    image: x/server:1\n    ports:\n      - \"3010:3010\"\n",
        encoding="utf-8")
    (d / ".env.example").write_text(
        "BETTER_AUTH_SECRET=changeme_please\nAPI_ENCRYPTION_KEY=your_key_here\n"
        "DB_NAME=fit\n", encoding="utf-8")
    return d


def test_the_copied_env_gets_new_secrets_that_never_reach_the_model(tmp_path, monkeypatch):
    d = _compose_app(tmp_path)
    run = _run(tmp_path, monkeypatch)
    said: list[str] = []
    monkeypatch.setattr(T, "say", said.append)
    cmd, why = run.rewrite("cd docker && docker compose -f docker-compose.prod.yml up -d")
    assert not why
    env = (d / ".env").read_text(encoding="utf-8")
    values = runbook.env_values(env)
    assert len(values["BETTER_AUTH_SECRET"]) == 64 and values["DB_NAME"] == "fit"
    assert values["BETTER_AUTH_SECRET"] != values["API_ENCRYPTION_KEY"]
    assert any("filled 2 secret settings with new random values" in s for s in said)
    assert not any(values["BETTER_AUTH_SECRET"] in s for s in said)
    # A `cat .env` result on its way back to the model: the values are hidden.
    wrapped = run._unless_done(lambda a, c=None: {"ok": True, "output": env}, "terminal")  # noqa: SLF001
    out = str(wrapped({"command": "cat .env"}))
    assert values["BETTER_AUTH_SECRET"] not in out and values["API_ENCRYPTION_KEY"] not in out
    assert "[new random value]" in out and "DB_NAME=fit" in out
    run.say(f"Last lines: {env}")
    assert values["API_ENCRYPTION_KEY"] not in said[-1]


def test_a_env_toolsmith_wrote_is_never_overwritten(tmp_path, monkeypatch):
    d = _compose_app(tmp_path)
    (d / ".env").write_text("BETTER_AUTH_SECRET=mine\n", encoding="utf-8")
    run = _run(tmp_path, monkeypatch)
    run.rewrite("cd docker && docker compose -f docker-compose.prod.yml up -d")
    assert (d / ".env").read_text(encoding="utf-8") == "BETTER_AUTH_SECRET=mine\n"


def _filled_run(tmp_path, monkeypatch):
    d = _compose_app(tmp_path)
    run = _run(tmp_path, monkeypatch)
    run.rewrite("cd docker && docker compose -f docker-compose.prod.yml up -d")
    return run, d, runbook.env_values((d / ".env").read_text(encoding="utf-8"))


def test_a_hidden_value_the_model_copies_back_is_put_back(tmp_path, monkeypatch):
    run, d, v = _filled_run(tmp_path, monkeypatch)
    text = ("BETTER_AUTH_SECRET=[new random value]\nAPI_ENCRYPTION_KEY=[new random value]\n"
            "DB_NAME=fit\nEXTRA=1\n")
    assert run.write_file({"path": "docker/.env", "text": text})["ok"]
    back = runbook.env_values((d / ".env").read_text(encoding="utf-8"))
    assert back["BETTER_AUTH_SECRET"] == v["BETTER_AUTH_SECRET"]
    assert back["API_ENCRYPTION_KEY"] == v["API_ENCRYPTION_KEY"] and back["EXTRA"] == "1"
    # A heredoc or echo in the terminal: the same.
    run.terminal({"command": "echo 'BETTER_AUTH_SECRET=[new random value]' >> docker/.env"})
    assert v["BETTER_AUTH_SECRET"] in run.sent[-1][0] and "[new random" not in run.sent[-1][0]


def test_a_hidden_value_that_cannot_be_told_apart_is_refused(tmp_path, monkeypatch):
    run, d, v = _filled_run(tmp_path, monkeypatch)
    before = (d / ".env").read_text(encoding="utf-8")
    got = run.write_file({"path": "docker/.env", "text": "OTHER=[new random value]\n"})
    assert not got["ok"] and "Do not copy hidden values" in got["said"]
    assert (d / ".env").read_text(encoding="utf-8") == before


def test_a_give_up_reason_never_carries_a_made_value(tmp_path, monkeypatch):
    run, _d, v = _filled_run(tmp_path, monkeypatch)
    monkeypatch.setattr(run, "_stop_started", lambda: None)
    monkeypatch.setattr(T, "remove_try_folder", lambda folder: False)
    run.give_up({"reason": f"auth failed with BETTER_AUTH_SECRET={v['BETTER_AUTH_SECRET']}"})
    assert v["BETTER_AUTH_SECRET"] not in str(run.whys) + str(run.failed)


# 5 ── one extension, when it runs and is close ──────────────────────────────

def _at_deadline(run):
    run.app_started = time.monotonic() - run.minutes_max * 60 - 30
    run.started_at = {"r1": run.app_started}


def test_one_extension_when_it_runs_and_its_page_answers(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch)
    _at_deadline(run)
    monkeypatch.setattr(run, "_running_now", lambda: True)
    monkeypatch.setattr(run, "started_urls", lambda: ["http://127.0.0.1:65240/"])
    monkeypatch.setattr(T, "port_open", lambda port: True)
    assert run._extend()  # noqa: SLF001
    assert 4.5 < run.minutes_left() <= T.EXTRA_MINUTES
    _at_deadline(run)
    assert not run._extend()                 # only once per try  # noqa: SLF001


def test_an_extension_for_a_setting_to_fix_but_not_for_nothing_running(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch)
    _at_deadline(run)
    monkeypatch.setattr(run, "started_urls", lambda: [])
    monkeypatch.setattr(T, "port_open", lambda port: False)
    run.last_error = "FATAL: Placeholder values in env vars: BETTER_AUTH_SECRET"
    monkeypatch.setattr(run, "_running_now", lambda: False)
    assert not run._extend()  # noqa: SLF001
    monkeypatch.setattr(run, "_running_now", lambda: True)
    assert run._extend()  # noqa: SLF001
    run2 = _run(tmp_path, monkeypatch)
    _at_deadline(run2)
    monkeypatch.setattr(run2, "_running_now", lambda: True)
    monkeypatch.setattr(run2, "started_urls", lambda: [])
    run2.last_error = "npm ERR! network timeout"
    assert not run2._extend()  # noqa: SLF001


# 6 ── a sibling service restarting is not working ───────────────────────────

ROWS = [{"Service": "frontend", "State": "running", "ExitCode": 0},
        {"Service": "server", "State": "restarting", "ExitCode": 1},
        {"Service": "migrate", "State": "exited", "ExitCode": 0},
        {"Service": "db", "State": "running", "Health": "healthy"}]


def test_compose_trouble_names_the_services_that_are_not_up():
    assert T.compose_trouble(ROWS) == [("server", "restarting")]
    rows = [{"Service": "api", "Name": "a", "State": "exited", "ExitCode": 3},
            {"Service": "web", "Name": "w", "State": "created"},
            {"Service": "seed", "Name": "s", "State": "exited", "ExitCode": 1},
            {"Service": "static", "Name": "st", "State": "exited", "ExitCode": 0}]
    # A server that failed blocks; a failed step that serves nothing is a note;
    # one that exited 0 is fine, ports or not.
    assert T.compose_trouble(rows, {"a": True, "st": True}) == [
        ("api", "exited with code 3"), ("web", "created")]
    assert T.compose_notes(rows, {"a": True, "st": True}) == [("seed", "exited with code 1")]
    assert T.compose_trouble(ROWS[:1] + ROWS[2:]) == []


def test_a_page_answering_while_a_sibling_restarts_is_not_working(tmp_path, monkeypatch):
    import subprocess
    run = _run(tmp_path, monkeypatch)
    run.used_compose = True
    monkeypatch.setattr(run, "_compose_rows", lambda: ROWS)
    monkeypatch.setattr(T, "_program", lambda name: "docker")

    def fake_run(argv, **kw):
        assert kw.get("encoding") == "utf-8"
        return subprocess.CompletedProcess(argv, 0, "server-1 | FATAL: Placeholder values in "
                                                    "env vars: BETTER_AUTH_SECRET\n", "")
    monkeypatch.setattr(T.subprocess, "run", fake_run)
    why = run._working_check({"text": "Welcome to Fit. Sign in to continue."})  # noqa: SLF001
    assert "server service is restarting" in why and "Placeholder values" in why
    got = run.done({"url": "http://127.0.0.1:65240/"},
                   page={"ok": True, "text": "Welcome to Fit. Sign in to continue."})
    assert not got["ok"] and "restarting" in got["said"] and run.result is None


def test_the_final_check_gives_up_with_the_restarting_service(tmp_path, monkeypatch):
    from puffpai.integrate import host_tools
    run = _run(tmp_path, monkeypatch)
    run.used_compose = True
    monkeypatch.setattr(run, "_compose_rows", lambda: ROWS)
    monkeypatch.setattr(run, "started_urls", lambda: ["http://127.0.0.1:65240/"])
    monkeypatch.setattr(T, "answering_host", lambda port: "127.0.0.1")
    monkeypatch.setattr(host_tools, "read_page",
                        lambda url: {"ok": True, "text": "Welcome to Fit. Sign in to continue."})
    monkeypatch.setattr(run, "_stop_started", lambda: None)
    monkeypatch.setattr(T, "remove_try_folder", lambda folder: False)
    t = time.monotonic()
    run.out_of_budget("it did not reach a working page within 12 minutes", wait_s=30)
    assert time.monotonic() - t < 5               # no 30 s of retries once it said why
    assert run.result is None and "r1" in run.gave_up and "restarting" in run.whys[-1]


# 7 ── no output decoded with the system code page ───────────────────────────

def test_no_text_mode_subprocess_without_utf8_in_build_and_the_page_reader():
    root = pathlib.Path(T.__file__).resolve().parent
    files = list(root.glob("*.py")) + [root.parent / "integrate" / "page_reader.py"]
    bad = []
    for p in files:
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Call):
                kw = {k.arg: k.value for k in n.keywords if k.arg}
                text = any(isinstance(kw.get(k), ast.Constant) and kw[k].value is True
                           for k in ("text", "universal_newlines"))
                if text and "encoding" not in kw:
                    bad.append(f"{p.name}:{n.lineno}")
    assert not bad, bad


# 8 ── the app's own page, not its marketing site (2026-10-15) ───────────────

from puffpai.build import runbook as R  # noqa: E402

SITE_ROWS = [
    {"Service": "www", "Name": "p-www-1", "State": "running",
     "Publishers": [{"PublishedPort": 4321, "Protocol": "tcp"}]},
    {"Service": "proxy", "Name": "p-proxy-1", "State": "created", "ExitCode": 0,
     "Publishers": []},
    {"Service": "frontend", "Name": "p-frontend-1", "State": "running",
     "Publishers": [{"PublishedPort": 55946, "Protocol": "tcp"}]},
    {"Service": "backend", "Name": "p-backend-1", "State": "running", "Publishers": []},
]
BIND_ERROR = ("driver failed programming external connectivity on endpoint p-proxy-1: "
              "Bind for 127.0.0.1:55946 failed: port is already allocated")


def test_front_services_rank_first_and_side_sites_last():
    services = {4321: "www", 55946: "frontend", 8080: "proxy", 9000: "backend",
                3000: "api", 3001: "docs", 3002: "grafana"}
    got = R.main_page_order(list(services), services)
    assert got[:2] == [8080, 55946] and got[2] == 9000
    assert set(got[3:]) == {4321, 3000, 3001, 3002}
    assert R.side_service("recipesage-www") and R.side_service("admin")
    assert R.main_service("web") and not R.main_service("web-admin")
    # The README's own address still comes first, even a side service's.
    assert R.main_page_order([55946, 4321], services,
                             "Open http://localhost:4321 in your browser")[0] == 4321


def _site_run(tmp_path, monkeypatch, rows=SITE_ROWS, error=BIND_ERROR):
    import json
    import subprocess
    run = _run(tmp_path, monkeypatch)
    run.used_compose = True
    run.port = 55946                        # BuildBot's own port for this app
    monkeypatch.setattr(run, "_compose_rows", lambda: rows)
    monkeypatch.setattr(T, "_program", lambda name: "docker")

    def fake_run(argv, **kw):
        if argv[1] == "inspect":
            out = "".join(f"/{n}\t{json.dumps(error if 'proxy' in n else '')}\t"
                          f"{json.dumps({'80/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '55946'}]})}\n"
                          for n in argv[4:])
            return subprocess.CompletedProcess(argv, 0, out, "")
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(T.subprocess, "run", fake_run)
    return run


def test_a_marketing_site_page_is_not_the_app_while_its_proxy_is_down(tmp_path, monkeypatch):
    run = _site_run(tmp_path, monkeypatch)
    page = {"ok": True, "title": "RecipeSage",
            "text": "All of your recipes in one place. Sign up today."}
    got = run.done({"url": "http://127.0.0.1:4321/"}, page=page)
    assert not got["ok"] and run.result is None
    said = got["said"]
    assert "www service" in said and "proxy service" in said and "created" in said
    assert "port is already allocated" in said                       # Docker's own error
    assert "Port 55946 is asked for twice" in said and "frontend" in said   # the clash, plainly


def test_a_created_service_that_publishes_ports_is_not_working(tmp_path, monkeypatch):
    rows = [dict(r) for r in SITE_ROWS]
    rows[1]["State"], rows[1]["ExitCode"] = "exited", 1   # a server that stopped, exit 1
    run = _site_run(tmp_path, monkeypatch, rows=rows, error="")
    why = run._compose_trouble_line()  # noqa: SLF001
    assert "proxy service is exited with code 1" in why
    rows[1]["ExitCode"] = 0                               # ended well: never blocks
    assert run._compose_trouble_line() == ""  # noqa: SLF001
    # A one-off step that exited 0 and publishes nothing is fine.
    assert T.compose_trouble([{"Service": "migrate", "Name": "m", "State": "exited",
                               "ExitCode": 0}], {"m": False}) == []


def test_the_front_page_works_when_every_service_is_up(tmp_path, monkeypatch):
    rows = [dict(r) for r in SITE_ROWS]
    rows[1] = {"Service": "proxy", "Name": "p-proxy-1", "State": "running",
               "Publishers": [{"PublishedPort": 8443, "Protocol": "tcp"}]}
    run = _site_run(tmp_path, monkeypatch, rows=rows)
    monkeypatch.setattr(T, "_keep", lambda app_id, entry: None)
    monkeypatch.setattr(run, "_reachable", lambda port: False)
    page = {"ok": True, "title": "RecipeSage", "text": "Welcome back. Log in to your recipes."}
    side = run.done({"url": "http://127.0.0.1:4321/"}, page=page)
    assert not side["ok"] and "http://127.0.0.1:8443/" in side["said"]
    assert run.done({"url": "http://127.0.0.1:8443/"}, page=page)["ok"]


# 9 ── the verifier's cases (2026-10-15) ────────────────────────────────────

PAGE = {"ok": True, "title": "Recipes", "text": "Welcome back. Log in to your recipes now."}


def _pub(p):
    return [{"PublishedPort": p, "Protocol": "tcp"}]


def _mk(tmp_path, monkeypatch, rows, compose=True):
    import subprocess
    run = _run(tmp_path, monkeypatch)
    run.used_compose = compose
    run.port = 55000
    monkeypatch.setattr(run, "_compose_rows", lambda: rows)
    monkeypatch.setattr(T, "_program", lambda name: "docker")
    monkeypatch.setattr(T.subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "", ""))
    monkeypatch.setattr(T, "_keep", lambda app_id, entry: None)
    monkeypatch.setattr(run, "_reachable", lambda port: False)
    return run


@pytest.mark.parametrize("rows", [
    [{"Service": "web-api", "Name": "a", "State": "running", "Publishers": _pub(55000)},
     {"Service": "db", "Name": "b", "State": "running", "Health": "healthy", "Publishers": []}],
    [{"Service": "api", "Name": "a", "State": "running", "Publishers": _pub(55000)},
     {"Service": "db", "Name": "b", "State": "running", "Publishers": []}],
    [{"Service": "server", "Name": "a", "State": "running", "Publishers": _pub(55000)},
     {"Service": "postgres", "Name": "b", "State": "running", "Publishers": []}],
    [{"Service": "web-api", "Name": "a", "State": "running", "Publishers": _pub(55000)},
     {"Service": "app-worker", "Name": "w", "State": "running", "Publishers": []},
     {"Service": "db", "Name": "b", "State": "running", "Publishers": []}],
    [{"Service": "api", "Name": "a", "State": "running", "Publishers": _pub(55000)},
     {"Service": "web", "Name": "w", "State": "exited", "ExitCode": 0, "Publishers": []}],
    [{"Service": "app", "Name": "a", "State": "running", "Publishers": _pub(55000)},
     {"Service": "createbuckets", "Name": "c", "State": "exited", "ExitCode": 1,
      "Publishers": []}],
], ids=["webapi+db", "api+db", "server+postgres", "webapi+app-worker",
        "api+web-static-exited0", "app+failed-oneoff"])
def test_an_app_whose_only_page_answers_is_working(tmp_path, monkeypatch, rows):
    run = _mk(tmp_path, monkeypatch, rows)
    got = run.done({"url": "http://127.0.0.1:55000/"}, page=PAGE)
    assert got["ok"], got


def test_a_failed_step_with_no_page_is_a_note_with_the_working_app(tmp_path, monkeypatch):
    rows = [{"Service": "app", "Name": "a", "State": "running", "Publishers": _pub(55000)},
            {"Service": "createbuckets", "Name": "c", "State": "exited", "ExitCode": 1}]
    run = _mk(tmp_path, monkeypatch, rows)
    got = run.done({"url": "http://127.0.0.1:55000/"}, page=PAGE)
    assert got["ok"] and "createbuckets" in got["note"]


@pytest.mark.parametrize("attr", ["containers", "pids"])
def test_a_docker_run_or_server_app_is_working(tmp_path, monkeypatch, attr):
    run = _mk(tmp_path, monkeypatch, [], compose=False)
    setattr(run, attr, ["bb-x"] if attr == "containers" else [12345])
    assert run.done({"url": "http://127.0.0.1:55000/"}, page=PAGE)["ok"]


def test_no_extension_for_a_server_that_is_gone(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch)
    run.app_started = time.monotonic() - run.minutes_max * 60 - 30
    run.pids = [999999]                     # crashed long ago, never pruned
    monkeypatch.setattr(T, "pid_alive", lambda pid: False)
    monkeypatch.setattr(run, "started_urls", lambda: [])
    run.last_error = "FATAL: Placeholder values in env vars: BETTER_AUTH_SECRET"
    assert not run._extend()  # noqa: SLF001
    monkeypatch.setattr(T, "pid_alive", lambda pid: True)
    assert run._extend()  # noqa: SLF001


def test_the_extension_is_five_minutes_even_long_past_the_deadline(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch)
    run.app_started = time.monotonic() - run.minutes_max * 60 - 600
    run.started_at = {"r1": run.app_started}
    monkeypatch.setattr(run, "_running_now", lambda: True)
    monkeypatch.setattr(run, "started_urls", lambda: ["http://127.0.0.1:65240/"])
    monkeypatch.setattr(T, "port_open", lambda port: True)
    assert run._extend() and 4.5 < run.minutes_left() <= 5  # noqa: SLF001
    assert not any(run._extend() for _ in range(3))  # noqa: SLF001


def test_pid_alive_reads_without_signalling():
    assert T.pid_alive(os.getpid()) and not T.pid_alive(0) and not T.pid_alive("x")


def test_a_failed_start_is_forgotten_after_a_later_command_that_worked(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch, lambda cmd: (
        "1", "Error: bind failed") if "compose" in cmd else ("0", "started"))
    (tmp_path / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    run.terminal({"command": "docker compose up -d"})
    assert run.last_start and not run.last_start["ok"]
    run.terminal({"command": "ls"})                 # only reads: still remembered
    assert run.last_start and not run.last_start["ok"]
    run.terminal({"command": "./start.sh"})         # started it another way
    assert run.last_start is None


@pytest.mark.parametrize("cmd, build, start", [
    ("docker compose logs up", False, False),
    ("docker compose logs --tail 5 up", False, False),
    ("docker compose -f up.yml ps", False, False),
    ("docker compose -p x -f a.yml --ansi=never up -d", True, True),
    ("docker compose --profile web up", True, True),
    ("docker compose pull", True, False),
])
def test_only_compose_s_own_subcommand_counts(cmd, build, start):
    assert T.is_build(cmd) == build and T.is_start(cmd) == start


def test_a_port_clash_is_explained_from_any_service(tmp_path, monkeypatch):
    rows = [dict(r) for r in SITE_ROWS]
    rows.append({"Service": "worker", "Name": "p-worker-1", "State": "restarting"})
    run = _site_run(tmp_path, monkeypatch, rows=rows)
    why = run._compose_trouble_line()  # noqa: SLF001
    assert why.startswith("its proxy service is created")
    assert "Port 55946 is asked for twice" in why


def test_a_hidden_value_inside_a_url_is_put_back_by_its_setting(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch)
    run.made_names = {"DB_PASSWORD": "AAA", "DATABASE_URL": "AAA", "JWT_SECRET": "BBB"}
    run.made_secrets = {"AAA", "BBB"}
    got, why = run.restore_made("DATABASE_URL=postgres://app:[new random value]@db/app\n"
                                "  JWT_SECRET: [new random value]\n")
    assert not why and got == "DATABASE_URL=postgres://app:AAA@db/app\n  JWT_SECRET: BBB\n"
