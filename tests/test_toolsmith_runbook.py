"""Toolsmith's run book: the per-stack run table, the address an app's page is
told, compose on Windows, and the working check (credentials, banners, logs)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from puffpai.build import runbook as R

FIX = Path(__file__).resolve().parent / "fixtures" / "runbook"
P = 18123


def _copy(tmp_path: Path, name: str) -> Path:
    dest = tmp_path / name
    shutil.copytree(FIX / name, dest)
    return dest


# ── the run table, one test per row ─────────────────────────────────────────

def test_next_app_runs_next_dev_on_the_port_and_this_computer():
    got = R.detect(str(FIX / "next"), P)
    assert got["stack"] == "Next.js" and got["via"] == "start_server"
    assert got["start"] == f"npx next dev -p {P} -H 127.0.0.1"
    assert f"npx next start -p {P} -H 127.0.0.1" in got["fallback"]
    assert got["install"] == "npm install"


def test_vite_app_with_a_server_runs_vite_on_the_port():
    got = R.detect(str(FIX / "vite_server"), P)
    assert got["stack"] == "Vite"
    assert got["start"] == f"npx vite --port {P} --host 127.0.0.1 --strictPort"


def test_client_only_build_is_built_then_served_static_with_its_package_manager():
    got = R.detect(str(FIX / "vite_static"), P)
    assert got["stack"].startswith("client-only")
    # packageManager field: npx -y <pm>@<version>, never corepack.
    assert got["install"] == "npx -y pnpm@9.1.0 install"
    assert got["build"] == "npx -y pnpm@9.1.0 run build"
    assert got["start"] == f"python -I -m http.server {P} --bind 127.0.0.1 -d dist"
    assert "vite --port" in got["fallback"]


def test_create_react_app_without_a_build_runs_npm_start_with_port_and_host():
    got = R.detect(str(FIX / "cra"), P)
    assert got["stack"] == "Create React App"
    assert got["start"] == f"PORT={P} HOST=127.0.0.1 npm start"


def test_create_react_app_with_a_build_is_served_static(tmp_path):
    app = _copy(tmp_path, "cra")
    pkg = json.loads((app / "package.json").read_text())
    pkg["scripts"]["build"] = "react-scripts build"
    (app / "package.json").write_text(json.dumps(pkg))
    got = R.detect(str(app), P)
    assert got["start"].endswith("-d build") and "npm start" in got["fallback"]


def test_plain_index_html_is_served_static():
    got = R.detect(str(FIX / "plain_html"), P)
    assert got["start"] == f"python -I -m http.server {P} --bind 127.0.0.1"


def test_compose_files_run_with_docker_compose():
    got = R.detect(str(FIX / "compose_api"), P)
    assert got["stack"] == "docker compose" and got["start"] == "docker compose up -d"


def test_dockerfile_only_builds_and_runs_on_this_computer():
    got = R.detect(str(FIX / "dockerfile"), P, name="My App")
    assert got["build"] == "docker build -t my-app ."
    assert got["start"] == f"docker run -d -p 127.0.0.1:{P}:9090 my-app"


def test_python_web_app_gets_the_readmes_command_with_the_port():
    readme = "## Run\n\n```\nflask --app web run --debug\n```\n"
    got = R.detect(str(FIX / "flask"), P, readme=readme)
    assert got["stack"] == "Python (Flask)"
    assert got["start"] == f"flask --app web run --debug --host 127.0.0.1 --port {P}"


@pytest.mark.parametrize("cmd, want", [
    ("uvicorn main:app --reload --port 8000", f"uvicorn main:app --reload --host 127.0.0.1 --port {P}"),
    ("python manage.py runserver 0.0.0.0:8000", f"python manage.py runserver 127.0.0.1:{P}"),
    ("gunicorn app:app -b 0.0.0.0:80", f"gunicorn -b 127.0.0.1:{P} app:app"),
])
def test_python_port_flags(cmd, want):
    assert R.with_port(cmd, P) == want


def test_package_runner_from_a_lock_file(tmp_path):
    (tmp_path / "yarn.lock").write_text("")
    assert R.package_runner(str(tmp_path), {}) == "npx -y yarn"
    assert R.package_runner(str(tmp_path), {"packageManager": "npm@10.0.0"}) == "npm"


def test_nothing_known_gives_no_row(tmp_path):
    (tmp_path / "README.md").write_text("hello")
    assert R.detect(str(tmp_path), P) == {}


def test_facts_block_names_stack_command_port_credentials_and_api_settings():
    run = R.detect(str(FIX / "vite_static"), P)
    text = R.facts_text(run=run, port=P, creds=("demo", "demo"),
                        api=R.api_settings(str(FIX / "compose_api")))
    assert text.startswith("Facts")
    assert f"python -I -m http.server {P}" in text and f"Assigned port: {P}" in text
    assert "demo / demo" in text and "APP_HTTP_PUBLIC_URL=http://localhost:8080" in text
    assert text.rstrip().endswith("read files only to fix a failure.")
    assert "\u2014" not in text


# ── the address its page is told ────────────────────────────────────────────

def test_api_settings_are_found_in_env_and_compose():
    got = R.api_settings(str(FIX / "compose_api"))
    names = {(a["name"], a["where"]) for a in got}
    assert ("APP_HTTP_PUBLIC_URL", ".env.example") in names
    assert ("VITE_API_URL", ".env.example") in names
    assert ("APP_HTTP_PUBLIC_URL", "docker-compose.yml") in names
    assert not any(a["name"] == "APP_DB_URL" for a in got)


def test_served_config_js_with_a_local_address_is_found(tmp_path):
    (tmp_path / "public").mkdir()
    (tmp_path / "public" / "config.js").write_text(
        "window.CONFIG = { api: 'http://localhost:8080/api' };\n")
    got = R.served_configs(str(tmp_path))
    assert got == [{"path": "public/config.js",
                    "line": "window.CONFIG = { api: 'http://localhost:8080/api' };"}]


def test_a_busy_port_is_moved_and_the_page_is_told_the_real_address(tmp_path):
    app = _copy(tmp_path, "compose_api")
    text = (app / "docker-compose.yml").read_text()
    new, info = R.prepare_compose(text, app_dir="C:\\apps\\x", port=P, busy=lambda p: p == 8080)
    assert info["remapped"] == {8080: P} and (P, 8080) in info["published"]
    assert f'"{P}:8080"' in new
    env = (app / ".env.example").read_text()
    over, said = R.api_override(text, env, info["published"], info["remapped"])
    doc = yaml.safe_load(over)
    assert doc["services"]["app"]["environment"]["APP_HTTP_PUBLIC_URL"] == f"http://127.0.0.1:{P}"
    # Only in its env file, read through env_file: set too.
    assert doc["services"]["app"]["environment"]["VITE_API_URL"] == f"http://127.0.0.1:{P}"
    assert "db" not in doc["services"]
    assert f"APP_HTTP_PUBLIC_URL=http://127.0.0.1:{P}" in said
    fixed = R.fix_env_text(env, info["published"], info["remapped"])
    assert f"APP_HTTP_PUBLIC_URL=http://127.0.0.1:{P}\n" in fixed
    assert f"VITE_API_URL=http://127.0.0.1:{P}/api" in fixed
    assert "APP_SECRET=change-me" in fixed


def test_a_free_port_changes_nothing_about_the_address(tmp_path):
    text = (FIX / "compose_api" / "docker-compose.yml").read_text()
    new, info = R.prepare_compose(text, app_dir="C:/a", port=P)
    assert info["remapped"] == {} and '"8080:8080"' in new
    assert R.api_override(text, "", info["published"], info["remapped"]) == ("", [])


# ── compose on Windows ──────────────────────────────────────────────────────

def test_pwd_becomes_the_app_folder_in_forward_slash_form():
    text = (FIX / "compose_api" / "docker-compose.yml").read_text()
    new, _info = R.prepare_compose(text, app_dir="C:\\Users\\o\\apps\\bb-x", port=P)
    assert "C:/Users/o/apps/bb-x/data:/data" in new and "${PWD}" not in new
    new2, _ = R.prepare_compose("x: $PWD/a\n", app_dir="D:\\a", port=P)
    assert new2 == "x: D:/a/a\n"


def test_a_port_variable_is_set_to_the_assigned_port_but_never_a_databases():
    text = (FIX / "compose_api" / "docker-compose.yml").read_text()
    _new, info = R.prepare_compose(text, app_dir="C:/a", port=P)
    assert "DB_PORT" not in info["port_vars"]
    web = ("services:\n  web:\n    environment:\n      - LISTEN=${WEB_PORT:-3000}\n"
           "    ports:\n      - \"${WEB_PORT:-3000}:3000\"\n")
    new, info = R.prepare_compose(web, app_dir="C:/a", port=P)
    assert info["port_vars"] == {"WEB_PORT": str(P)} and info["remapped"] == {3000: P}
    assert f"LISTEN={P}" in new and f'"{P}:3000"' in new


def test_example_files_are_copied_when_missing(tmp_path):
    app = _copy(tmp_path, "compose_api")
    compose = (app / "docker-compose.yml").read_text()
    readme = "Copy `docs/app.yml` to `config/app.yml` before you start."
    got = R.example_copies(str(app), readme, compose)
    assert (".env.example", ".env") in got
    assert ("docs/app.yml", "config/app.yml") in got
    (app / ".env").write_text("X=1\n")
    assert (".env.example", ".env") not in R.example_copies(str(app), readme, compose)


# ── the working check ───────────────────────────────────────────────────────

@pytest.mark.parametrize("readme, want", [
    ("## Login\n\nUse the demo account: `demo/demo`.\n", ("demo", "demo")),
    ("### Default credentials\n\nusername: admin, password: admin\n", ("admin", "admin")),
    ("## Usage\nSign in with **admin** / **changeme**\n", ("admin", "changeme")),
    ("## Install\nRun it with npm/yarn and open the page.\n", None),
    ("See https://example.com/docs for more.", None),
])
def test_readme_credentials(readme, want):
    assert R.readme_credentials(readme) == want


def test_sign_in_line_is_plain():
    assert R.sign_in_line(("demo", "demo")) == "Sign in with demo / demo (from its README)"


def test_error_banner_is_found():
    assert R.banner("My Feeds\nFailed to fetch\nArticles") == "Failed to fetch"
    assert R.banner("My Feeds\nArticles\nSettings") == ""


LOG_401 = """
app-1  | 2026/10/14 13:12:01 GET /static/app.js 200
app-1  | 127.0.0.1 - - "POST /graphql HTTP/1.1" 401 12
app-1  | 127.0.0.1 - - "POST /graphql HTTP/1.1" 401 12
"""
LOG_OK_OUTSIDE_DOWN = """
127.0.0.1 - - [14/Oct/2026] "GET /api/iss HTTP/1.1" 200 -
127.0.0.1 - - [14/Oct/2026] "GET /api/picture HTTP/1.1" 200 -
upstream https://api.example.org/apod answered 503 Service Unavailable
"""


def test_request_counts_own_calls_and_outside_outages():
    got = R.request_counts(LOG_401)
    assert got["ok"] == 0 and got["bad"] == 2 and "/graphql" in got["bad_line"]
    got = R.request_counts(LOG_OK_OUTSIDE_DOWN)
    assert got["ok"] == 2 and got["bad"] == 0 and got["down"] == ["api.example.org"]
    kv = R.request_counts('level=info method=GET path=/api/items status=500 dur=3ms\n')
    assert kv["bad"] == 1


def test_judge_banner_with_failing_own_api_is_not_working():
    got = R.judge("Feeds\nFailed to fetch", LOG_401)
    assert not got["working"] and "POST /graphql" in got["said"]


def test_judge_banner_with_no_api_calls_points_at_the_address():
    got = R.judge("Feeds\nFailed to fetch", "GET /index.html 200\n")
    assert not got["working"] and "wrong address" in got["said"]


def test_judge_outside_outage_is_working_with_a_note():
    got = R.judge("Space\nUnable to load picture", LOG_OK_OUTSIDE_DOWN)
    assert got["working"]
    assert got["note"] == "api.example.org is not answering right now; the app works"


def test_judge_clean_page_is_working():
    assert R.judge("Dashboard\nItems", "GET /api/items 200\n") == {
        "working": True, "note": "", "said": ""}


# ── a start script of its own beats serving its files ─────────────────────

def _pkg_folder(tmp_path: Path, scripts: dict, index: bool = True, deps=None) -> Path:
    d = tmp_path / "app"
    d.mkdir()
    (d / "package.json").write_text(json.dumps({"name": "app", "scripts": scripts,
                                                "dependencies": deps or {}}))
    if index:
        (d / "index.html").write_text("<h1>hi</h1>")
    return d


def test_a_node_start_script_with_an_index_html_runs_its_own_server(tmp_path):
    # The verifier's case: start = node server.js AND an index.html.
    got = R.detect(str(_pkg_folder(tmp_path, {"start": "node server.js"})), P)
    assert got["stack"] == "Node app with its own start script"
    assert got["start"] == f"PORT={P} HOST=127.0.0.1 npm run start"
    assert got["install"] == "npm install" and "http.server" not in str(got)


@pytest.mark.parametrize("scripts,run", [
    ({"dev": "nodemon app.js"}, "dev"),
    ({"serve": "ts-node src/index.ts"}, "serve"),
    ({"start": "node server.js", "build": "webpack --mode production"}, "start"),
    ({"start": "serve -s ."}, "start"),
    ({"build": "tsc", "start": "node dist/server.js"}, "start"),
])
def test_start_dev_or_serve_scripts_are_preferred_over_static(tmp_path, scripts, run):
    got = R.detect(str(_pkg_folder(tmp_path, scripts)), P)
    assert got["start"].endswith(f"npm run {run}") and "http.server" not in got["start"]


@pytest.mark.parametrize("scripts", [{}, {"start": "tsc"}, {"dev": "tailwindcss -o out.css"}])
def test_a_bare_index_html_with_no_server_script_is_still_served_static(tmp_path, scripts):
    got = R.detect(str(_pkg_folder(tmp_path, scripts)), P)
    assert got["start"] == f"python -I -m http.server {P} --bind 127.0.0.1"


def test_a_local_folder_with_a_start_script_and_index_html_runs_npm(tmp_path, monkeypatch):
    from puffpai.build import apps
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    d = _pkg_folder(tmp_path, {"start": "node server.js"})
    c = apps.from_local_folder(str(d))
    assert c["readme_steps"][0] == "npm install"
    assert c["readme_steps"][-1].endswith("npm run start")
    assert not any("http.server" in s for s in c["readme_steps"])


# ── the main page of a multi-service app (2026-10-14, item 19) ──────────────

def test_the_readme_address_comes_first_then_a_front_service():
    readme = (FIX / "otel_like" / "README.md").read_text()
    assert R.readme_open_ports(readme)[0] == 8080
    services = {9464: "ad", 4317: "otel-collector", 8080: "frontend-proxy"}
    assert R.main_page_order([9464, 4317, 8080], services, readme)[0] == 8080
    # No README address: the front service (by its name) first, the collector last.
    got = R.main_page_order([9464, 4317, 8080], services)
    assert got[0] == 8080 and got[-1] == 4317
    # The README's port mapped to the host port Docker published (busy 8080 moved).
    assert R.main_page_order([9464, 5123], {}, readme, pairs=[(5123, 8080)],
                             remapped={8080: 5123})[0] == 5123


@pytest.mark.parametrize("page, url", [
    ({"title": "Prometheus Java Client", "text": "Metrics are at /metrics"},
     "http://127.0.0.1:9464/"),
    ({"title": "", "text": "# HELP up Up\n# TYPE up gauge\nup 1\njvm_threads 12\n"},
     "http://127.0.0.1:9464/"),
    ({"title": "", "text": '{"status": 404, "error": "Not Found"}'}, "http://127.0.0.1:8081/"),
    ({"title": "Whitelabel Error Page", "text": "This application has no explicit mapping"},
     "http://127.0.0.1:8082/"),
    ({"title": "Shop", "text": "Telescopes for sale"}, "http://127.0.0.1:8080/metrics"),
])
def test_metrics_and_api_pages_are_not_the_main_page(page, url):
    assert R.not_main_page(page, url)


def test_a_real_page_is_the_main_page():
    assert R.not_main_page({"title": "Astronomy Shop", "text": "Telescopes, binoculars, "
                            "star charts. Add to cart."}, "http://127.0.0.1:8080/") == ""
    # The app itself is Prometheus: its own title is fine.
    assert R.not_main_page({"title": "Prometheus Time Series", "text": "Graph Alerts"},
                           "http://127.0.0.1:9090/", app_name="prometheus") == ""


# ── the demo-login detector reads sign-in text only (2026-10-14, item 20) ───

OTEL_README = """# OpenTelemetry Demo

