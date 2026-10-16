"""Removing BuildBot.

* Remove BuildBot takes this install's own apps (the app folders named by ids
  it issued) and its own projects in a locked box that stays; the whole apps
  folder only when no other BuildBot is known; the saved entries (the
  vault's among them) only after the vault folder.
* The locked box's own folder check reads one deny list per BuildBot, kept in
  the box, that holds its vault and state folders (security finding 3).
* A box web build whose parts come from a host the box does not reach stops
  at once with a plain reason (e2e C, Jsonmatic).
* The hidden browser's stopped background calls are one line on "What
  leaves your computer" (wedding).

No network: the box is a stand-in wsl.exe, the log a scratch file.
"""

from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

import pytest

from puffpai import remove_cli
from puffpai.box import driver
from puffpai.box import wording as W
from puffpai.security import owner_folders

from test_remove_buildbot import (_args, _base, _real_install, _run_ps, _write, inst,  # noqa: F401
                            needs_windows)

OWN_A = "to-do-list-app-exjfy67k"
OWN_B = "jsonmatic-h427hlnh"
THEIRS = "csvkit-gbdu5o3l"


def _issue(state: Path, *ids: str) -> None:
    path = state / "webapps" / "issued_ids.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for i in ids:
            fh.write(i + "\n")


def _another_install(inst_, distro: str = "PuffPAI-sandbox") -> Path:
    other = inst_["base"] / "another-install"
    other.mkdir(exist_ok=True)
    folder = inst_["box"] / "claims" / distro
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ("9" * 16 + ".json")).write_text(json.dumps(
        {"install_root": str(other), "state_dir": "", "created": ""}), encoding="utf-8")
    return other


# ── which ids are this install's ─────────────────────────────────────────────

def test_own_ids_come_from_this_installs_records_only(inst):
    state = inst["state"]
    _issue(state, OWN_A, "not an id", "../escape-aaaaaaaa", "UPPER-aaaaaaaa")
    (state / "integrations" / OWN_B).mkdir(parents=True)
    (state / "apps" / "seating").mkdir(parents=True)          # not an id shape
    assert remove_cli.own_ids(str(state)) == {OWN_A, OWN_B}
    assert remove_cli.own_ids(str(inst["base"] / "missing")) == set()


def test_the_id_shape_is_the_one_buildbot_issues():
    from puffpai import webapps
    from puffpai.integrate import records
    assert remove_cli.ID_RE.pattern.rstrip("Z\\$") == records.ID_RE.pattern.rstrip("Z\\$")
    assert remove_cli.ID_RE.pattern.rstrip("Z\\$") == webapps.ID_RE.pattern.rstrip("Z\\$")


# ── the apps folder ──────────────────────────────────────────────────────────

def test_with_another_buildbot_only_this_installs_apps_go(inst):
    apps, state = inst["apps"], inst["state"]
    _issue(state, OWN_A, OWN_B)
    _write(apps / OWN_A / "web" / "index.html", "<html>")
    _write(apps / OWN_B / "src" / "package.json", "{}")
    _write(apps / THEIRS / "web" / "index.html", "theirs")
    _another_install(inst)
    plan = remove_cli.build_plan()
    assert not plan.refused and not plan.apps_delete
    own = sorted(p for kind, p, _n in plan.roots if kind == "app")
    assert own == sorted([str(apps / OWN_A), str(apps / OWN_B)])
    first, rest = remove_cli.plan_lines(plan)
    assert any(line.startswith("  the 2 apps this BuildBot added, in ") for line in first)
    assert W.REMOVE_OTHER_APPS_STAY.format(path=apps) in rest
    assert remove_cli.facts(plan)["apps_own"] == own
    assert remove_cli.do_remove(plan, keep_vault=False, sleep=lambda s: None) == 0
    assert not (apps / OWN_A).exists() and not (apps / OWN_B).exists()
    assert (apps / THEIRS / "web" / "index.html").read_text(encoding="utf-8") == "theirs"
    assert (apps / "seating" / "index.html").exists()
    assert not inst["install"].exists() and not state.exists()


