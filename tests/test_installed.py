"""Everything installed is one list the brain sees and the dashboard shows
(owner: "The LLM should have access to any tool, skill or app it installs").

The list reads the registries that already exist (Toolsmith's apps, run_here,
assembled apps, skills, tool servers, box command line tools); the brain's
system prompt gets every row, a stopped app included; a running app's own
OpenAPI description becomes use_local_app calls; a stopped app is started
before read_app reads it, with the same yes start_local_app asks. Nothing
here goes online.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import os

import pytest

from puffpai import installed

FIELDS = {"id", "kind", "name", "what", "status", "url", "source_url", "brain"}


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    from puffpai.integrate import host_tools
    host_tools._OPS_CACHE.clear()  # noqa: SLF001
    return tmp_path / "state"


def _skill(name: str, desc: str) -> None:
    from puffpai import skills
    folder = os.path.join(skills.skills_dir(create=True), name)
    os.makedirs(folder)
    with open(os.path.join(folder, "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write(f"---\nname: {name}\ndescription: {desc}\n---\nDo the thing.")


def _toolsmith(monkeypatch, apps: dict, running: set) -> None:
    from puffpai.build import toolsmith_run as T
    monkeypatch.setattr(T, "registry", lambda: apps)
    monkeypatch.setattr(T, "port_open", lambda port: port in running)


def _mock_everything(monkeypatch):
    from puffpai import mcp_client
    from puffpai.assemble import api as assemble_api
    from puffpai.integrate import records, run_here
    _toolsmith(monkeypatch, {"ledger-abc123": {
        "name": "Ledger", "url": "http://127.0.0.1:5999/", "port": 5999,
        "repo": "someone/ledger", "ref": "github:someone/ledger"}}, set())
    plans = {"notes-aaaaaaaa": {"how": "node_server"}, "tidy-bbbbbbbb": {"how": "python_cli"},
             "pics-cccccccc": {"how": "assembled", "assembled": {"app_id": "space_pics"}}}
    monkeypatch.setattr(run_here, "all_ids", lambda: list(plans))
    monkeypatch.setattr(records, "get", lambda iid: {
        "status": "ready", "name": iid.split("-")[0].title(), "purpose": f"{iid} purpose",
        "from": {"url": f"https://github.com/o/{iid.split('-')[0]}"}})
    monkeypatch.setattr(run_here, "read_state", lambda iid: {"plan": plans[iid]})
    monkeypatch.setattr(run_here, "status", lambda iid: {
        "state": "running" if iid != "tidy-bbbbbbbb" else "set_up",
        "url": "http://127.0.0.1:6001/", "can_run": iid != "tidy-bbbbbbbb",
        "how_words": ""})
    monkeypatch.setattr(run_here, "cli_commands", lambda iid: {"tidy": '"C:/v/tidy.exe"'}
                        if iid == "tidy-bbbbbbbb" else {})
    monkeypatch.setattr(records, "all", lambda: [
        {"id": "csvkit-dddddddd", "mode": "cli_tool", "status": "ready", "name": "csvkit",
         "purpose": "Work with CSV files", "from": {"url": "https://pypi.org/project/csvkit/"}}])
    monkeypatch.setattr(assemble_api, "all_specs", lambda: [
        {"id": "space_pics", "title": "Space pictures", "actions": [{"id": "pictures"}]}])
    monkeypatch.setattr(mcp_client, "servers", lambda: [
        {"name": "maps", "label": "Maps", "tools": ["route", "geocode"], "switched_on": True,
         "source": "https://github.com/o/maps-mcp"}])
    _skill("garden-helper", "Plan a vegetable garden.")
    from puffpai import programs
    monkeypatch.setattr(programs, "rows", lambda: [
        {"id": "p0000000a", "name": "My converter", "kind": "cli", "path": "C:/t/conv.exe",
         "command": "conv", "added_at": "", "help": "", "always_allow": False,
         "commands": [{"command": "conv convert", "argv": ["conv", "convert", "{file}"]}]},
        {"id": "p0000000b", "name": "Word", "kind": "desktop", "path": "C:/o/WINWORD.EXE",
         "command": "", "added_at": "", "help": "", "commands": [], "always_allow": False}])


def test_listing_has_every_kind_in_the_pinned_shape(state, monkeypatch):
    _mock_everything(monkeypatch)
    rows = installed.listing()
    assert all(set(r) == FIELDS for r in rows)
    by_id = {r["id"]: r for r in rows}
    led = by_id["ledger-abc123"]
    assert (led["kind"], led["status"], led["url"]) == ("app", "stopped", "")
    assert led["source_url"] == "https://github.com/someone/ledger"
    assert "starts it first" in led["brain"]
    notes = by_id["notes-aaaaaaaa"]
    assert (notes["kind"], notes["status"], notes["url"]) == ("app", "running",
                                                              "http://127.0.0.1:6001/")
    assert by_id["tidy-bbbbbbbb"]["kind"] == "cli" and "tidy" in by_id["tidy-bbbbbbbb"]["brain"]
    pics = by_id["assembled:space_pics"]
    assert pics["status"] == "running" and "space_pics__pictures" in pics["brain"]
    assert "pics-cccccccc" not in by_id               # one row per assembled app
    assert by_id["mcp:maps"]["kind"] == "tool_server" and "mcp__maps__route" in \
        by_id["mcp:maps"]["brain"]
    assert by_id["csvkit-dddddddd"]["kind"] == "cli"
    assert by_id["skill:garden-helper"]["kind"] == "skill"
    mine = by_id["program:p0000000a"]
    assert mine["kind"] == "program" and mine["status"] == "ready"
    assert mine["brain"].startswith("run_command conv") and "conv convert" in mine["what"]
    word = by_id["program:p0000000b"]
    assert word["kind"] == "desktop" and "open files in it" in word["brain"]
    assert {r["kind"] for r in rows} == set(installed.KINDS) - {"knowledge"}
    assert {r["status"] for r in rows} <= set(installed.STATUSES)


def test_her_own_github_project_says_so(state, monkeypatch):
    from puffpai import github_account
    _toolsmith(monkeypatch, {"mine-abc123": {"name": "Mine", "port": 1, "repo": "her/mine"}},
               {1})
    monkeypatch.setattr(github_account, "status", lambda: {"login": "Her"})
    row = next(r for r in installed.listing() if r["id"] == "mine-abc123")
    assert row["what"].startswith("Your own project on GitHub")


def test_prompt_block_has_a_stopped_app_and_every_skill(state, monkeypatch):
    _toolsmith(monkeypatch, {"ledger-abc123": {"name": "Ledger", "port": 5999,
                                               "url": "http://127.0.0.1:5999/"}}, set())
    for n in range(15):
        _skill(f"skill-{n:02d}", f"Does job {n}.")
    block = installed.prompt_block()
    assert "- Ledger (app, stopped)" in block
    assert all(f"skill-{n:02d}" in block for n in range(15))


def test_no_eight_app_cut_and_a_named_rest_above_sixty(state, monkeypatch):
    from puffpai import brain_loop
    apps = {f"app{n:02d}-abc123": {"name": f"App {n:02d}", "port": 10000 + n}
            for n in range(20)}
    _toolsmith(monkeypatch, apps, set())
    notes = "\n".join(brain_loop.system_notes([]))
    assert all(f"App {n:02d}" in notes for n in range(20))
    rows = [installed._row(f"s{n}", "skill", f"s{n}") for n in range(70)]  # noqa: SLF001
    block = installed.prompt_block(rows)
    assert "s59" in block and "- s60 " not in block
    assert "10 more, ask list_installed" in block


def test_list_installed_tool_gives_the_whole_list(state, monkeypatch):
    from puffpai import brain_tools
    _skill("garden-helper", "Plan a vegetable garden.")
    installed.register_tool()
    got = brain_tools.call("list_installed", {"kind": "skill"}, {})
    assert got["ok"] and any("garden-helper" in line for line in got["result"])


def _spec_server(monkeypatch, calls: list):
    from puffpai.integrate import run_here
    spec = {"swagger": "2.0", "paths": {"/items": {"get": {"summary": "List items"},
                                                   "post": {"summary": "Add an item"}}}}

    def local_request(port, method, path, body=None, timeout=30.0):
        calls.append((method, path))
        if path == "/swagger.json":
            return 200, json.dumps(spec).encode()
        if path == "/openapi.json":
            return 200, b"<html>an app page</html>"
        if path == "/items":
            return 201 if method == "POST" else 200, b'{"items": []}'
        return 404, b""
    monkeypatch.setattr(run_here, "_local_request", local_request)


def test_a_toolsmith_apps_openapi_becomes_its_calls(state, monkeypatch):
    from puffpai import brain_tools
    from puffpai.integrate import host_tools
    _toolsmith(monkeypatch, {"ledger-abc123": {"name": "Ledger", "port": 5999,
                                               "url": "http://127.0.0.1:5999/",
                                               "started_at": 7}}, {5999})
    calls: list = []
    _spec_server(monkeypatch, calls)
    app = host_tools._apps()[0]  # noqa: SLF001
    assert {(o["method"], o["path"]) for o in app["ops"]} == {("GET", "/items"),
                                                             ("POST", "/items")}
    asked = len(calls)
    host_tools._apps()  # noqa: SLF001
    assert len(calls) == asked                      # read once per start
    names = [t.name for t in host_tools.provide()]
    assert "use_local_app" in names
    use = next(t for t in host_tools.provide() if t.name == "use_local_app")
    assert brain_tools.question_for(use, {"app": "Ledger", "method": "POST",
                                          "path": "/items"}, {})        # writes ask
    got = host_tools._use({"app": "Ledger", "method": "GET", "path": "/items"})  # noqa: SLF001
    assert got["ok"] and got["status"] == 200
    refused = host_tools._use({"app": "Ledger", "method": "DELETE", "path": "/x"})  # noqa: SLF001
    assert not refused["ok"]


def test_read_app_starts_a_stopped_toolsmith_app_after_its_yes(state, monkeypatch):
    from puffpai.build import toolsmith_run as T
    from puffpai.integrate import host_tools
    _toolsmith(monkeypatch, {"ledger-abc123": {"name": "Ledger", "port": 5999,
                                               "url": "http://127.0.0.1:5999/"}}, set())
    started = []
    monkeypatch.setattr(T, "restart", lambda app_id, wait=60.0: started.append(app_id) or {
        "ok": True, "said": "Ledger is running again.", "url": "http://127.0.0.1:5999/"})
    monkeypatch.setattr(host_tools, "read_page",
                        lambda url: {"ok": True, "url": url, "text": "Accounts"})
    q = host_tools._read_question({"app": "Ledger"})  # noqa: SLF001
    assert q and q["title"] == "May I start Ledger?"
    got = host_tools._read({"app": "ledger"})  # noqa: SLF001
    assert started == ["ledger-abc123"] and got["text"] == "Accounts"


def test_read_app_still_asks_when_the_app_list_breaks(state, monkeypatch):
    from puffpai.integrate import host_tools

    def broken():
        raise OSError("registry unreadable")
    monkeypatch.setattr(host_tools, "_apps", broken)
    q = host_tools._read_question({"app": "Ledger"})  # noqa: SLF001
    assert q and q["title"] == "May I start Ledger?"


def test_a_shown_address_never_carries_a_sign_in():
    assert installed._web("https://user:secret@example.com/x") == ""  # noqa: SLF001
    signed_in = "https://token" + "@" + "github.com/o/r"      # not written as one address
    assert installed._web(signed_in) == ""  # noqa: SLF001
    assert installed._web("https://github.com/o/r") == "https://github.com/o/r"  # noqa: SLF001
    assert installed._web("https://www.npmjs.com/package/@scope/x") \
        == "https://www.npmjs.com/package/@scope/x"  # noqa: SLF001
    assert installed._web("javascript:alert(1)") == ""  # noqa: SLF001


def test_restart_runs_the_start_command_that_answered(state, monkeypatch, tmp_path):
    from puffpai.build import toolsmith_run as T
    folder = tmp_path / "bb-ledger-abc123"
    folder.mkdir()
    reg = {"ledger-abc123": {"name": "Ledger", "port": 5999, "url": "http://127.0.0.1:5999/",
                             "folder": str(folder),
                             "starts": [{"command": "npm run dev", "port": 5999}]}}
    up = {"now": False}
    monkeypatch.setattr(T, "registry", lambda: reg)
    monkeypatch.setattr(T, "port_open", lambda port: up["now"])
    monkeypatch.setattr(T, "_program", lambda name: None)
    ran = []

    class P:
        pid = 4242

    def spawn(cmd, cwd, port, project, log):
        ran.append((cmd, cwd, port))
        up["now"] = True
        return P()
    monkeypatch.setattr(T, "spawn_server", spawn)
    monkeypatch.setattr(T.fsutil, "atomic_write_json", lambda path, data: None)
    got = T.restart("ledger-abc123", wait=1)
    assert got["ok"] and got["url"] == "http://127.0.0.1:5999/"
    assert ran == [("npm run dev", str(folder), 5999)]


def test_run_command_names_the_installed_command_line_tools(state, monkeypatch):
    from puffpai import brain_commands
    monkeypatch.setattr(installed, "cli_commands", lambda: {"tidy": '"C:/v/tidy.exe"'})
    assert 'tidy: "C:/v/tidy.exe"' in brain_commands.description()
    monkeypatch.setattr(installed, "cli_commands", lambda: {})
    assert brain_commands.description() == brain_commands.DESCRIPTION


def test_get_installed_route_shape(state, monkeypatch):
    from flask import Flask
    from puffpai.build import api
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    monkeypatch.setattr(api, "_device", lambda: "device:test")
    _skill("garden-helper", "Plan a vegetable garden.")
    got = app.test_client().get("/installed")
    assert got.status_code == 200
    body = got.get_json()
    assert set(body) == {"items"} and body["items"]
    assert all(set(r) == FIELDS for r in body["items"])
    monkeypatch.setattr(api, "_device", lambda: "")
    assert app.test_client().get("/installed").status_code == 403


def test_one_row_per_repo_the_running_copy_kept(state, monkeypatch):
    """A retry registered the same repo twice: home showed a stale STOPPED tile."""
    _toolsmith(monkeypatch, {
        "worldmonitor-b292c0": {"name": "worldmonitor", "port": 3001, "repo": "k/worldmonitor"},
        "worldmonitor-aaa111": {"name": "worldmonitor", "port": 3000, "repo": "k/worldmonitor"},
        "other-ccc333": {"name": "other", "port": 4000, "repo": "o/other"}}, {3000})
    rows = [r for r in installed.listing() if r["kind"] == "app"]
    ids = [r["id"] for r in rows]
    assert "worldmonitor-aaa111" in ids and "worldmonitor-b292c0" not in ids
    assert "other-ccc333" in ids
    assert next(r for r in rows if r["id"] == "worldmonitor-aaa111")["status"] == "running"
