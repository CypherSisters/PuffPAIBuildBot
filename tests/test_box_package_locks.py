"""Nothing updates by itself: recorded locks for code in the locked box

The pip report fixture was recorded in the real locked box (csvkit 2.2.0,
``pip install --only-binary=:all: --report``). No network, no box here.
"""

from __future__ import annotations

import io
import json
import os
import tarfile

import pytest

from puffpai.box import cli_tools, driver, locks
from puffpai.integrate import records

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "cli_help")
REG = locks.NPM_REGISTRY
INTEGRITY = "sha512-" + "A" * 86 + "=="


def _lock(packages: dict) -> bytes:
    body = {"name": "x", "lockfileVersion": 3, "requires": True,
            "packages": {"": {"dependencies": {"a": "1.0.0"}}, **packages}}
    return json.dumps(body).encode("utf-8")


def _pkg(name, resolved=None, **extra):
    entry = {"version": "1.0.0", "integrity": INTEGRITY,
             "resolved": resolved if resolved is not None else f"{REG}{name}/-/{name}-1.0.0.tgz"}
    entry.update(extra)
    return {f"node_modules/{name}": entry}


# ── npm: the scan ────────────────────────────────────────────────────────────

def test_a_lock_from_the_registry_is_clean():
    assert locks.scan_npm_lock(_lock({**_pkg("a"), **_pkg("b")})) == []


@pytest.mark.parametrize("resolved", [
    "git+https://github.com/x/y.git#abc", "git+ssh://github.com/x/y.git",
    "https://codeload.github.com/x/y/tar.gz/abc", "http://registry.npmjs.org/a/-/a-1.0.0.tgz",
    "file:../a", "https://registry.npmjs.org.evil.example/a/-/a-1.0.0.tgz",
    "https://registry.yarnpkg.com/a/-/a-1.0.0.tgz", "https://registry.npmjs.org/a/-/a b.tgz",
])
def test_anything_not_from_the_npm_registry_is_held(resolved):
    problems = locks.scan_npm_lock(_lock(_pkg("a", resolved)))
    assert len(problems) == 1


def test_a_package_without_an_address_or_fingerprint_is_held():
    no_addr = {"node_modules/a": {"version": "github:x/y#abc"}}
    assert locks.scan_npm_lock(_lock(no_addr))
    no_fp = {"node_modules/a": {"version": "1.0.0", "resolved": f"{REG}a/-/a-1.0.0.tgz"}}
    assert locks.scan_npm_lock(_lock(no_fp)) == ["node_modules/a: no fingerprint"]


def test_workspace_links_inside_the_project_are_not_downloads():
    ok = {"node_modules/client": {"resolved": "client", "link": True}}
    assert locks.scan_npm_lock(_lock(ok)) == []
    for bad in ("../elsewhere", "/abs", "C:\\x", "file:x"):
        assert locks.scan_npm_lock(_lock({"node_modules/c": {"resolved": bad, "link": True}}))


def test_an_old_style_lock_is_read_too():
    v1 = {"lockfileVersion": 1, "dependencies": {
        "a": {"version": "1.0.0", "resolved": f"{REG}a/-/a-1.0.0.tgz", "integrity": INTEGRITY,
              "dependencies": {"b": {"version": "2.0.0", "resolved": "git+https://x/y.git",
                                     "integrity": INTEGRITY}}}}}
    got = locks.scan_npm_lock(json.dumps(v1).encode())
    assert got == ["a/b: comes from somewhere other than the npm registry"]


def test_unreadable_and_huge_locks_are_held():
    assert locks.scan_npm_lock(b"not json")
    assert locks.scan_npm_lock(b"[1,2]")
    assert locks.scan_npm_lock(b" " * (locks.MAX_LOCK_BYTES + 1))


# ── pip: the lock from the report ────────────────────────────────────────────

def _report() -> bytes:
    with open(os.path.join(FIX, "csvkit_pip_report.json"), "rb") as fh:
        return fh.read()


def test_the_recorded_csvkit_report_becomes_a_hash_lock():
    text = locks.pip_lock_from_report(_report())
    lines = text.strip().splitlines()
    installs = json.loads(_report())["install"]
    assert len(lines) == len(installs) >= 5
    assert all(" --hash=sha256:" in ln and "==" in ln for ln in lines)
    assert any(ln.lower().startswith("csvkit==2.2.0 ") for ln in lines)
    assert locks.pip_lock_count(text) == len(installs)


def test_a_wheel_from_elsewhere_or_a_source_package_is_off_site():
    report = json.loads(_report())
    report["install"][0]["download_info"]["url"] = "https://example.com/x-1.0-py3-none-any.whl"
    with pytest.raises(locks.LockError) as exc:
        locks.pip_lock_from_report(json.dumps(report).encode())
    assert exc.value.code == "lock_offsite"
    report = json.loads(_report())
    report["install"][0]["download_info"]["url"] = locks.PYPI_FILES + "p/x/x-1.0.tar.gz"
    text, off = locks.pip_lock_parts(json.dumps(report).encode())
    assert off and text


def test_a_part_without_a_hash_is_refused():
    report = json.loads(_report())
    report["install"][1]["download_info"]["archive_info"]["hashes"] = {}
    with pytest.raises(locks.LockError) as exc:
        locks.pip_lock_from_report(json.dumps(report).encode())
    assert exc.value.code == "no_hashes"
    with pytest.raises(locks.LockError):
        locks.pip_lock_from_report(b"{}")


# ── the steps ────────────────────────────────────────────────────────────────

