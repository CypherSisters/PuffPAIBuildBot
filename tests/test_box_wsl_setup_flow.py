"""Setting the locked box up, turning Windows' Linux feature on, and things
waiting for the box.

Everything runs against a fake ``wsl.exe`` (it also plays the new distro and
its mounts), a fake download and fake tickets where noted; nothing here
starts a real program, touches the network or a real distro.

The setup-order tests exist because of an incident: a ported setup script
ran ``rm -rf /mnt/c`` in the first boot of a new distro, when Windows' C:
drive was still mounted there. Setup must lock the box and restart it before
anything else runs, and nothing in the box's files may delete recursively
under /mnt.
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import tarfile

import pytest

from puffpai import drawbridge
from puffpai.box import driver
from puffpai.box import wording as W
from puffpai.security import consent_tickets as ct

from test_box_status_states import FakeWsl, box_env  # noqa: F401  (fixture)

#: The WSL box is Windows only: off Windows box.driver answers before it
#: ever reaches WSL (Linux uses bubblewrap), so these have nothing to test.
wsl_box_only = pytest.mark.skipif(os.name != "nt", reason="the WSL box is Windows only")

ASSETS = os.path.join(os.path.dirname(driver.__file__), "assets")
MNT_C = "C:\\134 /mnt/c 9p rw,noatime,dirsync,aname=drvfs 0 0"
LOCKED = ("none /mnt/wsl tmpfs rw 0 0\nnone /mnt/wslg tmpfs rw 0 0\n"
          "/dev/sdd / ext4 rw 0 0\n")


class SetupWsl(FakeWsl):
    """A fake wsl.exe that also imports a distro and models its mounts: C: is
    mounted at /mnt/c until /etc/wsl.conf is written AND the distro restarted."""

    def __init__(self, monkeypatch, *, lock_works: bool = True, script_rc: int = 0):
        super().__init__(monkeypatch)
        self.lock_works = lock_works
        self.script_rc = script_rc
        self.conf_written = False
        self.locked = False
        self.order: list[str] = []
        self.assets: dict[str, bytes] = {}

    def spawn(self, argv, *, timeout, input_bytes=None, **kwargs):
        args = [str(a) for a in argv[1:]]
        if args[:1] == ["--import"]:
            self.calls.append([str(a) for a in argv])
            self.order.append("import")
            self.add(args[1], args[2], marker=False, launcher=False)
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        if args[:1] == ["--terminate"]:
            self.order.append("terminate")
            if self.conf_written and self.lock_works:
                self.locked = True
        if len(args) >= 6 and args[0] == "-d" and args[2:5] == ["-u", "root", "--exec"]:
            name, rest = args[1], args[5:]
            ok = subprocess.CompletedProcess(argv, 0, b"", b"")
            if rest[:2] == ["/bin/sh", "-c"] and rest[2].endswith(f"cat > {driver.MARKER_PATH}"):
                self.calls.append([str(a) for a in argv])
                self.order.append("marker")
                self.markers[name] = json.loads(input_bytes.decode("utf-8"))
                return ok
            if rest[:2] == ["/bin/sh", "-c"] and rest[2].endswith("cat > /etc/wsl.conf"):
                self.calls.append([str(a) for a in argv])
                self.order.append("lock")
                assert b"enabled = false" in input_bytes
                self.conf_written = True
                return ok
            if rest == ["/bin/cat", "/proc/mounts"]:
                self.calls.append([str(a) for a in argv])
                self.order.append("mounts")
                text = LOCKED if self.locked else LOCKED + MNT_C + "\n"
                return subprocess.CompletedProcess(argv, 0, text.encode(), b"")
            if rest[:2] == ["/bin/sh", "-c"] and "tar -x -C /tmp/boxsrc" in rest[2]:
                self.calls.append([str(a) for a in argv])
                self.order.append("assets")
                with tarfile.open(fileobj=io.BytesIO(input_bytes)) as tf:
                    for m in tf:
                        self.assets[m.name] = tf.extractfile(m).read()
                return ok
            if rest == ["/bin/sh", "/tmp/boxsrc/setup_box.sh"]:
                self.calls.append([str(a) for a in argv])
                self.order.append("setup_box")
                assert self.locked, "setup_box.sh ran while C: was mounted"
                self.launcher[name] = self.script_rc == 0
                return subprocess.CompletedProcess(argv, self.script_rc, b"provisioned\n", b"")
        return super().spawn(argv, timeout=timeout, input_bytes=input_bytes, **kwargs)


@pytest.fixture
def setup_env(box_env, monkeypatch, tmp_path):
    rootfs = tmp_path / "rootfs.tar.gz"
    rootfs.write_bytes(b"not really a rootfs")
    got = {"path": str(rootfs), "sha256": driver.ROOTFS_SHA256, "bytes": 19,
           "final_host": driver.ROOTFS_HOST}
    calls: list[dict] = []

    class FakeFetch:
        @staticmethod
        def get_archive(url, **kw):
            calls.append(dict(kw, url=url))
            return dict(got)

    real_optional = driver._optional
    monkeypatch.setattr(driver, "_optional",
                        lambda name: FakeFetch if name == "puffpai.source_fetch"
                        else real_optional(name))
    monkeypatch.setattr(drawbridge, "_checker_for",
                        lambda kind: {"rows_fn": driver.box_setup_rows,
                                      "block": driver.BOX_SETUP_BLOCK, "hold": frozenset(),
                                      "version": "r3.1"} if kind == "box_setup" else None)
    monkeypatch.setenv("PUFFPAI_BOX_DISTRO", "PuffPAI-sandbox-dev")
    ct.reset()
    return {"got": got, "fetch_calls": calls, **box_env}


def _ticket(distro="PuffPAI-sandbox-dev"):
    device = ct.terminal_device_id()
    return ct.mint("box_setup", ct.target_box(distro), "Set up the locked box.", device), device


# ── the setup order (the incident) ──────────────────────────────────────────

def test_setup_locks_and_restarts_before_anything_else_runs(setup_env, monkeypatch):
    fake = SetupWsl(monkeypatch)
    lines: list[str] = []
    ticket, device = _ticket()
    st = driver.setup(ticket=ticket, device_id=device, progress=lines.append)
    assert st.ready, st
    assert fake.order[:5] == ["import", "marker", "lock", "terminate", "mounts"]
    assert fake.order.index("setup_box") > fake.order.index("mounts") > fake.order.index("lock")
    assert fake.order[-1] != "setup_box" and "terminate" in fake.order[fake.order.index("setup_box"):]
    assert lines[:3] == [W.SETUP_DOWNLOADING, W.SETUP_UNPACKING, W.SETUP_TOOLS]
    assert lines[-1] == W.SETUP_DONE
    # the download: pinned sha, only the Alpine host, the box_install service
    fc = setup_env["fetch_calls"][0]
    assert fc["expect_sha256"] == driver.ROOTFS_SHA256
    assert fc["allowed_hosts"] == {driver.ROOTFS_HOST} and fc["service"] == "box_install"
    assert driver._has_my_claim("PuffPAI-sandbox-dev")
    assert not any("--shutdown" in c for c in fake.calls)


def test_setup_stops_and_takes_the_box_out_when_the_lock_does_not_hold(setup_env, monkeypatch):
    fake = SetupWsl(monkeypatch, lock_works=False)
    ticket, device = _ticket()
    st = driver.setup(ticket=ticket, device_id=device, progress=lambda line: None)
    assert not st.ready and st.message == W.SETUP_BROKE
    assert "assets" not in fake.order and "setup_box" not in fake.order
    assert fake.ran("--unregister", "PuffPAI-sandbox-dev")
    assert not driver._has_my_claim("PuffPAI-sandbox-dev")


def test_a_failed_setup_script_takes_the_box_back_out(setup_env, monkeypatch):
    fake = SetupWsl(monkeypatch, script_rc=3)
    ticket, device = _ticket()
    st = driver.setup(ticket=ticket, device_id=device, progress=lambda line: None)
    assert st.message == W.SETUP_BROKE
    assert fake.ran("--unregister", "PuffPAI-sandbox-dev")
    assert not os.path.exists(os.path.join(setup_env["root"], "PuffPAI-sandbox-dev"))


def test_mounts_are_locked_rule():
    assert driver.mounts_are_locked(LOCKED)
    assert not driver.mounts_are_locked(LOCKED + MNT_C)
    assert not driver.mounts_are_locked(LOCKED + "D:\\134 /mnt/d 9p rw 0 0")
    assert not driver.mounts_are_locked(LOCKED + "x /mnt/anything ext4 rw 0 0")
    assert not driver.mounts_are_locked("")


def test_no_box_file_deletes_recursively_under_mnt():
    """The incident's line, and any other recursive delete of a /mnt path, is
    never in the box's files."""
    bad = re.compile(r"rm\s+(-[A-Za-z]*[rR][A-Za-z]*\s+)+[^\n;&|]*?/mnt|rmtree\([^)]*/mnt|"
                     r"find\s+/mnt[^\n]*-delete")
    for name in os.listdir(ASSETS):
        if not os.path.isfile(os.path.join(ASSETS, name)) or name.endswith(".pyc"):
            continue        # a __pycache__ folder once a test imports from here
        text = open(os.path.join(ASSETS, name), encoding="utf-8").read()
        assert not bad.search(text), name
    src = open(driver.__file__, encoding="utf-8").read()
    assert not bad.search(src)