def test_an_apps_folder_left_empty_goes_too(inst):
    apps, state = inst["apps"], inst["state"]
    import shutil
    for child in apps.iterdir():
        shutil.rmtree(child)
    _issue(state, OWN_A)
    _write(apps / OWN_A / "web" / "index.html", "<html>")
    _another_install(inst)
    plan = remove_cli.build_plan()
    assert not plan.apps_delete and [k for k, _p, _n in plan.roots].count("app") == 1
    assert remove_cli.do_remove(plan, keep_vault=False, sleep=lambda s: None) == 0
    assert not apps.exists()


def test_a_custom_apps_folder_holding_only_this_installs_apps_goes_whole(inst, monkeypatch):
    custom = inst["base"] / "elsewhere" / "apps"
    _issue(inst["state"], OWN_A, OWN_B)
    _write(custom / OWN_A / "web" / "index.html", "<html>")
    _write(custom / OWN_B / "x.txt", "x")
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", str(custom))
    plan = remove_cli.build_plan()
    assert plan.apps_delete and ("apps", str(custom)) in [(k, p) for k, p, _ in plan.roots]
    assert remove_cli.do_remove(plan, keep_vault=False, sleep=lambda s: None) == 0
    assert not custom.exists() and (inst["base"] / "elsewhere").exists()


def test_an_apps_setting_in_her_folder_takes_only_buildbots_apps(inst, monkeypatch, capsys):
    holiday = inst["profile"] / "Pictures" / "holiday"
    _write(holiday / "beach.jpg", "her photo")
    _write(holiday / OWN_A / "web" / "index.html", "<html>")
    _issue(inst["state"], OWN_A)
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", str(holiday))
    plan = remove_cli.build_plan()
    assert not plan.apps_delete and not plan.refused
    assert remove_cli.cmd_remove(_args(yes=True)) == 0
    assert (holiday / "beach.jpg").read_text(encoding="utf-8") == "her photo"
    assert not (holiday / OWN_A).exists()
    assert (inst["profile"] / "Pictures" / "photo.jpg").exists()


@needs_windows
def test_an_own_app_folder_that_is_a_link_is_never_taken(inst, tmp_path):
    import _winapi
    target = tmp_path / "hers"
    _write(target / "keep.txt", "keep")
    _issue(inst["state"], OWN_A)
    _another_install(inst)
    _winapi.CreateJunction(str(target), str(inst["apps"] / OWN_A))
    plan = remove_cli.build_plan()
    assert "app" not in [k for k, _p, _n in plan.roots]
    assert remove_cli.do_remove(plan, keep_vault=False, sleep=lambda s: None) == 0
    assert (target / "keep.txt").read_text(encoding="utf-8") == "keep"


# ── the locked box ───────────────────────────────────────────────────────────

class _BoxProjects:
    """Answers the two box commands Remove uses, on top of the stand-in wsl."""

    def __init__(self, monkeypatch, fake, names):
        self.fake = fake
        self.names = set(names)
        self.removed: list[str] = []
        self.dropped: list[str] = []
        self.inner = fake.spawn
        monkeypatch.setattr(driver, "_spawn", self.spawn)

    def spawn(self, argv, *, timeout, input_bytes=None, **kwargs):
        import subprocess
        args = [str(a) for a in argv[1:]]
        if len(args) >= 6 and args[0] == "-d" and args[2:5] == ["-u", "root", "--exec"]:
            rest = args[5:]
            if rest[:2] == ["/bin/sh", "-c"] and rest[2].startswith("ls -1 "):
                self.fake.calls.append([str(a) for a in argv])
                out = "".join(f"{n}\n" for n in sorted(self.names))
                out += "".join(f"{n}.img\n" for n in sorted(self.names))
                return subprocess.CompletedProcess(argv, 0, out.encode(), b"")
            if rest[:2] == ["/bin/rm", "-f"]:
                self.fake.calls.append([str(a) for a in argv])
                self.dropped.append(rest[2])
                return subprocess.CompletedProcess(argv, 0, b"", b"")
            if rest[:2] == [driver.LAUNCHER, "--remove-project"]:
                self.fake.calls.append([str(a) for a in argv])
                self.removed.append(rest[2])
                self.names.discard(rest[2])
                return subprocess.CompletedProcess(argv, 0, b'{"ok": true}\n', b"")
        return self.inner(argv, timeout=timeout, input_bytes=input_bytes, **kwargs)