[![GitHub release](https://img.shields.io/github/v/release/open-telemetry/opentelemetry-demo)](https://github.com/open-telemetry/opentelemetry-demo/releases)

## Quick start

```
git clone https://github.com/open-telemetry/opentelemetry-demo.git
cd opentelemetry-demo/
docker compose up -d
```

We'd love your demo feedback: open-telemetry/opentelemetry-demo default setup.
The user guide is at https://opentelemetry.io/docs/demo/ for the login-free shop.
"""


def test_an_owner_repo_in_an_address_or_git_clone_is_never_a_sign_in():
    assert R.readme_credentials(OTEL_README) is None
    assert R.readme_credentials("Login docs: https://github.com/admin/secret") is None
    assert R.readme_credentials(OTEL_README + "\nSign in as open-telemetry/opentelemetry-demo"
                                "\n") is None


def test_a_real_sign_in_is_still_found():
    assert R.readme_credentials("## Demo\n\nUsername: admin / Password: admin\n") == (
        "admin", "admin")
    assert R.readme_credentials(OTEL_README + "\n## Login\n\nUsername: admin\nPassword: "
                                "admin\n") == ("admin", "admin")


# ── outside-widget errors do not fail an app (2026-10-14, d) ────────────────

GLANCE = FIX / "glance_like"


def test_an_outside_widget_error_leaves_the_app_working():
    got = R.judge((GLANCE / "page.txt").read_text(), (GLANCE / "server.log").read_text(),
                  "Glance")
    assert got["working"] and got["said"] == ""
    assert got["note"] == "www.reddit.com is not answering right now; the app works"


@pytest.mark.parametrize("line, host", [
    ("Error: 403 from api.example.com", "api.example.com"),
    ("Videos error: Reddit returned 403", "Reddit"),
    ("Error loading https://feeds.example.org/rss", "feeds.example.org"),
])
def test_a_banner_that_names_an_outside_service_is_not_the_apps_error(line, host):
    got = R.judge(f"Dashboard\nWeather\n{line}", "", "Glance")
    assert got["working"] and got["note"].startswith(f"{host} is not answering")


def test_its_own_api_errors_still_count():
    log = (GLANCE / "server.log").read_text() + \
        '127.0.0.1 - - "GET /api/pages/home HTTP/1.1" 500 12\n'
    got = R.judge("Home\nERROR", log, "Glance")
    assert not got["working"] and "/api/pages/home" in got["said"]
    assert not R.judge("Home\nERROR", "GET /index.html 200\n", "Glance")["working"]