def test_setup_script_refuses_to_run_with_a_windows_drive_mounted():
    script = open(os.path.join(ASSETS, "setup_box.sh"), encoding="utf-8").read()
    guard = script.index("/proc/mounts")
    first_cmd = script.index("apk add")
    assert guard < first_cmd and "exit 3" in script[guard:first_cmd]
    assert "rmdir /mnt/c" in script and "rm -rf /mnt" not in script


def test_the_launcher_refuses_to_run_with_a_windows_drive_mounted():
    import importlib.util
    import sys
    import types
    added = "fcntl" not in sys.modules
    if added:
        sys.modules["fcntl"] = types.ModuleType("fcntl")
    sys.path.insert(0, os.path.dirname(driver.__file__))
    try:
        spec = importlib.util.spec_from_file_location("l7_box_run_mounts",
                                                      os.path.join(ASSETS, "box_run.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.pop(0)
        if added:
            sys.modules.pop("fcntl", None)
    assert mod.windows_drive_mounted(LOCKED + MNT_C)
    assert not mod.windows_drive_mounted(LOCKED)
    src = open(os.path.join(ASSETS, "box_run.py"), encoding="utf-8").read()
    body = src[src.index("def execute("):]
    assert body.index("windows_drive_mounted()") < body.index("sweep()")


@wsl_box_only
def test_assets_stream_has_the_launcher_the_jail_and_no_windows_line_endings(setup_env):
    data = driver.assets_tar("PuffPAI-sandbox-dev")
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        names = {m.name: tf.extractfile(m).read() for m in tf}
    assert set(names) == {"box_run.py", "egress_proxy.py", "box-entry", "setup_box.sh",
                          "wsl.conf", "make_seccomp.py", "jail.py", "deny_windows.json"}
    for name, body in names.items():
        assert b"\r\n" not in body, name
    deny = json.loads(names["deny_windows.json"])
    assert "C:\\Windows" in deny
    assert any(p.lower().endswith("vault_data") for p in deny)


# ── setup: the yes, the download, adopting ──────────────────────────────────

def test_setup_needs_the_owners_yes_for_this_box(setup_env, monkeypatch):
    fake = SetupWsl(monkeypatch)
    _t, device = _ticket()
    other = ct.mint("box_setup", ct.target_box("PuffPAI-sandbox-other"), "x", device)
    st = driver.setup(ticket=other, device_id=device, progress=lambda line: None)
    assert not st.ready and "Nothing was set up" in st.message
    st = driver.setup(ticket="forged", device_id=device, progress=lambda line: None)
    assert not st.ready
    assert "import" not in fake.order and not setup_env["fetch_calls"]


def test_a_changed_download_is_never_imported(setup_env, monkeypatch):
    fake = SetupWsl(monkeypatch)
    setup_env["got"]["sha256"] = "0" * 64
    ticket, device = _ticket()
    st = driver.setup(ticket=ticket, device_id=device, progress=lambda line: None)
    assert st.message == W.SETUP_NOT_EXPECTED
    assert "import" not in fake.order


def test_setup_without_the_download_part_is_not_in_this_build(setup_env, monkeypatch):
    fake = SetupWsl(monkeypatch)
    import importlib
    monkeypatch.setattr(driver, "_optional",
                        lambda name: None if name == "puffpai.source_fetch"
                        else importlib.import_module(name))
    ticket, device = _ticket()
    st = driver.setup(ticket=ticket, device_id=device, progress=lambda line: None)
    assert st.message == W.SETUP_NOT_IN_BUILD
    assert "import" not in fake.order


def test_setup_never_imports_into_a_folder_that_has_files(setup_env, monkeypatch):
    fake = SetupWsl(monkeypatch)
    home = os.path.join(setup_env["root"], "PuffPAI-sandbox-dev")
    os.makedirs(home)
    open(os.path.join(home, "somebodys.txt"), "w").close()
    ticket, device = _ticket()
    st = driver.setup(ticket=ticket, device_id=device, progress=lambda line: None)
    assert st.message == W.SETUP_FOLDER_IN_USE and "import" not in fake.order


def test_box_setup_checklist_is_the_pinned_fingerprint():
    ok = driver.box_setup_rows({"rootfs_sha256": driver.ROOTFS_SHA256})
    bad = driver.box_setup_rows({"rootfs_sha256": "1" * 64})
    assert ok[0]["result"] == "pass" and bad[0]["result"] == "fail"
    assert bad[0]["code"] == "rootfs_changed" and "rootfs_changed" in driver.BOX_SETUP_BLOCK


# ── Windows' Linux feature missing ──────────────────────────────────────────

class FakePopen:
    def __init__(self, argv, rc):
        self.argv = argv
        self.rc = rc

    def wait(self, timeout=None):
        return self.rc


def test_install_wsl_asks_windows_by_full_path_after_a_yes(box_env, monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_BOX_DISTRO", "PuffPAI-sandbox-dev")
    started: list = []
    monkeypatch.setattr(driver, "_popen", lambda argv, **k: started.append(argv) or FakePopen(argv, 0))
    hp = type("HP", (), {"path": staticmethod(lambda n: r"C:\Windows\System32\wsl.exe"),
                         "run_kwargs": staticmethod(lambda: {})})
    real = driver._optional
    monkeypatch.setattr(driver, "_optional",
                        lambda n: hp if n == "puffpai.security.host_programs" else real(n))
    ct.reset()
    got = driver.install_wsl(ticket="nope", device_id=ct.terminal_device_id())
    assert got["started"] is False and not started
    ticket, device = _ticket()
    got = driver.install_wsl(ticket=ticket, device_id=device)
    assert got == {"started": True, "message": W.WSL_TURNING_ON}
    assert started == [[r"C:\Windows\System32\wsl.exe", "--install", "--no-distribution"]]


def test_a_declined_windows_prompt_is_said_plainly(box_env, monkeypatch):
    monkeypatch.setenv("PUFFPAI_BOX_DISTRO", "PuffPAI-sandbox-dev")
    import threading
    done = threading.Event()
    monkeypatch.setattr(driver, "_popen", lambda argv, **k: FakePopen(argv, 1))
    real_thread = threading.Thread

    class Now(real_thread):
        def start(self):
            self.run()
            done.set()
    monkeypatch.setattr(threading, "Thread", Now)
    hp = type("HP", (), {"path": staticmethod(lambda n: r"C:\Windows\System32\wsl.exe"),
                         "run_kwargs": staticmethod(lambda: {})})
    real = driver._optional
    monkeypatch.setattr(driver, "_optional",
                        lambda n: hp if n == "puffpai.security.host_programs" else real(n))
    for i in range(3):
        driver.add_waiting({"item": {"id": f"apps_code:npm:t{i}", "label": "t",
                                     "apply": {"kind": "cli_tool", "ref": "x"}}})
    ct.reset()
    ticket, device = _ticket()
    assert driver.install_wsl(ticket=ticket, device_id=device)["started"]
    assert done.is_set()
    got = driver.wsl_install_outcome()
    assert got == {"declined": True, "message": W.WSL_DECLINED.format(n=3)}


# ── things waiting for the box ───────────────────────────────────────────────

def _entry(i, kind="cli_tool"):
    return {"item": {"id": f"apps_code:npm:tool{i}", "label": f"Tool {i}",
                     "apply": {"kind": kind, "ref": f"npm:tool{i}"}},
            "found": {"id": f"npm:tool{i}", "package": {"registry": "npm", "name": f"tool{i}",
                                                        "version": "1.0.0"}}}


def test_waiting_items_are_kept_with_their_find_and_replaced_by_id(box_env):
    driver.add_waiting(_entry(1))
    driver.add_waiting(_entry(2))
    driver.add_waiting(_entry(1))
    got = driver.waiting()
    assert [w["item"]["id"] for w in got] == ["apps_code:npm:tool2", "apps_code:npm:tool1"]
    assert got[0]["found"]["package"]["name"] == "tool2"
    assert driver.waiting_lines() == [W.FINISH_CARD.format(n=2)]
    with pytest.raises(ValueError):
        driver.add_waiting({"item": {"label": "no id"}})


def test_finish_waiting_keeps_items_while_wsl_is_still_off(box_env, monkeypatch):
    driver.add_waiting(_entry(1))
    monkeypatch.setattr(driver, "status", lambda: driver.BoxStatus("no_wsl", W.NO_WSL, "install_wsl"))
    lines: list = []
    got = driver.finish_waiting(progress=lines.append)
    assert got[0]["status"] == "waiting" and lines == [W.FINISH_STILL_NO_WSL]
    assert len(driver.waiting()) == 1


def test_finish_waiting_sets_up_then_adds_each_item(box_env, monkeypatch):
    driver.add_waiting(_entry(1))
    driver.add_waiting(_entry(2, kind="something_else"))
    states = iter([driver.BoxStatus("no_box", W.NO_BOX, "set_up_box")])
    monkeypatch.setattr(driver, "status", lambda: next(states, driver.BoxStatus("ready", W.READY)))
    set_up: list = []
    monkeypatch.setattr(driver, "_do_setup",
                        lambda distro, progress: set_up.append(distro) or driver.BoxStatus("ready", W.READY))
    seen: list = []
    driver.register_waiting_applier("cli_tool", lambda e, p: seen.append(e["found"]["id"])
                                    or {"status": "added", "line": "Added."})
    got = driver.finish_waiting(progress=lambda line: None)
    assert set_up and seen == ["npm:tool1"]
    assert [g["status"] for g in got] == ["added", "failed"]
    assert driver.waiting() == []


def test_finish_waiting_says_why_when_setup_fails(box_env, monkeypatch):
    driver.add_waiting(_entry(1))
    monkeypatch.setattr(driver, "status", lambda: driver.BoxStatus("no_box", W.NO_BOX, "set_up_box"))
    monkeypatch.setattr(driver, "_do_setup",
                        lambda d, p: driver.BoxStatus("no_box", W.SETUP_DOWNLOAD_FAILED, "set_up_box"))
    lines: list = []
    got = driver.finish_waiting(progress=lines.append)
    assert got[0]["status"] == "failed"
    assert lines[-1] == W.SETUP_FAILED.format(message=W.SETUP_DOWNLOAD_FAILED)
    assert len(driver.waiting()) == 1


# ── the routes ───────────────────────────────────────────────────────────────

@pytest.fixture
def client(box_env, monkeypatch):
    from flask import Flask
    from puffpai.box import api
    app = Flask("l7-box-test")
    app.register_blueprint(api.bp)
    monkeypatch.setenv("PUFFPAI_BOX_DISTRO", "PuffPAI-sandbox-dev")
    monkeypatch.setattr(api, "_device", lambda: "device:abcdefgh1234")
    ct.reset()
    return app.test_client(), api


def test_routes_refuse_an_unpaired_caller(client, monkeypatch):
    c, api = client
    monkeypatch.setattr(api, "_device", lambda: "")
    for path in ("/box/setup", "/box/install-wsl", "/box/finish"):
        r = c.post(path)
        assert r.status_code == 403 and r.get_json()["message"] == api.NOT_PAIRED
    r = c.post("/box/tools/x-aaaaaaaa/folder", json={"path": "C:\\x", "mode": "ro"})
    assert r.status_code == 403


def test_status_route_says_what_waits(client, monkeypatch):
    c, _api = client
    driver.add_waiting(_entry(1))
    monkeypatch.setattr(driver, "status", lambda: driver.BoxStatus("no_wsl", W.NO_WSL, "install_wsl"))
    body = c.get("/box/status").get_json()
    assert body["state"] == "no_wsl" and body["waiting"] == 1
    assert body["waiting_lines"] == [W.FINISH_CARD_ONE]


def test_setup_route_binds_the_yes_to_the_calling_device(client, monkeypatch):
    c, api = client
    seen: dict = {}

    def fake_setup(*, ticket, device_id, progress):
        seen["ok"] = ct.redeem(ticket, device_id, "box_setup", ct.target_box("PuffPAI-sandbox-dev"))
        progress("hello")
        return driver.BoxStatus("ready", W.READY)
    monkeypatch.setattr(driver, "setup", fake_setup)
    import threading
    monkeypatch.setattr(threading, "Thread", _Inline)
    r = c.post("/box/setup")
    assert r.get_json() == {"ok": True, "started": True}
    assert seen["ok"] == (True, "ok")
    prog = c.get("/box/progress").get_json()
    assert prog["done"] and prog["lines"] == ["hello"] and prog["status"]["state"] == "ready"


class _Inline:
    def __init__(self, target=None, args=(), name=None, daemon=None, **kw):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


# ── puffpai box ... (the terminal) ──────────────────────────────────────────

def test_box_commands_parse_and_ask_first(box_env, monkeypatch, capsys):
    import argparse
    from puffpai import box_cli
    parser = argparse.ArgumentParser(prog="puffpai")
    box_cli.add_parsers(parser.add_subparsers(dest="command"))
    for words in (["box", "status"], ["box", "setup", "--yes"], ["box", "remove", "--orphans"],
                  ["box", "finish"], ["box"]):
        assert callable(parser.parse_args(words).func)
    monkeypatch.setenv("PUFFPAI_BOX_DISTRO", "PuffPAI-sandbox-dev")
    monkeypatch.setattr(driver, "status", lambda: driver.BoxStatus("no_box", W.NO_BOX, "set_up_box"))
    monkeypatch.setattr(driver, "setup", lambda **k: pytest.fail("set up without a yes"))
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    assert box_cli.main(["setup"]) == 1
    assert box_cli.KEPT in capsys.readouterr().out


def test_box_setup_from_the_terminal_is_bound_to_this_terminal(box_env, monkeypatch, capsys):
    from puffpai import box_cli
    ct.reset()
    monkeypatch.setenv("PUFFPAI_BOX_DISTRO", "PuffPAI-sandbox-dev")
    monkeypatch.setattr(driver, "status", lambda: driver.BoxStatus("no_box", W.NO_BOX, "set_up_box"))
    seen = {}

    def fake_setup(*, ticket, device_id, progress):
        seen["ok"] = ct.redeem(ticket, device_id, "box_setup", ct.target_box("PuffPAI-sandbox-dev"))
        seen["device"] = device_id
        progress(W.SETUP_DONE)
        return driver.BoxStatus("ready", W.READY)
    monkeypatch.setattr(driver, "setup", fake_setup)
    assert box_cli.main(["setup", "--yes"]) == 0
    assert seen["ok"] == (True, "ok") and seen["device"] == ct.terminal_device_id()
    assert W.SETUP_DONE in capsys.readouterr().out


def test_box_remove_and_finish_from_the_terminal(box_env, monkeypatch, capsys):
    from puffpai import box_cli
    monkeypatch.setattr(driver, "remove_box", lambda orphan=False: {"removed": False,
                                                                    "why": W.SHARED_STAYS})
    assert box_cli.main(["remove", "--yes"]) == 0
    assert W.SHARED_STAYS in capsys.readouterr().out
    assert box_cli.main(["finish", "--yes"]) == 0
    assert W.FINISH_NOTHING in capsys.readouterr().out
    monkeypatch.setattr(driver, "status", lambda: driver.BoxStatus("ready", W.READY))
    assert box_cli.main(["status"]) == 0