@pytest.mark.skipif(os.name != "nt", reason="the WSL box is Windows only")
def test_this_installs_projects_leave_a_shared_box_and_others_stay(inst, monkeypatch):
    _issue(inst["state"], OWN_A, OWN_B)
    _another_install(inst)
    box = _BoxProjects(monkeypatch, inst["fake"], [OWN_A, OWN_B, THEIRS])
    plan = remove_cli.build_plan()
    assert plan.box and not plan.box["last"]
    assert plan.box_projects == sorted([OWN_A, OWN_B])
    first, _rest = remove_cli.plan_lines(plan)
    assert W.REMOVE_BOX_PROJECTS.format(n=2) in first
    assert remove_cli.facts(plan)["box_projects"] == 2
    assert remove_cli.do_remove(plan, keep_vault=False, sleep=lambda s: None) == 0
    assert sorted(box.removed) == sorted([OWN_A, OWN_B])
    assert box.names == {THEIRS}
    # Its own list of private folders leaves the box too (the box stays).
    assert box.dropped == [f"{driver.DENY_DIR}/{driver.install_id()}.json"]
    assert not [c for c in inst["fake"].calls if "--unregister" in c]


def test_a_box_that_is_not_this_installs_is_never_looked_into(inst, monkeypatch):
    _issue(inst["state"], OWN_A)
    driver.drop_claim("PuffPAI-sandbox")
    _another_install(inst)
    box = _BoxProjects(monkeypatch, inst["fake"], [OWN_A])
    plan = remove_cli.build_plan()
    assert plan.box_projects == [] and box.removed == []
    assert not [c for c in inst["fake"].calls if any("ls -1" in a for a in c)]


def test_the_only_box_step_removes_projects_and_says_how_many_are_left(inst, monkeypatch,
                                                                        capsys):
    _issue(inst["state"], OWN_A)
    _another_install(inst)
    box = _BoxProjects(monkeypatch, inst["fake"], [OWN_A, THEIRS])
    assert remove_cli.cmd_remove(_args(yes=True, only="box")) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert json.loads(out[-1]) == {"box_projects_left": 0}
    assert box.removed == [OWN_A] and inst["install"].exists()


# ── the vault's saved entry goes after the vault ─────────────────────────────

def test_saved_entries_go_after_the_vault_folder_and_before_the_signs(inst, monkeypatch):
    seen = {}
    monkeypatch.setattr(remove_cli, "keyring_present",
                        lambda state, vd: [("puffpai-buildbot", "x"), ("puffpai", "y")])

    def record(entries):
        seen["entries"] = list(entries)
        seen["vault_there"] = inst["vault"].exists()
        seen["state_sign_there"] = (inst["state"] / "buildbot.env").exists()
        seen["install_sign_there"] = (inst["install"] / ".puffpai-install.json").exists()
        return len(entries)

    monkeypatch.setattr(remove_cli, "keyring_delete", record)
    plan = remove_cli.build_plan()
    assert plan.vault_goes
    kinds = [k for k, _p, _keep in remove_cli._phases(plan)]   # noqa: SLF001
    assert kinds.index("keyring") > kinds.index("vault")
    assert remove_cli.do_remove(plan, keep_vault=False, sleep=lambda s: None) == 0
    assert seen == {"entries": [("puffpai-buildbot", "x"), ("puffpai", "y")],
                    "vault_there": False, "state_sign_there": True,
                    "install_sign_there": True}


