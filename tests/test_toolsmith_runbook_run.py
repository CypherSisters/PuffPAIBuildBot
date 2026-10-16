"""The run book inside Toolsmith's Run: facts first, compose prep, the API
address override, README sign-in as Basic auth, and the log-based check."""
from __future__ import annotations

import http.server
import shutil
import threading
from pathlib import Path

import pytest
import yaml

from puffpai.build import runbook
from puffpai.build import toolsmith_run as T

FIX = Path(__file__).resolve().parent / "fixtures" / "runbook"


def _run_for(folder: Path, port: int = 18123) -> T.Run:
    run = T.Run({"id": "j1", "name": "Reader"}, [{"ref": "r1", "name": "Reader"}],
                lambda _l: None)
    run.app, run.app_id, run.folder = {"ref": "r1", "name": "Reader"}, "rd-abc123", str(folder)
    run.compose, run.port = "bbtest-rd-abc123", port
    return run


def test_the_first_message_carries_the_facts_once_downloaded(tmp_path):
    app = tmp_path / "app"
    shutil.copytree(FIX / "vite_static", app)
    run = _run_for(app)
    run.run_facts = T.runbook.detect(str(app), run.port)
    run.creds = ("demo", "demo")
    text = run.task()
    assert "Facts" in text and "npx -y pnpm@9.1.0 install" in text
    assert f"python -I -m http.server {run.port} --bind 127.0.0.1 -d dist" in text
    assert "Sign in" not in text and "demo / demo" in text
    assert "Start it with the command above unless it fails" in text
    assert '"facts"' in T.SYSTEM and "read files only to fix a failure" in T.SYSTEM


def test_compose_up_gets_pwd_its_env_and_the_api_address_override(tmp_path):
    app = tmp_path / "app"
    shutil.copytree(FIX / "compose_api", app)
    own = (app / "docker-compose.yml").read_text()
    run = _run_for(app)
    run.ports_before = {8080}                         # something else has 8080 here
    run.readme_full = "Copy `docs/app.yml` to `config/app.yml` first."
    cmd, why = run.rewrite("docker compose up -d")
    assert not why
    assert "-f .bb-local.docker-compose.yml -f .bb-local.api.yml" in cmd
    assert (app / "docker-compose.yml").read_text() == own        # its own file untouched
    local = (app / ".bb-local.docker-compose.yml").read_text()
    assert f"{str(app).replace(chr(92), '/')}/data:/data" in local
    assert f'"127.0.0.1:{run.port}:8080"' in local
    over = yaml.safe_load((app / ".bb-local.api.yml").read_text())
    want = f"http://127.0.0.1:{run.port}"
    assert over["services"]["app"]["environment"]["APP_HTTP_PUBLIC_URL"] == want
    env = (app / ".env").read_text()                  # written from .env.example
    assert f"APP_HTTP_PUBLIC_URL={want}" in env and "APP_SECRET=change-me" not in env
    # (its template secret got a fresh random value, 2026-10-15)
    assert len(runbook.env_values(env)["APP_SECRET"]) == 64
    assert (app / "config" / "app.yml").read_text() == "title: x\n"
    assert want in run.api_line


def test_a_free_port_writes_no_override(tmp_path):
    app = tmp_path / "app"
    shutil.copytree(FIX / "compose_api", app)
    run = _run_for(app)
    cmd, _ = run.rewrite("docker compose up -d")
    assert ".bb-local.api.yml" not in cmd and not (app / ".bb-local.api.yml").exists()


def _page_run(monkeypatch, tmp_path, text: str, log: str) -> T.Run:
    from puffpai.integrate import host_tools
    monkeypatch.setattr(host_tools, "read_page", lambda url: {
        "ok": True, "title": "Reader", "text": text})
    monkeypatch.setattr(T, "_keep", lambda app_id, entry: None)
    monkeypatch.setattr(T, "listen_addresses", lambda port: [])
    run = _run_for(tmp_path)
    monkeypatch.setattr(run, "_app_log", lambda: log)
    return run


def test_an_error_banner_with_its_api_failing_is_not_working(monkeypatch, tmp_path):
    log = 'app-1 | 127.0.0.1 - "POST /graphql HTTP/1.1" 401 12\n'
    run = _page_run(monkeypatch, tmp_path, "My articles\nFailed to fetch\nSettings", log)
    got = run.read_page({"url": "http://127.0.0.1:18123/"})
    assert run.result is None and not got["ok"]
    assert "Failed to fetch" in got["said"] and "POST /graphql" in got["said"]


def test_an_outside_service_down_is_working_with_a_note(monkeypatch, tmp_path):
    log = ('"GET /api/iss HTTP/1.1" 200 -\n'
           "fetch https://api.example.org/apod failed: 503 Service Unavailable\n")
    run = _page_run(monkeypatch, tmp_path, "Space today\nUnable to load the picture", log)
    run.creds = ("demo", "demo")
    got = run.read_page({"url": "http://127.0.0.1:18123/"})
    assert got["ok"] and run.result
    assert run.result["notes"] == ["Sign in with demo / demo (from its README)",
                                   "api.example.org is not answering right now; the app works"]


class _Basic(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - the http.server name
        if self.headers.get("Authorization") == "Basic ZGVtbzpkZW1v":     # demo:demo
            body = b"<title>Reader</title><p>Your unread articles are here</p>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="app"')
        self.end_headers()

    def log_message(self, *_a):
        pass


@pytest.fixture
def basic_server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Basic)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def test_readme_sign_in_is_sent_as_basic_auth_when_asked(monkeypatch, tmp_path, basic_server):
    from puffpai.integrate import host_tools
    monkeypatch.setattr(host_tools, "read_page", lambda url: {"ok": False, "said": "401"})
    monkeypatch.setattr(T, "_keep", lambda app_id, entry: None)
    monkeypatch.setattr(T, "listen_addresses", lambda port: [])
    run = _run_for(tmp_path)
    monkeypatch.setattr(run, "_app_log", lambda: "")
    url = f"http://127.0.0.1:{basic_server}/"
    assert T.basic_challenge(url)
    assert run.read_page({"url": url})["ok"] is False          # no README sign-in: not read
    run.creds = ("demo", "demo")
    got = run.read_page({"url": url})
    assert got["ok"] and run.result and run.signed_in
    assert run.result["notes"][0] == "Sign in with demo / demo (from its README)"


def test_make_keeps_the_notes_on_the_job():
    from puffpai.build import make
    got = make._working_app({"id": "j1", "name": "Reader"}, {  # noqa: SLF001
        "app_id": "rd-abc123", "name": "Reader", "url": "http://127.0.0.1:18123/",
        "notes": ["Sign in with demo / demo (from its README)"]})
    assert got["made"]["notes"] == ["Sign in with demo / demo (from its README)"]


def test_build_status_carries_the_notes_for_the_owner(monkeypatch):
    from puffpai.build import api, make
    from puffpai.build import schema as S
    monkeypatch.setattr(make, "load_made", lambda job: {"made": {
        "app_ref": "github:o/reader", "app_name": "Reader",
        "notes": ["Sign in with demo / demo (from its README)"]}})
    assert api.app_used({"route": S.A, "id": "j"}) == {
        "ref": "github:o/reader", "name": "Reader",
        "notes": ["Sign in with demo / demo (from its README)"]}
