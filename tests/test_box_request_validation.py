"""Box request validation, on both sides, and the one jail builder

The launcher (``box/assets/box_run.py``) runs inside the box; it is loaded
here with a stand-in ``fcntl`` so its own ``validate`` and ``check_folder``
can be tested against the same cases as the Windows side.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import types

import pytest

from puffpai.box import driver, jail

ASSETS = os.path.join(os.path.dirname(driver.__file__), "assets")


def _launcher():
    """Load the in-box launcher here. On Windows there is no fcntl: a stand-in
    is put in sys.modules only while it loads, and taken out again (a stand-in
    left behind would change what other code finds)."""
    added = "fcntl" not in sys.modules
    if added:
        sys.modules["fcntl"] = types.ModuleType("fcntl")
    sys.path.insert(0, os.path.dirname(driver.__file__))
    try:
        spec = importlib.util.spec_from_file_location("l7_box_run_validate",
                                                      os.path.join(ASSETS, "box_run.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.pop(0)
        if added:
            sys.modules.pop("fcntl", None)
    return mod


LAUNCHER = _launcher()


def _req(**over):
    base = dict(run_id="r1234", project="seating-planner", argv=["npm", "ci"],
                allow_hosts=[], what="box_traffic", limits={}, stdin="none",
                stdout_mode="capture", env={})
    base.update(over)
    return driver.BoxRequest(**base)


def _wire(req):
    w = req.wire()
    w["service"] = w["service"] or driver.service_for(req.project)
    return w


# ── the same refusals on both sides ──────────────────────────────────────────

BAD = [
    dict(run_id="../../etc"), dict(run_id="R1"), dict(run_id="a" * 41), dict(run_id="r1\n"),
    dict(run_id=""), dict(project="-x"), dict(project="x" * 41), dict(project="a/b"),
    dict(argv=[]), dict(argv=["x"] * 65), dict(argv=["a\x00b"]), dict(argv=["x" * 4097]),
    dict(argv="npm ci"), dict(allow_hosts=["registry.npmjs.org:22"]),
    dict(allow_hosts=["1.1.1.1"]), dict(allow_hosts=["[::1]"]),
    dict(allow_hosts=["x.example.com"] * 33), dict(allow_hosts=["bad host.com"]),
    dict(what="request"), dict(stdin="file"), dict(stdout_mode="both"),
    dict(limits={"mem_mb": 9000}), dict(limits={"wall_s": 0}), dict(limits={"cpu_s": True}),
    dict(limits={"gpu": 1}), dict(limits={"disk_mb": 20481}),
    dict(env={"1BAD": "x"}), dict(env={f"K{i}": "v" for i in range(33)}),
    dict(env={"K": "x" * 4097}),
    dict(folder={"windows_path": "C:\\x", "mode": "rwx", "name": "files"}),
    dict(folder={"windows_path": "C:\\x", "mode": "ro", "name": "Bad Name"}),
    dict(folder={"windows_path": "C:\\x", "mode": "ro", "extra": 1}),
]


@pytest.mark.parametrize("over", BAD)
def test_bad_requests_are_refused_here(over):
    with pytest.raises(driver.BoxRefused):
        driver.validate(_req(**over))


@pytest.mark.parametrize("over", [o for o in BAD if "folder" not in o and o.get("argv") != "npm ci"])
def test_bad_requests_are_refused_by_the_launcher_too(over):
    with pytest.raises(LAUNCHER.Refused):
        LAUNCHER.validate(_wire(_req(**over)))


def test_the_launcher_refuses_unknown_fields_and_other_services():
    w = _wire(_req())
    with pytest.raises(LAUNCHER.Refused):
        LAUNCHER.validate(dict(w, sneaky=1))
    for svc in ("box:seating", "box_" + "0" * 23, "box_" + "g" * 24, "", "box_" + "0" * 24 + "\n"):
        with pytest.raises(LAUNCHER.Refused):
            LAUNCHER.validate(dict(w, service=svc))
    ok = LAUNCHER.validate(dict(w))
    assert ok["_limits"] == LAUNCHER.LIMIT_DEF


def test_a_good_request_is_cleaned_and_named_for_its_project():
    got = driver.validate(_req(allow_hosts=["Registry.NPMJS.org", "registry.npmjs.org:443"],
                               what="box_install", limits={"mem_mb": 8192}))
    assert got.allow_hosts == ["registry.npmjs.org"]
    assert got.service == driver.service_for("seating-planner")
    assert driver._BOX_SERVICE_RE.match(got.service)


def test_limit_caps_are_the_proven_ones():
    assert driver.LIMIT_MAX == LAUNCHER.LIMIT_MAX == {
        "cpu_s": 3600, "mem_mb": 8192, "wall_s": 3600, "pids": 1024, "fsize_mb": 4096,
        "disk_mb": 20480}
    assert driver.LIMIT_DEF == LAUNCHER.LIMIT_DEF


def test_install_phases_get_no_folder_and_only_package_sites():
    with pytest.raises(driver.BoxRefused) as exc:
        driver.validate(_req(what="box_install",
                             folder={"windows_path": "C:\\x", "mode": "ro", "name": "files"}))
    assert exc.value.code == "folder_while_installing"
    with pytest.raises(LAUNCHER.Refused):
        LAUNCHER.validate(_wire(_req(what="box_install")) | {
            "folder": {"windows_path": "C:\\Users\\a\\Pictures\\x", "mode": "ro", "name": "f"}})
    with pytest.raises(driver.BoxRefused) as exc:
        driver.validate(_req(what="box_install", allow_hosts=["evil.example.com"]))
    assert exc.value.code == "install_host"
    driver.validate(_req(what="box_install", allow_hosts=["pypi.org", "files.pythonhosted.org"]))


# ── run(): checks before anything starts ─────────────────────────────────────

@pytest.fixture
def no_box(monkeypatch):
    started: list = []
    monkeypatch.setattr(driver, "_platform", lambda: "windows")
    monkeypatch.setattr(driver, "_box_ready_for_run", lambda d: None)
    monkeypatch.setattr(driver, "sync_deny_list", lambda d: True)
    monkeypatch.setattr(driver, "_run_wsl", lambda d, req, **k: started.append(req)
                        or driver.BoxResult(ok=True))
    return started


def test_run_time_hosts_need_the_owners_switch(no_box, monkeypatch):
    res = driver.run(_req(allow_hosts=["api.example.com"]))
    assert res.refused == "switched_off" and no_box == []
    sw = types.SimpleNamespace(get=lambda sid: sid == driver.service_for("seating-planner"))
    real = driver._optional
    monkeypatch.setattr(driver, "_optional",
                        lambda n: sw if n == "puffpai.security.outside_switches" else real(n))
    assert driver.run(_req(allow_hosts=["api.example.com"])).ok and len(no_box) == 1


def test_a_folder_is_checked_by_the_owner_folder_rules(no_box, monkeypatch):
    from puffpai.security import owner_folders
    from puffpai.box import wording as W
    monkeypatch.setattr(owner_folders, "check", lambda p, m: (None, W.FOLDER_PRIVATE))
    res = driver.run(_req(folder={"windows_path": "C:\\vault", "mode": "ro", "name": "files"}))
    assert res.refused == "folder" and res.error == W.FOLDER_PRIVATE and no_box == []
    monkeypatch.setattr(owner_folders, "check", lambda p, m: ("C:\\Users\\a\\Pictures\\Real", ""))
    driver.run(_req(folder={"windows_path": "C:\\Users\\a\\Pictures\\link", "mode": "ro",
                            "name": "files"}))
    # Only the resolved path is sent.
    assert no_box[0].folder["windows_path"] == "C:\\Users\\a\\Pictures\\Real"


def test_streams_need_their_files(no_box):
    assert driver.run(_req(stdin="pass")).refused == "bad_streams"
    assert driver.run(_req(stdout_mode="raw")).refused == "bad_streams"
    assert no_box == []


def test_run_refuses_when_the_network_setting_changed(monkeypatch, tmp_path):
    monkeypatch.setattr(driver, "_platform", lambda: "windows")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    (tmp_path / ".wslconfig").write_text("[wsl2]\nnetworkingMode=mirrored\n", encoding="utf-8")
    monkeypatch.setattr(driver, "_run_wsl", lambda *a, **k: pytest.fail("started"))
    res = driver.run(_req())
    assert res.refused == "not_nat"


def test_the_self_test_runs_again_after_wsl_changed(monkeypatch, tmp_path):
    monkeypatch.setattr(driver, "_platform", lambda: "windows")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(driver, "_entries_named", lambda d: [{"base_path": "x"}])
    monkeypatch.setattr(driver, "base_path_is_ours", lambda d, b: True)
    monkeypatch.setattr(driver, "_has_my_claim", lambda d: True)
    monkeypatch.setattr(driver, "_launcher_current", lambda d: True)
    versions = iter(["v1", "v1", "v2", "v2"])
    monkeypatch.setattr(driver, "wsl_version_sha", lambda: next(versions))
    tests: list = []
    results = iter([True, False])
    monkeypatch.setattr(driver, "self_test", lambda d: tests.append(d) or next(results))
    monkeypatch.setattr(driver, "_run_wsl", lambda *a, **k: driver.BoxResult(ok=True))
    assert driver.run(_req()).ok and len(tests) == 1          # first job: self-test
    assert driver.run(_req()).ok and len(tests) == 1          # same WSL: no test
    res = driver.run(_req())                                  # WSL changed: test, fails
    assert res.refused == "box_broken" and len(tests) == 2
    assert driver.run(_req()).refused == "box_broken"          # stays broken


# ── the one jail ─────────────────────────────────────────────────────────────

def test_the_jail_has_every_proven_wall():
    a = jail.bwrap_argv("/srv/box/projects/p")
    s = " ".join(a)
    for flag in ("--unshare-all", "--unshare-user", "--disable-userns", "--die-with-parent",
                 "--new-session", "--clearenv", "--cap-drop ALL", "--remount-ro /",
                 f"--seccomp {jail.SECCOMP_FD}", "--bind /srv/box/projects/p /work",
                 "--chdir /work", "--proc /proc", "--dev /dev", "--tmpfs /tmp"):
        assert flag in s, flag
    assert a[0] == "bwrap" and a[-2:] == ["--", jail.ENTRY]
    assert "/mnt" not in s and "--share-net" not in s and "/run/egress.sock" not in s
    assert "--ro-bind /usr /usr" in s and "--bind /usr" not in s


def test_folder_and_outside_socket_are_added_only_when_given():
    ro = jail.bwrap_argv("/w", folder={"path": "/srv/box/owner/r1", "mode": "ro", "name": "files"})
    assert "--ro-bind /srv/box/owner/r1 /data/files" in " ".join(ro)
    rw = jail.bwrap_argv("/w", folder={"path": "/srv/box/owner/r1", "mode": "rw", "name": "files"})
    assert "--bind /srv/box/owner/r1 /data/files" in " ".join(rw)
    eg = " ".join(jail.bwrap_argv("/w", egress_sock="/run/puffpai/r1/egress.sock"))
    assert "--ro-bind /run/puffpai/r1/egress.sock /run/egress.sock" in eg
    assert "--setenv HTTPS_PROXY http://127.0.0.1:3128" in eg and "NODE_USE_ENV_PROXY 1" in eg


@pytest.mark.parametrize("kw", [
    dict(folder={"path": "rel", "mode": "ro", "name": "f"}),
    dict(folder={"path": "/x/../y", "mode": "ro", "name": "f"}),
    dict(folder={"path": "/x", "mode": "rwx", "name": "f"}),
    dict(folder={"path": "/x", "mode": "ro", "name": "../f"}),
    dict(folder={"path": "/x", "mode": "ro", "name": "f", "extra": 1}),
    dict(egress_sock="relative.sock"),
    dict(env={"LD_PRELOAD": "/x.so"}), dict(env={"PATH": "/evil"}),
    dict(env={"HTTPS_PROXY": "http://evil"}), dict(env={"NODE_OPTIONS": "--require x"}),
    dict(env={"BWRAP_X": "1"}), dict(env={"ok": "a\x00b"}),
    dict(env={f"K{i}": "v" for i in range(33)}),
])
def test_the_jail_refuses_odd_inputs(kw):
    with pytest.raises(ValueError):
        jail.bwrap_argv("/w", **kw)
    with pytest.raises(ValueError):
        jail.bwrap_argv("relative")


def test_the_launcher_builds_its_runs_with_the_same_jail():
    req = LAUNCHER.validate(_wire(_req(argv=["npm", "run", "build"], env={"CI": "1"},
                                       limits={"cpu_s": 30, "fsize_mb": 10})))
    got = LAUNCHER.run_argv(req, "/srv/box/projects/p", None, None)
    prefix = got[:got.index("bwrap")]
    assert prefix[:6] == ["setpriv", "--reuid=61000", "--regid=61000", "--clear-groups",
                          "--no-new-privs", "--inh-caps=-all"]
    assert "--cpu=30" in prefix and f"--fsize={10 * 1024 * 1024}" in prefix and "--core=0" in prefix
    body = got[got.index("bwrap"):]
    assert body == jail.bwrap_argv("/srv/box/projects/p", env={"CI": "1"}) + ["npm", "run", "build"]
    assert LAUNCHER.jail.bwrap_argv is not None


def test_the_launcher_checks_owner_folders_against_its_deny_list(tmp_path, monkeypatch):
    deny = tmp_path / "deny.json"
    deny.write_text(json.dumps(["C:\\Users\\a\\PuffPAI\\vault_data", "C:\\Windows"]),
                    encoding="utf-8")
    monkeypatch.setattr(LAUNCHER, "DENY_FILE", str(deny))
    monkeypatch.setattr(LAUNCHER, "DENY_DIR", str(tmp_path / "no-deny.d"))
    monkeypatch.setattr(LAUNCHER, "_root_only", lambda p: None)   # Windows has no root owner
    LAUNCHER.check_folder("C:\\Users\\a\\Pictures\\Wedding")
    for bad in ("C:\\Users\\a\\PuffPAI\\vault_data", "C:\\Users\\a\\PuffPAI\\vault_data\\x",
                "C:\\Users\\a\\PuffPAI", "C:\\Users", "C:\\", "C:\\Users\\a\\..\\a\\x",
                "\\\\server\\share\\x", "C:/Users/a/Pictures", "C:\\Windows\\System32",
                "C:\\Users\\a\\Pictures\\x\x00"):
        with pytest.raises(LAUNCHER.Refused):
            LAUNCHER.check_folder(bad)
    monkeypatch.setattr(LAUNCHER, "DENY_FILE", str(tmp_path / "missing.json"))
    with pytest.raises(LAUNCHER.Refused):
        LAUNCHER.check_folder("C:\\Users\\a\\Pictures\\Wedding")     # fail closed


def test_result_lines_are_read_strictly():
    ok = driver.BoxResult.from_dict({"schema": driver.RESULT_SCHEMA, "ok": True, "exit_code": 0,
                                     "wall_s": 1.5, "outside": [{"host": "x"}, "junk"],
                                     "evil": "field"})
    assert ok.ok and ok.wall_s == 1.5 and ok.outside == [{"host": "x"}]
    assert not driver.BoxResult.from_dict({"schema": "other", "ok": True}).ok
    assert not driver.BoxResult.from_dict(None).ok
    refused = driver.BoxResult.from_dict({"schema": driver.RESULT_SCHEMA, "ok": True,
                                          "refused": "bad argv"})
    assert not refused.ok and refused.refused == "bad argv"
    assert not driver.BoxResult.from_dict({"schema": driver.RESULT_SCHEMA, "ok": "yes"}).ok