def test_a_delete_that_stops_before_the_vault_keeps_its_saved_entry(inst, monkeypatch):
    called = []
    monkeypatch.setattr(remove_cli, "keyring_present",
                        lambda state, vd: [("puffpai-buildbot", "x")])
    monkeypatch.setattr(remove_cli, "keyring_delete", lambda e: called.append(e))
    real = remove_cli.delete_tree

    def stop_at_state(path, **kw):
        if os.path.normcase(path) == os.path.normcase(str(inst["state"])):
            raise remove_cli.StillInUse(str(inst["state"] / "logs" / "bot.log"))
        return real(path, **kw)

    monkeypatch.setattr(remove_cli, "delete_tree", stop_at_state)
    plan = remove_cli.build_plan()
    assert remove_cli.do_remove(plan, keep_vault=False, sleep=lambda s: None) == 1
    assert called == [] and (inst["vault"] / "secret.txt").exists()


def test_the_generated_script_deletes_saved_entries_after_the_vault():
    text = (Path(__file__).resolve().parents[1] / "install.ps1").read_text(encoding="utf-8")
    body = text[text.index("$removeTemplate = @'"):]
    body = body[:body.index("\n'@")]
    assert '--only "box,keyring"' not in body
    assert body.index('Kind = "vault"') < body.index('Kind = "keyring"') \
        < body.index('[void]$Phases.Add([pscustomobject]@{ Kind = "state"; Path = $S; Skip = @() })')
    # Every folder is checked to still be a plain folder right before it is listed.
    tree = body[body.index("function Remove-Tree"):body.index("# The order leaves")]
    assert tree.count("Assert-Plain $dir") == 2


@needs_windows
def test_the_generated_script_takes_only_this_installs_apps(tmp_path, request):
    t = _real_install(_base(tmp_path, request))
    base = t["repo"].parent
    apps = base / "apps-outside-state"
    _write(apps / OWN_A / "web" / "index.html", "<html>")
    _write(apps / THEIRS / "web" / "index.html", "theirs")
    _issue(t["state"], OWN_A)
    other = base / "another-install"
    other.mkdir()
    claims = t["box"] / "claims" / "PuffPAI-sandbox-fxtest00"
    claims.mkdir(parents=True)
    (claims / ("9" * 16 + ".json")).write_text(json.dumps(
        {"install_root": str(other), "state_dir": "", "created": ""}), encoding="utf-8")
    env_file = base / "buildbot.env"
    env_file.write_text(env_file.read_text(encoding="utf-8").replace(
        f"PUFFPAI_APPS_ROOT={t['apps']}", f"PUFFPAI_APPS_ROOT={apps}"), encoding="utf-8")
    proc = _run_ps(t["script"], "-InstallRoot", str(t["repo"]), "-Yes",
                   env_extra={"PUFFPAI_BOX_ROOT": str(t["box"])})
    out = proc.stdout
    assert proc.returncode == 0, out + proc.stderr
    assert f"  the 1 app this BuildBot added, in {apps} (" in out
    assert W.REMOVE_OTHER_APPS_STAY.format(path=apps) in out
    assert not (apps / OWN_A).exists()
    assert (apps / THEIRS / "web" / "index.html").read_text(encoding="utf-8") == "theirs"
    assert not t["repo"].exists() and not t["state"].exists()


@needs_windows
def test_the_generated_script_removes_an_apps_folder_of_only_this_installs_apps(tmp_path,
                                                                               request):
    t = _real_install(_base(tmp_path, request))
    base = t["repo"].parent
    apps = base / "apps-outside-state"
    _write(apps / OWN_A / "web" / "index.html", "<html>")
    _issue(t["state"], OWN_A)
    env_file = base / "buildbot.env"
    env_file.write_text(env_file.read_text(encoding="utf-8").replace(
        f"PUFFPAI_APPS_ROOT={t['apps']}", f"PUFFPAI_APPS_ROOT={apps}"), encoding="utf-8")
    proc = _run_ps(t["script"], "-InstallRoot", str(t["repo"]), "-Yes",
                   env_extra={"PUFFPAI_BOX_ROOT": str(t["box"])})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"  the apps BuildBot added: {apps} (" in proc.stdout
    assert not apps.exists() and not t["repo"].exists() and not t["state"].exists()


