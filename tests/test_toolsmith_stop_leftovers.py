"""stop() from another process (after a backend restart) left vite and
esbuild serving: only the recorded shell was stopped. Now, when this process
does not hold the app's job, the process on the app's own port and the ones
running from inside its own folder are stopped too, each by its exact id.
Processes, PowerShell, lsof and ps are mocked; nothing is stopped for real.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os

import pytest

from puffpai.build import toolsmith_run as T


@pytest.fixture
def root(tmp_path, monkeypatch):
    apps = tmp_path / "host_apps"
    (apps / "bb-vite-abc123").mkdir(parents=True)
    monkeypatch.setattr(T, "apps_root", lambda: str(apps))
    return apps


def _registry(monkeypatch, folder, **extra):
    entry = {"name": "Vite app", "port": 5199, "folder": str(folder), "pids": [111], **extra}
    monkeypatch.setattr(T, "registry", lambda: {"vite-abc123": entry})
    killed = []
    monkeypatch.setattr(T, "_kill", lambda pid: killed.append(pid))
    monkeypatch.setattr(T, "_remove_containers", lambda *a, **k: None)
    monkeypatch.setattr(T, "_JOBS", {})
    return killed


def test_inside_folder_matches_paths_inside_only():
    f = os.path.join(os.sep, "state", "host_apps", "bb-vite-abc123")
    assert T.inside_folder(f + os.sep + "node_modules" + os.sep + "vite" + os.sep + "bin", f)
    assert T.inside_folder(f'node "{f}{os.sep}x.js"', f)
    assert T.inside_folder(f, f)
    assert not T.inside_folder(f + "2" + os.sep + "x.js", f)       # a sibling folder
    assert not T.inside_folder("node server.js", f)
    assert not T.inside_folder("", f)


def test_stop_from_another_process_stops_the_port_and_folder_leftovers(root, monkeypatch):
    folder = root / "bb-vite-abc123"
    killed = _registry(monkeypatch, folder)
    seen = {}

    def rows(f, port):
        seen["port"] = port
        return [("port", 222, ""),
                ("proc", 333, f"C:/Program Files/nodejs/node.exe node {folder}/node_modules/vite/bin/vite.js"),
                ("proc", 444, f"{folder}{os.sep}node_modules{os.sep}esbuild{os.sep}esbuild.exe --service"),
                ("proc", 555, "C:/Program Files/nodejs/node.exe C:/other/app/server.js"),
                ("proc", os.getpid(), f"python {folder}/x.py")]
    monkeypatch.setattr(T, "_strays_windows", rows)
    monkeypatch.setattr(T, "_strays_posix", rows)
    T.stop("vite-abc123")
    assert killed == [111, 222, 333, 444]
    assert seen["port"] == 5199


def test_stop_in_the_process_that_holds_the_job_stops_only_its_own_ids(root, monkeypatch):
    folder = root / "bb-vite-abc123"
    killed = _registry(monkeypatch, folder)
    monkeypatch.setattr(T, "_JOBS", {111: 9})
    monkeypatch.setattr(T.os, "name", "nt")
    monkeypatch.setattr(T, "_strays_windows", lambda f, p: [("port", 222, "")])
    T.stop("vite-abc123")
    assert killed == [111]


def test_a_docker_app_port_is_never_swept(root, monkeypatch):
    folder = root / "bb-vite-abc123"
    _registry(monkeypatch, folder, containers=["bb-vite-abc123"])
    seen = {}

    def rows(f, port):
        seen["port"] = port
        return []
    monkeypatch.setattr(T, "_strays_windows", rows)
    monkeypatch.setattr(T, "_strays_posix", rows)
    T.stop("vite-abc123")
    assert seen["port"] is None


def test_a_folder_outside_the_apps_folder_is_never_swept(root, monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(T, "_strays_windows", lambda f, p: called.append(1) or [("port", 1, "")])
    monkeypatch.setattr(T, "_strays_posix", lambda f, p: called.append(1) or [("port", 1, "")])
    assert T._strays(str(tmp_path), 5199) == []                     # noqa: SLF001
    assert T._strays(os.path.abspath(os.sep), 5199) == []           # noqa: SLF001
    assert T._strays("", 5199) == [] and not called                 # noqa: SLF001


def test_windows_rows_are_read_from_powershell_output(monkeypatch):
    monkeypatch.setattr(T, "_program", lambda name: "C:/ps.exe")
    seen = {}

    def run_text(argv):
        seen["script"] = argv[-1]
        return ("port\t222\t\r\nproc\t333\tC:/n/node.exe node C:/s/host_apps/bb-x/v.js\r\n"
                "junk line\r\nproc\tabc\tx\r\n")
    monkeypatch.setattr(T, "_run_text", run_text)
    rows = T._strays_windows("C:/s/host_apps/bb-x", 5199)  # noqa: SLF001
    assert rows == [("port", 222, ""), ("proc", 333, "C:/n/node.exe node C:/s/host_apps/bb-x/v.js")]
    assert "-LocalPort 5199" in seen["script"] and "Win32_Process" in seen["script"]


def test_mac_and_linux_rows_come_from_lsof_and_ps(monkeypatch):
    monkeypatch.setattr(T.os.path, "isfile", lambda p: p in ("/usr/sbin/lsof", "/bin/ps"))

    def run_text(argv):
        if argv[1] == "-t":
            return "222\n"
        if argv[1] == "-d":
            return "p333\nfcwd\nn/s/host_apps/bb-x\np444\nn/home/me\n"
        return "  555 node /s/host_apps/bb-x/node_modules/vite/bin/vite.js\n  666 bash\n"
    monkeypatch.setattr(T, "_run_text", run_text)
    rows = T._strays_posix("/s/host_apps/bb-x", 5199)  # noqa: SLF001
    assert ("port", 222, "") in rows
    assert ("proc", 333, "/s/host_apps/bb-x") in rows
    assert ("proc", 555, "node /s/host_apps/bb-x/node_modules/vite/bin/vite.js") in rows
