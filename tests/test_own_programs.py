"""Her own programs: command line tools and desktop apps she adds herself.

Owner: "If I have Office on my computer, can I add those? If I have my own
programs, can I connect them?" Nothing here starts a real program: every
start is a mock.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import types

import pytest

from puffpai import programs

ROW_KEYS = {"id", "name", "kind", "path", "command", "added_at", "help", "commands",
            "always_allow"}
HELP = ("conv 1.0, turns files into other files\n"
        "usage: conv [-o OUT] FILE\n\n"
        "positional arguments:\n  FILE   the input file (.txt or .md)\n\n"
        "options:\n  -o OUT, --output OUT  where to write\n")
IS_WIN = os.name == "nt"


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    os.makedirs(tmp_path / "state", exist_ok=True)
    data = tmp_path / "data"
    (data / "My files").mkdir(parents=True)
    linked = tmp_path / "linked"
    linked.mkdir()
    from puffpai.knowledge import my_data
    monkeypatch.setattr(my_data, "data_dir", lambda: str(data))
    monkeypatch.setattr(my_data, "linked_folders",
                        lambda: [{"id": "lf000001", "path": str(linked)}])
    audit: list = []
    monkeypatch.setattr(programs, "_audit", lambda action, details, extra=None:
                        audit.append((action, details, extra or {})))
    return types.SimpleNamespace(root=tmp_path, data=data, linked=linked, audit=audit)


def _program(folder, name="conv") -> str:
    path = os.path.join(str(folder), name + (".exe" if IS_WIN else ""))
    with open(path, "wb") as fh:
        fh.write(b"MZ fake program")
    os.chmod(path, 0o755)
    return path


class _Runs:
    """subprocess.run stand-in: records argv and keyword arguments."""

    def __init__(self, out=HELP, code=0):
        self.calls: list = []
        self.out, self.code = out, code

    def __call__(self, argv, **kw):
        self.calls.append((list(argv), kw))
        return subprocess.CompletedProcess(argv, self.code, self.out, "")


@pytest.fixture
def runs(monkeypatch):
    r = _Runs()
    monkeypatch.setattr(programs.subprocess, "run", r)
    return r


# ── adding ─────────────────────────────────────────────────────────────────

def test_register_by_path_reads_its_help_once_and_keeps_the_pinned_row(state, runs, tmp_path):
    path = _program(tmp_path)
    row = programs.register("cli", path=path, name="My converter")
    assert set(row) == ROW_KEYS
    assert row["kind"] == "cli" and row["path"] == os.path.abspath(path)
    assert row["command"] == "conv" and row["name"] == "My converter"
    assert row["always_allow"] is False and row["help"].startswith("conv 1.0")
    assert [c["argv"] for c in row["commands"]] == [["conv", "{file}"]]
    assert row["commands"][0]["files"]["file"]["ext"] == [".txt", ".md"]
    argv, kw = runs.calls[0]
    assert argv == [os.path.abspath(path), "--help"] and len(runs.calls) == 1
    assert kw["shell"] is False and kw["timeout"] == programs.HELP_SECONDS
    assert kw["stdin"] is subprocess.DEVNULL
    if IS_WIN:
        assert kw["creationflags"] & 0x08000000            # no window
    with open(tmp_path / "state" / "programs.json", encoding="utf-8") as fh:
        assert json.load(fh)["programs"][0]["id"] == row["id"]
    assert programs.rows() == [row]


def test_help_falls_back_to_h_and_keeps_200_lines(state, monkeypatch, tmp_path):
    seen = []

    def fake(argv, **kw):
        seen.append(argv[-1])
        out = "" if argv[-1] == "--help" else "\n".join(f"line {i}" for i in range(500))
        return subprocess.CompletedProcess(argv, 1, out, "")
    monkeypatch.setattr(programs.subprocess, "run", fake)
    row = programs.register("cli", path=_program(tmp_path))
    assert seen == ["--help", "-h"]
    assert len(row["help"].splitlines()) == programs.HELP_LINES


def test_register_by_command_name_finds_it_on_path_never_in_a_relative_entry(
        state, runs, tmp_path, monkeypatch):
    real = tmp_path / "bin"
    real.mkdir()
    planted = tmp_path / "here"
    planted.mkdir()
    _program(planted, "ffmpeg")
    full = _program(real, "ffmpeg")
    monkeypatch.chdir(planted)
    monkeypatch.setenv("PATH", os.pathsep.join([".", "", str(real)]))
    row = programs.register("cli", command="ffmpeg")
    assert row["path"] == os.path.abspath(full) and row["command"] == "ffmpeg"


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "cli_help")


def _fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return fh.read()


def _sub_helps(monkeypatch, top: str, subs: dict):
    """subprocess.run stand-in for a program with subcommands: the top help
    for ``<prog> --help``, ``subs[name]`` (or a short usage) for a subcommand."""
    calls: list = []

    def fake(argv, **kw):
        calls.append((list(argv), kw))
        rest = argv[1:-1]
        out = top if not rest else subs.get(rest[0], f"usage: x {rest[0]} [options]\n")
        return subprocess.CompletedProcess(argv, 0, out, "")
    monkeypatch.setattr(programs.subprocess, "run", fake)
    return calls


def test_git_lists_its_subcommands_and_reads_each_with_h_not_a_web_page(
        state, tmp_path, monkeypatch):
    calls = _sub_helps(monkeypatch, _fixture("git_help.txt"),
                       {"clone": _fixture("git_clone_help.txt")})
    row = programs.register("cli", path=_program(tmp_path, "git"))
    names = [c["command"] for c in row["commands"]]
    assert names[:3] == ["git clone", "git init", "git add"] and "git push" in names
    clone = row["commands"][0]
    assert clone["argv"] == ["git", "clone", "{repo}", "{dir}"]
    assert clone["what"] == "Clone a repository into a new directory"
    assert clone["bin"] == os.path.abspath(tmp_path / os.path.basename(clone["bin"]))
    subs = [argv for argv, _kw in calls[1:]]
    assert subs and all(a[-1] == "-h" and len(a) == 3 for a in subs)   # never --help
    assert not any(a[1] in programs.NO_SUB_HELP for a in subs)      # push, reset, rm, help
    for _argv, kw in calls:
        assert kw["shell"] is False and kw["timeout"] == programs.HELP_SECONDS
        assert kw["stdin"] is subprocess.DEVNULL
        if IS_WIN:
            assert kw["creationflags"] & 0x08000000


@pytest.mark.parametrize("name", ["docker", "npm"])
def test_docker_and_npm_subcommands_are_capped(state, tmp_path, monkeypatch, name):
    calls = _sub_helps(monkeypatch, _fixture(f"{name}_help.txt"), {})
    row = programs.register("cli", path=_program(tmp_path, name))
    names = [c["command"] for c in row["commands"]]
    assert len(names) == programs.COMMANDS_MAX == 60
    want = {"docker": ["docker run", "docker ps", "docker compose", "docker attach"],
            "npm": ["npm access", "npm ci", "npm help-search", "npm install"]}[name]
    assert all(w in names for w in want)
    subs = [argv for argv, _kw in calls[1:]]
    assert 0 < len(subs) <= programs.SUB_HELP_MAX
    assert all(a[-1] == "--help" for a in subs)
    assert not any(a[1] in programs.NO_SUB_HELP for a in subs)   # never docker run --help


def test_subcommand_reads_stop_at_the_time_budget(state, tmp_path, monkeypatch):
    calls = _sub_helps(monkeypatch, _fixture("docker_help.txt"), {})
    clock = iter(range(0, 10_000, 40))
    monkeypatch.setattr(programs.time, "monotonic", lambda: next(clock))
    row = programs.register("cli", path=_program(tmp_path, "docker"))
    assert len(row["commands"]) == 60 and len(calls) - 1 <= 2


def test_only_a_missing_program_is_refused(state, runs, tmp_path, monkeypatch):
    with pytest.raises(programs.ProgramError, match="no program at that place"):
        programs.register("cli", path=str(tmp_path / "gone.exe"))
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(programs.ProgramError, match="could not find a program"):
        programs.register("cli", command="nothere")
    with pytest.raises(programs.ProgramError):
        programs.register("cli", path="relative/conv.exe")


# ── host_programs: a separate list, by full path ───────────────────────────

def test_host_programs_knows_her_programs_by_full_path_only(state, runs, tmp_path):
    from puffpai.security import host_programs
    path = _program(tmp_path)
    row = programs.register("cli", path=path)
    assert host_programs.registered(row["id"]) == os.path.abspath(path)
    assert host_programs.registered(os.path.abspath(path)) == os.path.abspath(path)
    assert host_programs.registered("conv") is None              # never by a bare name
    assert "conv" not in host_programs.NAMES
    with pytest.raises(ValueError):
        host_programs.path("conv")                               # NAMES stays closed
    os.remove(path)
    assert host_programs.registered(row["id"]) is None           # gone: not started


# ── the brain: listed, asked, Always allow ─────────────────────────────────

def test_run_command_lists_her_program_with_its_commands(state, runs, tmp_path):
    from puffpai import brain_commands, installed
    programs.register("cli", path=_program(tmp_path), name="My converter")
    assert "conv" in installed.cli_commands()
    text = brain_commands.description()
    assert "conv ... (your own program My converter; for example conv {file})" in text


def _ctx(**kw):
    return {"caller": "owner", "ask": lambda q: True, **kw}


def test_without_always_allow_every_command_asks(state, runs, tmp_path):
    from puffpai import brain_commands
    programs.register("cli", path=_program(tmp_path))
    q = brain_commands.question({"command": "conv notes.txt"}, _ctx())
    assert q and q["title"] == "May I run this command?" and "your own program" in q["detail"]
    with pytest.raises(ValueError, match="not approved"):
        brain_commands.run({"command": "conv notes.txt"}, _ctx())


def test_always_allow_runs_a_plain_call_as_argv_no_shell_and_logs_it(state, runs, tmp_path):
    from puffpai import brain_commands
    path = os.path.abspath(_program(tmp_path))
    row = programs.register("cli", path=path)
    out = programs.set_always_allow(row["id"], True)
    assert out["program"]["always_allow"] is True and "warning" not in out
    runs.calls.clear()
    args = {"command": 'conv "my notes.txt" -o out.md'}
    assert brain_commands.question(args, _ctx()) is None          # not asked
    got = brain_commands.run(args, _ctx())                         # no said_yes
    assert got["worked"] is True
    argv, kw = runs.calls[-1]
    assert argv == [path, "my notes.txt", "-o", "out.md"]
    assert kw["shell"] is False
    assert [a for a in state.audit if a[0] == "own_program_run"][-1][2]["how"] == \
        "always_allow"


@pytest.mark.parametrize("command", [
    "conv a.txt && del x", "conv a.txt; rm -rf ~", "conv a.txt | sh", "conv $(whoami)",
    "conv `id`", "conv a.txt > out", "conv a.txt\nrm x"])
def test_always_allow_never_covers_shell_features(state, runs, tmp_path, command):
    from puffpai import brain_commands
    row = programs.register("cli", path=_program(tmp_path))
    programs.set_always_allow(row["id"], True)
    assert programs.always_allowed(command) is None
    try:
        q = brain_commands.question({"command": command}, _ctx())
    except ValueError:
        q = "refused"
    assert q is not None                                          # asked (or refused)
    with pytest.raises(ValueError):
        brain_commands.run({"command": command}, _ctx())          # never without a yes


def test_always_allow_on_a_shell_says_so_plainly(state, runs, tmp_path):
    path = _program(tmp_path, "powershell")
    row = programs.register("cli", path=path)
    out = programs.set_always_allow(row["id"], True)
    assert out["warning"] == programs.ALWAYS_ALLOW_WARNING


@pytest.mark.parametrize("name", ["cmd", "powershell", "pwsh", "bash", "sh", "python",
                                  "python3.12", "node"])
def test_a_shell_or_interpreter_never_runs_for_its_help(state, runs, tmp_path, name):
    row = programs.register("cli", path=_program(tmp_path, name))
    assert runs.calls == []                                       # no banner, no prompt
    assert row["help"] == programs.SHELL_NOTE and row["commands"] == []
    out = programs.set_always_allow(row["id"], True)
    if name != "python3.12":
        assert out["warning"] == programs.ALWAYS_ALLOW_WARNING   # kept as is


def test_a_command_never_names_the_programs_list(state):
    from puffpai import brain_commands
    with pytest.raises(ValueError):
        brain_commands.check_command("echo x > ../programs.json")


# ── desktop programs and files ─────────────────────────────────────────────

@pytest.fixture
def starts(monkeypatch):
    got: list = []
    monkeypatch.setattr(programs, "_start_window", lambda target, file="": got.append(
        (target, file)))
    return got


def _desktop(tmp_path) -> dict:
    path = _program(tmp_path, "WINWORD")
    if not IS_WIN:
        os.chmod(path, 0o755)
    return programs.register("desktop", path=path, name="Word")


def test_launch_starts_her_desktop_program_with_a_file_from_her_folders(state, starts,
                                                                     tmp_path):
    row = _desktop(tmp_path)
    assert row["command"] == "" and row["kind"] == "desktop"
    doc = state.data / "My files" / "letter.docx"
    doc.write_bytes(b"PK")
    assert programs.launch(row["id"], str(doc)) == {"ok": True}
    assert programs.launch(row["id"], "letter.docx") == {"ok": True}  # a My files name
    assert starts == [(row["path"], os.path.abspath(str(doc)))] * 2
    with pytest.raises(programs.ProgramError, match="only open files"):
        programs.launch(row["id"], str(tmp_path / "elsewhere.docx"))


def _client(monkeypatch, paired=True):
    from flask import Flask
    from puffpai import programs_api
    monkeypatch.setattr(programs_api, "_device", lambda: "device:abc" if paired else "")
    app = Flask(__name__)
    app.register_blueprint(programs_api.bp)
    return app.test_client()


def test_files_open_opens_only_files_in_her_folders(state, starts, monkeypatch, tmp_path):
    c = _client(monkeypatch)
    doc = state.data / "My files" / "plan.docx"
    doc.write_bytes(b"PK")
    sheet = state.linked / "costs.xlsx"
    sheet.write_bytes(b"PK")
    assert c.post("/files/open", json={"path": str(doc)}).get_json() == {"ok": True}
    assert c.post("/files/open", json={"path": str(sheet)}).get_json() == {"ok": True}
    assert starts == [("", os.path.abspath(str(doc))), ("", os.path.abspath(str(sheet)))]
    outside = tmp_path / "secret.docx"
    outside.write_bytes(b"PK")
    for bad in (str(outside), str(state.data / ".." / "secret.docx"),
                str(tmp_path / "state" / "programs.json"), "", "../../secret.docx"):
        got = c.post("/files/open", json={"path": bad})
        assert got.status_code == 400 and got.get_json()["message"] == programs.FILE_OUTSIDE
    runner = state.data / "My files" / "setup.exe"
    runner.write_bytes(b"MZ")
    got = c.post("/files/open", json={"path": str(runner)})
    assert got.status_code == 400 and got.get_json()["message"] == programs.FILE_RUNS
    assert len(starts) == 2


def test_routes_need_a_paired_device_and_keep_the_contract(state, runs, starts, monkeypatch,
                                                           tmp_path):
    assert _client(monkeypatch, paired=False).get("/programs").status_code == 403
    c = _client(monkeypatch)
    monkeypatch.setattr(programs, "installed_desktop", lambda: [{"name": "Word",
                                                                 "path": "C:/W.EXE"}])
    got = c.post("/programs", json={"kind": "cli", "path": _program(tmp_path)}).get_json()
    pid = got["program"]["id"]
    assert set(got["program"]) == ROW_KEYS
    listed = c.get("/programs").get_json()
    assert [r["id"] for r in listed["programs"]] == [pid]
    assert listed["installed_desktop"] == [{"name": "Word", "path": "C:/W.EXE"}]
    assert c.patch(f"/programs/{pid}", json={"always_allow": True}).get_json()[
        "program"]["always_allow"] is True
    assert c.patch(f"/programs/{pid}", json={"always_allow": "yes"}).status_code == 400
    desk = c.post("/programs", json={"kind": "desktop", "path": _program(tmp_path, "paint"),
                                     "name": "Paint"}).get_json()["program"]
    assert c.post(f"/programs/{desk['id']}/launch", json={}).get_json() == {"ok": True}
    assert starts == [(desk["path"], "")]
    assert c.delete(f"/programs/{pid}").get_json() == {"ok": True}
    assert c.delete(f"/programs/{pid}").status_code == 404
    assert c.post("/programs", json={"kind": "cli", "path": "C:/nope/x.exe"}).status_code == 400


def test_open_in_program_asks_the_first_time_per_program(state, starts, tmp_path):
    from puffpai import brain_tools
    row = _desktop(tmp_path)
    doc = state.data / "My files" / "a.docx"
    doc.write_bytes(b"PK")
    programs.register_tool()
    asked: list = []
    ctx = {"caller": "owner", "ask": lambda q: asked.append(q) or True}
    first = brain_tools.call("open_in_program", {"program": "Word", "file": "a.docx"}, ctx)
    second = brain_tools.call("open_in_program", {"program": "Word", "file": "a.docx"}, ctx)
    assert first["ok"] and second["ok"] and len(asked) == 1
    assert "Word" in asked[0]["title"]
    assert starts == [(row["path"], os.path.abspath(str(doc)))] * 2
    outside = brain_tools.call("open_in_program", {"file": str(tmp_path / "x.docx")}, ctx)
    assert not outside["ok"] and len(starts) == 2


# ── installed desktop programs to pick from ────────────────────────────────

def _lnk(target: str) -> bytes:
    """A minimal Shell Link with a LinkInfo local base path (MS-SHLLINK)."""
    head = bytearray(0x4C)
    struct.pack_into("<I", head, 0, 0x4C)
    struct.pack_into("<I", head, 0x14, 0x2)                       # HasLinkInfo
    volume = struct.pack("<IIII", 0x11, 3, 0, 0x10) + b"\0"
    base = target.encode("latin-1") + b"\0"
    info_head = 0x1C
    vol_off = info_head
    base_off = vol_off + len(volume)
    suffix_off = base_off + len(base)
    size = suffix_off + 1
    info = struct.pack("<IIIIIII", size, info_head, 1, vol_off, base_off, 0, suffix_off)
    return bytes(head) + info + volume + base + b"\0"


class _FakeReg:
    HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER, REG_SZ, REG_EXPAND_SZ = 1, 2, 1, 2

    def __init__(self, entries):
        self.entries = entries                                    # {exe: path}

    def OpenKey(self, parent, sub):
        if parent == self.HKEY_CURRENT_USER:
            raise OSError("none")                   # nothing per user here
        return _Key(sub.rsplit("\\", 1)[-1])

    def EnumKey(self, key, i):
        names = list(self.entries)
        if i >= len(names):
            raise OSError("done")
        return names[i]

    def QueryValueEx(self, key, name):
        return self.entries[key.sub], self.REG_SZ

    def CloseKey(self, key):
        pass


class _Key:
    def __init__(self, sub):
        self.sub = sub

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_installed_desktop_reads_start_menu_shortcuts_and_app_paths(state, tmp_path,
                                                                    monkeypatch):
    office = tmp_path / "Office16"
    office.mkdir()
    word, excel = office / "WINWORD.EXE", office / "EXCEL.EXE"
    word.write_bytes(b"MZ")
    excel.write_bytes(b"MZ")
    menu = tmp_path / "menu"
    (menu / "Microsoft Office").mkdir(parents=True)
    (menu / "Microsoft Office" / "Word.lnk").write_bytes(_lnk(str(word)))
    (menu / "Uninstall Thing.lnk").write_bytes(_lnk(str(word)))
    (menu / "Manual.lnk").write_bytes(_lnk(str(tmp_path / "manual.pdf")))
    assert programs._lnk_target(str(menu / "Microsoft Office" / "Word.lnk")) == str(word)
    monkeypatch.setattr(programs, "_start_menu_dirs", lambda: [str(menu)])
    reg = _FakeReg({"WINWORD.EXE": str(word), "excel.exe": f'"{excel}"',
                    "notes.txt": str(word), "gone.exe": str(tmp_path / "gone.exe")})
    got = programs.installed_desktop(finders=[programs._start_menu,
                                              lambda: programs._app_paths(reg)])
    assert got == [{"name": "excel", "path": str(excel)}, {"name": "Word", "path": str(word)}]


def test_linux_desktop_files_and_their_exec(state, tmp_path, monkeypatch):
    apps = tmp_path / "applications"
    apps.mkdir()
    (apps / "writer.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Writer\nExec=writer --doc %U\n",
        encoding="utf-8")
    (apps / "hidden.desktop").write_text(
        "[Desktop Entry]\nName=Hidden\nExec=x\nNoDisplay=true\n", encoding="utf-8")
    monkeypatch.setattr(programs, "_linux_dirs", lambda: [str(apps)])
    assert programs.installed_desktop(finders=[programs._linux]) == [
        {"name": "Writer", "path": str(apps / "writer.desktop")}]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = _program(bin_dir, "writer")
    monkeypatch.setenv("PATH", str(bin_dir))
    words = programs.desktop_exec(str(apps / "writer.desktop"))
    assert words == [os.path.abspath(exe), "--doc", "%U"]
    assert programs._fill(words, "/d/a.odt") == [os.path.abspath(exe), "--doc", "/d/a.odt"]


# ── Toolsmith: a desktop program counts as working ─────────────────────────

def test_toolsmith_done_accepts_an_installed_desktop_program(state, tmp_path):
    from puffpai.build import toolsmith_run as T
    run = T.Run.__new__(T.Run)
    run.app = {"name": "Paint Thing", "ref": "github:o/paint"}
    run.folder = str(tmp_path / "app")
    os.makedirs(run.folder)
    run.job = {"id": "j1"}
    run.result = None
    run.say = lambda text: None
    exe = _program(run.folder, "paint")
    got = run.done({"program": exe})
    assert got["ok"] and run.result["desktop"] is True and run.result["url"] == ""
    rows = programs.rows()
    assert [(r["kind"], r["name"], r["path"]) for r in rows] == [
        ("desktop", "Paint Thing", os.path.abspath(exe))]
    stray = _program(tmp_path, "stray")
    assert not run.done({"program": stray})["ok"]
    assert not run.done({"program": str(tmp_path / "app" / "none.exe")})["ok"]
    assert not run.done({})["ok"]