# ── the box's own deny list ──────────────────────────────────────────────────

@needs_windows
def test_the_box_is_told_this_installs_private_folders(inst, monkeypatch):
    got = {}
    inner = inst["fake"].spawn

    def spawn(argv, *, timeout, input_bytes=None, **kw):
        args = [str(a) for a in argv[1:]]
        if args[5:7] == ["/bin/sh", "-c"] and "deny.d" in args[7]:
            got["script"], got["body"] = args[7], input_bytes
            import subprocess
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        return inner(argv, timeout=timeout, input_bytes=input_bytes, **kw)

    monkeypatch.setattr(driver, "_spawn", spawn)
    assert driver.sync_deny_list("PuffPAI-sandbox")
    listed = [os.path.normcase(p) for p in json.loads(got["body"].decode("utf-8"))]
    vault = os.path.normcase(os.path.realpath(owner_folders.vault_dir()))
    state = os.path.normcase(os.path.realpath(str(inst["state"])))
    assert vault in listed and state in listed
    assert any(p.endswith(os.path.normcase("\\windows")) for p in listed)
    assert f"{driver.DENY_DIR}/{driver.install_id()}.json" in got["script"]
    # Not this install's box: nothing is written.
    driver.drop_claim("PuffPAI-sandbox")
    got.clear()
    assert not driver.sync_deny_list("PuffPAI-sandbox") and not got


def test_a_folder_run_is_refused_when_the_box_cannot_be_told(monkeypatch):
    req = driver.BoxRequest(run_id="r1234", project="seating-planner", argv=["ls"],
                            allow_hosts=[], what="box_traffic",
                            folder={"windows_path": "C:\\Users\\a\\Pictures\\x", "mode": "ro",
                                    "name": "files"},
                            limits={}, stdin="none", stdout_mode="capture", env={})
    monkeypatch.setattr(driver, "_platform", lambda: "windows")
    monkeypatch.setattr(driver, "validate", lambda r: r)
    monkeypatch.setattr(driver, "_runtime_hosts_allowed", lambda r: True)
    monkeypatch.setattr(driver, "_check_folder", lambda r: "C:\\Users\\a\\Pictures\\x")
    monkeypatch.setattr(driver, "distro_name", lambda: "PuffPAI-sandbox")
    monkeypatch.setattr(driver, "_box_ready_for_run", lambda d: None)
    monkeypatch.setattr(driver, "sync_deny_list", lambda d: False)

    def never(*a, **k):
        raise AssertionError("the launcher must not start")

    monkeypatch.setattr(driver, "_run_wsl", never)
    res = driver.run(req)
    assert not res.ok and res.refused == "deny_list" and res.error == W.DENY_LIST_NOT_SET


def _launcher():
    from test_box_request_validation import _launcher as load
    return load()


