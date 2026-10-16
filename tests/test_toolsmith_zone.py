"""Toolsmith's zone (owner, 2026-10-14): "Toolsmith shouldn't have access to
the internal parts, as it's the agent that touches the outside."

Each denied place is refused with one plain line, even with her yes; the
allowed places work; her account name never reaches the model; a Run's
first messages and tool results name nothing of BuildBot's own state, the
vault or the data folder. Nothing here goes online or starts a shell.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import os

import pytest

from puffpai.security import toolsmith_zone as Z


@pytest.fixture
def places(monkeypatch, tmp_path):
    """A fake home (keys, AppData, Documents), state, data and vault folder,
    one downloaded app's try folder and one toolchain."""
    home = tmp_path / "home"
    appdata, local = home / "AppData" / "Roaming", home / "AppData" / "Local"
    for d in (home / ".ssh", home / ".aws", home / "Documents" / "myapp", home / "Desktop",
              appdata / "Microsoft" / "Credentials", appdata / "npm-cache" / "_cacache",
              local / "Google" / "Chrome" / "User Data",
              appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"):
        d.mkdir(parents=True)
    (home / ".ssh" / "id_rsa").write_text("not a key", encoding="utf-8")
    for k, v in (("USERPROFILE", home), ("HOME", home), ("APPDATA", appdata),
                 ("LOCALAPPDATA", local), ("PUFFPAI_STATE_DIR", tmp_path / "state"),
                 ("PUFFPAI_DATA_DIR", tmp_path / "data")):
        monkeypatch.setenv(k, str(v))
    tools = tmp_path / "tools" / "nodejs"
    tools.mkdir(parents=True)
    (tools / "node.exe").write_text("", encoding="utf-8")
    real_which = Z.shutil.which
    monkeypatch.setattr(Z.shutil, "which", lambda n: str(tools / "node.exe") if n == "node"
                        else None if n in Z._TOOL_PROGRAMS else real_which(n))  # noqa: SLF001
    from puffpai.build import toolsmith_run as T
    folder = os.path.join(T.apps_root(), "bb-ledger-abc123")
    os.makedirs(folder)
    os.makedirs(os.path.join(T.apps_root(), "bb-other-def456"))
    state = tmp_path / "state"
    for name in ("toolsmith_model.json", "programs.json", "config.json"):
        (state / name).write_text("{}", encoding="utf-8")
    from puffpai.security import vault_place
    from puffpai.knowledge import my_data
    vault, data = vault_place.configured_folder(), my_data.data_dir()
    os.makedirs(vault, exist_ok=True)
    os.makedirs(os.path.join(data, "My files"), exist_ok=True)
    zone = T.zone_of(folder)
    return {"home": str(home), "appdata": str(appdata), "local": str(local),
            "state": str(state), "folder": folder, "zone": zone, "tools": str(tools),
            "vault": vault, "data": data, "tmp": tmp_path}


def _refused(cmd, p, category):
    line, _out = Z.command_check(cmd, p["zone"])
    assert line == Z.refused_line(category), (cmd, line)


def test_sign_in_keys_are_refused(places):
    _refused("cat ~/.ssh/id_rsa", places, Z.KEYS)
    _refused('cat "$HOME/.aws/credentials"', places, Z.KEYS)
    _refused(r"type %APPDATA%\Microsoft\Credentials\x", places, Z.KEYS)
    _refused(r"Get-Content $env:APPDATA\Microsoft\Credentials\x", places, Z.KEYS)


def test_buildbots_own_places_are_refused(places):
    _refused(f'cd "{places["state"]}"', places, Z.STATE)
    _refused("cat ../../toolsmith_model.json", places, Z.STATE)      # a .. escape
    _refused("cat ../bb-other-def456/.env", places, Z.OTHER_APP)
    _refused("cd ..", places, Z.OTHER_APP)
    _refused(f'echo hi > "{os.path.join(places["data"], "notes.txt")}"', places, Z.DATA)
    _refused(f'ls "{places["vault"]}"', places, Z.VAULT)
    from puffpai.build import apps
    _refused(f'cat "{os.path.join(apps.install_root(), "AGENTS.md")}"', places, Z.CODE)
    _refused("python -m puffpai vault status", places, Z.CODE)


def test_her_own_folders_are_refused(places):
    _refused("cp .env ~/Documents/", places, Z.PERSONAL)
    _refused(f'ls "{places["local"]}/Google/Chrome/User Data"', places, Z.BROWSER)
    _refused(f'cp x.bat "{places["appdata"]}/Microsoft/Windows/Start Menu/Programs/Startup/"',
             places, Z.STARTUP)
    _refused("grep -r token ~", places, Z.WHOLE)
    _refused("tar czf all.tgz ~", places, Z.WHOLE)


def test_docker_mounts_of_her_home_or_keys_are_refused(places):
    _refused(f'docker run -v "{places["home"]}:/x" img', places, Z.WHOLE)
    _refused("docker run -v ~/.aws:/root/.aws img", places, Z.KEYS)
    _refused(f"docker run --mount type=bind,source={places['home']}/.ssh,target=/k img",
             places, Z.KEYS)
    assert Z.command_check("docker run -v appdata:/data -v ./conf:/conf img",
                           places["zone"])[0] == ""


def test_a_compose_file_mounting_her_keys_is_refused(places):
    z, f = places["zone"], places["folder"]
    bad = "services:\n  app:\n    image: x\n    volumes:\n      - ~/.aws:/root/.aws\n"
    assert Z.compose_refusal(bad, f, z) == Z.refused_line(Z.KEYS)
    via_env = "services:\n  app:\n    image: x\n    volumes:\n      - ${KEYS}:/k\n"
    assert Z.compose_refusal(via_env, f, z, {"KEYS": "~/.ssh"}) == Z.refused_line(Z.KEYS)
    ok = "services:\n  app:\n    image: x\n    volumes:\n      - ./data:/data\n      - db:/v\n"
    assert Z.compose_refusal(ok, f, z) == ""


def test_a_container_holding_the_docker_engine_is_refused(places):
    z, f = places["zone"], places["folder"]
    for cmd in ("docker run -v /var/run/docker.sock:/var/run/docker.sock img",
                r"docker run -v \\.\pipe\docker_engine:\\.\pipe\docker_engine img",
                "docker run --privileged img"):
        assert Z.command_check(cmd, z)[0] == Z.refused_line(Z.ENGINE), cmd
    text = "services:\n  a:\n    image: x\n    privileged: true\n"
    assert Z.compose_refusal(text, f, z) == Z.refused_line(Z.ENGINE)


def test_the_allowlist_works(places):
    z = places["zone"]
    for cmd in ("npm install", "npm ci --no-audit", "cat ./README.md", "cp .env.example .env",
                f'ls "{places["appdata"]}/npm-cache"',
                f'"{os.path.join(places["tools"], "node.exe")}" --version',
                "cat /tmp/build.log" if os.name == "nt" else "ls ./node_modules"):
        assert Z.command_check(cmd, z) == ("", []), cmd
    from puffpai.build import toolsmith_run as T
    assert T.command_problem("npm install", places["folder"], zone=z) == ""
    # Read-only: the cache is read, never written to by a named path.
    assert z.classify(os.path.join(places["appdata"], "npm-cache", "x"), write=True)[0] \
        == Z.OUTSIDE


def test_the_owners_yes_never_covers_a_denied_place(places, monkeypatch):
    from puffpai.build import toolsmith_run as T
    cmd = "cat ~/.ssh/id_rsa"
    run = T.Run({"id": "j1", "name": "x"}, [{"ref": "a", "name": "A"}], lambda _t: None,
                approved=[cmd])
    run.app, run.folder = {"ref": "a", "name": "A"}, places["folder"]
    monkeypatch.setattr(run, "_ensure_shell", lambda: pytest.fail("a shell was started"))
    got = run.terminal({"command": cmd})
    assert got == {"ok": False, "said": Z.refused_line(Z.KEYS)}
    assert run.pending == []                     # never put on her list either


def test_start_server_outside_the_folder_is_refused(places, monkeypatch):
    from puffpai.build import toolsmith_run as T
    monkeypatch.setattr(T, "spawn_server", lambda *a, **k: pytest.fail("started"))
    run = T.Run({"id": "j1", "name": "x"}, [{"ref": "a", "name": "A"}], lambda _t: None)
    run.app, run.folder = {"ref": "a", "name": "A"}, places["folder"]
    got = run.start_server({"command": "cd ~/.ssh && python -m http.server"})
    assert got["said"] == Z.refused_line(Z.KEYS)
    got = run.start_server({"command": f'cd "{places["state"]}" && node server.js'})
    assert got["said"] == Z.refused_line(Z.STATE)
    # Its cwd itself must be inside the zone.
    run.folder = places["state"]
    run.zone = None
    assert run.start_server({"command": "node server.js"})["ok"] is False


def test_write_file_and_install_program_stay_in_the_zone(places):
    from puffpai.build import toolsmith_run as T
    run = T.Run({"id": "j1", "name": "x"}, [{"ref": "a", "name": "A"}], lambda _t: None)
    run.app, run.folder = {"ref": "a", "name": "A"}, places["folder"]
    assert run.write_file({"path": "../../programs.json", "text": "{}"})["ok"] is False
    assert run.write_file({"path": ".env", "text": "A=1"})["ok"] is True
    got = run.install_program({"program": "node", "package_id": "~/.ssh/id_rsa"})
    assert got["said"] == Z.refused_line(Z.KEYS)


def test_her_chosen_folder_is_allowed_but_not_its_parent(places):
    mine = os.path.join(places["home"], "Documents", "myapp")
    assert Z.folder_problem(mine) == ""
    z = Z.zone_for(mine)
    assert z.classify(os.path.join(mine, "src", "a.js"))[0] == Z.ALLOW
    assert z.classify(os.path.join(places["home"], "Documents")) == (Z.DENY, Z.PERSONAL)
    assert Z.command_check("cat ../notes.txt", z, mine)[0] == Z.refused_line(Z.PERSONAL)
    assert Z.folder_problem(places["home"]) == Z.refused_line(Z.WHOLE)
    assert Z.folder_problem(places["appdata"])          # holds keys and browser data


def test_her_name_is_scrubbed_from_outgoing_text_and_put_back_on_the_way_in():
    text = r"cloned into C:\Users\samplehost\x; /c/Users/samplehost/y; whoami: samplehost"
    out = Z.scrub(text, "samplehost")
    assert "samplehost" not in out and r"C:\Users\you\x" in out and "/c/Users/you/y" in out
    assert Z.scrub({"output": [text]}, "samplehost") == {"output": [out]}
    assert Z.unscrub(r"cd C:\Users\you\x", "samplehost") == r"cd C:\Users\samplehost\x"
    assert Z.scrub("a short name stays put", "al") == "a short name stays put"


def test_a_tool_result_is_scrubbed_before_the_model_sees_it(places, monkeypatch):
    from puffpai.build import toolsmith_run as T
    monkeypatch.setattr(Z, "user_name", lambda: "samplehost")
    run = T.Run({"id": "j1", "name": "x"}, [{"ref": "a", "name": "A"}], lambda _t: None)
    run.app, run.folder = {"ref": "a", "name": "A"}, places["folder"]
    monkeypatch.setattr(run, "_log_tail", lambda: r"error at C:\Users\samplehost\app\x.js")
    tools = {t.name: t.fn for t in run.tools()}
    assert tools["server_log"]({}) == {"ok": True, "log": r"error at C:\Users\you\app\x.js"}


def test_toolsmiths_shell_has_no_buildbot_command_line_or_settings(places):
    from puffpai.build import toolsmith_run as T
    env = T.toolsmith_env(places["folder"])
    assert not [k for k in env if k.upper().startswith("PUFFPAI")]
    path = next(v for k, v in env.items() if k.upper() == "PATH")
    shims = os.path.join(T.apps_root(), ".toolsmith-shims")
    for p in path.split(os.pathsep)[1:]:
        assert not Z.inside_or_equal(Z.canon(p), Z.canon(places["state"])), p
    assert path.split(os.pathsep)[0] == shims
    assert not [n for n in os.listdir(shims) if n.startswith("puffpai")]
    assert env["TEMP"] == env["TMP"] == T.scratch_for(places["folder"])


def test_nothing_internal_is_in_a_runs_conversation(places, monkeypatch):
    """The first messages and the tool results of a Run for a fake job name
    no state-folder file, no vault path and no data folder path."""
    from puffpai.build import apps, toolsmith_run as T
    state = places["state"]
    os.makedirs(os.path.join(state, "chats"))
    with open(os.path.join(state, "chats", "chat-2026.json"), "w", encoding="utf-8") as fh:
        json.dump({"turns": ["private words"]}, fh)
    for name in ("consent_tickets.json", "toolsmith_key_ready.json", "bot.log"):
        with open(os.path.join(state, name), "w", encoding="utf-8") as fh:
            fh.write("{}")
    code = places["tmp"] / "code" / "plants"
    code.mkdir(parents=True)
    (code / "README.md").write_text("# Plants\n\nTracks plants.\n\n## Quick start\n\n"
                                    "```\nnpm install\nnpm start\n```\n", encoding="utf-8")
    (code / "package.json").write_text('{"scripts": {"start": "node server.js"}}',
                                       encoding="utf-8")
    cand = apps.from_local_folder(str(code))
    monkeypatch.setattr(T, "listening_ports", lambda: set())
    monkeypatch.setattr(T.subprocess, "run", lambda *a, **k: pytest.fail("ran " + str(a)))
    run = T.Run({"id": "j1", "name": "Plant tracker", "does": "Track my plants."}, [cand],
                lambda _t: None)
    tools = {t.name: t.fn for t in run.tools()}
    seen = [run.task()]
    seen.append(tools["download"]({"app": cand["name"]}))
    seen.append(run.task())
    monkeypatch.setattr(run, "_ensure_shell", lambda: pytest.fail("a shell was started"))
    # (Commands naming no internal file: the model's own words are echoed back.)
    for cmd in ("cat ~/.ssh/id_rsa", f'cd "{state}"', "cd ../..", f'ls "{places["vault"]}"',
                f'cat "{places["data"]}/My files/x.txt"'):
        seen.append(tools["terminal"]({"command": cmd}))
    seen.append(tools["write_file"]({"path": ".env", "text": "PORT=1"}))
    seen.append(tools["server_log"]({}))
    seen.append(tools["install_program"]({"program": "x", "package_id": "~/.aws/config"}))
    text = json.dumps(seen, default=str)
    norm = text.replace("\\\\", "\\").lower()
    for name in sorted(os.listdir(state)):
        assert name.lower() not in norm, name
    for place in (state, places["vault"], places["data"]):
        assert os.path.normcase(place).lower() not in norm, place
        assert place.replace("\\", "/").lower() not in norm, place
    assert "private words" not in text
