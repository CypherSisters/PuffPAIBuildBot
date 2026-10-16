"""(ii) A skill's own command line program, installed by BuildBot into a
folder of that skill's own, gets the skill's key for one run (RunComfy/ElevenLabs). Import
shadowing holes are closed (P1-P5).

The whole isolated folder is hashed at install and re-walked before the run;
the program runs with BuildBot's own Python, isolated, with sys.path limited
to the stdlib plus that folder. The happy path really runs a tiny CLI that
prints a hash of its key, never the key. Nothing goes online.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import types

import pytest

from keys_support import PIN, fast_vault, reset_keys
from puffpai import brain_commands, deps
from puffpai.finding import install
from puffpai.security import host_programs, keys, skill_programs

TOKEN = "rc_live_7a6b5c4d3e2f1a0b9c8d7e6f"
VAR = "RUNCOMFY_TOKEN"
SKILL = "runcomfy-gen"
SKILL_MD = """---
name: runcomfy-gen
allowed-tools: Bash(runcomfy *)
description: Make songs with ElevenLabs Music on RunComfy via the runcomfy CLI.
metadata: {"openclaw":{"requires":{"bins":["runcomfy"],"env":["RUNCOMFY_TOKEN"]},"primaryEnv":"RUNCOMFY_TOKEN"}}
---
Run `runcomfy run --model elevenlabs-music "a calm song"`.
"""
CLI = """import hashlib, os, sys
def main():
    v = os.environ.get("RUNCOMFY_TOKEN", "")
    print("hash=" + hashlib.sha256(v.encode()).hexdigest())
    print("secretish=" + ",".join(sorted(k for k in os.environ
          if any(w in k.upper() for w in ("TOKEN", "KEY", "SECRET")))))
    print("args=" + "|".join(sys.argv[1:]))
    with open("made.txt", "w") as fh:
        fh.write("x")
    return 0