def test_install_steps_never_run_package_scripts():
    assert "--ignore-scripts" in locks.NPM_CI and "--ignore-scripts" in locks.NPM_INSTALL
    first = locks.pip_first_install("csvkit", "2.2.0")
    assert "--only-binary=:all:" in first and "--report" in first and first[-1] == "csvkit==2.2.0"
    re_ = locks.pip_rebuild()
    for flag in ("--require-hashes", "--no-deps", "--only-binary=:all:"):
        assert flag in re_
    assert locks.npm_package_install("@scope/tool", "1.2.3")[-1] == "@scope/tool@1.2.3"
    for name, ver in (("tool", "^1.2.3"), ("tool", "latest"), ("Tool;rm", "1.0.0"),
                      ("tool", "1.2"), ("../x", "1.0.0")):
        with pytest.raises(locks.LockError):
            locks.npm_package_install(name, ver)
    for name, ver in (("csvkit", ">=2"), ("csvkit", ""), ("-e git+x", "1.0")):
        with pytest.raises(locks.LockError):
            locks.pip_first_install(name, ver)


def test_first_build_tries_the_projects_lock_then_install_once_rebuild_only_ci():
    assert locks.npm_steps(project_has_lock=True, recorded=None) == [locks.NPM_CI, locks.NPM_INSTALL]
    assert locks.npm_steps(project_has_lock=False, recorded=None) == [locks.NPM_INSTALL]
    assert locks.npm_steps(project_has_lock=True, recorded={"sha256": "x"}) == [locks.NPM_CI]
    assert locks.npm_steps(project_has_lock=False, recorded={"sha256": "x"}) == [locks.NPM_CI]


# ── the record ───────────────────────────────────────────────────────────────

def test_a_recorded_lock_is_read_back_and_an_edited_one_is_refused():
    iid = records.new_id("tool")
    data = _lock(_pkg("a"))
    rec = locks.record(iid, "npm", data)
    assert rec["sha256"] == locks.sha256(data) and rec["path"].endswith(locks.NPM_LOCK)
    back = locks.recorded(iid)
    assert back["data"] == data and back["kind"] == "npm"
    with open(rec["path"], "ab") as fh:
        fh.write(b" ")
    with pytest.raises(locks.LockError) as exc:
        locks.recorded(iid)
    assert exc.value.code == "lock_changed"
    assert locks.recorded(records.new_id("never")) is None


def test_a_rebuild_refuses_a_project_that_brings_a_changed_lock():
    iid = records.new_id("tool")
    data = _lock(_pkg("a"))
    rec = locks.record(iid, "npm", data)
    locks.check_same(dict(rec, data=data), data)
    locks.check_same(dict(rec, data=data), None)
    with pytest.raises(locks.LockError) as exc:
        locks.check_same(dict(rec, data=data), _lock(_pkg("b")))
    assert exc.value.code == "lock_changed"


@pytest.mark.parametrize("kind", ["npm", "pip"])
def test_rebuild_uses_only_the_recorded_lock(monkeypatch, kind):
    iid = records.new_id("tool")
    data = _lock(_pkg("a")) if kind == "npm" else locks.pip_lock_from_report(_report()).encode()
    locks.record(iid, kind, data)
    cli_tools._save_json(cli_tools._cli_path(iid), {
        "package": {"registry": kind, "name": "a", "version": "1.0.0"}, "specs": [],
        "package_json": json.dumps({"private": True, "dependencies": {"a": "1.0.0"}})})
    puts: list = []
    runs: list = []

    def put(project, tar_path, *, disk_mb=4096):
        with tarfile.open(tar_path) as tf:
            puts.append({m.name: tf.extractfile(m).read() for m in tf})
        return driver.BoxResult(ok=True)
    monkeypatch.setattr(driver, "put_project", put)
    monkeypatch.setattr(driver, "remove_project", lambda p: None)
    monkeypatch.setattr(driver, "run", lambda req, **k: runs.append(req) or driver.BoxResult(ok=True))
    assert cli_tools.rebuild(iid).ok
    name = locks.NPM_LOCK if kind == "npm" else locks.PIP_LOCK
    assert puts[0][name] == data
    argvs = [r.argv for r in runs]
    if kind == "npm":
        assert argvs == [locks.NPM_CI] and "package.json" in puts[0]
        assert all("install" not in a for a in argvs)
    else:
        assert argvs == [locks.VENV, locks.pip_rebuild()]
    assert all(r.what == "box_install" and r.folder is None for r in runs)


def test_rebuild_refuses_when_the_lock_was_edited(monkeypatch):
    iid = records.new_id("tool")
    rec = locks.record(iid, "npm", _lock(_pkg("a")))
    cli_tools._save_json(cli_tools._cli_path(iid), {"package": {}, "specs": []})
    with open(rec["path"], "wb") as fh:
        fh.write(_lock(_pkg("evil")))
    monkeypatch.setattr(driver, "run", lambda *a, **k: pytest.fail("ran"))
    with pytest.raises(locks.LockError):
        cli_tools.rebuild(iid)


def test_a_real_workspace_lock_is_clean_and_its_own_folders_are_not_downloads():
    """JFrusher/Tableaux's lock, read out of the box in the live run: npm
    workspaces list the project's own folders ("client", "server") with no
    download address; they are source, not downloads."""
    with open(os.path.join(FIX, "tableaux_package_lock.json"), "rb") as fh:
        data = fh.read()
    assert locks.scan_npm_lock(data) == []
    lock = json.loads(data)
    assert "client" in lock["packages"] and "resolved" not in lock["packages"]["client"]
    lock["packages"]["../outside"] = {"version": "1.0.0"}
    lock["packages"]["client/node_modules/x"] = {"version": "1.0.0",
                                                 "resolved": "git+https://x/y.git"}
    got = locks.scan_npm_lock(json.dumps(lock).encode())
    assert len(got) == 2