def test_the_launcher_reads_every_buildbots_list_and_fails_closed(tmp_path, monkeypatch):
    box_run = _launcher()
    fixed = tmp_path / "deny_windows.json"
    fixed.write_text(json.dumps(["C:\\Windows"]), encoding="utf-8")
    folder = tmp_path / "deny.d"
    folder.mkdir()
    (folder / "aaaaaaaaaaaaaaaa.json").write_text(json.dumps(
        ["C:\\Users\\a\\BuildBot\\vault_data", "C:\\Users\\a\\AppData\\Local\\PuffPAI"]),
        encoding="utf-8")
    monkeypatch.setattr(box_run, "DENY_FILE", str(fixed))
    monkeypatch.setattr(box_run, "DENY_DIR", str(folder))
    monkeypatch.setattr(box_run, "_root_only", lambda p: None)   # Windows has no root owner
    box_run.check_folder("C:\\Users\\a\\Pictures\\Wedding")
    for bad in ("C:\\Users\\a\\BuildBot\\vault_data\\x", "C:\\Users\\a\\AppData\\Local\\PuffPAI",
                "C:\\Users\\a\\BuildBot", "C:\\Windows\\Temp"):
        with pytest.raises(box_run.Refused):
            box_run.check_folder(bad)
    (folder / "bbbbbbbbbbbbbbbb.json").write_text("{not a list", encoding="utf-8")
    with pytest.raises(box_run.Refused):
        box_run.check_folder("C:\\Users\\a\\Pictures\\Wedding")      # fail closed
    (folder / "bbbbbbbbbbbbbbbb.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
    with pytest.raises(box_run.Refused):
        box_run.check_folder("C:\\Users\\a\\Pictures\\Wedding")


def test_a_list_another_user_could_change_proves_nothing(monkeypatch):
    box_run = _launcher()
    stat_of = {"uid": 0, "mode": 0o100644}

    def lstat(path):
        return types.SimpleNamespace(st_uid=stat_of["uid"], st_mode=stat_of["mode"])

    fake_os = types.SimpleNamespace(lstat=lstat,
                                    path=types.SimpleNamespace(islink=lambda p: False))
    monkeypatch.setattr(box_run, "os", fake_os)
    box_run._root_only("/etc/puffpai/deny.d/x.json")               # noqa: SLF001
    for uid, mode in ((61000, 0o100644), (0, 0o100666), (0, 0o100664)):
        stat_of.update(uid=uid, mode=mode)
        with pytest.raises(box_run.Refused):
            box_run._root_only("/etc/puffpai/deny.d/x.json")       # noqa: SLF001


# ── a box web build whose parts come from elsewhere ──────────────────────────

def test_the_hosts_a_lock_downloads_from_are_named(tmp_path):
    from puffpai.integrate import apply
    lock = {"lockfileVersion": 3, "packages": {
        "": {"name": "jsonmatic"},
        "node_modules/react": {"version": "18.2.0",
                               "resolved": "https://registry.yarnpkg.com/react/-/react-18.2.0.tgz"},
        "node_modules/x": {"version": "1.0.0",
                           "resolved": "https://registry.npmjs.org/x/-/x-1.0.0.tgz"},
        "node_modules/y": {"version": "1.0.0",
                           "resolved": "git+https://github.com/o/y.git#abc"},
        "node_modules/client": {"link": True, "resolved": "client"}}}
    (tmp_path / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    assert apply.lock_download_hosts(str(tmp_path)) == ["github.com", "registry.yarnpkg.com"]
    assert apply._refused_hosts({"outside": [                               # noqa: SLF001
        {"host": "registry.yarnpkg.com:443", "outcome": "refused"},
        {"host": "registry.npmjs.org:443", "outcome": "done"}]}) == ["registry.yarnpkg.com"]


def test_after_her_yes_a_build_it_cannot_do_says_why_at_once(monkeypatch, tmp_path):
    from puffpai.integrate import apply, records, wording
    src = tmp_path / "src"
    src.mkdir()
    lock = {"lockfileVersion": 3, "packages": {"": {"name": "jsonmatic"},
            "node_modules/react": {"version": "18.2.0", "resolved":
                                   "https://registry.yarnpkg.com/react/-/react-18.2.0.tgz",
                                   "integrity": "sha512-x"}}}
    (src / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    fake_driver = types.SimpleNamespace(
        put_project=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no build")),
        run=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no build")),
        export=lambda *a, **k: [], status=lambda: types.SimpleNamespace(state="ready"))
    monkeypatch.setattr(apply, "optional", lambda name: fake_driver
                        if name == "puffpai.box.driver" else None)
    monkeypatch.setattr(apply, "_src_root", lambda rec: str(src))
    monkeypatch.setattr(apply, "_approved", lambda accepted, stage, sha: True)
    monkeypatch.setattr(apply, "_cleanup", lambda iid: None)
    saved = []
    monkeypatch.setattr(records, "save", lambda rec: saved.append(dict(rec)))
    said: list = []
    rec = {"id": OWN_B, "name": "Jsonmatic", "status": "checking"}
    got = apply._add_web_build(rec, say=said.append, lines=[], code_sha="c",   # noqa: SLF001
                               accepted={"box": "x"})
    line = wording.BUILD_HOSTS.format(hosts="registry.yarnpkg.com")
    assert got["status"] == "failed" and said == [line]
    assert line in got["line"] and saved[-1]["status"] == "failed"


def test_a_build_the_box_refused_hosts_for_names_them(monkeypatch, tmp_path):
    from puffpai.integrate import apply, records, wording
    src = tmp_path / "src"
    src.mkdir()
    lock = {"lockfileVersion": 3, "packages": {"": {"name": "app"},
            "node_modules/x": {"version": "1.0.0",
                               "resolved": "https://registry.npmjs.org/x/-/x-1.0.0.tgz"}}}
    (src / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    refused = types.SimpleNamespace(ok=False, exit_code=1, outside=[
        {"host": "cdn.example.org", "outcome": "refused", "code": "not_on_list"}])
    fake_driver = types.SimpleNamespace(
        put_project=lambda *a, **k: types.SimpleNamespace(ok=True, exit_code=0),
        run=lambda req: refused, export=lambda *a, **k: [], new_run_id=lambda: "r1",
        status=lambda: types.SimpleNamespace(state="ready"), BoxRequest=None,
        remove_project=lambda p: None)
    monkeypatch.setattr(apply, "optional", lambda name: fake_driver
                        if name == "puffpai.box.driver" else None)
    monkeypatch.setattr(apply, "_src_root", lambda rec: str(src))
    monkeypatch.setattr(apply, "_project_tar", lambda rec, s: str(tmp_path / "p.tar"))
    monkeypatch.setattr(apply, "_cleanup", lambda iid: None)
    monkeypatch.setattr(records, "save", lambda rec: None)
    said: list = []
    rec = {"id": OWN_A, "name": "To do", "status": "checking"}
    got = apply._add_web_build(rec, say=said.append, lines=[], code_sha="c",   # noqa: SLF001
                               accepted=None)
    assert got["status"] == "failed"
    assert said[-1] == wording.BUILD_HOSTS.format(hosts="cdn.example.org")


# ── What leaves your computer: one line for the stopped background calls ────

def test_the_hidden_browsers_stopped_calls_are_one_line(monkeypatch):
    from puffpai import outside_services
    from puffpai.security import egress_ledger
    recs = []
    for i, host in enumerate(["update.googleapis.com", "www.google.com", "update.googleapis.com",
                              "clients2.google.com", "accounts.google.com"]):
        recs.append({"t": f"2026-10-03T0{i}:00:00Z", "service": "app_runner", "host": host,
                     "what": "request", "outcome": "refused", "code": "not_allowed",
                     "status": "", "bytes_in": None})
    recs.insert(2, {"t": "2026-10-03T01:30:00Z", "service": "wikipedia", "host":
                    "en.wikipedia.org", "what": "request", "outcome": "done", "code": "",
                    "status": "2xx", "bytes_in": 2048})
    monkeypatch.setattr(egress_ledger, "read", lambda hours, limit=500: list(recs))
    monkeypatch.setattr(outside_services, "rows", lambda: [])
    got = outside_services.log_rows(24)
    runner = [r for r in got if r["service"] == "The hidden browser Toolsmith uses to try apps"]
    assert len(got) == 2 and len(runner) == 1
    row = runner[0]
    assert got[0] is row                                # at the newest one's place
    assert row["what"] == outside_services.QUIET_STOP_WHAT.format(n=5)
    assert row["result"] == outside_services.QUIET_STOP_RESULT
    assert row["website"] == "4 websites"
    assert "browser's own background call (like updates or search), which is never" \
        not in row["line"]
    assert row["line"].endswith("all stopped, as always: none of them reached the internet")
    other = [r for r in got if r is not row][0]
    assert other["website"] == "en.wikipedia.org"