"""
WANT_HASH = hashlib.sha256(TOKEN.encode()).hexdigest()


def _lay_pip(target, pkg="fakecomfy", dist="fakecomfy-1.0.dist-info", cli=CLI, entry="fakecomfy.cli:main"):
    """Mimic `pip install --target target`: the package and its dist-info."""
    os.makedirs(os.path.join(target, pkg), exist_ok=True)
    open(os.path.join(target, pkg, "__init__.py"), "w").close()
    with open(os.path.join(target, pkg, "cli.py"), "w", encoding="utf-8") as fh:
        fh.write(cli)
    info = os.path.join(target, dist)
    os.makedirs(info, exist_ok=True)
    with open(os.path.join(info, "entry_points.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"[console_scripts]\nruncomfy = {entry}\n")


@pytest.fixture
def world(monkeypatch, tmp_path):
    state = tmp_path / "state"
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(state))
    monkeypatch.setenv("SOME_OTHER_API_KEY", "never-reaches-the-program")
    monkeypatch.setattr(brain_commands, "_vault_dir", lambda: str(tmp_path / "vault"))
    reset_keys()
    v = fast_vault(monkeypatch, tmp_path / "vault")
    keys.set_vault_provider(lambda: v)
    install.write_skill(SKILL, {"SKILL.md": SKILL_MD.encode("utf-8")},
                        {"source": "clawhub", "owner": "agentspace", "slug": SKILL})
    keys.put_skill_key(SKILL, VAR, TOKEN, declared=deps.key_names(SKILL))

    def fake_installer(argv, extra_env=None):
        target = argv[argv.index("--target") + 1]
        _lay_pip(target)
        return {"code": 0, "output": "ok"}
    monkeypatch.setattr(deps, "_run_installer", fake_installer)
    assert skill_programs.record_installed({"kind": "pip", "name": "fakecomfy"}, "1.0",
                                           SKILL) == ["runcomfy"]
    yield {"vault": v, "state": state, "tmp": tmp_path,
           "root": skill_programs.iso_root(SKILL, "pip", "fakecomfy")}
    keys.set_vault_provider(None)


COMMAND = 'runcomfy run --model elevenlabs-music "a calm song"'


def _ask(command=COMMAND, skill=SKILL):
    return brain_commands.question({"command": command, "skill": skill}, {})


def _run(command=COMMAND, skill=SKILL):
    return brain_commands.run({"command": command, "skill": skill},
                              {"said_yes": True, "device_id": "deviceaaaa000001"})


def _audit(world) -> str:
    path = world["state"] / "security_logs" / "audit.jsonl"
    return path.read_text(encoding="utf-8") if path.exists() else ""


# ── happy path ───────────────────────────────────────────────────────────────

def test_the_recorded_cli_gets_only_its_skills_key_for_one_run(world):
    card = _ask()
    assert (f"gives your {SKILL} key ({VAR}) to runcomfy (from fakecomfy 1.0, installed "
            f"by BuildBot) for this one run") in card["detail"]
    assert card["key_use"] == {"names": [VAR], "program": "runcomfy",
                               "package": "fakecomfy", "version": "1.0"}
    assert TOKEN not in json.dumps(card)

    got = _run()
    assert got["worked"], got
    assert f"hash={WANT_HASH}" in got["output"]            # it had the key
    assert "secretish=RUNCOMFY_TOKEN\n" in got["output"] + "\n"   # and only that one
    assert "args=run|--model|elevenlabs-music|a calm song" in got["output"]
    made_root = os.path.realpath(brain_commands.made_dir(SKILL))
    assert os.path.realpath(got["folder"]).startswith(made_root)
    assert any(p.endswith("made.txt") for p in got.get("made", []))
    assert TOKEN not in json.dumps(got)
    log = _audit(world)
    assert TOKEN not in log and "run_command_skill_key_program" in log


# ── P1-P5 ─────────────────────────────────────────────────────

def test_P1_a_package_dir_planted_in_the_folder_gets_no_key(world):
    # A planted fakecomfy/cli/__init__.py that would shadow the hashed cli.py:
    # it is a new file in the folder, so the manifest no longer matches.
    sub = os.path.join(world["root"], "fakecomfy", "cli")
    os.makedirs(sub)
    with open(os.path.join(sub, "__init__.py"), "w", encoding="utf-8") as fh:
        fh.write("import os; print('EVIL-RAN ' + os.environ.get('RUNCOMFY_TOKEN',''))")
    for act in (_ask, _run):
        with pytest.raises(ValueError, match="files changed since BuildBot installed it"):
            act()
    assert not os.path.isdir(brain_commands.made_dir(SKILL))


def test_P2_a_planted_pyc_gets_no_key(world):
    cache = os.path.join(world["root"], "fakecomfy", "__pycache__")
    os.makedirs(cache)
    with open(os.path.join(cache, "cli.cpython-314.pyc"), "wb") as fh:
        fh.write(b"\x00planted")
    with pytest.raises(ValueError, match="files changed since BuildBot installed it"):
        _run()


def test_P3_a_toplevel_module_planted_in_the_folder_gets_no_key(world):
    with open(os.path.join(world["root"], "hashlib.py"), "w", encoding="utf-8") as fh:
        fh.write("raise SystemExit('shadowed')")
    with pytest.raises(ValueError, match="files changed since BuildBot installed it"):
        _run()


def test_P3b_the_stdlib_is_not_shadowed_by_a_folder_module(world, monkeypatch):
    # A hashlib.py present AT install time is recorded, so it does not trip the
    # manifest; the run must still import the real stdlib hashlib (appended
    # last), i.e. the program runs and reports the correct key hash.
    with open(os.path.join(world["root"], "hashlib.py"), "w", encoding="utf-8") as fh:
        fh.write("raise SystemExit('shadowed stdlib')")
    # re-record so the manifest includes the extra file
    def fake_installer(argv, extra_env=None):
        target = argv[argv.index("--target") + 1]
        _lay_pip(target)
        with open(os.path.join(target, "hashlib.py"), "w", encoding="utf-8") as fh:
            fh.write("raise SystemExit('shadowed stdlib')")
        return {"code": 0, "output": "ok"}
    monkeypatch.setattr(deps, "_run_installer", fake_installer)
    assert skill_programs.record_installed({"kind": "pip", "name": "fakecomfy"}, "1.0",
                                           SKILL) == ["runcomfy"]
    got = _run()
    assert got["worked"] and f"hash={WANT_HASH}" in got["output"], got


def test_P4_a_changed_package_init_gets_no_key(world):
    with open(os.path.join(world["root"], "fakecomfy", "__init__.py"), "w",
              encoding="utf-8") as fh:
        fh.write("import os; print('EVIL ' + os.environ.get('RUNCOMFY_TOKEN',''))")
    with pytest.raises(ValueError, match="files changed since BuildBot installed it"):
        _run()


def test_P5_another_skills_same_name_program_never_takes_over(world, monkeypatch):
    # A second skill also declares runcomfy and keeps its own key. Recording it
    # must not touch the first skill's record or hand it the first skill's key.
    other, other_key = "songmaker", "rc_live_OTHER_0000111122223333"
    install.write_skill(other, {"SKILL.md": SKILL_MD.replace("runcomfy-gen", other)
                                .encode("utf-8")},
                        {"source": "clawhub", "owner": "someoneelse", "slug": other})
    keys.put_skill_key(other, VAR, other_key, declared=deps.key_names(other))

    def fake_installer(argv, extra_env=None):
        _lay_pip(argv[argv.index("--target") + 1])
        return {"code": 0, "output": "ok"}
    monkeypatch.setattr(deps, "_run_installer", fake_installer)
    assert skill_programs.record_installed({"kind": "pip", "name": "fakecomfy"}, "1.0",
                                           other) == ["runcomfy"]
    # The first skill's record is untouched: its run still uses its own key.
    got = _run(skill=SKILL)
    assert got["worked"] and f"hash={WANT_HASH}" in got["output"]
    got2 = _run(skill=other)
    assert got2["worked"] and f"hash={hashlib.sha256(other_key.encode()).hexdigest()}" \
        in got2["output"]
    assert world["root"] != skill_programs.iso_root(other, "pip", "fakecomfy")


# ── tampering with the record and the vault ──────────────────────────────────

def test_a_tampered_vault_record_is_not_read(world):
    from puffpai.security import vault as vault_mod
    files = list((world["tmp"] / "vault").glob("vault*.enc"))
    assert files
    for data in files:
        raw = bytearray(data.read_bytes())
        raw[len(raw) // 2] ^= 0x41
        data.write_bytes(bytes(raw))
    v2 = vault_mod.Vault(vault_dir=str(world["tmp"] / "vault"))
    with pytest.raises(Exception):
        v2.unlock(PIN)
    keys.set_vault_provider(lambda: v2)
    with pytest.raises(ValueError, match="Open your vault"):
        _run()


def test_a_plain_file_decoy_record_is_ignored(world, monkeypatch):
    monkeypatch.setattr(keys, "program_record", lambda *_a, **_k: None)
    card = _ask()
    assert "key_use" not in card                          # runs keyless


# ── planted programs on PATH and shell features ──────────────────────────────

def test_a_same_name_program_on_path_is_never_used(world, monkeypatch, tmp_path):
    planted = tmp_path / "planted"
    planted.mkdir()
    for name in ("runcomfy", "runcomfy.cmd", "runcomfy.bat", "runcomfy.exe"):
        (planted / name).write_text("echo PLANTED\n", encoding="utf-8")
    monkeypatch.setenv("PATH", str(planted) + os.pathsep + os.environ.get("PATH", ""))
    shim = brain_commands.shim_dir()
    with open(os.path.join(shim, "runcomfy"), "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\necho PLANTED\n")
    got = _run()
    assert got["worked"] and f"hash={WANT_HASH}" in got["output"]
    assert "PLANTED" not in got["output"]


@pytest.mark.parametrize("command", [
    "runcomfy run x | cat", "runcomfy run x > out.txt", "runcomfy run $(whoami)",
    "runcomfy run x && env", "RUNCOMFY_HOME=/srv/scratch runcomfy run x", "./runcomfy run x",
    "otherprog run x",
])
def test_anything_but_one_plain_call_stays_keyless(world, command):
    card = _ask(command)
    assert "key_use" not in card and VAR not in card["detail"]


def test_a_locked_vault_asks_to_open_it(world):
    world["vault"].lock()
    with pytest.raises(ValueError, match="Open your vault"):
        _ask()
    with pytest.raises(ValueError, match="Open your vault"):
        _run()


def test_installed_with_the_vault_locked_records_nothing_and_says_so(world, monkeypatch):
    world["vault"].lock()
    said: list = []
    assert skill_programs.record_installed({"kind": "pip", "name": "fakecomfy"}, "1.0",
                                           SKILL, said.append) == []
    assert said and "vault was locked" in said[0]


# ── npm: argv shape (node is replaced; nothing is really started) ────────────

def test_an_npm_cli_runs_as_trusted_node_plus_its_recorded_entry(world, monkeypatch):
    def fake_installer(argv, extra_env=None):
        prefix = argv[argv.index("--prefix") + 1]
        pkg = os.path.join(prefix, "node_modules", "@runcomfy", "cli")
        os.makedirs(os.path.join(pkg, "bin"))
        with open(os.path.join(pkg, "package.json"), "w", encoding="utf-8") as fh:
            json.dump({"name": "@runcomfy/cli", "version": "1.2.3",
                       "bin": {"runcomfy": "bin/cli.js"}}, fh)
        with open(os.path.join(pkg, "bin", "cli.js"), "w", encoding="utf-8") as fh:
            fh.write("console.log('hi')\n")
        return {"code": 0, "output": "ok"}
    monkeypatch.setattr(deps, "_run_installer", fake_installer)
    # node is not on this machine, so installer_argv() would be None; give a
    # stand-in argv that carries --prefix (the fake installer reads it).
    monkeypatch.setattr(deps, "installer_argv",
                        lambda step, version, target=None: ["npm", "--prefix", target])
    real = host_programs.path
    monkeypatch.setattr(host_programs, "path",
                        lambda name: "/trusted/node" if name == "node" else real(name))
    assert skill_programs.record_installed({"kind": "npm", "name": "@runcomfy/cli"},
                                           "1.2.3", SKILL) == ["runcomfy"]
    started: list = []

    def fake_run(argv, **kw):
        started.append({"argv": list(argv), "env": dict(kw["env"]), "shell": kw.get("shell")})
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")
    monkeypatch.setattr(brain_commands, "subprocess", types.SimpleNamespace(
        run=fake_run, TimeoutExpired=subprocess.TimeoutExpired, DEVNULL=subprocess.DEVNULL))
    got = _run("runcomfy run x")
    assert got["worked"]
    [one] = started
    root = skill_programs.iso_root(SKILL, "npm", "@runcomfy/cli")
    assert one["shell"] is False
    run_base = os.path.realpath(deps.tools_dir("keycli-run"))
    assert one["argv"][0] == "/trusted/node" and one["argv"][2:] == ["run", "x"]
    entry = one["argv"][1]
    assert os.path.realpath(entry).startswith(run_base)     # the verified copy
    assert entry.replace("\\", "/").endswith("node_modules/@runcomfy/cli/bin/cli.js")
    assert not os.path.exists(entry)                        # removed after the run
    assert one["env"][VAR] == TOKEN
    assert "SOME_OTHER_API_KEY" not in one["env"] and "NODE_OPTIONS" not in one["env"]
    assert "NODE_PATH" not in one["env"]
    # Tamper a file in the npm folder: no key.
    with open(os.path.join(root, "node_modules", "@runcomfy", "cli", "bin", "cli.js"),
              "w", encoding="utf-8") as fh:
        fh.write("console.log('evil')\n")
    with pytest.raises(ValueError, match="files changed since BuildBot installed it"):
        _run("runcomfy run x")


def test_the_installer_records_with_the_skill(world, monkeypatch):
    calls: list = []
    monkeypatch.setattr(skill_programs, "record_installed",
                        lambda step, version, skill="", say=None, **_k: calls.append(
                            (step["kind"], step["name"], version, skill)) or [])
    monkeypatch.setattr(deps, "_switch_off", lambda: False)
    monkeypatch.setattr(deps, "check_step", lambda step: ({"verdict": "allow"}, "1.0"))
    monkeypatch.setattr(deps, "installer_argv", lambda step, version, target=None: ["x"])
    monkeypatch.setattr(deps, "_run_installer", lambda argv, extra=None: {"code": 0})
    plan = {"id": "p1", "skill": SKILL, "steps": [{"kind": "pip", "name": "fakecomfy"}]}
    monkeypatch.setattr(deps, "get_plan", lambda pid: plan if pid == "p1" else None)
    monkeypatch.setitem(deps._PLANS, "p1", plan)  # noqa: SLF001
    out = deps.run_plan("p1", said_yes=True)
    assert out["ok"], out
    assert calls == [("pip", "fakecomfy", "1.0", SKILL)]


# ── Q3d: the run uses a verified copy, never the original folder ─────────────

def test_Q3d_the_program_runs_from_a_verified_copy(world, monkeypatch):
    seen: dict = {}
    real_run = subprocess.run

    def spy_run(argv, **kw):
        boot = argv[argv.index("-c") + 1]
        seen["argv"] = list(argv)
        seen["boot"] = boot
        # Change the ORIGINAL folder after the check: the run must not care.
        cli = os.path.join(world["root"], "fakecomfy", "cli.py")
        with open(cli, "a", encoding="utf-8") as fh:
            fh.write("\nprint('changed-after-check')\n")
        return real_run(argv, **kw)
    monkeypatch.setattr(brain_commands, "subprocess", types.SimpleNamespace(
        run=spy_run, TimeoutExpired=subprocess.TimeoutExpired, DEVNULL=subprocess.DEVNULL))
    got = _run()
    assert got["worked"] and f"hash={WANT_HASH}" in got["output"], got
    assert "changed-after-check" not in got["output"]       # the copy ran
    assert world["root"] not in seen["boot"]                 # never the original
    run_base = os.path.realpath(deps.tools_dir("keycli-run"))
    copies = [p for p in seen["boot"].split("'") if os.path.realpath(p).startswith(run_base)]
    assert copies and not os.path.exists(copies[0])          # removed after the run


# ── Q6: no site import, sys.path = stdlib + the run copy ─────────────────────

PATH_CLI = """import sys
def main():
    print("site=" + str("site" in sys.modules))
    print("sp=" + str(any("site-packages" in p.lower() for p in sys.path)))
    print("flags=" + str(sys.flags.no_site) + str(sys.flags.isolated))
    return 0
"""


def test_Q6_no_site_packages_or_pth_ever_load(world, monkeypatch):
    def fake_installer(argv, extra_env=None):
        _lay_pip(argv[argv.index("--target") + 1], cli=PATH_CLI)
        return {"code": 0, "output": "ok"}
    monkeypatch.setattr(deps, "_run_installer", fake_installer)
    assert skill_programs.record_installed({"kind": "pip", "name": "fakecomfy"}, "1.0",
                                           SKILL) == ["runcomfy"]
    got = _run()
    assert got["worked"], got
    assert "site=False" in got["output"] and "sp=False" in got["output"]
    assert "flags=11" in got["output"]


def test_Q6_a_cli_that_needs_site_fails_closed_with_a_plain_line(world, monkeypatch):
    def fake_installer(argv, extra_env=None):
        _lay_pip(argv[argv.index("--target") + 1], entry="notinstalled_pkg.cli:main")
        return {"code": 0, "output": "ok"}
    monkeypatch.setattr(deps, "_run_installer", fake_installer)
    assert skill_programs.record_installed({"kind": "pip", "name": "fakecomfy"}, "1.0",
                                           SKILL) == ["runcomfy"]
    got = _run()
    assert not got["worked"] and skill_programs.NEEDS_SITE_LINE in got["output"]
